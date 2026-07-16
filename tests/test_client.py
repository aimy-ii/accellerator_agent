"""Тесты HTTP-клиента: путь и тело запросов + отсутствие блокирующей ФС.

Сеть не трогаем — подменяем httpx.AsyncClient на MockTransport, который ловит
исходящий запрос и отдаёт заранее заданный ответ.
"""
from __future__ import annotations

import inspect
import json

import httpx

import src.api.client as client_module
from src.api.client import AcceleratorAPI


def _patch_transport(monkeypatch, handler):
    """Заменяет httpx.AsyncClient так, чтобы запросы шли в MockTransport."""
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        for drop in ("verify", "trust_env"):
            kwargs.pop(drop, None)
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(client_module.httpx, "AsyncClient", factory)


async def test_list_project_invitations_path_and_parse(monkeypatch):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        return httpx.Response(
            200, json=[{"id": 1, "status": "potential"}, {"id": 2, "status": "accepted"}]
        )

    _patch_transport(monkeypatch, handler)
    api = AcceleratorAPI("tok", base_url="http://acc")

    data = await api.list_project_invitations(70)

    assert captured["method"] == "GET"
    assert captured["path"] == "/api/projects/70/invitations"
    assert len(data) == 2
    assert data[0]["status"] == "potential"


async def test_add_candidates_posts_intern_ids(monkeypatch):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"created": [1, 2], "skipped": []})

    _patch_transport(monkeypatch, handler)
    api = AcceleratorAPI("tok", base_url="http://acc")

    result = await api.add_candidates(70, [1, 2])

    assert captured["method"] == "POST"
    assert captured["path"] == "/api/projects/70/candidates"
    assert captured["body"] == {"intern_ids": [1, 2]}
    assert result["created"] == [1, 2]


async def test_invitations_empty_on_no_content(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"")

    _patch_transport(monkeypatch, handler)
    api = AcceleratorAPI("tok", base_url="http://acc")

    assert await api.list_project_invitations(70) == []


def test_download_file_has_no_sync_fs_in_event_loop():
    """Регрессия: mkdir/exists не должны вызываться синхронно в async-методе.

    Именно это ловил LangGraph как «Blocking call to os.mkdir» и ронял узел.
    Файловая возня вынесена в _read_cache и уходит в asyncio.to_thread.
    """
    src = inspect.getsource(AcceleratorAPI.download_file)
    assert ".mkdir(" not in src
    assert ".exists()" not in src
    assert "_read_cache" in src
    assert "to_thread" in src
