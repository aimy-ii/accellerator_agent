"""Модели этапа сбора требований."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class QuestionBatch(BaseModel):
    """Вопросы на один ход диалога — одной готовой репликой."""

    message: str = Field(
        default="",
        description=(
            "ГОТОВАЯ реплика заказчику: короткая вводная + сами вопросы списком. "
            "Это единственное, что он увидит. Пример:\n"
            "'Расскажите о вашей идее:\n"
            "• Что за продукт и какую задачу решает?\n"
            "• Кто им будет пользоваться?\n"
            "• В каком виде — сайт, приложение?'"
        )
    )
    has_questions: bool = Field(
        description="False — спрашивать больше нечего, можно писать ТЗ"
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
