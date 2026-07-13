"""Извлечение текста из файла ТЗ проекта.

ТЗ агент сохраняет как .md, но заказчик мог приложить .docx/.pdf —
поддерживаем оба, чтобы поднять текст старого ТЗ в контекст диалога.
"""
from __future__ import annotations

import asyncio
import io
import logging

log = logging.getLogger(__name__)

_TEXT_EXT = (".md", ".markdown", ".txt")
_SPEC_HINTS = ("тз", "техзадание", "tech", "spec", "техническое")


def _is_pdf(data: bytes) -> bool:
    return data[:5] == b"%PDF-"


def _is_docx(data: bytes) -> bool:
    return data[:2] == b"PK"


async def extract_text(data: bytes, file_name: str = "") -> str:
    """Возвращает текст документа.

    pdfplumber и python-docx синхронные и тяжёлые — уходят в поток, чтобы не
    блокировать event loop (LangGraph такое отлавливает и роняет узел).
    """
    return await asyncio.to_thread(_extract_sync, data, file_name)


def _extract_sync(data: bytes, file_name: str = "") -> str:
    """Синхронное извлечение. Вызывать ТОЛЬКО в потоке."""
    name = (file_name or "").lower()

    if _is_pdf(data):
        return _extract_pdf(data)
    if _is_docx(data) and name.endswith(".docx"):
        return _extract_docx(data)
    if name.endswith(_TEXT_EXT) or not (_is_pdf(data) or _is_docx(data)):
        return data.decode("utf-8", errors="replace")

    log.warning("Неизвестный формат файла %s — пробую как текст", file_name)
    return data.decode("utf-8", errors="replace")


def _extract_pdf(data: bytes) -> str:
    try:
        import pdfplumber
    except ImportError:  # noqa: BLE001
        log.warning("pdfplumber не установлен — PDF-ТЗ пропущено")
        return ""
    parts: list[str] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            parts.append(page.extract_text() or "")
    return "\n".join(parts).strip()


def _extract_docx(data: bytes) -> str:
    try:
        from docx import Document
    except ImportError:  # noqa: BLE001
        log.warning("python-docx не установлен — DOCX-ТЗ пропущено")
        return ""
    document = Document(io.BytesIO(data))
    return "\n".join(p.text for p in document.paragraphs).strip()


def pick_spec_file(files: list[dict]) -> dict | None:
    """Выбирает из файлов проекта тот, что похож на ТЗ.

    Приоритет: имя содержит «тз/техзадание/spec» → иначе первый .md → иначе первый файл.
    """
    if not files:
        return None

    for f in files:
        name = (f.get("file_name") or "").lower()
        if any(hint in name for hint in _SPEC_HINTS):
            return f

    for f in files:
        if (f.get("file_name") or "").lower().endswith(_TEXT_EXT):
            return f

    return files[0]