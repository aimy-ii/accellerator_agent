"""Сбор требований: вопросы заказчику и оценка достаточности информации."""
from __future__ import annotations

import logging

from langchain_core.messages import HumanMessage, SystemMessage

from src.requirements_flow.models import InfoAssessment, QuestionBatch
from src.requirements_flow.prompts import (
    ASSESS_SYSTEM,
    QUESTIONS_SYSTEM,
    assess_user_message,
    questions_user_message,
)
from src.utils.llm_gen import ainvoke_llm, get_llm

log = logging.getLogger(__name__)


async def next_questions(
    messages: list[dict],
    *,
    spec_context: str | None = None,
) -> QuestionBatch:
    """Возвращает готовую реплику с вопросами (или has_questions=False)."""
    async with get_llm(temperature=0.3, fast=True) as llm:
        structured = llm.with_structured_output(QuestionBatch)
        result: QuestionBatch = await ainvoke_llm(
            structured,
            [
                SystemMessage(content=QUESTIONS_SYSTEM),
                HumanMessage(
                    content=questions_user_message(messages, spec_context=spec_context)
                ),
            ],
        )
    log.info(
        "Вопросы: has_questions=%s coverage=%s can_generate=%s",
        result.has_questions,
        result.coverage,
        result.can_generate,
    )
    return result


async def assess_info(
    messages: list[dict],
    *,
    spec_context: str | None = None,
) -> InfoAssessment:
    """Оценивает, достаточно ли данных для ТЗ и насколько плотно они покрыты."""
    async with get_llm(temperature=0.0, fast=True) as llm:
        structured = llm.with_structured_output(InfoAssessment)
        result: InfoAssessment = await ainvoke_llm(
            structured,
            [
                SystemMessage(content=ASSESS_SYSTEM),
                HumanMessage(
                    content=assess_user_message(messages, spec_context=spec_context)
                ),
            ],
        )
    log.info(
        "Оценка информации: coverage=%s can_generate=%s missing=%d",
        result.coverage,
        result.can_generate,
        len(result.missing_topics),
    )
    return result