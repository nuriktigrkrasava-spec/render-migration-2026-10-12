"""Разбор фото чек-листов моделью — с четырьмя предохранителями по деньгам.

Слепой прогон стоил бы около миллиона токенов в день: 4 слота × 3 зоны ×
до 8 кадров × 9 филиалов, кадр 1024×768 в vision ≈ 1 050 токенов. Поэтому:

  1. КАДР ДО 512 px по длинной стороне — vision считает по площади,
     512×384 это ≈ 260 токенов вместо 1 050. Грязь на полу видно так же.
  2. ОДИН ЗАПРОС НА СЛОТ, а не на кадр: три зоны идут одним документом, и
     системный промпт оплачивается один раз вместо трёх.
  3. ВЫБОРОЧНЫЙ КОНТРОЛЬ: через модель идут не все сдачи, а доля (SAMPLE_PCT)
     плюс все спорные — сдано с опозданием, вчера было нарушение, мало кадров.
     Остальное просто лежит: человек посмотрит, если понадобится.
  4. КЭШ ПО ХЕШУ: тот же кадр второй раз не оплачивается.

Рубильник свой — CHECKLIST_AI. По умолчанию ВЫКЛЮЧЕН: пока владелец не сказал
«включай», ни один кадр в модель не уходит.
"""
import hashlib
import io
import json
import os
from typing import Any, Dict, List, Optional, Tuple

# Разбор фото ВКЛЮЧЁН. Выключается явным CHECKLIST_AI=0 — без деплоя.
#
# Раньше по умолчанию было выключено, и включали переменной на Render. Владелец
# попросил включить, а доступа к панели Render у ассистента нет, поэтому рубильник
# переехал в код: теперь «включено, пока не сказано обратное».
AI_ENABLED = (os.getenv("CHECKLIST_AI") or "1") != "0"
MODEL = os.getenv("CHECKLIST_MODEL", "claude-sonnet-5")
MAX_SIDE = int(os.getenv("CHECKLIST_MAX_SIDE", "512"))
SAMPLE_PCT = int(os.getenv("CHECKLIST_SAMPLE_PCT", "20"))
MAX_TOKENS = int(os.getenv("CHECKLIST_MAX_TOKENS", "700"))
MAX_PHOTOS_PER_SLOT = int(os.getenv("CHECKLIST_MAX_PHOTOS", "6"))

_client = None
if AI_ENABLED and os.getenv("ANTHROPIC_API_KEY"):
    try:
        from anthropic import AsyncAnthropic
        _client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    except Exception:
        _client = None

# Модель отвечает ПО КРИТЕРИЯМ, а не свободным текстом. Раньше приходил общий
# вердикт «ok / не ok» плюс россыпь замечаний, и управляющий не понимал, что
# именно зачли: список «что снять» на экране жил сам по себе, разбор — сам по
# себе. Теперь это один и тот же список, и по каждому пункту виден ответ.
PROMPT = (
    "Ты проверяешь чистоту в ресторане по фото. На вход — кадры одной сдачи "
    "чек-листа, перед каждым кадром указана его зона.\n\n"
    "Ниже — критерии по зонам. Ответь по КАЖДОМУ критерию той зоны, кадры "
    "которой тебе прислали. Критерии зон, кадров которых нет, пропусти.\n"
    "{criteria}\n"
    "Верни СТРОГО JSON без пояснений:\n"
    '{{"ok":true|false,"criteria":[{{"zone":"<зона>","item":"<критерий дословно>",'
    '"ok":true|false,"what":"<если не выполнен — что видно, до 80 знаков>"}}]}}\n\n'
    "Правила:\n"
    "• item копируй ДОСЛОВНО из списка — по нему ответ сходится с чек-листом;\n"
    "• ok=false ставь только на ВИДИМОЕ нарушение: грязный пол, мусор, разводы, "
    "немытая посуда, открытые продукты, переполненная урна;\n"
    "• если по кадрам критерий не видно — ok=true и what пустой: наказывать за "
    "ракурс нельзя, это работа фотографа, а не уборщика;\n"
    "• общий ok=false, если хотя бы один критерий не выполнен;\n"
    "• не придирайся к мелочам — задача поймать реальную грязь, а не идеальный "
    "порядок."
)


def criteria_text(criteria: Optional[Dict[str, Dict[str, Any]]], zones: List[str]) -> str:
    """Список критериев для промпта — только по зонам, кадры которых прислали."""
    if not criteria:
        return ""
    want = {z.strip().lower() for z in zones}
    out = []
    for spec in criteria.values():
        label = str(spec.get("label") or "")
        if want and label.strip().lower() not in want:
            continue
        items = "\n".join(f"  - {i}" for i in (spec.get("items") or []))
        out.append(f"{label}:\n{items}")
    return "\n".join(out)


def issues_from_criteria(criteria: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Невыполненные критерии → замечания в прежнем формате {zone, what}.

    Формат ответа не ломаем: доска владельца, «Гора» и уведомления читают
    issues, и переучивать их ради нового поля незачем.
    """
    out = []
    for c in criteria or []:
        if c.get("ok") is False:
            what = (c.get("what") or "").strip()
            item = (c.get("item") or "").strip()
            out.append({"zone": c.get("zone") or "",
                        "what": f"{item} — {what}" if what and item else (what or item)})
    return out


def photo_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:32]


def shrink(data: bytes, max_side: int = MAX_SIDE) -> Tuple[bytes, str]:
    """Кадр → JPEG не длиннее max_side. Без Pillow отдаём как есть."""
    try:
        from PIL import Image
    except Exception:
        return data, "image/jpeg"
    try:
        im = Image.open(io.BytesIO(data))
        im = im.convert("RGB")
        k = min(1.0, max_side / max(im.size))
        if k < 1.0:
            im = im.resize((int(im.width * k), int(im.height * k)), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=72, optimize=True)
        return buf.getvalue(), "image/jpeg"
    except Exception:
        return data, "image/jpeg"


def tokens_for(width: int, height: int) -> int:
    """Оценка стоимости кадра в токенах — по площади (примерно w*h/750)."""
    return int(width * height / 750)


def should_review(upload: Dict[str, Any], *, late: bool = False,
                  had_issue_yesterday: bool = False,
                  rnd: Optional[float] = None) -> Tuple[bool, str]:
    """Гнать ли эту сдачу через модель. Возвращает (да/нет, причина).

    Спорные случаи проверяем всегда, остальное — выборочно: смысл не в тотальном
    контроле, а в том, чтобы管 знал, что могут проверить.
    """
    if late:
        return True, "сдано с опозданием"
    if had_issue_yesterday:
        return True, "вчера было нарушение"
    photos = upload.get("photos") or []
    if len(photos) < 3:
        return True, "меньше трёх кадров"
    import random
    r = rnd if rnd is not None else random.random()
    if r * 100 < SAMPLE_PCT:
        return True, "выборочный контроль"
    return False, "не проверяем"


async def review_slot(photos: List[Tuple[bytes, str]],
                      cache: Optional[Dict[str, Any]] = None,
                      criteria: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Один запрос на слот: все зоны одним документом.

    cache — {хеш кадра: результат}; повторный кадр не оплачивается.
    Без ключа или с выключенным рубильником возвращает «не проверяли».
    """
    if not photos:
        return {"checked": False, "reason": "нет кадров"}
    cache = cache if cache is not None else {}
    keys = [photo_hash(d) for d, _ in photos]
    if keys and all(k in cache for k in keys):
        return {"checked": True, "cached": True, **cache[keys[0]]}
    if _client is None:
        return {"checked": False, "reason": "разбор фото выключен (CHECKLIST_AI)"}

    content: List[Dict[str, Any]] = []
    for data, zone in photos[:MAX_PHOTOS_PER_SLOT]:
        small, mime = shrink(data)
        import base64
        content.append({"type": "text", "text": f"Зона: {zone}"})
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": mime,
            "data": base64.b64encode(small).decode()}})
    zones = [z for _, z in photos[:MAX_PHOTOS_PER_SLOT]]
    system = PROMPT.replace("{criteria}", criteria_text(criteria, zones))
    try:
        resp = await _client.messages.create(
            model=MODEL, max_tokens=MAX_TOKENS, system=system,
            messages=[{"role": "user", "content": content}])
        txt = "".join(getattr(c, "text", "") for c in resp.content).strip()
        start, end = txt.find("{"), txt.rfind("}")
        data = json.loads(txt[start:end + 1]) if start >= 0 and end > start else {}
        crit = [c for c in (data.get("criteria") or []) if isinstance(c, dict)]
        # Замечания собираем из невыполненных критериев; свободный список от
        # модели принимаем, только если критериев она не вернула вовсе.
        issues = issues_from_criteria(crit) or (data.get("issues") or [])
        # Общий вердикт не берём на веру: модель ставила ok=true, забыв про
        # собственный же проваленный критерий. Считаем сами.
        ok = bool(data.get("ok")) and not any(c.get("ok") is False for c in crit)
        out = {"checked": True, "ok": ok, "criteria": crit, "issues": issues}
    except Exception as e:
        return {"checked": False, "reason": f"модель не ответила: {e!r}"[:160]}
    for k in keys:
        cache[k] = {"ok": out["ok"], "criteria": out["criteria"], "issues": out["issues"]}
    return out
