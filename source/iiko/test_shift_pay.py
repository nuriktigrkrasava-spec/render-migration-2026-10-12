"""Оплата за смену: пишется прямо на смене, ставок нет.

База — временная: тесты не должны трогать рабочий iiko_boss.db.
Запуск:  python3 test_shift_pay.py
"""
import os
import tempfile

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test_shift_pay.db")

import database as D

ORG = "org-astana"
DAY = "2026-09-07"
D.init_db()


def _shift(org=ORG, emp="e1", day=DAY):
    return next((r for r in D.get_staff_schedule(org, day, day)
                 if r["employee_id"] == emp), None)


def test_pay_survives_the_round_trip():
    D.set_staff_shift(ORG, "e1", DAY, "10:00", "18:00", 12000)
    r = _shift()
    assert r and r["pay"] == 12000, r
    print(f"  [OK] смена сохранилась с оплатой {r['pay']:.0f} ₸")


def test_missing_pay_is_none_not_zero():
    """Пустое поле значит «не проставили». Ноль значил бы «выход бесплатный»,
    и недельный фонд выглядел бы посчитанным, хотя суммы ещё не вписаны."""
    D.set_staff_shift(ORG, "e2", DAY, "12:00", "20:00")
    r = _shift(emp="e2")
    assert r and r["pay"] is None, r
    print("  [OK] смена без оплаты хранит NULL, а не 0")


def test_explicit_zero_is_kept_as_zero():
    D.set_staff_shift(ORG, "e3", DAY, "09:00", "17:00", 0)
    r = _shift(emp="e3")
    assert r and r["pay"] == 0 and r["pay"] is not None, r
    print("  [OK] осознанный ноль отличим от «не проставили»")


def test_clearing_pay_erases_the_old_sum():
    """Стёр сумму в редакторе — она должна исчезнуть, а не остаться прошлой."""
    D.set_staff_shift(ORG, "e1", DAY, "10:00", "18:00", None)
    r = _shift()
    assert r["pay"] is None, r
    print("  [OK] пустое поле стирает прежние 12 000, а не сохраняет их")


def test_pay_does_not_leak_between_branches():
    D.set_staff_shift("org-almaty", "e1", DAY, "10:00", "18:00", 99000)
    D.set_staff_shift(ORG, "e1", DAY, "10:00", "18:00", 15000)
    assert _shift()["pay"] == 15000, _shift()
    assert _shift(org="org-almaty")["pay"] == 99000, _shift(org="org-almaty")
    print("  [OK] у каждого филиала своя сумма на ту же смену")


def test_old_base_without_the_column_is_migrated():
    """Колонка pay добавлена позже: на рабочей базе таблица уже существует
    без неё, и ALTER должен пройти, не потеряв смены."""
    conn = D.get_db()
    conn.executescript("""
        DROP TABLE IF EXISTS staff_schedule;
        CREATE TABLE staff_schedule (
            organization_id TEXT NOT NULL, employee_id TEXT NOT NULL, day TEXT NOT NULL,
            start_time TEXT NOT NULL, end_time TEXT, updated_at TEXT,
            PRIMARY KEY (organization_id, employee_id, day));
        INSERT INTO staff_schedule VALUES ('org-old', 'e7', '2026-09-07', '11:00', '19:00', '');
    """)
    conn.commit()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(staff_schedule)").fetchall()}
    assert "pay" not in cols, cols
    D._ensure_staff_shifts(conn)
    conn.commit()
    conn.close()

    rows = D.get_staff_schedule("org-old", "2026-09-07", "2026-09-07")
    assert len(rows) == 1 and rows[0]["start_time"] == "11:00", rows
    assert rows[0]["pay"] is None, rows
    D.set_staff_shift("org-old", "e7", "2026-09-07", "11:00", "19:00", 8000)
    assert D.get_staff_schedule("org-old", "2026-09-07", "2026-09-07")[0]["pay"] == 8000
    print("  [OK] старая база получила колонку, смена цела, оплата пишется")


if __name__ == "__main__":
    failed = 0
    for t in (test_pay_survives_the_round_trip, test_missing_pay_is_none_not_zero,
              test_explicit_zero_is_kept_as_zero, test_clearing_pay_erases_the_old_sum,
              test_pay_does_not_leak_between_branches,
              test_old_base_without_the_column_is_migrated):
        print(f"\n>>> {t.__name__}")
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"  [FAIL] {e}")
        except Exception as e:
            failed += 1
            print(f"  [ERROR] {type(e).__name__}: {e}")
    print(f"\n{'='*50}\n{'ВСЕ ТЕСТЫ ОК' if not failed else f'ПРОВАЛЕНО: {failed}'}")
    raise SystemExit(1 if failed else 0)
