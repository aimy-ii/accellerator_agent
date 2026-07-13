"""Контракт пауз графа (human-in-the-loop).

ЕДИНСТВЕННОЕ, что видит фронт — объект Ask. Больше он ничего знать не обязан:
ни сценария, ни текущего узла, ни того, что будет дальше.

Правило фронта целиком:
    1. показать ask.message                      ← всегда, это реплика ассистента
    2. ask.choices непуст → кнопки
       ask.choices пуст   → одно поле ввода
    3. отправить Reply назад

ИНВАРИАНТ: message самодостаточен. Фронт, отрисовавший ТОЛЬКО message и одно
поле ввода, получает осмысленный диалог. choices/data существуют лишь для того,
чтобы то же самое показать удобнее (кнопки, карточки, документ).
Узел, который кладёт информацию в data, но не кладёт в message — сломан.
"""
from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.types import interrupt
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

# Типы пауз. Фронту нужны только чтобы решить, рисовать ли доп-виджет из data.
KIND_SELECT_PROJECT = "select_project"      # выбрать проект / создать новый
KIND_ASK_QUESTIONS = "ask_questions"        # вопросы (поле ввода)
KIND_MORE_OR_GENERATE = "more_or_generate"  # ещё вопросы или пишем ТЗ
KIND_CONFIRM_SPEC = "confirm_spec"          # подтвердить ТЗ (data: текст ТЗ)
KIND_TEAM_READY = "team_ready"              # подборка команды (data: карточки)


class Choice(BaseModel):
    """Вариант ответа — кнопка."""

    id: str | int
    title: str
    meta: dict[str, Any] = Field(default_factory=dict)


class Ask(BaseModel):
    """Пауза графа. Это и есть весь контракт с фронтом."""

    kind: str
    message: str = Field(min_length=1)
    choices: list[Choice] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)


class Reply(BaseModel):
    """Ответ пользователя.

    id   — нажал кнопку (пришёл идентификатор из choices);
    text — написал свободным текстом.
    Одно из двух заполнено; фронт волен слать любое.
    """

    id: str | int | None = None
    text: str = ""


def ask(a: Ask) -> Reply:
    """Ставит граф на паузу и возвращает разобранный ответ пользователя."""
    raw = interrupt(a.model_dump())
    return _parse_reply(raw)


def _parse_reply(raw: Any) -> Reply:
    """Приводит что угодно от фронта к Reply.

    Понимает: Reply | {"id": ...} | {"text": ...} | "строка" | 42
    """
    if isinstance(raw, Reply):
        return raw

    if isinstance(raw, dict):
        rid = raw.get("id")
        text = ""
        for key in ("text", "message", "content", "answer", "value"):
            candidate = raw.get(key)
            if isinstance(candidate, str) and candidate.strip():
                text = candidate.strip()
                break
        # {"id": "свободный текст"} — фронт мог положить текст в id
        if isinstance(rid, str) and not text and " " in rid:
            return Reply(text=rid.strip())
        return Reply(id=rid, text=text)

    if isinstance(raw, int):
        return Reply(id=raw)

    if isinstance(raw, str):
        value = raw.strip()
        if value.isdigit():
            return Reply(id=int(value))
        return Reply(id=value if " " not in value else None, text=value)

    return Reply()


# ─── резолвер: свести ответ пользователя к одному из choices ────────────────

class _Match(BaseModel):
    """Результат сопоставления свободного текста с вариантами."""

    choice_id: str | None = Field(
        default=None,
        description="id выбранного варианта из списка, либо null если не подходит ни один",
    )


_MATCH_SYSTEM = """Ты сопоставляешь ответ пользователя с предложенными вариантами.

Тебе дают список вариантов (id + название) и текст пользователя.
Определи, какой вариант он имел в виду.

Правила:
1. Верни ТОЛЬКО id из списка. Ничего не выдумывай.
2. Если ни один вариант явно не подходит — верни null. Не угадывай наугад.
3. Пользователь мог написать неточно, с ошибкой, частью названия — это нормально.

Верни строго JSON по схеме _Match. Ничего вне JSON."""


async def resolve(reply: Reply, choices: list[Choice]) -> str | int | None:
    """Сводит ответ пользователя к id одного из вариантов.

    Порядок (от дешёвого к дорогому, без эвристик на списках слов):
        1. пришёл id и он есть в choices → он;
        2. текст точно совпал с title варианта → его id;
        3. свободный текст → LLM-классификатор по списку вариантов;
        4. не разобрали → None (узел переспросит).
    """
    if not choices:
        return None

    by_id = {str(c.id): c.id for c in choices}

    if reply.id is not None and str(reply.id) in by_id:
        return by_id[str(reply.id)]

    text = reply.text.strip()
    if not text:
        return None

    lowered = text.lower()
    for c in choices:
        if c.title.lower() == lowered:
            return c.id

    # Свободный текст — сопоставляем моделью, а не набором слов.
    from src.utils.llm_gen import ainvoke_llm, get_llm

    options = "\n".join(f"- id={c.id}: {c.title}" for c in choices)
    try:
        async with get_llm(temperature=0.0, fast=True) as llm:
            structured = llm.with_structured_output(_Match)
            match: _Match = await ainvoke_llm(
                structured,
                [
                    SystemMessage(content=_MATCH_SYSTEM),
                    HumanMessage(
                        content=f"Варианты:\n{options}\n\nОтвет пользователя: {text}"
                    ),
                ],
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("Не удалось сопоставить ответ с вариантами: %s", exc)
        return None

    if match.choice_id is None:
        return None
    return by_id.get(str(match.choice_id))


def render_choices(choices: list[Choice], *, numbered: bool = True) -> str:
    """Рендерит варианты текстом — чтобы message оставался самодостаточным.

    Фронт с кнопками покажет кнопки; фронт с одним полем ввода покажет этот
    текст и поймёт, из чего выбирать.
    """
    if not choices:
        return ""
    if numbered:
        return "\n".join(f"{i}. {c.title}" for i, c in enumerate(choices, 1))
    return "\n".join(f"— {c.title}" for c in choices)