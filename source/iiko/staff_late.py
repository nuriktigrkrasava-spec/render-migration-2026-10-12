"""Опоздания сотрудников: приход из явок iiko против эталона старта смены.

Эталон гибридный (решение владельца 25.08):
  • по умолчанию — время старта смены ФИЛИАЛА (утро/вечер), задаётся один раз;
  • если на сотрудника в этот день стоит плановая смена — она перебивает;
  • допуск grace минут: пришёл в его пределах — вовремя, а не «опоздал на 3».

Чего тут намеренно НЕТ:
  • прогулов. Отличить выходной от прогула по одним явкам нельзя, а ложный
    «прогул» в оценке филиала дороже пропущенного настоящего;
  • ухода раньше и переработок — это отдельный разговор про зарплату.

Модуль чистый: ни iiko, ни БД. Всё, что нужно, приходит аргументами — поэтому
логику можно проверить тестами, не поднимая ни сервера, ни базы.
"""
from typing import Any, Dict, Iterable, List, Optional, Tuple

DAY = 24 * 60
DEFAULT_GRACE_MIN = 10
# Больше этого — уже не опоздание, а смена, о которой мы не знаем. Проверено на
# живых явках Алиева: при одном старте 11:00 вечерние сотрудники (19:11, 21:47)
# получали «опоздание» на 8–10 часов и в одиночку топили оценку филиала. Три
# часа опоздания в ресторане не бывает — бывает вторая смена. Такие приходы
# помечаем «вне графика»: они видны на экране, но в знаменатель не идут.
# Лечится по-настоящему тем, что филиал заводит второй старт смены.
MAX_LATE_MIN = 120
# Время старта по умолчанию, пока филиал своё не задал. Утро — открытие кухни,
# вечер — пересменка. Оба перебиваются и настройкой филиала, и графиком.
DEFAULT_STARTS = ("09:00", "16:00")


def to_min(hhmm: str) -> Optional[int]:
    """'09:30' → 570. Мусор → None, а не исключение: явки приходят из чужой системы."""
    try:
        h, m = str(hhmm).strip().split(":")[:2]
        v = int(h) * 60 + int(m)
    except Exception:
        return None
    return v if 0 <= v < DAY else None


def _circle_gap(a: int, b: int) -> int:
    """Расстояние между временами по 24-часовому кругу: 23:50 и 00:10 — 20 минут,
    а не 1420. Иначе ночная смена всегда цеплялась бы к утреннему старту."""
    d = abs(a - b) % DAY
    return min(d, DAY - d)


def _lateness(arrival_m: int, planned_m: int) -> int:
    """На сколько минут позже плана. По кругу: план 23:00, приход 23:20 → 20;
    план 09:00, приход 08:50 → 0 (пришёл раньше — не опоздание)."""
    d = (arrival_m - planned_m) % DAY
    return d if d <= DAY // 2 else 0


def pick_planned(arrival: str,
                 shift_start: Optional[str] = None,
                 branch_starts: Iterable[str] = ()) -> Optional[str]:
    """Эталон для этого прихода: график сотрудника, иначе ближайший старт филиала.

    «Ближайший» — по кругу и по модулю: сотрудник вечерней смены, пришедший в
    15:40, сверяется с 16:00, а не с 09:00, и считается пришедшим вовремя.
    """
    if shift_start and to_min(shift_start) is not None:
        return shift_start
    a = to_min(arrival)
    if a is None:
        return None
    cands = [s for s in branch_starts if to_min(s) is not None]
    if not cands:
        return None
    return min(cands, key=lambda s: _circle_gap(a, to_min(s)))  # type: ignore[arg-type]


def late_rows(arrivals: List[Dict[str, Any]],
              schedule: Optional[Dict[Tuple[str, str], str]] = None,
              branch_starts: Iterable[str] = DEFAULT_STARTS,
              grace_min: int = DEFAULT_GRACE_MIN,
              max_late_min: int = MAX_LATE_MIN) -> List[Dict[str, Any]]:
    """Явки → строки «кто во сколько пришёл и опоздал ли».

    arrivals — строки отметок смены (employee_id, name, date, start).
    На сотрудника за день берём САМЫЙ РАННИЙ приход: терминал часто пишет
    несколько интервалов (разные роли), и поздний из них — не опоздание.
    schedule — {(employee_id, date): 'HH:MM'} плановое начало смены.
    """
    sched = schedule or {}
    starts = list(branch_starts or ())
    first: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for a in arrivals:
        eid, day, start = a.get("employee_id"), a.get("date"), a.get("start")
        if not eid or not day or to_min(start) is None:
            continue
        key = (eid, day)
        prev = first.get(key)
        if prev is None or to_min(start) < to_min(prev["start"]):  # type: ignore[operator]
            first[key] = a

    out: List[Dict[str, Any]] = []
    for (eid, day), a in first.items():
        arrival = a["start"]
        # Явка ещё не закрыта — человек прямо сейчас на смене. Для опоздания это
        # ничего не меняет (считаем по приходу), но экран должен это показывать.
        on_shift = bool(a.get("open"))
        planned = pick_planned(arrival, sched.get((eid, day)), starts)
        if planned is None:
            # Эталона нет — не «вовремя» и не «опоздал»: критерий по этому
            # человеку молчит, а не выдумывает результат.
            out.append({"employee_id": eid, "name": a.get("name") or eid[:8], "date": day,
                        "arrival": arrival, "planned": None, "late_min": 0,
                        "status": "noplan", "open": on_shift})
            continue
        late = _lateness(to_min(arrival), to_min(planned))  # type: ignore[arg-type]
        if late > max_late_min:
            # Ни к одному известному старту не подходит — молчим, а не выдумываем
            # опоздание в полсмены.
            out.append({"employee_id": eid, "name": a.get("name") or eid[:8], "date": day,
                        "arrival": arrival, "planned": None, "late_min": 0,
                        "status": "noplan", "open": on_shift})
            continue
        out.append({"employee_id": eid, "name": a.get("name") or eid[:8], "date": day,
                    "arrival": arrival, "planned": planned,
                    "late_min": late if late > grace_min else 0,
                    "status": "late" if late > grace_min else "ontime",
                    "open": on_shift})
    out.sort(key=lambda r: (r["date"], r["arrival"], r["name"]))
    return out


def by_employee(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Кто чаще опаздывает за период: одна строка на сотрудника.

    Знаменатель — только дни с эталоном (как в summarize): «вне графика» не
    делает человека ни хорошим, ни плохим. Сортировка по числу опозданий, потом
    по минутам — владельцу нужен не тот, кто разово опоздал на час, а тот, кто
    опаздывает каждый день.
    """
    agg: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        eid = r["employee_id"]
        e = agg.setdefault(eid, {"employee_id": eid, "name": r.get("name") or eid[:8],
                                 "days": 0, "late_days": 0, "late_minutes": 0,
                                 "worst_min": 0, "last_late": None, "noplan": 0})
        if r["status"] == "noplan":
            e["noplan"] += 1
            continue
        e["days"] += 1
        if r["status"] == "late":
            e["late_days"] += 1
            e["late_minutes"] += r["late_min"]
            if r["late_min"] > e["worst_min"]:
                e["worst_min"] = r["late_min"]
            if not e["last_late"] or r["date"] > e["last_late"]:
                e["last_late"] = r["date"]
    out = []
    for e in agg.values():
        e["late_pct"] = round(e["late_days"] / e["days"] * 100, 1) if e["days"] else None
        e["avg_late_min"] = round(e["late_minutes"] / e["late_days"]) if e["late_days"] else 0
        out.append(e)
    out.sort(key=lambda e: (-e["late_days"], -e["late_minutes"], e["name"]))
    return out


def by_day(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """История по дням: [{date, total, late, late_minutes}] — чтобы было видно,
    это разовый провал или так каждый день."""
    days: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        if r["status"] == "noplan":
            continue
        d = days.setdefault(r["date"], {"date": r["date"], "total": 0, "late": 0,
                                        "late_minutes": 0})
        d["total"] += 1
        if r["status"] == "late":
            d["late"] += 1
            d["late_minutes"] += r["late_min"]
    return [days[k] for k in sorted(days)]


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Сводка для экрана и для оценки филиала. Строки без эталона в знаменатель
    не идут: иначе филиал без заданного старта смены выглядел бы идеальным."""
    counted = [r for r in rows if r["status"] in ("late", "ontime")]
    late = [r for r in counted if r["status"] == "late"]
    return {
        "total": len(counted),
        "late": len(late),
        "late_pct": round(len(late) / len(counted) * 100, 1) if counted else None,
        "late_minutes": sum(r["late_min"] for r in late),
        "noplan": len(rows) - len(counted),
    }
