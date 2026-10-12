"""Панель контроля журналов: /j/{token} — личная ссылка владельца без пароля.

Отдельный модуль-роутер, как widget_routes: подключается одним include_router и
не конфликтует при слиянии веток. Токен переиспользуем от виджета — механизм
подписанного stateless-uid уже отлажен, второй заводить незачем.

Считаем всё на сервере и отдаём странице готовую структуру: страница только
рисует. Так одна и та же арифметика «сдано N из M» не разъедется между
графом, списком и утренним отчётом в боте.
"""
import html
import os
import json
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse

import journals as J
from database import (get_branch_managers, get_journal_entries,
                      get_journal_range, save_journal_entry)
from widget_routes import _uid_from_token, widget_token_for

router = APIRouter()

_SITES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sites")


def _read_site(name: str) -> str:
    """HTML страницы — отдельным файлом в sites/, не строкой в модуле."""
    with open(os.path.join(_SITES_DIR, name), encoding="utf-8") as f:
        return f.read()

def _esc(t: str) -> str:
    """Текст владельца уходит в Telegram с parse_mode=HTML — экранируем, иначе
    одна угловая скобка обрушит всю рассылку.

    После экранирования возвращаем ровно один приём разметки: **жирный**.
    Порядок важен — сначала escape, потом разметка, иначе через «**» можно
    было бы протащить произвольный тег.
    """
    s = html.escape(t or "")
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s, flags=re.S)


_RU_DOW = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
_RU_MON = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
           "августа", "сентября", "октября", "ноября", "декабря"]


def journal_link_for(uid: int, base: str = "") -> str:
    return f"{base}/j/{widget_token_for(uid)}"


def short_name(full: str) -> str:
    """«Астана, ул. Бухар Жырау 36» → «Бухар Жырау». Узлу графа нужна короткая
    подпись, иначе они наезжают друг на друга."""
    s = (full or "").strip()
    for city in ("Астана,", "Актобе,", "Актау,", "Атырау,", "Костанай,"):
        if s.startswith(city):
            rest = s[len(city):].strip()
            s = rest if city == "Астана," else city.rstrip(",")
            break
    for p in ("ул.", "мкр", "микрорайон"):
        if s.startswith(p):
            s = s[len(p):].strip()
    # Скобки: короткое уточнение — это и есть народное имя филиала («EXPO»),
    # длинное — историческая справка («бывш. Чубары»), её выкидываем.
    m = re.search(r"\(([^)]*)\)", s)
    if m:
        inner = m.group(1).strip()
        s = inner if len(inner) <= 6 else s[:m.start()].strip()
    # Номер дома в подписи узла не нужен: «Бухар Жырау 36» → «Бухар Жырау».
    parts = s.split()
    if len(parts) > 1 and re.fullmatch(r"\d+[А-Яа-яA-Za-z]?", parts[-1]):
        s = " ".join(parts[:-1])
    return s[:18] or (full or "Филиал")[:18]


# Журналы ведут только филиалы Астаны — так решил владелец. Регионы остаются в
# выручке и накладных, но чистоту, температуру и остатки не сдают.
JOURNAL_CITY = "Астана"
# Оба на Алпамыс 61: заготовочный цех и распредцентр. Это не гостевые точки —
# зала и санузла для гостей там нет, а в знаменателе «сдано N из M» они
# завышали план на две трети слота.
JOURNAL_SKIP = ("(КЦ)", "(ЦЕХ)")


def in_journals(full: str) -> bool:
    s = full or ""
    return s.startswith(JOURNAL_CITY) and not any(m in s for m in JOURNAL_SKIP)


# В iiko названия записаны казахскими буквами («Бейбітшілік» через «і»), а ищут
# их русской раскладкой («бейбитшилик»). Для строкового сравнения это разные
# символы, и филиал просто не находился.
_KZ_FOLD = str.maketrans({
    "і": "и", "ө": "о", "ә": "а", "ғ": "г", "қ": "к",
    "ң": "н", "ұ": "у", "ү": "у", "һ": "х", "ї": "и",
})


def fold(s: str) -> str:
    return (s or "").lower().translate(_KZ_FOLD)


def find_branch(orgs: List[Dict[str, Any]], query: str) -> List[Dict[str, Any]]:
    """Филиалы, подходящие под обрывок названия, независимо от раскладки."""
    q = fold(query).strip()
    if not q:
        return []
    return [r for r in orgs if q in fold(r.get("organization_name"))]


def _payload(row) -> Dict[str, Any]:
    try:
        return json.loads(row.get("payload") or "{}")
    except Exception:
        return {}


def build_board(uid: int, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Состояние сети на текущий час: филиалы, что сдано, где дыры."""
    import api  # ленивый импорт: избегаем цикла на загрузке
    now = now or api.local_now()
    day = now.date().isoformat()

    rests = [r for r in api.get_restaurants(uid)     # владелец — все свои, управляющий — свои
             if in_journals(r.get("organization_name"))]
    orgs = {r["organization_id"]: r for r in rests}
    mgrs = {m["organization_id"]: m for m in get_branch_managers()}
    entries = [e for e in get_journal_entries(day) if e["organization_id"] in orgs]

    due = J.due_items(now)                            # [(kind, zone, slot)]
    due_n = len(due)

    # штрафы за 7 дней: сколько положенных позиций не сдано
    since = (now.date() - timedelta(days=7)).isoformat()
    past = [e for e in get_journal_range(since, day) if e["organization_id"] in orgs]
    full_day = len(J.due_items(now.replace(hour=23, minute=59)))
    done_by_day: Dict[str, int] = {}
    for e in past:
        if e["day"] != day:                           # текущий день ещё не закончился
            done_by_day[f'{e["organization_id"]}|{e["day"]}'] = \
                done_by_day.get(f'{e["organization_id"]}|{e["day"]}', 0) + 1
    days_past = sorted({e["day"] for e in past if e["day"] != day})

    branches, gaps = [], []
    for org_id, r in orgs.items():
        full = r.get("organization_name") or "Филиал"
        mine = [e for e in entries if e["organization_id"] == org_id]
        have = {(e["kind"], e["zone"], e["slot"]) for e in mine if e["status"] == "ok"}
        done = sum(1 for d in due if d in have)

        mgr = mgrs.get(org_id)
        connected = bool(mgr)

        # температура: последний сданный замер и его нарушения
        temp_rows = {e["slot"]: _payload(e) for e in mine if e["kind"] == "temp"}
        alarms: List[Dict[str, Any]] = []
        for slot, vals in temp_rows.items():
            for a in J.temp_alarms(vals or {}):
                a["slot"] = slot
                alarms.append(a)

        if not connected:
            st = "off"
        elif alarms:
            st = "bad"
        elif done >= due_n:
            st = "ok"
        elif done == 0:
            st = "bad"
        else:
            st = "part"

        pen = 0
        for d in days_past:
            pen += J.penalty_for(full_day - done_by_day.get(f"{org_id}|{d}", 0))

        clean: Dict[str, Dict[str, str]] = {}
        for kind, zone, slot in due:
            if kind != "clean":
                continue
            clean.setdefault(slot, {})[zone] = "ok" if ("clean", zone, slot) in have else "none"

        branches.append({
            "org": org_id, "n": short_name(full), "full": full,
            "done": done, "of": due_n, "st": st,
            "penalty": pen, "who": (f"@{mgr.get('name')}" if mgr and mgr.get("name") else None),
            "clean": clean,
            "temp": {s: v for s, v in temp_rows.items()},
            "temp_alarms": alarms,
            "stock": ("stock", "", J.STOCK_SLOT) in have,
            # уже введённые остатки — форма подставит их, чтобы не набивать заново
            "stock_counts": next((_payload(e) for e in mine if e["kind"] == "stock"), {}),
        })

        # дыры — то, ради чего экран открывают
        if not connected:
            gaps.append({"b": short_name(full), "st": "off",
                         "what": "Управляющий не подключён — ссылка исчерпана",
                         "since": "—"})
            continue
        for a in alarms:
            sign = "+" if a["value"] > 0 else ""
            gaps.append({"b": short_name(full), "st": "bad",
                         "what": f'{a["title"]}: {sign}{a["value"]:g} °C при норме {a["norm"]}',
                         "since": f'с {a["slot"]}'})
        for kind, zone, slot in due:
            if (kind, zone, slot) in have:
                continue
            if kind == "clean":
                what = f'Чистота · {J.ZONES[zone]["title"]}, слот {slot} — не сдана'
            elif kind == "temp":
                what = f"Температура · замер {slot} — не сдан"
            else:
                what = "Остатки не сданы"
            gaps.append({"b": short_name(full), "st": "part", "what": what,
                         "since": f"с {slot}"})

    rank = {"bad": 0, "off": 1, "part": 2}
    gaps.sort(key=lambda g: rank.get(g["st"], 3))

    slots = []
    for slot in sorted(set(J.CLEAN_SLOTS + J.TEMP_SLOTS), key=lambda s: J._hm(s)):
        items = [d for d in J.due_items(now.replace(hour=23, minute=59)) if d[2] == slot]
        of = len(items) * len(orgs)
        got = sum(1 for e in entries
                  if (e["kind"], e["zone"], e["slot"]) in {(i[0], i[1], i[2]) for i in items}
                  and e["status"] == "ok")
        is_due = J.slot_is_due(slot, now)
        what = []
        if slot in J.CLEAN_SLOTS:
            what.append("Чистота · зал, кухня, санузел")
        if slot in J.TEMP_SLOTS:
            what.append(f"Температура · {len(J.CAMERAS)} камер")
        slots.append({
            "t": slot, "what": " + ".join(what), "done": got, "of": of,
            "state": ("wait" if not is_due else ("done" if got >= of else "part")),
            "now": False,          # проставляется ниже: «сейчас» = последний наступивший
        })
    # «сейчас» — последний наступивший слот
    live = [s for s in slots if J.slot_is_due(s["t"], now)]
    for s in slots:
        s["now"] = bool(live) and s["t"] == live[-1]["t"]

    return {
        "day": day,
        "now": now.strftime("%H:%M"),
        "date_label": f"{_RU_DOW[now.weekday()]}, {now.day} {_RU_MON[now.month - 1]}",
        "due_n": due_n,
        "slots": slots,
        "branches": branches,
        "gaps": gaps,
        "cameras": J.CAMERAS,
        "norms": J.NORMS,
        "zones": {k: {"title": v["title"], "emoji": v["emoji"]} for k, v in J.ZONES.items()},
        "stock_places": [{"key": p["key"], "title": p["title"], "items": p["items"]}
                         for p in J.STOCK_PLACES],
    }


@router.get("/api/journals/data/{token}")
async def journals_data(token: str):
    uid = _uid_from_token(token)
    if not uid:
        return JSONResponse({"error": "bad token"}, status_code=404)
    try:
        return JSONResponse(build_board(uid))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/journals/stock/{token}")
async def journals_stock(token: str, body: Dict[str, Any]):
    """Остатки вводятся на сайте: в чате 9 мест по 25 позиций никто не заполнит."""
    uid = _uid_from_token(token)
    if not uid:
        return JSONResponse({"error": "bad token"}, status_code=404)
    import api
    org = str(body.get("org") or "")
    allowed = {r["organization_id"] for r in api.get_restaurants(uid)}
    if org not in allowed:
        return JSONResponse({"error": "нет доступа к филиалу"}, status_code=403)
    day = api.local_now().date().isoformat()
    save_journal_entry(org, "stock", "", J.STOCK_SLOT, day, uid,
                       json.dumps(body.get("counts") or {}, ensure_ascii=False))
    return JSONResponse({"ok": True})


HELP = [
    "просто текст          — уйдёт всем управляющим в Telegram",
    "/кто                  — кто подключён к филиалам",
    "/серверы [применить]  — свой сервер у каждого филиала",
    "/напомнить            — повторить задание текущего слота тем, кто не сдал",
    "/филиал <часть> <текст> — написать одному филиалу",
    "/оживить <часть>      — сбросить счётчик исчерпанной ссылки",
    "/помощь               — этот список",
]


def _owner_orgs(uid: int) -> List[Dict[str, Any]]:
    """Филиалы, которыми человек распоряжается в консоли.

    Берём тот же источник, что и панель — get_restaurants(uid). Через
    get_all_restaurants() нельзя: она схлопывает дубли по организации, а на
    проде одна и та же точка заведена под несколькими аккаунтами, и владелец
    в отфильтрованном списке просто не находился.

    Область действия команд равна тому, что человек и так видит на экране:
    написать можно только управляющим своих филиалов.
    """
    import api
    return [r for r in api.get_restaurants(int(uid))
            if in_journals(r.get("organization_name"))]


def _managers_of(orgs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    keys = {r["organization_id"] for r in orgs}
    return [m for m in get_branch_managers() if m["organization_id"] in keys]


@router.post("/api/journals/say/{token}")
async def journals_say(token: str, body: Dict[str, Any]):
    """Командная строка панели: свободный текст — рассылка, «/» — команда.

    Пишет только владелец: у управляющего в restaurants своих строк нет, и
    список филиалов для него окажется пустым.
    """
    uid = _uid_from_token(token)
    if not uid:
        return JSONResponse({"error": "bad token"}, status_code=404)
    import api
    from database import set_app_state

    text = (body.get("text") or "").strip()
    if not text:
        return JSONResponse({"log": []})
    orgs = _owner_orgs(uid)
    if not orgs:
        # Показываем, что именно увидел сервер: «доступ запрещён» без цифр
        # заставляет гадать, а на проде и в копии базы данные разные.
        allr = api.get_all_restaurants()
        mine = [r for r in allr if str(r.get("telegram_id")) == str(uid)]
        return JSONResponse({"log": [
            ["err", "Команды доступны владельцу сети."],
            ["info", f"uid={uid} · всего точек {len(allr)} · ваших {len(mine)} · "
                     f"из них в журналах {sum(1 for r in mine if in_journals(r.get('organization_name')))}"],
        ]})

    by_org = {r["organization_id"]: r for r in orgs}
    mgrs = _managers_of(orgs)
    log: List[List[str]] = []

    async def send(tg_id: int, msg: str, kb: Optional[dict] = None) -> bool:
        try:
            await api._tg_send(int(tg_id), msg, kb)
            return True
        except Exception as e:
            log.append(["err", f"{tg_id}: {e!r}"[:160]])
            return False

    if not text.startswith("/"):
        if not mgrs:
            return JSONResponse({"log": [["err", "Ни один управляющий не подключён — писать некому."]]})
        # Без шапки: владелец пишет живым людям, а не рассылает циркуляр.
        # Приписка «Сообщение от руководства» к каждой строчке только мешает.
        head = ""
        sent = 0
        # Один человек может вести два филиала — шлём ему ОДНО сообщение,
        # а не по копии на каждый адрес.
        seen: set = set()
        for m in mgrs:
            tg = int(m["telegram_id"])
            if tg in seen:
                continue
            seen.add(tg)
            where = ", ".join(short_name(by_org[x["organization_id"]].get("organization_name"))
                              for x in mgrs if int(x["telegram_id"]) == tg)
            if await send(tg, head + _esc(text)):
                sent += 1
                log.append(["ok", f'→ {where} · @{m.get("name") or tg}'])
        log.append(["info", f"Отправлено получателям: {sent}"])
        return JSONResponse({"log": log})

    parts = text.split(maxsplit=1)
    cmd = parts[0].lower().lstrip("/")
    rest = parts[1].strip() if len(parts) > 1 else ""

    if cmd in ("помощь", "help", "?"):
        return JSONResponse({"log": [["info", h] for h in HELP]})

    if cmd in ("серверы", "servers"):
        # Серверы касаются ВСЕХ филиалов, а не только тех, что ведут журналы:
        # регионы точно так же читают отстающий центральный.
        orgs = api.get_restaurants(int(uid))
        # Каждый филиал должен читать свой сервер: центральный отстаёт в течение
        # дня и путает смены соседних филиалов (одна концепция на двоих).
        from datetime import timedelta as _td
        from database import set_restaurant_server
        apply = rest.strip().lower() in ("применить", "apply", "да")
        if not apply:
            for r in sorted(orgs, key=lambda x: x.get("organization_name") or ""):
                host = (r.get("iiko_server") or "").replace("https://", "").split(".")[0]
                mark = "ok" if host and host != api.CENTRAL_HOST else "err"
                log.append([mark, f'{short_name(r.get("organization_name")):22} {host or "—"}'])
            log.append(["info", "«/серверы применить» — определить и записать свои серверы"])
            return JSONResponse({"log": log})

        day = (api.local_now().date() - _td(days=1)).isoformat()
        seed = orgs[0]
        res = await api.discover_branch_servers(
            seed["api_login"], seed.get("api_password"), day)
        found = res["found"]
        saved = 0
        for r in orgs:
            name = r.get("organization_name") or ""
            host = found.get(name)
            if not host:
                log.append(["err", f"{short_name(name):22} сервер не найден — остаётся центральный"])
                continue
            n = set_restaurant_server(r["organization_id"], api.branch_server_url(host))
            saved += 1
            log.append(["ok", f"{short_name(name):22} → {host}  (строк {n})"])
        for h in res["idle"]:
            log.append(["info", f"{h} — продаж нет, не привязан"])
        for h, why in res["failed"].items():
            log.append(["info", f"{h} — {why}"])
        log.append(["info", f"записано филиалов: {saved} из {len(orgs)}"])
        return JSONResponse({"log": log})

    if cmd in ("кто", "who"):
        for r in orgs:
            who = [m for m in mgrs if m["organization_id"] == r["organization_id"]]
            name = short_name(r.get("organization_name"))
            if who:
                log.append(["ok", f'{name} — ' + ", ".join(
                    f'@{m.get("name") or m["telegram_id"]}' for m in who)])
            else:
                log.append(["err", f"{name} — управляющий не подключён"])
        return JSONResponse({"log": log})

    if cmd in ("оживить", "ozhivit", "reset"):
        if not rest:
            return JSONResponse({"log": [["err", "Укажите филиал: /оживить кажымукана"]]})
        hit = find_branch(orgs, rest)
        if len(hit) != 1:
            return JSONResponse({"log": [["err",
                "Не нашёл филиал" if not hit else
                "Подошло несколько: " + ", ".join(short_name(h.get("organization_name")) for h in hit)]]})
        set_app_state(f'invite_used:{hit[0]["organization_id"]}', "")
        return JSONResponse({"log": [
            ["ok", f'{short_name(hit[0].get("organization_name"))} — счётчик сброшен'],
            ["info", api.invite_link_for(hit[0]["organization_id"])]]})

    if cmd in ("филиал", "branch"):
        bits = rest.split(maxsplit=1)
        if len(bits) < 2:
            return JSONResponse({"log": [["err", "Формат: /филиал кажымукана текст сообщения"]]})
        hit = find_branch(orgs, bits[0])
        if len(hit) != 1:
            return JSONResponse({"log": [["err",
                "Не нашёл филиал" if not hit else
                "Подошло несколько: " + ", ".join(short_name(h.get("organization_name")) for h in hit)]]})
        who = [m for m in mgrs if m["organization_id"] == hit[0]["organization_id"]]
        if not who:
            return JSONResponse({"log": [["err", "К этому филиалу никто не подключён"]]})
        for m in who:
            await send(m["telegram_id"], "📣 <b>Сообщение от руководства</b>\n\n" + _esc(bits[1]))
        log.append(["ok", f'{short_name(hit[0].get("organization_name"))} — отправлено'])
        return JSONResponse({"log": log})

    if cmd in ("напомнить", "remind"):
        now = api.local_now()
        due = J.due_items(now)
        if not due:
            return JSONResponse({"log": [["info", "Сейчас нечего напоминать — слоты ещё не наступили."]]})
        slot = max((d[2] for d in due), key=lambda s: J._hm(s))
        day = now.date().isoformat()
        entries = get_journal_entries(day)
        n = 0
        for m in mgrs:
            org = m["organization_id"]
            have = {(e["kind"], e["zone"], e["slot"]) for e in entries
                    if e["organization_id"] == org and e["status"] == "ok"}
            missing = [d for d in due if d[2] == slot and d not in have]
            if not missing:
                continue
            if await send(m["telegram_id"], api.journal_slot_message(slot, missing),
                          api._journal_kb(slot, missing, org)):
                n += 1
                log.append(["ok", f"→ {short_name(by_org[org].get('organization_name'))}"
                                  f" · не сдано {len(missing)}"])
        log.append(["info", f"Напоминание по слоту {slot}: {n} получателей"])
        return JSONResponse({"log": log})

    return JSONResponse({"log": [["err", f"Неизвестная команда «{cmd}». /помощь — список."]]})


@router.get("/j/{token}")
async def journals_page(token: str):
    if not _uid_from_token(token):
        return HTMLResponse("<h1>Ссылка недействительна</h1>", status_code=404)
    return HTMLResponse(PAGE.replace("__TOKEN__", token))


PAGE = _read_site("journals.html")

