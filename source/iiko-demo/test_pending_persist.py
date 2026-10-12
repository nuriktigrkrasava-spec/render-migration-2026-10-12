"""Тест: pending-накладные переживают рестарт (персист в БД + восстановление).
Запуск: python3 test_pending_persist.py
Использует ВРЕМЕННУЮ БД (DB_PATH задаётся до импорта), боевую не трогает.
"""
import os, json, tempfile

os.environ["DB_PATH"] = tempfile.mktemp(suffix="_pending_test.db")
import database
database.init_db()
import api


def test_db_helpers_roundtrip():
    database.save_pending_invoice("pidA", 111, 222, json.dumps({"x": 1}))
    assert json.loads(database.get_pending_invoice("pidA")["data"]) == {"x": 1}
    assert database.get_pending_invoice_by_chat(111)["pending_id"] == "pidA"
    database.delete_pending_invoice_by_chat(111)
    assert database.get_pending_invoice("pidA") is None
    print("  [OK] db helpers: save/get/get_by_chat/delete")


def test_persist_strips_bytes_and_cache():
    pending = {
        "pending_id": "pidB", "chat_id": 555, "telegram_id": 777,
        "images": [{"file_id": "f1", "bytes": b"RAWIMAGEBYTES", "mime": "image/jpeg"}],
        "suppliers_cache": [{"id": "s"}] * 50,
        "result": {"items": [{"name": "Соль"}], "supplier": "ACME", "total_amount": 100},
        "organization_id": "org-1", "supplier_id": "sup-9", "state": "preview",
    }
    api._persist_pending(pending)
    raw = database.get_pending_invoice("pidB")["data"]
    saved = json.loads(raw)
    assert "RAWIMAGEBYTES" not in raw, "сырые байты фото попали в БД!"
    assert "suppliers_cache" not in saved, "suppliers_cache не должен персиститься"
    assert saved["result"]["supplier"] == "ACME"
    assert saved["organization_id"] == "org-1"
    assert saved["images_meta"] == [{"file_id": "f1", "mime": "image/jpeg"}]
    print("  [OK] persist: байты/кэш вырезаны, result+метаданные сохранены")


def test_rehydrate_after_restart():
    # имитируем рестарт: память пустая, данные только в БД
    api._pending_invoices.clear()
    api._pending_by_id.clear()
    p = api._get_pending_by_id("pidB")
    assert p and p["result"]["supplier"] == "ACME", "не восстановилось по pid"
    assert p["images"] == [], "после рестарта images должен быть пустым"
    assert api._pending_invoices.get(555) is p, "память не перезаполнилась"

    api._pending_invoices.clear()
    api._pending_by_id.clear()
    p2 = api._get_pending_by_chat(555)
    assert p2 and p2["pending_id"] == "pidB", "не восстановилось по chat_id"
    print("  [OK] rehydrate: pending восстановлен из БД и по pid, и по chat_id")


def test_drop_removes_from_db():
    api._get_pending_by_chat(555)  # убедимся что есть
    api._drop_pending(555)
    assert database.get_pending_invoice("pidB") is None, "drop не удалил из БД"
    assert api._get_pending_by_chat(555) is None
    print("  [OK] drop: pending удалён и из памяти, и из БД")


if __name__ == "__main__":
    failed = 0
    for t in (test_db_helpers_roundtrip, test_persist_strips_bytes_and_cache,
              test_rehydrate_after_restart, test_drop_removes_from_db):
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
