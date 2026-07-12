"""Модели презентации проекта."""
from __future__ import annotations

from pydantic import BaseModel, Field


class Slide(BaseModel):
    """Один слайд презентации."""

    title: str = Field(description="Заголовок слайда")
    bullets: list[str] = Field(
        default_factory=list, description="Тезисы слайда, 3-5 штук"
    )
    notes: str | None = Field(default=None, description="Заметки докладчику")


class Deck(BaseModel):
    """Структура презентации проекта."""

    title: str = Field(description="Название презентации")
    subtitle: str | None = Field(default=None)
    slides: list[Slide] = Field(default_factory=list)


class PresentationResult(BaseModel):
    """Результат генерации презентации."""

    provider: str = Field(description="gamma | local | off")
    deck: Deck | None = Field(default=None, description="Структура слайдов")
    file_url: str | None = Field(
        default=None, description="Ссылка на наш файл .pptx (постоянная)"
    )
    preview_url: str | None = Field(
        default=None, description="Ссылка на просмотр в Gamma (если provider=gamma)"
    )
    error: str | None = Field(default=None)
