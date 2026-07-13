"""Markdown → Word.

Бэкенд акселератора принимает только pdf/doc/docx/xls/xlsx/zip/png/jpg —
markdown он отклоняет. Поэтому ТЗ уходит в проект как .docx.

Конвертация — через pandoc (pypandoc-binary: бинарь в колесе, ставить в систему
ничего не надо). Никакого ручного разбора markdown: pandoc сам делает заголовки
стилями Word, списки — списками, таблицы — настоящими таблицами Word.

ВАЖНО: pandoc синхронный и трогает файловую систему (os.getcwd, временные файлы).
Вызывать его напрямую из async-узла нельзя — LangGraph блокирует такое как
"Blocking call to os.getcwd" (синхронный вызов в event loop тормозит весь сервер).
Поэтому конвертация уходит в отдельный поток через asyncio.to_thread.
"""
from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _convert(markdown: str) -> bytes:
    """Синхронная конвертация. Вызывать ТОЛЬКО в отдельном потоке."""
    import pypandoc

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "spec.docx"
        pypandoc.convert_text(
            markdown,
            to="docx",
            format="markdown",
            outputfile=str(out),
            extra_args=["--standalone"],
        )
        return out.read_bytes()


async def markdown_to_docx(markdown: str) -> bytes:
    """Конвертирует markdown-текст ТЗ в .docx.

    Синхронный pandoc выносится в поток — event loop не блокируется.
    """
    data = await asyncio.to_thread(_convert, markdown)
    log.info("ТЗ сконвертировано в docx: %d байт", len(data))
    return data