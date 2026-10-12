"""Сравнение год к году: та же мера, что в кассе, и честное окно.

Запуск:  python3 test_comparison_yoy.py

Два дефекта, из-за которых экран показывал «совсем другие цифры»:

1. Мера. Дашборд, каналы и типы оплат считают DishDiscountSumInt — реально
   оплачено, сходится с кассой. Сравнение брало DishAmountSum (до скидок) и
   завышало: на живом филиале 184,2 млн против 170,2 млн за 2026 год (+8,3%).

2. Незакрытый месяц. Текущий год обрывается сегодняшним днём, а прошлый брался
   целыми месяцами — сентябрь из одного дня сравнивался с целым сентябрём год
   назад. На живом филиале это давало рост +14,5% вместо честных +26,4%.
"""
import os
import tempfile
from datetime import date

os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(prefix="yoy-"), "t.db"))
os.environ.pop("TELEGRAM_BOT_TOKEN", None)

import api  # noqa: E402


def test_previous_year_is_cut_at_the_same_day():
    """Прошлый год обрезается тем же днём, что и сегодня в текущем."""
    assert api._prev_year_cutoff(date(2026, 9, 1)) == date(2025, 9, 1)
    assert api._prev_year_cutoff(date(2026, 1, 1)) == date(2025, 1, 1)
    assert api._prev_year_cutoff(date(2026, 12, 31)) == date(2025, 12, 31)
    print("  [OK] 01.09.2026 → окно прошлого года заканчивается 01.09.2025")


def test_leap_day_does_not_crash():
    """29 февраля в невисокосном прошлом году не существует — берём 28-е."""
    assert api._prev_year_cutoff(date(2028, 2, 29)) == date(2027, 2, 28)
    print("  [OK] 29.02 съезжает на 28.02, а не падает")


def test_revenue_uses_the_cash_measure():
    """Строка OLAP → выручка. Берём оплаченное, а не сумму до скидок."""
    row = {"DishDiscountSumInt": 10_591_966, "DishAmountSum": 12_346_761}
    assert api._olap_revenue(row) == 10_591_966, api._olap_revenue(row)
    print("  [OK] из двух мер берётся DishDiscountSumInt (как в кассе)")


def test_falls_back_when_server_hides_the_discount_measure():
    """Не у каждого сервера есть DishDiscountSumInt — тогда лучше сумма до
    скидок, чем ноль: пустой экран хуже завышенного."""
    assert api._olap_revenue({"DishAmountSum": 500}) == 500
    assert api._olap_revenue({"DishDiscountSumInt": 0, "DishAmountSum": 500}) == 500
    assert api._olap_revenue({}) == 0
    print("  [OK] без DishDiscountSumInt берётся DishAmountSum, без обеих — 0")


def test_zero_revenue_day_is_not_mistaken_for_missing():
    """День с нулём по обеим мерам — это ноль, а не отсутствие данных."""
    assert api._olap_revenue({"DishDiscountSumInt": 0, "DishAmountSum": 0}) == 0
    print("  [OK] честный ноль остаётся нулём")


if __name__ == "__main__":
    failed = 0
    for t in (test_previous_year_is_cut_at_the_same_day,
              test_leap_day_does_not_crash,
              test_revenue_uses_the_cash_measure,
              test_falls_back_when_server_hides_the_discount_measure,
              test_zero_revenue_day_is_not_mistaken_for_missing):
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
