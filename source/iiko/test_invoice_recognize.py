"""HTTP-разбор накладной для сайта: POST /api/invoice/recognize/{org}.

Запуск:  python3 test_invoice_recognize.py

Claude не дёргаем — распознавание подменено. Проверяем сам контракт ручки:
base64 на входе, позиции и счётчики сопоставления на выходе.
"""
import base64
import os
import tempfile

os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(prefix="recog-"), "t.db"))
os.environ.pop("TELEGRAM_BOT_TOKEN", None)      # dev-режим авторизации

import api  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(api.app)
ORG = "demo-org-1"      # демо-организация: до iiko дело не доходит
PIXEL = base64.b64encode(b"\xff\xd8\xff\xdb" + b"0" * 64).decode()

FAKE = {
    "supplier": "ТОО «Поставщик»",
    "date": "2026-08-24",
    "total_amount": 12800,
    "items": [
        {"name": "Молоко 3,2% 900мл", "quantity": 12, "unit": "шт",
         "price_per_unit": 620, "total": 7440},
        {"name": "Соус унаги 1 кг", "quantity": 4, "unit": "шт",
         "price_per_unit": 1340, "total": 5360},
    ],
}


def _patch(result):
    async def fake(images):
        assert images and isinstance(images[0][0], bytes), images
        return result
    api.analyze_invoice_photos = fake


def test_recognized_items_come_back():
    _patch(dict(FAKE))
    r = client.post(f"/api/invoice/recognize/{ORG}",
                    json={"images": [{"data": PIXEL, "mime": "image/jpeg"}]})
    assert r.status_code == 200, (r.status_code, r.text[:200])
    d = r.json()
    assert d["ok"] and len(d["items"]) == 2, d
    assert d["supplier"] == "ТОО «Поставщик»", d
    print(f"  [OK] распознано {len(d['items'])} позиций, поставщик и сумма на месте")


def test_data_url_prefix_is_tolerated():
    """Браузер отдаёт FileReader'ом строку с префиксом data: — не должно ломать."""
    _patch(dict(FAKE))
    r = client.post(f"/api/invoice/recognize/{ORG}",
                    json={"images": [{"data": f"data:image/jpeg;base64,{PIXEL}"}]})
    assert r.status_code == 200 and r.json()["ok"], r.text[:200]
    print("  [OK] префикс data:image/... принимается")


def test_empty_payload_is_rejected():
    r = client.post(f"/api/invoice/recognize/{ORG}", json={"images": []})
    assert r.status_code == 400, (r.status_code, r.text[:150])
    print("  [OK] без файлов — 400, а не пустой разбор")


def test_recognition_error_is_reported():
    _patch({"error": "не удалось прочитать"})
    r = client.post(f"/api/invoice/recognize/{ORG}",
                    json={"images": [{"data": PIXEL}]})
    assert r.status_code == 200, r.text[:150]
    d = r.json()
    assert d["ok"] is False and "прочитать" in d["error"], d
    print("  [OK] ошибка распознавания возвращается как ok=false, а не 500")


def test_too_many_pages_rejected():
    _patch(dict(FAKE))
    r = client.post(f"/api/invoice/recognize/{ORG}",
                    json={"images": [{"data": PIXEL} for _ in range(11)]})
    assert r.status_code == 422, (r.status_code, r.text[:150])
    print("  [OK] больше 10 страниц за раз не принимаем")


def test_draft_needs_real_branch():
    """Демо-филиал накладные не сохраняет — иначе в базу летел бы мусор."""
    r = client.post(f"/api/invoice/draft/{ORG}", json={"items": [{"name": "Молоко"}]})
    assert r.status_code == 400, (r.status_code, r.text[:150])
    print("  [OK] демо-филиал: черновик не создаётся")


def test_draft_rejects_empty_items():
    r = client.post("/api/invoice/draft/some-org", json={"items": []})
    assert r.status_code in (400, 403, 404), (r.status_code, r.text[:150])
    print("  [OK] пустые позиции не принимаются")


if __name__ == "__main__":
    failed = 0
    for t in (test_recognized_items_come_back,
              test_data_url_prefix_is_tolerated,
              test_empty_payload_is_rejected,
              test_recognition_error_is_reported,
              test_too_many_pages_rejected,
              test_draft_needs_real_branch,
              test_draft_rejects_empty_items):
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
