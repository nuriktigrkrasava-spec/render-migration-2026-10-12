"""Журналы: разбор показаний, нормы камер, сборка панели, штрафы.

Проверяем то, что ломается тихо: минус набранный типографским тире, замер
вне нормы, «сдано N из M» до наступления слота и филиал не из Астаны,
который в журналы попадать не должен.
"""
import json
import os
import sys
from datetime import datetime

import pytest

os.environ.setdefault("ENCRYPTION_KEY", "test-key-journals")
sys.path.insert(0, os.path.dirname(__file__))

import journals as J  # noqa: E402

OWNER = 8727082281
MGR = 777

ORG_A = "aaaaaaaa-0000-0000-0000-000000000001"
ORG_B = "bbbbbbbb-0000-0000-0000-000000000002"
ORG_REG = "cccccccc-0000-0000-0000-000000000003"

RESTS = [
    {"organization_id": ORG_A, "organization_name": "Астана, ул. Бухар Жырау 36",
     "telegram_id": OWNER},
    {"organization_id": ORG_B, "organization_name": "Астана, ул. Кажымукана 16",
     "telegram_id": OWNER},
    {"organization_id": ORG_REG, "organization_name": "Актобе, ул. Локомотивная 7",
     "telegram_id": OWNER},
]

AFTERNOON = datetime(2026, 8, 14, 15, 40)


# ── разбор показаний ────────────────────────────────────────────────────────

def test_parses_nine_numbers():
    vals, err = J.parse_temps("-20 4 -19 3 5 4 3 6 -21")
    assert err is None
    assert vals["fish_fr"] == -20 and vals["snack_fr"] == -21


def test_typographic_minus_is_accepted():
    """Телефон подставляет «−» вместо дефиса — это не должно ломать сдачу."""
    vals, err = J.parse_temps("−20 4 −19 3 5 4 3 6 −21")
    assert err is None and vals["fish_fr"] == -20


def test_newlines_and_degree_signs_ok():
    vals, err = J.parse_temps("-20°C\n4\n-19\n3\n5\n4\n3\n6\n-21")
    assert err is None and len(vals) == 9


def test_wrong_count_rejected():
    _, err = J.parse_temps("-20 4 -19")
    assert err and "9" in err


def test_garbage_rejected():
    _, err = J.parse_temps("холодно тепло -19 3 5 4 3 6 -21")
    assert err and "не число" in err


def test_absurd_value_rejected():
    _, err = J.parse_temps("-20 4 -19 3 5 4 3 6 -900")
    assert err and "опечатк" in err


# ── нормы ───────────────────────────────────────────────────────────────────

def test_freezer_norm():
    assert J.temp_status("fish_fr", -20) == "ok"
    assert J.temp_status("fish_fr", -9) == "bad"      # разморозился


def test_fridge_norm():
    assert J.temp_status("veg", 4) == "ok"
    assert J.temp_status("veg", 12) == "bad"
    assert J.temp_status("veg", -5) == "bad"          # подморозил овощи


def test_alarms_list_only_violations():
    vals = {c["key"]: (-20 if c["kind"] == "fr" else 4) for c in J.CAMERAS}
    assert J.temp_alarms(vals) == []
    vals["snack_fr"] = -9
    al = J.temp_alarms(vals)
    assert len(al) == 1 and al[0]["key"] == "snack_fr"


# ── что положено сдать ──────────────────────────────────────────────────────

def test_due_grows_through_the_day():
    morning = J.due_items(datetime(2026, 8, 14, 9, 0))
    noon = J.due_items(AFTERNOON)
    night = J.due_items(datetime(2026, 8, 14, 23, 59))
    assert len(morning) == 0                      # до 10:45 ещё ничего не горит
    assert 0 < len(noon) < len(night)


def test_due_counts_zones_separately():
    """Зал, кухня и санузел — три отдельные сдачи, а не одна «чистота»."""
    at1100 = J.due_items(datetime(2026, 8, 14, 11, 0))
    clean = [d for d in at1100 if d[0] == "clean"]
    assert len(clean) == len(J.ZONE_ORDER)


def test_task_text_carries_criteria():
    t = J.clean_task_text("hall", "10:45")
    assert "коврик" in t.lower() and "разводов" in t


def test_temp_task_lists_all_cameras():
    t = J.temp_task_text("11:00")
    for c in J.CAMERAS:
        assert c["title"] in t


# ── сборка панели ───────────────────────────────────────────────────────────

@pytest.fixture()
def board(monkeypatch, tmp_path):
    import database
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "j.db"))
    database.init_db()
    import api
    import journal_routes
    monkeypatch.setattr(api, "get_restaurants", lambda uid: RESTS)
    monkeypatch.setattr(api, "get_all_restaurants", lambda: RESTS)
    monkeypatch.setattr(api, "local_now", lambda: AFTERNOON)
    monkeypatch.setattr(database, "get_branch_managers",
                        lambda org_id=None: [{"organization_id": ORG_A,
                                              "telegram_id": MGR, "name": "albina"}])
    monkeypatch.setattr(journal_routes, "get_branch_managers",
                        lambda org_id=None: [{"organization_id": ORG_A,
                                              "telegram_id": MGR, "name": "albina"}])
    return journal_routes, database


def test_regions_are_excluded(board):
    jr, _ = board
    b = jr.build_board(OWNER, AFTERNOON)
    names = {x["full"] for x in b["branches"]}
    assert "Актобе, ул. Локомотивная 7" not in names
    assert len(b["branches"]) == 2


def test_branch_without_manager_is_off(board):
    jr, _ = board
    b = jr.build_board(OWNER, AFTERNOON)
    kazh = next(x for x in b["branches"] if "Кажымукана" in x["full"])
    assert kazh["st"] == "off"
    assert any(g["st"] == "off" for g in b["gaps"])


def test_submitted_zone_counts_as_done(board):
    jr, db = board
    day = AFTERNOON.date().isoformat()
    db.save_journal_entry(ORG_A, "clean", "hall", "10:45", day, MGR,
                          json.dumps({"photos": [1, 2, 3]}))
    b = jr.build_board(OWNER, AFTERNOON)
    buh = next(x for x in b["branches"] if "Бухар" in x["full"])
    assert buh["done"] == 1
    assert not any("Зал" in g["what"] and "10:45" in g["what"] for g in b["gaps"])


def test_temperature_out_of_norm_turns_branch_red(board):
    jr, db = board
    day = AFTERNOON.date().isoformat()
    vals = {c["key"]: (-20 if c["kind"] == "fr" else 4) for c in J.CAMERAS}
    vals["snack_fr"] = -9
    db.save_journal_entry(ORG_A, "temp", "", "11:00", day, MGR, json.dumps(vals))
    b = jr.build_board(OWNER, AFTERNOON)
    buh = next(x for x in b["branches"] if "Бухар" in x["full"])
    assert buh["st"] == "bad"
    assert any("−9" in g["what"] or "-9" in g["what"] for g in b["gaps"])


def test_resubmission_replaces_not_duplicates(board):
    jr, db = board
    day = AFTERNOON.date().isoformat()
    db.save_journal_entry(ORG_A, "clean", "hall", "10:45", day, MGR, json.dumps({"photos": [1]}))
    db.save_journal_entry(ORG_A, "clean", "hall", "10:45", day, MGR, json.dumps({"photos": [1, 2]}))
    rows = [e for e in db.get_journal_entries(day)
            if e["organization_id"] == ORG_A and e["kind"] == "clean"]
    assert len(rows) == 1
    assert len(json.loads(rows[0]["payload"])["photos"]) == 2


def test_photo_hash_is_remembered(board):
    _, db = board
    db.remember_photo("abc123", ORG_A, "hall", "2026-08-13", "file1")
    assert db.seen_photo("abc123", ORG_A)["day"] == "2026-08-13"
    assert db.seen_photo("abc123", ORG_B) is None      # хэши не текут между филиалами


def test_short_name_shortens_astana_addresses(board):
    jr, _ = board
    assert jr.short_name("Астана, ул. Бухар Жырау 36") == "Бухар Жырау"
    assert jr.short_name("Астана, ул. Кажымукана 16") == "Кажымукана"


def test_workshop_and_hub_excluded(board):
    """Цех и распредцентр на Алпамыс 61 — не гостевые точки, журналы не ведут."""
    jr, _ = board
    assert not jr.in_journals("Астана, ул. Алпамыс 61 (КЦ)")
    assert not jr.in_journals("Астана, ул. Алпамыс 61 (ЦЕХ)")
    assert jr.in_journals("Астана, ул. Бухар Жырау 36")
    assert not jr.in_journals("Актобе, ул. Локомотивная 7")


def test_bold_markup_survives_escaping(board):
    """**жирный** доезжает тегом, а угловые скобки — экранированными."""
    jr, _ = board
    out = jr._esc("Норма **≤ −18 °C** для <морозильника>")
    assert "<b>≤ −18 °C</b>" in out
    assert "&lt;морозильника&gt;" in out


def test_escape_blocks_tag_injection(board):
    """Через «**» нельзя протащить произвольный тег."""
    jr, _ = board
    out = jr._esc("**<script>alert(1)</script>**")
    assert "<script>" not in out and "&lt;script&gt;" in out


def test_console_scope_survives_duplicate_registrations(board, monkeypatch):
    """На проде одна точка заведена под несколькими аккаунтами: get_all_restaurants
    схлопывает дубли и может оставить чужую строку. Консоль должна брать филиалы
    оттуда же, откуда панель, иначе владелец теряет доступ к своим командам."""
    jr, _ = board
    import api
    # дубль: та же организация, но первой идёт строка другого аккаунта
    monkeypatch.setattr(api, "get_all_restaurants", lambda: [
        {"organization_id": ORG_A, "organization_name": "Астана, ул. Бухар Жырау 36",
         "telegram_id": 111},
    ])
    assert len(jr._owner_orgs(OWNER)) == 2      # видит свои через get_restaurants


# ── кнопки задания ──────────────────────────────────────────────────────────

def test_slot_survives_callback_roundtrip(board):
    """Слот «10:45» содержит двоеточие — тот же символ, что разделяет поля в
    callback_data. Без кодирования слот превращался в «45», и сдача не сходилась
    ни с одним заданием: фото уходило в никуда."""
    import api
    for slot in set(J.CLEAN_SLOTS + J.TEMP_SLOTS):
        assert api._slot_from_code(api._slot_code(slot)) == slot


def test_callback_carries_branch_and_slot(board):
    """Филиал зашит в кнопку — управляющий двух адресов ничего не выбирает."""
    import api
    kb = api._journal_kb("10:45", [("clean", "hall", "10:45")], ORG_A)
    data = kb["inline_keyboard"][0][0]["callback_data"]
    bits = data.split(":")
    assert bits[0] == "jr" and bits[1] == "c" and bits[2] == "hall"
    assert api._slot_from_code(bits[3]) == "10:45"
    assert bits[4] == api._jshort(ORG_A)
    assert len(bits) == 5                    # разбор однозначен


def test_temp_button_shape(board):
    import api
    kb = api._journal_kb("11:00", [("temp", "", "11:00")], ORG_B)
    bits = kb["inline_keyboard"][0][0]["callback_data"].split(":")
    assert bits[1] == "t" and api._slot_from_code(bits[2]) == "11:00"
    assert bits[3] == api._jshort(ORG_B) and len(bits) == 4


def test_callback_data_fits_telegram_limit(board):
    """Telegram режет callback_data на 64 байтах."""
    import api
    kb = api._journal_kb("10:45", [("clean", "kitchen", "10:45")], ORG_A)
    assert len(kb["inline_keyboard"][0][0]["callback_data"].encode()) <= 64


def test_stock_saved_counts_are_returned(board):
    """Форма подставляет уже введённое — иначе набивать 150 позиций заново."""
    jr, db = board
    day = AFTERNOON.date().isoformat()
    db.save_journal_entry(ORG_A, "stock", "", J.STOCK_SLOT, day, MGR,
                          json.dumps({"veg": {"Авокадо": 12, "Лимон": 3}}))
    b = jr.build_board(OWNER, AFTERNOON)
    buh = next(x for x in b["branches"] if "Бухар" in x["full"])
    assert buh["stock"] is True
    assert buh["stock_counts"]["veg"]["Авокадо"] == 12


def test_stock_places_carry_full_nomenclature(board):
    jr, _ = board
    b = jr.build_board(OWNER, AFTERNOON)
    keys = {p["key"] for p in b["stock_places"]}
    assert len(b["stock_places"]) == 9
    assert "veg" in keys
    veg = next(p for p in b["stock_places"] if p["key"] == "veg")
    assert "Авокадо" in veg["items"]


def test_overdue_slot_is_skipped_not_blasted(board):
    """После дневного перезапуска утренние слоты не должны прилетать пачкой."""
    import api
    assert api.JOURNAL_LATE_MIN >= 30
    late = 15 * 60 + 40 - J._hm("10:45")     # 10:45 против 15:40
    assert late > api.JOURNAL_LATE_MIN


# ── поиск филиала по названию ───────────────────────────────────────────────

KZ = [{"organization_id": "x1", "organization_name": "Астана, ул. Бейбітшілік 71"},
      {"organization_id": "x2", "organization_name": "Астана, ул. Кажымукана 16"},
      {"organization_id": "x3", "organization_name": "Астана, ул. Бухар Жырау 36"}]


def test_russian_spelling_finds_kazakh_name(board):
    """В iiko «Бейбітшілік» через казахскую «і», а ищут русской раскладкой."""
    jr, _ = board
    assert len(jr.find_branch(KZ, "бейбитшилик")) == 1
    assert len(jr.find_branch(KZ, "Бейбітшілік")) == 1
    assert len(jr.find_branch(KZ, "БЕЙБ")) == 1


def test_search_is_case_and_fragment_tolerant(board):
    jr, _ = board
    assert len(jr.find_branch(KZ, "кажымукана")) == 1
    assert len(jr.find_branch(KZ, "жырау")) == 1


def test_ambiguous_and_empty_queries(board):
    jr, _ = board
    assert len(jr.find_branch(KZ, "астана")) == 3      # общее слово — неоднозначно
    assert jr.find_branch(KZ, "") == []
    assert jr.find_branch(KZ, "сыганак") == []


# ── выручка филиала: защита от чужой смены ──────────────────────────────────

def test_shift_sanity_ratio_is_tight():
    """Оплаченное не может заметно превышать проданное — запас только на
    округления и правки при приёмке смены."""
    import api
    assert 1.0 <= api.SHIFT_SANITY_RATIO <= 1.10


def test_contaminated_selection_is_rejected():
    """Калдаякова: 588 821 своих + 458 470 чужих = 1 047 291 при продажах
    845 480. Оплатить больше, чем продано, нельзя — выборку надо отбросить."""
    import api
    picked, gross = 1_047_291.0, 845_480.0
    assert picked > gross * api.SHIFT_SANITY_RATIO


def test_clean_selection_passes():
    """Нормальный филиал: оплачено меньше проданного из-за скидок — берём смены."""
    import api
    picked, gross = 588_821.0, 845_480.0
    assert picked <= gross * api.SHIFT_SANITY_RATIO
