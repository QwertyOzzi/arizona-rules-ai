"""Persistent rules database with SQLite FTS5 full-text search."""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

CATEGORIES = ["Правила проекта", "Государственные структуры", "ФБР", "Законодательство", "Другое"]
STOP_WORDS = set("что такое как какие какая какой когда где для про это тот при его мне меня есть все из под над или также либо еще уже ли же не на по от за ко со во у к о об их мы вы он она они ну правила правило".split())


def database_path() -> Path:
    folder = Path(os.getenv("DATA_DIR", "./data"))
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "rules.sqlite3"


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(database_path(), timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def initialize() -> None:
    with connect() as db:
        db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                source_url TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                char_count INTEGER NOT NULL,
                chunk_count INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_documents_category ON documents(category);
            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                ordinal INTEGER NOT NULL,
                content TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_chunks_document_id ON chunks(document_id);
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                chunk_id UNINDEXED, title, content,
                tokenize='unicode61 remove_diacritics 2'
            );
        """)


def categorize(title: str, body: str) -> str:
    heading = title.lower()
    start = body[:1800].lower()
    if re.search(r"\b(фбр|fbi|федеральн\w+ бюро)\b", heading):
        return "ФБР"
    if re.search(r"\b(у(к|п)\b|кодекс|закон|конституц|постановлен|юрисдикц)", heading):
        return "Законодательство"
    if re.search(r"\b(госс|гос\w+ структур|полици|лспд|сфпд|армия|мэрия|фракц|мз|правительств|департамент)", heading):
        return "Государственные структуры"
    if re.search(r"\b(общие правила|правила сервера|правила проекта|правила игроков|основные правила)", heading):
        return "Правила проекта"
    if "fbi" in start or "фбр" in start:
        return "ФБР"
    if "государственн" in start or "госс" in start:
        return "Государственные структуры"
    if "правила проекта" in start or "правила сервера" in start:
        return "Правила проекта"
    return "Другое"


def split_chunks(text: str, limit: int = 1250, overlap: int = 130) -> list[str]:
    """Split by paragraphs, then by character window for oversized paragraphs."""
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    chunks = []
    current = ""
    for paragraph in paragraphs:
        sections = [paragraph] if len(paragraph) <= limit else [
            paragraph[i:i + limit] for i in range(0, len(paragraph), limit - overlap)
        ]
        for segment in sections:
            if current and len(current) + len(segment) + 2 > limit:
                chunks.append(current)
                current = current[-overlap:].strip() if len(current) > overlap else ""
            if current and len(current) + len(segment) + 2 > limit:
                chunks.append(current)
                current = ""
            current = (current + "\n\n" + segment).strip() if current else segment
    if current:
        chunks.append(current)
    return [chunk for chunk in chunks if len(chunk.strip()) >= 15]


def insert_document(title: str, text: str, source_url: str = "", category: str = "Автоматически") -> dict:
    title = (title or "Без названия").strip()[:220]
    text = text.replace("\x00", "").replace("\r\n", "\n").strip()
    if len(text) < 30:
        raise ValueError("Слишком мало текста: минимум 30 символов.")
    if len(text) > 800_000:
        raise ValueError("Один документ не может превышать 800 000 символов.")
    parts = split_chunks(text)
    if not parts:
        raise ValueError("Не удалось выделить текстовые фрагменты.")
    group = category if category in CATEGORIES else categorize(title, text)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    with connect() as db:
        existing = db.execute("SELECT id, title FROM documents WHERE sha256 = ?", (digest,)).fetchone()
        if existing:
            return {"id": existing["id"], "title": existing["title"], "category": group, "chunks": len(parts), "duplicate": True}
        if source_url:
            previous = db.execute("SELECT id FROM documents WHERE source_url = ?", (source_url,)).fetchall()
            for row in previous:
                db.execute("DELETE FROM chunks_fts WHERE chunk_id IN (SELECT id FROM chunks WHERE document_id = ?)", (row["id"],))
                db.execute("DELETE FROM documents WHERE id = ?", (row["id"],))
        cursor = db.execute(
            "INSERT INTO documents(title,source_url,category,sha256,char_count,chunk_count,created_at) VALUES (?,?,?,?,?,?,?)",
            (title, source_url, group, digest, len(text), len(parts), datetime.now(timezone.utc).isoformat())
        )
        doc_id = cursor.lastrowid
        for index, part in enumerate(parts):
            chunk_id = db.execute("INSERT INTO chunks(document_id,ordinal,content) VALUES (?,?,?)", (doc_id, index, part)).lastrowid
            db.execute("INSERT INTO chunks_fts(chunk_id,title,content) VALUES (?,?,?)", (chunk_id, title, part))
    return {"id": doc_id, "title": title, "category": group, "chunks": len(parts), "duplicate": False}


def list_documents() -> list[dict]:
    with connect() as db:
        rows = db.execute("SELECT id,title,source_url,category,char_count,chunk_count,created_at FROM documents ORDER BY id DESC").fetchall()
    return [dict(row) for row in rows]


def delete_document(doc_id: int) -> bool:
    with connect() as db:
        db.execute("DELETE FROM chunks_fts WHERE chunk_id IN (SELECT id FROM chunks WHERE document_id = ?)", (doc_id,))
        result = db.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    return result.rowcount > 0


def stats() -> dict:
    with connect() as db:
        count = db.execute("SELECT COUNT(*) AS n, COALESCE(SUM(chunk_count),0) AS chunks FROM documents").fetchone()
        categories = db.execute("SELECT category,COUNT(*) AS n FROM documents GROUP BY category ORDER BY n DESC").fetchall()
    return {"documents": count["n"], "chunks": count["chunks"], "categories": [dict(row) for row in categories]}


def search(question: str, limit: int = 7) -> list[dict]:
    words = re.findall(r"[\wа-яё]+", question.lower(), re.UNICODE)
    words = [word for word in words if len(word) >= 3 and word not in STOP_WORDS][:12]
    if not words:
        return []
    # Prefix terms are helpful for Russian word endings (силы / сила, стадии / стадия).
    prefixes = list(dict.fromkeys(word[:max(3, len(word) - (2 if len(word) >= 8 else 1))] for word in words))
    fts_query = " OR ".join(f'"{prefix}"*' for prefix in prefixes)
    with connect() as db:
        rows = db.execute("""
            SELECT c.id AS chunk_id,c.content,d.id AS document_id,d.title,d.category,d.source_url,
                   bm25(chunks_fts, 0.0, 3.0, 1.0) AS rank
            FROM chunks_fts
            JOIN chunks c ON c.id = chunks_fts.chunk_id
            JOIN documents d ON d.id = c.document_id
            WHERE chunks_fts MATCH ?
            ORDER BY rank ASC LIMIT ?
        """, (fts_query, limit)).fetchall()
    return [dict(row) for row in rows]
