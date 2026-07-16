"""Тесты детерминированной сводки проекта (build_edit_summary) и ролей из БД."""
from __future__ import annotations

from src.graph.nodes import (
    _required_specialists_from_team,
    _roles_from_project,
    build_edit_summary,
)


def test_summary_with_spec_file(project_with_spec, invitations_sample):
    text = build_edit_summary(project_with_spec, invitations_sample)
    assert "Бот для записи к врачу" in text
    assert "файл прикреплён" in text
    assert "требуется по проекту: 3" in text
    assert "откликов от специалистов: 4" in text
    # два potential, один accepted
    assert "претендентов (наброски подборки): 2" in text
    assert "в команде (приняли приглашение): 1" in text


def test_summary_without_spec_file(project_no_spec):
    text = build_edit_summary(project_no_spec, [])
    assert "файла нет" in text
    # состав специалистов не задан (0) — строку про «требуется» не показываем
    assert "требуется по проекту" not in text
    # приглашений нет → счётчики нулевые, но строки присутствуют
    assert "претендентов (наброски подборки): 0" in text
    assert "в команде (приняли приглашение): 0" in text


def test_summary_invitations_unknown_omits_counts(project_with_spec):
    text = build_edit_summary(project_with_spec, None)
    assert "претендентов" not in text
    assert "в команде" not in text
    # отклики берутся из самого проекта — они остаются
    assert "откликов от специалистов: 4" in text


def test_summary_truncates_long_description(project_with_spec):
    project_with_spec["description"] = "оч" * 400  # 800 символов
    text = build_edit_summary(project_with_spec, [], char_limit=100)
    assert "…" in text
    # усечённый кусок описания не длиннее лимита + многоточие
    desc_line = next(ln for ln in text.splitlines() if ln.startswith("— кратко:"))
    assert len(desc_line) <= len("— кратко: ") + 100 + 1


def test_summary_title_fallback_when_missing():
    project = {"id": 99, "files": [], "responses_count": 0, "specialists_count": 0}
    text = build_edit_summary(project, None)
    assert "Проект #99" in text


def test_roles_from_project_reads_profession_names(project_with_spec):
    roles = _roles_from_project(project_with_spec)
    assert roles == ["Backend-разработчик", "Frontend-разработчик"]


def test_roles_from_project_empty_when_no_specialists(project_no_spec):
    assert _roles_from_project(project_no_spec) == []


# ─── _required_specialists_from_team (роли → состав проекта) ─────────────────

def test_required_specialists_maps_role_to_first_profession():
    team = [
        {"role": "Backend", "profession_ids": [10], "count": 2, "candidates": []},
        {"role": "Frontend", "profession_ids": [11], "count": 1, "candidates": []},
    ]
    assert _required_specialists_from_team(team) == [
        {"profession_id": 10, "count": 2},
        {"profession_id": 11, "count": 1},
    ]


def test_required_specialists_sums_same_profession():
    team = [
        {"role": "Backend", "profession_ids": [10], "count": 2, "candidates": []},
        {"role": "API", "profession_ids": [10, 99], "count": 1, "candidates": []},
    ]
    # одна профессия из разных ролей — количества складываются, берём первый id
    assert _required_specialists_from_team(team) == [{"profession_id": 10, "count": 3}]


def test_required_specialists_skips_roles_without_profession():
    team = [
        {"role": "Backend", "profession_ids": [10], "count": 1, "candidates": []},
        {"role": "Загадка", "profession_ids": [], "count": 4, "candidates": []},
    ]
    assert _required_specialists_from_team(team) == [{"profession_id": 10, "count": 1}]


def test_required_specialists_empty_team():
    assert _required_specialists_from_team([]) == []
