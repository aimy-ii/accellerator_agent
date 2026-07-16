"""Тесты маршрутизации графа — чистые функции роутеров на словарях состояния.

Проверяем ЗНАЧЕНИЯ переходов, а не только их наличие: развилку доработки
(spec / team / both), условие (пере)подбора команды, fan-out после
переименования и выход attach в финал, когда команду не трогаем.
"""
from __future__ import annotations

from src.graph.graph import (
    _after_attach,
    _after_confirm,
    _after_edit_menu,
    _after_load_spec,
    _after_rename,
    _should_match_team,
)


# ─── _after_load_spec ───────────────────────────────────────────────────────

def test_load_spec_ok_goes_to_edit_menu():
    assert _after_load_spec({}) == "edit_menu"


def test_load_spec_error_goes_to_finalize():
    assert _after_load_spec({"error": "boom"}) == "finalize"


# ─── _after_edit_menu ───────────────────────────────────────────────────────

def test_edit_menu_team_goes_to_match():
    assert _after_edit_menu({"edit_intent": "team"}) == "match_team"


def test_edit_menu_spec_goes_to_questions():
    assert _after_edit_menu({"edit_intent": "spec"}) == "ask_questions"


def test_edit_menu_both_goes_to_questions():
    assert _after_edit_menu({"edit_intent": "both"}) == "ask_questions"


def test_edit_menu_unresolved_reasks():
    assert _after_edit_menu({}) == "edit_menu"


def test_edit_menu_error_goes_to_finalize():
    assert _after_edit_menu({"error": "boom", "edit_intent": "team"}) == "finalize"


# ─── _should_match_team ─────────────────────────────────────────────────────

def test_match_needed_on_create():
    assert _should_match_team({"mode": "create"}) is True


def test_match_needed_on_edit_both():
    assert _should_match_team({"mode": "edit", "edit_intent": "both"}) is True


def test_match_needed_on_edit_spec_only_if_roles_changed():
    changed = {"mode": "edit", "edit_intent": "spec", "roles_changed": True}
    same = {"mode": "edit", "edit_intent": "spec", "roles_changed": False}
    assert _should_match_team(changed) is True
    assert _should_match_team(same) is False


def test_match_needed_on_edit_team():
    assert _should_match_team({"mode": "edit", "edit_intent": "team"}) is True


# ─── _after_confirm ─────────────────────────────────────────────────────────

def test_confirm_ok_goes_to_rename():
    assert _after_confirm({"spec_confirmed": True}) == "confirm_rename"


def test_confirm_not_ok_goes_to_refine():
    assert _after_confirm({"spec_confirmed": False}) == "refine_spec"


def test_confirm_error_goes_to_finalize():
    assert _after_confirm({"error": "boom", "spec_confirmed": True}) == "finalize"


# ─── _after_rename (fan-out) ────────────────────────────────────────────────

def test_rename_fanout_create_matches_team():
    assert _after_rename({"mode": "create"}) == ["attach_spec", "match_team"]


def test_rename_fanout_edit_spec_no_role_change_attach_only():
    state = {"mode": "edit", "edit_intent": "spec", "roles_changed": False}
    assert _after_rename(state) == ["attach_spec"]


def test_rename_fanout_edit_spec_role_change_matches_team():
    state = {"mode": "edit", "edit_intent": "spec", "roles_changed": True}
    assert _after_rename(state) == ["attach_spec", "match_team"]


def test_rename_fanout_edit_both_matches_team():
    state = {"mode": "edit", "edit_intent": "both"}
    assert _after_rename(state) == ["attach_spec", "match_team"]


def test_rename_error_goes_to_finalize():
    assert _after_rename({"error": "boom"}) == ["finalize"]


# ─── _after_attach ──────────────────────────────────────────────────────────

def test_attach_presents_team_when_match_ran():
    state = {"mode": "edit", "edit_intent": "both"}
    assert _after_attach(state) == "present_team"


def test_attach_finalizes_when_no_team():
    state = {"mode": "edit", "edit_intent": "spec", "roles_changed": False}
    assert _after_attach(state) == "finalize"


def test_attach_error_goes_to_finalize():
    assert _after_attach({"error": "boom"}) == "finalize"
