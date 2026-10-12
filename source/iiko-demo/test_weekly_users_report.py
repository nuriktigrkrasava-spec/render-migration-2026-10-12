"""Тесты понедельничного отчёта владельцам (чистые функции, без сети).
Запуск: python3 test_weekly_users_report.py
"""
import os, tempfile
from datetime import date

os.environ["DB_PATH"] = tempfile.mktemp(suffix="_weekly_users_test.db")
import database
database.init_db()
import api


def test_last_week_ranges():
    # понедельник 06.07.2026 → прошлая неделя 29.06–05.07, позапрошлая 22.06–28.06
    cf, ct, pf, pt = api._last_week_ranges(date(2026, 7, 6))
    assert (cf, ct, pf, pt) == ("2026-06-29", "2026-07-05", "2026-06-22", "2026-06-28")
    # среда даёт ту же прошлую полную неделю (для превью среди недели)
    assert api._last_week_ranges(date(2026, 7, 8)) == (cf, ct, pf, pt)
    print("  [OK] _last_week_ranges: пн и среда дают одну и ту же полную неделю")


def test_fmt_delta_money():
    assert api._fmt_delta_money(1120, 1000) == "🟢 ▲ +12% (+120 ₸)"
    assert api._fmt_delta_money(900, 1000) == "🔴 ▼ -10% (−100 ₸)"
    assert api._fmt_delta_money(1000, 1000) == "⚪ 0%"          # без абсолюта
    assert "нет данных" in api._fmt_delta_money(500, 0)         # prev=0 → как _fmt_delta
    print("  [OK] _fmt_delta_money: проценты + абсолют, краевые случаи")


def _summary(name="Чашка", rev=2_150_000, prev_rev=1_920_000, problems=None):
    return {"name": name,
            "cur": {"revenue": rev, "guests": 443, "avg_check": rev / 443},
            "prev": {"revenue": prev_rev, "guests": 410, "avg_check": prev_rev / 410},
            "problems": problems}


def test_build_owner_week_text_clean():
    text = api._build_owner_week_text(
        [_summary(problems={"diff_shifts": 0, "diff_sum": 0.0, "unaccepted": 0,
                            "edits": 0, "type_changes": 0})],
        "2026-06-29", "2026-07-05", "2026-06-22", "2026-06-28")
    assert "Итоги недели" in text and "29.06–05.07" in text
    assert "Чашка" in text and "Выручка" in text and "Гости" in text
    assert "✅ Смены чистые" in text
    assert "Итого" not in text  # один филиал — без итога по сети
    print("  [OK] текст: один филиал без проблем")


def test_build_owner_week_text_problems():
    text = api._build_owner_week_text(
        [_summary(problems={"diff_shifts": 2, "diff_sum": -12500.0, "unaccepted": 1,
                            "edits": 3, "type_changes": 1})],
        "2026-06-29", "2026-07-05", "2026-06-22", "2026-06-28")
    assert "⚠️ Проблемы за неделю" in text
    assert "смен с расхождением кассы: 2" in text
    assert "исправленных платежей: 3, из них со сменой типа оплаты: 1" in text
    assert "непринятых смен: 1" in text
    print("  [OK] текст: блок проблем со сменой типа оплаты")


def test_build_owner_week_text_multi_and_empty():
    # два филиала → строка «Итого»; филиал без данных → «нет данных», не в итоге
    text = api._build_owner_week_text(
        [_summary("Чашка"), _summary("Час Пик", rev=1_000_000, prev_rev=1_100_000),
         {"name": "Мёртвый", "cur": None,
          "prev": {"revenue": 0, "guests": 0, "avg_check": 0}, "problems": None}],
        "2026-06-29", "2026-07-05", "2026-06-22", "2026-06-28")
    assert "Итого" in text and "нет данных" in text
    assert "3 150 000" in text  # сумма только живых филиалов
    # совсем нет данных → None (не спамим пустым отчётом)
    assert api._build_owner_week_text(
        [{"name": "X", "cur": None, "prev": {}, "problems": None}],
        "2026-06-29", "2026-07-05", "2026-06-22", "2026-06-28") is None
    print("  [OK] текст: итог по сети, филиал без данных, пустой отчёт → None")


def test_dead_branches_hidden():
    # филиал с нулями обе недели (цех/закрытая точка) скрывается из отчёта
    dead = _summary("Цех Алпамыс", rev=0, prev_rev=0)
    text = api._build_owner_week_text(
        [_summary("Чашка"), dead],
        "2026-06-29", "2026-07-05", "2026-06-22", "2026-06-28")
    assert "Цех Алпамыс" not in text and "Чашка" in text
    # если ВСЕ филиалы пустые — отчёт не шлём вовсе
    assert api._build_owner_week_text(
        [dead], "2026-06-29", "2026-07-05", "2026-06-22", "2026-06-28") is None
    print("  [OK] текст: пустые филиалы скрыты, полностью пустой отчёт → None")


def test_cloud_client_no_problems_block():
    # облачный iiko: problems=None → без блока проблем и без «✅ чистые»
    text = api._build_owner_week_text(
        [_summary(problems=None)],
        "2026-06-29", "2026-07-05", "2026-06-22", "2026-06-28")
    assert "Проблемы" not in text and "Смены чистые" not in text
    print("  [OK] текст: облачный клиент — без блока проблем")


if __name__ == "__main__":
    test_last_week_ranges()
    test_fmt_delta_money()
    test_build_owner_week_text_clean()
    test_build_owner_week_text_problems()
    test_build_owner_week_text_multi_and_empty()
    test_dead_branches_hidden()
    test_cloud_client_no_problems_block()
    print("Все тесты прошли ✅")
