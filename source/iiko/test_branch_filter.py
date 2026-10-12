"""Фильтр филиалов по названию не имеет права оставить человека без всего.

Запуск:  python3 test_branch_filter.py
"""
import os
import tempfile

os.environ.setdefault("DB_PATH", tempfile.mktemp(suffix=".db"))
import mountain_routes as mr  # noqa: E402


def test_a_network_outside_astana_is_not_wiped_out():
    """MOUNTAIN_ONLY=астана — настройка ПОД ОДНУ сеть: у неё двадцать три точки,
    и служебные «КЦ»/«ЦЕХ» из списка надо убирать. Но тот же фильтр молча
    выбрасывал ВСЕ филиалы любого, кто зарегистрировался не в Астане: сайт
    открывался пустым, и понять почему было нельзя."""
    rows = [{"organization_id": "1", "name": "Актау, 12 микрорайон 30а"},
            {"organization_id": "2", "name": "Костанай, ул. Абая 153"}]
    got = mr.visible_branches(rows, lambda r: r["name"])
    assert len(got) == 2, got
    print("  [OK] сеть вне Астаны не обнуляется фильтром")


def test_a_single_restaurant_with_any_name_survives():
    """Самый частый случай новой регистрации — один ресторан с любым названием."""
    rows = [{"organization_id": "1", "name": "Пиццерия у дома"}]
    assert len(mr.visible_branches(rows, lambda r: r["name"])) == 1
    print("  [OK] одиночный ресторан с произвольным названием виден")


def test_service_units_are_still_dropped_when_there_is_what_to_keep():
    """Ради чего фильтр и заводился: у сети, где есть настоящие точки, КЦ и ЦЕХ
    не должны раздувать список. Это поведение обязано сохраниться."""
    rows = [{"organization_id": "1", "name": "Астана, ул. Бокейхана 25а"},
            {"organization_id": "2", "name": "Астана, ул. Алпамыс 61 (КЦ)"},
            {"organization_id": "3", "name": "Астана, ул. Алпамыс 61 (ЦЕХ)"}]
    got = mr.visible_branches(rows, lambda r: r["name"])
    assert [r["organization_id"] for r in got] == ["1"], got
    print("  [OK] служебные КЦ и ЦЕХ по-прежнему отсеиваются")


def test_an_empty_list_stays_empty():
    assert mr.visible_branches([], lambda r: r["name"]) == []
    print("  [OK] пустой список остаётся пустым")


if __name__ == "__main__":
    failed = 0
    for t in (test_a_network_outside_astana_is_not_wiped_out,
              test_a_single_restaurant_with_any_name_survives,
              test_service_units_are_still_dropped_when_there_is_what_to_keep,
              test_an_empty_list_stays_empty):
        print(f"\n>>> {t.__name__}")
        try:
            t()
        except AssertionError as e:
            failed += 1; print(f"  [FAIL] {e}")
        except Exception as e:
            failed += 1; print(f"  [ERROR] {type(e).__name__}: {e}")
    print("\n" + "=" * 50)
    print("ВСЕ ТЕСТЫ ОК" if not failed else f"ПРОВАЛЕНО: {failed}")
