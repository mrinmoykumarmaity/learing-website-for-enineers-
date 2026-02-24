import json
import os
import tempfile
from datetime import datetime
from functools import wraps

from flask import Flask, flash, redirect, render_template, request, url_for
from flask_login import LoginManager, current_user, login_required, login_user, logout_user
from sqlalchemy import func

from models import Course, CourseCategory, Roadmap, User, UserCourseProgress, db


app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-this-in-production")
database_url = os.environ.get("DATABASE_URL")
if not database_url:
    is_serverless = bool(
        os.environ.get("VERCEL")
        or os.environ.get("VERCEL_ENV")
        or os.environ.get("NOW_REGION")
        or os.environ.get("AWS_LAMBDA_FUNCTION_NAME")
    )
    if is_serverless:
        temp_db = os.path.join(tempfile.gettempdir(), "database.db").replace("\\", "/")
        database_url = f"sqlite:///{temp_db}"
    else:
        local_db = os.path.join(os.path.dirname(os.path.abspath(__file__)), "database.db").replace("\\", "/")
        database_url = f"sqlite:///{local_db}"
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)

login_manager = LoginManager(app)
login_manager.login_view = "dashboard"
login_manager.login_message_category = "warning"


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


def admin_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            flash("Admin access is required.", "danger")
            return redirect(url_for("dashboard"))
        return view_func(*args, **kwargs)

    return wrapped


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
            "category_name": "Software Engineering",
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
    ]

    for item in requested_courses:
        exists = Course.query.filter_by(playlist_url=item["playlist_url"]).first()
        if exists:
            continue

        category = CourseCategory.query.filter_by(name=item["category_name"]).first()
        if not category:
            category = CourseCategory(
                name=item["category_name"],
                description=f"Top curated playlists for {item['category_name']}.",
            )
            db.session.add(category)
            db.session.flush()

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


def get_user_progress_map(user_id):
    rows = UserCourseProgress.query.filter_by(user_id=user_id).all()
    return {row.course_id: row for row in rows}


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


@app.route("/")
def index():
    return redirect(url_for("dashboard"))


@app.route("/register", methods=["GET", "POST"])
def register():
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

    total_courses = Course.query.count()
    if current_user.is_authenticated:
        completed_courses = (
            db.session.query(func.count(UserCourseProgress.id))
            .filter(UserCourseProgress.user_id == current_user.id, UserCourseProgress.completed.is_(True))
            .scalar()
        )
        overall_progress = round((completed_courses / total_courses) * 100, 2) if total_courses else 0
        user_progress_map = get_user_progress_map(current_user.id)
        latest_roadmap = (
            Roadmap.query.filter_by(user_id=current_user.id)
            .order_by(Roadmap.created_at.desc())
            .first()
        )
    else:
        completed_courses = 0
        overall_progress = 0
        user_progress_map = {}
        latest_roadmap = None

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
    db.session.commit()

    flash_message = "Course marked as completed." if progress_item.completed else "Course marked as not completed."
    flash(flash_message, "success")

    next_url = request.form.get("next")
    if next_url:
        return redirect(next_url)
    return redirect(url_for("courses", category_id=course.category_id))


@app.route("/roadmap", methods=["GET", "POST"])
def roadmap():
    generated = None

    if request.method == "POST":
        current_level = request.form.get("current_level", "Beginner")
        target_career = request.form.get("target_career", "Software Developer")
        daily_study_time = int(request.form.get("daily_study_time", 2))

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
            db.session.commit()
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


@app.route("/admin/courses", methods=["GET", "POST"])
@login_required
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

            db.session.commit()
            flash(f"{added_count} playlists uploaded successfully.", "success")
            return redirect(url_for("admin_courses"))

        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        playlist_url = request.form.get("playlist_url", "").strip()
        category_id = request.form.get("category_id", "").strip()

        if not (title and description and playlist_url and category_id):
            flash("All fields are required.", "danger")
            return render_template("admin_courses.html", categories=categories)

        db.session.add(
            Course(
                title=title,
                description=description,
                playlist_url=playlist_url,
                category_id=int(category_id),
            )
        )
        db.session.commit()

        flash("Course added successfully.", "success")
        return redirect(url_for("admin_courses"))

    latest_courses = Course.query.order_by(Course.id.desc()).limit(12).all()
    return render_template("admin_courses.html", categories=categories, latest_courses=latest_courses)


@app.context_processor
def inject_year():
    return {"year": datetime.utcnow().year}


with app.app_context():
    db.create_all()
    seed_categories_and_courses()
    remove_retired_requested_courses()
    ensure_requested_courses()


if __name__ == "__main__":
    app.run(debug=True)
