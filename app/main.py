"""FastAPI entrypoint: private knowledge base for Arizona RP rules."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict, deque
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import ai, ingest, storage

APP_DIR = Path(__file__).resolve().parent
ENV = os.getenv("APP_ENV", "development")
PASSWORD = os.getenv("APP_PASSWORD", "")
SECRET = os.getenv("APP_SECRET") or secrets.token_urlsafe(40)
COOKIE = "arizona_rules_session"
FAILED_LOGINS: dict[str, deque[float]] = defaultdict(deque)

if ENV == "production" and (
    len(PASSWORD) < 12
    or PASSWORD == "replace-with-a-long-private-password"
    or not os.getenv("APP_SECRET")
    or os.getenv("APP_SECRET") == "replace-with-a-long-random-string"
):
    raise RuntimeError("Production requires APP_PASSWORD (12+ chars) and APP_SECRET.")

storage.initialize()
app = FastAPI(title="Arizona Rules AI", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")


class Login(BaseModel):
    password: str = Field(min_length=1, max_length=300)


class TextImport(BaseModel):
    title: str = Field(min_length=1, max_length=220)
    text: str = Field(min_length=30, max_length=800_000)
    source_url: str = Field(default="", max_length=2000)
    category: str = "Автоматически"


class UrlImport(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=8)
    category: str = "Автоматически"


class Question(BaseModel):
    question: str = Field(min_length=3, max_length=800)


def local_request(request: Request) -> bool:
    return request.client is not None and request.client.host in {"127.0.0.1", "::1", "localhost"}


def issue_session() -> str:
    expiry = int(time.time()) + 7 * 86400
    payload = f"{expiry}.{secrets.token_hex(10)}"
    signature = hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def valid_session(token: str | None) -> bool:
    if not token:
        return False
    try:
        expiry, nonce, signature = token.split(".")
        if int(expiry) <= time.time() or len(nonce) != 20:
            return False
        payload = f"{expiry}.{nonce}"
        expected = hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)
    except (ValueError, TypeError):
        return False


def is_authorized(request: Request) -> bool:
    if not PASSWORD:
        return ENV != "production" and local_request(request)
    return valid_session(request.cookies.get(COOKIE))


def require_auth(request: Request) -> None:
    if not is_authorized(request):
        raise HTTPException(status_code=401, detail="Требуется вход.")
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("origin", "")
        if origin and urlparse(origin).netloc.lower() != request.headers.get("host", "").lower():
            raise HTTPException(status_code=403, detail="Недопустимое происхождение запроса.")


@app.get("/")
def home():
    return FileResponse(APP_DIR / "static" / "index.html")


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/api/session")
def session(request: Request):
    return {"authenticated": is_authorized(request), "password_required": bool(PASSWORD)}


@app.post("/api/session")
def login(data: Login, request: Request, response: Response):
    if not PASSWORD:
        raise HTTPException(status_code=400, detail="Пароль сервера не настроен.")
    client = request.client.host if request.client else "unknown"
    now = time.monotonic()
    attempts = FAILED_LOGINS[client]
    while attempts and now - attempts[0] > 600:
        attempts.popleft()
    if len(attempts) >= 10:
        raise HTTPException(status_code=429, detail="Слишком много попыток. Повтори позже.")
    if not hmac.compare_digest(data.password.encode(), PASSWORD.encode()):
        attempts.append(now)
        raise HTTPException(status_code=401, detail="Неправильный пароль.")
    attempts.clear()
    response.set_cookie(
        COOKIE, issue_session(), max_age=7 * 86400, httponly=True,
        secure=(ENV == "production" or request.headers.get("x-forwarded-proto") == "https"),
        samesite="lax", path="/"
    )
    return {"ok": True}


@app.delete("/api/session")
def logout(response: Response):
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@app.get("/api/stats", dependencies=[Depends(require_auth)])
def stats():
    return {**storage.stats(), "gemini_enabled": bool(os.getenv("GEMINI_API_KEY", "").strip())}


@app.get("/api/documents", dependencies=[Depends(require_auth)])
def documents():
    return {"documents": storage.list_documents()}


@app.delete("/api/documents/{doc_id}", dependencies=[Depends(require_auth)])
def delete_document(doc_id: int):
    if not storage.delete_document(doc_id):
        raise HTTPException(status_code=404, detail="Документ не найден.")
    return {"ok": True}


def validate_source_url(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc or parsed.username or parsed.password:
        raise HTTPException(status_code=400, detail="Ссылка источника должна начинаться с https:// или http://.")
    return value


@app.post("/api/ingest/text", dependencies=[Depends(require_auth)])
def upload_text(data: TextImport):
    try:
        entry = storage.insert_document(data.title, data.text, validate_source_url(data.source_url), data.category)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"items": [entry], "errors": []}


@app.post("/api/ingest/files", dependencies=[Depends(require_auth)])
async def upload_files(files: list[UploadFile] = File(...), category: str = Form("Автоматически")):
    if len(files) > 12:
        raise HTTPException(status_code=400, detail="Максимум 12 файлов за раз.")
    result, errors = [], []
    for file in files:
        try:
            payload = await file.read(ingest.MAX_BYTES + 1)
            title, text = ingest.extract_file(file.filename or "unknown", payload)
            result.append(storage.insert_document(title, text, category=category))
        except Exception as error:
            # Parser errors are file-specific; do not abort other valid uploads.
            errors.append({"file": (file.filename or "unknown")[:220], "error": str(error)[:260]})
        finally:
            await file.close()
    return {"items": result, "errors": errors}


@app.post("/api/ingest/urls", dependencies=[Depends(require_auth)])
async def upload_urls(data: UrlImport):
    result, errors = [], []
    for url in data.urls:
        try:
            url = ingest.validate_forum_url(url)
            title, text = await ingest.fetch_forum_page(url)
            result.append(storage.insert_document(title, text, source_url=url, category=data.category))
        except Exception as error:
            errors.append({"file": url[:220], "error": str(error)[:260]})
    return {"items": result, "errors": errors}


@app.post("/api/chat", dependencies=[Depends(require_auth)])
async def chat(data: Question):
    matches = storage.search(data.question)
    reply, mode = await ai.answer(data.question, matches)
    sources = [
        {"ref": i, "document_id": m["document_id"], "title": m["title"],
         "category": m["category"], "url": m["source_url"], "excerpt": m["content"][:380]}
        for i, m in enumerate(matches, start=1)
    ]
    return {"answer": reply, "mode": mode, "sources": sources}
