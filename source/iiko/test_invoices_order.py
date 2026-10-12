"""Порядок накладных в списке /api/invoices.

Запуск:  python3 test_invoices_order.py

Зачем: в одном списке лежат накладные двух источников с РАЗНЫМ форматом даты —
из iiko приходит «15.01.2026» (_fmt_date), у своих «2026-01-15». Сортировка
строк по такому винегрету ставила все iiko-накладные ниже своих независимо от
дня. Фура на «Складе» показывает только первые 12 ячеек — значит в кузове
оказывались не самые свежие поставки.
"""
import os
import tempfile

os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(prefix="invord-"), "t.db"))
os.environ.pop("TELEGRAM_BOT_TOKEN", None)

import api  # noqa: E402


def test_both_date_formats_share_one_key():
    """Ключ приводит оба формата к ГГГГ-ММ-ДД — их можно сравнивать."""
    assert api._inv_date_key("15.01.2026") == "2026-01-15"
    assert api._inv_date_key("2026-01-15") == "2026-01-15"
    print("  [OK] «15.01.2026» и «2026-01-15» дают один ключ")


def test_fresh_first_across_sources():
    """Свежая накладная из iiko обязана быть выше старой своей."""
    rows = [
        {"id": "BOT-1", "date": "2026-08-01"},     # своя, старая
        {"id": "IIKO-1", "date": "28.08.2026"},    # из iiko, свежая
        {"id": "BOT-2", "date": "2026-08-20"},     # своя, средняя
    ]
    rows.sort(key=lambda x: api._inv_date_key(x["date"]), reverse=True)
    assert [r["id"] for r in rows] == ["IIKO-1", "BOT-2", "BOT-1"], rows
    print("  [OK] порядок по дню, а не по источнику: IIKO-1 → BOT-2 → BOT-1")


def test_broken_date_does_not_crash():
    """Пустая или кривая дата не должна ронять список."""
    assert api._inv_date_key("") == ""
    assert api._inv_date_key(None) == ""
    assert api._inv_date_key("неизвестно") == "неизвестно"
    print("  [OK] пустая и кривая дата переживаются без исключения")


if __name__ == "__main__":
    failed = 0
    for t in (test_both_date_formats_share_one_key,
              test_fresh_first_across_sources,
              test_broken_date_does_not_crash):
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
