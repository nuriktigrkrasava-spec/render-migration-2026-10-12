import asyncio
import logging
import httpx
import os
import hmac
import hashlib
import html
import json
import csv
import io
import re
from datetime import datetime, timedelta, date, timezone
from enum import Enum
from typing import Optional, List, Dict, Any, Tuple
from uuid import uuid4
from fastapi import FastAPI, Header, HTTPException, Request, Depends, Query
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import uvicorn

from database import (
    init_db, get_user, create_user, set_demo_mode,
    get_demo_invoice_count, inc_demo_invoice_count,
    start_demo_trial, get_demo_trial,
    get_trials_due_for_sales, mark_trial_sales_sent,
    get_restaurants, add_restaurant, delete_restaurant, delete_all_restaurants,
    wipe_demo_user,
    add_manual_expense, get_manual_expenses, delete_manual_expense,
    save_bot_invoice, get_bot_invoices, get_bot_invoice,
    update_bot_invoice, find_invoice_by_dedup, get_price_history,
    get_db, decrypt_password,
    save_pending_invoice, get_pending_invoice, get_pending_invoice_by_chat,
    delete_pending_invoice_by_chat,
    get_plan_url, set_plan_url,
    get_client_url, set_client_url,
    get_all_restaurants, get_user_by_username, get_app_state, set_app_state,
    record_acquisition, acquisition_stats, acquisitions_today,
    get_quota, quota_available, consume_quota, add_quota_extra,
    reset_quota_if_due, should_notify_limit, quota_stats,
)
from iiko import IikoClient, get_client
from claude_api import analyze_with_claude, analyze_invoice_photos, analyze_invoice_text
from foodcost_calc import compute_recipe_foodcost

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

IS_PROD = os.getenv("ENVIRONMENT") == "production"

# Swagger отключён в production
app = FastAPI(
    title="iikoBoss Analytics",
    version="1.0.0",
    docs_url=None if IS_PROD else "/docs",
    redoc_url=None if IS_PROD else "/redoc",
    openapi_url=None if IS_PROD else "/openapi.json",
)

# Rate limiter
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Security headers
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        if IS_PROD:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

app.add_middleware(SecurityHeadersMiddleware)

# Статика для скринов FAQ: docs/screenshots → /static/screenshots/*
# Telegram дёргает фото по публичному URL, поэтому путь должен быть доступен снаружи.
if os.path.isdir("docs/screenshots"):
    app.mount("/static/screenshots",
              StaticFiles(directory="docs/screenshots"),
              name="screenshots")

# CORS — только Telegram origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://web.telegram.org",
        "https://webk.telegram.org",
        "https://webz.telegram.org",
        "null",  # Telegram Mini App webview
    ] if IS_PROD else ["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["X-Telegram-Init-Data", "Content-Type"],
)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
MINIAPP_URL = os.getenv("MINIAPP_URL", "https://iiko.onrender.com")
DEMO_API_LOGIN = os.getenv("DEMO_API_LOGIN", "demo")
TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET")
# Срок годности Telegram initData (сек). По умолчанию сутки.
INIT_DATA_MAX_AGE = int(os.getenv("INIT_DATA_MAX_AGE_SEC", "86400"))
# Демо-бот: длительность триала с реальными данными (часов).
DEMO_TRIAL_HOURS = int(os.getenv("DEMO_TRIAL_HOURS", "168"))  # 7 дней
# DEMO_ONLY=1 — это отдельный демо-бот: все юзеры всегда видят фейковые данные.
# Пусто (основной бот) — демо-режим полностью выключен (нет кнопки, /api/demo даёт 404).
DEMO_ONLY = os.getenv("DEMO_ONLY", "").strip().lower() in ("1", "true", "yes", "on")
OWNER_CHAT_ID = int(os.getenv("OWNER_CHAT_ID", "0"))  # владелец — пуши и /stats
# Рубильник блокировки по лимиту. По умолчанию 0 — лимит только считается
# (кнопка «Лимит»/статистика показывают расход), но распознавание НЕ блокируется.
# Поставить 1, когда владелец убедится в корректности цифр — тогда лимит блокирует.
QUOTA_ENFORCE = os.getenv("QUOTA_ENFORCE", "0").strip().lower() in ("1", "true", "yes", "on")

# ── saya-kds (внешняя аналитика доставки/кухни) ──
# Прокси /api/chaspik держит ключ на бэке и шлёт его в заголовке X-API-Key.
ANALYTICS_API_KEY = os.getenv("ANALYTICS_API_KEY", "")
CHASPIK_ORG_ID = os.getenv("CHASPIK_ORG_ID", "")
CHASPIK_BASE_URL = os.getenv("CHASPIK_BASE_URL", "https://saya-kds.onrender.com")
CHASPIK_TG_IDS = {355897224, 8727082281}

# Часовой пояс ресторанов. Казахстан с 2024 — единый UTC+5 (Asia/Almaty).
# Сервер на Render работает в UTC, поэтому «сегодня» считаем по локальному времени.
LOCAL_TZ = timezone(timedelta(hours=int(os.getenv("TZ_OFFSET_HOURS", "5"))))


def local_now() -> datetime:
    """Текущее время в часовом поясе ресторана (UTC+5), не зависит от tz сервера."""
    return datetime.now(LOCAL_TZ)


class PeriodEnum(str, Enum):
    today = "today"
    yesterday = "yesterday"
    day_before_yesterday = "day_before_yesterday"
    week = "week"
    month = "month"

# In-memory cache for iiko responses (изолирован по user_id)
_cache: Dict[str, Any] = {}
_cache_time: Dict[str, datetime] = {}
# TTL кэша ответов iiko. 3 мин — баланс свежести и нагрузки на iiko-серверы.
_CACHE_TTL = timedelta(minutes=3)


def _cache_key(user_id: int, endpoint: str, org_id: str, period: str) -> str:
    return f"{user_id}:{endpoint}:{org_id}:{period}"


def _get_cached(key: str) -> Optional[Any]:
    if key in _cache and key in _cache_time:
        if datetime.now() - _cache_time[key] < _CACHE_TTL:
            return _cache[key]
    return None


def _set_cached(key: str, value: Any):
    # Перед записью чистим протухшее и держим потолок размера — иначе _cache рос
    # бесконечно (ключи: user×org×период×эндпоинт) и тёк по памяти до рестарта.
    now = datetime.now()
    if len(_cache) > 500:
        for k in [k for k, t in list(_cache_time.items()) if now - t >= _CACHE_TTL]:
            _cache.pop(k, None)
            _cache_time.pop(k, None)
        if len(_cache) > 500:  # всё ещё много — сносим самые старые
            for k in sorted(_cache_time, key=_cache_time.get)[:len(_cache) - 500]:
                _cache.pop(k, None)
                _cache_time.pop(k, None)
    _cache[key] = value
    _cache_time[key] = now


def _invalidate_cache(endpoint: str, org_id: str):
    """Сбросить кэш эндпойнта по org у ВСЕХ пользователей и периодов.
    Вызывать после мутаций накладной (создание черновика / проведение /
    распроведение): иначе /api/invoices отдаёт устаревший статус из кэша
    (TTL 3 мин) и кнопка действия не обновляется. Ключ: user:endpoint:org:period,
    поэтому ищем по подстроке `:endpoint:org:` (с хвостовым `:` — чтобы org-1
    не цеплял org-12)."""
    needle = f":{endpoint}:{org_id}:"
    for k in [k for k in list(_cache) if needle in k]:
        _cache.pop(k, None)
        _cache_time.pop(k, None)


def validate_telegram_init_data(init_data: str) -> Dict[str, Any]:
    if not BOT_TOKEN:
        return {"id": 0, "username": "dev", "first_name": "Developer"}

    from urllib.parse import unquote, parse_qsl
    try:
        parsed = dict(parse_qsl(init_data, keep_blank_values=True))
        if not parsed:
            parsed = dict(x.split("=", 1) for x in init_data.split("&") if "=" in x)

        hash_ = parsed.pop("hash", "")
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(calculated_hash, hash_):
            raise HTTPException(status_code=403, detail="Invalid Telegram init data")

        # Срок годности initData: без проверки перехваченная строка — вечный пропуск.
        try:
            auth_date = int(parsed.get("auth_date", "0"))
        except (ValueError, TypeError):
            auth_date = 0
        if auth_date and (datetime.now(timezone.utc).timestamp() - auth_date) > INIT_DATA_MAX_AGE:
            raise HTTPException(status_code=401, detail="Telegram init data expired")

        user_raw = unquote(parsed.get("user", "{}"))
        user = json.loads(user_raw)
        return user
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"Auth validation error: {e}")
        raise HTTPException(status_code=403, detail="Invalid Telegram init data")


async def get_current_user(x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    if not x_telegram_init_data:
        if not BOT_TOKEN:
            return {"id": 0, "username": "dev", "first_name": "Developer"}
        raise HTTPException(status_code=401, detail="Missing init data")
    user = validate_telegram_init_data(x_telegram_init_data)
    return user


def get_period_dates(period: str, custom_from: Optional[str] = None,
                     custom_to: Optional[str] = None) -> tuple:
    today = local_now().date()
    if period == "custom":
        if not custom_from or not custom_to:
            raise HTTPException(status_code=400, detail="custom period requires from/to")
        try:
            df = date.fromisoformat(custom_from)
            dt = date.fromisoformat(custom_to)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format, use YYYY-MM-DD")
        if df > dt:
            raise HTTPException(status_code=400, detail="from must be <= to")
        if (dt - df).days > 366:
            raise HTTPException(status_code=400, detail="Range too large (max 366 days)")
        return df.isoformat(), dt.isoformat()
    if period == "today":
        return today.isoformat(), today.isoformat()
    elif period == "yesterday":
        y = today - timedelta(days=1)
        return y.isoformat(), y.isoformat()
    elif period == "day_before_yesterday":
        y = today - timedelta(days=2)
        return y.isoformat(), y.isoformat()
    elif period == "week":
        start = today - timedelta(days=6)
        return start.isoformat(), today.isoformat()
    elif period == "month":
        start = today.replace(day=1)
        return start.isoformat(), today.isoformat()
    else:
        return today.isoformat(), today.isoformat()


def format_money(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ") + " ₸"


# ============ DEMO DATA ============
DEMO_RESTAURANT = {
    "id": 1,
    "organization_id": "demo-org-1",
    "organization_name": "Демо Ресторан",
    "api_login": "demo"
}

DEMO_DASHBOARD = {
    "revenue_today": 944617,
    "revenue_change": 21.4,
    "avg_check": 14759,
    "avg_check_change": 21.4,
    "guests": 85,
    "guests_change": 11.8,
    "open_orders": 8,
    "revenue_yesterday": 0,
    "forecast": 1322464,
    "chart_7days": {
        "labels": ["Вс", "Пн", "Вт", "Ср", "Чт", "Пт", "Сб"],
        "data": [750000, 690000, 330000, 330000, 330000, 778267, 950000]
    }
}

DEMO_SALES = {
    "channels": [
        {"name": "Зал", "revenue": 18720000, "guests": 2340, "avg_check": 8000, "top": True},
        {"name": "Доставка", "revenue": 4180000, "guests": 385, "avg_check": 10857, "top": True},
        {"name": "Самовывоз", "revenue": 2950000, "guests": 410, "avg_check": 7195, "top": True},
        {"name": "Банкеты", "revenue": 1680000, "guests": 125, "avg_check": 13440, "top": False},
        {"name": "Кейтеринг", "revenue": 720000, "guests": 42, "avg_check": 17143, "top": False},
        {"name": "Стафф", "revenue": 400000, "guests": 180, "avg_check": 2222, "top": False},
    ],
    "pay_types": [
        {"name": "Терминал Kaspi", "revenue": 9800000, "account": "Kaspi"},
        {"name": "КАСПИ онлайн", "revenue": 4200000, "account": "Kaspi"},
        {"name": "Наличные", "revenue": 5100000, "account": "Наличные"},
        {"name": "Wolt Онлайн", "revenue": 3300000, "account": "Wolt"},
        {"name": "Яндекс безнал", "revenue": 2600000, "account": "Яндекс"},
        {"name": "Glovo Онлайн", "revenue": 1400000, "account": "Glovo"},
        {"name": "Перевод", "revenue": 1250000, "account": "Перевод"},
    ],
    "accounts": [
        {"name": "Kaspi", "revenue": 14000000},
        {"name": "Наличные", "revenue": 5100000},
        {"name": "Wolt", "revenue": 3300000},
        {"name": "Яндекс", "revenue": 2600000},
        {"name": "Glovo", "revenue": 1400000},
        {"name": "Перевод", "revenue": 1250000},
    ],
    "top_dishes": [
        {"name": "Стейк Рибай", "revenue": 2100000, "count": 420},
        {"name": "Паста с морепродуктами", "revenue": 1800000, "count": 360},
        {"name": "Пицца 4 сыра", "revenue": 2100000, "count": 525},
        {"name": "Лосось гриль", "revenue": 2600000, "count": 400},
        {"name": "Стейк Мачете", "revenue": 3100000, "count": 310},
        {"name": "Тёплый салат с говядиной", "revenue": 1900000, "count": 380},
        {"name": "Вино Prosecco", "revenue": 1200000, "count": 480},
        {"name": "Coca-Cola 0.25", "revenue": 950000, "count": 1900},
        {"name": "Свежевыжатый апельсиновый", "revenue": 1100000, "count": 550},
        {"name": "Доп. пармезан", "revenue": 85000, "count": 340},
    ],
    "categories": [
        {"name": "Основные блюда", "value": 35, "color": "#4A90E2"},
        {"name": "Паста", "value": 15, "color": "#34C759"},
        {"name": "Пицца", "value": 12, "color": "#FF9500"},
        {"name": "Салаты", "value": 10, "color": "#AF52DE"},
        {"name": "Напитки", "value": 8, "color": "#FF2D55"},
        {"name": "Вино", "value": 7, "color": "#5AC8FA"},
        {"name": "Десерты", "value": 5, "color": "#FFD60A"},
        {"name": "Соусы/допы", "value": 8, "color": "#FF3B30"},
    ]
}

DEMO_STAFF = {
    "total_revenue": 18850000,
    "orders": 897,
    "guests": 1656,
    "staff": [
        {"name": "Виктория Ким", "revenue": 3850000, "orders": 172, "guests": 318},
        {"name": "Артём Волков", "revenue": 3420000, "orders": 158, "guests": 285},
        {"name": "Камила Ахметова", "revenue": 2780000, "orders": 135, "guests": 248},
        {"name": "Данияр Оспанов", "revenue": 2350000, "orders": 112, "guests": 208},
        {"name": "Алина Морозова", "revenue": 2010000, "orders": 98, "guests": 182},
        {"name": "Тимур Касымов", "revenue": 1740000, "orders": 86, "guests": 162},
    ]
}

DEMO_FOODCOST = {
    "norm_pct": 30,
    "fact_pct": 36,
    "diff_pct": 6,
    "norm_sum": 14272282,
    "fact_sum": 17126739,
    "warning": "Себестоимость не заполнена у 2 из 30 блюд",
    "trend": [
        {"month": "Фев", "fact": 32, "norm": 30},
        {"month": "Мар", "fact": 34, "norm": 30},
        {"month": "Апр", "fact": 35, "norm": 30},
        {"month": "Май", "fact": 36, "norm": 30},
    ],
    "dishes": [
        {"name": "Доп. пармезан", "category": "Доп. прочее", "revenue": 85000, "count": 340, "cost_pct": 72.0},
        {"name": "Доп. соус цезарь", "category": "Доп. соусы", "revenue": 120000, "count": 600, "cost_pct": 70.0},
        {"name": "Стейк рибай", "category": "Основные блюда", "revenue": 4200000, "count": 420, "cost_pct": 52.0},
        {"name": "Лосось гриль", "category": "Основные блюда", "revenue": 2600000, "count": 400, "cost_pct": 39.4},
        {"name": "Стейк мачете с соусом", "category": "Осн. блюда", "revenue": 3100000, "count": 310, "cost_pct": 34.0},
        {"name": "Свежевыжатый апельсиновый", "category": "Напитки", "revenue": 1100000, "count": 550,
         "cost_pct": 34.0},
        {"name": "Тёплый салат из говядины", "category": "Салаты", "revenue": 1900000, "count": 380, "cost_pct": 30.0},
        {"name": "Coca-Cola 0.25", "category": "Напитки", "revenue": 950000, "count": 1900, "cost_pct": 30.0},
        {"name": "Паста с морепродуктами", "category": "Паста", "revenue": 1800000, "count": 360, "cost_pct": 29.0},
        {"name": "Вино Prosecco (бокал)", "category": "Вино", "revenue": 1200000, "count": 480, "cost_pct": 24.8},
        {"name": "Пицца 4 сыра", "category": "Пицца", "revenue": 2100000, "count": 525, "cost_pct": 23.8},
    ]
}

DEMO_EXPENSES = {
    "total": 674420,
    "operations": [
        {"name": "Маркетинг", "cat": "маркетинг", "date": "25.03.2026", "kassa": "Основная касса", "amount": 20000},
        {"name": "Услуги специалистов", "cat": "Заплатили охраннику", "date": "23.03.2026",
         "kassa": "Касса руководства", "amount": 70000},
        {"name": "Услуги специалистов", "cat": "Зп официантам", "date": "20.03.2026", "kassa": "Касса руководства",
         "amount": 200000},
    ],
    "categories": [
        {"name": "Услуги специалистов", "amount": 270000},
        {"name": "Хозяйственные расходы", "amount": 150000},
        {"name": "Маркетинг", "amount": 20000},
        {"name": "Аренда", "amount": 234420},
    ]
}

DEMO_INVOICES = [
    {"id": "0013", "status": "Проведена", "supplier": "ТОО АгроПром", "date": "08.03.2026", "items": 3,
     "amount": 279850},
    {"id": "0014", "status": "Проведена", "supplier": "ТОО МясоОпт", "date": "09.03.2026", "items": 2,
     "amount": 185000},
    {"id": "0015", "status": "Проведена", "supplier": "ИП Фермер", "date": "10.03.2026", "items": 2, "amount": 92500},
    {"id": "0016", "status": "Новая", "supplier": "ТОО АгроПром", "date": "12.03.2026", "items": 1, "amount": 312400},
]

DEMO_WRITEOFFS = {
    "total_written": 245800,
    "purchases": 1500000,
    "loss_pct": 16.4,
    "categories": [
        {"name": "Мясо и птица", "written": 85000, "purchases": 450000, "pct": 18.9, "alert": True},
        {"name": "Овощи и зелень", "written": 52000, "purchases": 180000, "pct": 28.9, "alert": True},
        {"name": "Молочные продукты", "written": 38000, "purchases": 120000, "pct": 31.7, "alert": True},
        {"name": "Хозтовары", "written": 15000, "purchases": 45000, "pct": 33.7, "alert": True},
        {"name": "Бакалея", "written": 12000, "purchases": 187500, "pct": 6.4, "alert": False},
        {"name": "Напитки", "written": 18000, "purchases": 295000, "pct": 6.1, "alert": False},
        {"name": "Рыба и морепродукты", "written": 25800, "purchases": 537500, "pct": 4.8, "alert": False},
    ]
}

DEMO_WITHDRAWALS = {
    "withdrawals": [
        {"date": "2026-06-05", "sum": 120000, "comment": "Инкассация"},
        {"date": "2026-06-04", "sum": 35000, "comment": "Выемка на закуп"},
        {"date": "2026-06-02", "sum": 50000, "comment": "Изъятие наличных"},
    ],
    "total": 205000,
}

def _demo_heatmap():
    import random as _r
    _r.seed(7)
    cells, mx, peak = [], 0, {"d": 0, "h": 0, "orders": 0}
    for d in range(7):
        for h in range(10, 23):
            base = 6 if h in (13, 14, 19, 20) else 2
            wk = 1.4 if d >= 4 else 1.0
            orders = max(0, int(base * wk + _r.randint(-1, 3)))
            if orders <= 0:
                continue
            cells.append({"d": d, "h": h, "orders": orders, "revenue": orders * 4500})
            if orders > mx:
                mx = orders
            if orders > peak["orders"]:
                peak = {"d": d, "h": h, "orders": orders}
    return {"cells": cells, "max": mx, "peak": peak, "metric": "orders"}

DEMO_HEATMAP = _demo_heatmap()

DEMO_STOPLIST = {
    "items": [
        {"name": "Стейк Рибай", "sku": "ST-001", "balance": 0, "category": "Основные блюда", "status": "stop"},
        {"name": "Лосось гриль", "sku": "LS-007", "balance": 0, "category": "Рыба", "status": "stop"},
        {"name": "Паста карбонара", "sku": "PS-003", "balance": 1, "category": "Паста", "status": "low"},
    ],
    "total": 3,
    "updated_at": "14:30"
}

DEMO_RETENTION = {
    "clients": 1847,
    "orders": 5231,
    "cohorts": [
        {"name": "Янв", "size": 620, "jan": 16, "feb": 7, "mar": 5},
        {"name": "Фев", "size": 580, "jan": None, "feb": 17, "mar": 7},
        {"name": "Мар", "size": 647, "jan": None, "feb": None, "mar": 15},
    ],
    "frequency": [1120, 412, 185, 78, 52]
}

# Демо-номенклатура для редактора накладной на демо-боте: к этим позициям
# клиент сопоставляет распознанные строки (реальной iiko номенклатуры нет).
DEMO_NOMENCLATURE = [
    {"id": "demo-prod-1",  "name": "Молоко 3.2%",                "unit": "л",  "code": "1001"},
    {"id": "demo-prod-2",  "name": "Мука пшеничная в/с",        "unit": "кг", "code": "1002"},
    {"id": "demo-prod-3",  "name": "Куриное филе охл.",         "unit": "кг", "code": "1003"},
    {"id": "demo-prod-4",  "name": "Масло подсолнечное",        "unit": "л",  "code": "1004"},
    {"id": "demo-prod-5",  "name": "Сахар-песок",               "unit": "кг", "code": "1005"},
    {"id": "demo-prod-6",  "name": "Сыр Моцарелла",             "unit": "кг", "code": "1006"},
    {"id": "demo-prod-7",  "name": "Помидоры свежие",           "unit": "кг", "code": "1007"},
    {"id": "demo-prod-8",  "name": "Огурцы свежие",             "unit": "кг", "code": "1008"},
    {"id": "demo-prod-9",  "name": "Кофе в зёрнах",             "unit": "кг", "code": "1009"},
    {"id": "demo-prod-10", "name": "Картофель",                 "unit": "кг", "code": "1010"},
    {"id": "demo-prod-11", "name": "Лук репчатый",              "unit": "кг", "code": "1011"},
    {"id": "demo-prod-12", "name": "Говядина вырезка",          "unit": "кг", "code": "1012"},
    {"id": "demo-prod-13", "name": "Лосось с/м",                "unit": "кг", "code": "1013"},
    {"id": "demo-prod-14", "name": "Рис круглозёрный",          "unit": "кг", "code": "1014"},
    {"id": "demo-prod-15", "name": "Масло сливочное 82.5%",     "unit": "кг", "code": "1015"},
    {"id": "demo-prod-16", "name": "Яйцо куриное С1",           "unit": "шт", "code": "1016"},
    {"id": "demo-prod-17", "name": "Соль поваренная",           "unit": "кг", "code": "1017"},
    {"id": "demo-prod-18", "name": "Сметана 20%",               "unit": "кг", "code": "1018"},
    {"id": "demo-prod-19", "name": "Майонез",                   "unit": "кг", "code": "1019"},
    {"id": "demo-prod-20", "name": "Кетчуп томатный",           "unit": "кг", "code": "1020"},
]

# Продающее сообщение демо-бота — после первого (и единственного) распознавания.
# Лимит «До N накладных/мес» в платном тарифе:
DEMO_PLAN_INVOICES = 130  # базовый месячный лимит накладных на аккаунт (default base_limit)
DEMO_FREE_RECOGNITIONS = 5  # сколько накладных распознаёт НЕподключённый демо-юзер до нуджа
DEMO_OFFER_TEXT = (
    "⏳ Ваш недельный доступ к данным закончился.\n\n"
    "Вы видели свои реальные цифры — выручку, фудкост, смены. Хотите оставить это навсегда, каждый день?\n\n"
    "💰 Разработка и настройка под ваше заведение — <b>300 000 ₸</b> (под вашу номенклатуру, "
    "поставщиков, сеть). Дальше обслуживание — <b>30 000 ₸/мес</b>.\n\n"
    "👉 Напишите — подключу за день."
)

def _demo_upsell_kb() -> dict:
    return {"inline_keyboard": [
        [{"text": "💬 Подключить — написать менеджеру", "url": "https://t.me/wiregroup"}],
    ]}

LIMIT_REACHED_TEXT = (
    "📦 Лимит {limit} накладных в этом месяце исчерпан.\n"
    "Докупить: +50 за 4 000 ₸ или +100 за 8 000 ₸ — напишите менеджеру.\n"
    "Остаток докупленного переносится на следующий месяц."
)

def _wiregroup_kb() -> dict:
    return {"inline_keyboard": [
        [{"text": "💬 Написать менеджеру — докупить", "url": "https://t.me/wiregroup"}],
    ]}


def _build_stats_text() -> str:
    """Дайджест владельцу. Демо-бот и основной — разные Render-сервисы с разными
    SQLite: юзеры (user_acquisition) есть только у демо, квоты (invoice_quota) —
    только у основного. Поэтому каждый деплой шлёт СВОЮ половину, иначе чужая
    секция всегда была бы нулями."""
    if DEMO_ONLY:
        a = acquisition_stats()
        # escape — на случай старых source, записанных до санитизации
        src = " · ".join(f"{html.escape(s)} {c}" for s, c in a["by_source"]) or "—"
        return (
            f"👥 Юзеры демо-бота\n"
            f"Всего: {a['total']} · сегодня: {a['today']} · за неделю: {a['week']}\n"
            f"Источники: {src}"
        )
    q = quota_stats()
    return (
        f"📦 Накладные (платные аккаунты)\n"
        f"Активных аккаунтов: {q['accounts']}\n"
        f"Распознано в этом месяце: {q['used']}\n"
        f"Докуплено (баланс): {q['extra']}\n"
        f"Упёрлись в лимит: {q['at_limit']} аккаунтов"
    )


def _build_today_text() -> str:
    rows = acquisitions_today()
    if not rows:
        return "За сегодня новых юзеров нет."
    lines = [f"🆕 За сегодня: {len(rows)}"]
    for r in rows:
        t = datetime.fromtimestamp(r["ts"]).strftime("%H:%M")
        uname = f"@{r['username']}" if r["username"] else "(без username)"
        # escape — на случай старых строк, записанных до санитизации source
        lines.append(f"{t} · id {r['telegram_id']} · {uname} · {html.escape(r['source'])}")
    return "\n".join(lines)


def _limit_text(telegram_id: int) -> str:
    reset_quota_if_due(telegram_id)
    q = get_quota(telegram_id)
    base_left = max(0, q["base_limit"] - q["used"])
    avail = base_left + q["extra"]
    reset_date = datetime.fromtimestamp(q["period_start_ts"] + 30 * 86400).strftime("%d.%m.%Y")
    return (
        f"📦 Накладные\n"
        f"По подписке в этом месяце: {base_left} из {q['base_limit']}\n"
        f"Докуплено (баланс): {q['extra']}\n"
        f"Доступно всего: {avail}\n"
        f"Лимит обновится: {reset_date}"
    )


# ============ DEMO DATA для остальных вкладок (курьеры/клиенты/год-год/план/…) ============
# Чтобы демо-бот показывал заполненными ВСЕ вкладки, как основной. Формы 1-в-1 с реальными
# ветками эндпоинтов. avg_way/avg_delay у курьеров — в минутах (так их рисует фронт).

DEMO_COURIERS = {
    "couriers": [
        {"name": "Алексей Соколов",   "deliveries": 312, "avg_way": 22, "avg_delay": 4,  "late_pct": 11},
        {"name": "Марат Жумабеков",   "deliveries": 287, "avg_way": 25, "avg_delay": 5,  "late_pct": 14},
        {"name": "Ержан Сапаров",     "deliveries": 241, "avg_way": 21, "avg_delay": 3,  "late_pct": 8},
        {"name": "Дмитрий Орлов",     "deliveries": 198, "avg_way": 27, "avg_delay": 7,  "late_pct": 19},
        {"name": "Игорь Лебедев",     "deliveries": 156, "avg_way": 29, "avg_delay": 9,  "late_pct": 23},
    ],
    "summary": {"deliveries": 1194, "couriers": 5, "avg_way": 24, "avg_delay": 5, "late_pct": 14},
}

DEMO_CANCELS = {
    "items": [
        {"name": "Стейк Рибай",       "amount": 18900},
        {"name": "Лосось гриль",      "amount": 12400},
        {"name": "Паста карбонара",   "amount": 7650},
        {"name": "Пицца Пепперони",   "amount": 6300},
        {"name": "Цезарь с курицей",  "amount": 4180},
        {"name": "Тирамису",          "amount": 2900},
    ],
    "total_amount": 52330,
    "total_orders": 19,
}

DEMO_COMPANIONS = {
    "pairs": [
        {"a": "Пицца Маргарита", "b": "Кока-кола 0.5", "count": 48},
        {"a": "Бургер Классик",  "b": "Картофель фри", "count": 41},
        {"a": "Цезарь с курицей","b": "Лимонад",       "count": 33},
        {"a": "Паста карбонара", "b": "Тирамису",       "count": 27},
        {"a": "Суши-сет",        "b": "Мисо-суп",       "count": 24},
        {"a": "Стейк Рибай",     "b": "Красное вино",   "count": 19},
    ],
    "companions": {
        "Пицца Маргарита":  [{"name": "Кока-кола 0.5", "count": 48}, {"name": "Чесночный соус", "count": 22}, {"name": "Лимонад", "count": 15}],
        "Бургер Классик":   [{"name": "Картофель фри", "count": 41}, {"name": "Кока-кола 0.5", "count": 30}, {"name": "Соус барбекю", "count": 18}],
        "Цезарь с курицей": [{"name": "Лимонад", "count": 33}, {"name": "Чиабатта", "count": 14}, {"name": "Эспрессо", "count": 9}],
        "Суши-сет":         [{"name": "Мисо-суп", "count": 24}, {"name": "Соевый соус", "count": 20}, {"name": "Зелёный чай", "count": 12}],
    },
}


def _demo_clients_agg() -> Dict[str, Dict[str, float]]:
    """Синтетическая база клиентов для демо: смесь разовых/лояльных/VIP.
    Прогоняется через _clients_segments/_compute_rfm — форма ответа гарантированно верная."""
    today = local_now().date()
    agg: Dict[str, Dict[str, float]] = {}
    for i in range(140):
        phone = f"+7700{1000000 + (i * 73491) % 8999999:07d}"
        if i % 12 == 0:
            orders = 11 + (i % 6)        # VIP (>=10)
        elif i % 3 == 0:
            orders = 2 + (i % 7)         # лояльные
        else:
            orders = 1                   # разовые
        avg_check = 3800 + (i * 137) % 5200
        spend = float(orders * avg_check)
        days_ago = (i * 11) % 210
        agg[phone] = {"orders": orders, "spend": spend,
                      "last_date": (today - timedelta(days=days_ago)).isoformat()}
    return agg


_DEMO_CHAN_SHARE = [("Обычный заказ", 0.52), ("Доставка", 0.28), ("Самовывоз", 0.12), ("Wolt", 0.08)]
_DEMO_MONTH_SEASON = [0.92, 0.90, 0.98, 1.02, 1.08, 1.12, 1.10, 1.05, 1.04, 1.06, 1.00, 1.20]


def _demo_period(key, label, year, base_rev, growth):
    avg_check = 4200 * growth
    channels = {}
    total_rev = 0
    total_orders = 0
    for ch, share in _DEMO_CHAN_SHARE:
        rev = round(base_rev * growth * share)
        ac = avg_check if ch == "Обычный заказ" else avg_check * 0.8
        orders = round(rev / ac) if ac else 0
        channels[ch] = {"rev": rev, "orders": orders}
        total_rev += rev
        total_orders += orders
    return {"key": key, "label": label, "year": year,
            "total_rev": total_rev, "total_orders": total_orders, "channels": channels}


def _demo_comparison() -> dict:
    today = local_now().date()
    cur_year = today.year
    prev_year = cur_year - 1
    months_ru = ['Янв', 'Фев', 'Мар', 'Апр', 'Май', 'Июн', 'Июл', 'Авг', 'Сен', 'Окт', 'Ноя', 'Дек']
    cur_month = today.month
    cur_week = today.isocalendar()[1]
    GROW = 1.18  # текущий год +18% к прошлому

    def month(year, m, growth):
        return _demo_period(f"{year}-{m:02d}", months_ru[m - 1], year,
                            11_000_000 * _DEMO_MONTH_SEASON[m - 1], growth)

    def week(year, w, growth):
        season = 0.90 + ((w * 7) % 20) / 100.0
        return _demo_period(f"{year}-W{w:02d}", f"Нед {w}", year, 2_550_000 * season, growth)

    return {
        "weeks": {
            "current":  [week(cur_year, w, GROW) for w in range(1, cur_week + 1)],
            "previous": [week(prev_year, w, 1.0) for w in range(1, 53)],
        },
        "months": {
            "current":  [month(cur_year, m, GROW) for m in range(1, cur_month + 1)],
            "previous": [month(prev_year, m, 1.0) for m in range(1, 13)],
        },
        "channels": sorted(ch for ch, _ in _DEMO_CHAN_SHARE),
        "cur_year": cur_year,
        "prev_year": prev_year,
    }


def _demo_plan() -> dict:
    today = local_now().date()
    yr = today.year
    cur_week = today.isocalendar()[1]
    rows = []
    for w in range(max(1, cur_week - 9), cur_week + 1):
        buckets = {}
        for b, base in [("Доставка", 520), ("Зал / вынос", 780), ("Агрегаторы", 240)]:
            season = 0.90 + ((w * 3) % 20) / 100.0
            plan = round(base * season)
            fact = round(plan * (0.85 + ((w * 7) % 30) / 100.0))
            buckets[b] = {"plan": plan, "fact": fact,
                          "pct": round(fact / plan * 100) if plan else None}
        try:
            first = date.fromisocalendar(yr, w, 1)
            label = f"Нед {w} · {first.day:02d}.{first.month:02d}"
        except Exception:
            label = f"Нед {w}"
        rows.append({"week": w, "label": label, "buckets": buckets})
    return {"has_link": True, "url": "", "buckets": PLAN_BUCKETS, "rows": rows, "year": yr}


def _demo_price_stats(product_id: str, days: int) -> dict:
    prod = next((p for p in DEMO_NOMENCLATURE if p["id"] == product_id), None)
    name = prod["name"] if prod else "Товар"
    unit = prod["unit"] if prod else "кг"
    try:
        base = 600 + (int(prod["code"]) % 20) * 70 if prod else 850
    except Exception:
        base = 850
    today = local_now().date()
    raw = [
        ("demo-sup-1", "ТОО «Демо-Поставщик»", 1.00, 6),
        ("demo-sup-2", "ИП Свежие Продукты",   1.08, 12),
        ("demo-sup-3", "ТОО «ОптТорг»",          1.15, 21),
    ]
    suppliers = [
        {"supplier_id": sid, "supplier_name": sname,
         "price_per_unit": round(base * mult), "unit": unit,
         "date": (today - timedelta(days=ago)).isoformat()}
        for sid, sname, mult, ago in raw
    ]
    suppliers.sort(key=lambda r: r["price_per_unit"])
    best = suppliers[0]
    for s in suppliers:
        s["delta_pct"] = ((s["price_per_unit"] - best["price_per_unit"]) /
                          best["price_per_unit"] * 100) if best["price_per_unit"] else 0.0
    return {"product": {"id": product_id, "name": name, "unit": unit},
            "best": best, "suppliers": suppliers,
            "stats": {"purchases": 9, "days": days}, "iiko_ok": True}

MONTH_NAMES_RU = ["Янв", "Фев", "Мар", "Апр", "Май", "Июн", "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"]


def _fmt_date(dt_str: str) -> str:
    """'2024-01-15T...' → '15.01.2024'"""
    d = (dt_str or "")[:10]
    if len(d) == 10 and "-" in d:
        return f"{d[8:10]}.{d[5:7]}.{d[0:4]}"
    return d


async def _foodcost_month(client, org_id: str, date_from: str, date_to: str):
    # На самохостед iikoWeb себестоимости (DishCostSumInt) нет — не выдаём 0%/−30
    # как «факт», а помечаем недоступной, чтобы не показывать клиенту неверную цифру.
    cost_available = getattr(client, "supports_cost", True)
    result = await client.olap_report(
        org_id, date_from, date_to,
        ["DishName", "ProductCategory.Name"],
        ["DishAmountSum", "DishCostSumInt", "DishCount"]
    )
    dishes, total_rev, total_cost = [], 0.0, 0.0
    for row in result.get("data", []):
        name = row.get("DishName", "")
        if not name:
            continue
        rev = float(row.get("DishAmountSum", 0) or 0)
        cost = float(row.get("DishCostSumInt", 0) or 0)
        count = int(row.get("DishCount", 0) or 0)
        cat = row.get("ProductCategory.Name") or row.get("ProductCategoryName", "")
        total_rev += rev
        total_cost += cost
        dishes.append({
            "name": name, "category": cat, "revenue": rev, "count": count,
            "cost_pct": (round(cost / rev * 100, 1) if rev else 0.0) if cost_available else None
        })
    return dishes, total_rev, total_cost, cost_available


# кеш техкарт: {(base_url, productId): (ts, items)}; техкарты меняются редко
_recipe_cache: Dict[tuple, tuple] = {}
_RECIPE_TTL = 24 * 3600

# кеш готового результата расчёта фудкоста по техкартам: {(org_id, period): (ts, resp)}
_foodcost_result_cache: Dict[tuple, tuple] = {}
_FOODCOST_RESULT_TTL = 24 * 3600

async def _get_recipe_cached(client, product_id: str, date: str) -> list:
    ck = (getattr(client, "base_url", ""), product_id)
    hit = _recipe_cache.get(ck)
    now = int(datetime.now().timestamp())
    if hit and now - hit[0] < _RECIPE_TTL:
        return hit[1]
    items = await client.get_assembly_prepared(product_id, date)
    _recipe_cache[ck] = (now, items)
    return items


async def _foodcost_from_recipes(client, dishes_sold: list, date_to: str) -> dict:
    """dishes_sold — уже полученный список проданных блюд из _foodcost_month (results[0][0])."""
    # карта имя->productId и цены (параллельно)
    name_map, prices = await asyncio.gather(client.get_dish_name_map(), client.get_stock_prices())
    # собираем sales с productId и тянем техкарты только по проданным
    date_for_recipe = date_to[:10]
    sales = []
    for d in dishes_sold:
        pid = name_map.get(str(d.get("name", "")).strip().lower())
        sales.append({"productId": pid, "name": d.get("name", ""),
                      "category": d.get("category", ""),
                      "revenue": d.get("revenue", 0), "count": d.get("count", 0)})
    recipes = {}
    pids = [s["productId"] for s in sales if s["productId"]]
    # пачками по 6 параллельно
    for i in range(0, len(pids), 6):
        batch = pids[i:i + 6]
        got = await asyncio.gather(*[_get_recipe_cached(client, p, date_for_recipe) for p in batch],
                                   return_exceptions=True)
        for p, items in zip(batch, got):
            recipes[p] = [] if isinstance(items, Exception) else items
    return compute_recipe_foodcost(sales, recipes, prices)


# ============ MODELS ============
class SetupValidate(BaseModel):
    api_login: str
    iiko_server: Optional[str] = None
    api_password: Optional[str] = None


class SetupSave(BaseModel):
    api_login: str
    organization_id: str
    organization_name: str
    iiko_server: Optional[str] = None
    api_password: Optional[str] = None


class AIAnalyze(BaseModel):
    question: str
    context: Dict[str, Any]


class ExpenseCreate(BaseModel):
    name: str
    category: str
    amount: float
    kassa: str
    date: str


class ExportSendPayload(BaseModel):
    filename: str = Field(..., max_length=200)
    content: str = Field(..., max_length=5_000_000)
    caption: Optional[str] = None


# ============ ENDPOINTS ============
def _iiko_unavailable() -> Dict[str, Any]:
    """Ответ при сбое iiko для РЕАЛЬНОГО клиента: не подсовываем фейковые DEMO-данные
    (выдуманную выручку/ФИО) как настоящие — честно сообщаем о недоступности."""
    return {"_iiko_error": "Данные iiko временно недоступны. Обновите через минуту."}


def _check_required_secrets() -> None:
    """Fail-closed в проде: без BOT_TOKEN авторизация отключается (все становятся
    user id 0), без ENCRYPTION_KEY пароли iiko пишутся в БД открытым текстом.
    Все три секрета обязательны в production."""
    if not IS_PROD:
        return
    missing = []
    if not BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN (без него авторизация выключена)")
    if not os.getenv("ENCRYPTION_KEY"):
        missing.append("ENCRYPTION_KEY (без него пароли iiko в БД открытым текстом)")
    if not TELEGRAM_WEBHOOK_SECRET:
        missing.append("TELEGRAM_WEBHOOK_SECRET")
    if missing:
        raise RuntimeError("Отказ запуска (fail-closed): не заданы " + "; ".join(missing))


@app.on_event("startup")
async def startup():
    _check_required_secrets()
    init_db()
    from database import init_reviews_db, init_salary_db, init_salary_overrides_db
    init_reviews_db()
    init_salary_db()
    init_salary_overrides_db()
    # Фоновый планировщик воскресного отчёта ген.директору
    asyncio.create_task(_weekly_report_scheduler())
    # Понедельничные итоги недели — каждому владельцу по его филиалам
    asyncio.create_task(_weekly_users_report_scheduler())
    # Ночная сводка кассовых смен (00:05)
    asyncio.create_task(_daily_shifts_scheduler())
    # Ежедневная сводка владельцу — на ОБОИХ деплоях: демо шлёт юзеров,
    # основной — квоты накладных (см. _build_stats_text, БД у сервисов разные).
    asyncio.create_task(_daily_stats_scheduler())
    if DEMO_ONLY:
        asyncio.create_task(_demo_sales_scheduler())


@app.get("/")
async def root():
    # no-store — иначе Telegram-webview держит старый бандл и не подхватывает
    # новые правки фронта (показывал старую выручку из старого index.html).
    return FileResponse("index.html",
                        headers={"Cache-Control": "no-store, must-revalidate"})


@app.get("/api/version")
async def version():
    """Хеш задеплоенного коммита — чтобы проверять, что прод реально обновился.
    На Render берётся из RENDER_GIT_COMMIT, локально — git rev-parse. Без авторизации."""
    commit = os.environ.get("RENDER_GIT_COMMIT") or ""
    if not commit:
        try:
            import subprocess
            commit = subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=os.path.dirname(os.path.abspath(__file__)),
                stderr=subprocess.DEVNULL,
            ).decode().strip()
        except Exception:
            commit = "unknown"
    short = commit[:7] if commit and commit != "unknown" else commit
    return {"service": "iiko", "commit": commit, "short": short,
            "branch": os.environ.get("RENDER_GIT_BRANCH")}


@app.get("/api/config")
async def get_config():
    """Публичный конфиг для фронта. demo_only=True — это демо-бот."""
    return {"demo_only": DEMO_ONLY}


@app.get("/api/demo/trial")
async def demo_trial(user: Dict = Depends(get_current_user)):
    """Статус 24ч-триала для текущего демо-юзера (фронт ветвится по state)."""
    if not DEMO_ONLY:
        return {"state": "disabled"}
    return _demo_trial_state(int(user.get("id", 0) or 0))


@app.post("/api/setup/validate")
@limiter.limit("5/15 minutes")
async def setup_validate(request: Request, payload: SetupValidate, user: Dict = Depends(get_current_user)):
    if DEMO_ONLY and (payload.api_login == "demo" or payload.api_login == DEMO_API_LOGIN):
        return {"valid": True, "organizations": [{"id": "demo-org-1", "name": "Демо Ресторан"}]}
    try:
        client = get_client(payload.api_login, payload.iiko_server, payload.api_password)
        orgs = await client.get_organizations()
        return {
            "valid": True,
            "organizations": [{"id": o["id"], "name": o["name"]} for o in orgs]
        }
    except Exception as e:
        return {"valid": False, "error": str(e)}


@app.post("/api/setup/save")
async def setup_save(payload: SetupSave, user: Dict = Depends(get_current_user)):
    tid = user.get("id", 0)
    create_user(tid, user.get("username"), user.get("first_name"))
    add_restaurant(tid, payload.api_login, payload.organization_id, payload.organization_name,
                   payload.iiko_server, payload.api_password)
    set_demo_mode(tid, 0)
    return {"success": True}


@app.get("/api/restaurants")
async def list_restaurants(user: Dict = Depends(get_current_user)):
    tid = user.get("id", 0)
    create_user(tid, user.get("username"), user.get("first_name"))
    rests = get_restaurants(tid)
    if not rests:
        # Не меняем is_demo флаг автоматически — только явный /api/demo его ставит
        return [DEMO_RESTAURANT]
    # Если есть реальные рестораны — сбрасываем demo флаг на всякий случай
    set_demo_mode(tid, 0)
    return rests


@app.delete("/api/restaurants/{rest_id}")
async def remove_restaurant(rest_id: int, user: Dict = Depends(get_current_user)):
    delete_restaurant(rest_id, user.get("id", 0))
    return {"success": True}


@app.post("/api/demo")
async def enable_demo(user: Dict = Depends(get_current_user)):
    if not DEMO_ONLY:
        raise HTTPException(status_code=404, detail="Not found")
    tid = user.get("id", 0)
    create_user(tid, user.get("username"), user.get("first_name"))
    set_demo_mode(tid, 1)
    return {"success": True}


@app.post("/api/logout")
async def logout(user: Dict = Depends(get_current_user)):
    """Полный логаут: удаляем все рестораны юзера + сбрасываем демо-флаг."""
    tid = user.get("id", 0)
    create_user(tid, user.get("username"), user.get("first_name"))
    n = delete_all_restaurants(tid)
    set_demo_mode(tid, 0)
    return {"success": True, "deleted": n}


def _is_demo_user(user: Dict) -> bool:
    # Демо живёт только на демо-боте (DEMO_ONLY=1): там все юзеры демо.
    # На основном боте демо-режима нет вообще.
    return DEMO_ONLY


def _demo_trial_state(telegram_id: int) -> Dict[str, Any]:
    """Состояние триала (DEMO_TRIAL_HOURS): none | active | ended (+ org_id, остаток секунд)."""
    row = get_demo_trial(telegram_id)
    if not row:
        return {"state": "none"}
    now = datetime.now(timezone.utc).timestamp()
    left = int(row["started_ts"] + DEMO_TRIAL_HOURS * 3600 - now)
    if left > 0:
        return {"state": "active", "org_id": row["org_id"],
                "seconds_left": left, "hours_left": round(left / 3600, 1)}
    return {"state": "ended", "org_id": row["org_id"], "seconds_left": 0}


def _trial_active(telegram_id: int) -> bool:
    return DEMO_ONLY and _demo_trial_state(telegram_id).get("state") == "active"


def _use_demo_data(user: Dict, org_id: str) -> bool:
    """Отдавать ли демо-сэмпл вместо реальных данных.
    - demo-org-1 → всегда демо;
    - демо-юзер без активного триала → демо;
    - демо-юзер с активным триалом → НЕ демо (его реальные данные).
    На основном боте `_is_demo_user` = False → возвращает False без обращения к БД."""
    if org_id == "demo-org-1":
        return True
    return _is_demo_user(user) and not _trial_active(int(user.get("id", 0) or 0))


def _get_org_id(org_id: str, user: Dict) -> str:
    if _use_demo_data(user, org_id):
        return "demo"
    rests = get_restaurants(user.get("id", 0))
    for r in rests:
        if r["organization_id"] == org_id:
            return r["organization_id"]
    raise HTTPException(status_code=404, detail="Restaurant not found")


def _get_restaurant_info(org_id: str, user: Dict) -> Dict[str, Any]:
    """Возвращает api_login, iiko_server, api_password, org_name для ресторана."""
    if org_id == "demo" or _use_demo_data(user, org_id):
        return {"api_login": "demo", "iiko_server": None, "api_password": None, "org_name": ""}
    rests = get_restaurants(user.get("id", 0))
    for r in rests:
        if r["organization_id"] == org_id:
            return {
                "api_login": r["api_login"],
                "iiko_server": r.get("iiko_server"),
                "api_password": r.get("api_password"),
                "org_name": r.get("organization_name", ""),
            }
    return {"api_login": "demo", "iiko_server": None, "api_password": None, "org_name": ""}


def _olap_dept(real_org: str, info: Dict) -> str:
    """iikoWeb OLAP Department фильтр принимает название, облако — UUID."""
    if info.get("iiko_server") and info.get("org_name"):
        return info["org_name"]
    return real_org


def _parse_org_ids(org_ids_csv: Optional[str], path_org_id: str,
                   user: Dict) -> List[str]:
    """Список валидных org_id. Если org_ids_csv пуст — fallback на path_org_id."""
    if not org_ids_csv:
        return [_get_org_id(path_org_id, user)]
    ids = [x.strip() for x in org_ids_csv.split(",") if x.strip()]
    if not ids:
        return [_get_org_id(path_org_id, user)]
    valid = []
    for oid in ids:
        try:
            valid.append(_get_org_id(oid, user))
        except HTTPException:
            continue
    if not valid:
        raise HTTPException(status_code=404, detail="No accessible restaurants")
    return valid


def _cache_period_part(period: str, custom_from: Optional[str], custom_to: Optional[str]) -> str:
    if period == "custom":
        return f"custom:{custom_from}:{custom_to}"
    return period


def _get_api_login(org_id: str, user: Dict) -> str:
    return _get_restaurant_info(org_id, user)["api_login"]


async def branch_shift_sessions(client, dept: str, date_from: str, date_to: str
                                ) -> Tuple[Optional[List[Dict[str, Any]]], Dict[str, Any]]:
    """Кассовые смены ОДНОГО филиала за период (операционный день = как в кассе).

    Сервер может держать несколько филиалов (концепций) на одних кредах, а в
    смене из полей-разделителей есть только conceptionId. Поэтому сужаем смены
    до концепций ИМЕННО этого филиала по детерминированной цепочке (без имён):
      Department(филиал) → Conception.Code (OLAP) → conceptionId (entities/list).
    Если сузить не удалось (одиночный сервер/справочник пуст) — берём все смены.
    Возвращает (смены | None при ошибке, debug-инфо).
    """
    dbg: Dict[str, Any] = {}

    async def _fetch_sessions():
        return await client.get_cashshifts(date_from, date_to)

    async def _fetch_codes():
        try:
            r = await client.olap_report(dept, date_from, date_to,
                                         ["Conception.Code"], ["DishAmountSum"])
            return {str(x.get("Conception.Code")) for x in r.get("data", [])
                    if x.get("Conception.Code") is not None}, None
        except Exception as e:
            return set(), str(e)[:160]

    # cashshifts и OLAP-коды концепции — параллельно (уменьшает лаг)
    sessions, (codes, codes_err) = await asyncio.gather(_fetch_sessions(), _fetch_codes())
    dbg["session_count"] = len(sessions)
    if codes_err:
        dbg["codes_err"] = codes_err
    dbg["codes"] = sorted(codes)

    target_ids: set = set()
    if codes:
        try:
            concs = await client.get_conceptions()
            target_ids = {c["id"] for c in concs
                          if c.get("code") in codes and c.get("id")}
        except Exception as e:
            dbg["conceptions_err"] = str(e)[:160]
    dbg["target_ids"] = sorted(target_ids)

    if target_ids:
        sel = [s for s in sessions if s.get("conceptionId") in target_ids]
    else:
        # код концепции филиала не определился. Если сервер одиночный (≤1 концепция
        # в сменах) — все смены его, берём. Если мультифилиальный — НЕЛЬЗЯ брать все
        # (покажем чужую выручку всей сети), отдаём None → фронт останется на OLAP.
        distinct = {s.get("conceptionId") for s in sessions if s.get("conceptionId")}
        if len(distinct) <= 1:
            sel = sessions
        else:
            dbg["ambiguous_multibranch"] = True
            dbg["selected_sessions"] = 0
            return None, dbg
    dbg["selected_sessions"] = len(sel)
    return sel, dbg


async def branch_shift_revenue(client, dept: str, date_from: str, date_to: str
                               ) -> Tuple[Optional[float], Dict[str, Any]]:
    """Выручка ОДНОГО филиала из кассовых смен — см. branch_shift_sessions."""
    sel, dbg = await branch_shift_sessions(client, dept, date_from, date_to)
    if sel is None:
        return None, dbg
    rev = client.cashshifts_revenue(sel)
    dbg["revenue"] = rev
    return rev, dbg


@app.get("/api/dashboard/{org_id}")
async def dashboard(org_id: str, response: Response, period: str = "today",
                    from_: Optional[str] = Query(None, alias="from"),
                    to: Optional[str] = Query(None),
                    org_ids: Optional[str] = Query(None),
                    user: Dict = Depends(get_current_user)):
    # webview не должен кешировать выручку (показывал старую цифру после фикса)
    response.headers["Cache-Control"] = "no-store"
    if _use_demo_data(user, org_id):
        return DEMO_DASHBOARD

    orgs = _parse_org_ids(org_ids, org_id, user)
    date_from, date_to = get_period_dates(period, from_, to)
    branches_key = ",".join(sorted(orgs))
    cache_period_key = _cache_period_part(period, from_, to) + ":" + branches_key
    key = _cache_key(user.get("id", 0), "dashboard", branches_key, cache_period_key)
    cached = _get_cached(key)
    if cached:
        return cached

    async def fetch_one(real_org: str):
        info = _get_restaurant_info(real_org, user)
        client = get_client(info["api_login"], info.get("iiko_server"), info.get("api_password"))
        try:
            r = await client.olap_report(
                _olap_dept(real_org, info), date_from, date_to,
                ["OpenDate.Typed"], ["GuestNum", "DishAmountSum", "OrdersCount"]
            )
            return r.get("data", [])
        except Exception as e:
            logger.warning(f"[dashboard] {real_org}: {e}")
            return []

    # Выручка = кассовые смены (операционный день), как в кассе. SALES OLAP режет
    # по дате открытия заказа и расходится с кассой; смены — эталон. Каждый филиал
    # сужаем до своих концепций (см. branch_shift_sessions), поэтому на общем сервере
    # филиалы не смешиваются и не задваиваются. Смены тянем сразу расширенным окном
    # (период + последние 7 дней): из ОДНОГО набора считаем и карточку, и график —
    # иначе цифры на графике и в карточке расходятся.
    today = local_now().date()
    chart_days = [(today - timedelta(days=i)) for i in range(6, -1, -1)]
    win_from = min(date_from, chart_days[0].isoformat())
    win_to = max(date_to, today.isoformat())

    async def fetch_sessions(real_org: str):
        info = _get_restaurant_info(real_org, user)
        client = get_client(info["api_login"], info.get("iiko_server"), info.get("api_password"))
        if not hasattr(client, "get_cashshifts"):
            return None  # облачный клиент — кассовых смен нет, fallback на OLAP
        try:
            sel, _dbg = await branch_shift_sessions(
                client, _olap_dept(real_org, info), win_from, win_to)
            return sel
        except Exception as e:
            logger.warning(f"[dashboard] cashshifts {real_org}: {e}")
            return None

    def _sess_day(s: Dict[str, Any]) -> str:
        return (s.get("openDate") or "")[:10]

    RU_DOW = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

    try:
        results = await asyncio.gather(*(fetch_one(o) for o in orgs))
        all_rows = [row for batch in results for row in batch]
        olap_total = sum(float(r.get("DishAmountSum") or 0) for r in all_rows)
        guests = sum(float(r.get("GuestNum") or 0) for r in all_rows)
        orders = sum(float(r.get("OrdersCount") or 0) for r in all_rows)

        sess_results = await asyncio.gather(*(fetch_sessions(o) for o in orgs))
        got_sessions = [s for s in sess_results if s is not None]
        all_sessions = [s for batch in got_sessions for s in batch]

        if got_sessions and len(got_sessions) == len(orgs):
            # карточка: смены только выбранного периода
            total = round(sum(float(s.get("payOrders") or 0) for s in all_sessions
                              if date_from[:10] <= _sess_day(s) <= date_to[:10]), 2)
        else:
            total = olap_total  # не по всем филиалам есть смены — OLAP, как раньше
        avg_check = total / guests if guests else 0

        # график: те же смены, что и карточка; без смен — OLAP
        per_day: Dict[str, float] = {}
        if got_sessions:
            for s in all_sessions:
                d = _sess_day(s)
                per_day[d] = per_day.get(d, 0.0) + float(s.get("payOrders") or 0)
        else:
            for r in all_rows:
                d = str(r.get("OpenDate.Typed") or "")[:10]
                per_day[d] = per_day.get(d, 0.0) + float(r.get("DishAmountSum") or 0)

        # Дни графика = выбранный период, иначе сумма столбцов не сходится с
        # карточкой. Однодневные периоды (Сегодня/Вчера/Позавчера) — один столбец
        # бессмыслен: рисуем последние 7 дней, а ПОДСВЕЧИВАЕМ выбранный день —
        # его столбец равен карточке. highlight = индекс подсвеченного столбца.
        _df, _dt = date.fromisoformat(date_from[:10]), date.fromisoformat(date_to[:10])
        if _df == _dt and chart_days[0] <= _df <= chart_days[-1]:
            groups = [(RU_DOW[d.weekday()], [d]) for d in chart_days]
            highlight = (_df - chart_days[0]).days
        else:
            days = [_df + timedelta(days=i) for i in range((_dt - _df).days + 1)]
            if len(days) <= 7:
                groups = [(RU_DOW[d.weekday()], [d]) for d in days]
            elif len(days) <= 31:
                groups = [(d.strftime("%d.%m"), [d]) for d in days]
            else:  # длинный произвольный период — по неделям (метка = начало недели)
                groups = [(days[i].strftime("%d.%m"), days[i:i + 7])
                          for i in range(0, len(days), 7)]
            highlight = len(groups) - 1  # последний столбец = самый свежий
        chart = {"labels": [g[0] for g in groups],
                 "data": [round(sum(per_day.get(d.isoformat(), 0.0) for d in g[1]), 2)
                          for g in groups],
                 "highlight": highlight}

        resp = {
            "revenue_today": total,
            "revenue_change": 0,
            "avg_check": avg_check,
            "avg_check_change": 0,
            "guests": guests,
            "guests_change": 0,
            "open_orders": orders,
            "revenue_yesterday": 0,
            "forecast": total * 1.4,
            "chart_7days": chart,
            "branches_count": len(orgs),
        }
        _set_cached(key, resp)
        return resp
    except Exception as e:
        print(f"[dashboard] {e}")
        return _iiko_unavailable()


@app.get("/api/revenue/{org_id}")
async def revenue(org_id: str, response: Response, period: str = "today",
                  from_: Optional[str] = Query(None, alias="from"),
                  to: Optional[str] = Query(None),
                  org_ids: Optional[str] = Query(None),
                  user: Dict = Depends(get_current_user)):
    """Выручка из КАССОВЫХ СМЕН (операционный день = как в кассе). Фронт берёт
    её для шапки дашборда вместо OLAP-суммы (та считается по дате открытия и
    расходится с кассой). Гости/чеки/график остаются на OLAP."""
    response.headers["Cache-Control"] = "no-store"
    if _use_demo_data(user, org_id):
        return {"revenue": DEMO_DASHBOARD["revenue_today"], "ok": False, "source": "demo"}

    orgs = _parse_org_ids(org_ids, org_id, user)
    date_from, date_to = get_period_dates(period, from_, to)
    branches_key = ",".join(sorted(orgs))
    cache_period_key = _cache_period_part(period, from_, to) + ":" + branches_key
    key = _cache_key(user.get("id", 0), "revenue", branches_key, cache_period_key)
    cached = _get_cached(key)
    if cached:
        return cached

    async def one(real_org: str):
        info = _get_restaurant_info(real_org, user)
        client = get_client(info["api_login"], info.get("iiko_server"), info.get("api_password"))
        if not hasattr(client, "get_cashshifts"):
            return None
        try:
            rev, _dbg = await branch_shift_revenue(
                client, _olap_dept(real_org, info), date_from, date_to)
            return rev
        except Exception as e:
            logger.warning(f"[revenue] {real_org}: {e}")
            return None

    results = await asyncio.gather(*(one(o) for o in orgs))
    revs = [r for r in results if r is not None]
    if not revs:
        # не смогли получить смены (облако/ошибка) — пусть фронт остаётся на OLAP
        return {"revenue": None, "ok": False, "source": "none"}
    resp = {"revenue": round(sum(revs), 2), "ok": True, "source": "cashshifts"}
    _set_cached(key, resp)
    return resp


# ── Индивидуальная поправка каналов «для дяди» ──────────────────────────────
# SaaS для всех остаётся как есть, но для конкретного клиента «сайтовые» каналы
# склеиваются с обычными по имени: «Сайт Доставка» → «Доставка»,
# «Сайт Самовывоз» → «Самовывоз». Применяется ТОЛЬКО для его telegram_id.
# TODO: проставить реальный telegram_id дяди (придёт позже) — пока None = выключено.
# Если в его iiko каналы названы иначе — поправить ключи в UNCLE_CHANNEL_MERGE.
UNCLE_TELEGRAM_ID: Optional[int] = None

UNCLE_CHANNEL_MERGE: Dict[str, str] = {
    "Сайт Доставка":  "Доставка",
    "Сайт Самовывоз": "Самовывоз",
}


def _normalize_channel_name(name: str, user: Dict) -> str:
    """Для дяди склеивает сайтовые каналы с обычными. Остальным — без изменений."""
    if UNCLE_TELEGRAM_ID is not None and user.get("id") == UNCLE_TELEGRAM_ID:
        return UNCLE_CHANNEL_MERGE.get(name.strip(), name)
    return name


# ── Тип оплаты → счёт (куда ушли деньги) ────────────────────────────────────
# iiko API связь «тип оплаты → счёт» не отдаёт (accountId в словаре PaymentType
# пустой), поэтому укрупняем сами по ключевым словам в названии типа оплаты.
# Порядок важен: первое совпадение выигрывает. Незнакомые типы → «Прочее»
# (ничего не теряется — сумма по счетам = сумме по типам оплаты = выручке).
_PAY_ACCOUNT_RULES: List[Tuple[str, str]] = [
    ("нал",        "Наличные"),
    ("cash",       "Наличные"),
    ("kaspi",      "Kaspi"),
    ("каспи",      "Kaspi"),
    ("wolt",       "Wolt"),
    ("волт",       "Wolt"),
    ("glovo",      "Glovo"),
    ("глово",      "Glovo"),
    ("яндекс",     "Яндекс"),
    ("yandex",     "Яндекс"),
    ("choco",      "Chocofood"),
    ("чоко",       "Chocofood"),
    ("gourmet",    "Gourmet"),
    ("перевод",    "Перевод"),
    ("онлайн",     "Онлайн-оплата"),
    ("epay",       "Онлайн-оплата"),
    ("терминал",   "Эквайринг (банк)"),
    ("карт",       "Эквайринг (банк)"),
    ("балл",       "Бонусы / списания"),
    ("промокод",   "Бонусы / списания"),
    ("зп",         "Бонусы / списания"),
    ("сотрудник",  "Бонусы / списания"),
]


def _pay_account(pay_type: str) -> str:
    """Укрупнённый счёт для типа оплаты по ключевым словам в названии."""
    low = (pay_type or "").lower()
    for kw, account in _PAY_ACCOUNT_RULES:
        if kw in low:
            return account
    return "Прочее"


@app.get("/api/sales/{org_id}")
async def sales(org_id: str, period: str = "today",
                from_: Optional[str] = Query(None, alias="from"),
                to: Optional[str] = Query(None),
                org_ids: Optional[str] = Query(None),
                user: Dict = Depends(get_current_user)):
    if _use_demo_data(user, org_id):
        return DEMO_SALES

    orgs = _parse_org_ids(org_ids, org_id, user)
    date_from, date_to = get_period_dates(period, from_, to)
    branches_key = ",".join(sorted(orgs))
    cache_period_key = _cache_period_part(period, from_, to) + ":" + branches_key
    key = _cache_key(user.get("id", 0), "sales", branches_key, cache_period_key)
    cached = _get_cached(key)
    if cached:
        return cached

    async def fetch_one(real_org: str):
        info = _get_restaurant_info(real_org, user)
        client = get_client(info["api_login"], info.get("iiko_server"), info.get("api_password"))
        async def safe_olap(*args, **kwargs):
            try:
                return await client.olap_report(*args, **kwargs)
            except Exception as e:
                logger.warning(f"[sales] iiko olap failed for org={real_org}: {e} | group_by={args[3] if len(args) > 3 else kwargs.get('group_by')}")
                return {"data": []}
        dept = _olap_dept(real_org, info)
        return await asyncio.gather(
            # DishDiscountSumInt (после скидок) — чтобы сумма каналов = выручке кассы.
            # OrdersCount по каналам корректен (заказ ровно в одном канале) —
            # отсюда точное общее число заказов для «Корзины» на фронте.
            safe_olap(dept, date_from, date_to, ["OrderType.Name"], ["DishDiscountSumInt", "GuestNum", "OrdersCount"]),
            safe_olap(dept, date_from, date_to, ["DishName"], ["DishAmountSum", "DishCount"]),
            safe_olap(dept, date_from, date_to, ["ProductCategory.Name"], ["DishAmountSum"]),
            # Выручка по типам оплаты. Мера DishDiscountSumInt = реально оплачено
            # (после скидок) — сходится с кассой (payOrders) в ноль, в отличие от
            # DishAmountSum/DishSumInt (до скидок).
            safe_olap(dept, date_from, date_to, ["PayTypes"], ["DishDiscountSumInt"]),
        )

    try:
        per_org = await asyncio.gather(*(fetch_one(o) for o in orgs))
        channels_rows = [r for t in per_org for r in t[0].get("data", [])]
        dishes_rows   = [r for t in per_org for r in t[1].get("data", [])]
        cats_rows     = [r for t in per_org for r in t[2].get("data", [])]
        pay_rows      = [r for t in per_org for r in t[3].get("data", [])]

        # Дедупликация по имени для каналов (несколько филиалов могут вернуть один и тот же канал)
        ch_agg: Dict[str, Dict[str, float]] = {}
        for row in channels_rows:
            name = row.get("OrderType.Name") or row.get("OrderTypeName", "")
            if not name:
                continue
            name = _normalize_channel_name(name, user)
            if name not in ch_agg:
                ch_agg[name] = {"revenue": 0.0, "guests": 0, "orders": 0}
            ch_agg[name]["revenue"] += float(row.get("DishDiscountSumInt", 0) or 0)
            ch_agg[name]["guests"]  += int(row.get("GuestNum", 0) or 0)
            ch_agg[name]["orders"]  += int(float(row.get("OrdersCount", 0) or 0))
        channels = [{
            "name": n, "revenue": v["revenue"], "guests": int(v["guests"]),
            "orders": int(v["orders"]),
            "avg_check": v["revenue"] / v["guests"] if v["guests"] else 0, "top": True
        } for n, v in ch_agg.items()]
        channels.sort(key=lambda x: x["revenue"], reverse=True)
        total_orders = sum(c["orders"] for c in channels)

        # Дедупликация по названию блюда
        di_agg: Dict[str, Dict[str, float]] = {}
        for row in dishes_rows:
            name = row.get("DishName", "")
            if not name:
                continue
            if name not in di_agg:
                di_agg[name] = {"revenue": 0.0, "count": 0}
            di_agg[name]["revenue"] += float(row.get("DishAmountSum", 0) or 0)
            di_agg[name]["count"]   += int(row.get("DishCount", 0) or 0)
        dishes = [{"name": n, "revenue": v["revenue"], "count": int(v["count"])} for n, v in di_agg.items()]
        dishes.sort(key=lambda x: x["revenue"], reverse=True)

        # Дедупликация по категории
        cat_agg: Dict[str, float] = {}
        for row in cats_rows:
            name = row.get("ProductCategory.Name") or row.get("ProductCategoryName", "")
            if not name:
                continue
            cat_agg[name] = cat_agg.get(name, 0.0) + float(row.get("DishAmountSum", 0) or 0)
        COLORS = ["#4A90E2", "#34C759", "#FF9500", "#AF52DE", "#FF2D55", "#5AC8FA", "#FFD60A", "#FF3B30"]
        cats_sorted = sorted(cat_agg.items(), key=lambda kv: kv[1], reverse=True)
        total_rev = sum(v for _, v in cats_sorted)
        categories = [{
            "name": name,
            "value": round(amount / total_rev * 100, 1) if total_rev else 0,
            "color": COLORS[i % len(COLORS)]
        } for i, (name, amount) in enumerate(cats_sorted)]

        # Выручка по типам оплаты + укрупнение в «счета». Обе разбивки сходятся
        # с кассой (payOrders), т.к. мера DishDiscountSumInt = реально оплачено.
        pt_agg: Dict[str, float] = {}
        for row in pay_rows:
            name = row.get("PayTypes") or row.get("PayTypes.Name") or ""
            if not name:
                continue
            pt_agg[name] = pt_agg.get(name, 0.0) + float(row.get("DishDiscountSumInt", 0) or 0)
        pay_types = [{"name": n, "revenue": round(v, 2), "account": _pay_account(n)}
                     for n, v in pt_agg.items() if round(v, 2) != 0]
        pay_types.sort(key=lambda x: x["revenue"], reverse=True)

        acc_agg: Dict[str, float] = {}
        for p in pay_types:
            acc_agg[p["account"]] = acc_agg.get(p["account"], 0.0) + p["revenue"]
        accounts = [{"name": n, "revenue": round(v, 2)} for n, v in acc_agg.items()]
        accounts.sort(key=lambda x: x["revenue"], reverse=True)

        # all_dishes — полная номенклатура для ABC-анализа (топ-10 для него мал);
        # orders — точное число заказов (сумма OrdersCount по каналам).
        result = {"channels": channels, "top_dishes": dishes[:10],
                  "all_dishes": dishes, "orders": total_orders,
                  "categories": categories,
                  "pay_types": pay_types, "accounts": accounts,
                  "branches_count": len(orgs)}
        _set_cached(key, result)
        return result
    except Exception as e:
        print(f"[sales] {e}")
        return _iiko_unavailable()


@app.get("/api/shifts/{org_id}")
async def shifts(org_id: str, user: Dict = Depends(get_current_user)):
    """Сотрудники на смене сегодня с временем начала."""
    if _use_demo_data(user, org_id):
        return {"staff": [
            {"name": "Виктория Ким", "since": "10:00", "revenue": 1250000, "orders": 48},
            {"name": "Артём Волков",  "since": "10:00", "revenue": 980000,  "orders": 38},
            {"name": "Камила Ахметова", "since": "14:00", "revenue": 620000, "orders": 24},
        ]}
    real_org = _get_org_id(org_id, user)
    info = _get_restaurant_info(real_org, user)
    today = local_now().date()
    tomorrow = (today + timedelta(days=1)).isoformat()
    client = get_client(info["api_login"], info.get("iiko_server"), info.get("api_password"))
    try:
        result = await client.olap_report(
            _olap_dept(real_org, info),
            today.isoformat(), today.isoformat(),
            group_by=["WaiterName", "HourOpen"],
            aggregates=["DishAmountSum", "OrdersCount"]
        )
        rows = result.get("data", [])
        waiters: Dict[str, Any] = {}
        for row in rows:
            name = row.get("WaiterName", "")
            if not name:
                continue
            h_raw = row.get("HourOpen", 99)
            try:
                h_int = int(str(h_raw).split(":")[0])
            except Exception:
                h_int = 99
            rev = float(row.get("DishAmountSum", 0) or 0)
            orders = int(row.get("OrdersCount", 0) or 0)
            if name not in waiters:
                waiters[name] = {"min_hour": h_int, "revenue": 0.0, "orders": 0}
            if h_int < waiters[name]["min_hour"]:
                waiters[name]["min_hour"] = h_int
            waiters[name]["revenue"] += rev
            waiters[name]["orders"] += orders

        staff_list = sorted(
            [{"name": n, "since": f"{d['min_hour']:02d}:00",
              "revenue": round(d["revenue"]), "orders": d["orders"]}
             for n, d in waiters.items() if d["revenue"] > 0],
            key=lambda x: x["since"]
        )
        return {"staff": staff_list}
    except Exception as e:
        logger.warning(f"[shifts] {e}")
        return {"staff": []}


@app.get("/api/couriers/{org_id}")
async def couriers(org_id: str, period: str = "month",
                   from_: Optional[str] = Query(None, alias="from"),
                   to: Optional[str] = Query(None),
                   org_ids: Optional[str] = Query(None),
                   user: Dict = Depends(get_current_user)):
    """Аналитика курьеров: доставок, ср. время в пути, ср. опоздание, % опозданий.
    Один DELIVERIES OLAP на филиал (курьер×опоздание), агрегируем по курьеру."""
    if _use_demo_data(user, org_id):
        return DEMO_COURIERS
    orgs = _parse_org_ids(org_ids, org_id, user)
    if from_ and to:
        date_from, date_to = get_period_dates("custom", from_, to)
    else:
        # по умолчанию — скользящие 30 дней (карточка обещает «30 дней»)
        end = local_now().date()
        date_from, date_to = (end - timedelta(days=30)).isoformat(), end.isoformat()

    async def fetch_one(real_org: str):
        info = _get_restaurant_info(real_org, user)
        client = get_client(info["api_login"], info.get("iiko_server"), info.get("api_password"))
        if not hasattr(client, "olap_couriers"):
            return []
        try:
            return await client.olap_couriers(_olap_dept(real_org, info), date_from, date_to)
        except Exception as e:
            logger.warning(f"[couriers] {real_org}: {e}")
            return []

    per_org = await asyncio.gather(*(fetch_one(o) for o in orgs))
    rows = [r for batch in per_org for r in batch]

    agg: Dict[str, Dict[str, float]] = {}
    for d in rows:
        name = str(d.get("Delivery.Courier") or "").strip()
        if not name or "амовывоз" in name.lower():  # отсекаем самовывоз/не-курьеров
            continue
        n = float(d.get("UniqOrderId.OrdersCount") or 0)
        if n <= 0:
            continue
        delay = float(d.get("Delivery.Delay") or 0)
        way = float(d.get("Delivery.WayDurationSum") or 0)
        a = agg.setdefault(name, {"n": 0.0, "late": 0.0, "way": 0.0, "delaysum": 0.0})
        a["n"] += n
        a["way"] += way
        a["delaysum"] += delay * n
        if delay > 0:
            a["late"] += n

    couriers_list = []
    tot_n = tot_way = tot_delaysum = tot_late = 0.0
    for name, a in agg.items():
        n = a["n"]
        if n < 1:
            continue
        couriers_list.append({
            "name": name,
            "deliveries": int(n),
            "avg_way": round(a["way"] / n),
            "avg_delay": round(a["delaysum"] / n),
            "late_pct": round(a["late"] / n * 100),
        })
        tot_n += n; tot_way += a["way"]; tot_delaysum += a["delaysum"]; tot_late += a["late"]
    couriers_list.sort(key=lambda x: x["deliveries"], reverse=True)

    summary = {}
    if tot_n:
        summary = {
            "deliveries": int(tot_n),
            "couriers": len(couriers_list),
            "avg_way": round(tot_way / tot_n),
            "avg_delay": round(tot_delaysum / tot_n),
            "late_pct": round(tot_late / tot_n * 100),
        }
    return {"couriers": couriers_list, "summary": summary}


@app.get("/api/staff/{org_id}")
async def staff(org_id: str, period: str = "week", user: Dict = Depends(get_current_user)):
    if _use_demo_data(user, org_id):
        return DEMO_STAFF

    real_org = _get_org_id(org_id, user)
    info = _get_restaurant_info(real_org, user)
    login = info["api_login"]
    key = _cache_key(user.get("id", 0), "staff", real_org, period)
    cached = _get_cached(key)
    if cached:
        return cached

    date_from, date_to = get_period_dates(period)
    client = get_client(login, info.get("iiko_server"), info.get("api_password"))
    try:
        result = await client.olap_report(
            _olap_dept(real_org, info), date_from, date_to,
            ["WaiterName"],
            ["DishAmountSum", "GuestNum", "OrdersCount"]
        )
        staff_list = []
        total_rev = total_orders = total_guests = 0
        for row in result.get("data", []):
            name = row.get("WaiterName", "")
            if not name:
                continue
            revenue = float(row.get("DishAmountSum", 0) or 0)
            orders = int(row.get("OrdersCount", 0) or 0)
            guests = int(row.get("GuestNum", 0) or 0)
            staff_list.append({"name": name, "revenue": revenue, "orders": orders, "guests": guests})
            total_rev += revenue
            total_orders += orders
            total_guests += guests
        staff_list.sort(key=lambda x: x["revenue"], reverse=True)
        resp = {"total_revenue": total_rev, "orders": total_orders, "guests": total_guests, "staff": staff_list}
        _set_cached(key, resp)
        return resp
    except Exception as e:
        print(f"[staff] {e}")
        return _iiko_unavailable()


@app.get("/api/load-heatmap/{org_id}")
async def load_heatmap(org_id: str, period: str = "month",
                       org_ids: Optional[str] = Query(None),
                       user: Dict = Depends(get_current_user)):
    """Загрузка по дням недели × часам (heatmap «Час пик»). Метрика — кол-во заказов.
    Поддерживает «Все филиалы» через org_ids (агрегация по всем филиалам)."""
    if _use_demo_data(user, org_id):
        return DEMO_HEATMAP

    orgs = _parse_org_ids(org_ids, org_id, user)
    key = _cache_key(user.get("id", 0), "heatmap", ",".join(sorted(orgs)), period)
    cached = _get_cached(key)
    if cached:
        return cached

    date_from, date_to = get_period_dates(period)

    async def fetch_one(real: str):
        info = _get_restaurant_info(real, user)
        client = get_client(info["api_login"], info.get("iiko_server"), info.get("api_password"))
        try:
            return await client.olap_report(
                _olap_dept(real, info), date_from, date_to,
                ["HourOpen", "OpenDate.Typed"],
                ["OrdersCount", "DishAmountSum"],
            )
        except Exception as e:
            logger.warning(f"[heatmap] {real}: {e}")
            return {"data": []}

    per_org = await asyncio.gather(*(fetch_one(o) for o in orgs))
    grid: Dict[Tuple[int, int], Dict[str, float]] = {}
    for result in per_org:
        for row in result.get("data", []):
            try:
                h = int(str(row.get("HourOpen", "")).split(":")[0])
            except Exception:
                continue
            ds = str(row.get("OpenDate.Typed", ""))[:10]
            try:
                d = datetime.strptime(ds, "%Y-%m-%d").weekday()  # 0=Пн
            except Exception:
                continue
            orders = int(row.get("OrdersCount", 0) or 0)
            revenue = float(row.get("DishAmountSum", 0) or 0)
            cell = grid.setdefault((d, h), {"orders": 0, "revenue": 0.0})
            cell["orders"] += orders
            cell["revenue"] += revenue

    cells, mx, peak = [], 0, {"d": 0, "h": 0, "orders": 0}
    for (d, h), v in grid.items():
        o = int(v["orders"])
        if o <= 0:
            continue
        cells.append({"d": d, "h": h, "orders": o, "revenue": round(v["revenue"])})
        mx = max(mx, o)
        if o > peak["orders"]:
            peak = {"d": d, "h": h, "orders": o}
    resp = {"cells": cells, "max": mx, "peak": peak, "metric": "orders"}
    _set_cached(key, resp)
    return resp


@app.get("/api/chaspik")
async def chaspik_export(
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
    sla_minutes: Optional[int] = Query(None),
    user: Dict = Depends(get_current_user),
) -> dict:
    """Прокси к saya-kds /admin/analytics/export. Виден только CHASPIK_TG_IDS.
    Ключ X-API-Key держим на бэке и не отдаём во фронт."""
    if int(user.get("id", 0)) not in CHASPIK_TG_IDS:
        raise HTTPException(status_code=403, detail="forbidden")
    if not ANALYTICS_API_KEY or not CHASPIK_ORG_ID:
        raise HTTPException(
            status_code=503,
            detail="saya-kds выгрузка не настроена (нет ANALYTICS_API_KEY или CHASPIK_ORG_ID)")
    params: Dict = {"org_id": CHASPIK_ORG_ID}
    if from_:
        params["from"] = from_
    if to:
        params["to"] = to
    if sla_minutes is not None:
        params["sla_minutes"] = sla_minutes
    try:
        async with httpx.AsyncClient(timeout=60.0) as c:
            r = await c.get(f"{CHASPIK_BASE_URL}/admin/analytics/export",
                            params=params, headers={"X-API-Key": ANALYTICS_API_KEY})
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"saya-kds недоступен: {e}")
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"saya-kds {r.status_code}: {r.text[:200]}")
    return r.json()


@app.get("/api/foodcost/{org_id}")
async def foodcost(org_id: str, period: str = "month", compute: int = 0, user: Dict = Depends(get_current_user)):
    if _use_demo_data(user, org_id):
        return DEMO_FOODCOST

    real_org = _get_org_id(org_id, user)
    info = _get_restaurant_info(real_org, user)
    login = info["api_login"]
    key = _cache_key(user.get("id", 0), "foodcost", real_org, period)
    cached = _get_cached(key)
    if cached:
        return cached

    date_from, date_to = get_period_dates(period)
    client = get_client(login, info.get("iiko_server"), info.get("api_password"))
    try:
        today = local_now().date()
        trend_periods = []
        for i in range(3, -1, -1):
            m, y = today.month - i, today.year
            while m <= 0:
                m += 12
                y -= 1
            first = datetime(y, m, 1).date()
            last = (datetime(y, m + 1, 1).date() if m < 12 else datetime(y + 1, 1, 1).date()) - timedelta(days=1)
            trend_periods.append((MONTH_NAMES_RU[m - 1], first.isoformat(), min(last, today).isoformat()))

        tasks = [_foodcost_month(client, _olap_dept(real_org, info), date_from, date_to)]
        for _, mf, mt in trend_periods:
            tasks.append(_foodcost_month(client, _olap_dept(real_org, info), mf, mt))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        if isinstance(results[0], Exception):
            raise results[0]
        dishes, total_rev, total_cost, cost_available = results[0]

        NORM_PCT = 30
        dishes.sort(key=lambda x: (x["cost_pct"] if x["cost_pct"] is not None else -1), reverse=True)

        trend = []
        for idx, (month_name, _, _) in enumerate(trend_periods):
            r = results[idx + 1]
            if isinstance(r, Exception) or not cost_available:
                m_fact = None if not cost_available else 0.0
            else:
                _, m_rev, m_cost, _ = r
                m_fact = round(m_cost / m_rev * 100, 1) if m_rev else 0.0
            trend.append({"month": month_name, "fact": m_fact, "norm": NORM_PCT})

        if cost_available:
            fact_pct = round(total_cost / total_rev * 100, 1) if total_rev else 0.0
            resp = {
                "norm_pct": NORM_PCT,
                "fact_pct": fact_pct,
                "diff_pct": round(fact_pct - NORM_PCT, 1),
                "norm_sum": round(total_rev * NORM_PCT / 100),
                "fact_sum": round(total_cost),
                "warning": None,
                "cost_available": True,
                "trend": trend,
                "dishes": dishes[:20]
            }
        else:
            # Самохостед iikoWeb: считаем теоретический фудкост по техкартам.
            rc_key = (real_org, period)
            hit = _foodcost_result_cache.get(rc_key)
            now_ts = int(datetime.now().timestamp())
            if not compute:
                if hit and now_ts - hit[0] < _FOODCOST_RESULT_TTL:
                    return hit[1]
                resp = {
                    "norm_pct": NORM_PCT, "fact_pct": None, "diff_pct": None,
                    "norm_sum": round(total_rev * NORM_PCT / 100), "fact_sum": None,
                    "warning": None, "cost_available": False, "needs_compute": True,
                    "method": "recipes", "trend": trend, "dishes": dishes[:20],
                }
                return resp  # НЕ кешируем «заглушку» — расчёт по кнопке
            rec = await _foodcost_from_recipes(client, dishes, date_to)
            fpct = rec["fact_pct"]
            cov = rec["coverage_pct"]
            resp = {
                "norm_pct": NORM_PCT,
                "fact_pct": fpct,
                "diff_pct": (round(fpct - NORM_PCT, 1) if fpct is not None else None),
                "norm_sum": round((rec["base_rev"] or total_rev) * NORM_PCT / 100),
                "fact_sum": round(rec["fact_sum"]),
                "coverage_pct": cov,
                "warning": ("Нет продаж за период" if cov is None else
                            (None if cov >= 70 else
                             f"Оценка неполная: посчитано {cov}% выручки. Добавьте приходные цены/техкарты в iiko.")),
                "cost_available": True, "method": "recipes",
                "trend": trend, "dishes": rec["dishes"][:20],
            }
            _foodcost_result_cache[rc_key] = (now_ts, resp)
        _set_cached(key, resp)
        return resp
    except Exception as e:
        print(f"[foodcost] {e}")
        return _iiko_unavailable()


@app.get("/api/expenses/{org_id}")
async def expenses(org_id: str, period: str = "week", user: Dict = Depends(get_current_user)):
    if _use_demo_data(user, org_id):
        return DEMO_EXPENSES

    real_org = _get_org_id(org_id, user)
    info = _get_restaurant_info(real_org, user)
    login = info["api_login"]
    date_from, date_to = get_period_dates(period)
    client = get_client(login, info.get("iiko_server"), info.get("api_password"))
    operations = []

    try:
        iiko_data = await client.get_expense_acts([_olap_dept(real_org, info)], date_from, date_to)
        docs = iiko_data.get("expenses") or iiko_data.get("expenseDocuments") or []
        for doc in docs:
            doc_date = _fmt_date(doc.get("dateIncoming", ""))
            kassa = (doc.get("paymentType", {}) or {}).get("name", "")
            items = doc.get("items") or []
            if items:
                for item in items:
                    cat = (item.get("category", {}) or {}).get("name", "")
                    comment = item.get("comment", "") or ""
                    amount = float(item.get("sum", 0) or 0)
                    if amount <= 0:
                        continue
                    operations.append({
                        "id": doc.get("id", ""), "name": comment or cat or "Расход",
                        "cat": cat, "date": doc_date, "kassa": kassa,
                        "amount": amount, "type": "iiko"
                    })
            else:
                amount = float(doc.get("sum", 0) or 0)
                if amount > 0:
                    name = doc.get("comment", "") or doc.get("documentNumber", "Расход")
                    operations.append({
                        "id": doc.get("id", ""), "name": name,
                        "cat": "", "date": doc_date, "kassa": kassa,
                        "amount": amount, "type": "iiko"
                    })
    except Exception as e:
        print(f"[expenses/iiko] {e}")

    try:
        for m in get_manual_expenses(real_org, date_from, date_to):
            operations.append({
                "id": str(m["id"]), "name": m["name"], "cat": m["category"],
                "date": m["date"], "kassa": m.get("kassa", ""),
                "amount": float(m["amount"]), "type": "manual"
            })
    except Exception as e:
        print(f"[expenses/manual] {e}")

    cat_totals: Dict[str, float] = {}
    for op in operations:
        cat = op["cat"] or "Прочее"
        cat_totals[cat] = cat_totals.get(cat, 0.0) + op["amount"]

    operations.sort(key=lambda x: x["date"], reverse=True)
    categories = [{"name": c, "amount": round(a)} for c, a in sorted(cat_totals.items(), key=lambda x: x[1], reverse=True)]
    return {"total": round(sum(op["amount"] for op in operations)), "operations": operations, "categories": categories}


@app.get("/api/invoices/{org_id}")
async def invoices(org_id: str, period: str = "week", user: Dict = Depends(get_current_user)):
    if _use_demo_data(user, org_id):
        return {"invoices": DEMO_INVOICES}

    real_org = _get_org_id(org_id, user)
    info = _get_restaurant_info(real_org, user)
    login = info["api_login"]
    key = _cache_key(user.get("id", 0), "invoices", real_org, period)
    cached = _get_cached(key)
    if cached:
        return cached

    # Скользящие окна, чтобы период был вложенным (месяц ⊇ неделя ⊇ день).
    # Календарный «с 1-го числа» в начале месяца оказывался уже недели — путало.
    _today = local_now().date()
    _win = {"day": 0, "week": 6, "month": 29}.get(period)
    if _win is not None:
        date_from = (_today - timedelta(days=_win)).isoformat()
        date_to = _today.isoformat()
    else:
        date_from, date_to = get_period_dates(period)
    client = get_client(login, info.get("iiko_server"), info.get("api_password"))
    d_from, d_to = date_from[:10], date_to[:10]
    result_list = []

    # ── Накладные из iiko (OLAP) — изолированный try, чтобы сбой не убил весь список ──
    try:
        data = await client.get_invoices([_olap_dept(real_org, info)], date_from, date_to)
        STATUS_MAP = {
            "New": "Новая", "Processed": "Проведена", "Deleted": "Удалена",
            "InProgress": "В работе", "Accepted": "Проведена",
        }
        for inv in data.get("invoiceIncomings", []):
            supplier = inv.get("supplier", {}) or {}
            items = inv.get("items", []) or []
            result_list.append({
                "id": (inv.get("documentNumber", "") or (inv.get("id", "") or "")[:8])[:8],
                "status": STATUS_MAP.get(inv.get("status", ""), inv.get("status", "")),
                "supplier": supplier.get("name", "—"),
                "date": _fmt_date(inv.get("dateIncoming", "")),
                "items": len(items),
                "amount": float(inv.get("sum", 0) or 0)
            })
    except Exception as e:
        print(f"[invoices] OLAP failed: {e}")

    # ── Накладные из Telegram бота за период (всегда, даже если OLAP упал) ──
    BOT_STATUS_MAP = {
        "draft": "Черновик", "draft_in_iiko": "Создан в iiko", "posted": "Проведена",
        "needs_mapping": "Нужно сопоставление", "iiko_error": "Ошибка iiko", "pending": "В обработке",
        "ready": "Готова — проверь",
    }
    try:
        bot_invs = get_bot_invoices(limit=1000, organization_id=real_org)
        # Показываем накладную, если в окно периода попадает ДАТА ДОКУМЕНТА ИЛИ
        # ДАТА ДОБАВЛЕНИЯ (created_at). Иначе свежепробитая накладная со старой
        # датой поставщика выпадала из окна День/Неделя, хотя добавлена только что.
        bot_invs = [b for b in bot_invs
                    if (d_from <= (b.get("date") or "")[:10] <= d_to)
                    or (d_from <= (b.get("created_at") or "")[:10] <= d_to)]
        for b in bot_invs:
            raw_status = b.get("status") or "draft"
            if raw_status == "pending":
                continue  # ещё в работе бота
            result_list.append({
                "id": f"BOT-{b['id']}",
                "bot_id": b["id"],
                "status": BOT_STATUS_MAP.get(raw_status, raw_status),
                "raw_status": raw_status,
                "supplier": b.get("supplier") or "—",
                "date": (b.get("date") or "")[:10] or (b.get("created_at") or "")[:10],
                "items": len(json.loads(b.get("items") or "[]")),
                "amount": float(b.get("total_amount") or 0),
                "vat_amount": float(b.get("vat_amount") or 0),
                "iiko_doc_id": b.get("iiko_doc_id") or "",
                "iiko_doc_number": b.get("iiko_doc_number") or "",
                "source": "bot",
                "branch": b.get("branch") or "",
                "by": b.get("telegram_user") or "",
            })
    except Exception as e:
        print(f"[invoices] bot invoices failed: {e}")

    result_list.sort(key=lambda x: x["date"], reverse=True)
    total_amount = round(sum(float(x.get("amount") or 0) for x in result_list))
    result = {"invoices": result_list, "total_amount": total_amount, "count": len(result_list)}
    _set_cached(key, result)
    return result


# ============ PRICE STATS (Mini App «📊 Цены») ============
# Cache of iiko invoice exports per (org_id, days) → (timestamp, raw_payload).
# TTL 1 hour. Avoids hitting iiko on every nomenclature pick.
_price_stats_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_PRICE_STATS_TTL_SECONDS = 3600


@app.get("/api/price-stats/{org_id}")
async def price_stats(org_id: str, product_id: str = Query(..., min_length=1),
                       days: int = Query(60, ge=1, le=365),
                       user: Dict = Depends(get_current_user)):
    """Агрегат цен товара по поставщикам за `days` дней.
    Источник: iiko export invoice_incoming + локальные bot_invoices.
    """
    import time
    if _use_demo_data(user, org_id):
        return _demo_price_stats(product_id, days)

    real_org = _get_org_id(org_id, user)
    info = _get_restaurant_info(real_org, user)
    client = get_client(info["api_login"], info.get("iiko_server"), info.get("api_password"))

    date_to = local_now().strftime("%Y-%m-%d")
    date_from = (local_now() - timedelta(days=days)).strftime("%Y-%m-%d")

    cache_key = f"{real_org}|{days}"
    now_ts = time.time()
    cached = _price_stats_cache.get(cache_key)
    iiko_ok = True
    if cached and (now_ts - cached[0]) < _PRICE_STATS_TTL_SECONDS:
        rows = cached[1]
    else:
        try:
            rows = await client.get_invoice_items_olap([_olap_dept(real_org, info)], date_from, date_to)
            _price_stats_cache[cache_key] = (now_ts, rows)
        except Exception as e:
            logger.warning(f"[price_stats] iiko fetch failed: {e}")
            rows = []
            iiko_ok = False

    product_name = "—"
    product_unit = ""

    by_supplier: Dict[str, Dict[str, Any]] = {}
    purchases_count = 0
    for r in rows:
        if r.get("product_id") != product_id:
            continue
        price = float(r.get("price_per_unit") or 0)
        if price <= 0:
            continue
        supplier_id = r.get("supplier_id") or r.get("supplier_name") or "—"
        supplier_name = r.get("supplier_name") or "—"
        unit = r.get("unit") or ""
        purchases_count += 1
        if not product_name or product_name == "—":
            product_name = r.get("product_name") or product_name
            product_unit = unit
        cur = by_supplier.get(supplier_id)
        if cur is None or price < cur["price_per_unit"]:
            by_supplier[supplier_id] = {
                "supplier_id": supplier_id,
                "supplier_name": supplier_name,
                "price_per_unit": price,
                "date": r.get("date") or "",
                "unit": unit,
            }

    try:
        local = get_price_history(user.get("id") or 0, days=days)
    except Exception:
        local = []
    for h in local:
        if h["key"] != product_id:
            continue
        sid = f"local:{h['supplier']}"
        purchases_count += 1
        cur = by_supplier.get(sid)
        if cur is None or h["price_per_unit"] < cur["price_per_unit"]:
            by_supplier[sid] = {
                "supplier_id": sid,
                "supplier_name": h["supplier"],
                "price_per_unit": h["price_per_unit"],
                "date": h["date"],
                "unit": h["unit"] or product_unit,
            }

    suppliers = sorted(by_supplier.values(), key=lambda r: r["price_per_unit"])
    best = suppliers[0] if suppliers else None
    if best:
        for s in suppliers:
            s["delta_pct"] = ((s["price_per_unit"] - best["price_per_unit"]) /
                              best["price_per_unit"] * 100) if best["price_per_unit"] > 0 else 0.0

    return {
        "product": {"id": product_id, "name": product_name, "unit": product_unit},
        "best": best,
        "suppliers": suppliers,
        "stats": {"purchases": purchases_count, "days": days},
        "iiko_ok": iiko_ok,
    }


@app.get("/api/writeoffs/{org_id}")
async def writeoffs(org_id: str, period: str = "week", user: Dict = Depends(get_current_user)):
    if _use_demo_data(user, org_id):
        return DEMO_WRITEOFFS

    real_org = _get_org_id(org_id, user)
    info = _get_restaurant_info(real_org, user)
    login = info["api_login"]
    key = _cache_key(user.get("id", 0), "writeoffs", real_org, period)
    cached = _get_cached(key)
    if cached:
        return cached

    date_from, date_to = get_period_dates(period)
    client = get_client(login, info.get("iiko_server"), info.get("api_password"))
    try:
        data = await client.get_writeoffs([_olap_dept(real_org, info)], date_from, date_to)
        cat_totals: Dict[str, float] = {}
        total_written = 0.0
        for doc in data.get("writeoffs", []):
            for item in doc.get("items", []):
                product = item.get("product", {}) or {}
                cat = (product.get("category", {}) or {}).get("name", "Прочее") or "Прочее"
                amount = float(item.get("sum", 0) or 0)
                cat_totals[cat] = cat_totals.get(cat, 0.0) + amount
                total_written += amount
        categories = []
        for cat_name, written in sorted(cat_totals.items(), key=lambda x: x[1], reverse=True):
            pct = round(written / total_written * 100, 1) if total_written else 0
            categories.append({
                "name": cat_name, "written": round(written),
                "purchases": 0, "pct": pct, "alert": pct > 20
            })
        result = {"total_written": round(total_written), "purchases": 0, "loss_pct": 0, "categories": categories}
        _set_cached(key, result)
        return result
    except Exception as e:
        print(f"[writeoffs] {e}")
        return _iiko_unavailable()


@app.get("/api/weekly-report/preview")
async def weekly_report_preview(
        send: bool = False,
        token: Optional[str] = Query(None),
        x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    """Предпросмотр воскресного отчёта (и отправка получателю при send=true).
    ВНУТРЕННИЙ отчёт оператора по ВСЕМ филиалам сети — доступ только оператору
    (получателю REPORT_RECIPIENT_USERNAME) или по секретному токену. Обычный клиент
    НЕ должен видеть выручку чужих филиалов."""
    authed = bool(REPORT_PREVIEW_TOKEN and token
                  and hmac.compare_digest(token.encode(), REPORT_PREVIEW_TOKEN.encode()))
    if not authed:
        try:
            u = await get_current_user(x_telegram_init_data)
            recipient = get_user_by_username(REPORT_RECIPIENT_USERNAME)
            authed = bool(recipient and not _is_demo_user(u)
                          and int(u.get("id", 0)) == int(recipient.get("telegram_id") or 0))
        except Exception:
            authed = False
    if not authed:
        raise HTTPException(status_code=404, detail="Not Found")
    text = await _build_weekly_report_text()
    if not text:
        return {"ok": False, "error": "нет филиалов"}
    result = {"ok": True, "text": text, "recipient": REPORT_RECIPIENT_USERNAME}
    if send:
        recipient = get_user_by_username(REPORT_RECIPIENT_USERNAME)
        if not recipient or not recipient.get("telegram_id"):
            result["sent"] = False
            result["error"] = (f"@{REPORT_RECIPIENT_USERNAME} ещё не запускал бота — "
                               f"chat_id неизвестен. Пусть нажмёт Start в боте.")
        else:
            await _tg_send(int(recipient["telegram_id"]), text)
            result["sent"] = True
    return result


@app.get("/api/weekly-user-report/preview")
async def weekly_user_report_preview(
        send: bool = False,
        user: Dict = Depends(get_current_user)):
    """Предпросмотр понедельничного отчёта: юзер видит ТОЛЬКО свой (по своим
    филиалам), чужие данные недоступны. send=true — прислать себе в Telegram."""
    tg_id = int(user.get("id", 0))
    owner_rests = [r for r in get_all_restaurants()
                   if int(r.get("telegram_id") or 0) == tg_id]
    if not owner_rests:
        return {"ok": False, "error": "нет подключённых филиалов"}
    text = await _owner_week_report(owner_rests)
    if not text:
        return {"ok": False, "error": "нет данных за прошлую неделю"}
    result: Dict[str, Any] = {"ok": True, "text": text}
    if send:
        result["sent"] = await _tg_send(tg_id, text) is not None
    return result


@app.get("/api/withdrawals/{org_id}")
async def withdrawals(org_id: str, period: str = "month", user: Dict = Depends(get_current_user)):
    """Изъятия наличных из кассы (выемка/инкассация) за период."""
    if _use_demo_data(user, org_id):
        return DEMO_WITHDRAWALS

    real_org = _get_org_id(org_id, user)
    info = _get_restaurant_info(real_org, user)
    key = _cache_key(user.get("id", 0), "withdrawals", real_org, period)
    cached = _get_cached(key)
    if cached:
        return cached

    date_from, date_to = get_period_dates(period)
    client = get_client(info["api_login"], info.get("iiko_server"), info.get("api_password"))
    try:
        if not hasattr(client, "get_cash_withdrawals"):
            return {"withdrawals": [], "total": 0}
        data = await client.get_cash_withdrawals([_olap_dept(real_org, info)], date_from, date_to)
        ws = data.get("withdrawals", [])
        ws.sort(key=lambda w: w.get("date", ""), reverse=True)
        result = {"withdrawals": ws, "total": round(sum(float(w.get("sum") or 0) for w in ws))}
        _set_cached(key, result)
        return result
    except Exception as e:
        print(f"[withdrawals] {e}")
        return {"withdrawals": [], "total": 0}


@app.get("/api/cancels/{org_id}")
async def cancels(org_id: str, period: str = "today", user: Dict = Depends(get_current_user)):
    if _use_demo_data(user, org_id):
        return DEMO_CANCELS

    real_org = _get_org_id(org_id, user)
    info = _get_restaurant_info(real_org, user)
    date_from, date_to = get_period_dates(period)
    client = get_client(info["api_login"], info.get("iiko_server"), info.get("api_password"))
    try:
        result = await client.olap_report(
            _olap_dept(real_org, info), date_from, date_to,
            group_by=["DishName"],
            aggregates=["DishAmountSum", "OrdersCount"],
            deleted_only=True
        )
        rows = result.get("data", [])
        items = [
            {"name": r.get("DishName", ""), "amount": float(r.get("DishAmountSum", 0) or 0)}
            for r in rows if r.get("DishName") and float(r.get("DishAmountSum", 0) or 0) > 0
        ]
        items.sort(key=lambda x: x["amount"], reverse=True)
        total_amount = sum(i["amount"] for i in items)
        total_orders = int(sum(float(r.get("OrdersCount", 0) or 0) for r in rows))
        return {"items": items[:15], "total_amount": total_amount, "total_orders": total_orders}
    except Exception as e:
        logger.warning(f"[cancels] {e}")
        return {"items": [], "total_amount": 0, "total_orders": 0}


@app.get("/api/companions/{org_id}")
async def companions(org_id: str, period: str = "week", user: Dict = Depends(get_current_user)):
    """Анализ компаньонов — блюда которые покупают вместе."""
    if _use_demo_data(user, org_id):
        return DEMO_COMPANIONS

    real_org = _get_org_id(org_id, user)
    info = _get_restaurant_info(real_org, user)
    date_from, date_to = get_period_dates(period)
    client = get_client(info["api_login"], info.get("iiko_server"), info.get("api_password"))
    try:
        result = await client.olap_report(
            _olap_dept(real_org, info), date_from, date_to,
            group_by=["OpenDate.Typed", "OrderNum", "DishName"],
            aggregates=["DishAmountSum"]
        )
        rows = result.get("data", [])
        # Строим словарь order → [dishes], ключ = дата+номер для уникальности
        order_dishes: Dict[str, List[str]] = {}
        for r in rows:
            date_key = str(r.get("OpenDate.Typed", ""))[:10]
            order = f"{date_key}_{r.get('OrderNum', '')}"
            dish = r.get("DishName", "")
            if not order or not dish:
                continue
            if order not in order_dishes:
                order_dishes[order] = []
            if dish not in order_dishes[order]:
                order_dishes[order].append(dish)

        # Считаем пары
        pair_count: Dict[str, int] = {}
        companion_count: Dict[str, Dict[str, int]] = {}
        for dishes in order_dishes.values():
            if len(dishes) < 2:
                continue
            for i in range(len(dishes)):
                for j in range(i + 1, len(dishes)):
                    a, b = sorted([dishes[i], dishes[j]])
                    key = f"{a}|||{b}"
                    pair_count[key] = pair_count.get(key, 0) + 1
                    # Компаньоны для каждого блюда
                    companion_count.setdefault(dishes[i], {})[dishes[j]] = \
                        companion_count.get(dishes[i], {}).get(dishes[j], 0) + 1
                    companion_count.setdefault(dishes[j], {})[dishes[i]] = \
                        companion_count.get(dishes[j], {}).get(dishes[i], 0) + 1

        # Топ пар
        top_pairs = sorted(pair_count.items(), key=lambda x: x[1], reverse=True)[:10]
        pairs = [{"a": k.split("|||")[0], "b": k.split("|||")[1], "count": v} for k, v in top_pairs]

        # Топ компаньоны для топ-блюд
        top_companions = {}
        for dish, comp in sorted(companion_count.items(), key=lambda x: sum(x[1].values()), reverse=True)[:8]:
            top = sorted(comp.items(), key=lambda x: x[1], reverse=True)[:3]
            top_companions[dish] = [{"name": n, "count": c} for n, c in top]

        return {"pairs": pairs, "companions": top_companions}
    except Exception as e:
        logger.warning(f"[companions] {e}")
        return {"pairs": [], "companions": {}}


@app.get("/api/retention/{org_id}")
async def retention(org_id: str, user: Dict = Depends(get_current_user)):
    if _use_demo_data(user, org_id):
        return DEMO_RETENTION
    return {"clients": 0, "orders": 0, "cohorts": [], "frequency": []}


@app.get("/api/comparison/{org_id}")
async def comparison(org_id: str,
                     org_ids: Optional[str] = Query(None),
                     user: Dict = Depends(get_current_user)):
    """YoY сравнение по неделям и месяцам с разбивкой по каналам."""
    if _use_demo_data(user, org_id):
        return _demo_comparison()

    orgs = _parse_org_ids(org_ids, org_id, user)

    from datetime import date as date_cls

    async def fetch_year_for_org(real_org: str, year: int):
        info = _get_restaurant_info(real_org, user)
        client = get_client(info["api_login"], info.get("iiko_server"), info.get("api_password"))
        start = f"{year}-01-01"
        end = f"{year}-12-31"
        try:
            result = await client.olap_report(
                _olap_dept(real_org, info), start, end,
                group_by=["OpenDate.Typed", "OrderType.Name"],
                aggregates=["DishAmountSum", "OrdersCount"]
            )
            return result.get("data", [])
        except Exception as e:
            logger.warning(f"[comparison] {real_org}/{year}: {e}")
            return []

    cur_year = local_now().year
    cur_batches, prev_batches = await asyncio.gather(
        asyncio.gather(*(fetch_year_for_org(o, cur_year) for o in orgs)),
        asyncio.gather(*(fetch_year_for_org(o, cur_year - 1) for o in orgs)),
    )
    cur_rows  = [r for batch in cur_batches  for r in batch]
    prev_rows = [r for batch in prev_batches for r in batch]

    def aggregate(rows, by: str):
        result = {}
        for row in rows:
            dt_str = (row.get("OpenDate.Typed") or "")[:10]
            if not dt_str or len(dt_str) < 10:
                continue
            try:
                dt = date_cls.fromisoformat(dt_str)
            except Exception:
                continue
            channel = row.get("OrderType.Name", "Прочее") or "Прочее"
            rev = float(row.get("DishAmountSum", 0) or 0)
            orders = int(row.get("OrdersCount", 0) or 0)

            if by == "week":
                iso = dt.isocalendar()
                key = f"{iso.year}-W{iso.week:02d}"
                label = f"Нед {iso.week}"
            else:
                key = f"{dt.year}-{dt.month:02d}"
                label = ['Янв','Фев','Мар','Апр','Май','Июн','Июл','Авг','Сен','Окт','Ноя','Дек'][dt.month-1]

            if key not in result:
                result[key] = {"key": key, "label": label, "year": dt.year,
                               "total_rev": 0, "total_orders": 0, "channels": {}}
            result[key]["total_rev"] += rev
            result[key]["total_orders"] += orders
            if channel not in result[key]["channels"]:
                result[key]["channels"][channel] = {"rev": 0, "orders": 0}
            result[key]["channels"][channel]["rev"] += rev
            result[key]["channels"][channel]["orders"] += orders
        return sorted(result.values(), key=lambda x: x["key"])

    cur_weeks = aggregate(cur_rows, "week")
    prev_weeks = aggregate(prev_rows, "week")
    cur_months = aggregate(cur_rows, "month")
    prev_months = aggregate(prev_rows, "month")

    # Список всех каналов
    all_channels = set()
    for row in cur_rows + prev_rows:
        ch = row.get("OrderType.Name", "")
        if ch:
            all_channels.add(ch)

    return {
        "weeks":    {"current": cur_weeks,  "previous": prev_weeks},
        "months":   {"current": cur_months, "previous": prev_months},
        "channels": sorted(all_channels),
        "cur_year": cur_year,
        "prev_year": cur_year - 1,
    }


# ============ ПЛАН ПРОДАЖ (Год/год → суб-вкладка «План») ============
# Клиент вставляет ссылку на Google-таблицу с планом по филиалу. План разбит на
# 3 корзины × по дням. Метрика — КОЛИЧЕСТВО ЗАКАЗОВ (OrdersCount), не выручка.
# Факт берём из iiko ОДНИМ OLAP-запросом на филиал и бакечим 8 типов в 3 корзины.

PLAN_BUCKETS = ["Доставка", "Зал / вынос", "Агрегаторы"]

# Корзины «Зал / вынос» и «Доставка» — фиксированные наборы типов. Всё остальное
# (Wolt, Яндекс Еда, Glovo, Chocofood и любые новые площадки) — Агрегаторы по умолчанию,
# чтобы ни один тип заказа не терялся при разнице наборов между филиалами.
_PLAN_ZAL = {"Обычный заказ", "Самовывоз", "Сайт самовывоз"}
_PLAN_DELIVERY = {"Доставка", "Сайт доставка"}


def _plan_bucket(order_type: str) -> Optional[str]:
    t = (order_type or "").strip()
    if not t:
        return None
    if t in _PLAN_ZAL:
        return "Зал / вынос"
    if t in _PLAN_DELIVERY:
        return "Доставка"
    return "Агрегаторы"


def _gsheet_csv_url(url: str) -> str:
    """Превращает ссылку на Google-таблицу в export-CSV. Прочие ссылки — как есть."""
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url)
    if m:
        doc_id = m.group(1)
        gid_m = re.search(r"[#&?]gid=(\d+)", url)
        gid = f"&gid={gid_m.group(1)}" if gid_m else ""
        return f"https://docs.google.com/spreadsheets/d/{doc_id}/export?format=csv{gid}"
    return url


def _norm_branch(s: str) -> str:
    """Нормализация названия филиала для устойчивого совпадения:
    без учёта регистра и лишних пробелов."""
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _iso_from_ddmmyyyy(d: str) -> Optional[str]:
    try:
        dd, mm, yy = d.strip().split(".")
        return f"{int(yy):04d}-{int(mm):02d}-{int(dd):02d}"
    except (ValueError, IndexError):
        return None


def _parse_plan_csv(text: str, org_name: str):
    """Парсит CSV плана для одного филиала. Возвращает (plan, date2week, week_meta):
      plan[корзина][неделя] = сумма ПЛАНА (строки «{тип} план» нижних блоков),
      date2week["YYYY-MM-DD"] = номер недели (из строки «Неделя» верхних секций),
      week_meta[неделя] = {"first": "YYYY-MM-DD"}.
    План берём ТОЛЬКО из строк «Доставка план / Зал / вынос план / Агрегаторы план»
    в поблочной части по филиалам (короткое имя филиала входит в organization_name).
    Факт из таблицы НЕ берём — он приходит из iiko. Недели — из строки «Неделя»,
    колонки→даты — из верхней шапки «Торговое предприятие» (DD.MM.YYYY)."""
    target = _norm_branch(org_name)
    rows = list(csv.reader(text.splitlines()))
    plan: Dict[str, Dict[int, float]] = {b: {} for b in PLAN_BUCKETS}
    col2iso: Dict[int, str] = {}
    date2week: Dict[str, int] = {}
    week_meta: Dict[int, Dict[str, str]] = {}

    NON_BRANCH = set(PLAN_BUCKETS) | {
        "Торговое предприятие", "Неделя", "Месяц", "День недели", "Итого",
    }

    def _is_branch_header(label: str) -> bool:
        if not label or label in NON_BRANCH:
            return False
        if label.endswith(" план") or label.endswith(" факт"):
            return False
        if "Выполнение плана" in label:
            return False
        return True

    weeks_row: Optional[List[str]] = None
    in_target = False
    for r in rows:
        label = (r[0] if r else "").strip()

        if label == "Неделя":
            weeks_row = r
            continue
        if label == "Торговое предприятие":
            for col in range(1, len(r)):
                iso = _iso_from_ddmmyyyy(r[col])
                if iso and col not in col2iso:
                    col2iso[col] = iso
            if weeks_row:
                for col in range(1, min(len(r), len(weeks_row))):
                    iso = _iso_from_ddmmyyyy(r[col])
                    wk = weeks_row[col].strip()
                    if not iso or not wk:
                        continue
                    try:
                        week = int(wk)
                    except ValueError:
                        continue
                    date2week[iso] = week
                    cur = week_meta.get(week)
                    if cur is None or iso < cur["first"]:
                        week_meta[week] = {"first": iso}
            weeks_row = None
            continue

        if _is_branch_header(label):
            nl = _norm_branch(label)
            in_target = bool(nl) and nl != "астана" and nl in target
            continue

        if in_target and label.endswith(" план"):
            bucket = label[:-len(" план")].strip()
            if bucket in PLAN_BUCKETS:
                for col in range(1, len(r)):
                    iso = col2iso.get(col)
                    week = date2week.get(iso) if iso else None
                    if week is None:
                        continue
                    v = r[col].replace("\xa0", "").strip().replace(" ", "").replace(",", ".")
                    if not v:
                        continue
                    try:
                        val = float(v)
                    except ValueError:
                        continue
                    plan[bucket][week] = plan[bucket].get(week, 0.0) + val

    return plan, date2week, week_meta


@app.post("/api/plan/{org_id}")
async def plan_set_link(org_id: str, payload: Dict[str, Any],
                        user: Dict = Depends(get_current_user)):
    """Сохранить ссылку на таблицу с планом для филиала."""
    if _use_demo_data(user, org_id):
        return {"ok": False, "error": "demo"}
    real_org = _get_org_id(org_id, user)
    url = (payload.get("url") or "").strip()
    if url and not url.lower().startswith("http"):
        raise HTTPException(status_code=400, detail="Ссылка должна начинаться с http")
    set_plan_url(real_org, url)
    return {"ok": True, "url": url}


@app.get("/api/plan/{org_id}")
async def plan_comparison(org_id: str, year: Optional[int] = Query(None),
                          user: Dict = Depends(get_current_user)):
    """План/факт/% по 3 корзинам, понедельно. Факт — 1 OLAP-запрос на филиал.
    Недели берём из таблицы клиента, чтобы план и факт делились одинаково."""
    if _use_demo_data(user, org_id):
        return _demo_plan()
    real_org = _get_org_id(org_id, user)
    info = _get_restaurant_info(real_org, user)
    org_name = info.get("org_name", "")
    url = get_plan_url(real_org)
    if not url:
        return {"has_link": False, "buckets": PLAN_BUCKETS, "rows": [], "url": ""}

    yr = year or local_now().year

    # 1) План из таблицы (понедельно) + карта дата→неделя
    plan: Dict[str, Dict[int, float]] = {b: {} for b in PLAN_BUCKETS}
    date2week: Dict[str, int] = {}
    week_meta: Dict[int, Dict[str, str]] = {}
    plan_error = None
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as cli:
            resp = await cli.get(_gsheet_csv_url(url))
        if resp.status_code == 200 and "text/csv" in resp.headers.get("content-type", ""):
            plan, date2week, week_meta = _parse_plan_csv(resp.text, org_name)
            if not any(plan[b] for b in PLAN_BUCKETS):
                plan_error = (f"Филиал «{org_name}» не найден в таблице (или у него пусто). "
                              f"Проверь, что название строки в таблице совпадает с названием филиала в приложении.")
        else:
            plan_error = "Не удалось прочитать таблицу — проверь доступ «по ссылке» и что это Google-таблица"
    except Exception as e:
        logger.warning(f"[plan] fetch failed org={real_org}: {e}")
        plan_error = "Таблица недоступна по ссылке"

    # 2) Факт из iiko — один OLAP по филиалу, бакечим по неделям той же карты
    fact: Dict[str, Dict[int, float]] = {b: {} for b in PLAN_BUCKETS}
    try:
        client = get_client(info["api_login"], info.get("iiko_server"), info.get("api_password"))
        res = await client.olap_report(
            _olap_dept(real_org, info), f"{yr}-01-01", f"{yr}-12-31",
            group_by=["OrderType.Name", "OpenDate.Typed"], aggregates=["OrdersCount"]
        )
        for d in res.get("data", []):
            bucket = _plan_bucket(d.get("OrderType.Name"))
            iso = (d.get("OpenDate.Typed") or "")[:10]
            week = date2week.get(iso)
            if not bucket or week is None:
                continue
            fact[bucket][week] = fact[bucket].get(week, 0.0) + float(d.get("OrdersCount", 0) or 0)
    except Exception as e:
        logger.warning(f"[plan] olap failed org={real_org}: {e}")

    # 3) Сборка недельной таблицы (только недели, где ЕСТЬ план — это план-период)
    rows_out = []
    for wk in sorted(week_meta.keys()):
        first = week_meta[wk].get("first", "")
        label = f"Нед {wk}"
        if len(first) >= 10:
            label = f"Нед {wk} · {first[8:10]}.{first[5:7]}"
        bk: Dict[str, Any] = {}
        has_plan = False
        for b in PLAN_BUCKETS:
            p = round(plan[b].get(wk, 0))
            f = round(fact[b].get(wk, 0))
            pct = round(f / p * 100) if p else None
            bk[b] = {"plan": p, "fact": f, "pct": pct}
            if p:
                has_plan = True
        if has_plan:
            rows_out.append({"week": wk, "label": label, "buckets": bk})

    return {
        "has_link": True,
        "url": url,
        "year": yr,
        "buckets": PLAN_BUCKETS,
        "rows": rows_out,
        "error": plan_error,
    }


# ============ КЛИЕНТЫ / ЛОЯЛЬНОСТЬ (Продажи → суб-вкладка «Клиенты») ============
# Два источника (тумблер на фронте):
#   source=iiko  — напрямую из iiko (DELIVERIES по Delivery.CustomerPhone)
#   source=table — из вставленной Google-таблицы заказов (колонки
#                  «Торговое предприятие» + «Телефон клиента», опц. «Итого»)
# Оба пути сводятся к agg {телефон: {orders, spend}} и общей сборке сегментов.

def _mask_phone(p: str) -> str:
    d = re.sub(r"\D", "", p or "")
    if len(d) < 7:
        return p or "—"
    return f"+{d[:2]}…{d[-4:]}"


def _norm_phone(s: str) -> Optional[str]:
    s = (s or "").split(",")[0]
    d = re.sub(r"\D", "", s)
    return d[-10:] if len(d) >= 10 else None


def _clients_segments(agg: Dict[str, Dict[str, float]]):
    """agg {phone: {orders, spend}} → (segments, top_vip)."""
    total = len(agg)
    if not total:
        return None, []
    unique = sum(1 for a in agg.values() if a["orders"] == 1)
    loyal = sum(1 for a in agg.values() if a["orders"] >= 2)
    vip = sum(1 for a in agg.values() if a["orders"] >= 10)
    spend = sum(a["spend"] for a in agg.values())
    # Сколько принесли лояльные и сколько ещё могут — если довести их до VIP (10 заказов)
    loyal_revenue = sum(a["spend"] for a in agg.values() if a["orders"] >= 2)
    loyal_potential = sum(
        max(0, 10 - a["orders"]) * (a["spend"] / a["orders"])
        for a in agg.values() if 2 <= a["orders"] < 10 and a["orders"]
    )
    pct = lambda x: round(x / total * 100) if total else 0
    top = sorted(agg.items(), key=lambda kv: kv[1]["orders"], reverse=True)[:8]
    top_vip = [{"phone": _mask_phone(ph), "orders": int(a["orders"]), "spend": round(a["spend"])}
               for ph, a in top]
    segments = {
        "total": total,
        "unique": {"count": unique, "pct": pct(unique)},
        "loyal": {"count": loyal, "pct": pct(loyal)},
        "vip": {"count": vip, "pct": pct(vip)},
        "repeat_pct": pct(loyal),
        "revenue": round(spend),
        "ltv": round(spend / total) if total else 0,
        "loyal_revenue": round(loyal_revenue),
        "loyal_potential": round(loyal_potential),
    }
    return segments, top_vip


def _compute_rfm(agg: Dict[str, Dict[str, float]], today):
    """agg {phone:{orders,spend,last_date}} → RFM-сегменты.
    Скоринг R (давность) и F (частота) квинтилями (5 = лучший), сегмент по (R,F)."""
    custs = [(ph, a) for ph, a in agg.items() if a.get("orders", 0) > 0]
    if not custs:
        return None

    def rec_days(a):
        ld = a.get("last_date") or ""
        if not ld:
            return 9999
        try:
            return (today - date.fromisoformat(ld[:10])).days
        except Exception:
            return 9999

    def quintile(pairs, higher_better):
        order = sorted(pairs, key=lambda kv: kv[1], reverse=higher_better)
        L = len(order)
        return {k: 5 - (i * 5 // L) for i, (k, _) in enumerate(order)}

    rs = quintile([(ph, rec_days(a)) for ph, a in custs], higher_better=False)
    fs = quintile([(ph, a["orders"]) for ph, a in custs], higher_better=True)

    def seg_of(r, f):
        if r >= 4 and f >= 4: return "champions"
        if f >= 4:            return "loyal"
        if r >= 4:            return "promising"
        if r <= 2 and f >= 3: return "at_risk"
        if r <= 1 and f <= 1: return "lost"
        if r <= 2:            return "hibernating"
        return "others"

    META = [
        ("champions", "🏆", "Чемпионы"),
        ("loyal", "💚", "Лояльные"),
        ("promising", "🌱", "Перспективные"),
        ("at_risk", "⚠️", "В зоне риска"),
        ("hibernating", "😴", "Спящие"),
        ("lost", "❌", "Потерянные"),
        ("others", "•", "Прочие"),
    ]
    buckets = {k: {"count": 0, "revenue": 0.0} for k, _, _ in META}
    for ph, a in custs:
        b = buckets[seg_of(rs[ph], fs[ph])]
        b["count"] += 1
        b["revenue"] += a.get("spend", 0.0)
    total = len(custs)
    segs = [{"key": k, "emoji": e, "label": lbl,
             "count": buckets[k]["count"],
             "pct": round(buckets[k]["count"] / total * 100),
             "revenue": round(buckets[k]["revenue"])}
            for k, e, lbl in META if buckets[k]["count"] > 0]
    return {"total": total, "segments": segs}


def _parse_clients_csv(text: str, org_name: str) -> Dict[str, Dict[str, float]]:
    """Из таблицы заказов: заказы и выручка на телефон для филиала → agg."""
    target = _norm_branch(org_name)
    rows = list(csv.reader(text.splitlines()))
    if not rows:
        return {}
    header = rows[0]

    def col_idx(*names):
        for i, h in enumerate(header):
            hn = h.strip().lower()
            if any(nm in hn for nm in names):
                return i
        return -1

    i_branch = col_idx("торговое предприятие", "предприятие", "филиал")
    i_phone = col_idx("телефон")
    i_sum = col_idx("итого", "сумма")
    if i_branch < 0 or i_phone < 0:
        return {}
    agg: Dict[str, Dict[str, float]] = {}
    for r in rows[1:]:
        if len(r) <= max(i_branch, i_phone):
            continue
        if _norm_branch(r[i_branch]) != target:
            continue
        p = _norm_phone(r[i_phone])
        if not p:
            continue
        a = agg.setdefault(p, {"orders": 0, "spend": 0.0})
        a["orders"] += 1
        if 0 <= i_sum < len(r):
            v = r[i_sum].replace(" ", "").replace("\xa0", "").replace(",", ".")
            try:
                a["spend"] += float(v)
            except ValueError:
                pass
    return agg


@app.post("/api/clients/{org_id}")
async def clients_set_link(org_id: str, payload: Dict[str, Any],
                           user: Dict = Depends(get_current_user)):
    """Сохранить ссылку на таблицу клиентов (для source=table)."""
    if _use_demo_data(user, org_id):
        return {"ok": False, "error": "demo"}
    real_org = _get_org_id(org_id, user)
    url = (payload.get("url") or "").strip()
    if url and not url.lower().startswith("http"):
        raise HTTPException(status_code=400, detail="Ссылка должна начинаться с http")
    set_client_url(real_org, url)
    return {"ok": True, "url": url}


@app.get("/api/clients/{org_id}")
async def clients_loyalty(org_id: str, source: str = "iiko", period: str = "year",
                          from_: Optional[str] = Query(None, alias="from"),
                          to: Optional[str] = Query(None),
                          org_ids: Optional[str] = Query(None),
                          user: Dict = Depends(get_current_user)):
    """Лояльность клиентов: source=iiko (из доставок iiko) или source=table (из ссылки)."""
    if _use_demo_data(user, org_id):
        agg = _demo_clients_agg()
        segments, top_vip = _clients_segments(agg)
        return {"segments": segments, "top_vip": top_vip,
                "rfm": _compute_rfm(agg, local_now().date()),
                "source": source, "year": str(local_now().year)}
    real_org = _get_org_id(org_id, user)
    info = _get_restaurant_info(real_org, user)
    org_name = info.get("org_name", "")

    # ── Источник: таблица по ссылке ──
    if source == "table":
        url = get_client_url(real_org)
        if not url:
            return {"segments": None, "source": "table", "has_link": False, "url": ""}
        agg: Dict[str, Dict[str, float]] = {}
        error = None
        try:
            async with httpx.AsyncClient(timeout=40.0, follow_redirects=True) as cli:
                resp = await cli.get(_gsheet_csv_url(url))
            if resp.status_code == 200 and "text/csv" in resp.headers.get("content-type", ""):
                agg = _parse_clients_csv(resp.text, org_name)
                if not agg:
                    error = (f"Для филиала «{org_name}» нет данных. Проверь колонки "
                             f"«Торговое предприятие» / «Телефон клиента» и совпадение филиала.")
            else:
                error = "Не удалось прочитать таблицу — открой доступ «по ссылке»"
        except Exception as e:
            logger.warning(f"[clients/table] {real_org}: {e}")
            error = "Таблица недоступна по ссылке"
        segments, top_vip = _clients_segments(agg)
        return {"segments": segments, "top_vip": top_vip, "source": "table",
                "has_link": True, "url": url, "error": error}

    # ── Источник: iiko (по умолчанию) ──
    orgs = _parse_org_ids(org_ids, org_id, user)
    if from_ and to:
        date_from, date_to = get_period_dates("custom", from_, to)
    else:
        date_from, date_to = f"{local_now().year}-01-01", local_now().date().isoformat()

    async def fetch_one(real: str):
        i = _get_restaurant_info(real, user)
        client = get_client(i["api_login"], i.get("iiko_server"), i.get("api_password"))
        fn = getattr(client, "olap_clients_rfm", None) or getattr(client, "olap_clients", None)
        if not fn:
            return []
        try:
            return await fn(_olap_dept(real, i), date_from, date_to)
        except Exception as e:
            logger.warning(f"[clients/iiko] {real}: {e}")
            return []

    per_org = await asyncio.gather(*(fetch_one(o) for o in orgs))
    agg = {}
    for batch in per_org:
        for d in batch:
            ph = (d.get("Delivery.CustomerPhone") or "").strip()
            n = int(d.get("UniqOrderId.OrdersCount") or 0)
            if not ph or n <= 0:
                continue
            a = agg.setdefault(ph, {"orders": 0, "spend": 0.0, "last_date": ""})
            a["orders"] += n
            a["spend"] += float(d.get("DishDiscountSumInt") or 0)
            od = (d.get("OpenDate.Typed") or "")[:10]
            if od and od > a["last_date"]:
                a["last_date"] = od
    segments, top_vip = _clients_segments(agg)
    rfm = _compute_rfm(agg, local_now().date())
    return {"segments": segments, "top_vip": top_vip, "rfm": rfm,
            "source": "iiko", "year": date_from[:4]}


@app.get("/api/olap/{org_id}")
async def olap_raw(org_id: str, period: str = "today",
                   from_: Optional[str] = Query(None, alias="from"),
                   to: Optional[str] = Query(None),
                   org_ids: Optional[str] = Query(None),
                   user: Dict = Depends(get_current_user)):
    """Один большой OLAP-запрос — фронтенд сам фильтрует."""
    if _use_demo_data(user, org_id):
        return {"data": [], "demo": True}

    orgs = _parse_org_ids(org_ids, org_id, user)
    date_from, date_to = get_period_dates(period, from_, to)
    branches_key = ",".join(sorted(orgs))
    cache_period_key = _cache_period_part(period, from_, to) + ":" + branches_key
    key = _cache_key(user.get("id", 0), "olap_raw", branches_key, cache_period_key)
    cached = _get_cached(key)
    if cached:
        return cached

    async def fetch_one(real_org: str):
        info = _get_restaurant_info(real_org, user)
        client = get_client(info["api_login"], info.get("iiko_server"), info.get("api_password"))
        try:
            r = await client.olap_report(
                _olap_dept(real_org, info), date_from, date_to,
                group_by=["OpenDate.Typed", "DishName", "ProductCategory.Name",
                          "WaiterName", "OrderType.Name"],
                aggregates=["DishAmountSum", "GuestNum", "OrdersCount", "DishCount"]
            )
            return r.get("data", [])
        except Exception as e:
            logger.warning(f"[olap_raw] {real_org}: {e}")
            return []

    results = await asyncio.gather(*(fetch_one(o) for o in orgs))
    merged = [row for batch in results for row in batch]
    resp = {"data": merged, "demo": False, "date_from": date_from, "date_to": date_to,
            "branches_count": len(orgs)}
    _set_cached(key, resp)
    return resp


@app.get("/api/stoplist/{org_id}")
async def get_stoplist(org_id: str, user: Dict = Depends(get_current_user)):
    if _use_demo_data(user, org_id):
        demo = dict(DEMO_STOPLIST)
        demo["updated_at"] = local_now().strftime("%H:%M")
        return demo

    real_org = _get_org_id(org_id, user)
    info = _get_restaurant_info(real_org, user)
    login = info["api_login"]
    try:
        client = get_client(login, info.get("iiko_server"), info.get("api_password"))
        stop_data, nomen_data = await asyncio.gather(
            client.get_stop_lists([_olap_dept(real_org, info)]),
            client.get_nomenclature(real_org),
            return_exceptions=True
        )
        if isinstance(stop_data, Exception) or isinstance(nomen_data, Exception):
            return _iiko_unavailable()

        id_to_product = {p["id"]: p for p in nomen_data.get("products", [])}
        stopped = []
        for loc in stop_data.get("productionLocations", []):
            for item in loc.get("stopList", {}).get("items", []):
                pid = item.get("productId", "")
                product = id_to_product.get(pid, {})
                balance = item.get("balance", 0)
                stopped.append({
                    "name": product.get("name", pid),
                    "sku": product.get("code", ""),
                    "balance": balance,
                    "category": product.get("productCategoryId", ""),
                    "status": "stop" if balance == 0 else "low"
                })

        return {"items": stopped, "total": len(stopped), "updated_at": local_now().strftime("%H:%M")}
    except Exception:
        return _iiko_unavailable()


@app.post("/api/expenses/manual")
async def create_manual_expense(org_id: str, payload: ExpenseCreate, user: Dict = Depends(get_current_user)):
    _get_org_id(org_id, user)  # проверка доступа
    add_manual_expense(org_id, payload.name, payload.category, payload.amount, payload.kassa, payload.date)
    return {"success": True}


@app.get("/api/expenses/manual")
async def list_manual_expenses(org_id: str, date_from: str, date_to: str, user: Dict = Depends(get_current_user)):
    _get_org_id(org_id, user)  # проверка доступа
    items = get_manual_expenses(org_id, date_from, date_to)
    return {"items": items}


@app.delete("/api/expenses/manual/{exp_id}")
async def remove_manual_expense(exp_id: int, org_id: str, user: Dict = Depends(get_current_user)):
    _get_org_id(org_id, user)  # проверка доступа
    delete_manual_expense(exp_id, org_id)
    return {"success": True}


@app.get("/api/export/{report_type}")
async def export_report(report_type: str, org_id: str = "demo-org-1", period: str = "today",
                        user: Dict = Depends(get_current_user)):
    """Генерирует CSV файл для скачивания"""
    from fastapi.responses import StreamingResponse
    import io

    rows = []
    filename = f"{report_type}-{local_now().strftime('%Y-%m-%d')}.csv"

    if report_type == "channels":
        rows = [["Канал", "Выручка", "Гостей", "Ср. чек"]]
        data = DEMO_SALES.get("channels", [])
        for c in data:
            rows.append([c.get("name",""), c.get("revenue",0), c.get("guests",0), round(c.get("avg_check",0))])
    elif report_type == "staff":
        rows = [["Имя", "Выручка", "Заказы", "Гости"]]
        data = DEMO_STAFF.get("staff", [])
        for s in data:
            rows.append([s.get("name",""), s.get("revenue",0), s.get("orders",0), s.get("guests",0)])
    elif report_type == "abc":
        rows = [["Блюдо", "Категория", "Выручка", "%", "Накоп.%", "ABC"]]
        # Берём демо данные ABC
        dishes = sorted(DEMO_SALES.get("top_dishes", []), key=lambda x: x.get("revenue", 0), reverse=True)
        total = sum(d.get("revenue", 0) for d in dishes)
        cum = 0
        for d in dishes:
            pct = round(d.get("revenue", 0) / total * 100, 1) if total else 0
            cum += pct
            abc = "A" if cum <= 80 else ("B" if cum <= 95 else "C")
            rows.append([d.get("name",""), "", d.get("revenue",0), pct, round(cum,1), abc])
    elif report_type == "engineering":
        rows = [["Блюдо", "Категория", "Выручка", "Маржа%", "Кол-во"]]
        for d in DEMO_SALES.get("top_dishes", []):
            rows.append([d.get("name",""), "", d.get("revenue",0), "—", d.get("count",0)])
    elif report_type == "expenses":
        rows = [["Название", "Категория", "Сумма", "Дата"]]
        for e in DEMO_EXPENSES.get("operations", []):
            rows.append([e.get("name",""), e.get("category",""), e.get("amount",0), e.get("date","")])
    elif report_type == "writeoffs":
        rows = [["Позиция", "Категория", "Сумма", "Дата"]]
        for w in DEMO_WRITEOFFS.get("categories", []):
            rows.append([w.get("name",""), w.get("category",""), w.get("amount",0), ""])
    else:
        rows = [["Нет данных"]]

    csv_content = '\uFEFF' + '\n'.join(';'.join(str(c) for c in row) for row in rows)
    output = io.BytesIO(csv_content.encode('utf-8-sig'))

    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.post("/api/export-send")
@limiter.limit("10/minute")
async def export_send(request: Request, payload: ExportSendPayload,
                      user: Dict = Depends(get_current_user)):
    """Отправляет CSV/текстовый файл в чат с пользователем через бота."""
    chat_id = user.get("id")
    if not chat_id:
        raise HTTPException(status_code=400, detail="no chat_id")
    if not BOT_TOKEN:
        raise HTTPException(status_code=503, detail="bot not configured")

    safe_name = "".join(c for c in payload.filename if c.isalnum() or c in "._- ") or "export.csv"
    if not safe_name.lower().endswith((".csv", ".txt")):
        safe_name += ".csv"

    content_bytes = payload.content.encode("utf-8")
    if not content_bytes.startswith(b"\xef\xbb\xbf"):
        content_bytes = b"\xef\xbb\xbf" + content_bytes

    files = {"document": (safe_name, content_bytes, "text/csv")}
    data = {"chat_id": str(chat_id)}
    if payload.caption:
        data["caption"] = payload.caption[:1000]

    try:
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument",
                             data=data, files=files)
        result = r.json()
        if not result.get("ok"):
            desc = result.get("description", "send failed")
            if "chat not found" in desc.lower() or "bot was blocked" in desc.lower():
                raise HTTPException(status_code=409,
                    detail="Откройте чат с ботом и нажмите Start, потом повторите.")
            raise HTTPException(status_code=502, detail=f"Telegram: {desc}")
        return {"ok": True}
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Telegram request failed: {e}")


# ============ INVOICE EDITOR (Mini App) + POST/UNPOST ============

class InvoiceEditPayload(BaseModel):
    items: List[Dict[str, Any]]
    supplier: Optional[str] = None
    date: Optional[str] = None
    total_amount: Optional[float] = None
    vat_amount: Optional[float] = None


@app.get("/api/invoice-edit/{pending_id}")
async def invoice_edit_get(pending_id: str, user: Dict = Depends(get_current_user)):
    """Возвращает данные pending-накладной для редактирования в Mini App.
    Доступ только владельцу (по telegram_id из initData)."""
    pending = _get_pending_by_id(pending_id)
    if not pending:
        raise HTTPException(status_code=404, detail="Pending invoice not found or expired")
    if pending.get("telegram_id") != user.get("id"):
        raise HTTPException(status_code=403, detail="Not your invoice")
    result = pending.get("result") or {}
    rests = _get_user_orgs(pending["telegram_id"])
    org_id_for_search = rests[0]["organization_id"] if rests else None
    return {
        "pending_id": pending_id,
        "supplier": result.get("supplier"),
        "date": result.get("date"),
        "items": result.get("items", []),
        "total_amount": result.get("total_amount", 0),
        "vat_amount": result.get("vat_amount", 0),
        "vat_rate": result.get("vat_rate", 12.0),
        "currency": result.get("currency", "KZT"),
        "search_org_id": org_id_for_search,
    }


@app.post("/api/invoice-edit/{pending_id}")
async def invoice_edit_post(pending_id: str, payload: InvoiceEditPayload,
                             user: Dict = Depends(get_current_user)):
    """Принимает обновлённую накладную из Mini App (fallback к WebApp.sendData).
    После обновления pending — бот сам перерисует превью при следующем callback'е,
    но для лучшего UX делаем edit прямо здесь."""
    pending = _get_pending_by_id(pending_id)
    if not pending:
        raise HTTPException(status_code=404, detail="Pending invoice not found")
    if pending.get("telegram_id") != user.get("id"):
        raise HTTPException(status_code=403, detail="Not your invoice")
    r = pending.setdefault("result", {})
    r["items"] = payload.items
    if payload.supplier is not None: r["supplier"] = payload.supplier
    if payload.date is not None: r["date"] = payload.date
    if payload.total_amount is not None: r["total_amount"] = payload.total_amount
    if payload.vat_amount is not None: r["vat_amount"] = payload.vat_amount
    pending["state"] = "preview"
    _persist_pending(pending)
    # Перерисуем превью в боте
    if pending.get("message_id"):
        await _tg_edit(pending["chat_id"], pending["message_id"],
                       _format_invoice_v2(pending, "preview"),
                       _invoice_kb(pending["pending_id"]))
    return {"ok": True}


@app.get("/api/nomenclature/search/{org_id}")
async def nomenclature_search(org_id: str, q: str = Query("", max_length=100),
                               limit: int = Query(30, le=50),
                               user: Dict = Depends(get_current_user)):
    """Поиск товаров по имени в номенклатуре iiko (для редактора накладных)."""
    if _use_demo_data(user, org_id):
        ql = (q or "").strip().lower()
        items = [p for p in DEMO_NOMENCLATURE if not ql or ql in p["name"].lower()]
        return {"products": [
            {"id": p["id"], "name": p["name"], "num": p["code"], "code": p["code"],
             "unit": p["unit"], "containers": [], "_container_keys": []}
            for p in items[:limit]
        ]}
    real_org = _get_org_id(org_id, user)
    info = _get_restaurant_info(real_org, user)
    client = get_client(info["api_login"], info.get("iiko_server"), info.get("api_password"))
    try:
        products = await client.search_nomenclature(real_org, q, limit=limit)
    except Exception as e:
        logger.warning(f"[nomenclature_search] {e}")
        products = []
    # mainUnit в iikoServer — UUID единицы; переводим в название (кг/шт/л),
    # иначе в редакторе в поле единицы вставлялась «каша символов».
    units_map: Dict[str, str] = {}
    if hasattr(client, "get_measure_units_map"):
        try:
            units_map = await client.get_measure_units_map()
        except Exception:
            pass

    def _unit_name(u):
        if isinstance(u, dict):
            u = u.get("name") or u.get("id") or ""
        u = str(u or "")
        return units_map.get(u, "" if re.fullmatch(r"[0-9a-f-]{36}", u) else u)

    return {"products": [
        {"id": p.get("id"), "name": p.get("name"), "num": p.get("num"),
         "code": p.get("code"), "unit": _unit_name(p.get("unit") or p.get("mainUnit")),
         "containers": p.get("containers") or [],
         "_container_keys": p.get("_container_keys") or []}
        for p in products
    ]}


@app.get("/api/stores/{org_id}")
async def stores_list(org_id: str, user: Dict = Depends(get_current_user)):
    """Склады филиала из iiko (для выбора склада накладной в редакторе)."""
    if _use_demo_data(user, org_id):
        return {"stores": [{"id": "d1", "name": "Основной склад"}]}
    real_org = _get_org_id(org_id, user)
    info = _get_restaurant_info(real_org, user)
    client = get_client(info["api_login"], info.get("iiko_server"), info.get("api_password"))
    if not hasattr(client, "get_stores"):
        return {"stores": []}
    try:
        stores = await client.get_stores(info.get("org_name", ""))
    except Exception as e:
        logger.warning(f"[stores_list] {e}")
        stores = []
    return {"stores": [{"id": s["id"], "name": s["name"]} for s in stores]}


@app.get("/api/suppliers/{org_id}")
async def suppliers_list(org_id: str, user: Dict = Depends(get_current_user)):
    """Список поставщиков из iiko (для редактора)."""
    if _use_demo_data(user, org_id):
        return {"suppliers": [{"id": "demo-sup-1", "name": "ТОО «Демо-Поставщик»"}]}
    real_org = _get_org_id(org_id, user)
    info = _get_restaurant_info(real_org, user)
    client = get_client(info["api_login"], info.get("iiko_server"), info.get("api_password"))
    try:
        suppliers = await client.get_suppliers(real_org)
    except Exception as e:
        logger.warning(f"[suppliers_list] {e}")
        suppliers = []
    return {"suppliers": suppliers}


@app.get("/api/invoice-saved/{invoice_id}")
async def invoice_saved_get(invoice_id: int, user: Dict = Depends(get_current_user)):
    """Загрузить сохранённую накладную (из БД) для редактирования в Mini App.
    Используется для накладных со статусом needs_mapping / iiko_error / draft."""
    inv = get_bot_invoice(invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if inv.get("telegram_id") != user.get("id"):
        raise HTTPException(status_code=403, detail="Not your invoice")
    try:
        items = json.loads(inv.get("items") or "[]")
    except Exception:
        items = []
    return {
        "invoice_id": invoice_id,
        "supplier": inv.get("supplier"),
        "supplier_id": inv.get("supplier_id"),
        "organization_id": inv.get("organization_id"),
        "date": inv.get("date"),
        "items": items,
        "total_amount": inv.get("total_amount", 0),
        "vat_amount": inv.get("vat_amount", 0),
        "vat_rate": 12.0,
        "currency": "KZT",
        "comment": inv.get("comment") or "",
        "search_org_id": inv.get("organization_id"),
        "status": inv.get("status"),
    }


class SavedInvoicePayload(BaseModel):
    items: List[Dict[str, Any]]
    supplier: Optional[str] = None
    supplier_id: Optional[str] = None
    organization_id: Optional[str] = None
    date: Optional[str] = None
    total_amount: Optional[float] = None
    vat_amount: Optional[float] = None
    store_id: Optional[str] = None  # склад накладной (выбор в редакторе)
    comment: Optional[str] = None   # комментарий оператора → поле «Комментарий» в iikoOffice


@app.post("/api/invoice-saved/{invoice_id}/send")
async def invoice_saved_send(invoice_id: int, payload: SavedInvoicePayload,
                              user: Dict = Depends(get_current_user)):
    """После ручного маппинга в Mini App — обновить позиции и попробовать отправить в iiko."""
    inv = get_bot_invoice(invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if inv.get("telegram_id") != user.get("id"):
        raise HTTPException(status_code=403, detail="Not your invoice")

    # Сохраняем обновлённые поля (оператор мог сменить филиал/поставщика/дату)
    update_fields = {"items": json.dumps(payload.items, ensure_ascii=False)}
    if payload.supplier is not None: update_fields["supplier"] = payload.supplier
    if payload.supplier_id is not None: update_fields["supplier_id"] = payload.supplier_id
    if payload.organization_id is not None: update_fields["organization_id"] = payload.organization_id
    if payload.date is not None: update_fields["date"] = payload.date
    if payload.total_amount is not None: update_fields["total_amount"] = payload.total_amount
    if payload.vat_amount is not None: update_fields["vat_amount"] = payload.vat_amount
    if payload.comment is not None: update_fields["comment"] = payload.comment
    update_bot_invoice(invoice_id, **update_fields)

    # Готовим и шлём в iiko — приоритет у значений из payload (смена филиала/поставщика)
    org_id = payload.organization_id or inv.get("organization_id")
    supplier_id = payload.supplier_id or inv.get("supplier_id")
    if not org_id or not supplier_id:
        raise HTTPException(status_code=400, detail="Не указан филиал или поставщик")
    real_org = _get_org_id(org_id, user)
    info = _get_restaurant_info(real_org, user)
    client = get_client(info["api_login"], info.get("iiko_server"), info.get("api_password"))

    # Собираем sendable items — только те у кого есть iiko_product_id.
    # Единицы: количество уходит в iiko в ЕДИНИЦЕ ТОВАРА из самой iiko (iiko_unit).
    # Известные пары (г↔кг, мл↔л) конвертируем с пересчётом цены; неизвестное
    # расхождение единиц не молчим — предупреждаем оператора (unit_warnings).
    sendable, unmatched, unit_warnings = [], [], []
    for it in payload.items:
        prod_id = it.get("iiko_product_id")
        if not prod_id:
            unmatched.append(it.get("name", "—"))
            continue
        amount, price, total, warning = _reconcile_amount(it)
        if warning:
            unit_warnings.append(warning)
        sendable.append({"productId": prod_id, "amount": amount, "price": price, "sum": total})

    if unmatched:
        update_bot_invoice(invoice_id, status="needs_mapping")
        _invalidate_cache("invoices", real_org)
        return {"ok": False, "unmatched": unmatched,
                "error": f"Не сопоставлено {len(unmatched)} позиций — выбери товар iiko для каждой"}

    date_str = payload.date or inv.get("date") or local_now().strftime("%Y-%m-%d")
    try:
        resp = await client.create_incoming_invoice(
            org_id=real_org, supplier_id=supplier_id,
            items=sendable, date=date_str,
            store_id=payload.store_id, comment=payload.comment
        )
    except Exception as e:
        update_bot_invoice(invoice_id, status="iiko_error")
        _invalidate_cache("invoices", real_org)
        raise HTTPException(status_code=502, detail=f"iiko: {e}")

    if not resp.get("ok"):
        update_bot_invoice(invoice_id, status="iiko_error")
        _invalidate_cache("invoices", real_org)
        err = resp.get("error", "unknown")
        body_txt = resp.get("body") or ""
        if body_txt:
            err = f"{err}: {body_txt[:300]}"
        return {"ok": False, "error": err}

    update_bot_invoice(invoice_id,
        iiko_doc_id=resp.get("id") or "",
        iiko_doc_number=resp.get("number") or "",
        status="draft_in_iiko")
    _invalidate_cache("invoices", real_org)  # иначе список отдаёт старый 'ready' и нет кнопки «Провести»
    return {"ok": True, "iiko_doc_id": resp.get("id"), "iiko_doc_number": resp.get("number"),
            "unit_warnings": unit_warnings,
            "comment_applied": resp.get("comment_applied", False),
            "had_comment": bool(payload.comment)}


def _inv_reimport_fallback(inv: Dict[str, Any]) -> dict:
    """Содержимое накладной из данных бота — страховка для ре-импорта, если
    export iiko не отдаст позиции (см. IikoWebClient.set_document_status).
    Позиции пригодны только если ВСЕ сопоставлены с номенклатурой (есть id)."""
    try:
        rows = json.loads(inv.get("items") or "[]")
    except (json.JSONDecodeError, TypeError):
        rows = []
    items = []
    for it in rows:
        pid = it.get("iiko_product_id") or it.get("productId")
        if not pid:
            items = []
            break
        qty = float(it.get("quantity") or it.get("amount") or 0)
        price = float(it.get("price_per_unit") or it.get("price") or 0)
        items.append({"productId": pid, "amount": qty, "price": price,
                      "sum": float(it.get("total") or it.get("sum") or qty * price)})
    return {"supplier": inv.get("supplier_id") or "", "items": items,
            "date": (inv.get("date") or "")[:10]}


@app.post("/api/invoices/{invoice_id}/post")
async def invoice_post_doc(invoice_id: int, user: Dict = Depends(get_current_user)):
    """Провести накладную в iiko."""
    inv = get_bot_invoice(invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if inv.get("telegram_id") != user.get("id"):
        raise HTTPException(status_code=403, detail="Not your invoice")
    real_org = _get_org_id(inv["organization_id"], user)
    info = _get_restaurant_info(real_org, user)
    client = get_client(info["api_login"], info.get("iiko_server"), info.get("api_password"))
    # id мог не сохраниться при создании (import не возвращает documentId) —
    # дочитываем его по номеру с сервера, иначе провести нечего.
    doc_id = inv.get("iiko_doc_id")
    if not doc_id and inv.get("iiko_doc_number") and hasattr(client, "_resolve_incoming_doc_id"):
        date_part = (inv.get("date") or "")[:10]
        doc_id = await client._resolve_incoming_doc_id(inv["iiko_doc_number"], date_part)
        if doc_id:
            update_bot_invoice(invoice_id, iiko_doc_id=doc_id)
    if not doc_id:
        raise HTTPException(status_code=400, detail="Накладная не создана в iiko (нет doc_id)")
    try:
        resp = await client.set_document_status(
            real_org, doc_id, "PROCESSED",
            doc_number=inv.get("iiko_doc_number"), date=(inv.get("date") or "")[:10],
            fallback=_inv_reimport_fallback(inv))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"iiko: {e}")
    if not resp.get("ok"):
        raise HTTPException(status_code=502, detail=resp.get("error") or "iiko error")
    update_bot_invoice(invoice_id, status="posted")
    _invalidate_cache("invoices", real_org)  # чтобы появилась кнопка «Распровести»
    return {"ok": True}


@app.post("/api/invoices/{invoice_id}/unpost")
async def invoice_unpost_doc(invoice_id: int, user: Dict = Depends(get_current_user)):
    """Распровести накладную в iiko."""
    inv = get_bot_invoice(invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if inv.get("telegram_id") != user.get("id"):
        raise HTTPException(status_code=403, detail="Not your invoice")
    if not inv.get("iiko_doc_id"):
        raise HTTPException(status_code=400, detail="Нет doc_id")
    real_org = _get_org_id(inv["organization_id"], user)
    info = _get_restaurant_info(real_org, user)
    client = get_client(info["api_login"], info.get("iiko_server"), info.get("api_password"))
    try:
        resp = await client.set_document_status(
            real_org, inv["iiko_doc_id"], "NEW",
            doc_number=inv.get("iiko_doc_number"), date=(inv.get("date") or "")[:10],
            fallback=_inv_reimport_fallback(inv))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"iiko: {e}")
    if not resp.get("ok"):
        raise HTTPException(status_code=502, detail=resp.get("error") or "iiko error")
    update_bot_invoice(invoice_id, status="draft_in_iiko")
    _invalidate_cache("invoices", real_org)  # чтобы вернулась кнопка «Провести»
    return {"ok": True}


# ============ INVOICE BOT v2: state + helpers ============
# _pending_invoices: chat_id -> pending dict
# _pending_by_id:    pending_id -> chat_id (reverse index для Mini App, этап 5)
_pending_invoices: Dict[int, dict] = {}
_pending_by_id: Dict[str, int] = {}

# Демо-бот: пошаговое подключение реального iiko прямо в чате.
# chat_id -> {"step": "server"|"login"|"password", "server", "login", "pending_id"}
_demo_reg: Dict[int, dict] = {}

DEMO_REG_PROMPTS = {
    "server": ("🔌 Подключим ваш iiko — сразу после этого на 7 дней откроется "
               "ваша реальная аналитика.\n\n"
               "Шаг 1/3 — пришлите адрес вашего iiko-сервера. Это адрес, по которому вы открываете "
               "iikoWeb/iikoOffice в браузере (например <code>https://ваш-сервер.iiko.it/resto</code>) — "
               "скопируйте из адресной строки.\n\nОтменить — /cancel"),
    "login": ("Шаг 2/3 — логин API-пользователя iiko.\n"
              "Создаётся в iikoOffice → Администрирование → Сотрудники/Права: нужна роль с доступом к "
              "OLAP/REST API. Если не уверены — спросите вашего iiko-админа."),
    "password": ("Шаг 3/3 — пароль этого API-пользователя.\n\n"
                 "🔒 Сообщение с паролем удалю сразу после проверки."),
}

def _invoice_kb(pending_id: str) -> dict:
    """Динамическая клавиатура превью — WebApp-кнопка редактирования с pending_id в URL."""
    return {
        "inline_keyboard": [
            [{"text": "📤 Отправить в iiko", "callback_data": "act:send"}],
            [{"text": "✏️ Редактировать",   "web_app": {"url": f"{MINIAPP_URL}/?edit={pending_id}"}},
             {"text": "❌ Отменить",         "callback_data": "act:cancel"}],
        ]
    }


# Backward-compat alias для мест где не нужна WebApp-кнопка
INVOICE_KB_FALLBACK = {
    "inline_keyboard": [
        [{"text": "📤 Отправить в iiko", "callback_data": "act:send"}],
        [{"text": "✏️ Редактировать",   "callback_data": "act:edit"},
         {"text": "❌ Отменить",         "callback_data": "act:cancel"}],
    ]
}


# ============ ОНБОРДИНГ / FAQ / SETUP ============

WELCOME_TEXT = (
    "👋 <b>Привет! Это бот ресторанной аналитики iiko Analytics.</b>\n\n"
    "Что я умею:\n"
    "📊 Аналитика — выручка, средний чек, гости, фудкост, поставщики (Mini App)\n"
    "📎 Распознавание накладных — фото/файл/текст → автоматически в iiko\n"
    "💰 Анализ цен поставщиков — кто из них задирает цену\n\n"
    "<b>⚠️ Важно про iiko</b>\n"
    "Я работаю с <b>iikoServer / iikoWeb</b> по REST API (<code>/resto/api/v2/…</code>) — "
    "это формат, который используют все коробочные установки iiko, в том числе хостинг "
    "<code>*.iiko.it</code>.\n\n"
    "❌ <b>iiko Cloud API</b> (<code>api-ru.iiko.services</code>, отдельный продукт iiko с "
    "Bearer-токеном из ЛК iiko.biz) пока <b>НЕ поддерживается</b>. Если у вас интеграция "
    "идёт через него — этот бот вам не подойдёт.\n\n"
    "Не уверены, какой у вас iiko? Жмите «🔧 Как настроить iiko Server»."
)


def _start_kb() -> dict:
    return {
        "inline_keyboard": [
            [{"text": "🚀 Открыть Mini App", "web_app": {"url": f"{MINIAPP_URL}/"}}],
            [{"text": "📦 Лимит накладных", "callback_data": "limit:show"}],
            [{"text": "📖 FAQ / Инструкция", "callback_data": "info:faq"}],
            [{"text": "🔧 Как настроить iiko Server", "callback_data": "info:setup"}],
            [{"text": "💬 Связаться / Сообщить об ошибке", "url": "https://t.me/wiregroup"}],
        ]
    }


# Демо-бот: приветствие с призывом распознать первую накладную (без техтекста iiko).
DEMO_WELCOME_TEXT = (
    "👋 Это бот ресторанной аналитики iiko + распознавание накладных с фото.\n\n"
    "🔌 <b>Подключите свой iiko</b> — прямо здесь, в чате, 3 коротких шага. "
    "Сразу после подключения на <b>7 дней</b> открою вашу реальную аналитику в Mini App: "
    "выручка, фудкост, смены, расходы — всё по вашему заведению.\n\n"
    "📸 А накладные бот распознаёт с фото за ~10 секунд и складывает в Mini App → "
    "вкладка <b>«Расходы» → «Накладные»</b>, откуда вы отправляете приход в iiko.\n\n"
    "⚠️ Пока свой iiko не подключён, накладную можно распознать и посмотреть, "
    "но <b>провести её в iiko не получится</b> — сначала подключите сервер.\n\n"
    "Начнём — нажмите <b>«🔌 Подключить iiko»</b> 👇"
)


def _demo_start_kb() -> dict:
    return {
        "inline_keyboard": [
            [{"text": "🔌 Подключить iiko", "callback_data": "demo:connect"}],
            [{"text": "📊 Посмотреть демо (тестовые данные)", "web_app": {"url": f"{MINIAPP_URL}/"}}],
            [{"text": "💬 Написать менеджеру", "url": "https://t.me/wiregroup"}],
        ]
    }


# Каждый раздел: label, опциональный photo (имя файла в docs/screenshots),
# и caption — короткий текст-подпись (Telegram лимит — 1024 символа на caption).
# Если photo=None, шлём как обычное текстовое сообщение.
FAQ_SECTIONS: Dict[str, Dict[str, Any]] = {
    "start": {
        "label": "🚀 С чего начать",
        "photo": "01_onboarding_welcome.png",
        "caption": (
            "🚀 <b>С чего начать</b>\n\n"
            "1. Нажми «🚀 Открыть Mini App» внизу чата.\n"
            "2. На первом экране — «<b>Попробовать демо</b>» (на тестовых данных) или «<b>Начать →</b>» "
            "(нужны URL iikoServer + API-логин + пароль).\n"
            "3. Не знаешь что вводить — в меню бота жми «🔧 Как настроить iiko Server».\n\n"
            "После подключения попадёшь на главный экран. Снизу — вкладки Продажи, Персонал, Расходы, Год/год."
        ),
    },
    "invoices": {
        "label": "📎 Накладные через бота",
        "photo": "08_invoices_list.png",
        "caption": (
            "📎 <b>Накладные через бота</b>\n\n"
            "Пришли фото (jpg/png/heic), картинку файлом или текст с позициями — ИИ распознает "
            "поставщика, дату, позиции и сумму.\n\n"
            "Дальше: «<b>✏️ Редактировать</b>» — поправить в Mini App. «<b>📤 Отправить в iiko</b>» — "
            "выбираешь филиал и поставщика → накладная летит в iiko как черновик.\n\n"
            "В Mini App → Расходы → Накладные провести/распровести в один клик, без iikoOffice."
        ),
    },
    "prices": {
        "label": "💰 Анализ цен поставщиков",
        "photo": "11_price_card.png",
        "caption": (
            "💰 <b>Анализ цен поставщиков</b>\n\n"
            "Mini App → Расходы → Накладные → <b>📊 Цены</b>. Ищешь товар — видишь:\n"
            "• 🟢 Лучшая цена за 60 дней и у кого\n"
            "• Все поставщики с дельтой в %\n"
            "• Переплата от +10% подсвечивается красным\n\n"
            "Пример: помидор по 720 у одного и 850 у другого (+18%) — звонок и торг.\n\n"
            "В iikoOffice такого «из коробки» нет."
        ),
    },
    "zeros": {
        "label": "🤔 Показывает 0 за сегодня",
        "photo": None,
        "caption": (
            "🤔 <b>Показывает 0 за сегодня, хотя касса работает</b>\n\n"
            "Это особенность iiko, не баг бота. Три типичные причины:\n\n"
            "<b>1. Сеть с iikoChain.</b> Креды от центрального сервера → продажи приезжают с филиалов "
            "не онлайн, а пачкой при закрытии смены (после 24:00). Локальный сервер филиала покажет real-time.\n\n"
            "<b>2. Роль API-юзера ограничена.</b> В iikoOffice может стоять «Видеть только закрытые периоды» — сними галку.\n\n"
            "<b>3. iikoFront потерял связь.</b> Чеки копятся локально и зальются при восстановлении.\n\n"
            "Если за вчера данные есть, а 0 только за сегодня — это всегда одно из этих трёх."
        ),
    },
    "security": {
        "label": "🛡 Безопасность данных",
        "photo": None,
        "caption": (
            "🛡 <b>Безопасность данных</b>\n\n"
            "• Пароль iiko шифруется (Fernet/AES) перед записью в БД. В открытом виде не лежит.\n"
            "• Запросы Mini App проверяются по подписи Telegram <code>initData</code> — чужой telegram_id подменить нельзя.\n"
            "• Каждый юзер видит только СВОИ подключённые рестораны.\n"
            "• Мы — прокси к ВАШЕМУ iiko-серверу, копий ваших OLAP-отчётов не храним.\n\n"
            "<b>Рекомендуем:</b> заведи в iikoOffice отдельную учётку для бота с минимальными правами "
            "(Просмотр отчётов + Номенклатура + Создание накладных). Не используй админский логин."
        ),
    },
    "multi": {
        "label": "🏢 Несколько ресторанов",
        "photo": "07_settings_modal.png",
        "caption": (
            "🏢 <b>Несколько ресторанов / филиалов</b>\n\n"
            "Mini App → ⚙️ → «+ Добавить ресторан». Подключить можно любое число — каждый со своими "
            "URL/логином/паролем.\n\n"
            "В шапке главного экрана — переключатель «один филиал / Все филиалы».\n"
            "«Все филиалы» суммирует метрики по сети, запросы идут параллельно.\n\n"
            "📌 Чтобы видеть real-time по каждой точке — добавляй филиалы с их <b>локальными</b> "
            "iikoServer-URL, а не один общий iikoChain."
        ),
    },
}


def _section_photo_url(filename: Optional[str]) -> Optional[str]:
    """Полный URL к скрину для Telegram (telegram должен иметь публичный доступ)."""
    if not filename:
        return None
    return f"{MINIAPP_URL}/static/screenshots/{filename}"


def _faq_menu_kb() -> dict:
    rows = [[{"text": data["label"], "callback_data": f"info:faq:{key}"}]
            for key, data in FAQ_SECTIONS.items()]
    rows.append([{"text": "← В начало", "callback_data": "info:start"}])
    return {"inline_keyboard": rows}


def _faq_back_kb() -> dict:
    return {
        "inline_keyboard": [
            [{"text": "← К списку вопросов", "callback_data": "info:faq"}],
            [{"text": "🚀 Открыть Mini App", "web_app": {"url": f"{MINIAPP_URL}/"}}],
        ]
    }


FAQ_MENU_TEXT = (
    "📖 <b>FAQ / Инструкция</b>\n\n"
    "Выберите раздел — отвечу подробно. В любом разделе будет кнопка вернуться сюда."
)


SETUP_IIKO_TEXT = (
    "🔧 <b>Как настроить iiko для работы с ботом</b>\n\n"
    "<b>1. Какой у вас iiko?</b>\n"
    "Бот работает только с <b>iikoServer / iikoWeb</b> через REST API "
    "(<code>/resto/api/v2/…</code>). Этот API есть у всех, кто:\n"
    "• Развернул iikoServer на своём VDS/железе\n"
    "• Использует хостинг iiko на <code>*.iiko.it</code> (тот же протокол, просто iiko хостит за вас)\n\n"
    "❌ <b>Не работает</b> с iiko Cloud API (<code>api-ru.iiko.services</code>) — это отдельный продукт "
    "с другой авторизацией (Bearer-токен из ЛК iiko.biz). Если ваша интеграция идёт через него — пока не поддерживаем.\n\n"
    "<b>2. Узнайте URL вашего iikoServer</b>\n"
    "Адрес iikoOffice / iikoWeb. Примеры:\n"
    "• <code>https://ваш-ресторан.iiko.it:443/resto</code>\n"
    "• <code>https://192.168.1.10:443/resto</code> (локалка)\n"
    "• <code>https://your-domain.com:443/resto</code>\n"
    "Не знаете — спросите вашего iiko-администратора или интегратора.\n\n"
    "<b>3. Заведите API-пользователя в iikoOffice</b>\n"
    "Не давайте свой админский логин. Создайте отдельного:\n"
    "• Администрирование → <b>Сотрудники</b> → создать сотрудника\n"
    "• Логин/пароль — запомните, потом введёте в Mini App\n"
    "• Дайте права (раздел «Доступ» или роль):\n"
    "  — <b>Просмотр отчётов</b> (для аналитики)\n"
    "  — <b>Просмотр номенклатуры</b> (для накладных)\n"
    "  — <b>Создание накладных</b> (если хотите отправлять накладные ботом)\n"
    "  — <b>Просмотр сотрудников</b> (для вкладки «Персонал»)\n\n"
    "<b>4. Введите в Mini App</b>\n"
    "Откройте Mini App → введите URL + логин + пароль. Пароль шифруется (Fernet/AES) перед сохранением.\n\n"
    "<b>📚 Официальная документация iiko:</b>\n"
    "• <a href=\"https://ru.iiko.help/articles/#!api-documentations/iikoserver-api\">iikoServer API</a>\n"
    "• <a href=\"https://documenter.getpostman.com/view/2896430/TVemBpmn\">iikoWeb Public API (Postman)</a>\n"
    "• <a href=\"https://ru.iiko.help/\">База знаний iiko</a>\n\n"
    "Не разобрались — напишите вашему iiko-интегратору или в техподдержку: <code>api@iiko.ru</code>."
)


def _setup_back_kb() -> dict:
    return {
        "inline_keyboard": [
            [{"text": "← В начало", "callback_data": "info:start"}],
            [{"text": "🚀 Открыть Mini App", "web_app": {"url": f"{MINIAPP_URL}/"}}],
        ]
    }


async def _tg_send(chat_id: int, text: str, reply_markup: dict = None) -> Optional[int]:
    """Шлёт сообщение, возвращает message_id (или None при ошибке)."""
    if not BOT_TOKEN:
        return None
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json=payload)
        return r.json().get("result", {}).get("message_id")
    except Exception as e:
        logger.warning(f"[tg_send] {e}")
        return None


async def _tg_send_status(chat_id: int, text: str, reply_markup: dict = None) -> str:
    """Как _tg_send, но с различимым исходом: 'ok' — доставлено; 'blocked' —
    постоянная недоставка (юзер заблокировал бота / чат не найден, 400/403 —
    ретраить бессмысленно); 'error' — временная (сеть/5xx), имеет смысл ретраить.
    Нужен там, где от факта доставки зависит состояние (см. _demo_sales_scheduler):
    _tg_send глотает все ошибки и не даёт их различить."""
    if not BOT_TOKEN:
        return "error"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json=payload)
        if r.json().get("ok"):
            return "ok"
        logger.warning(f"[tg_send_status] {chat_id}: HTTP {r.status_code} {r.text[:200]}")
        return "blocked" if r.status_code in (400, 403) else "error"
    except Exception as e:
        logger.warning(f"[tg_send_status] {chat_id}: {e}")
        return "error"


# ============ ВОСКРЕСНЫЙ ОТЧЁТ ГЕН.ДИРЕКТОРУ ============
# Каждое воскресенье — одному получателю (@username), по каждому филиалу отдельно:
# оборот неделя-к-неделе (рост/падение) и средний чек.
# Получатель воскресного отчёта — ген.директор (дядя), он уже нажал Start в боте.
REPORT_RECIPIENT_USERNAME = os.getenv("REPORT_RECIPIENT_USERNAME", "AibolatAvilkhan")
REPORT_HOUR_LOCAL = int(os.getenv("REPORT_HOUR_LOCAL", "10"))  # во сколько (локальное) слать
# Секрет для открытия превью отчёта из браузера (URL никуда не выводится).
# Без явно заданного env — токен-доступ ВЫКЛЮЧЕН (fail-closed), остаётся только
# доступ оператора по Telegram. Хардкод-дефолт убран — это была дыра.
REPORT_PREVIEW_TOKEN = os.getenv("REPORT_PREVIEW_TOKEN", "")


async def _branch_week_metrics(rest: Dict[str, Any], date_from: str, date_to: str):
    """Оборот, гости и средний чек одного филиала за период. None при ошибке."""
    client = get_client(rest["api_login"], rest.get("iiko_server"), rest.get("api_password"))
    dept = rest.get("organization_name") or rest.get("organization_id")
    try:
        r = await client.olap_report(dept, date_from, date_to,
                                     ["OpenDate.Typed"], ["GuestNum", "DishAmountSum", "OrdersCount"])
        rows = r.get("data", [])
        revenue = sum(float(x.get("DishAmountSum", 0) or 0) for x in rows)
        guests = sum(float(x.get("GuestNum", 0) or 0) for x in rows)
        avg_check = revenue / guests if guests else 0
        return {"revenue": revenue, "guests": guests, "avg_check": avg_check}
    except Exception as e:
        logger.warning(f"[weekly_report] {dept}: {e}")
        return None


def _fmt_delta(cur: float, prev: float) -> str:
    """'▲ +12%' / '▼ −8%' / '— 0%' к прошлой неделе."""
    if prev <= 0:
        return "🆕 нет данных за прошлую неделю" if cur > 0 else "—"
    pct = round((cur - prev) / prev * 100)
    if pct > 0:
        return f"🟢 ▲ +{pct}%"
    if pct < 0:
        return f"🔴 ▼ {pct}%"
    return "⚪ 0%"


async def _build_weekly_report_text() -> Optional[str]:
    """Формирует текст отчёта по всем филиалам: оборот н/н + средний чек."""
    today = local_now().date()
    cur_from, cur_to = (today - timedelta(days=6)).isoformat(), today.isoformat()
    prev_from, prev_to = (today - timedelta(days=13)).isoformat(), (today - timedelta(days=7)).isoformat()

    rests = get_all_restaurants()
    if not rests:
        return None

    cur = await asyncio.gather(*(_branch_week_metrics(r, cur_from, cur_to) for r in rests))
    prev = await asyncio.gather(*(_branch_week_metrics(r, prev_from, prev_to) for r in rests))

    def d(x): return datetime.fromisoformat(x).strftime("%d.%m")
    lines = [f"📊 <b>Недельный отчёт</b> ({d(cur_from)}–{d(cur_to)})",
             f"<i>сравнение с прошлой неделей ({d(prev_from)}–{d(prev_to)})</i>", ""]
    tot_cur = tot_prev = 0.0
    for rest, c, p in zip(rests, cur, prev):
        name = rest.get("organization_name") or "Филиал"
        if not c:
            lines.append(f"🏢 <b>{name}</b>\n   ⚠️ нет данных\n")
            continue
        pc = p or {"revenue": 0, "avg_check": 0}
        tot_cur += c["revenue"]; tot_prev += pc["revenue"]
        lines.append(
            f"🏢 <b>{name}</b>\n"
            f"   Оборот: <b>{format_money(c['revenue'])}</b>  {_fmt_delta(c['revenue'], pc['revenue'])}\n"
            f"   Средний чек: <b>{format_money(c['avg_check'])}</b>  {_fmt_delta(c['avg_check'], pc['avg_check'])}\n"
        )
    lines.append(f"━━━━━━━━━━━━━━\n💰 <b>Итого по сети:</b> {format_money(tot_cur)}  {_fmt_delta(tot_cur, tot_prev)}")
    return "\n".join(lines)


async def _send_weekly_report() -> bool:
    """Шлёт отчёт получателю (@REPORT_RECIPIENT_USERNAME). True если отправлено."""
    user = get_user_by_username(REPORT_RECIPIENT_USERNAME)
    if not user or not user.get("telegram_id"):
        logger.warning(f"[weekly_report] получатель @{REPORT_RECIPIENT_USERNAME} не найден в боте "
                       f"(он должен нажать Start в боте). Отчёт не отправлен.")
        return False
    text = await _build_weekly_report_text()
    if not text:
        logger.warning("[weekly_report] нет филиалов — отчёт пуст")
        return False
    await _tg_send(int(user["telegram_id"]), text)
    return True


# ============ ПОНЕДЕЛЬНИЧНЫЙ ОТЧЁТ ВЛАДЕЛЬЦАМ ============
# Каждый понедельник — КАЖДОМУ юзеру с подключённым iiko по его филиалам:
# итоги прошлой полной недели (пн–вс) против позапрошлой + проблемы смен
# (расхождения кассы, исправленные платежи, непринятые смены).
# Не путать с воскресным отчётом ген.директору выше — тот один на всю сеть.

def _fmt_delta_money(cur: float, prev: float) -> str:
    """Как _fmt_delta, но с абсолютной дельтой: '🟢 ▲ +12% (+230 000 ₸)'."""
    base = _fmt_delta(cur, prev)
    if prev <= 0 or not base.startswith(("🟢", "🔴")):
        return base
    diff = cur - prev
    sign = "+" if diff >= 0 else "−"
    return f"{base} ({sign}{format_money(abs(diff))})"


def _last_week_ranges(today: date) -> Tuple[str, str, str, str]:
    """(cur_from, cur_to, prev_from, prev_to): прошлая полная неделя пн–вс
    и неделя перед ней. Работает в любой день недели (для превью)."""
    monday = today - timedelta(days=today.weekday())
    return ((monday - timedelta(days=7)).isoformat(),
            (monday - timedelta(days=1)).isoformat(),
            (monday - timedelta(days=14)).isoformat(),
            (monday - timedelta(days=8)).isoformat())


async def _branch_week_summary(rest: Dict[str, Any], cur_from: str, cur_to: str,
                               prev_from: str, prev_to: str) -> Dict[str, Any]:
    """Метрики недели + проблемы смен одного филиала.
    cur=None — данных нет совсем. Выручка — из кассовых смен (как в дашборде),
    фолбэк на OLAP; гости — всегда OLAP; ср. чек = выручка/гости."""
    cur, prev = await asyncio.gather(
        _branch_week_metrics(rest, cur_from, cur_to),
        _branch_week_metrics(rest, prev_from, prev_to),
    )
    out: Dict[str, Any] = {"name": rest.get("organization_name") or "Филиал",
                           "cur": cur,
                           "prev": prev or {"revenue": 0, "guests": 0, "avg_check": 0},
                           "problems": None}
    client = get_client(rest["api_login"], rest.get("iiko_server"), rest.get("api_password"))
    if not hasattr(client, "get_cashshifts"):
        return out  # облачный iiko — смен нет, остаёмся на OLAP без блока проблем
    dept = rest.get("organization_name") or rest.get("organization_id")
    try:
        cur_s, prev_s = await asyncio.gather(
            _branch_sessions(client, dept, cur_from, cur_to),
            _branch_sessions(client, dept, prev_from, prev_to),
        )
    except Exception as e:
        logger.warning(f"[weekly_users] смены {dept}: {e}")
        return out
    # выручка из смен (эталон кассы), гости OLAP-овские — как /api/dashboard
    if cur_s:
        rev = sum(float(s.get("payOrders") or 0) for s in cur_s)
        guests = (cur or {}).get("guests", 0)
        out["cur"] = {"revenue": rev, "guests": guests,
                      "avg_check": rev / guests if guests else 0}
    if prev_s:
        rev_p = sum(float(s.get("payOrders") or 0) for s in prev_s)
        g_p = out["prev"].get("guests", 0)
        out["prev"] = {"revenue": rev_p, "guests": g_p,
                       "avg_check": rev_p / g_p if g_p else 0}
    if not cur_s:
        return out
    diff_shifts, diff_sum, unaccepted, edits, type_changes = 0, 0.0, 0, 0, 0
    for s in cur_s:
        d = float(s.get("cashDiff") or 0)
        # cashRemain=0 при ненулевом cashDiff = кассир не вводил пересчёт кассы,
        # расхождение фиктивное (та же логика, что counted в cashshifts_day)
        if abs(d) > 0.01 and (s.get("cashRemain") or 0) != 0:
            diff_shifts += 1
            diff_sum += d
        if s.get("sessionStatus") != "ACCEPTED":
            unaccepted += 1
        try:
            pay = await client.get_cashshift_payments(s["id"])
            for e in _payment_edits(pay):
                edits += 1
                if e.get("account_changed"):
                    type_changes += 1
        except Exception as e:
            logger.warning(f"[weekly_users] платежи {dept} №{s.get('sessionNumber')}: {e}")
    out["problems"] = {"diff_shifts": diff_shifts, "diff_sum": diff_sum,
                       "unaccepted": unaccepted, "edits": edits,
                       "type_changes": type_changes}
    return out


def _build_owner_week_text(summaries: List[Dict[str, Any]], cur_from: str, cur_to: str,
                           prev_from: str, prev_to: str) -> Optional[str]:
    """Текст понедельничного отчёта из готовых сводок филиалов. None — слать нечего."""
    if not any(b.get("cur") for b in summaries):
        return None
    def d(x): return datetime.fromisoformat(x).strftime("%d.%m")
    lines = [f"📊 <b>Итоги недели</b> ({d(cur_from)}–{d(cur_to)})",
             f"<i>сравнение с прошлой ({d(prev_from)}–{d(prev_to)})</i>", ""]
    tot_cur = tot_prev = 0.0
    shown = 0
    for b in summaries:
        c = b.get("cur")
        p = b["prev"]
        # филиал без оборота обе недели (цех/закрытая точка) — не шумим нулями
        if c and not c.get("revenue") and not p.get("revenue"):
            continue
        lines.append(f"🏢 <b>{b['name']}</b>")
        if not c:
            lines.append("   ⚠️ нет данных\n")
            continue
        shown += 1
        tot_cur += c["revenue"]; tot_prev += p["revenue"]
        lines.append(f"   Выручка: <b>{format_money(c['revenue'])}</b>  "
                     f"{_fmt_delta_money(c['revenue'], p['revenue'])}")
        lines.append(f"   Средний чек: <b>{format_money(c['avg_check'])}</b>  "
                     f"{_fmt_delta(c['avg_check'], p['avg_check'])}")
        if c.get("guests"):
            lines.append(f"   Гости: <b>{int(c['guests'])}</b>  "
                         f"{_fmt_delta(c['guests'], p.get('guests', 0))}")
        pr = b.get("problems")
        if pr is None:
            pass  # смены недоступны — блок проблем не показываем
        elif pr["diff_shifts"] or pr["edits"] or pr["unaccepted"]:
            lines.append("   ⚠️ Проблемы за неделю:")
            if pr["diff_shifts"]:
                lines.append(f"   • смен с расхождением кассы: {pr['diff_shifts']} "
                             f"({format_money(pr['diff_sum'])})")
            if pr["edits"]:
                extra = (f", из них со сменой типа оплаты: {pr['type_changes']}"
                         if pr["type_changes"] else "")
                lines.append(f"   • исправленных платежей: {pr['edits']}{extra}")
            if pr["unaccepted"]:
                lines.append(f"   • непринятых смен: {pr['unaccepted']}")
        else:
            lines.append("   ✅ Смены чистые, расхождений нет")
        lines.append("")
    if not shown:
        return None  # все филиалы пустые — не шлём отчёт из одних заголовков
    if shown > 1:
        lines.append(f"━━━━━━━━━━━━━━\n💰 <b>Итого:</b> {format_money(tot_cur)}  "
                     f"{_fmt_delta_money(tot_cur, tot_prev)}")
    return "\n".join(lines).strip()


async def _owner_week_report(owner_rests: List[Dict[str, Any]]) -> Optional[str]:
    """Понедельничный отчёт одного владельца по всем его филиалам."""
    cf, ct, pf, pt = _last_week_ranges(local_now().date())
    summaries = await asyncio.gather(
        *(_branch_week_summary(r, cf, ct, pf, pt) for r in owner_rests))
    return _build_owner_week_text(list(summaries), cf, ct, pf, pt)


async def _send_weekly_users_report() -> int:
    """Шлёт понедельничный отчёт каждому владельцу + копию оператору
    (REPORT_RECIPIENT_USERNAME), чтобы видеть, что реально ушло юзерам.
    Возвращает число отправленных."""
    rests = get_all_restaurants()
    by_owner: Dict[int, list] = {}
    for r in rests:
        by_owner.setdefault(r["telegram_id"], []).append(r)
    op = get_user_by_username(REPORT_RECIPIENT_USERNAME)
    op_id = int(op["telegram_id"]) if op and op.get("telegram_id") else 0
    sent = 0
    for tg_id, owner_rests in by_owner.items():
        try:
            text = await _owner_week_report(owner_rests)
            if text and await _tg_send(int(tg_id), text) is not None:
                sent += 1
                if op_id and int(tg_id) != op_id:  # свой же отчёт не дублируем
                    u = get_user(int(tg_id)) or {}
                    uname = f"@{u['username']}" if u.get("username") else str(tg_id)
                    await _tg_send(op_id, f"📋 Копия еженедельного отчёта {uname}:\n\n{text}")
        except Exception as e:
            logger.warning(f"[weekly_users] owner {tg_id}: {e}")
    return sent


async def _weekly_users_report_scheduler():
    """Фоновый цикл: в ночь на понедельник (после 00:05) — итоги недели
    владельцам, как только неделя закрылась. app_state-дедупликация
    переживает рестарты Render."""
    while True:
        try:
            now = local_now()
            # weekday(): 0 = понедельник; 00:05 — форы кассам закрыться, как у ночной сводки
            if now.weekday() == 0 and (now.hour >= 1 or (now.hour == 0 and now.minute >= 5)):
                today_str = now.date().isoformat()
                if get_app_state("weekly_users_last_sent") != today_str:
                    n = await _send_weekly_users_report()
                    # помечаем и при n=0 — иначе весь день молотим iiko впустую
                    set_app_state("weekly_users_last_sent", today_str)
                    logger.info(f"[weekly_users] отправлено {n} владельцам за {today_str}")
        except Exception as e:
            logger.warning(f"[weekly_users] цикл: {e}")
        await asyncio.sleep(1800)


async def _demo_sales_scheduler():
    """Демо-бот: через DEMO_TRIAL_HOURS после подключения шлёт оффер в чат — один раз на юзера.
    В личке Telegram chat_id == telegram_id."""
    if not DEMO_ONLY:
        return
    while True:
        try:
            now = int(datetime.now(timezone.utc).timestamp())
            for tid in get_trials_due_for_sales(now, DEMO_TRIAL_HOURS * 3600):
                try:
                    # Единственный продающий пуш — терять нельзя. Временная ошибка
                    # (сеть/5xx) → НЕ помечаем, ретрай следующим тиком. Заблокировал
                    # бота (400/403) → помечаем, иначе бесконечный ретрай каждые 5 мин.
                    status = await _tg_send_status(tid, DEMO_OFFER_TEXT, _demo_upsell_kb())
                    if status == "error":
                        logger.warning(f"[demo_sales] offer {tid}: временная ошибка, ретрай через 5 мин")
                        continue
                    if status == "blocked":
                        logger.warning(f"[demo_sales] offer {tid}: юзер заблокировал бота, помечаю отправленным")
                    mark_trial_sales_sent(tid)
                    # Уведомляем владельца: у кого закончился пробный период.
                    try:
                        u = get_user(tid) or {}
                        uname = f"@{u['username']}" if u.get("username") else "(без username)"
                        await _tg_send(OWNER_CHAT_ID,
                                       f"⌛ Пробный период закончился\n{uname}\nid: {tid}")
                    except Exception as e:
                        logger.warning(f"[demo_sales] owner notify {tid}: {e}")
                except Exception as e:
                    logger.warning(f"[demo_sales] {tid}: {e}")
        except Exception as e:
            logger.warning(f"[demo_sales] loop: {e}")
        await asyncio.sleep(300)


async def _daily_stats_scheduler():
    """Раз в сутки после REPORT_HOUR_LOCAL шлёт владельцу сводку: юзеры + накладные.
    Использует wall-clock (local_now) + app_state-дедупликацию — переживает рестарты Render."""
    while True:
        try:
            now = local_now()
            if now.hour >= REPORT_HOUR_LOCAL:
                today_str = now.date().isoformat()
                if get_app_state("daily_stats_last_sent") != today_str:
                    await _tg_send(OWNER_CHAT_ID, _build_stats_text())
                    set_app_state("daily_stats_last_sent", today_str)
                    logger.info(f"[daily_stats] отправлена за {today_str}")
        except Exception as e:
            logger.warning(f"[daily_stats] {e}")
        await asyncio.sleep(300)


async def _weekly_report_scheduler():
    """Фоновый цикл: по воскресеньям после REPORT_HOUR_LOCAL шлёт отчёт один раз."""
    while True:
        try:
            now = local_now()
            # weekday(): 6 = воскресенье
            if now.weekday() == 6 and now.hour >= REPORT_HOUR_LOCAL:
                today_str = now.date().isoformat()
                if get_app_state("weekly_report_last_sent") != today_str:
                    ok = await _send_weekly_report()
                    if ok:
                        set_app_state("weekly_report_last_sent", today_str)
                        logger.info(f"[weekly_report] отправлен за {today_str}")
        except Exception as e:
            logger.warning(f"[weekly_report] цикл: {e}")
        await asyncio.sleep(1800)  # проверка каждые 30 мин


async def _tg_edit(chat_id: int, message_id: int, text: str, reply_markup: dict = None):
    if not BOT_TOKEN or not message_id:
        return
    payload = {"chat_id": chat_id, "message_id": message_id,
               "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            await c.post(f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText", json=payload)
    except Exception as e:
        logger.warning(f"[tg_edit] {e}")


async def _tg_send_photo(chat_id: int, photo_url: str, caption: str = "",
                          reply_markup: dict = None) -> Optional[int]:
    """Шлёт фото по URL. Telegram сам качает картинку с нашего сервера."""
    if not BOT_TOKEN:
        return None
    payload = {"chat_id": chat_id, "photo": photo_url,
               "caption": caption, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto", json=payload)
            data = r.json()
            return (data.get("result") or {}).get("message_id")
    except Exception as e:
        logger.warning(f"[tg_send_photo] {e}")
        return None


async def _tg_delete_msg(chat_id: int, message_id: int):
    """Удаляет сообщение. Тихо игнорит ошибку (сообщение могло уже быть удалено)."""
    if not BOT_TOKEN or not message_id:
        return
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            await c.post(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteMessage",
                         json={"chat_id": chat_id, "message_id": message_id})
    except Exception as e:
        logger.warning(f"[tg_delete] {e}")


async def _tg_download_file(file_id: str) -> Optional[Tuple[bytes, str]]:
    """Скачивает файл по file_id. Возвращает (bytes, mime_type) или None."""
    if not BOT_TOKEN:
        return None
    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getFile",
                            params={"file_id": file_id})
            result = r.json().get("result")
            if not result:
                return None
            file_path = result.get("file_path", "")
            img_r = await c.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}")
            return img_r.content, "image/jpeg"
    except Exception as e:
        logger.warning(f"[tg_download] {e}")
        return None


def _pending_id() -> str:
    """Короткий UUID для Mini App URL."""
    return uuid4().hex[:16]


def _fmt_kzt(n: float) -> str:
    """12500.0 -> '12 500'."""
    try:
        return f"{int(round(float(n))):,}".replace(",", " ")
    except Exception:
        return str(n)


def _photo_count_text(n: int) -> str:
    return "1 фото" if n == 1 else f"{n} фото"


def _format_invoice_v2(p: dict, status: str = "preview") -> str:
    """Превью накладной: позиции + «Итого X ₸ (в т.ч. НДС Y ₸)»."""
    result = p.get("result", {}) or {}
    supplier = result.get("supplier") or "—"
    date_str = result.get("date") or "—"
    items = result.get("items", []) or []
    total = float(result.get("total_amount") or 0)
    vat = float(result.get("vat_amount") or 0)
    n_photos = len(p.get("images") or [])

    icon = {"preview": "📋", "recognizing": "🔍",
            "sent": "✅", "cancelled": "❌"}.get(status, "📋")

    lines = [f"{icon} <b>Накладная распознана</b>"]
    if n_photos > 1:
        lines.append(f"📎 {_photo_count_text(n_photos)} объединено")
    lines.append(f"🏭 Поставщик: {supplier}")
    lines.append(f"📅 Дата: {date_str}")

    if items:
        lines.append(f"\n<b>Позиции ({len(items)}):</b>")
        for it in items[:15]:
            nm = it.get("name", "—") or "—"
            qty = it.get("quantity")
            un = it.get("unit") or ""
            ppu = it.get("price_per_unit")
            tot = it.get("total")
            qty_s = "—" if qty is None else str(qty)
            ppu_s = "—" if ppu is None else _fmt_kzt(ppu)
            tot_s = "—" if tot is None else _fmt_kzt(tot)
            lines.append(f"• {nm}: {qty_s} {un} × {ppu_s} ₸ = {tot_s} ₸")
        if len(items) > 15:
            lines.append(f"  … ещё {len(items)-15} позиций")

    total_line = f"\n💰 <b>Итого: {_fmt_kzt(total)} ₸</b>"
    if vat and vat > 0:
        total_line += f"  <i>(в т.ч. НДС {_fmt_kzt(vat)} ₸)</i>"
    lines.append(total_line)

    if status == "preview":
        lines.append("\n⚠️ <i>Распознано ИИ — могут быть ошибки. Проверь перед отправкой.</i>")
        if DEMO_ONLY:
            lines.append(
                "\n<b>Кнопки ниже — что делают:</b>\n"
                "📤 <b>Отправить в iiko</b> — здесь нужно подключить ваш iiko (быстрая регистрация "
                "в чате: сервер → логин → пароль). После этого накладная реально уйдёт к вам в учётку, "
                "и откроется доступ к вашей аналитике на 7 дней.\n"
                "✏️ <b>Редактировать</b> — открыть в приложении: поправить позиции, количество и "
                "суммы, сопоставить с вашей номенклатурой.\n"
                "❌ <b>Отменить</b> — сбросить эту накладную."
            )
        else:
            lines.append("<i>Выбери действие ниже:</i>")
    elif status == "sent":
        lines.append("\n<b>✅ Отправлено в iiko</b>")
    elif status == "cancelled":
        lines.append("\n<i>Отменено</i>")

    return "\n".join(lines)


def _format_dup_invoice(existing: Dict[str, Any]) -> str:
    """Карточка ранее сохранённой накладной — показываем при срабатывании дедупа,
    чтобы пользователь видел ЧТО именно уже есть и решил, создавать ли копию."""
    status_map = {
        "draft_in_iiko": "📥 Черновик в iiko",
        "needs_mapping": "✏️ Требует сопоставления",
        "iiko_error":    "⚠️ Ошибка при отправке в iiko",
        "pending":       "⏳ В обработке",
        "draft":         "📝 Локальный черновик",
    }
    status = existing.get("status") or "draft"
    status_txt = status_map.get(status, status)
    doc_num = existing.get("iiko_doc_number") or ""
    if status == "draft_in_iiko" and doc_num:
        status_txt += f" #{doc_num}"

    lines = ["⚠️ <b>Похоже, эту накладную уже отправляли</b>", ""]
    lines.append(f"📅 Добавлена: {(existing.get('created_at') or '')[:10]}")
    lines.append(f"🏭 Поставщик: {existing.get('supplier') or '—'}")
    if existing.get("branch"):
        lines.append(f"🏢 Филиал: {existing['branch']}")
    lines.append(f"📊 Статус: {status_txt}")
    lines.append(f"💰 Итого: {_fmt_kzt(existing.get('total_amount') or 0)} ₸")

    try:
        items = json.loads(existing.get("items") or "[]")
    except (json.JSONDecodeError, TypeError):
        items = []
    if items:
        lines.append(f"\n<b>Позиции ({len(items)}):</b>")
        for it in items[:15]:
            nm = it.get("name", "—") or "—"
            qty = it.get("quantity")
            un = it.get("unit") or ""
            ppu = it.get("price_per_unit")
            tot = it.get("total")
            qty_s = "—" if qty is None else str(qty)
            ppu_s = "—" if ppu is None else _fmt_kzt(ppu)
            tot_s = "—" if tot is None else _fmt_kzt(tot)
            lines.append(f"• {nm}: {qty_s} {un} × {ppu_s} ₸ = {tot_s} ₸")
        if len(items) > 15:
            lines.append(f"  … ещё {len(items)-15} позиций")

    lines.append("\n<b>Создать копию?</b>")
    return "\n".join(lines)


def _drop_pending(chat_id: int) -> None:
    p = _pending_invoices.pop(chat_id, None)
    if p:
        _pending_by_id.pop(p.get("pending_id", ""), None)
    try:
        delete_pending_invoice_by_chat(chat_id)
    except Exception as e:
        logger.warning(f"[pending-drop] {e}")


# ---- Персист pending в БД, чтобы он пережил рестарт сервера (см. issue с 404) ----
# В БД НЕ кладём сырые байты фото и suppliers_cache — только то, что нужно
# для редактирования и отправки (result + метаданные). После рестарта мультифото
# доснять нельзя, но открыть редактор и отправить накладную — можно.
def _persist_pending(pending: dict) -> None:
    try:
        slim = {k: v for k, v in pending.items()
                if k not in ("images", "suppliers_cache")}
        imgs = pending.get("images") or []
        slim["images_meta"] = [{"file_id": i.get("file_id"), "mime": i.get("mime")}
                               for i in imgs]
        save_pending_invoice(
            pending["pending_id"], pending["chat_id"], pending["telegram_id"],
            json.dumps(slim, ensure_ascii=False, default=str),
        )
    except Exception as e:
        logger.warning(f"[pending-persist] {e}")


def _rehydrate_pending(row: Optional[Dict[str, Any]]) -> Optional[dict]:
    """Восстанавливает pending из БД-строки в память."""
    if not row:
        return None
    try:
        p = json.loads(row.get("data") or "{}")
    except (json.JSONDecodeError, TypeError):
        return None
    p.setdefault("images", [])  # байты потеряны при рестарте — мультифото недоступно
    cid = p.get("chat_id")
    if cid is not None:
        _pending_invoices[cid] = p
        _pending_by_id[p.get("pending_id", "")] = cid
    return p


def _get_pending_by_chat(chat_id: int) -> Optional[dict]:
    p = _pending_invoices.get(chat_id)
    if p:
        return p
    return _rehydrate_pending(get_pending_invoice_by_chat(chat_id))


def _get_pending_by_id(pending_id: str) -> Optional[dict]:
    cid = _pending_by_id.get(pending_id)
    p = _pending_invoices.get(cid) if cid is not None else None
    if p:
        return p
    return _rehydrate_pending(get_pending_invoice(pending_id))


def _get_user_orgs(telegram_id: int) -> List[Dict[str, Any]]:
    """Все рестораны пользователя (org_id + creds). Дедупликация по organization_id уже в get_restaurants."""
    return get_restaurants(telegram_id) or []


def _has_real_iiko(telegram_id: int) -> bool:
    """Подключил ли юзер реальный iiko (не демо-заглушку)."""
    return any(r.get("api_login") != "demo" for r in _get_user_orgs(telegram_id))


def _bot_iiko_client(rest: Dict[str, Any]):
    """IikoClient/IikoWebClient для конкретной записи ресторана."""
    return get_client(rest["api_login"], rest.get("iiko_server"), rest.get("api_password"))


def _compute_dedup_hash(supplier: str, date_str: str, total: float,
                        items: List[Dict[str, Any]], org_id: str = "") -> str:
    """Стабильный хеш накладной для защиты от дублей. Включает филиал (org_id):
    одинаковая закупка в разные филиалы — не дубль."""
    sig_items = sorted([
        (str(it.get("name", "")).strip().lower(),
         round(float(it.get("quantity") or 0), 3),
         round(float(it.get("price_per_unit") or 0), 2))
        for it in (items or [])
    ])
    payload = json.dumps({
        "o": (org_id or "").strip(),
        "s": (supplier or "").strip().lower(),
        "d": (date_str or "").strip(),
        "t": round(float(total or 0), 2),
        "i": sig_items,
    }, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# Нормализация единиц измерения: варианты написания → каноническая форма
_UNIT_ALIASES = {
    "кг": "кг", "kg": "кг", "килограмм": "кг",
    "г": "г", "гр": "г", "грамм": "г", "g": "г",
    "л": "л", "литр": "л", "l": "л",
    "мл": "мл", "ml": "мл",
    "шт": "шт", "штук": "шт", "штука": "шт", "pcs": "шт", "порц": "шт",
    "уп": "уп", "упак": "уп", "упаковка": "уп", "пач": "уп", "пачка": "уп",
    "ящ": "ящ", "ящик": "ящ", "кор": "ящ", "коробка": "ящ",
    "бут": "бут", "бутылка": "бут", "бан": "бан", "банка": "бан",
}
# Безопасные автоконверсии: (из, в) → множитель количества
_UNIT_CONVERT = {("г", "кг"): 0.001, ("кг", "г"): 1000.0,
                 ("мл", "л"): 0.001, ("л", "мл"): 1000.0}


def _norm_unit(u: str) -> str:
    return _UNIT_ALIASES.get((u or "").strip().lower().rstrip("."), (u or "").strip().lower())


def _reconcile_amount(it: Dict[str, Any]) -> Tuple[float, float, float, Optional[str]]:
    """Готовит позицию для iiko в ЕДИНИЦЕ ТОВАРА из iiko (it['iiko_unit']).
    Возвращает (amount, price, sum, warning):
      • amount — количество, переведённое в единицу товара iiko (г→кг, мл→л);
      • sum    — итог строки (его видит и подтверждает оператор, ieLineTotal);
      • price  — выводится из суммы (sum/amount), чтобы amount×price==sum всегда,
                 независимо от того, за какую единицу была введена цена;
      • warning — если единицы расходятся и пара неизвестна (количество не трогаем,
                 оператор проверит черновик в iiko Office).
    """
    qty = float(it.get("quantity") or 0)
    price_in = float(it.get("price_per_unit") or 0)
    total = float(it.get("total") or (qty * price_in))
    warning = None
    amount = qty
    converted = False
    inv_u_raw = (it.get("unit") or "").strip().lower().rstrip(".")
    # 1) Фасовка: выбранная единица совпала с названием контейнера товара iiko
    #    (ящик/мешок/упаковка) → количество = qty × коэффициент фасовки (в базовых ед.).
    for c in (it.get("iiko_containers") or []):
        cname = (c.get("name") or "").strip().lower()
        cfactor = c.get("factor")
        if cname and cfactor and cname == inv_u_raw:
            amount = qty * float(cfactor)
            converted = True
            break
    # 2) Иначе — безопасная конверсия г↔кг / мл↔л по единице товара iiko
    if not converted:
        inv_u = _norm_unit(it.get("unit") or "")
        iiko_u = _norm_unit(it.get("iiko_unit") or "")
        if inv_u and iiko_u and inv_u != iiko_u:
            factor = _UNIT_CONVERT.get((inv_u, iiko_u))
            if factor:
                amount = qty * factor
                converted = True
            else:
                warning = (f"{it.get('name', '—')}: в накладной «{it.get('unit')}», "
                           f"в iiko «{it.get('iiko_unit')}»")
    # Цену выводим из суммы ТОЛЬКО при реальной конвертации единицы. Без конвертации
    # сохраняем исходное поведение (price_per_unit) — чтобы обычные накладные слать
    # ровно как раньше и ничего не сломать.
    price_out = (total / amount) if (converted and amount) else price_in
    return amount, price_out, total, warning


async def _auto_match_items(client, org_id: str,
                             items: List[Dict[str, Any]]
                             ) -> Tuple[List[Dict], List[str], List[str]]:
    """Сопоставляет позиции с номенклатурой iiko по имени.
    Возвращает (sendable_items, unmatched_names, unit_warnings).
    sendable_items — формат для create_incoming_invoice (product, amount, price, sum).
    Единицы: гр↔кг и мл↔л конвертируются автоматически (с пересчётом цены);
    несовместимые (уп vs кг и т.п.) не трогаем, но честно предупреждаем.
    """
    sendable, unmatched, unit_warnings = [], [], []
    for it in (items or []):
        # Если уже сопоставлено в редакторе — используем
        prod_id = it.get("iiko_product_id")
        prod_unit = it.get("iiko_unit") or ""
        if not prod_id:
            try:
                matches = await client.search_nomenclature(org_id, it.get("name", ""), limit=1)
            except Exception as e:
                logger.warning(f"[invoice-match] iiko search_nomenclature failed for org={org_id}, name='{it.get('name', '')}': {e}")
                matches = []
            if matches:
                prod_id = matches[0].get("id")
                prod_unit = matches[0].get("unit") or ""
                # Аннотируем саму позицию, чтобы сохранённый черновик нёс сопоставление
                # (редактор показывает «✓ сопоставлено», повторная отправка не теряет id).
                it["iiko_product_id"] = prod_id
                it["iiko_product_name"] = matches[0].get("name") or it.get("name")
                it["iiko_unit"] = prod_unit
        if not prod_id:
            unmatched.append(it.get("name", "—"))
            continue
        qty = float(it.get("quantity") or 0)
        price = float(it.get("price_per_unit") or 0)

        inv_u = _norm_unit(it.get("unit") or "")
        iiko_u = _norm_unit(prod_unit)
        if inv_u and iiko_u and inv_u != iiko_u:
            factor = _UNIT_CONVERT.get((inv_u, iiko_u))
            if factor:
                # автоконверсия: 500 г → 0.5 кг, цена за единицу пересчитывается
                qty = qty * factor
                price = price / factor if factor else price
                it["unit_converted"] = f"{it.get('unit')}→{prod_unit}"
            else:
                unit_warnings.append(
                    f"{it.get('name', '—')}: в накладной «{it.get('unit')}», "
                    f"в iiko товар в «{prod_unit}»")

        total = float(it.get("total") or (qty * price))
        sendable.append({
            "productId": prod_id,
            "amount": qty,
            "price": price,
            "sum": total,
        })
    return sendable, unmatched, unit_warnings


def _suppliers_kb(suppliers: List[Dict[str, Any]], page: int = 0) -> dict:
    """Клавиатура выбора поставщика (по 20 на страницу)."""
    PAGE = 20
    start = page * PAGE
    chunk = suppliers[start:start + PAGE]
    rows = []
    for s in chunk:
        sid = s.get("id") or s.get("supplierId")
        name = (s.get("name") or "—")[:35]
        if sid:
            rows.append([{"text": name, "callback_data": f"sup:{sid}"}])
    nav = []
    if page > 0:
        nav.append({"text": "← Назад", "callback_data": f"sup:page:{page-1}"})
    if start + PAGE < len(suppliers):
        nav.append({"text": "Ещё →", "callback_data": f"sup:page:{page+1}"})
    if nav:
        rows.append(nav)
    rows.append([{"text": "❌ Отмена", "callback_data": "act:cancel"}])
    return {"inline_keyboard": rows}


def _orgs_kb(orgs: List[Dict[str, Any]]) -> dict:
    """Клавиатура выбора филиала."""
    rows = []
    for o in orgs:
        oid = o.get("organization_id") or o.get("id")
        name = (o.get("organization_name") or o.get("name") or "—")[:35]
        if oid:
            rows.append([{"text": name, "callback_data": f"org:{oid}"}])
    rows.append([{"text": "❌ Отмена", "callback_data": "act:cancel"}])
    return {"inline_keyboard": rows}


def _dedup_kb() -> dict:
    return {
        "inline_keyboard": [[
            {"text": "✅ Да, создать копию", "callback_data": "dedup:yes"},
            {"text": "❌ Нет, отменить",     "callback_data": "dedup:no"},
        ]]
    }


async def _show_supplier_picker(chat_id: int, msg_id: int, pending: dict) -> bool:
    """Показывает список поставщиков. True если показано, False если ошибка."""
    rests = _get_user_orgs(pending["telegram_id"])
    if not rests:
        await _tg_edit(chat_id, msg_id,
            "⚠️ У тебя не настроен ни один ресторан в Mini App. Сначала добавь iiko-учётку.")
        return False
    first = rests[0]
    if first["api_login"] == "demo":
        await _tg_edit(chat_id, msg_id,
            "⚠️ Ты в demo-режиме. Отправка в iiko недоступна — добавь реальную учётку.")
        return False
    client = _bot_iiko_client(first)
    try:
        suppliers = await client.get_suppliers(first["organization_id"])
    except Exception as e:
        logger.warning(f"[suppliers] {e}")
        suppliers = []
    if not suppliers:
        await _tg_edit(chat_id, msg_id,
            "⚠️ Не удалось получить список поставщиков из iiko. Попробуй позже.")
        return False
    # сохраним в pending для пагинации/выбора по id
    pending["state"] = "choosing_supplier"
    pending["suppliers_cache"] = suppliers
    await _tg_edit(chat_id, msg_id,
        f"🏭 <b>Выбери поставщика</b> ({len(suppliers)}):",
        _suppliers_kb(suppliers, 0))
    return True


async def _show_supplier_picker_for_org(chat_id: int, msg_id: int, pending: dict, org_id: str) -> bool:
    """Показывает список поставщиков КОНКРЕТНОГО филиала. True если показано."""
    rests = _get_user_orgs(pending["telegram_id"])
    rest = next((r for r in rests if r.get("organization_id") == org_id), None)
    if not rest:
        await _tg_edit(chat_id, msg_id, "⚠️ Филиал не найден среди подключённых.")
        return False
    if rest["api_login"] == "demo":
        await _tg_edit(chat_id, msg_id,
            "⚠️ Ты в demo-режиме. Отправка в iiko недоступна — добавь реальную учётку.")
        return False
    client = _bot_iiko_client(rest)
    try:
        suppliers = await client.get_suppliers(org_id)
    except Exception as e:
        logger.warning(f"[suppliers for {org_id}] {e}")
        suppliers = []
    if not suppliers:
        await _tg_edit(chat_id, msg_id,
            "⚠️ Не удалось получить список поставщиков из iiko. Попробуй позже.")
        return False
    pending["state"] = "choosing_supplier"
    pending["suppliers_cache"] = suppliers
    await _tg_edit(chat_id, msg_id,
        f"🏭 <b>Выбери поставщика</b> ({len(suppliers)}):",
        _suppliers_kb(suppliers, 0))
    return True


async def _show_org_picker(chat_id: int, msg_id: int, pending: dict) -> bool:
    """Показывает список филиалов того же iiko-сервера."""
    rests = _get_user_orgs(pending["telegram_id"])
    if not rests:
        await _tg_edit(chat_id, msg_id, "⚠️ Нет филиалов в твоей учётке.")
        return False
    pending["state"] = "choosing_org"
    await _tg_edit(chat_id, msg_id,
        f"🏢 <b>Выбери филиал</b> ({len(rests)}):",
        _orgs_kb(rests))
    return True


def _compare_prices(telegram_id: int, current_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Для каждой позиции текущей накладной возвращает сравнение с историческим минимумом.

    Возвращает список dict'ов в том же порядке что current_items, каждый:
      {name, current_price, quantity, unit,
       min_price, min_supplier, min_date,  # None если нет истории
       status: 'new_min' | 'overpay' | 'normal' | 'no_history',
       delta_pct: float}
    """
    history = get_price_history(telegram_id, days=90)
    by_key: Dict[str, List[Dict[str, Any]]] = {}
    for h in history:
        by_key.setdefault(h["key"], []).append(h)

    rows: List[Dict[str, Any]] = []
    for it in current_items or []:
        name = (it.get("name") or "").strip()
        current_price = float(it.get("price_per_unit") or 0)
        quantity = float(it.get("quantity") or 0)
        unit = it.get("unit") or ""
        key = str(it.get("iiko_product_id") or name.lower())

        past = by_key.get(key, [])
        # Берём минимум среди прошлых закупок (НЕ включая текущую — она ещё не сохранена).
        if past:
            best = min(past, key=lambda r: r["price_per_unit"])
            min_price = best["price_per_unit"]
            min_supplier = best["supplier"]
            min_date = best["date"]
            if current_price <= min_price * 0.99:
                status = "new_min"
            elif current_price >= min_price * 1.1:
                status = "overpay"
            else:
                status = "normal"
            delta_pct = ((current_price - min_price) / min_price * 100) if min_price > 0 else 0.0
        else:
            min_price = None
            min_supplier = None
            min_date = None
            status = "no_history"
            delta_pct = 0.0

        rows.append({
            "name": name,
            "current_price": current_price,
            "quantity": quantity,
            "unit": unit,
            "min_price": min_price,
            "min_supplier": min_supplier,
            "min_date": min_date,
            "status": status,
            "delta_pct": delta_pct,
        })
    return rows


def _format_price_comparison(rows: List[Dict[str, Any]]) -> str:
    """HTML-сообщение для бота с построчным сравнением цен."""
    if not rows:
        return "📊 <b>Сравнение цен</b>\n\nВ накладной нет позиций с ценами для сравнения."

    if all(r["status"] == "no_history" for r in rows):
        return ("📊 <b>Сравнение цен</b>\n\n"
                "📭 Это первая накладная — истории закупок для сравнения пока нет. "
                "После нескольких отправленных накладных я смогу показывать где переплачиваешь, "
                "а где удалось купить дешевле обычного.")

    icons = {"new_min": "🟢", "overpay": "🔴", "normal": "⚪", "no_history": "🟡"}
    lines = ["📊 <b>Сравнение цен с историей (90 дней)</b>", ""]

    overpay_savings = 0.0
    for r in rows:
        icon = icons.get(r["status"], "⚪")
        name = html.escape(r["name"] or "—")
        cur = r["current_price"]
        unit = html.escape(r["unit"] or "ед")
        if r["status"] == "new_min":
            tail = f"новый минимум, было {r['min_price']:.0f} ₸"
        elif r["status"] == "overpay":
            supplier = html.escape(r["min_supplier"] or "—")
            tail = (f"+{r['delta_pct']:.0f}% к минимуму {r['min_price']:.0f} ₸ "
                    f"у \"{supplier}\", {r['min_date']}")
            overpay_savings += (cur - r["min_price"]) * (r["quantity"] or 0)
        elif r["status"] == "normal":
            sign = "+" if r["delta_pct"] >= 0 else ""
            tail = f"как обычно ({sign}{r['delta_pct']:.0f}%)"
        else:  # no_history
            tail = "нет истории"
        lines.append(f"{icon} <b>{name}</b> — {cur:.0f} ₸/{unit} <i>({tail})</i>")

    if overpay_savings > 0:
        lines.append("")
        lines.append(f"💡 <i>Потенциальная экономия если брать у дешёвых: "
                     f"{overpay_savings:,.0f} ₸</i>".replace(",", " "))
    return "\n".join(lines)


async def _finalize_send(chat_id: int, msg_id: int, pending: dict,
                          force: bool = False) -> None:
    """Финальный шаг: dedup-проверка + auto-match + create_incoming_invoice + сохранение в БД."""
    result = pending.get("result") or {}
    supplier_name = pending.get("supplier_name") or result.get("supplier") or "—"
    supplier_id = pending.get("supplier_id") or ""
    org_id = pending.get("organization_id") or ""
    items = result.get("items", []) or []
    total = float(result.get("total_amount") or 0)
    vat = float(result.get("vat_amount") or 0)
    date_str = result.get("date") or local_now().strftime("%Y-%m-%d")

    # ---- Dedup ----
    dedup_hash = _compute_dedup_hash(supplier_name, date_str, total, items, org_id)
    pending["dedup_hash"] = dedup_hash
    if not force:
        existing = find_invoice_by_dedup(dedup_hash, pending["telegram_id"], days=30)
        if existing:
            await _tg_edit(chat_id, msg_id, _format_dup_invoice(existing), _dedup_kb())
            return

    # ---- Resolve restaurant credentials for the picked org ----
    rests = _get_user_orgs(pending["telegram_id"])
    rest = next((r for r in rests if r["organization_id"] == org_id), None) or (rests[0] if rests else None)
    if not rest:
        await _tg_edit(chat_id, msg_id, "❌ Филиал не найден. Отмена.")
        _drop_pending(chat_id)
        return
    client = _bot_iiko_client(rest)

    # ---- Auto-match items to iiko nomenclature ----
    await _tg_edit(chat_id, msg_id, "🔎 Сопоставляю позиции с номенклатурой iiko...")
    sendable, unmatched, unit_warnings = await _auto_match_items(client, org_id, items)

    if unmatched:
        # Сохраняем как локальный черновик, просим открыть редактор для маппинга
        save_bot_invoice(
            telegram_id=pending["telegram_id"],
            telegram_user=pending.get("username") or "",
            raw_text="[ФОТО]",
            supplier=supplier_name, branch=rest.get("organization_name", ""),
            items_json=json.dumps(items, ensure_ascii=False),
            total_amount=total, date=date_str,
            vat_amount=vat, supplier_id=supplier_id,
            organization_id=org_id, dedup_hash=dedup_hash,
            status="needs_mapping",
        )
        names_preview = ", ".join(unmatched[:5])
        more = f" и ещё {len(unmatched)-5}" if len(unmatched) > 5 else ""
        await _tg_edit(chat_id, msg_id,
            f"⚠️ Не сопоставлено {len(unmatched)} позиций: {names_preview}{more}\n\n"
            f"📋 Накладная сохранена как черновик. Открой Mini App → Расходы → Накладные, "
            f"нажми ✏️ чтобы выбрать товары вручную и отправить.")
        _drop_pending(chat_id)
        return

    # ---- Сохраняем локальный черновик (review-first: в iiko НЕ создаём) ----
    # Оператор проверит/исправит филиал, поставщика, дату, позиции в Mini App
    # и сам нажмёт «Создать черновик в iiko». Так нет дублей/сирот в iiko, и
    # ошибки сопоставления (филиал/поставщик) исправляются без перепробивки.
    inv_id = save_bot_invoice(
        telegram_id=pending["telegram_id"],
        telegram_user=pending.get("username") or "",
        raw_text="[ФОТО]",
        supplier=supplier_name, branch=rest.get("organization_name", ""),
        items_json=json.dumps(items, ensure_ascii=False),
        total_amount=total, date=date_str,
        vat_amount=vat, supplier_id=supplier_id,
        organization_id=org_id, dedup_hash=dedup_hash,
        status="ready",
    )
    await _tg_edit(chat_id, msg_id,
        f"✅ <b>Накладная готова</b>\n\n"
        f"🏭 Поставщик: {supplier_name}\n"
        f"🏢 Филиал: {rest.get('organization_name','')}\n"
        f"💰 Итого: {_fmt_kzt(total)} ₸\n"
        + (("\n⚠️ <b>Проверь единицы измерения:</b>\n"
            + "\n".join(f"• {html.escape(w)}" for w in unit_warnings[:8])
            + ("\n  … и ещё " + str(len(unit_warnings) - 8) if len(unit_warnings) > 8 else "")
            + "\nКоличество могло распознаться не в той единице — поправь в редакторе.\n")
           if unit_warnings else "")
        + f"\nОткрой Mini App → Расходы → Накладные → ✏️ проверь филиал/поставщика "
        f"и нажми «Создать черновик в iiko».")
    _drop_pending(chat_id)


async def _demo_block_if_used(chat_id: int, telegram_id: int) -> bool:
    """Демо-бот: гейт распознавания.
    - Подключённый юзер с АКТИВНЫМ триалом распознаёт свободно (ядро продукта).
    - Подключённый с ИСТЁКШИМ триалом — блок + оффер: иначе платная фича
      (распознавание + приход в iiko) остаётся бесплатной навсегда.
    - НЕподключённый после DEMO_FREE_RECOGNITIONS — мягкий нудж подключить iiko.
    - Владелец бота гейт обходит: ему нужно тестить и показывать живые демо."""
    if not DEMO_ONLY:
        return False
    if telegram_id == OWNER_CHAT_ID:
        return False
    if _has_real_iiko(telegram_id):
        if _demo_trial_state(telegram_id).get("state") == "ended":
            await _tg_send(chat_id, DEMO_OFFER_TEXT, _demo_upsell_kb())
            return True
        return False
    if get_demo_invoice_count(telegram_id) >= DEMO_FREE_RECOGNITIONS:
        await _tg_send(chat_id,
            f"Вы распознали {DEMO_FREE_RECOGNITIONS} накладных — лимит демо исчерпан 👍\n\n"
            "Чтобы грузить накладные к себе и открыть доступ к вашей аналитике на 7 дней — "
            "в распознанной накладной выше нажмите «📤 Отправить в iiko»: подключим ваш iiko прямо в чате.")
        return True
    return False


async def _demo_after_recognition(chat_id: int, telegram_id: int) -> None:
    """Демо-бот: фиксируем лимит распознавания. Оффер здесь НЕ шлём —
    он уходит после окончания триала (см. _demo_sales_scheduler)."""
    if DEMO_ONLY:
        inc_demo_invoice_count(telegram_id)


async def _quota_gate(chat_id: int, telegram_id: int, username: str) -> bool:
    """Основной бот: месячный лимит накладных. True если распознавание блокировано."""
    if DEMO_ONLY:
        return False  # на демо-боте свой лимит — _demo_block_if_used
    reset_quota_if_due(telegram_id)
    if quota_available(telegram_id) > 0:
        return False
    if not QUOTA_ENFORCE:
        return False  # рубильник выключен: лимит только считается, не блокируем
    await _tg_send(chat_id, LIMIT_REACHED_TEXT.format(limit=DEMO_PLAN_INVOICES), _wiregroup_kb())
    if should_notify_limit(telegram_id):
        uname = f"@{username}" if username else "(без username)"
        try:
            await _tg_send(OWNER_CHAT_ID, f"🚫 Аккаунт упёрся в лимит\nid: {telegram_id} · {uname}")
        except Exception as e:
            logger.warning(f"[quota] owner notify {telegram_id}: {e}")
    return True


async def _invoice_gate(chat_id: int, telegram_id: int, username: str) -> bool:
    """Единый гейт перед распознаванием: демо-лимит ИЛИ месячный лимит основного бота."""
    if await _demo_block_if_used(chat_id, telegram_id):
        return True
    if await _quota_gate(chat_id, telegram_id, username):
        return True
    return False


async def _after_recognition(chat_id: int, telegram_id: int) -> None:
    """После успешного распознавания: демо-счётчик ИЛИ списание месячной квоты."""
    await _demo_after_recognition(chat_id, telegram_id)
    if not DEMO_ONLY:
        consume_quota(telegram_id)


# Буфер альбомов Telegram: несколько фото с одним media_group_id — это ОДНА
# многостраничная накладная. Иначе каждое фото распозналось бы отдельно =
# N превью и N× списание лимита. Собираем все фото группы и распознаём один раз.
_album_buffers: Dict[str, Dict[str, Any]] = {}
_ALBUM_DEBOUNCE_SEC = 2.0


async def _buffer_album_photo(chat_id: int, telegram_id: int, username: str,
                              media_group_id: str, image: Dict[str, Any]) -> None:
    """Копит фото альбома и через дебаунс распознаёт всю группу разом."""
    key = f"{chat_id}:{media_group_id}"
    buf = _album_buffers.get(key)
    if not buf:
        buf = {"chat_id": chat_id, "telegram_id": telegram_id, "username": username,
               "images": [], "task": None}
        _album_buffers[key] = buf
    buf["images"].append(image)
    if buf.get("task"):
        buf["task"].cancel()
    buf["task"] = asyncio.create_task(_flush_album_after_delay(key))


async def _flush_album_after_delay(key: str) -> None:
    try:
        await asyncio.sleep(_ALBUM_DEBOUNCE_SEC)
    except asyncio.CancelledError:
        return  # пришло ещё фото группы — таймер перезапущен
    buf = _album_buffers.pop(key, None)
    if not buf or not buf["images"]:
        return
    try:
        await _recognize_images_and_create_pending(
            buf["chat_id"], buf["telegram_id"], buf["username"], buf["images"])
    except Exception as e:
        logger.warning(f"[album] flush {key}: {e}")


async def _recognize_invoice_and_create_pending(
    chat_id: int, telegram_id: int, username: str,
    file_id: str, image_bytes: bytes, mime: str
) -> None:
    """Распознаёт одно фото и создаёт pending invoice. Универсально для photo и document.
    Многостраничные накладные — альбомом (media_group), см. _buffer_album_photo."""
    await _recognize_images_and_create_pending(
        chat_id, telegram_id, username,
        [{"file_id": file_id, "bytes": image_bytes, "mime": mime}])


async def _recognize_images_and_create_pending(
    chat_id: int, telegram_id: int, username: str,
    images: List[Dict[str, Any]]
) -> None:
    """Распознаёт набор фото (1 или несколько страниц) как ОДНУ накладную:
    один гейт лимита, один запрос к модели, одно списание."""
    if not images:
        return
    # Гейт: демо-лимит ИЛИ месячный лимит накладных основного бота.
    if await _invoice_gate(chat_id, telegram_id, username):
        return

    suffix = f" ({len(images)} фото)" if len(images) > 1 else ""
    status_msg_id = await _tg_send(chat_id, f"🔍 Распознаю накладную{suffix}...")
    result = await analyze_invoice_photos([(im["bytes"], im["mime"]) for im in images])
    if "error" in result:
        msg = f"❌ Не удалось распознать: {result['error']}\n\nПопробуй сфотографировать чётче."
        if status_msg_id:
            await _tg_edit(chat_id, status_msg_id, msg)
        else:
            await _tg_send(chat_id, msg)
        return

    pid = _pending_id()
    pending = {
        "pending_id": pid,
        "telegram_id": telegram_id,
        "username": username,
        "chat_id": chat_id,
        "message_id": status_msg_id,
        "state": "preview",
        "images": images,
        "result": result,
        "supplier_id": None,
        "organization_id": None,
        "created_at": datetime.now().isoformat(),
    }
    _pending_invoices[chat_id] = pending
    _pending_by_id[pid] = chat_id
    _persist_pending(pending)

    if status_msg_id:
        await _tg_edit(chat_id, status_msg_id, _format_invoice_v2(pending, "preview"), _invoice_kb(pending["pending_id"]))
    else:
        new_id = await _tg_send(chat_id, _format_invoice_v2(pending, "preview"), _invoice_kb(pending["pending_id"]))
        pending["message_id"] = new_id

    await _after_recognition(chat_id, telegram_id)


async def _handle_demo_registration(chat_id: int, telegram_id: int, username: str,
                                    message: dict) -> bool:
    """Демо-бот: пошаговое подключение реального iiko в чате (server→login→password),
    проверка доступа и реальная отправка отложенной накладной. True если сообщение
    обработано как часть регистрации."""
    reg = _demo_reg.get(chat_id)
    if not reg:
        return False
    # Сохранение из Mini App-редактора не перехватываем — пусть идёт по своему пути.
    if message.get("web_app_data"):
        return False
    text = (message.get("text") or "").strip()
    # /start выходит из регистрации и показывает обычное приветствие.
    if text == "/start":
        _demo_reg.pop(chat_id, None)
        return False
    if text.lower() in ("/cancel", "отмена", "cancel"):
        _demo_reg.pop(chat_id, None)
        await _tg_send(chat_id, "Окей, подключение отменено. Нажмите «🔌 Подключить iiko» в /start, когда будете готовы.")
        return True
    if not text:
        await _tg_send(chat_id, "Заверши подключение iiko — пришли ответ текстом по шагам, или /cancel.")
        return True

    step = reg.get("step")
    if step == "server":
        srv = text if text.startswith("http") else "https://" + text
        reg["server"] = srv
        reg["step"] = "login"
        await _tg_send(chat_id, DEMO_REG_PROMPTS["login"])
        return True
    if step == "login":
        reg["login"] = text
        reg["step"] = "password"
        await _tg_send(chat_id, DEMO_REG_PROMPTS["password"])
        return True
    if step == "password":
        # Пароль в чате — удаляем сообщение сразу, чтобы не висел в истории.
        mid = message.get("message_id")
        if mid:
            await _tg_delete_msg(chat_id, mid)
        password = text
        server, login = reg.get("server"), reg.get("login")
        pending_id = reg.get("pending_id")
        status = await _tg_send(chat_id, "🔐 Проверяю доступ к iiko...")
        try:
            client = get_client(login, server, password)
            orgs = await client.get_organizations()
        except Exception as e:
            reg["step"] = "server"
            await _tg_edit(chat_id, status,
                f"❌ Не получилось подключиться: {e}\n\n"
                f"Пришли адрес сервера заново (шаг 1/3), или /cancel.")
            return True
        if not orgs:
            reg["step"] = "server"
            await _tg_edit(chat_id, status,
                "❌ По этим доступам не нашлось организаций. Пришли адрес сервера заново, или /cancel.")
            return True
        create_user(telegram_id, username, username)
        # Повторное подключение: сносим старые записи, чтобы обновились креды
        # (add_restaurant всегда INSERT, а get_restaurants берёт старейшую строку).
        delete_all_restaurants(telegram_id)
        for o in orgs:
            add_restaurant(telegram_id, login, o["id"], o.get("name") or "iiko", server, password)
        _demo_reg.pop(chat_id, None)
        start_demo_trial(telegram_id, orgs[0]["id"],
                         int(datetime.now(timezone.utc).timestamp()))
        await _tg_edit(chat_id, status, f"✅ iiko подключён ({orgs[0].get('name') or 'организация'}).")
        # start_demo_trial — INSERT OR IGNORE: повторное подключение НЕ перезапускает
        # триал (иначе вечный бесплатный доступ). Поэтому обещать «7 дней» можно
        # только если триал реально активен — истёкшему честно шлём оффер.
        if _demo_trial_state(telegram_id).get("state") == "ended":
            await _tg_send(chat_id,
                "Ваш пробный период уже был использован, поэтому повторно доступ "
                "к аналитике не открылся.\n\n"
                "💰 Подключение навсегда — разработка и настройка под ваше заведение "
                "<b>300 000 ₸</b>, дальше обслуживание <b>30 000 ₸/мес</b>.\n\n"
                "👉 Напишите — подключу за день.",
                _demo_upsell_kb())
        else:
            await _tg_send(chat_id,
                "🎁 На <b>7 дней</b> открыл вам полный доступ к <b>вашей</b> аналитике в Mini App — "
                "реальная выручка, фудкост, смены, всё по-настоящему. Откройте и посмотрите свои цифры 👇\n\n"
                "Через неделю доступ закроется.",
                {"inline_keyboard": [[{"text": "📊 Открыть мою аналитику", "web_app": {"url": f"{MINIAPP_URL}/"}}]]})

        pending = _get_pending_by_id(pending_id) if pending_id else _get_pending_by_chat(chat_id)
        if not pending:
            if pending_id:
                await _tg_send(chat_id, "Накладная устарела — пришли её заново и нажми «Отправить в iiko».")
            else:
                await _tg_send(chat_id,
                    "📸 Теперь просто присылайте накладные фото — бот распознает их и положит в "
                    "Mini App → <b>Расходы → Накладные</b>, откуда вы отправите приход в iiko.")
            return True
        rests = [r for r in _get_user_orgs(telegram_id) if r.get("api_login") != "demo"]
        send_msg = await _tg_send(chat_id, "➡️ Отправляю накладную в iiko...")
        if len(rests) == 1:
            pending["organization_id"] = rests[0]["organization_id"]
            _persist_pending(pending)
            await _show_supplier_picker_for_org(chat_id, send_msg, pending, rests[0]["organization_id"])
        else:
            await _show_org_picker(chat_id, send_msg, pending)
        return True
    return True


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    """Обработчик сообщений и callback_query Telegram бота."""
    if not TELEGRAM_WEBHOOK_SECRET or not hmac.compare_digest(
        request.headers.get("X-Telegram-Bot-Api-Secret-Token", "").encode(), TELEGRAM_WEBHOOK_SECRET.encode()):
        raise HTTPException(status_code=403, detail="Invalid webhook secret")
    try:
        data = await request.json()

        # === CALLBACK_QUERY ===
        if "callback_query" in data:
            cb = data["callback_query"]
            cb_id = cb["id"]
            chat_id = cb["message"]["chat"]["id"]
            msg_id = cb["message"]["message_id"]
            action = cb.get("data", "")

            async with httpx.AsyncClient(timeout=10.0) as c:
                try:
                    await c.post(f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery",
                                 json={"callback_query_id": cb_id})
                except Exception:
                    pass

            pending = _get_pending_by_chat(chat_id)

            # Отмена — работает даже если pending нет
            if action == "act:cancel":
                _drop_pending(chat_id)
                await _tg_edit(chat_id, msg_id, "❌ <i>Отменено</i>")
                return {"ok": True}

            # Инфо-меню: FAQ и setup. Не зависят от pending, работают всегда.
            # Текущее сообщение может быть как текстом, так и фото (с раздела FAQ).
            # editMessageText не работает на фото-сообщениях — для перехода фото→текст
            # удаляем старое сообщение и шлём новое.
            cb_msg = cb.get("message") or {}
            is_photo_msg = bool(cb_msg.get("photo"))

            async def _show_text(text: str, kb: dict):
                if is_photo_msg:
                    await _tg_delete_msg(chat_id, msg_id)
                    await _tg_send(chat_id, text, kb)
                else:
                    await _tg_edit(chat_id, msg_id, text, kb)

            if action == "info:start":
                if DEMO_ONLY:
                    await _show_text(DEMO_WELCOME_TEXT, _demo_start_kb())
                else:
                    await _show_text(WELCOME_TEXT, _start_kb())
                return {"ok": True}
            if action == "info:faq":
                await _show_text(FAQ_MENU_TEXT, _faq_menu_kb())
                return {"ok": True}
            if action == "demo:connect" and DEMO_ONLY:
                # Демо-бот: подключение iiko — первым шагом, без накладной.
                _demo_reg[chat_id] = {"step": "server"}
                await _tg_send(chat_id, DEMO_REG_PROMPTS["server"])
                return {"ok": True}
            if action == "limit:show":
                tg_id = cb.get("from", {}).get("id", 0)
                await _show_text(_limit_text(tg_id), _start_kb())
                return {"ok": True}
            if action.startswith("info:faq:"):
                key = action.split(":", 2)[2]
                section = FAQ_SECTIONS.get(key)
                if not section:
                    await _show_text(FAQ_MENU_TEXT, _faq_menu_kb())
                    return {"ok": True}
                # FAQ без скринов: всегда редактируем текущее сообщение текстом.
                # Раньше для разделов с фото старое сообщение удалялось ДО отправки скрина —
                # если скрин недоступен (не задеплоен), сообщение пропадало и оставалась пустота.
                await _show_text(section["caption"], _faq_back_kb())
                return {"ok": True}
            if action == "info:setup":
                await _show_text(SETUP_IIKO_TEXT, _setup_back_kb())
                return {"ok": True}

            if not pending:
                await _tg_edit(chat_id, msg_id,
                               "⚠️ Данные устарели. Пришли накладную (фото, файлом или текстом) заново.")
                return {"ok": True}

            if action == "act:compare":
                items = (pending.get("result") or {}).get("items") or []
                rows = _compare_prices(pending["telegram_id"], items)
                text = _format_price_comparison(rows)
                # Шлём НОВЫМ сообщением — превью с кнопками остаётся доступным
                await _tg_send(chat_id, text)
                return {"ok": True}

            if action == "act:send":
                rests = [r for r in _get_user_orgs(pending["telegram_id"])
                         if r.get("api_login") != "demo"]
                if not rests:
                    # Демо-бот: подключаем реальный iiko прямо в чате, потом отправляем.
                    if DEMO_ONLY:
                        _demo_reg[chat_id] = {"step": "server", "pending_id": pending["pending_id"]}
                        await _tg_send(chat_id, DEMO_REG_PROMPTS["server"])
                        return {"ok": True}
                    await _tg_edit(chat_id, msg_id, "⚠️ У тебя не настроен ни один ресторан. Открой Mini App и подключи iiko-учётку.")
                    return {"ok": True}
                if len(rests) == 1:
                    pending["organization_id"] = rests[0]["organization_id"]
                    _persist_pending(pending)
                    await _show_supplier_picker_for_org(chat_id, msg_id, pending, rests[0]["organization_id"])
                else:
                    await _show_org_picker(chat_id, msg_id, pending)
                return {"ok": True}

            if action == "act:edit":
                # act:edit теперь WebApp-кнопка, callback не приходит. Но если старый клиент:
                stub = (_format_invoice_v2(pending, "preview")
                        + "\n\n⏳ <i>Обнови Telegram для встроенного редактора.</i>")
                await _tg_edit(chat_id, msg_id, stub, _invoice_kb(pending["pending_id"]))
                return {"ok": True}

            # === Пагинация поставщиков ===
            if action.startswith("sup:page:"):
                page = int(action.rsplit(":", 1)[-1])
                suppliers = pending.get("suppliers_cache") or []
                await _tg_edit(chat_id, msg_id,
                    f"🏭 <b>Выбери поставщика</b> ({len(suppliers)}):",
                    _suppliers_kb(suppliers, page))
                return {"ok": True}

            # === Выбор поставщика ===
            if action.startswith("sup:"):
                supplier_id = action.split(":", 1)[1]
                suppliers = pending.get("suppliers_cache") or []
                supplier = next((s for s in suppliers if (s.get("id") or s.get("supplierId")) == supplier_id), None)
                pending["supplier_id"] = supplier_id
                pending["supplier_name"] = supplier.get("name") if supplier else "—"
                _persist_pending(pending)
                # Филиал уже выбран на предыдущем шаге (или это был единственный) — финализируем
                await _finalize_send(chat_id, msg_id, pending, force=False)
                return {"ok": True}

            # === Выбор филиала → показываем поставщиков этого филиала ===
            if action.startswith("org:"):
                org_id = action.split(":", 1)[1]
                pending["organization_id"] = org_id
                _persist_pending(pending)
                await _show_supplier_picker_for_org(chat_id, msg_id, pending, org_id)
                return {"ok": True}

            # === Подтверждение дубля ===
            if action == "dedup:yes":
                await _finalize_send(chat_id, msg_id, pending, force=True)
                return {"ok": True}
            if action == "dedup:no":
                _drop_pending(chat_id)
                await _tg_edit(chat_id, msg_id, "❌ <i>Отменено (дубль).</i>")
                return {"ok": True}

            # неизвестный callback — игнорируем
            return {"ok": True}

        # === MESSAGE ===
        message = data.get("message", {})
        if not message:
            return {"ok": True}

        chat_id = message.get("chat", {}).get("id")
        user_tg = message.get("from", {})
        telegram_id = user_tg.get("id", 0)
        username = user_tg.get("username") or user_tg.get("first_name", "user")

        # Демо-бот: идёт пошаговое подключение iiko — перехватываем ввод до распознавания.
        if await _handle_demo_registration(chat_id, telegram_id, username, message):
            return {"ok": True}

        # === WEB_APP_DATA (Mini App-редактор → бот) ===
        if message.get("web_app_data"):
            raw = message["web_app_data"].get("data", "")
            try:
                payload = json.loads(raw)
            except Exception:
                payload = {}
            pid = payload.get("pending_id") or ""
            pending = _get_pending_by_id(pid)
            if not pending:
                await _tg_send(chat_id, "⚠️ Сессия редактирования истекла. Пришли накладную заново.")
                return {"ok": True}
            # Обновляем поля result. WebApp шлёт обновлённые items/supplier/date/total/vat
            r = pending.setdefault("result", {})
            if "items" in payload:    r["items"] = payload["items"]
            if "supplier" in payload: r["supplier"] = payload["supplier"]
            if "date" in payload:     r["date"] = payload["date"]
            if "total_amount" in payload: r["total_amount"] = payload["total_amount"]
            if "vat_amount" in payload:   r["vat_amount"] = payload["vat_amount"]
            pending["state"] = "preview"
            _persist_pending(pending)
            # Перерисовываем превью (на том же сообщении если знаем message_id)
            if pending.get("message_id"):
                await _tg_edit(pending["chat_id"], pending["message_id"],
                               _format_invoice_v2(pending, "preview"),
                               _invoice_kb(pending["pending_id"]))
            else:
                new_id = await _tg_send(pending["chat_id"],
                                        _format_invoice_v2(pending, "preview"),
                                        _invoice_kb(pending["pending_id"]))
                pending["message_id"] = new_id
            return {"ok": True}

        # === DOCUMENT (image как файл) ===
        doc = message.get("document")
        if doc and (doc.get("mime_type") or "").startswith("image/"):
            file_id = doc["file_id"]
            file_data = await _tg_download_file(file_id)
            if not file_data:
                await _tg_send(chat_id, "❌ Не удалось скачать файл.")
                return {"ok": True}
            image_bytes, mime = file_data
            mgid = message.get("media_group_id")
            if mgid:
                await _buffer_album_photo(chat_id, telegram_id, username, mgid,
                                          {"file_id": file_id, "bytes": image_bytes, "mime": mime})
            else:
                await _recognize_invoice_and_create_pending(chat_id, telegram_id, username, file_id, image_bytes, mime)
            return {"ok": True}

        # === DOCUMENT (не-image — PDF и пр.) ===
        if doc:
            await _tg_send(chat_id, "📎 Я принимаю только фото/картинки накладных (jpg/png/heic). PDF и другие форматы пока не поддерживаются.")
            return {"ok": True}

        # === PHOTO (сжатое) — принимаем самый большой размер ===
        if message.get("photo"):
            photo_sizes = message["photo"]
            biggest = photo_sizes[-1]
            file_id = biggest["file_id"]
            file_data = await _tg_download_file(file_id)
            if not file_data:
                await _tg_send(chat_id, "❌ Не удалось скачать фото.")
                return {"ok": True}
            image_bytes, mime = file_data
            if not mime:
                mime = "image/jpeg"
            mgid = message.get("media_group_id")
            if mgid:
                await _buffer_album_photo(chat_id, telegram_id, username, mgid,
                                          {"file_id": file_id, "bytes": image_bytes, "mime": mime})
            else:
                await _recognize_invoice_and_create_pending(chat_id, telegram_id, username, file_id, image_bytes, mime)
            return {"ok": True}

        # === TEXT ===
        text = message.get("text") or ""
        if text == "/start" or text.startswith("/start "):
            parts = text.split(maxsplit=1)
            # Вручную набранный «/start <текст>» может содержать HTML — неэкранированный
            # источник, попав в user_acquisition, ломал бы parse_mode=HTML во всех
            # пушах владельцу (/stats, /today, дайджест). Deep-link и так [A-Za-z0-9_-].
            raw_source = parts[1].strip() if len(parts) > 1 else ""
            source = re.sub(r"[^0-9A-Za-z_\-]", "", raw_source)[:64] or "direct"
            if DEMO_ONLY:
                try:
                    is_new = record_acquisition(telegram_id, username, source)
                except Exception as e:
                    logger.warning(f"[acq] record_acquisition {telegram_id}: {e}")
                    is_new = False
                if is_new:
                    try:
                        ts = datetime.now().strftime("%d.%m.%Y %H:%M")
                        uname = f"@{username}" if username else "(без username)"
                        await _tg_send(OWNER_CHAT_ID,
                                       f"🆕 Новый юзер {uname}\nid: {telegram_id}\nИсточник: {source}\n{ts}")
                    except Exception as e:
                        logger.warning(f"[acq] owner notify {telegram_id}: {e}")
                await _tg_send(chat_id, DEMO_WELCOME_TEXT, _demo_start_kb())
            else:
                await _tg_send(chat_id, WELCOME_TEXT, _start_kb())
            return {"ok": True}
        if text in ("/faq", "/help"):
            await _tg_send(chat_id, FAQ_MENU_TEXT, _faq_menu_kb())
            return {"ok": True}
        if text == "/stats" and chat_id == OWNER_CHAT_ID:
            try:
                body = _build_stats_text()
            except Exception as e:
                body = f"⚠️ Ошибка сбора статистики: {e}"
            await _tg_send(chat_id, body)
            return {"ok": True}
        if text == "/today" and chat_id == OWNER_CHAT_ID:
            try:
                body = _build_today_text()
            except Exception as e:
                body = f"⚠️ Ошибка: {e}"
            await _tg_send(chat_id, body)
            return {"ok": True}
        if text in ("/limit", "📦 Лимит накладных") and not DEMO_ONLY:
            await _tg_send(chat_id, _limit_text(telegram_id))
            return {"ok": True}
        if text.startswith("/addquota") and chat_id == OWNER_CHAT_ID:
            parts = text.split()
            if len(parts) == 3 and parts[1].isdigit() and parts[2].lstrip("-").isdigit():
                tid, n = int(parts[1]), int(parts[2])
                new_extra = add_quota_extra(tid, n)
                await _tg_send(chat_id,
                               f"✅ Аккаунту {tid} начислено {n}. Баланс докупленного: {new_extra}.")
            else:
                await _tg_send(chat_id, "Формат: /addquota <telegram_id> <кол-во>")
            return {"ok": True}
        if text.startswith("/resetuser") and chat_id == OWNER_CHAT_ID:
            parts = text.split()
            if len(parts) == 2 and parts[1].isdigit():
                tid = int(parts[1])
                # Чистим и in-memory состояние (в личке chat_id == telegram_id),
                # иначе досбросовый pending «приклеит» новое фото к старой накладной.
                _demo_reg.pop(tid, None)  # на случай зависшей регистрации
                _drop_pending(tid)
                for k in [k for k in _album_buffers if k.startswith(f"{tid}:")]:
                    buf = _album_buffers.pop(k, None)
                    if buf and buf.get("task"):
                        buf["task"].cancel()
                res = wipe_demo_user(tid)
                total = sum(res.values())
                detail = ", ".join(f"{t}:{n}" for t, n in res.items() if n)
                await _tg_send(chat_id,
                    f"🧹 Юзер {tid} удалён (строк: {total}). {detail or 'ничего не было'}.\n"
                    f"Теперь он может начать заново: /start → «🔌 Подключить iiko».")
            else:
                await _tg_send(chat_id, "Формат: /resetuser <telegram_id>")
            return {"ok": True}
        if text.startswith("/"):
            return {"ok": True}
        if text:
            # Короткие сообщения (приветствия и т.п.) — не пытаемся распознать
            if len(text.strip()) < 20:
                await _tg_send(chat_id,
                    "📎 Пришли накладную — фото, файлом или текстом (минимум 20 символов с позициями и ценами).")
                return {"ok": True}

            # Гейт: демо-лимит ИЛИ месячный лимит накладных основного бота.
            if await _invoice_gate(chat_id, telegram_id, username):
                return {"ok": True}

            status_msg_id = await _tg_send(chat_id, "🔍 Распознаю накладную из текста...")
            result = await analyze_invoice_text(text)
            if "error" in result or not (result.get("items") or []):
                err = result.get("error") if "error" in result else "не нашёл позиций в тексте"
                await _tg_edit(chat_id, status_msg_id,
                    f"⚠️ Не получилось распознать накладную из текста: {err}.\n\n"
                    f"Попробуй прислать фото или файл — это надёжнее.")
                return {"ok": True}

            # Создаём pending как для фото
            pid = _pending_id()
            pending = {
                "pending_id": pid,
                "telegram_id": telegram_id,
                "username": username,
                "chat_id": chat_id,
                "message_id": status_msg_id,
                "state": "preview",
                "images": [],
                "result": result,
                "supplier_id": None,
                "organization_id": None,
                "created_at": datetime.now().isoformat(),
            }
            _pending_invoices[chat_id] = pending
            _pending_by_id[pid] = chat_id
            _persist_pending(pending)
            await _tg_edit(chat_id, status_msg_id,
                           _format_invoice_v2(pending, "preview"),
                           _invoice_kb(pending["pending_id"]))
            await _after_recognition(chat_id, telegram_id)
            return {"ok": True}

    except Exception as e:
        logger.warning(f"[webhook] {e}")

    return {"ok": True}





# ==== Отзывы с внешних площадок (2ГИС, Wolt) ====

@app.get("/api/reviews/{org_id}")
async def branch_reviews(org_id: str, user: Dict = Depends(get_current_user)):
    """Рейтинг и отзывы филиала с 2ГИС и Wolt + динамика за неделю."""
    import reviews as rv
    from database import (get_review_link, set_review_link,
                          save_rating_snapshot, get_rating_history)

    if _use_demo_data(user, org_id):
        return {"platforms": [
            {"platform": "2gis", "label": "2ГИС", "rating": 4.6, "scale": 5,
             "reviews_count": 1234, "delta": 0.1, "title": "Demo"},
            {"platform": "wolt", "label": "Wolt", "rating": 8.8, "scale": 10,
             "reviews_count": None, "delta": -0.2, "title": "Demo"},
        ], "reviews": [
            {"rating": 5, "text": "Очень вкусные роллы, быстро привезли!",
             "date": "2026-06-10", "author": "Гость"},
            {"rating": 3, "text": "Долго ждали заказ в зале.",
             "date": "2026-06-09", "author": "Гость"},
        ]}

    real_org = _get_org_id(org_id, user)
    info = _get_restaurant_info(real_org, user)
    org_name = info.get("org_name", "")
    # бренд для поиска точек: настройка пользователя, по умолчанию Saya Sushi
    brand = get_app_state(f"review_brand:{user.get('id', 0)}") or rv.BRAND_QUERY

    cache_key = f"reviews:{real_org}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    today = local_now().strftime("%Y-%m-%d")
    week_ago = (local_now() - timedelta(days=7)).strftime("%Y-%m-%d")
    out: Dict[str, Any] = {"platforms": [], "reviews": []}

    platforms = [
        ("2gis", "2ГИС", 5, lambda name: rv.discover_2gis(name, brand),
         lambda link: rv.fetch_2gis(link["external_id"])),
        ("wolt", "Wolt", 10, lambda name: rv.discover_wolt(name, brand),
         lambda link: rv.fetch_wolt(link["external_id"], link.get("extra") or "", brand)),
    ]
    for platform, label, scale, discover, fetch in platforms:
        try:
            link = get_review_link(real_org, platform)
            if not link and org_name:
                found = await discover(org_name)
                if found:
                    set_review_link(real_org, platform, found["external_id"],
                                    found.get("title", ""), found.get("extra", ""))
                    link = get_review_link(real_org, platform)
            if not link:
                continue
            data = await fetch(link)
            if not data or data.get("rating") is None:
                continue
            save_rating_snapshot(real_org, platform, today,
                                 data["rating"], data.get("reviews_count"))
            # динамика: сравниваем с самым свежим снапшотом недельной давности
            # (или самым старым из имеющихся, пока истории меньше недели)
            hist = [h for h in get_rating_history(real_org, platform, days=30)
                    if h["date"] < today and h["rating"] is not None]
            base = None
            old = [h for h in hist if h["date"] <= week_ago]
            if old:
                base = old[-1]
            elif hist:
                base = hist[0]
            delta = round(data["rating"] - base["rating"], 2) if base else None
            out["platforms"].append({
                "platform": platform, "label": label, "scale": scale,
                "rating": data["rating"], "reviews_count": data.get("reviews_count"),
                "delta": delta, "since": base["date"] if base else None,
                "title": link.get("title", ""),
            })
            if platform == "2gis":
                out["reviews"] = data.get("reviews", [])
        except Exception as e:
            logger.warning(f"[reviews] {platform} {real_org}: {e}")

    _set_cached(cache_key, out)
    return out


class ReviewBrandIn(BaseModel):
    brand: str


@app.post("/api/reviews/brand")
async def set_review_brand(payload: ReviewBrandIn, user: Dict = Depends(get_current_user)):
    """Название бренда для поиска точек на площадках отзывов (для любых клиентов)."""
    set_app_state(f"review_brand:{user.get('id', 0)}", payload.brand.strip()[:60])
    return {"ok": True}


# ==== Вкладка «Смены»: сверка кассовых смен и зарплата ====

async def _branch_sessions(client, dept: str, date_from: str, date_to: str) -> List[Dict[str, Any]]:
    """Кассовые смены ОДНОГО филиала (та же цепочка, что в branch_shift_revenue)."""
    sessions, codes = await asyncio.gather(
        client.get_cashshifts(date_from, date_to),
        client.olap_report(dept, date_from, date_to, ["Conception.Code"], ["DishAmountSum"]),
    )
    code_set = {str(x.get("Conception.Code")) for x in codes.get("data", [])
                if x.get("Conception.Code") is not None}
    target_ids: set = set()
    if code_set:
        try:
            concs = await client.get_conceptions()
            target_ids = {c["id"] for c in concs if c.get("code") in code_set and c.get("id")}
        except Exception:
            pass
    if target_ids:
        return [s for s in sessions if s.get("conceptionId") in target_ids]
    distinct = {s.get("conceptionId") for s in sessions if s.get("conceptionId")}
    return sessions if len(distinct) <= 1 else []


def _payment_edits(payments: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Платежи, исправленные при принятии смены (тип/сумма original → edited)."""
    out = []
    for rec in payments.get("cashlessRecords", []) or []:
        orig_sum = rec.get("originalSum")
        act_sum = rec.get("actualSum")
        if orig_sum != act_sum or rec.get("originalPayAccountId") != rec.get("editedPayAccountId"):
            info = rec.get("info") or {}
            out.append({"sum_was": orig_sum, "sum_now": act_sum,
                        "type": info.get("type", ""), "time": (info.get("creationDate") or "")[11:16],
                        "account_changed": rec.get("originalPayAccountId") != rec.get("editedPayAccountId")})
    return out


@app.get("/api/cashshifts_day/{org_id}")
async def cashshifts_day(org_id: str, date: str, user: Dict = Depends(get_current_user)):
    """Смены филиала за дату: суммы, расхождения, исправленные платежи, кассиры."""
    if _use_demo_data(user, org_id):
        return {"date": date, "shifts": [
            {"number": 12, "cash_reg": 1, "cashier": "Виктория Ким", "open": "10:02", "close": "23:48",
             "cash": 55970, "card": 276508, "credit": 8973, "total": 341451,
             "pay_in": 50706, "pay_out": 42665, "cash_diff": -8500, "status": "UNACCEPTED",
             "edits": [{"sum_was": 1090, "sum_now": 1090, "type": "CARD", "time": "22:37", "account_changed": True}]},
        ]}
    real_org = _get_org_id(org_id, user)
    info = _get_restaurant_info(real_org, user)
    client = get_client(info["api_login"], info.get("iiko_server"), info.get("api_password"))
    if not hasattr(client, "get_cashshifts"):
        raise HTTPException(status_code=400, detail="Кассовые смены доступны только на самохостед iiko")
    cache_key = f"cashshifts_day:{real_org}:{date}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached
    dept = _olap_dept(real_org, info)
    sessions, employees, paytype_names = await asyncio.gather(
        _branch_sessions(client, dept, date, date),
        client.get_employees_map(),
        client.get_payment_types_map(),
    )
    shifts = []
    for s in sessions:
        edits: List[Dict[str, Any]] = []
        breakdown: List[Dict[str, Any]] = []
        try:
            payments = await client.get_cashshift_payments(s["id"])
            edits = _payment_edits(payments)
            # разбивка смены по типам оплат (безнал из платёжных записей + наличные)
            sums: Dict[str, float] = {}
            for rec in payments.get("cashlessRecords", []) or []:
                pt = rec.get("paymentTypeId") or (rec.get("info") or {}).get("paymentTypeId")
                sums[pt] = sums.get(pt, 0) + float(rec.get("actualSum") or 0)
            breakdown = [{"name": paytype_names.get(pt, "Безнал"), "sum": round(v)}
                         for pt, v in sums.items() if v]
            breakdown.sort(key=lambda x: -x["sum"])
            if s.get("salesCash"):
                breakdown.insert(0, {"name": "Наличные", "sum": round(float(s["salesCash"]))})
        except Exception as e:
            logger.warning(f"[cashshifts_day] payments {s.get('id')}: {e}")
        shifts.append({
            "number": s.get("sessionNumber"), "cash_reg": s.get("cashRegNumber"),
            "cashier": employees.get(s.get("responsibleUserId") or "", "—"),
            "open": (s.get("openDate") or "")[11:16], "close": (s.get("closeDate") or "")[11:16],
            "cash": s.get("salesCash"), "card": s.get("salesCard"), "credit": s.get("salesCredit"),
            "total": s.get("payOrders"), "pay_in": s.get("payIn"), "pay_out": s.get("payOut"),
            "cash_diff": s.get("cashDiff"), "status": s.get("sessionStatus"),
            "conception_id": s.get("conceptionId"),
            # пересчёт кассы вводился? (остаток 0 при ненулевом расхождении =
            # кассир не вводил фактическую наличность — это не ошибка смены)
            "counted": bool((s.get("cashRemain") or 0) != 0
                            or abs(float(s.get("cashDiff") or 0)) < 0.01),
            "edits": edits,
            "pay_breakdown": breakdown,
        })
    resp = {"date": date, "shifts": shifts}
    _set_cached(cache_key, resp)
    return resp


def _attendance_journal(attendance: List[Dict[str, Any]],
                        employees: Dict[str, str],
                        org_name: str = "") -> List[Dict[str, Any]]:
    """Явки iiko → строки приход/уход по (сотрудник, день). Пересекающиеся
    интервалы (задвоенные роли на терминале) сливаются, чтобы часы не удваивались.
    Формат строки: employee_id, name, date(YYYY-MM-DD), start/end(HH:MM), hours."""
    by_emp_day: Dict[Any, list] = {}
    for a in attendance:
        if org_name and a.get("departmentName") != org_name:
            continue
        try:
            df = datetime.fromisoformat(a["dateFrom"])
            dt_ = datetime.fromisoformat(a["dateTo"])
        except Exception:
            continue
        if dt_ <= df:
            continue
        by_emp_day.setdefault((a["employeeId"], df.date()), []).append((df, dt_))
    rows: List[Dict[str, Any]] = []
    for (eid, _day), ivs in by_emp_day.items():
        ivs.sort()
        merged = [list(ivs[0])]
        for df, dt_ in ivs[1:]:
            if df <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], dt_)
            else:
                merged.append([df, dt_])
        for a0, b0 in merged:
            rows.append({
                "employee_id": eid,
                "name": employees.get(eid, eid[:8]),
                "date": a0.date().isoformat(),
                "start": a0.strftime("%H:%M"),
                "end": b0.strftime("%H:%M"),
                "hours": round((b0 - a0).total_seconds() / 3600, 2),
            })
    rows.sort(key=lambda r: (r["date"], r["name"], r["start"]))
    return rows


# Демо-бригада для мини-аппа: те же люди, что в демо-зарплате, + плановая смена.
_DEMO_TEAM = [
    {"id": "d1", "name": "Виктория Ким", "start": "10:00", "end": "18:00"},
    {"id": "d2", "name": "Артём Волков", "start": "12:00", "end": "20:00"},
    {"id": "d3", "name": "Асан Нуров", "start": "09:00", "end": "17:00"},
]


def _demo_attendance(from_: str, to: str) -> Dict[str, Any]:
    """Детерминированный журнал явок за период: будни, лёгкие опоздания и редкая
    переработка — чтобы «план vs факт» в мини-аппе было что показать."""
    def _hm(t: str) -> int:
        h, m = t.split(":"); return int(h) * 60 + int(m)
    try:
        d0 = datetime.fromisoformat(from_[:10]).date()
        d1 = datetime.fromisoformat(to[:10]).date()
    except Exception:
        d1 = local_now().date(); d0 = d1 - timedelta(days=6)
    rows: List[Dict[str, Any]] = []
    day = d0
    while day <= d1:
        if day.weekday() != 6:  # без воскресенья
            for i, m in enumerate(_DEMO_TEAM):
                late = (day.toordinal() * 7 + i * 5) % 13  # 0..12 мин
                sh = int(m["start"][:2])
                start = f"{sh:02d}:{late:02d}"
                eh, em = int(m["end"][:2]), int(m["end"][3:])
                over = (day.toordinal() + i) % 4 == 0
                end = f"{(eh + (1 if over else 0)):02d}:{em:02d}"
                rows.append({
                    "employee_id": m["id"], "name": m["name"],
                    "date": day.isoformat(), "start": start, "end": end,
                    "hours": round((_hm(end) - _hm(start)) / 60, 2),
                })
        day += timedelta(days=1)
    rows.sort(key=lambda r: (r["date"], r["name"], r["start"]))
    return {"employees": [{"id": m["id"], "name": m["name"]} for m in _DEMO_TEAM],
            "rows": rows}


@app.get("/api/attendance/{org_id}")
async def attendance(org_id: str,
                     from_: str = Query(..., alias="from"), to: str = Query(...),
                     user: Dict = Depends(get_current_user)):
    """Журнал явок (факт из iiko) + список сотрудников для расписания.
    План (расписание) хранится на стороне клиента — здесь только факт."""
    if _use_demo_data(user, org_id):
        return _demo_attendance(from_, to)
    real_org = _get_org_id(org_id, user)
    info = _get_restaurant_info(real_org, user)
    client = get_client(info["api_login"], info.get("iiko_server"), info.get("api_password"))
    if not hasattr(client, "get_attendance"):
        raise HTTPException(status_code=400, detail="Явки доступны только на самохостед iiko")
    today = local_now().date().isoformat()
    calls = [client.get_attendance(from_, to), client.get_employees_map()]
    # Плановый график iiko отдаётся только при запросе С СЕГОДНЯ вперёд. Если период
    # захватывает будущее — тянем плановые смены отдельно и мержим (dedupe).
    want_future = to >= today
    if want_future:
        calls.append(client.get_attendance(today, to))
    results = await asyncio.gather(*calls)
    att, employees = results[0], results[1]
    if want_future:
        seen = {(a.get("employeeId"), a.get("dateFrom")) for a in att}
        for a in results[2]:
            k = (a.get("employeeId"), a.get("dateFrom"))
            if k not in seen:
                att.append(a); seen.add(k)
    rows = _attendance_journal(att, employees, info.get("org_name", ""))
    # роспись филиала = сотрудники, у кого есть явки/график в этом филиале за период
    branch_ids = {r["employee_id"] for r in rows}
    emp_list = [{"id": eid, "name": employees.get(eid, eid[:8])} for eid in branch_ids]
    emp_list.sort(key=lambda x: x["name"])
    return {"employees": emp_list, "rows": rows}


@app.get("/api/salary/{org_id}")
async def salary(org_id: str, response: Response,
                 from_: str = Query(..., alias="from"), to: str = Query(...),
                 user: Dict = Depends(get_current_user)):
    """Часы сотрудников филиала из явок iiko × ставка (хранится у нас)."""
    if _use_demo_data(user, org_id):
        # согласовано с демо-явками: часы за период × демо-ставка (та же бригада)
        att = _demo_attendance(from_, to)
        rates = {"d1": 900, "d2": 800, "d3": 850}
        agg: Dict[str, Dict[str, Any]] = {}
        for r in att["rows"]:
            a = agg.setdefault(r["employee_id"], {"name": r["name"], "hours": 0.0, "days": set()})
            a["hours"] += r["hours"]; a["days"].add(r["date"])
        return {"rows": [
            {"employee_id": eid, "name": v["name"], "hours": round(v["hours"], 1),
             "days": len(v["days"]), "rate": rates.get(eid, 0),
             "total": round(v["hours"] * rates.get(eid, 0)),
             "suspicious": False, "overridden": False}
            for eid, v in agg.items()
        ], "period_key": f"demo:{from_}:{to}"}
    real_org = _get_org_id(org_id, user)
    info = _get_restaurant_info(real_org, user)
    client = get_client(info["api_login"], info.get("iiko_server"), info.get("api_password"))
    if not hasattr(client, "get_attendance"):
        raise HTTPException(status_code=400, detail="Явки доступны только на самохостед iiko")
    from database import get_salary_rates
    attendance, employees = await asyncio.gather(
        client.get_attendance(from_, to),
        client.get_employees_map(),
    )
    org_name = info.get("org_name", "")
    # Явки в iiko бывают задвоены (несколько ролей одновременно) — объединяем
    # пересекающиеся интервалы каждого сотрудника, чтобы часы не удваивались.
    intervals: Dict[str, list] = {}
    for a in attendance:
        if org_name and a["departmentName"] != org_name:
            continue
        try:
            df = datetime.fromisoformat(a["dateFrom"])
            dt_ = datetime.fromisoformat(a["dateTo"])
            if dt_ > df:
                intervals.setdefault(a["employeeId"], []).append((df, dt_))
        except Exception:
            continue
    from database import get_salary_overrides
    period_key = f"{real_org}:{from_}:{to}"
    overrides = get_salary_overrides(period_key)
    rates = get_salary_rates()
    rows = []
    for eid, ivs in intervals.items():
        ivs.sort()
        merged = [list(ivs[0])]
        for df, dt_ in ivs[1:]:
            if df <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], dt_)
            else:
                merged.append([df, dt_])
        h = sum((b - a).total_seconds() for a, b in merged) / 3600
        days = len({a.date() for a, b in merged})
        rate = rates.get(eid, 0)
        ov = overrides.get(eid)
        rows.append({"employee_id": eid, "name": employees.get(eid, eid[:8]),
                     "hours": round(h, 1), "days": days,
                     "suspicious": days > 0 and h / days > 16,
                     "rate": rate,
                     "total": round(ov) if ov is not None else round(h * rate),
                     "overridden": ov is not None})
    rows.sort(key=lambda x: -x["hours"])
    return {"rows": rows, "period_key": period_key}


class SalaryRateIn(BaseModel):
    org_id: str
    employee_id: str
    rate: float


class SalaryOverrideIn(BaseModel):
    org_id: str
    employee_id: str
    period_key: str
    amount: Optional[float] = None  # None = убрать ручную правку


# ВНИМАНИЕ (остаточный зазор, задокументирован): таблицы salary_rates/salary_overrides
# ключуются только по employee_id (без org). Проверки ниже не дают писать демо-юзеру
# и не-владельцу филиала, но владелец org A, узнавший employee_id из org B (iiko-UUID,
# не перечисляем), технически может записать ставку для чужого сотрудника. Полный фикс —
# добавить org_id в схему salary и/или проверять employee_id ∈ employees(real_org).
@app.post("/api/salary/rate")
async def salary_rate(payload: SalaryRateIn, user: Dict = Depends(get_current_user)):
    real_org = _get_org_id(payload.org_id, user)  # 404, если вызывающий не владелец
    if real_org == "demo" or _is_demo_user(user):
        raise HTTPException(status_code=403, detail="Salary write not allowed in demo")
    from database import set_salary_rate
    set_salary_rate(payload.employee_id, payload.rate)
    return {"ok": True}


@app.post("/api/salary/override")
async def salary_override(payload: SalaryOverrideIn, user: Dict = Depends(get_current_user)):
    """Ручная правка итоговой зарплаты за период (None — вернуть авторасчёт)."""
    real_org = _get_org_id(payload.org_id, user)  # 404, если вызывающий не владелец
    if real_org == "demo" or _is_demo_user(user):
        raise HTTPException(status_code=403, detail="Salary write not allowed in demo")
    from database import set_salary_override
    set_salary_override(payload.employee_id, payload.period_key, payload.amount)
    return {"ok": True}


# «к прошлому понедельнику», … — дательный падеж для строки выручки в сводке
RU_DOW_PREV = ["прошлому понедельнику", "прошлому вторнику", "прошлой среде",
               "прошлому четвергу", "прошлой пятнице", "прошлой субботе",
               "прошлому воскресенью"]


async def _send_daily_shifts_report() -> bool:
    """Сводка по сменам всех филиалов за вчера — владельцам в Telegram.
    Плюс выручка за вчера против того же дня недели назад (сб к сб, а не к пт —
    иначе после выходных всегда ложное «падение»)."""
    from database import get_all_restaurants
    rests = get_all_restaurants()
    if not rests:
        return False
    yd = (local_now() - timedelta(days=1)).date()
    yesterday = yd.isoformat()
    week_ago = (yd - timedelta(days=7)).isoformat()
    by_owner: Dict[int, list] = {}
    for r in rests:
        by_owner.setdefault(r["telegram_id"], []).append(r)
    for tg_id, owner_rests in by_owner.items():
        lines = [f"🌙 <b>Кассовые смены за {yesterday}</b>"]
        problems = 0
        rev_cur = rev_prev = 0.0
        got_prev = False
        for r in owner_rests:
            try:
                info = {"api_login": r["api_login"], "iiko_server": r.get("iiko_server"),
                        "api_password": r.get("api_password"), "org_name": r.get("organization_name", "")}
                # get_all_restaurants уже возвращает расшифрованные пароли
                client = get_client(info["api_login"], info.get("iiko_server"),
                                    info.get("api_password"))
                if not hasattr(client, "get_cashshifts"):
                    continue
                dept = r.get("organization_name", "")
                sessions = await _branch_sessions(client, dept, yesterday, yesterday)
                for s in sessions:
                    diff = float(s.get("cashDiff") or 0)
                    total = float(s.get("payOrders") or 0)
                    rev_cur += total
                    name = (r.get("organization_name") or "")[:28]
                    if abs(diff) > 0.01:
                        problems += 1
                        lines.append(f"❌ {name}: смена №{s.get('sessionNumber')} — "
                                     f"итого {format_money(total)}, расхождение {format_money(diff)}")
                    else:
                        lines.append(f"✅ {name}: №{s.get('sessionNumber')} — {format_money(total)}")
                try:
                    prev_sessions = await _branch_sessions(client, dept, week_ago, week_ago)
                    rev_prev += sum(float(s.get("payOrders") or 0) for s in prev_sessions)
                    got_prev = got_prev or bool(prev_sessions)
                except Exception as e:
                    logger.warning(f"[daily_shifts] прошлая неделя {r.get('organization_name')}: {e}")
            except Exception as e:
                logger.warning(f"[daily_shifts] {r.get('organization_name')}: {e}")
        if len(lines) > 1:
            lines.insert(1, f"Проблемных смен: {problems}" if problems else "Все смены без расхождений 👌")
            rev_line = f"\n💰 Выручка вчера: <b>{format_money(rev_cur)}</b>"
            if got_prev:
                rev_line += f"  {_fmt_delta(rev_cur, rev_prev)} к {RU_DOW_PREV[yd.weekday()]}"
            lines.append(rev_line)
            try:
                async with httpx.AsyncClient(timeout=15.0) as c:
                    await c.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                                 json={"chat_id": tg_id, "text": "\n".join(lines[:60]),
                                       "parse_mode": "HTML"})
            except Exception as e:
                logger.warning(f"[daily_shifts] send {tg_id}: {e}")
    return True


async def _daily_shifts_scheduler():
    """Каждую ночь после 00:05 — сводка смен за прошедший день."""
    while True:
        try:
            now = local_now()
            if now.hour == 0 and now.minute >= 5 or now.hour >= 1:
                today_str = now.date().isoformat()
                if get_app_state("daily_shifts_last_sent") != today_str:
                    if await _send_daily_shifts_report():
                        set_app_state("daily_shifts_last_sent", today_str)
                        logger.info(f"[daily_shifts] отправлена за {today_str}")
        except Exception as e:
            logger.warning(f"[daily_shifts] цикл: {e}")
        await asyncio.sleep(600)


@app.get("/api/cashshifts_pending/{org_id}")
async def cashshifts_pending(org_id: str, days: int = 30,
                             user: Dict = Depends(get_current_user)):
    """Непринятые/незакрытые смены филиала за последние N дней (для счётчика)."""
    if _use_demo_data(user, org_id):
        return {"count": 3, "items": [
            {"date": "2026-06-10", "number": 41, "status": "UNACCEPTED", "open_now": False},
            {"date": "2026-06-09", "number": 40, "status": "UNACCEPTED", "open_now": False},
            {"date": "2026-06-08", "number": 39, "status": "OPEN", "open_now": True},
        ]}
    real_org = _get_org_id(org_id, user)
    info = _get_restaurant_info(real_org, user)
    client = get_client(info["api_login"], info.get("iiko_server"), info.get("api_password"))
    if not hasattr(client, "get_cashshifts"):
        raise HTTPException(status_code=400, detail="Только для самохостед iiko")
    cache_key = f"cs_pending:{real_org}:{days}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached
    date_to = local_now().date().isoformat()
    date_from = (local_now() - timedelta(days=days)).date().isoformat()
    sessions = await _branch_sessions(client, _olap_dept(real_org, info), date_from, date_to)
    items = []
    for s in sessions:
        if s.get("sessionStatus") == "ACCEPTED":
            continue
        items.append({"date": (s.get("openDate") or "")[:10],
                      "number": s.get("sessionNumber"),
                      "status": s.get("sessionStatus"),
                      "open_now": not s.get("closeDate")})
    items.sort(key=lambda x: x["date"], reverse=True)
    resp = {"count": len(items), "items": items}
    _set_cached(cache_key, resp)
    return resp


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
