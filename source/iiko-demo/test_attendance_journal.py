"""Журнал явок: свёртка XML-явок iiko в строки приход/уход по дню.
Задвоенные роли (пересекающиеся интервалы) не должны удваивать часы."""
import database; database.init_db()
import api


def test_single_attendance_becomes_one_row():
    att = [{"employeeId": "e1", "dateFrom": "2026-07-20T10:00:00",
            "dateTo": "2026-07-20T18:00:00", "departmentName": "Демо"}]
    rows = api._attendance_journal(att, {"e1": "Виктория Ким"}, "Демо")
    assert rows == [{"employee_id": "e1", "name": "Виктория Ким",
                     "date": "2026-07-20", "start": "10:00", "end": "18:00", "hours": 8.0}]


def test_doubled_roles_do_not_double_hours():
    att = [
        {"employeeId": "e1", "dateFrom": "2026-07-20T10:00:00",
         "dateTo": "2026-07-20T18:00:00", "departmentName": "Демо"},
        {"employeeId": "e1", "dateFrom": "2026-07-20T12:00:00",
         "dateTo": "2026-07-20T16:00:00", "departmentName": "Демо"},
    ]
    rows = api._attendance_journal(att, {"e1": "Ким"}, "Демо")
    assert len(rows) == 1
    assert rows[0]["hours"] == 8.0
    assert rows[0]["start"] == "10:00" and rows[0]["end"] == "18:00"


def test_filters_by_department():
    att = [
        {"employeeId": "e1", "dateFrom": "2026-07-20T10:00:00",
         "dateTo": "2026-07-20T18:00:00", "departmentName": "Демо"},
        {"employeeId": "e2", "dateFrom": "2026-07-20T09:00:00",
         "dateTo": "2026-07-20T17:00:00", "departmentName": "Другой филиал"},
    ]
    rows = api._attendance_journal(att, {"e1": "Ким", "e2": "Иванов"}, "Демо")
    assert [r["employee_id"] for r in rows] == ["e1"]


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"  [OK] {name}")
            except AssertionError as e:
                failed += 1; print(f"  [FAIL] {name}: {e}")
            except Exception as e:
                failed += 1; print(f"  [ERROR] {name}: {type(e).__name__}: {e}")
    print(f"\n{'ВСЕ ТЕСТЫ ОК' if not failed else f'ПРОВАЛЕНО: {failed}'}")
