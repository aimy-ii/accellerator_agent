"""Прогресс работы агента.

Узлы пишут события в стрим — фронт видит, что происходит прямо сейчас,
а не смотрит в пустоту, пока крутится LLM.

Фронт подписывается так:
    async for mode, chunk in client.runs.stream(
        thread_id, "customer_agent", input=..., stream_mode=["updates", "custom"]
    ):
        if mode == "custom":
            chunk == {"stage": "match_team", "text": "Ищу: Backend-разработчик…"}

В Studio эти события видны в панели прогона.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def emit(stage: str, text: str) -> None:
    """Публикует шаг работы агента: и в стрим (фронту), и в лог (нам).

    Работает только внутри узла графа. Вне графа (тесты, скрипты) — молча
    пишет в лог, не падая.
    """
    log.info("[%s] %s", stage, text)
    try:
        from langgraph.config import get_stream_writer

        writer = get_stream_writer()
    except Exception:  # noqa: BLE001
        return
    if writer is None:
        return
    try:
        writer({"stage": stage, "text": text})
    except Exception:  # noqa: BLE001
        pass


def progress_for(stage: str):
    """Возвращает колбэк прогресса, привязанный к этапу.

    Чтобы сервисы (подбор команды) докладывали, не зная про LangGraph.
    """

    def _say(text: str) -> None:
        emit(stage, text)

    return _say