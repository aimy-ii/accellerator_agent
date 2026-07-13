"""Карточка проекта из диалога.

Нужна, чтобы создать проект в БД СРАЗУ после сбора требований — до того,
как ИИ напишет полное ТЗ (оно долгое). Заказчик видит проект в списке
уже во время генерации ТЗ.
"""
from __future__ import annotations

import logging

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from src.requirements_flow.prompts import format_dialog
from src.utils.llm_gen import ainvoke_llm, get_llm

log = logging.getLogger(__name__)


class ProjectCard(BaseModel):
    """Минимум, чтобы завести проект в базе."""

    title: str = Field(
        max_length=255,
        description="Короткое название проекта — как его назвал бы заказчик. Без кавычек",
    )
    description: str = Field(
        description=(
            "Суть проекта в 2-3 предложениях простым языком. "
            "Это увидят в карточке проекта, пока ТЗ ещё пишется"
        )
    )


CARD_SYSTEM = """Ты формируешь карточку проекта по диалогу с заказчиком.

Задача: короткое название и суть в 2-3 предложениях. Ничего больше.

Правила:
1. Название — как назвал бы сам заказчик. Не выдумывай пафосных брендов.
2. Описание — простым языком, без жаргона. Что за продукт, для кого, зачем.
3. Опирайся только на диалог. Не досочиняй функционал.

Верни строго JSON по схеме ProjectCard. Ничего вне JSON."""


async def make_card(messages: list[dict]) -> ProjectCard:
    """Собирает название и описание проекта из диалога."""
    async with get_llm(temperature=0.2, fast=True) as llm:
        structured = llm.with_structured_output(ProjectCard)
        card: ProjectCard = await ainvoke_llm(
            structured,
            [
                SystemMessage(content=CARD_SYSTEM),
                HumanMessage(
                    content=f"Диалог с заказчиком:\n---\n{format_dialog(messages)}\n---"
                ),
            ],
        )
    log.info("Карточка проекта: %s", card.title)
    return card