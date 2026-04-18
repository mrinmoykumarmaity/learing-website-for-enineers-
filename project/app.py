import io
import json
import os
import random
import secrets
import tempfile
import textwrap
import time
from datetime import datetime, timedelta
from functools import wraps
from urllib.parse import parse_qs, unquote, urlencode, urlparse

from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    session,
    url_for,
)
from flask_login import LoginManager, current_user, login_required, logout_user
from sqlalchemy import func, or_, text
from openai import OpenAI
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.utils import secure_filename

try:
    from models import (
        Course,
        CourseCategory,
        LearningResource,
        ResourceEngagement,
        MockTestAttempt,
        Roadmap,
        User,
        UserCourseProgress,
        UserPreference,
        UserNote,
        db,
    )
except ModuleNotFoundError:
    from project.models import (
        Course,
        CourseCategory,
        LearningResource,
        ResourceEngagement,
        MockTestAttempt,
        Roadmap,
        User,
        UserCourseProgress,
        UserPreference,
        UserNote,
        db,
    )


app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-this-in-production")
is_serverless_runtime = bool(
    os.environ.get("VERCEL")
    or os.environ.get("VERCEL_ENV")
    or os.environ.get("NOW_REGION")
    or os.environ.get("AWS_LAMBDA_FUNCTION_NAME")
)
database_url = os.environ.get("DATABASE_URL")
if not database_url:
    if is_serverless_runtime:
        temp_db = os.path.join(tempfile.gettempdir(), "database.db").replace("\\", "/")
        database_url = f"sqlite:///{temp_db}"
    else:
        local_db = os.path.join(os.path.dirname(os.path.abspath(__file__)), "database.db").replace("\\", "/")
        database_url = f"sqlite:///{local_db}"
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024
if is_serverless_runtime:
    app.config["RESOURCES_UPLOAD_DIR"] = os.path.join(tempfile.gettempdir(), "resources")
else:
    app.config["RESOURCES_UPLOAD_DIR"] = os.path.join(app.static_folder, "resources")
try:
    os.makedirs(app.config["RESOURCES_UPLOAD_DIR"], exist_ok=True)
except OSError:
    app.config["RESOURCES_UPLOAD_DIR"] = os.path.join(tempfile.gettempdir(), "resources")
    os.makedirs(app.config["RESOURCES_UPLOAD_DIR"], exist_ok=True)

db.init_app(app)

login_manager = LoginManager(app)
login_manager.login_view = "dashboard"
login_manager.login_message_category = "warning"

MOCK_TEST_TOKEN_SALT = "mock-test-payload-v1"
MOCK_TEST_TOKEN_MAX_AGE_SECONDS = 60 * 60
AI_ASSISTANT_CACHE_TTL_SECONDS = 10 * 60
AI_ASSISTANT_CACHE_MAX_ENTRIES = 200
ai_assistant_response_cache = {}
ADMIN_SESSION_TTL_SECONDS_DEFAULT = 4 * 60 * 60
VALID_RESOURCE_TYPES = ("Video", "PDF", "Practice")
RESOURCE_FILTER_OPTIONS = ("All", "Video", "PDF", "Practice")
ALLOWED_RESOURCE_EXTENSIONS = {".pdf"}
NOTES_FILE_EXTENSIONS = {".pdf", ".txt"}
NOTE_SEARCH_QUERY_MAX_LENGTH = 100
USER_NOTE_TITLE_MAX_LENGTH = 160
USER_NOTE_AUTHOR_MAX_LENGTH = 120
USER_NOTE_CONTENT_MAX_LENGTH = 4000
PROJECT_IDEA_LEVEL_OPTIONS = ("Beginner", "Intermediate", "Advanced")
DEFAULT_WEEKLY_GOAL = 3
MAX_WEEKLY_GOAL = 10
AI_HISTORY_MAX_ENTRIES = 6
SECONDS_PER_MOCK_QUESTION = 75
RESOURCE_SORT_OPTIONS = ("newest", "popular", "shortest", "oldest", "title")
RESOURCE_SORT_LABELS = {
    "newest": "Newest",
    "popular": "Most Popular",
    "shortest": "Shortest",
    "oldest": "Oldest",
    "title": "A-Z",
}
ROLE_KEYWORD_MAP = {
    "ai engineer": ["ai", "machine learning", "deep learning", "ml", "python", "llm", "data"],
    "data analyst": ["data", "analytics", "sql", "excel", "power bi", "tableau", "visualization"],
    "software developer": ["software", "backend", "frontend", "web", "api", "python", "javascript", "java"],
}
ROLE_STOP_WORDS = {
    "and",
    "or",
    "the",
    "a",
    "an",
    "of",
    "for",
    "to",
    "in",
    "with",
    "engineer",
    "developer",
    "analyst",
    "specialist",
    "manager",
}
RESUME_FIELD_LIMITS = {
    "full_name": 120,
    "target_role": 120,
    "email": 140,
    "phone": 40,
    "location": 120,
    "linkedin_url": 220,
    "github_url": 220,
    "summary": 600,
    "skills_text": 800,
    "projects_text": 1800,
    "experience_text": 1800,
    "education_text": 1200,
    "certifications_text": 900,
}
INTERVIEW_QUESTION_GROUPS = [
    {
        "level": "Beginner",
        "questions": [
            {
                "question": "What is Object-Oriented Programming (OOP)?",
                "answer": "OOP is a programming paradigm based on objects and classes. It's a way to structure code to make it more modular, reusable, and maintainable. In OOP, data (attributes) and behavior (methods) are bundled together into objects. This approach mirrors real-world entities where objects have properties and perform actions.",
            },
            {
                "question": "What are the four pillars of OOP?",
                "answer": "The four pillars of OOP are: 1) Encapsulation - bundling data and methods together and hiding internal details, 2) Abstraction - hiding complex implementation details and showing only necessary features, 3) Inheritance - allowing classes to inherit properties and methods from parent classes, 4) Polymorphism - allowing objects to take multiple forms and methods to have the same name with different implementations.",
            },
            {
                "question": "What is a class vs an object?",
                "answer": "A class is a blueprint or template that defines the structure and behavior of objects. An object is an instance of a class - a concrete realization of the class. For example, 'Car' is a class (blueprint) while 'my car' is an object (specific instance). You can create multiple objects from a single class.",
            },
            {
                "question": "What is encapsulation?",
                "answer": "Encapsulation is the bundling of data (attributes) and methods (functions) into a single unit called a class, while hiding the internal details from the outside world. It uses access modifiers (public, private, protected) to control visibility. Benefits include data protection, flexibility to change internal implementation, and reduced complexity.",
            },
            {
                "question": "What is abstraction?",
                "answer": "Abstraction is the concept of hiding complex implementation details and showing only the necessary features of an object. It reduces complexity by letting programmers work with objects at a higher level without worrying about internal implementation. For example, you use a car without knowing its internal engine mechanics.",
            },
            {
                "question": "What is inheritance?",
                "answer": "Inheritance is a mechanism where a new class (child/derived class) inherits properties and methods from an existing class (parent/base class). It promotes code reuse and establishes a hierarchical relationship. For example, 'Dog' and 'Cat' can inherit from 'Animal'. Child classes can override parent methods or add new functionality.",
            },
            {
                "question": "What is polymorphism?",
                "answer": "Polymorphism means 'many forms'. It allows objects to take multiple forms and functions to have the same name with different implementations. Two main types: 1) Method overloading (same method name, different parameters), 2) Method overriding (child class redefines parent's method). Polymorphism enables flexible and reusable code.",
            },
            {
                "question": "Method overloading vs overriding?",
                "answer": "Method overloading occurs in the same class where multiple methods have the same name but different parameters (number, type, or order). Method overriding occurs when a derived class redefines a method from the parent class with the same signature. Overloading is compile-time (static) polymorphism, while overriding is runtime (dynamic) polymorphism.",
            },
            {
                "question": "What are access modifiers?",
                "answer": "Access modifiers control the visibility of class members (variables and methods). Common types: 1) Public - accessible from anywhere, 2) Private - accessible only within the class, 3) Protected - accessible within the class and derived classes, 4) Default/Package - accessible within the same package. They enforce encapsulation and data protection.",
            },
            {
                "question": "What is a constructor?",
                "answer": "A constructor is a special method that initializes an object when it's created. It has the same name as the class and no return type. Constructors are called automatically when using the 'new' keyword. They can be parameterized (accepting arguments) or parameterless. Default constructors are provided if none is defined. Constructors help set up initial object state.",
            },
        ],
    },
    {
        "level": "Intermediate",
        "questions": [
            {
                "question": "What is dynamic (runtime) polymorphism?",
                "answer": "Dynamic polymorphism is when the method to be called is determined at runtime rather than compile time. It's achieved through method overriding in inheritance hierarchies. When a parent class reference points to a child class object, calling a virtual method executes the child's version. This allows flexible code where specific behavior depends on actual object type.",
            },
            {
                "question": "Interface vs abstract class?",
                "answer": "Interfaces define contracts (what must be implemented) but provide no implementation. Abstract classes can have both abstract methods (no implementation) and concrete methods. Classes can implement multiple interfaces but inherit from only one class. Use interfaces for unrelated classes providing the same capability; use abstract classes for related classes sharing common code and state.",
            },
            {
                "question": "Composition over inheritance - why?",
                "answer": "Composition (has-a relationship) is often preferred over inheritance (is-a relationship) because it's more flexible and avoids tight coupling. With composition, you build objects by combining smaller objects rather than creating deep inheritance hierarchies. It reduces fragility - changes to parent classes don't affect child classes. It also avoids the diamond problem and makes testing easier.",
            },
            {
                "question": "What is coupling and cohesion?",
                "answer": "Coupling measures how dependent classes are on each other - low coupling is preferred. High coupling makes code hard to modify and test. Cohesion measures how closely related methods and data are within a class - high cohesion is preferred. A class with high cohesion has methods that work together toward a single purpose. Good design aims for low coupling and high cohesion.",
            },
            {
                "question": "What is SOLID?",
                "answer": "SOLID is an acronym for five design principles: Single Responsibility (class has one reason to change), Open-Closed (open for extension, closed for modification), Liskov Substitution (child classes must be substitutable for parent), Interface Segregation (clients shouldn't depend on unused methods), Dependency Inversion (depend on abstractions, not concretions). These principles create maintainable, flexible code.",
            },
            {
                "question": "Explain Liskov Substitution Principle.",
                "answer": "LSP states that objects of a derived class must be substitutable for objects of the base class without breaking the application. Child class objects should work wherever parent class objects are expected. Example: if Bird is parent and Penguin is child, we shouldn't restrict Penguin's functionality (like flying). Violations occur when derived classes are incompatible with base class contracts.",
            },
            {
                "question": "What is Dependency Injection?",
                "answer": "Dependency Injection (DI) is a design pattern where an object's dependencies are provided externally rather than created internally. Instead of a class creating the objects it needs, those objects are injected through constructors, methods, or properties. Benefits include loose coupling, easier testing (inject mock objects), and improved flexibility. It's a key aspect of the Dependency Inversion Principle.",
            },
            {
                "question": "Aggregation vs composition?",
                "answer": "Both represent 'has-a' relationships. Aggregation is a weak relationship where the child can exist independently of the parent (e.g., Department has Employees - employees exist without department). Composition is a strong relationship where the child cannot exist without the parent (e.g., House has Rooms - rooms don't exist without house). In composition, deleting parent deletes children; in aggregation, they can exist separately.",
            },
            {
                "question": "Shallow copy vs deep copy?",
                "answer": "Shallow copy copies an object's reference fields, not the objects they point to. Changes to referenced objects affect both original and copy. Deep copy recursively copies all objects and nested structures, creating independent copies. Shallow copies are faster but risky with mutable objects. Deep copies are slower but ensure true independence. Choice depends on whether objects share data or are independent.",
            },
            {
                "question": "Static vs final methods - why use them?",
                "answer": "Static methods belong to the class, not instances - called on the class itself. They can't access instance variables. Use for utility functions. Final methods can't be overridden by subclasses - prevents modification of critical behavior. Use when you want to ensure a method's implementation remains unchanged. Final classes can't be subclassed. These provide control and prevent unintended modifications.",
            },
        ],
    },
    {
        "level": "Advanced",
        "questions": [
            {
                "question": "Multiple inheritance problem and solutions?",
                "answer": "The diamond problem occurs when a class inherits from two classes that share a common parent, creating ambiguity about which parent's methods to use. C++ solves this with virtual inheritance. Java avoids multiple inheritance, using single inheritance + interfaces instead. Solutions include: explicitly specifying which parent's method to call, using composition instead, or using interfaces (which define contracts without implementation ambiguity).",
            },
            {
                "question": "Common OOP design patterns?",
                "answer": "Common patterns include: Singleton (single instance), Factory (create objects without specifying exact classes), Observer (notify multiple objects of state changes), Strategy (encapsulate interchangeable algorithms), Decorator (add behavior dynamically), Adapter (make incompatible interfaces compatible), Template Method (define algorithm skeleton in base, let derived classes override steps), Builder (construct complex objects step by step).",
            },
            {
                "question": "Constructor vs destructor/finalizer?",
                "answer": "Constructors initialize objects when created, allocating resources and setting initial state. Destructors/finalizers clean up resources when objects are destroyed. In languages with garbage collection (Java, Python), finalizers are unreliable - use try-finally or try-with-resources instead. In C++, destructors are critical. Proper cleanup prevents memory leaks and resource exhaustion. Modern approaches prefer explicit cleanup patterns over relying on destructors.",
            },
            {
                "question": "Garbage collection vs manual memory management?",
                "answer": "Garbage collection automatically frees unused memory, reducing bugs and development time but adds overhead and unpredictable pauses. Manual memory management (C++) gives control and performance but requires careful bookkeeping and risks memory leaks. Most modern languages use garbage collection. Performance-critical systems sometimes use manual management. Languages like Rust use ownership/borrowing to avoid both approaches' drawbacks.",
            },
            {
                "question": "Object lifecycle in OOP?",
                "answer": "Object lifecycle includes: 1) Creation (constructor called, memory allocated), 2) Initialization (state set up), 3) Usage (methods called, state modified), 4) Cleanup (resources released), 5) Destruction (memory deallocated). In garbage-collected languages, cleanup/destruction are automatic. Understanding lifecycle helps prevent memory leaks, resource exhaustion, and improper state. Proper initialization and cleanup are critical design concerns.",
            },
            {
                "question": "Single vs multiple dispatch?",
                "answer": "Single dispatch (used in most OOP languages) chooses the method based on the runtime type of one object (usually 'this'). Multiple dispatch chooses based on types of multiple objects. Java uses single dispatch. Languages like Common Lisp support multiple dispatch, which is more powerful but complex. Single dispatch is simpler and sufficient for most cases; multiple dispatch is useful when behavior depends on multiple objects' types.",
            },
            {
                "question": "Mixins / traits - when to use?",
                "answer": "Mixins (or traits) are classes providing reusable functionality without being parent classes. Languages like Ruby support mixins; languages like Scala have traits. Use when: multiple unrelated classes need the same behavior, but inheritance doesn't fit semantically, or you need multiple inheritance benefits without complexity. They provide code reuse without tight coupling. Mixins are more flexible than inheritance for cross-cutting functionality.",
            },
            {
                "question": "Designing immutable objects?",
                "answer": "Immutable objects can't be modified after creation. Design: make class final, fields private and final, initialize all fields in constructor, don't provide setters, return copies instead of mutable collections. Benefits: thread-safety (no locks needed), can be safely shared, useful as map keys or in caches. Tradeoff: creating slightly modified objects requires creating new instances. Essential for functional programming and concurrent systems.",
            },
            {
                "question": "OOP pitfalls of overusing inheritance?",
                "answer": "Common pitfalls: 1) Deep inheritance hierarchies become hard to understand and modify, 2) Tight coupling between parent and child classes, 3) Violating Liskov Substitution Principle, 4) The fragile base class problem - parent changes break children, 5) Code duplication in unrelated classes forced into inheritance. Solutions: favor composition over inheritance, keep hierarchies shallow, use interfaces, apply design principles like SOLID.",
            },
            {
                "question": "Refactor a God class - how?",
                "answer": "A God class has too many responsibilities, violating Single Responsibility Principle. Refactoring steps: 1) Identify distinct responsibilities using cohesion analysis, 2) Extract related data and methods into new classes, 3) Move methods to appropriate classes, 4) Use composition or inheritance where appropriate, 5) Apply design patterns (Facade, Decorator, Strategy), 6) Ensure each class has one clear purpose. Result: easier to test, modify, and understand.",
            },
        ],
    },
]


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


def admin_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not can_access_admin_panel():
            flash("Admin access is locked. Enter your admin token.", "warning")
            return redirect(url_for("admin_unlock", next=request.path))
        return view_func(*args, **kwargs)

    return wrapped


def is_safe_next_url(target):
    if not target:
        return False
    parsed = urlparse(target)
    return parsed.scheme == "" and parsed.netloc == "" and target.startswith("/")


def parse_int(value, default, min_value=None, max_value=None):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default

    if min_value is not None and parsed < min_value:
        return default
    if max_value is not None and parsed > max_value:
        return default
    return parsed


def sanitize_text(value, max_length):
    cleaned = (value or "").strip()
    if len(cleaned) > max_length:
        return cleaned[:max_length]
    return cleaned


def parse_text_items(raw_value, max_items=12, max_item_length=120):
    normalized = (raw_value or "").replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.replace(";", "\n").replace(",", "\n")

    items = []
    seen = set()
    for chunk in normalized.split("\n"):
        item = " ".join(chunk.split())
        if not item:
            continue
        item = sanitize_text(item, max_item_length)
        lowered = item.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        items.append(item)
        if len(items) >= max_items:
            break
    return items


def build_ats_resume_text(resume_form):
    full_name = sanitize_text(resume_form.get("full_name", ""), RESUME_FIELD_LIMITS["full_name"]) or "Your Name"
    target_role = sanitize_text(resume_form.get("target_role", ""), RESUME_FIELD_LIMITS["target_role"]) or "Software Developer"
    email = sanitize_text(resume_form.get("email", ""), RESUME_FIELD_LIMITS["email"])
    phone = sanitize_text(resume_form.get("phone", ""), RESUME_FIELD_LIMITS["phone"])
    location = sanitize_text(resume_form.get("location", ""), RESUME_FIELD_LIMITS["location"])
    linkedin_url = sanitize_text(resume_form.get("linkedin_url", ""), RESUME_FIELD_LIMITS["linkedin_url"])
    github_url = sanitize_text(resume_form.get("github_url", ""), RESUME_FIELD_LIMITS["github_url"])
    summary = sanitize_text(resume_form.get("summary", ""), RESUME_FIELD_LIMITS["summary"])
    if not summary:
        summary = (
            f"Entry-level {target_role} candidate with hands-on learning projects, "
            "strong problem-solving skills, and focus on clean implementation."
        )

    skills = parse_text_items(
        sanitize_text(resume_form.get("skills_text", ""), RESUME_FIELD_LIMITS["skills_text"]),
        max_items=20,
        max_item_length=80,
    )
    projects = parse_text_items(
        sanitize_text(resume_form.get("projects_text", ""), RESUME_FIELD_LIMITS["projects_text"]),
        max_items=10,
        max_item_length=220,
    )
    experience = parse_text_items(
        sanitize_text(resume_form.get("experience_text", ""), RESUME_FIELD_LIMITS["experience_text"]),
        max_items=10,
        max_item_length=220,
    )
    education = parse_text_items(
        sanitize_text(resume_form.get("education_text", ""), RESUME_FIELD_LIMITS["education_text"]),
        max_items=8,
        max_item_length=220,
    )
    certifications = parse_text_items(
        sanitize_text(resume_form.get("certifications_text", ""), RESUME_FIELD_LIMITS["certifications_text"]),
        max_items=8,
        max_item_length=220,
    )

    lines = [
        full_name.upper(),
        target_role,
        "",
    ]
    contact_parts = [value for value in [location, phone, email] if value]
    if contact_parts:
        lines.append(" | ".join(contact_parts))

    profile_links = []
    if linkedin_url:
        profile_links.append(f"LinkedIn: {linkedin_url}")
    if github_url:
        profile_links.append(f"GitHub: {github_url}")
    if profile_links:
        lines.append(" | ".join(profile_links))

    lines.extend(
        [
            "",
            "PROFESSIONAL SUMMARY",
            summary,
            "",
            "SKILLS",
        ]
    )
    if skills:
        lines.extend(f"- {item}" for item in skills)
    else:
        lines.append("- Python")
        lines.append("- SQL")
        lines.append("- Problem Solving")

    lines.append("")
    lines.append("PROJECTS")
    if projects:
        lines.extend(f"- {item}" for item in projects)
    else:
        lines.append("- Add 2-3 relevant projects with measurable outcomes.")

    lines.append("")
    lines.append("EXPERIENCE")
    if experience:
        lines.extend(f"- {item}" for item in experience)
    else:
        lines.append("- Include internships, freelance work, or leadership activities.")

    lines.append("")
    lines.append("EDUCATION")
    if education:
        lines.extend(f"- {item}" for item in education)
    else:
        lines.append("- Add degree, institute, and graduation year.")

    lines.append("")
    lines.append("CERTIFICATIONS")
    if certifications:
        lines.extend(f"- {item}" for item in certifications)
    else:
        lines.append("- Add job-relevant certifications or coursework.")

    return "\n".join(lines).strip()


def build_project_ideas_fallback(topic, level, idea_count):
    clean_topic = sanitize_text(topic, 120) or "software development"
    clean_level = level if level in PROJECT_IDEA_LEVEL_OPTIONS else "Beginner"
    capped_count = max(3, min(parse_int(idea_count, 5), 8))
    focus_slug = clean_topic.lower()

    if "data" in focus_slug:
        stack = "Python, Pandas, SQL, Streamlit"
    elif "web" in focus_slug or "frontend" in focus_slug or "backend" in focus_slug:
        stack = "Python/Node.js, Flask/Express, SQL, HTML/CSS"
    elif "ai" in focus_slug or "ml" in focus_slug:
        stack = "Python, scikit-learn, FastAPI, SQLite"
    else:
        stack = "Python, Flask, SQLite, Bootstrap"

    blueprints = [
        {
            "title": f"{clean_topic.title()} Tracker",
            "build": "Track progress, deadlines, and milestones with filters and dashboard metrics.",
            "bullet": "Built a full CRUD system with search, filtering, and analytics-ready data export.",
        },
        {
            "title": "Interview Prep Portal",
            "build": "Create topic-wise question practice, score history, and weak-area recommendations.",
            "bullet": "Designed assessment workflows and improved feedback quality with structured scoring.",
        },
        {
            "title": "Resume Analyzer",
            "build": "Parse resumes, compare against job keywords, and provide ATS improvement suggestions.",
            "bullet": "Implemented keyword-gap analysis and generated actionable resume recommendations.",
        },
        {
            "title": "Team Collaboration Board",
            "build": "Build task assignment, status movement, comments, and role-based access controls.",
            "bullet": "Delivered collaborative workflow tooling with secure role-based permissions.",
        },
        {
            "title": "Content Recommendation Engine",
            "build": "Suggest personalized learning content using user preferences and activity signals.",
            "bullet": "Built recommendation logic that improved content relevance and user retention.",
        },
        {
            "title": "Support Ticket Classifier",
            "build": "Auto-tag incoming issues by topic and priority, then route to correct queues.",
            "bullet": "Automated classification pipeline to reduce manual triage time.",
        },
        {
            "title": "Job Application Organizer",
            "build": "Track applications, follow-up reminders, interview rounds, and outcome reports.",
            "bullet": "Created productivity tooling to improve application tracking and interview readiness.",
        },
        {
            "title": "Skill Gap Planner",
            "build": "Map current skills to role requirements and generate weekly upskilling plans.",
            "bullet": "Built planning automation that linked skill gaps to actionable learning tasks.",
        },
    ]

    lines = [f"Project ideas for {clean_topic} ({clean_level}):"]
    for idx, blueprint in enumerate(blueprints[:capped_count], start=1):
        lines.append(f"{idx}. {blueprint['title']}")
        lines.append(f"   Level: {clean_level}")
        lines.append(f"   Stack: {stack}")
        lines.append(f"   Build: {blueprint['build']}")
        lines.append(f"   Resume bullet: {blueprint['bullet']}")
    return "\n".join(lines)


def get_ai_project_ideas(topic, level, idea_count):
    clean_topic = sanitize_text(topic, 120) or "software development"
    clean_level = level if level in PROJECT_IDEA_LEVEL_OPTIONS else "Beginner"
    capped_count = max(3, min(parse_int(idea_count, 5), 8))

    client, model = get_openai_client_and_model()
    if not client:
        return build_project_ideas_fallback(clean_topic, clean_level, capped_count)

    prompt = (
        "You are a practical software mentor.\n"
        f"Generate {capped_count} ATS-friendly project ideas for topic: {clean_topic}.\n"
        f"Candidate level: {clean_level}\n"
        "For each idea include: title, stack, what to build, and one resume bullet.\n"
        "Format as a clear numbered list. Keep concise and practical."
    )
    try:
        response = client.with_options(timeout=12.0, max_retries=0).responses.create(
            model=model,
            input=prompt,
            max_output_tokens=420,
        )
        answer = (response.output_text or "").strip()
        if len(answer) < 40:
            return build_project_ideas_fallback(clean_topic, clean_level, capped_count)
        return answer
    except Exception:
        return build_project_ideas_fallback(clean_topic, clean_level, capped_count)


def is_valid_http_url(value):
    if not value:
        return False
    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def get_first_query_value(query_map, key):
    values = query_map.get(key) or []
    if not values:
        return ""
    return (values[0] or "").strip()


def sanitize_youtube_token(value, max_length=64):
    token = (value or "").strip()
    if not token or len(token) > max_length:
        return ""
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
    if any(char not in allowed for char in token):
        return ""
    return token


def parse_youtube_start_seconds(query_params):
    raw_value = get_first_query_value(query_params, "start") or get_first_query_value(query_params, "t")
    if not raw_value:
        return None

    raw_value = raw_value.lower().strip()
    if raw_value.isdigit():
        parsed_seconds = int(raw_value)
        return parsed_seconds if parsed_seconds > 0 else None

    total_seconds = 0
    current_number = ""
    contains_unit_suffix = False
    for char in raw_value:
        if char.isdigit():
            current_number += char
            continue

        if char not in {"h", "m", "s"} or not current_number:
            return None

        contains_unit_suffix = True
        numeric_value = int(current_number)
        if char == "h":
            total_seconds += numeric_value * 3600
        elif char == "m":
            total_seconds += numeric_value * 60
        else:
            total_seconds += numeric_value
        current_number = ""

    if current_number:
        tail_value = int(current_number)
        total_seconds += tail_value

    if not contains_unit_suffix and total_seconds <= 0:
        return None
    return total_seconds if total_seconds > 0 else None


def build_youtube_embed_url(raw_url):
    if not is_valid_http_url(raw_url):
        return None

    parsed = urlparse(raw_url.strip())
    host = parsed.netloc.lower().replace("www.", "")
    query_params = parse_qs(parsed.query)
    path_parts = [segment for segment in parsed.path.split("/") if segment]
    path_head = path_parts[0] if path_parts else ""

    video_id = ""
    playlist_id = ""

    if host in {"youtube.com", "m.youtube.com", "music.youtube.com", "youtube-nocookie.com"}:
        if path_head == "watch":
            video_id = sanitize_youtube_token(get_first_query_value(query_params, "v"), max_length=32)
            playlist_id = sanitize_youtube_token(get_first_query_value(query_params, "list"), max_length=80)
        elif path_head == "playlist":
            playlist_id = sanitize_youtube_token(get_first_query_value(query_params, "list"), max_length=80)
        elif path_head in {"shorts", "embed", "live"} and len(path_parts) > 1:
            video_id = sanitize_youtube_token(path_parts[1], max_length=32)
            playlist_id = sanitize_youtube_token(get_first_query_value(query_params, "list"), max_length=80)
    elif host == "youtu.be" and path_parts:
        video_id = sanitize_youtube_token(path_parts[0], max_length=32)
        playlist_id = sanitize_youtube_token(get_first_query_value(query_params, "list"), max_length=80)
    else:
        return None

    if not video_id and not playlist_id:
        return None

    if video_id:
        query_args = {"rel": "0"}
        if playlist_id:
            query_args["list"] = playlist_id

        start_seconds = parse_youtube_start_seconds(query_params)
        if start_seconds:
            query_args["start"] = str(start_seconds)
        return f"https://www.youtube.com/embed/{video_id}?{urlencode(query_args)}"

    return f"https://www.youtube.com/embed/videoseries?{urlencode({'list': playlist_id})}"


def get_admin_access_token():
    return get_env_var_with_windows_fallback("ADMIN_ACCESS_TOKEN")


def get_admin_session_ttl_seconds():
    configured = get_env_var_with_windows_fallback("ADMIN_SESSION_TTL_SECONDS")
    return parse_int(
        configured,
        ADMIN_SESSION_TTL_SECONDS_DEFAULT,
        min_value=5 * 60,
        max_value=24 * 60 * 60,
    )


def unlock_admin_session():
    session["admin_unlocked_at"] = time.time()


def lock_admin_session():
    session.pop("admin_unlocked_at", None)


def can_access_admin_panel():
    if current_user.is_authenticated and current_user.is_admin:
        return True

    token = get_admin_access_token()
    if not token:
        return False

    unlocked_at_raw = session.get("admin_unlocked_at")
    if unlocked_at_raw is None:
        return False

    try:
        unlocked_at = float(unlocked_at_raw)
    except (TypeError, ValueError):
        lock_admin_session()
        return False

    if (time.time() - unlocked_at) > get_admin_session_ttl_seconds():
        lock_admin_session()
        return False

    return True


def get_weekly_progress_chart(user_id):
    today = datetime.utcnow().date()
    dates = [today - timedelta(days=offset) for offset in range(6, -1, -1)]
    day_counts = {day: 0 for day in dates}
    baseline_completed = 0

    completed_rows = UserCourseProgress.query.filter_by(user_id=user_id, completed=True).all()
    for row in completed_rows:
        completed_at = row.completed_at
        if completed_at is None:
            baseline_completed += 1
            continue
        completed_date = completed_at.date()
        if completed_date in day_counts:
            day_counts[completed_date] += 1
        else:
            baseline_completed += 1

    labels = [day.strftime("%a") for day in dates]
    daily_data = [day_counts[day] for day in dates]
    cumulative_data = []
    running_total = baseline_completed
    for completed_count in daily_data:
        running_total += completed_count
        cumulative_data.append(running_total)

    return {
        "labels": labels,
        "daily": daily_data,
        "cumulative": cumulative_data,
    }


def save_uploaded_resource_file(file_storage):
    if file_storage is None:
        return None, "Please select a file."

    raw_name = secure_filename(file_storage.filename or "")
    if not raw_name:
        return None, "Please choose a valid file."

    extension = os.path.splitext(raw_name)[1].lower()
    if extension not in ALLOWED_RESOURCE_EXTENSIONS:
        return None, "Only PDF uploads are supported."

    unique_name = f"{int(time.time())}-{secrets.token_hex(4)}{extension}"
    upload_dir = app.config["RESOURCES_UPLOAD_DIR"]
    os.makedirs(upload_dir, exist_ok=True)
    absolute_path = os.path.join(upload_dir, unique_name)

    try:
        file_storage.save(absolute_path)
    except Exception:
        return None, "Could not save uploaded file."

    return f"uploads/{unique_name}", None


def commit_or_rollback():
    try:
        db.session.commit()
        return True
    except Exception:
        db.session.rollback()
        return False


def get_user_preferences(user_id):
    if not user_id:
        return None
    return UserPreference.query.filter_by(user_id=user_id).first()


def save_user_preferences(user_id, target_role, weekly_goal):
    if not user_id:
        return False
    preference = UserPreference.query.filter_by(user_id=user_id).first()
    if preference is None:
        preference = UserPreference(user_id=user_id)
        db.session.add(preference)
    preference.target_role = target_role or None
    preference.weekly_goal = weekly_goal
    return commit_or_rollback()


def normalize_target_role(raw_role):
    cleaned = sanitize_text(raw_role or "", 120)
    return cleaned


def extract_goal_keywords(target_role):
    if not target_role:
        return []
    role_key = target_role.strip().lower()
    if role_key in ROLE_KEYWORD_MAP:
        return ROLE_KEYWORD_MAP[role_key]

    tokens = [token.strip() for token in role_key.replace("/", " ").replace("-", " ").split()]
    keywords = []
    for token in tokens:
        if not token or token in ROLE_STOP_WORDS:
            continue
        keywords.append(token)
    return keywords[:6]


def apply_keyword_filter(query, keywords, *columns):
    cleaned = [kw for kw in (keywords or []) if kw]
    if not cleaned:
        return query
    clauses = []
    for keyword in cleaned:
        like_pattern = f"%{keyword}%"
        column_clauses = [column.ilike(like_pattern) for column in columns if column is not None]
        if column_clauses:
            clauses.append(or_(*column_clauses))
    if not clauses:
        return query
    return query.filter(or_(*clauses))


def get_weekly_goal_progress(user_id, weekly_goal):
    if not user_id:
        return 0
    end_date = datetime.utcnow().date()
    start_date = end_date - timedelta(days=6)
    completed_rows = (
        UserCourseProgress.query.filter_by(user_id=user_id, completed=True)
        .filter(UserCourseProgress.completed_at.isnot(None))
        .all()
    )
    completed_count = 0
    for row in completed_rows:
        completed_date = row.completed_at.date()
        if start_date <= completed_date <= end_date:
            completed_count += 1
    goal_value = weekly_goal or DEFAULT_WEEKLY_GOAL
    goal_value = max(1, min(goal_value, MAX_WEEKLY_GOAL))
    percent = round((completed_count / goal_value) * 100, 2) if goal_value else 0
    return {
        "count": completed_count,
        "goal": goal_value,
        "percent": min(percent, 100),
    }


def get_learning_streak(user_id):
    if not user_id:
        return 0
    completed_rows = (
        UserCourseProgress.query.filter_by(user_id=user_id, completed=True)
        .filter(UserCourseProgress.completed_at.isnot(None))
        .all()
    )
    completion_dates = {row.completed_at.date() for row in completed_rows if row.completed_at}
    if not completion_dates:
        return 0
    streak = 0
    current_day = datetime.utcnow().date()
    while current_day in completion_dates:
        streak += 1
        current_day -= timedelta(days=1)
    return streak


def get_recommended_courses_for_goal(completed_ids, target_role, limit=8):
    query = Course.query
    if completed_ids:
        query = query.filter(~Course.id.in_(completed_ids))
    keywords = extract_goal_keywords(target_role)
    query = apply_keyword_filter(query, keywords, Course.title, Course.description)
    return query.order_by(Course.title.asc()).limit(limit).all()


def get_resource_size_hint(resource):
    if resource.file_path:
        file_name = os.path.basename(resource.file_path)
        upload_dir = app.config["RESOURCES_UPLOAD_DIR"]
        candidate_path = os.path.join(upload_dir, file_name)
        if os.path.isfile(candidate_path):
            return os.path.getsize(candidate_path)
        static_path = os.path.join(app.static_folder, resource.file_path)
        if os.path.isfile(static_path):
            return os.path.getsize(static_path)
    description = resource.description or ""
    return 200000 + len(description) * 100


def get_resource_popularity_map(resource_ids):
    if not resource_ids:
        return {}
    rows = ResourceEngagement.query.filter(ResourceEngagement.resource_id.in_(resource_ids)).all()
    return {row.resource_id: row.open_count for row in rows}


def record_resource_open(resource_id):
    engagement = ResourceEngagement.query.filter_by(resource_id=resource_id).first()
    if engagement is None:
        engagement = ResourceEngagement(resource_id=resource_id, open_count=0)
        db.session.add(engagement)
    engagement.open_count += 1
    engagement.last_opened_at = datetime.utcnow()
    commit_or_rollback()


def build_simple_pdf(title, lines):
    safe_title = sanitize_text(title or "Document", 120) or "Document"
    wrapped_lines = []
    for line in lines:
        cleaned = (line or "").strip()
        if not cleaned:
            wrapped_lines.append("")
            continue
        wrapped_lines.extend(textwrap.wrap(cleaned, width=95) or [""])

    def pdf_escape(value):
        return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    content_lines = ["BT", "/F1 12 Tf", "50 760 Td", f"({pdf_escape(safe_title)}) Tj"]
    for line in wrapped_lines:
        content_lines.append("0 -14 Td")
        content_lines.append(f"({pdf_escape(line)}) Tj")
    content_lines.append("ET")
    content_stream = "\n".join(content_lines)
    content_bytes = content_stream.encode("latin-1", errors="ignore")

    objects = []
    objects.append(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")
    objects.append(b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n")
    objects.append(
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n"
    )
    objects.append(
        f"4 0 obj\n<< /Length {len(content_bytes)} >>\nstream\n".encode("latin-1")
        + content_bytes
        + b"\nendstream\nendobj\n"
    )
    objects.append(b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n")

    offsets = []
    pdf_output = io.BytesIO()
    pdf_output.write(b"%PDF-1.4\n")
    for obj in objects:
        offsets.append(pdf_output.tell())
        pdf_output.write(obj)
    xref_position = pdf_output.tell()
    pdf_output.write(f"xref\n0 {len(objects) + 1}\n".encode("latin-1"))
    pdf_output.write(b"0000000000 65535 f \n")
    for offset in offsets:
        pdf_output.write(f"{offset:010d} 00000 n \n".encode("latin-1"))
    pdf_output.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_position}\n%%EOF".encode(
            "latin-1"
        )
    )
    return pdf_output.getvalue()


def build_roadmap_pdf_lines(payload):
    generated = payload.get("generated") or []
    lines = [
        f"Current level: {payload.get('current_level', 'Beginner')}",
        f"Target career: {payload.get('target_career', 'Software Developer')}",
        f"Daily study time: {payload.get('daily_study_time', 2)} hours",
        "",
        "Roadmap details:",
        "",
    ]
    for month in generated:
        lines.append(f"Month {month.get('month')}: {month.get('focus_topic')}")
        lines.append(f"Weekly hours: {month.get('weekly_hours')}")
        lines.append(f"Project: {month.get('project')}")
        courses = month.get("recommended_courses") or []
        if courses:
            lines.append(f"Courses: {', '.join(courses)}")
        lines.append("")
    return lines


def infer_resource_tags(title):
    if not title:
        return ["General"]
    lowered = title.lower()
    tag_map = {
        "AI": ["ai", "machine learning", "ml", "deep learning", "llm"],
        "Data": ["data", "analytics", "analysis", "dataset"],
        "SQL": ["sql", "database"],
        "Python": ["python"],
        "Web": ["web", "html", "css", "javascript", "frontend", "backend"],
        "OOP": ["oop", "object oriented"],
        "DSA": ["dsa", "data structure", "algorithm"],
        "System Design": ["system design", "architecture", "scalable"],
        "Interview": ["interview", "question"],
        "Resume": ["resume", "cv"],
    }
    tags = []
    for tag, keywords in tag_map.items():
        if any(keyword in lowered for keyword in keywords):
            tags.append(tag)
    return tags or ["General"]


def build_preview_snippet(title, preview_text=None):
    if preview_text:
        return sanitize_text(preview_text, 180)
    if not title:
        return "Resource preview not available."
    return f"Quick reference notes for {title}."


def get_roadmap_pdf_path(filename):
    safe_name = (filename or "").strip()
    if not safe_name:
        return None

    candidates = [
        os.path.join(os.path.dirname(app.root_path), "public", "roadmaps", safe_name),
        os.path.join(app.static_folder, "roadmaps", safe_name),
    ]
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return None


def get_interview_pdf_library():
    static_subdir = "interview_questions"
    library_dir = os.path.join(app.static_folder, static_subdir)
    library_items = []
    if not os.path.isdir(library_dir):
        return library_items

    manifest_path = os.path.join(library_dir, "manifest.json")
    manifest_entries = []
    if os.path.isfile(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as manifest_file:
                manifest_payload = json.load(manifest_file)
                if isinstance(manifest_payload, list):
                    manifest_entries = [item for item in manifest_payload if isinstance(item, dict)]
        except Exception:
            manifest_entries = []

    if manifest_entries:
        for item in manifest_entries:
            file_name = (item.get("file_name") or "").strip()
            if not file_name.lower().endswith(".pdf"):
                continue

            absolute_path = os.path.join(library_dir, file_name)
            if not os.path.isfile(absolute_path):
                continue

            title = sanitize_text((item.get("title") or os.path.splitext(file_name)[0]), 140)
            raw_tags = item.get("tags")
            tags = [sanitize_text(tag, 40) for tag in raw_tags] if isinstance(raw_tags, list) else []
            if not tags:
                tags = infer_resource_tags(title)
            preview_text = item.get("preview") or item.get("summary")
            preview = build_preview_snippet(title, preview_text)
            size_mb = round(os.path.getsize(absolute_path) / (1024 * 1024), 2)
            library_items.append(
                {
                    "id": len(library_items) + 1,
                    "title": title,
                    "file_name": file_name,
                    "static_path": f"{static_subdir}/{file_name}",
                    "size_mb": size_mb,
                    "tags": tags,
                    "preview": preview,
                }
            )
        return library_items

    for file_name in sorted(os.listdir(library_dir), key=lambda item: item.lower()):
        if not file_name.lower().endswith(".pdf"):
            continue

        absolute_path = os.path.join(library_dir, file_name)
        if not os.path.isfile(absolute_path):
            continue

        title = os.path.splitext(file_name)[0]
        tags = infer_resource_tags(title)
        preview = build_preview_snippet(title)
        size_mb = round(os.path.getsize(absolute_path) / (1024 * 1024), 2)
        library_items.append(
            {
                "id": len(library_items) + 1,
                "title": title,
                "file_name": file_name,
                "static_path": f"{static_subdir}/{file_name}",
                "size_mb": size_mb,
                "tags": tags,
                "preview": preview,
            }
        )

    return library_items


def get_interview_pdf_lookup():
    lookup = {}
    for item in get_interview_pdf_library():
        safe_name = (item.get("file_name") or "").strip()
        title = (item.get("title") or "").strip()
        if safe_name:
            lookup[safe_name.lower()] = safe_name
        if title:
            lookup[f"{title}.pdf".lower()] = safe_name

    manifest_path = os.path.join(app.static_folder, "interview_questions", "manifest.json")
    if os.path.isfile(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as manifest_file:
                manifest_payload = json.load(manifest_file)
            if isinstance(manifest_payload, list):
                for row in manifest_payload:
                    if not isinstance(row, dict):
                        continue
                    safe_name = (row.get("file_name") or "").strip()
                    original_name = (row.get("original_name") or "").strip()
                    if safe_name and original_name:
                        lookup[original_name.lower()] = safe_name
        except Exception:
            pass

    return lookup


def format_file_size(file_size_bytes):
    if file_size_bytes >= 1024 * 1024:
        return f"{round(file_size_bytes / (1024 * 1024), 2)} MB"
    return f"{round(file_size_bytes / 1024, 1)} KB"


def get_notes_library():
    static_subdir = "notes"
    library_dir = os.path.join(app.static_folder, static_subdir)
    library_items = []
    if not os.path.isdir(library_dir):
        return library_items

    manifest_path = os.path.join(library_dir, "manifest.json")
    manifest_entries = []
    if os.path.isfile(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as manifest_file:
                manifest_payload = json.load(manifest_file)
                if isinstance(manifest_payload, list):
                    manifest_entries = [item for item in manifest_payload if isinstance(item, dict)]
        except Exception:
            manifest_entries = []

    if manifest_entries:
        for item in manifest_entries:
            file_name = (item.get("file_name") or "").strip()
            extension = os.path.splitext(file_name)[1].lower()
            if extension not in NOTES_FILE_EXTENSIONS:
                continue

            title = sanitize_text((item.get("title") or os.path.splitext(file_name)[0]), 140)
            file_url = sanitize_text((item.get("file_url") or f"/notes-assets/{file_name}"), 240)
            size_label = sanitize_text((item.get("size_label") or ""), 40)
            if not size_label:
                absolute_path = os.path.join(library_dir, file_name)
                if os.path.isfile(absolute_path):
                    size_label = format_file_size(os.path.getsize(absolute_path))
                else:
                    size_label = "Unknown size"
            preview_text = item.get("preview") or item.get("summary")
            if not preview_text and extension == ".txt":
                absolute_path = os.path.join(library_dir, file_name)
                if os.path.isfile(absolute_path):
                    try:
                        with open(absolute_path, "r", encoding="utf-8", errors="ignore") as note_file:
                            preview_text = note_file.read(220)
                    except Exception:
                        preview_text = ""
            raw_tags = item.get("tags")
            tags = [sanitize_text(tag, 40) for tag in raw_tags] if isinstance(raw_tags, list) else []
            if not tags:
                tags = infer_resource_tags(title)
            library_items.append(
                {
                    "title": title,
                    "file_name": file_name,
                    "file_url": file_url,
                    "resource_type": "PDF" if extension == ".pdf" else "Text",
                    "size_label": size_label,
                    "tags": tags,
                    "preview": build_preview_snippet(title, preview_text),
                }
            )
        return library_items

    for file_name in sorted(os.listdir(library_dir), key=lambda item: item.lower()):
        extension = os.path.splitext(file_name)[1].lower()
        if extension not in NOTES_FILE_EXTENSIONS:
            continue

        absolute_path = os.path.join(library_dir, file_name)
        if not os.path.isfile(absolute_path):
            continue

        file_size = os.path.getsize(absolute_path)
        preview_text = ""
        if extension == ".txt":
            try:
                with open(absolute_path, "r", encoding="utf-8", errors="ignore") as note_file:
                    preview_text = note_file.read(220)
            except Exception:
                preview_text = ""
        tags = infer_resource_tags(os.path.splitext(file_name)[0])
        library_items.append(
            {
                "title": os.path.splitext(file_name)[0],
                "file_name": file_name,
                "file_url": url_for("static", filename=f"{static_subdir}/{file_name}"),
                "resource_type": "PDF" if extension == ".pdf" else "Text",
                "size_label": format_file_size(file_size),
                "tags": tags,
                "preview": build_preview_snippet(os.path.splitext(file_name)[0], preview_text),
            }
        )

    return library_items


def get_mock_test_token_serializer():
    return URLSafeTimedSerializer(app.config["SECRET_KEY"], salt=MOCK_TEST_TOKEN_SALT)


def make_mock_test_payload_token(payload):
    serializer = get_mock_test_token_serializer()
    return serializer.dumps(payload)


def read_mock_test_payload_token(token):
    if not token:
        return None
    serializer = get_mock_test_token_serializer()
    try:
        payload = serializer.loads(token, max_age=MOCK_TEST_TOKEN_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None

    if not isinstance(payload, dict):
        return None

    questions = payload.get("questions")
    if not isinstance(questions, list):
        return None

    for item in questions:
        if not isinstance(item, dict):
            return None
        if not isinstance(item.get("id"), str):
            return None
        if not isinstance(item.get("question"), str):
            return None
        if not isinstance(item.get("options"), list) or len(item.get("options")) != 4:
            return None
        if not isinstance(item.get("answer_index"), int):
            return None

    return payload


def seed_categories_and_courses():
    if CourseCategory.query.count() > 0:
        return

    catalog = {
        "Software Engineering": [
            {
                "title": "Software Engineering Full Course",
                "description": "Core principles, SDLC, architecture and best practices.",
                "playlist_url": "https://www.youtube.com/playlist?list=PLWKjhJtqVAbljtmLi3si3aQfT3mQjJx2f",
            },
            {
                "title": "System Design Fundamentals",
                "description": "Scalable backend architecture and design tradeoffs.",
                "playlist_url": "https://www.youtube.com/playlist?list=PLMCXHnjXnTnvo6alSjVkgxV-VH6EPyvoX",
            },
        ],
        "AI / Machine Learning": [
            {
                "title": "Machine Learning for Everybody",
                "description": "Hands-on ML foundations and practical model development.",
                "playlist_url": "https://www.youtube.com/playlist?list=PLblh5JKOoLUICTaGLRoHQDuF_7q2GfuJF",
            },
        ],
        "Data Analysis": [
            {
                "title": "Data Analysis with Python",
                "description": "Pandas, NumPy, data wrangling and visualization.",
                "playlist_url": "https://www.youtube.com/playlist?list=PL-osiE80TeTsWmV9i9c58mdDCSskIFdDS",
            },
            {
                "title": "SQL for Data Analysis",
                "description": "SQL querying, joins and analytics workflows.",
                "playlist_url": "https://www.youtube.com/playlist?list=PLv6MQO1Zzdmq5w4YkdkWyW8AaWatSQ0kX",
            },
        ],
        "Web Development": [
            {
                "title": "Full Stack Web Development",
                "description": "Frontend and backend web app development.",
                "playlist_url": "https://www.youtube.com/playlist?list=PL4cUxeGkcC9gcy9f-VZ6M9Rz7zYqf6gYx",
            },
            {
                "title": "Flask Tutorial",
                "description": "Build dynamic web applications using Flask.",
                "playlist_url": "https://www.youtube.com/playlist?list=PL-osiE80TeTs4UjLw5MM6OjgkjFeUxCYH",
            },
        ],
        "DevOps": [
            {
                "title": "Docker and Kubernetes",
                "description": "Container orchestration and deployment practices.",
                "playlist_url": "https://www.youtube.com/playlist?list=PLy7NrYWoggjwPggqtFsI_zMAwvG0SqYCb",
            },
        ],
    }

    for category_name, courses in catalog.items():
        category = CourseCategory(name=category_name, description=f"Top curated playlists for {category_name}.")
        db.session.add(category)
        db.session.flush()

        for item in courses:
            db.session.add(
                Course(
                    category_id=category.id,
                    title=item["title"],
                    description=item["description"],
                    playlist_url=item["playlist_url"],
                )
            )

    db.session.commit()


def ensure_requested_courses():
    requested_courses = [
        {
            "category_name": "Programming Languages",
            "title": "Python Programming Course",
            "description": "User-requested YouTube course video.",
            "playlist_url": "https://www.youtube.com/watch?v=qQEigNVHlX8&t=793s",
        },
        {
            "category_name": "DevOps",
            "title": "Azure DevOps Course (Requested)",
            "description": "User-requested Azure DevOps YouTube playlist.",
            "playlist_url": "https://www.youtube.com/watch?v=A_N5oHwwmTQ&list=PLl4APkPHzsUXseJO1a03CtfRDzr2hivbD",
        },
        {
            "category_name": "Web Development",
            "title": "Web Development Course (Requested)",
            "description": "User-requested web development YouTube playlist.",
            "playlist_url": "https://www.youtube.com/watch?v=tVzUXW6siu0&list=PLu0W_9lII9agq5TrH9XLIKQvv0iaF2X3w",
        },
        {
            "category_name": "Software Engineering",
            "title": "Java DSA & Interview Preparation",
            "description": "Comprehensive Java + data structures and algorithms playlist.",
            "playlist_url": "https://www.youtube.com/playlist?list=PLfqMhTWNBTe0b2nM6JHVCnAkhQRGiZMSJ",
        },
        {
            "category_name": "Software Engineering",
            "title": "Git and GitHub Full Course",
            "description": "Version control fundamentals and practical GitHub workflow.",
            "playlist_url": "https://www.youtube.com/watch?v=RGOj5yH7evk",
        },
        {
            "category_name": "Programming Languages",
            "title": "C++ Programming Full Course",
            "description": "C++ fundamentals, OOP, STL, and problem solving for beginners.",
            "playlist_url": "https://www.youtube.com/watch?v=vLnPwxZdW4Y",
        },
        {
            "category_name": "Programming Languages",
            "title": "Java Programming Full Course",
            "description": "Java basics to advanced concepts with hands-on coding examples.",
            "playlist_url": "https://www.youtube.com/watch?v=eIrMbAQSU34",
        },
        {
            "category_name": "Programming Languages",
            "title": "JavaScript Full Course",
            "description": "Learn JavaScript from basics to advanced with practical projects.",
            "playlist_url": "https://www.youtube.com/watch?v=PkZNo7MFNFg",
        },
        {
            "category_name": "AI / Machine Learning",
            "title": "Neural Networks from Scratch",
            "description": "Practical deep learning implementation walkthrough.",
            "playlist_url": "https://www.youtube.com/watch?v=Wo5dMEP_BbI",
        },
        {
            "category_name": "AI / Machine Learning",
            "title": "Deep Learning Course (Requested)",
            "description": "User-requested deep learning YouTube lecture.",
            "playlist_url": "https://www.youtube.com/watch?v=VyWAvY2CF9c",
        },
        {
            "category_name": "AI / Machine Learning",
            "title": "LangChain + RAG Crash Course",
            "description": "Build AI apps using LLM orchestration and retrieval pipelines.",
            "playlist_url": "https://www.youtube.com/watch?v=lG7Uxts9SXs",
        },
        {
            "category_name": "Data Analysis",
            "title": "Power BI Full Course",
            "description": "Dashboards, data modeling, and reporting in Power BI.",
            "playlist_url": "https://www.youtube.com/watch?v=e6QD8lP-m6E",
        },
        {
            "category_name": "Data Analysis",
            "title": "Statistics for Data Science",
            "description": "Statistics concepts for analytics and ML workflows.",
            "playlist_url": "https://www.youtube.com/watch?v=xxpc-HPKN28",
        },
        {
            "category_name": "Web Development",
            "title": "React JS Course (Requested)",
            "description": "User-requested React YouTube playlist.",
            "playlist_url": "https://www.youtube.com/watch?v=vz1RlUyrc3w&list=PLu71SKxNbfoDqgPchmvIsL4hTnJIrtige",
        },
        {
            "category_name": "Web Development",
            "title": "Node.js + Express Backend Course",
            "description": "Create backend APIs with Node.js, Express, and MongoDB basics.",
            "playlist_url": "https://www.youtube.com/watch?v=Oe421EPjeBE",
        },
        {
            "category_name": "DevOps",
            "title": "Jenkins CI/CD Tutorial",
            "description": "CI/CD pipeline setup with Jenkins from basics to deployment.",
            "playlist_url": "https://www.youtube.com/watch?v=7KCS70sCoK0",
        },
        {
            "category_name": "DevOps",
            "title": "Terraform Full Course",
            "description": "Infrastructure as code with Terraform and cloud examples.",
            "playlist_url": "https://www.youtube.com/watch?v=7xngnjfIlK4",
        },
        {
            "category_name": "Cybersecurity",
            "title": "Cybersecurity Full Course",
            "description": "Security fundamentals, threats, and defensive best practices.",
            "playlist_url": "https://www.youtube.com/watch?v=inWWhr5tnEA",
        },
        {
            "category_name": "Cybersecurity",
            "title": "Ethical Hacking for Beginners",
            "description": "Beginner-friendly ethical hacking and penetration testing intro.",
            "playlist_url": "https://www.youtube.com/watch?v=3Kq1MIfTWCE",
        },
        {
            "category_name": "Mobile Development",
            "title": "Flutter App Development Course",
            "description": "Build Android/iOS apps using Flutter and Dart.",
            "playlist_url": "https://www.youtube.com/watch?v=VPvVD8t02U8",
        },
        {
            "category_name": "Mobile Development",
            "title": "Android Development with Kotlin",
            "description": "Modern Android app development fundamentals with Kotlin.",
            "playlist_url": "https://www.youtube.com/watch?v=F9UC9DY-vIU",
        },
        {
            "category_name": "Backend Development",
            "title": "Backend Development Full Course",
            "description": "Backend architecture, APIs, databases, and deployment fundamentals.",
            "playlist_url": "https://www.youtube.com/watch?v=1oTuMPIwHmk",
        },
        {
            "category_name": "Backend Development",
            "title": "Node.js and Express Full Course",
            "description": "Build production backend services with Node.js and Express.",
            "playlist_url": "https://www.youtube.com/watch?v=Oe421EPjeBE",
        },
        {
            "category_name": "Backend Development",
            "title": "Django Full Course",
            "description": "Complete Django backend web development tutorial.",
            "playlist_url": "https://www.youtube.com/watch?v=F5mRW0jo-U4",
        },
        {
            "category_name": "Backend Development",
            "title": "Flask Full Course",
            "description": "Flask fundamentals for building backend web applications.",
            "playlist_url": "https://www.youtube.com/watch?v=Z1RJmh_OqeA",
        },
        {
            "category_name": "Backend Development",
            "title": "FastAPI Full Course",
            "description": "Build high-performance Python APIs using FastAPI.",
            "playlist_url": "https://www.youtube.com/watch?v=0sOvCWFmrtA",
        },
        {
            "category_name": "Backend Development",
            "title": "Spring Boot Full Course",
            "description": "Java backend development with Spring Boot and REST APIs.",
            "playlist_url": "https://www.youtube.com/watch?v=9SGDpanrc8U",
        },
        {
            "category_name": "Backend Development",
            "title": "ASP.NET Core Web API Course",
            "description": ".NET backend API development with ASP.NET Core.",
            "playlist_url": "https://www.youtube.com/watch?v=fmvcAzHpsk8",
        },
        {
            "category_name": "Backend Development",
            "title": "SQL for Backend Developers",
            "description": "SQL essentials for backend and API-driven applications.",
            "playlist_url": "https://www.youtube.com/watch?v=HXV3zeQKqGY",
        },
        {
            "category_name": "Backend Development",
            "title": "MongoDB Course for Beginners",
            "description": "NoSQL database fundamentals for backend projects.",
            "playlist_url": "https://www.youtube.com/watch?v=ExcRbA7fy_A",
        },
    ]

    for item in requested_courses:
        category = CourseCategory.query.filter_by(name=item["category_name"]).first()
        if not category:
            category = CourseCategory(
                name=item["category_name"],
                description=f"Top curated playlists for {item['category_name']}.",
            )
            db.session.add(category)
            db.session.flush()

        exists = Course.query.filter_by(playlist_url=item["playlist_url"]).first()
        if exists:
            # Keep requested playlists aligned to intended categories/titles on existing databases.
            exists.category_id = category.id
            exists.title = item["title"]
            exists.description = item["description"]
            continue

        db.session.add(
            Course(
                category_id=category.id,
                title=item["title"],
                description=item["description"],
                playlist_url=item["playlist_url"],
            )
        )

    db.session.commit()


def remove_retired_requested_courses():
    retired_urls = [
        "https://www.youtube.com/playlist?list=PLjVLYmrlmjGdRs1sGqRrTE-EMraLclJga",
        "https://www.youtube.com/playlist?list=PLQ4bwxL7hYl4f4wP3M7Jin7M8hQd5a7rD",
    ]
    Course.query.filter(Course.playlist_url.in_(retired_urls)).delete(synchronize_session=False)
    db.session.commit()


def ensure_default_learning_resources():
    default_resources = [
        {
            "title": "Full Stack Web Development PDF",
            "description": "Roadmap guide for learners interested in full-stack development.",
            "resource_type": "PDF",
            "file_path": "roadmaps/full-stack-web-development-roadmap.pdf",
            "external_url": None,
        },
        {
            "title": "Non-Coding Careers PDF",
            "description": "Career roadmap and guidance for non-coding fields.",
            "resource_type": "PDF",
            "file_path": "roadmaps/non-coding-careers.pdf",
            "external_url": None,
        },
        {
            "title": "How to Become a Data Analyst (PDF)",
            "description": "Step-by-step guide and daily routine for aspiring data analysts.",
            "resource_type": "PDF",
            "file_path": "roadmaps/how-to-become-data-analyst.pdf",
            "external_url": None,
        },
        {
            "title": "How to Become an AI Engineer (PDF)",
            "description": "CampusX masterlist guide to build your AI Engineer roadmap.",
            "resource_type": "PDF",
            "file_path": "roadmaps/how-to-become-ai-engineer.pdf",
            "external_url": None,
        },
    ]

    added_any = False
    for item in default_resources:
        file_exists = os.path.exists(os.path.join(app.static_folder, item["file_path"]))
        if not file_exists:
            continue

        exists = LearningResource.query.filter_by(
            title=item["title"],
            resource_type=item["resource_type"],
            file_path=item["file_path"],
        ).first()
        if exists:
            continue

        db.session.add(
            LearningResource(
                title=item["title"],
                description=item["description"],
                resource_type=item["resource_type"],
                file_path=item["file_path"],
                external_url=item["external_url"],
                created_by="system",
            )
        )
        added_any = True

    if added_any:
        db.session.commit()


def get_user_progress_map(user_id):
    rows = UserCourseProgress.query.filter_by(user_id=user_id).all()
    return {row.course_id: row for row in rows}


def get_user_course_completion_stats(user_id):
    total_courses = Course.query.count()
    completed_courses = (
        db.session.query(func.count(UserCourseProgress.id))
        .filter(UserCourseProgress.user_id == user_id, UserCourseProgress.completed.is_(True))
        .scalar()
    )
    is_all_completed = total_courses > 0 and completed_courses == total_courses
    return completed_courses, total_courses, is_all_completed


def get_fallback_mock_test_questions(subject_name, question_count=20):
    subject_topics = {
        "Programming Languages": [
            "static vs dynamic typing",
            "memory management and garbage collection",
            "compilation and runtime execution",
            "object-oriented programming",
            "functional programming basics",
            "error handling patterns",
            "collections and iterators",
            "asynchronous programming",
            "immutability and mutability",
            "language-specific tooling",
            "modules and package management",
            "testing in language ecosystems",
        ],
        "Backend Development": [
            "REST API design",
            "authentication and authorization",
            "database indexing",
            "caching strategies",
            "idempotent HTTP methods",
            "query optimization",
            "connection pooling",
            "service-level error handling",
            "API versioning",
            "message queues and async jobs",
            "rate limiting",
            "backend performance profiling",
        ],
        "Web Development": [
            "semantic HTML",
            "CSS layout systems",
            "responsive design",
            "browser rendering pipeline",
            "DOM events and propagation",
            "state management in UI apps",
            "accessibility basics",
            "frontend build tools",
            "client-side routing",
            "CORS in browsers",
            "SSR vs CSR",
            "web performance optimization",
        ],
        "AI / Machine Learning": [
            "supervised learning",
            "model overfitting and underfitting",
            "feature engineering",
            "model evaluation metrics",
            "train-validation-test split",
            "gradient-based optimization",
            "classification vs regression",
            "embeddings and vector search",
            "RAG systems",
            "inference pipelines",
            "model drift",
            "hyperparameter tuning",
        ],
        "Data Analysis": [
            "SQL joins and aggregation",
            "data cleaning workflows",
            "handling missing values",
            "outlier treatment",
            "descriptive statistics",
            "dashboard design",
            "time-series trend analysis",
            "A/B experiment basics",
            "data visualization choices",
            "pandas groupby operations",
            "correlation analysis",
            "business KPI reporting",
        ],
        "DevOps": [
            "CI pipeline checks",
            "CD deployment strategies",
            "containerization concepts",
            "Kubernetes basics",
            "infrastructure as code",
            "monitoring metrics and alerts",
            "centralized logging",
            "blue-green deployment",
            "secret management",
            "incident rollback planning",
            "build artifact versioning",
            "environment configuration management",
        ],
        "Cybersecurity": [
            "least privilege principle",
            "multi-factor authentication",
            "secure password storage",
            "SQL injection prevention",
            "XSS mitigation",
            "secure transport with TLS",
            "vulnerability patching",
            "incident response containment",
            "phishing defense awareness",
            "secure coding practices",
            "token/session security",
            "threat modeling",
        ],
        "Mobile Development": [
            "mobile lifecycle management",
            "state management in mobile apps",
            "offline-first data sync",
            "battery/performance optimization",
            "push notification systems",
            "API integration and retries",
            "mobile UI responsiveness",
            "runtime permissions",
            "app release process",
            "mobile testing strategy",
            "secure local storage",
            "error tracking on devices",
        ],
        "Software Engineering": [
            "SOLID design principles",
            "code review process",
            "unit vs integration testing",
            "agile sprint planning",
            "technical debt management",
            "system design trade-offs",
            "version control workflows",
            "requirements engineering",
            "quality gates in CI",
            "refactoring practices",
            "observability in production",
            "scalability planning",
        ],
    }

    generic_topics = [
        "core programming fundamentals",
        "API communication basics",
        "debugging strategy",
        "time complexity awareness",
        "test automation mindset",
        "deployment readiness checks",
        "error monitoring setup",
        "secure coding foundations",
        "data modeling trade-offs",
        "performance optimization",
        "code maintainability",
        "team collaboration workflow",
    ]

    if subject_name == "All Subjects":
        topic_pool = []
        for sub_name, topics in subject_topics.items():
            topic_pool.extend([(sub_name, topic) for topic in topics[:2]])
        topic_pool.extend([("All Subjects", topic) for topic in generic_topics])
    else:
        specific_topics = subject_topics.get(subject_name, [])
        if specific_topics:
            topic_pool = [(subject_name, topic) for topic in specific_topics]
        else:
            topic_pool = [(subject_name, topic) for topic in generic_topics]

    if not topic_pool:
        return []

    target_count = max(5, int(question_count))
    selected_topics = []
    while len(selected_topics) < target_count:
        pool_copy = list(topic_pool)
        random.shuffle(pool_copy)
        selected_topics.extend(pool_copy)
    selected_topics = selected_topics[:target_count]

    question_stems = [
        "Which statement best describes",
        "What is the primary goal of",
        "Which option is most accurate about",
        "In practice, what does",
        "Which statement is correct regarding",
    ]

    prepared = []
    for idx, (subject_label, topic) in enumerate(selected_topics, start=1):
        positive_statement = (
            f"It is a core concept in {subject_label} used to build robust solutions."
            if subject_label != "All Subjects"
            else "It is a core software engineering concept used in real projects."
        )
        options = [
            positive_statement,
            "It is mainly a graphic design rule for selecting color palettes.",
            "It is only related to physical hardware assembly processes.",
            "It is a social media marketing trick with no engineering relevance.",
        ]
        shift = idx % 4
        rotated_options = options[shift:] + options[:shift]
        answer_index = (0 - shift) % 4

        stem = question_stems[(idx - 1) % len(question_stems)]
        prepared.append(
            {
                "id": f"q{idx}",
                "question": f"[{subject_label}] {stem} {topic}?",
                "options": rotated_options,
                "answer_index": answer_index,
                "topic": topic,
            }
        )

    return prepared


def is_subject_specific_question_set(subject_name, questions):
    if subject_name == "All Subjects":
        return True

    keyword_map = {
        "Programming Languages": ["python", "java", "c++", "javascript", "typing", "compiler", "runtime"],
        "Backend Development": ["api", "rest", "database", "query", "jwt", "http", "backend"],
        "Web Development": ["html", "css", "browser", "dom", "frontend", "cors", "responsive"],
        "AI / Machine Learning": ["model", "learning", "embedding", "gradient", "classification", "rag"],
        "Data Analysis": ["sql", "pandas", "dashboard", "kpi", "analysis", "statistics"],
        "DevOps": ["ci", "cd", "docker", "kubernetes", "terraform", "deployment"],
        "Cybersecurity": ["security", "xss", "injection", "tls", "mfa", "threat"],
        "Mobile Development": ["mobile", "android", "ios", "flutter", "permission", "lifecycle"],
        "Software Engineering": ["solid", "testing", "agile", "refactoring", "design", "technical debt"],
    }

    keywords = keyword_map.get(subject_name, [])
    if not keywords:
        return True

    hits = 0
    for item in questions:
        text = f"{item.get('question', '')} {item.get('topic', '')}".lower()
        if any(key in text for key in keywords):
            hits += 1

    return hits >= max(3, len(questions) // 3)


def build_roadmap(current_level, target_career, daily_study_time):
    months = 3 if daily_study_time >= 4 else 4 if daily_study_time >= 2 else 6

    career_map = {
        "AI Engineer": {
            "core_topics": [
                "Python and data structures",
                "Math for ML (linear algebra, probability)",
                "Machine learning algorithms",
                "Deep learning and model deployment",
            ],
            "projects": [
                "Build a sentiment analysis app",
                "Create an image classifier",
                "Deploy an ML model as an API",
            ],
            "preferred_categories": ["AI / Machine Learning", "Data Analysis", "Software Engineering"],
        },
        "Data Analyst": {
            "core_topics": [
                "Excel and SQL foundations",
                "Data cleaning with Python",
                "Visualization and dashboards",
                "Business analytics storytelling",
            ],
            "projects": [
                "Sales dashboard with KPI tracking",
                "Customer churn analysis report",
                "Public dataset exploratory analysis",
            ],
            "preferred_categories": ["Data Analysis", "Software Engineering", "Web Development"],
        },
        "Software Developer": {
            "core_topics": [
                "Programming fundamentals",
                "Version control and testing",
                "Backend development",
                "System design and deployment",
            ],
            "projects": [
                "Build a REST API",
                "Build a full-stack web app",
                "Deploy app with CI/CD pipeline",
            ],
            "preferred_categories": ["Software Engineering", "Web Development", "DevOps"],
        },
    }

    level_multiplier = {"Beginner": 1, "Intermediate": 2, "Advanced": 3}.get(current_level, 1)
    profile = career_map.get(target_career)
    if not profile:
        generic_topics = [
            f"Foundations for {target_career}",
            "Core programming and problem solving",
            "Project building and portfolio",
            "Interview prep and mock tests",
        ]
        profile = {
            "core_topics": generic_topics,
            "projects": [
                f"Build a beginner {target_career} project",
                f"Create a portfolio-ready {target_career} case study",
                "Ship and document a capstone project",
            ],
            "preferred_categories": ["Software Engineering", "Web Development", "Data Analysis"],
        }

    recommended_courses = []
    for category_name in profile["preferred_categories"]:
        category = CourseCategory.query.filter_by(name=category_name).first()
        if category:
            sample_courses = Course.query.filter_by(category_id=category.id).limit(level_multiplier).all()
            recommended_courses.extend([c.title for c in sample_courses])

    roadmap = []
    topic_list = profile["core_topics"]

    for month in range(1, months + 1):
        topic_index = min(month - 1, len(topic_list) - 1)
        project_index = min(month - 1, len(profile["projects"]) - 1)

        roadmap.append(
            {
                "month": month,
                "focus_topic": topic_list[topic_index],
                "recommended_courses": recommended_courses[max(0, month - 1): month + 2],
                "project": profile["projects"][project_index],
                "weekly_hours": daily_study_time * 7,
            }
        )

    return roadmap


def get_env_var_with_windows_fallback(var_name):
    value = os.environ.get(var_name, "").strip()
    if value:
        return value

    # setx writes to Windows user environment (registry); this fallback avoids requiring IDE restart.
    if os.name == "nt":
        try:
            import winreg

            key_path = "Environment"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as env_key:
                registry_value, _ = winreg.QueryValueEx(env_key, var_name)
                return str(registry_value).strip()
        except Exception:
            return ""

    return ""


def get_openai_client_and_model():
    api_key = (
        get_env_var_with_windows_fallback("OPENAI_API_KEY")
        or get_env_var_with_windows_fallback("OPENROUTER_API_KEY")
    )
    if not api_key:
        return None, None

    timeout_seconds = get_env_var_with_windows_fallback("OPENAI_TIMEOUT_SECONDS") or "30"
    try:
        timeout_seconds = float(timeout_seconds)
        if timeout_seconds <= 0:
            timeout_seconds = 30.0
    except (TypeError, ValueError):
        timeout_seconds = 30.0

    if api_key.startswith("sk-or-"):
        client = OpenAI(
            api_key=api_key,
            base_url=get_env_var_with_windows_fallback("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1",
            timeout=timeout_seconds,
            max_retries=1,
        )
        model = get_env_var_with_windows_fallback("OPENAI_MODEL") or "openai/gpt-4o-mini"
    else:
        client = OpenAI(
            api_key=api_key,
            timeout=timeout_seconds,
            max_retries=1,
        )
        model = get_env_var_with_windows_fallback("OPENAI_MODEL") or "gpt-4o-mini"

    return client, model


def extract_json_object(raw_text):
    text = (raw_text or "").strip()
    if not text:
        return None

    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start: end + 1]

    try:
        return json.loads(text)
    except Exception:
        return None


def generate_ai_mock_test_questions(subject_name, question_count=20):
    client, model = get_openai_client_and_model()
    if not client:
        return get_fallback_mock_test_questions(subject_name, question_count), False

    prompt = (
        "You are an AI technical interviewer. Generate a mock test in strict JSON.\n"
        "Return ONLY valid JSON object with this shape:\n"
        "{"
        "\"questions\": ["
        "{\"question\":\"...\",\"options\":[\"A\",\"B\",\"C\",\"D\"],\"answer_index\":0,\"topic\":\"...\"}"
        "]"
        "}\n"
        f"Create exactly {question_count} multiple-choice questions for subject: {subject_name}.\n"
        "Rules: 4 options each, exactly one correct answer, answer_index must be 0..3, concise questions.\n"
        "Questions must be specific to the selected subject. Avoid repeated generic questions."
    )

    try:
        response = client.responses.create(
            model=model,
            input=prompt,
            max_output_tokens=min(5000, max(1200, question_count * 140)),
        )
        data = extract_json_object(response.output_text)
    except Exception:
        data = None

    if not isinstance(data, dict):
        retry_prompt = prompt + "\nReturn JSON only (no markdown, no commentary)."
        try:
            retry_response = client.responses.create(
                model=model,
                input=retry_prompt,
                max_output_tokens=min(5000, max(1200, question_count * 140)),
            )
            data = extract_json_object(retry_response.output_text)
        except Exception:
            data = None

    raw_questions = data.get("questions") if isinstance(data, dict) else None
    if not isinstance(raw_questions, list):
        return get_fallback_mock_test_questions(subject_name, question_count), False

    cleaned = []
    for idx, row in enumerate(raw_questions, start=1):
        if not isinstance(row, dict):
            continue
        question = str(row.get("question", "")).strip()
        options = row.get("options")
        answer_index = row.get("answer_index")
        topic = str(row.get("topic", "General")).strip() or "General"
        if not question or not isinstance(options, list) or len(options) != 4:
            continue
        if not isinstance(answer_index, int) or answer_index < 0 or answer_index > 3:
            continue
        option_values = [str(opt).strip() for opt in options]
        if any(not opt for opt in option_values):
            continue

        cleaned.append(
            {
                "id": f"q{idx}",
                "question": question,
                "options": option_values,
                "answer_index": answer_index,
                "topic": topic,
            }
        )

    if len(cleaned) < 5:
        return get_fallback_mock_test_questions(subject_name, question_count), False

    if not is_subject_specific_question_set(subject_name, cleaned):
        return get_fallback_mock_test_questions(subject_name, question_count), False

    return cleaned[:question_count], True


def generate_ai_interviewer_feedback(subject_name, score, correct_answers, total_questions, weak_topics):
    client, model = get_openai_client_and_model()
    if not client:
        weak_text = ", ".join(sorted(set(weak_topics))) if weak_topics else "general revision"
        return (
            f"Interviewer feedback: You scored {score}%. "
            f"Strengthen these areas next: {weak_text}."
        )

    weak_text = ", ".join(sorted(set(weak_topics))) if weak_topics else "none"
    prompt = (
        "You are an AI technical interviewer giving concise result feedback.\n"
        f"Subject: {subject_name}\n"
        f"Score: {score}% ({correct_answers}/{total_questions})\n"
        f"Weak topics: {weak_text}\n"
        "Give a short feedback with: strengths, weaknesses, and a 7-day improvement plan in 4-6 bullet points."
    )
    try:
        response = client.responses.create(model=model, input=prompt, max_output_tokens=350)
        return (response.output_text or "").strip() or "Feedback unavailable."
    except Exception:
        return (
            f"Interviewer feedback: You scored {score}%. "
            f"Revise these topics: {weak_text if weak_text != 'none' else 'core concepts and practice questions'}."
        )


def get_ai_learning_help(user_question, force_refresh=False):
    client, model = get_openai_client_and_model()
    if not client:
        return (
            "AI support is enabled, but API key is missing. "
            "Set OPENAI_API_KEY or OPENROUTER_API_KEY in environment variables."
        )

    cleaned_question = " ".join((user_question or "").split())
    if not cleaned_question:
        return "Please enter a clearer question."
    cleaned_question = cleaned_question[:700]

    cache_key = cleaned_question.lower()
    now = time.time()
    cached = ai_assistant_response_cache.get(cache_key)
    if (not force_refresh) and cached and (now - cached["ts"]) <= AI_ASSISTANT_CACHE_TTL_SECONDS:
        return cached["answer"]
    if force_refresh:
        ai_assistant_response_cache.pop(cache_key, None)

    token_limit_raw = get_env_var_with_windows_fallback("OPENAI_ASSISTANT_MAX_OUTPUT_TOKENS") or "180"
    timeout_raw = get_env_var_with_windows_fallback("OPENAI_ASSISTANT_TIMEOUT_SECONDS") or "12"
    try:
        token_limit = int(token_limit_raw)
    except (TypeError, ValueError):
        token_limit = 180
    token_limit = max(80, min(token_limit, 280))

    try:
        fast_timeout = float(timeout_raw)
    except (TypeError, ValueError):
        fast_timeout = 12.0
    fast_timeout = max(4.0, min(fast_timeout, 20.0))

    try:
        response = client.with_options(timeout=fast_timeout, max_retries=0).responses.create(
            model=model,
            input=(
                "You are a fast learning assistant for a course platform. "
                "Reply in short practical bullets and keep it concise.\n\n"
                f"User question: {cleaned_question}"
            ),
            max_output_tokens=token_limit,
        )
        answer = (response.output_text or "").strip() or "No answer generated. Please try a clearer question."
        ai_assistant_response_cache[cache_key] = {"answer": answer, "ts": now}

        expired_keys = [
            key
            for key, payload in ai_assistant_response_cache.items()
            if (now - payload["ts"]) > AI_ASSISTANT_CACHE_TTL_SECONDS
        ]
        for key in expired_keys:
            ai_assistant_response_cache.pop(key, None)

        if len(ai_assistant_response_cache) > AI_ASSISTANT_CACHE_MAX_ENTRIES:
            oldest_key = min(ai_assistant_response_cache.items(), key=lambda item: item[1]["ts"])[0]
            ai_assistant_response_cache.pop(oldest_key, None)

        return answer
    except Exception:
        return (
            "Quick fallback:\n"
            "1. Set a clear 2-4 week goal.\n"
            "2. Study 1 core topic daily with 30-60 minutes practice.\n"
            "3. Build one small project each week.\n"
            "4. Revise mistakes and keep a short notes file.\n"
            "5. Share progress publicly for accountability."
        )


@app.teardown_request
def rollback_on_error(exception=None):
    if exception is not None:
        db.session.rollback()


@app.route("/healthz")
def healthz():
    try:
        db.session.execute(text("SELECT 1"))
        return {"status": "ok", "database": "up", "time": datetime.utcnow().isoformat() + "Z"}, 200
    except Exception:
        db.session.rollback()
        return {"status": "degraded", "database": "down"}, 503


@app.route("/")
def index():
    return redirect(url_for("dashboard"))


@app.route("/register", methods=["GET", "POST"])
def register():
    flash("Register page has been removed.", "info")
    return redirect(url_for("dashboard"))


@app.route("/login", methods=["GET", "POST"])
def login():
    flash("Login page has been removed.", "info")
    return redirect(url_for("dashboard"))


@app.route("/admin/unlock", methods=["GET", "POST"])
def admin_unlock():
    next_url = request.args.get("next") or request.form.get("next") or url_for("admin_courses")
    if not is_safe_next_url(next_url):
        next_url = url_for("admin_courses")

    if can_access_admin_panel():
        return redirect(next_url)

    configured_token = get_admin_access_token()
    if request.method == "POST":
        token_input = request.form.get("admin_token", "").strip()

        if configured_token and token_input and secrets.compare_digest(token_input, configured_token):
            unlock_admin_session()
            flash("Admin access unlocked.", "success")
            return redirect(next_url)

        if configured_token or token_input:
            flash("Invalid admin token.", "danger")

    return render_template(
        "admin_unlock.html",
        next_url=next_url,
        admin_token_configured=bool(configured_token),
    )


@app.route("/admin/lock", methods=["POST"])
def admin_lock():
    lock_admin_session()
    flash("Admin access locked.", "info")
    return redirect(url_for("dashboard"))


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Logged out successfully.", "info")
    return redirect(url_for("dashboard"))


@app.route("/dashboard")
def dashboard():
    categories = CourseCategory.query.order_by(CourseCategory.name.asc()).all()
    selected_resource_filter = request.args.get("resource_type", "All").strip()
    if selected_resource_filter not in RESOURCE_FILTER_OPTIONS:
        selected_resource_filter = "All"

    resource_search_query = sanitize_text(request.args.get("resource_q", ""), 120)
    resource_sort = request.args.get("resource_sort", "newest").strip().lower()
    if resource_sort not in RESOURCE_SORT_OPTIONS:
        resource_sort = "newest"

    resources_query = LearningResource.query
    if selected_resource_filter != "All":
        resources_query = resources_query.filter_by(resource_type=selected_resource_filter)
    if resource_search_query:
        like_pattern = f"%{resource_search_query}%"
        resources_query = resources_query.filter(
            (LearningResource.title.ilike(like_pattern)) | (LearningResource.description.ilike(like_pattern))
        )

    sort_map = {
        "newest": LearningResource.created_at.desc(),
        "oldest": LearningResource.created_at.asc(),
        "title": LearningResource.title.asc(),
    }
    base_sort_key = resource_sort if resource_sort in sort_map else "newest"
    resources_query = resources_query.order_by(sort_map.get(base_sort_key, LearningResource.created_at.desc()))

    total_courses = Course.query.count()
    weekly_progress_chart = {"labels": [], "daily": [], "cumulative": []}
    goal_preference = None
    weekly_goal = DEFAULT_WEEKLY_GOAL
    weekly_goal_progress = {"count": 0, "goal": DEFAULT_WEEKLY_GOAL, "percent": 0}
    learning_streak = 0
    next_action_course = None
    next_action_reason = ""
    goal_filter_active = False
    if current_user.is_authenticated:
        completed_courses, total_courses, _ = get_user_course_completion_stats(current_user.id)
        overall_progress = round((completed_courses / total_courses) * 100, 2) if total_courses else 0
        user_progress_map = get_user_progress_map(current_user.id)
        weekly_progress_chart = get_weekly_progress_chart(current_user.id)
        goal_preference = get_user_preferences(current_user.id)
        if goal_preference and goal_preference.weekly_goal:
            weekly_goal = goal_preference.weekly_goal
        weekly_goal_progress = get_weekly_goal_progress(current_user.id, weekly_goal)
        learning_streak = get_learning_streak(current_user.id)
        latest_roadmap = (
            Roadmap.query.filter_by(user_id=current_user.id)
            .order_by(Roadmap.created_at.desc())
            .first()
        )
        latest_mock_test = (
            MockTestAttempt.query.filter_by(user_id=current_user.id)
            .order_by(MockTestAttempt.created_at.desc())
            .first()
        )
    else:
        completed_courses = 0
        overall_progress = 0
        user_progress_map = {}
        latest_roadmap = None
        latest_mock_test = None

    goal_role = goal_preference.target_role if goal_preference else ""
    goal_prompt_needed = current_user.is_authenticated and not goal_role
    goal_toggle = request.args.get("goal", "on").strip().lower()
    goal_filter_active = bool(goal_role) and goal_toggle != "off"

    prefetch_limit = 60 if resource_sort in {"popular", "shortest"} else 30

    if goal_filter_active:
        keywords = extract_goal_keywords(goal_role)
        goal_filtered_query = apply_keyword_filter(
            resources_query,
            keywords,
            LearningResource.title,
            LearningResource.description,
        )
        learning_resources = goal_filtered_query.limit(prefetch_limit).all()
        if not learning_resources:
            goal_filter_active = False
            learning_resources = resources_query.limit(prefetch_limit).all()
    else:
        learning_resources = resources_query.limit(prefetch_limit).all()

    if resource_sort == "popular":
        counts = get_resource_popularity_map([resource.id for resource in learning_resources])
        learning_resources.sort(key=lambda resource: counts.get(resource.id, 0), reverse=True)
    elif resource_sort == "shortest":
        learning_resources.sort(key=get_resource_size_hint)

    learning_resources = learning_resources[:30]

    last_course_id = session.get("last_course_id")
    if last_course_id:
        last_course = Course.query.get(last_course_id)
        if last_course:
            if current_user.is_authenticated:
                progress_item = user_progress_map.get(last_course.id)
                if not (progress_item and progress_item.completed):
                    next_action_course = last_course
                    next_action_reason = "Continue your last lesson"
            else:
                next_action_course = last_course
                next_action_reason = "Continue your last lesson"

    if current_user.is_authenticated and not next_action_course:
        completed_ids = {
            p.course_id
            for p in UserCourseProgress.query.filter_by(user_id=current_user.id, completed=True).all()
        }
        recommended = get_recommended_courses_for_goal(completed_ids, goal_role, limit=1)
        if not recommended:
            recommended = (
                Course.query.filter(~Course.id.in_(completed_ids))
                .order_by(Course.title.asc())
                .limit(1)
                .all()
            )
        if recommended:
            next_action_course = recommended[0]
            next_action_reason = "Suggested next course"

    category_progress = []

    for category in categories:
        category_total = len(category.courses)
        category_completed = 0

        for course in category.courses:
            progress_item = user_progress_map.get(course.id)
            if progress_item and progress_item.completed:
                category_completed += 1

        progress_percent = round((category_completed / category_total) * 100, 2) if category_total else 0
        category_progress.append(
            {
                "category": category,
                "total": category_total,
                "completed": category_completed,
                "progress": progress_percent,
            }
        )

    return render_template(
        "dashboard.html",
        categories=categories,
        overall_progress=overall_progress,
        completed_courses=completed_courses,
        total_courses=total_courses,
        category_progress=category_progress,
        latest_roadmap=latest_roadmap,
        latest_mock_test=latest_mock_test,
        weekly_progress_chart=weekly_progress_chart,
        weekly_goal_progress=weekly_goal_progress,
        learning_streak=learning_streak,
        learning_resources=learning_resources,
        selected_resource_filter=selected_resource_filter,
        resource_filter_options=RESOURCE_FILTER_OPTIONS,
        resource_sort_options=RESOURCE_SORT_LABELS,
        resource_search_query=resource_search_query,
        resource_sort=resource_sort,
        goal_preference=goal_preference,
        goal_prompt_needed=goal_prompt_needed,
        goal_filter_active=goal_filter_active,
        next_action_course=next_action_course,
        next_action_reason=next_action_reason,
    )


@app.route("/preferences/goal", methods=["POST"])
@login_required
def save_goal_preferences():
    target_role = normalize_target_role(request.form.get("target_role", ""))
    custom_role = normalize_target_role(request.form.get("custom_target_role", ""))
    if custom_role:
        target_role = custom_role

    weekly_goal = parse_int(
        request.form.get("weekly_goal", DEFAULT_WEEKLY_GOAL),
        DEFAULT_WEEKLY_GOAL,
        min_value=1,
        max_value=MAX_WEEKLY_GOAL,
    )

    if not target_role:
        flash("Please enter a goal role to personalize your plan.", "warning")
        return redirect(url_for("dashboard"))

    if not save_user_preferences(current_user.id, target_role, weekly_goal):
        flash("Unable to save your goal right now.", "danger")
        return redirect(url_for("dashboard"))

    flash("Your learning goal has been saved.", "success")
    next_url = request.form.get("next")
    if is_safe_next_url(next_url):
        return redirect(next_url)
    return redirect(url_for("dashboard"))


@app.route("/courses/<int:category_id>")
def courses(category_id):
    category = CourseCategory.query.get_or_404(category_id)
    search_query = request.args.get("q", "").strip()

    query = Course.query.filter_by(category_id=category.id)
    if search_query:
        like_pattern = f"%{search_query}%"
        query = query.filter((Course.title.ilike(like_pattern)) | (Course.description.ilike(like_pattern)))

    all_courses = query.order_by(Course.title.asc()).all()
    user_progress_map = get_user_progress_map(current_user.id) if current_user.is_authenticated else {}

    return render_template(
        "courses.html",
        category=category,
        courses=all_courses,
        search_query=search_query,
        user_progress_map=user_progress_map,
    )


@app.route("/courses/<int:course_id>/watch")
def watch_course(course_id):
    course = Course.query.get_or_404(course_id)
    embed_url = build_youtube_embed_url(course.playlist_url)
    progress_item = None
    if current_user.is_authenticated:
        progress_item = UserCourseProgress.query.filter_by(user_id=current_user.id, course_id=course.id).first()
        if progress_item is None:
            progress_item = UserCourseProgress(user_id=current_user.id, course_id=course.id, completed=False)
            db.session.add(progress_item)
            commit_or_rollback()
    session["last_course_id"] = course.id
    return render_template(
        "watch_course.html",
        course=course,
        embed_url=embed_url,
        progress_item=progress_item,
    )

@app.route("/courses/<int:course_id>/toggle-complete", methods=["POST"])
@login_required
def toggle_course_completion(course_id):
    course = Course.query.get_or_404(course_id)

    progress_item = UserCourseProgress.query.filter_by(user_id=current_user.id, course_id=course.id).first()
    if progress_item is None:
        progress_item = UserCourseProgress(user_id=current_user.id, course_id=course.id, completed=False)
        db.session.add(progress_item)

    progress_item.completed = not progress_item.completed
    progress_item.completed_at = datetime.utcnow() if progress_item.completed else None
    if not commit_or_rollback():
        flash("Unable to update course progress right now.", "danger")
        return redirect(url_for("courses", category_id=course.category_id))

    flash_message = "Course marked as completed." if progress_item.completed else "Course marked as not completed."
    flash(flash_message, "success")

    next_url = request.form.get("next")
    if is_safe_next_url(next_url):
        return redirect(next_url)
    return redirect(url_for("courses", category_id=course.category_id))


@app.route("/roadmap", methods=["GET", "POST"])
def roadmap():
    generated = None
    selected_target_career = "Software Developer"
    custom_target_career = ""

    if request.method == "POST":
        current_level = request.form.get("current_level", "Beginner")
        selected_target_career = request.form.get("target_career", "Software Developer")
        custom_target_career = sanitize_text(request.form.get("custom_target_career", ""), 80)
        target_career = custom_target_career or selected_target_career
        daily_study_time = parse_int(request.form.get("daily_study_time", 2), 2, min_value=1, max_value=10)

        generated = build_roadmap(current_level, target_career, daily_study_time)
        session["last_roadmap_payload"] = {
            "current_level": current_level,
            "target_career": target_career,
            "daily_study_time": daily_study_time,
            "generated": generated,
            "created_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
        }
        if current_user.is_authenticated:
            roadmap_record = Roadmap(
                user_id=current_user.id,
                current_level=current_level,
                target_career=target_career,
                daily_study_time=daily_study_time,
                content_json=json.dumps(generated),
            )
            db.session.add(roadmap_record)
            if not commit_or_rollback():
                flash("Roadmap generated, but saving failed. Please retry.", "warning")
                return render_template("roadmap.html", generated=generated, saved_roadmaps=[])
            flash("Roadmap generated and saved.", "success")
        else:
            flash("Roadmap generated. Sign in to save roadmap history.", "info")

    saved_roadmaps = []
    if current_user.is_authenticated:
        saved_roadmaps = (
            Roadmap.query.filter_by(user_id=current_user.id)
            .order_by(Roadmap.created_at.desc())
            .all()
        )

    return render_template(
        "roadmap.html",
        generated=generated,
        saved_roadmaps=saved_roadmaps,
        selected_target_career=selected_target_career,
        custom_target_career=custom_target_career,
        target_career_options=["AI Engineer", "Data Analyst", "Software Developer", "Other"],
    )


@app.route("/roadmap/pdf/latest")
def roadmap_pdf_latest():
    payload = session.get("last_roadmap_payload")
    if not payload:
        flash("Generate a roadmap first to download the PDF.", "warning")
        return redirect(url_for("roadmap"))
    lines = build_roadmap_pdf_lines(payload)
    pdf_bytes = build_simple_pdf("Learning Roadmap", lines)
    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name="learning-roadmap.pdf",
    )


@app.route("/roadmaps/<path:filename>")
def roadmap_static_pdf(filename):
    safe_name = (filename or "").strip()
    if not safe_name.lower().endswith(".pdf"):
        abort(404)

    file_path = get_roadmap_pdf_path(safe_name)
    if file_path is None:
        abort(404)

    return send_from_directory(os.path.dirname(file_path), os.path.basename(file_path), as_attachment=False)


@app.route("/roadmap/pdf/<int:roadmap_id>")
@login_required
def roadmap_pdf(roadmap_id):
    roadmap_item = Roadmap.query.filter_by(id=roadmap_id, user_id=current_user.id).first()
    if roadmap_item is None:
        abort(404)
    try:
        generated = json.loads(roadmap_item.content_json)
    except Exception:
        generated = []
    payload = {
        "current_level": roadmap_item.current_level,
        "target_career": roadmap_item.target_career,
        "daily_study_time": roadmap_item.daily_study_time,
        "generated": generated,
    }
    lines = build_roadmap_pdf_lines(payload)
    pdf_bytes = build_simple_pdf("Learning Roadmap", lines)
    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name="learning-roadmap.pdf",
    )


@app.route("/recommendations")
@login_required
def recommendations():
    completed_ids = {
        p.course_id
        for p in UserCourseProgress.query.filter_by(user_id=current_user.id, completed=True).all()
    }

    goal_preference = get_user_preferences(current_user.id)
    goal_role = goal_preference.target_role if goal_preference else ""
    goal_prompt_needed = not goal_role

    base_query = Course.query
    if completed_ids:
        base_query = base_query.filter(~Course.id.in_(completed_ids))

    recommended_query = base_query
    if goal_role:
        keywords = extract_goal_keywords(goal_role)
        recommended_query = apply_keyword_filter(recommended_query, keywords, Course.title, Course.description)

    recommended = recommended_query.order_by(Course.title.asc()).limit(8).all()
    if goal_role and not recommended:
        recommended = base_query.order_by(Course.title.asc()).limit(8).all()

    return render_template(
        "recommendations.html",
        courses=recommended,
        goal_preference=goal_preference,
        goal_prompt_needed=goal_prompt_needed,
    )


@app.route("/mock-test", methods=["GET", "POST"])
def mock_test():
    latest_attempt = None
    score = None
    correct_answers = None
    total_questions = 0
    passed = None
    interviewer_feedback = None
    is_guest_mode = not current_user.is_authenticated
    ai_generated = False
    selected_subject = "All Subjects"
    question_count_options = [10, 20, 30, 40]
    selected_question_count = 20
    available_subjects = ["All Subjects"] + [c.name for c in CourseCategory.query.order_by(CourseCategory.name.asc()).all()]
    questions = []
    payload_token = ""
    timer_seconds = 0
    selected_answers = {}
    review_mode = False

    if current_user.is_authenticated:
        latest_attempt = (
            MockTestAttempt.query.filter_by(user_id=current_user.id)
            .order_by(MockTestAttempt.created_at.desc())
            .first()
        )

    if request.method == "POST" and request.form.get("action") == "generate":
        selected_subject = request.form.get("subject", "All Subjects").strip() or "All Subjects"
        if selected_subject not in available_subjects:
            selected_subject = "All Subjects"
        selected_question_count = parse_int(request.form.get("question_count", "20"), 20)
        if selected_question_count not in question_count_options:
            selected_question_count = 20

        questions, ai_generated = generate_ai_mock_test_questions(
            selected_subject,
            question_count=selected_question_count,
        )
        payload = {
            "subject": selected_subject,
            "questions": questions,
            "ai_generated": ai_generated,
            "question_count": selected_question_count,
        }
        payload_token = make_mock_test_payload_token(payload)
        total_questions = len(questions)
        timer_seconds = total_questions * SECONDS_PER_MOCK_QUESTION
        flash(
            "AI interviewer prepared your test."
            if ai_generated
            else "AI unavailable, fallback test generated.",
            "success",
        )

    if request.method == "POST" and request.form.get("action", "").strip().lower() == "submit":
        payload_token = request.form.get("payload_token", "").strip()
        saved_payload = read_mock_test_payload_token(payload_token)
    else:
        saved_payload = None

    if isinstance(saved_payload, dict):
        questions = saved_payload.get("questions") or questions
        selected_subject = saved_payload.get("subject", selected_subject)
        ai_generated = bool(saved_payload.get("ai_generated"))
        payload_count = parse_int(saved_payload.get("question_count"), selected_question_count)
        if payload_count in question_count_options:
            selected_question_count = payload_count
        if questions:
            timer_seconds = len(questions) * SECONDS_PER_MOCK_QUESTION

    if request.method == "POST":
        action = request.form.get("action", "").strip().lower()
        if action == "submit":
            if not questions:
                flash("Test session expired. Generate a new test and try again.", "warning")
                return redirect(url_for("mock_test"))

            correct_count = 0
            weak_topics = []
            for item in questions:
                selected = request.form.get(item["id"], "")
                try:
                    selected_index = int(selected)
                except (TypeError, ValueError):
                    selected_index = -1
                selected_answers[item["id"]] = selected_index
                if selected_index == item["answer_index"]:
                    correct_count += 1
                else:
                    weak_topics.append(item.get("topic", "General"))

            total_questions = len(questions)
            score = round((correct_count / total_questions) * 100, 2) if total_questions else 0.0
            correct_answers = correct_count
            passed = score >= 60.0
            interviewer_feedback = generate_ai_interviewer_feedback(
                selected_subject,
                score,
                correct_answers,
                total_questions,
                weak_topics,
            )

            if current_user.is_authenticated:
                db.session.add(
                    MockTestAttempt(
                        user_id=current_user.id,
                        score_percent=score,
                        correct_answers=correct_answers,
                        total_questions=total_questions,
                    )
                )
                if not commit_or_rollback():
                    flash("Test submitted, but saving history failed.", "warning")
            flash("Mock test submitted successfully.", "success")
            payload_token = ""
            review_mode = True
        else:
            total_questions = len(questions)

    return render_template(
        "mock_test.html",
        questions=questions,
        payload_token=payload_token,
        available_subjects=available_subjects,
        selected_subject=selected_subject,
        question_count_options=question_count_options,
        selected_question_count=selected_question_count,
        ai_generated=ai_generated,
        latest_attempt=latest_attempt,
        score=score,
        correct_answers=correct_answers,
        total_questions=total_questions,
        passed=passed,
        is_guest_mode=is_guest_mode,
        interviewer_feedback=interviewer_feedback,
        timer_seconds=timer_seconds,
        selected_answers=selected_answers,
        review_mode=review_mode,
    )


@app.route("/interview-questions")
def interview_questions():
    selected_tag = request.args.get("tag", "").strip()
    interview_pdfs = get_interview_pdf_library()
    available_tags = sorted({tag for item in interview_pdfs for tag in (item.get("tags") or [])})
    if selected_tag and selected_tag in available_tags:
        interview_pdfs = [item for item in interview_pdfs if selected_tag in (item.get("tags") or [])]
    return render_template(
        "interview_questions.html",
        question_groups=INTERVIEW_QUESTION_GROUPS,
        interview_pdfs=interview_pdfs,
        available_tags=available_tags,
        selected_tag=selected_tag,
    )


@app.route("/interview-questions/pdf/<int:pdf_id>")
def interview_question_pdf(pdf_id):
    interview_pdfs = get_interview_pdf_library()
    if pdf_id < 1 or pdf_id > len(interview_pdfs):
        abort(404)

    file_name = interview_pdfs[pdf_id - 1]["file_name"]
    library_dir = os.path.join(app.static_folder, "interview_questions")
    return send_from_directory(library_dir, file_name, as_attachment=False)


@app.route("/interview-questions/legacy/<path:legacy_name>")
def interview_question_legacy_file(legacy_name):
    requested_name = unquote((legacy_name or "").strip())
    if not requested_name:
        abort(404)

    lookup = get_interview_pdf_lookup()
    file_name = lookup.get(requested_name.lower())
    if not file_name:
        abort(404)

    library_dir = os.path.join(app.static_folder, "interview_questions")
    return send_from_directory(library_dir, file_name, as_attachment=False)


@app.route("/notes")
def notes():
    selected_tag = request.args.get("tag", "").strip()
    notes_library = get_notes_library()
    available_tags = sorted({tag for item in notes_library for tag in (item.get("tags") or [])})
    if selected_tag and selected_tag in available_tags:
        notes_library = [item for item in notes_library if selected_tag in (item.get("tags") or [])]
    return render_template(
        "notes.html",
        notes_library=notes_library,
        available_tags=available_tags,
        selected_tag=selected_tag,
    )


@app.route("/resume-builder", methods=["GET", "POST"])
def resume_builder():
    resume_form = {
        "full_name": "",
        "target_role": "",
        "email": "",
        "phone": "",
        "location": "",
        "linkedin_url": "",
        "github_url": "",
        "summary": "",
        "skills_text": "",
        "projects_text": "",
        "experience_text": "",
        "education_text": "",
        "certifications_text": "",
    }
    ats_resume_text = None

    if current_user.is_authenticated:
        resume_form["full_name"] = sanitize_text(getattr(current_user, "name", ""), RESUME_FIELD_LIMITS["full_name"])
        resume_form["email"] = sanitize_text(getattr(current_user, "email", ""), RESUME_FIELD_LIMITS["email"])

    if request.method == "POST":
        for key, max_length in RESUME_FIELD_LIMITS.items():
            resume_form[key] = sanitize_text(request.form.get(key, ""), max_length)

        if not resume_form["full_name"]:
            flash("Please enter your full name for the resume.", "warning")
        else:
            ats_resume_text = build_ats_resume_text(resume_form)
            flash("ATS-friendly resume generated. Review and customize before applying.", "success")

    return render_template(
        "resume_builder.html",
        resume_form=resume_form,
        ats_resume_text=ats_resume_text,
    )


@app.route("/resume-builder/pdf", methods=["POST"])
def resume_builder_pdf():
    raw_text = request.form.get("ats_text", "")
    text_value = (raw_text or "").strip()
    if not text_value:
        resume_form = {}
        for key, max_length in RESUME_FIELD_LIMITS.items():
            resume_form[key] = sanitize_text(request.form.get(key, ""), max_length)
        text_value = build_ats_resume_text(resume_form)
    if len(text_value) > 10000:
        text_value = text_value[:10000]
    lines = [line.strip() for line in text_value.splitlines()]
    pdf_bytes = build_simple_pdf("ATS Resume", lines)
    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name="ats-resume.pdf",
    )


@app.route("/ai-assistant", methods=["GET", "POST"])
def ai_assistant():
    """
    GET /ai-assistant: Render a page for the AI learning assistant.
    POST /ai-assistant: Process a question and return an AI response.
    """
    question = ""
    answer = None
    project_topic = ""
    project_level = "Beginner"
    project_count = 5
    history = session.get("ai_history", [])

    if request.method == "POST":
        action = request.form.get("action", "ask").strip().lower()
        if action not in {"ask", "regenerate", "project_ideas"}:
            action = "ask"

        project_topic = sanitize_text(request.form.get("project_topic", ""), 120)
        project_level = sanitize_text(request.form.get("project_level", "Beginner"), 30)
        if project_level not in PROJECT_IDEA_LEVEL_OPTIONS:
            project_level = "Beginner"
        project_count = parse_int(request.form.get("project_count", "5"), 5, min_value=3, max_value=8)

        if action == "project_ideas":
            answer = get_ai_project_ideas(project_topic, project_level, project_count)
            question = f"Project ideas for {project_topic or 'software development'}"
        else:
            question = sanitize_text(request.form.get("question", ""), 700)
            if not question:
                flash("Please enter a question.", "warning")
            else:
                try:
                    answer = get_ai_learning_help(question, force_refresh=(action == "regenerate"))
                    if action == "regenerate":
                        flash("Generated a fresh answer.", "info")
                except Exception:
                    answer = "AI assistant is temporarily unavailable. Please try again."
                    flash("Unable to fetch AI response right now.", "danger")

        if question and answer:
            history = history if isinstance(history, list) else []
            history.insert(
                0,
                {
                    "question": question,
                    "answer": answer,
                    "created_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
                },
            )
            history = history[:AI_HISTORY_MAX_ENTRIES]
            session["ai_history"] = history

    return render_template(
        "ai_assistant.html",
        question=question,
        answer=answer,
        project_topic=project_topic,
        project_level=project_level,
        project_count=project_count,
        project_level_options=PROJECT_IDEA_LEVEL_OPTIONS,
        history=history,
    )


@app.route("/resources/open/<int:resource_id>")
def open_learning_resource(resource_id):
    resource = LearningResource.query.get_or_404(resource_id)
    record_resource_open(resource.id)
    if resource.file_path:
        if resource.file_path.startswith("roadmaps/"):
            return redirect(url_for("roadmap_static_pdf", filename=os.path.basename(resource.file_path)))
        return redirect(url_for("static", filename=resource.file_path))
    if resource.external_url and is_valid_http_url(resource.external_url):
        return redirect(resource.external_url)
    abort(404)


@app.route("/uploads/<path:filename>")
def uploaded_resource_file(filename):
    safe_name = secure_filename(filename or "")
    if not safe_name or safe_name != filename:
        abort(404)

    upload_dir = app.config["RESOURCES_UPLOAD_DIR"]
    absolute_path = os.path.join(upload_dir, safe_name)
    if not os.path.isfile(absolute_path):
        abort(404)

    return send_from_directory(upload_dir, safe_name, as_attachment=False)


@app.route("/admin/resources", methods=["POST"])
@admin_required
def admin_resources():
    title = sanitize_text(request.form.get("title", ""), 160)
    description = sanitize_text(request.form.get("description", ""), 1500)
    resource_type = sanitize_text(request.form.get("resource_type", ""), 20)
    external_url = sanitize_text(request.form.get("external_url", ""), 500)
    file_storage = request.files.get("resource_file")

    if resource_type not in VALID_RESOURCE_TYPES:
        flash("Please choose a valid resource type.", "danger")
        return redirect(url_for("dashboard"))

    if not title:
        flash("Resource title is required.", "danger")
        return redirect(url_for("dashboard"))

    has_file_upload = bool(file_storage and file_storage.filename)
    saved_file_path = None

    if resource_type == "PDF":
        if has_file_upload:
            saved_file_path, upload_error = save_uploaded_resource_file(file_storage)
            if upload_error:
                flash(upload_error, "danger")
                return redirect(url_for("dashboard", resource_type="PDF"))
        elif external_url:
            if not is_valid_http_url(external_url):
                flash("PDF URL must start with http:// or https://", "danger")
                return redirect(url_for("dashboard", resource_type="PDF"))
        else:
            flash("For PDF resources, upload a PDF file or provide a valid PDF URL.", "danger")
            return redirect(url_for("dashboard", resource_type="PDF"))
    else:
        if has_file_upload:
            flash("File uploads are only allowed for PDF resources.", "danger")
            return redirect(url_for("dashboard", resource_type=resource_type))
        if not external_url or not is_valid_http_url(external_url):
            flash(f"{resource_type} resources require a valid URL.", "danger")
            return redirect(url_for("dashboard", resource_type=resource_type))

    db.session.add(
        LearningResource(
            title=title,
            description=description or None,
            resource_type=resource_type,
            external_url=external_url or None,
            file_path=saved_file_path,
            created_by=(current_user.email if current_user.is_authenticated else "admin-session"),
        )
    )
    if not commit_or_rollback():
        flash("Unable to save resource right now.", "danger")
        return redirect(url_for("dashboard", resource_type=resource_type))

    flash("Resource added successfully.", "success")
    return redirect(url_for("dashboard", resource_type=resource_type))


@app.route("/admin/courses", methods=["GET", "POST"])
@admin_required
def admin_courses():
    categories = CourseCategory.query.order_by(CourseCategory.name.asc()).all()

    if request.method == "POST":
        bulk_rows = request.form.get("bulk_courses", "").strip()
        if bulk_rows:
            category_lookup = {category.name.lower(): category.id for category in categories}
            added_count = 0
            line_number = 0

            for raw_line in bulk_rows.splitlines():
                line_number += 1
                line = raw_line.strip()
                if not line:
                    continue

                parts = [part.strip() for part in line.split("|")]
                if len(parts) != 4:
                    flash(f"Line {line_number}: use Title | Description | Playlist URL | Category Name.", "danger")
                    return render_template("admin_courses.html", categories=categories)

                title, description, playlist_url, category_name = parts
                if not (title and description and playlist_url and category_name):
                    flash(f"Line {line_number}: all 4 values are required.", "danger")
                    return render_template("admin_courses.html", categories=categories)
                if not is_valid_http_url(playlist_url):
                    flash(f"Line {line_number}: playlist URL must start with http:// or https://", "danger")
                    return render_template("admin_courses.html", categories=categories)

                category_id = category_lookup.get(category_name.lower())
                if category_id is None:
                    flash(f"Line {line_number}: unknown category '{category_name}'.", "danger")
                    return render_template("admin_courses.html", categories=categories)

                db.session.add(
                    Course(
                        title=title,
                        description=description,
                        playlist_url=playlist_url,
                        category_id=category_id,
                    )
                )
                added_count += 1

            if added_count == 0:
                flash("No valid playlist rows found.", "warning")
                return render_template("admin_courses.html", categories=categories)

            if not commit_or_rollback():
                flash("Unable to upload playlists right now.", "danger")
                return render_template("admin_courses.html", categories=categories)
            flash(f"{added_count} playlists uploaded successfully.", "success")
            return redirect(url_for("admin_courses"))

        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        playlist_url = request.form.get("playlist_url", "").strip()
        category_id = request.form.get("category_id", "").strip()

        if not (title and description and playlist_url and category_id):
            flash("All fields are required.", "danger")
            return render_template("admin_courses.html", categories=categories)
        if len(title) > 255:
            flash("Title must be 255 characters or less.", "danger")
            return render_template("admin_courses.html", categories=categories)
        if len(description) > 2000:
            flash("Description is too long.", "danger")
            return render_template("admin_courses.html", categories=categories)
        if not is_valid_http_url(playlist_url):
            flash("Playlist URL must start with http:// or https://", "danger")
            return render_template("admin_courses.html", categories=categories)

        category_id_int = parse_int(category_id, None, min_value=1)
        if category_id_int is None or not CourseCategory.query.get(category_id_int):
            flash("Please select a valid category.", "danger")
            return render_template("admin_courses.html", categories=categories)

        db.session.add(
            Course(
                title=title,
                description=description,
                playlist_url=playlist_url,
                category_id=category_id_int,
            )
        )
        if not commit_or_rollback():
            flash("Unable to add course right now.", "danger")
            return render_template("admin_courses.html", categories=categories)

        flash("Course added successfully.", "success")
        return redirect(url_for("admin_courses"))

    latest_courses = Course.query.order_by(Course.id.desc()).limit(12).all()
    return render_template("admin_courses.html", categories=categories, latest_courses=latest_courses)


@app.errorhandler(404)
def not_found_error(_error):
    return render_template("404.html"), 404


@app.errorhandler(500)
def internal_server_error(_error):
    db.session.rollback()
    return render_template("500.html"), 500


@app.context_processor
def inject_template_globals():
    return {
        "year": datetime.utcnow().year,
        "can_access_admin": can_access_admin_panel(),
        "admin_token_configured": bool(get_admin_access_token()),
    }


with app.app_context():
    db.create_all()
    seed_categories_and_courses()
    remove_retired_requested_courses()
    ensure_requested_courses()
    ensure_default_learning_resources()


if __name__ == "__main__":
    app.run(
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "5000")),
        debug=True,
    )
