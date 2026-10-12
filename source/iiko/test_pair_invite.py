"""Ссылка-приглашение на несколько филиалов.

Проверяем ровно то, что можно сломать: старые одиночные ссылки должны
продолжать работать (их уже раздали управляющим), парная — открывать оба
филиала, а подделка и склейка чужих подписей — не открывать ничего.
"""
import os
import sys

import pytest

os.environ.setdefault("ENCRYPTION_KEY", "test-key-for-invites")

ORG_A = "045cc6b2-4c2f-4a11-9c11-000000000001"   # Бухар Жырау
ORG_B = "9e83a8af-02d8-4a22-9c22-000000000002"   # Бокейхана
ORG_C = "e7e40dc8-8885-4a33-9c33-000000000003"   # Актау — посторонний

FAKE_RESTAURANTS = [
    {"organization_id": ORG_A, "organization_name": "Бухар Жырау 36", "telegram_id": 1},
    {"organization_id": ORG_B, "organization_name": "Бокейхана 25а", "telegram_id": 1},
    {"organization_id": ORG_C, "organization_name": "Актау", "telegram_id": 1},
]


@pytest.fixture()
def api(monkeypatch):
    sys.path.insert(0, os.path.dirname(__file__))
    import api as api_module
    monkeypatch.setattr(api_module, "get_all_restaurants", lambda: FAKE_RESTAURANTS)
    return api_module


def payload(link: str) -> str:
    return link.split("start=", 1)[1]


def test_single_link_unchanged(api):
    """Формат одиночной ссылки не изменился — раздача не протухла."""
    link = api.invite_link_for(ORG_A)
    assert payload(link) == f"inv_045cc6b24c2f_{api._invite_sign(ORG_A)}"
    assert api._orgs_from_invite(payload(link)) == [ORG_A]


def test_pair_link_opens_both(api):
    link = api.invite_link_for_many([ORG_A, ORG_B])
    assert api._orgs_from_invite(payload(link)) == [ORG_A, ORG_B]


def test_pair_link_fits_deep_link_limit(api):
    """Telegram обрезает start-параметр на 64 символах."""
    assert len(payload(api.invite_link_for_many([ORG_A, ORG_B]))) <= 64
    assert len(payload(api.invite_link_for_many([ORG_A, ORG_B, ORG_C]))) <= 64


def test_one_org_falls_back_to_single_format(api):
    assert api.invite_link_for_many([ORG_A]) == api.invite_link_for(ORG_A)


def test_tampered_signature_rejected(api):
    p = payload(api.invite_link_for_many([ORG_A, ORG_B]))
    assert api._orgs_from_invite(p[:-1] + ("x" if p[-1] != "x" else "y")) == []


def test_swapped_branch_rejected(api):
    """Подпись на набор: подменить один филиал в паре нельзя."""
    good = payload(api.invite_link_for_many([ORG_A, ORG_B]))
    forged = good.replace(ORG_B.replace("-", "")[:12], ORG_C.replace("-", "")[:12])
    assert api._orgs_from_invite(forged) == []


def test_single_signatures_cannot_be_glued_into_pair(api):
    """Из двух честных одиночных ссылок не собрать пару."""
    a = ORG_A.replace("-", "")[:12]
    b = ORG_B.replace("-", "")[:12]
    assert api._orgs_from_invite(f"inv2_{a}_{b}_{api._invite_sign(ORG_A)}") == []
    assert api._orgs_from_invite(f"inv2_{a}_{b}_{api._invite_sign(ORG_B)}") == []


def test_unknown_branch_rejected(api):
    sig = api._invite_sign_many([ORG_A, ORG_B])
    assert api._orgs_from_invite(f"inv2_045cc6b24c2f_ffffffffffff_{sig}") == []


def test_wrong_count_rejected(api):
    """Число в префиксе должно совпадать с тем, что в теле."""
    sig = api._invite_sign_many([ORG_A, ORG_B])
    assert api._orgs_from_invite(f"inv3_045cc6b24c2f_9e83a8af02d8_{sig}") == []


def test_garbage_rejected(api):
    for p in ("inv_", "inv", "instagram", "inv_045cc6b24c2f", ""):
        assert api._orgs_from_invite(p) == []


# ── привязка управляющего к нескольким филиалам ──────────────────────────────

@pytest.fixture()
def db(tmp_path, monkeypatch):
    import database
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "t.db"))
    return database


def test_manager_bound_to_two_branches(db):
    db.add_branch_manager([ORG_A, ORG_B], 777, "albina")
    orgs = {m["organization_id"] for m in db.get_branch_managers()
            if m["telegram_id"] == 777}
    assert orgs == {ORG_A, ORG_B}


def test_link_defines_the_whole_set(db):
    """Набор задаёт ссылка: по одиночной ссылке пара схлопывается до филиала."""
    db.add_branch_manager([ORG_A, ORG_B], 778, "albina")
    db.add_branch_manager(ORG_C, 778, "albina")
    orgs = {m["organization_id"] for m in db.get_branch_managers()
            if m["telegram_id"] == 778}
    assert orgs == {ORG_C}


def test_string_argument_still_works(db):
    db.add_branch_manager(ORG_A, 779, "kanat")
    orgs = {m["organization_id"] for m in db.get_branch_managers()
            if m["telegram_id"] == 779}
    assert orgs == {ORG_A}
