"""State графа и Context.

ВАЖНО про токен: пользовательский JWT живёт в Context (configurable), а НЕ в State.
State уходит в чекпоинт БД — держать там токен нельзя. Context передаётся на каждый
run заново, в чекпоинт не пишется.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, Optional

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class Context(TypedDict, total=False):
    """Параметры run'а. Передаются при каждом вызове графа.

    user_token: JWT заказчика. Инструменты ходят в API акселератора от его лица.
        Обновляется на КАЖДЫЙ запрос — так он не протухает внутри диалога.
    api_base_url: адрес акселератора (по умолчанию из .env).
    """

    user_token: str
    api_base_url: str


class AgentState(TypedDict, total=False):
    """Общая память графа."""

    # ── диалог ──────────────────────────────────────────────────────────────
    messages: Annotated[list[BaseMessage], add_messages]

    # ── проекты заказчика (шаг 1) ───────────────────────────────────────────
    projects: list[dict]            # список проектов заказчика (из API)
    mode: Optional[Literal["create", "edit"]]
    target_project_id: Optional[int]  # при create появляется только после POST

    # ── скрытый контекст существующего ТЗ (режим edit) ──────────────────────
    # Заказчик этого в чате НЕ видит — ИИ просто «уже знаком» с проектом.
    existing_spec_text: Optional[str]
    existing_spec_file: Optional[dict]

    # ── сбор требований ─────────────────────────────────────────────────────
    question_rounds: int            # сколько ходов вопросов уже задали
    coverage: Optional[str]         # thin | workable | rich
    missing_topics: list[str]
    ready_to_generate: bool         # заказчик сказал «хватит, пиши ТЗ»

    # ── ТЗ ──────────────────────────────────────────────────────────────────
    spec_title: Optional[str]
    spec_summary: Optional[str]
    spec_text: Optional[str]        # markdown ТЗ (итог)
    spec_assumptions: list[str]
    spec_changes: list[str]         # что изменилось (режим edit)
    roles_needed: list[str]
    spec_file_url: Optional[str]    # ТЗ, прикреплённое к проекту
    spec_confirmed: bool            # заказчик подтвердил ТЗ (роутер читает это, не текст)

    # ── команда (пока ТОЛЬКО в state, в БД не пишем) ────────────────────────
    team: list[dict]                # [{role, candidates: [...]}, ...]
    candidate_ids: list[int]        # id всех показанных — exclude для «подобрать ещё»
    wants_more_candidates: bool     # заказчик нажал «подобрать ещё»

    # ── презентация ─────────────────────────────────────────────────────────
    presentation: Optional[dict]

    # ── финал ───────────────────────────────────────────────────────────────
    finished: bool                  # все узлы пройдены — просим завести новый чат
    error: Optional[str]


def new_state_defaults() -> dict[str, Any]:
    """Дефолты для полей-списков, чтобы узлы не падали на None."""
    return {
        "projects": [],
        "missing_topics": [],
        "spec_assumptions": [],
        "spec_changes": [],
        "roles_needed": [],
        "team": [],
        "candidate_ids": [],
        "question_rounds": 0,
        "ready_to_generate": False,
        "spec_confirmed": False,
        "wants_more_candidates": False,
        "finished": False,
    }
