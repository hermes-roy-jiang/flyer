# Flyer System

## Overview
A flyer management system for hermes.royjiang.me. Users can upload HTML flyers, share them publicly or with specific users, and manage them through a dashboard.

## Architecture
- **Backend**: FastAPI on port 8013
- **Database**: Shared SQLite at `/root/paper-ask-app/paper_ask.db` (via hermes_auth)
- **Auth**: `hermes_auth` shared module (session cookie, email verification, magic emails)
- **Frontend**: Single-page app with dark theme, mobile-responsive
- **Flyer storage**: HTML files at `/root/flyer-app/flyers/`

## URLs
- `https://hermes.royjiang.me/flyer/` — public gallery
- `https://hermes.royjiang.me/flyer/view/{id}` — view a flyer
- `https://hermes.royjiang.me/flyer/dashboard` — user dashboard (auth required)
- `https://hermes.royjiang.me/flyer/admin` — admin panel (admin only)

## Database Schema

```sql
CREATE TABLE IF NOT EXISTS flyers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    owner_email TEXT NOT NULL,
    html_filename TEXT NOT NULL,       -- filename in /root/flyer-app/flyers/
    is_public INTEGER DEFAULT 1,       -- 1=public, 0=private
    requires_auth INTEGER DEFAULT 0,   -- 1=must be logged in to view
    view_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS flyer_access (
    flyer_id INTEGER NOT NULL,
    user_email TEXT NOT NULL,
    granted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (flyer_id, user_email),
    FOREIGN KEY (flyer_id) REFERENCES flyers(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS flyer_views (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    flyer_id INTEGER NOT NULL,
    viewer_email TEXT,                 -- NULL if anonymous
    viewed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (flyer_id) REFERENCES flyers(id) ON DELETE CASCADE
);
```

## API Endpoints

### Public
- `GET /api/flyers` — list public flyers (or all accessible if logged in)
- `GET /api/flyers/{id}` — get flyer metadata + check access
- `GET /api/flyers/{id}/html` — serve the HTML file (checks access)
- `POST /api/flyers/{id}/view` — record a view

### Authenticated
- `POST /api/upload` — upload a new flyer (multipart: file + title + description + is_public + requires_auth)
- `GET /api/my/flyers` — list current user's flyers
- `PUT /api/my/flyers/{id}` — update flyer metadata
- `DELETE /api/my/flyers/{id}` — delete flyer
- `POST /api/my/flyers/{id}/access` — grant access to a user
- `DELETE /api/my/flyers/{id}/access/{email}` — revoke access

### Admin
- `GET /api/admin/flyers` — list ALL flyers
- `DELETE /api/admin/flyers/{id}` — delete any flyer
- `GET /api/admin/stats` — view counts, user counts, etc.

## Frontend Pages

### 1. Gallery (`/flyer/`)
- Grid of flyer cards (thumbnail/title/description/view count)
- Filter: All / Public / My Flyers (if logged in)
- Search by title
- "Upload" button (links to dashboard)
- Login link in header (or user info if logged in)
- Mobile: 1 column, Desktop: 3-4 columns

### 2. Flyer View (`/flyer/view/{id}`)
- Full-screen iframe rendering the HTML flyer
- Header bar with: title, owner, view count, share button
- Mobile: iframe fills viewport, header collapsible
- If flyer requires auth and user not logged in → redirect to login
- If flyer is private and user has no access → 403 page

### 3. User Dashboard (`/flyer/dashboard`)
- My Flyers list (table with title, public/private, views, date, actions)
- Upload form:
  - File input (accept .html)
  - OR text area to paste HTML
  - Title, Description
  - Public toggle
  - Requires Auth toggle
  - Access list (add/remove users)
- Edit existing flyer
- Mobile: stacked layout, Desktop: side-by-side

### 4. Admin Panel (`/flyer/admin`)
- All Flyers table (sortable by date, views, owner)
- User management (list users with flyer counts)
- Delete any flyer
- View stats (total flyers, total views, top flyers)

## Design Requirements
- Dark theme matching existing site (#0a0a0f bg, #12121a cards, #6366f1 accent)
- Light/dark toggle
- Mobile-first responsive design
- All CSS inline in HTML files
- Vanilla JS only (no frameworks)
- KaTeX not needed for this project

## hermes_auth Integration

```python
# In main.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from hermes_auth import init_auth_db, seed_admin_users, get_session_email, is_admin

# Auth middleware extracts session from cookie
# Redirect to /auth/?redirect=/flyer/dashboard if not authenticated
# Admin page checks is_admin()
```

## Nginx Config

```nginx
# Flyer API proxy
location /flyer/api/ {
    proxy_pass http://127.0.0.1:8013/api/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_buffering off;
    proxy_cache off;
}

# Flyer static files
location /flyer/ {
    alias /root/flyer-app/static/;
    index index.html;
    try_files $uri $uri/ /flyer/index.html;
}
```

## Systemd Service

```ini
[Unit]
Description=Flyer System Backend
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/flyer-app
ExecStart=/usr/local/lib/hermes-agent/venv/bin/python3 -m uvicorn main:app --host 127.0.0.1 --port 8013
Restart=always
RestartSec=5
Environment=PYTHONPATH=/root

[Install]
WantedBy=multi-user.target
```

## Pitfalls (from web-app-stack skill)
- Cookie `path` must be `/` for cross-app session sharing
- `from hermes_auth import config` gives the instance, use `config.SESSION_EXPIRY_HOURS`
- Use venv python (`/usr/local/lib/hermes-agent/venv/bin/python3`), not system python
- nginx `try_files` with `alias` must include subpath prefix
- Mobile: `font-size: 16px !important` on inputs to prevent iOS zoom
- Full-screen layouts need `overflow: hidden` on html, body
