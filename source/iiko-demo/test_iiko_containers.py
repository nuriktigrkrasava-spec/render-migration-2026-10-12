"""Тест: iiko.parse_containers толерантно достаёт фасовки товара
[{name, factor}] из ответа iiko с разными именами полей; при отсутствии —
кладёт диагностические ключи в _container_keys (для разбора структуры на деплое).
Запуск: python3 test_iiko_containers.py
"""
import iiko


def test_standard_containers():
    p = {"id": "1", "name": "Мука", "containers": [
        {"name": "Мешок", "count": 25},
        {"name": "Упаковка", "count": 2.0},
    ]}
    out = iiko.parse_containers(p)
    assert out == [{"name": "Мешок", "factor": 25.0},
                   {"name": "Упаковка", "factor": 2.0}], out
    print("  [OK] стандартные containers с count → [{name,factor}]")


def test_alt_field_names():
    p = {"id": "2", "name": "Масло", "packagings": [
        {"shortName": "Ящик", "fullCount": 12},
    ]}
    out = iiko.parse_containers(p)
    assert out == [{"name": "Ящик", "factor": 12.0}], out
    print("  [OK] альтернативные поля (packagings/shortName/fullCount)")


def test_no_containers_records_diagnostic_keys():
    p = {"id": "3", "name": "Соль", "productContainers": "не список",
         "packagingInfo": {}}
    out = iiko.parse_containers(p)
    assert out == []
    assert "_container_keys" in p and "packagingInfo" in p["_container_keys"], p.get("_container_keys")
    print("  [OK] нет фасовок → диагностика контейнер-ключей в _container_keys")


def test_clean_product_no_keys():
    p = {"id": "4", "name": "Вода", "num": "10"}
    out = iiko.parse_containers(p)
    assert out == [] and "_container_keys" not in p
    print("  [OK] товар без контейнер-полей → пусто, без мусора")


if __name__ == "__main__":
    failed = 0
    for t in (test_standard_containers, test_alt_field_names,
              test_no_containers_records_diagnostic_keys, test_clean_product_no_keys):
        print(f"\n>>> {t.__name__}")
        try:
            t()
        except AssertionError as e:
            failed += 1; print(f"  [FAIL] {e}")
        except Exception as e:
            failed += 1; print(f"  [ERROR] {type(e).__name__}: {e}")
    print(f"\n{'='*50}\n{'ВСЕ ТЕСТЫ ОК' if not failed else f'ПРОВАЛЕНО: {failed}'}")
    raise SystemExit(1 if failed else 0)
