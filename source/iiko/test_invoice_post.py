"""Тесты проводки/распроведения накладной через ре-импорт (set_document_status).
setStatus-эндпоинты на saya-co.iiko.it закрыты (404, логи 2026-07-04) —
единственный write-путь это import/incomingInvoice со <status> и <id> документа.
Запуск: python3 test_invoice_post.py
"""
import asyncio
import re

import iiko

DOC_ID = "e7e8fdca-1210-c6ad-019f-2bdf0e2bd3de"
DOC_NUM = "BOT-260704-1"

def _export_body(docs):
    """XML export из [{id, number, status}]."""
    blocks = []
    for d in docs:
        blocks.append(
            f"<document><id>{d['id']}</id>"
            f"<documentNumber>{d['number']}</documentNumber>"
            f"<dateIncoming>2026-07-04T10:00:00</dateIncoming>"
            f"<status>{d['status']}</status>"
            f"<supplier>SUP-1</supplier><defaultStore>STORE-1</defaultStore>"
            f"<items><item><num>1</num><product>PROD-1</product>"
            f"<amount>2.0</amount><price>50.0</price><sum>100.0</sum>"
            f"<store>STORE-1</store></item></items></document>")
    return "<incomingInvoiceDtoes>" + "".join(blocks) + "</incomingInvoiceDtoes>"


class _FakeResp:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


class _FakeHTTP:
    """Мок httpx.AsyncClient: export отдаёт state, import меняет state.
    duplicate_on_import=True имитирует сервер, игнорирующий <id> (дубль)."""
    state = []                 # текущие документы «на сервере»
    duplicate_on_import = False
    imports = []               # (status, xml) каждого импорта

    def __init__(self, *a, **kw): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False

    async def get(self, url, params=None):
        assert "export/incomingInvoice" in url, url
        return _FakeResp(200, _export_body(_FakeHTTP.state))

    async def post(self, url, params=None, headers=None, content=None, json=None):
        assert "import/incomingInvoice" in url, url
        xml = content.decode()
        status = re.search(r"<status>(\w+)</status>", xml).group(1)
        doc_id = re.search(r"<id>(.*?)</id>", xml).group(1)
        _FakeHTTP.imports.append((status, xml))
        if _FakeHTTP.duplicate_on_import:
            _FakeHTTP.state.append({"id": "DUP-" + str(len(_FakeHTTP.state)),
                                    "number": DOC_NUM, "status": status})
        else:
            for d in _FakeHTTP.state:
                if d["id"] == doc_id:
                    d["status"] = status
        return _FakeResp(200, "<documentValidationResult><valid>true</valid></documentValidationResult>")


def _client():
    c = iiko.IikoWebClient("https://example.local/resto", "login", "pwd")
    async def _tok(): return "TOKEN"
    c._ensure_token = _tok
    return c


def _run(status, **kw):
    return asyncio.run(_client().set_document_status(
        "ORG-1", DOC_ID, status, doc_number=DOC_NUM, date="2026-07-04", **kw))


def _patch():
    _FakeHTTP.state = [{"id": DOC_ID, "number": DOC_NUM, "status": "NEW"}]
    _FakeHTTP.duplicate_on_import = False
    _FakeHTTP.imports = []
    iiko.httpx.AsyncClient = _FakeHTTP


def test_post_updates_in_place():
    """Сервер обновляет по <id>: двухфазный ре-импорт (NEW→PROCESSED), без дублей."""
    _patch()
    r = _run("PROCESSED")
    assert r.get("ok"), r
    assert [s for s, _ in _FakeHTTP.imports] == ["NEW", "PROCESSED"], _FakeHTTP.imports
    assert len(_FakeHTTP.state) == 1 and _FakeHTTP.state[0]["status"] == "PROCESSED"
    # ре-импорт несёт id, номер и содержимое из export
    xml = _FakeHTTP.imports[-1][1]
    for frag in (f"<id>{DOC_ID}</id>", f"<documentNumber>{DOC_NUM}</documentNumber>",
                 "<product>PROD-1</product>", "<supplier>SUP-1</supplier>",
                 "<defaultStore>STORE-1</defaultStore>"):
        assert frag in xml, xml
    print("  [OK] проводка: NEW-проба → PROCESSED, документ один, содержимое из export")


def test_post_aborts_on_duplicate():
    """Сервер игнорирует <id> (дубль): стоп после NEW-пробы, PROCESSED не шлём."""
    _patch()
    _FakeHTTP.duplicate_on_import = True
    r = _run("PROCESSED")
    assert not r.get("ok") and "дубль" in r.get("error", ""), r
    assert [s for s, _ in _FakeHTTP.imports] == ["NEW"], "PROCESSED не должен уйти после дубля"
    assert all(d["status"] != "PROCESSED" for d in _FakeHTTP.state), _FakeHTTP.state
    print("  [OK] дубль ловится на безопасной NEW-фазе, проведённых дублей нет")


def test_unpost_single_phase():
    """Распроведение PROCESSED→NEW: одна фаза + верификация."""
    _patch()
    _FakeHTTP.state[0]["status"] = "PROCESSED"
    r = _run("NEW")
    assert r.get("ok"), r
    assert [s for s, _ in _FakeHTTP.imports] == ["NEW"], _FakeHTTP.imports
    assert _FakeHTTP.state[0]["status"] == "NEW"
    print("  [OK] распроведение одной фазой")


def test_missing_doc():
    """Документа нет в export за дату — честная ошибка, импортов ноль."""
    _patch()
    _FakeHTTP.state = []
    r = _run("PROCESSED")
    assert not r.get("ok") and "не найден" in r.get("error", ""), r
    assert not _FakeHTTP.imports
    print("  [OK] отсутствующий документ → ошибка без импортов")


def test_requires_number_and_date():
    _patch()
    r = asyncio.run(_client().set_document_status("ORG-1", DOC_ID, "PROCESSED"))
    assert not r.get("ok") and not _FakeHTTP.imports, r
    print("  [OK] без номера/даты — ошибка без похода в iiko")


if __name__ == "__main__":
    orig = iiko.httpx.AsyncClient
    failed = 0
    try:
        for t in (test_post_updates_in_place, test_post_aborts_on_duplicate,
                  test_unpost_single_phase, test_missing_doc, test_requires_number_and_date):
            print(f"\n>>> {t.__name__}")
            try:
                t()
            except AssertionError as e:
                failed += 1; print(f"  [FAIL] {e}")
            except Exception as e:
                failed += 1; print(f"  [ERROR] {type(e).__name__}: {e}")
    finally:
        iiko.httpx.AsyncClient = orig
    print(f"\n{'='*50}\n{'ВСЕ ТЕСТЫ ОК' if not failed else f'ПРОВАЛЕНО: {failed}'}")
    raise SystemExit(1 if failed else 0)
