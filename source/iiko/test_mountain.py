"""«Гора»: роли по ссылке, баллы, ступени, кто может ставить задачи.

Запуск:  python3 test_mountain.py

Ни iiko, ни Claude не дёргаем: проверяем правила доступа и арифметику баллов.
"""
import os
import tempfile

os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(prefix="mountain-"), "t.db"))
os.environ.pop("TELEGRAM_BOT_TOKEN", None)

import database as db  # noqa: E402
import mountain_routes as M  # noqa: E402
import assistant_ai as A  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import api  # noqa: E402
import scoring as S  # noqa: E402

client = TestClient(api.app)
OWNER, MANAGER = 900001, 900002
ORG_A, ORG_B = "org-a", "org-b"


def _seed():
    db.init_db()
    conn = db.get_db()
    conn.execute("DELETE FROM restaurants WHERE telegram_id IN (?,?)", (OWNER, MANAGER))
    conn.commit(); conn.close()
    db.add_restaurant(OWNER, "login", ORG_A, "Астана, Филиал А", iiko_server="", api_password="")
    db.add_restaurant(OWNER, "login", ORG_B, "Астана, Филиал Б", iiko_server="", api_password="")
    db.set_branch_manager(ORG_B, MANAGER, "Управляющий Б")


def _clear_checklists():
    """Сдачи от соседних тестов не должны влиять на арифметику ожидания."""
    conn = db.get_db()
    conn.execute("DELETE FROM checklist_uploads WHERE organization_id IN (?,?)", (ORG_A, ORG_B))
    conn.commit(); conn.close()


def test_token_is_signed():
    """Ссылку нельзя подделать, поменяв uid: подпись не сойдётся."""
    t = M.token_for(OWNER)
    assert M.uid_from_token(t) == OWNER
    fake = f"{MANAGER}." + t.split(".", 1)[1]
    assert M.uid_from_token(fake) is None, "чужой uid с чужой подписью не должен проходить"
    print("  [OK] токен подписан: подмена uid не проходит")


def test_owner_sees_all_manager_sees_one():
    _seed()
    o = client.get(f"/api/mountain/data/{M.token_for(OWNER)}").json()
    m = client.get(f"/api/mountain/data/{M.token_for(MANAGER)}").json()
    assert o["role"] == "owner" and len(o["branches"]) == 2, o
    assert m["role"] == "manager" and len(m["branches"]) == 1, m
    assert m["branches"][0]["organization_id"] == ORG_B, m
    print("  [OK] владелец видит 2 филиала, управляющий — только свой")


def test_only_owner_assigns():
    _seed()
    task = [{"organization_id": ORG_B, "title": "Разобрать списания", "effort": "средне"}]
    bad = client.post(f"/api/mountain/assign/{M.token_for(MANAGER)}", json={"tasks": task})
    assert bad.status_code == 403, (bad.status_code, bad.text[:120])
    ok = client.post(f"/api/mountain/assign/{M.token_for(OWNER)}", json={"tasks": task})
    assert ok.status_code == 200 and ok.json()["created"] == 1, ok.text[:150]
    print("  [OK] задачу ставит только владелец, управляющему — 403")


def test_points_and_step():
    """Три задачи не поднимают на вершину: 40 ступеней по 25 баллов."""
    _seed()
    tasks = [{"organization_id": ORG_A, "title": f"Задача {i}", "effort": "долго"} for i in range(3)]
    client.post(f"/api/mountain/assign/{M.token_for(OWNER)}", json={"tasks": tasks})
    board = client.get(f"/api/mountain/data/{M.token_for(OWNER)}").json()
    a = next(b for b in board["branches"] if b["organization_id"] == ORG_A)
    for t in a["tasks_open"]:
        client.post(f"/api/mountain/close/{M.token_for(OWNER)}", json={"task_id": t["id"]})
    board = client.get(f"/api/mountain/data/{M.token_for(OWNER)}").json()
    a = next(b for b in board["branches"] if b["organization_id"] == ORG_A)
    assert a["points"] == 150, a           # 3 × «долго» = 150
    assert a["level"]["step"] == 15, a     # 150 / 10
    assert a["level"]["steps"] == 40, a
    assert a["level"]["summit"] == 400, a  # вершина достижима за месяц хорошей работы
    # 150 баллов — это ещё «Тропа»: «Первый лагерь» начинается со 160
    assert a["level"]["stage"]["name"] == "Тропа", a["level"]["stage"]
    assert a["level"]["stage"]["to_next"] == 10, a["level"]["stage"]
    print(f"  [OK] 3 задачи «долго» = {a['points']} баллов → {a['level']['stage']['name']}, "
          f"ступень {a['level']['step']} из 40")


def test_manager_closes_only_his_branch():
    _seed()
    client.post(f"/api/mountain/assign/{M.token_for(OWNER)}",
                json={"tasks": [{"organization_id": ORG_A, "title": "Чужая задача"}]})
    tid = db.get_branch_tasks([ORG_A])[0]["id"]
    r = client.post(f"/api/mountain/close/{M.token_for(MANAGER)}", json={"task_id": tid})
    assert r.status_code == 404, (r.status_code, r.text[:120])
    print("  [OK] управляющий не закрывает задачу чужого филиала")


def test_penalty_pulls_down():
    """Штрафы за чек-листы вычитаются: филиал реально опускается по горе."""
    # считаем по своим сдачам: сдал слот +1, пропустил −1
    ch = M._checklist_points_own({ORG_A: {"done": 4, "expect": 34, "missed": -30}}, ORG_A)
    assert ch["points"] == 4 - 30, ch
    assert M._level(max(0, 150 + ch["points"]))["step"] < M._level(150)["step"], "штраф должен опускать"
    print(f"  [OK] штраф тянет вниз: {ch['points']} баллов за чек-листы")


def test_rules_pick_the_costliest():
    """Правила выбирают самое дорогое больное место — модель зовём один раз на сеть."""
    rows = A._task_rows({"branches": [{
        "name": "Актау", "revenue": 9_000_000,
        "foodcost": {"fact_pct": 38.0, "norm_pct": 30, "coverage": 85},
        "cancels": 120000, "writeoffs": {"total": 300000, "pct": 7},
        "reviews": [{"label": "2ГИС", "rating": 4.1}], "stop_count": 3,
    }, {"name": "Пустой", "revenue": 0}]})
    assert len(rows) == 1 and rows[0]["key"] == "fc", rows
    assert rows[0]["money"] == 720000, rows
    print(f"  [OK] выбран фудкост на {rows[0]['money']} ₸, филиал без проблем задачи не получил")


def test_low_recipe_coverage_is_not_money():
    """При низком покрытии техкарт факт-фудкост завышен — деньги по нему не обещаем."""
    rows = A._task_rows({"branches": [{
        "name": "Мало техкарт", "revenue": 9_000_000,
        "foodcost": {"fact_pct": 92.0, "norm_pct": 30, "coverage": 12},
        "writeoffs": {"total": 200000},
    }]})
    assert rows and rows[0]["key"] == "wo", rows
    print("  [OK] фудкост с покрытием 12% отброшен, задача ушла на списания")


def test_money_is_capped_by_revenue():
    """Задача не может «стоить» больше трети месячной выручки филиала."""
    rows = A._task_rows({"branches": [{
        "name": "Перекос", "revenue": 1_000_000, "cancels": 90_000_000,
    }]})
    assert rows and rows[0]["money"] == 330000, rows
    print(f"  [OK] 90 млн отмен урезаны до {rows[0]['money']} ₸ — трети выручки")


def test_service_orgs_are_out():
    """КЦ, ЦЕХ, ОФИС — не точки продаж: в горе и задачах им делать нечего."""
    assert M._is_branch("Астана, ул. Г.Алиева 10 (EXPO)")
    for junk in ("КЦ", "ЦЕХ", "ОФИС ", "РОЯЛТИ", "Астана, ул. Алпамыс 61 (ЦЕХ)"):
        assert not M._is_branch(junk), junk
    print("  [OK] служебные организации отфильтрованы, филиалы остались")


def test_summit_is_reachable_in_a_month():
    """Вершина берётся месяцем очень хорошей работы, но не даром.
    Идеальные чек-листы дают 360, задачи — ещё 100–400."""
    assert M.STEPS * M.STEP_POINTS == 400, (M.STEPS, M.STEP_POINTS)
    perfect_checklists = 4 * 3 * 30
    assert perfect_checklists < 400, "одними чек-листами вершину брать нельзя"
    assert perfect_checklists + 4 * 25 >= 400, "с задачами — должна браться"
    print(f"  [OK] вершина {M.STEPS * M.STEP_POINTS}: чек-листы дают {perfect_checklists}, "
          f"остальное — задачами")


def test_stages_are_ordered_and_named():
    """Путь разбит на участки: видно, где ты и что следующее."""
    prev = -1
    for need, name in M.STAGES:
        assert need > prev and name, (need, name)
        prev = need
    st = M._stage(175)
    assert st["name"] == "Первый лагерь" and st["next"] == "Скальный пояс" and st["to_next"] == 65, st
    assert M._stage(400)["next"] is None, M._stage(400)
    print("  [OK] участки: " + " → ".join(n for _v, n in M.STAGES))


def test_ranking_does_not_leak_between_clients():
    """Рейтинг сети — только свои филиалы. Таблица ресторанов многоарендная,
    и чужой клиент с филиалом «Астана…» не должен появляться на нашей горе."""
    _seed()
    OTHER = 900099
    db.add_restaurant(OTHER, "login", "org-other", "Астана, Чужой филиал",
                      iiko_server="", api_password="")
    mine = {r["organization_id"] for r in M._all_branches(OWNER)}
    assert mine == {ORG_A, ORG_B}, mine
    board = client.get(f"/api/mountain/data/{M.token_for(OWNER)}").json()
    names = {r["name"] for r in board["ranking"]}
    assert "Астана, Чужой филиал" not in names, names
    print("  [OK] в рейтинге только свои филиалы, чужой клиент не виден")


def test_manager_sees_whole_network_ranking():
    """Управляющий видит места всех филиалов сети, но задачи — только свои."""
    _seed()
    d = client.get(f"/api/mountain/data/{M.token_for(MANAGER)}").json()
    assert len(d["branches"]) == 1, d["branches"]
    assert len(d["ranking"]) == 2, d["ranking"]
    assert all("place" in r for r in d["ranking"]), d["ranking"]
    print("  [OK] управляющему видна вся сеть в рейтинге, свой филиал — один")


def test_negative_points_stay_at_the_foot():
    """Минус по баллам — подножие, а не вершина: раньше next был None и это
    читалось как «идти некуда»."""
    st = M._stage(-26)
    assert st["name"] == "Подножие" and st["next"] == "Тропа" and st["to_next"] == 80, st
    print("  [OK] отрицательные баллы → подножие, до «Тропы» 80")


def test_plan_tab_shows_the_network_only_to_the_owner():
    """Вкладка «План»: сеть целиком видит только владелец. У управляющего своя
    подписанная ссылка — и она главнее флага network в запросе, иначе он
    подставил бы network=1 и получил чужие филиалы."""
    _seed()
    own = api._plan_branches(ORG_B, True, {"id": OWNER})
    assert {b["organization_id"] for b in own} == {ORG_A, ORG_B}, own

    mt = M.token_for(MANAGER)
    # даже с network=1 и чужим org в пути — только свой филиал
    got = api._plan_branches(ORG_A, True, {"id": OWNER}, mt)
    assert [b["organization_id"] for b in got] == [ORG_B], got
    print("  [OK] владельцу вся сеть, управляющему по ссылке — только свой филиал")


def test_manager_cannot_write_plan_to_a_foreign_branch():
    """Запись плана ограничена тем же кругом, что и просмотр."""
    _seed()
    mt = M.token_for(MANAGER)
    bad = client.post(f"/api/plan-week/{ORG_B}", json={
        "week": "2026-W35", "token": mt,
        "cells": [{"org": ORG_A, "dow": 1, "ch_type": "hall", "planned": 99}]})
    assert bad.status_code == 403, (bad.status_code, bad.text[:120])
    ok = client.post(f"/api/plan-week/{ORG_B}", json={
        "week": "2026-W35", "token": mt,
        "cells": [{"org": ORG_B, "dow": 1, "ch_type": "hall", "planned": 99}]})
    assert ok.status_code == 200 and ok.json()["saved"] == 1, ok.text[:150]
    print("  [OK] в чужой филиал план не записать, в свой — можно")


def test_late_orders_signal_is_read_from_the_real_response():
    """/api/late-deliveries отдаёт late_pct ВЕРХНИМ полем, никакого summary там нет.
    Код читал ld["summary"]["late_pct"] — и критерий «Опоздавшие заказы» молчал
    у всех девяти филиалов, хотя данные были (Алиева: 429 доставок, 52 опоздали).
    Форма ответа снята с живого сервера 25.08."""
    live = {"delivered": 429, "late": 52, "late_pct": 12, "median_delay": 6,
            "worst_delay": 373, "suspicious": 3, "by_type": {}, "causes": [],
            "period": {"from": "2026-08-01", "to": "2026-08-25"}}
    sig = api._late_signal(live)
    assert sig == {"late_pct": 12, "deliveries": 429, "late": 52}, sig
    crit = {c["key"]: c for c in S.score_branch({"late": sig}, {})["criteria"]}
    assert crit["late"]["counted"] and crit["late"]["value"] == 20, crit["late"]
    print(f"  [OK] 52 из 429 → {sig['late_pct']}% опозданий, критерий посчитан ({crit['late']['value']})")


def test_manager_sees_scores_of_other_branches():
    """Владелец 25.08: управляющему не нужен разбор по критериям — нужен
    рейтинг филиалов с оценками, чтобы сравнить себя одним взглядом.
    Значит и показатели ключуются на владельца сети, а не на того, кто открыл
    ссылку: свой кэш управляющего знал бы только про его точку."""
    _seed()
    d = client.get(f"/api/mountain/data/{M.token_for(MANAGER)}").json()
    assert d["role"] == "manager"
    assert {r["organization_id"] for r in d["ranking"]} == {ORG_A, ORG_B}, d["ranking"]
    assert all("score" in r for r in d["ranking"]), d["ranking"]
    print(f"  [OK] управляющий видит рейтинг из {len(d['ranking'])} филиалов с полем оценки")


def test_weekly_average_ranks_and_shows_movement():
    """Вторничная сводка: средняя за неделю, порядок по убыванию и куда сдвинулось.
    Дни без оценки в среднее не идут — иначе филиал «падал» просто потому,
    что один ночной пересчёт не собрался."""
    _seed()
    from datetime import date, timedelta as td
    today = date.today()
    for i in range(1, 8):                     # текущая неделя
        db.save_branch_score(ORG_A, (today - td(days=i)).isoformat(), 80, 5)
        db.save_branch_score(ORG_B, (today - td(days=i)).isoformat(), 60, 5)
    db.save_branch_score(ORG_A, (today - td(days=3)).isoformat(), None, 1)   # день не собрался
    for i in range(8, 15):                    # прошлая неделя
        db.save_branch_score(ORG_A, (today - td(days=i)).isoformat(), 70, 5)
        db.save_branch_score(ORG_B, (today - td(days=i)).isoformat(), 60, 5)
    lines = M.weekly_score_lines([{"organization_id": ORG_A, "name": "Филиал А"},
                                  {"organization_id": ORG_B, "name": "Филиал Б"}])
    assert len(lines) == 2, lines
    assert lines[0].startswith("1. <b>Филиал А</b> — 80 из 100"), lines[0]
    assert "+10 к прошлой неделе" in lines[0], lines[0]
    assert "без изменений" in lines[1], lines[1]
    print(f"  [OK] {lines[0].replace('<b>','').replace('</b>','')}")


def test_score_history_is_scoped_like_everything_else():
    """История оценок: владельцу вся сеть, управляющему только его филиал."""
    _seed()
    db.save_branch_score(ORG_A, "2026-08-20", 71, 5)
    db.save_branch_score(ORG_B, "2026-08-20", 44, 5)
    o = client.get(f"/api/mountain/scores/{M.token_for(OWNER)}").json()
    m = client.get(f"/api/mountain/scores/{M.token_for(MANAGER)}").json()
    assert len(o["branches"]) == 2 and len(m["branches"]) == 1, (o, m)
    assert m["branches"][0]["organization_id"] == ORG_B, m
    assert any(h["score"] == 44 for h in m["branches"][0]["history"]), m
    print("  [OK] историю оценок управляющий видит только по своему филиалу")


def test_rating_signal_normalises_wolt_to_five_points():
    """Wolt отдаёт 8,8 из 10, 2ГИС — 4,6 из 5. Среднее «сырых» чисел дало бы
    филиалу с Wolt полтора балла форы из воздуха — приводим к пятибалльной."""
    sig = api._rating_signal({"platforms": [
        {"platform": "2gis", "rating": 4.6, "scale": 5},
        {"platform": "wolt", "rating": 8.8, "scale": 10}]})
    assert sig == {"avg": 4.5, "platforms": 2}, sig      # 4,6 и 8,8/10=4,4 → 4,5
    assert api._rating_signal({"platforms": []}) is None
    assert api._rating_signal(None) is None
    print(f"  [OK] 2ГИС 4,6/5 и Wolt 8,8/10 → средний {sig['avg']} из 5")


def test_shifts_signal_separates_zero_from_no_data():
    assert api._shifts_signal({"count": 0}) == {"pending": 0}
    assert api._shifts_signal({"count": 3}) == {"pending": 3}
    assert api._shifts_signal({}) is None and api._shifts_signal(None) is None
    print("  [OK] ноль непринятых смен — результат, отсутствие ответа — молчание")


def test_late_orders_signal_stays_silent_without_deliveries():
    """Филиал без доставок — не «идеальный»: критерий выпадает, а не даёт 100."""
    assert api._late_signal({"delivered": 0, "late": 0, "late_pct": None}) is None
    assert api._late_signal({"error": "iiko недоступен"}) is None
    assert api._late_signal(None) is None
    print("  [OK] нет доставок или iiko молчит → критерий выпадает")


def test_notice_reaches_the_addressed_branch_only():
    """Сообщение «всем» видит любой филиал, адресное — только свой."""
    _seed()
    to, tm = M.token_for(OWNER), M.token_for(MANAGER)
    assert client.post(f"/api/mountain/notice/{to}", json={"text": "всем"}).json()["ok"]
    nid_b = client.post(f"/api/mountain/notice/{to}",
                        json={"text": "только Б", "org": ORG_B}).json()["id"]
    nid_a = client.post(f"/api/mountain/notice/{to}",
                        json={"text": "только А", "org": ORG_A}).json()["id"]
    texts = [n["text"] for n in client.get(f"/api/mountain/notices/{tm}").json()["notices"]]
    assert "всем" in texts and "только Б" in texts, texts
    assert "только А" not in texts, "управляющий Б увидел сообщение чужого филиала"
    assert client.post(f"/api/mountain/notice/{tm}/read/{nid_b}").status_code == 200
    assert client.post(f"/api/mountain/notice/{tm}/read/{nid_a}").status_code == 403, \
        "чужое сообщение нельзя отметить прочитанным"
    print("  [OK] адресное сообщение видит и закрывает только свой филиал")


def test_read_receipt_cannot_be_faked_by_a_stranger():
    """«Прочитали N» — то, по чему владелец судит, дошло ли сообщение. Подписи
    токена мало: посторонний uid без роли накручивал счётчик."""
    _seed()
    to, tm = M.token_for(OWNER), M.token_for(MANAGER)
    nid = client.post(f"/api/mountain/notice/{to}", json={"text": "проверка"}).json()["id"]
    stranger = M.token_for(999999)          # подпись верная, роли нет
    assert client.post(f"/api/mountain/notice/{stranger}/read/{nid}").status_code == 403
    assert client.post(f"/api/mountain/notice/{tm}/read/{nid}").status_code == 200
    stats = {n["id"]: n["read_by"] for n in client.get(f"/api/mountain/notices/{to}").json()["notices"]}
    assert stats[nid] == 1, f"счётчик прочтений врёт: {stats[nid]}"
    print("  [OK] отметку «прочитал» ставит только адресат, счётчик не накрутить")


def test_branch_link_confines_access_regardless_of_telegram_tables():
    """Доступ на сайте определяет ТИП ССЫЛКИ, а не таблицы бота.

    Правило «владелец — чья регистрация филиала первая» пробовали и откатили:
    на проде филиал заведён несколько раз под разными telegram id, первой
    оказалась не запись владельца, и он терял всю сеть. Теперь ограничивает
    ссылка: по ссылке филиала виден ровно один филиал, кто бы её ни открыл."""
    _seed()
    sc = M.scope_of_token(M.token_for_org(ORG_B))
    assert sc["role"] == "manager" and sc["org"] == ORG_B, sc
    assert [b["organization_id"] for b in sc["branches"]] == [ORG_B], sc["branches"]

    # владелец по своей ссылке видит сеть
    so = M.scope_of_token(M.token_for(OWNER))
    assert so["role"] == "owner" and so["org"] is None, so
    assert {b["organization_id"] for b in so["branches"]} == {ORG_A, ORG_B}

    # по ссылке филиала задачу чужому филиалу не поставить
    r = client.post(f"/api/mountain/assign/{M.token_for_org(ORG_B)}",
                    json={"tasks": [{"organization_id": ORG_A, "title": "чужая"}]})
    assert r.status_code == 403, (r.status_code, r.text)
    print("  [OK] ссылка филиала ограничивает доступ независимо от telegram id")


def test_branch_link_gets_the_branch_board_not_the_owners():
    """Ссылка филиала открывает доску ФИЛИАЛА, а не сеть владельца.

    scope_of_token разбирал доступ верно — manager, один филиал. Но _board_for
    принимал только uid, а у ссылки филиала это uid ВЛАДЕЛЬЦА (своих кредов iiko
    у управляющего нет, см. scope_of_token), и роль считалась заново от него.
    Управляющий получал доску владельца целиком: форму постановки задач,
    рассылку «отправить всем» и прогресс всех девяти филиалов — а сдача
    чек-листа с экрана исчезала, она рисуется только не-владельцу.
    """
    _seed()
    t = M.token_for_org(ORG_B)
    assert M.scope_of_token(t)["role"] == "manager", "ссылка филиала — не владелец"
    d = client.get(f"/api/mountain/data/{t}").json()
    assert d.get("role") == "manager", f'доска отдала роль {d.get("role")}'
    names = [b["name"] for b in d.get("branches") or []]
    assert names == ["Астана, Филиал Б"], f"на доске чужие филиалы: {names}"
    print("  [OK] по ссылке филиала — только свой филиал и роль управляющего")


def test_personal_link_opens_the_site():
    """Ссылка вида /m/{токен} должна открывать сайт.

    `/api/mountain/links` раздаёт ссылки именно в таком виде, а маршрута под них
    не было вовсе: catch-all исключает префикс `m/`, и каждая розданная ссылка
    отвечала 404. Токен сайт читает из ?m=, поэтому путь ведёт туда.
    """
    _seed()
    t = M.token_for_org(ORG_B)
    r = client.get(f"/m/{t}", follow_redirects=False)
    assert r.status_code in (302, 307), f"личная ссылка отвечает {r.status_code}"
    assert r.headers["location"] == f"/?m={t}", r.headers.get("location")
    print("  [OK] /m/{токен} ведёт на сайт с этим токеном")


def test_checklist_time_is_the_restaurants_not_the_servers():
    """Время чек-листа — астанинское, независимо от часов сервера.

    Render крутится в UTC, а запись шла голым datetime.now(): чек-лист, сданный
    в 10:47 по Астане, показывался сданным в 05:47. Хуже цифры на экране было
    другое — по UTC считались и «какой сегодня день» (сутки переворачивались в
    05:00), и «сколько слотов уже прошло»: филиалу насчитывались пропуски за
    слоты, которые ещё не наступили, и оценка уезжала вниз.
    """
    from datetime import datetime as _dt, timezone as _tz
    utc_naive = _dt.now(_tz.utc).replace(tzinfo=None)
    off = round((db.local_now() - utc_naive).total_seconds() / 3600)
    assert off == 5, f"часы ресторана отстают/спешат на {off} ч от UTC, ждали +5"

    _seed()
    day = db.local_now().date().isoformat()
    db.save_checklist_upload(ORG_B, day, "10:45", "hall", ["h"], by=MANAGER)
    row = db.get_checklist_uploads([ORG_B], day)[0]
    stored = _dt.fromisoformat(row["created_at"])
    drift = abs((stored - db.local_now()).total_seconds())
    assert drift < 120, f"в базу записано не время филиала: {row['created_at']}"
    print("  [OK] время чек-листа — по Астане (UTC+5), а не по часам сервера")


def test_photos_are_not_kept_on_disk():
    """Кадры чек-листа не должны оседать на диске.

    Их не отдавала наружу ни одна ручка: клиент всегда получал только
    количество, картинки читал лишь разбор. Посмотреть их глазами было
    невозможно, а копились они бессрочно — около двух гигабайт в месяц на
    девяти филиалах. Разбору диск не нужен: кадры уже в памяти запроса.
    """
    import base64, os as _os
    from database import checklist_dir, get_checklist_uploads
    _seed()
    # однопиксельный JPEG — достаточно, чтобы пройти путь целиком
    px = base64.b64encode(bytes.fromhex(
        "ffd8ffe000104a46494600010100000100010000ffdb004300ff"
        "c00011080001000101011100ffc4001f0000010501010101010100000000000000000102030405060708090a0b"
        "ffda0008010100003f00d2cf20ffd9")).decode()
    before = sum(len(f) for _r, _d, f in _os.walk(checklist_dir()))
    r = client.post(f"/api/mountain/checklist/{M.token_for_org(ORG_B)}",
                    json={"slot": "10:45", "zone": "hall", "photos": [px, px]})
    assert r.status_code == 200, r.text[:200]
    assert r.json()["photos"] == 2, r.json()

    after = sum(len(f) for _r, _d, f in _os.walk(checklist_dir()))
    assert after == before, "кадры всё ещё пишутся на диск"

    day = db.local_now().date().isoformat()
    rows = [u for u in get_checklist_uploads([ORG_B], day) if u["slot"] == "10:45"]
    assert rows and len(rows[0]["photos"]) == 2, rows
    assert rows[0]["photos"][0].get("hash"), "не сохранён след кадра (хеш)"
    print("  [OK] кадры разобраны и не сохранены; в базе остался хеш и счётчик")


def test_zone_verdicts_do_not_overwrite_each_other():
    """Зоны сдаются по одной, а вердикт в базе один на слот.

    Простое сохранение затирало бы разбор зала, когда приедет кухня, — и
    владелец видел бы только последнюю зону.
    """
    from database import merge_checklist_review, get_checklist_reviews
    _seed()
    day = db.local_now().date().isoformat()
    merge_checklist_review(ORG_B, day, "10:45", True, [], "тест",
                           [{"zone": "зал", "item": "Урна не переполнена", "ok": True}])
    merge_checklist_review(ORG_B, day, "10:45", False,
                           [{"zone": "санузел", "what": "нет мыла"}], "тест",
                           [{"zone": "санузел", "item": "Есть мыло и бумага", "ok": False}])
    row = [r for r in get_checklist_reviews([ORG_B], day) if r["slot"] == "10:45"][0]
    zones = {c["zone"] for c in row["criteria"]}
    assert zones == {"зал", "санузел"}, f"разбор зоны потерян: {zones}"
    assert row["ok"] == 0, "слот с грязной зоной не может быть чистым"
    print("  [OK] разбор второй зоны дописывается, а не затирает первую")


def test_upload_says_honestly_why_there_was_no_review():
    """Когда разбора не было, управляющему говорят НАСТОЯЩУЮ причину.

    Причина от review_slot («выключен CHECKLIST_AI», «модель не ответила»)
    затиралась причиной отбора, и на экране появлялось «разбор не запускался:
    кадры не хранятся — проверяем каждую сдачу». Это не объясняет ничего:
    человек делает вывод, что виновато отключённое хранение кадров.
    """
    import base64
    _seed()
    px = base64.b64encode(bytes.fromhex(
        "ffd8ffe000104a46494600010100000100010000ffdb004300ff"
        "c00011080001000101011100ffc4001f0000010501010101010100000000000000000102030405060708090a0b"
        "ffda0008010100003f00d2cf20ffd9")).decode()
    r = client.post(f"/api/mountain/checklist/{M.token_for_org(ORG_B)}",
                    json={"slot": "15:00", "zone": "wc", "photos": [px]})
    assert r.status_code == 200, r.text[:200]
    reason = (r.json().get("review") or {}).get("reason") or ""
    assert "CHECKLIST_AI" in reason, f"причина подменена: {reason!r}"
    print(f"  [OK] причина отсутствия разбора честная: {reason}")


def test_checklists_do_not_count_until_the_owner_starts_them():
    """Без даты старта чек-листы не дают ни баллов, ни штрафов.

    Нужно и для тестов, и для честной раскатки: пока владелец не сказал «с
    такого-то дня работаем», спрашивать не за что.
    """
    _seed()
    _clear_checklists()
    day = db.local_now().date().isoformat()
    db.save_checklist_upload(ORG_B, day, "10:45", "hall", [{"hash": "h"}], by=MANAGER)
    prog = db.checklist_progress([ORG_A, ORG_B], "2026-08-01", day, slots_passed_today=4, start="")
    assert prog == {}, f"чек-листы считаются до старта: {prog}"
    print("  [OK] старт не задан — чек-листы вне зачёта")


def test_trying_once_no_longer_costs_more_than_never_trying():
    """Попытка не должна быть хуже бездействия.

    Отсчёт шёл с ПЕРВОЙ сдачи филиала: сдал один слот — и с этого дня отвечаешь
    за все, а кто не сдавал ни разу, в выборку не попадал вовсе. Филиал,
    попробовавший один раз, уходил в −29, восемь молчащих стояли на нуле.
    Спрашиваем со всех с одной даты.
    """
    _seed()
    _clear_checklists()
    day = db.local_now().date().isoformat()
    db.save_checklist_upload(ORG_B, day, "10:45", "hall", [{"hash": "h"}], by=MANAGER)
    prog = db.checklist_progress([ORG_A, ORG_B], "2026-08-01", day,
                                 slots_passed_today=4, start=day)
    tried, silent = prog[ORG_B], prog[ORG_A]
    assert silent["missed"] < 0, "молчащий филиал обязан получать штраф"
    assert tried["missed"] > silent["missed"], (
        f"попытка хуже бездействия: сдавший {tried}, молчащий {silent}")
    assert tried["done"] == 1 and silent["done"] == 0
    print(f"  [OK] сдавший {tried['missed']} против молчащего {silent['missed']} — попытка выгоднее")


def test_expectation_counts_from_the_start_not_from_the_first_upload():
    """Ожидание отсчитывается от даты старта, а не от первой сдачи филиала."""
    _seed()
    _clear_checklists()
    day = db.local_now().date().isoformat()
    db.save_checklist_upload(ORG_B, day, "10:45", "hall", [{"hash": "h"}], by=MANAGER)
    # старт сегодня: прошло 0 полных дней, ждём только прошедшие слоты
    today_only = db.checklist_progress([ORG_B], "2026-08-01", day,
                                       slots_passed_today=1, start=day)[ORG_B]
    assert today_only["expect"] == 3, today_only     # один слот x три зоны
    print(f"  [OK] старт сегодня, прошёл один слот — ждём 3 сдачи, получили {today_only['done']}")


if __name__ == "__main__":
    failed = 0
    for t in (test_token_is_signed,
              test_owner_sees_all_manager_sees_one,
              test_only_owner_assigns,
              test_points_and_step,
              test_manager_closes_only_his_branch,
              test_penalty_pulls_down,
              test_rules_pick_the_costliest,
              test_low_recipe_coverage_is_not_money,
              test_money_is_capped_by_revenue,
              test_service_orgs_are_out,
              test_summit_is_reachable_in_a_month,
              test_stages_are_ordered_and_named,
              test_ranking_does_not_leak_between_clients,
              test_manager_sees_whole_network_ranking,
              test_negative_points_stay_at_the_foot,
              test_plan_tab_shows_the_network_only_to_the_owner,
              test_manager_cannot_write_plan_to_a_foreign_branch,
              test_late_orders_signal_is_read_from_the_real_response,
              test_late_orders_signal_stays_silent_without_deliveries,
              test_manager_sees_scores_of_other_branches,
              test_weekly_average_ranks_and_shows_movement,
              test_score_history_is_scoped_like_everything_else,
              test_rating_signal_normalises_wolt_to_five_points,
              test_shifts_signal_separates_zero_from_no_data,
              test_notice_reaches_the_addressed_branch_only,
              test_read_receipt_cannot_be_faked_by_a_stranger,
              test_branch_link_confines_access_regardless_of_telegram_tables,
              test_branch_link_gets_the_branch_board_not_the_owners,
              test_personal_link_opens_the_site,
              test_checklist_time_is_the_restaurants_not_the_servers,
              test_photos_are_not_kept_on_disk,
              test_zone_verdicts_do_not_overwrite_each_other,
              test_upload_says_honestly_why_there_was_no_review,
              test_checklists_do_not_count_until_the_owner_starts_them,
              test_trying_once_no_longer_costs_more_than_never_trying,
              test_expectation_counts_from_the_start_not_from_the_first_upload):
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
