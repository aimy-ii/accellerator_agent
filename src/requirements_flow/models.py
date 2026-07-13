"""Модели этапа сбора требований."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class QuestionBatch(BaseModel):
    """Вопросы + оценка достаточности — за ОДИН вызов модели.

    Модель, формулируя вопросы, и так видит весь диалог и понимает, хватает ли
    данных. Отдельный «оценщик» был лишним вызовом (минус ~10 секунд с хода).
    """

    message: str = Field(
        default="",
        description=(
            "Готовая реплика: короткая вводная + вопросы списком. "
            "Это ЕДИНСТВЕННОЕ, что увидит заказчик. Пусто, если has_questions=false"
        ),
    )
    has_questions: bool = Field(
        description="Есть ли что ещё спросить по существу"
    )
    coverage: Literal["thin", "workable", "rich"] = Field(
        description=(
            "Сколько информации уже собрано: "
            "thin — только идея, ТЗ придётся во многом достраивать; "
            "workable — понятны цель, тип и основные сценарии; "
            "rich — заказчик рассказал подробно, придумывать почти нечего"
        )
    )
    can_generate: bool = Field(
        description=(
            "Хватает ли минимума (цель + тип продукта + основной функционал), "
            "чтобы уже писать ТЗ. Отсутствие технических деталей — НЕ повод для false"
        )
    )
    missing_topics: list[str] = Field(
        default_factory=list,
        description="Что осталось нераскрытым — только существенное",
    )


class InfoAssessment(BaseModel):
    """Оценка: достаточно ли собрано, чтобы писать ТЗ."""

    coverage: Literal["thin", "workable", "rich"] = Field(
        description=(
            "thin — данных мало, ИИ будет много достраивать; "
            "workable — можно писать ТЗ с умеренными допущениями; "
            "rich — заказчик рассказал много, придумывать почти не нужно"
        )
    )
    covered_topics: list[str] = Field(
        default_factory=list,
        description="Какие темы заказчик реально раскрыл",
    )
    missing_topics: list[str] = Field(
        default_factory=list,
        description="Что осталось нераскрытым (только существенное)",
    )
    can_generate: bool = Field(
        description="Хватает ли минимума (цель + тип продукта + основной функционал), чтобы писать ТЗ"
    )