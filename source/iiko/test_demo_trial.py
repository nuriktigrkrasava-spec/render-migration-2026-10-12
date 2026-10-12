"""Тесты триала демо-бота (длительность DEMO_TRIAL_HOURS, сейчас 7 дней).
Запуск: python3 test_demo_trial.py
"""
import os, tempfile, asyncio, time
os.environ.setdefault("DB_PATH", tempfile.mktemp(suffix="_trial_test.db"))
os.environ["DEMO_ONLY"] = "1"  # триал живёт только на демо-боте

import database; database.init_db()
import api
from fastapi import HTTPException


def test_trial_insert_and_get():
    database.start_demo_trial(111, "org-A", 1_000_000)
    row = database.get_demo_trial(111)
    assert row and row["org_id"] == "org-A" and row["started_ts"] == 1_000_000, row
    print("  [OK] start_demo_trial + get_demo_trial")


def test_trial_not_reset_on_second_connect():
    database.start_demo_trial(222, "org-X", 5_000)
    database.start_demo_trial(222, "org-Y", 9_999)  # повтор не должен перезаписать
    row = database.get_demo_trial(222)
    assert row["org_id"] == "org-X" and row["started_ts"] == 5_000, row
    print("  [OK] повторное подключение не перезапускает триал")


def test_get_trial_none():
    assert database.get_demo_trial(999999) is None
    print("  [OK] нет триала → None")


def test_use_demo_data_active_trial():
    tid = 333
    database.start_demo_trial(tid, "org-real", int(time.time()))  # только что
    user = {"id": tid}
    # активный триал + реальный org → НЕ демо
    assert api._use_demo_data(user, "org-real") is False
    # но demo-org-1 всегда демо
    assert api._use_demo_data(user, "demo-org-1") is True
    print("  [OK] активный триал → реальные данные, demo-org-1 всё равно демо")


def test_use_demo_data_ended_trial():
    tid = 444
    database.start_demo_trial(tid, "org-real",
                              int(time.time()) - (api.DEMO_TRIAL_HOURS + 2) * 3600)  # триал истёк
    assert api._use_demo_data({"id": tid}, "org-real") is True  # ended → демо
    st = api._demo_trial_state(tid)
    assert st["state"] == "ended", st
    print("  [OK] истёкший триал → демо-сэмпл, state=ended")


def test_use_demo_data_no_trial():
    assert api._use_demo_data({"id": 555}, "whatever") is True  # демо-юзер без триала
    assert api._demo_trial_state(555)["state"] == "none"
    print("  [OK] нет триала → демо")


def test_get_org_id_trial_returns_real():
    tid = 666
    database.start_demo_trial(tid, "org-real-666", int(time.time()))
    # во время триала _get_org_id НЕ должен схлопывать реальный org в "demo".
    # (без записи в restaurants реальный путь кинет 404 — это и проверяем: НЕ "demo")
    try:
        res = api._get_org_id("org-real-666", {"id": tid})
        assert res != "demo", f"ожидали не demo, got {res}"
        print("  [OK] _get_org_id в триале не схлопывает в demo")
    except HTTPException as e:
        assert e.status_code == 404
        print("  [OK] _get_org_id в триале идёт реальным путём (404 без записи в БД — ожидаемо)")


def test_get_org_id_no_trial_is_demo():
    res = api._get_org_id("anything", {"id": 777})  # демо-юзер без триала
    assert res == "demo", res
    print("  [OK] _get_org_id без триала → demo")


def test_demo_trial_endpoint_active():
    tid = 888
    database.start_demo_trial(tid, "org-888", int(time.time()))
    out = asyncio.run(api.demo_trial(user={"id": tid}))
    assert out["state"] == "active" and out["org_id"] == "org-888", out
    print("  [OK] /api/demo/trial → active с org_id")


def test_block_recognition_after_trial_ended():
    """Подключённый юзер с истёкшим триалом НЕ распознаёт бесплатно (гейт),
    с активным — распознаёт свободно. БОТ_TOKEN в тестах пуст — _tg_send no-op."""
    tid_ended, tid_active = 2001, 2002
    for tid in (tid_ended, tid_active):
        database.add_restaurant(tid, "real_login", f"org-{tid}", "Ресторан", "https://x.iiko.it")
    database.start_demo_trial(tid_ended, f"org-{tid_ended}",
                              int(time.time()) - (api.DEMO_TRIAL_HOURS + 2) * 3600)
    database.start_demo_trial(tid_active, f"org-{tid_active}", int(time.time()))
    assert asyncio.run(api._demo_block_if_used(tid_ended, tid_ended)) is True
    assert asyncio.run(api._demo_block_if_used(tid_active, tid_active)) is False
    print("  [OK] истёкший триал блокирует распознавание, активный — нет")


def test_wipe_keeps_other_users_expenses():
    """/resetuser не должен стирать ручные расходы другого юзера на той же орге."""
    org = "org-shared"
    database.add_restaurant(3001, "real_login", org, "Ресторан", "https://x.iiko.it")
    database.add_restaurant(3002, "real_login", org, "Ресторан", "https://x.iiko.it")
    database.add_manual_expense(org, "Аренда", "прочее", 100000, "касса", "2026-07-01")
    database.wipe_demo_user(3001)
    left = database.get_manual_expenses(org, "2026-01-01", "2026-12-31")
    assert len(left) == 1, left  # орга ещё у 3002 — расходы целы
    database.wipe_demo_user(3002)
    left2 = database.get_manual_expenses(org, "2026-01-01", "2026-12-31")
    assert left2 == [], left2  # последний владелец орги — теперь чистим
    print("  [OK] wipe щадит чужие расходы, чистит осиротевшие")


def test_trials_due_for_sales():
    database.start_demo_trial(1001, "o", 1000)            # давно — должен попасть
    database.start_demo_trial(1002, "o", int(time.time()))  # только что — не должен
    due = database.get_trials_due_for_sales(int(time.time()), 24 * 3600)
    assert 1001 in due and 1002 not in due, due
    database.mark_trial_sales_sent(1001)
    due2 = database.get_trials_due_for_sales(int(time.time()), 24 * 3600)
    assert 1001 not in due2, "после mark_trial_sales_sent не должен повторяться"
    print("  [OK] get_trials_due_for_sales + mark_trial_sales_sent")


if __name__ == "__main__":
    tests = [test_trial_insert_and_get, test_trial_not_reset_on_second_connect, test_get_trial_none,
             test_use_demo_data_active_trial, test_use_demo_data_ended_trial, test_use_demo_data_no_trial,
             test_get_org_id_trial_returns_real, test_get_org_id_no_trial_is_demo,
             test_demo_trial_endpoint_active, test_block_recognition_after_trial_ended,
             test_wipe_keeps_other_users_expenses, test_trials_due_for_sales]
    failed = 0
    for t in tests:
        print(f"\n>>> {t.__name__}")
        try:
            t()
        except AssertionError as e:
            failed += 1; print(f"  [FAIL] {e}")
        except Exception as e:
            failed += 1; print(f"  [ERROR] {type(e).__name__}: {e}")
    try:
        os.remove(os.environ["DB_PATH"])
    except OSError:
        pass
    print(f"\n{'='*50}\n{'ВСЕ ТЕСТЫ ОК' if not failed else f'ПРОВАЛЕНО: {failed}'}")
