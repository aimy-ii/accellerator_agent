"""Выбор ТЗ из списка файлов проекта: приоритет hints, защита от pptx-fallback."""
from __future__ import annotations

from src.api.documents import is_presentation_file, pick_spec_file


DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
PPTX_MIME = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)


def test_pick_spec_prefers_tz_prefix_over_presentation():
    files = [
        {"file_name": "presentation_28.pptx", "mime_type": PPTX_MIME},
        {"file_name": "TZ_parser_20260719.docx", "mime_type": DOCX_MIME},
    ]
    assert pick_spec_file(files)["file_name"] == "TZ_parser_20260719.docx"


def test_pick_spec_returns_none_when_only_presentation():
    # Fallback НЕ должен вернуть pptx — extract_text примет его за docx.
    files = [{"file_name": "slides.pptx", "mime_type": PPTX_MIME}]
    assert pick_spec_file(files) is None


def test_pick_spec_fallback_takes_non_presentation_when_no_hints():
    files = [
        {"file_name": "presentation_28.pptx", "mime_type": PPTX_MIME},
        {"file_name": "brief.docx", "mime_type": DOCX_MIME},
    ]
    # Ни у одного нет hint «тз/spec/…», нет .md/.txt — берём первый не-презентация.
    assert pick_spec_file(files)["file_name"] == "brief.docx"


def test_pick_spec_prefers_markdown_over_docx_in_fallback():
    files = [
        {"file_name": "notes.md", "mime_type": "text/markdown"},
        {"file_name": "brief.docx", "mime_type": DOCX_MIME},
    ]
    assert pick_spec_file(files)["file_name"] == "notes.md"


def test_pick_spec_none_on_empty_list():
    assert pick_spec_file([]) is None


def test_is_presentation_detects_by_mime():
    assert is_presentation_file({"file_name": "x", "mime_type": PPTX_MIME})


def test_is_presentation_detects_by_extension_when_mime_missing():
    assert is_presentation_file({"file_name": "slides.pptx", "mime_type": ""})


def test_is_presentation_detects_legacy_ppt():
    assert is_presentation_file(
        {"file_name": "old.ppt", "mime_type": "application/vnd.ms-powerpoint"}
    )


def test_is_presentation_false_for_docx():
    assert not is_presentation_file({"file_name": "TZ.docx", "mime_type": DOCX_MIME})
