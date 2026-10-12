import os
import hashlib
import json
import base64
from typing import Dict, Any, Optional, List, Tuple
from anthropic import AsyncAnthropic
from database import get_ai_cache, set_ai_cache

client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY")) if os.getenv("ANTHROPIC_API_KEY") else None

async def analyze_with_claude(
    question: str,
    context: Dict[str, Any],
    org_id: str
) -> str:
    if not client:
        return "🤖 AI помощник временно недоступен. Попробуйте позже."
    
    date_str = context.get("date", "unknown")
    context_json = json.dumps(context, ensure_ascii=False, default=str)
    query_hash = hashlib.sha256(f"{question}:{context_json}".encode()).hexdigest()[:32]
    
    cached = get_ai_cache(query_hash, org_id, date_str)
    if cached:
        return cached
    
    system_prompt = (
        "Ты — аналитик ресторана. Отвечай на русском языке, кратко и по делу. "
        "Используй эмодзи где уместно. Давай конкретные рекомендации на основе предоставленных метрик. "
        "Не придумывай данных, которых нет в контексте."
    )
    
    user_prompt = f"""Контекст ресторана (JSON):
{context_json}

Вопрос менеджера: {question}

Дай развёрнутый, но лаконичный ответ с конкретными цифрами из контекста."""

    try:
        message = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}]
        )
        response = message.content[0].text
        set_ai_cache(query_hash, response, org_id, date_str)
        return response
    except Exception as e:
        return f"⚠️ Ошибка AI: {str(e)}"


INVOICE_PROMPT = """Ты помощник по распознаванию накладных и товарных чеков казахстанских ресторанов.
Извлеки структурированные данные и верни ТОЛЬКО JSON без пояснений и без markdown-обёртки:
{
  "supplier": "название поставщика или null",
  "branch": "название филиала/ресторана если указан или null",
  "date": "дата в формате YYYY-MM-DD или null",
  "items": [
    {"name": "название товара", "quantity": 0.0, "unit": "кг/гр/шт/уп/л/мл/ящ", "price_per_unit": 0.0, "total": 0.0}
  ],
  "total_amount": 0.0,
  "vat_amount": 0.0,
  "vat_rate": 12.0,
  "currency": "KZT",
  "notes": "любые важные заметки или null"
}

Правила извлечения:
1. Если значения нет — null. Quantity и цены — числа, не строки. Не используй пробелы как разделители тысяч в числах: 12 500 → 12500.
2. total_amount — итоговая сумма С НДС, как пишут в накладной.
3. vat_amount — сумма НДС в тенге. Если в накладной не указана, но видно что цены С НДС: vat_amount = total_amount - total_amount / (1 + vat_rate/100). В Казахстане vat_rate = 12.0 по умолчанию.
4. Если фото содержит несколько страниц одной накладной — объедини все позиции в один items[], не дублируй заголовок/итог.

Правила для казахстанских накладных:
5. Даты могут быть DD.MM.YYYY, DD/MM/YY, DD.MM (без года) или DD месяц прописью. Всегда конвертируй в YYYY-MM-DD. Если год отсутствует — используй текущий год.
6. Единицы измерения нормализуй до одного из: кг, гр, шт, уп, л, мл, ящ. Маппинг: "г" → "гр", "упак"/"уп." → "уп", "блок" → "уп", "коробка"/"кор" → "ящ", "литр"/"л." → "л".
7. Поставщик обычно в шапке (ТОО/ИП/ИНН/БИН). Извлеки только название без формы собственности, если она в отдельной строке. Например "ТОО \\"Sapa Foods\\"" → "Sapa Foods".

Правила валидации (важно!):
8. Если в строке есть quantity, price_per_unit и total — проверь что quantity * price_per_unit ≈ total (допуск 0.5 ₸). При расхождении дополни notes: "price-mismatch: <название позиции>".
9. ЕДИНИЦЫ ИЗМЕРЕНИЯ — строго: unit бери РОВНО ту, что написана в строке накладной (кг, гр, шт, уп, ящ, л, мл, бут, бан) — не переводи и не угадывай. quantity указывай в этой же единице. Если в строке составная фасовка («5 уп × 2 кг»), то quantity=5, unit="уп", а в notes добавь "фасовка: <название>: 5 уп по 2 кг". Если единица не написана вообще — unit="шт".
9. Сумма всех items[].total должна быть близка к total_amount (допуск 1 ₸). При расхождении дополни notes: "items-sum-mismatch".
10. Если строка плохо читается — НЕ выдумывай, пропусти и дополни notes: "skipped-unreadable-row".
11. Поле notes — это конкатенация всех замечаний через "; ". Если замечаний нет — null.

Пример (для калибровки):
Накладная: ТОО "Sapa Foods", 14.05.2026, Помидоры 5 кг × 480₸ = 2400₸, Огурцы 3.2 кг × 600₸ = 1920₸. Итого: 4320₸.
Ответ:
{"supplier":"Sapa Foods","branch":null,"date":"2026-05-14","items":[{"name":"Помидоры","quantity":5.0,"unit":"кг","price_per_unit":480.0,"total":2400.0},{"name":"Огурцы","quantity":3.2,"unit":"кг","price_per_unit":600.0,"total":1920.0}],"total_amount":4320.0,"vat_amount":462.86,"vat_rate":12.0,"currency":"KZT","notes":null}
"""


def _parse_claude_json(raw_text: str) -> Dict[str, Any]:
    """Снимает markdown-обёртку с ответа Claude и парсит JSON. Бросает исключение при ошибке."""
    raw = raw_text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


async def analyze_invoice_photos(images: List[Tuple[bytes, str]]) -> Dict[str, Any]:
    """Распознаёт накладную из одного или нескольких фото через Claude Vision.
    Все изображения обрабатываются в одном запросе — Claude собирает их в один документ.

    Args:
        images: список (image_bytes, mime_type), напр. [(b"...", "image/jpeg"), ...]
    Returns:
        dict с полями supplier/date/items/total_amount/vat_amount/... или {"error": "..."}.
    """
    if not client:
        return {"error": "AI недоступен"}
    if not images:
        return {"error": "Нет изображений для распознавания"}

    content = []
    for image_bytes, mime_type in images:
        image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": mime_type, "data": image_b64}
        })
    content.append({"type": "text", "text": INVOICE_PROMPT})

    message = None
    try:
        message = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            messages=[{"role": "user", "content": content}]
        )
        return _parse_claude_json(message.content[0].text)
    except json.JSONDecodeError:
        raw_preview = message.content[0].text[:500] if message else ""
        return {"error": "Не удалось распарсить ответ", "raw": raw_preview}
    except Exception as e:
        return {"error": str(e)}


async def analyze_invoice_text(text: str) -> Dict[str, Any]:
    """Распознаёт накладную из текстового сообщения через Claude.
    Возвращает тот же dict-формат что analyze_invoice_photos, или {"error": "..."}.
    """
    if not client:
        return {"error": "AI недоступен"}
    if not text or not text.strip():
        return {"error": "Пустой текст"}

    user_content = (
        f"Распознай накладную из текста:\n\n{text.strip()}\n\n"
        + INVOICE_PROMPT
    )

    message = None
    try:
        message = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            messages=[{"role": "user", "content": user_content}]
        )
        return _parse_claude_json(message.content[0].text)
    except json.JSONDecodeError:
        raw_preview = message.content[0].text[:500] if message else ""
        return {"error": "Не удалось распарсить ответ", "raw": raw_preview}
    except Exception as e:
        return {"error": str(e)}

