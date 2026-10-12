"""Сравнение цен по накладной: лучшая цена, переплата в деньгах, сеть.

Запуск:  python3 test_price_compare.py
"""
import price_compare as P

ROWS_MINE = [   # свой филиал: колу брали у двух поставщиков
    {"product_id": "cola", "price_per_unit": 320, "supplier_name": "ТОО Напитки", "date": "2026-08-12", "unit": "шт"},
    {"product_id": "cola", "price_per_unit": 305, "supplier_name": "ИП Оптовик", "date": "2026-08-20", "unit": "шт"},
    {"product_id": "milk", "price_per_unit": 700, "supplier_name": "ТОО Молоко", "date": "2026-08-19", "unit": "л"},
    {"product_id": "other", "price_per_unit": 10, "supplier_name": "Левый", "date": "2026-08-01", "unit": "кг"},
]
ROWS_NEIGHBOUR = [  # соседний филиал берёт колу заметно дешевле
    {"product_id": "cola", "price_per_unit": 285, "supplier_name": "ТОО Кока-Кола", "date": "2026-08-28", "unit": "шт"},
]
INVOICE = [
    {"iiko_product_id": "cola", "iiko_product_name": "Кока-Кола 0.5", "quantity": 120,
     "price_per_unit": 320, "unit": "шт"},
    {"iiko_product_id": "milk", "iiko_product_name": "Молоко 3.2%", "quantity": 10,
     "price_per_unit": 690, "unit": "л"},
    {"iiko_product_id": "new", "iiko_product_name": "Новинка", "quantity": 5,
     "price_per_unit": 999, "unit": "шт"},
]


def test_best_price_is_the_lowest_of_the_branch():
    best = P.best_by_product(ROWS_MINE, ["cola", "milk"], branch="Астана")
    assert best["cola"]["price_per_unit"] == 305, best
    assert best["cola"]["supplier_name"] == "ИП Оптовик", best
    assert "other" not in best, best                 # чужие товары не подмешиваются
    assert best["milk"]["branch"] == "Астана", best
    print(f"  [OK] лучшая по филиалу: кола {best['cola']['price_per_unit']} у {best['cola']['supplier_name']}")


def test_network_beats_the_branch():
    mine = P.best_by_product(ROWS_MINE, ["cola"], branch="Астана")
    nb = P.best_by_product(ROWS_NEIGHBOUR, ["cola"], branch="Алматы")
    net = P.merge_best([mine, nb])
    assert net["cola"]["price_per_unit"] == 285 and net["cola"]["branch"] == "Алматы", net
    print("  [OK] по сети дешевле: 285 ₸ в Алматы вместо 305 в своём филиале")


def test_overpay_is_counted_in_money_not_percent():
    """120 бутылок по 320 против 285 — это 4 200 ₸, а не «на 12 % дороже»."""
    net = P.merge_best([P.best_by_product(ROWS_MINE, ["cola", "milk"], "Астана"),
                        P.best_by_product(ROWS_NEIGHBOUR, ["cola"], "Алматы")])
    r = P.overpay(INVOICE, net)
    assert r["positions"] == 1 and r["lines"][0]["product_id"] == "cola", r
    assert r["lines"][0]["money"] == 4200.0, r       # (320 − 285) × 120
    assert r["total"] == 4200.0, r
    assert r["lines"][0]["branch"] == "Алматы", r
    print(f"  [OK] переплата {r['total']:.0f} ₸ на позиции «{r['lines'][0]['name']}»")


def test_cheaper_than_before_is_not_an_overpay():
    """Молоко взяли по 690 при лучшей 700 — это находка, а не переплата."""
    best = P.best_by_product(ROWS_MINE, ["milk"], "Астана")
    r = P.overpay(INVOICE, best)
    assert r["positions"] == 0 and r["total"] == 0.0, r
    assert r["cheaper"] == 1, r
    print("  [OK] взяли дешевле прошлого — считается находкой, а не переплатой")


def test_position_without_history_stays_silent():
    """«Новинка» не сравнивается: молчание честнее выдуманной выгоды."""
    best = P.best_by_product(ROWS_MINE, ["cola", "milk", "new"], "Астана")
    assert "new" not in best, best
    r = P.overpay(INVOICE, best)
    assert r["compared"] == 2, r
    assert all(l["product_id"] != "new" for l in r["lines"]), r
    print("  [OK] позиция без закупок в истории в переплату не попадает")


def test_kopeck_difference_is_noise():
    best = {"cola": {"price_per_unit": 319.5, "supplier_name": "X", "unit": "шт"}}
    r = P.overpay([INVOICE[0]], best)
    assert r["positions"] == 0, r                     # 0.16 % — округление, а не находка
    print("  [OK] разница в доли процента не выдаётся за переплату")


if __name__ == "__main__":
    failed = 0
    for t in (test_best_price_is_the_lowest_of_the_branch, test_network_beats_the_branch,
              test_overpay_is_counted_in_money_not_percent,
              test_cheaper_than_before_is_not_an_overpay,
              test_position_without_history_stays_silent, test_kopeck_difference_is_noise):
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
