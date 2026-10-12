"""Расписание без iiko: наш факт смены, опоздания по нему и порядок имён.

База — временная: тесты не должны трогать рабочий iiko_boss.db.
Запуск:  python3 test_staff_attendance.py
"""
import asyncio
import os
import tempfile

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test_staff_att.db")

import database as D
import staff_late as SL

ORG = "org-astana"
OTHER = "org-almaty"
DAY = "2026-08-25"
D.init_db()


def test_mark_becomes_a_row_with_hours():
    D.set_staff_attendance(ORG, "e1", DAY, "09:07", "18:00", "Ким")
    rows = D.get_staff_attendance(ORG, DAY, DAY)
    assert len(rows) == 1, rows
    r = rows[0]
    assert (r["employee_id"], r["name"], r["start"], r["end"]) == ("e1", "Ким", "09:07", "18:00"), r
    assert r["hours"] == 8.88 and r["open"] is False, r
    print(f"  [OK] отметка 09:07–18:00 → {r['hours']} ч, смена закрыта")


def test_mark_without_end_means_still_on_shift():
    """Ушёл ещё не отметился — человек НА СМЕНЕ, а не отработал ноль часов."""
    D.set_staff_attendance(ORG, "e2", DAY, "16:00", "", "Волков")
    r = [x for x in D.get_staff_attendance(ORG, DAY, DAY) if x["employee_id"] == "e2"][0]
    assert r["open"] is True and r["hours"] == 0.0, r
    print("  [OK] отметка без ухода — open, часов пока нет")


def test_night_shift_hours_are_not_negative():
    D.set_staff_attendance(ORG, "e3", DAY, "22:00", "02:00", "Нуров")
    r = [x for x in D.get_staff_attendance(ORG, DAY, DAY) if x["employee_id"] == "e3"][0]
    assert r["hours"] == 4.0, r
    print("  [OK] 22:00–02:00 → 4 ч, а не минус двадцать")


def test_foreign_branch_marks_are_not_mixed_in():
    D.set_staff_attendance(OTHER, "z9", DAY, "10:00", "19:00", "Чужой")
    ids = {r["employee_id"] for r in D.get_staff_attendance(ORG, DAY, DAY)}
    assert "z9" not in ids, ids
    print("  [OK] отметки чужого филиала не попадают в наш")


def test_lateness_is_counted_from_our_mark():
    """Ради этого всё и затевалось: опоздание считается по нашей отметке."""
    rows = D.get_staff_attendance(ORG, DAY, DAY)
    calc = {r["employee_id"]: r for r in SL.late_rows(rows, branch_starts=["09:00", "16:00"],
                                                      grace_min=5)}
    assert calc["e1"]["status"] == "late" and calc["e1"]["late_min"] == 7, calc["e1"]
    assert calc["e2"]["status"] == "ontime", calc["e2"]
    assert calc["e2"]["open"] is True, calc["e2"]
    print("  [OK] Ким опоздал на 7 мин, Волков вовремя и ещё на смене")


def test_branch_without_marks_drops_out_instead_of_being_perfect():
    """Пустой филиал — это «данных нет», а не «все пришли вовремя»: иначе
    филиал, который ничего не отмечает, обгонял бы честных по оценке."""
    import api
    rows = asyncio.run(api._attendance_rows("empty-org", "empty-org", DAY, DAY,
                                            {"id": 0, "username": "t"}))
    assert rows is None, rows
    print("  [OK] филиал без отметок отдаёт None, критерий выпадает")


def test_attendance_rows_read_our_store_not_iiko():
    import api
    rows = asyncio.run(api._attendance_rows(ORG, ORG, DAY, DAY, {"id": 0, "username": "t"}))
    assert rows is not None and {r["employee_id"] for r in rows} == {"e1", "e2", "e3"}, rows
    print("  [OK] источник факта — наша таблица, в iiko запроса нет")


def test_frequent_faces_rank_first():
    """Имена приезжают на весь сервер: кого чаще ставят в график — тот выше."""
    for day in ("2026-08-24", "2026-08-25", "2026-08-26"):
        D.set_staff_shift(ORG, "e1", day, "09:00", "18:00")
    D.set_staff_shift(ORG, "e2", "2026-08-24", "16:00", "23:00")
    counts = D.staff_shift_counts(ORG)
    assert counts == {"e1": 3, "e2": 1}, counts
    names = {"e1": "Ким", "e2": "Волков", "e9": "Новенький"}
    out = [{"id": i, "name": names.get(i, i), "shifts": counts.get(i, 0)}
           for i in set(names) | set(counts)]
    out.sort(key=lambda e: (-e["shifts"], e["name"]))
    assert [e["id"] for e in out] == ["e1", "e2", "e9"], out
    print("  [OK] Ким (3 смены) выше Волкова (1) и незнакомого")


def test_shift_counts_do_not_leak_between_branches():
    D.set_staff_shift(OTHER, "e1", "2026-08-24", "09:00", "18:00")
    assert D.staff_shift_counts(ORG)["e1"] == 3, D.staff_shift_counts(ORG)
    print("  [OK] смены соседнего филиала не поднимают человека в нашем списке")


if __name__ == "__main__":
    failed = 0
    for t in (test_mark_becomes_a_row_with_hours, test_mark_without_end_means_still_on_shift,
              test_night_shift_hours_are_not_negative, test_foreign_branch_marks_are_not_mixed_in,
              test_lateness_is_counted_from_our_mark,
              test_branch_without_marks_drops_out_instead_of_being_perfect,
              test_attendance_rows_read_our_store_not_iiko,
              test_frequent_faces_rank_first, test_shift_counts_do_not_leak_between_branches):
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
