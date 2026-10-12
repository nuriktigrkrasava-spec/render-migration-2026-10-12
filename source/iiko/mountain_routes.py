"""«Гора» — задачи филиалам, баллы и уровень, плюс чек-листы из чата.

Доступ по личной подписанной ссылке, как у виджета и журналов: `/m/{token}`,
где токен = uid + подпись на общем ENCRYPTION_KEY. Владелец видит все свои
филиалы, управляющий — только свой. Отдельного входа не заводим.

Роли:
  • владелец   — ему принадлежат рестораны (`restaurants.telegram_id`);
                 ТОЛЬКО он ставит задачи (ИИ предлагает, он подтверждает);
  • управляющий — привязан к филиалу в `branch_managers`; задачу может закрыть,
                 создать — нет.

Баллы (согласовано с владельцем):
  задача  — быстро 10, средне 25, долго 50;
  чек-лист — +1 за сданный слот, −1 за пропущенный.
Уровень на горе — 40 отметок по 25 баллов: три закрытые задачи не поднимают на
вершину, подъём должен быть виден месяцами, а штрафы тянут вниз.
"""
import base64
import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import scoring as S
from database import (
    # local_now — стенные часы ресторана (UTC+5). Голый datetime.now() на Render
    # давал UTC: и время сдачи, и «какой сегодня день», и «сколько слотов уже
    # прошло» уезжали на пять часов, а вместе с ними и оценка филиала.
    local_now,
    get_restaurants, get_owned_restaurants, get_branch_managers,
    add_branch_task, close_branch_task, get_branch_tasks, TASK_POINTS,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# ── Гора: 40 отметок по 10 баллов, вершина — 400 ─────────────────────────────
# Считано по потолку месяца: идеальные чек-листы дают 360 баллов, задачи ещё
# 100–400. При прежней вершине в 1000 её не достигал никто и никогда — цель, до
# которой нельзя дойти, азарта не создаёт. 400 берётся месяцем очень хорошей
# работы, но не даром.
STEP_POINTS = int(os.getenv("MOUNTAIN_STEP", "10"))
STEPS = int(os.getenv("MOUNTAIN_STEPS", "40"))

# Путь наверх — не сплошная линейка, а участки с именами: видно, где ты и что
# следующее. Порог — в баллах, подъём идёт слева направо змейкой (см. фронт).
STAGES = [
    (0,   "Подножие"),
    (80,  "Тропа"),
    (160, "Первый лагерь"),
    (240, "Скальный пояс"),
    (320, "Штурмовой лагерь"),
    (400, "Вершина"),
]


def _stage(points: int) -> Dict[str, Any]:
    """Участок пути и сколько до следующего — чтобы подъём читался этапами."""
    points = max(0, points)          # минус по баллам — это подножие, а не вершина
    cur = STAGES[0]
    nxt = STAGES[1] if len(STAGES) > 1 else None
    for i, (need, name) in enumerate(STAGES):
        if points >= need:
            cur = (need, name)
            nxt = STAGES[i + 1] if i + 1 < len(STAGES) else None
    return {"name": cur[1], "from": cur[0],
            "next": nxt[1] if nxt else None,
            "to_next": max(0, nxt[0] - points) if nxt else 0,
            "index": [n for n, _ in STAGES].index(cur[0]),
            "count": len(STAGES)}
CHECKLIST_POINT = 1        # сданный слот
CHECKLIST_PENALTY = -1     # пропущенный


def _secret() -> bytes:
    value = os.getenv("WIDGET_SECRET") or os.getenv("ENCRYPTION_KEY")
    if not value:
        raise RuntimeError("Configure WIDGET_SECRET or ENCRYPTION_KEY")
    return value.encode()


def _sign(uid: int) -> str:
    mac = hmac.new(_secret(), f"mountain:{uid}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")[:20]


def token_for(uid: int) -> str:
    """Ссылка ВЛАДЕЛЬЦА: подписанный telegram id. Одинакова локально и на
    проде — общий ENCRYPTION_KEY, без обращения к базе."""
    return f"{uid}.{_sign(uid)}"


def _sign_org(org_id: str) -> str:
    mac = hmac.new(_secret(), f"mountain-org:{org_id}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")[:20]


def token_for_org(org_id: str) -> str:
    """Ссылка ФИЛИАЛА — подписанный organization_id.

    Раньше ссылка выдавалась человеку: подписывался telegram id управляющего.
    Из-за этого филиал без управляющего в боте ссылку получить не мог (семь из
    двадцати трёх), а права на сайте зависели от таблиц бота. Сайт — это сайт:
    ссылка привязана к филиалу, телеграм для неё не нужен вообще.
    """
    return f"b{org_id}.{_sign_org(org_id)}"


def org_from_token(token: str) -> Optional[str]:
    """organization_id из ссылки филиала. None — это не ссылка филиала."""
    if not token or not token.startswith("b") or "." not in token:
        return None
    org, sig = token[1:].rsplit(".", 1)
    if not org:
        return None
    return org if hmac.compare_digest(sig.encode("utf-8", "ignore"),
                                      _sign_org(org).encode()) else None


def _sign_demo(code: str) -> str:
    mac = hmac.new(_secret(), f"mountain-demo:{code}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")[:20]


def token_for_demo(code: str) -> str:
    """Демо-ссылка: подписанный код показа. Ведёт на выдуманную сеть и никуда
    больше — реальных филиалов этот скоуп не знает вовсе."""
    return f"d{code}.{_sign_demo(code)}"


def demo_from_token(token: str) -> Optional[str]:
    """Код показа из демо-ссылки. None — это не демо-ссылка."""
    if not token or not token.startswith("d") or "." not in token:
        return None
    code, sig = token[1:].rsplit(".", 1)
    if not code:
        return None
    return code if hmac.compare_digest(sig.encode("utf-8", "ignore"),
                                       _sign_demo(code).encode()) else None


def uid_from_token(token: str) -> Optional[int]:
    if not token or "." not in token:
        return None
    uid_s, sig = token.rsplit(".", 1)
    try:
        uid = int(uid_s)
    except ValueError:
        return None
    return uid if hmac.compare_digest(sig.encode("utf-8", "ignore"), _sign(uid).encode()) else None


# Какие филиалы участвуют. Служебные организации (КЦ, ЦЕХ, ОФИС, РОЯЛТИ) — не
# точки продаж: задачи и баллы по ним бессмысленны, а список раздувают до 30
# строк. Список настраивается: MOUNTAIN_ONLY — что оставить, MOUNTAIN_SKIP — что
# выкинуть; оба ищутся в названии филиала без учёта регистра.
ONLY = [x.strip().lower() for x in os.getenv("MOUNTAIN_ONLY", "астана").split(",") if x.strip()]
SKIP = [x.strip().lower() for x in os.getenv("MOUNTAIN_SKIP", "кц,цех,офис,роялти").split(",") if x.strip()]


def _is_branch(name: str) -> bool:
    n = (name or "").lower()
    if ONLY and not any(o in n for o in ONLY):
        return False
    # «кц»/«цех» проверяем как отдельные слова: иначе «Кцентр» и «Цеховая» тоже отпадут
    words = set(n.replace("(", " ").replace(")", " ").replace(",", " ").split())
    return not any(sk in words or (len(sk) > 4 and sk in n) for sk in SKIP)


def visible_branches(rows, name_of):
    """Отсев служебных организаций — но НИКОГДА не до пустоты.

    MOUNTAIN_ONLY=астана заведён под одну сеть: у неё двадцать три записи, и
    «КЦ»/«ЦЕХ»/«ОФИС» из списка убирать надо. Тот же фильтр молча выбрасывал ВСЕ
    филиалы любого, кто зарегистрировался не в Астане: сайт открывался пустым,
    а понять почему было нельзя — ни ошибки, ни подсказки, просто ничего.

    Правило простое: если после отсева не осталось ничего, отсев не применяем.
    Показать лишнюю строку — мелкая неприятность, показать пустой продукт —
    потерянный клиент.
    """
    rows = list(rows or [])
    kept = [r for r in rows if _is_branch(name_of(r) or "")]
    return kept if kept else rows


def scope_of_token(token: str) -> Dict[str, Any]:
    """Что открывает эта ссылка. Единственная точка разбора доступа на сайте.

      • ссылка ФИЛИАЛА (`b{org}.{подпись}`) — ровно один филиал. Телеграм для
        неё не нужен: филиалу не обязательно иметь управляющего в боте;
      • ссылка ВЛАДЕЛЬЦА (`{uid}.{подпись}`) — вся его сеть.

    `org` — чем ограничен доступ (у владельца None), `uid` — от чьего имени
    ходить в iiko (у ссылки филиала это владелец филиала: своих кредов у
    управляющего нет, см. грабли из 234.md).
    """
    code = demo_from_token(token)
    if code:
        # Демо. uid = 0 намеренно: от его имени в iiko не сходить — у нуля нет
        # ни филиалов, ни кредов, — а филиалы отдаём выдуманные. Попытка
        # подставить чужой org упрётся в _get_org_id: он пускает только свои.
        import demo as _demo
        from database import touch_demo_link
        try:
            touch_demo_link(code)
        except Exception as e:
            logger.warning(f"[demo] счётчик {code}: {e!r}")
        return {"role": "owner", "org": None, "uid": 0, "valid": True,
                "demo": True, "demo_code": code,
                "branches": [dict(b) for b in _demo.BRANCHES]}

    org = org_from_token(token)
    if org:
        from database import get_db
        conn = get_db()
        row = conn.execute("SELECT organization_name FROM restaurants "
                           "WHERE organization_id = ? ORDER BY id LIMIT 1", (org,)).fetchone()
        conn.close()
        if not row:
            return {"role": None, "branches": [], "org": None, "uid": 0, "valid": False}
        return {"role": "manager", "org": org, "uid": _owner_of(org), "valid": True,
                "branches": [{"organization_id": org, "name": row[0] or "Филиал"}]}
    uid = uid_from_token(token)
    if not uid:
        # подпись не сошлась — ссылки не существует
        return {"role": None, "branches": [], "org": None, "uid": 0, "valid": False}
    sc = _scope(uid)
    # valid = подпись верна. Роли при этом может не быть: uid, у которого нет
    # ни своих филиалов, ни назначения управляющим. Это «прав нет» (403), а не
    # «ссылки нет» (404) — счётчик прочтений отличает одно от другого.
    sc["org"], sc["uid"], sc["valid"] = None, uid, True
    return sc


def _scope(uid: int) -> Dict[str, Any]:
    """Что видит этот uid: владелец — свои филиалы, управляющий — свой."""
    # ВЛАДЕНИЕ, а не доступ: get_restaurants отдаёт и филиалы, где человек лишь
    # управляющий, и по ней роль определять нельзя.
    #
    # Правило «владелец — чья регистрация ПЕРВАЯ» пробовали и откатили: на проде
    # один филиал заведён несколько раз под разными telegram id, и первой
    # оказалась не запись владельца — он терял свою сеть целиком. А главное,
    # нужда в таком правиле отпала: доступ на сайте определяет ТИП ССЫЛКИ
    # (ссылка филиала открывает один филиал), а не то, что творится в таблицах
    # бота. См. scope_of_token.
    own = get_owned_restaurants(uid) or []
    if own:
        return {"role": "owner", "branches": [
            {"organization_id": r["organization_id"],
             "name": r.get("organization_name") or "Филиал"}
            for r in visible_branches(own, lambda r: r.get("organization_name"))]}
    mine = [m for m in get_branch_managers() if int(m.get("telegram_id") or 0) == uid]
    if not mine:
        return {"role": None, "branches": []}
    # имя филиала берём из справочника ресторанов: у записи управляющего лежит
    # его собственное имя, и без этого филиал подписывался «Управляющий такой-то»
    from database import get_db
    conn = get_db()
    names = {r[0]: r[1] for r in conn.execute(
        "SELECT organization_id, organization_name FROM restaurants").fetchall()}
    conn.close()
    return {"role": "manager", "branches": [
        {"organization_id": m["organization_id"],
         "name": names.get(m["organization_id"]) or m.get("name") or "Филиал"} for m in mine]}


def _db_checklist_from() -> str:
    """С какого дня чек-листы идут в зачёт (пусто — не идут)."""
    import database as _db
    return _db.CHECKLIST_FROM


def _month_start() -> str:
    now = local_now()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()


def _level(points: int) -> Dict[str, Any]:
    """Баллы → отметка на горе. Ниже нуля не опускаемся, выше вершины не растём."""
    step = max(0, min(STEPS, points // STEP_POINTS))
    return {"step": step, "steps": STEPS,
            "height_pct": round(step / STEPS * 100, 1),
            "summit": STEPS * STEP_POINTS,
            "stage": _stage(points),
            "to_next": max(0, (step + 1) * STEP_POINTS - points) if step < STEPS else 0}


def _checklist_points_own(prog: Dict[str, Dict[str, int]], org: str) -> Dict[str, int]:
    """Баллы за чек-листы по НАШИМ сдачам: сдал слот +1, пропустил −1.
    Доску из чата больше не спрашиваем — бота не будет."""
    p = prog.get(org) or {}
    done, missed = int(p.get("done") or 0), abs(int(p.get("missed") or 0))
    return {"done": done, "missed": -missed, "expect": int(p.get("expect") or 0),
            "points": done * CHECKLIST_POINT - missed * abs(CHECKLIST_PENALTY)}


# Показатели для ОЦЕНКИ филиала доска собирает сама. Раньше она читала кэш
# ассистента: пока никто не нажал «Обновить», критериев не было, вес уходил на
# двух выживших — и филиал получал 100 из 100 по выручке и списаниям. Кэш на
# час: сбор по девяти филиалам идёт десятки секунд, на каждый заход его не гоняем.
SCORE_TTL = timedelta(hours=1)
_score_key = lambda uid: "mountain-score:%d" % int(uid)


async def _collect_score_signals(uid: int, branches: List[Dict[str, str]]) -> Dict[str, Dict[str, Any]]:
    """Динамика выручки и заказов, опоздания доставок и опоздания сотрудников —
    по каждому филиалу. Сбой одного филиала не роняет остальные."""
    import asyncio as _aio
    import api
    month_from = _month_start()[:10]
    today = local_now().date().isoformat()
    sem = _aio.Semaphore(4)

    async def one(b: Dict[str, str]):
        org = b["organization_id"]
        # Креды — ПО ФИЛИАЛУ: у управляющего своих ресторанов нет, и запрос от
        # его имени подставил бы демо-клиента (грабли из 234.md).
        owner = api._owner_user_of(org)
        if not owner:
            return b["name"], None
        sig: Dict[str, Any] = {}
        async with sem:
            try:
                sig["changes"] = await api._week_over_week(org, owner)
            except Exception as e:
                logger.warning(f"[score] дельты {b['name']}: {e!r}")
            try:
                sig["late"] = api._late_signal(
                    await api.late_deliveries(org, "month", None, None, 1, owner))
            except Exception as e:
                logger.warning(f"[score] опоздания заказов {b['name']}: {e!r}")
            try:
                sig["rating"] = api._rating_signal(await api.branch_reviews(org, owner))
            except Exception as e:
                logger.warning(f"[score] рейтинги {b['name']}: {e!r}")
            try:
                sig["shifts"] = api._shifts_signal(
                    await api.cashshifts_pending(org, 30, owner))
            except Exception as e:
                logger.warning(f"[score] непринятые смены {b['name']}: {e!r}")
            try:
                sig["plan"] = await api._plan_exec_signal(org, owner)
            except Exception as e:
                logger.warning(f"[score] выполнение плана {b['name']}: {e!r}")
            try:
                # Заодно наполняет staff_late в базе — экран и уведомления
                # читают уже посчитанное, а не ходят в iiko второй раз.
                sl = await api._staff_late_for(org, org, month_from, today, owner)
                # Филиал, который не задал свой старт смены, судим по общему
                # 09:00/16:00 — а он почти всегда не тот (Алиева открывается в
                # 11:00). Такой критерий не «ноль», а молчание: иначе оценка
                # падает не за опоздания, а за ненастроенность.
                if sl and sl["configured"] and sl["summary"]["total"]:
                    sig["staff_late"] = sl["summary"]
            except Exception as e:
                logger.warning(f"[score] опоздания сотрудников {b['name']}: {e!r}")
        return b["name"], sig

    out: Dict[str, Dict[str, Any]] = {}
    for name, sig in await _aio.gather(*(one(b) for b in branches)):
        if sig is not None:
            out[name] = sig
    api._set_cached(_score_key(uid), out, ttl=SCORE_TTL)
    return out


def _signals_by_branch(uid: int) -> Dict[str, Dict[str, Any]]:
    """Готовые показатели из кэша. Пусто — значит сбор ещё не проходил."""
    import api
    return api._get_cached(_score_key(uid), ttl=SCORE_TTL) or {}


def _owner_of(org_id: str) -> int:
    """Кому принадлежит филиал. Нужен, чтобы управляющий видел рейтинг СВОЕЙ
    сети, а не всех клиентов платформы.

    Владелец — тот, кто зарегистрировал филиал ПЕРВЫМ. Порядок задан явно:
    один и тот же филиал бывает заведён несколько раз под разными telegram id
    (управляющему дали креды владельца, и он подключил всю сеть через бота), а
    без ORDER BY «первый» зависел бы от того, как SQLite сегодня вернёт строки.
    """
    from database import get_db
    conn = get_db()
    row = conn.execute("SELECT telegram_id FROM restaurants WHERE organization_id = ? "
                       "ORDER BY id LIMIT 1", (org_id,)).fetchone()
    conn.close()
    return int(row[0]) if row and row[0] else 0


def _owned_org_ids(uid: int) -> set:
    """Филиалы, где uid — ПЕРВАЯ регистрация, то есть настоящий владелец.

    Одним запросом, а не _owner_of по каждому: филиалов два десятка.
    """
    from database import get_db
    conn = get_db()
    rows = conn.execute(
        "SELECT organization_id, telegram_id FROM restaurants r1 WHERE id = "
        "(SELECT MIN(id) FROM restaurants r2 WHERE r2.organization_id = r1.organization_id)"
    ).fetchall()
    conn.close()
    return {r[0] for r in rows if int(r[1] or 0) == int(uid)}


def _all_branches(owner_uid: int) -> List[Dict[str, str]]:
    """Точки сети ОДНОГО владельца (без служебных организаций).

    Раньше здесь был SELECT по всей таблице restaurants — она многоарендная, и
    в рейтинг попал бы чужой клиент, у которого филиал назван «Астана…».
    Дедуплицируем по organization_id: одна точка бывает заведена под разными
    аккаунтами и иначе появлялась на горе дважды.
    """
    from database import get_db
    conn = get_db()
    rows = conn.execute("SELECT organization_id, organization_name FROM restaurants "
                        "WHERE telegram_id = ?", (int(owner_uid),)).fetchall()
    conn.close()
    seen: Dict[str, str] = {}
    for org, name in rows:
        if org and org not in seen and _is_branch(name or ""):
            seen[org] = name or ""
    return [{"organization_id": o, "name": n} for o, n in seen.items()]


def _network_ranking(owner_uid: int) -> List[Dict[str, Any]]:
    """Место каждого филиала сети по баллам месяца. Считается по базе, без iiko."""
    from database import checklist_progress
    branches = _all_branches(owner_uid)
    orgs = [b["organization_id"] for b in branches]
    tasks = get_branch_tasks(orgs, since=_month_start())
    now_hm = local_now().strftime("%H:%M")
    passed = sum(1 for sl in SLOTS if sl <= now_hm)
    prog = checklist_progress(orgs, _month_start()[:10],
                              local_now().date().isoformat(), slots_passed_today=passed)
    out = []
    for b in branches:
        org = b["organization_id"]
        done = [t for t in tasks if t["organization_id"] == org and t["status"] == "done"]
        ch = _checklist_points_own(prog, org)
        pts = sum(int(t["points"] or 0) for t in done) + ch["points"]
        out.append({**b, "points": pts, "level": _level(pts), "tasks_done": len(done)})
    out.sort(key=lambda r: -r["points"])
    for i, r in enumerate(out):
        r["place"] = i + 1
    return out


def _ranking_with_scores(ranking: List[Dict[str, Any]],
                         signals: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Дописывает в рейтинг оценку каждого филиала.

    Управляющему нужен именно этот срез: сравнить себя с другими одним
    взглядом. Разбор по критериям в рейтинге не даём — чужие слабые места
    его не касаются, да и владелец их видит у себя.
    """
    from database import checklist_progress
    orgs = [r["organization_id"] for r in ranking]
    now_hm = local_now().strftime("%H:%M")
    passed = sum(1 for sl in SLOTS if sl <= now_hm)
    prog = checklist_progress(orgs, _month_start()[:10],
                              local_now().date().isoformat(), slots_passed_today=passed)
    out = []
    for r in ranking:
        ch = _checklist_points_own(prog, r["organization_id"])
        sc = S.score_branch(signals.get(r["name"]), ch)
        out.append({**r, "score": sc["score"], "counted": sc.get("counted")})
    return out


def _board_for(uid: int, journals: Optional[Dict[str, Any]] = None,
               signals: Optional[Dict[str, Any]] = None,
               scope: Optional[Dict[str, Any]] = None,
               checklist_start: Optional[str] = None) -> Dict[str, Any]:
    """Доска по УЖЕ разобранному доступу.

    `scope` обязателен везде, где доступ пришёл ссылкой. Раньше сюда передавали
    только uid и роль считалась заново — а у ссылки ФИЛИАЛА uid принадлежит
    ВЛАДЕЛЬЦУ (своих кредов iiko у управляющего нет, см. scope_of_token). Роль
    выходила «owner», и управляющий получал доску владельца целиком: форму
    постановки задач, рассылку «отправить всем» и прогресс всех филиалов, — а
    сдача чек-листа с экрана пропадала, она рисуется только не-владельцу.
    Без scope (ночной пересчёт оценок) считаем от uid, как раньше.
    """
    sc = scope or _scope(uid)
    if not sc["role"]:
        return {"error": "нет доступа"}
    orgs = [b["organization_id"] for b in sc["branches"]]
    tasks = get_branch_tasks(orgs, since=_month_start())
    journals = journals or {}
    signals = _signals_by_branch(uid) if signals is None else signals
    from database import checklist_progress
    now_hm = local_now().strftime("%H:%M")
    passed = sum(1 for sl in SLOTS if sl <= now_hm)      # какие слоты уже прошли сегодня
    progress = checklist_progress(orgs, _month_start()[:10],
                                  local_now().date().isoformat(), slots_passed_today=passed,
                                  start=checklist_start)

    rows = []
    for b in sc["branches"]:
        org = b["organization_id"]
        mine = [t for t in tasks if t["organization_id"] == org]
        done = [t for t in mine if t["status"] == "done"]
        task_points = sum(int(t["points"] or 0) for t in done)
        ch = _checklist_points_own(progress, org)
        total = task_points + ch["points"]
        rows.append({
            **b,
            "tasks_open": [t for t in mine if t["status"] == "open"],
            "tasks_done": len(done),
            "task_points": task_points,
            "checklist": ch,
            "points": total,
            "level": _level(total),
            **S.score_branch(signals.get(b["name"]), ch),
        })
    rows.sort(key=lambda r: -r["points"])

    # Рейтинг сети виден ВСЕМ, включая управляющих: соревнование без чужих
    # результатов не работает. Задачи и чек-листы при этом остаются своими.
    owner_uid = uid if sc["role"] == "owner" else _owner_of(orgs[0] if orgs else "")
    ranking = _ranking_with_scores(_network_ranking(owner_uid), signals)
    return {"role": sc["role"], "month": local_now().strftime("%Y-%m"),
            "ranking": ranking,
            # true — показатели ещё собираются, оценка появится через минуту
            "score_pending": not signals,
            # Пусто — чек-листы не в зачёте: экран так и пишет, чтобы нули на
            # горе не читались как «все провалили».
            "checklist_from": checklist_start or _db_checklist_from(),
            "scale": {"tasks": TASK_POINTS, "checklist_done": CHECKLIST_POINT,
                      "checklist_missed": CHECKLIST_PENALTY,
                      "step_points": STEP_POINTS, "steps": STEPS},
            "branches": rows}


async def recompute_all_scores() -> int:
    """Ночной пересчёт оценок по всем владельцам сети. Кладёт снимок дня в
    branch_scores — из него потом растут вторничная средняя и динамика.

    Считаем по владельцам, а не по филиалам: кэш показателей ключуется на uid,
    и один проход на владельца греет его же доску.
    """
    from database import get_db, save_branch_score
    import api
    conn = get_db()
    uids = [int(r[0]) for r in conn.execute(
        "SELECT DISTINCT telegram_id FROM restaurants WHERE telegram_id IS NOT NULL").fetchall()]
    conn.close()
    day = local_now().date().isoformat()
    saved = 0
    for uid in uids:
        sc = _scope(uid)
        if sc["role"] != "owner" or not sc["branches"]:
            continue
        try:
            signals = await _collect_score_signals(uid, sc["branches"])
        except Exception as e:
            logger.warning(f"[scores] сбор для {uid}: {e!r}")
            continue
        board = _board_for(uid, signals=signals)
        for b in board.get("branches") or []:
            save_branch_score(b["organization_id"], day, b.get("score"),
                              b.get("counted") or 0, b.get("criteria"))
            saved += 1
    return saved


def weekly_score_lines(orgs: List[Dict[str, str]]) -> List[str]:
    """Строки вторничной сводки: средняя за 7 дней, место и куда движется.

    Отдельной функцией, чтобы её можно было проверить тестом, не поднимая
    ни телеграма, ни планировщика.
    """
    from database import branch_score_average
    ids = [b["organization_id"] for b in orgs]
    today = local_now().date()
    cur = branch_score_average(ids, (today - timedelta(days=7)).isoformat(), today.isoformat())
    prev = branch_score_average(ids, (today - timedelta(days=14)).isoformat(),
                                (today - timedelta(days=8)).isoformat())
    rows = [(b["name"], cur[b["organization_id"]]) for b in orgs if b["organization_id"] in cur]
    rows.sort(key=lambda r: -r[1]["avg"])
    out = []
    for i, (name, c) in enumerate(rows, 1):
        org = next(b["organization_id"] for b in orgs if b["name"] == name)
        was = (prev.get(org) or {}).get("avg")
        delta = "" if was is None else (f" ({c['avg'] - was:+d} к прошлой неделе)" if c["avg"] != was
                                        else " (без изменений)")
        out.append(f"{i}. <b>{name}</b> — {c['avg']} из 100{delta}")
    return out


@router.get("/api/mountain/scores/{token}")
async def mountain_scores(token: str, days: int = 30):
    """История оценок: график динамики на доске. Управляющему — только его филиал."""
    from database import get_branch_scores
    sc = scope_of_token(token)
    uid = sc["uid"]
    if not sc["valid"]:
        return JSONResponse({"error": "bad token"}, status_code=404)
    if not sc["role"]:
        return JSONResponse({"error": "нет доступа"}, status_code=403)
    orgs = [b["organization_id"] for b in sc["branches"]]
    today = local_now().date()
    hist = get_branch_scores(orgs, (today - timedelta(days=max(1, days))).isoformat(),
                             today.isoformat())
    from database import branch_score_average
    avg = branch_score_average(orgs, (today - timedelta(days=7)).isoformat(), today.isoformat())
    return JSONResponse({"branches": [
        {**b, "history": hist.get(b["organization_id"]) or [],
         "week_avg": (avg.get(b["organization_id"]) or {}).get("avg")}
        for b in sc["branches"]]})


@router.get("/api/mountain/whoami/{token}")
async def whoami(token: str):
    """Кто пришёл по ссылке. Лёгкий запрос: шапке сайта нужна только роль,
    гонять ради неё всю доску (задачи, чек-листы, рейтинг) незачем."""
    sc = scope_of_token(token)
    uid = sc["uid"]
    if not sc["valid"]:
        return JSONResponse({"role": None}, status_code=404)
    return JSONResponse({"role": sc["role"],
                         "branch": sc["branches"][0]["name"] if sc["branches"] else ""})


@router.get("/api/mountain/data/{token}")
async def mountain_data(token: str, refresh: int = 0):
    """Доска. Оценка считается из кэша показателей; если его нет — доска
    отдаётся сразу, а сбор уходит в фон (score_pending=true). Ждать минуту с
    белым экраном ради числа в кружке нельзя. refresh=1 — считать сейчас."""
    import asyncio as _aio
    sc = scope_of_token(token)
    uid = sc["uid"]
    if not sc["valid"]:
        return JSONResponse({"error": "bad token"}, status_code=404)
    if not sc["role"]:
        return JSONResponse({"error": "нет доступа"}, status_code=403)
    if sc.get("demo"):
        # Демо: показатели берём из выдуманной сети. В iiko не ходим вовсе —
        # у uid=0 нет ни филиалов, ни кредов, и фоновый сбор тут завис бы.
        import demo as _demo
        try:
            _demo.seed()
        except Exception as e:
            logger.warning(f"[demo] засев: {e!r}")
        # Чек-листы в демо ДОЛЖНЫ быть в зачёте: без даты старта прогресс
        # пустой, критерий не считается, и продукт выглядит недоделанным.
        board = _board_for(0, signals=_demo.signals(), scope=sc,
                           checklist_start=_demo.checklist_start())
        board["demo"] = True
        return JSONResponse(board)

    orgs = [b["organization_id"] for b in sc["branches"]]
    # Показатели ключуем на ВЛАДЕЛЬЦА сети, а не на того, кто открыл ссылку.
    # Управляющему нужны оценки чужих филиалов (сравнить себя одним взглядом),
    # а его собственный кэш знал бы только про его точку. Заодно ночной
    # пересчёт греет кэш сразу и для управляющих.
    owner_uid = uid if sc["role"] == "owner" else _owner_of(orgs[0] if orgs else "")
    network = _all_branches(owner_uid) if owner_uid else sc["branches"]
    signals = _signals_by_branch(owner_uid or uid)
    if not signals and network:
        if refresh:
            signals = await _collect_score_signals(owner_uid or uid, network)
        else:
            _aio.create_task(_collect_score_signals(owner_uid or uid, network))
    return JSONResponse(_board_for(uid, signals=signals, scope=sc))


class AssignPayload(BaseModel):
    tasks: List[Dict[str, Any]]


@router.post("/api/mountain/assign/{token}")
async def mountain_assign(token: str, payload: AssignPayload):
    """Владелец подтверждает предложенные задачи. Управляющему сюда нельзя."""
    sc = scope_of_token(token)
    uid = sc["uid"]
    if not sc["valid"]:
        return JSONResponse({"error": "bad token"}, status_code=404)
    if sc["role"] != "owner":
        return JSONResponse({"error": "задачи ставит только владелец"}, status_code=403)
    allowed = {b["organization_id"]: b["name"] for b in sc["branches"]}
    created = 0
    fresh: List[tuple] = []            # что поставили — чтобы догнать в телеграм
    for t in payload.tasks:
        org = str(t.get("organization_id") or "")
        title = str(t.get("title") or "").strip()
        if org not in allowed or not title:
            continue
        effort = t.get("effort") if t.get("effort") in TASK_POINTS else "средне"
        add_branch_task(org, allowed[org], title, detail=str(t.get("detail") or ""),
                        money=float(t.get("money") or 0), effort=effort,
                        task_key=str(t.get("task_key") or ""), created_by=uid)
        created += 1
        fresh.append((org, allowed[org], title, str(t.get("detail") or "")))
    notified = await _tasks_to_telegram(fresh, author=uid)
    return JSONResponse({"ok": True, "created": created, "telegram": notified})


async def _tasks_to_telegram(fresh: List[tuple], author: int) -> int:
    """Догоняет поставленные задачи в телеграме. Возвращает число писем.

    Без кнопок «Сделал / Не успел»: эта задача живёт на «Горе» и закрывается
    там же, а кнопка завела бы ВТОРУЮ задачу в другой таблице — человек закрыл
    бы её в чате, а на доске она осталась бы висеть.

    Телеграм тут дубль: задача уже записана, и молчащий бот не должен
    выглядеть как «задача не поставлена».
    """
    import api                                   # ленивый импорт: избегаем цикла
    from database import get_branch_managers
    if not fresh:
        return 0
    by_org: Dict[str, List[int]] = {}
    for m in get_branch_managers():
        tg = int(m.get("telegram_id") or 0)
        if tg and tg != author:                  # себе своё же не шлём
            by_org.setdefault(m["organization_id"], []).append(tg)
    sent = 0
    for org, branch, title, detail in fresh:
        body = (f"📌 <b>{title}</b>\n<i>{branch}</i>"
                + (f"\n\n{detail}" if detail else "")
                + "\n\nОтметить сделанной — на «Горе».")
        for tg in by_org.get(org, []):
            try:
                await api._tg_send(tg, body)
                sent += 1
            except Exception as e:
                logger.warning(f"[assign] телеграм {tg}: {e!r}")
    return sent


class ClosePayload(BaseModel):
    task_id: int


@router.post("/api/mountain/close/{token}")
async def mountain_close(token: str, payload: ClosePayload):
    """Закрыть задачу может и управляющий своего филиала, и владелец."""
    sc = scope_of_token(token)
    uid = sc["uid"]
    if not sc["valid"]:
        return JSONResponse({"error": "bad token"}, status_code=404)
    if not sc["role"]:
        return JSONResponse({"error": "нет доступа"}, status_code=403)
    orgs = {b["organization_id"] for b in sc["branches"]}
    task = next((t for t in get_branch_tasks(list(orgs)) if t["id"] == payload.task_id), None)
    if not task:
        return JSONResponse({"error": "задача не найдена"}, status_code=404)
    ok = close_branch_task(payload.task_id, done_by=uid)
    return JSONResponse({"ok": ok, "points": int(task["points"] or 0)})

# ── Акты разбора: П/Ф стейк → обрезки + нарезка ──────────────────────────────
# Экран живёт в той же вкладке и на тех же ссылках: владелец выбирает филиал,
# управляющий работает со своим. Цена входа берётся из накладных — руками её не
# вбивают, иначе себестоимость превращается в фантазию.
import acts as A


def _branch_or_403(sc: Dict[str, Any], org: Optional[str]) -> Optional[Dict[str, Any]]:
    """Филиал из уже разобранного доступа.

    Раньше принимал uid и сам звал _scope. Со ссылкой ФИЛИАЛА это стало дырой:
    uid у неё — владелец филиала (своих кредов у управляющего нет), и проверка
    пускала бы в любой филиал сети. Доступ разбирается один раз в
    scope_of_token, сюда приходит уже готовым.
    """
    if not sc.get("role"):
        return None
    branches = sc.get("branches") or []
    if not org:
        return branches[0] if branches else None
    return next((b for b in branches if b["organization_id"] == org), None)


def _rest_by_org(org_id: str) -> Dict[str, Any]:
    """Креды филиала — ПО ФИЛИАЛУ, а не по тому, кто открыл ссылку.

    Управляющий сам ресторанов не владеет: поиск по его uid возвращал пусто, и
    подставлялся демо-клиент (облачный, без складов и актов). Отсюда «акт не
    отправился» на ссылке филиала.
    """
    from database import get_db, decrypt_password
    conn = get_db()
    row = conn.execute(
        "SELECT api_login, api_password, iiko_server, organization_name FROM restaurants "
        "WHERE organization_id = ? AND COALESCE(iiko_server,'') != '' LIMIT 1", (org_id,)
    ).fetchone()
    if row is None:
        row = conn.execute("SELECT api_login, api_password, iiko_server, organization_name "
                           "FROM restaurants WHERE organization_id = ? LIMIT 1", (org_id,)).fetchone()
    conn.close()
    if not row:
        return {}
    return {"api_login": row["api_login"], "iiko_server": row["iiko_server"],
            "api_password": decrypt_password(row["api_password"] or ""),
            "org_name": row["organization_name"] or ""}


async def _salmon_context(sc: Dict[str, Any], org: Optional[str],
                          kind: str = A.DEFAULT_KIND) -> Dict[str, Any]:
    import api                                   # ленивый импорт: избегаем цикла
    spec = A.kind_spec(kind)
    uid = sc["uid"]        # от чьего имени идём в iiko (владелец филиала)
    b = _branch_or_403(sc, org)
    if not b:
        return {"error": "нет доступа к филиалу"}
    real_org = b["organization_id"]
    info = _rest_by_org(real_org)
    if not info:
        return {"error": "у филиала нет подключения к iiko"}
    client = api.get_client(info["api_login"], info.get("iiko_server"), info.get("api_password"))
    try:
        # П/Ф — тип PREPARED, а закупаемый каталог отфильтрован до GOODS,
        # поэтому здесь спрашиваем оба типа отдельным запросом.
        catalog = await client.search_nomenclature(real_org, "", limit=100000,
                                                   types=["GOODS", "PREPARED"])
    except Exception as e:
        logger.warning(f"[acts] номенклатура {real_org}: {e!r}")
        catalog = []
    prods = A.find_products(catalog, spec["key"])

    async def _invoice_price(org_id: str, product_id: str):
        st = await api.price_stats(org_id, product_id, 60, {"id": uid})
        best = (st or {}).get("best") or {}
        return (float(best.get("price_per_unit") or 0),
                f'{best.get("supplier_name") or ""} · {best.get("date") or ""}'.strip(" ·"))

    costs = ({} if spec.get("price_const")
             else await _stock_costs(client, info.get("org_name") or b.get("name") or "", real_org))

    async def _cost_of(role: str):
        """Себестоимость позиции: учётная из iiko, потом накладная СВОЕГО филиала.

        Раньше первым источником была накладная, а если позиции в ней нет —
        цена бралась у соседей, с приоритетом ЦЕХа. Для П/Ф это давало ОДНУ
        цену на всю сеть: в филиал стейк и пласт приходят внутренним
        перемещением, накладной у них нет, и все четыре точки получали
        цифру ЦЕХа. На вкладке «Угорь» это и был тот самый 9 300 ₸/кг.

        Учётная себестоимость считается по складам самого филиала, поэтому
        она у каждого своя — и это то число, по которому в iiko и живёт
        позиция. Накладная осталась запасным источником для того, что в
        филиал действительно закупают (соус), а поиск у соседей убран: он
        подставлял правдоподобное чужое число вместо честного «не знаем».
        """
        prod = prods.get(role)
        if not prod:
            return 0.0, ""
        cost = float(costs.get(prod["id"]) or 0)
        if cost > 0:
            return round(cost, 2), "себестоимость iiko"
        try:
            price, src = await _invoice_price(real_org, prod["id"])
        except Exception as e:
            logger.warning(f"[acts] цена {role} {real_org}: {e!r}")
            price, src = 0.0, ""
        if price > 0:
            return price, f"накладная · {src}" if src else "накладная"
        return 0.0, ""

    if spec.get("price_const"):
        # Угорь считается по константам учёта целиком: акты разбора живут у нас,
        # интеграции по угрю с iiko нет, и цена у него одна на всю сеть. Ходить
        # за ней в номенклатуру и остатки незачем — и экран открывается сразу.
        price, price_from = float(spec.get("raw_price") or 0), "константа учёта"
        trim_price, trim_from, trim_const = float(spec["trim_price"] or 0), "константа учёта", True
    else:
        price, price_from = await _cost_of("raw")
        # Обрезки: сперва их учётная себестоимость — она и есть то, во что они
        # обошлись филиалу. Константа учёта осталась запасной, а дальше цену
        # спрашиваем полем в акте.
        trim_price, trim_from = await _cost_of("trim")
        trim_const = False
        if trim_price <= 0 and spec["trim_price"]:
            trim_price, trim_from, trim_const = float(spec["trim_price"]), "константа учёта", True
    return {"branch": b, "products": prods, "price_per_kg": price,
            "price_from": price_from, "trim_price": trim_price,
            "trim_from": trim_from, "trim_const": trim_const,
            "kind": spec["key"], "title": spec["title"],
            "raw_label": spec["raw_label"], "raw_short": spec["raw_short"],
            "kinds": _TABS}


async def _stock_costs(client, org_name: str, real_org: str) -> Dict[str, float]:
    """Учётная себестоимость позиций филиала. Пусто — значит не знаем."""
    try:
        return await client.get_stock_costs(org_name)
    except Exception as e:
        logger.warning(f"[acts] себестоимость {real_org}: {e!r}")
        return {}


# Переключатель наверху экрана: две рыбы и жарка. Жарка стоит рядом, потому что
# открывают их подряд, но это другой документ — и адреса у неё свои.
_TABS = ([{"key": k["key"], "title": k["title"], "tab": k["tab"]} for k in A.KINDS.values()]
         + [{"key": "teriyaki", "title": A.TERIYAKI_SPEC["title"],
             "tab": A.TERIYAKI_SPEC["tab"]}])


async def _ctx_for(sc: Dict[str, Any], org: Optional[str], kind: str) -> Dict[str, Any]:
    """Контекст разбора — демо-справочник или живой филиал.

    Демо-ветка стояла только в GET: экран получал справочник и цены, а первый
    же ввод веса уходил в POST, где `_rest_by_org("demo-b1")` не находил
    подключения и возвращал 403. Поэтому в демо не считалось НИЧЕГО — ни
    отхода, ни процентов, ни денег. Развилка одна на все маршруты, чтобы
    следующий обработчик не забыл её снова.
    """
    if sc.get("demo"):
        import demo as _demo
        if kind == "teriyaki":
            return {**_demo.teriyaki_context(), "branch": sc["branches"][0], "kinds": _TABS}
        return {**_demo.acts_context(kind), "branch": sc["branches"][0], "kinds": _TABS}
    if kind == "teriyaki":
        return await _teriyaki_context(sc, org)
    return await _salmon_context(sc, org, kind)


def _kind_or_404(kind: str) -> Optional[str]:
    """Вид разбора из адреса. Чужое слово — 404, а не молчаливый лосось: иначе
    опечатка в ссылке считала бы угря по лососёвой цене обрезков."""
    return kind if kind in A.KINDS else None


@router.get("/api/acts/{kind}/{token}")
async def acts_salmon_get(kind: str, token: str, org: Optional[str] = None):
    if not _kind_or_404(kind):
        return JSONResponse({"error": "неизвестный разбор"}, status_code=404)
    sc = scope_of_token(token)
    uid = sc["uid"]
    if not sc["valid"]:
        return JSONResponse({"error": "bad token"}, status_code=404)
    # по ссылке филиала акт делается только по СВОЕМУ филиалу:
    # выбор из адреса игнорируем, иначе ссылка открыла бы чужой склад
    # Демо: справочник и цены готовые, в iiko не ходим — там нечего искать.
    ctx = await _ctx_for(sc, sc["org"] or org, kind)
    if ctx.get("error"):
        return JSONResponse(ctx, status_code=403)
    # филиалы для выбора — из УЖЕ разобранного доступа. Тут стоял _scope(uid), и
    # по ссылке филиала выпадал список всей сети: uid у неё — владелец.
    ctx["branches"] = sc["branches"]
    ctx["role"] = sc["role"]
    return JSONResponse(ctx)


class SalmonPayload(BaseModel):
    org: Optional[str] = None
    weight_raw: float                        # сколько входа (стейк, угорь) пошло в разбор
    weight_slice: float                      # сколько вышло нарезки
    weight_trim: float = 0.0                 # сколько вышло обрезков
    price_per_kg: Optional[float] = None     # переопределение цены, если закуп разовый
    trim_price: Optional[float] = None       # цена обрезков, если её нет в накладных
    note: Optional[str] = ""                 # чем разбор отличался, если отличался


NO_PRICE = ("Нет цены входа: в накладных за 60 дней его не было. "
            "Впиши цену вручную.")
NO_TRIM_PRICE = "Цена обрезков не задана — впиши, почём их считать."
NO_SAUCE_PRICE = ("Соуса терияки нет в накладных за 60 дней — впиши его цену, "
                  "₸/кг, иначе граммы не во что пересчитать.")


def _prices(ctx: Dict[str, Any], payload: "SalmonPayload") -> Dict[str, Any]:
    """Цены разбора: вход и обрезки. Обе берутся из себестоимости и обе можно
    переписать руками — бывает разовый закуп по другой цене.

    Ненайденная цена здесь больше не ошибка. Веса и проценты от неё не
    зависят, а расчёт целиком падал в ошибку и уносил с собой отход: у угря,
    где константы обрезков нет, экран не показывал вообще ничего. Чего не
    хватает для денег, скажет сам расчёт — полем `need`.
    """
    return {"price": float(payload.price_per_kg or ctx["price_per_kg"] or 0),
            "trim_price": float(payload.trim_price or ctx["trim_price"] or 0)}


# Чего не хватило, чтобы посчитать деньги, — словами и на том же языке, на
# котором человек видит поля.
NEED_TEXT = {
    "price": NO_PRICE,
    "trim_price": NO_TRIM_PRICE,
    "sauce_price": NO_SAUCE_PRICE,
}


@router.post("/api/acts/{kind}/{token}")
async def acts_salmon_calc(kind: str, token: str, payload: SalmonPayload):
    """Считает разбор, ничего не записывая: цифры на экране пересчитываются на
    каждый ввод, а фиксация — отдельным действием человека."""
    if not _kind_or_404(kind):
        return JSONResponse({"error": "неизвестный разбор"}, status_code=404)
    sc = scope_of_token(token)
    if not sc["valid"]:
        return JSONResponse({"error": "bad token"}, status_code=404)
    # по ссылке филиала разбор делается только по СВОЕМУ филиалу:
    # выбор из адреса игнорируем, иначе ссылка открыла бы чужой склад
    ctx = await _ctx_for(sc, sc["org"] or payload.org, kind)
    if ctx.get("error"):
        return JSONResponse(ctx, status_code=403)
    pr = _prices(ctx, payload)
    res = A.split_act(pr["price"], payload.weight_raw, payload.weight_slice,
                      payload.weight_trim, trim_price=pr["trim_price"], kind=kind)
    if res.get("ok") and res.get("need"):
        res["need_text"] = NEED_TEXT.get(res["need"], "")
    res["products"] = ctx["products"]
    res["branch"] = ctx["branch"]
    return JSONResponse(res)


@router.post("/api/acts/{kind}/{token}/save")
async def acts_salmon_save(kind: str, token: str, payload: SalmonPayload):
    """Фиксирует разбор в нашей базе. В iiko поштучно не уходит: администратор
    заносит один сводный акт за период руками, и второй документ на тот же вес
    задвоил бы списание. Считаем здесь заново — числам из браузера не верим."""
    from database import add_salmon_act
    sc = scope_of_token(token)
    if not sc["valid"]:
        return JSONResponse({"error": "bad token"}, status_code=404)
    if not _kind_or_404(kind):
        return JSONResponse({"error": "неизвестный разбор"}, status_code=404)
    ctx = await _ctx_for(sc, sc["org"] or payload.org, kind)
    if ctx.get("error"):
        return JSONResponse(ctx, status_code=403)
    pr = _prices(ctx, payload)
    calc = A.split_act(pr["price"], payload.weight_raw, payload.weight_slice,
                       payload.weight_trim, trim_price=pr["trim_price"], kind=kind)
    if not calc.get("ok"):
        return JSONResponse(calc)
    # Показывать разбор без денег можно, записывать — нет: в своде он встанет
    # нулевой себестоимостью и утянет вниз среднее по филиалу.
    if not calc.get("money"):
        return JSONResponse({"ok": False, "error": NEED_TEXT.get(calc.get("need"), NO_PRICE)})
    aid = add_salmon_act(ctx["branch"]["organization_id"], sc["uid"], calc, pr["price"],
                         pr["trim_price"], payload.note or "", kind=kind)
    return JSONResponse({**calc, "saved": True, "id": aid})


def _period_bounds(date_from: Optional[str], date_to: Optional[str]) -> Dict[str, str]:
    """Пусто = текущий месяц: чаще всего свод заносят именно за него."""
    today = local_now().date()
    return {"from": date_from or today.replace(day=1).isoformat(),
            "to": date_to or today.isoformat()}


def _all_branch_names() -> Dict[str, str]:
    """Имена ВСЕХ филиалов сети, а не только своих: публичная таблица без имён
    ничего не подсказывает — управляющему не с кем себя сравнить."""
    from database import get_db
    conn = get_db()
    rows = conn.execute("SELECT organization_id, organization_name FROM restaurants").fetchall()
    conn.close()
    return {r[0]: (r[1] or "Филиал") for r in rows if r[0]}


@router.get("/api/acts/{kind}/{token}/period")
async def acts_salmon_period(kind: str, token: str, org: Optional[str] = None,
                             date_from: Optional[str] = None, date_to: Optional[str] = None,
                             only_open: bool = False):
    """Журнал разборов филиала и свод за период — то, что администратор заносит
    в iiko одним актом. only_open — только ещё не занесённые."""
    from database import get_salmon_acts
    sc = scope_of_token(token)
    if not sc["valid"]:
        return JSONResponse({"error": "bad token"}, status_code=404)
    if not _kind_or_404(kind):
        return JSONResponse({"error": "неизвестный разбор"}, status_code=404)
    b = _branch_or_403(sc, sc["org"] or org)
    if not b:
        return JSONResponse({"error": "нет доступа к филиалу"}, status_code=403)
    p = _period_bounds(date_from, date_to)
    rows = get_salmon_acts([b["organization_id"]], p["from"], p["to"],
                           only_open=only_open, kind=kind)
    spec = A.kind_spec(kind)
    return JSONResponse({"branch": b, "period": p, "only_open": only_open,
                         "acts": rows, "total": A.summarize(rows),
                         "kind": spec["key"], "trim_price": spec["trim_price"]})


class SalmonPostedPayload(BaseModel):
    org: Optional[str] = None
    ids: List[int]                 # ровно те разборы, что человек видел в своде


@router.post("/api/acts/{kind}/{token}/posted")
async def acts_salmon_posted(kind: str, token: str, payload: SalmonPostedPayload):
    """«Свод занесён в iiko вручную» — закрывает пачку разборов."""
    from database import mark_salmon_posted
    sc = scope_of_token(token)
    if not sc["valid"]:
        return JSONResponse({"error": "bad token"}, status_code=404)
    b = _branch_or_403(sc, sc["org"] or payload.org)
    if not b:
        return JSONResponse({"error": "нет доступа к филиалу"}, status_code=403)
    n = mark_salmon_posted(payload.ids, [b["organization_id"]], sc["uid"])
    return JSONResponse({"ok": True, "closed": n})


@router.post("/api/acts/{kind}/{token}/delete/{act_id}")
async def acts_salmon_delete(kind: str, token: str, act_id: int, org: Optional[str] = None):
    """Убрать ошибочный разбор — пока свод не занесён в iiko. После этого нельзя:
    сумма на экране разошлась бы с уже проведённым документом."""
    from database import delete_salmon_act
    sc = scope_of_token(token)
    if not sc["valid"]:
        return JSONResponse({"error": "bad token"}, status_code=404)
    b = _branch_or_403(sc, sc["org"] or org)
    if not b:
        return JSONResponse({"error": "нет доступа к филиалу"}, status_code=403)
    ok = delete_salmon_act(act_id, [b["organization_id"]])
    return JSONResponse({"ok": ok} if ok else
                        {"ok": False, "error": "разбор уже занесён в iiko или не ваш"})


@router.get("/api/acts/{kind}/{token}/network")
async def acts_salmon_network(kind: str, token: str, date_from: Optional[str] = None,
                              date_to: Optional[str] = None):
    """Публичная таблица по сети: себестоимость нарезки и % отхода по каждому
    филиалу. Видна любому филиалу — сравнить себя не с кем, если показывать
    только собственные числа."""
    from database import get_salmon_acts, all_salmon_orgs
    sc = scope_of_token(token)
    if not sc["valid"]:
        return JSONResponse({"error": "bad token"}, status_code=404)
    if not sc["role"]:
        return JSONResponse({"error": "нет доступа"}, status_code=403)
    if not _kind_or_404(kind):
        return JSONResponse({"error": "неизвестный разбор"}, status_code=404)
    p = _period_bounds(date_from, date_to)
    rows = get_salmon_acts(all_salmon_orgs(kind), p["from"], p["to"], limit=5000, kind=kind)
    mine = {b["organization_id"] for b in sc["branches"]}
    branches = A.by_branch(rows, _all_branch_names())
    for r in branches:
        r["mine"] = r["organization_id"] in mine
    spec = A.kind_spec(kind)
    return JSONResponse({"period": p, "branches": branches, "kind": spec["key"],
                         "total": A.summarize(rows), "trim_price": spec["trim_price"]})


# ── Терияки: обрезки на сковороду ───────────────────────────────────────────
# Не разбор, а передел, и потому свои адреса, своя таблица весов и свой свод.
# Пока он жил внутри разбора, один экран отвечал на два разных вопроса:
# «насколько аккуратно разделали рыбу» и «сколько дала сковорода».


async def _teriyaki_context(sc: Dict[str, Any], org: Optional[str]) -> Dict[str, Any]:
    """Позиции и цены жарки: обрезки по константе учёта, соус — из накладных."""
    import api
    spec = A.TERIYAKI_SPEC
    uid = sc["uid"]
    b = _branch_or_403(sc, org)
    if not b:
        return {"error": "нет доступа к филиалу"}
    real_org = b["organization_id"]
    info = _rest_by_org(real_org)
    if not info:
        return {"error": "у филиала нет подключения к iiko"}
    client = api.get_client(info["api_login"], info.get("iiko_server"), info.get("api_password"))
    try:
        catalog = await client.search_nomenclature(real_org, "", limit=100000,
                                                   types=["GOODS", "PREPARED"])
    except Exception as e:
        logger.warning(f"[teriyaki] номенклатура {real_org}: {e!r}")
        catalog = []
    prods = A.find_products(catalog, "teriyaki")
    costs = await _stock_costs(client, info.get("org_name") or b.get("name") or "", real_org)

    async def _cost_of(role: str):
        """Себестоимость позиции жарки: учётная из iiko, потом накладная.

        Обрезки шли по константе 8 800 ₸ на всю сеть — от неё и была неверная
        себестоимость лосося терияки: обрезки в филиале стоят столько, во
        сколько их обошёлся разбор, а не столько, сколько записано в коде.
        Константа осталась последней запасной, чтобы жарка не вставала совсем.
        """
        prod = prods.get(role)
        if not prod:
            return 0.0, ""
        cost = float(costs.get(prod["id"]) or 0)
        if cost > 0:
            return round(cost, 2), "себестоимость iiko"
        try:
            st = await api.price_stats(real_org, prod["id"], 60, {"id": uid})
            best = (st or {}).get("best") or {}
            price = float(best.get("price_per_unit") or 0)
            if price > 0:
                src = f'{best.get("supplier_name") or ""} · {best.get("date") or ""}'.strip(" ·")
                return price, f"накладная · {src}" if src else "накладная"
        except Exception as e:
            logger.warning(f"[teriyaki] цена {role} {real_org}: {e!r}")
        return 0.0, ""

    trim_price, trim_from = await _cost_of("trim")
    trim_const = False
    if trim_price <= 0 and spec["trim_price"]:
        trim_price, trim_from, trim_const = float(spec["trim_price"]), "константа учёта", True
    sauce_price, sauce_from = await _cost_of("sauce")
    return {"branch": b, "products": prods,
            "trim_price": trim_price, "trim_from": trim_from, "trim_const": trim_const,
            "sauce_price": sauce_price, "sauce_from": sauce_from,
            "kind": "teriyaki", "title": spec["title"], "kinds": _TABS}


class TeriyakiPayload(BaseModel):
    org: Optional[str] = None
    weight_fry: float                        # сколько обрезков положили жарить
    weight_teriyaki: float                   # сколько терияки сняли со сковороды
    weight_sauce: float = 0.0                # ГРАММЫ соуса на эту жарку
    trim_price: Optional[float] = None       # цена обрезков, если закуп разовый
    sauce_price: Optional[float] = None      # цена соуса, если закуп разовый
    note: Optional[str] = ""


def _teriyaki_prices(ctx: Dict[str, Any], payload: "TeriyakiPayload") -> Dict[str, Any]:
    """Себестоимость обрезков и соуса; обе можно переписать руками.

    Ненайденная цена здесь не ошибка: ужарка и выход жарки — это веса, и
    падать на цене, к которой они не относятся, нельзя. Чего не хватает для
    денег, скажет сам расчёт. Цена соуса при этом нужна только когда соус
    в самом деле лили.
    """
    return {"trim_price": float(payload.trim_price or ctx["trim_price"] or 0),
            "sauce_price": float(payload.sauce_price or ctx.get("sauce_price") or 0)}


def _teriyaki_calc(ctx: Dict[str, Any], payload: "TeriyakiPayload") -> Dict[str, Any]:
    pr = _teriyaki_prices(ctx, payload)
    res = A.fry_teriyaki(payload.weight_fry, payload.weight_teriyaki,
                         trim_price=pr["trim_price"],
                         g_sauce=payload.weight_sauce, sauce_price=pr["sauce_price"])
    if res.get("ok") and res.get("need"):
        res["need_text"] = NEED_TEXT.get(res["need"], "")
    return res


@router.get("/api/teriyaki/{token}")
async def teriyaki_get(token: str, org: Optional[str] = None):
    sc = scope_of_token(token)
    if not sc["valid"]:
        return JSONResponse({"error": "bad token"}, status_code=404)
    ctx = await _ctx_for(sc, sc["org"] or org, "teriyaki")
    if ctx.get("error"):
        return JSONResponse(ctx, status_code=403)
    ctx["branches"] = sc["branches"]
    ctx["role"] = sc["role"]
    return JSONResponse(ctx)


@router.post("/api/teriyaki/{token}")
async def teriyaki_calc(token: str, payload: TeriyakiPayload):
    """Считает жарку, ничего не записывая: цифры пересчитываются на каждый ввод."""
    sc = scope_of_token(token)
    if not sc["valid"]:
        return JSONResponse({"error": "bad token"}, status_code=404)
    ctx = await _ctx_for(sc, sc["org"] or payload.org, "teriyaki")
    if ctx.get("error"):
        return JSONResponse(ctx, status_code=403)
    res = _teriyaki_calc(ctx, payload)
    res["products"] = ctx["products"]
    res["branch"] = ctx["branch"]
    return JSONResponse(res)


@router.post("/api/teriyaki/{token}/save")
async def teriyaki_save(token: str, payload: TeriyakiPayload):
    """Фиксирует жарку у нас. В iiko поштучно не уходит — за период
    администратор заносит один сводный акт руками."""
    from database import add_teriyaki_act
    sc = scope_of_token(token)
    if not sc["valid"]:
        return JSONResponse({"error": "bad token"}, status_code=404)
    ctx = await _ctx_for(sc, sc["org"] or payload.org, "teriyaki")
    if ctx.get("error"):
        return JSONResponse(ctx, status_code=403)
    calc = _teriyaki_calc(ctx, payload)          # считаем заново: браузеру не верим
    if not calc.get("ok"):
        return JSONResponse(calc)
    # Жарку без денег показываем, но не записываем: в своде она встанет нулевой
    # себестоимостью терияки и испортит средневзвешенную цену периода.
    if not calc.get("money"):
        return JSONResponse({"ok": False, "error": NEED_TEXT.get(calc.get("need"), NO_TRIM_PRICE)})
    aid = add_teriyaki_act(ctx["branch"]["organization_id"], sc["uid"], calc,
                           payload.note or "")
    return JSONResponse({**calc, "saved": True, "id": aid})


@router.get("/api/teriyaki/{token}/period")
async def teriyaki_period(token: str, org: Optional[str] = None,
                          date_from: Optional[str] = None, date_to: Optional[str] = None,
                          only_open: bool = False):
    from database import get_salmon_acts
    sc = scope_of_token(token)
    if not sc["valid"]:
        return JSONResponse({"error": "bad token"}, status_code=404)
    b = _branch_or_403(sc, sc["org"] or org)
    if not b:
        return JSONResponse({"error": "нет доступа к филиалу"}, status_code=403)
    p = _period_bounds(date_from, date_to)
    rows = get_salmon_acts([b["organization_id"]], p["from"], p["to"],
                           only_open=only_open, kind="teriyaki")
    return JSONResponse({"branch": b, "period": p, "only_open": only_open,
                         "acts": rows, "total": A.summarize_teriyaki(rows),
                         "kind": "teriyaki", "trim_price": A.TERIYAKI_SPEC["trim_price"]})


@router.post("/api/teriyaki/{token}/posted")
async def teriyaki_posted(token: str, payload: SalmonPostedPayload):
    from database import mark_salmon_posted
    sc = scope_of_token(token)
    if not sc["valid"]:
        return JSONResponse({"error": "bad token"}, status_code=404)
    b = _branch_or_403(sc, sc["org"] or payload.org)
    if not b:
        return JSONResponse({"error": "нет доступа к филиалу"}, status_code=403)
    n = mark_salmon_posted(payload.ids, [b["organization_id"]], sc["uid"])
    return JSONResponse({"ok": True, "closed": n})


@router.post("/api/teriyaki/{token}/delete/{act_id}")
async def teriyaki_delete(token: str, act_id: int, org: Optional[str] = None):
    from database import delete_salmon_act
    sc = scope_of_token(token)
    if not sc["valid"]:
        return JSONResponse({"error": "bad token"}, status_code=404)
    b = _branch_or_403(sc, sc["org"] or org)
    if not b:
        return JSONResponse({"error": "нет доступа к филиалу"}, status_code=403)
    ok = delete_salmon_act(act_id, [b["organization_id"]])
    return JSONResponse({"ok": ok} if ok else
                        {"ok": False, "error": "жарка уже занесена в iiko или не ваша"})


# ── Демо-ссылки: показ продукта новым клиентам ──────────────────────────────
# Ссылку выдаёт владелец под конкретного клиента и потом видит, смотрели её или
# нет. Одна ссылка на всех такого не покажет, а продавцу важно именно это.


@router.get("/api/demo/hints/{token}")
async def demo_hints(token: str):
    """Подсказки по разделам — только для демо-ссылки.

    Отдаём словарём по id раздела, а показывает оболочка при открытии вкладки:
    вываленные списком на входе шесть карточек читаются как рекламный блок, а
    одна по делу — как объяснение того, что человек прямо сейчас видит.

    Для обычной ссылки — пусто: своему клиенту рассказывать про его же продукт
    незачем.
    """
    import demo as _demo
    sc = scope_of_token(token)
    if not sc["valid"] or not sc.get("demo"):
        return JSONResponse({"demo": False, "hints": {}})
    return JSONResponse({"demo": True, "hints": _demo.HINTS})


class DemoLinkPayload(BaseModel):
    label: str = ""            # под кого: «Сеть Ромашка»


@router.post("/api/demo-link/{token}")
async def demo_link_create(token: str, payload: DemoLinkPayload):
    """Завести демо-ссылку. Только владельцу: это инструмент продажи."""
    import secrets
    from database import add_demo_link
    sc = scope_of_token(token)
    if not sc["valid"]:
        return JSONResponse({"error": "bad token"}, status_code=404)
    if sc["role"] != "owner" or sc.get("demo"):
        return JSONResponse({"error": "демо-ссылки выдаёт владелец"}, status_code=403)
    code = secrets.token_urlsafe(9)
    add_demo_link(code, payload.label or "", created_by=sc["uid"])
    return JSONResponse({"ok": True, "code": code,
                         "url": f"/m/{token_for_demo(code)}"})


@router.get("/api/demo-links/{token}")
async def demo_links_list(token: str):
    """Выданные ссылки со счётчиком: сколько раз открывали и когда в последний."""
    from database import get_demo_links
    sc = scope_of_token(token)
    if not sc["valid"]:
        return JSONResponse({"error": "bad token"}, status_code=404)
    if sc["role"] != "owner" or sc.get("demo"):
        return JSONResponse({"error": "нет доступа"}, status_code=403)
    rows = get_demo_links(sc["uid"])
    return JSONResponse({"links": [{
        "code": r["code"], "label": r["label"] or "",
        "url": f"/m/{token_for_demo(r['code'])}",
        "opens": int(r["opens"] or 0),
        "created_at": r["created_at"],
        "first_open_at": r["first_open_at"], "last_open_at": r["last_open_at"],
    } for r in rows]})


# ── Бумажные журналы: галочки и неделя истории ───────────────────────────────
# Три тетради, которые спрашивает проверка: температурный, бракеражный, уборки.
# Сами данные бот собирает отдельно (фото зон, замеры камер) — здесь отмечают
# факт заполнения бумаги. Экран живёт рядом с чек-листами: заполняют их одни и
# те же люди в один и тот же заход, разносить по вкладкам незачем.
import journals as J


class JournalPayload(BaseModel):
    org: Optional[str] = None
    journal: str                   # temp | brak | clean
    on: bool = True                # снятие галочки — тоже действие человека


def _journal_week(orgs: List[str], today) -> Dict[str, Any]:
    """Неделя истории: семь дней, включая сегодня."""
    from database import get_paper_journals
    days = [(today - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
    rows = get_paper_journals(orgs, days[0], days[-1])
    done = {(r["organization_id"], r["day"], r["journal"]): r for r in rows}
    return {"days": days, "done": done}


@router.get("/api/mountain/journals/{token}")
async def journals_panel(token: str, org: Optional[str] = None):
    """Что отмечено сегодня и как было всю неделю. Владелец видит все филиалы —
    ему важно «кто не заполняет», управляющий свой."""
    sc = scope_of_token(token)
    if not sc["valid"]:
        return JSONResponse({"error": "bad token"}, status_code=404)
    if not sc["role"]:
        return JSONResponse({"error": "нет доступа"}, status_code=403)
    branches = sc["branches"]
    pick = sc["org"] or org
    if pick:
        branches = [b for b in branches if b["organization_id"] == pick]
        if not branches:
            return JSONResponse({"error": "чужой филиал"}, status_code=403)
    orgs = [b["organization_id"] for b in branches]
    today = local_now().date()
    week = _journal_week(orgs, today)
    rows = []
    for b in branches:
        o = b["organization_id"]
        rows.append({
            "organization_id": o, "name": b["name"],
            "days": [{"day": d, "done": [k for k in J.PAPER_KEYS
                                         if (o, d, k) in week["done"]]} for d in week["days"]],
            "today": {k: bool((o, today.isoformat(), k) in week["done"]) for k in J.PAPER_KEYS},
        })
    return JSONResponse({"role": sc["role"], "journals": J.PAPER, "slot": J.PAPER_SLOT,
                         "today": today.isoformat(), "days": week["days"], "branches": rows})


@router.post("/api/mountain/journals/{token}")
async def journals_mark(token: str, payload: JournalPayload):
    from database import mark_paper_journal
    sc = scope_of_token(token)
    if not sc["valid"]:
        return JSONResponse({"error": "bad token"}, status_code=404)
    b = _branch_or_403(sc, sc["org"] or payload.org)
    if not b:
        return JSONResponse({"error": "нет доступа к филиалу"}, status_code=403)
    if payload.journal not in J.PAPER_KEYS:
        return JSONResponse({"error": "неизвестный журнал"}, status_code=400)
    day = local_now().date().isoformat()
    mark_paper_journal(b["organization_id"], day, payload.journal, sc["uid"],
                       on=payload.on, slot=J.PAPER_SLOT)
    return JSONResponse({"ok": True, "journal": payload.journal, "on": payload.on, "day": day})


# ── Консоль владельца: сообщение управляющим ─────────────────────────────────
# Уведомление живёт на сайте И дублируется в телеграм. Сайт остаётся источником
# правды: там отметка «прочитал», по которой владелец судит, дошло ли. Но ждать,
# пока человек сам откроет «Гору», нельзя — срочное сообщение так лежит до
# вечера. Телеграм догоняет его там, где он и так весь день.
from database import (add_branch_notice, get_branch_notices, mark_notice_read,
                      notice_is_for, notice_read_stats)


class NoticePayload(BaseModel):
    text: str
    org: Optional[str] = None      # пусто = всем филиалам


@router.post("/api/mountain/notice/{token}")
async def notice_send(token: str, payload: NoticePayload):
    sc = scope_of_token(token)
    uid = sc["uid"]
    if not sc["valid"]:
        return JSONResponse({"error": "bad token"}, status_code=404)
    if sc["role"] != "owner":
        return JSONResponse({"error": "сообщения рассылает только владелец"}, status_code=403)
    text = (payload.text or "").strip()
    if not text:
        return JSONResponse({"error": "пустое сообщение"}, status_code=400)
    if len(text) > 2000:
        return JSONResponse({"error": "слишком длинно, до 2000 знаков"}, status_code=400)
    org = payload.org or ""
    if org and org not in {b["organization_id"] for b in sc["branches"]}:
        return JSONResponse({"error": "чужой филиал"}, status_code=403)
    nid = add_branch_notice(text, uid, org)
    sent = await _notice_to_telegram(sc, org, text, author=uid)
    return JSONResponse({"ok": True, "id": nid, "telegram": sent})


async def _notice_to_telegram(sc: Dict[str, Any], org: str, text: str,
                              author: int) -> int:
    """Догоняет сообщение в телеграме. Возвращает, скольким людям ушло.

    Телеграм здесь — дубль, а не доставка: уведомление уже записано, и если бот
    молчит, владелец всё равно увидит его на сайте. Поэтому ни одна ошибка
    отсюда не роняет ручку — иначе упавший телеграм означал бы «сообщение не
    отправлено», хотя оно отправлено.
    """
    import api                                   # ленивый импорт: избегаем цикла
    from database import get_branch_managers
    orgs = [org] if org else [b["organization_id"] for b in sc["branches"]]
    body = "📣 <b>Сообщение от руководства</b>\n\n" + text
    sent, seen = 0, {author}                     # себе своё же сообщение не шлём
    for m in get_branch_managers():
        if m["organization_id"] not in orgs:
            continue
        tg = int(m["telegram_id"] or 0)
        # один человек на двух филиалах получает ОДНО сообщение, а не две копии
        if not tg or tg in seen:
            continue
        seen.add(tg)
        try:
            await api._tg_send(tg, body)
            sent += 1
        except Exception as e:
            logger.warning(f"[notice] телеграм {tg}: {e!r}")
    return sent


@router.get("/api/mountain/notices/{token}")
async def notices_list(token: str):
    sc = scope_of_token(token)
    uid = sc["uid"]
    if not sc["valid"]:
        return JSONResponse({"error": "bad token"}, status_code=404)
    if not sc["role"]:
        return JSONResponse({"error": "нет доступа"}, status_code=403)
    orgs = [b["organization_id"] for b in sc["branches"]]
    rows = get_branch_notices(orgs, reader=uid)
    if sc["role"] == "owner":
        names = {b["organization_id"]: b["name"] for b in sc["branches"]}
        for r in rows:
            r["read_by"] = notice_read_stats(r["id"])          # сколько прочитали
            r["branch"] = names.get(r.get("organization_id") or "", "все филиалы")
    return JSONResponse({"role": sc["role"], "notices": rows,
                         "unread": sum(1 for r in rows if not r.get("is_read"))})


@router.post("/api/mountain/notice/{token}/read/{notice_id}")
async def notice_read(token: str, notice_id: int):
    """Отметка «прочитал». Подписи токена мало: владелец по этой отметке судит,
    дошло ли сообщение до филиала. Раньше любой подписанный uid — в том числе
    без роли вообще — мог отметить ЛЮБОЕ сообщение, и счётчик «прочитали N»
    показывал больше, чем есть управляющих. Пускаем только адресата."""
    sc = scope_of_token(token)
    uid = sc["uid"]
    if not sc["valid"]:
        return JSONResponse({"error": "bad token"}, status_code=404)
    if not sc["role"]:
        return JSONResponse({"error": "нет доступа"}, status_code=403)
    orgs = [b["organization_id"] for b in sc["branches"]]
    if not notice_is_for(notice_id, orgs):
        return JSONResponse({"error": "сообщение не для вас"}, status_code=403)
    mark_notice_read(notice_id, uid)
    return JSONResponse({"ok": True})


# ── Сдача чек-листа с сайта ──────────────────────────────────────────────────
# Раньше фото уходили только в чат бота. Управляющий и так открывает «Гору»
# каждый день — пусть сдаёт отсюда. Кадры сжимаем ДО отправки в браузере, а
# здесь ограничиваем размер: фотоконтроль не должен превращаться в файлопомойку.
import base64 as _b64
from database import (save_checklist_upload, get_checklist_uploads, checklist_dir)

ZONES = {"hall": "зал", "kitchen": "кухня", "wc": "санузел"}
SLOTS = ["10:45", "15:00", "20:45", "23:45"]
MAX_PHOTO_MB = 4
# Фора на сдачу слота: снять три зоны за минуту нельзя, и считать это
# опозданием — значит гонять через модель каждую сдачу.
SLOT_GRACE_MIN = int(os.getenv("CHECKLIST_GRACE_MIN", "20"))
# Хранить ли сами кадры на диске. По умолчанию НЕТ.
#
# Кадры не отдавала наружу ни одна ручка: клиент всегда получал только их
# количество, а картинки читал лишь разбор. То есть посмотреть их глазами было
# невозможно в принципе — они писались в никуда и копили гигабайты на /data
# (девять филиалов × четыре слота × три зоны × четыре кадра ≈ 2 ГБ в месяц,
# бессрочно). Разбору диск не нужен вовсе: кадры уже в памяти запроса. Храним
# вместо них хеш и вес — этого хватает и на счётчик, и на кэш вердиктов.
KEEP_PHOTOS = os.getenv("CHECKLIST_KEEP_PHOTOS", "0") == "1"


def _minutes(hm: str) -> int:
    """«10:45» → 645. Сравниваем числа, а не строки: строковое сравнение
    работало лишь потому, что все слоты двузначные."""
    try:
        h, m = hm.split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return 0
MAX_PHOTOS = 4        # больше четырёх на зону не нужно — и дороже в разборе


class ChecklistPayload(BaseModel):
    org: Optional[str] = None
    slot: str
    zone: str
    photos: List[str] = []          # base64, до MAX_PHOTOS штук
    note: Optional[str] = ""


@router.post("/api/mountain/checklist/{token}")
async def checklist_upload(token: str, payload: ChecklistPayload):
    sc = scope_of_token(token)
    uid = sc["uid"]
    if not sc["valid"]:
        return JSONResponse({"error": "bad token"}, status_code=404)
    b = _branch_or_403(sc, payload.org)
    if not b:
        return JSONResponse({"error": "нет доступа к филиалу"}, status_code=403)
    if payload.slot not in SLOTS:
        return JSONResponse({"error": f"слот должен быть одним из {', '.join(SLOTS)}"}, status_code=400)
    if payload.zone not in ZONES:
        return JSONResponse({"error": "зона: hall, kitchen или wc"}, status_code=400)
    if not payload.photos:
        return JSONResponse({"error": "нужно хотя бы одно фото"}, status_code=400)
    if len(payload.photos) > MAX_PHOTOS:
        return JSONResponse({"error": f"не больше {MAX_PHOTOS} кадров на зону"}, status_code=400)

    import checklist_ai as _C
    day = local_now().date().isoformat()
    org = b["organization_id"]

    blobs: List[Tuple[bytes, str]] = []
    saved: List[Dict[str, Any]] = []
    for i, raw in enumerate(payload.photos):
        try:
            data = _b64.b64decode((raw or "").split(",", 1)[-1], validate=False)
        except Exception:
            return JSONResponse({"error": "файл не читается"}, status_code=400)
        if len(data) > MAX_PHOTO_MB * 1024 * 1024:
            return JSONResponse({"error": f"кадр больше {MAX_PHOTO_MB} МБ — сожми"}, status_code=413)
        blobs.append((data, ZONES[payload.zone]))
        # В базе остаётся след кадра — хеш и вес, — но не сам кадр. Хеш ещё и
        # ключ кэша вердиктов: тот же снимок второй раз не оплачивается.
        rec: Dict[str, Any] = {"hash": _C.photo_hash(data), "bytes": len(data)}
        if KEEP_PHOTOS:
            folder = os.path.join(checklist_dir(), org, day)
            os.makedirs(folder, exist_ok=True)
            name = f"{payload.slot.replace(':', '')}_{payload.zone}_{int(local_now().timestamp())}_{i}.jpg"
            with open(os.path.join(folder, name), "wb") as f:
                f.write(data)
            rec["path"] = os.path.join(org, day, name)
        saved.append(rec)

    rid = save_checklist_upload(org, day, payload.slot, payload.zone,
                                saved, payload.note or "", uid)
    # Разбор идёт по кадрам ЭТОЙ зоны прямо из памяти. Раньше он перечитывал с
    # диска все зоны слота заново на каждую сдачу: три зоны — три запроса с 1, 2
    # и 3 наборами кадров, то есть за одни и те же снимки платили трижды.
    review = await _review_upload(org, day, payload.slot, blobs)
    return JSONResponse({"ok": True, "id": rid, "photos": len(saved),
                         "slot": payload.slot, "zone": ZONES[payload.zone],
                         "review": review})


async def _review_upload(org: str, day: str, slot: str,
                         blobs: List[Tuple[bytes, str]]) -> Dict[str, Any]:
    """Разбор кадров одной сдачи — со всеми предохранителями по деньгам.

    Кадры приходят из памяти запроса: на диск они больше не ложатся (см.
    KEEP_PHOTOS). Проверяем ровно то, что сейчас прислали, и дописываем вердикт
    к слоту — зоны сдаются по одной, а вердикт в базе один на слот.

    Выборка (SAMPLE_PCT) применяется, ТОЛЬКО когда кадры сохраняются: без диска
    непроверенную сдачу уже никто не посмотрит, и пропустить её — значит
    превратить фотоконтроль в галочки. Без хранения проверяем всё.
    """
    import checklist_ai as C
    from database import (merge_checklist_review, photo_verdict_cache, save_photo_verdict)
    if not blobs:
        return {"checked": False, "reason": "нет кадров"}

    why = "кадры не хранятся — проверяем каждую сдачу"
    if KEEP_PHOTOS:
        late = _minutes(local_now().strftime("%H:%M")) > _minutes(slot) + SLOT_GRACE_MIN
        do, why = C.should_review({"photos": [1] * len(blobs)}, late=late)
        if not do:
            return {"checked": False, "reason": why}

    hashes = [C.photo_hash(d) for d, _ in blobs]
    cache = photo_verdict_cache(hashes)
    res = await C.review_slot(blobs, cache=cache, criteria=S.ZONE_CRITERIA)
    if res.get("checked"):
        merge_checklist_review(org, day, slot, bool(res.get("ok")),
                               res.get("issues") or [], why, res.get("criteria") or [])
        for hsh in hashes:
            save_photo_verdict(hsh, {"ok": res.get("ok"), "issues": res.get("issues") or [],
                                     "criteria": res.get("criteria") or []})
        # Причину подменяем ТОЛЬКО у состоявшегося разбора: тут она отвечает на
        # «почему проверяли». Когда разбора не было, причину знает review_slot —
        # «выключен CHECKLIST_AI», «модель не ответила», — и затирать её нельзя:
        # управляющий читал «разбор не запускался: кадры не хранятся», что не
        # объясняет ничего.
        res["reason"] = why
    return res


@router.get("/api/mountain/checklist/{token}")
async def checklist_today(token: str):
    """Что сдано с сайта сегодня: слоты × зоны по каждому доступному филиалу."""
    sc = scope_of_token(token)
    uid = sc["uid"]
    if not sc["valid"]:
        return JSONResponse({"error": "bad token"}, status_code=404)
    if not sc["role"]:
        return JSONResponse({"error": "нет доступа"}, status_code=403)
    day = local_now().date().isoformat()
    orgs = [b["organization_id"] for b in sc["branches"]]
    rows = get_checklist_uploads(orgs, day)
    names = {b["organization_id"]: b["name"] for b in sc["branches"]}
    done: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        key = f'{r["organization_id"]}|{r["slot"]}|{r["zone"]}'
        done.setdefault(key, {"branch": names.get(r["organization_id"], ""),
                              "slot": r["slot"], "zone": ZONES.get(r["zone"], r["zone"]),
                              # ключ зоны как в базе («hall»): сайт сверял сдачу по
                              # ПОДПИСИ и не сходился на регистре — у нас «зал», у
                              # него «Зал», и сданный слот выглядел несданным
                              "zone_key": r["zone"],
                              "photos": len(r["photos"]), "at": (r["created_at"] or "")[11:16]})
    from database import get_checklist_reviews
    reviews = [{"slot": r["slot"], "ok": bool(r["ok"]), "issues": r["issues"],
                "criteria": r.get("criteria") or [],
                "reason": r.get("reason") or "",
                "branch": names.get(r["organization_id"], "")}
               for r in get_checklist_reviews(orgs, day)]
    return JSONResponse({"day": day, "slots": SLOTS, "zones": ZONES,
                         "criteria": S.ZONE_CRITERIA,
                         "role": sc["role"], "done": list(done.values()),
                         "reviews": reviews})
