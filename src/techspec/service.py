"""Генерация и правка ТЗ."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from src.requirements_flow.prompts import format_dialog
from src.techspec.models import RefinedSpec, TechSpec
from src.techspec.prompts import (
    CREATIVE_SYSTEM,
    REFINE_SYSTEM,
    STRICT_SYSTEM,
    generate_user_message,
    refine_user_message,
)
from src.utils.llm_gen import ainvoke_llm, astream_structured, get_llm

log = logging.getLogger(__name__)


async def generate_spec(
    messages: list[dict],
    *,
    creative: bool,
    missing_topics: list[str] | None = None,
    on_delta=None,
) -> TechSpec:
    """Генерирует ТЗ с нуля.

    Args:
        creative: True — данных мало, ИИ достраивает продукт сам (и пишет
            всё придуманное в assumptions). False — данных много, ИИ почти
            ничего не придумывает и оформляет сказанное.
        missing_topics: нераскрытые темы — подсказка креативному режиму.
        on_delta: колбэк дельт поля tech_spec_text (потокенная печать);
            если задан — astream_structured, иначе ainvoke_llm.
    """
    system = CREATIVE_SYSTEM if creative else STRICT_SYSTEM
    hint = ", ".join(missing_topics or []) if creative else None

    log.info("Генерация ТЗ: режим=%s", "creative" if creative else "strict")

    async with get_llm(temperature=0.4 if creative else 0.1, max_tokens=16000) as llm:
        structured = llm.with_structured_output(TechSpec)
        result: TechSpec = await astream_structured(
            structured,
            [
                SystemMessage(content=system),
                HumanMessage(
                    content=generate_user_message(
                        format_dialog(messages),
                        assumptions_hint=hint,
                    )
                ),
            ],
            on_text_delta=on_delta,
            text_field="tech_spec_text",
        )
    log.info(
        "ТЗ готово: %s, допущений=%d, ролей=%d",
        result.title,
        len(result.assumptions),
        len(result.roles_needed),
    )
    return result


async def refine_spec(
    current_spec: str,
    messages: list[dict],
    *,
    current_title: str = "",
    on_delta=None,
) -> RefinedSpec:
    """Точечно правит существующее ТЗ по замечаниям из диалога.

    Args:
        current_title: текущее название проекта — модель решит, не устарело ли оно
            после правок (см. RefinedSpec.proposed_title).
        on_delta: колбэк дельт поля tech_spec_text (потокенная печать);
            если задан — astream_structured, иначе ainvoke_llm.
    """
    log.info("Правка ТЗ: длина исходного=%d символов", len(current_spec))

    async with get_llm(temperature=0.1, max_tokens=16000) as llm:
        structured = llm.with_structured_output(RefinedSpec)
        result: RefinedSpec = await astream_structured(
            structured,
            [
                SystemMessage(content=REFINE_SYSTEM),
                HumanMessage(
                    content=refine_user_message(
                        current_spec, format_dialog(messages), current_title
                    )
                ),
            ],
            on_text_delta=on_delta,
            text_field="tech_spec_text",
        )
    log.info(
        "ТЗ обновлено: изменений=%d, роли изменились=%s, новое название=%s",
        len(result.change_summary),
        result.roles_changed,
        result.proposed_title,
    )
    return result


_ROLES_SYSTEM = """Ты выделяешь состав команды из готового технического задания.

Верни список ролей простыми словами — кто нужен, чтобы сделать проект:
например 'Backend-разработчик', 'Frontend-разработчик', 'Дизайнер', 'QA-инженер'.

Правила:
1. Опирайся только на текст ТЗ. Не выдумывай ролей сверх нужного.
2. Каждая роль — отдельным элементом. Без количества, без пояснений.

Верни строго JSON по схеме. Ничего вне JSON."""


class _SpecRoles(BaseModel):
    """Роли, извлечённые из готового ТЗ."""

    roles_needed: list[str] = Field(
        default_factory=list,
        description="Список ролей команды простыми словами (кто нужен на проект)",
    )


async def extract_roles_from_spec(spec_text: str) -> list[str]:
    """Достаёт состав ролей из готового ТЗ.

    Нужно для маршрута «только подобрать команду» при доработке проекта: там ТЗ
    не правится, а roles_needed в стейте нет (проект пришёл из БД).
    """
    if not (spec_text or "").strip():
        return []
    async with get_llm(temperature=0.0, fast=True) as llm:
        structured = llm.with_structured_output(_SpecRoles)
        result: _SpecRoles = await ainvoke_llm(
            structured,
            [
                SystemMessage(content=_ROLES_SYSTEM),
                HumanMessage(content=f"Техническое задание:\n---\n{spec_text}\n---"),
            ],
        )
    log.info("Из ТЗ извлечено ролей: %d", len(result.roles_needed))
    return result.roles_needed


def slugify(title: str) -> str:
    """Транслитерирует название в имя файла."""
    table = str.maketrans(
        "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
        "abvgdeejziyklmnoprstufhc4wwyyyeua",
    )
    slug = title.lower().translate(table)
    slug = re.sub(r"[^a-z0-9]+", "_", slug).strip("_")
    return slug[:40] or "project"


def spec_file_name(title: str, *, version: int = 1) -> str:
    """Имя файла ТЗ: TZ_<slug>_v<N>_<дата>.docx (бэкенд не принимает .md)."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    suffix = f"_v{version}" if version > 1 else ""
    return f"TZ_{slugify(title)}{suffix}_{stamp}.docx"