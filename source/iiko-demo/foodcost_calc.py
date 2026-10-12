"""Чистый расчёт теоретического фудкоста из техкарт и цен. Без зависимостей от приложения."""


def compute_recipe_foodcost(sales: list, recipes: dict, prices: dict) -> dict:
    """Теоретический фудкост из техкарт и цен. Честно: в итог идут ТОЛЬКО полностью
    оценённые блюда; частично оценённые (не все ингредиенты имеют цену) не искажают
    цифру — у них cost_pct=None, они лишь снижают coverage_pct.
    base_rev — выручка полностью оценённых блюд (база, к которой относится fact_pct).
    """
    total_sold_rev = 0.0
    covered_rev = 0.0
    fact_sum = 0.0
    out_dishes = []
    for s in sales:
        rev = float(s.get("revenue", 0) or 0)
        cnt = float(s.get("count", 0) or 0)
        total_sold_rev += rev
        name = s.get("name", "")
        cat = s.get("category", "")
        recipe = recipes.get(s.get("productId"))
        if not recipe:
            out_dishes.append({"name": name, "category": cat, "revenue": rev,
                               "count": int(cnt), "cost_pct": None})
            continue
        cost_per_portion = 0.0
        all_priced = True
        for ing in recipe:
            price = prices.get(ing.get("productId"))
            if price is None:
                all_priced = False
                continue
            cost_per_portion += float(ing.get("amount", 0) or 0) * float(price)
        if all_priced:
            dish_cost = cost_per_portion * cnt
            fact_sum += dish_cost
            covered_rev += rev
            cost_pct = round(dish_cost / rev * 100, 1) if rev else None
        else:
            cost_pct = None  # частично оценено — не показываем цифру, только снижаем покрытие
        out_dishes.append({"name": name, "category": cat, "revenue": rev,
                           "count": int(cnt), "cost_pct": cost_pct})
    fact_pct = round(fact_sum / covered_rev * 100, 1) if covered_rev else None
    coverage_pct = round(covered_rev / total_sold_rev * 100, 1) if total_sold_rev else None
    out_dishes.sort(key=lambda d: (d["cost_pct"] if d["cost_pct"] is not None else -1), reverse=True)
    return {"fact_sum": fact_sum, "base_rev": covered_rev, "fact_pct": fact_pct,
            "coverage_pct": coverage_pct, "dishes": out_dishes}
