from datetime import datetime

from flask_login import UserMixin

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    progress_entries = db.relationship("UserCourseProgress", back_populates="user", cascade="all, delete-orphan")
    roadmaps = db.relationship("Roadmap", back_populates="user", cascade="all, delete-orphan")
    mock_test_attempts = db.relationship("MockTestAttempt", back_populates="user", cascade="all, delete-orphan")


class CourseCategory(db.Model):
    __tablename__ = "course_categories"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    description = db.Column(db.Text, nullable=True)

    courses = db.relationship("Course", back_populates="category", cascade="all, delete-orphan")


class Course(db.Model):
    __tablename__ = "courses"

    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(db.Integer, db.ForeignKey("course_categories.id"), nullable=False, index=True)
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    playlist_url = db.Column(db.String(500), nullable=False)

    category = db.relationship("CourseCategory", back_populates="courses")
    progress_entries = db.relationship("UserCourseProgress", back_populates="course", cascade="all, delete-orphan")


class LearningResource(db.Model):
    __tablename__ = "learning_resources"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text, nullable=True)
    resource_type = db.Column(db.String(20), nullable=False, index=True)
    external_url = db.Column(db.String(500), nullable=True)
    file_path = db.Column(db.String(300), nullable=True)
    created_by = db.Column(db.String(120), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class UserCourseProgress(db.Model):
    __tablename__ = "user_course_progress"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    course_id = db.Column(db.Integer, db.ForeignKey("courses.id"), nullable=False, index=True)
    completed = db.Column(db.Boolean, default=False, nullable=False)
    completed_at = db.Column(db.DateTime, nullable=True)

    user = db.relationship("User", back_populates="progress_entries")
    course = db.relationship("Course", back_populates="progress_entries")

    __table_args__ = (
        db.UniqueConstraint("user_id", "course_id", name="uq_user_course"),
    )

class Roadmap(db.Model):
    __tablename__ = "roadmaps"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    current_level = db.Column(db.String(50), nullable=False)
    target_career = db.Column(db.String(80), nullable=False)
    daily_study_time = db.Column(db.Integer, nullable=False)
    content_json = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship("User", back_populates="roadmaps")


class MockTestAttempt(db.Model):
    __tablename__ = "mock_test_attempts"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    score_percent = db.Column(db.Float, nullable=False)
    correct_answers = db.Column(db.Integer, nullable=False)
    total_questions = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship("User", back_populates="mock_test_attempts")
