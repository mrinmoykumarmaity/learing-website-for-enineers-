import json
import os
import random
import secrets
import tempfile
import time
from datetime import datetime, timedelta
from functools import wraps
from urllib.parse import urlparse

from flask import Flask, flash, redirect, render_template, request, session, url_for
from flask_login import LoginManager, current_user, login_required, logout_user
from sqlalchemy import func, text
from openai import OpenAI
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.utils import secure_filename

try:
    from models import Course, CourseCategory, LearningResource, MockTestAttempt, Roadmap, User, UserCourseProgress, db
except ModuleNotFoundError:
    from project.models import Course, CourseCategory, LearningResource, MockTestAttempt, Roadmap, User, UserCourseProgress, db


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


def is_valid_http_url(value):
    if not value:
        return False
    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


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

    return f"resources/{unique_name}", None


def commit_or_rollback():
    try:
        db.session.commit()
        return True
    except Exception:
        db.session.rollback()
        return False


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
            {
                "title": "Deep Learning Course",
                "description": "Neural networks, CNN, RNN and modern deep learning workflows.",
                "playlist_url": "https://www.youtube.com/playlist?list=PLZoTAELRMXVONh2lF-0Y3lQf5V6Qe--8-",
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
                "title": "DevOps Full Course",
                "description": "CI/CD, containers, automation and cloud deployment.",
                "playlist_url": "https://www.youtube.com/playlist?list=PLQ4bwxL7hYl4f4wP3M7Jin7M8hQd5a7rD",
            },
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
            "title": "React JS Full Course",
            "description": "Build frontend apps with React and modern tooling.",
            "playlist_url": "https://www.youtube.com/watch?v=bMknfKXIFA8",
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
    profile = career_map[target_career]

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
        if not configured_token:
            flash("ADMIN_ACCESS_TOKEN is not configured on the server.", "danger")
            return redirect(url_for("dashboard"))

        if token_input and secrets.compare_digest(token_input, configured_token):
            unlock_admin_session()
            flash("Admin access unlocked.", "success")
            return redirect(next_url)

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

    resources_query = LearningResource.query.order_by(LearningResource.created_at.desc())
    if selected_resource_filter != "All":
        resources_query = resources_query.filter_by(resource_type=selected_resource_filter)
    learning_resources = resources_query.limit(30).all()

    total_courses = Course.query.count()
    weekly_progress_chart = {"labels": [], "daily": [], "cumulative": []}
    if current_user.is_authenticated:
        completed_courses, total_courses, _ = get_user_course_completion_stats(current_user.id)
        overall_progress = round((completed_courses / total_courses) * 100, 2) if total_courses else 0
        user_progress_map = get_user_progress_map(current_user.id)
        weekly_progress_chart = get_weekly_progress_chart(current_user.id)
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
        learning_resources=learning_resources,
        selected_resource_filter=selected_resource_filter,
        resource_filter_options=RESOURCE_FILTER_OPTIONS,
    )


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

    if request.method == "POST":
        current_level = request.form.get("current_level", "Beginner")
        target_career = request.form.get("target_career", "Software Developer")
        daily_study_time = parse_int(request.form.get("daily_study_time", 2), 2, min_value=1, max_value=10)

        generated = build_roadmap(current_level, target_career, daily_study_time)
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

    return render_template("roadmap.html", generated=generated, saved_roadmaps=saved_roadmaps)


@app.route("/recommendations")
@login_required
def recommendations():
    completed_ids = {
        p.course_id
        for p in UserCourseProgress.query.filter_by(user_id=current_user.id, completed=True).all()
    }

    recommended = (
        Course.query.filter(~Course.id.in_(completed_ids))
        .order_by(Course.title.asc())
        .limit(8)
        .all()
    )

    return render_template("recommendations.html", courses=recommended)


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
    )


@app.route("/ai-assistant", methods=["GET", "POST"])
def ai_assistant():
    """
    GET /ai-assistant: Render a page for the AI learning assistant.
    POST /ai-assistant: Process a question and return an AI response.
    """
    question = ""
    answer = None

    if request.method == "POST":
        action = request.form.get("action", "ask").strip().lower()
        if action not in {"ask", "regenerate"}:
            action = "ask"

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

    return render_template("ai_assistant.html", question=question, answer=answer)


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
