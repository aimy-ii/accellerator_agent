"""Фикстуры для тестов агента.

Тесты не ходят в сеть и не дёргают LLM: проверяем детерминированную логику
(маршруты графа, сборку сводки) и построение HTTP-запросов клиента через
httpx.MockTransport.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def project_with_spec() -> dict:
    """Проект с прикреплённым файлом ТЗ и составом специалистов."""
    return {
        "id": 70,
        "title": "Бот для записи к врачу",
        "description": "Телеграм-бот, который помогает пациентам записываться на приём.",
        "status": "active",
        "specialists_count": 3,
        "views_count": 12,
        "responses_count": 4,
        "required_specialists": [
            {"id": 1, "profession_id": 10, "count": 1, "profession_name": "Backend-разработчик"},
            {"id": 2, "profession_id": 11, "count": 1, "profession_name": "Frontend-разработчик"},
        ],
        "files": [
            {
                "id": 5,
                "file_name": "TZ_bot_20260101.docx",
                "file_url": "/files/tz.docx",
                "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            }
        ],
    }


@pytest.fixture
def project_no_spec() -> dict:
    """Проект без файла ТЗ и без состава специалистов (заведён ассистентом)."""
    return {
        "id": 71,
        "title": "Маркетплейс услуг",
        "description": "",
        "status": "active",
        "specialists_count": 0,
        "views_count": 0,
        "responses_count": 0,
        "required_specialists": [],
        "files": [],
    }


@pytest.fixture
def invitations_sample() -> list[dict]:
    """Приглашения проекта со всеми статусами."""
    return [
        {"id": 1, "status": "potential"},
        {"id": 2, "status": "potential"},
        {"id": 3, "status": "invited"},
        {"id": 4, "status": "accepted"},
        {"id": 5, "status": "declined"},
    ]
