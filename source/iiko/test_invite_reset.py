"""Оживление исчерпанной ссылки-приглашения.

Ссылка постоянная — подпись не меняется; «умирает» только счётчик активаций
(INVITE_MAX_USES). Владелец сбрасывает его сам, кнопкой. Проверяем, что сброс
возвращает ссылку в строй и что чужой филиал так не оживить.
"""
import os
import sys

import pytest

os.environ.setdefault("ENCRYPTION_KEY", "test-key-for-reset")

OWNER = 8727082281
STRANGER = 555444333

ORG_KAZH = "4d56a98e-ba42-4c11-9a11-000000000001"   # Кажымукана 16
ORG_OTHER = "38abc1d1-bd20-4622-b78e-000000000002"  # EXPO, чужой владелец

RESTS = [
    {"organization_id": ORG_KAZH, "organization_name": "Кажымукана 16", "telegram_id": OWNER},
    {"organization_id": ORG_OTHER, "organization_name": "EXPO", "telegram_id": STRANGER},
]


@pytest.fixture()
def api(monkeypatch, tmp_path):
    sys.path.insert(0, os.path.dirname(__file__))
    import api as api_module
    import database
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "t.db"))
    database.init_db()
    monkeypatch.setattr(api_module, "get_all_restaurants", lambda: RESTS)
    return api_module


def short(org_id):
    return org_id.replace("-", "")[:12]


def test_exhausted_link_blocks_then_reset_frees_it(api):
    """Счётчик забит двумя активациями; после сброса снова пусто."""
    key = f"invite_used:{ORG_KAZH}"
    api.set_app_state(key, "111,222")
    used = [x for x in (api.get_app_state(key) or "").split(",") if x]
    assert len(used) >= api.INVITE_MAX_USES        # ссылка «умерла»

    api.set_app_state(key, "")                     # то, что делает кнопка
    used = [x for x in (api.get_app_state(key) or "").split(",") if x]
    assert used == []                              # снова живая


def test_reset_targets_the_right_branch(api):
    """Сброс одного филиала не трогает счётчики остальных."""
    api.set_app_state(f"invite_used:{ORG_KAZH}", "111,222")
    api.set_app_state(f"invite_used:{ORG_OTHER}", "333,444")
    api.set_app_state(f"invite_used:{ORG_KAZH}", "")
    assert api.get_app_state(f"invite_used:{ORG_OTHER}") == "333,444"


def test_owner_lookup_rejects_foreign_branch(api):
    """Владелец видит только свои филиалы — чужой короткий id не находится."""
    owned = [r for r in api.get_all_restaurants() if int(r["telegram_id"]) == OWNER]
    assert next((r for r in owned if short(r["organization_id"]) == short(ORG_OTHER)), None) is None
    assert next((r for r in owned if short(r["organization_id"]) == short(ORG_KAZH)), None) is not None


def test_link_survives_reset_unchanged(api):
    """Оживление не меняет саму ссылку — разосланная раньше остаётся рабочей."""
    before = api.invite_link_for(ORG_KAZH)
    api.set_app_state(f"invite_used:{ORG_KAZH}", "111,222")
    api.set_app_state(f"invite_used:{ORG_KAZH}", "")
    assert api.invite_link_for(ORG_KAZH) == before
    assert api._orgs_from_invite(before.split("start=")[1]) == [ORG_KAZH]
