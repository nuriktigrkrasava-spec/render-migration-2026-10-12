"""Каналы продаж: одно правило группировки на весь продукт.

Запуск:  python3 test_channels.py

Зачем: у сети девять типов заказа в iiko («Обычный заказ», «Самовывоз», «Сайт
самовывоз», «Доставка», «Сайт доставка», Wolt, Glovo, Chocofood, «Яндекс Еда»),
а смысла в них четыре: зал, самовывоз, доставка, агрегаторы. Разложены они были
в двух местах и по-разному — «План» группировал сам, «Продажи» показывали девять
сырых долек, а Chocofood не был записан в агрегаторы вообще и считался залом.
"""
import os
import tempfile

os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(prefix="chan-"), "t.db"))
os.environ.pop("TELEGRAM_BOT_TOKEN", None)

import api  # noqa: E402

# Все девять названий, что реально приходят из iiko по 23 филиалам сети
REAL = {
    "Обычный заказ": "hall",
    "Самовывоз": "pickup",
    "Сайт самовывоз": "pickup",
    "Доставка": "delivery",
    "Сайт доставка": "delivery",
    "Агрегаторы": "agg",
    "Wolt": "agg",
    "Glovo": "agg",
    "Chocofood": "agg",
    "Яндекс Еда": "agg",
}


def test_every_real_channel_lands_in_its_group():
    wrong = {n: api._classify_channel(n) for n, want in REAL.items()
             if api._classify_channel(n) != want}
    assert not wrong, wrong
    print(f"  [OK] все {len(REAL)} названий сети разложены верно")


def test_the_type_named_aggregators_is_not_hall():
    """Сеть пробивает большинство агрегаторских заказов ОДНИМ типом «Агрегаторы»,
    а не именем площадки. Этого названия в правиле не было, и оно молча падало
    в зал: на живых серверах за четыре недели так потерялось 2181 заказ из 2423
    агрегаторских — зал оказался завышен на 9 %, агрегаторы занижены вдесятеро.
    """
    assert api._classify_channel("Агрегаторы") == "agg"
    assert api._classify_channel("агрегатор") == "agg"
    assert api._classify_channel("Агрегатор Wolt") == "agg"
    print("  [OK] «Агрегаторы» — это агрегаторы, а не зал")


def test_chocofood_is_an_aggregator_not_hall():
    """Отдельным тестом: раньше Chocofood не совпадал ни с одним правилом и
    молча падал в «Зал» — выручка агрегатора считалась залом."""
    assert api._classify_channel("Chocofood") == "agg"
    assert api._classify_channel("ЧокоФуд") == "agg"
    print("  [OK] Chocofood и «ЧокоФуд» — агрегаторы, а не зал")


def test_site_pickup_is_not_delivery():
    """«Сайт самовывоз» содержит и «сайт», и «самовывоз» — порядок правил важен."""
    assert api._classify_channel("Сайт самовывоз") == "pickup"
    assert api._classify_channel("Сайт доставка") == "delivery"
    print("  [OK] «Сайт самовывоз» — вынос, «Сайт доставка» — доставка")


def test_unknown_channel_falls_back_to_hall():
    """Новый тип заказа не должен теряться: он попадает в зал, а не исчезает."""
    assert api._classify_channel("Банкет") == "hall"
    assert api._classify_channel("") == "hall"
    print("  [OK] незнакомый тип уходит в зал, ничего не пропадает")


def test_group_labels_cover_all_four():
    assert set(api.CHANNEL_GROUP_RU) == set(api.PLAN_TYPES), api.CHANNEL_GROUP_RU
    assert api.CHANNEL_GROUP_RU["agg"] == "Агрегаторы"
    assert api.CHANNEL_GROUP_RU["pickup"] == "Самовывоз"
    print(f"  [OK] подписи групп: {list(api.CHANNEL_GROUP_RU.values())}")


def test_sales_rows_collapse_to_four_channels():
    """Ради чего всё: девять сырых строк OLAP → четыре канала карточки,
    и выручка при этом не теряется и не удваивается."""
    rows = [
        {"OrderType.Name": "Обычный заказ", "DishDiscountSumInt": 10_591_966, "GuestNum": 3211, "OrdersCount": 3211},
        {"OrderType.Name": "Сайт доставка", "DishDiscountSumInt": 5_521_306, "GuestNum": 468, "OrdersCount": 468},
        {"OrderType.Name": "Wolt", "DishDiscountSumInt": 2_046_070, "GuestNum": 207, "OrdersCount": 207},
        {"OrderType.Name": "Доставка", "DishDiscountSumInt": 1_658_570, "GuestNum": 130, "OrdersCount": 130},
        {"OrderType.Name": "Сайт самовывоз", "DishDiscountSumInt": 810_110, "GuestNum": 79, "OrdersCount": 79},
        {"OrderType.Name": "Самовывоз", "DishDiscountSumInt": 368_740, "GuestNum": 53, "OrdersCount": 53},
        {"OrderType.Name": "Glovo", "DishDiscountSumInt": 269_177, "GuestNum": 28, "OrdersCount": 28},
        {"OrderType.Name": "Chocofood", "DishDiscountSumInt": 26_985, "GuestNum": 3, "OrdersCount": 3},
    ]
    got = api._group_sales_channels(rows)
    by = {c["name"]: c for c in got}
    assert set(by) == {"Зал", "Доставка", "Агрегаторы", "Самовывоз"}, list(by)
    assert by["Доставка"]["revenue"] == 5_521_306 + 1_658_570, by["Доставка"]
    assert by["Самовывоз"]["revenue"] == 810_110 + 368_740, by["Самовывоз"]
    assert by["Агрегаторы"]["revenue"] == 2_046_070 + 269_177 + 26_985, by["Агрегаторы"]
    assert by["Зал"]["revenue"] == 10_591_966, by["Зал"]
    assert sum(c["revenue"] for c in got) == sum(r["DishDiscountSumInt"] for r in rows), "выручка утекла"
    assert [c["name"] for c in got] == sorted(by, key=lambda n: -by[n]["revenue"]), "не по убыванию"
    print("  [OK] 8 строк → 4 канала, сумма сошлась: "
          + ", ".join(f"{c['name']} {c['revenue']:,.0f}".replace(",", " ") for c in got))


def test_empty_group_is_not_shown():
    """Филиал без агрегаторов не должен получать пустую дольку «Агрегаторы»."""
    got = api._group_sales_channels(
        [{"OrderType.Name": "Обычный заказ", "DishDiscountSumInt": 100, "GuestNum": 1, "OrdersCount": 1}])
    assert [c["name"] for c in got] == ["Зал"], got
    print("  [OK] пустые группы в карточку не попадают")


if __name__ == "__main__":
    failed = 0
    for t in (test_every_real_channel_lands_in_its_group,
              test_chocofood_is_an_aggregator_not_hall,
              test_site_pickup_is_not_delivery,
              test_unknown_channel_falls_back_to_hall,
              test_group_labels_cover_all_four,
              test_sales_rows_collapse_to_four_channels,
              test_empty_group_is_not_shown):
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
