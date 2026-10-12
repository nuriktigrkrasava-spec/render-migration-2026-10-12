"""Справочник соответствий «строка поставщика → товар iiko».

Запуск:  python3 test_match_memory.py

Зачем: в iiko прайс-лист поставщика хранит его наименования и привязку к
номенклатуре, поэтому там сопоставление учится. У нас таблица под это была
(invoice_match_memory), но пополнялась ТОЛЬКО после успешной отправки накладной
в iiko. Оператор мог поправить десять строк и не отправить — вся работа
выброшена, следующая накладная от того же поставщика опять приходила пустой.
Справочник так и стоял нулевым, а автосопоставление не училось.

Теперь выбор оператора запоминается сразу, как в iiko, и справочник видно и
можно править: ошибочную запись удаляют или перепривязывают.
"""
import os
import tempfile

os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(prefix="mem-"), "t.db"))
os.environ.pop("TELEGRAM_BOT_TOKEN", None)

import api  # noqa: E402
from database import (forget_invoice_match, get_invoice_match_memory,  # noqa: E402
                      init_db, list_invoice_match_memory, remember_invoice_match)

init_db()
KEY = "web:ss-turk-new.iiko.it"
OTHER = "web:saya-co.iiko.it"


def test_operator_choice_is_remembered_at_once():
    """Главное: выбор запомнен сразу, не дожидаясь отправки в iiko."""
    remember_invoice_match(KEY, api._match_memory_key("Молоко коровье 3,2% 1л"),
                           "p-moloko", "Молоко коровье", name_raw="Молоко коровье 3,2% 1л")
    mem = get_invoice_match_memory(KEY)
    assert mem[api._match_memory_key("Молоко коровье 3,2% 1л")]["product_id"] == "p-moloko", mem
    print("  [OK] «Молоко коровье 3,2% 1л» → «Молоко коровье» запомнено сразу")


def test_next_invoice_matches_by_memory():
    """Ради чего справочник: та же строка в следующей накладной подставляется
    сама, без скоринга и без пометки «проверьте»."""
    catalog = [{"id": "p-moloko", "name": "Молоко коровье", "unit": "л"}]
    by_id = {"p-moloko": catalog[0]}
    it = {"name": "Молоко коровье 3,2% 1л"}
    assert api._apply_match(it, catalog, get_invoice_match_memory(KEY), by_id, KEY)
    assert it["iiko_product_id"] == "p-moloko", it
    assert it["needs_review"] is False, it
    print("  [OK] следующая накладная сопоставилась по памяти, без «проверьте»")


def test_raw_line_is_kept_for_the_tab():
    """В таблице показываем строку так, как её печатает поставщик, а не ключ."""
    rows = list_invoice_match_memory(KEY)
    assert rows and rows[0]["name_raw"] == "Молоко коровье 3,2% 1л", rows
    assert rows[0]["product_name"] == "Молоко коровье", rows
    print("  [OK] в справочнике видна исходная строка «Молоко коровье 3,2% 1л»")


def test_rebinding_overwrites_and_counts_hits():
    """Оператор понял, что привязал не туда, — перепривязка, а не второй ряд."""
    remember_invoice_match(KEY, api._match_memory_key("Молоко коровье 3,2% 1л"),
                           "p-moloko32", "Молоко 3,2", name_raw="Молоко коровье 3,2% 1л")
    rows = [r for r in list_invoice_match_memory(KEY) if "Молоко коровье" in (r["name_raw"] or "")]
    assert len(rows) == 1, rows
    assert rows[0]["product_id"] == "p-moloko32", rows[0]
    assert rows[0]["hits"] == 2, rows[0]
    print("  [OK] перепривязка меняет запись, а не плодит вторую (hits=2)")


def test_memory_is_scoped_to_its_iiko_server():
    """У сети свой сервер почти на каждый филиал: id товара с чужого сервера
    здесь невалиден, память не должна течь между ними."""
    remember_invoice_match(OTHER, api._match_memory_key("Соус унаги"),
                           "p-other", "Соус Унаги", name_raw="Соус унаги")
    assert api._match_memory_key("Соус унаги") not in get_invoice_match_memory(KEY)
    assert len(list_invoice_match_memory(OTHER)) == 1
    print("  [OK] память не течёт между серверами филиалов")


def test_wrong_entry_can_be_forgotten():
    remember_invoice_match(KEY, "мусор", "p-x", "Ошибка", name_raw="мусор")
    assert forget_invoice_match(KEY, "мусор") is True
    assert "мусор" not in get_invoice_match_memory(KEY)
    assert forget_invoice_match(KEY, "мусор") is False, "повторное удаление — не ошибка"
    print("  [OK] ошибочную запись можно удалить, повтор безопасен")


def test_line_that_normalizes_to_nothing_cannot_be_remembered():
    """Честная граница механизма. «М/КОР 3,2% 1Л» сворачивается в ПУСТОЙ ключ:
    «м» короче двух букв, а «кор» — стоп-слово (коробка). Такую строку нельзя
    ни сопоставить, ни запомнить — её оператор выбирает руками каждый раз."""
    assert api._match_memory_key("М/КОР 3,2% 1Л") == ""
    before = len(list_invoice_match_memory(KEY))
    remember_invoice_match(KEY, api._match_memory_key("М/КОР 3,2% 1Л"), "p-x", "Молоко")
    assert len(list_invoice_match_memory(KEY)) == before
    print("  [OK] строка с пустым ключом не запоминается (и не сопоставляется)")


def test_incomplete_rows_are_ignored():
    before = len(list_invoice_match_memory(KEY))
    remember_invoice_match(KEY, "", "p-1", "Товар")
    remember_invoice_match(KEY, "имя", "", "Товар")
    remember_invoice_match("", "имя", "p-1", "Товар")
    assert len(list_invoice_match_memory(KEY)) == before
    print("  [OK] запись без ключа или без товара не сохраняется")


if __name__ == "__main__":
    failed = 0
    for t in (test_operator_choice_is_remembered_at_once,
              test_next_invoice_matches_by_memory,
              test_raw_line_is_kept_for_the_tab,
              test_rebinding_overwrites_and_counts_hits,
              test_memory_is_scoped_to_its_iiko_server,
              test_wrong_entry_can_be_forgotten,
              test_line_that_normalizes_to_nothing_cannot_be_remembered,
              test_incomplete_rows_are_ignored):
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
