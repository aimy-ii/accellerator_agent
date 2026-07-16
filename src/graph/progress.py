"""Прогресс работы агента.

Узлы пишут события в стрим — фронт видит, что происходит прямо сейчас,
а не смотрит в пустоту, пока крутится LLM.

Custom-стрим принимает ровно два вида полезной нагрузки:

    {"stage": "generate_spec", "phase": "start", "text": "Пишу техническое задание…"}
    {"token": "…кусок текста ответа…"}

- phase ∈ "start" | "done": start — агент собирается делать шаг; done — сделал.
- token — дельта прироста текста ответа (печать как в ChatGPT).

Фронт подписывается так:
    async for mode, chunk in client.runs.stream(
        thread_id, "customer_agent", input=..., stream_mode=["updates", "custom"]
    ):
        if mode == "custom":
            # либо {"stage", "phase", "text"}, либо {"token"}

В Studio эти события видны в панели прогона.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def emit(stage: str, text: str, phase: str = "done") -> None:
    """Публикует шаг работы агента: и в стрим (фронту), и в лог (нам).

    phase: "start" — агент собирается делать шаг; "done" — шаг завершён
    (мгновенные подтверждения/эхо выбора — тоже "done", это дефолт).

    Payload в custom-стрим: {"stage", "phase", "text"}.
    Работает только внутри узла графа; вне графа — молча пишет в лог.
    """
    log.info("[%s|%s] %s", stage, phase, text)
    try:
        from langgraph.config import get_stream_writer

        writer = get_stream_writer()
    except Exception:  # noqa: BLE001
        return
    if writer is None:
        return
    try:
        writer({"stage": stage, "phase": phase, "text": text})
    except Exception:  # noqa: BLE001
        pass


def emit_token(text: str) -> None:
    """Публикует дельту текста ответа ассистента (потокенная печать).

    Payload в custom-стрим: {"token": text}. Пустые дельты не шлём.
    Работает только внутри узла графа; вне графа — молча выходит.
    """
    if not text:
        return
    try:
        from langgraph.config import get_stream_writer

        writer = get_stream_writer()
    except Exception:  # noqa: BLE001
        return
    if writer is None:
        return
    try:
        writer({"token": text})
    except Exception:  # noqa: BLE001
        pass


def progress_for(stage: str):
    """Возвращает колбэк прогресса, привязанный к этапу.

    Чтобы сервисы (подбор команды) докладывали, не зная про LangGraph.
    Гранулярные строки («Ищу: …») идут как phase="start".
    """

    def _say(text: str) -> None:
        emit(stage, text, "start")

    return _say
