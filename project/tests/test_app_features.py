import importlib
import os
import sys
from io import BytesIO

import pytest


@pytest.fixture(scope="session")
def app_module(tmp_path_factory):
    temp_dir = tmp_path_factory.mktemp("learning_hub_tests")
    db_path = temp_dir / "test.db"
    upload_dir = temp_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    os.environ["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
    os.environ["SECRET_KEY"] = "test-secret-key"
    os.environ["ADMIN_ACCESS_TOKEN"] = "test-admin-token"
    os.environ.pop("OPENAI_API_KEY", None)
    os.environ.pop("OPENROUTER_API_KEY", None)

    if "app" in sys.modules:
        del sys.modules["app"]
    module = importlib.import_module("app")
    module.app.config["TESTING"] = True
    module.app.config["PROPAGATE_EXCEPTIONS"] = False
    module.app.config["RESOURCES_UPLOAD_DIR"] = str(upload_dir)
    return module


@pytest.fixture
def client(app_module):
    with app_module.app.test_client() as flask_client:
        yield flask_client


def unlock_admin(client):
    return client.post(
        "/admin/unlock",
        data={"admin_token": "test-admin-token", "next": "/admin/courses"},
        follow_redirects=True,
    )


def test_dashboard_page_loads(client):
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert b"Learning Categories" in response.data


def test_admin_route_requires_unlock(client):
    response = client.get("/admin/courses")
    assert response.status_code == 302
    assert "/admin/unlock" in response.headers.get("Location", "")


def test_admin_unlock_then_access_courses_page(client):
    unlock_response = unlock_admin(client)
    assert unlock_response.status_code == 200
    assert b"Admin: Add Course" in unlock_response.data


def test_admin_can_add_video_resource(client):
    unlock_admin(client)
    response = client.post(
        "/admin/resources",
        data={
            "title": "YouTube SQL Practice",
            "description": "Short SQL drills and exercises.",
            "resource_type": "Video",
            "external_url": "https://www.youtube.com/watch?v=abc123",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"YouTube SQL Practice" in response.data


def test_admin_can_upload_pdf_resource(client):
    unlock_admin(client)
    response = client.post(
        "/admin/resources",
        data={
            "title": "Practice PDF Resource",
            "description": "PDF upload test",
            "resource_type": "PDF",
            "resource_file": (BytesIO(b"%PDF-1.4 test"), "practice.pdf"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Practice PDF Resource" in response.data


def test_dashboard_resource_filter(client):
    response = client.get("/dashboard?resource_type=Video")
    assert response.status_code == 200
    assert b"Learning Resources" in response.data


def test_ai_assistant_works_without_api_key(client):
    response = client.post(
        "/ai-assistant",
        data={"question": "How can I improve in Python?", "action": "ask"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert (
        b"AI support is enabled, but API key is missing." in response.data
        or b"Quick fallback:" in response.data
        or b"AI Answer" in response.data
    )


def test_404_error_page(client):
    response = client.get("/this-page-does-not-exist")
    assert response.status_code == 404
    assert b"Page Not Found" in response.data
