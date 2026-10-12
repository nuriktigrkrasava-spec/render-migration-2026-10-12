"""Демо-ссылка: показ продукта новым клиентам.

Главное, что здесь проверяется, — что демо НЕ МОЖЕТ дотянуться до настоящих
данных. Это ссылка, которую раздают незнакомым людям.

Запуск:  python3 -m pytest -q test_demo_link.py
"""
import os
import tempfile

os.environ.setdefault("DB_PATH", tempfile.mktemp(suffix=".db"))
os.environ.setdefault("ENCRYPTION_KEY", "demo-test-key")

import database as D  # noqa: E402
import demo  # noqa: E402
import mountain_routes as M  # noqa: E402
import api  # noqa: E402

D.init_db()


def _link(label="Сеть Ромашка"):
    import secrets
    code = secrets.token_urlsafe(6)
    D.add_demo_link(code, label, created_by=0)
    return code, M.token_for_demo(code)


def test_demo_link_opens_a_made_up_network():
    code, token = _link()
    sc = M.scope_of_token(token)
    assert sc["valid"] and sc["role"] == "owner" and sc["demo"] is True
    names = {b["name"] for b in sc["branches"]}
    assert names == {b["name"] for b in demo.BRANCHES}, names
    assert all(b["organization_id"].startswith("demo-") for b in sc["branches"])


def test_a_forged_signature_opens_nothing():
    """Подпись — единственная защита ссылки: кода мало."""
    assert M.scope_of_token("dабвгд.подделка")["valid"] is False
    code, token = _link()
    # Менять последний символ на фиксированный нельзя: если подпись на него и
    # кончалась, токен оставался прежним и тест проходил случайно.
    last = token[-1]
    assert M.scope_of_token(token[:-1] + ("y" if last != "y" else "z"))["valid"] is False
    assert M.scope_of_token(token.split(".")[0])["valid"] is False      # без подписи
    assert M.scope_of_token("d" + code + ".")["valid"] is False         # пустая подпись


def test_demo_never_reaches_a_real_branch():
    """Главное свойство. Демо-филиалы уходят в демо-режим ПО ПРЕФИКСУ, до любого
    обращения к iiko, а настоящий UUID-филиал в демо-режим не попадает."""
    user = {"id": 0}
    for b in demo.BRANCHES:
        assert api._use_demo_data(user, b["organization_id"]) is True
    # реальный организационный id — UUID, префикса demo- у него быть не может
    assert api._use_demo_data(user, "4d56a98e-ba42-11ee-0a80-0c4b0002dc21") is False


def test_demo_scope_has_no_credentials():
    """uid=0 намеренно: от его имени в iiko не сходить — филиалов у него нет."""
    code, token = _link()
    sc = M.scope_of_token(token)
    assert sc["uid"] == 0
    assert D.get_restaurants(0) == [] or all(
        r.get("organization_id", "").startswith("demo-") for r in D.get_restaurants(0))


def test_counter_counts_every_open():
    code, token = _link("Клиент А")
    M.scope_of_token(token)
    M.scope_of_token(token)
    M.scope_of_token(token)
    row = [r for r in D.get_demo_links(0) if r["code"] == code][0]
    assert row["opens"] == 3, row
    assert row["first_open_at"] and row["last_open_at"]
    assert row["label"] == "Клиент А"


def test_counter_remembers_the_first_open_separately():
    """«Посмотрел через час» и «через неделю» — разные разговоры с клиентом."""
    code, token = _link()
    M.scope_of_token(token)
    first = [r for r in D.get_demo_links(0) if r["code"] == code][0]["first_open_at"]
    M.scope_of_token(token)
    again = [r for r in D.get_demo_links(0) if r["code"] == code][0]
    assert again["first_open_at"] == first, "первое открытие не должно переписываться"
    assert again["opens"] == 2


def test_the_demo_network_is_worth_showing():
    """Сеть, где все одинаковы, показывать нечего: продукт ровно про разницу."""
    import scoring as S
    scores = [S.score_branch(sig, {"done": 20, "missed": 4})["score"]
              for sig in demo.signals().values()]
    assert all(s is not None for s in scores), scores
    assert max(scores) - min(scores) >= 20, scores       # лидер и отстающий видны
    assert all(30 <= s <= 95 for s in scores), scores    # без карикатуры


def test_seeding_twice_does_not_double_the_data(tmp_path, monkeypatch):
    """Засев зовут при каждом открытии ссылки.

    Своя база: засев пишет разборы и задачи в ОБЫЧНЫЕ таблицы, и в общей
    тестовой базе демо-филиалы протекали в чужие проверки — таблица разборов по
    сети начинала видеть «Абая 52» рядом с настоящими филиалами.
    """
    monkeypatch.setattr(D, "DB_PATH", str(tmp_path / "seed.db"))
    D.init_db()
    orgs = [b["organization_id"] for b in demo.BRANCHES]
    demo.seed()
    before = len(D.get_branch_tasks(orgs))
    assert before > 0, "засев должен что-то создать"
    demo.seed()
    assert len(D.get_branch_tasks(orgs)) == before


def test_every_screen_has_a_hint():
    """Подсказка объясняет ЗАЧЕМ экран, а не что на нём нарисовано.

    Ключи — id разделов сайта: подсказка приходит вместе с открытием вкладки.
    Разборы и чек-листы живут внутри «Горы», поэтому про них рассказывает её
    подсказка, а не отдельная.
    """
    assert set(demo.HINTS) >= {"mountain", "plan", "supply", "dash", "staff"}
    assert "разбор" in demo.HINTS["mountain"]["body"].lower()
    assert "чек-лист" in demo.HINTS["mountain"]["body"].lower()
    for key, h in demo.HINTS.items():
        assert h["title"] and h["body"], key
        assert len(h["body"]) > 60, f"{key}: слишком коротко, чтобы что-то объяснить"


def test_counter_starts_even_for_a_link_issued_outside_the_cabinet():
    """Ссылка действительна по ПОДПИСИ, а не по строке в базе. Без этого
    выпущенная в обход кабинета открывалась бы молча, и счётчик врал бы нулём."""
    token = M.token_for_demo("вне-кабинета")
    assert M.scope_of_token(token)["valid"] is True
    row = [r for r in D.get_demo_links(0) if r["code"] == "вне-кабинета"]
    assert row and row[0]["opens"] == 1, row


def test_plan_knows_the_demo_network():
    """Вкладка «План» падала белым экраном: uid_from_token не разбирал демо-токен,
    филиалов выходило ноль, сервер отдавал короткий ответ без сводки сети, а
    фронт читал у неё .fact. Лечим с обеих сторон — здесь серверную."""
    code, token = _link()
    got = api._plan_branches("demo-org-1", network=True, user={"id": 0}, token=token)
    assert len(got) == len(demo.BRANCHES), got
    assert {b["name"] for b in got} == {b["name"] for b in demo.BRANCHES}


def test_the_qr_hint_says_we_work_with_iiko():
    """Открытие смены на кассе требует плагина и лицензии iiko — обещать это
    как готовое нельзя, но и молчать незачем: работа идёт."""
    h = demo.HINTS["staff"]
    text = (h["title"] + " " + h["body"]).lower()
    assert "iiko" in text and "лицензи" in text, h


def test_checklists_count_in_demo():
    """Без даты старта прогресс пустой, критерий «Чек-листы» не считается, и
    продукт выглядит недоделанным именно там, где он силён."""
    from datetime import date
    start = demo.checklist_start()
    assert start < date.today().isoformat(), start


def test_acts_have_a_catalog_in_demo():
    """Разбор ходит в номенклатуру iiko и в накладные. В демо ни того, ни
    другого нет — отдаём готовый справочник, иначе экран отвечал 403."""
    for kind in ("salmon", "eel"):
        ctx = demo.acts_context(kind)
        assert ctx["price_per_kg"] > 0, kind
        for role in ("raw", "slice", "trim", "waste"):
            assert ctx["products"][role]["name"], (kind, role)
    ter = demo.teriyaki_context()
    assert ter["sauce_price"] > 0 and ter["trim_price"] > 0
    assert set(ter["products"]) == {"trim", "teriyaki", "sauce"}


def test_hints_are_keyed_by_tab_and_honest():
    """Подсказка приходит к своей вкладке, а не списком на входе. И она обязана
    говорить правду о том, чем является: «обсудить» значит, что этого НЕТ."""
    assert set(demo.HINTS) >= {"mountain", "plan", "supply", "dash", "staff"}
    kinds = {h["kind"] for h in demo.HINTS.values()}
    assert kinds <= {"есть", "сервис", "обсудить"}, kinds
    # в продукте есть то, чего у нас нет, и то, что живёт отдельно — оба случая
    assert any(h["kind"] == "обсудить" for h in demo.HINTS.values())
    assert any(h["kind"] == "сервис" for h in demo.HINTS.values())
    for key, h in demo.HINTS.items():
        assert h["title"] and len(h["body"]) > 60, key


def test_the_invoice_hint_says_where_it_was_cheaper():
    """Главное, ради чего смотрят накладные: видно, где то же самое брали
    дешевле. Если подсказка про склад об этом молчит — она бесполезна."""
    h = demo.HINTS["supply"]
    text = (h["title"] + " " + h["body"]).lower()
    assert "дешевле" in text, h
    assert "переплат" in text or "сверя" in text, h


def test_hints_are_only_for_the_demo_link():
    """Своему клиенту рассказывать про его же продукт незачем."""
    import asyncio
    code, token = _link()
    got = asyncio.run(M.demo_hints(token))
    import json
    assert json.loads(got.body)["demo"] is True
    real = asyncio.run(M.demo_hints("12345.подделка"))
    assert json.loads(real.body)["hints"] == {}


def test_task_numbers_match_what_the_screens_show(tmp_path, monkeypatch):
    """Сверка всех чисел демо. Задача, обещающая «отход 7.4 %» при реальных 7.6,
    убивает демо ровно в тот момент, когда клиент решит проверить. Поэтому текст
    задач считается из тех же источников, что и цифры на экранах, — а тест
    сторожит, что источник действительно один.
    """
    import acts as A
    monkeypatch.setattr(D, "DB_PATH", str(tmp_path / "nums.db"))
    D.init_db()
    demo.seed()

    rows = D.get_salmon_acts([b["organization_id"] for b in demo.BRANCHES], limit=99)
    for b in demo.BRANCHES:
        mine = [r for r in rows if r["organization_id"] == b["organization_id"]]
        if not mine:
            continue
        real = A.summarize(mine)["waste_pct"]
        assert abs(real - demo.waste_pct(b["organization_id"])) < 0.05, (b["name"], real)

    # отход «Кунаева» действительно выше нормы, у остальных — в норме
    assert demo.waste_pct("demo-b3") > 5, demo.waste_pct("demo-b3")
    for org in ("demo-b1", "demo-b2", "demo-b4"):
        assert demo.waste_pct(org) <= 5, (org, demo.waste_pct(org))

    # число в тексте задачи — то же, что посчиталось
    t = dict(demo.tasks())
    assert f"{demo.waste_pct('demo-b3')} %" in t["demo-b3"][0][1]
    assert f"{demo.late_pct('Жибек Жолы 7')} %" in t["demo-b4"][0][1]
    assert f"{demo.signals()['Сатпаева 30']['plan']['pct']} %" in t["demo-b2"][0][1]


def test_shifts_section_has_people(tmp_path, monkeypatch):
    """Раздел смен без привязанных сотрудников выглядит так, будто мини-апп
    никто не подключил. У «Жибек Жолы» подключены не все — это его болячка."""
    monkeypatch.setattr(D, "DB_PATH", str(tmp_path / "staff.db"))
    D.init_db()
    demo.seed()
    for b in demo.BRANCHES:
        acc = D.list_staff_access(b["organization_id"])
        assert len(acc) == len(demo.TEAM), b["name"]
        bound = sum(1 for a in acc if a["telegram_id"])
        assert bound == (len(demo.TEAM) - 1 if b["organization_id"] == "demo-b4"
                         else len(demo.TEAM)), b["name"]


def test_the_branch_switcher_shows_the_demo_network(tmp_path, monkeypatch):
    """Переключатель отдавал один «Демо Ресторан», и «Расписание» жило в другом
    филиале, чем «Гора»: сеть на доске была, а выбрать её было нельзя."""
    import asyncio
    code, token = _link()
    got = asyncio.run(api.list_restaurants(user={"id": 0}, x_site_token=token))
    assert len(got) == len(demo.BRANCHES), got
    assert {r["organization_name"] for r in got} == {b["name"] for b in demo.BRANCHES}


def test_the_schedule_grid_has_shifts(tmp_path, monkeypatch):
    """В сетку графика человек попадает СМЕНОЙ, а не привязкой: без смен экран
    писал «в этом филиале ещё никого не ставили», хотя сотрудники есть."""
    from datetime import date, timedelta
    monkeypatch.setattr(D, "DB_PATH", str(tmp_path / "sched.db"))
    D.init_db()
    demo.seed()
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    rows = D.get_staff_schedule("demo-b1", monday.isoformat(),
                                (monday + timedelta(days=6)).isoformat())
    assert len(rows) == len(demo.TEAM) * 5, rows      # пять будних дней на человека
    assert all(r["pay"] and r["start_time"] and r["end_time"] for r in rows)
    # оплата разная по ролям: одинаковая читалась бы как заглушка
    assert len({r["pay"] for r in rows}) == len(demo.TEAM)
    demo.seed()
    assert len(D.get_staff_schedule("demo-b1", monday.isoformat(),
                                    (monday + timedelta(days=6)).isoformat())) == len(rows)


def test_demo_employees_report_real_shift_counts(tmp_path, monkeypatch):
    """В сетку графика попадают только те, у кого shifts > 0. Демо-справочник
    отдавал жёсткий ноль, и бригада не показывалась вовсе."""
    monkeypatch.setattr(D, "DB_PATH", str(tmp_path / "emp.db"))
    D.init_db()
    demo.seed()
    counts = D.staff_shift_counts("demo-b1")
    assert set(counts) == {eid for eid, _ in demo.TEAM}, counts
    assert all(v > 0 for v in counts.values()), counts
