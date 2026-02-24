# Smart Career Learning Hub

## Tech Stack
- Backend: Flask
- Frontend: HTML, CSS, Bootstrap
- Database: SQLite (`database.db`)

## Project Structure
project/
- app.py
- models.py
- requirements.txt
- templates/
- static/
- database.db (auto-created)

## Setup
1. Open terminal in `project/`
2. Create virtual environment:
   - `python -m venv .venv`
   - `.venv\Scripts\activate` (Windows)
3. Install dependencies:
   - `pip install -r requirements.txt`
4. Run app:
   - `python app.py`
5. Open: `http://127.0.0.1:5000`

## Sample Data
- Categories and starter YouTube playlists are auto-seeded on first run.

## Admin User
- Register a normal account first.
- To make it admin, run this once in `project/`:
  - `python -c "from app import app,db,User;\nwith app.app_context():\n u=User.query.filter_by(email='YOUR_EMAIL').first(); u.is_admin=True; db.session.commit(); print('admin updated')"`

## Main Features
- User registration/login/logout with password hashing
- Dashboard with category cards and progress bars
- Category-based YouTube playlists with open-in-new-tab
- Rule-based AI roadmap generation and history storage
- Course completion tracking with percentage progress
- Recommendation page for incomplete courses
- Admin course management panel
- Search courses within category
- Dark mode toggle

## Deploy (Render)
1. Open this one-click deploy URL:
   - `https://render.com/deploy?repo=https://github.com/mrinmoykumarmaity/syudy-hub-`
2. Click **Create Web Service**.
3. After deploy completes, Render will show your live URL:
   - `https://<your-service-name>.onrender.com`
