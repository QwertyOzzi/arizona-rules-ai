"""Bounded parsing for uploads and public Arizona RP forum pages."""
from __future__ import annotations

import io
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from docx import Document
from pypdf import PdfReader

MAX_BYTES = 8 * 1024 * 1024
EXTENSIONS = {".txt", ".md", ".pdf", ".docx", ".html", ".htm"}


def parse_html(raw: str, title_fallback: str = "Страница форума") -> tuple[str, str]:
    soup = BeautifulSoup(raw, "html.parser")
    title_tag = soup.find("h1") or soup.find("title")
    title = title_tag.get_text(" ", strip=True) if title_tag else title_fallback
    # XenForo normally stores the post text in .bbWrapper containers.
    posts = soup.select("article.message .bbWrapper, .message-body .bbWrapper, .message-content .bbWrapper")
    if posts:
        text = "\n\n".join(post.get_text("\n", strip=True) for post in posts)
    else:
        for element in soup(["script", "style", "noscript", "nav", "header", "footer", "aside", "form"]):
            element.decompose()
        main = soup.select_one("main") or soup.select_one("article") or soup.body or soup
        text = main.get_text("\n", strip=True)
    return title[:220], text


def extract_file(filename: str, payload: bytes) -> tuple[str, str]:
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix not in EXTENSIONS:
        raise ValueError("Поддерживаются TXT, MD, HTML, PDF и DOCX.")
    if len(payload) > MAX_BYTES:
        raise ValueError("Размер файла больше 8 МБ.")
    if suffix in {".txt", ".md"}:
        body = payload.decode("utf-8-sig", errors="replace")
    elif suffix in {".html", ".htm"}:
        _, body = parse_html(payload.decode("utf-8-sig", errors="replace"), filename)
    elif suffix == ".pdf":
        reader = PdfReader(io.BytesIO(payload))
        if len(reader.pages) > 250:
            raise ValueError("PDF содержит более 250 страниц. Раздели его на части.")
        body = "\n\n".join((page.extract_text() or "") for page in reader.pages)
    else:
        document = Document(io.BytesIO(payload))
        body = "\n\n".join(p.text for p in document.paragraphs if p.text.strip())
        for table in document.tables:
            for row in table.rows:
                body += "\n" + " | ".join(cell.text for cell in row.cells)
    return filename.rsplit(".", 1)[0][:220], body


def validate_forum_url(value: str) -> str:
    value = value.strip()
    parsed = urlparse(value)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != "forum.arizona-rp.com":
        raise ValueError("Для загрузки по ссылке разрешён только https://forum.arizona-rp.com/.")
    if parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError("Некорректная ссылка форума.")
    return value


async def fetch_forum_page(url: str) -> tuple[str, str]:
    url = validate_forum_url(url)
    headers = {"User-Agent": "ArizonaRulesKnowledgeBase/1.0 (+manual public rules import)", "Accept": "text/html"}
    async with httpx.AsyncClient(timeout=12, follow_redirects=False, headers=headers) as client:
        async with client.stream("GET", url) as response:
            if 300 <= response.status_code < 400:
                raise ValueError("Форум перенаправил запрос. Открой ссылку и скопируй текст вручную.")
            if response.status_code != 200:
                raise ValueError(f"Форум вернул HTTP {response.status_code}. Можно вставить текст вручную.")
            if "text/html" not in response.headers.get("content-type", ""):
                raise ValueError("Ссылка не ведёт на HTML-страницу с правилами.")
            buffer = bytearray()
            async for part in response.aiter_bytes():
                buffer.extend(part)
                if len(buffer) > 3 * 1024 * 1024:
                    raise ValueError("Страница слишком большая, загрузи текст файлом.")
    return parse_html(buffer.decode("utf-8", errors="replace"))
