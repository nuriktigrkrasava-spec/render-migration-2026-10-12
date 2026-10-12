"""Вкладка «План»: недели ISO, свёртка факта по дням недели, итоги и сводка сети.

Запуск:  python3 test_plan.py
"""
import contextlib
import os
import tempfile
from datetime import date, datetime

os.environ.setdefault("DB_PATH", tempfile.mktemp(suffix=".db"))
import api  # noqa: E402
from database import init_salary_db, init_salary_overrides_db  # noqa: E402

# Таблицы ставок в проде создаёт startup приложения; в тестах его нет.
init_salary_db()
init_salary_overrides_db()


def olap(day: str, order_type: str, n: int):
    return {"OpenDate.Typed": day + " 00:00:00", "OrderType.Name": order_type, "OrdersCount": n}


@contextlib.contextmanager
def _iiko_attendance(rows, org_names: dict, roles=None, coef=None,
                     server: str = "https://s1:443"):
    """Подменяет iiko-сервер филиалов на заданные явки, должности и типы явок.

    rows=None — сервер, у которого явок нет вовсе (облачный iiko);
    rows=Exception — сервер лежит. Считает обращения, чтобы было видно, сколько
    раз мы реально сходили в iiko.
    """
    calls = {"attendance": 0, "roles": 0}

    class _Cloud:            # облачный клиент: метода get_attendance у него нет
        pass

    class _Web:
        async def get_attendance(self, d_from, d_to):
            calls["attendance"] += 1
            if isinstance(rows, Exception):
                raise rows
            return rows

        async def get_employee_roles(self):
            calls["roles"] += 1
            return roles or {}

        async def get_attendance_types(self):
            return coef or {}

    client = _Cloud() if rows is None else _Web()
    real_info, real_client = api._get_restaurant_info, api.get_client
    api._get_restaurant_info = lambda org, user: {
        "api_login": "login", "iiko_server": server, "api_password": "pwd",
        "org_name": org_names.get(org, "")}
    api.get_client = lambda login, srv=None, pwd=None: client
    try:
        yield calls
    finally:
        api._get_restaurant_info, api.get_client = real_info, real_client


def att(eid, day, start, end, dept, role=None, kind=None):
    return {"employeeId": eid, "dateFrom": f"{day}T{start}:00",
            "dateTo": f"{day}T{end}:00", "departmentName": dept,
            "roleId": role, "attendanceTypeId": kind}


def test_iso_week_matches_the_owners_numbering():
    """«Неделя 35» в таблице владельца — это ISO-неделя, а не 35-я семидневка месяца."""
    assert api._iso_week_bounds("2026-W35") == (date(2026, 8, 24), date(2026, 8, 30))
    assert api._iso_week_of(date(2026, 8, 25)) == "2026-W35"
    assert api._iso_week_of(date(2026, 8, 30)) == "2026-W35", "воскресенье — тот же ISO-номер"
    assert api._iso_week_of(date(2026, 8, 31)) == "2026-W36"
    print("  [OK] неделя 35 = 24–30.08, воскресенье не уезжает в следующую")


def test_fact_folds_into_days_and_channels():
    rows = [olap("2026-08-24", "Доставка", 11), olap("2026-08-24", "Обычный заказ", 90),
            olap("2026-08-24", "Самовывоз", 8),
            olap("2026-08-24", "Wolt", 4), olap("2026-08-24", "Yandex Eda", 2),
            olap("2026-08-30", "Доставка", 15)]
    f = api._fold_by_dow(rows)["dow"]
    assert f["1"] == {"hall": 90, "pickup": 8, "delivery": 11, "agg": 6}, f["1"]
    assert f["7"]["delivery"] == 15 and f["7"]["hall"] == 0, f["7"]
    print("  [OK] понедельник: зал 90, вынос 8, доставка 11, агрегаторы 6")


def test_pickup_is_its_own_channel():
    """Вынос отделён от зала (решение владельца 26.08.2026): это разные
    процессы и разная нагрузка на кухню, смешивать их в одной колонке нельзя."""
    f = api._fold_by_dow([olap("2026-08-25", "Самовывоз", 7)])["dow"]
    assert f["2"] == {"hall": 0, "pickup": 7, "delivery": 0, "agg": 0}, f["2"]
    print("  [OK] самовывоз — отдельный канал, не зал")


def test_site_pickup_is_pickup_not_delivery():
    """«Сайт самовывоз» содержит слово «сайт», но это вынос. Проверяем порядок
    правил: самовывоз разбирается ДО доставки, иначе «Сайт самовывоз» мог бы
    уехать в доставку вместе с «Сайт доставка»."""
    assert api._classify_channel("Сайт самовывоз") == "pickup"
    assert api._classify_channel("Сайт доставка") == "delivery"
    assert api._classify_channel("Обычный заказ") == "hall"
    assert api._classify_channel("Wolt") == "agg"
    assert api._classify_channel("Яндекс Еда") == "agg"
    print("  [OK] все семь реальных типов заказа сети разложены верно")


def test_broken_dates_do_not_crash_the_week():
    f = api._fold_by_dow([{"OpenDate.Typed": "", "OrderType.Name": "Доставка", "OrdersCount": 5},
                          {"OpenDate.Typed": "не дата", "OrderType.Name": "Доставка", "OrdersCount": 5},
                          olap("2026-08-24", "Доставка", 3)])["dow"]
    assert sum(f[str(i)]["delivery"] for i in range(1, 8)) == 3, f
    print("  [OK] битые даты выброшены, живая строка посчитана")


NOW = {"kitchen": 18, "road": 12, "late_pct": 8.0, "rating": 4.5, "orders": 900}
PREV = {"kitchen": 24, "road": 12, "late_pct": 14.0, "rating": 4.2, "orders": 800}   # +12,5%


def test_wins_name_what_actually_got_better():
    """Поздравляем за улучшение, а не за «хорошо». Время меньше — победа,
    рейтинг больше — победа, а неизменившийся показатель молчит."""
    wins = api.plan_wins(NOW, PREV)
    keys = {w["key"] for w in wins}
    assert keys == {"kitchen", "late_pct", "rating", "orders"}, keys
    assert "road" not in keys, "не изменилось — не повод поздравлять"
    k = next(w for w in wins if w["key"] == "kitchen")
    assert k["from"] == 24 and k["to"] == 18 and k["better"] == 6, k
    print("  [OK] " + " · ".join(f'{w["label"]} {w["from"]}→{w["to"]}' for w in wins))


def test_losses_name_what_got_worse_by_the_same_rule():
    """На собрании называют обе стороны. Пороги те же, что у поздравления —
    иначе один и тот же дребезг попадал бы и в улучшения, и в просадки."""
    losses = api.plan_losses(PREV, NOW)          # поменяли местами: стало хуже
    keys = {w["key"] for w in losses}
    assert keys == {"kitchen", "late_pct", "rating", "orders"}, keys
    k = next(w for w in losses if w["key"] == "kitchen")
    assert k["from"] == 18 and k["to"] == 24 and k["worse"] == 6, k
    assert api.plan_losses(NOW, PREV) == [], "улучшение просадкой не притворяется"
    print("  [OK] " + " · ".join(f'{w["label"]} {w["from"]}→{w["to"]}' for w in losses))


def test_tiny_wobble_is_not_a_loss():
    assert api.plan_losses({"kitchen": 18.4, "rating": 4.50, "late_pct": 8.0},
                           {"kitchen": 18, "rating": 4.51, "late_pct": 7.6}) == []
    print("  [OK] дрожь в пределах порога просадкой не считается")


def test_staff_lateness_is_a_week_metric():
    """Опоздания сотрудников — такой же показатель недели, как кухня и дорога:
    меньше значит лучше, и он попадает и в улучшения, и в просадки."""
    keys = [m[0] for m in api.PLAN_METRICS]
    assert "staff_late_pct" in keys, keys
    win = api.plan_wins({"staff_late_pct": 12.0}, {"staff_late_pct": 40.0})
    assert [w["key"] for w in win] == ["staff_late_pct"], win
    loss = api.plan_losses({"staff_late_pct": 40.0}, {"staff_late_pct": 12.0})
    assert [w["key"] for w in loss] == ["staff_late_pct"], loss
    assert api.plan_wins({"staff_late_pct": 12.0}, {"staff_late_pct": 14.0}) == [], "2 п.п. — шум"
    print("  [OK] опоздания сотрудников считаются наравне с кухней и дорогой")


def test_getting_worse_is_never_a_win():
    wins = api.plan_wins(PREV, NOW)      # поменяли местами: стало хуже
    assert wins == [], wins
    print("  [OK] ухудшение поздравлением не притворяется")


def test_tiny_wobble_is_not_a_win():
    """Минута туда-сюда и 0,02 рейтинга — шум. Поздравлять за шум нельзя:
    поздравления обесценятся за неделю."""
    assert api.plan_wins({"kitchen": 18, "rating": 4.51, "late_pct": 7.6},
                         {"kitchen": 18.4, "rating": 4.50, "late_pct": 8.0}) == []
    print("  [OK] дрожь в пределах порога поздравлением не считается")


def test_seasonal_order_growth_does_not_congratulate_everyone():
    """Живой случай 25.08: порог заказов был в ШТУКАХ, и «плюс три заказа»
    прошло у всех девяти филиалов разом — открытку получила вся сеть просто
    за сезон. Порог сделан относительным."""
    assert api.plan_wins({"orders": 702}, {"orders": 671}) == [], "рост 4,6% — это сезон"
    win = api.plan_wins({"orders": 436}, {"orders": 355})
    assert [w["key"] for w in win] == ["orders"] and win[0]["pct"] == 23, win
    print("  [OK] +4,6% заказов молчит, +23% поздравляется")


def test_missing_metric_is_skipped_not_crashed():
    wins = api.plan_wins({"kitchen": 10}, {"kitchen": 20, "rating": 4.0})
    assert [w["key"] for w in wins] == ["kitchen"], wins
    assert api.plan_wins({}, {}) == []
    print("  [OK] отсутствующий показатель пропускается, а не роняет карточку")


def test_partial_week_compares_only_elapsed_days():
    """Живой случай 25.08: владелец проставил план на всю сеть на ТЕКУЩУЮ неделю,
    а критерий смотрел на завершённую — и «выполнение плана» молчало у восьми
    филиалов. Незавершённую неделю считаем пропорционально: план Пн–Ср против
    факта Пн–Ср, иначе филиал получает ноль просто за то, что сегодня среда."""
    plans = {d: {"hall": 100, "delivery": 10, "agg": 5} for d in range(1, 8)}
    assert api._plan_days_and_total(plans, 7) == 805, "неделя целиком"
    assert api._plan_days_and_total(plans, 3) == 345, "только Пн–Ср"
    assert api._plan_days_and_total(plans, 0) == 0
    print("  [OK] план недели 805, план трёх дней 345 — сравнивать есть с чем")


def test_partial_plan_ignores_days_that_have_not_happened():
    """Ключевое: дни, которых ещё не было, в знаменатель не идут."""
    plans = {1: {"hall": 90}, 2: {"hall": 90}, 6: {"hall": 200}, 7: {"hall": 200}}
    assert api._plan_days_and_total(plans, 2) == 180, "выходные не должны попасть"
    assert api._plan_days_and_total(plans, 7) == 580
    print("  [OK] пятничный и субботний план не тянет вниз оценку в понедельник")


def _grid(v):
    return {str(i): {t: v for t in api.PLAN_TYPES} for i in range(1, 8)}


def test_totals_and_network_row():
    rows = [{"organization_id": "a", "name": "Алиева", "plan": _grid(10), "fact": _grid(8), "base": _grid(9)},
            {"organization_id": "b", "name": "Достык", "plan": _grid(5), "fact": _grid(7), "base": _grid(6)}]
    r = api._plan_response("2026-W35", date(2026, 8, 24), date(2026, 8, 30), rows, demo=False)
    assert r["branches"][0]["total_plan"]["hall"] == 70, r["branches"][0]["total_plan"]
    assert r["branches"][0]["total_fact"]["hall"] == 56
    net = r["network"]
    assert net["name"] == "Итого сеть"
    assert net["plan"]["1"]["hall"] == 15 and net["total_fact"]["hall"] == 105, net
    assert net["fot"] == {"amount": 0, "priced": 0, "total": 0, "unit": None,
                          "complete": False, "gaps": []}, net["fot"]
    print("  [OK] итог филиала 70 план / 56 факт, сеть по понедельнику 15")


def test_network_row_sums_the_same_missing_role_across_branches():
    """Один и тот же Кассир не заведён у всех филиалов. Владельцу нужна одна
    строка «вот кого заполнить», а не девять одинаковых."""
    fot = lambda h: {"amount": 0, "priced": 0, "total": h, "unit": "hour",
                     "complete": False, "gaps": [{"role": "Кассир", "hours": h}]}
    rows = [{"organization_id": "a", "name": "Алиева", "plan": _grid(1), "fact": _grid(1),
             "base": _grid(1), "fot": fot(93)},
            {"organization_id": "b", "name": "Достык", "plan": _grid(1), "fact": _grid(1),
             "base": _grid(1), "fot": fot(70)}]
    r = api._plan_response("2026-W38", date(2026, 9, 14), date(2026, 9, 20), rows, demo=False)
    assert r["network"]["fot"]["gaps"] == [{"role": "Кассир", "hours": 163}], r["network"]["fot"]
    print("  [OK] Кассир по сети одной строкой: 163 ч без ставки")


def test_network_row_is_not_double_counted_into_branches():
    """Строка «Итого сеть» отдельная — в списке филиалов её быть не должно,
    иначе сумма сети удвоится при следующем сложении."""
    rows = [{"organization_id": "a", "name": "Алиева", "plan": _grid(10), "fact": _grid(8), "base": _grid(9)}]
    r = api._plan_response("2026-W35", date(2026, 8, 24), date(2026, 8, 30), rows, demo=False)
    assert [b["name"] for b in r["branches"]] == ["Алиева"], r["branches"]
    print("  [OK] «Итого сеть» отдельным полем, среди филиалов её нет")


def test_empty_week_has_full_grid():
    """Пустая неделя всё равно отдаёт 7 дней × 3 канала: таблица не должна
    рассыпаться на филиале, который ещё ничего не продал."""
    f = api._fold_by_dow([])["dow"]
    assert set(f) == {str(i) for i in range(1, 8)}
    assert all(set(f[d]) == set(api.PLAN_TYPES) for d in f), f
    print("  [OK] пустая неделя — полная сетка из нулей")


def test_day_plan_survives_a_round_trip():
    from database import set_day_plans, get_day_plans
    set_day_plans([{"org": "o1", "week": "2026-W35", "dow": 1, "ch_type": "hall", "planned": 90},
                   {"org": "o1", "week": "2026-W35", "dow": 5, "ch_type": "delivery", "planned": 22}])
    set_day_plans([{"org": "o1", "week": "2026-W35", "dow": 1, "ch_type": "hall", "planned": 97}])
    got = get_day_plans(["o1"], "2026-W35")
    assert got["o1"][1]["hall"] == 97, got      # перезапись, а не дубль
    assert got["o1"][5]["delivery"] == 22, got
    assert get_day_plans(["o1"], "2026-W36") == {}, "чужая неделя не подмешивается"
    print("  [OK] план сохраняется, перезаписывается и не течёт между неделями")


def test_done_days_counts_only_closed_days():
    """Сравнение с прошлой неделей идёт день в день, поэтому число закрытых
    дней считает сервер. Во вторник закрыт ровно один день — понедельник;
    сегодняшний ещё идёт и в сравнение не входит."""
    import api as A
    real_now = A.local_now

    def at(d: date):
        A.local_now = lambda: datetime(d.year, d.month, d.day, 14, 0)
        rows = [{"organization_id": "a", "name": "Алиева", "plan": _grid(10),
                 "fact": _grid(8), "base": _grid(9), "prev": _grid(7)}]
        return A._plan_response("2026-W35", date(2026, 8, 24), date(2026, 8, 30),
                                rows, demo=False)
    try:
        assert at(date(2026, 8, 24))["done_days"] == 0, "понедельник: закрытых дней нет"
        assert at(date(2026, 8, 25))["done_days"] == 1, "вторник: закрыт понедельник"
        assert at(date(2026, 8, 30))["done_days"] == 6, "воскресенье: шесть закрытых"
        past = at(date(2026, 9, 2))
        assert past["done_days"] == 7 and past["running"] is False, "прошедшая неделя — вся"
        future = at(date(2026, 8, 17))
        assert future["done_days"] == 0 and future["running"] is False
    finally:
        A.local_now = real_now
    print("  [OK] вторник = 1 закрытый день, закрытая неделя = 7, будущая = 0")


def test_previous_week_is_taken_from_the_base_window():
    """Прошлая неделя приходит рядом с фактом — иначе динамику не показать.
    Берётся из уже загруженного окна базы, отдельного запроса в iiko нет."""
    rows = [{"organization_id": "a", "name": "Алиева", "plan": _grid(10),
             "fact": _grid(8), "base": _grid(9), "prev": _grid(6)}]
    r = api._plan_response("2026-W35", date(2026, 8, 24), date(2026, 8, 30), rows, demo=False)
    assert r["branches"][0]["total_prev"]["hall"] == 42, r["branches"][0]["total_prev"]
    assert r["network"]["total_prev"]["hall"] == 42, r["network"]["total_prev"]
    print("  [OK] прошлая неделя в итогах филиала и сети: 42")


def test_row_without_previous_week_does_not_crash():
    """Филиал, по которому iiko промолчал, не должен ронять всю вкладку."""
    rows = [{"organization_id": "a", "name": "Алиева", "plan": _grid(10),
             "fact": _grid(8), "base": _grid(9)}]          # prev отсутствует
    r = api._plan_response("2026-W35", date(2026, 8, 24), date(2026, 8, 30), rows, demo=False)
    assert r["branches"][0]["total_prev"] == {t: 0 for t in api.PLAN_TYPES}
    print("  [OK] без прошлой недели итог нулевой, а не исключение")


def _cache_env(today: date, olap_rows, calls):
    """Подменяет часы и OLAP: тесты кэша не должны ходить в iiko."""
    import api as A

    async def fake_orders(real_org, d_from, d_to, user):
        calls.append((d_from.isoformat(), d_to.isoformat()))
        return [r for r in olap_rows
                if d_from.isoformat() <= r["OpenDate.Typed"][:10] <= d_to.isoformat()]

    A.local_now = lambda: datetime(today.year, today.month, today.day, 14, 0)
    A._orders_by_day = fake_orders


def test_closed_days_are_read_from_the_snapshot_not_from_iiko():
    """Второе открытие вкладки не должно стрелять в iiko: закрытый день уже
    лежит в базе. Ради этого весь кэш и заводился."""
    import api as A
    import asyncio as aio
    real_now, real_orders = A.local_now, A._orders_by_day
    calls = []
    rows = [olap("2026-08-24", "Обычный заказ", 90), olap("2026-08-25", "Доставка", 11)]
    try:
        _cache_env(date(2026, 8, 26), rows, calls)
        first = aio.run(A._orders_cached("org-cache-1", date(2026, 8, 24), date(2026, 8, 30), {"id": 1}))
        assert first["2026-08-24"]["hall"] == 90, first
        assert len(calls) == 1, f"первый заход — один запрос, было {calls}"
        second = aio.run(A._orders_cached("org-cache-1", date(2026, 8, 24), date(2026, 8, 30), {"id": 1}))
        assert second == first, (first, second)
        assert len(calls) == 1, f"второй заход не должен ходить в iiko, было {calls}"
    finally:
        A.local_now, A._orders_by_day = real_now, real_orders
    print("  [OK] второе открытие вкладки читает снимок, в iiko не идёт")


def test_today_is_never_returned_or_cached():
    """Сегодняшний день ещё идёт: положить его в снимок — значит навсегда
    зафиксировать половину дня и сравнивать её с целым днём прошлой недели."""
    import api as A
    import asyncio as aio
    real_now, real_orders = A.local_now, A._orders_by_day
    calls = []
    rows = [olap("2026-08-25", "Доставка", 11), olap("2026-08-26", "Доставка", 4)]
    try:
        _cache_env(date(2026, 8, 26), rows, calls)
        got = aio.run(A._orders_cached("org-cache-2", date(2026, 8, 24), date(2026, 8, 30), {"id": 1}))
        assert "2026-08-26" not in got, got
        assert got["2026-08-25"]["delivery"] == 11, got
        assert calls and calls[0][1] == "2026-08-25", f"в iiko не просим сегодняшний день: {calls}"
    finally:
        A.local_now, A._orders_by_day = real_now, real_orders
    print("  [OK] сегодняшний день не отдаётся и не кэшируется")


def test_day_without_orders_is_not_refetched_forever():
    """День, в который заказов не было, тоже закрыт. Не запиши мы нули —
    он считался бы «отсутствующим» и добирался при каждом открытии."""
    import api as A
    import asyncio as aio
    real_now, real_orders = A.local_now, A._orders_by_day
    calls = []
    try:
        _cache_env(date(2026, 8, 26), [], calls)          # iiko отдаёт пусто
        aio.run(A._orders_cached("org-cache-3", date(2026, 8, 24), date(2026, 8, 25), {"id": 1}))
        aio.run(A._orders_cached("org-cache-3", date(2026, 8, 24), date(2026, 8, 25), {"id": 1}))
        assert len(calls) == 1, f"нулевой день должен закрыться с первого раза, было {calls}"
    finally:
        A.local_now, A._orders_by_day = real_now, real_orders
    print("  [OK] день без заказов закрывается нулями, повторно не тянется")


def test_forced_refresh_corrects_a_closed_day():
    """Возврат или поздно закрытый заказ меняет уже закрытый день. Ночной
    проход обязан заменить снимок, а не решить, что существующая строка вечная."""
    import api as A
    import asyncio as aio
    from database import get_plan_day_facts, save_plan_day_facts
    real_orders = A._orders_by_day
    day = date(2026, 9, 14)
    save_plan_day_facts("org-refresh", {day.isoformat(): {
        "hall": 323, "pickup": 7, "delivery": 19, "agg": 10}})

    async def fresh(_org, _a, _z, _user):
        return [olap(day.isoformat(), "Обычный заказ", 325),
                olap(day.isoformat(), "Самовывоз", 7),
                olap(day.isoformat(), "Доставка", 19),
                olap(day.isoformat(), "Агрегаторы", 10)]
    try:
        A._orders_by_day = fresh
        aio.run(A._refresh_plan_facts("org-refresh", day, day, {"id": 1}))
        got = get_plan_day_facts("org-refresh", day.isoformat(), day.isoformat())
        assert got[day.isoformat()]["hall"] == 325, got
    finally:
        A._orders_by_day = real_orders
    print("  [OK] ночная сверка исправляет закрытый день 323→325")


def test_branch_fot_reports_amount_and_missing_shift_pay():
    from database import set_staff_shift, branch_fot_summary
    set_staff_shift("org-fot", "e1", "2026-09-14", "09:00", "18:00", 15000)
    set_staff_shift("org-fot", "e2", "2026-09-14", "10:00", "19:00", None)
    got = branch_fot_summary("org-fot", "2026-09-14", "2026-09-20")
    assert got == {"amount": 15000, "shifts_priced": 1,
                   "shifts_total": 2, "complete": False}, got
    print("  [OK] ФОТ 15 000 и явно показывает 1 незаполненную смену")


ROLES = {"r-cashier": {"name": "Кассир", "payment_per_hour": 0.0},
         "r-sushi": {"name": "Сушист", "payment_per_hour": 900.0},
         "r-chef": {"name": "Су-шеф", "payment_per_hour": 1230.77}}
COEF = {"t-day": 1.0, "t-night": 1.2, "t-holiday": 2.0}


def _fot(rows, org="org-a", name="Алиева", roles=None, coef=None, calls=None):
    import asyncio as aio
    with _iiko_attendance(rows, {org: name}, roles or ROLES, coef or COEF) as c:
        got = aio.run(api._fot_by_branch([{"organization_id": org, "name": name}],
                                         date(2026, 9, 14), date(2026, 9, 20), {"id": 1}))
    if calls is not None:
        calls.update(c)
    return got[org]


def test_fot_comes_from_the_role_rate_in_iiko():
    """Ставка в iiko лежит на ДОЛЖНОСТИ (`/api/employees/roles` →
    paymentPerHour), а не на человеке, и денег в самой явке нет. Именно поэтому
    виджет, читавший только ручную оплату смены, показывал 0."""
    got = _fot([att("e1", "2026-09-14", "09:00", "19:00", "Алиева", "r-sushi", "t-day"),
                att("e2", "2026-09-15", "10:00", "18:00", "Алиева", "r-chef", "t-day"),
                att("e3", "2026-09-16", "09:00", "14:00", "Достык", "r-sushi", "t-day")])
    # 10 ч × 900 + 8 ч × 1230.77 = 18 846. Явка чужого филиала не считается.
    assert got["amount"] == 18846, got
    assert got["unit"] == "hour" and got["total"] == 18, got
    assert got["complete"] is True, got
    print("  [OK] ФОТ 18 846 ₸ по ставкам должностей, чужой филиал не приплюсован")


def test_night_and_holiday_raise_the_rate_as_iiko_says():
    """Тип явки — это множитель к ставке: у сети ночной 1.2, праздничный 2.0.
    Без него ночные смены считались бы по дневной ставке и ФОТ был бы занижен."""
    got = _fot([att("e1", "2026-09-14", "20:00", "23:00", "Алиева", "r-sushi", "t-night"),
                att("e2", "2026-09-15", "10:00", "13:00", "Алиева", "r-sushi", "t-holiday")])
    # 3 ч × 900 × 1.2 + 3 ч × 900 × 2.0 = 3 240 + 5 400
    assert got["amount"] == 8640, got
    print("  [OK] ночная ×1.2 и праздничная ×2.0 учтены")


def test_role_without_a_rate_is_a_hole_not_a_zero():
    """У сети ставка заведена у 7 должностей из 21: Кассир, Курьер и Тех
    персонал работают «бесплатно». Это незаполненная ставка, а не бесплатный
    труд — часы обязаны попасть в непокрытые, а сумма помечена неполной."""
    got = _fot([att("e1", "2026-09-14", "09:00", "19:00", "Алиева", "r-sushi", "t-day"),
                att("e2", "2026-09-14", "09:00", "19:00", "Алиева", "r-cashier", "t-day")])
    assert got["amount"] == 9000, got
    assert got["priced"] == 10 and got["total"] == 20, got
    assert got["complete"] is False, got
    assert got["gaps"] == [{"role": "Кассир", "hours": 10}], got["gaps"]
    print("  [OK] 10 из 20 часов со ставкой, дыра названа: Кассир")


def test_our_own_rate_beats_the_one_from_iiko():
    """Пока должность в iiko без ставки, её можно закрыть у себя — и наша
    ставка должна побеждать, иначе исправить чужой ноль было бы нечем."""
    from database import set_salary_rate
    set_salary_rate("org-a", "e2", 700)
    got = _fot([att("e2", "2026-09-14", "09:00", "19:00", "Алиева", "r-cashier", "t-day")])
    assert got["amount"] == 7000, got
    assert got["complete"] is True, got
    print("  [OK] своя ставка закрывает должность без ставки в iiko")


def test_double_attendance_does_not_double_the_hours():
    """Явки задваиваются, когда сотрудник числится в двух ролях. Пересечение
    обрезается — иначе ФОТ вырос бы вдвое на ровном месте."""
    got = _fot([att("e1", "2026-09-14", "09:00", "19:00", "Алиева", "r-sushi", "t-day"),
                att("e1", "2026-09-14", "12:00", "19:00", "Алиева", "r-sushi", "t-day")])
    assert got["total"] == 10 and got["amount"] == 9000, got
    print("  [OK] задвоенная явка даёт 10 часов, а не 17")


def test_the_overlapping_part_keeps_the_rate_of_its_own_role():
    """Обрезаем пересечение, а не выбрасываем явку целиком: у второй роли своя
    ставка, и остаток после пересечения обязан считаться по ней."""
    got = _fot([att("e1", "2026-09-14", "09:00", "13:00", "Алиева", "r-sushi", "t-day"),
                att("e1", "2026-09-14", "11:00", "15:00", "Алиева", "r-chef", "t-day")])
    # 4 ч сушистом (900) + оставшиеся 2 ч су-шефом (1230.77) = 3 600 + 2 462
    assert got["total"] == 6 and got["amount"] == 6062, got
    print("  [OK] хвост после пересечения посчитан по своей должности")


def test_fot_asks_each_iiko_server_once_not_once_per_branch():
    """Девять филиалов сети живут на одном сервере, а /employees/attendance
    отдаёт явки всех департаментов сразу. Запрос на филиал — это девять
    одинаковых ответов и полминуты на открытие вкладки."""
    import asyncio as aio
    rows = [att("e1", "2026-09-14", "09:00", "19:00", "Алиева", "r-sushi", "t-day"),
            att("e1", "2026-09-14", "09:00", "19:00", "Достык", "r-sushi", "t-day")]
    with _iiko_attendance(rows, {"org-a": "Алиева", "org-b": "Достык"}, ROLES, COEF) as calls:
        aio.run(api._fot_by_branch([{"organization_id": "org-a", "name": "Алиева"},
                                    {"organization_id": "org-b", "name": "Достык"}],
                                   date(2026, 9, 14), date(2026, 9, 20), {"id": 1}))
    assert calls["attendance"] == 1 and calls["roles"] == 1, calls
    print("  [OK] один запрос явок и должностей на сервер, а не на каждый филиал")


def test_fot_falls_back_to_the_schedule_when_iiko_has_no_attendance():
    """Облачный iiko явок не отдаёт вовсе. Там ФОТ остаётся тем, что проставили
    руками в графике смен, — но нулём мы это подменять не имеем права."""
    from database import set_staff_shift
    set_staff_shift("org-cloud", "e9", "2026-09-14", "09:00", "18:00", 12000)
    got = _fot(None, org="org-cloud", name="Облако")
    assert got == {"amount": 12000, "priced": 1, "total": 1,
                   "unit": "shift", "complete": True}, got
    print("  [OK] без явок ФОТ берётся из графика смен, а не обнуляется")


def test_fot_without_any_source_is_not_reported_as_zero():
    """Ни явок, ни графика — «нет данных». Ноль на собрании читается как
    «людям не платили», и это прямое враньё."""
    got = _fot(None, org="org-empty", name="Пусто")
    assert got["unit"] is None and got["total"] == 0, got
    print("  [OK] без источника ФОТ помечен «нет данных», а не нулём")


def test_fot_of_a_broken_iiko_does_not_break_the_week():
    """iiko-сервер филиала может лежать. Вкладка «Планы» — это план и факт
    заказов; ронять её из-за явок нельзя."""
    from database import set_staff_shift
    set_staff_shift("org-down", "e9", "2026-09-14", "09:00", "18:00", 5000)
    got = _fot(RuntimeError("iiko лежит"), org="org-down", name="Лежит")
    assert got["amount"] == 5000 and got["unit"] == "shift", got
    print("  [OK] упавший iiko не роняет вкладку, ФОТ падает на график")


def test_named_gaps_account_for_every_unpriced_hour():
    """«2158 из 6181 ч со ставкой» значит 4023 непокрытых часа. Если список
    должностей под этой строкой их не объясняет, числа на собрании не сходятся.
    Отдаём ВСЕ дыры; сколько показать — решает экран, но сумма обязана биться."""
    # Идентификаторы свои: в соседних тестах у e1/e2 заведены наши ставки, и на
    # общей тестовой базе они бы сюда протекли.
    rows = [att("gap-paid", "2026-09-14", "09:00", "19:00", "Алиева", "r-sushi", "t-day")]
    # пять должностей без ставки, чтобы обрезка «до трёх» была заметна
    for i, h in enumerate([("10:00", "20:00"), ("10:00", "19:00"), ("10:00", "18:00"),
                           ("10:00", "17:00"), ("10:00", "16:00")]):
        rows.append(att(f"gap-{i}", "2026-09-14", h[0], h[1], "Алиева", f"r-x{i}", "t-day"))
    roles = dict(ROLES)
    for i in range(5):
        roles[f"r-x{i}"] = {"name": f"Должность {i}", "payment_per_hour": 0.0}
    got = _fot(rows, roles=roles)
    assert got["total"] - got["priced"] == sum(g["hours"] for g in got["gaps"]), got
    assert len(got["gaps"]) == 5, got["gaps"]
    print("  [OK] все непокрытые часы объяснены должностями, ничего не срезано")


def test_rounding_of_gaps_still_adds_up_to_the_whole():
    """Округли каждую дыру саму по себе — и список перестанет сходиться с
    «столько-то из стольких часов». Части обязаны складываться в целое."""
    # три части по 1/3 часа: каждая округлилась бы в 0, а в сумме это 1 час
    got = api._round_parts({"А": 10.4, "Б": 10.3, "В": 10.3}, 31)
    assert sum(g["hours"] for g in got) == 31, got
    assert api._round_parts({}, 0) == []
    # остаток вниз тоже разносится, а не оставляет хвост
    got2 = api._round_parts({"А": 10.6, "Б": 10.6, "В": 10.6}, 31)
    assert sum(g["hours"] for g in got2) == 31, got2
    print("  [OK] округление дыр всегда складывается в целое")


def test_network_gaps_also_account_for_every_unpriced_hour():
    """То же по сети: свод дыр обязан объяснять непокрытые часы целиком."""
    def fot(gaps):
        return {"amount": 0, "priced": 0, "total": sum(g["hours"] for g in gaps),
                "unit": "hour", "complete": False, "gaps": gaps}
    rows = [{"organization_id": "a", "name": "А", "plan": _grid(1), "fact": _grid(1),
             "base": _grid(1), "fot": fot([{"role": f"Р{i}", "hours": 10 * (i + 1)}
                                           for i in range(5)])},
            {"organization_id": "b", "name": "Б", "plan": _grid(1), "fact": _grid(1),
             "base": _grid(1), "fot": fot([{"role": f"Р{i}", "hours": 5} for i in range(5)])}]
    r = api._plan_response("2026-W38", date(2026, 9, 14), date(2026, 9, 20), rows, demo=False)
    f = r["network"]["fot"]
    assert f["total"] - f["priced"] == sum(g["hours"] for g in f["gaps"]), f
    print("  [OK] свод дыр по сети сходится с непокрытыми часами")


def test_a_rate_of_one_branch_does_not_leak_into_another():
    """Ставки лежали в таблице БЕЗ organization_id — общие на всю базу. Владелец
    org A, узнавший employee_id из org B (это iiko-UUID), мог перебить чужую
    ставку, а у двух сетей на одном сервере ставки просто смешивались."""
    from database import get_salary_rates, set_salary_rate
    set_salary_rate("org-A", "shared-eid", 1000)
    set_salary_rate("org-B", "shared-eid", 400)
    assert get_salary_rates("org-A")["shared-eid"] == 1000
    assert get_salary_rates("org-B")["shared-eid"] == 400
    assert "shared-eid" not in get_salary_rates("org-C")
    print("  [OK] ставка живёт в своём филиале и в чужой не течёт")


def test_a_rate_from_the_old_schema_is_not_counted_for_anyone():
    """Ставки старой схемы лежат без филиала. Подмешивать их всем нельзя: у двух
    сетей на одном iiko-сервере employee_id общий, и чужая ставка попала бы в
    ФОТ. Лучше не посчитать, чем посчитать по чужой цене."""
    from database import LEGACY_RATE_ORG, get_db, get_salary_rates
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO salary_rates (organization_id, employee_id, rate) "
                 "VALUES (?,?,?)", (LEGACY_RATE_ORG, "legacy-eid", 999))
    conn.commit()
    conn.close()
    assert "legacy-eid" not in get_salary_rates("org-any"), get_salary_rates("org-any")
    print("  [OK] ставка без филиала не считается ни одному филиалу")


def test_fot_uses_only_the_rates_of_its_own_branch():
    """Тот же сотрудник на двух филиалах может стоить по-разному. ФОТ обязан
    брать ставку своего филиала, а не первую попавшуюся."""
    from database import set_salary_rate
    set_salary_rate("org-r1", "e-dual", 1000)
    set_salary_rate("org-r2", "e-dual", 500)
    rows = [att("e-dual", "2026-09-14", "09:00", "19:00", "Первый", "r-cashier", "t-day"),
            att("e-dual", "2026-09-14", "09:00", "19:00", "Второй", "r-cashier", "t-day")]
    import asyncio as aio
    with _iiko_attendance(rows, {"org-r1": "Первый", "org-r2": "Второй"}, ROLES, COEF):
        got = aio.run(api._fot_by_branch(
            [{"organization_id": "org-r1", "name": "Первый"},
             {"organization_id": "org-r2", "name": "Второй"}],
            date(2026, 9, 14), date(2026, 9, 20), {"id": 1}))
    assert got["org-r1"]["amount"] == 10000, got["org-r1"]
    assert got["org-r2"]["amount"] == 5000, got["org-r2"]
    print("  [OK] каждый филиал считается по своей ставке: 10 000 и 5 000")


def test_manual_correction_covers_all_hours_of_that_employee():
    """Ручная правка суммы за период — это ИТОГ по человеку. Если он часть смен
    отработал должностью без ставки, эти часы всё равно закрыты: сумма известна.
    Иначе правка занижала бы покрытие и сама себе рисовала дыру."""
    from database import set_salary_override
    set_salary_override("e-mix", "org-ov:2026-09-14:2026-09-20", 30000)
    rows = [att("e-mix", "2026-09-14", "09:00", "14:00", "Смесь", "r-sushi", "t-day"),
            att("e-mix", "2026-09-15", "09:00", "14:00", "Смесь", "r-cashier", "t-day")]
    import asyncio as aio
    with _iiko_attendance(rows, {"org-ov": "Смесь"}, ROLES, COEF):
        got = aio.run(api._fot_by_branch([{"organization_id": "org-ov", "name": "Смесь"}],
                                         date(2026, 9, 14), date(2026, 9, 20), {"id": 1}))
    assert got["org-ov"]["amount"] == 30000, got["org-ov"]
    assert got["org-ov"]["priced"] == 10 and got["org-ov"]["total"] == 10, got["org-ov"]
    assert got["org-ov"]["complete"] is True, got["org-ov"]
    assert got["org-ov"]["gaps"] == [], got["org-ov"]
    print("  [OK] правка за период закрывает все 10 ч, дыры не рисует")


def test_fot_window_matches_the_closed_days_of_the_orders():
    """ФОТ стоит на карточке рядом с выполнением плана и динамикой, а те
    считаются по ЗАКРЫТЫМ дням. Взять ФОТ за всю неделю — поставить рядом два
    числа за разные периоды: в среду деньги были бы за три дня, а заказы за два.
    """
    assert api._done_days(date(2026, 9, 14), date(2026, 9, 20),
                          today=date(2026, 9, 16)) == 2
    assert api._fot_window(date(2026, 9, 14), date(2026, 9, 20),
                           today=date(2026, 9, 16)) == (date(2026, 9, 14), date(2026, 9, 15))
    # закрытая неделя — целиком
    assert api._fot_window(date(2026, 9, 7), date(2026, 9, 13),
                           today=date(2026, 9, 16)) == (date(2026, 9, 7), date(2026, 9, 13))
    print("  [OK] ФОТ считается по тем же закрытым дням, что и заказы")


def test_fot_of_a_week_without_closed_days_asks_iiko_for_nothing():
    """Понедельник и будущая неделя: закрытых дней нет. Спрашивать явки не о
    чем, а сумма за «ноль дней» — это «нет данных», а не 0 ₸."""
    assert api._fot_window(date(2026, 9, 14), date(2026, 9, 20),
                           today=date(2026, 9, 14)) is None
    assert api._fot_window(date(2026, 9, 21), date(2026, 9, 27),
                           today=date(2026, 9, 16)) is None
    print("  [OK] без закрытых дней ФОТ в iiko не ходит")


def test_future_week_asks_iiko_for_nothing():
    """Неделя, которая ещё не наступила: закрытых дней нет, запросов тоже."""
    import api as A
    import asyncio as aio
    real_now, real_orders = A.local_now, A._orders_by_day
    calls = []
    try:
        _cache_env(date(2026, 8, 26), [], calls)
        got = aio.run(A._orders_cached("org-cache-4", date(2026, 9, 7), date(2026, 9, 13), {"id": 1}))
        assert got == {} and not calls, (got, calls)
    finally:
        A.local_now, A._orders_by_day = real_now, real_orders
    print("  [OK] будущая неделя: ни данных, ни запросов в iiko")


def test_unconfigured_branch_does_not_report_staff_lateness():
    """Живой случай 27.08: восемь филиалов из девяти не задали старт смены, и
    сравнение шло с дефолтом 09:00 — а смена там начинается в 10:40. Получалось
    «опоздали 100%», хотя это про ненастроенность, а не про людей. Такой
    показатель не должен попадать ни в сравнение, ни в среднее по сети."""
    import inspect
    src = inspect.getsource(api.plan_week_metrics)
    assert 'ref["configured"]' in src, "процент явок обязан зависеть от того, задан ли эталон"
    assert '"starts": ref["starts"]' in src, "экран должен видеть, с чем сравнивали"
    print("  [OK] филиал без старта смены не отдаёт долю опоздавших")


def test_cause_window_is_a_full_week_ending_on_the_meeting_day():
    """Решение владельца 27.08: неделя заканчивается во ВТОРНИК, в день собрания.

    Во вторник ISO-неделя закрыта на один день, и причина опозданий считалась бы
    по одному понедельнику. Берём семь дней до последнего закрытого дня — ту
    самую неделю, которую на собрании и подводят.
    """
    from datetime import date
    mon = date(2026, 8, 24)
    # собрание во вторник: закрыт только понедельник
    assert api.cause_window(mon, 1) == (date(2026, 8, 18), date(2026, 8, 24))
    a, z = api.cause_window(mon, 1)
    assert (z - a).days == 6, "окно обязано быть недельным, а не однодневным"
    # четверг: закрыты пн-ср, окно всё равно недельное
    assert api.cause_window(mon, 3) == (date(2026, 8, 20), date(2026, 8, 26))
    # неделя закрыта целиком — окно совпадает с ISO, второй запрос не нужен
    assert api.cause_window(mon, 7) == (mon, date(2026, 8, 30))
    # неделя не начиналась — окна нет, а не «минус шесть дней»
    assert api.cause_window(mon, 0) == (mon, mon)
    print("  [OK] причина опозданий считается за неделю, а не за закрытые дни")


def test_cause_window_is_used_by_metrics():
    """Окно должно реально применяться, а не лежать рядом с эндпоинтом."""
    import inspect
    src = inspect.getsource(api.plan_week_metrics)
    assert "cause_window(d_from, done)" in src, src[:200]
    assert "cause_from != d_from" in src, "при закрытой неделе второго запроса быть не должно"
    print("  [OK] эндпоинт считает причину по недельному окну")


if __name__ == "__main__":
    failed = 0
    for t in (test_wins_name_what_actually_got_better, test_getting_worse_is_never_a_win,
              test_tiny_wobble_is_not_a_win, test_missing_metric_is_skipped_not_crashed,
              test_seasonal_order_growth_does_not_congratulate_everyone,
              test_partial_week_compares_only_elapsed_days,
              test_partial_plan_ignores_days_that_have_not_happened,
              test_iso_week_matches_the_owners_numbering, test_fact_folds_into_days_and_channels,
              test_pickup_is_its_own_channel, test_site_pickup_is_pickup_not_delivery,
              test_broken_dates_do_not_crash_the_week,
              test_totals_and_network_row, test_network_row_sums_the_same_missing_role_across_branches,
              test_network_row_is_not_double_counted_into_branches,
              test_empty_week_has_full_grid, test_day_plan_survives_a_round_trip,
              test_done_days_counts_only_closed_days,
              test_previous_week_is_taken_from_the_base_window,
              test_row_without_previous_week_does_not_crash,
              test_closed_days_are_read_from_the_snapshot_not_from_iiko,
              test_today_is_never_returned_or_cached,
              test_day_without_orders_is_not_refetched_forever,
              test_forced_refresh_corrects_a_closed_day,
              test_branch_fot_reports_amount_and_missing_shift_pay,
              test_fot_comes_from_the_role_rate_in_iiko,
              test_night_and_holiday_raise_the_rate_as_iiko_says,
              test_role_without_a_rate_is_a_hole_not_a_zero,
              test_our_own_rate_beats_the_one_from_iiko,
              test_double_attendance_does_not_double_the_hours,
              test_the_overlapping_part_keeps_the_rate_of_its_own_role,
              test_fot_asks_each_iiko_server_once_not_once_per_branch,
              test_fot_falls_back_to_the_schedule_when_iiko_has_no_attendance,
              test_fot_without_any_source_is_not_reported_as_zero,
              test_fot_of_a_broken_iiko_does_not_break_the_week,
              test_named_gaps_account_for_every_unpriced_hour,
              test_rounding_of_gaps_still_adds_up_to_the_whole,
              test_network_gaps_also_account_for_every_unpriced_hour,
              test_a_rate_of_one_branch_does_not_leak_into_another,
              test_a_rate_from_the_old_schema_is_not_counted_for_anyone,
              test_fot_uses_only_the_rates_of_its_own_branch,
              test_manual_correction_covers_all_hours_of_that_employee,
              test_fot_window_matches_the_closed_days_of_the_orders,
              test_fot_of_a_week_without_closed_days_asks_iiko_for_nothing,
              test_future_week_asks_iiko_for_nothing,
              test_losses_name_what_got_worse_by_the_same_rule, test_tiny_wobble_is_not_a_loss,
              test_staff_lateness_is_a_week_metric,
              test_unconfigured_branch_does_not_report_staff_lateness,
              test_cause_window_is_a_full_week_ending_on_the_meeting_day,
              test_cause_window_is_used_by_metrics):
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


# ── Автоплан: план ставится сам, если его не ставили ────────────────────────
# Без плана у филиала нет ни процентов выполнения, ни критерия «выполнение
# плана» в оценке. Нажать кнопку легко забыть — тогда вкладка просто мертва.

def _avg(hall=10):
    return {str(i): {"hall": hall, "pickup": 2, "delivery": 3, "agg": 1} for i in range(1, 8)}


def test_autoplan_writes_the_average_week():
    import database as D
    api._autoplan("org-auto", "2026-W40", _avg(12))
    got = D.get_day_plans(["org-auto"], "2026-W40")["org-auto"]
    assert len(got) == 7, got
    assert got[1] == {"hall": 12, "pickup": 2, "delivery": 3, "agg": 1}, got[1]
    print("  [OK] средняя неделя записана планом на все семь дней")


def test_autoplan_does_not_touch_a_hand_made_zero():
    """Ноль, поставленный руками, — решение, а не пустота: строки в базе у него
    ЕСТЬ. Затирать его автопланом нельзя — филиал сказал «на этой неделе не
    работаем», а мы поставили бы ему цель."""
    hand_zero = {i: {t: 0 for t in api.PLAN_TYPES} for i in range(1, 8)}
    assert api._should_autoplan(hand_zero, _avg(), fill_auto=True) is False
    assert api._should_autoplan({}, _avg(), fill_auto=True) is True
    print("  [OK] «ноль руками» и «не задан» — разные вещи")


def test_autoplan_is_only_for_this_week_and_later():
    """Прошлую неделю задним числом заполнять нельзя: это придумало бы филиалу
    цель, которой у него не было, и переписало бы его оценку за ту неделю."""
    assert api._should_autoplan({}, _avg(), fill_auto=False) is False
    assert api._should_autoplan({}, _avg(), fill_auto=True) is True
    print("  [OK] прошлую неделю автоплан не трогает")


def test_autoplan_skips_a_branch_without_history():
    """Филиал без истории: средняя пустая, и писать нули незачем — это тот же
    «план не задан», только с лишними строками в базе."""
    empty = {str(i): {t: 0 for t in api.PLAN_TYPES} for i in range(1, 8)}
    assert api._should_autoplan({}, empty, fill_auto=True) is False
    print("  [OK] без истории план не выдумывается")
