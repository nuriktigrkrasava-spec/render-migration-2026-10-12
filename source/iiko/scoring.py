"""Оценка филиала 0–100 и критерии сдачи чек-листа по зонам.

Принципы (по просьбе владельца):
  • оценка НЕ зависит от задач — иначе филиал поднимался бы, просто закрывая
    формулировки, а не наводя порядок;
  • каждый критерий логичен и проверяем: факт против нормы, а не «ощущение»;
  • чего нет в данных — не штрафуем: критерий выпадает, вес перераспределяется
    на остальные. Филиал без подключённых отзывов не должен быть «плохим»;
  • но и числа из воздуха не рисуем: посчиталось меньше MIN_COUNTED критериев —
    оценки нет, есть «мало данных». Прежняя версия при двух критериях на
    потолке показывала честные с виду 100 из 100 — ровно от этого и чинили;
  • веса лежат тут, одним местом — менять оценку можно, не трогая экраны.
"""
from typing import Any, Dict, List, Optional, Tuple

# Что снимать в каждой зоне. Список короткий намеренно: длинный не выполняют.
ZONE_CRITERIA: Dict[str, Dict[str, Any]] = {
    "hall": {
        "label": "Зал",
        "items": ["Пол под столами и у входа", "Столы и стулья без липкости и крошек",
                  "Витрина и стекло без разводов", "Урна не переполнена"],
    },
    "kitchen": {
        "label": "Кухня",
        "items": ["Рабочие поверхности пустые и вытертые", "Плита и вытяжка без жира",
                  "Холодильник: продукты закрыты и подписаны", "Пол у линии и под столами",
                  "Раковина без посуды"],
    },
    "wc": {
        "label": "Санузел",
        "items": ["Унитаз и раковина чистые", "Пол сухой", "Есть мыло и бумага",
                  "Зеркало без разводов", "Урна не переполнена"],
    },
}

# Критерии оценки: вес и как считается. Меняются здесь и только здесь.
# Состав задан владельцем 25.08: динамика выручки, рост числа заказов, опоздания
# доставок, опоздания сотрудников и чек-листы. Фудкост и списания убраны — на
# них у большинства филиалов не было данных, и оценка вырождалась в «100 из 100
# по двум критериям из пяти». Задач тут нет намеренно: оценка не должна расти
# от того, что закрыли формулировку.
WEIGHTS = {
    "revenue": 20,      # динамика выручки неделя к неделе
    "orders": 20,       # рост КОЛИЧЕСТВА заказов неделя к неделе
    "late": 15,         # опоздавшие заказы — чем больше, тем ниже
    "staff_late": 12,   # опоздания сотрудников на смену
    "checklist": 10,    # сдают ли чистоту вовремя
    "rating": 10,       # средний балл 2ГИС / Wolt / Yandex — голос гостя
    "plan": 8,          # выполнение недельного плана заказов
    "shifts": 5,        # непринятые кассовые смены — незакрытый учётный день
}

# Сколько критериев должно посчитаться, чтобы число вообще имело смысл.
# Без этого порога правило «чего нет — не штрафуем» давало ровно ту болезнь,
# из-за которой оценку и переделывали: два критерия на потолке → 100 из 100.
# Критериев стало восемь — порог подняли до четырёх: три из восьми это снова
# «сто баллов по четверти данных».
MIN_COUNTED = 4


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def _revenue(b: Dict[str, Any]) -> Optional[float]:
    """Динамика выручки неделя к неделе: −10% и хуже — ноль, +10% и лучше — единица."""
    ch = b.get("changes")
    if not isinstance(ch, dict) or ch.get("revenue_pct") is None:
        return None
    return _clamp((float(ch["revenue_pct"]) + 10) / 20)


def _orders(b: Dict[str, Any]) -> Optional[float]:
    """Рост числа заказов неделя к неделе — та же шкала, что у выручки.

    Считается отдельно от выручки намеренно: выручку можно вытянуть ценой,
    а заказы — это гости. Филиал, поднявший чек и потерявший людей, здесь
    получит по рукам, хотя по выручке выглядит растущим.
    """
    ch = b.get("changes")
    if not isinstance(ch, dict) or ch.get("orders_pct") is None:
        return None
    return _clamp((float(ch["orders_pct"]) + 10) / 20)


# Опоздания сотрудников — единственный критерий, который филиал не может
# исправить задним числом: люди уже пришли. Раньше 20% опоздавших обнуляли его
# целиком, а 20% на живых данных — это норма (когда на филиале один старт смены
# на утро и вечер, «опоздавшими» становятся все вечерние). Филиалы теряли весь
# критерий и оценка переставала показывать что-либо, кроме опозданий. Решение
# владельца 27.08: не обнулять, а немного снижать — максимум на 40% веса.
STAFF_LATE_FLOOR = 0.6


def _staff_late(b: Dict[str, Any]) -> Optional[float]:
    """Опоздания сотрудников: доля опоздавших явок. 0% — единица,
    100% — STAFF_LATE_FLOOR (в ноль критерий не уходит никогда)."""
    sl = b.get("staff_late")
    if not isinstance(sl, dict):
        return None
    pct = sl.get("late_pct")
    if pct is None:
        total, late_n = sl.get("total"), sl.get("late")
        if not total:
            return None
        pct = float(late_n or 0) / float(total) * 100
    return _clamp(1 - (1 - STAFF_LATE_FLOOR) * float(pct) / 100)


def _late(b: Dict[str, Any]) -> Optional[float]:
    """Опоздавшие заказы: доля опоздавших доставок. 0% — единица, 15% — ноль."""
    lt = b.get("late")
    if not isinstance(lt, dict):
        return None
    pct = lt.get("late_pct")
    if pct is None:
        total, late_n = lt.get("total"), lt.get("late")
        if not total:
            return None
        pct = float(late_n or 0) / float(total) * 100
    return _clamp(1 - float(pct) / 15)


def _plan(b: Dict[str, Any]) -> Optional[float]:
    """Выполнение недельного плана заказов: 100% и выше — единица, 70% — ноль.

    Вес намеренно небольшой (решение владельца 25.08: «не сильно»). Плановый
    критерий с большим весом учит не работать, а торговаться за низкий план:
    филиал влияет на собственный знаменатель, чего нет у выручки и опозданий.
    """
    pl = b.get("plan")
    if not isinstance(pl, dict) or pl.get("pct") is None:
        return None
    return _clamp((float(pl["pct"]) - 70) / 30)


def _rating(b: Dict[str, Any]) -> Optional[float]:
    """Средний балл агрегаторов: 4,8 — единица, 3,8 — ноль.

    Шкала снята с таблицы владельца: там по сети 3,9–4,8, и разброс внутри этой
    вилки — вся разница между «жалуются» и «хвалят». Ставить 0..5 бессмысленно:
    ниже 3,5 в агрегаторах не живут, и вся сеть слиплась бы в верхней части.
    """
    rt = b.get("rating")
    if not isinstance(rt, dict) or rt.get("avg") is None:
        return None
    return _clamp((float(rt["avg"]) - 3.8) / 1.0)


def _shifts(b: Dict[str, Any]) -> Optional[float]:
    """Непринятые кассовые смены: ни одной — единица, пять и больше — ноль.

    Ноль непринятых и отсутствие данных — разные вещи: у облачного филиала касс
    нет вовсе, и он не должен получать за это сто баллов. Поэтому смотрим на
    наличие ключа, а не на его правдивость.
    """
    sh = b.get("shifts")
    if not isinstance(sh, dict) or sh.get("pending") is None:
        return None
    return _clamp(1 - float(sh["pending"]) / 5)


def _checklist(ch: Dict[str, Any]) -> Optional[float]:
    if not ch:
        return None
    done, missed = int(ch.get("done") or 0), abs(int(ch.get("missed") or 0))
    if done + missed == 0:
        return None
    return _clamp(done / (done + missed))


def score_branch(signals: Optional[Dict[str, Any]],
                 checklist: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Оценка филиала. Возвращает балл и разбор по критериям — чтобы владелец
    видел, из чего он сложился, а не верил числу на слово."""
    b = signals or {}
    parts: List[Tuple[str, str, Optional[float], int]] = [
        ("revenue", "Динамика выручки", _revenue(b), WEIGHTS["revenue"]),
        ("orders", "Рост заказов", _orders(b), WEIGHTS["orders"]),
        ("late", "Опоздавшие заказы", _late(b), WEIGHTS["late"]),
        ("staff_late", "Опоздания сотрудников", _staff_late(b), WEIGHTS["staff_late"]),
        ("checklist", "Чек-листы", _checklist(checklist or {}), WEIGHTS["checklist"]),
        ("rating", "Рейтинг агрегаторов", _rating(b), WEIGHTS["rating"]),
        ("plan", "Выполнение плана", _plan(b), WEIGHTS["plan"]),
        ("shifts", "Непринятые смены", _shifts(b), WEIGHTS["shifts"]),
    ]
    criteria = [
        {"key": k, "label": label, "value": None if v is None else round(v * 100),
         "weight": w, "counted": v is not None}
        for k, label, v, w in parts
    ]
    have = [(k, label, v, w) for k, label, v, w in parts if v is not None]
    if len(have) < MIN_COUNTED:
        # Числа не даём: посчитанного слишком мало, чтобы оно что-то значило.
        missing = [label for k, label, v, _w in parts if v is None]
        return {"score": None, "criteria": criteria,
                "score_note": "мало данных: " + ", ".join(m.lower() for m in missing),
                "counted": len(have)}
    total_w = sum(w for _k, _l, _v, w in have)
    score = round(sum(v * w for _k, _l, v, w in have) / total_w * 100)
    return {"score": int(score), "criteria": criteria, "counted": len(have),
            "score_note": None if len(have) == len(parts) else
            "считано %d критерия из %d" % (len(have), len(parts))}
