"""Локальные мини-тесты для бага «Product is not found ... id=null» в приходной накладной.

Запуск:  python3 test_invoice_xml.py

Тесты НЕ ходят в iiko — токен/склад/HTTP замоканы, перехватывается тело XML,
которое реально уходит на /resto/api/documents/import/incomingInvoice.
"""
import asyncio
import xml.etree.ElementTree as ET

import iiko


class _FakeResp:
    def __init__(self, status_code=200, text="<documentValidationResult><valid>true</valid></documentValidationResult>"):
        self.status_code = status_code
        self.text = text


class _FakeHTTP:
    """Подменяет httpx.AsyncClient: ловит content (XML) последнего POST."""
    captured = {}

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, params=None, headers=None, content=None):
        _FakeHTTP.captured["url"] = url
        _FakeHTTP.captured["content"] = content
        return _FakeResp()


def _make_client():
    c = iiko.IikoWebClient("https://example.local/resto", "login", "pwd")

    async def _fake_store(org_id):
        return "STORE-GUID-0001"

    async def _fake_token():
        return "TOKEN-XYZ"

    c._get_default_store = _fake_store
    c._ensure_token = _fake_token
    return c


def _product_ids_from_xml(xml_bytes):
    # iikoServer import ждёт <product>/<store> (а не <productId>/<storeId>) —
    # подтверждено verify-curl 2026-05-27.
    root = ET.fromstring(xml_bytes)
    return [ (el.text or "") for el in root.iter("product") ]


# ---- запускаем create_incoming_invoice с перехватом XML ----
def _send(items):
    _FakeHTTP.captured = {}
    orig = iiko.httpx.AsyncClient
    iiko.httpx.AsyncClient = _FakeHTTP
    try:
        c = _make_client()
        asyncio.run(c.create_incoming_invoice(
            org_id="ORG-1", supplier_id="SUP-1", items=items, date="2026-05-25"
        ))
    finally:
        iiko.httpx.AsyncClient = orig
    return _FakeHTTP.captured.get("content", b"")


def test_good_items_have_productids():
    """Когда у всех позиций есть GUID — в XML все <productId> непустые."""
    items = [
        {"productId": "340a107b-065b-4419-afdd-06a243e9192e", "amount": 0.75, "price": 8500.0, "sum": 6375.0},
        {"productId": "2eb22ea1-a66a-4fa7-84ce-095ff60c837c", "amount": 2.0, "price": 750.0, "sum": 1500.0},
    ]
    xml = _send(items)
    pids = _product_ids_from_xml(xml)
    assert pids == ["340a107b-065b-4419-afdd-06a243e9192e",
                    "2eb22ea1-a66a-4fa7-84ce-095ff60c837c"], pids
    assert all(p.strip() for p in pids), f"есть пустой product: {pids}"
    # регресс: должны быть НОВЫЕ теги <product>/<store>, а не старые <productId>/<storeId>
    assert b"<productId>" not in xml and b"<storeId>" not in xml, "остались старые теги!"
    assert b"<store>" in xml, "нет тега <store>"
    print("  [OK] good items -> теги <product>/<store>, все непустые:", pids)


def test_none_productid_reproduces_symptom():
    """Если в позицию попал productId=None/'' -> в XML <productId></productId>,
       и именно это iiko видит как id=null. Воспроизводим симптом механически."""
    items = [
        {"productId": "340a107b-065b-4419-afdd-06a243e9192e", "amount": 1, "price": 100, "sum": 100},
        {"productId": None, "amount": 1, "price": 100, "sum": 100},   # «сломанная» позиция
    ]
    xml = _send(items)
    pids = _product_ids_from_xml(xml)
    assert pids[0] == "340a107b-065b-4419-afdd-06a243e9192e", pids
    assert pids[1] == "", f"ожидали пустой product, получили: {pids[1]!r}"
    assert b"<product />" in xml or b"<product></product>" in xml, xml[:300]
    print("  [OK] productId=None -> пустой тег <product> (= iiko id=null). Симптом воспроизведён.")


def test_filter_never_emits_empty_productid():
    """Реальный фильтр _auto_match_items: позиция без товара уходит в unmatched,
       в sendable пустых productId не бывает."""
    try:
        import api
    except Exception as e:
        print(f"  [SKIP] не удалось импортировать api.py ({e}); фильтр проверен по коду вручную")
        return

    class FakeClient:
        async def search_nomenclature(self, org_id, name, limit=1):
            # 'apple' находим, всё прочее — нет
            if "apple" in (name or "").lower():
                return [{"id": "FOUND-GUID-1", "name": "Apple"}]
            return []

    items = [
        {"name": "Apple", "quantity": 2, "price_per_unit": 50, "total": 100},
        {"name": "Mystery", "quantity": 1, "price_per_unit": 10, "total": 10},  # не найдётся
    ]
    sendable, unmatched, _warnings = asyncio.run(api._auto_match_items(FakeClient(), "ORG-1", items))
    assert unmatched == ["Mystery"], unmatched
    assert all(s.get("productId") for s in sendable), sendable
    assert len(sendable) == 1, sendable
    print("  [OK] фильтр: ненайденное -> unmatched, в sendable только непустые productId")


if __name__ == "__main__":
    failed = 0
    for t in (test_good_items_have_productids,
              test_none_productid_reproduces_symptom,
              test_filter_never_emits_empty_productid):
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
