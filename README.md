# Flyer System

A flyer management system for [hermes.royjiang.me/flyer/](https://hermes.royjiang.me/flyer/).

## Features

- **Upload HTML flyers** — file upload or paste HTML directly
- **Three access levels** — public, auth-required (must be logged in), private (specific users)
- **User dashboard** — manage your flyers, grant/revoke access
- **Admin panel** — all flyers, stats, user management
- **Mobile-responsive** — works on all devices
- **Dark/light theme** — toggle with localStorage persistence

## Architecture

- **Backend**: FastAPI (Python) on port 8013
- **Database**: SQLite (shared with other apps via hermes_auth)
- **Auth**: hermes_auth shared module (email verification, session cookies)
- **Frontend**: Vanilla JS SPA with hash routing

## API

- `GET /api/flyers` — list public flyers
- `POST /api/upload` — upload a new flyer (auth required)
- `GET /api/flyers/{id}/html` — serve flyer HTML (access-controlled)
- `GET /api/my/flyers` — list user's flyers (auth required)
- `GET /api/admin/flyers` — list all flyers (admin only)
- `GET /api/admin/stats` — view stats (admin only)

## Deployment

```bash
# Systemd service
sudo systemctl start flyer-app

# Nginx config in hermes.royjiang.me
location /flyer/ {
    proxy_pass http://127.0.0.1:8013/;
    ...
}
```
