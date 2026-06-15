"""Flyer System — FastAPI backend for hermes.royjiang.me/flyer/."""
import os
import sys
import secrets
from datetime import datetime
from contextlib import asynccontextmanager

# Make hermes_auth importable
sys.path.insert(0, "/root")

from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from hermes_auth import config as auth_config
from hermes_auth.database import get_db, init_auth_db, seed_admin_users, is_admin
from hermes_auth.auth import get_session_email

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FLYERS_DIR = os.path.join(BASE_DIR, "flyers")
STATIC_DIR = os.path.join(BASE_DIR, "static")
os.makedirs(FLYERS_DIR, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)


async def init_flyer_db():
    """Create flyer tables in the shared database."""
    db = await get_db()
    try:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS flyers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                owner_email TEXT NOT NULL,
                html_filename TEXT NOT NULL,
                is_public INTEGER DEFAULT 1,
                requires_auth INTEGER DEFAULT 0,
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
                viewer_email TEXT,
                viewed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (flyer_id) REFERENCES flyers(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_flyer_owner ON flyers(owner_email);
            CREATE INDEX IF NOT EXISTS idx_access_email ON flyer_access(user_email);
            CREATE INDEX IF NOT EXISTS idx_views_flyer ON flyer_views(flyer_id);
        """)
        await db.commit()
    finally:
        await db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_auth_db()
    await seed_admin_users()
    await init_flyer_db()
    yield


app = FastAPI(title="Flyer System", lifespan=lifespan)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    token = request.cookies.get("session_token")
    request.state.email = None
    if token:
        request.state.email = await get_session_email(token)
    return await call_next(request)


# --- Helpers ---

async def fetch_flyer(db, flyer_id: int):
    rows = await db.execute_fetchall(
        "SELECT * FROM flyers WHERE id = ?", (flyer_id,)
    )
    return rows[0] if rows else None


async def has_access(db, flyer, email):
    """Return (can_view: bool, reason: str|None)."""
    if flyer is None:
        return False, "not_found"
    owner = flyer["owner_email"]
    # Owner and admins always have access
    if email and (email == owner or await is_admin(email)):
        return True, None
    # Private flyers: only owner / explicitly granted / admin
    if not flyer["is_public"]:
        if not email:
            return False, "auth_required"
        granted = await db.execute_fetchall(
            "SELECT 1 FROM flyer_access WHERE flyer_id = ? AND user_email = ?",
            (flyer["id"], email),
        )
        if granted:
            return True, None
        return False, "forbidden"
    # Public but requires login
    if flyer["requires_auth"] and not email:
        return False, "auth_required"
    return True, None


def flyer_dict(row, *, include_owner=True):
    d = {
        "id": row["id"],
        "title": row["title"],
        "description": row["description"] or "",
        "is_public": bool(row["is_public"]),
        "requires_auth": bool(row["requires_auth"]),
        "view_count": row["view_count"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if include_owner:
        d["owner_email"] = row["owner_email"]
    return d


def require_auth(request):
    if not request.state.email:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return request.state.email


async def require_admin(request):
    email = require_auth(request)
    if not await is_admin(email):
        raise HTTPException(status_code=403, detail="Admin access required")
    return email


# --- Auth info ---

@app.get("/api/me")
async def me(request: Request):
    if request.state.email:
        return {
            "authenticated": True,
            "email": request.state.email,
            "is_admin": await is_admin(request.state.email),
        }
    return {"authenticated": False}


# --- Public flyer API ---

@app.get("/api/flyers")
async def list_flyers(request: Request):
    """List public flyers, plus any private ones the user can access."""
    email = request.state.email
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT * FROM flyers ORDER BY created_at DESC"
        )
        result = []
        for r in rows:
            can, _ = await has_access(db, r, email)
            # Show in gallery if publicly listed, or user has access
            if r["is_public"] or can:
                d = flyer_dict(r)
                d["can_view"] = can
                d["is_owner"] = bool(email and email == r["owner_email"])
                result.append(d)
        return {"flyers": result}
    finally:
        await db.close()


@app.get("/api/flyers/{flyer_id}")
async def get_flyer(request: Request, flyer_id: int):
    db = await get_db()
    try:
        flyer = await fetch_flyer(db, flyer_id)
        if not flyer:
            raise HTTPException(status_code=404, detail="Flyer not found")
        can, reason = await has_access(db, flyer, request.state.email)
        d = flyer_dict(flyer)
        d["can_view"] = can
        d["access_reason"] = reason
        d["is_owner"] = bool(request.state.email and request.state.email == flyer["owner_email"])
        return d
    finally:
        await db.close()


@app.get("/api/flyers/{flyer_id}/html", response_class=HTMLResponse)
async def get_flyer_html(request: Request, flyer_id: int):
    db = await get_db()
    try:
        flyer = await fetch_flyer(db, flyer_id)
        if not flyer:
            raise HTTPException(status_code=404, detail="Flyer not found")
        can, reason = await has_access(db, flyer, request.state.email)
        if not can:
            code = 401 if reason == "auth_required" else 403
            raise HTTPException(status_code=code, detail=reason or "No access")
        path = os.path.join(FLYERS_DIR, flyer["html_filename"])
        if not os.path.isfile(path):
            raise HTTPException(status_code=404, detail="Flyer file missing")
        return FileResponse(path, media_type="text/html")
    finally:
        await db.close()


@app.post("/api/flyers/{flyer_id}/view")
async def record_view(request: Request, flyer_id: int):
    db = await get_db()
    try:
        flyer = await fetch_flyer(db, flyer_id)
        if not flyer:
            raise HTTPException(status_code=404, detail="Flyer not found")
        can, _ = await has_access(db, flyer, request.state.email)
        if not can:
            raise HTTPException(status_code=403, detail="No access")
        await db.execute(
            "INSERT INTO flyer_views (flyer_id, viewer_email) VALUES (?, ?)",
            (flyer_id, request.state.email),
        )
        await db.execute(
            "UPDATE flyers SET view_count = view_count + 1 WHERE id = ?", (flyer_id,)
        )
        await db.commit()
        row = await db.execute_fetchall(
            "SELECT view_count FROM flyers WHERE id = ?", (flyer_id,)
        )
        return {"ok": True, "view_count": row[0]["view_count"]}
    finally:
        await db.close()


# --- Authenticated flyer management ---

def _save_html(content: str) -> str:
    filename = f"flyer_{secrets.token_hex(12)}.html"
    with open(os.path.join(FLYERS_DIR, filename), "w", encoding="utf-8") as f:
        f.write(content)
    return filename


def _truthy(v) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on")


@app.post("/api/upload")
async def upload_flyer(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    is_public: str = Form("1"),
    requires_auth: str = Form("0"),
    file: UploadFile = File(None),
    html_text: str = Form(None),
):
    email = require_auth(request)
    content = None
    if file is not None:
        raw = await file.read()
        if raw:
            content = raw.decode("utf-8", errors="replace")
    if (content is None or not content.strip()) and html_text:
        content = html_text
    if not content or not content.strip():
        raise HTTPException(status_code=400, detail="No HTML content provided")
    if not title.strip():
        raise HTTPException(status_code=400, detail="Title required")

    filename = _save_html(content)
    db = await get_db()
    try:
        cur = await db.execute(
            """INSERT INTO flyers (title, description, owner_email, html_filename,
                                   is_public, requires_auth)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (title.strip(), description.strip(), email, filename,
             1 if _truthy(is_public) else 0, 1 if _truthy(requires_auth) else 0),
        )
        await db.commit()
        flyer = await fetch_flyer(db, cur.lastrowid)
        return flyer_dict(flyer)
    finally:
        await db.close()


@app.get("/api/my/flyers")
async def my_flyers(request: Request):
    email = require_auth(request)
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT * FROM flyers WHERE owner_email = ? ORDER BY created_at DESC",
            (email,),
        )
        result = []
        for r in rows:
            d = flyer_dict(r)
            access = await db.execute_fetchall(
                "SELECT user_email FROM flyer_access WHERE flyer_id = ?", (r["id"],)
            )
            d["access_list"] = [a["user_email"] for a in access]
            result.append(d)
        return {"flyers": result}
    finally:
        await db.close()


@app.put("/api/my/flyers/{flyer_id}")
async def update_flyer(request: Request, flyer_id: int):
    email = require_auth(request)
    body = await request.json()
    db = await get_db()
    try:
        flyer = await fetch_flyer(db, flyer_id)
        if not flyer:
            raise HTTPException(status_code=404, detail="Flyer not found")
        if flyer["owner_email"] != email and not await is_admin(email):
            raise HTTPException(status_code=403, detail="Not your flyer")

        fields, values = [], []
        if "title" in body:
            fields.append("title = ?"); values.append(str(body["title"]).strip())
        if "description" in body:
            fields.append("description = ?"); values.append(str(body["description"]).strip())
        if "is_public" in body:
            fields.append("is_public = ?"); values.append(1 if body["is_public"] else 0)
        if "requires_auth" in body:
            fields.append("requires_auth = ?"); values.append(1 if body["requires_auth"] else 0)
        if "html_text" in body and body["html_text"] and body["html_text"].strip():
            with open(os.path.join(FLYERS_DIR, flyer["html_filename"]), "w", encoding="utf-8") as f:
                f.write(body["html_text"])
        if fields:
            fields.append("updated_at = CURRENT_TIMESTAMP")
            values.append(flyer_id)
            await db.execute(f"UPDATE flyers SET {', '.join(fields)} WHERE id = ?", values)
            await db.commit()
        flyer = await fetch_flyer(db, flyer_id)
        return flyer_dict(flyer)
    finally:
        await db.close()


@app.delete("/api/my/flyers/{flyer_id}")
async def delete_flyer(request: Request, flyer_id: int):
    email = require_auth(request)
    db = await get_db()
    try:
        flyer = await fetch_flyer(db, flyer_id)
        if not flyer:
            raise HTTPException(status_code=404, detail="Flyer not found")
        if flyer["owner_email"] != email and not await is_admin(email):
            raise HTTPException(status_code=403, detail="Not your flyer")
        await db.execute("DELETE FROM flyer_access WHERE flyer_id = ?", (flyer_id,))
        await db.execute("DELETE FROM flyer_views WHERE flyer_id = ?", (flyer_id,))
        await db.execute("DELETE FROM flyers WHERE id = ?", (flyer_id,))
        await db.commit()
        path = os.path.join(FLYERS_DIR, flyer["html_filename"])
        if os.path.isfile(path):
            os.remove(path)
        return {"ok": True}
    finally:
        await db.close()


@app.post("/api/my/flyers/{flyer_id}/access")
async def grant_access(request: Request, flyer_id: int):
    email = require_auth(request)
    body = await request.json()
    target = body.get("email", "").strip().lower()
    if not target:
        raise HTTPException(status_code=400, detail="Email required")
    db = await get_db()
    try:
        flyer = await fetch_flyer(db, flyer_id)
        if not flyer:
            raise HTTPException(status_code=404, detail="Flyer not found")
        if flyer["owner_email"] != email and not await is_admin(email):
            raise HTTPException(status_code=403, detail="Not your flyer")
        await db.execute(
            "INSERT OR IGNORE INTO flyer_access (flyer_id, user_email) VALUES (?, ?)",
            (flyer_id, target),
        )
        await db.commit()
        rows = await db.execute_fetchall(
            "SELECT user_email FROM flyer_access WHERE flyer_id = ?", (flyer_id,)
        )
        return {"ok": True, "access_list": [r["user_email"] for r in rows]}
    finally:
        await db.close()


@app.delete("/api/my/flyers/{flyer_id}/access/{target_email}")
async def revoke_access(request: Request, flyer_id: int, target_email: str):
    email = require_auth(request)
    db = await get_db()
    try:
        flyer = await fetch_flyer(db, flyer_id)
        if not flyer:
            raise HTTPException(status_code=404, detail="Flyer not found")
        if flyer["owner_email"] != email and not await is_admin(email):
            raise HTTPException(status_code=403, detail="Not your flyer")
        await db.execute(
            "DELETE FROM flyer_access WHERE flyer_id = ? AND user_email = ?",
            (flyer_id, target_email.lower()),
        )
        await db.commit()
        return {"ok": True}
    finally:
        await db.close()


# --- Admin ---

@app.get("/api/admin/flyers")
async def admin_flyers(request: Request):
    await require_admin(request)
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT * FROM flyers ORDER BY created_at DESC"
        )
        return {"flyers": [flyer_dict(r) for r in rows]}
    finally:
        await db.close()


@app.delete("/api/admin/flyers/{flyer_id}")
async def admin_delete_flyer(request: Request, flyer_id: int):
    await require_admin(request)
    db = await get_db()
    try:
        flyer = await fetch_flyer(db, flyer_id)
        if not flyer:
            raise HTTPException(status_code=404, detail="Flyer not found")
        await db.execute("DELETE FROM flyer_access WHERE flyer_id = ?", (flyer_id,))
        await db.execute("DELETE FROM flyer_views WHERE flyer_id = ?", (flyer_id,))
        await db.execute("DELETE FROM flyers WHERE id = ?", (flyer_id,))
        await db.commit()
        path = os.path.join(FLYERS_DIR, flyer["html_filename"])
        if os.path.isfile(path):
            os.remove(path)
        return {"ok": True}
    finally:
        await db.close()


@app.get("/api/admin/stats")
async def admin_stats(request: Request):
    await require_admin(request)
    db = await get_db()
    try:
        total_flyers = (await db.execute_fetchall("SELECT COUNT(*) c FROM flyers"))[0]["c"]
        total_views = (await db.execute_fetchall("SELECT COALESCE(SUM(view_count),0) c FROM flyers"))[0]["c"]
        active_users = (await db.execute_fetchall(
            "SELECT COUNT(DISTINCT owner_email) c FROM flyers"))[0]["c"]
        public_count = (await db.execute_fetchall(
            "SELECT COUNT(*) c FROM flyers WHERE is_public = 1"))[0]["c"]
        top = await db.execute_fetchall(
            "SELECT id, title, owner_email, view_count FROM flyers "
            "ORDER BY view_count DESC LIMIT 10"
        )
        return {
            "total_flyers": total_flyers,
            "total_views": total_views,
            "active_users": active_users,
            "public_flyers": public_count,
            "private_flyers": total_flyers - public_count,
            "top_flyers": [
                {"id": r["id"], "title": r["title"],
                 "owner_email": r["owner_email"], "view_count": r["view_count"]}
                for r in top
            ],
        }
    finally:
        await db.close()


# --- Static frontend (nginx serves this in prod; included for standalone dev) ---

@app.get("/", response_class=HTMLResponse)
async def root():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/{full_path:path}", response_class=HTMLResponse)
async def spa_fallback(full_path: str):
    # Serve the SPA shell for any non-API path (deep links like view/5)
    index = os.path.join(STATIC_DIR, "index.html")
    if os.path.isfile(index):
        return FileResponse(index)
    raise HTTPException(status_code=404, detail="Not found")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8013)
