"""Basic signup/login with SQLite (stdlib only) and cookie sessions."""
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
from html import escape

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

DB_PATH = os.environ.get("AUTH_DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "users.db"))
COOKIE = "session"
SESSION_TTL = 7 * 24 * 3600
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")

router = APIRouter()


def _db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL COLLATE NOCASE, pw_hash TEXT NOT NULL, created_at REAL NOT NULL)")
    con.execute("CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires_at REAL NOT NULL)")
    return con


def _hash(password: str, salt: bytes = None) -> str:
    salt = salt or os.urandom(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return salt.hex() + "$" + h.hex()


def _verify(password: str, stored: str) -> bool:
    salt, h = stored.split("$")
    return hmac.compare_digest(_hash(password, bytes.fromhex(salt)).split("$")[1], h)


def current_user(request: Request):
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    with _db() as con:
        row = con.execute("SELECT u.id, u.username FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token = ? AND s.expires_at > ?", (token, time.time())).fetchone()
    return dict(row) if row else None


def _start_session(request: Request, user_id: int, to: str = "/"):
    token = secrets.token_urlsafe(32)
    with _db() as con:
        con.execute("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))
        con.execute("INSERT INTO sessions VALUES (?,?,?)", (token, user_id, time.time() + SESSION_TTL))
    resp = RedirectResponse(to, status_code=303)
    secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    resp.set_cookie(COOKIE, token, max_age=SESSION_TTL, httponly=True, samesite="lax", secure=secure)
    return resp


def _page(title, action, other_href, other_text, error=""):
    err = f'<p class="err">{escape(error)}</p>' if error else ""
    return HTMLResponse(f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} - GeradorShorts</title><style>
body{{font-family:system-ui,sans-serif;background:#0f1115;color:#e8eaed;display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0}}
form{{background:#181b22;padding:2rem;border-radius:12px;width:320px;display:flex;flex-direction:column;gap:.8rem}}
input,button{{padding:.7rem;border-radius:8px;border:1px solid #333;background:#0f1115;color:inherit;font-size:1rem}}
button{{background:#6d5dfc;border:0;cursor:pointer}}a{{color:#9a8fff}}.err{{color:#ff6b6b;margin:0}}</style></head><body>
<form method="post" action="{action}"><h2 style="margin:0">{title}</h2>{err}
<input name="username" placeholder="Usuário" autocomplete="username" required>
<input name="password" type="password" placeholder="Senha (mín. 8 caracteres)" autocomplete="current-password" required>
<button type="submit">{title}</button><a href="{other_href}">{other_text}</a></form></body></html>""")


@router.get("/cadastro", response_class=HTMLResponse)
async def signup_page():
    return _page("Cadastro", "/cadastro", "/login", "Já tenho conta")


@router.post("/cadastro")
async def signup(request: Request, username: str = Form(...), password: str = Form(...)):
    username = username.strip()
    if not USERNAME_RE.match(username):
        return _page("Cadastro", "/cadastro", "/login", "Já tenho conta", "Usuário: 3-32 caracteres (letras, números, _ . -).")
    if len(password) < 8:
        return _page("Cadastro", "/cadastro", "/login", "Já tenho conta", "A senha precisa ter ao menos 8 caracteres.")
    try:
        with _db() as con:
            cur = con.execute("INSERT INTO users (username, pw_hash, created_at) VALUES (?,?,?)", (username, _hash(password), time.time()))
            uid = cur.lastrowid
    except sqlite3.IntegrityError:
        return _page("Cadastro", "/cadastro", "/login", "Já tenho conta", "Esse usuário já existe.")
    return _start_session(request, uid)


@router.get("/login", response_class=HTMLResponse)
async def login_page():
    return _page("Login", "/login", "/cadastro", "Criar conta")


@router.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    with _db() as con:
        row = con.execute("SELECT id, pw_hash FROM users WHERE username = ?", (username.strip(),)).fetchone()
    if not row or not _verify(password, row["pw_hash"]):
        return _page("Login", "/login", "/cadastro", "Criar conta", "Usuário ou senha inválidos.")
    return _start_session(request, row["id"])


@router.post("/logout")
async def logout(request: Request):
    token = request.cookies.get(COOKIE)
    if token:
        with _db() as con:
            con.execute("DELETE FROM sessions WHERE token = ?", (token,))
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE)
    return resp


@router.get("/api/me")
async def me(request: Request):
    user = current_user(request)
    if not user:
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)
    return {"id": user["id"], "username": user["username"]}


@router.get("/health")
async def health():
    return {"status": "ok"}
