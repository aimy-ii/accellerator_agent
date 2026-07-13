"""Markdown → Word.

Бэкенд акселератора принимает только pdf/doc/docx/xls/xlsx/zip/png/jpg —
markdown он отклоняет. Поэтому ТЗ уходит в проект как .docx.

Конвертация — через pandoc (pypandoc-binary: бинарь в колесе, ставить в систему
ничего не надо). Никакого ручного разбора markdown: pandoc сам делает заголовки
стилями Word, списки — списками, таблицы — настоящими таблицами Word.
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def markdown_to_docx(markdown: str) -> bytes:
    """Конвертирует markdown-текст ТЗ в .docx и возвращает байты файла."""
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
        data = out.read_bytes()

    log.info("ТЗ сконвертировано в docx: %d байт", len(data))
    return data