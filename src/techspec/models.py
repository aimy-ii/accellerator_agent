"""Модели генерации и правки ТЗ."""
from __future__ import annotations

from pydantic import BaseModel, Field


class TechSpec(BaseModel):
    """Сгенерированное техническое задание."""

    title: str = Field(description="Короткое название проекта (для карточки проекта)")
    summary: str = Field(
        description="Суть проекта в 2-3 предложениях, человеческим языком"
    )
    tech_spec_text: str = Field(
        description=(
            "Полный текст ТЗ в markdown. Начинается строго с '# Техническое задание', "
            "следующая строка — '## <Название проекта>'"
        )
    )
    assumptions: list[str] = Field(
        default_factory=list,
        description="Допущения, которые ИИ сделал сам (заказчик этого не говорил)",
    )
    roles_needed: list[str] = Field(
        default_factory=list,
        description=(
            "Кто нужен в команду под этот проект, простыми словами: "
            "например 'Backend-разработчик', 'Frontend-разработчик', 'Дизайнер'"
        ),
    )
    execution_days: int | None = Field(
        default=None,
        description=(
            "Ориентировочный срок реализации проекта в КАЛЕНДАРНЫХ днях "
            "(целое число). Модель оценивает по объёму функционала и составу "
            "ролей; если оценить нельзя — верни null."
        ),
    )


class RefinedSpec(BaseModel):
    """Обновлённое ТЗ после правок заказчика."""

    tech_spec_text: str = Field(
        description="ПОЛНЫЙ обновлённый текст ТЗ в markdown (не фрагмент)"
    )
    change_summary: list[str] = Field(
        default_factory=list,
        description="Что именно изменилось — по пунктам, для показа заказчику",
    )
    roles_needed: list[str] = Field(
        default_factory=list,
        description="Актуальный состав ролей после правок",
    )
    proposed_title: str | None = Field(
        default=None,
        description=(
            "Новое короткое название проекта, ЕСЛИ из-за правок суть изменилась "
            "настолько, что прежнее название больше не подходит (например, был "
            "'бот', стало 'мобильное приложение'). Если название по-прежнему "
            "подходит — верни null. Без кавычек."
        ),
    )
    roles_changed: bool = Field(
        default=False,
        description=(
            "true, ЕСЛИ правки изменили состав команды — роли добавились, "
            "убрались или поменялись. false, если состав ролей остался прежним."
        ),
    )
    execution_days: int | None = Field(
        default=None,
        description=(
            "Ориентировочный срок реализации проекта в КАЛЕНДАРНЫХ днях "
            "(целое число). Модель оценивает по объёму функционала и составу "
            "ролей; если оценить нельзя — верни null."
        ),
    )