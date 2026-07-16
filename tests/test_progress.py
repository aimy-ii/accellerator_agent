"""Тесты custom-стрима прогресса: phase и token."""
from __future__ import annotations

import langgraph.config as lg_config

from src.graph.progress import emit, emit_token


def test_emit_default_phase_done(monkeypatch):
    payloads = []
    monkeypatch.setattr(lg_config, "get_stream_writer", lambda: payloads.append)

    emit("s", "t")
    assert payloads == [{"stage": "s", "phase": "done", "text": "t"}]


def test_emit_phase_start(monkeypatch):
    payloads = []
    monkeypatch.setattr(lg_config, "get_stream_writer", lambda: payloads.append)

    emit("s", "t", "start")
    assert payloads == [{"stage": "s", "phase": "start", "text": "t"}]


def test_emit_token(monkeypatch):
    payloads = []
    monkeypatch.setattr(lg_config, "get_stream_writer", lambda: payloads.append)

    emit_token("abc")
    assert payloads == [{"token": "abc"}]

    emit_token("")
    assert payloads == [{"token": "abc"}]


def test_emit_no_writer_raises(monkeypatch):
    def _boom():
        raise RuntimeError("no writer")

    monkeypatch.setattr(lg_config, "get_stream_writer", _boom)
    emit("s", "t")
    emit_token("x")


def test_emit_writer_none(monkeypatch):
    monkeypatch.setattr(lg_config, "get_stream_writer", lambda: None)
    emit("s", "t")
    emit_token("x")
