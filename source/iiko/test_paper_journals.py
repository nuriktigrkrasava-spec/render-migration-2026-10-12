"""Бумажные журналы: галочка, неделя истории и напоминание по недостающим.

База — временная: тесты не должны трогать рабочий iiko_boss.db.
Запуск:  python3 test_paper_journals.py
"""
import os
import tempfile
from datetime import date, timedelta

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test_paper.db")

import database as D
import journals as J

A, B = "org-astana", "org-almaty"
TODAY = date.today().isoformat()
YESTERDAY = (date.today() - timedelta(days=1)).isoformat()
D.init_db()


def test_three_journals_from_the_owner_list():
    assert J.PAPER_KEYS == ["temp", "brak", "clean"], J.PAPER_KEYS
    assert J.PAPER_SLOT == "11:00", J.PAPER_SLOT
    print("  [OK] температурный, бракеражный, уборки; напоминание в 11:00")


def test_mark_is_saved_with_who_and_when():
    D.mark_paper_journal(A, TODAY, "temp", 777)
    rows = D.get_paper_journals([A], TODAY, TODAY)
    assert len(rows) == 1 and rows[0]["journal"] == "temp", rows
    assert rows[0]["telegram_id"] == 777 and rows[0]["created_at"], rows
    print(f"  [OK] отметка записана: кто {rows[0]['telegram_id']}, когда {rows[0]['created_at'][11:16]}")


def test_second_mark_does_not_double_the_row():
    D.mark_paper_journal(A, TODAY, "temp", 888)
    rows = D.get_paper_journals([A], TODAY, TODAY)
    assert len(rows) == 1 and rows[0]["telegram_id"] == 888, rows
    print("  [OK] повторная отметка перезаписывает, а не плодит строки")


def test_unmark_removes_the_mark():
    D.mark_paper_journal(A, TODAY, "temp", 888, on=False)
    assert D.get_paper_journals([A], TODAY, TODAY) == []
    print("  [OK] снятая галочка = журнал не отмечен")


def test_week_history_keeps_days_apart():
    D.mark_paper_journal(A, YESTERDAY, "clean", 1)
    D.mark_paper_journal(A, TODAY, "brak", 1)
    week = D.get_paper_journals([A], YESTERDAY, TODAY)
    got = {(r["day"], r["journal"]) for r in week}
    assert got == {(YESTERDAY, "clean"), (TODAY, "brak")}, got
    assert D.get_paper_journals([A], TODAY, TODAY)[0]["journal"] == "brak"
    print("  [OK] вчерашняя уборка не засчитывается за сегодня")


def test_foreign_branch_marks_are_not_mixed_in():
    D.mark_paper_journal(B, TODAY, "temp", 2)
    mine = D.get_paper_journals([A], TODAY, TODAY)
    assert {r["organization_id"] for r in mine} == {A}, mine
    both = D.get_paper_journals([A, B], TODAY, TODAY)
    assert {r["organization_id"] for r in both} == {A, B}, both
    print("  [OK] филиалы не перемешиваются")


def test_reminder_lists_only_what_is_missing():
    """Филиалу, который всё заполнил, писать нечего."""
    marks = {(r["organization_id"], r["journal"])
             for r in D.get_paper_journals([A, B], TODAY, TODAY)}
    missing_a = [k for k in J.PAPER_KEYS if (A, k) not in marks]
    assert missing_a == ["temp", "clean"], missing_a          # brak отмечен выше
    text = J.paper_task_text(missing_a)
    assert "Температурный" in text and "Уборки" in text and "Бракеражный" not in text, text
    for k in J.PAPER_KEYS:
        D.mark_paper_journal(A, TODAY, k, 1)
    marks = {(r["organization_id"], r["journal"])
             for r in D.get_paper_journals([A], TODAY, TODAY)}
    assert [k for k in J.PAPER_KEYS if (A, k) not in marks] == []
    print("  [OK] напоминание только по незаполненным; всё отмечено — молчим")


def test_paper_marks_do_not_touch_photo_journals():
    """Отметки живут в journal_entries рядом с фото и замерами и не мешают им."""
    D.save_journal_entry(A, "clean", "hall", "10:45", TODAY, 5, "{}")
    entries = D.get_journal_entries(TODAY, A)
    kinds = {e["kind"] for e in entries}
    assert kinds == {"paper", "clean"}, kinds
    paper = [e for e in entries if e["kind"] == "paper"]
    assert len(paper) == 3 and {e["slot"] for e in paper} == {"11:00"}, paper
    print("  [OK] бумага и фотосдача лежат в одной таблице, не наступая друг на друга")


if __name__ == "__main__":
    failed = 0
    for t in (test_three_journals_from_the_owner_list, test_mark_is_saved_with_who_and_when,
              test_second_mark_does_not_double_the_row, test_unmark_removes_the_mark,
              test_week_history_keeps_days_apart, test_foreign_branch_marks_are_not_mixed_in,
              test_reminder_lists_only_what_is_missing,
              test_paper_marks_do_not_touch_photo_journals):
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
