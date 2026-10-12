"""ИИ-слой аналитики поверх мини-аппа. Отдельный сервис (:8010), чтобы не трогать
прод api.py. Тянет демо-данные из основного бэкенда (:8000), считает потери в тенге
детерминированно (числа не выдумываем), а Claude превращает их в инсайты + план роста.

Запуск:  ANTHROPIC_API_KEY=... .venv/bin/python -m uvicorn insights_service:app --port 8010
Фронт ходит через Vite-прокси: /api/insights/{org} → :8010
"""
import os
import json
import logging
import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from anthropic import AsyncAnthropic

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("insights")

BACKEND = os.getenv("BACKEND_URL", "http://localhost:8000")
MODEL = "claude-opus-4-8"  # ИИ-аналитика — максимальная модель
client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY")) if os.getenv("ANTHROPIC_API_KEY") else None

app = FastAPI(title="iiko Insights")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def tng(n):
    try:
        return f"{round(n):,}".replace(",", " ") + " ₸"
    except Exception:
        return "—"


async def _fetch(cli, path):
    try:
        r = await cli.get(f"{BACKEND}/api/{path}", timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.warning(f"fetch {path}: {e}")
        return {}


async def gather(org):
    async with httpx.AsyncClient() as cli:
        dash = await _fetch(cli, f"dashboard/{org}?period=month")
        fc = await _fetch(cli, f"foodcost/{org}?period=month")
        exp = await _fetch(cli, f"expenses/{org}?period=month")
        canc = await _fetch(cli, f"cancels/{org}?period=month")
        wd = await _fetch(cli, f"withdrawals/{org}?period=month")
        cmp = await _fetch(cli, f"comparison/{org}")
        rev = await _fetch(cli, f"reviews/{org}")
    return dash, fc, exp, canc, wd, cmp, rev


def compute(dash, fc, exp, canc, wd, cmp, rev):
    """Детерминированные сигналы в тенге — фундамент, который Claude не может переврать."""
    sig = {}

    # 1) Фудкост-переплата
    over = (fc.get("fact_sum", 0) or 0) - (fc.get("norm_sum", 0) or 0)
    sig["foodcost"] = {
        "fact_pct": fc.get("fact_pct"), "norm_pct": fc.get("norm_pct"),
        "overspend_month": max(0, round(over)),
        "top_dishes": sorted(fc.get("dishes", []), key=lambda d: d.get("cost_pct") or 0, reverse=True)[:3],
    }
    # 2) Отмены/возвраты
    sig["cancels"] = {"amount": canc.get("total_amount", 0), "orders": canc.get("total_orders", 0),
                      "top": (canc.get("items") or [])[:3]}
    # 3) Средний чек vs выручка (рост трафика или чека?)
    sig["revenue"] = {"rev_change": dash.get("revenue_change"), "check_change": dash.get("avg_check_change"),
                      "revenue": dash.get("revenue_today"), "avg_check": dash.get("avg_check")}
    # 4) Год-к-году
    cur = cmp.get("weeks", {}).get("current", [])
    prev = cmp.get("weeks", {}).get("previous", [])
    ct = sum(w.get("total_rev", 0) for w in cur)
    pt = sum(w.get("total_rev", 0) for w in prev)
    sig["yoy"] = {"cur": ct, "prev": pt, "delta_pct": round((ct - pt) / pt * 100, 1) if pt else None}
    # 5) Отзывы
    plats = rev.get("platforms", [])
    negs = [r for r in rev.get("reviews", []) if (r.get("rating") or 5) <= 3]
    sig["reviews"] = {"platforms": [{"label": p.get("label"), "rating": p.get("rating"),
                                     "scale": p.get("scale"), "delta": p.get("delta")} for p in plats],
                      "negatives": [r.get("text") for r in negs][:3]}
    # 6) Расходы
    sig["expenses"] = {"total": exp.get("total"),
                       "top": sorted(exp.get("categories", []), key=lambda c: c.get("amount") or 0, reverse=True)[:1]}

    # Оценка потерь/мес = переплата по фудкосту + отмены (консервативно)
    leakage = sig["foodcost"]["overspend_month"] + (sig["cancels"]["amount"] or 0)
    sig["leakage_total"] = round(leakage)

    # Светофор здоровья
    def light(ok, warn):
        return "green" if ok else ("yellow" if warn else "red")
    health = []
    fp, npct = fc.get("fact_pct"), fc.get("norm_pct")
    if fp is not None and npct is not None:
        health.append({"key": "Фудкост", "value": f"{fp}%",
                       "status": light(fp <= npct + 1, fp <= npct + 5)})
    ydelta = sig["yoy"]["delta_pct"]
    if ydelta is not None:
        health.append({"key": "Выручка г/г", "value": f"{'+' if ydelta>0 else ''}{ydelta}%",
                       "status": light(ydelta >= 0, ydelta >= -10)})
    cc = dash.get("avg_check_change")
    if cc is not None:
        health.append({"key": "Средний чек", "value": f"{'+' if cc>0 else ''}{cc}%",
                       "status": light(cc >= 0, cc >= -5)})
    if sig["cancels"]["amount"]:
        health.append({"key": "Отмены", "value": tng(sig["cancels"]["amount"]),
                       "status": light(sig["cancels"]["amount"] < 20000, sig["cancels"]["amount"] < 60000)})
    if plats:
        r0 = plats[0]
        norm = (r0.get("rating") or 0) / (r0.get("scale") or 5)
        health.append({"key": r0.get("label", "Отзывы"), "value": f"{r0.get('rating')}",
                       "status": light(norm >= 0.9, norm >= 0.8)})
    sig["health"] = health
    return sig


async def ai_narrate(sig):
    """Claude превращает числа в инсайты + план. Числа передаём готовыми — не выдумывает."""
    if client is None:
        return None
    prompt = f"""Ты — ИИ-финдиректор ресторана. На основе РЕАЛЬНЫХ цифр ниже сформулируй
инсайты и план роста для владельца. Пиши по-русски, кратко, по делу, как консультант,
который залез в цифры и нашёл деньги.

ЖЁСТКИЕ ПРАВИЛА:
- Используй ТОЛЬКО числа из данных ниже. Не выдумывай суммы.
- Каждый инсайт должен быть привязан к деньгам (поле money — строка с ₸) или к % изменения.
- Суммы в тенге форматируй с пробелами (напр. "180 000 ₸").
- severity: high (теряются деньги / падение), medium (риск), good (что хорошо).
- 4-6 инсайтов, 3-4 пункта плана. План — конкретные действия с ожидаемым эффектом.

ДАННЫЕ (тенге за месяц):
{json.dumps(sig, ensure_ascii=False, indent=2)}

Верни СТРОГО JSON (без markdown-обёртки) в формате:
{{
  "headline": "одна фраза-вывод про главную возможность/потерю",
  "insights": [
    {{"title": "...", "detail": "1-2 предложения", "money": "180 000 ₸/мес" или "+18% г/г", "severity": "high|medium|good", "dimension": "Фудкост|Выручка|Смены|Отзывы|Расходы"}}
  ],
  "plan": [
    {{"action": "что сделать", "effect": "ожидаемый эффект в ₸ или %", "priority": 1}}
  ]
}}"""
    try:
        msg = await client.messages.create(
            model=MODEL, max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in msg.content if b.type == "text").strip()
        if text.startswith("```"):
            text = text.split("```", 2)[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.rsplit("```", 1)[0]
        return json.loads(text)
    except Exception as e:
        logger.warning(f"ai_narrate failed: {e}")
        return None


def fallback(sig):
    """Если Claude недоступен — правила дают базовые инсайты (сайт не ломается)."""
    ins = []
    fc = sig["foodcost"]
    if fc["overspend_month"] > 0:
        ins.append({"title": "Переплата по фудкосту", "money": f"{tng(fc['overspend_month'])}/мес",
                    "detail": f"Факт {fc['fact_pct']}% против нормы {fc['norm_pct']}%.",
                    "severity": "high", "dimension": "Фудкост"})
    if sig["cancels"]["amount"]:
        ins.append({"title": "Отмены и возвраты", "money": f"{tng(sig['cancels']['amount'])}/мес",
                    "detail": f"{sig['cancels']['orders']} отмен за период — сигнал слива.",
                    "severity": "high", "dimension": "Смены"})
    rc, cc = sig["revenue"]["rev_change"], sig["revenue"]["check_change"]
    if rc is not None and cc is not None and cc < rc:
        ins.append({"title": "Рост за счёт трафика, не чека", "money": f"чек {cc:+}%",
                    "detail": "Выручка растёт, но средний чек отстаёт — недобираете на допродажах.",
                    "severity": "medium", "dimension": "Выручка"})
    if sig["yoy"]["delta_pct"] is not None:
        d = sig["yoy"]["delta_pct"]
        ins.append({"title": "Динамика год к году", "money": f"{d:+}% г/г",
                    "detail": f"{tng(sig['yoy']['cur'])} против {tng(sig['yoy']['prev'])} год назад.",
                    "severity": "good" if d >= 0 else "high", "dimension": "Выручка"})
    # план: приоритезированные действия из сигналов
    plan = []
    if fc["overspend_month"] > 0:
        plan.append({"action": "Пересобрать техкарты 3 блюд-лидеров по себестоимости",
                     "effect": f"до {tng(fc['overspend_month'])}/мес", "priority": len(plan) + 1})
    if sig["cancels"]["amount"]:
        plan.append({"action": "Разобрать причины отмен и возвратов, закрыть слив",
                     "effect": f"{tng(sig['cancels']['amount'])}/мес", "priority": len(plan) + 1})
    if rc is not None and cc is not None and cc < rc:
        plan.append({"action": "Ввести допродажи (гарнир/напиток/десерт) на кассе и в зале",
                     "effect": "рост среднего чека 3–5%", "priority": len(plan) + 1})
    if not plan:
        plan.append({"action": "Держать фудкост в норме и следить за отменами",
                     "effect": "удержание маржи", "priority": 1})

    drv = ins[0]["dimension"].lower() if ins else "фудкост"
    headline = (f"За месяц теряете ≈ {tng(sig['leakage_total'])} — "
                f"главный источник {drv}. Ниже приоритеты и план.")
    return {"headline": headline, "insights": ins, "plan": plan}


@app.get("/api/insights/{org}")
async def insights(org: str):
    data = await gather(org)
    sig = compute(*data)
    # INSIGHTS_AI=1 включает live-ИИ (медленнее, жжёт токены). По умолчанию —
    # мгновенный детерминированный режим: без ожидания и без токенов.
    use_ai = os.getenv("INSIGHTS_AI") == "1" and client is not None
    ai = (await ai_narrate(sig) if use_ai else None) or fallback(sig)
    return {
        "leakage_total": sig["leakage_total"],
        "health": sig["health"],
        "headline": ai.get("headline"),
        "insights": ai.get("insights", []),
        "plan": ai.get("plan", []),
        "ai": use_ai,
    }
