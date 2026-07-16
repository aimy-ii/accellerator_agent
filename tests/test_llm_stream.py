"""Тесты astream_structured: дельты текстового поля и фолбэк."""
from __future__ import annotations

import src.utils.llm_gen as llm_gen


class _FakeStructured:
    def __init__(self, chunks):
        self._chunks = chunks
        self.astream_called = False

    async def astream(self, messages):
        self.astream_called = True
        for chunk in self._chunks:
            yield chunk


async def test_astream_structured_deltas():
    chunks = [
        {"tech_spec_text": "Ab"},
        {"tech_spec_text": "Abcd"},
        {"tech_spec_text": "Abcdef"},
    ]
    structured = _FakeStructured(chunks)
    deltas: list[str] = []

    result = await llm_gen.astream_structured(
        structured,
        [],
        on_text_delta=deltas.append,
        text_field="tech_spec_text",
    )

    assert "".join(deltas) == "Abcdef"
    assert result == chunks[-1]


async def test_astream_structured_fallback_on_error(monkeypatch):
    class _Broken:
        async def astream(self, messages):
            raise RuntimeError("stream failed")
            yield  # noqa: RET503 — делаем async generator

    deltas: list[str] = []
    marker = {"ok": True}

    async def _fake_ainvoke(structured, messages):
        return marker

    monkeypatch.setattr(llm_gen, "ainvoke_llm", _fake_ainvoke)

    result = await llm_gen.astream_structured(
        _Broken(),
        [],
        on_text_delta=deltas.append,
        text_field="tech_spec_text",
    )

    assert result is marker
    assert deltas == []


async def test_astream_structured_no_callback_uses_ainvoke(monkeypatch):
    structured = _FakeStructured([{"tech_spec_text": "Ab"}])
    marker = {"via": "ainvoke"}

    async def _fake_ainvoke(s, messages):
        return marker

    monkeypatch.setattr(llm_gen, "ainvoke_llm", _fake_ainvoke)

    result = await llm_gen.astream_structured(
        structured,
        [],
        on_text_delta=None,
        text_field="tech_spec_text",
    )

    assert result is marker
    assert structured.astream_called is False
