"""Уведомления управляющим: утренний дайджест и триггеры.

До этой правки рассылка группировалась по restaurants.telegram_id, то есть
уходила только владельцу. Проверяем, что управляющий получает свои филиалы,
владелец — по-прежнему все свои, и что филиал считается один раз на всех.
"""
import asyncio
import os
import sys

import pytest

os.environ.setdefault("ENCRYPTION_KEY", "test-key-for-notifications")

OWNER = 1
ALBINA = 777          # управляющая двух филиалов
KANAT = 888           # управляющий одного

ORG_A = "org-buhar"
ORG_B = "org-bokey"
ORG_C = "org-expo"

RESTS = [
    {"organization_id": ORG_A, "organization_name": "Бухар Жырау 36", "telegram_id": OWNER},
    {"organization_id": ORG_B, "organization_name": "Бокейхана 25а", "telegram_id": OWNER},
    {"organization_id": ORG_C, "organization_name": "EXPO", "telegram_id": OWNER},
]


@pytest.fixture()
def api(monkeypatch):
    sys.path.insert(0, os.path.dirname(__file__))
    import api as api_module
    import database
    monkeypatch.setattr(api_module, "get_all_restaurants", lambda: RESTS)
    monkeypatch.setattr(database, "get_all_restaurants", lambda: RESTS, raising=False)
    return api_module


def _managers(monkeypatch, rows):
    import database
    monkeypatch.setattr(database, "get_branch_managers", lambda org_id=None: rows)


def orgs_for(recipients, tg_id):
    return {r["organization_id"] for r in recipients.get(tg_id, [])}


def test_manager_of_two_branches_gets_both(api, monkeypatch):
    _managers(monkeypatch, [
        {"organization_id": ORG_A, "telegram_id": ALBINA, "name": "albina"},
        {"organization_id": ORG_B, "telegram_id": ALBINA, "name": "albina"},
    ])
    rec = api._report_recipients(RESTS)
    assert orgs_for(rec, ALBINA) == {ORG_A, ORG_B}


def test_manager_does_not_get_other_branches(api, monkeypatch):
    _managers(monkeypatch, [
        {"organization_id": ORG_A, "telegram_id": ALBINA, "name": "albina"},
        {"organization_id": ORG_C, "telegram_id": KANAT, "name": "kanat"},
    ])
    rec = api._report_recipients(RESTS)
    assert orgs_for(rec, ALBINA) == {ORG_A}
    assert orgs_for(rec, KANAT) == {ORG_C}


def test_owner_still_gets_everything(api, monkeypatch):
    _managers(monkeypatch, [
        {"organization_id": ORG_A, "telegram_id": ALBINA, "name": "albina"},
    ])
    rec = api._report_recipients(RESTS)
    assert orgs_for(rec, OWNER) == {ORG_A, ORG_B, ORG_C}


def test_no_managers_changes_nothing(api, monkeypatch):
    _managers(monkeypatch, [])
    rec = api._report_recipients(RESTS)
    assert list(rec) == [OWNER]
    assert orgs_for(rec, OWNER) == {ORG_A, ORG_B, ORG_C}


def test_owner_as_own_manager_gets_no_duplicate(api, monkeypatch):
    """Владелец, назначенный управляющим сам себе, не должен получить филиал дважды."""
    _managers(monkeypatch, [
        {"organization_id": ORG_A, "telegram_id": OWNER, "name": "owner"},
    ])
    rec = api._report_recipients(RESTS)
    assert len(rec[OWNER]) == 3


def test_manager_of_unknown_branch_ignored(api, monkeypatch):
    """Отвязанный филиал в branch_managers не должен ломать рассылку."""
    _managers(monkeypatch, [
        {"organization_id": "org-удалён", "telegram_id": ALBINA, "name": "albina"},
    ])
    rec = api._report_recipients(RESTS)
    assert ALBINA not in rec


# ── филиал считается один раз на всех получателей ───────────────────────────

def _run_morning(api, monkeypatch, calls=None):
    """Гоняет утреннюю рассылку на фейковых цифрах. Возвращает [(chat_id, text)]."""
    async def fake_branch(r, yd):
        if calls is not None:
            calls.append(r["organization_id"])
        return {"name": r["organization_name"], "org": r["organization_id"],
                "rev": 100.0, "prev_day": 90.0, "prev_week": 80.0,
                "mtd": 1000.0, "forecast": 2000.0, "typical": 95.0,
                "stops": [], "lows": []}

    sends = []

    class FakeResp:
        def json(self):
            return {"ok": True}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, **kw):
            sends.append((json["chat_id"], json["text"]))
            return FakeResp()

    monkeypatch.setattr(api, "_branch_morning", fake_branch)
    monkeypatch.setattr(api, "BOT_TOKEN", "test-token")
    monkeypatch.setattr(api.httpx, "AsyncClient", lambda *a, **kw: FakeClient())

    assert asyncio.run(api._send_morning_report()) is True
    return sends


def test_branch_computed_once_for_owner_and_manager(api, monkeypatch):
    """Точка с владельцем и управляющим не должна дёргать OLAP дважды."""
    _managers(monkeypatch, [
        {"organization_id": ORG_A, "telegram_id": ALBINA, "name": "albina"},
        {"organization_id": ORG_B, "telegram_id": ALBINA, "name": "albina"},
    ])
    calls = []
    sends = _run_morning(api, monkeypatch, calls)
    assert sorted(calls) == sorted([ORG_A, ORG_B, ORG_C])   # ровно по одному разу
    assert sorted(c for c, _ in sends) == sorted([OWNER, ALBINA])  # оба получили письмо


def test_invoice_hint_only_for_managers(api, monkeypatch):
    """Про накладные напоминаем управляющему; владельцу эта строка не нужна."""
    _managers(monkeypatch, [
        {"organization_id": ORG_A, "telegram_id": ALBINA, "name": "albina"},
    ])
    sends = _run_morning(api, monkeypatch)
    by_id = dict(sends)
    assert "Накладные можно пробивать прямо здесь" in by_id[ALBINA]
    assert "Накладные можно пробивать прямо здесь" not in by_id[OWNER]


def test_morning_hour_is_11_local(api):
    """Владелец просил 11:00 по Казахстану; local_now — UTC+5."""
    assert api.MORNING_HOUR_LOCAL == 11
    assert api.local_now().utcoffset().total_seconds() == 5 * 3600


# ── Вечерняя выручка, сообщения и новые задачи ──────────────────────────────
# Три канала, по которым управляющий узнаёт о своём филиале, не открывая сайт.


class _FakeHttp:
    """Ловит исходящие sendMessage вместо телеграма."""
    sent: list = []

    def __init__(self, *a, **kw): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False

    async def post(self, url, json=None, **kw):
        _FakeHttp.sent.append((json["chat_id"], json["text"]))
        class R:
            @staticmethod
            def json(): return {"ok": True}
        return R()


def test_nightly_revenue_reaches_the_manager(api, monkeypatch):
    """Выручка в конце дня шла только владельцу: сводка группировалась по
    restaurants.telegram_id. Управляющий на неё влияет, а видел её только он.

    Гоняем НАСТОЯЩУЮ рассылку и смотрим, кому что ушло.
    """
    _managers(monkeypatch, [
        {"organization_id": ORG_A, "telegram_id": ALBINA, "name": "albina"},
    ])

    async def fake_night(r, yesterday, week_ago):
        return {"org": r["organization_id"], "name": r["organization_name"],
                "sessions": [{"sessionNumber": 7, "payOrders": 500000, "cashDiff": 0}],
                "prev": 400000.0, "got_prev": True, "rev": 500000.0}

    monkeypatch.setattr(api, "_branch_night", fake_night)
    monkeypatch.setattr(api, "get_all_restaurants", lambda: RESTS)
    monkeypatch.setattr(api, "BOT_TOKEN", "x")
    _FakeHttp.sent = []
    monkeypatch.setattr(api.httpx, "AsyncClient", _FakeHttp)
    asyncio.run(api._send_daily_shifts_report())

    by_tg = {tg: text for tg, text in _FakeHttp.sent}
    assert ALBINA in by_tg, "управляющему выручка не ушла"
    assert OWNER in by_tg, "владелец её получать не перестал"
    # управляющему — только его филиал, владельцу — все три
    assert by_tg[ALBINA].count("№7") == 1, by_tg[ALBINA]
    assert by_tg[OWNER].count("№7") == 3, by_tg[OWNER]
    assert "Выручка вчера" in by_tg[ALBINA]


def test_branch_night_computed_once_for_both(api, monkeypatch):
    """Филиал с владельцем И управляющим считается ОДИН раз: иначе каждая такая
    точка дёргала бы кассовые смены дважды за ночь."""
    _managers(monkeypatch, [
        {"organization_id": ORG_A, "telegram_id": ALBINA, "name": "albina"},
    ])
    calls = []

    async def fake_night(r, yesterday, week_ago):
        calls.append(r["organization_id"])
        return {"org": r["organization_id"], "name": r["organization_name"],
                "sessions": [], "prev": 0.0, "got_prev": False, "rev": 0.0}

    monkeypatch.setattr(api, "_branch_night", fake_night)
    monkeypatch.setattr(api, "get_all_restaurants", lambda: RESTS)
    monkeypatch.setattr(api, "BOT_TOKEN", "x")
    asyncio.run(api._send_daily_shifts_report())
    assert sorted(calls) == sorted([ORG_A, ORG_B, ORG_C]), calls
    assert len(calls) == 3, "ORG_A не должен считаться дважды"


def test_notice_goes_to_managers_once_each(api, monkeypatch):
    """Сообщение из карточки догоняет управляющих в телеграме.

    Человек на двух филиалах получает ОДНО сообщение, а не две копии, и автору
    его же текст не возвращается.
    """
    import mountain_routes as mr
    _managers(monkeypatch, [
        {"organization_id": ORG_A, "telegram_id": ALBINA, "name": "albina"},
        {"organization_id": ORG_B, "telegram_id": ALBINA, "name": "albina"},
        {"organization_id": ORG_C, "telegram_id": OWNER, "name": "сам себе"},
    ])
    got = []

    async def fake_send(tg, text, kb=None):
        got.append((tg, text))
    monkeypatch.setattr(api, "_tg_send", fake_send)

    sc = {"branches": [{"organization_id": o} for o in (ORG_A, ORG_B, ORG_C)]}
    sent = asyncio.run(mr._notice_to_telegram(sc, "", "Инвентаризация в пятницу", author=OWNER))
    assert sent == 1, got                      # Альбина один раз, владелец — ноль
    assert got[0][0] == ALBINA
    assert "Инвентаризация в пятницу" in got[0][1]


def test_notice_survives_a_dead_telegram(api, monkeypatch):
    """Телеграм здесь дубль: уведомление уже записано, и его падение не должно
    выглядеть как «сообщение не отправлено»."""
    import mountain_routes as mr
    _managers(monkeypatch, [{"organization_id": ORG_A, "telegram_id": ALBINA, "name": "a"}])

    async def boom(tg, text, kb=None):
        raise RuntimeError("телеграм лёг")
    monkeypatch.setattr(api, "_tg_send", boom)

    sc = {"branches": [{"organization_id": ORG_A}]}
    assert asyncio.run(mr._notice_to_telegram(sc, ORG_A, "текст", author=OWNER)) == 0


def test_new_task_notifies_only_once(api, monkeypatch, tmp_path):
    """Задачи пересчитываются при каждом открытии экрана. Уведомление уходит
    только в тот заход, когда задача появилась, иначе это десять писем в день.

    Проверяем НАСТОЯЩИЙ upsert на временной базе: подделка здесь проверяла бы
    саму себя.
    """
    import database
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "t.db"))

    first = database.upsert_director_task(ORG_A, "fc", "Фудкост", 40.0, 32.0, 100)
    again = database.upsert_director_task(ORG_A, "fc", "Фудкост", 41.0, 32.0, 100)
    assert first["is_new"] is True, first
    assert again["is_new"] is False, "второй пересчёт не должен слать уведомление"
    # точка отсчёта не поехала вместе с показателем
    assert float(again["baseline"]) == 40.0, again
    # другая задача того же филиала — снова новая
    other = database.upsert_director_task(ORG_A, "wo", "Списания", 9.0, 5.0, 50)
    assert other["is_new"] is True, other


def test_new_task_goes_to_manager_not_owner(api, monkeypatch):
    """Владелец задачи видит на том же экране, с которого они посчитались —
    дубль в чат ему не нужен. Управляющему нужен."""
    import database
    _managers(monkeypatch, [{"organization_id": ORG_A, "telegram_id": ALBINA, "name": "a"}])
    monkeypatch.setattr(database, "branch_name", lambda org: "Бухар Жырау 36", raising=False)
    monkeypatch.setattr(api, "BOT_TOKEN", "x")
    got = []

    async def fake_task(tg_id, org_id, branch, title, detail="", money=0):
        got.append((tg_id, title))
        return 1
    monkeypatch.setattr(api, "_send_task", fake_task)

    n = asyncio.run(api._notify_new_tasks(ORG_A, [("Фудкост", 40.0, 32.0, 100)]))
    assert n == 1 and got[0][0] == ALBINA, got
    assert "Фудкост" in got[0][1]


def test_assigned_task_reaches_the_branch_manager(api, monkeypatch):
    """Задача от владельца лежала на «Горе» и ждала, пока управляющий зайдёт.
    Теперь догоняет в телеграме — но только управляющего своего филиала."""
    import mountain_routes as mr
    _managers(monkeypatch, [
        {"organization_id": ORG_A, "telegram_id": ALBINA, "name": "albina"},
        {"organization_id": ORG_C, "telegram_id": KANAT, "name": "kanat"},
    ])
    got = []

    async def fake_send(tg, text, kb=None):
        got.append((tg, text))
    monkeypatch.setattr(api, "_tg_send", fake_send)

    n = asyncio.run(mr._tasks_to_telegram(
        [(ORG_A, "Бухар Жырау 36", "Свести инвентаризацию", "до пятницы")], author=OWNER))
    assert n == 1 and got[0][0] == ALBINA, got
    assert "Свести инвентаризацию" in got[0][1]
    assert "до пятницы" in got[0][1]
    assert KANAT not in [t for t, _ in got], "чужому филиалу уходить не должно"


def test_assigned_task_has_no_buttons(api, monkeypatch):
    """Кнопка «Сделал» завела бы ВТОРУЮ задачу в другой таблице: закрыл в чате —
    на доске висит. Поэтому уведомление без кнопок, закрывают на «Горе»."""
    import mountain_routes as mr
    _managers(monkeypatch, [{"organization_id": ORG_A, "telegram_id": ALBINA, "name": "a"}])
    got = []

    async def fake_send(tg, text, kb=None):
        got.append(kb)
    monkeypatch.setattr(api, "_tg_send", fake_send)

    asyncio.run(mr._tasks_to_telegram([(ORG_A, "Филиал", "Задача", "")], author=OWNER))
    assert got == [None], got
