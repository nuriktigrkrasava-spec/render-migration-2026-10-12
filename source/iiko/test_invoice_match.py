"""Тесты умного сопоставления накладных и локальной базы номенклатуры.

Запуск:  python3 test_invoice_match.py

В iiko не ходим: клиент замокан, база — временный SQLite-файл.
"""
import asyncio
import os
import tempfile

# Своя БД до импорта database/api — иначе подцепим боевую
_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="match-test-"), "test.db")
os.environ["DB_PATH"] = _TMP_DB

import database  # noqa: E402
import api  # noqa: E402


# ── фикстуры ──────────────────────────────────────────────────────────────
CATALOG = [
    {"id": "P-MILK", "name": "Молоко 3,2%", "unit": "л", "type": "GOODS"},
    {"id": "P-MILKSHAKE", "name": "Молочный коктейль клубника", "unit": "порц", "type": "DISH"},
    {"id": "P-BUTTER", "name": "Масло сливочное 82,5%", "unit": "кг", "type": "GOODS"},
    {"id": "P-ROLL", "name": "Ролл Филадельфия", "unit": "порц", "type": "DISH"},
    {"id": "P-SAUCE", "name": "Соус унаги заготовка", "unit": "кг", "type": "PREPARED"},
    {"id": "P-MOD", "name": "Без лука", "unit": "шт", "type": "MODIFIER"},
    {"id": "P-DELIVERY", "name": "Услуга доставки", "unit": "шт", "type": "SERVICE"},
    {"id": "P-NOTYPE", "name": "Салфетки бумажные", "unit": "шт"},  # тип не пришёл
]


class FakeClient:
    """Отдаёт весь каталог на пустой запрос, считает походы в iiko."""

    def __init__(self, catalog=None, fail=False):
        self.catalog = CATALOG if catalog is None else catalog
        self.calls = 0
        self.fail = fail
        self.base_url = "https://iiko.example.local/resto"

    def catalog_key(self, org_id):
        return "web:iiko.example.local"

    async def search_nomenclature(self, org_id, query="", limit=30, types=None):
        self.calls += 1
        self.last_types = types
        if self.fail:
            raise RuntimeError("iiko недоступен")
        items = self.catalog
        if types:   # как настоящий iiko: фильтр по типам на его стороне
            want = {t.upper() for t in types}
            items = [p for p in items if not p.get("type") or p["type"].upper() in want]
        q = (query or "").strip().lower()
        items = [p for p in items if not q or q in p["name"].lower()]
        return items[:limit]


def _reset_db():
    api._CATALOG_LAST_FAIL.clear()   # пауза после отказа iiko живёт в процессе
    conn = database.get_db()
    for t in ("iiko_products", "iiko_products_sync", "invoice_match_memory"):
        try:
            conn.execute(f"DELETE FROM {t}")
        except Exception:
            pass
    conn.commit()
    conn.close()


# ── тесты ─────────────────────────────────────────────────────────────────
def test_score_picks_right_product():
    """Бренд в кавычках, проценты и объём не должны уводить в сторону."""
    goods = api._filter_purchasable(CATALOG)
    best, score, confident = api._best_product_match(
        goods, 'Молоко "Простоквашино" 3,2% 900мл')
    assert best and best["id"] == "P-MILK", (best, score)
    print(f"  [OK] «Молоко \"Простоквашино\" 3,2% 900мл» → Молоко 3,2% (score={score:.2f})")


def test_brand_breaks_the_tie():
    """Бренд в кавычках не мешает совпасть по сути, но решает спор одинаковых."""
    catalog = [
        {"id": "S-TER", "name": "Соус терияки", "unit": "кг", "type": "GOODS"},
        {"id": "S-UNA", "name": "Соус унаги", "unit": "кг", "type": "GOODS"},
    ]
    best, score, _c = api._best_product_match(catalog, 'Соус "Унаги" 1 кг')
    assert best and best["id"] == "S-UNA", (best, score)
    print(f"  [OK] «Соус \"Унаги\" 1 кг» → Соус унаги (score={score:.2f})")


def test_dishes_are_filtered_out():
    """В базу идут только закупаемые товары: блюда/полуфабрикаты/модификаторы — мимо."""
    kept = {p["id"] for p in api._filter_purchasable(CATALOG)}
    assert "P-MILK" in kept and "P-BUTTER" in kept, kept
    for dropped in ("P-MILKSHAKE", "P-ROLL", "P-SAUCE", "P-MOD", "P-DELIVERY"):
        assert dropped not in kept, f"{dropped} не должен попасть в базу: {kept}"
    assert "P-NOTYPE" in kept, "позиция без поля type должна остаться (толерантность)"
    print(f"  [OK] из {len(CATALOG)} позиций в базу ушло {len(kept)}: блюда и модификаторы отсеяны")


def test_asks_iiko_only_for_goods():
    """У iiko просим сразу только закупаемые типы — не весь справочник с блюдами."""
    _reset_db()
    client = FakeClient()
    asyncio.run(api._auto_match_items(client, "ORG-1",
                                      [{"name": "Молоко", "quantity": 1,
                                        "price_per_unit": 1, "total": 1}]))
    assert client.last_types == ["GOODS"], client.last_types
    print(f"  [OK] запрос в iiko с фильтром types={client.last_types}")


def test_dish_does_not_win_match():
    """Блюдо «Молочный коктейль» не должно перебивать товар «Молоко»."""
    _reset_db()
    client = FakeClient()
    items = [{"name": "Молоко 3,2% 900 мл", "quantity": 2, "price_per_unit": 500, "total": 1000}]
    sendable, unmatched, _w = asyncio.run(api._auto_match_items(client, "ORG-1", items))
    assert not unmatched, unmatched
    assert sendable[0]["productId"] == "P-MILK", sendable
    print("  [OK] сопоставилось с товаром, не с блюдом")


def test_catalog_is_cached_in_db():
    """Второй разбор накладной не должен ходить в iiko: каталог уже в базе."""
    _reset_db()
    client = FakeClient()
    items = [{"name": "Масло сливочное", "quantity": 1, "price_per_unit": 100, "total": 100}]
    asyncio.run(api._auto_match_items(client, "ORG-1", list(items)))
    first = client.calls
    asyncio.run(api._auto_match_items(client, "ORG-1", [dict(items[0])]))
    assert first == 1, f"первый разбор должен сходить в iiko ровно раз, было {first}"
    assert client.calls == 1, f"второй разбор не должен ходить в iiko, было {client.calls}"
    rows = database.load_iiko_products("web:iiko.example.local")
    assert rows and all(r.get("type") != "DISH" for r in rows), rows
    print(f"  [OK] каталог в базе ({len(rows)} позиций), второй разбор iiko не дёргает")


def test_falls_back_to_db_when_iiko_down():
    """iiko лёг — матчим по последнему сохранённому каталогу, а не пустоте."""
    _reset_db()
    asyncio.run(api._auto_match_items(FakeClient(), "ORG-1",
                                      [{"name": "Молоко", "quantity": 1,
                                        "price_per_unit": 1, "total": 1}]))
    database.touch_iiko_products_sync("web:iiko.example.local", stale=True)
    dead = FakeClient(fail=True)
    sendable, unmatched, _w = asyncio.run(api._auto_match_items(
        dead, "ORG-1", [{"name": "Масло сливочное 82,5%", "quantity": 1,
                         "price_per_unit": 1, "total": 1}]))
    assert not unmatched, unmatched
    assert sendable[0]["productId"] == "P-BUTTER", sendable
    print("  [OK] iiko недоступен → сопоставили по базе")


def test_new_product_is_picked_up_without_waiting_for_ttl():
    """Товар завели в iiko только что — позиция не должна остаться несопоставленной:
    промах по снимку заставляет обновить справочник и попробовать снова."""
    _reset_db()
    client = FakeClient()
    asyncio.run(api._auto_match_items(client, "ORG-1",
                                      [{"name": "Молоко", "quantity": 1,
                                        "price_per_unit": 1, "total": 1}]))
    calls_before = client.calls
    # снимок ещё свежий по TTL (сутки), но старше окна досыла
    database.touch_iiko_products_sync("web:iiko.example.local", age_minutes=30)
    client.catalog = CATALOG + [{"id": "P-NEW", "name": "Мука пшеничная в/с",
                                 "unit": "кг", "type": "GOODS"}]
    items = [{"name": "Мука пшеничная высший сорт", "quantity": 1,
              "price_per_unit": 1, "total": 1}]
    sendable, unmatched, _w = asyncio.run(api._auto_match_items(client, "ORG-1", items))
    assert not unmatched, unmatched
    assert sendable[0]["productId"] == "P-NEW", sendable
    assert client.calls > calls_before, "справочник должен был обновиться"
    print("  [OK] промах → обновили справочник → нашли новый товар")


def test_unit_uuid_is_resolved_and_converted():
    """У самохостед iiko единица приходит UUID'ом. Её надо расшифровать, иначе
    г→кг не конвертируется и оператор видит предупреждение с «кашей символов»."""
    _reset_db()
    uuid = "7ba81c3a-8de5-8f9d-fb9f-e39efcbc57cc"

    class UnitClient(FakeClient):
        def __init__(self):
            super().__init__([{"id": "P-FLOUR", "name": "Мука пшеничная",
                               "unit": uuid, "type": "GOODS"}])

        async def get_measure_units_map(self):
            return {uuid: "кг"}

    client = UnitClient()
    items = [{"name": "Мука пшеничная", "quantity": 500, "unit": "г",
              "price_per_unit": 0.4, "total": 200}]
    sendable, unmatched, warns = asyncio.run(api._auto_match_items(client, "ORG-1", items))
    assert not unmatched and not warns, (unmatched, warns)
    assert items[0]["iiko_unit"] == "кг", items[0]
    assert abs(sendable[0]["amount"] - 0.5) < 1e-9, sendable
    assert abs(sendable[0]["price"] - 400) < 1e-6, sendable
    print("  [OK] единица UUID → «кг», 500 г → 0.5 кг, цена пересчитана")


def test_iiko_failure_does_not_hammer_the_server():
    """После отказа iiko не долбим авторизацией: работаем по снимку из базы."""
    _reset_db()
    asyncio.run(api._auto_match_items(FakeClient(), "ORG-1",
                                      [{"name": "Молоко", "quantity": 1,
                                        "price_per_unit": 1, "total": 1}]))
    database.touch_iiko_products_sync("web:iiko.example.local", stale=True)
    dead = FakeClient(fail=True)
    for _ in range(3):
        asyncio.run(api._auto_match_items(
            dead, "ORG-1", [{"name": "Неведомая позиция", "quantity": 1,
                             "price_per_unit": 1, "total": 1}]))
    assert dead.calls == 1, f"после отказа должен быть ровно 1 поход в iiko, было {dead.calls}"
    print("  [OK] отказ iiko → одна попытка, дальше пауза")


def test_saved_draft_is_rematched_on_open():
    """Черновик, сохранённый несопоставленным, досопоставляется при открытии
    в редакторе: сбой iiko в прошлом не должен оставлять оператора с пустотой."""
    _reset_db()
    client = FakeClient()
    # накладная распозналась, когда iiko лежал → позиции без товара
    items = [{"name": "Молоко 3,2%", "quantity": 1, "price_per_unit": 1, "total": 1}]
    asyncio.run(api._auto_match_items(FakeClient(fail=True), "ORG-1", items))
    assert not items[0].get("iiko_product_id"), items[0]
    # оператор открывает черновик позже, iiko уже отвечает
    _reset_db()
    asyncio.run(api._auto_match_items(client, "ORG-1", items))
    assert items[0].get("iiko_product_id") == "P-MILK", items[0]
    print("  [OK] сохранённый черновик досопоставился при повторном открытии")


def test_match_from_another_server_is_redone():
    """У сети свой iiko-сервер почти на каждый филиал: товар, подобранный по
    справочнику одного сервера, на другом невалиден — сопоставляем заново."""
    _reset_db()
    a = FakeClient(); a.catalog_key = lambda org: "web:server-a"
    items = [{"name": "Молоко 3,2%", "quantity": 1, "price_per_unit": 1, "total": 1}]
    asyncio.run(api._auto_match_items(a, "ORG-A", items))
    assert items[0]["iiko_source"] == "web:server-a", items[0]
    old_id = items[0]["iiko_product_id"]

    b = FakeClient([{"id": "B-MILK", "name": "Молоко 3,2%", "unit": "л", "type": "GOODS"}])
    b.catalog_key = lambda org: "web:server-b"
    sendable, unmatched, _w = asyncio.run(api._auto_match_items(b, "ORG-B", items))
    assert not unmatched, unmatched
    assert sendable[0]["productId"] == "B-MILK" != old_id, sendable
    assert items[0]["iiko_source"] == "web:server-b", items[0]
    print("  [OK] при смене филиала товар пересопоставлен по его справочнику")


def test_memory_beats_algorithm():
    """Исправление оператора запоминается и в следующий раз ставится уверенно."""
    _reset_db()
    client = FakeClient()
    # оператор в редакторе выбрал «Масло сливочное» для строки «М/сл 82,5 Бурёнка»
    api.remember_invoice_matches(client, "ORG-1", [
        {"name": "М/сл 82,5 Бурёнка", "iiko_product_id": "P-BUTTER",
         "iiko_product_name": "Масло сливочное 82,5%"},
    ])
    items = [{"name": "М/сл 82,5 Бурёнка", "quantity": 1, "price_per_unit": 100, "total": 100}]
    sendable, unmatched, _w = asyncio.run(api._auto_match_items(client, "ORG-1", items))
    assert not unmatched, unmatched
    assert sendable[0]["productId"] == "P-BUTTER", sendable
    assert items[0].get("needs_review") is False, items[0]
    print("  [OK] запомненное сопоставление применилось без «проверьте»")


def test_unconfident_match_is_flagged():
    """Неуверенное совпадение помечается needs_review, но подставляется."""
    _reset_db()
    client = FakeClient()
    items = [{"name": "Масло 82", "quantity": 1, "price_per_unit": 100, "total": 100}]
    sendable, unmatched, _w = asyncio.run(api._auto_match_items(client, "ORG-1", items))
    assert not unmatched, unmatched
    assert items[0].get("auto_matched") is True, items[0]
    assert "match_score" in items[0], items[0]
    print(f"  [OK] авто-подбор помечен: score={items[0]['match_score']}, "
          f"needs_review={items[0].get('needs_review')}")


def test_unmatched_never_leaks_empty_productid():
    """Совсем непохожее уходит в unmatched, пустых productId в отправке нет."""
    _reset_db()
    client = FakeClient()
    items = [
        {"name": "Молоко 3,2%", "quantity": 2, "price_per_unit": 50, "total": 100},
        {"name": "Керосин авиационный", "quantity": 1, "price_per_unit": 10, "total": 10},
    ]
    sendable, unmatched, _w = asyncio.run(api._auto_match_items(client, "ORG-1", items))
    assert unmatched == ["Керосин авиационный"], unmatched
    assert all(s.get("productId") for s in sendable), sendable
    print("  [OK] ненайденное → unmatched, пустых productId нет")


def test_own_invoice_shows_its_composition():
    """Накладные из iiko отдавали состав в positions, а свои — только ЧИСЛО
    позиций: в карточке у них было пусто, «состава нет». Ключи у своих другие
    (quantity / price_per_unit), поэтому читаем оба набора."""
    own = api._own_position({"name": "Лосось с/м", "quantity": 10, "unit": "кг",
                             "price_per_unit": 9300, "total": 93000})
    assert own == {"name": "Лосось с/м", "qty": 10, "unit": "кг",
                   "price": 9300, "sum": 93000}, own
    iiko_like = api._own_position({"name": "Рис", "amount": 5, "unit": "кг",
                                   "price": 800, "sum": 4000})
    assert iiko_like["qty"] == 5 and iiko_like["price"] == 800, iiko_like
    print("  [OK] состав своей накладной читается и в своих ключах, и в ключах iiko")


def test_position_without_name_does_not_break_the_card():
    p = api._own_position({"iiko_product_name": "Сопоставленный товар", "quantity": 2})
    assert p["name"] == "Сопоставленный товар" and p["price"] == 0, p
    assert api._own_position({})["name"] == "Позиция"
    print("  [OK] позиция без имени и цены не роняет карточку")


if __name__ == "__main__":
    failed = 0
    for t in (test_own_invoice_shows_its_composition,
              test_position_without_name_does_not_break_the_card,
              test_score_picks_right_product,
              test_brand_breaks_the_tie,
              test_dishes_are_filtered_out,
              test_asks_iiko_only_for_goods,
              test_dish_does_not_win_match,
              test_catalog_is_cached_in_db,
              test_falls_back_to_db_when_iiko_down,
              test_new_product_is_picked_up_without_waiting_for_ttl,
              test_unit_uuid_is_resolved_and_converted,
              test_iiko_failure_does_not_hammer_the_server,
              test_saved_draft_is_rematched_on_open,
              test_match_from_another_server_is_redone,
              test_memory_beats_algorithm,
              test_unconfident_match_is_flagged,
              test_unmatched_never_leaks_empty_productid):
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
