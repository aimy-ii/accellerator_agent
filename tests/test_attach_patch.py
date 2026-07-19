"""Сборка PATCH проекта в attach_spec — покрываем ветки create/edit и срок."""
from __future__ import annotations

from src.graph.nodes import _build_project_patch


def test_create_empty_when_no_execution_days():
    # В create проект уже создан с title/description; без срока патчить нечего.
    patch = _build_project_patch(
        mode="create",
        spec_summary="что угодно",
        rename_confirmed=False,
        spec_title="Проект",
        execution_days=None,
    )
    assert patch == {}


def test_create_writes_only_execution_days():
    patch = _build_project_patch(
        mode="create",
        spec_summary="что угодно",
        rename_confirmed=False,
        spec_title="Проект",
        execution_days=45,
    )
    assert patch == {"execution_days": 45}


def test_edit_writes_description_even_without_rename():
    patch = _build_project_patch(
        mode="edit",
        spec_summary="новая суть",
        rename_confirmed=False,
        spec_title="Новое название",
        execution_days=None,
    )
    assert patch == {"description": "новая суть"}


def test_edit_writes_title_only_when_rename_confirmed():
    patch = _build_project_patch(
        mode="edit",
        spec_summary="суть",
        rename_confirmed=True,
        spec_title="Мобильное приложение",
        execution_days=None,
    )
    assert patch == {"description": "суть", "title": "Мобильное приложение"}


def test_edit_combines_all_fields():
    patch = _build_project_patch(
        mode="edit",
        spec_summary="суть",
        rename_confirmed=True,
        spec_title="Мобильное приложение",
        execution_days=60,
    )
    assert patch == {
        "description": "суть",
        "title": "Мобильное приложение",
        "execution_days": 60,
    }


def test_execution_days_zero_or_negative_dropped():
    # Бэкенд принимает ge=1 — 0 и отрицательные не отправляем.
    for bad in (0, -3):
        patch = _build_project_patch(
            mode="create",
            spec_summary="",
            rename_confirmed=False,
            spec_title="",
            execution_days=bad,
        )
        assert patch == {}


def test_execution_days_string_number_coerced():
    # Модель иногда возвращает строку вместо int — приводим и пишем.
    patch = _build_project_patch(
        mode="create",
        spec_summary="",
        rename_confirmed=False,
        spec_title="",
        execution_days="30",  # type: ignore[arg-type]
    )
    assert patch == {"execution_days": 30}


def test_execution_days_garbage_dropped():
    patch = _build_project_patch(
        mode="create",
        spec_summary="",
        rename_confirmed=False,
        spec_title="",
        execution_days="около месяца",  # type: ignore[arg-type]
    )
    assert patch == {}


def test_edit_rename_confirmed_but_empty_title_ignored():
    # Пустое или пробельное название не пишем даже при согласии на переименование.
    patch = _build_project_patch(
        mode="edit",
        spec_summary="суть",
        rename_confirmed=True,
        spec_title="   ",
        execution_days=None,
    )
    assert patch == {"description": "суть"}
