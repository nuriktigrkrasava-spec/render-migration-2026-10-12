"""Поставщик определяется с фото накладной.

Запуск:  python3 test_supplier_match.py

Зачем: имя поставщика Claude с фото читает, но оно оставалось строкой — в
выпадающем списке из 292 позиций оператор искал его руками. Подбираем id на
бэкенде и отдаём готовым.

Справочник поставщиков живой и грязный: ведущие пробелы и кавычки (« Ер-Амаль»),
дубли латиницей и кириллицей («AFOOD-Афуд» / «Afood-Афуд»), казахские буквы
(«Айғаным» рядом с «Айганым»), приписки («Айганым поставщик»), склады филиалов.

Главное правило: НЕ УГАДЫВАТЬ. Накладная, проведённая на чужого поставщика, —
это ошибка в учёте, которую потом ищут вручную. Не уверены — оставляем выбор
оператору.
"""
import os
import tempfile

os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(prefix="sup-"), "t.db"))
os.environ.pop("TELEGRAM_BOT_TOKEN", None)

import api  # noqa: E402

# Срез настоящего справочника филиала «Астана, Г.Алиева 10» (292 поставщика)
SUPPLIERS = [
    {"id": "s1", "name": '" Ер-Амаль"'},
    {"id": "s2", "name": "AFOOD-Афуд"},
    {"id": "s3", "name": "Afood-Афуд"},
    {"id": "s4", "name": "Айганым"},
    {"id": "s5", "name": "Айғаным поставщик"},
    {"id": "s6", "name": "Альфа"},
    {"id": "s7", "name": "Yans"},
    {"id": "s8", "name": "7 морей"},
    {"id": "s9", "name": "Global Cuisine"},
    {"id": "s10", "name": "Актау, 12 микрорайон 30а: Актау Склад"},
    {"id": "s11", "name": "ICE FOOD"},
]


def pick(name):
    s, score = api._best_supplier_match(SUPPLIERS, name)
    return (s["name"] if s else None), round(score, 2)


def test_legal_form_and_quotes_do_not_get_in_the_way():
    """Как поставщик подписывается в накладной против того, как заведён в iiko."""
    assert pick('ТОО "Ер-Амаль"')[0] == '" Ер-Амаль"', pick('ТОО "Ер-Амаль"')
    assert pick("ТОО Ер Амаль")[0] == '" Ер-Амаль"', pick("ТОО Ер Амаль")
    assert pick("Альфа ТОО")[0] == "Альфа", pick("Альфа ТОО")
    print("  [OK] ТОО/кавычки/дефис не мешают: «ТОО \"Ер-Амаль\"» → « Ер-Амаль»")


def test_kazakh_letters_are_folded():
    """«Айғаным» и «Айганым» — один поставщик, различие только в ғ."""
    got = pick("ИП Айғаным")[0]
    assert got in ("Айганым", "Айғаным поставщик"), got
    print(f"  [OK] «ИП Айғаным» → «{got}»")


def test_latin_name_matches():
    assert pick("AFOOD")[0] in ("AFOOD-Афуд", "Afood-Афуд"), pick("AFOOD")
    assert pick("ICE FOOD ТОО")[0] == "ICE FOOD", pick("ICE FOOD ТОО")
    print("  [OK] латиница подбирается: AFOOD, ICE FOOD")


def test_digits_in_the_name_survive():
    """«7 морей» — цифра часть имени, обычный отброс чисел его бы обезглавил."""
    assert pick("ТОО 7 морей")[0] == "7 морей", pick("ТОО 7 морей")
    print("  [OK] «ТОО 7 морей» → «7 морей», цифра из имени не выброшена")


def test_unknown_supplier_is_not_guessed():
    """Ради чего всё правило: чужого в справочнике нет — не подставляем никого."""
    assert pick("ТОО Ромашка-Караганда")[0] is None, pick("ТОО Ромашка-Караганда")
    assert pick("")[0] is None
    assert pick(None)[0] is None
    print("  [OK] незнакомый поставщик не подставляется — выбирает оператор")


def test_legal_form_alone_matches_nobody():
    """Из накладной пришло только «ТОО» — это не имя, подставлять нечего."""
    assert pick("ТОО")[0] is None, pick("ТОО")
    assert pick("ИП")[0] is None
    print("  [OK] одно «ТОО» никого не выбирает")


def test_branch_warehouse_is_not_a_supplier_match():
    """Склады филиалов лежат в том же справочнике — по имени поставщика они
    не должны всплывать вместо него."""
    got = pick("ТОО Ер-Амаль")[0]
    assert "Склад" not in (got or ""), got
    print("  [OK] склад филиала не подменяет поставщика")


if __name__ == "__main__":
    failed = 0
    for t in (test_legal_form_and_quotes_do_not_get_in_the_way,
              test_kazakh_letters_are_folded,
              test_latin_name_matches,
              test_digits_in_the_name_survive,
              test_unknown_supplier_is_not_guessed,
              test_legal_form_alone_matches_nobody,
              test_branch_warehouse_is_not_a_supplier_match):
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
