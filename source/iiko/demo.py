"""Демо-сеть для показа продукта новым клиентам.

Демо продаёт правдоподобием. Поэтому это не «рыба» из круглых чисел, а сеть,
которую можно читать как настоящую: у филиалов разные болячки, и каждая видна
тем экраном, который её и лечит. «Абая» лидирует, «Сатпаева» проваливает план
по доставке, у «Кунаева» отход при разборе выше нормы, «Жибек Жолы» отстаёт по
чек-листам. Если бы все были одинаково хороши, показывать было бы нечего.

Устройство простое до скуки: демо-филиалы получают ОБЫЧНЫЕ строки в обычных
таблицах — задачи в branch_tasks, разборы в salmon_acts. Поэтому экраны про
демо ничего не знают и работать с ним не учатся: они просто показывают то, что
лежит в базе. Единственное, что знает о демо, — разбор ссылки и запрет ходить
в iiko.

Идентификаторы филиалов начинаются с `demo-`. По этому префиксу `_use_demo_data`
отличает демо от настоящего филиала: у реальных iiko-организаций id — UUID, и
начаться с `demo-` он не может.
"""
from datetime import datetime, timedelta
from typing import Any, Dict, List

DEMO_PREFIX = "demo-"

# Сеть. Порядок не важен — доска сортирует по очкам.
BRANCHES: List[Dict[str, str]] = [
    {"organization_id": "demo-b1", "name": "Абая 52"},
    {"organization_id": "demo-b2", "name": "Сатпаева 30"},
    {"organization_id": "demo-b3", "name": "Кунаева 14"},
    {"organization_id": "demo-b4", "name": "Жибек Жолы 7"},
]

BRANCH_NAMES = {b["organization_id"]: b["name"] for b in BRANCHES}


def is_demo_org(org_id: str) -> bool:
    return str(org_id or "").startswith(DEMO_PREFIX)


# ── Показатели для оценки ────────────────────────────────────────────────────
# Ключ — ИМЯ филиала: так их хранит кэш оценок (_signals_by_branch).
# Числа подобраны так, чтобы оценка получилась разной и объяснимой: у лидера
# ровно всё, у отстающего проваливается один-два критерия, а не «всё плохо».

def signals() -> Dict[str, Dict[str, Any]]:
    """Показатели для оценки. Структура — та, что читает scoring.score_branch:
    вложенные словари, а не плоские числа.

    Подобраны так, чтобы оценка вышла разной и ОБЪЯСНИМОЙ: у лидера ровно всё,
    у отстающего проваливаются один-два критерия, а не «всё плохо». Сеть, где
    все одинаковы, показывать бессмысленно — продукт ровно про то, чтобы
    разницу было видно.
    """
    return {
        # Лидер: растёт, почти не опаздывает, план перевыполнен.
        "Абая 52": {
            "changes": {"revenue_pct": 7.3, "orders_pct": 5.1},
            "staff_late": {"total": 96, "late": 3},
            "late": {"total": 412, "late": 9},
            "rating": {"avg": 4.8, "scale": 5},
            "plan": {"pct": 104},
            "shifts": {"pending": 0},
        },
        # Проваливает план и опаздывает — про него и задачи на доске.
        "Сатпаева 30": {
            "changes": {"revenue_pct": -4.1, "orders_pct": -2.6},
            "staff_late": {"total": 88, "late": 9},
            "late": {"total": 377, "late": 41},
            "rating": {"avg": 4.4, "scale": 5},
            "plan": {"pct": 87},
            "shifts": {"pending": 2},
        },
        # Крепкий середняк: растёт, но копятся непринятые смены.
        "Кунаева 14": {
            "changes": {"revenue_pct": 6.8, "orders_pct": 4.4},
            "staff_late": {"total": 91, "late": 5},
            "late": {"total": 398, "late": 18},
            "rating": {"avg": 4.6, "scale": 5},
            "plan": {"pct": 98},
            "shifts": {"pending": 3},
        },
        # Стоит на месте, и каждая восьмая смена начинается позже графика.
        "Жибек Жолы 7": {
            "changes": {"revenue_pct": 1.2, "orders_pct": 0.4},
            "staff_late": {"total": 81, "late": 10},
            "late": {"total": 302, "late": 27},
            "rating": {"avg": 4.2, "scale": 5},
            "plan": {"pct": 92},
            "shifts": {"pending": 1},
        },
    }


# ── Подсказки о возможностях ────────────────────────────────────────────────
# Демо смотрят без провожатого, и половина силы продукта глазом не видна:
# «отход 4.1 %» ничего не говорит тому, кто не знает, что раньше эту цифру не
# считали вовсе. Карточка объясняет не ЧТО на экране, а ЗАЧЕМ оно.
#
# Ключ — раздел сайта (id из SECTIONS во фронте), подсказка приходит вместе с
# открытием вкладки, а не вываливается списком на входе: шесть карточек разом
# читают как рекламный блок, одну по делу — как объяснение.
#
# kind говорит, чем это является на самом деле, и врать тут нельзя:
#   есть      — работает, видно прямо сейчас на этом экране;
#   сервис    — отдельный продукт рядом, включается отдельно;
#   обсудить  — в продукте этого нет. Честное «обсудим» лучше витрины с
#               обещаниями: на внедрении обман вскроется всё равно, только
#               дороже.

HINTS: Dict[str, Dict[str, str]] = {
    "mountain": {
        "kind": "есть",
        "title": "Филиалы соревнуются, а не отчитываются",
        "body": "Оценка складывается из выручки, опозданий, отзывов, плана и "
                "чек-листов — и видна всем управляющим сразу. Отстающий узнаёт "
                "об этом не на собрании через месяц, а сегодня. Задачи ставит "
                "владелец, часть система заводит сама по показателям. Здесь же "
                "чек-листы с фото и разбором ИИ и акты разбора рыбы: стейк "
                "раскладывается на нарезку, обрезки и отход, а процент отхода по "
                "филиалам рядом — видно, кто режет аккуратно.",
    },
    "plan": {
        "kind": "есть",
        "title": "План ставится сам",
        "body": "По средней за шесть недель, отдельно по залу, выносу, доставке "
                "и агрегаторам. Нажимать ничего не надо. Факт сравнивается день "
                "в день — во вторник с вторником, а не с целой прошлой неделей, "
                "иначе любой филиал во вторник выглядел бы провалившимся.",
    },
    "supply": {
        "kind": "есть",
        "title": "Видно, где то же самое брали дешевле",
        "body": "Цена каждой позиции в накладной сверяется с историей закупа и с "
                "другими филиалами сети: система показывает, у какого поставщика "
                "и когда этот товар брали дешевле и сколько вы переплатили на "
                "этой накладной. Видно до того, как её провели.",
    },
    "dash": {
        "kind": "есть",
        "title": "Выручка вживую, а не вчерашняя",
        "body": "Касса обновляется в течение дня, рядом — вчера, тот же день "
                "недели назад и прогноз месяца. Утром в 11:00 то же самое "
                "приходит в телеграм владельцу и управляющим по их филиалам.",
    },
    "menu": {
        "kind": "есть",
        "title": "Что продаётся, а что лежит",
        "body": "Продажи по позициям и каналам за период: зал, вынос, доставка, "
                "агрегаторы. Видно, что тянет выручку, а что занимает место в "
                "меню и в стоп-листе.",
    },
    "shifts": {
        "kind": "есть",
        "title": "Расхождения по кассе не теряются",
        "body": "Каждая кассовая смена сверяется: выручка, наличные, карта. "
                "Расхождение и незакрытые смены попадают в ночную сводку и в "
                "оценку филиала — искать их руками в iikoOffice не нужно.",
    },
    "staff": {
        "kind": "есть",
        "title": "Смены по QR, без бумажного табеля",
        "body": "Сотрудник сканирует QR в подсобке, отмечается телефоном, "
                "геопозиция проверяется. Опоздания считаются сами, попадают в "
                "оценку филиала, а зарплата — начислено, выдано, остаток — "
                "считается по фактическим сменам. Открытие личной смены прямо "
                "на кассе делаем совместно с iiko: работаем с их командой, "
                "лицензии в процессе получения.",
    },
    "sched": {
        "kind": "есть",
        "title": "График, который видит сам сотрудник",
        "body": "Управляющий ставит смены, сотрудник видит свои в телеграме "
                "вместе с оплатой за день. Ставки задаются прямо на смене.",
    },
    "rep": {
        "kind": "есть",
        "title": "Отзывы со всех площадок в одном месте",
        "body": "Рейтинги агрегаторов и карт собираются вместе и входят в оценку "
                "филиала. Падение рейтинга становится задачей, а не новостью "
                "через квартал.",
    },
    "kds": {
        "kind": "сервис",
        "title": "Кухонный экран — отдельный продукт",
        "body": "Экран повара со временем сборки и очередью заказов у нас есть, "
                "но это отдельный сервис рядом с этим кабинетом, он и "
                "подключается отдельно. Покажем и расскажем про условия.",
    },
    "stock": {
        "kind": "обсудить",
        "title": "Строгий складской учёт — под задачу",
        "body": "Сверку «было + приход − остаток» против расхода по техкартам мы "
                "пробовали и убрали: без порядка в техкартах она показывала "
                "недостачу там, где её нет. Если учёт у вас ведётся строго — "
                "это обсуждаемо, делается под вашу номенклатуру.",
    },
}


# ── Позиции разбора и цены для демо ─────────────────────────────────────────
# Разбор ходит в номенклатуру iiko за позициями и в накладные за ценой. В демо
# ни того, ни другого нет, поэтому отдаём готовый справочник — экран о разнице
# не знает и работает обычным путём.

def checklist_start() -> str:
    """С какого дня чек-листы в зачёте. В демо — неделя назад: ровно столько
    сдач мы и засеваем, а более ранний старт дал бы филиалам долг за дни,
    которых в демо не было."""
    from datetime import date
    return (date.today() - timedelta(days=7)).isoformat()


def acts_context(kind: str) -> Dict[str, Any]:
    import acts as A
    spec = A.kind_spec(kind)
    names = {r: (spec["names"].get(r) or ["—"])[0] for r in A.roles_of(spec)}
    return {
        "products": {r: {"id": f"demo-{r}", "name": n, "unit": "кг"}
                     for r, n in names.items()},
        "price_per_kg": spec.get("raw_price") or 9300,
        "price_from": "константа учёта" if spec.get("price_const") else "себестоимость iiko · демо",
        "trim_price": spec["trim_price"] or 12000,
        "trim_from": "константа учёта", "trim_const": bool(spec["trim_price"]),
        "kind": spec["key"], "title": spec["title"],
        "raw_label": spec["raw_label"], "raw_short": spec["raw_short"],
    }


def teriyaki_context() -> Dict[str, Any]:
    import acts as A
    spec = A.TERIYAKI_SPEC
    return {
        "products": {r: {"id": f"demo-{r}", "name": (spec["names"][r])[0], "unit": "кг"}
                     for r in spec["roles"]},
        "trim_price": spec["trim_price"],
        "sauce_price": 4200, "sauce_from": "Азия Фуд · демо",
        "kind": "teriyaki", "title": spec["title"],
    }


# ── Засев базы ───────────────────────────────────────────────────────────────
# Демо-филиалы получают обычные строки в обычных таблицах. Идемпотентно: засев
# зовут при каждом открытии ссылки, и он не должен плодить дубли.

def _today():
    from datetime import date
    return date.today()


def seed(force: bool = False) -> Dict[str, int]:
    """Кладёт демо-данные в обычные таблицы. Возвращает, сколько чего создано.

    Задачи ставятся с задержкой в днях, чтобы на доске были и открытые, и
    закрытые: сеть, где всё сделано, выглядит мёртвой, а где ничего не сделано —
    заброшенной.
    """
    from database import get_db, add_branch_task, get_branch_tasks
    made = {"tasks": 0, "acts": 0}

    orgs = [b["organization_id"] for b in BRANCHES]
    existing = {t["organization_id"] for t in get_branch_tasks(orgs)}
    for b in BRANCHES:
        org = b["organization_id"]
        if org in existing and not force:
            continue
        for title, detail, money, effort in tasks().get(org, []):
            add_branch_task(org, b["name"], title, detail=detail, money=money,
                            effort=effort, created_by=0)
            made["tasks"] += 1
        # плюс уже сделанные — они дают очки и поднимают филиал на горе
        for i in range(_EXTRA_DONE.get(org, 0)):
            add_branch_task(org, b["name"], _DONE_TITLES[i % len(_DONE_TITLES)],
                            detail="", money=0, effort="средне", created_by=0)
            made["tasks"] += 1

    made["staff"] = _seed_staff_access(force)
    made["shifts"] = _seed_schedule(force)
    made["done"] = _close_some_tasks()
    made["checklists"] = _seed_checklists(force)
    made["acts"] = _seed_acts(force)
    return made


# Сколько задач филиал уже закрыл. Сеть, где не сделано ничего, выглядит
# заброшенной, а где сделано всё — мёртвой: очки на доске должны отличаться,
# иначе гора плоская и смотреть на неё незачем.
_DONE_SHARE = {"demo-b1": 1.0, "demo-b2": 0.0, "demo-b3": 1.0, "demo-b4": 0.5}


def _close_some_tasks() -> int:
    """Закрывает часть задач — очки на доске берутся именно из закрытых."""
    from database import close_branch_task, get_branch_tasks
    n = 0
    for b in BRANCHES:
        org = b["organization_id"]
        mine = [t for t in get_branch_tasks([org]) if t["status"] == "open"]
        # у лидера закрыто всё, у отстающего — ничего; открытые нужны тоже,
        # иначе на доске не будет задач, которые показывают, как это работает
        keep_open = _EXTRA_DONE.get(org, 0)
        for t in mine[:keep_open]:
            if close_branch_task(t["id"], done_by=0):
                n += 1
    return n


# Сколько ДОПОЛНИТЕЛЬНЫХ (уже закрытых) задач завести филиалу: они дают очки,
# а в списке открытых не висят.
_EXTRA_DONE = {"demo-b1": 6, "demo-b2": 1, "demo-b3": 4, "demo-b4": 2}


# Команда филиала. Те же имена, что отдаёт демо-справочник сотрудников
# (_DEMO_TEAM в api), иначе на «Расписании» одни люди, а в «Сменах» другие.
TEAM = [("d1", "Виктория Ким"), ("d2", "Артём Волков"), ("d3", "Асан Нуров")]


def _seed_staff_access(force: bool) -> int:
    """Привязанные сотрудники: без них экран смен показывает пустой список и
    выглядит так, будто мини-апп никто не подключил."""
    from database import issue_staff_access, bind_staff_access, list_staff_access
    n = 0
    for bi, b in enumerate(BRANCHES):
        org = b["organization_id"]
        if list_staff_access(org) and not force:
            continue
        for i, (eid, name) in enumerate(TEAM):
            code = issue_staff_access(org, eid, name)
            # у «Жибек Жолы» подключён не весь состав — так бывает, и на это
            # управляющему как раз и надо смотреть
            if not (org == "demo-b4" and i == len(TEAM) - 1):
                bind_staff_access(code, 900000 + bi * 10 + i)
            n += 1
    return n


# Оплата за смену. Разная по ролям — одинаковая выглядела бы как заглушка.
_PAY = {"d1": 14_000, "d2": 12_000, "d3": 11_000}


def _seed_schedule(force: bool) -> int:
    """График на текущую и прошлую неделю.

    Без него сетка пустая и пишет «в этом филиале ещё никого не ставили», хотя
    сотрудники в филиале есть: в сетку человек попадает сменой, а не привязкой.
    Ставим будни — в выходные состав другой, и показывать это в демо незачем.
    """
    from datetime import date
    from database import set_staff_shift, get_staff_schedule
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    start, end = monday - timedelta(days=7), monday + timedelta(days=6)
    # get_staff_schedule берёт ОДИН филиал, не список
    if get_staff_schedule(BRANCHES[0]["organization_id"],
                          start.isoformat(), end.isoformat()) and not force:
        return 0
    n = 0
    for b in BRANCHES:
        org = b["organization_id"]
        for back in range(0, 14):
            d = start + timedelta(days=back)
            if d.weekday() >= 5:                 # суббота и воскресенье пропускаем
                continue
            for eid, _name in TEAM:
                hours = {"d1": ("10:00", "18:00"), "d2": ("12:00", "20:00"),
                         "d3": ("09:00", "17:00")}[eid]
                set_staff_shift(org, eid, d.isoformat(), hours[0], hours[1],
                                pay=_PAY[eid])
                n += 1
    return n


def _seed_checklists(force: bool) -> int:
    """Сдачи чек-листов за последние дни: без них критерий «Чек-листы» не
    считается вовсе, и оценка выходит неполной."""
    from database import save_checklist_upload, get_db
    conn = get_db()
    try:
        have = conn.execute(
            "SELECT COUNT(*) FROM checklist_uploads WHERE organization_id LIKE 'demo-%'"
        ).fetchone()[0]
    except Exception:
        have = 0
    finally:
        conn.close()
    if have and not force:
        return 0
    from datetime import date
    slots = ["10:45", "15:00", "20:45", "23:45"]
    zones = ["hall", "kitchen", "wc"]
    # доля сданных слотов: «Жибек Жолы» отстаёт — это его болячка на доске
    share = {"demo-b1": 4, "demo-b2": 3, "demo-b3": 4, "demo-b4": 2}
    n = 0
    for b in BRANCHES:
        org = b["organization_id"]
        for back in range(1, 8):
            day = (date.today() - timedelta(days=back)).isoformat()
            for sl in slots[:share.get(org, 3)]:
                for z in zones:
                    save_checklist_upload(org, day, sl, z, [], note="демо", by=0)
                    n += 1
    return n


_DONE_TITLES = [
    "Свести инвентаризацию", "Поднять рейтинг на Wolt", "Убрать пересорт по соусам",
    "Обучить нового повара горячему цеху", "Закрыть долги по чек-листам",
    "Проверить закуп рыбы у второго поставщика",
]

# Разборы: (филиал, вход, нарезка, обрезки). Отход считается сам и выходит
# разным — у «Кунаева» выше нормы, и это видно в таблице по сети.
_ACT_PLAN = [
    ("demo-b1", 12.0, 9.6, 2.1), ("demo-b1", 10.0, 8.1, 1.7),
    ("demo-b2", 11.5, 9.0, 2.0), ("demo-b3", 12.0, 8.9, 2.2),
    ("demo-b3", 9.0, 6.6, 1.7), ("demo-b4", 10.5, 8.3, 1.9),
]


def waste_pct(org: str) -> float:
    """Отход филиала по засеянным разборам. Задачи берут число ОТСЮДА, а не
    пишут его руками: разойдясь на 0.2 %, демо перестаёт быть убедительным
    ровно в тот момент, когда клиент решит сверить."""
    import acts as A
    rows = [{"weight_raw": raw, "weight_slice": sl, "weight_trim": tr,
             "weight_waste": round(raw - sl - tr, 3), "price_per_kg": 9300,
             "trim_price": A.TRIM_PRICE}
            for o, raw, sl, tr in _ACT_PLAN if o == org]
    return A.summarize(rows)["waste_pct"] if rows else 0.0


def late_pct(name: str) -> float:
    sl = signals()[name]["staff_late"]
    return round(sl["late"] / sl["total"] * 100, 1)


def tasks() -> Dict[str, List]:
    """Задачи филиалов. Числа в тексте берутся из тех же источников, что и
    цифры на экранах, — иначе задача обещает одно, а доска показывает другое.
    """
    sg = signals()
    return {
        "demo-b1": [
            ("Снять стоп-лист по лососю",
             "Третий день в стопе — теряем роллы на витрине.", 180_000, "средне"),
        ],
        "demo-b2": [
            ("Разобрать провал плана по доставке",
             f"{sg['Сатпаева 30']['plan']['pct']} % плана вторую неделю. "
             "Проверь курьеров и время сборки.", 640_000, "сложно"),
            ("Выручка падает",
             f"{abs(sg['Сатпаева 30']['changes']['revenue_pct'])} % к прошлому периоду, "
             f"заказов меньше на {abs(sg['Сатпаева 30']['changes']['orders_pct'])} %. "
             "Смотри доставку и стоп-лист.", 420_000, "сложно"),
        ],
        "demo-b3": [
            ("Отход при разборе выше нормы",
             f"{waste_pct('demo-b3')} % при норме до 5. "
             "Пересмотри, кто разделывает и чем.", 210_000, "средне"),
        ],
        "demo-b4": [
            ("Опоздания сотрудников",
             f"{late_pct('Жибек Жолы 7')} % смен начинаются позже графика.",
             95_000, "легко"),
            ("Сдать чек-листы за вчера",
             "Пропущены два слота из четырёх.", 0, "легко"),
        ],
    }


def _seed_acts(force: bool) -> int:
    """Разборы лосося за последние дни — чтобы свод и таблица по сети не пустовали."""
    import acts as A
    from database import add_salmon_act, get_salmon_acts
    if get_salmon_acts([b["organization_id"] for b in BRANCHES], limit=1) and not force:
        return 0
    n = 0
    for org, raw, sl, trim in _ACT_PLAN:
        calc = A.split_salmon(9300, raw, sl, trim)
        if calc.get("ok"):
            add_salmon_act(org, 0, calc, 9300, A.TRIM_PRICE)
            n += 1
    return n
