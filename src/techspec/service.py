"""Генерация и правка ТЗ."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from langchain_core.messages import HumanMessage, SystemMessage

from src.requirements_flow.prompts import format_dialog
from src.techspec.models import RefinedSpec, TechSpec
from src.techspec.prompts import (
    CREATIVE_SYSTEM,
    REFINE_SYSTEM,
    STRICT_SYSTEM,
    generate_user_message,
    refine_user_message,
)
from src.utils.llm_gen import ainvoke_llm, get_llm

log = logging.getLogger(__name__)


async def generate_spec(
    messages: list[dict],
    *,
    creative: bool,
    missing_topics: list[str] | None = None,
) -> TechSpec:
    """Генерирует ТЗ с нуля.

    Args:
        creative: True — данных мало, ИИ достраивает продукт сам (и пишет
            всё придуманное в assumptions). False — данных много, ИИ почти
            ничего не придумывает и оформляет сказанное.
        missing_topics: нераскрытые темы — подсказка креативному режиму.
    """
    system = CREATIVE_SYSTEM if creative else STRICT_SYSTEM
    hint = ", ".join(missing_topics or []) if creative else None

    log.info("Генерация ТЗ: режим=%s", "creative" if creative else "strict")

    async with get_llm(temperature=0.4 if creative else 0.1, max_tokens=16000) as llm:
        structured = llm.with_structured_output(TechSpec)
        result: TechSpec = await ainvoke_llm(
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
        )
    log.info(
        "ТЗ готово: %s, допущений=%d, ролей=%d",
        result.title,
        len(result.assumptions),
        len(result.roles_needed),
    )
    return result


async def refine_spec(current_spec: str, messages: list[dict]) -> RefinedSpec:
    """Точечно правит существующее ТЗ по замечаниям из диалога."""
    log.info("Правка ТЗ: длина исходного=%d символов", len(current_spec))

    async with get_llm(temperature=0.1, max_tokens=16000) as llm:
        structured = llm.with_structured_output(RefinedSpec)
        result: RefinedSpec = await ainvoke_llm(
            structured,
            [
                SystemMessage(content=REFINE_SYSTEM),
                HumanMessage(
                    content=refine_user_message(current_spec, format_dialog(messages))
                ),
            ],
        )
    log.info("ТЗ обновлено: изменений=%d", len(result.change_summary))
    return result


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