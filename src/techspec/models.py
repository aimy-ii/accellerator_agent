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
