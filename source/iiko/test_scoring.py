"""Оценка филиала: критерии логичны, задачи на неё не влияют, пустота не рисует 100.

Запуск:  python3 test_scoring.py
"""
import scoring as S

GOOD = {
    "name": "Хороший", "revenue": 10_000_000,
    "changes": {"revenue_pct": 8, "orders_pct": 7},
    "late": {"late_pct": 2},
    "staff_late": {"total": 40, "late": 1, "late_pct": 2.5},
}
BAD = {
    "name": "Плохой", "revenue": 10_000_000,
    "changes": {"revenue_pct": -12, "orders_pct": -15},
    "late": {"late_pct": 20},
    "staff_late": {"total": 40, "late": 12, "late_pct": 30},
}
CH_OK = {"done": 24, "missed": 0}
CH_BAD = {"done": 6, "missed": -18}


def test_good_beats_bad():
    good = S.score_branch(GOOD, CH_OK)["score"]
    bad = S.score_branch(BAD, CH_BAD)["score"]
    # Потолок «разрухи» поднят с 15 до 20: опоздания сотрудников больше не
    # обнуляются (решение владельца 27.08), и даже у худшего филиала этот
    # критерий отдаёт свои 60% веса.
    assert good >= 85 and bad <= 20, (good, bad)
    print(f"  [OK] порядок в филиале {good} из 100, разруха {bad} из 100")


def test_tasks_do_not_affect_score():
    """Задачи в формулу не входят вовсе — закрыть их и «поднять оценку» нельзя."""
    keys = {c["key"] for c in S.score_branch(GOOD, CH_OK)["criteria"]}
    assert "tasks" not in keys, keys
    assert keys == {"revenue", "orders", "late", "staff_late", "checklist",
                    "rating", "plan", "shifts"}, keys
    print(f"  [OK] критериев {len(keys)}, задач среди них нет")


def test_foodcost_and_writeoffs_are_gone():
    """Убраны по решению владельца 25.08: данных по ним у филиалов не было."""
    keys = {c["key"] for c in S.score_branch(GOOD, CH_OK)["criteria"]}
    assert "foodcost" not in keys and "writeoffs" not in keys, keys
    assert "foodcost" not in S.WEIGHTS and "writeoffs" not in S.WEIGHTS, S.WEIGHTS
    print("  [OK] фудкоста и списаний в оценке больше нет")


def test_two_maxed_criteria_no_longer_give_100():
    """Та самая болезнь: выручка и списания на потолке → «100 из 100».
    Теперь двух критериев мало — вместо числа честное «мало данных»."""
    thin = {"changes": {"revenue_pct": 10}}          # только выручка + чек-листы
    r = S.score_branch(thin, CH_OK)
    assert r["score"] is None and r["counted"] == 2, r
    assert "мало данных" in r["score_note"], r
    print(f"  [OK] два критерия → оценки нет: «{r['score_note']}»")


def test_threshold_grew_with_the_number_of_criteria():
    """Критериев восемь — порог посчитанных подняли до четырёх. Три из восьми
    это снова «сто баллов по четверти данных», ради чего оценку и чинили."""
    three = S.score_branch({"changes": {"revenue_pct": 10, "orders_pct": 10}}, CH_OK)
    assert three["score"] is None and three["counted"] == 3, three
    four = S.score_branch({"changes": {"revenue_pct": 10, "orders_pct": 10},
                           "shifts": {"pending": 0}}, CH_OK)
    assert four["score"] == 100 and four["counted"] == 4, four
    assert "4 критерия из 8" in four["score_note"], four
    print(f"  [OK] три критерия → оценки нет, четыре → {four['score']} с подписью «{four['score_note']}»")


def test_no_data_at_all_gives_none():
    r = S.score_branch({}, {})
    assert r["score"] is None and all(c["value"] is None for c in r["criteria"]), r
    print("  [OK] совсем без данных оценки нет — прочерк, а не ноль")


def test_plan_execution_is_counted_but_lightly():
    """Владелец 25.08: план в оценке нужен, но «не сильно» — вес меньше выручки
    и заказов, иначе филиал начнёт торговаться за низкий план вместо работы."""
    assert S.WEIGHTS["plan"] < S.WEIGHTS["revenue"], S.WEIGHTS
    done = S.score_branch({**GOOD, "plan": {"pct": 105}}, CH_OK)
    miss = S.score_branch({**GOOD, "plan": {"pct": 70}}, CH_OK)
    crit = {c["key"]: c for c in done["criteria"]}
    assert crit["plan"]["value"] == 100, crit["plan"]
    assert done["score"] > miss["score"], (done["score"], miss["score"])
    print(f"  [OK] план 105% → {done['score']}, план 70% → {miss['score']}, вес {S.WEIGHTS['plan']}")


def test_aggregator_rating_is_a_criterion():
    """Рейтинги 2ГИС/Wolt/Yandex сайт уже читает — теперь они в оценке.
    Шкала снята с таблицы владельца: там 4,8 отличный, 3,8 провальный."""
    hi = S.score_branch({**GOOD, "rating": {"avg": 4.8}}, CH_OK)
    lo = S.score_branch({**GOOD, "rating": {"avg": 3.8}}, CH_OK)
    crit = {c["key"]: c for c in hi["criteria"]}
    assert crit["rating"]["value"] == 100, crit["rating"]
    assert {c["key"]: c for c in lo["criteria"]}["rating"]["value"] == 0
    assert hi["score"] > lo["score"], (hi["score"], lo["score"])
    print(f"  [OK] рейтинг 4,8 → {hi['score']}, 3,8 → {lo['score']}")


def test_unaccepted_shifts_lower_the_score():
    """Непринятые кассовые смены — незакрытый учётный день, чистая дисциплина."""
    clean = S.score_branch({**GOOD, "shifts": {"pending": 0}}, CH_OK)
    dirty = S.score_branch({**GOOD, "shifts": {"pending": 6}}, CH_OK)
    crit = {c["key"]: c for c in clean["criteria"]}
    assert crit["shifts"]["value"] == 100, crit["shifts"]
    assert {c["key"]: c for c in dirty["criteria"]}["shifts"]["value"] == 0
    assert clean["score"] > dirty["score"], (clean["score"], dirty["score"])
    print(f"  [OK] смен непринятых 0 → {clean['score']}, шесть → {dirty['score']}")


def test_zero_is_not_the_same_as_no_data():
    """Ноль непринятых смен — это 100 баллов, а отсутствие данных — прочерк.
    Путать нельзя: иначе облачный филиал без касс выглядел бы образцовым."""
    crit = {c["key"]: c for c in S.score_branch({**GOOD, "shifts": {"pending": 0}}, CH_OK)["criteria"]}
    assert crit["shifts"]["counted"] and crit["shifts"]["value"] == 100
    crit2 = {c["key"]: c for c in S.score_branch(GOOD, CH_OK)["criteria"]}
    assert crit2["shifts"]["counted"] is False and crit2["shifts"]["value"] is None
    print("  [OK] ноль смен = 100 баллов, отсутствие данных = прочерк")


def test_criteria_have_weights_and_sum_to_100():
    assert sum(S.WEIGHTS.values()) == 100, S.WEIGHTS
    assert S.WEIGHTS["revenue"] == 20 and S.WEIGHTS["orders"] == 20, S.WEIGHTS
    assert S.WEIGHTS["late"] == 15 and S.WEIGHTS["staff_late"] == 12, S.WEIGHTS
    print(f"  [OK] веса критериев: {S.WEIGHTS}, сумма 100")


def test_orders_are_scored_apart_from_revenue():
    """Выручку можно вытянуть ценой. Заказы упали — оценка обязана просесть."""
    same = {**GOOD, "changes": {"revenue_pct": 8, "orders_pct": -12}}
    assert S.score_branch(same, CH_OK)["score"] < S.score_branch(GOOD, CH_OK)["score"]
    print("  [OK] рост выручки при падении заказов оценку не спасает")


def test_staff_lateness_lowers_the_score():
    """Главное требование владельца: чем больше опоздавших, тем ниже филиал."""
    ok = S.score_branch({**GOOD, "staff_late": {"total": 40, "late": 0}}, CH_OK)["score"]
    bad = S.score_branch({**GOOD, "staff_late": {"total": 40, "late": 10}}, CH_OK)["score"]
    assert ok > bad, (ok, bad)
    print(f"  [OK] опоздавших нет → {ok}, опоздал каждый четвёртый → {bad}")


def test_late_orders_lower_the_score():
    fast = S.score_branch({**GOOD, "late": {"late_pct": 0}}, CH_OK)["score"]
    slow = S.score_branch({**GOOD, "late": {"late_pct": 18}}, CH_OK)["score"]
    assert fast > slow, (fast, slow)
    print(f"  [OK] опозданий заказов 0% → {fast}, 18% → {slow}")


def test_growth_is_directional():
    up = S.score_branch({**GOOD, "changes": {"revenue_pct": 12, "orders_pct": 12}}, CH_OK)["score"]
    down = S.score_branch({**GOOD, "changes": {"revenue_pct": -12, "orders_pct": -12}}, CH_OK)["score"]
    assert up > down, (up, down)
    print(f"  [OK] рост +12% → {up}, падение −12% → {down}")


def test_staff_late_pct_can_be_derived_from_counts():
    """Если процент не посчитан, берём его из total/late — не теряем критерий."""
    crit = {c["key"]: c for c in
            S.score_branch({**GOOD, "staff_late": {"total": 10, "late": 1}}, CH_OK)["criteria"]}
    assert crit["staff_late"]["counted"] and crit["staff_late"]["value"] == 96, crit["staff_late"]
    print("  [OK] 1 опоздавший из 10 = 10% → 96 баллов критерия")


def test_staff_lateness_never_zeroes_the_criterion():
    """Решение владельца 27.08: опоздания снижают оценку, но не обнуляют критерий.
    Даже когда опоздали все, филиал не теряет его целиком."""
    crit = {c["key"]: c for c in
            S.score_branch({**GOOD, "staff_late": {"total": 10, "late": 10}}, CH_OK)["criteria"]}
    v = crit["staff_late"]["value"]
    assert v == round(S.STAFF_LATE_FLOOR * 100), v
    half = {c["key"]: c for c in
            S.score_branch({**GOOD, "staff_late": {"total": 10, "late": 5}}, CH_OK)["criteria"]}
    assert half["staff_late"]["value"] > v, (half["staff_late"]["value"], v)
    print(f"  [OK] опоздали все → {v} баллов критерия, половина → {half['staff_late']['value']}")


def test_zone_criteria_are_short_and_specific():
    for zone, d in S.ZONE_CRITERIA.items():
        assert d["label"] and 3 <= len(d["items"]) <= 6, (zone, d)
        assert all(len(i) < 60 for i in d["items"]), zone
    print("  [OK] критерии зон: " + ", ".join(
        f'{d["label"]} — {len(d["items"])}' for d in S.ZONE_CRITERIA.values()))


if __name__ == "__main__":
    failed = 0
    for t in (test_good_beats_bad, test_tasks_do_not_affect_score,
              test_foodcost_and_writeoffs_are_gone,
              test_two_maxed_criteria_no_longer_give_100, test_threshold_grew_with_the_number_of_criteria,
              test_no_data_at_all_gives_none, test_criteria_have_weights_and_sum_to_100,
              test_plan_execution_is_counted_but_lightly, test_aggregator_rating_is_a_criterion,
              test_unaccepted_shifts_lower_the_score, test_zero_is_not_the_same_as_no_data,
              test_orders_are_scored_apart_from_revenue, test_staff_lateness_lowers_the_score,
              test_late_orders_lower_the_score, test_growth_is_directional,
              test_staff_late_pct_can_be_derived_from_counts,
              test_staff_lateness_never_zeroes_the_criterion,
              test_zone_criteria_are_short_and_specific):
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
