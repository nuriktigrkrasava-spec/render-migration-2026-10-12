"""Тест: количество в iiko уходит в ЕДИНИЦЕ ТОВАРА из самой iiko (it['iiko_unit']).
_reconcile_amount переводит кол-во в единицу товара (г→кг, мл→л), а цену выводит
из суммы, чтобы amount*price==sum всегда. Неизвестное расхождение единиц — warning.
Запуск: python3 test_invoice_units.py
"""
import os, tempfile
os.environ.setdefault("DB_PATH", tempfile.mktemp(suffix="_units_test.db"))
import database; database.init_db()
import api


def _approx(a, b, eps=1e-6):
    return abs(a - b) < eps


def test_same_unit_no_conversion():
    it = {"name": "Кола", "quantity": 6, "unit": "шт", "iiko_unit": "шт",
          "price_per_unit": 500, "total": 3000}
    amount, price, total, warn = api._reconcile_amount(it)
    assert _approx(amount, 6) and _approx(total, 3000) and _approx(price, 500)
    assert warn is None
    assert _approx(amount * price, total)  # инвариант iiko
    print("  [OK] одинаковая единица (шт): без конверсии, amount*price==sum")


def test_grams_to_kg():
    # 500 г, товар в iiko в кг, итог строки 500 ₸ → 0.5 кг по 1000 ₸/кг
    it = {"name": "Соль", "quantity": 500, "unit": "г", "iiko_unit": "кг",
          "price_per_unit": 1000, "total": 500}
    amount, price, total, warn = api._reconcile_amount(it)
    assert _approx(amount, 0.5), f"amount={amount}, ждали 0.5 кг"
    assert _approx(price, 1000), f"price={price}, ждали 1000 ₸/кг"
    assert _approx(total, 500) and warn is None
    assert _approx(amount * price, total)
    print("  [OK] г→кг: 500 г → 0.5 кг, цена 1000 ₸/кг, amount*price==sum")


def test_ml_to_l():
    it = {"name": "Масло", "quantity": 2000, "unit": "мл", "iiko_unit": "л",
          "price_per_unit": 0, "total": 1800}
    amount, price, total, warn = api._reconcile_amount(it)
    assert _approx(amount, 2.0), f"amount={amount}, ждали 2 л"
    assert _approx(price, 900), f"price={price}, ждали 900 ₸/л"
    assert warn is None and _approx(amount * price, total)
    print("  [OK] мл→л: 2000 мл → 2 л, цена выведена из суммы")


def test_container_packaging_conversion():
    # 2 ящика; товар в iiko в «шт», фасовка Ящик ×12 → 24 шт, цена из суммы
    it = {"name": "Пиво", "quantity": 2, "unit": "Ящик", "iiko_unit": "шт",
          "iiko_containers": [{"name": "Ящик", "factor": 12}],
          "price_per_unit": 0, "total": 2400}
    amount, price, total, warn = api._reconcile_amount(it)
    assert _approx(amount, 24), f"amount={amount}, ждали 24 шт"
    assert _approx(price, 100), f"price={price}, ждали 100 ₸/шт"
    assert warn is None and _approx(amount * price, total)
    print("  [OK] фасовка: 2 ящика → 24 шт (×12), цена из суммы")


def test_unknown_mismatch_warns_and_keeps_qty():
    # ящик vs шт — фактор неизвестен: количество не трогаем, но предупреждаем
    it = {"name": "Пиво", "quantity": 3, "unit": "ящик", "iiko_unit": "шт",
          "price_per_unit": 100, "total": 300}
    amount, price, total, warn = api._reconcile_amount(it)
    assert _approx(amount, 3), "при неизвестной паре количество менять нельзя"
    assert warn and "ящик" in warn and "шт" in warn
    assert _approx(amount * price, total)
    print("  [OK] неизвестная пара (ящик/шт): warning, кол-во не тронуто")


def test_no_iiko_unit_backward_compat():
    # старые позиции без iiko_unit — ведём себя как раньше
    it = {"name": "X", "quantity": 4, "unit": "шт", "price_per_unit": 25, "total": 100}
    amount, price, total, warn = api._reconcile_amount(it)
    assert _approx(amount, 4) and warn is None and _approx(amount * price, total)
    print("  [OK] без iiko_unit: обратная совместимость, без конверсии")


if __name__ == "__main__":
    failed = 0
    for t in (test_same_unit_no_conversion, test_grams_to_kg, test_ml_to_l,
              test_container_packaging_conversion,
              test_unknown_mismatch_warns_and_keeps_qty, test_no_iiko_unit_backward_compat):
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
    raise SystemExit(1 if failed else 0)
