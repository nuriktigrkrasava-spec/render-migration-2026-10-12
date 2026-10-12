"""ИИ-ассистент «Директор»: детерминированные сигналы → советы.

rules_advice — фоллбэк без сети (числа только из сигналов). advise — Claude
поверх тех же сигналов (грудинг), с деградацией на rules_advice.
Пo образцу claude_api.py."""
import json
import os
from typing import Any, Dict, List, Optional

from anthropic import AsyncAnthropic

# Haiku на этой задаче писала под копирку («у всех фудкост»), владелец сказал —
# это не продукт. Sonnet 5 даёт живой разбор при вменяемой цене; ассистент зовётся
# раз в 10 минут на кабинет (кэш _cache_key), так что счёт остаётся небольшим.
MODEL = os.getenv("ASSISTANT_MODEL", "claude-sonnet-5")

# РУБИЛЬНИК. Одного наличия ANTHROPIC_API_KEY мало: ключ в окружении нужен и боту
# (разбор накладных), и insights, а разбор ассистента — самый дорогой вызов в проекте
# (13 филиалов в одном запросе). Платим за него только по явному ASSISTANT_AI=1,
# иначе работают бесплатные правила. Так «забыл выключить» не превращается в счёт.
AI_ENABLED = os.getenv("ASSISTANT_AI", "0") == "1"
_client = (AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
           if AI_ENABLED and os.getenv("ANTHROPIC_API_KEY") else None)

DOW_RU = {0: "Пн", 1: "Вт", 2: "Ср", 3: "Чт", 4: "Пт", 5: "Сб", 6: "Вс"}


def _adv(branch, severity, title, detail, money, action=None) -> Dict[str, Any]:
    return {"id": f"{branch}:{title}"[:80], "branch": branch, "severity": severity,
            "title": title, "detail": detail, "money": money, "action": action}


def _fmt_pct(v: float) -> str:
    return f"{v:+.1f}".replace(".", ",").rstrip("0").rstrip(",") + "%"


def wins(branch: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Похвала: за выполненный недельный план и за выросший показатель.

    Причину роста называем ТОЛЬКО когда она видна в данных — по снимку прошлого
    раза (prev) понятно, что владелец реально сделал: заполнил техкарты, закрыл
    стоп-лист, убрал списания. Если такого следа нет — хвалим за результат без
    придуманного «благодаря чему»: выдуманная причина хуже, чем её отсутствие.
    """
    name = branch.get("name") or "Филиал"
    out: List[Dict[str, Any]] = []

    # ── что владелец сделал с прошлого раза (управляемые входы) ──
    # Причину привязываем ТОЛЬКО к тому показателю, на который она реально влияет:
    # «рейтинг вырос благодаря техкартам» — бессмыслица, которая обесценивает похвалу.
    prev = branch.get("prev") or {}
    fc = branch.get("foodcost") or {}
    wo = branch.get("writeoffs") or {}
    did: Dict[str, str] = {}
    cov, cov0 = fc.get("coverage"), prev.get("coverage")
    if cov is not None and cov0 is not None and cov - cov0 >= 5:
        did["recipes"] = f"техкарты заполнены плотнее ({cov0:.0f}% → {cov:.0f}% выручки)"
    sc, sc0 = branch.get("stop_count"), prev.get("stop_count")
    if sc is not None and sc0 is not None and sc0 > 0 and sc < sc0:
        did["stop"] = f"стоп-лист разобран ({sc0} → {sc} позиций)"
    wp, wp0 = wo.get("pct"), prev.get("writeoffs_pct")
    if wp is not None and wp0 is not None and wp0 - wp >= 1:
        did["writeoffs"] = f"списания снижены ({wp0:.1f}% → {wp:.1f}% закупа)"
    cn, cn0 = branch.get("cancels"), prev.get("cancels")
    if cn is not None and cn0 is not None and cn0 > 0 and cn < cn0 * 0.8:
        did["cancels"] = "отмен и возвратов стало меньше"

    def because(*kinds: str) -> str:
        """Причина роста — только из действий, влияющих на ЭТОТ показатель."""
        for k in kinds:
            if k in did:
                return f" Похоже, сработало: {did[k]}."
        return ""

    # ── план недели выполнен ──
    wpl = branch.get("weekplan") or {}
    done = [t for t in (wpl.get("types") or []) if t.get("done")]
    if done:
        parts = ", ".join(f"{t['type']} {t['fact_orders']} из {t['plan_orders']}" for t in done)
        missed = [t for t in (wpl.get("types") or []) if not t.get("done")]
        missed_txt = ", ".join("{} {}/{}".format(t["type"], t["fact_orders"], t["plan_orders"])
                               for t in missed)
        tail = (f" По остальному ещё недобор: {missed_txt}."
                if missed else " Неделя закрыта полностью.")
        # Факт втрое выше плана — план занижен (встречается «зал 23» при факте 510).
        # Хвалим всё равно, но честно говорим, что планку пора поднять: молча
        # поздравлять с заниженной целью — обманывать владельца.
        if any(t["fact_orders"] > t["plan_orders"] * 3 for t in done):
            tail += " Факт кратно выше плана — планку стоит поднять, иначе цель не мотивирует."
        out.append(_adv(name, "good", "План недели выполнен",
                        f"{name}: за неделю {wpl.get('week')} план по заказам взят — {parts}.{tail}",
                        None, {"type": "navigate", "tab": "plan", "hint": None}))

    # ── показатель вырос ──
    ch = branch.get("changes") or {}
    wk = f" ({ch.get('week')} против {ch.get('prev_week')})" if ch.get("week") else ""
    rev_pct = ch.get("revenue_pct")
    if isinstance(rev_pct, (int, float)) and rev_pct >= 5:
        # выручку двигают вернувшиеся в продажу позиции и упавшие отмены
        out.append(_adv(name, "good", "Выручка за неделю выросла",
                        f"{name}: выручка {_fmt_pct(rev_pct)} к прошлой неделе{wk}."
                        f"{because('stop', 'cancels')}",
                        None, {"type": "navigate", "tab": "dash", "hint": None}))
    ac_pct = ch.get("avg_check_pct")
    if isinstance(ac_pct, (int, float)) and ac_pct >= 5:
        # средний чек не привязываем ни к одному из наших входов — честнее без причины
        out.append(_adv(name, "good", "Средний чек вырос",
                        f"{name}: средний чек {_fmt_pct(ac_pct)} к прошлой неделе — "
                        f"гости берут дороже или больше позиций.",
                        None, {"type": "navigate", "tab": "menu", "hint": None}))

    # ── фудкост снизился (снимок прошлого раза) ──
    f_now, f_prev = fc.get("fact_pct"), prev.get("foodcost_pct")
    if (f_now is not None and f_prev is not None and f_prev - f_now >= 1
            and (fc.get("coverage") or 0) >= 70):
        out.append(_adv(name, "good", "Фудкост снизился",
                        f"{name}: фудкост {f_prev:.0f}% → {f_now:.0f}% при норме "
                        f"{fc.get('norm_pct', 30)}%.{because('recipes', 'writeoffs')}",
                        None, {"type": "navigate", "tab": "fc", "hint": None}))

    # ── рейтинг подрос ──
    for p in (branch.get("reviews") or []):
        r_now, label = p.get("rating"), p.get("label")
        r_prev = (prev.get("ratings") or {}).get(label)
        if r_now is not None and r_prev is not None and r_now - r_prev >= 0.1:
            # причину рейтинга из наших входов не вывести — хвалим за факт
            out.append(_adv(name, "good", f"Рейтинг вырос · {label}",
                            f"{name}: {label} {r_prev} → {r_now}.",
                            None, {"type": "navigate", "tab": "rep", "hint": None}))
            break  # одна площадка на филиал, чтобы не заваливать похвалой
    # не больше двух похвал на филиал: третья превращает признание в шум
    return out[:2]


def rules_advice(signals: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Детерминированный фоллбэк. Все числа берутся из signals, ничего не выдумываем."""
    out: List[Dict[str, Any]] = []
    for b in signals.get("branches", []):
        name = b.get("name") or "Филиал"
        out.extend(wins(b))  # сначала за что похвалить, потом за что взяться
        fc = b.get("foodcost")
        rev = b.get("revenue") or 0
        # фудкост выше нормы → деньги = перерасход к норме
        if fc and fc.get("fact_pct") is not None and fc.get("fact_pct") > fc.get("norm_pct", 30):
            over = (fc["fact_pct"] - fc["norm_pct"]) / 100 * (fc.get("fact_sum", 0) / (fc["fact_pct"] / 100) if fc["fact_pct"] else 0)
            out.append(_adv(name, "high", "Фудкост выше нормы",
                            f"{name}: фудкост {fc['fact_pct']:.0f}% при норме {fc['norm_pct']}%. "
                            f"Пересобери техкарты топ-блюд и передоговорись с поставщиками.",
                            round(over) if over > 0 else None,
                            {"type": "navigate", "tab": "fc", "hint": None}))
        # техкарт нет → фудкост не считается: план (во вкладку Директор)
        if b.get("has_recipes") is False:
            out.append(_adv(name, "med", "Техкарты не заведены",
                            f"{name}: заполни техкарты в iiko, чтобы считать фудкост.",
                            None, {"type": "navigate", "tab": "director", "hint": None}))
        # техкарты есть, но покрывают мало выручки → фудкост неполный
        elif b.get("has_recipes") is True and fc and fc.get("coverage") is not None and fc["coverage"] < 70:
            cov = fc["coverage"]
            out.append(_adv(name, "med", "Техкарты неполные",
                            f"{name}: техкарты покрывают {cov}% выручки — заполни остальные "
                            f"во вкладке Директор, чтобы фудкост считался полностью.",
                            None, {"type": "navigate", "tab": "director", "hint": None}))
        # отмены/возвраты
        cn = b.get("cancels") or 0
        if cn > 0:
            out.append(_adv(name, "med", "Отмены и возвраты",
                            f"{name}: отмен и возвратов на {cn:,.0f} ₸ за период — разбери топ-причину.".replace(",", " "),
                            cn, {"type": "navigate", "tab": "supply", "hint": None}))
        # стоп-лист: ходовые позиции с 0 в остатке = упущенная выручка
        sc = b.get("stop_count") or 0
        if sc > 0:
            top = ", ".join(b.get("stop_items") or [])
            out.append(_adv(name, "med", "Стоп-лист — упущенная выручка",
                            f"{name}: {sc} позиций в стоп-листе (0 в остатке){f' — {top}' if top else ''}. "
                            f"Гости не могут заказать ходовое — дозакажи, вернёшь выручку.",
                            None, {"type": "navigate", "tab": "supply", "hint": None}))
        # списания / порча
        wo = b.get("writeoffs")
        if wo and (wo.get("pct") or 0) > 5:
            out.append(_adv(name, "med", "Списания выше нормы",
                            f"{name}: списано {wo.get('total'):,.0f} ₸ ({wo.get('pct')}% закупа) — порча/перезаказ, деньги в мусор.".replace(",", " "),
                            wo.get("total"), {"type": "navigate", "tab": "supply", "hint": None}))
        # соцсети не подключены
        if b.get("has_socials") is False:
            out.append(_adv(name, "low", "Соцсети не подключены",
                            f"{name}: площадки отзывов (2ГИС/Wolt) не найдены — укажи бренд в «Подключить».",
                            None, {"type": "navigate", "tab": "rep", "hint": None}))
        # слабые отзывы → одна карточка на филиал (худшая площадка)
        worst = None
        for p in (b.get("reviews") or []):
            rating = p.get("rating")
            if rating is None:
                continue
            delta = p.get("delta")
            low = rating < 4 if rating <= 5 else rating < 8
            neg = isinstance(delta, (int, float)) and delta < 0
            if (low or neg) and (worst is None or rating < worst.get("rating", 1e9)):
                worst = p
        if worst is not None:
            wr, wl = worst.get("rating"), worst.get("label")
            wd = worst.get("delta")
            falling = " (падает)" if isinstance(wd, (int, float)) and wd < 0 else ""
            out.append(_adv(name, "med", f"Слабый отзыв · {wl}",
                            f"{name}: {wl} рейтинг {wr}{falling} — разбери свежие жалобы.",
                            None, {"type": "navigate", "tab": "rep", "hint": None}))
        # час-пик → штат
        peak = b.get("peak")
        if peak:
            out.append(_adv(name, "low", "Час-пик — усилить смену",
                            f"{name}: пик нагрузки {peak.get('day')} около {peak.get('hour')}:00 — "
                            f"добавь официантов на этот слот.",
                            None, {"type": "navigate", "tab": "sched", "hint": None}))
    # похвала идёт первой (её money=None и по деньгам она бы утонула), дальше —
    # проблемы по убыванию денег
    out.sort(key=lambda a: (a["severity"] != "good", -(a["money"] or 0)))
    return out


def _parse_advice(raw_text: str) -> List[Dict[str, Any]]:
    """Снимает markdown-обёртку и парсит JSON-массив советов. Бросает при мусоре."""
    t = raw_text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1]
        if t.startswith("json"):
            t = t[4:]
        t = t.strip("`").strip()
    data = json.loads(t)
    if isinstance(data, dict) and "advice" in data:
        data = data["advice"]
    if not isinstance(data, list):
        raise ValueError("advice не массив")
    # оставляем только корректные объекты: не-dict элемент (null/строка/число)
    # уронил бы рендер фронта (a.id/a.severity на не-объекте) — защищаемся тут.
    return [x for x in data if isinstance(x, dict)]


_SYSTEM = (
    "Ты — опытный операционный директор сети ресторанов, который лично отвечает за прибыль. "
    "Говоришь как живой практик-управленец, а не как бот: коротко, по делу, с цифрой и конкретным действием. "
    "КАК ТЫ ПИШЕШЬ (это важнее всего остального — владелец забракует шаблонный текст):\n"
    "— НИКАКИХ повторяющихся конструкций. Если про один филиал написал 'фудкост X% при норме Y% — пересобери "
    "техкарты', про следующий сформулируй ИНАЧЕ: другой заход, другой порядок слов, другой глагол. "
    "Одинаковые фразы у двух филиалов = провал задачи.\n"
    "— Пиши как человек, который эти рестораны видел: 'В Актобе доставка почти встала — 13 заказов за неделю "
    "при плане 130. Это не спад, это что-то сломалось: проверь, включён ли филиал в агрегаторах'. "
    "А не 'Показатель доставки ниже планового значения'.\n"
    "— Не начинай каждый detail с названия филиала — оно и так есть в поле branch. Начинай с сути.\n"
    "— Никакого канцелярита: 'необходимо оптимизировать', 'рекомендуется обратить внимание', "
    "'наблюдается снижение' — запрещено. Говори: 'теряешь', 'проверь', 'сорвалось', 'вытащи'.\n"
    "— Свяжи факты между собой, если они связаны: стоп-лист + падение выручки = одна история, а не два совета.\n"
    "Тебе дают УЖЕ ПОСЧИТАННЫЕ числа по каждому филиалу (фудкост, выручка, отмены, списания, отзывы, час-пик). "
    "Используй ТОЛЬКО эти числа — ничего не выдумывай, проценты и суммы бери из данных.\n"
    "ТВОЯ ЗАДАЧА — замечать ВСЁ, что вредит ПРИБЫЛИ, КАЧЕСТВУ и РЕПУТАЦИИ ресторана, и говорить об этом. "
    "Смотри на весь набор: отмены/возвраты, СТОП-ЛИСТ (ходовые позиции с 0 в остатке = упущенная выручка), "
    "СПИСАНИЯ/порча (деньги в мусор), слабый отзыв/низкий рейтинг (уводит гостей — репутация), час-пик без штата, "
    "падение выручки/среднего чека, неполные техкарты, высокий фудкост. "
    "ГЛАВНОЕ — НЕ ШАБЛОНЬ: у разных филиалов РАЗНЫЕ боли, показывай именно их, а не одно и то же.\n"
    "ВАЖНО про фудкост: если у филиала foodcost.coverage < 70% — процент фудкоста ПОСЧИТАН НА НЕПОЛНЫХ "
    "ТЕХКАРТАХ и НЕНАДЁЖЕН. Тогда НЕ пиши 'фудкост X%' как проблему (это кривая цифра) — вместо этого дай совет "
    "'заполни техкарты, покрытие только Y%' (tab 'director') и разбери ДРУГИЕ, надёжные проблемы филиала "
    "(отмены, отзыв, час-пик). Процент фудкоста подавай как проблему ТОЛЬКО если coverage >= 70%.\n"
    "Дай на филиал 1–2 совета: его главная надёжная боль + при желании вторая по деньгам — чтобы картина была "
    "живой и разной, а не 'у всех фудкост'. Если реально всё ок — не выдумывай.\n"
    "ХВАЛИ ЗА РЕЗУЛЬТАТ (severity 'good'). Похвала обязательна для КАЖДОГО филиала, где есть хоть одно из:\n"
    "— weekplan: есть тип с done=true → 'план недели выполнен', назови тип, факт и план числами. "
    "Если таких филиалов восемь — похвали ВСЕ ВОСЕМЬ, разными словами. Пропустить филиал, который "
    "выполнил план, — обидеть человека, который его выполнил;\n"
    "— changes (неделя к прошлой неделе): revenue_pct / avg_check_pct / guests_pct >= 5 → показатель вырос, "
    "назови КАКОЙ, НА СКОЛЬКО и за какую неделю (changes.week против changes.prev_week);\n"
    "— prev: foodcost стал ниже prev.foodcost_pct, рейтинг выше prev.ratings[площадка].\n"
    "Если факт кратно выше плана (fact > plan*3) — всё равно похвали, но скажи прямо, что планка занижена "
    "и её пора поднять.\n"
    "В похвале ВСЕГДА называй конкретный показатель и его числа (было → стало).\n"
    "ПРИЧИНУ роста ('благодаря чему') указывай ТОЛЬКО если она видна в данных: сравни текущие значения с prev — "
    "coverage вырос (заполнили техкарты), stop_count упал (разобрали стоп-лист), writeoffs_pct упал (убрали списания), "
    "cancels упали. Если ничего из этого не изменилось — просто похвали за показатель и НЕ придумывай причину: "
    "выдуманное 'благодаря вашей работе с меню' — это ложь, она обесценивает похвалу.\n"
    "Похвала идёт ПЕРВОЙ в массиве, у неё money=null.\n"
    "Верни JSON-массив советов: сначала похвала ('good'), затем проблемы по деньгам убыв. Каждый совет: "
    "{id, branch, severity('good'|'high'|'med'|'low'), title, detail, money(число ₸/мес или null), "
    "action(null | {type:'navigate'|'showMetric', tab, hint})}. tab — один из: "
    "'fc'(фудкост),'supply'(склад),'rep'(соцсети),'sched'(расписание),'director'(директор),"
    "'plan'(недельный план),'dash'(главная),'menu'(меню и продажи). "
    "Пиши по-русски, конкретно: филиал, время, сумма, один шаг (не общими словами). "
    "Лимит — 40 советов: при 13 филиалах на каждый приходится похвала + 1–2 боли, и обрезать этот список "
    "нельзя, иначе половина филиалов останется без разбора. "
    "Только JSON, без пояснений."
)


def _slim(x: Any) -> Any:
    """Чистит сигналы перед отправкой: пустые поля и хвосты дробей — это оплаченные
    входные токены, которые модели ничего не говорят. На 13 филиалах экономит заметно."""
    if isinstance(x, dict):
        out = {}
        for k, v in x.items():
            v = _slim(v)
            if v is None or v == [] or v == {} or v == "":
                continue
            out[k] = v
        return out
    if isinstance(x, list):
        return [_slim(i) for i in x]
    if isinstance(x, float):
        return round(x, 1)
    return x


async def advise(signals: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Claude поверх сигналов; при любой проблеме — rules_advice."""
    if _client is None:
        return rules_advice(signals)
    signals = _slim(signals)
    try:
        # Стрим обязателен: на таком max_tokens SDK отклоняет обычный запрос
        # («Streaming is required for operations that may take longer than 10 minutes»).
        # Ответ всё равно собираем целиком — фронту нужен готовый JSON.
        async with _client.messages.stream(
            model=MODEL,
            # max_tokens — потолок, а не счёт: платим за фактические токены. Держим
            # с запасом, иначе текстовый блок приходит пустым (размышление съедает лимит).
            max_tokens=int(os.getenv("ASSISTANT_MAX_TOKENS", "32000")),
            # ГЛАВНЫЙ РЫЧАГ ЦЕНЫ. Токены размышления оплачиваются как выходные ($15/1М),
            # а без output_config действует effort=high — модель думает по максимуму.
            # Задача тут не «решить сложную проблему», а описать УЖЕ ПОСЧИТАННЫЕ числа,
            # поэтому low хватает. Поднять до medium/high, если текст станет хуже.
            output_config={"effort": os.getenv("ASSISTANT_EFFORT", "low")},
            system=_SYSTEM,
            messages=[{"role": "user", "content": json.dumps(signals, ensure_ascii=False)}],
        ) as stream:
            msg = await stream.get_final_message()
        u = msg.usage
        # Цена вызова в логе: без неё «почему счёт вырос» опять придётся угадывать.
        cost = (u.input_tokens / 1e6) * 3 + (u.output_tokens / 1e6) * 15
        print(f"[assistant] токены: вход {u.input_tokens}, выход {u.output_tokens} "
              f"→ ≈ ${cost:.3f} (effort={os.getenv('ASSISTANT_EFFORT', 'low')})")
        text = next((b.text for b in msg.content if b.type == "text"), "")
        if not text.strip():
            # без этой диагностики «почему опять шаблоны» не отследить
            print(f"[assistant] пустой ответ модели (stop_reason={msg.stop_reason}, "
                  f"блоки={[b.type for b in msg.content]}) → правила")
            return rules_advice(signals)
        parsed = _parse_advice(text)
        if not parsed:
            return rules_advice(signals)
        return _ensure_praise(parsed, signals)
    except Exception as e:  # сеть/квота/невалидный JSON → детерминированный фоллбэк
        print(f"[assistant] Claude недоступен, фоллбэк на правила: {e!r}")
        return rules_advice(signals)


def _ensure_praise(advice: List[Dict[str, Any]], signals: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Достраивает похвалу филиалам, которых модель пропустила.

    Филиал выполнил план, а в разборе о нём ни слова — так делать нельзя, и полагаться
    на то, что модель ничего не забудет, тоже. Проверяем по данным и дописываем.
    """
    praised = {a.get("branch") for a in advice if a.get("severity") == "good"}
    added = 0
    for b in signals.get("branches", []):
        if b.get("name") in praised:
            continue
        for w in wins(b):
            advice.append(w)
            added += 1
            break  # по одной догоняющей похвале на филиал
    if added:
        print(f"[assistant] модель пропустила похвалу по {added} филиалам — добавил из правил")
    advice.sort(key=lambda a: (a.get("severity") != "good", -(a.get("money") or 0)))
    return advice


# ── Задачи филиалам для «Горы» ───────────────────────────────────────────────
# Экономия токенов здесь — не украшение, а требование владельца. Поэтому:
#   • правила сами выбирают, ЧТО болит у каждого филиала (бесплатно);
#   • модель зовём ОДИН раз на всю сеть и просим только формулировки;
#   • потолок ответа маленький: одна задача на филиал, а не разбор.
TASKS_MAX_TOKENS = int(os.getenv("MOUNTAIN_MAX_TOKENS", "1200"))
# Рубильник СВОЙ, не ASSISTANT_AI: владелец просил ИИ на задачи, но не на
# ассистента — иначе один флаг включал бы и дорогой разбор всех филиалов.
# Здесь вызов один на всю сеть и потолок 1200 токенов, поэтому по умолчанию вкл.
TASKS_AI = os.getenv("MOUNTAIN_AI", "1") != "0"
_tasks_client = (AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
                 if TASKS_AI and os.getenv("ANTHROPIC_API_KEY") else None)

_TASKS_PROMPT = (
    "Ты ставишь задачи управляющим филиалов ресторана. На вход — таблица: филиал, "
    "показатель, факт, норма, деньги на кону.\n"
    "Верни СТРОГО JSON-массив, по одному объекту на филиал:\n"
    '[{"branch":"<имя как во входе>","title":"<задача, до 60 знаков, повелительно>",'
    '"detail":"<первый шаг: что открыть, что посчитать, с кем поговорить, до 130 знаков>",'
    '"effort":"быстро|средне|долго"}]\n'
    "\nЖЁСТКИЕ ТРЕБОВАНИЯ:\n"
    "1. Задачи должны быть РАЗНЫМИ. Не повторяй один глагол и одну конструкцию: "
    "если у двух филиалов одна проблема, зайди с разных сторон — у одного через "
    "поставщика, у другого через смену, у третьего через порционирование.\n"
    "2. В title — конкретная цифра из строки (сколько ₸ или сколько процентов), "
    "а не «до нормы».\n"
    "3. В detail — ПЕРВОЕ действие на завтра, а не пересказ проблемы. Глагол в начале.\n"
    "4. Усилие ставь честно: «быстро» — до часа, «средне» — неделя, «долго» — месяц.\n"
    "5. Запрещено: «оптимизировать», «проработать», «взять под контроль», "
    "«довести до нормы», «улучшить показатели». Это пустые слова.\n"
    "Пиши так, как говорят управляющему в смене: коротко и по делу."
)


def _task_rows(signals: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Правила: по одному худшему показателю на филиал. Это и есть вход для модели.

    Форма сигналов — плоская (см. _gather_assistant_signals): foodcost — словарь,
    cancels — число, writeoffs — число, reviews — список площадок.
    """
    rows: List[Dict[str, Any]] = []
    for b in (signals.get("branches") or []):
        name = b.get("name") or "Филиал"
        rev = float(b.get("revenue") or 0)
        cands: List[Dict[str, Any]] = []

        fc = b.get("foodcost")
        if isinstance(fc, dict) and fc.get("fact_pct") and fc.get("norm_pct"):
            over = float(fc["fact_pct"]) - float(fc["norm_pct"])
            cov = float(fc.get("coverage") or 0)
            # При низком покрытии техкарт факт-фудкост завышен в разы: считать по
            # нему деньги — обещать управляющему миллионы из воздуха.
            if over > 0 and cov >= 60:
                cands.append({"metric": "Фудкост", "fact": f'{fc["fact_pct"]}%',
                              "norm": f'{fc["norm_pct"]}%', "key": "fc",
                              "money": round(rev * over / 100)})

        wo = b.get("writeoffs")
        wo_sum = (float(wo.get("total") or wo.get("total_written") or 0)
                  if isinstance(wo, dict) else float(wo or 0))
        if wo_sum > 0:
            cands.append({"metric": "Списания", "fact": f"{round(wo_sum)} ₸", "norm": "минимум",
                          "key": "wo", "money": round(wo_sum)})

        cn = b.get("cancels")
        cn_sum = float(cn.get("total_amount") or 0) if isinstance(cn, dict) else float(cn or 0)
        if cn_sum > 0:
            cands.append({"metric": "Отмены", "fact": f"{round(cn_sum)} ₸", "norm": "0 ₸",
                          "key": "cn", "money": round(cn_sum)})

        stops = b.get("stop_count")
        if stops:
            cands.append({"metric": "Стоп-лист", "fact": f"{stops} позиций", "norm": "0",
                          "key": "stop", "money": round(rev * 0.01)})

        worst_rating = None
        for p in (b.get("reviews") or []):
            r, lbl = p.get("rating"), p.get("label")
            if r is None:
                continue
            scale = 10 if float(r) > 5 else 5
            gap = (9 if scale == 10 else 4.7) - float(r)
            if gap > 0 and (worst_rating is None or gap > worst_rating[0]):
                worst_rating = (gap, lbl, r)
        if worst_rating:
            gap, lbl, r = worst_rating
            cands.append({"metric": f"Рейтинг {lbl}", "fact": str(r),
                          "norm": "9" if float(r) > 5 else "4.7", "key": "rev",
                          "money": round(rev * min(0.1, gap / 10))})

        if not cands:
            continue
        # Немного контекста филиала — модели есть чем различать задачи: выручка
        # задаёт масштаб, вторая проблема подсказывает угол захода.
        cands.sort(key=lambda c: -(c.get("money") or 0))
        # Потолок вменяемости: задача не может «стоить» больше трети выручки
        # филиала за месяц — иначе цифра только подрывает доверие к остальным.
        cap = rev * 0.33 if rev else None
        for c in cands:
            if cap and (c.get("money") or 0) > cap:
                c["money"] = round(cap)
        best = cands[0]
        second = cands[1]["metric"] if len(cands) > 1 else ""
        rows.append({"branch": name, **best, "revenue": round(rev), "second": second})
    return rows


async def propose_tasks(signals: Dict[str, Any]) -> List[Dict[str, Any]]:
    """По одной задаче на филиал. Один вызов модели на всю сеть; без ключа —
    формулировки по правилам, чтобы экран работал всегда."""
    rows = _task_rows(signals)
    if not rows:
        return []
    fallback = [{"branch": r["branch"], "task_key": r["key"], "money": r["money"],
                 "effort": "средне",
                 "title": f'{r["metric"]}: довести до нормы',
                 "detail": f'Сейчас {r["fact"]}, норма {r["norm"]}. Разбери причину и закрой.'}
                for r in rows]
    if _tasks_client is None:
        return fallback
    try:
        table = "\n".join(
            f'{r["branch"]} | {r["metric"]} | факт {r["fact"]} | норма {r["norm"]} '
            f'| на кону {r["money"]} ₸ | выручка филиала {r.get("revenue", 0)} ₸'
            + (f' | вторая проблема: {r["second"]}' if r.get("second") else "")
            for r in rows)
        resp = await _tasks_client.messages.create(
            model=MODEL, max_tokens=TASKS_MAX_TOKENS,
            system=_TASKS_PROMPT,
            messages=[{"role": "user", "content": table}],
        )
        txt = "".join(getattr(c, "text", "") for c in resp.content).strip()
        start, end = txt.find("["), txt.rfind("]")
        parsed = json.loads(txt[start:end + 1]) if start >= 0 and end > start else []
        by_branch = {str(p.get("branch")): p for p in parsed if isinstance(p, dict)}
        out = []
        for r, f in zip(rows, fallback):
            p = by_branch.get(r["branch"]) or {}
            out.append({**f,
                        "title": str(p.get("title") or f["title"])[:80],
                        "detail": str(p.get("detail") or f["detail"])[:200],
                        "effort": p.get("effort") if p.get("effort") in ("быстро", "средне", "долго") else "средне"})
        return out
    except Exception as e:
        print(f"[mountain] Claude не дал формулировки, идём по правилам: {e!r}")
        return fallback


# ── «Что изменилось» вместо готовых задач ────────────────────────────────────
# Владелец пишет задачи сам, а от модели ему нужно другое: короткое наблюдение
# по филиалу — куда уходят деньги и что поменялось за неделю. Один вызов на всю
# сеть, потолок ответа маленький, шаблонные обороты запрещены прямо в промпте.
DIGEST_MAX_TOKENS = int(os.getenv("MOUNTAIN_DIGEST_TOKENS", "900"))

_DIGEST_PROMPT = (
    "Ты смотришь на показатели филиалов ресторана и говоришь владельцу, ЧТО "
    "ИЗМЕНИЛОСЬ и куда уходят деньги. Не задачи, не советы «оптимизировать» — "
    "наблюдение.\n"
    "Верни СТРОГО JSON-массив: "
    '[{"branch":"<имя как во входе>","note":"<одно предложение, до 120 знаков>"}]\n'
    "Правила: называй конкретную цифру; пиши по-русски просто, как сказал бы "
    "управляющему; если филиал в порядке — так и скажи одной фразой. "
    "Запрещено: «оптимизировать», «проработать», «взять под контроль», "
    "«улучшить показатели», «обратить внимание»."
)


def _digest_rows(signals: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Компактная выжимка по филиалу: выручка и её динамика, фудкост, списания,
    закупки, опоздания. Ровно то, из чего видно «что изменилось»."""
    rows = []
    for b in (signals.get("branches") or []):
        ch = b.get("changes") or {}
        wo = b.get("writeoffs") or {}
        fc = b.get("foodcost") or {}
        lt = b.get("late") or {}
        rows.append({
            "branch": b.get("name") or "Филиал",
            "revenue": round(float(b.get("revenue") or 0)),
            "revenue_pct": ch.get("revenue_pct"),
            "check_pct": ch.get("avg_check_pct"),
            "foodcost": fc.get("fact_pct"),
            "writeoffs": wo.get("total"),
            "cancels": b.get("cancels"),
            "late_pct": lt.get("late_pct"),
        })
    return rows


async def digest(signals: Dict[str, Any]) -> List[Dict[str, Any]]:
    """По одному наблюдению на филиал. Без ключа — короткая фраза по правилам."""
    rows = _digest_rows(signals)
    if not rows:
        return []

    def rule(r: Dict[str, Any]) -> str:
        bits = []
        if r.get("revenue_pct") is not None:
            d = float(r["revenue_pct"])
            bits.append(f"выручка {'выросла' if d >= 0 else 'упала'} на {abs(d):.0f}% к прошлой неделе")
        if r.get("writeoffs"):
            bits.append(f"списаний на {round(float(r['writeoffs'])):,} ₸".replace(",", " "))
        if r.get("late_pct") is not None:
            bits.append(f"опозданий {float(r['late_pct']):.0f}%")
        return "; ".join(bits) or "данных за период мало"

    fallback = [{"branch": r["branch"], "note": rule(r)} for r in rows]
    if _tasks_client is None:
        return fallback
    try:
        table = "\n".join(
            f'{r["branch"]} | выручка {r["revenue"]} ₸'
            + (f' ({r["revenue_pct"]:+.0f}% к прошлой неделе)' if r.get("revenue_pct") is not None else "")
            + (f' | фудкост {r["foodcost"]}%' if r.get("foodcost") else "")
            + (f' | списания {round(float(r["writeoffs"]))} ₸' if r.get("writeoffs") else "")
            + (f' | отмены {round(float(r["cancels"]))} ₸' if r.get("cancels") else "")
            + (f' | опоздания {r["late_pct"]}%' if r.get("late_pct") is not None else "")
            for r in rows)
        # Потолок ответа растёт от числа филиалов: на 23 строки фиксированные 900
        # токенов не хватало, JSON обрывался — и все наблюдения молча падали
        # в шаблонный фоллбэк.
        cap = min(4000, 140 * len(rows) + 200)
        resp = await _tasks_client.messages.create(
            model=MODEL, max_tokens=max(DIGEST_MAX_TOKENS, cap), system=_DIGEST_PROMPT,
            messages=[{"role": "user", "content": table}])
        txt = "".join(getattr(c, "text", "") for c in resp.content).strip()
        start, end = txt.find("["), txt.rfind("]")
        parsed = json.loads(txt[start:end + 1]) if start >= 0 and end > start else []
        by = {str(p.get("branch")): str(p.get("note") or "") for p in parsed if isinstance(p, dict)}
        return [{"branch": r["branch"], "note": by.get(r["branch"]) or f["note"]}
                for r, f in zip(rows, fallback)]
    except Exception as e:
        print(f"[mountain] дайджест не собрался, идём по правилам: {e!r}")
        return fallback
