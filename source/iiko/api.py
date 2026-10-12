import asyncio
import logging
import httpx
import os
import hmac
import hashlib
import base64
import html
import json
import base64
import csv
import re
import time
import calendar
from difflib import SequenceMatcher
from datetime import datetime, timedelta, date, timezone
from difflib import SequenceMatcher
from enum import Enum
from typing import Optional, List, Dict, Any, Tuple
from uuid import uuid4
from fastapi import FastAPI, Header, HTTPException, Request, Depends, Query
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse, RedirectResponse
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
    save_iiko_products, load_iiko_products, iiko_products_synced_at,
    list_invoice_match_memory, forget_invoice_match,
    iiko_products_stats, remember_invoice_match, get_invoice_match_memory,
    get_demo_invoice_count, inc_demo_invoice_count,
    start_demo_trial, get_demo_trial,
    get_trials_due_for_sales, mark_trial_sales_sent,
    get_restaurants, get_owned_restaurants, decrypt_password,
    add_restaurant, delete_restaurant, delete_all_restaurants,
    wipe_demo_user,
    add_manual_expense, get_manual_expenses, delete_manual_expense,
    create_bot_task, answer_bot_task, get_bot_tasks_all,
    add_branch_manager, get_branch_managers,
    save_journal_entry, get_journal_entries, seen_photo, remember_photo,
    set_weekly_plan, get_weekly_plans,
    get_signal_snapshot, save_signal_snapshot,
    get_branch_managers, set_branch_manager,
    upsert_director_task, close_director_task, get_director_tasks,
    get_bot_tasks,
    save_bot_invoice, get_bot_invoices, get_bot_invoice,
    update_bot_invoice, find_invoice_by_dedup, get_price_history,
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
import assistant_ai
import social_ig
import staff_late as SL
from foodcost_calc import compute_recipe_foodcost

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

IS_PROD = os.getenv("ENVIRONMENT") == "production"

# Swagger отключён в production
app = FastAPI(
    title="iikoBoss Analytics",
    version="1.0.0",
    docs_url=None if IS_PROD else "/docs",
    redoc_url=None if IS_PROD else "/redoc",
)

# Rate limiter
def _client_ip(request: Request) -> str:
    """Адрес ПОСЕТИТЕЛЯ, а не прокси Render.

    uvicorn на проде запущен без --proxy-headers, поэтому request.client.host —
    это внутренний адрес прокси, один и тот же у всех. На нём лимитер считал
    всех посетителей как одного: пять попыток кем угодно закрывали форму
    регистрации для всего мира на пятнадцать минут.

    Берём ПОСЛЕДНЮЮ запись X-Forwarded-For: её дописывает прокси по адресу
    сокета. Подделать заголовок можно, но подделка окажется левее — и в ключ
    не попадёт.
    """
    xff = request.headers.get("X-Forwarded-For") or ""
    parts = [p.strip() for p in xff.split(",") if p.strip()]
    if parts:
        return parts[-1]
    return get_remote_address(request)


limiter = Limiter(key_func=_client_ip)
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

# Виджет на домашний экран (личная ссылка /w/{token}) — отдельный роутер
import widget_routes
app.include_router(widget_routes.router)
import journal_routes
app.include_router(journal_routes.router)

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
# Одна формулировка на все ручки КДС, чтобы экран и бот говорили одно и то же.
KDS_ONLY_DETAIL = "Раздел для пользователей нашего КДС"
CHASPIK_BASE_URL = os.getenv("CHASPIK_BASE_URL", "https://saya-kds-irlg.onrender.com")
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
# Выручка текущего дня растёт весь день, и трёхминутный кэш делал её заметно
# «отстающей». Для периодов, включающих сегодня, держим ответ секунды, а не
# минуты; на iiko это по-прежнему не больше одного запроса в _CACHE_TTL_LIVE,
# сколько бы раз фронт ни спросил.
_CACHE_TTL_LIVE = timedelta(seconds=int(os.getenv("CACHE_TTL_LIVE_SEC", "45")))
_cache_ttl: Dict[str, timedelta] = {}


def _is_live_period(date_from: str, date_to: str) -> bool:
    """Период захватывает сегодняшний день — значит цифра ещё меняется."""
    return str(date_to)[:10] >= local_now().date().isoformat()


# Насколько оплаченное может превысить проданное, прежде чем считать выборку
# смен загрязнённой. Небольшой запас — на округления и правки при приёмке смены.
SHIFT_SANITY_RATIO = float(os.getenv("SHIFT_SANITY_RATIO", "1.02"))
# Совет ассистента меняется не чаще раза в день — держим его отдельно и долго,
# иначе каждый заход на сайт оплачивается новым вызовом модели.
ASSISTANT_TTL = timedelta(hours=int(os.getenv("ASSISTANT_TTL_HOURS", "12")))


def _cache_key(user_id: int, endpoint: str, org_id: str, period: str) -> str:
    return f"{user_id}:{endpoint}:{org_id}:{period}"


def _get_cached(key: str, ttl: Optional[timedelta] = None) -> Optional[Any]:
    if key in _cache and key in _cache_time:
        if datetime.now() - _cache_time[key] < (ttl or _cache_ttl.get(key) or _CACHE_TTL):
            return _cache[key]
    return None


def _set_cached(key: str, value: Any, ttl: Optional[timedelta] = None):
    # Перед записью чистим протухшее и держим потолок размера — иначе _cache рос
    # бесконечно (ключи: user×org×период×эндпоинт) и тёк по памяти до рестарта.
    now = datetime.now()
    if len(_cache) > 500:
        for k in [k for k, t in list(_cache_time.items())
                  if now - t >= _cache_ttl.get(k, _CACHE_TTL)]:
            _cache.pop(k, None)
            _cache_time.pop(k, None)
            _cache_ttl.pop(k, None)
        if len(_cache) > 500:  # всё ещё много — сносим самые старые
            for k in sorted(_cache_time, key=_cache_time.get)[:len(_cache) - 500]:
                _cache.pop(k, None)
                _cache_time.pop(k, None)
                _cache_ttl.pop(k, None)
    _cache[key] = value
    _cache_time[key] = now
    if ttl:
        _cache_ttl[key] = ttl
    else:
        _cache_ttl.pop(key, None)


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


# Когда последний раз отмечали заход каждого. Экран открывает десятки запросов
# подряд, и писать в базу на каждый — впустую: для «активен за 7 дней» хватает
# точности в полчаса.
_SEEN_EVERY = 1800
_seen_at: Dict[int, float] = {}


def _mark_seen(uid: int):
    if not uid:
        return
    now = time.time()
    if now - _seen_at.get(uid, 0) < _SEEN_EVERY:
        return
    _seen_at[uid] = now
    try:
        from database import touch_acquisition
        touch_acquisition(uid)
    except Exception as e:
        logger.warning(f"[acq] touch {uid}: {e!r}")


async def get_current_user(x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data"),
                           x_site_token: Optional[str] = Header(None, alias="X-Site-Token")):
    """Кто спрашивает. Три пути входа, по убыванию строгости.

    1. `X-Telegram-Init-Data` — подпись Telegram, как у мини-аппа.
    2. `X-Site-Token` — личная ссылка сайта (`?m=…`), подписанная на
       ENCRYPTION_KEY. Без неё сайт на проде отвечал бы 401 на КАЖДЫЙ запрос:
       он не живёт внутри Telegram и init-data взять негде. Токен даёт ровно ту
       же личность, что и uid в подписи, дальше работает обычная изоляция по
       филиалам — новых прав он не открывает.
    3. dev-режим без бота: DEV_TID. Только когда BOT_TOKEN пуст.
    """
    if x_telegram_init_data:
        u = validate_telegram_init_data(x_telegram_init_data)
        _mark_seen(int(u.get("id") or 0))
        return u
    if x_site_token:
        import mountain_routes
        sc = mountain_routes.scope_of_token(x_site_token)
        if sc.get("role") == "owner":
            _mark_seen(int(sc["uid"]))
            return {"id": sc["uid"], "username": "site", "first_name": "Site"}
        if sc.get("role") and sc.get("org"):
            # Ссылка ФИЛИАЛА. В iiko ходим от имени владельца — своих кредов у
            # филиала нет, — но доступ прибит к одному org: `only_org` проверяет
            # _get_org_id, единственная точка, через которую экраны получают филиал.
            return {"id": sc["uid"], "username": "site", "first_name": "Site",
                    "only_org": sc["org"]}
        raise HTTPException(status_code=401, detail="Ссылка недействительна")
    if not BOT_TOKEN:
        # dev-режим без Telegram: DEV_TID позволяет действовать как владелец
        # (видеть его реальные филиалы из локальной БД). По умолчанию 0.
        dev_tid = int(os.getenv("DEV_TID", "0") or 0)
        return {"id": dev_tid, "username": "dev", "first_name": "Developer"}
    raise HTTPException(status_code=401, detail="Missing init data")


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
    "total": 26640000,
    "operations": [
        {"name": "Зарплата официантам и поварам", "cat": "ФОТ (зарплаты)", "date": "25.03.2026", "kassa": "Касса руководства", "amount": 6400000},
        {"name": "Аренда помещения", "cat": "Аренда", "date": "01.03.2026", "kassa": "Касса руководства", "amount": 4757000},
        {"name": "Налоги", "cat": "Налоги", "date": "20.03.2026", "kassa": "Касса руководства", "amount": 2854000},
        {"name": "Роялти франчайзеру", "cat": "Роялти (франшиза)", "date": "05.03.2026", "kassa": "Касса руководства", "amount": 2379000},
        {"name": "Электричество и вода", "cat": "Коммуналка", "date": "15.03.2026", "kassa": "Основная касса", "amount": 1189000},
        {"name": "Комиссия эквайринга", "cat": "Эквайринг", "date": "28.03.2026", "kassa": "Основная касса", "amount": 951000},
        {"name": "Реклама в соцсетях", "cat": "Маркетинг", "date": "22.03.2026", "kassa": "Основная касса", "amount": 951000},
        {"name": "Хозтовары и уборка", "cat": "Хозрасходы", "date": "18.03.2026", "kassa": "Основная касса", "amount": 714000},
    ],
    "categories": [
        {"name": "ФОТ (зарплаты)", "amount": 12845000},
        {"name": "Аренда", "amount": 4757000},
        {"name": "Налоги", "amount": 2854000},
        {"name": "Роялти (франшиза)", "amount": 2379000},
        {"name": "Коммуналка", "amount": 1189000},
        {"name": "Эквайринг", "amount": 951000},
        {"name": "Маркетинг", "amount": 951000},
        {"name": "Хозрасходы", "amount": 714000},
    ]
}

DEMO_INVOICES = [
    {"id": "0013", "status": "Проведена", "supplier": "ТОО АгроПром", "date": "08.03.2026", "items": 3, "amount": 279850,
     "positions": [{"name": "Говядина вырезка", "qty": 20, "unit": "кг", "price": 9500, "sum": 190000},
                   {"name": "Овощи микс", "qty": 45, "unit": "кг", "price": 1300, "sum": 58500},
                   {"name": "Зелень свежая", "qty": 21, "unit": "кг", "price": 1493, "sum": 31350}]},
    {"id": "0014", "status": "Проведена", "supplier": "ТОО МясоОпт", "date": "09.03.2026", "items": 2, "amount": 185000,
     "positions": [{"name": "Куриное филе", "qty": 60, "unit": "кг", "price": 1950, "sum": 117000},
                   {"name": "Фарш говяжий", "qty": 40, "unit": "кг", "price": 1700, "sum": 68000}]},
    {"id": "0015", "status": "Проведена", "supplier": "ИП Фермер", "date": "10.03.2026", "items": 2, "amount": 92500,
     "positions": [{"name": "Молоко 3.2%", "qty": 100, "unit": "л", "price": 450, "sum": 45000},
                   {"name": "Сыр моцарелла", "qty": 25, "unit": "кг", "price": 1900, "sum": 47500}]},
    {"id": "0016", "status": "Новая", "supplier": "ТОО АгроПром", "date": "12.03.2026", "items": 1, "amount": 312400,
     "positions": [{"name": "Стейк рибай (отруб)", "qty": 40, "unit": "кг", "price": 7810, "sum": 312400}]},
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
        # «Пришло» и «пользуется» — разные числа, и без второго первое ничего
        # не говорит: тысяча открывших ссылку и ноль подключивших iiko — провал,
        # а выглядело бы победой.
        conv = round(100 * a["connected"] / a["total"]) if a["total"] else 0
        return (
            f"👥 Юзеры\n"
            f"Пришло: {a['total']} · сегодня: {a['today']} · за неделю: {a['week']}\n"
            f"Подключили iiko: {a['connected']} ({conv}%)\n"
            f"Активных за 7 дней: {a['active']}\n"
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
    cloud_api_login: Optional[str] = None  # ключ iiko Cloud для живого зала/стоп-листов


class SiteSignup(SetupSave):
    """Регистрация с сайта: те же поля подключения плюс телеграм для уведомлений."""
    tg_username: str


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
    Лучше не подняться, чем тихо работать дырявым. Webhook-секрет — громкое
    предупреждение (его отсутствие открывает вебхук, но не валит прод)."""
    if not IS_PROD:
        return
    missing = []
    if not BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN (без него авторизация выключена)")
    if not os.getenv("ENCRYPTION_KEY"):
        missing.append("ENCRYPTION_KEY (без него пароли iiko в БД открытым текстом)")
    if missing:
        raise RuntimeError("Отказ запуска (fail-closed): не заданы " + "; ".join(missing))
    if not TELEGRAM_WEBHOOK_SECRET:
        logger.warning("[security] TELEGRAM_WEBHOOK_SECRET не задан — вебхук бота "
                       "принимает запросы без проверки. Задайте секрет в проде.")


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
    # Утренняя сводка: вчерашняя выручка, сравнения, прогноз месяца, стоп-лист
    asyncio.create_task(_morning_report_scheduler())
    # Триггеры в течение дня: касса молчит / выручка проваливается
    asyncio.create_task(_trigger_scheduler())
    # Журналы: задание на слот тем, кто ещё не сдал
    asyncio.create_task(_journal_scheduler())
    # Ежедневная сводка владельцу — на ОБОИХ деплоях: демо шлёт юзеров,
    # основной — квоты накладных (см. _build_stats_text, БД у сервисов разные).
    asyncio.create_task(_daily_stats_scheduler())
    # Ночная оценка филиалов + вторничная средняя
    asyncio.create_task(_daily_scores_scheduler())
    # Опоздания сотрудников: управляющему сразу, владельцу — сводка в 21:00.
    # За рубильником: без реальных telegram id управляющих слать некому.
    if os.getenv("LATE_ALERTS") == "1":
        asyncio.create_task(_late_alerts_scheduler())
    # «Завтра у вас смена» — сотрудникам, привязавшим личную ссылку
    asyncio.create_task(_shift_reminders_scheduler())
    if DEMO_ONLY:
        asyncio.create_task(_demo_sales_scheduler())


# ── Сайт ──
# Раньше по «/» лежал мини-апп (index.html, 5 400 строк одним файлом). Теперь
# отдаём собранный сайт из web/dist. Он попадает в репозиторий собранным:
# у Render'а в этом сервисе Python-окружение без Node, собирать там нечем.
SITE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "dist")

if os.path.isdir(os.path.join(SITE_DIR, "assets")):
    app.mount("/assets", StaticFiles(directory=os.path.join(SITE_DIR, "assets")),
              name="site-assets")


@app.get("/")
async def root():
    # no-store — иначе Telegram-webview держит старый бандл и не подхватывает
    # новые правки фронта (показывал старую выручку из старого index.html).
    index = os.path.join(SITE_DIR, "index.html")
    if not os.path.exists(index):
        # Честная ошибка вместо белого экрана: значит, сайт не собран.
        return HTMLResponse(
            "<meta charset='utf-8'><body style='font:15px system-ui;padding:40px'>"
            "Сайт не собран: нет <code>web/dist</code>. Собрать: "
            "<code>cd web &amp;&amp; npm ci &amp;&amp; npm run build</code>, "
            "затем закоммитить dist.</body>", status_code=503)
    return FileResponse(index, headers={"Cache-Control": "no-store, must-revalidate"})



@app.get("/m/{token}")
async def personal_link(token: str):
    """Личная ссылка владельца и филиала.

    `/api/mountain/links` раздаёт ссылки именно в таком виде, а маршрута под них
    не было: catch-all в конце файла префикс `m/` исключает, и каждая розданная
    ссылка отвечала 404 — на сайт по ней было не попасть. Сам сайт читает токен
    из `?m=` (lib/api.ts шлёт его как X-Site-Token), поэтому путь переводим в
    запрос, а не отдаём index.html молча: иначе токен потерялся бы по дороге.
    """
    return RedirectResponse(f"/?m={token}", status_code=307)


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
# Проверка доступов — это набор адреса и пароля руками, с опечатками. Пять
# попыток человек тратит, ещё не дойдя до своей организации.
@limiter.limit("30/15 minutes")
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


@app.post("/api/signup")
# Тут лимит строже: ручка создаёт аккаунты. Но считается он теперь по адресу
# посетителя, а не по прокси, поэтому чужие попытки соседа не задевают.
@limiter.limit("10/hour")
async def site_signup(request: Request, payload: SiteSignup):
    """Регистрация прямо с сайта, без Telegram.

    Человек приходит по ссылке, вводит доступы к своему iiko и телеграм для
    уведомлений — и сразу видит СВОИ цифры. Телеграм-id у него на этот момент
    ещё нет, поэтому аккаунт заводится со своим (отрицательным) и переезжает на
    настоящий, когда человек напишет боту: `claim_web_signup` в /start.

    Единственный эндпоинт без `get_current_user`: личности у пришедшего ещё нет,
    он её здесь и получает. Поэтому лимит по адресу обязателен — иначе это
    открытая ручка, создающая аккаунты, — а доступы проверяются в iiko ДО
    записи: не подошли, значит и аккаунта заводить незачем.
    """
    from database import normalize_username, web_signup
    uname = normalize_username(payload.tg_username)
    if not uname or not re.fullmatch(r"[A-Za-z0-9_]{4,32}", uname):
        raise HTTPException(status_code=400, detail="Укажите телеграм вида @username")
    try:
        client = get_client(payload.api_login, payload.iiko_server, payload.api_password)
        orgs = await client.get_organizations()
    except Exception as e:
        logger.warning(f"[signup] проверка доступов: {e!r}")
        raise HTTPException(status_code=400, detail="iiko не принял эти доступы")
    if payload.organization_id not in {o["id"] for o in orgs}:
        raise HTTPException(status_code=400, detail="Этой организации нет у ваших доступов")
    uid, code = web_signup(uname, payload.organization_name)
    add_restaurant(uid, payload.api_login, payload.organization_id, payload.organization_name,
                   payload.iiko_server, payload.api_password, payload.cloud_api_login)
    import mountain_routes as _mr
    token = _mr.token_for(uid)
    try:
        await _tg_send(OWNER_CHAT_ID,
                       f"🆕 Регистрация с сайта: @{html.escape(uname)}\n"
                       f"{html.escape(payload.organization_name)}")
    except Exception as e:
        logger.warning(f"[signup] owner notify: {e!r}")
    # Телеграм подтверждается ОДНОРАЗОВЫМ кодом по ссылке бота, а не введённым
    # @username: имя публично, и привязка по нему означала бы, что любой заводит
    # аккаунт на чужое имя, а владелец этого имени забирает его первым /start.
    return {"success": True, "token": token, "url": f"/?m={token}",
            "claim_url": f"https://t.me/{BOT_USERNAME}?start=link_{code}"}


@app.post("/api/setup/save")
async def setup_save(payload: SetupSave, user: Dict = Depends(get_current_user)):
    tid = user.get("id", 0)
    create_user(tid, user.get("username"), user.get("first_name"))
    add_restaurant(tid, payload.api_login, payload.organization_id, payload.organization_name,
                   payload.iiko_server, payload.api_password, payload.cloud_api_login)
    set_demo_mode(tid, 0)
    return {"success": True}


def _safe_restaurant(r: Dict[str, Any]) -> Dict[str, Any]:
    """Только безопасные поля в браузер — БЕЗ api_password / cloud_api_login / api_login."""
    return {
        "id": r.get("id"),
        "organization_id": r.get("organization_id"),
        "organization_name": r.get("organization_name"),
        "has_cloud": bool(r.get("cloud_api_login")),  # подключён ли живой зал (Cloud)
    }


@app.get("/api/restaurants")
async def list_restaurants(user: Dict = Depends(get_current_user),
                           x_site_token: Optional[str] = Header(None, alias="X-Site-Token")):
    # Демо-ссылка: переключатель показывает выдуманную СЕТЬ. Без этого он отдавал
    # один «Демо Ресторан», экраны спрашивали данные по нему, и «Расписание» с
    # «Гора» жили в разных филиалах — сеть на доске была, а выбрать её нельзя.
    if x_site_token:
        import mountain_routes as _mr
        if _mr.demo_from_token(x_site_token):
            import demo as _demo
            return [{"id": i + 1, "organization_id": b["organization_id"],
                     "organization_name": b["name"], "has_cloud": False}
                    for i, b in enumerate(_demo.BRANCHES)]
    tid = user.get("id", 0)
    create_user(tid, user.get("username"), user.get("first_name"))
    rests = get_restaurants(tid)
    # По ссылке филиала список — из одного филиала: переключатель сети в шапке
    # не должен обещать доступ, которого нет.
    only = user.get("only_org")
    if only:
        rests = [r for r in rests if r.get("organization_id") == only]
    if not rests:
        # Не меняем is_demo флаг автоматически — только явный /api/demo его ставит
        return [_safe_restaurant(DEMO_RESTAURANT)]
    # Если есть реальные рестораны — сбрасываем demo флаг на всякий случай
    set_demo_mode(tid, 0)
    return [_safe_restaurant(r) for r in rests]


def _pseudo_section(name: str) -> bool:
    """Псевдо-секции (не настоящий зал): вынос, доставка, агрегаторы, касса."""
    n = (name or "").lower()
    # Кириллица обязательна: филиалы называют секции «Вольт» и «Глово», и без этих
    # написаний агрегаторы попадали в список ЗАЛОВ — с фейковыми столами без схемы.
    return any(k in n for k in ["вынос", "достав", "wolt", "glovo", "вольт", "глово",
                                "яндекс", "yandex", "касса", "самовывоз", "агрегат"])


_hall_cache: Dict[str, tuple] = {}  # org_id -> (ts, data)
_HALL_TTL = 20  # сек: живой зал кэшируем, чтобы не хаммерить Cloud и переживать флап сети


@app.get("/api/hall/{org_id}")
async def hall(org_id: str, user: Dict = Depends(get_current_user)):
    """Живой зал из iiko Cloud: секции+схема+занятость. Нужен cloud_api_login у филиала."""
    if _use_demo_data(user, org_id):
        return {"connected": False, "demo": True}
    # Ссылка филиала открывает ровно свой филиал: без этой строки зал СОСЕДНЕЙ
    # точки читался по чужой ссылке — единственная ручка, где проверка была
    # забыта совсем (остальные ходят через _get_org_id).
    _get_org_id(org_id, user)
    rests = get_restaurants(user.get("id", 0))
    r = next((x for x in rests if x.get("organization_id") == org_id), None)
    enc = r.get("cloud_api_login") if r else None
    if not enc:
        return {"connected": False}
    # свежий кэш — сразу
    cached = _hall_cache.get(org_id)
    if cached and (time.time() - cached[0]) < _HALL_TTL:
        return cached[1]
    last_err = ""
    for _attempt in range(2):  # ретрай на флап сети до Cloud
      try:
        client = get_client(decrypt_password(enc))  # кэш-клиент: токен переиспользуется (не хаммерим /access_token)
        org_name = (r.get("organization_name") or "").strip().lower()
        orgs = await client.get_organizations()
        cloud = next((o for o in orgs if (o.get("name") or "").strip().lower() == org_name), None)
        if not cloud:
            return {"connected": False, "error": "Организация не найдена в iiko Cloud"}
        coid = cloud["id"]
        tgs = await client.terminal_groups([coid])
        secs = await client.restaurant_sections(tgs)
        all_ids = [t["id"] for s in secs for t in s.get("tables", [])]
        orders = await client.hall_orders(coid, all_ids, tgs[0] if tgs else None)
        busy, guests_by, sum_by = {}, {}, {}
        for o in orders:
            oo = o.get("order", {}) or {}
            g = oo.get("guestCount") or oo.get("guestsCount") or 0
            sm = oo.get("sum") or 0
            for tid in (oo.get("tableIds") or []):
                busy[tid] = True
                guests_by[tid] = g
                sum_by[tid] = sm
        tables, channels = [], []
        real_sec_ids = []  # id настоящих залов (для запроса броней)
        for s in secs:
            sname = s.get("name", "")
            if _pseudo_section(sname):
                cnt = sum(1 for t in s.get("tables", []) if busy.get(t["id"]))
                channels.append({"name": sname, "count": cnt})
                continue
            if s.get("id"):
                real_sec_ids.append(s["id"])
            schema = s.get("schema") or {}
            elems = {e.get("tableId"): e for e in schema.get("tableElements", [])}
            # iiko отдаёт tableElements, но НЕ отдаёт размер холста. Прежние 810×550
            # были выдумкой: у реальных схем столы стоят на y=550 при высоте 80, то
            # есть вылезают за холст — на плане они уезжали за пол. Считаем габариты
            # по самим элементам (правый-нижний край + поле), тогда план сходится.
            # Заявленный размер холста бывает МЕНЬШЕ расстановки: у Г.Алиева schema
            # 810×550, а стол стоит на y=550 при высоте 80 — нижний край 630. Берём
            # максимум из заявленного и фактического, иначе столы уезжают за пол.
            pad = 40
            right = [(e.get("x") or 0) + (e.get("width") or 0) for e in elems.values()]
            bottom = [(e.get("y") or 0) + (e.get("height") or 0) for e in elems.values()]
            sw = max(schema.get("width") or 0, (max(right) + pad) if right else 0) or 810
            sh = max(schema.get("height") or 0, (max(bottom) + pad) if bottom else 0) or 550
            for t in s.get("tables", []):
                tid = t["id"]
                el = elems.get(tid, {})
                tables.append({
                    "id": tid, "section": sname, "number": t.get("number"),
                    "seats": t.get("seatingCapacity") or 0,
                    "x": el.get("x"), "y": el.get("y"), "w": el.get("width"), "h": el.get("height"),
                    "angle": el.get("angle") or 0, "sw": sw, "sh": sh,
                    "busy": bool(busy.get(tid)), "guests": guests_by.get(tid, 0), "sum": sum_by.get(tid, 0),
                })
        occ = [t for t in tables if t["busy"]]

        # ── Бронь: показываем ТОЛЬКО если у организации включён модуль броней (reserve API).
        # Ошибка reserve не должна ронять зал → has_reservations=false, столы просто без метки.
        has_reservations = False
        reserved_ids = set()
        try:
            reserve_orgs = await client.reserve_available_orgs([coid])
            has_reservations = coid in reserve_orgs
            if has_reservations and real_sec_ids:
                _n = local_now()
                d0 = _n.strftime("%Y-%m-%d %H:%M:%S.000")
                d1 = _n.replace(hour=23, minute=59, second=59, microsecond=0).strftime("%Y-%m-%d %H:%M:%S.000")
                for rv in await client.reserve_workload(real_sec_ids, d0, d1):
                    tids = rv.get("tableIds") or ([rv["tableId"]] if rv.get("tableId") else [])
                    for tid in tids:
                        reserved_ids.add(tid)
        except Exception as _re:
            logger.warning(f"[hall] reserve for {org_id}: {_re!r}")
        for t in tables:
            # занятый стол важнее брони: если за ним уже сидят — это occ, а не «бронь».
            t["reserved"] = (not t["busy"]) and (t["id"] in reserved_ids)

        # Список секций для переключателя залов (только настоящие залы, без псевдо-секций).
        section_names = []
        for t in tables:
            if t["section"] not in section_names:
                section_names.append(t["section"])
        result = {
            "connected": True, "branch": r.get("organization_name"),
            "tables": tables, "channels": channels, "sections": section_names,
            "has_reservations": has_reservations,
            "reserved": sum(1 for t in tables if t["reserved"]),
            "guests": sum(t["guests"] for t in occ), "open_orders": len(orders),
            "occupied": len(occ), "total": len(tables),
            "load": round(len(occ) / len(tables) * 100) if tables else 0,
        }
        _hall_cache[org_id] = (time.time(), result)
        return result
      except Exception as e:
        last_err = repr(e)
        continue
    # обе попытки не удались — отдаём последний хороший кэш (если есть), иначе ошибку
    if cached:
        return cached[1]
    return {"connected": False, "error": last_err}


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
    - любой филиал с префиксом `demo-` → всегда демо. Сюда попадают и старый
      demo-org-1, и выдуманная сеть демо-ссылки (demo.BRANCHES). Реальные
      организации iiko — UUID, начаться с `demo-` не могут, так что префикс
      безопасен и не пускает демо-режим на живой филиал;
    - демо-юзер без активного триала → демо;
    - демо-юзер с активным триалом → НЕ демо (его реальные данные).
    На основном боте `_is_demo_user` = False → возвращает False без обращения к БД."""
    if str(org_id or "").startswith("demo-"):
        return True
    return _is_demo_user(user) and not _trial_active(int(user.get("id", 0) or 0))


def _get_org_id(org_id: str, user: Dict) -> str:
    # Ссылка филиала открывает ровно свой филиал. Проверка здесь, потому что
    # через эту функцию проходит КАЖДЫЙ экран: поставь её в самих экранах —
    # забудешь в одном, и ссылка откроет чужую точку.
    only = user.get("only_org")
    if only and org_id not in (only, "demo-org-1"):
        raise HTTPException(status_code=403, detail="Ссылка открывает только свой филиал")
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


# Опоздание больше этого — почти наверняка заказ, который забыли закрыть в iiko
# (у Алиева встречаются «опоздания» на 21 час). Не прячем, но помечаем.
LATE_SUSPICIOUS_MIN = 120

DEMO_LATE_DELIVERIES = {
    "delivered": 128, "late": 14, "late_pct": 11, "median_delay": 17,
    "worst_delay": 63, "suspicious": 0,
    "orders": [
        {"number": 412, "date": "2026-08-09", "courier": "Жумабек", "address": "ул. Кабанбай батыра 53",
         "expected": "09.08 19:40", "actual": "09.08 20:43", "delay_min": 63, "amount": 14200, "suspicious": False},
        {"number": 398, "date": "2026-08-08", "courier": "Рысдаулет", "address": "пр. Мангилик Ел 28",
         "expected": "08.08 13:10", "actual": "08.08 13:52", "delay_min": 42, "amount": 8900, "suspicious": False},
        {"number": 377, "date": "2026-08-07", "courier": "Жумабек", "address": "ул. Сыганак 10",
         "expected": "07.08 20:05", "actual": "07.08 20:28", "delay_min": 23, "amount": 21400, "suspicious": False},
    ],
    "period": {"from": "2026-07-12", "to": "2026-08-11"},
}


_SOURCE_RU = {"WEB": "сайт", "IOS": "iOS", "ANDROID": "Android",
              "MOBILE": "приложение", "CALLCENTER": "колл-центр"}


def _clean_address(v: Any, line1: Any = None) -> str:
    """Адрес доставки без мусора оператора.

    У заказов с сайта и приложения улица приходит заглушкой «ул. ---------- д. 0»,
    а настоящий адрес лежит в Delivery.Line1 — берём его. Если и там пусто,
    честнее сказать, что адреса нет, чем показывать прочерки.
    """
    s = " ".join(str(v or "").split())
    l1 = " ".join(str(line1 or "").split())
    if (not s or re.search(r"-{3,}", s)) and l1:
        # «г. Астана, ул. г. Астана, ул. …» — оператор дублирует префикс, чистим
        return re.sub(r"^(г\.\s*[^,]+,\s*ул\.\s*)(?=г\.\s)", "", l1).strip(" ,")
    if not s:
        return l1
    body = re.sub(r"^ул\.\s*", "", s, flags=re.I)          # «ул. » ничего не добавляет
    body = re.sub(r"\s*д\.\s*0\b", "", body)               # «д. 0» — дом не указан
    street = re.split(r"\s*д\.\s*", body)[0].strip(" .,-–—_")
    if not street or not re.search(r"[a-zA-Zа-яА-ЯёЁ0-9]", street):
        return ""                                          # одни прочерки — адреса нет
    return body.strip(" .,-") or ""


def _hhmm(v: Any) -> str:
    """'2026-07-15T13:36:15.644' → '15.07 13:36'. Пусто — прочерк."""
    s = str(v or "")
    if len(s) < 16 or "T" not in s:
        return "—"
    d, t = s[:10], s[11:16]
    return f"{d[8:10]}.{d[5:7]} {t}"


def _mins_between(a: Any, b: Any) -> Optional[float]:
    """Минуты между двумя отметками iiko. None — если хоть одной нет."""
    from datetime import datetime as _dt
    try:
        fmt = lambda v: _dt.fromisoformat(str(v)[:26])
        return (fmt(b) - fmt(a)).total_seconds() / 60.0
    except Exception:
        return None


# Ярлыки этапов. Их видит человек, поэтому формулируем как ответственность,
# а не как техническое название поля.
LATE_STAGES = {
    "kitchen": "кухня готовила дольше обычного",
    "idle": "заказ ждал курьера",
    "road": "дорога заняла дольше обычного",
    "accepted_late": "заказ приняли впритык",
    "unknown": "нет отметок времени",
}


def _late_stages(r: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """Опоздание по этапам: кухня, простой готового заказа, дорога."""
    open_t = r.get("OpenTime")
    cooked = r.get("Delivery.CookingFinishTime")
    sent = r.get("Delivery.SendTime")
    actual = r.get("Delivery.ActualTime")
    road = r.get("Delivery.WayDuration")
    return {
        "kitchen": _mins_between(open_t, cooked),
        "idle": _mins_between(cooked, sent),
        # WayDuration надёжнее разницы отметок: у части заказов ActualTime
        # проставляют вручную при закрытии, и разница уезжает в часы.
        "road": (float(road) if road not in (None, "") else _mins_between(sent, actual)),
    }


def _median(xs: List[float]) -> float:
    xs = sorted(x for x in xs if x is not None)
    return xs[len(xs) // 2] if xs else 0.0


def _attribute_cause(stages: Dict[str, Optional[float]],
                     norms: Dict[str, float], delay: float) -> Dict[str, Any]:
    """Кто съел время. Сравниваем каждый этап с НОРМОЙ ЭТОГО ЖЕ ФИЛИАЛА — так
    «долго» означает «дольше, чем у них обычно», а не абстрактный норматив."""
    excess = {k: max(0.0, (v or 0.0) - norms.get(k, 0.0))
              for k, v in stages.items() if v is not None}
    if not excess or max(excess.values()) <= 1:
        # Все этапы в своей норме, а заказ всё равно опоздал — значит обещали
        # срок, в который эта кухня и дорога не укладываются в принципе.
        key = "accepted_late" if any(v is not None for v in stages.values()) else "unknown"
        return {"key": key, "label": LATE_STAGES[key], "minutes": round(delay)}
    key = max(excess, key=excess.get)
    return {"key": key, "label": LATE_STAGES[key], "minutes": round(excess[key])}


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
            data = r.get("data", [])
            # Из этого же ответа берём валовую выручку филиала — она нужна ниже
            # как потолок вменяемости. Отдельный запрос ради неё не нужен.
            gross = sum(float(x.get("DishAmountSum") or 0) for x in data)
            return ({str(x.get("Conception.Code")) for x in data
                     if x.get("Conception.Code") is not None}, gross), None
        except Exception as e:
            return (set(), 0.0), str(e)[:160]

    # cashshifts и OLAP-коды концепции — параллельно (уменьшает лаг)
    sessions, ((codes, gross), codes_err) = await asyncio.gather(_fetch_sessions(), _fetch_codes())
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

    # Проверка вменяемости. Концепция — не филиал: на общем сервере две точки
    # могут делить одну концепцию, и тогда чужая смена целиком приезжает сюда.
    # Поймано на Калдаяковой: 588 821 своих + 458 470 чужих = 1 047 291, что
    # больше всей проданной выручки филиала (845 480). Оплатить больше, чем
    # продано, нельзя — значит выборка загрязнена, и брать её нельзя.
    picked = client.cashshifts_revenue(sel) or 0.0
    if gross > 0 and picked > gross * SHIFT_SANITY_RATIO:
        dbg["contaminated"] = {"picked": round(picked), "gross": round(gross)}
        logger.warning(f"[shifts] {dept}: смены дают {picked:,.0f} при продажах "
                       f"{gross:,.0f} — чужая смена в выборке, откат на OLAP")
        return None, dbg

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


@app.get("/api/widget/{org_id}")
async def widget(org_id: str, response: Response, user: Dict = Depends(get_current_user)):
    """Единый render-ready JSON для iOS-виджетов («Команды»/WidgetKit).
    Виджет тянет только этот ответ и рисует — без расчётов на клиенте.
    Демо-филиал отдаёт готовые демо-значения; для реального орга агрегатор
    переиспользует dashboard()/insights (следующий шаг)."""
    response.headers["Cache-Control"] = "max-age=300"  # кэш 5 минут
    is_demo = _use_demo_data(user, org_id)
    if not is_demo:
        _get_org_id(org_id, user)   # чужой филиал по ссылке своего — 403
    d = DEMO_DASHBOARD
    spark = d["chart_7days"]["data"]
    rev, fc = d["revenue_today"], d["forecast"]
    pct = round(rev / fc * 100) if fc else 0

    # задачи недели — из плана ИИ-советника (insights-сервис :8010)
    tasks = []
    try:
        async with httpx.AsyncClient(timeout=4) as c:
            r = await c.get(f"http://localhost:8010/api/insights/{org_id}")
            if r.status_code == 200:
                for i, p in enumerate(r.json().get("plan", [])[:4]):
                    tasks.append({"title": (p.get("action") or "")[:48],
                                  "target": p.get("effect", ""),
                                  "done": i < 2})
    except Exception as e:
        logger.warning(f"[widget] insights unavailable: {e}")

    done = sum(1 for t in tasks if t["done"])
    yday = spark[-1]
    return {
        "generated_at": local_now().isoformat(),
        "demo": is_demo,
        "revenue": {
            "today": rev, "forecast": fc, "pct_to_forecast": pct,
            "delta_pct": d["revenue_change"], "avg_check": d["avg_check"],
            "guests": d["guests"], "open_orders": d["open_orders"], "spark7": spark,
        },
        "todo": {"progress": f"{done}/{len(tasks)}" if tasks else "0/0", "tasks": tasks},
        "seating": {
            "orders_now": d["open_orders"], "peak_orders": 11, "peak_label": "Пт 19:00",
            "occupancy_pct": round(d["open_orders"] / 11 * 100),
        },
        "push": {
            "hero": f"Вчера {yday:,} ₸".replace(",", " "),
            "body": f"+{d['revenue_change']:.0f}% к плану. Фудкост 36% — 1 задача на сегодня.",
        },
    }


# Виджет подключён выше, рядом с остальными роутерами: после слияния веток
# include_router(widget_routes) стоял дважды и /w/{token} регистрировался
# два раза. Работало (побеждал первый), но такое надо убирать сразу.

# ==== «Гора»: задачи филиалам, баллы, чек-листы. Доступ по личной ссылке ====
import mountain_routes
app.include_router(mountain_routes.router)


@app.get("/api/mountain/links")
async def mountain_links(user: Dict = Depends(get_current_user)):
    """Личные ссылки: владельцу — своя, каждому филиалу — своя. ТОЛЬКО ВЛАДЕЛЬЦУ.

    Раньше ручка смотрела только на user["id"], а у ссылки ФИЛИАЛА это uid
    владельца — своих кредов iiko у филиала нет (см. scope_of_token). Управляющий
    получал ссылку владельца и по ней открывал всю сеть, ставил задачи и рассылал
    сообщения всем филиалам. Это была прямая привилегия-эскалация, а не утечка
    списка: ссылка = вход.

    Владение спрашиваем через get_owned_restaurants: get_restaurants намеренно
    отдаёт и филиалы, где человек лишь управляющий, и владельца по ней не отличить.
    """
    if user.get("only_org"):
        raise HTTPException(status_code=403, detail="Ссылки раздаёт владелец")
    uid = user.get("id", 0)
    rests = get_owned_restaurants(uid) or []
    if not rests:
        raise HTTPException(status_code=403, detail="Ссылки раздаёт владелец")
    mgrs = {m["organization_id"]: m for m in get_branch_managers()}
    branches = []
    for r in rests:
        org = r["organization_id"]
        # Ссылки выдаём по тому же правилу, что и «Гора»: только точки продаж.
        # Иначе в списке оказывались КЦ, ЦЕХ, ОФИС, РОЯЛТИ и города, которых на
        # доске нет вовсе — семнадцать ссылок вместо девяти.
        if not mountain_routes._is_branch(r.get("organization_name") or ""):
            continue
        m = mgrs.get(org)
        branches.append({
            "organization_id": org,
            "name": r.get("organization_name") or "Филиал",
            # telegram id оставлен справочно — для бота. Ссылка от него больше
            # НЕ зависит: она подписывает сам филиал, поэтому есть у каждого,
            # а не только у тех семи, кому успели завести управляющего.
            "manager_tid": (m or {}).get("telegram_id"),
            "link": f"/m/{mountain_routes.token_for_org(org)}",
        })
    return {"owner": {"tid": uid, "link": f"/m/{mountain_routes.token_for(uid)}"},
            "branches": branches}


# uid'ы, по которым сбор уже идёт: второе нажатие «Обновить» не должно
# запускать второй проход по всем филиалам. Рядом — ССЫЛКИ на сами задачи:
# asyncio держит их слабо, и задача без ссылки может быть собрана сборщиком
# мусора прямо посреди сбора, молча и без следа в логах.
_digest_running: set = set()
_digest_tasks: set = set()


def _spawn_digest(uid: int, sig_key: str, key: str,
                  signals: Optional[Dict[str, Any]] = None) -> None:
    _digest_running.add(uid)
    task = asyncio.create_task(_build_digest(uid, sig_key, key, signals=signals))
    _digest_tasks.add(task)
    task.add_done_callback(_digest_tasks.discard)


async def _build_digest(uid: int, sig_key: str, key: str,
                        signals: Optional[Dict[str, Any]] = None) -> None:
    """Сбор показателей и наблюдений в фоне. Результат кладётся в кэш, откуда
    его заберёт следующий запрос экрана."""
    try:
        if signals is None:
            signals = await _gather_assistant_signals({"id": uid})
            _set_cached(sig_key, signals, ttl=ASSISTANT_TTL)
        # Считаем только по филиалам «Горы»: регионы и служебные организации
        # владельцу в этой сводке не нужны, а каждая лишняя строка — токены.
        names = {b["name"] for b in mountain_routes._scope(uid)["branches"]}
        slim = {"branches": [b for b in (signals.get("branches") or []) if b.get("name") in names]}
        # Проверяем СПИСОК, а не словарь: {"branches": []} истинен, и клиент без
        # подходящих филиалов получал пустой дайджест, закэшированный на 12 часов.
        notes = await assistant_ai.digest(slim if slim["branches"] else signals)
        _set_cached(key, {"notes": notes, "stale": False}, ttl=ASSISTANT_TTL)
    except Exception as e:
        logger.warning(f"[digest] фоновый сбор для {uid}: {e!r}")
        # Ошибку кладём в кэш ненадолго: экран должен УВИДЕТЬ провал, а не
        # опрашивать вечно. Через минуту можно пробовать снова.
        _set_cached(key, {"notes": [], "stale": True,
                          "hint": f"Не удалось посчитать: {str(e)[:120]}"}, ttl=timedelta(minutes=1))
    finally:
        _digest_running.discard(uid)


@app.get("/api/mountain/digest/{token}")
async def mountain_digest(token: str, refresh: bool = Query(False)):
    """«Что изменилось» по каждому филиалу — наблюдение, а не задачи.

    По умолчанию считается ТОЛЬКО из кэша: сбор сигналов идёт по всем филиалам
    и занимает полторы минуты, а владелец ждать не должен. refresh=1 — когда он
    осознанно хочет свежие цифры.
    """
    uid = mountain_routes.uid_from_token(token)
    if not uid:
        raise HTTPException(status_code=404, detail="bad token")
    if not (get_restaurants(uid) or []):
        raise HTTPException(status_code=403, detail="только владельцу")
    sig_key = _cache_key(uid, "mountain-signals", "all", "month")
    key = _cache_key(uid, "mountain-digest", "all", "month")
    signals = None if refresh else _get_cached(sig_key, ttl=ASSISTANT_TTL)
    if signals is None:
        if not refresh:
            return {"notes": [], "stale": True,
                    "hint": "Показатели ещё не собраны — нажми «Обновить»."}
        # Сбор уходит В ФОН, ручка отвечает сразу.
        #
        # Раньше «Обновить» держал запрос до конца сбора — полторы-две минуты, и
        # владелец сидел над кнопкой «Считаю…», не зная, жив ли запрос вообще.
        # Тот же приём уже работает у оценки филиалов на «Горе» (score_pending):
        # ответить сразу, результат подтянуть следующим запросом.
        if uid not in _digest_running:
            _spawn_digest(uid, sig_key, key)
        return {"notes": [], "pending": True,
                "hint": "Считаю показатели по всем филиалам — это до двух минут. "
                        "Страницу можно не держать открытой."}
    cached = _get_cached(key, ttl=ASSISTANT_TTL)
    if cached is not None and not refresh:
        return cached
    if uid in _digest_running:
        return {"notes": [], "pending": True, "hint": "Считаю показатели по всем филиалам…"}
    _spawn_digest(uid, sig_key, key, signals=signals)
    return {"notes": [], "pending": True, "hint": "Считаю наблюдения…"}


@app.post("/api/mountain/propose/{token}")
async def mountain_propose(token: str, force: bool = Query(False),
                           org: Optional[str] = Query(None)):
    """ИИ предлагает по одной задаче на филиал. Ставит их потом владелец руками.

    Один вызов модели на ВСЮ сеть: правила выбирают больное место каждого
    филиала бесплатно, модель только формулирует. Тринадцать филиалов — один
    запрос, а не тринадцать."""
    uid = mountain_routes.uid_from_token(token)
    if not uid:
        raise HTTPException(status_code=404, detail="bad token")
    # Владение, а не доступ: get_restaurants отдаёт и филиалы, где человек лишь
    # управляющий, и владельца по ней не отличить (см. get_owned_restaurants).
    if not (get_owned_restaurants(uid) or []):
        raise HTTPException(status_code=403, detail="предлагать задачи может только владелец")
    # Сбор сигналов — это поход в iiko по всем филиалам, дорого и небыстро.
    # Держим результат столько же, сколько разбор ассистента: повторное нажатие
    # кнопки не должно ни платить модели, ни хаммерить сервера клиента.
    key = _cache_key(uid, "mountain-propose", org or "all", "month")
    cached = _get_cached(key, ttl=ASSISTANT_TTL)
    if cached is not None and not force:
        return cached

    signals = await _gather_assistant_signals({"id": uid})
    # Кладём сигналы в кэш: по ним «Гора» считает оценку филиала, не запуская
    # сбор заново — он идёт по всем филиалам и стоит минуты.
    _set_cached(_cache_key(uid, "mountain-signals", "all", "month"), signals, ttl=ASSISTANT_TTL)
    if org:
        # Проверка на одном филиале: не тратим ни токены, ни походы в iiko на сеть.
        want = next((r.get("organization_name") for r in get_restaurants(uid)
                     if r["organization_id"] == org), None)
        signals["branches"] = [b for b in (signals.get("branches") or [])
                               if b.get("name") == want]
    names = {r.get("organization_name") or "Филиал": r["organization_id"]
             for r in get_restaurants(uid)}
    tasks = await assistant_ai.propose_tasks(signals)
    for t in tasks:
        t["organization_id"] = names.get(t.get("branch"), "")
    res = {"tasks": [t for t in tasks if t.get("organization_id")]}
    _set_cached(key, res, ttl=ASSISTANT_TTL)
    return res


@app.post("/api/widget/token")
async def widget_token(request: Request, user: Dict = Depends(get_current_user)):
    """Личная ссылка на виджет владельца + ссылки по каждому филиалу."""
    uid = int(user.get("id", 0) or 0)
    if not uid:
        raise HTTPException(status_code=401, detail="no user")
    t = widget_routes.widget_token_for(uid)
    base = str(request.base_url).rstrip("/")
    branches = [{"id": o["organization_id"], "name": o["organization_name"],
                 "url": f"{base}/w/{t}/{o['organization_id']}"}
                for o in get_restaurants(uid)]
    return {"token": t, "url": f"{base}/w/{t}", "branches": branches}


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
                # DishDiscountSumInt (после скидок) = реально оплачено и сходится
                # с кассой; DishAmountSum (до скидок) её завышает. Держим обе:
                # первая идёт в выручку, вторая осталась ради совместимости графика.
                ["OpenDate.Typed"],
                ["GuestNum", "DishAmountSum", "DishDiscountSumInt", "OrdersCount"]
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
        # Откат считаем по оплаченному, а не по проданному до скидок: иначе при
        # отказе от смен выручка филиала подскакивает на величину всех скидок.
        olap_total = sum(float(r.get("DishDiscountSumInt") or r.get("DishAmountSum") or 0)
                         for r in all_rows)
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
            # Откуда взялась выручка: смены (как в кассе) или откат на OLAP.
            # Без этого поля ни сайт, ни мини-апп не могли отличить эталон от
            # запасного источника — и расхождение выглядело как «баг в цифре».
            "source": "cashshifts" if (got_sessions and len(got_sessions) == len(orgs)) else "olap",
        }
        _set_cached(key, resp,
                    ttl=_CACHE_TTL_LIVE if _is_live_period(date_from, date_to) else None)
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
    resp = {"revenue": round(sum(revs), 2), "ok": True, "source": "cashshifts",
            "as_of": local_now().strftime("%H:%M")}
    _set_cached(key, resp, ttl=_CACHE_TTL_LIVE if _is_live_period(date_from, date_to) else None)
    return resp


# ── Каналы продаж: девять типов заказа iiko → четыре канала ─────────────────
# Смысла в типах заказа четыре: зал, самовывоз, доставка, агрегаторы. Карточка
# показывала девять сырых долек («Доставка» и «Сайт доставка» отдельно, каждый
# агрегатор своей), и по ней нельзя было ответить «сколько даёт доставка».
# Группируем ТЕМ ЖЕ _classify_channel, что и «План», — правило одно на продукт.
CHANNEL_GROUP_RU: Dict[str, str] = {
    "hall": "Зал", "pickup": "Самовывоз",
    "delivery": "Доставка", "agg": "Агрегаторы",
}


def _group_sales_channels(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Строки OLAP по OrderType.Name → каналы карточки, по убыванию выручки.

    Это чистая перегруппировка: сумма по каналам равна выручке, как и до неё.
    Пустые группы не показываем — филиалу без агрегаторов пустая долька в
    легенде только мешает.
    """
    agg: Dict[str, Dict[str, float]] = {}
    for row in rows:
        name = row.get("OrderType.Name") or row.get("OrderTypeName", "")
        if not name:
            continue
        key = _classify_channel(name)
        cell = agg.setdefault(key, {"revenue": 0.0, "guests": 0.0, "orders": 0.0})
        cell["revenue"] += float(row.get("DishDiscountSumInt", 0) or 0)
        cell["guests"] += float(row.get("GuestNum", 0) or 0)
        cell["orders"] += float(row.get("OrdersCount", 0) or 0)
    out = [{
        "name": CHANNEL_GROUP_RU[k], "key": k,
        "revenue": v["revenue"], "guests": int(v["guests"]), "orders": int(v["orders"]),
        "avg_check": v["revenue"] / v["guests"] if v["guests"] else 0, "top": True,
    } for k, v in agg.items()]
    out.sort(key=lambda x: x["revenue"], reverse=True)
    return out


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
        channels = _group_sales_channels(channels_rows)
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


@app.get("/api/late-deliveries/{org_id}")
async def late_deliveries(org_id: str, period: str = "month",
                          from_: Optional[str] = Query(None, alias="from"),
                          to: Optional[str] = Query(None),
                          limit: int = Query(200, ge=1, le=1000),
                          user: Dict = Depends(get_current_user)):
    """Опоздавшие доставки филиала ПОИМЁННО — из OLAP «Заказы-доставка».

    Строка = заказ: номер, дата, курьер, адрес, обещанное и фактическое время,
    опоздание в минутах, сумма. Отдаём и сводку (сколько всего доставок, сколько
    опоздали, медиана опоздания), и список каждого опоздания.
    """
    if _use_demo_data(user, org_id):
        return DEMO_LATE_DELIVERIES
    real_org = _get_org_id(org_id, user)
    if from_ and to:
        date_from, date_to = get_period_dates("custom", from_, to)
    else:
        date_from, date_to = get_period_dates(period)

    key = _cache_key(user.get("id", 0), "late-deliveries", real_org, f"{date_from}:{date_to}")
    cached = _get_cached(key)
    if cached is not None:
        return cached

    info = _get_restaurant_info(real_org, user)
    client = get_client(info["api_login"], info.get("iiko_server"), info.get("api_password"))
    try:
        rows = await client.olap_deliveries_detail(_olap_dept(real_org, info), date_from, date_to)
    except Exception as e:
        logger.warning(f"[late-deliveries] {real_org}: {e!r}")
        return _iiko_unavailable()

    # Самовывоз — не доставка: у Алиева он давал 318 «опозданий» из 443 и полностью
    # искажал картину. Отсекаем так же, как в /api/couriers.
    def _is_courier(name: str) -> bool:
        n = (name or "").strip().lower()
        return bool(n) and "амовывоз" not in n

    # Разрез по типу заказа. Одни и те же строки уже загружены — второй запрос
    # в iiko не нужен. Важно: у самовывоза отметка «приготовлено» стоит далеко
    # не всегда (её ставят только на предзаказах с сайта и приложения), поэтому
    # рядом с медианой отдаём охват — сколько заказов её вообще имеют.
    def _kind(name: str) -> str:
        return "pickup" if "амовывоз" in (name or "").lower() else "courier"

    by_type: Dict[str, Any] = {}
    for kind_key, title in (("courier", "Доставка курьером"), ("pickup", "Вынос и самовывоз")):
        grp = [x for x in rows
               if str(x.get("Delivery.Courier") or "").strip()
               and _kind(str(x.get("Delivery.Courier"))) == kind_key]
        if not grp:
            continue
        st = [_late_stages(x) for x in grp]
        pick = lambda k: [v[k] for v in st if v[k] is not None and 0 <= v[k] < 600]
        kitchen, idle, road = pick("kitchen"), pick("idle"), pick("road")
        by_type[kind_key] = {
            "title": title,
            "orders": len(grp),
            "measured": len(kitchen),
            "kitchen": round(_median(kitchen)),
            "idle": round(_median(idle)),
            "road": round(_median(road)) if kind_key == "courier" else 0,
            "total": round(_median(kitchen) + _median(idle)
                           + (_median(road) if kind_key == "courier" else 0)),
        }

    # Норма этапов — медиана по ВСЕМ доставкам филиала за период, включая
    # приехавшие вовремя. Считать норму по одним опоздавшим нельзя: тогда
    # «нормально» окажется тем, что как раз и сломалось.
    stage_pool: Dict[str, List[float]] = {"kitchen": [], "idle": [], "road": []}
    for r in rows:
        if not _is_courier(str(r.get("Delivery.Courier") or "")):
            continue
        for k, v in _late_stages(r).items():
            if v is not None and 0 <= v < 600:
                stage_pool[k].append(v)
    norms = {k: _median(v) for k, v in stage_pool.items()}

    delivered, late = 0, []
    for r in rows:
        if not _is_courier(str(r.get("Delivery.Courier") or "")):
            continue
        delivered += 1
        delay = float(r.get("Delivery.Delay") or 0)
        if delay <= 0:
            continue
        stages = _late_stages(r)
        late.append({
            "cause": _attribute_cause(stages, norms, delay),
            "stages": {k: (round(v) if v is not None else None) for k, v in stages.items()},
            "number": r.get("Delivery.Number"),
            "date": str(r.get("OpenDate.Typed") or "")[:10],
            "courier": str(r.get("Delivery.Courier") or "").strip(),
            "address": _clean_address(r.get("Delivery.Address"), r.get("Delivery.Line1")),
            "source": _SOURCE_RU.get(str(r.get("Delivery.SourceKey") or "").upper(), ""),
            "expected": _hhmm(r.get("Delivery.ExpectedTime")),
            "actual": _hhmm(r.get("Delivery.ActualTime")),
            "delay_min": round(delay),
            "amount": round(float(r.get("DishDiscountSumInt") or 0)),
            # Опоздание в сутки — это не «курьер задержался», а незакрытый вовремя
            # заказ. Помечаем, чтобы такие строки не читались как реальный сервис.
            "suspicious": delay > LATE_SUSPICIOUS_MIN,
        })

    late.sort(key=lambda x: x["delay_min"], reverse=True)
    delays = sorted(x["delay_min"] for x in late)
    # Что чаще всего съедало время — главный вывод карточки, а не список строк.
    cause_count: Dict[str, int] = {}
    for x in late:
        cause_count[x["cause"]["key"]] = cause_count.get(x["cause"]["key"], 0) + 1
    top = max(cause_count, key=cause_count.get) if cause_count else None
    resp = {
        "delivered": delivered,
        "late": len(late),
        "norms": {k: round(v) for k, v in norms.items()},
        "by_type": by_type,
        "causes": [{"key": k, "label": LATE_STAGES[k], "count": n}
                   for k, n in sorted(cause_count.items(), key=lambda kv: -kv[1])],
        "top_cause": ({"key": top, "label": LATE_STAGES[top], "count": cause_count[top]}
                      if top else None),
        "late_pct": round(len(late) / delivered * 100) if delivered else 0,
        "median_delay": delays[len(delays) // 2] if delays else 0,
        "worst_delay": delays[-1] if delays else 0,
        "suspicious": sum(1 for x in late if x["suspicious"]),
        "orders": late[:limit],
        "period": {"from": date_from, "to": date_to},
    }
    _set_cached(key, resp)
    return resp


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
    # Карточки считаются по данным НАШЕГО КДС (saya-kds) и остаются закрытыми
    # для всех остальных. Текст человеческий: «forbidden» на экране выглядит
    # поломкой продукта, хотя это не поломка, а другой продукт.
    if int(user.get("id", 0)) not in CHASPIK_TG_IDS:
        raise HTTPException(status_code=403, detail=KDS_ONLY_DETAIL)
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


def _inv_date_key(date_str: str) -> str:
    """Дата накладной → сортируемый ключ ГГГГ-ММ-ДД.

    В одном списке лежат два формата: из iiko приходит «15.01.2026» (_fmt_date),
    у своих накладных — «2026-01-15». Сортировка строк по такому винегрету
    ставила все iiko-накладные ниже своих независимо от дня. Фура на «Складе»
    показывает только первые 12 ячеек — значит в кузове оказывались не самые
    свежие поставки.
    """
    d = (date_str or "").strip()[:10]
    if len(d) == 10 and d[2] == "." and d[5] == ".":
        return f"{d[6:10]}-{d[3:5]}-{d[0:2]}"
    return d


def _own_position(it: Dict[str, Any]) -> Dict[str, Any]:
    """Позиция своей накладной → строка состава для карточки.

    Ключи у своих накладных СВОИ: распознавание и редактор пишут quantity и
    price_per_unit, а из iiko приходят amount и price. Читаем оба набора —
    иначе состав есть в базе, а в карточке нули.
    """
    return {
        "name": it.get("name") or it.get("iiko_product_name") or "Позиция",
        "qty": it.get("quantity") or it.get("qty") or it.get("amount") or 0,
        "unit": it.get("unit") or it.get("iiko_unit") or "",
        "price": it.get("price_per_unit") or it.get("price") or 0,
        "sum": it.get("total") or it.get("sum") or 0,
    }


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
            positions = [{
                "name": (it.get("product", {}) or {}).get("name") or it.get("productName") or it.get("name") or "Позиция",
                "qty": it.get("amount") or it.get("num") or 0,
                "unit": (it.get("product", {}) or {}).get("mainUnit") or it.get("unit") or "",
                "price": it.get("price") or 0,
                "sum": it.get("sum") or it.get("sumWithoutNds") or 0,
            } for it in items[:40]]
            result_list.append({
                "id": (inv.get("documentNumber", "") or (inv.get("id", "") or "")[:8])[:8],
                "status": STATUS_MAP.get(inv.get("status", ""), inv.get("status", "")),
                "supplier": supplier.get("name", "—"),
                "date": _fmt_date(inv.get("dateIncoming", "")),
                "items": len(items),
                "amount": float(inv.get("sum", 0) or 0),
                "positions": positions,
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
            # Состав. Накладные из iiko отдавали positions, а свои — только
            # ЧИСЛО позиций: в карточке у них было пусто, «состава нет».
            # Состав всё это время лежал в базе, просто не доезжал до экрана.
            try:
                own_items = json.loads(b.get("items") or "[]")
            except Exception as e:
                logger.warning(f"[invoices] битый состав BOT-{b['id']}: {e!r}")
                own_items = []
            positions = [_own_position(it) for it in own_items[:40] if isinstance(it, dict)]
            result_list.append({
                "id": f"BOT-{b['id']}",
                "bot_id": b["id"],
                "status": BOT_STATUS_MAP.get(raw_status, raw_status),
                "raw_status": raw_status,
                "supplier": b.get("supplier") or "—",
                "date": (b.get("date") or "")[:10] or (b.get("created_at") or "")[:10],
                "items": len(own_items),
                "positions": positions,
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

    result_list.sort(key=lambda x: _inv_date_key(x["date"]), reverse=True)
    total_amount = round(sum(float(x.get("amount") or 0) for x in result_list))
    result = {"invoices": result_list, "total_amount": total_amount, "count": len(result_list)}
    _set_cached(key, result)
    return result


# ============ PRICE STATS (Mini App «📊 Цены») ============
# Cache of iiko invoice exports per (org_id, days) → (timestamp, raw_payload).
# TTL 1 hour. Avoids hitting iiko on every nomenclature pick.
_price_stats_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_PRICE_STATS_TTL_SECONDS = 3600


async def _invoice_rows(real_org: str, info: Dict[str, Any], days: int
                        ) -> Tuple[List[Dict[str, Any]], bool]:
    """Строки приходных накладных филиала за `days` дней + признак «iiko ответил».

    Кэш общий с карточкой товара: разбор накладной на 30 позиций иначе тянул бы
    один и тот же OLAP тридцать раз.
    """
    client = get_client(info["api_login"], info.get("iiko_server"), info.get("api_password"))
    date_to = local_now().strftime("%Y-%m-%d")
    date_from = (local_now() - timedelta(days=days)).strftime("%Y-%m-%d")
    cache_key = f"{real_org}|{days}"
    cached = _price_stats_cache.get(cache_key)
    if cached and (time.time() - cached[0]) < _PRICE_STATS_TTL_SECONDS:
        return cached[1], True
    try:
        rows = await client.get_invoice_items_olap([_olap_dept(real_org, info)], date_from, date_to)
        _price_stats_cache[cache_key] = (time.time(), rows)
        return rows, True
    except Exception as e:
        logger.warning(f"[price_stats] iiko fetch failed: {e}")
        return [], False


@app.get("/api/price-stats/{org_id}")
async def price_stats(org_id: str, product_id: str = Query(..., min_length=1),
                       days: int = Query(60, ge=1, le=365),
                       user: Dict = Depends(get_current_user)):
    """Агрегат цен товара по поставщикам за `days` дней.
    Источник: iiko export invoice_incoming + локальные bot_invoices.
    """
    if _use_demo_data(user, org_id):
        return _demo_price_stats(product_id, days)

    real_org = _get_org_id(org_id, user)
    info = _get_restaurant_info(real_org, user)
    rows, iiko_ok = await _invoice_rows(real_org, info, days)

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


# ── Разбор цен целой накладной ───────────────────────────────────────────────
# Экран пробития спрашивал цены по одному товару за раз: тридцать позиций —
# тридцать запросов, и ни одного ответа на главный вопрос «сколько мы на этой
# накладной потеряли». Здесь всё сразу и одним числом.

class InvoicePricesPayload(BaseModel):
    product_ids: List[str] = Field(default_factory=list)
    items: List[Dict[str, Any]] = Field(default_factory=list)   # позиции накладной
    days: int = 60
    network: bool = False        # сравнивать со всей сетью, а не только со своим


# Сколько филиалов опрашиваем в сетевом сравнении. Каждый — свой OLAP; без
# потолка «сравнить по сети» на двадцати трёх филиалах превращается в минуту
# ожидания. Кэш на час делает второй заход мгновенным.
NETWORK_BRANCH_LIMIT = int(os.getenv("PRICE_NETWORK_LIMIT", "8"))


@app.post("/api/invoice-prices/{org_id}")
async def invoice_prices(org_id: str, payload: InvoicePricesPayload,
                         user: Dict = Depends(get_current_user)):
    """Лучшие цены по всем позициям накладной и итог переплаты.

    network=true — ищем и по соседним филиалам: «у кого-то дешевле» чаще всего
    значит «у соседа», а не «у другого поставщика в этом же филиале». Филиал,
    который не ответил, просто не участвует: сравнение из семи филиалов лучше,
    чем ошибка из-за одного упавшего сервера.
    """
    import price_compare as PC
    real_org = _get_org_id(org_id, user)
    items = payload.items or []
    pids = [p for p in (payload.product_ids or
                        [i.get("iiko_product_id") or "" for i in items]) if p]
    if not pids:
        return {"best": {}, "overpay": PC.overpay([], {}), "scope": "branch", "branches": []}

    days = max(1, min(int(payload.days or 60), 365))
    info = _get_restaurant_info(real_org, user)
    rows, iiko_ok = await _invoice_rows(real_org, info, days)
    mine_name = info.get("organization_name") or "мой филиал"
    parts = [PC.best_by_product(rows, pids, branch=mine_name)]
    asked = [mine_name]

    if payload.network:
        others = [r for r in (get_restaurants(user.get("id") or 0) or [])
                  if r["organization_id"] != real_org]
        sem = asyncio.Semaphore(4)

        async def one(rest: Dict[str, Any]):
            async with sem:
                try:
                    o_info = _get_restaurant_info(rest["organization_id"], user)
                    o_rows, ok = await _invoice_rows(rest["organization_id"], o_info, days)
                    if not ok:
                        return None
                    name = rest.get("organization_name") or "филиал"
                    asked.append(name)
                    return PC.best_by_product(o_rows, pids, branch=name)
                except Exception as e:
                    logger.warning(f"[invoice-prices] {rest.get('organization_name')}: {e!r}")
                    return None

        got = await asyncio.gather(*[one(r) for r in others[:NETWORK_BRANCH_LIMIT]])
        parts += [g for g in got if g]

    best = PC.merge_best(parts)
    return {"best": best, "overpay": PC.overpay(items, best), "days": days,
            "scope": "network" if payload.network else "branch",
            "branches": asked, "iiko_ok": iiko_ok}


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


# ── ВРЕМЕННОЕ: миграция SQLite между хостингами ──
# Включается ТОЛЬКО env-переменной MIGRATE_TOKEN (fail-closed: без неё 404).
# dump — целостная копия БД (sqlite backup) на скачивание; restore — принять
# файл БД и подменить DB_PATH (после restore сервис нужно перезапустить).
# После переезда: удалить env MIGRATE_TOKEN, затем выпилить этот блок.
MIGRATE_TOKEN = os.getenv("MIGRATE_TOKEN", "")


def _migrate_auth(token: str):
    if not MIGRATE_TOKEN or not hmac.compare_digest(token.encode(), MIGRATE_TOKEN.encode()):
        raise HTTPException(status_code=404, detail="Not Found")


@app.get("/api/admin/migrate/dump")
async def migrate_dump(token: str = Query("")):
    _migrate_auth(token)
    import sqlite3 as _sq
    from database import DB_PATH as _dbp
    dst = _dbp + ".dump"
    src = _sq.connect(_dbp)
    out = _sq.connect(dst)
    with out:
        src.backup(out)
    out.close(); src.close()
    return FileResponse(dst, filename="db.sqlite",
                        media_type="application/octet-stream")


@app.post("/api/admin/migrate/restore")
async def migrate_restore(request: Request, token: str = Query("")):
    _migrate_auth(token)
    import sqlite3 as _sq
    from database import DB_PATH as _dbp
    body = await request.body()
    if not body.startswith(b"SQLite format 3\x00"):
        raise HTTPException(status_code=400, detail="not a sqlite db")
    tmp = _dbp + ".incoming"
    with open(tmp, "wb") as f:
        f.write(body)
    con = _sq.connect(tmp)
    n = con.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]
    con.close()
    if not n:
        raise HTTPException(status_code=400, detail="empty db")
    # хвосты WAL от старого файла не должны примениться к новому
    for suf in ("-wal", "-shm"):
        try:
            os.remove(_dbp + suf)
        except FileNotFoundError:
            pass
    os.replace(tmp, _dbp)
    return {"ok": True, "tables": n, "bytes": len(body)}


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
    _get_org_id(org_id, user)   # данных пока нет, но филиал проверяем как везде
    return {"clients": 0, "orders": 0, "cohorts": [], "frequency": []}


def _olap_revenue(row: Dict[str, Any]) -> float:
    """Выручка из строки OLAP. DishDiscountSumInt — реально оплачено, сходится
    с кассой; DishAmountSum — сумма до скидок, она её завышает (на живом
    филиале за 2026 год 184,2 млн против 170,2 млн). Берём кассовую, а если
    сервер её не отдаёт — сумму до скидок: пустой экран хуже завышенного.
    """
    paid = float(row.get("DishDiscountSumInt") or 0)
    return paid or float(row.get("DishAmountSum") or 0)


def _prev_year_cutoff(today: date) -> date:
    """Тот же день год назад — граница окна прошлого года.

    Текущий год обрывается сегодняшним днём, а прошлый брался целиком по
    месяцам: сентябрь из одного дня сравнивался с целым сентябрём год назад и
    рост выходил +14,5% вместо честных +26,4%. Сравнивать надо равное с равным.
    29 февраля в невисокосном году не существует — съезжаем на 28-е.
    """
    try:
        return today.replace(year=today.year - 1)
    except ValueError:
        return today.replace(year=today.year - 1, day=28)


@app.get("/api/comparison/{org_id}")
async def comparison(org_id: str,
                     org_ids: Optional[str] = Query(None),
                     user: Dict = Depends(get_current_user)):
    """YoY сравнение по неделям и месяцам с разбивкой по каналам."""
    if _use_demo_data(user, org_id):
        return _demo_comparison()

    orgs = _parse_org_ids(org_ids, org_id, user)

    from datetime import date as date_cls

    async def fetch_year_for_org(real_org: str, year: int, end: str):
        info = _get_restaurant_info(real_org, user)
        client = get_client(info["api_login"], info.get("iiko_server"), info.get("api_password"))
        start = f"{year}-01-01"
        try:
            result = await client.olap_report(
                _olap_dept(real_org, info), start, end,
                group_by=["OpenDate.Typed", "OrderType.Name"],
                aggregates=["DishDiscountSumInt", "DishAmountSum", "OrdersCount"]
            )
            return result.get("data", [])
        except Exception as e:
            logger.warning(f"[comparison] {real_org}/{year}: {e}")
            return []

    today = local_now().date()
    cur_year = today.year
    # Прошлый год обрезаем тем же днём: иначе незакрытый месяц текущего года
    # сравнивается с целым месяцем прошлого и рост занижается.
    prev_end = _prev_year_cutoff(today).isoformat()
    cur_batches, prev_batches = await asyncio.gather(
        asyncio.gather(*(fetch_year_for_org(o, cur_year, today.isoformat()) for o in orgs)),
        asyncio.gather(*(fetch_year_for_org(o, cur_year - 1, prev_end) for o in orgs)),
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
            rev = _olap_revenue(row)
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
        # до какого дня посчитан каждый год — экран подписывает этим текущий месяц
        "as_of": today.isoformat(),
        "prev_as_of": prev_end,
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
    # Страховка: накладная могла распознаться до появления пред-сопоставления или
    # в момент, когда iiko не отдал справочник. Пробуем ещё раз при открытии.
    if any(not it.get("iiko_product_id") for it in (result.get("items") or [])):
        await _prematch_pending(pending)
        _persist_pending(pending)
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


class RecognizeImage(BaseModel):
    data: str                       # base64 без префикса data:
    mime: str = "image/jpeg"


class RecognizePayload(BaseModel):
    images: List[RecognizeImage] = Field(default_factory=list, max_length=10)


# ── Поставщик с фото ────────────────────────────────────────────────────────
# Имя поставщика Claude с накладной читает, но списком из трёхсот позиций
# оператор всё равно пользовался руками. Подбираем id здесь.
#
# Справочник живой и грязный: ведущие пробелы и кавычки (« Ер-Амаль»), дубли
# латиницей и кириллицей («AFOOD-Афуд» / «Afood-Афуд»), казахские буквы
# («Айғаным» рядом с «Айганым»), приписки «поставщик», склады филиалов.
_SUPPLIER_NOISE = {
    "тоо", "ооо", "ип", "зао", "оао", "ао", "жшс", "кх", "чп", "фх",
    "llp", "ltd", "llc", "inc", "co", "company",
    "поставщик", "поставщика", "склад",
}
# Казахские буквы к ближайшим русским: «Айғаным» и «Айганым» — один поставщик.
_KZ_FOLD = str.maketrans({"ғ": "г", "қ": "к", "ң": "н", "ө": "о", "ұ": "у",
                          "ү": "у", "һ": "х", "і": "и", "ә": "а"})


def _supplier_tokens(name: str) -> List[str]:
    """Имя поставщика → значимые слова.

    В отличие от товаров числа НЕ выбрасываем: у «7 морей» цифра — часть имени,
    и без неё от поставщика ничего не остаётся.
    """
    s = (name or "").lower().replace("ё", "е").translate(_KZ_FOLD)
    s = re.sub(r"[^a-zа-я0-9]+", " ", s)
    return [w for w in s.split() if w not in _SUPPLIER_NOISE and len(w) > 1 or w.isdigit()]


def _supplier_score(recognized: str, candidate: str) -> float:
    """0..1 — насколько поставщик из iiko похож на прочитанного с фото."""
    a, b = _supplier_tokens(recognized), _supplier_tokens(candidate)
    if not a or not b:
        return 0.0
    hit = sum(1 for x in a if _word_hit(x, b)) / len(a)
    ratio = SequenceMatcher(None, " ".join(a), " ".join(b)).ratio()
    return 0.6 * hit + 0.4 * ratio


# Порог намеренно выше товарного: накладная, проведённая на ЧУЖОГО поставщика,
# — ошибка в учёте, которую потом ищут руками. Сомневаемся — выбирает оператор.
_SUPPLIER_MIN_SCORE = float(os.getenv("INVOICE_SUPPLIER_MIN", "0.72"))


def _best_supplier_match(suppliers: List[Dict[str, Any]], recognized: str
                         ) -> Tuple[Optional[Dict[str, Any]], float]:
    """Лучший поставщик под имя с фото, либо (None, score) — если не уверены."""
    if not recognized or not _supplier_tokens(recognized):
        return None, 0.0
    best, best_s = None, 0.0
    for sup in suppliers or []:
        sc = _supplier_score(recognized, sup.get("name") or "")
        if sc > best_s:
            best, best_s = sup, sc
    if best_s < _SUPPLIER_MIN_SCORE:
        return None, best_s
    return best, best_s


@app.post("/api/invoice/recognize/{org_id}")
async def invoice_recognize(org_id: str, payload: RecognizePayload,
                            user: Dict = Depends(get_current_user)):
    """Фото накладной → распознанные позиции, СРАЗУ сопоставленные с товарами iiko.

    Тот же путь, что у бота, только по HTTP: сайт грузит снимки, Claude Vision
    читает их как один документ, дальше работает наше сопоставление (память
    исправлений → скоринг по словам) по локальной базе закупаемых товаров.
    В iiko отсюда ничего не уходит: это разбор, отправку делает оператор.
    """
    # base64, а не multipart: python-multipart в зависимостях нет, а падать
    # на импорте из-за одной ручки нельзя — это уронило бы весь сервис.
    if not payload.images:
        raise HTTPException(status_code=400, detail="Нет файлов")
    images: List[Tuple[bytes, str]] = []
    for im in payload.images:
        b64 = (im.data or "").split(",", 1)[-1]     # терпим префикс data:image/...
        try:
            raw = base64.b64decode(b64, validate=False)
        except Exception:
            raise HTTPException(status_code=400, detail="Файл не читается")
        if not raw:
            continue
        if len(raw) > 12 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Файл больше 12 МБ")
        images.append((raw, im.mime or "image/jpeg"))
    if not images:
        raise HTTPException(status_code=400, detail="Пустые файлы")

    result = await analyze_invoice_photos(images)
    if "error" in result:
        return {"ok": False, "error": result["error"]}
    items = result.get("items") or []

    matched = unmatched = 0
    supplier_id = ""
    supplier_matched = ""
    if not _use_demo_data(user, org_id):
        real_org = _get_org_id(org_id, user)
        info = _get_restaurant_info(real_org, user)
        client = get_client(info["api_login"], info.get("iiko_server"), info.get("api_password"))
        try:
            _s, miss, _w = await _auto_match_items(client, real_org, items)
            unmatched = len(miss)
        except Exception as e:
            logger.warning(f"[invoice-recognize] сопоставление {org_id}: {e!r}")
        matched = sum(1 for it in items if it.get("iiko_product_id"))
        # Поставщик — отдельным шагом и в своём try: справочник поставщиков может
        # не ответить, но разбор позиций из-за этого терять нельзя.
        try:
            sup, score = _best_supplier_match(
                await client.get_suppliers(real_org), result.get("supplier") or "")
            if sup:
                supplier_id = sup.get("id") or ""
                supplier_matched = sup.get("name") or ""
                logger.info(f"[invoice-recognize] поставщик «{result.get('supplier')}» "
                            f"→ «{supplier_matched}» ({score:.2f})")
        except Exception as e:
            logger.warning(f"[invoice-recognize] поставщик {org_id}: {e!r}")
    return {
        "ok": True,
        "supplier": result.get("supplier"),
        # id поставщика из iiko — редактор ставит его в выпадающий список сразу.
        # Пусто = не уверены, выбирает оператор (см. _SUPPLIER_MIN_SCORE).
        "supplier_id": supplier_id,
        "supplier_matched": supplier_matched,
        "date": result.get("date"),
        "total_amount": result.get("total_amount", 0),
        "vat_amount": result.get("vat_amount", 0),
        "items": items,
        "matched": matched,
        "unmatched": unmatched,
        "needs_review": sum(1 for it in items if it.get("needs_review")),
    }


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
    # Ищем по локальной базе закупаемых товаров: оператору в редакторе накладной
    # блюда и модификаторы не нужны, а база отвечает мгновенно и без похода в iiko.
    try:
        products, _key = await _nomenclature_catalog(client, real_org)
    except Exception as e:
        logger.warning(f"[nomenclature_search] {e}")
        products = []
    ql = (q or "").strip().lower()
    if ql:
        found = [p for p in products
                 if ql in (p.get("name") or "").lower()
                 or ql in (p.get("num") or "").lower()
                 or ql in (p.get("code") or "").lower()]
        # Подстроки не хватило — добираем похожими по словам («м/сл» → «масло сливочное»)
        if len(found) < limit:
            seen = {p.get("id") for p in found}
            ranked = sorted(((_match_score(q, p.get("name") or ""), p) for p in products
                             if p.get("id") not in seen), key=lambda t: -t[0])
            found += [p for s, p in ranked[:limit - len(found)] if s >= _MATCH_MIN_SCORE]
        products = found
    products = products[:limit]
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


class MatchMemoryPayload(BaseModel):
    name: str                       # строка накладной как её напечатал поставщик
    product_id: str
    product_name: Optional[str] = None


def _memory_source_key(org_id: str, user: Dict) -> Tuple[str, Any]:
    """Ключ справочника соответствий = ключ справочника номенклатуры.

    У сети свой iiko-сервер почти на каждый филиал, и id товара с одного на
    другом невалиден: память обязана жить в тех же границах, что номенклатура.
    """
    real_org = _get_org_id(org_id, user)
    info = _get_restaurant_info(real_org, user)
    client = get_client(info["api_login"], info.get("iiko_server"), info.get("api_password"))
    return _catalog_key(client, real_org), client


@app.get("/api/invoice/match-memory/{org_id}")
async def match_memory_list(org_id: str, user: Dict = Depends(get_current_user)):
    """Справочник «строка поставщика → товар iiko» этого сервера."""
    if _use_demo_data(user, org_id):
        return {"entries": [], "demo": True}
    key, _client = _memory_source_key(org_id, user)
    return {"entries": list_invoice_match_memory(key), "source_key": key}


@app.post("/api/invoice/match-memory/{org_id}")
async def match_memory_remember(org_id: str, payload: MatchMemoryPayload,
                                user: Dict = Depends(get_current_user)):
    """Запомнить выбор оператора СРАЗУ, не дожидаясь отправки накладной в iiko.

    Так делает сам iiko со своим прайс-листом поставщика. Раньше мы запоминали
    только после успешной отправки: оператор мог поправить десять строк и не
    отправить накладную — вся работа пропадала, и следующая накладная того же
    поставщика снова приходила несопоставленной.
    """
    if _use_demo_data(user, org_id):
        return {"ok": False, "error": "демо-режим"}
    name_key = _match_memory_key(payload.name or "")
    if not name_key or not payload.product_id:
        # Строка вроде «М/КОР 3,2% 1Л» сворачивается в пустой ключ («м» короче
        # двух букв, «кор» — стоп-слово). Запоминать нечего, но и не ошибка.
        return {"ok": False, "remembered": False, "reason": "пустой ключ строки"}
    key, _client = _memory_source_key(org_id, user)
    remember_invoice_match(key, name_key, payload.product_id,
                           payload.product_name or "", name_raw=payload.name or "")
    return {"ok": True, "remembered": True, "name_norm": name_key}


@app.delete("/api/invoice/match-memory/{org_id}")
async def match_memory_forget(org_id: str, name_norm: str = Query(..., min_length=1),
                              user: Dict = Depends(get_current_user)):
    """Убрать ошибочное соответствие из справочника."""
    if _use_demo_data(user, org_id):
        return {"ok": False, "error": "демо-режим"}
    key, _client = _memory_source_key(org_id, user)
    return {"ok": True, "removed": forget_invoice_match(key, name_norm)}


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
    # Черновик мог сохраниться несопоставленным: распознан до появления базы
    # товаров или в момент, когда iiko не отдал справочник. Досопоставляем при
    # открытии — иначе оператор навсегда получает пустой редактор из-за сбоя,
    # который уже прошёл.
    org_saved = inv.get("organization_id")
    if org_saved and items and any(not it.get("iiko_product_id") for it in items) \
            and not _use_demo_data(user, org_saved):
        try:
            real_org = _get_org_id(org_saved, user)
            info = _get_restaurant_info(real_org, user)
            client = get_client(info["api_login"], info.get("iiko_server"),
                                info.get("api_password"))
            await _auto_match_items(client, real_org, items)
            update_bot_invoice(invoice_id, items=json.dumps(items, ensure_ascii=False))
        except Exception as e:
            logger.warning(f"[invoice-saved] досопоставление при открытии: {e!r}")
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


class DraftPayload(BaseModel):
    items: List[Dict[str, Any]]
    supplier: Optional[str] = None
    supplier_id: Optional[str] = None
    date: Optional[str] = None
    total_amount: Optional[float] = None
    vat_amount: Optional[float] = None


@app.post("/api/invoice/draft/{org_id}")
async def invoice_draft_create(org_id: str, payload: DraftPayload,
                               user: Dict = Depends(get_current_user)):
    """Сохранить разобранную накладную как локальный черновик и вернуть её id.

    Нужен сайту: распознали фото → сопоставили → сохранили → дальше отправка
    идёт существующей ручкой /api/invoice-saved/{id}/send. Раньше черновик мог
    создать только бот, поэтому без телеграма накладную было не пробить.
    В iiko здесь ничего не создаётся — это наш черновик.
    """
    if _use_demo_data(user, org_id):
        raise HTTPException(status_code=400, detail="Демо-режим: накладные не сохраняются")
    if not payload.items:
        raise HTTPException(status_code=400, detail="Нет позиций")
    real_org = _get_org_id(org_id, user)
    info = _get_restaurant_info(real_org, user)
    date_str = (payload.date or local_now().strftime("%Y-%m-%d"))[:10]
    total = float(payload.total_amount or sum(float(i.get("total") or 0) for i in payload.items))
    unmatched = [i for i in payload.items if not i.get("iiko_product_id")]
    inv_id = save_bot_invoice(
        telegram_id=user.get("id", 0),
        telegram_user=user.get("username") or "site",
        raw_text="[САЙТ]",
        supplier=payload.supplier or "",
        branch=info.get("org_name", "") or "",
        items_json=json.dumps(payload.items, ensure_ascii=False),
        total_amount=total,
        date=date_str,
        vat_amount=float(payload.vat_amount or 0),
        supplier_id=payload.supplier_id,
        organization_id=real_org,
        dedup_hash=_compute_dedup_hash(payload.supplier or "", date_str, total,
                                       payload.items, real_org),
        status="needs_mapping" if unmatched else "ready",
    )
    _invalidate_cache("invoices", real_org)
    return {"ok": True, "invoice_id": inv_id, "unmatched": len(unmatched)}


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

    # Черновик в iiko создан — значит оператор подтвердил сопоставление позиций.
    # Запоминаем пары «строка накладной → товар iiko»: следующая накладная от
    # того же поставщика сопоставится точно и без пометки «проверьте».
    remember_invoice_matches(client, real_org, payload.items)

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
    """Клавиатура превью накладной.

    Кнопки «Редактировать» больше нет: она открывала мини-апп, а мини-аппа
    больше нет — по «/» теперь сайт, и `?edit=` он не понимает. Открывать
    вместо редактора главный экран хуже, чем не открывать вовсе. Правка
    накладной переехала на сайт, вкладка «Склад · заявки».
    Отправка и отмена работают как работали — поток бота цел.
    """
    del pending_id                      # больше не нужен: ссылки на мини-апп нет
    return {
        "inline_keyboard": [
            [{"text": "📤 Отправить в iiko", "callback_data": "act:send"}],
            [{"text": "❌ Отменить", "callback_data": "act:cancel"}],
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
            [{"text": "🚀 Открыть кабинет", "web_app": {"url": f"{MINIAPP_URL}/"}}],
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
            [{"text": "🚀 Открыть кабинет", "web_app": {"url": f"{MINIAPP_URL}/"}}],
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
            [{"text": "🚀 Открыть кабинет", "web_app": {"url": f"{MINIAPP_URL}/"}}],
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
        # Сколько уже подставлено автоматически — видно сразу, не открывая редактор
        matched = sum(1 for it in items if it.get("iiko_product_id"))
        review = sum(1 for it in items if it.get("iiko_product_id") and it.get("needs_review"))
        head = f"\n<b>Позиции ({len(items)}):</b>"
        if matched:
            head += f"  <i>сопоставлено {matched} из {len(items)}</i>"
            if review:
                head += f", <i>проверить {review}</i>"
        lines.append(head)
        for it in items[:15]:
            nm = it.get("name", "—") or "—"
            qty = it.get("quantity")
            un = it.get("unit") or ""
            ppu = it.get("price_per_unit")
            tot = it.get("total")
            qty_s = "—" if qty is None else str(qty)
            ppu_s = "—" if ppu is None else _fmt_kzt(ppu)
            tot_s = "—" if tot is None else _fmt_kzt(tot)
            mark = ""
            if it.get("iiko_product_id"):
                mark = " 🔎" if it.get("needs_review") else " ✓"
            lines.append(f"• {nm}{mark}: {qty_s} {un} × {ppu_s} ₸ = {tot_s} ₸")
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


# ── Нечёткий подбор товара по названию из накладной ────────────────────────
# Поставщик пишет «Молоко "Простоквашино" 3,2% 900мл», в iiko лежит «Молоко 3,2%».
# Поиск подстрокой по всей строке тут не находит ничего — клиенту приходилось
# сопоставлять руками каждую позицию. Считаем похожесть по словам.

# Слова, которые есть у всех и различать товары не помогают.
# ── Сопоставление строк накладной с номенклатурой iiko ────────────────────
# Слова, которые в названии ничего не различают: организационно-правовые формы,
# тара, состояние товара. Без их выброса «Масло сливочное в упаковке» и
# «Упаковка для салата» выглядят похожими.
_MATCH_STOP = {
    "ооо", "тоо", "ип", "зао", "оао", "гост", "ту", "шт", "уп", "упак", "упаковка",
    "пак", "пачка", "бут", "бутылка", "банка", "кг", "гр", "г", "мл", "л", "литр",
    "кор", "коробка", "лоток", "пэт", "ваккум", "вакуум", "св", "охл", "зам",
    "замороженный", "охлажденный", "свежий", "новый", "продукт", "товар",
}
_MATCH_MIN_SCORE = float(os.getenv("INVOICE_MATCH_MIN", "0.55"))
# Поставщик привозит ТОВАРЫ. Блюда, полуфабрикаты и модификаторы в справочнике
# iiko — это то, что готовится у нас, в приходной накладной их не бывает, и в
# сопоставлении они дают только ложные попадания («Молочный коктейль» вместо
# «Молоко»). Список типов оставляем настраиваемым: у чужих установок бывает иначе.
_PURCHASABLE_TYPES = {t.strip().upper() for t in
                      os.getenv("INVOICE_MATCH_TYPES", "GOODS").split(",") if t.strip()}
_TYPE_ALIASES = {"GOOD": "GOODS", "PRODUCT": "GOODS"}   # облако пишет тип иначе
_NOMENCLATURE_TTL_H = float(os.getenv("NOMENCLATURE_TTL_HOURS", "24"))


def _product_type(p: Dict[str, Any]) -> str:
    t = (p.get("type") or p.get("productType") or "").strip().upper()
    return _TYPE_ALIASES.get(t, t)


def _filter_purchasable(products: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Оставляет только закупаемые позиции.
    Позицию без типа НЕ выбрасываем: на части установок iiko поле не приходит
    вовсе, и жёсткий фильтр обнулил бы весь справочник."""
    out = []
    for p in products or []:
        if not p.get("id"):
            continue
        t = _product_type(p)
        if t and t not in _PURCHASABLE_TYPES:
            continue
        out.append(p)
    return out


def _catalog_key(client, org_id: str) -> str:
    """Ключ справочника в локальной базе (см. catalog_key() клиентов iiko)."""
    getter = getattr(client, "catalog_key", None)
    if callable(getter):
        try:
            return getter(org_id)
        except Exception:
            pass
    return f"org:{org_id}"


async def _resolve_units(client, products: List[Dict[str, Any]]):
    """Переводит единицу товара из UUID в название (кг/шт/л).

    У самохостед iiko mainUnit приходит идентификатором. Пока он оставался UUID,
    сравнение единиц накладной и товара никогда не совпадало: автоконверсия
    г→кг не срабатывала, а оператор видел предупреждение с «кашей символов».
    """
    if not products or not hasattr(client, "get_measure_units_map"):
        return
    if not any(re.fullmatch(r"[0-9a-f-]{36}", str(p.get("unit") or "")) for p in products):
        return
    try:
        units = await client.get_measure_units_map()
    except Exception as e:
        logger.warning(f"[nomenclature] единицы измерения: {e!r}")
        return
    for p in products:
        u = str(p.get("unit") or "")
        if u in units:
            p["unit"] = units[u]
        elif re.fullmatch(r"[0-9a-f-]{36}", u):
            p["unit"] = ""      # неизвестный UUID лучше пустого места не занимал


# Когда iiko отказал (сессия занята, сеть, 401), повторная попытка через секунду
# только злит сервер: он отвечает 401 ещё дольше. Держим паузу и работаем по
# последнему снимку из базы.
_CATALOG_LAST_FAIL: Dict[str, float] = {}
_CATALOG_FAIL_COOLDOWN = float(os.getenv("NOMENCLATURE_FAIL_COOLDOWN", "60"))


async def _nomenclature_catalog(client, org_id: str, force: bool = False
                                ) -> Tuple[List[Dict[str, Any]], str]:
    """Справочник закупаемых товаров из локальной базы; в iiko ходим не чаще TTL.

    Раньше номенклатура тянулась целиком в память на каждый рестарт процесса и
    жила 30 минут — на Render это значило полный справочник почти на каждую
    накладную. Теперь снимок лежит в SQLite, отфильтрованный от блюд.
    Если iiko недоступен — работаем по последнему снимку, а не по пустоте.
    """
    key = _catalog_key(client, org_id)
    synced = iiko_products_synced_at(key)
    fresh = bool(synced) and (datetime.now() - synced) < timedelta(hours=_NOMENCLATURE_TTL_H)
    if fresh and not force:
        cached = load_iiko_products(key)
        if cached:
            return cached, key

    if time.time() - _CATALOG_LAST_FAIL.get(key, 0) < _CATALOG_FAIL_COOLDOWN:
        return load_iiko_products(key), key

    raw = None
    try:
        try:
            # Просим у iiko сразу только закупаемые типы: на реальной базе это
            # 544 позиции вместо 1730 и 1.2 МБ вместо 7.3 МБ. Локальный фильтр
            # ниже всё равно оставляем — старые сборки iiko параметр игнорируют.
            raw = await client.search_nomenclature(org_id, "", limit=100000,
                                                   types=sorted(_PURCHASABLE_TYPES))
        except TypeError:   # клиент без поддержки types
            raw = await client.search_nomenclature(org_id, "", limit=100000)
    except Exception as e:
        _CATALOG_LAST_FAIL[key] = time.time()
        logger.warning(f"[nomenclature] {key}: iiko не отдал номенклатуру: {e!r}")
    if raw:
        _CATALOG_LAST_FAIL.pop(key, None)
        goods = _filter_purchasable(raw)
        await _resolve_units(client, goods)
        if goods:
            try:
                save_iiko_products(key, goods, fetched=len(raw))
                logger.info(f"[nomenclature] {key}: {len(raw)} позиций из iiko → "
                            f"{len(goods)} закупаемых в базе")
            except Exception as e:
                logger.warning(f"[nomenclature] {key}: не смог сохранить в базу: {e!r}")
            return goods, key
        logger.warning(f"[nomenclature] {key}: после фильтра типов не осталось позиций "
                       f"(из {len(raw)}) — оставляем прежний снимок")
    return load_iiko_products(key), key


_QUOTED_RE = re.compile(r"[«\"\'`]([^«»\"\'`]{2,})[»\"\'`]")


def _words(s: str) -> List[str]:
    """Строка → значимые слова: без чисел, единиц и организационного мусора."""
    s = re.sub(r"[^a-zа-я0-9]+", " ", (s or "").lower().replace("ё", "е"))
    out = []
    for w in s.split():
        if w.isdigit() or len(w) < 2:
            continue
        if w in _MATCH_STOP:
            continue
        if re.fullmatch(r"\d+[a-zа-я]*", w):   # «900мл», «3»
            continue
        out.append(w)
    return out


def _split_brand(s: str) -> Tuple[List[str], List[str]]:
    """Делит название на суть и бренд в кавычках.

    «Молоко "Простоквашино" 3,2% 900мл» → (['молоко'], ['простоквашино']).
    В iiko товар обычно заведён без бренда поставщика, поэтому бренд в оценку
    названия не берём. Но если кроме бренда ничего не осталось («Кока-Кола» 1л),
    бренд И ЕСТЬ название — тогда считаем по нему.
    """
    s = s or ""
    brand: List[str] = []
    for m in _QUOTED_RE.finditer(s):
        brand += _words(m.group(1))
    core = _words(_QUOTED_RE.sub(" ", s))
    if not core:
        return brand, []
    return core, brand


def _match_tokens(s: str) -> List[str]:
    """Название → значимые слова (бренд в кавычках отброшен, если есть суть)."""
    core, _brand = _split_brand(s)
    return core


def _word_hit(x: str, words: List[str]) -> bool:
    """Слово x встречается в words с точностью до склонения/окончания."""
    stem = x[:max(4, len(x) - 2)]
    return any(y.startswith(stem) or x.startswith(y[:max(4, len(y) - 2)]) for y in words)


def _match_score(inv_name: str, prod_name: str) -> float:
    """0..1 — насколько товар из iiko похож на строку накладной.

    Половина веса — доля слов накладной, нашедшихся в названии товара (с учётом
    склонений: сравниваем по началу слова), половина — общая похожесть строк.
    """
    a, brand = _split_brand(inv_name)
    b = _match_tokens(prod_name)
    if not a or not b:
        return 0.0
    coverage = sum(1 for x in a if _word_hit(x, b)) / len(a)
    ratio = SequenceMatcher(None, " ".join(a), " ".join(b)).ratio()
    score = 0.6 * coverage + 0.4 * ratio
    # Бренд не участвует в основной оценке, но решает спор равных кандидатов:
    # «Соус "Терияки"» и «Соус "Унаги"» иначе неотличимы по слову «соус».
    if brand and any(_word_hit(x, b) for x in brand):
        score = min(1.0, score + 0.08)
    return score


def _best_product_match(products: List[Dict[str, Any]], name: str
                        ) -> Tuple[Optional[Dict[str, Any]], float, bool]:
    """Лучший товар, оценка и уверенность.

    Возвращаем кандидата всегда, когда он дотянул до порога, даже если рядом есть
    похожий: подставить вариант и дать поправить одним тапом полезнее, чем оставить
    строку пустой. Спорные случаи помечаем неуверенными — редактор их подсветит.
    """
    best, best_s, second_s = None, 0.0, 0.0
    for p in products:
        s = _match_score(name, p.get("name") or "")
        if s > best_s:
            best, second_s, best_s = p, best_s, s
        elif s > second_s:
            second_s = s
    if best_s < _MATCH_MIN_SCORE:
        return None, best_s, False
    confident = best_s >= 0.75 and (best_s - second_s) >= 0.06
    return best, best_s, confident


def _match_memory_key(name: str) -> str:
    """Ключ памяти сопоставлений: значимые слова строки накладной.
    Через токены, а не сырую строку — чтобы «М/сл 82,5» и «М/сл  82.5»
    попадали в одну запись."""
    return " ".join(_match_tokens(name))


def remember_invoice_matches(client, org_id: str, items: List[Dict[str, Any]]):
    """Запоминает сопоставления, за которые человек отвечает: выбранные руками
    и уверенные авто-подборы, которые оператор оставил и отправил в iiko.
    Неуверенные (needs_review) не запоминаем — иначе зацементируем ошибку."""
    key = _catalog_key(client, org_id)
    for it in items or []:
        if not isinstance(it, dict):
            continue
        pid = it.get("iiko_product_id")
        name_key = _match_memory_key(it.get("name") or "")
        if not pid or not name_key or it.get("needs_review"):
            continue
        try:
            remember_invoice_match(key, name_key, pid,
                                   it.get("iiko_product_name") or "",
                                   name_raw=it.get("name") or "")
        except Exception as e:
            logger.warning(f"[invoice-match] память {key}: {e!r}")


_CATALOG_REFRESH_MIN = float(os.getenv("NOMENCLATURE_REFRESH_MIN", "10"))


def _apply_match(it: Dict[str, Any], catalog: List[Dict[str, Any]],
                 memory: Dict[str, Dict[str, Any]],
                 by_id: Dict[str, Dict[str, Any]], source_key: str = "") -> bool:
    """Ставит позиции товар iiko. True — если получилось.
    Порядок: память исправлений оператора → скоринг по словам."""
    best, score, confident = None, 0.0, False
    remembered = memory.get(_match_memory_key(it.get("name") or ""))
    if remembered and remembered.get("product_id") in by_id:
        best, score, confident = by_id[remembered["product_id"]], 1.0, True
    if best is None:
        best, score, confident = _best_product_match(catalog, it.get("name", ""))
    if not best:
        return False
    # Аннотируем саму позицию, чтобы сохранённый черновик нёс сопоставление
    # (редактор показывает «✓ сопоставлено», повторная отправка не теряет id).
    it["iiko_product_id"] = best.get("id")
    it["iiko_product_name"] = best.get("name") or it.get("name")
    it["iiko_unit"] = best.get("unit") or ""
    # По какому справочнику подставлено. У сети свой iiko-сервер почти на каждый
    # филиал: id товара с одного сервера на другом невалиден, поэтому при смене
    # филиала такое сопоставление надо переиграть, а не слать в iiko вслепую.
    it["iiko_source"] = source_key
    if best.get("containers"):
        it["iiko_containers"] = best["containers"]
    # Подобрано машиной, а не человеком: редактор пометит «проверьте»
    it["auto_matched"] = True
    it["match_score"] = round(score, 2)
    it["needs_review"] = not confident
    return True


async def _auto_match_items(client, org_id: str,
                             items: List[Dict[str, Any]]
                             ) -> Tuple[List[Dict], List[str], List[str]]:
    """Сопоставляет позиции с номенклатурой iiko по имени.
    Возвращает (sendable_items, unmatched_names, unit_warnings).
    sendable_items — формат для create_incoming_invoice (product, amount, price, sum).
    Единицы: гр↔кг и мл↔л конвертируются автоматически (с пересчётом цены);
    несовместимые (уп vs кг и т.п.) не трогаем, но честно предупреждаем.
    """
    key_now = _catalog_key(client, org_id)
    # Позиция, подобранная по справочнику ДРУГОГО сервера (сопоставляли до выбора
    # филиала), здесь считается несопоставленной — id оттуда в этот iiko не годится.
    for it in (items or []):
        src = it.get("iiko_source")
        if src and src != key_now and it.get("auto_matched"):
            it.pop("iiko_product_id", None)
            it.pop("iiko_product_name", None)
            it.pop("iiko_unit", None)
    todo = [it for it in (items or []) if not it.get("iiko_product_id")]
    if todo:
        catalog, key = await _nomenclature_catalog(client, org_id)
        by_id = {p["id"]: p for p in catalog if p.get("id")}
        try:
            memory = get_invoice_match_memory(key)
        except Exception as e:
            logger.warning(f"[invoice-match] память {key}: {e!r}")
            memory = {}
        if catalog:
            todo = [it for it in todo if not _apply_match(it, catalog, memory, by_id, key)]
        # Что-то не нашлось — возможно, товар завели в iiko только что, а у нас
        # снимок справочника суточной давности. Обновляем и пробуем ещё раз.
        synced = iiko_products_synced_at(key)
        stale_enough = (not synced or
                        (datetime.now() - synced) > timedelta(minutes=_CATALOG_REFRESH_MIN))
        if todo and stale_enough:
            fresh, _key = await _nomenclature_catalog(client, org_id, force=True)
            if fresh:
                by_id = {p["id"]: p for p in fresh if p.get("id")}
                todo = [it for it in todo if not _apply_match(it, fresh, memory, by_id, key)]

    sendable, unmatched, unit_warnings = [], [], []
    for it in (items or []):
        prod_id = it.get("iiko_product_id")
        prod_unit = it.get("iiko_unit") or ""
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
        auto_n = sum(1 for x in items if x.get("auto_matched"))
        names_preview = ", ".join(unmatched[:5])
        more = f" и ещё {len(unmatched)-5}" if len(unmatched) > 5 else ""
        done_line = (f"✅ Подобрано автоматически: {auto_n} из {len(items)}\n" if auto_n else "")
        await _tg_edit(chat_id, msg_id,
            f"{done_line}⚠️ Осталось выбрать вручную ({len(unmatched)}): {names_preview}{more}\n\n"
            f"📋 Накладная сохранена как черновик. Открой Mini App → Расходы → Накладные, "
            f"нажми ✏️ — подобранное уже проставлено, поправь остальное и отправь.")
        _drop_pending(chat_id)
        return

    # ---- Сохраняем локальный черновик (review-first: в iiko НЕ создаём) ----
    # Оператор проверит/исправит филиал, поставщика, дату, позиции в Mini App
    # и сам нажмёт «Создать черновик в iiko». Так нет дублей/сирот в iiko, и
    # ошибки сопоставления (филиал/поставщик) исправляются без перепробивки.
    save_bot_invoice(
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
        + "\nОткрой Mini App → Расходы → Накладные → ✏️ проверь филиал/поставщика "
        "и нажми «Создать черновик в iiko».")
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


async def _prematch_pending(pending: Dict[str, Any]) -> None:
    """Сопоставляет распознанные позиции сразу после фото — до выбора поставщика
    и филиала.

    Оператор открывает редактор именно на этом шаге, и раньше видел там голые
    строки: сопоставление запускалось только на отправке в iiko, то есть после
    того, как он уже всё промапил руками. Берём справочник филиала по умолчанию
    (того же, по которому редактор ищет товары). Если на шаге 4 выбран филиал с
    другого iiko-сервера, _auto_match_items переиграет сопоставление сам —
    позиции помечены ключом справочника (iiko_source).
    """
    result = pending.get("result") or {}
    items = result.get("items") or []
    if not items:
        return
    rests = _get_user_orgs(pending["telegram_id"])
    rest = next((r for r in rests if r.get("api_login") != "demo"), None)
    if not rest:
        return
    try:
        client = _bot_iiko_client(rest)
        _s, unmatched, _w = await _auto_match_items(client, rest["organization_id"], items)
        logger.info(f"[prematch] {pending.get('pending_id')}: "
                    f"{len(items) - len(unmatched)} из {len(items)} сопоставлено")
    except Exception as e:
        logger.warning(f"[prematch] {pending.get('pending_id')}: {e!r}")


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

    # Сопоставляем сразу: редактор открывают на этом шаге, а не после выбора филиала.
    if status_msg_id:
        await _tg_edit(chat_id, status_msg_id, "🔎 Сопоставляю позиции с номенклатурой iiko...")
    await _prematch_pending(pending)
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
    if TELEGRAM_WEBHOOK_SECRET:
        if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != TELEGRAM_WEBHOOK_SECRET:
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

            # Трекер задач: отметка «сделал / не успел». Не зависит от pending —
            # это отдельный поток, приходит из уведомлений, а не из накладной.
            if action.startswith("task:"):
                _, what, sid = (action.split(":", 2) + ["", ""])[:3]
                who = (cb.get("from") or {}).get("first_name") or str(chat_id)
                try:
                    task = answer_bot_task(int(sid), "done" if what == "done" else "later", who)
                except Exception as e:
                    logger.warning(f"[task] отметка {sid}: {e!r}")
                    task = None
                if not task:
                    await _tg_edit(chat_id, msg_id, "⚠️ Задача не найдена (возможно, устарела)")
                    return {"ok": True}
                mark = "✅ <b>Сделано</b>" if what == "done" else "⏳ <b>Отложено</b>"
                when = local_now().strftime("%d.%m %H:%M")
                money_line = (f"\n💸 На кону было: {format_money(task['money'])}"
                              if task.get("money") else "")
                await _tg_edit(chat_id, msg_id,
                               f"📌 <b>{task['title']}</b>\n<i>{task['branch']}</i>\n\n"
                               f"{task.get('detail') or ''}{money_line}\n\n"
                               f"{mark} · {who}, {when}")
                return {"ok": True}

            # Журналы: выбор зоны/замера, выбор филиала, «Готово», отмена.
            if action.startswith("jr:"):
                who = int((cb.get("from") or {}).get("id") or chat_id)
                bits = action.split(":")
                op = bits[1] if len(bits) > 1 else ""
                orgs = _journal_orgs_for(who)
                if op == "cancel":
                    _JOURNAL_MODE.pop(chat_id, None)
                    await _tg_edit(chat_id, msg_id, "✖️ <i>Сдача отменена</i>")
                    return {"ok": True}
                if op == "done":
                    st = _journal_mode(chat_id)
                    if not st or st["kind"] != "clean":
                        await _tg_edit(chat_id, msg_id, "⚠️ Сдача уже закрыта или устарела.")
                        return {"ok": True}
                    await _journal_finish_clean(chat_id, who, st)
                    return {"ok": True}
                if not orgs:
                    await _tg_send(chat_id, "Вы не привязаны ни к одному филиалу с журналами.")
                    return {"ok": True}
                if op in ("c", "t"):    # jr:c:<зона>:<ччмм>:<филиал> | jr:t:<ччмм>:<филиал>
                    zone = bits[2] if op == "c" else ""
                    code = bits[3] if op == "c" else bits[2]
                    short = bits[4] if op == "c" else bits[3]
                    slot = _slot_from_code(code)
                    if not slot:
                        await _tg_edit(chat_id, msg_id, "⚠️ Задание устарело — дождитесь следующего.")
                        return {"ok": True}
                    org = _org_by_short(orgs, short) or orgs[0]
                    await _journal_start(chat_id, who, org,
                                         "temp" if op == "t" else "clean", zone, slot)
                    return {"ok": True}
                return {"ok": True}

            # Оживить исчерпанную ссылку-приглашение. Ссылка постоянная (подпись
            # не меняется), «умирает» только счётчик активаций — его и сбрасываем.
            if action.startswith("invreset:"):
                who_id = int((cb.get("from") or {}).get("id") or 0)
                short = action.split(":", 1)[1]
                owned = [r for r in get_all_restaurants()
                         if int(r["telegram_id"]) == who_id]
                rest = next((r for r in owned
                             if (r["organization_id"] or "").replace("-", "")[:12] == short), None)
                if not rest:
                    await _tg_edit(chat_id, msg_id,
                                   "⚠️ Филиал не найден или он не ваш.")
                    return {"ok": True}
                org_id = rest["organization_id"]
                set_app_state(f"invite_used:{org_id}", "")
                await _tg_edit(
                    chat_id, msg_id,
                    f"♻️ Ссылка снова активна: <b>{rest.get('organization_name')}</b>\n"
                    f"Доступно активаций: {INVITE_MAX_USES}\n\n"
                    f"{invite_link_for(org_id)}")
                return {"ok": True}

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
            # Режим журнала перехватывает фото ДО распознавания накладной —
            # иначе снимок зала уедет в Claude и заведёт мусорную накладную.
            jst = _journal_mode(chat_id)
            if jst and jst["kind"] == "clean":
                await _journal_take_photo(chat_id, telegram_id, jst, file_id, image_bytes)
                return {"ok": True}
            mgid = message.get("media_group_id")
            if mgid:
                await _buffer_album_photo(chat_id, telegram_id, username, mgid,
                                          {"file_id": file_id, "bytes": image_bytes, "mime": mime})
            else:
                await _recognize_invoice_and_create_pending(chat_id, telegram_id, username, file_id, image_bytes, mime)
            return {"ok": True}

        # === TEXT ===
        text = message.get("text") or ""
        # Показания температуры принимаем только в режиме замера: девять чисел
        # в обычном чате — это скорее всего не термометр, а что-то ещё.
        jst = _journal_mode(chat_id)
        if jst and jst["kind"] == "temp" and text and not text.startswith("/"):
            await _journal_take_temp(chat_id, telegram_id, jst, text)
            return {"ok": True}
        if text == "/start" or text.startswith("/start "):
            parts = text.split(maxsplit=1)
            # Вручную набранный «/start <текст>» может содержать HTML — неэкранированный
            # источник, попав в user_acquisition, ломал бы parse_mode=HTML во всех
            # пушах владельцу (/stats, /today, дайджест). Deep-link и так [A-Za-z0-9_-].
            raw_source = parts[1].strip() if len(parts) > 1 else ""
            source = re.sub(r"[^0-9A-Za-z_\-]", "", raw_source)[:64] or "direct"

            # Мини-апп смен. Обычная https-ссылка сюда не годится: открытая
            # даже внутри Telegram, она попадает во встроенный браузер, а не в
            # Mini App, и подписи там нет. Поэтому сотрудник приходит к боту, а
            # тот отвечает кнопкой web_app — она открывает настоящий Mini App.
            if source.startswith("join_") or source.startswith("emp_"):
                from database import org_by_join_code, staff_access_by_code, branch_name
                code = source.split("_", 1)[1]
                if source.startswith("join_"):
                    org = org_by_join_code(code)
                    if not org:
                        await _tg_send(chat_id, "❌ Код филиала недействителен. "
                                                "Попросите управляющего показать новый.")
                        return {"ok": True}
                    await _tg_send(
                        chat_id,
                        f"<b>{branch_name(org)}</b>\n\nНажмите кнопку и выберите себя из списка — "
                        f"это делается один раз.",
                        {"inline_keyboard": [[{"text": "Выбрать себя",
                                               "web_app": {"url": f"{_miniapp_url()}/?j={code}"}}]]})
                    return {"ok": True}
                acc = staff_access_by_code(code)
                if not acc:
                    await _tg_send(chat_id, "❌ Ссылка недействительна. "
                                            "Попросите управляющего выдать новую.")
                    return {"ok": True}
                await _tg_send(
                    chat_id,
                    f"<b>Смены · {branch_name(acc['organization_id'])}</b>\n\n"
                    f"Здесь ваш график, оплата и открытие смены.",
                    {"inline_keyboard": [[{"text": "Мои смены",
                                           "web_app": {"url": f"{_miniapp_url()}/?e={code}"}}]]})
                return {"ok": True}

            # Приглашение управляющего: /start inv_<org>_<подпись> на один филиал
            # или inv<N>_<org>…_<подпись> на несколько (один человек — два адреса).
            # Подпись на ENCRYPTION_KEY — по чужой ссылке к филиалу не подключиться.
            if re.match(r"^inv\d*_", source):
                org_ids = _orgs_from_invite(source)
                if not org_ids:
                    await _tg_send(chat_id, "❌ Ссылка недействительна или устарела.")
                    return {"ok": True}
                rests = [r for r in get_all_restaurants()
                         if r["organization_id"] in org_ids]
                if len(rests) != len(org_ids):
                    await _tg_send(chat_id, "❌ Филиал не найден.")
                    return {"ok": True}

                # Ссылка на две активации: одну владелец тратит на проверку,
                # вторую — сам управляющий. Дальше мертва, чтобы пересланная
                # ссылка не открывала филиал кому попало. Повторный вход тем же
                # человеком активацию не тратит. У ссылки на пару филиалов свой
                # счётчик: она отдельная ссылка, а не сумма одиночных.
                used_key = f"invite_used:{','.join(org_ids)}"
                names = ", ".join(r.get("organization_name", "филиал") for r in rests)
                used = [x for x in (get_app_state(used_key) or "").split(",") if x]
                if str(telegram_id) not in used:
                    if len(used) >= INVITE_MAX_USES:
                        await _tg_send(chat_id,
                                       "🔒 Ссылка исчерпана — по ней уже подключились.\n"
                                       "Попросите владельца выдать новую.")
                        # Кнопка прямо в предупреждении: владелец узнаёт о проблеме
                        # и чинит её одним нажатием, не вспоминая команду.
                        kb = {"inline_keyboard": [
                            [{"text": f"♻️ Оживить: {r.get('organization_name') or 'филиал'}",
                              "callback_data": f"invreset:{(r['organization_id'] or '').replace('-', '')[:12]}"}]
                            for r in rests]}
                        for owner in {r["telegram_id"] for r in rests}:
                            try:
                                await _tg_send(owner,
                                               f"⚠️ По ссылке <b>{names}</b> "
                                               f"пытался подключиться @{username or telegram_id} "
                                               f"(id {telegram_id}) — активации исчерпаны.", kb)
                            except Exception:
                                pass
                        return {"ok": True}
                    set_app_state(used_key, ",".join(used + [str(telegram_id)]))

                who = username or str(telegram_id)
                add_branch_manager(org_ids, telegram_id, who)
                what = "филиалы" if len(rests) > 1 else "филиал"
                await _tg_send(chat_id,
                               f"✅ Вы подключены как управляющий:\n<b>{names}</b>\n\n"
                               f"Открывайте мини-апп — увидите данные только своего "
                               f"{what}. Задачи будут приходить сюда, отмечайте кнопками.")
                for owner in {r["telegram_id"] for r in rests}:  # владельцу — кто подключился
                    try:
                        await _tg_send(owner,
                                       f"👤 К <b>{names}</b> подключился управляющий: "
                                       f"@{who} (id {telegram_id})")
                    except Exception as e:
                        logger.warning(f"[invite] notify owner: {e!r}")
                return {"ok": True}
            # Человек мог зарегистрироваться на сайте раньше, чем написал боту.
            # Привязываем ТОЛЬКО по одноразовому коду из ссылки: он и есть
            # доказательство, что телеграм принадлежит тому, кто регистрировался.
            if raw_source.startswith("link_"):
                try:
                    from database import claim_web_signup_by_code
                    if claim_web_signup_by_code(raw_source[5:], telegram_id):
                        logger.info(f"[signup] телеграм подтверждён: {telegram_id}")
                        # Ресторан переехал на этот телеграм-id, а ссылка, по
                        # которой человек смотрел данные до подтверждения, теперь
                        # ведёт в пустой аккаунт. Не сказать об этом — значит
                        # оставить его перед сайтом без данных и без объяснения.
                        import mountain_routes as _mr2
                        link = f"{MINIAPP_URL}/m/{_mr2.token_for(telegram_id)}"
                        await _tg_send(
                            chat_id,
                            "✅ Телеграм подключён — уведомления по вашему ресторану "
                            "будут приходить сюда.\n\n"
                            "Дальше открывайте кабинет по <b>этой</b> ссылке, она теперь "
                            f"ваша постоянная:\n{link}\n\n"
                            "Прежняя ссылка со страницы регистрации больше не работает — "
                            "так сделано нарочно, чтобы по ней не попал кто-то ещё.")
                except Exception as e:
                    logger.warning(f"[signup] claim {telegram_id}: {e!r}")
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
        # ── Админка владельца: филиалы, доступы, отчёт по задачам ──
        # Владелец — тот, к кому привязаны рестораны. Управляющий эти команды
        # не получит: у него в restaurants нет своих строк, только доступ.
        if text in ("/branches", "/filiali"):
            owned = [r for r in get_all_restaurants() if r["telegram_id"] == telegram_id]
            if not owned:
                await _tg_send(chat_id, "Команда доступна владельцу сети.")
                return {"ok": True}
            mgrs = get_branch_managers()
            L = ["🏢 <b>Филиалы и доступы</b>", ""]
            for r in owned:
                org, nm = r["organization_id"], r.get("organization_name", "—")
                who = [m for m in mgrs if m["organization_id"] == org]
                L.append(f"<b>{nm}</b>")
                if who:
                    L.append("  👤 " + ", ".join(f"@{m.get('name') or m['telegram_id']}"
                                                 for m in who))
                else:
                    L.append("  ⚠️ управляющий не подключён")
                L.append(f"  🔗 {invite_link_for(org)}")
                L.append("")
            L.append("Отправьте ссылку управляющему — он нажмёт «Старт» "
                     "и получит доступ только к своему филиалу.")
            L.append("Ссылка перестала пускать? — /ozhivit")
            await _tg_send(chat_id, "\n".join(L))
            return {"ok": True}

        # Ссылка не ломается и не протухает — кончаются активации (INVITE_MAX_USES).
        # Даём владельцу сбросить счётчик самому, не дожидаясь правки в коде.
        if text in ("/ozhivit", "/reset"):
            owned = [r for r in get_all_restaurants() if r["telegram_id"] == telegram_id]
            if not owned:
                await _tg_send(chat_id, "Команда доступна владельцу сети.")
                return {"ok": True}
            rows = [[{"text": r.get("organization_name") or "Филиал",
                      "callback_data": f"invreset:{(r['organization_id'] or '').replace('-', '')[:12]}"}]
                    for r in owned]
            await _tg_send(chat_id,
                           "♻️ <b>Оживить ссылку</b>\n\n"
                           "Выберите филиал — счётчик активаций обнулится, "
                           "и по той же ссылке снова можно будет подключиться.",
                           {"inline_keyboard": rows})
            return {"ok": True}

        if text in ("/tasks", "/zadachi"):
            owned = [r for r in get_all_restaurants() if r["telegram_id"] == telegram_id]
            if not owned:
                await _tg_send(chat_id, "Команда доступна владельцу сети.")
                return {"ok": True}
            names = {r.get("organization_name") for r in owned}
            rows = [t for t in get_bot_tasks_all(14) if t.get("branch") in names]
            if not rows:
                await _tg_send(chat_id, "За две недели задач не отправлялось.")
                return {"ok": True}
            done = [t for t in rows if t.get("status") == "done"]
            openn = [t for t in rows if t.get("status") == "open"]
            L = [f"📋 <b>Задачи за 14 дней: {len(done)} из {len(rows)} закрыто</b>", ""]
            by: Dict[str, List[Dict[str, Any]]] = {}
            for t in rows:
                by.setdefault(t.get("branch") or "—", []).append(t)
            for nm, ts in sorted(by.items(), key=lambda kv: -sum(
                    1 for x in kv[1] if x.get("status") == "open")):
                d = sum(1 for x in ts if x.get("status") == "done")
                icon = "✅" if d == len(ts) else ("🔴" if d == 0 else "🟡")
                L.append(f"{icon} {nm[:28]} — {d}/{len(ts)}")
            if openn:
                money = sum(float(t.get("money") or 0) for t in openn)
                L.append(f"\nБез ответа {len(openn)} задач"
                         + (f" · на кону {format_money(money)}" if money else ""))
            await _tg_send(chat_id, "\n".join(L))
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


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)


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


def _last_completed_week() -> Optional[Tuple[str, Dict[str, Any]]]:
    """Последняя ПОЛНОСТЬЮ прошедшая неделя плана → (ym, {widx, from, to}).

    Недели те же, что во вкладке «План» (7-дневные куски от 1-го числа), иначе
    похвала «план недели выполнен» считалась бы не по той неделе, что видит
    владелец. В начале месяца берём последнюю неделю прошлого месяца.
    """
    today = local_now().date()
    for ym in (today.strftime("%Y-%m"),
               (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")):
        done = [w for w in _month_weeks(ym) if date.fromisoformat(w["to"]) < today]
        if done:
            return ym, done[-1]
    return None


async def _weekplan_status(org: str, real_org: str, user: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """План vs факт за последнюю завершённую неделю — основа похвалы «план выполнен».

    Один запрос продаж на филиал (не пять, как в /api/weekplan): ассистент и так
    делает 7 обращений к iiko на филиал, а филиалов бывает 13.
    """
    wk = _last_completed_week()
    if not wk:
        return None
    ym, w = wk
    plan = {t: 0 for t in PLAN_TYPES}
    for r in get_weekly_plans(real_org, ym):
        if r["widx"] == w["widx"] and r["ch_type"] in PLAN_TYPES:
            plan[r["ch_type"]] = r["planned"]
    if not any(plan.values()):
        return None  # плана на неделю нет — хвалить не за что
    fact = {t: 0 for t in PLAN_TYPES}
    try:
        s = await sales(org, "custom", w["from"], w["to"], None, user=user)
        for c in s.get("channels", []):
            fact[_classify_channel(c.get("name"))] += int(c.get("orders", 0) or 0)
    except Exception as e:
        logger.warning(f"[assistant] план недели {real_org}: {e!r}")
        return None
    TITLES = {"hall": "зал", "pickup": "вынос", "delivery": "доставка", "agg": "агрегаторы"}
    return {
        "week": f"{w['from'][5:]}–{w['to'][5:]}",
        # единица подписана явно: без неё модель пересказывала план как «гостей»
        "unit": "заказов",
        "types": [{"type": TITLES[t], "plan_orders": plan[t], "fact_orders": fact[t],
                   "done": fact[t] >= plan[t]}
                  for t in PLAN_TYPES if plan[t] > 0],
    }


def _late_signal(ld: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Ответ /api/late-deliveries → сигнал «опоздавшие заказы» для оценки.

    Отдельной функцией, потому что на форме этого ответа уже обожглись: late_pct
    лежит ВЕРХНИМ полем, а оба вызывающих читали ld["summary"]["late_pct"].
    Ключа summary там нет и не было — критерий молчал у всех девяти филиалов,
    хотя данные приходили (Алиева: 429 доставок, 52 опоздали, 12%).
    """
    if not isinstance(ld, dict) or ld.get("error"):
        return None
    delivered = int(ld.get("delivered") or 0)
    pct = ld.get("late_pct")
    if pct is None or not delivered:
        return None      # доставок нет — филиал не «идеальный», критерий выпадает
    return {"late_pct": pct, "deliveries": delivered, "late": int(ld.get("late") or 0)}


# Показатели недели для вкладки «План» — те же, что в листе «Неделя NN» у
# владельца: время приготовления, курьер в дороге, опоздания, рейтинг.
# Всё это сайт уже достаёт, просто лежало на разных вкладках.
# ключ, подпись, единица, «меньше — лучше», порог сдвига, порог в процентах?
# Порог заказов намеренно относительный: в штуках «плюс три заказа» проходило
# у всех девяти филиалов разом, и открытку получала вся сеть просто за сезон.
PLAN_METRICS = [
    ("kitchen", "Время приготовления", "мин", True, 2.0, False),
    ("road", "Курьер в дороге", "мин", True, 2.0, False),
    ("idle", "Ожидание курьера", "мин", True, 2.0, False),
    ("late_pct", "Опоздавших заказов", "%", True, 2.0, False),
    # Опоздания сотрудников берутся из нашей же базы (снимок явок), а не из
    # iiko: лишних запросов на филиал не добавляют, а на собрании это второй
    # вопрос после заказов — «кто приходит вовремя».
    ("staff_late_pct", "Опозданий сотрудников", "%", True, 3.0, False),
    # Истории рейтинга по неделям пока нет: это снимок на момент открытия.
    # Подписываем честно, чтобы при просмотре старой недели его не приняли за
    # историческое значение.
    ("rating", "Рейтинг сейчас", "", False, 0.1, False),
    ("orders", "Заказов за неделю", "", False, 10.0, True),
]


def cause_window(d_from: date, done: int) -> Tuple[date, date]:
    """Окно, за которое считаем ПРИЧИНУ опозданий: семь дней до последнего
    закрытого дня.

    Неделя владельца заканчивается во вторник — в день собрания, а не в
    воскресенье. Во вторник ISO-неделя закрыта ровно на один день, и «причина
    опозданий» посчиталась бы по одному понедельнику: это не причина, это один
    вечер. Скользящие семь дней дают ту самую неделю, которая на собрании и
    подводится. Когда неделя закрыта целиком, окно совпадает с ISO-неделей —
    и второй запрос в iiko не нужен.
    """
    if not done:
        return d_from, d_from
    to = d_from + timedelta(days=done - 1)
    return to - timedelta(days=6), to


def plan_wins(now: Dict[str, Any], prev: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Что реально стало лучше неделя к неделе.

    Поздравлять надо за движение, а не за «и так хорошо»: филиал с вечными
    18 минутами не должен получать открытку каждую неделю. И порог обязателен —
    минута дрожи или 0,02 рейтинга это шум, а поздравления за шум обесцениваются
    быстрее, чем успевают кого-то порадовать.
    """
    out: List[Dict[str, Any]] = []
    for key, label, unit, lower_better, eps, rel in PLAN_METRICS:
        a, b = now.get(key), prev.get(key)
        if a is None or b is None:
            continue
        diff = (float(b) - float(a)) if lower_better else (float(a) - float(b))
        if diff <= 0:
            continue
        if diff < (abs(float(b)) * eps / 100 if rel else eps):
            continue
        out.append({"key": key, "label": label, "unit": unit,
                    "from": round(float(b), 2), "to": round(float(a), 2),
                    "better": round(diff, 2),
                    "pct": round(diff / float(b) * 100) if float(b) else None})
    out.sort(key=lambda w: -(w["pct"] or 0))
    return out


def plan_losses(now: Dict[str, Any], prev: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Что стало хуже — теми же порогами, что и plan_wins.

    На собрании называют обе стороны. Считать «ухудшения» отдельным правилом
    нельзя: на одних порогах филиал попадал бы и в улучшившиеся, и в
    просевшие по одной и той же дрожи в полминуты.
    """
    out: List[Dict[str, Any]] = []
    for key, label, unit, lower_better, eps, rel in PLAN_METRICS:
        a, b = now.get(key), prev.get(key)
        if a is None or b is None:
            continue
        diff = (float(a) - float(b)) if lower_better else (float(b) - float(a))
        if diff <= 0:
            continue
        if diff < (abs(float(b)) * eps / 100 if rel else eps):
            continue
        out.append({"key": key, "label": label, "unit": unit,
                    "from": round(float(b), 2), "to": round(float(a), 2),
                    "worse": round(diff, 2),
                    "pct": round(diff / float(b) * 100) if float(b) else None})
    out.sort(key=lambda w: -(w["pct"] or 0))
    return out


def _rating_signal(rv: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Отзывы филиала → средний балл для оценки, приведённый к пятибалльной шкале.

    Приводить обязательно: Wolt отдаёт 8,8 из 10, 2ГИС — 4,6 из 5. Среднее
    «сырых» чисел дало бы филиалу с Wolt фору в полтора балла из воздуха.
    """
    if not isinstance(rv, dict):
        return None
    vals = []
    for p in rv.get("platforms") or []:
        r, scale = p.get("rating"), float(p.get("scale") or 5)
        if r is None or scale <= 0:
            continue
        vals.append(float(r) / scale * 5)
    if not vals:
        return None
    return {"avg": round(sum(vals) / len(vals), 2), "platforms": len(vals)}


def _shifts_signal(pending: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Непринятые кассовые смены → сигнал. Ноль непринятых — это результат,
    а отсутствие ответа (облачный iiko) — молчание, а не сто баллов."""
    if not isinstance(pending, dict) or pending.get("count") is None:
        return None
    return {"pending": int(pending["count"])}


def _plan_days_and_total(plans: Dict[int, Dict[str, int]], upto_dow: int) -> int:
    """Сумма плана по дням недели включительно до upto_dow (1..7).

    Нужна для незавершённой недели: сравнивать факт трёх дней с планом семи —
    значит выдать филиалу ноль просто за то, что сегодня среда.
    """
    return sum(sum(v.values()) for d, v in plans.items() if 1 <= int(d) <= upto_dow)


async def _plan_exec_signal(org: str, user: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Выполнение плана заказов.

    Берём последнюю ЗАВЕРШЁННУЮ неделю — она честнее всего. Но если плана на неё
    нет, а на текущую есть (владелец только что проставил план на всю сеть),
    считаем текущую ПРОПОРЦИОНАЛЬНО прошедшим дням: план понедельника–среды
    против факта понедельника–среды. Иначе критерий молчал бы до воскресенья,
    хотя план уже введён — ровно на это и жаловались 25.08.
    """
    from database import get_day_plans
    today = local_now().date()
    mon_this = today - timedelta(days=today.isoweekday() - 1)
    done_wk = _iso_week_of(mon_this - timedelta(days=1))
    cur_wk = _iso_week_of(today)

    plans_done = get_day_plans([org], done_wk).get(org) or {}
    if _plan_days_and_total(plans_done, 7):
        wk, plans, upto, partial = done_wk, plans_done, 7, False
    else:
        plans_cur = get_day_plans([org], cur_wk).get(org) or {}
        # Считаем с первого же завершённого дня. Порог в два дня я поставил из
        # осторожности — и во вторник критерий снова молчал, хотя план введён и
        # понедельник закрыт. План на день берётся из средней за шесть недель,
        # так что день против дня — сравнение честное, а не качели.
        upto = today.isoweekday() - 1
        if upto < 1 or not _plan_days_and_total(plans_cur, upto):
            return None
        wk, plans, partial = cur_wk, plans_cur, True

    total_plan = _plan_days_and_total(plans, upto)
    if not total_plan:
        return None
    d_from, d_to = _iso_week_bounds(wk)
    if partial:
        d_to = d_from + timedelta(days=upto - 1)
    try:
        fact = _fold_by_dow(await _orders_by_day(org, d_from, d_to, user))["dow"]
    except Exception as e:
        logger.warning(f"[score] план/факт {org}: {e!r}")
        return None
    total_fact = sum(sum(v.values()) for d, v in fact.items() if 1 <= int(d) <= upto)
    return {"pct": round(total_fact / total_plan * 100, 1), "week": wk,
            "plan": total_plan, "fact": total_fact,
            "days": upto, "partial": partial}


async def _week_over_week(org: str, user: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Эта неделя против прошлой: выручка, средний чек, гости.

    Считаем здесь, а не берём из /api/dashboard: там revenue_change/avg_check_change
    захардкожены нулём, то есть дельт у нас до сих пор не было вообще.
    """
    from fastapi import Response as _Resp
    today = local_now().date()
    # Берём две ПОЛНЫЕ недели: в понедельник текущая неделя — это один день, и
    # сравнение «1 день против 7» дало бы фальшивое падение на 85%.
    cur_to = today - timedelta(days=today.weekday() + 1)     # воскресенье прошлой недели
    cur_from = cur_to - timedelta(days=6)
    prev_to, prev_from = cur_from - timedelta(days=1), cur_from - timedelta(days=7)

    async def rev(d_from, d_to):
        d = await dashboard(org, _Resp(), "custom", d_from.isoformat(), d_to.isoformat(), None, user=user)
        return (float(d.get("revenue_today") or 0), float(d.get("avg_check") or 0),
                float(d.get("guests") or 0), float(d.get("open_orders") or 0))

    try:
        cur, prev = await asyncio.gather(rev(cur_from, cur_to), rev(prev_from, prev_to))
    except Exception as e:
        logger.warning(f"[assistant] неделя к неделе {org}: {e!r}")
        return None
    if not prev[0]:
        return None  # прошлой недели нет — сравнивать не с чем

    def pct(a: float, b: float) -> Optional[float]:
        return round((a - b) / b * 100, 1) if b else None

    return {
        "week": f"{cur_from.strftime('%d.%m')}–{cur_to.strftime('%d.%m')}",
        "prev_week": f"{prev_from.strftime('%d.%m')}–{prev_to.strftime('%d.%m')}",
        "revenue_pct": pct(cur[0], prev[0]),
        "avg_check_pct": pct(cur[1], prev[1]),
        "guests_pct": pct(cur[2], prev[2]),
        # Рост КОЛИЧЕСТВА заказов — отдельный критерий оценки филиала: выручку
        # можно поднять ценой, а заказы это гости. open_orders = OrdersCount из OLAP.
        "orders_pct": pct(cur[3], prev[3]),
        "orders_now": round(cur[3]), "orders_was": round(prev[3]),
        "revenue_now": round(cur[0]), "revenue_was": round(prev[0]),
    }


# Фудкост в наблюдениях «Что изменилось». ВЫКЛЮЧЕН: у самохостед-iikoWeb он
# считается из техкарт × цен по всему меню — самый тяжёлый показатель сбора, а
# в оценку филиала на «Горе» он не входит вовсе (см. scoring.py — там его нет).
# Владелец попросил не считать. Вернуть: ASSISTANT_FOODCOST=1.
ASSISTANT_FOODCOST = os.getenv("ASSISTANT_FOODCOST", "0") == "1"


async def _gather_assistant_signals(user: Dict[str, Any]) -> Dict[str, Any]:
    """Компактные сигналы по всем филиалам ЗАЛОГИНЕННОГО клиента (1..N) для ИИ-ассистента.
    Сбой одного филиала не роняет остальные. Семафор ограничивает нагрузку на iiko
    у больших сетей (десятки филиалов)."""
    from fastapi import Response as _Resp
    rests = get_restaurants(user.get("id", 0))
    out_branches: List[Dict[str, Any]] = []
    sem = asyncio.Semaphore(6)   # не более 6 филиалов одновременно
    # Показатели внутри филиала теперь идут параллельно, поэтому потолок держим
    # на самих запросах, а не на филиалах: без него шесть филиалов × девять
    # показателей дали бы полсотни одновременных обращений к серверу клиента.
    call_sem = asyncio.Semaphore(int(os.getenv("ASSISTANT_MAX_CALLS", "12")))

    async def one(r: Dict[str, Any]) -> Dict[str, Any]:
        org = r.get("organization_id")
        name = r.get("organization_name") or "Филиал"
        b: Dict[str, Any] = {"name": name, "foodcost": None, "revenue": None,
                             "cancels": None, "writeoffs": None, "reviews": [],
                             "peak": None, "has_recipes": None, "has_socials": None,
                             "stop_count": None, "stop_items": [],
                             "changes": None, "weekplan": None, "prev": None}

        async def safe(label: str, coro):
            """Один показатель. Падение одного не должно ронять остальные —
            поэтому не gather(return_exceptions=True) снаружи, а try здесь:
            так в лог попадает ИМЯ показателя, а не безымянное исключение."""
            async with call_sem:
                try:
                    return await coro
                except Exception as e:
                    logger.warning(f"[assistant] {label} {name}: {e!r}")
                    return None

        # Девять показателей независимы друг от друга, а шли строго по очереди:
        # время филиала было СУММОЙ девяти походов в iiko, и сбор по сети занимал
        # полторы-две минуты. Теперь они идут разом — время филиала равно самому
        # медленному запросу. Общий семафор call_sem держит нагрузку на сервер
        # клиента в узде: параллелизм вырос не в девять раз, а до потолка.
        jobs = {
            "dashboard": safe("dashboard", dashboard(org, _Resp(), "month", None, None, None, user=user)),
            "changes": safe("дельты", _week_over_week(org, user)),
            "weekplan": safe("weekplan", _weekplan_status(org, _get_org_id(org, user), user)),
            "cancels": safe("cancels", cancels(org, "month", user)),
            "reviews": safe("reviews", branch_reviews(org, user)),
            "late": safe("опоздания", late_deliveries(org, "month", None, None, 1, user)),
            "heatmap": safe("heatmap", load_heatmap(org, "month", None, user=user)),
            "stoplist": safe("stoplist", get_stoplist(org, user)),
            "writeoffs": safe("writeoffs", writeoffs(org, "month", user)),
        }
        if ASSISTANT_FOODCOST:
            jobs["foodcost"] = safe("foodcost", foodcost(org, "month", 1, user))
        done = dict(zip(jobs.keys(), await asyncio.gather(*jobs.values())))

        fc = done.get("foodcost")
        if fc:
            b["has_recipes"] = fc.get("fact_pct") is not None
            if fc.get("fact_pct") is not None:
                b["foodcost"] = {"fact_pct": fc["fact_pct"], "norm_pct": fc.get("norm_pct", 30),
                                 "fact_sum": fc.get("fact_sum"), "coverage": fc.get("coverage_pct")}
        if done.get("dashboard"):
            b["revenue"] = done["dashboard"].get("revenue_today")  # revenue_today = выручка периода
        b["changes"] = done.get("changes")
        b["weekplan"] = done.get("weekplan")
        if done.get("cancels"):
            b["cancels"] = done["cancels"].get("total_amount")
        rv = done.get("reviews")
        if rv:
            b["has_socials"] = bool(rv.get("platforms"))
            b["reviews"] = [{"label": p.get("label"), "rating": p.get("rating"),
                             "delta": p.get("delta")} for p in rv.get("platforms", [])]
        if done.get("late") is not None:
            b["late"] = _late_signal(done["late"])
        hm = done.get("heatmap")
        pk = hm.get("peak") if isinstance(hm, dict) else None
        if pk and pk.get("orders"):
            b["peak"] = {"day": assistant_ai.DOW_RU.get(pk.get("d")), "hour": pk.get("h")}
        st = done.get("stoplist")
        if st:
            stops = [i.get("name") for i in st.get("items", []) if i.get("status") == "stop"]
            b["stop_count"] = len(stops)
            b["stop_items"] = stops[:5]
        wo = done.get("writeoffs")
        if wo and wo.get("total_written"):
            b["writeoffs"] = {"total": wo.get("total_written"), "pct": wo.get("loss_pct")}
        return b

    async def throttled(r: Dict[str, Any]) -> Dict[str, Any]:
        async with sem:  # семафор ограничивает число одновременных обращений к iiko
            return await one(r)

    results = await asyncio.gather(*(throttled(r) for r in rests), return_exceptions=True)
    tid = int(user.get("id", 0) or 0)
    for r in results:
        if isinstance(r, dict):
            # прошлый снимок → «стало лучше, чем было»; затем кладём текущий.
            # Снимок пишем ПОСЛЕ чтения, иначе сравнивали бы сами с собой.
            snap = get_signal_snapshot(tid, r["name"]) if tid else None
            if snap:
                r["prev"] = {"taken_at": snap["taken_at"], **snap["payload"]}
            if tid:
                save_signal_snapshot(tid, r["name"], _snapshot_of(r))
            out_branches.append(r)
    return {"branches": out_branches}


def _snapshot_of(b: Dict[str, Any]) -> Dict[str, Any]:
    """Что запоминаем о филиале до следующего раза: показатели-результаты и
    управляемые входы. По входам видно, ЧТО владелец сделал (заполнил техкарты,
    закрыл стоп-лист), по результатам — что из этого вышло."""
    fc = b.get("foodcost") or {}
    wo = b.get("writeoffs") or {}
    return {
        "revenue": b.get("revenue"),
        "foodcost_pct": fc.get("fact_pct"),
        "coverage": fc.get("coverage"),
        "cancels": b.get("cancels"),
        "stop_count": b.get("stop_count"),
        "writeoffs_pct": wo.get("pct"),
        "ratings": {p.get("label"): p.get("rating")
                    for p in (b.get("reviews") or []) if p.get("rating") is not None},
    }


_DEMO_ADVICE = [
    {"id": "demo:fc", "branch": "Демо-ресторан", "severity": "high",
     "title": "Фудкост выше нормы", "detail": "Фудкост 36% при норме 30% — теряешь ≈ 2,9 млн/мес. Начни с техкарт топ-блюд.",
     "money": 2900000, "action": {"type": "navigate", "tab": "fc", "hint": None}},
    {"id": "demo:cn", "branch": "Демо-ресторан", "severity": "med",
     "title": "Отмены и возвраты", "detail": "Отмены на 52 тыс/мес — разбери топ-причину.",
     "money": 52000, "action": {"type": "navigate", "tab": "supply", "hint": None}},
]


@app.get("/api/tasks/{org_id}")
async def tasks_status(org_id: str, days: int = Query(14, ge=1, le=90),
                       user: Dict = Depends(get_current_user)):
    """Трекер задач из бота: какие филиалы отметились, какие нет.

    Показываем СВОДКУ по филиалам, а не ленту сообщений: владельцу важно
    «кто закрыл, кто молчит», а не хронология нажатий.
    """
    # Сужаем по organization_id, а не по названию филиала, и БЕЗ отката «нет
    # своих филиалов → показать всё»: get_bot_tasks отдаёт задачи всех клиентов
    # сразу. Раньше org_id из пути не участвовал вовсе — по ссылке филиала
    # трекер показывал всю сеть, а при пустом списке своих филиалов ещё и чужих
    # клиентов. Ссылка филиала сужает до одного org, владелец видит свои.
    _get_org_id(org_id, user)          # чужой филиал в пути — 403, как везде
    allowed = {r.get("organization_id") for r in get_restaurants(user.get("id", 0))}
    only = user.get("only_org")
    if only:
        allowed = {only}               # владельцу — сводка по сети, филиалу — своё
    rows = [t for t in get_bot_tasks(days) if t.get("organization_id") in allowed]

    by_branch: Dict[str, Dict[str, Any]] = {}
    for t in rows:
        b = by_branch.setdefault(t.get("branch") or "—", {
            "branch": t.get("branch") or "—", "done": 0, "later": 0, "open": 0,
            "money_open": 0.0, "last": None, "tasks": []})
        st = t.get("status") or "open"
        b[st if st in ("done", "later", "open") else "open"] += 1
        if st != "done":
            b["money_open"] += float(t.get("money") or 0)
        b["tasks"].append({
            "id": t["id"], "title": t.get("title"), "status": st,
            "money": round(float(t.get("money") or 0)),
            "created_at": t.get("created_at"), "answered_at": t.get("answered_at"),
            "answered_by": t.get("answered_by"),
        })
        if st == "done" and (not b["last"] or (t.get("answered_at") or "") > b["last"]):
            b["last"] = t.get("answered_at")

    out = sorted(by_branch.values(), key=lambda x: (-x["open"], -x["money_open"]))
    total = sum(len(b["tasks"]) for b in out)
    done = sum(b["done"] for b in out)
    return {
        "branches": out, "total": total, "done": done,
        "open": sum(b["open"] for b in out), "later": sum(b["later"] for b in out),
        "done_pct": round(done / total * 100) if total else 0,
        "money_open": round(sum(b["money_open"] for b in out)),
    }


@app.get("/api/director/plan/{org_id}")
async def director_plan(org_id: str, user: Dict = Depends(get_current_user)):
    """План ИИ-директора: задачи, которые он нашёл сам, и прогресс по ним.

    Задачи считаются из живых данных, но точка отсчёта (baseline) берётся из БД —
    от неё и меряется движение. Дошли до цели → задача закрывается сама.
    Сайт показывает прогресс, бот шлёт по этому же источнику уведомления.
    """
    real_org = _get_org_id(org_id, user)
    from fastapi import Response as _Resp

    async def safe(coro, label):
        try:
            return await coro
        except Exception as e:
            logger.warning(f"[director-plan] {label} {real_org}: {e!r}")
            return None

    fc, wo, cn, rv = await asyncio.gather(
        safe(foodcost(org_id, "month", 1, user), "foodcost"),
        safe(writeoffs(org_id, "month", user), "writeoffs"),
        safe(cancels(org_id, "month", user), "cancels"),
        safe(branch_reviews(org_id, user), "reviews"),
    )

    # (key, метрика, текущее, цель, ₸ на кону, «меньше — лучше»)
    found: List[Tuple[str, str, float, float, float, bool]] = []

    if fc and fc.get("fact_pct") is not None and (fc.get("coverage_pct") or 0) >= 70:
        fact, norm = float(fc["fact_pct"]), float(fc.get("norm_pct") or 30)
        if fact > norm:
            over = float(fc.get("fact_sum") or 0) * (fact - norm) / fact if fact else 0
            found.append(("fc", "Фудкост", fact, norm, round(over), True))

    if wo and (wo.get("loss_pct") or 0) > 5:
        pct = float(wo["loss_pct"])
        money = float(wo.get("total_written") or 0) * (pct - 5) / pct if pct else 0
        found.append(("wo", "Списания", pct, 5.0, round(money), True))

    if cn and (cn.get("total_amount") or 0) > 0:
        amt = float(cn["total_amount"])
        found.append(("cn", "Отмены и возвраты", amt, 0.0, round(amt), True))

    if rv and rv.get("platforms"):
        worst = min(rv["platforms"], key=lambda p: (p.get("rating") or 0) / (p.get("scale") or 5))
        if worst.get("rating") is not None:
            scale = worst.get("scale") or 5
            target = 9.0 if scale >= 10 else 4.7
            if float(worst["rating"]) < target:
                found.append(("rev", f"Рейтинг · {worst.get('label')}",
                              float(worst["rating"]), target, 0, False))

    out = []
    fresh = []                      # задачи, появившиеся именно в этот заход
    for key, metric, value, target, money, lower_better in found:
        row = upsert_director_task(real_org, key, metric, value, target, money)
        if row.get("is_new"):
            fresh.append((metric, value, target, money))
        base = float(row.get("baseline") if row.get("baseline") is not None else value)
        span = (base - target) if lower_better else (target - base)
        moved = (base - value) if lower_better else (value - base)
        progress = 0.0 if span <= 0 else max(0.0, min(1.0, moved / span))
        reached = (value <= target) if lower_better else (value >= target)
        if reached and row.get("status") != "done":
            close_director_task(real_org, key, value)
            row["status"] = "done"
        out.append({
            "key": key, "metric": metric,
            "baseline": round(base, 2), "current": round(value, 2), "target": round(target, 2),
            "money": money, "progress": round(progress * 100),
            "status": "done" if reached else row.get("status", "open"),
            "created_at": row.get("created_at"),
        })

    # Задачи, которых больше нет в данных: показатель ушёл из проблемной зоны —
    # считаем закрытыми, иначе план никогда не «выполнялся» бы полностью.
    live = {k for k, *_ in found}
    for row in get_director_tasks(real_org):
        if row["task_key"] in live:
            continue
        if row.get("status") != "done":
            close_director_task(real_org, row["task_key"], row.get("last_value") or 0)
        out.append({
            "key": row["task_key"], "metric": row.get("metric") or row["task_key"],
            "baseline": row.get("baseline"), "current": row.get("last_value"),
            "target": row.get("target"), "money": row.get("money") or 0,
            "progress": 100, "status": "done", "created_at": row.get("created_at"),
        })

    # Новые задачи догоняем в телеграм. Уведомление без обратной связи — шум,
    # поэтому шлём тем же отправщиком, что даёт кнопки «Сделал / Не успел»:
    # владелец потом видит, взялись за задачу или она висит вторую неделю.
    if fresh:
        await _notify_new_tasks(real_org, fresh)

    done = [t for t in out if t["status"] == "done"]
    out.sort(key=lambda t: (t["status"] == "done", -(t["money"] or 0)))
    return {
        "tasks": out,
        "total": len(out),
        "done": len(done),
        "done_pct": round(len(done) / len(out) * 100) if out else 0,
        "money_at_stake": sum(t["money"] or 0 for t in out if t["status"] != "done"),
    }


@app.get("/api/assistant")
async def assistant(user: Dict = Depends(get_current_user)):
    """Проактивные советы ИИ-ассистента по всем филиалам залогиненного клиента."""
    # демо-режим: не зовём Claude, отдаём демо-советы
    rests = get_restaurants(user.get("id", 0))
    if not rests or _use_demo_data(user, (rests[0].get("organization_id") if rests else "demo-org-1")):
        return {"advice": _DEMO_ADVICE, "count": len(_DEMO_ADVICE), "demo": True}
    # Разбор ассистента — суточная вещь, а общий кэш живёт 3 минуты. С ним каждые
    # три минуты работы с сайтом уходил новый платный вызов Claude на 13 филиалов:
    # держим ответ 12 часов, чтобы он считался раз в день, а не при каждом заходе.
    # Снимок готового разбора: путь в ASSISTANT_SNAPSHOT. Нужен, когда модель
    # недоступна (кончился баланс), а показать живой разбор надо сейчас —
    # отдаём последний удачный ответ вместо шаблонных правил.
    snap = os.getenv("ASSISTANT_SNAPSHOT")
    if snap:
        try:
            with open(snap, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"[assistant] снимок {snap}: {e!r}")

    key = _cache_key(user.get("id", 0), "assistant", "all", "month")
    cached = _get_cached(key, ttl=ASSISTANT_TTL)
    if cached is not None:
        return cached
    signals = await _gather_assistant_signals(user)
    advice = await assistant_ai.advise(signals)
    res = {"advice": advice, "count": len(advice), "demo": False}
    # TTL пишем и при записи: чистка кэша по размеру ориентируется на него, иначе
    # 12-часовой разбор выметался как трёхминутный — и уходил новый платный вызов.
    _set_cached(key, res, ttl=ASSISTANT_TTL)
    return res


# ── Вкладка «План»: недельный план по типам каналов (КОЛ-ВО заказов) + факт ──
PLAN_TYPES = ["hall", "pickup", "delivery", "agg"]

# Версия классификатора каналов. Снимок факта (plan_day_facts) считается ПО НЕЙ,
# поэтому при её смене старые строки надо выбросить: до v2 вынос лежал внутри
# зала, и сравнение «эта неделя против прошлой» смешивало бы два разных правила.
PLAN_FACTS_VER = "4"


def _classify_channel(name: str) -> str:
    """Тип заказа iiko → канал плана.

    Реальные значения OrderType.Name у сети: «Обычный заказ», «Агрегаторы»,
    «Самовывоз», «Сайт самовывоз», «Доставка», «Сайт доставка», Wolt, Glovo,
    Chocofood, «Яндекс Еда». Самовывоз проверяем ДО доставки: «Сайт самовывоз» —
    это вынос, а не доставка.

    Дважды одно и то же: сперва Chocofood, потом «Агрегаторы» не совпадали ни с
    одним правилом и молча падали в зал. Второй раз дорого: этим типом сеть
    пробивает БОЛЬШИНСТВО агрегаторских заказов, а не именем площадки — за
    четыре недели так потерялось 2181 заказ из 2423, зал был завышен на 9 %,
    агрегаторы занижены вдесятеро.

    Отсюда правило: неизвестное имя падает в зал молча, поэтому список сверяют
    с живыми серверами, а не дописывают по памяти (test_channels.REAL).

    Это единственное правило группировки каналов в продукте: по нему живут и
    «План», и карточка «Каналы продаж».
    """
    s = (name or "").lower()
    if any(k in s for k in ("агрегат", "wolt", "волт", "glovo", "глово",
                            "яндекс", "yandex", "choco", "чоко")):
        return "agg"
    if "самовывоз" in s or "вынос" in s:
        return "pickup"
    if "достав" in s:
        return "delivery"
    return "hall"  # «Обычный заказ» и всё, что съели в зале


def _month_weeks(ym: str) -> List[Dict[str, Any]]:
    """Месяц 'YYYY-MM' → недели 7-дневными кусками от 1-го числа (widx 1..5)."""
    y, m = int(ym[:4]), int(ym[5:7])
    last = calendar.monthrange(y, m)[1]
    weeks, d, widx = [], 1, 1
    while d <= last:
        end = min(d + 6, last)
        weeks.append({"widx": widx, "from": f"{y:04d}-{m:02d}-{d:02d}", "to": f"{y:04d}-{m:02d}-{end:02d}"})
        d, widx = end + 1, widx + 1
    return weeks


def _empty_plan_grid(weeks: List[Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
    return {str(w["widx"]): {t: 0 for t in PLAN_TYPES} for w in weeks}


@app.get("/api/weekplan/{org_id}")
async def plan_get(org_id: str, month: Optional[str] = None, user: Dict = Depends(get_current_user)):
    """План (из нашей БД) + факт (кол-во заказов по типам из /api/sales) по неделям месяца."""
    ym = month or local_now().strftime("%Y-%m")
    weeks = _month_weeks(ym)
    if _use_demo_data(user, org_id):
        plan = {str(w["widx"]): {"hall": 120, "pickup": 30, "delivery": 60, "agg": 40} for w in weeks}
        fact = {str(w["widx"]): {"hall": 108, "pickup": 34, "delivery": 55, "agg": 47} for w in weeks}
        return {"month": ym, "weeks": weeks, "types": PLAN_TYPES, "plan": plan, "fact": fact, "demo": True}

    real_org = _get_org_id(org_id, user)
    plan = _empty_plan_grid(weeks)
    for r in get_weekly_plans(real_org, ym):
        wk = str(r["widx"])
        if wk in plan and r["ch_type"] in PLAN_TYPES:
            plan[wk][r["ch_type"]] = r["planned"]

    fact = _empty_plan_grid(weeks)

    async def _wk_fact(w):
        f = {t: 0 for t in PLAN_TYPES}
        try:
            s = await sales(org_id, "custom", w["from"], w["to"], None, user=user)
            for c in s.get("channels", []):
                f[_classify_channel(c.get("name"))] += int(c.get("orders", 0) or 0)
        except Exception as e:
            logger.warning(f"[plan] факт неделя {w['widx']} {real_org}: {e!r}")
        return w["widx"], f

    for widx, f in await asyncio.gather(*(_wk_fact(w) for w in weeks)):
        fact[str(widx)] = f
    return {"month": ym, "weeks": weeks, "types": PLAN_TYPES, "plan": plan, "fact": fact, "demo": False}


# ============ ПЛАН ПО ДНЯМ НЕДЕЛИ (вкладка «План» = таблица владельца) ============
# Маршрут /api/plan-week, а не /api/plan: последний с самого начала занят старым
# разбором Google-таблицы владельца по ссылке (plan_comparison выше). Тот план
# читался из чужого документа, этот хранится у нас — и заменяет его.
# Таблица владельца устроена так: на каждый филиал карточка «Неделя NN», строки
# Пн–Вс, столбцы Доставка / Зал+вынос / Агрегаторы, под каждым днём план и факт,
# снизу «Итого план / Итого факт». Плюс сводка сети и «база плана» — средняя
# неделя, из которой план и ставится. Повторяем это, а не неделю одной строкой.

PLAN_BASE_WEEKS = 6       # сколько прошлых недель усредняем для «базы плана»


def _iso_week_bounds(week: str) -> Tuple[date, date]:
    """'2026-W35' → (понедельник, воскресенье). Нумерация ISO — та же, что
    в «Неделя 35» у владельца, поэтому недели месяца тут ни при чём."""
    y, w = int(week[:4]), int(week.split("W")[1])
    mon = date.fromisocalendar(y, w, 1)
    return mon, mon + timedelta(days=6)


def _iso_week_of(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y:04d}-W{w:02d}"


def _empty_days() -> Dict[str, Dict[str, int]]:
    return {str(i): {t: 0 for t in PLAN_TYPES} for i in range(1, 8)}


async def _orders_by_day(real_org: str, d_from: date, d_to: date,
                         user: Dict[str, Any]) -> List[Dict[str, Any]]:
    """День × канал × число заказов одним запросом в OLAP. Семь запросов на
    филиал (по одному на день) сеть бы не пережила: филиалов девять."""
    info = _get_restaurant_info(real_org, user)
    client = get_client(info["api_login"], info.get("iiko_server"), info.get("api_password"))
    res = await client.olap_report(
        _olap_dept(real_org, info), d_from.isoformat(), d_to.isoformat(),
        group_by=["OpenDate.Typed", "OrderType.Name"], aggregates=["OrdersCount"])
    return res.get("data", [])


_plan_facts_checked = False


def _ensure_plan_facts_version():
    """Сменился классификатор каналов — старый снимок выбрасываем. Раз за
    запуск: проверка стоит запроса в базу, а меняется версия раз в год."""
    global _plan_facts_checked
    if _plan_facts_checked:
        return
    _plan_facts_checked = True
    from database import drop_plan_day_facts, get_app_state, set_app_state
    if get_app_state("plan_facts_ver") != PLAN_FACTS_VER:
        drop_plan_day_facts()
        set_app_state("plan_facts_ver", PLAN_FACTS_VER)
        logger.info(f"[plan] снимок факта сброшен под классификатор v{PLAN_FACTS_VER}")


async def _orders_cached(real_org: str, d_from: date, d_to: date,
                        user: Dict[str, Any]) -> Dict[str, Dict[str, int]]:
    """Факт заказов по дням — {день: {канал: заказов}} — ТОЛЬКО за закрытые дни.

    Закрытый день из iiko больше не меняется, поэтому держим его снимком в
    своей базе: раньше каждое открытие вкладки «План» стреляло двумя OLAP-
    запросами на филиал, девять филиалов на девяти серверах — десятки секунд.
    Ночной сбор закрывает вчерашний день; чего в снимке нет (первый запуск,
    пропущенная ночь) — доберём здесь одним запросом и положим.

    Сегодняшний день не отдаём вообще: он ещё идёт, и сравнивать его с целым
    днём прошлой недели нельзя.
    """
    from database import get_plan_day_facts
    _ensure_plan_facts_version()
    last_closed = local_now().date() - timedelta(days=1)
    if d_to > last_closed:
        d_to = last_closed
    if d_from > d_to:
        return {}

    have = get_plan_day_facts(real_org, d_from.isoformat(), d_to.isoformat())
    want = [d_from + timedelta(days=i) for i in range((d_to - d_from).days + 1)]
    missing = [d for d in want if d.isoformat() not in have]
    if missing:
        # Одним запросом по всему пробелу: iiko-серверов девять, и дробить
        # добор по дням дороже, чем взять диапазон с запасом.
        a, z = min(missing), max(missing)
        span = await _refresh_plan_facts(real_org, a, z, user)
        have.update(span)
    return {ds: cells for ds, cells in have.items()
            if d_from.isoformat() <= ds <= d_to.isoformat()}


async def _refresh_plan_facts(real_org: str, d_from: date, d_to: date,
                              user: Dict[str, Any]) -> Dict[str, Dict[str, int]]:
    """Принудительно перечитывает закрытые дни из iiko и обновляет снимок.

    Закрытая смена всё же может измениться задним числом: возврат, перепробитие
    или позднее закрытие заказа. Раньше существующая строка считалась вечной,
    из-за чего «Планы» отставали от свежего OLAP.
    """
    from database import save_plan_day_facts
    fetched = _fold_by_dow(await _orders_by_day(real_org, d_from, d_to, user))["date"]
    span = {(d_from + timedelta(days=i)).isoformat(): {t: 0 for t in PLAN_TYPES}
            for i in range((d_to - d_from).days + 1)}
    for ds, cells in fetched.items():
        if ds in span:
            span[ds] = {t: int(cells.get(t) or 0) for t in PLAN_TYPES}
    save_plan_day_facts(real_org, span)
    return span


def _dow_from_days(days: Dict[str, Dict[str, int]]) -> Dict[str, Dict[str, int]]:
    """{день: {канал: n}} → {день-недели: {канал: n}}."""
    grid = _empty_days()
    for ds, cells in days.items():
        try:
            d = date.fromisoformat(ds)
        except ValueError:
            continue
        for t in PLAN_TYPES:
            grid[str(d.isoweekday())][t] += int(cells.get(t) or 0)
    return grid


def _fold_by_dow(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Dict[str, int]]]:
    """Строки OLAP → {день-недели: {канал: заказов}} и то же по датам."""
    by_dow: Dict[str, Dict[str, int]] = {str(i): {t: 0 for t in PLAN_TYPES} for i in range(1, 8)}
    by_date: Dict[str, Dict[str, int]] = {}
    for r in rows:
        ds = str(r.get("OpenDate.Typed") or "")[:10]
        if not ds:
            continue
        try:
            d = date.fromisoformat(ds)
        except ValueError:
            continue
        t = _classify_channel(r.get("OrderType.Name") or "")
        n = int(float(r.get("OrdersCount") or 0))
        by_dow[str(d.isoweekday())][t] = by_dow[str(d.isoweekday())][t] + n
        by_date.setdefault(ds, {t2: 0 for t2 in PLAN_TYPES})[t] += n
    return {"dow": by_dow, "date": by_date}


def _plan_branches(org_id: str, network: bool, user: Dict[str, Any],
                   token: Optional[str] = None) -> List[Dict[str, str]]:
    """Чьи карточки показываем.

    Сеть целиком видит ТОЛЬКО владелец. Раньше охват решал бы клиент параметром
    network — но у управляющего своя подписанная ссылка, и её надо слушать:
    подставил бы network=1 и увидел чужие филиалы. Токен здесь главнее флага.
    """
    import mountain_routes as _mr
    if token and _mr.demo_from_token(token):
        # Демо-ссылка: выдуманная сеть. Без этого uid_from_token не разбирал
        # демо-токен, филиалов выходило ноль, и вкладка «План» падала на пустоте.
        import demo as _demo
        return [dict(b) for b in _demo.BRANCHES]
    if token:
        sc = _mr._scope(_mr.uid_from_token(token) or 0)
        if sc["role"] == "manager":
            return [{"organization_id": b["organization_id"], "name": b["name"]}
                    for b in sc["branches"]]
    if not network:
        return [{"organization_id": _get_org_id(org_id, user),
                 "name": _branch_name(_get_org_id(org_id, user), user)}]
    seen: Dict[str, str] = {}
    for r in get_restaurants(user.get("id", 0)) or []:
        org, name = r.get("organization_id"), r.get("organization_name") or "Филиал"
        if org and org not in seen:
            seen[org] = name
    # Отсев служебных организаций — но не до пустоты: у зарегистрировавшегося не
    # в Астане иначе не осталось бы ни одного филиала, и вкладка «Планы»
    # открывалась бы пустой без всякого объяснения.
    rows = [{"organization_id": o, "name": n} for o, n in seen.items()]
    return _mr.visible_branches(rows, lambda r: r["name"])


def _attendance_segments(attendance: List[Dict[str, Any]],
                         org_name: str) -> List[Dict[str, Any]]:
    """Явки филиала → НЕпересекающиеся отрезки работы.

    Два подвоха. Первый: на одном iiko-сервере живут все филиалы сети, поэтому
    отбор по departmentName обязателен. Второй: явки задваиваются, когда
    сотрудник числится в двух ролях одновременно. Отрезок при этом не
    выбрасывается целиком, а обрезается по уже занятому времени — так и часы не
    удваиваются, и у каждого куска остаётся своя должность и свой тип явки
    (по ним и считается ставка с ночной или праздничной надбавкой).
    """
    by_emp: Dict[str, list] = {}
    for a in attendance or []:
        if org_name and a.get("departmentName") != org_name:
            continue
        try:
            df = datetime.fromisoformat(a["dateFrom"])
            dt_ = datetime.fromisoformat(a["dateTo"])
        except Exception:
            continue
        if dt_ > df:
            by_emp.setdefault(a["employeeId"], []).append(
                (df, dt_, a.get("roleId"), a.get("attendanceTypeId")))
    out: List[Dict[str, Any]] = []
    for eid, ivs in by_emp.items():
        ivs.sort(key=lambda x: (x[0], x[1]))
        covered = None
        for df, dt_, rid, tid in ivs:
            start = df if covered is None or df > covered else covered
            if dt_ <= start:
                continue
            out.append({"employee_id": eid, "role_id": rid, "type_id": tid,
                        "hours": (dt_ - start).total_seconds() / 3600,
                        "day": start.date()})
            covered = dt_ if covered is None or dt_ > covered else covered
    return out


def _merge_attendance_hours(attendance: List[Dict[str, Any]],
                            org_name: str) -> Dict[str, Dict[str, float]]:
    """Часы и дни каждого сотрудника филиала: {сотрудник: {hours, days}}."""
    out: Dict[str, Dict[str, float]] = {}
    for seg in _attendance_segments(attendance, org_name):
        w = out.setdefault(seg["employee_id"], {"hours": 0.0, "_days": set()})
        w["hours"] += seg["hours"]
        w["_days"].add(seg["day"])
    return {eid: {"hours": w["hours"], "days": float(len(w["_days"]))}
            for eid, w in out.items()}


def _fot_from_schedule(org: str, d_from: str, d_to: str) -> Dict[str, Any]:
    """Запасной ФОТ — из оплат, проставленных руками в графике смен."""
    from database import branch_fot_summary
    s = branch_fot_summary(org, d_from, d_to)
    return {"amount": s["amount"], "priced": s["shifts_priced"],
            "total": s["shifts_total"],
            "unit": "shift" if s["shifts_total"] else None,
            "complete": s["complete"]}


def _round_parts(parts: Dict[str, float], target: int) -> List[Dict[str, Any]]:
    """Округляет части так, чтобы их сумма В ТОЧНОСТИ равнялась целому.

    Округли каждую саму по себе — и на экране под «1235 непокрытых часов»
    встанет список на 1237: остаток расходится, а на собрании числа обязаны
    складываться. Метод наибольшего остатка: сначала всем целую часть, потом
    оставшиеся единицы тем, у кого дробная часть больше.
    """
    items = sorted(parts.items(), key=lambda kv: -kv[1])
    if not items:
        return []
    vals = [int(v // 1) for _, v in items]
    left = target - sum(vals)
    by_frac = sorted(range(len(items)), key=lambda i: -(items[i][1] % 1))
    i = 0
    while left > 0:
        vals[by_frac[i % len(vals)]] += 1
        left -= 1
        i += 1
    # Отрицательный остаток снимаем с самых крупных: там единица менее заметна.
    i = 0
    while left < 0 and any(v > 0 for v in vals):
        j = i % len(vals)
        if vals[j] > 0:
            vals[j] -= 1
            left += 1
        i += 1
    return [{"role": n, "hours": v} for (n, _), v in zip(items, vals) if v > 0]


def _fot_of_segments(segments: List[Dict[str, Any]], roles: Dict[str, Dict[str, Any]],
                     coef: Dict[str, float], rates: Dict[str, float],
                     overrides: Dict[str, Any]) -> Dict[str, Any]:
    """Часы × ставка = ФОТ филиала, с честным покрытием в часах.

    Ставку берём в таком порядке: ручная правка суммы за период → наша ставка
    сотрудника → `paymentPerHour` его должности в iiko. Нулевая ставка должности
    означает «её не завели» (у сети так у Кассира, Курьера и Тех персонала) —
    такие часы в сумму не идут, но и молчать о них нельзя: иначе неполный ФОТ
    выдавался бы за полный. Покрытие считаем в ЧАСАХ, а не в людях: кассир со
    106 часами и бухгалтер с двумя — разного веса дыры.

    Считаем ПО СОТРУДНИКУ целиком, а не по отдельным отрезкам: правка за период
    задаёт итог по человеку, и если он часть смен отработал должностью без
    ставки, эти часы тоже закрыты — сумма по нему известна. Иначе правка сама
    себе рисовала бы дыру и занижала покрытие.
    """
    by_emp: Dict[str, List[Dict[str, Any]]] = {}
    for seg in segments:
        by_emp.setdefault(seg["employee_id"], []).append(seg)
    hours_total = hours_priced = amount = 0.0
    gaps: Dict[str, float] = {}
    for eid, segs in by_emp.items():
        hours = sum(s["hours"] for s in segs)
        hours_total += hours
        if eid in overrides:
            amount += float(overrides[eid] or 0)
            hours_priced += hours
            continue
        own = rates.get(eid)
        for s_ in segs:
            role = roles.get(s_["role_id"] or "") or {}
            rate = own or role.get("payment_per_hour") or 0.0
            if rate:
                hours_priced += s_["hours"]
                amount += s_["hours"] * float(rate) * coef.get(s_["type_id"] or "", 1.0)
            else:
                name = role.get("name") or "без должности"
                gaps[name] = gaps.get(name, 0.0) + s_["hours"]
    # Дыры отдаём ВСЕ, а не три верхних: под суммой стоит «столько-то из стольких
    # часов со ставкой», и если список должностей не объясняет разницу целиком,
    # числа на собрании не сходятся. Сколько показать — решает экран.
    priced, total = round(hours_priced), round(hours_total)
    return {"amount": round(amount), "priced": priced, "total": total, "unit": "hour",
            "complete": total > 0 and priced >= total,
            "gaps": _round_parts(gaps, total - priced)}


def _done_days(d_from: date, d_to: date, today: Optional[date] = None) -> int:
    """Сколько дней недели уже ЗАКРЫТО. Сегодняшний день не закрыт.

    Считаем на сервере и ровно в одном месте: сравнение с прошлой неделей идёт
    день в день, и если хоть один потребитель разойдётся в этом числе на день,
    динамика во вторник соврёт.
    """
    today = today or local_now().date()
    if d_to < today:
        return 7
    if d_from > today:
        return 0
    return (today - d_from).days


def _fot_window(d_from: date, d_to: date,
                today: Optional[date] = None) -> Optional[Tuple[date, date]]:
    """Период, за который считаем ФОТ, — ТЕ ЖЕ закрытые дни, что и у заказов.

    ФОТ стоит на карточке рядом с выполнением плана и динамикой к прошлой
    неделе, а обе считаются по закрытым дням. Возьми ФОТ за всю неделю — и в
    среду рядом окажутся деньги за три дня и заказы за два. None значит
    «закрытых дней нет»: в iiko идти незачем, а «ноль часов» и «нет данных» —
    разные вещи.
    """
    done = _done_days(d_from, d_to, today)
    if not done:
        return None
    return d_from, d_from + timedelta(days=done - 1)


async def _fot_by_branch(branches: List[Dict[str, str]], d_from: date, d_to: date,
                         user: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """ФОТ филиалов за период — из iiko, без ручного ввода в нашем приложении.

    Денег в явке iiko не отдаёт, но отдаёт всё, из чего она складывается:
    часы (`/api/employees/attendance`), ставку должности
    (`/api/employees/roles` → paymentPerHour) и надбавку за ночь или праздник
    (`/api/employees/attendance/types` → payRate). Раньше вкладка читала ТОЛЬКО
    оплату смены, проставленную руками в графике, — её никто не проставлял, и
    ФОТ честно показывал 0.

    Запросы — по одному на СЕРВЕР, а не на филиал: явки приезжают по всем
    департаментам сразу, а девять филиалов сети живут на одном сервере.
    """
    from database import get_salary_overrides, get_salary_rates
    d1, d2 = d_from.isoformat(), d_to.isoformat()
    infos: Dict[str, Dict[str, Any]] = {}
    by_server: Dict[Tuple[Any, Any], List[str]] = {}
    for b in branches:
        org = b["organization_id"]
        infos[org] = info = _get_restaurant_info(org, user)
        by_server.setdefault((info["api_login"], info.get("iiko_server")), []).append(org)

    async def pull(key: Tuple[Any, Any], org: str):
        info = infos[org]
        client = get_client(info["api_login"], info.get("iiko_server"),
                            info.get("api_password"))
        # Облачный iiko явок не отдаёт вовсе — там остаётся только график смен.
        if not hasattr(client, "get_attendance"):
            return key, None
        try:
            att, roles, coef = await asyncio.gather(
                client.get_attendance(d1, d2),
                client.get_employee_roles(),
                client.get_attendance_types())
            return key, (att, roles, coef)
        except Exception as e:
            # Лежащий iiko-сервер не должен ронять план и факт заказов: ФОТ
            # тогда просто падает на график смен.
            logger.warning(f"[plan] явки {info.get('iiko_server')}: {e!r}")
            return key, None

    pulled = dict(await asyncio.gather(*(pull(k, orgs[0]) for k, orgs in by_server.items())))
    out: Dict[str, Dict[str, Any]] = {}
    for key, orgs in by_server.items():
        got = pulled.get(key)
        for org in orgs:
            segments = _attendance_segments(got[0], infos[org]["org_name"]) if got else []
            if not segments:
                out[org] = _fot_from_schedule(org, d1, d2)
                continue
            # Ставки — СВОЕГО филиала: один и тот же сотрудник на двух точках
            # может стоить по-разному.
            out[org] = _fot_of_segments(segments, got[1], got[2], get_salary_rates(org),
                                        get_salary_overrides(f"{org}:{d1}:{d2}"))
    return out


def _branch_name(real_org: str, user: Dict[str, Any]) -> str:
    for r in get_restaurants(user.get("id", 0)) or []:
        if r.get("organization_id") == real_org:
            return r.get("organization_name") or "Филиал"
    try:
        return _get_restaurant_info(real_org, user).get("org_name") or "Филиал"
    except Exception:
        return "Филиал"


@app.get("/api/plan-week/{org_id}")
async def plan_week(org_id: str, week: Optional[str] = None, network: int = 0,
                    token: Optional[str] = None,
                    user: Dict = Depends(get_current_user)):
    """План и факт недели по дням и каналам — карточка на филиал плюс сводка.

    База плана — средняя неделя за последние PLAN_BASE_WEEKS завершённых недель:
    именно из неё владелец и ставит план в таблице, поэтому она приходит рядом,
    а не считается на глаз.
    """
    wk = week or _iso_week_of(local_now().date())
    try:
        d_from, d_to = _iso_week_bounds(wk)
    except Exception:
        raise HTTPException(status_code=400, detail="bad week, ожидается YYYY-Www")
    branches = _plan_branches(org_id, bool(network), user, token)
    if not branches:
        return {"week": wk, "from": d_from.isoformat(), "to": d_to.isoformat(),
                "types": PLAN_TYPES, "branches": [], "demo": False}

    from database import get_day_plans
    plans = get_day_plans([b["organization_id"] for b in branches], wk)

    if _use_demo_data(user, org_id):
        demo_p = {"hall": 90, "pickup": 21, "delivery": 12, "agg": 7}
        out = []
        for b in branches:
            plan = {str(i): dict(demo_p) for i in range(1, 8)}
            fact = {str(i): {t: max(0, v - (i % 4) * 2) for t, v in demo_p.items()} for i in range(1, 8)}
            prev = {str(i): {t: max(0, v - (i % 3) * 3) for t, v in demo_p.items()} for i in range(1, 8)}
            out.append({**b, "plan": plan, "fact": fact, "base": plan, "prev": prev,
                        "fot": {"amount": 420000, "priced": 430, "total": 430,
                                "unit": "hour", "complete": True, "gaps": []}})
        return _plan_response(wk, d_from, d_to, out, demo=True)

    base_from = d_from - timedelta(weeks=PLAN_BASE_WEEKS)
    base_to = d_from - timedelta(days=1)
    prev_from, prev_to = d_from - timedelta(days=7), d_from - timedelta(days=1)
    solo, today_d = len(branches) == 1, local_now().date()
    # Сам план ставим только на текущую и будущие недели. Прошлую заполнять
    # задним числом нельзя: это придумало бы филиалу цель, которой у него не
    # было, и задним же числом переписало бы его оценку за ту неделю.
    fill_auto = d_to >= today_d

    async def one(b: Dict[str, str]):
        org = b["organization_id"]
        cur_days: Dict[str, Dict[str, int]] = {}
        base_days: Dict[str, Dict[str, int]] = {}
        try:
            cur_days = await _orders_cached(org, d_from, d_to, user)
        except Exception as e:
            logger.warning(f"[plan] факт {b['name']} {wk}: {e!r}")
        try:
            base_days = await _orders_cached(org, base_from, base_to, user)
        except Exception as e:
            logger.warning(f"[plan] база {b['name']}: {e!r}")
        # Один филиал в ответе — это управляющий у себя (или владелец с одной
        # точкой). Ему нужен СЕГОДНЯШНИЙ день: он по этой карточке работает
        # прямо сейчас, а «факт 0» до полуночи выглядел бы поломкой. Один
        # лишний запрос на один филиал — не девять на девять серверов, поэтому
        # владельцу сети сегодняшний день по-прежнему не тянем.
        if solo and d_from <= today_d <= d_to:
            try:
                live = _fold_by_dow(await _orders_by_day(org, today_d, today_d, user))["date"]
                cur_days = {**cur_days, **{ds: {t: int(c.get(t) or 0) for t in PLAN_TYPES}
                                           for ds, c in live.items()}}
            except Exception as e:
                logger.warning(f"[plan] сегодня {b['name']}: {e!r}")
        base_dow = _dow_from_days(base_days)
        # Средняя неделя: сумма по дню недели / число таких дней в окне.
        avg = {str(i): {t: round(base_dow[str(i)][t] / PLAN_BASE_WEEKS)
                        for t in PLAN_TYPES} for i in range(1, 8)}
        # Прошлая неделя — БЕЗ отдельного запроса в iiko: она уже внутри окна,
        # по которому считается средняя. Девять филиалов на девяти серверах:
        # лишний круг запросов стоил бы ещё полминуты на открытие вкладки.
        prev = _dow_from_days({ds: cells for ds, cells in base_days.items()
                               if prev_from.isoformat() <= ds <= prev_to.isoformat()})
        saved = plans.get(org, {})
        auto = False
        if _should_autoplan(saved, avg, fill_auto):
            # Плана нет — ставим по средней сами. Без него у филиала нет ни
            # процентов выполнения, ни критерия «выполнение плана» в оценке, а
            # нажать кнопку легко забыть: вкладка тогда просто мертва.
            _autoplan(org, wk, avg)
            saved = {i: dict(avg[str(i)]) for i in range(1, 8)}
            auto = True
        plan = {str(i): {t: int(saved.get(i, {}).get(t, 0)) for t in PLAN_TYPES}
                for i in range(1, 8)}
        return {**b, "plan": plan, "fact": _dow_from_days(cur_days),
                "base": avg, "prev": prev, "plan_auto": auto}

    # ФОТ тянем ПАРАЛЛЕЛЬНО с заказами: это отдельный поход в iiko, и ставить
    # его в очередь за девятью OLAP-запросами значило бы удлинить открытие
    # вкладки на время самого медленного сервера.
    fot_win = _fot_window(d_from, d_to)

    async def fot_safe():
        if fot_win is None:
            return {}
        try:
            return await _fot_by_branch(branches, fot_win[0], fot_win[1], user)
        except Exception as e:
            logger.warning(f"[plan] ФОТ {wk}: {e!r}")
            return {}

    rows, fots = await asyncio.gather(asyncio.gather(*(one(b) for b in branches)),
                                      fot_safe())
    rows = [{**r, "fot": fots.get(r["organization_id"])} for r in rows]
    return _plan_response(wk, d_from, d_to, list(rows), demo=False,
                          live_today=solo and d_from <= today_d <= d_to)


def _should_autoplan(saved: Dict[int, Dict[str, int]],
                     avg: Dict[str, Dict[str, int]], fill_auto: bool) -> bool:
    """Ставить ли план самим. Три условия, и каждое — про отдельную беду.

    saved непустой — план уже ставили, в том числе НУЛЁМ: ноль руками это
    решение «на этой неделе не работаем», у него строки в базе есть, и затирать
    его нельзя. «Ноль» и «не задан» тут разные вещи.

    fill_auto — неделя не в прошлом. Задним числом план придумал бы филиалу
    цель, которой у него не было, и переписал бы его оценку за ту неделю.

    avg пустая — у филиала нет истории. Записать нули значит получить тот же
    «план не задан», только с лишними строками.
    """
    if saved or not fill_auto:
        return False
    return any(avg[str(i)][t] for i in range(1, 8) for t in PLAN_TYPES)


def _autoplan(org: str, wk: str, avg: Dict[str, Dict[str, int]]) -> None:
    """Записывает среднюю неделю как план. Молча: экран важнее записи.

    Перезаписи не будет — зовут только когда сохранённых строк нет вовсе.
    План, выставленный руками в ноль, строки в базе ИМЕЕТ, поэтому «ноль» и
    «не задан» здесь разные вещи, и осознанный ноль мы не затрём.
    """
    from database import set_day_plans
    try:
        set_day_plans([{"org": org, "week": wk, "dow": i,
                        "ch_type": t, "planned": avg[str(i)][t]}
                       for i in range(1, 8) for t in PLAN_TYPES])
    except Exception as e:
        logger.warning(f"[plan] автоплан {org} {wk}: {e!r}")


def _plan_response(wk: str, d_from: date, d_to: date,
                   rows: List[Dict[str, Any]], demo: bool,
                   live_today: bool = False) -> Dict[str, Any]:
    """Считает итоги филиалов и строку сети. Суммы считаем здесь, а не на
    фронте: та же арифметика на двух сторонах расходится молча."""
    def _totals(grid: Dict[str, Dict[str, int]]) -> Dict[str, int]:
        return {t: sum(grid[str(i)][t] for i in range(1, 8)) for t in PLAN_TYPES}

    net_plan, net_fact = _empty_days(), _empty_days()
    net_base, net_prev = _empty_days(), _empty_days()
    for r in rows:
        r.setdefault("prev", _empty_days())
        r["total_plan"], r["total_fact"] = _totals(r["plan"]), _totals(r["fact"])
        r["total_base"], r["total_prev"] = _totals(r["base"]), _totals(r["prev"])
        for i in range(1, 8):
            for t in PLAN_TYPES:
                net_plan[str(i)][t] += r["plan"][str(i)][t]
                net_fact[str(i)][t] += r["fact"][str(i)][t]
                net_base[str(i)][t] += r["base"][str(i)][t]
                net_prev[str(i)][t] += r["prev"][str(i)][t]
    network_row = {"organization_id": "", "name": "Итого сеть",
                   "plan": net_plan, "fact": net_fact, "base": net_base, "prev": net_prev,
                   "total_plan": _totals(net_plan), "total_fact": _totals(net_fact),
                   "total_base": _totals(net_base), "total_prev": _totals(net_prev)}
    fot_rows = [r.get("fot") or {} for r in rows]
    # Единицу покрытия складываем, только если она у всех одна: сотрудники со
    # ставкой и смены с оплатой — разные вещи, и «5 из 7» из их смеси соврало бы.
    units = {x.get("unit") for x in fot_rows if x.get("unit")}
    total = sum(int(x.get("total") or 0) for x in fot_rows)
    priced = sum(int(x.get("priced") or 0) for x in fot_rows)
    # Дыры в ставках по сети: у девяти филиалов не заведён один и тот же
    # Кассир, и владельцу нужна одна строка «вот кого заполнить», а не девять.
    gaps: Dict[str, float] = {}
    for x in fot_rows:
        for g in x.get("gaps") or []:
            gaps[g["role"]] = gaps.get(g["role"], 0) + float(g.get("hours") or 0)
    network_row["fot"] = {
        "amount": round(sum(float(x.get("amount") or 0) for x in fot_rows)),
        "priced": priced, "total": total,
        "unit": units.pop() if len(units) == 1 else ("mixed" if units else None),
        "complete": total > 0 and priced == total,
        "gaps": [{"role": n, "hours": round(h)}
                 for n, h in sorted(gaps.items(), key=lambda x: -x[1])]}
    today = local_now().date()
    done_days = _done_days(d_from, d_to, today)
    return {"week": wk, "from": d_from.isoformat(), "to": d_to.isoformat(),
            "types": PLAN_TYPES, "branches": rows, "network": network_row,
            "base_weeks": PLAN_BASE_WEEKS, "done_days": done_days,
            "running": d_from <= today <= d_to, "live_today": live_today, "demo": demo}


@app.get("/api/plan-week/{org_id}/metrics")
async def plan_week_metrics(org_id: str, week: Optional[str] = None, network: int = 0,
                            token: Optional[str] = None,
                            user: Dict = Depends(get_current_user)):
    """Показатели недели по филиалам + что улучшилось к прошлой неделе.

    Отдельным запросом от сетки плана намеренно: сетку владелец заполняет
    сразу, а эти цифры требуют ещё двух обращений в iiko на филиал. Пусть
    таблица открывается мгновенно, а показатели догружаются рядом.
    """
    wk = week or _iso_week_of(local_now().date())
    try:
        d_from, d_to = _iso_week_bounds(wk)
    except Exception:
        raise HTTPException(status_code=400, detail="bad week")
    # Сравниваем ДЕНЬ В ДЕНЬ, как и вся вкладка: сколько дней текущей недели
    # закрыто, столько же берём у прошлой. Иначе во вторник «заказов за неделю»
    # сравнивалось два дня против семи — и в улучшения они не попадали НИКОГДА,
    # сколько бы филиал ни прибавил.
    today = local_now().date()
    done = 7 if d_to < today else (0 if d_from > today else (today - d_from).days)
    now_to = d_from + timedelta(days=done - 1) if done else d_from
    prev_from = d_from - timedelta(days=7)
    prev_to = prev_from + timedelta(days=done - 1) if done else prev_from
    cause_from, _cause_to = cause_window(d_from, done)
    branches = _plan_branches(org_id, bool(network), user, token)
    if not done:
        # Неделя только началась: сравнивать нечего, и врать нулями не будем.
        return {"week": wk, "from": d_from.isoformat(), "to": d_to.isoformat(),
                "done_days": 0,
                "metrics": [{"key": k, "label": lb, "unit": u, "lower_better": low}
                            for k, lb, u, low, _e, _r in PLAN_METRICS],
                "branches": [{**b, "now": {}, "prev": {}, "wins": [], "losses": []}
                             for b in branches]}

    # Опоздания сотрудников — из нашей базы (снимок явок кладёт оценка филиала),
    # одним запросом на всю сеть за каждую из двух недель. В iiko за ними не
    # ходим: девять филиалов на девяти серверах, и на собрании это ждать нельзя.
    from database import staff_late_progress
    _org_ids = [b["organization_id"] for b in branches]
    sl_now = staff_late_progress(_org_ids, d_from.isoformat(), now_to.isoformat())
    sl_prev = staff_late_progress(_org_ids, prev_from.isoformat(), prev_to.isoformat())

    async def one(b: Dict[str, str]):
        org = b["organization_id"]

        # meta — то, что не число: причина опозданий, медиана задержки. В now/prev
        # им не место, там только сравнимые показатели.
        async def week_metrics(a: date, z: date) -> Tuple[Dict[str, Any], Dict[str, Any]]:
            m: Dict[str, Any] = {}
            meta: Dict[str, Any] = {}
            try:
                ld = await late_deliveries(org, "custom", a.isoformat(), z.isoformat(), 1, user)
                if isinstance(ld, dict) and not ld.get("error"):
                    courier = (ld.get("by_type") or {}).get("courier") or {}
                    # median-этапы курьерских заказов: ровно то, что в таблице
                    # владельца названо «сред. время подачи» и «курьер в дороге»
                    for k in ("kitchen", "road", "idle"):
                        if courier.get(k) is not None:
                            m[k] = courier[k]
                    if ld.get("late_pct") is not None:
                        m["late_pct"] = ld["late_pct"]
                    m["deliveries"] = ld.get("delivered")
                    m["late_orders_count"] = ld.get("late")
                    # Главный вывод по опозданиям — не «сколько», а «из-за чего».
                    # Считается тем же запросом, вторым походом в iiko не платим.
                    meta["late_cause"] = ld.get("top_cause")
                    meta["late_causes"] = (ld.get("causes") or [])[:3]
                    meta["median_delay"] = ld.get("median_delay")
                    meta["late_orders"] = ld.get("late")
            except Exception as e:
                logger.warning(f"[plan-metrics] опоздания {b['name']}: {e!r}")
            try:
                # Через снимок: те же цифры, что и в сетке плана, и без похода
                # в iiko за уже закрытыми днями.
                m["orders"] = sum(sum(c.values()) for c in
                                  (await _orders_cached(org, a, z, user)).values())
            except Exception as e:
                logger.warning(f"[plan-metrics] заказы {b['name']}: {e!r}")
            return m, meta

        (now, meta), (prev, _prev_meta) = await asyncio.gather(
            week_metrics(d_from, now_to), week_metrics(prev_from, prev_to))
        # Причина опозданий — за ПОЛНУЮ неделю, а не за закрытые дни ISO-недели.
        # Собрание по вторникам, а во вторник ISO-неделя закрыта на один день:
        # «причина за понедельник» — это не причина, это один вечер. Берём
        # скользящие семь дней до последнего закрытого дня — ровно ту неделю,
        # которая на собрании и заканчивается.
        if cause_from != d_from:
            _cm, cause_meta = await week_metrics(cause_from, now_to)
            meta = {**meta, **{k: v for k, v in cause_meta.items() if v is not None}}
        meta["cause_period"] = {"from": cause_from.isoformat(), "to": now_to.isoformat()}
        try:
            # Рейтинг агрегаторов — снимок «сейчас», истории по неделям у нас нет,
            # поэтому в сравнение он попадает только когда обе цифры есть.
            rt = _rating_signal(await branch_reviews(org, user))
            if rt:
                now["rating"] = rt["avg"]
        except Exception as e:
            logger.warning(f"[plan-metrics] рейтинг {b['name']}: {e!r}")
        # Опоздания сотрудников считаются от старта смены филиала. Если филиал
        # его не задал, сравнение идёт с дефолтом 09:00/16:00 — а смена там
        # начинается в 10:40, и «опоздали 100%» это про ненастроенность, а не
        # про людей. Такой показатель в сравнение НЕ пускаем: он утащил бы за
        # собой и среднее по сети, и «что ухудшилось».
        ref = _late_reference(org)
        if ref["configured"]:
            if org in sl_now:
                now["staff_late_pct"] = sl_now[org]["late_pct"]
                now["staff_late_count"] = sl_now[org]["late"]
                now["staff_total_count"] = sl_now[org]["total"]
            if org in sl_prev:
                prev["staff_late_pct"] = sl_prev[org]["late_pct"]
                prev["staff_late_count"] = sl_prev[org]["late"]
                prev["staff_total_count"] = sl_prev[org]["total"]
        staff = dict(sl_now.get(org) or {})
        staff.update({"configured": ref["configured"], "starts": ref["starts"]})
        return {**b, "now": now, "prev": prev,
                "wins": plan_wins(now, prev), "losses": plan_losses(now, prev),
                # счётчики рядом с процентом: «12% опоздавших» на собрании
                # спрашивают «это сколько человек?»
                "staff": staff if sl_now.get(org) or not ref["configured"] else None,
                **meta}

    rows = list(await asyncio.gather(*(one(b) for b in branches)))
    return {"week": wk, "from": d_from.isoformat(), "to": d_to.isoformat(),
            "done_days": done,
            "metrics": [{"key": k, "label": lb, "unit": u, "lower_better": low}
                        for k, lb, u, low, _e, _r in PLAN_METRICS],
            "branches": rows}


class PlanCellsIn(BaseModel):
    week: str
    cells: List[Dict[str, Any]]     # [{"org":…, "dow":1, "ch_type":"hall", "planned":90}]
    token: Optional[str] = None     # ссылка управляющего: правит только свой филиал


@app.post("/api/plan-week/{org_id}")
async def plan_week_set(org_id: str, payload: PlanCellsIn,
                        user: Dict = Depends(get_current_user)):
    """Сохранить ячейки плана. Одним эндпоинтом и правка одной клетки, и
    «поставить всю неделю по средней» — на фронте это одно и то же действие."""
    from database import set_day_plans
    # Куда вообще можно писать: у владельца — вся сеть, у управляющего — его
    # филиал и только он (токен перебивает всё, см. _plan_branches).
    mine = {b["organization_id"] for b in _plan_branches(org_id, True, user, payload.token)}
    if not payload.token:
        mine.add(_get_org_id(org_id, user))
    cells = []
    for c in payload.cells:
        org = c.get("org") or _get_org_id(org_id, user)
        if org not in mine:
            raise HTTPException(status_code=403, detail="чужой филиал")
        if c.get("ch_type") not in PLAN_TYPES or not (1 <= int(c.get("dow", 0)) <= 7):
            raise HTTPException(status_code=400, detail="bad cell")
        cells.append({"org": org, "week": payload.week, "dow": int(c["dow"]),
                      "ch_type": c["ch_type"], "planned": max(0, int(c.get("planned") or 0))})
    if cells:
        set_day_plans(cells)
    return {"ok": True, "saved": len(cells)}


class PlanSetIn(BaseModel):
    month: str
    ch_type: str
    planned: int
    widx: Optional[int] = None
    all_weeks: bool = False


@app.post("/api/weekplan/{org_id}")
async def plan_set(org_id: str, payload: PlanSetIn, user: Dict = Depends(get_current_user)):
    """Сохранить план: одну неделю (widx) или все недели месяца (all_weeks)."""
    if payload.ch_type not in PLAN_TYPES:
        raise HTTPException(status_code=400, detail="bad ch_type")
    real_org = _get_org_id(org_id, user)
    planned = max(0, int(payload.planned))
    if payload.all_weeks:
        for w in _month_weeks(payload.month):
            set_weekly_plan(real_org, payload.month, w["widx"], payload.ch_type, planned)
    elif payload.widx is not None:
        set_weekly_plan(real_org, payload.month, int(payload.widx), payload.ch_type, planned)
    else:
        raise HTTPException(status_code=400, detail="widx or all_weeks required")
    return {"ok": True}


# ==== Соцсети: Instagram Business через Meta OAuth ====
# Каркас. Заработает, когда в окружении появятся META_APP_ID/META_APP_SECRET
# (приложение Meta) и Instagram-аккаунт клиента будет Business + привязан к FB-странице.

def _ig_close_html(msg: str, ok: bool = False) -> str:
    """Крошечная страница для popup: сообщает опенеру результат и закрывается."""
    return (
        "<!doctype html><meta charset='utf-8'>"
        "<body style='font:15px system-ui;padding:40px;text-align:center;color:#111'>"
        f"<p>{msg}</p>"
        "<script>try{window.opener&&window.opener.postMessage("
        f"{{source:'ig-oauth',ok:{'true' if ok else 'false'}}},'*');}}catch(e){{}}"
        "setTimeout(function(){window.close();},1200);</script></body>"
    )


@app.get("/api/social/instagram/connect/{org_id}")
async def ig_connect(org_id: str, request: Request, user: Dict = Depends(get_current_user)):
    """Ссылка на вход через Facebook. Если приложение Meta не настроено — configured:false."""
    # Филиал проверяем ДО ответа «не настроено»: иначе ручка отвечала 200 на
    # чужой филиал и выпадала из сплошной проверки изоляции.
    real_org = _get_org_id(org_id, user)
    if not social_ig.is_configured():
        return {"configured": False,
                "reason": "Нет META_APP_ID/META_APP_SECRET. Зарегистрируй приложение на "
                          "developers.facebook.com (тип Business) и добавь ключи в окружение."}
    redirect_uri = str(request.base_url).rstrip("/") + "/api/social/instagram/callback"
    return {"configured": True, "url": social_ig.authorize_url(redirect_uri, real_org)}


@app.get("/api/social/instagram/callback")
async def ig_callback(request: Request, code: Optional[str] = None,
                      state: Optional[str] = None, error: Optional[str] = None):
    """Facebook редиректит сюда после согласия. state = real_org (без auth-заголовка)."""
    from database import set_social_account, save_social_snapshot
    if error or not code or not state:
        return HTMLResponse(_ig_close_html(f"Не удалось подключить Instagram: {error or 'нет кода'}"))
    redirect_uri = str(request.base_url).rstrip("/") + "/api/social/instagram/callback"
    try:
        token = await social_ig.exchange_code(code, redirect_uri)
        acc = await social_ig.get_ig_account(token)
        if not acc:
            return HTMLResponse(_ig_close_html(
                "Instagram Business-аккаунт не найден. Переведи профиль в Business "
                "и привяжи к Facebook-странице."))
        prof = await social_ig.fetch_profile(acc["id"], token)
        set_social_account(state, "instagram", acc["id"], token,
                           prof.get("username") or acc.get("username", ""))
        if prof.get("followers") is not None:
            save_social_snapshot(state, "instagram", local_now().strftime("%Y-%m-%d"), prof["followers"])
        return HTMLResponse(_ig_close_html("Instagram подключён ✓", ok=True))
    except Exception as e:
        logger.warning(f"[social] ig callback: {e!r}")
        return HTMLResponse(_ig_close_html("Ошибка подключения Instagram."))


@app.get("/api/social/instagram/{org_id}")
async def ig_data(org_id: str, user: Dict = Depends(get_current_user)):
    """Данные подключённого Instagram: подписчики, рост за 30 дней, последние посты."""
    from database import get_social_account, save_social_snapshot, get_social_growth
    real_org = _get_org_id(org_id, user)
    acc = get_social_account(real_org, "instagram")
    if not acc:
        return {"connected": False, "configured": social_ig.is_configured()}
    try:
        prof = await social_ig.fetch_profile(acc["account_id"], acc["token"])
    except Exception as e:
        logger.warning(f"[social] ig data: {e!r}")
        return {"connected": True, "stale": True, "username": acc.get("username")}
    if prof.get("followers") is not None:
        save_social_snapshot(real_org, "instagram", local_now().strftime("%Y-%m-%d"), prof["followers"])
    growth = get_social_growth(real_org, "instagram", days=30)
    start = growth[0]["followers"] if growth else None
    delta = (prof["followers"] - start) if (start is not None and prof.get("followers") is not None) else None
    return {"connected": True, "username": prof.get("username"),
            "followers": prof.get("followers"), "media_count": prof.get("media_count"),
            "delta_30d": delta, "growth": growth, "posts": prof.get("posts", [])}


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


@app.get("/api/employees/{org_id}")
async def employees_directory(org_id: str, user: Dict = Depends(get_current_user)):
    """Имена сотрудников для расписания. Из iiko берём ТОЛЬКО имена: план, факт
    и опоздания считаются у нас, чтобы расписание не зависело от того, пробил
    ли человек чек на терминале.

    Кого чаще ставят в график в этом филиале — тот выше. /api/employees отдаёт
    сотрудников всего сервера одной пачкой, и без этого управляющий искал бы
    свою бригаду среди чужих точек."""
    if _use_demo_data(user, org_id):
        # Смены считаем ПО-НАСТОЯЩЕМУ: в сетку графика попадают только те, кого
        # уже ставили, а с жёстким нулём демо-бригада не показывалась вовсе —
        # экран писал «в этом филиале ещё никого не ставили».
        from database import staff_shift_counts
        try:
            cnt = staff_shift_counts(org_id)
        except Exception:
            cnt = {}
        return {"employees": [{"id": m["id"], "name": m["name"],
                               "shifts": int(cnt.get(m["id"], 0))}
                              for m in _DEMO_TEAM]}
    real_org = _get_org_id(org_id, user)
    info = _get_restaurant_info(real_org, user)
    client = get_client(info["api_login"], info.get("iiko_server"), info.get("api_password"))
    if not hasattr(client, "get_employees_map"):
        raise HTTPException(status_code=400,
                            detail="Список сотрудников доступен только на самохостед iiko")
    from database import staff_shift_counts
    try:
        employees = await client.get_employees_map()
    except Exception as e:
        logger.warning(f"[staff] имена {real_org}: {e!r}")
        employees = {}
    counts = staff_shift_counts(real_org)
    # Кто уже стоит в графике, но пропал из iiko (уволен, переименован), из
    # списка не выпадает: иначе его смены в сетке остались бы без имени.
    out = [{"id": eid, "name": employees.get(eid) or eid[:8], "shifts": counts.get(eid, 0)}
           for eid in set(employees) | set(counts)]
    out.sort(key=lambda e: (-e["shifts"], e["name"]))
    return {"employees": out}


@app.get("/api/attendance/{org_id}")
async def attendance(org_id: str,
                     from_: str = Query(..., alias="from"), to: str = Query(...),
                     user: Dict = Depends(get_current_user)):
    """Факт смен филиала — наш, не явки iiko. По нему считаются опоздания.
    Отметки кладёт POST ниже; пока их никто не ставит, ответ честно пустой."""
    if _use_demo_data(user, org_id):
        return _demo_attendance(from_, to)
    real_org = _get_org_id(org_id, user)
    from database import get_staff_attendance
    rows = get_staff_attendance(real_org, from_, to)
    emps: Dict[str, str] = {}
    for r in rows:
        emps.setdefault(r["employee_id"], r["name"] or r["employee_id"][:8])
    emp_list = [{"id": eid, "name": name} for eid, name in emps.items()]
    emp_list.sort(key=lambda x: x["name"])
    return {"employees": emp_list, "rows": rows}


class AttendanceMarkIn(BaseModel):
    employee_id: str
    date: str
    start: Optional[str] = None      # None — отметку снять
    end: Optional[str] = None        # пусто — человек ещё на смене
    name: Optional[str] = None


@app.post("/api/attendance/{org_id}")
async def attendance_mark(org_id: str, payload: AttendanceMarkIn,
                          user: Dict = Depends(get_current_user)):
    """Отметка «пришёл/ушёл». Одна на (сотрудник, день) — как и плановая смена."""
    from database import set_staff_attendance, remove_staff_attendance
    real_org = _get_org_id(org_id, user)
    if not payload.start:
        remove_staff_attendance(real_org, payload.employee_id, payload.date)
        return {"ok": True, "removed": True}
    if SL.to_min(payload.start) is None:
        raise HTTPException(status_code=400, detail="bad start time")
    if payload.end and SL.to_min(payload.end) is None:
        raise HTTPException(status_code=400, detail="bad end time")
    set_staff_attendance(real_org, payload.employee_id, payload.date,
                         payload.start, payload.end or "", payload.name or "")
    return {"ok": True}


# ============ МИНИ-АПП СМЕН: ВХОД СОТРУДНИКА ============
# Отдельный вход, намеренно НЕ через get_current_user. Там ветка «роль + филиал»
# (api.py, X-Site-Token) отдаёт права управляющего, и сотрудник получил бы весь
# дашборд филиала: выручку, склад, чужие зарплаты. Здесь личность — это личная
# ссылка плюс телеграм, а филиал берётся ИЗ КОДА, не из адреса запроса: ручки
# сотрудника не принимают org_id, поэтому подставить чужой филиал физически негде.

async def get_staff(
    x_staff_code: Optional[str] = Header(None, alias="X-Staff-Code"),
    x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data"),
) -> Dict[str, Any]:
    """Кто из сотрудников спрашивает: {org, employee_id, name, telegram_id}."""
    from database import staff_access_by_code, bind_staff_access
    if not x_staff_code:
        raise HTTPException(status_code=401, detail="Нужна личная ссылка")
    row = staff_access_by_code(x_staff_code)
    if not row:
        raise HTTPException(status_code=401, detail="Ссылка недействительна")
    if BOT_TOKEN:
        # Код без телеграма не пускаем: ссылку пересылают в общий чат, и тогда
        # отмечаться за человека мог бы кто угодно, кто её открыл.
        if not x_telegram_init_data:
            raise HTTPException(status_code=401, detail="Откройте ссылку в Telegram")
        tid = int(validate_telegram_init_data(x_telegram_init_data).get("id") or 0)
    else:
        tid = int(os.getenv("DEV_TID", "0") or 0)
    if not tid:
        raise HTTPException(status_code=401, detail="Не удалось определить пользователя")
    if not bind_staff_access(x_staff_code, tid):
        raise HTTPException(status_code=403,
                            detail="Ссылка уже привязана к другому телефону — попросите новую")
    return {"org": row["organization_id"], "employee_id": row["employee_id"],
            "name": row["name"] or "", "telegram_id": tid}


def _week_bounds(anchor_day: Optional[str] = None) -> tuple:
    """Понедельник–воскресенье недели, в которую попадает день."""
    d = date.fromisoformat(anchor_day) if anchor_day else local_now().date()
    mon = d - timedelta(days=d.weekday())
    return mon.isoformat(), (mon + timedelta(days=6)).isoformat()


@app.get("/api/me")
async def me_who(staff: Dict = Depends(get_staff)):
    """Кто я и где работаю. Заодно проверка, что ссылка ещё жива."""
    from database import get_branch_geo, branch_name
    return {"name": staff["name"], "employee_id": staff["employee_id"],
            "branch": branch_name(staff["org"]),
            "geo_required": bool(get_branch_geo(staff["org"]))}


@app.get("/api/me/week")
async def me_week(day: Optional[str] = Query(None), staff: Dict = Depends(get_staff)):
    """Мои смены на неделю: план, оплата и моя отметка о приходе."""
    from database import get_staff_schedule, get_staff_attendance
    org, eid = staff["org"], staff["employee_id"]
    d_from, d_to = _week_bounds(day)
    plan = {r["day"]: r for r in get_staff_schedule(org, d_from, d_to)
            if r["employee_id"] == eid}
    fact = {r["date"]: r for r in get_staff_attendance(org, d_from, d_to)
            if r["employee_id"] == eid}
    days = []
    cur = date.fromisoformat(d_from)
    while cur.isoformat() <= d_to:
        k = cur.isoformat()
        p, f = plan.get(k), fact.get(k)
        days.append({
            "date": k,
            "start": (p or {}).get("start_time") or "",
            "end": (p or {}).get("end_time") or "",
            "pay": (p or {}).get("pay"),
            "opened": (f or {}).get("start") or "",
            "geo_ok": (f or {}).get("geo_ok"),
        })
        cur += timedelta(days=1)
    # Смены без проставленной оплаты в сумму не идут — см. решение в staff_schedule.
    pay_total = sum(d["pay"] for d in days if d["pay"] is not None)
    return {"from": d_from, "to": d_to, "days": days, "pay_total": pay_total,
            "today": local_now().date().isoformat()}


class OpenShiftIn(BaseModel):
    lat: Optional[float] = None
    lon: Optional[float] = None


@app.post("/api/me/open-shift")
async def me_open_shift(payload: OpenShiftIn, staff: Dict = Depends(get_staff)):
    """Открыть смену. Геопозиция несовпала или её не дали — смену всё равно
    открываем, но помечаем: решение владельца. GPS в помещении врёт на сотни
    метров, и повар, застрявший на экране «вы не на месте», пойдёт звонить
    управляющему вместо того, чтобы готовить. Управляющий пометку видит.

    Повторное нажатие ничего не портит: отметка одна на (сотрудник, день), и
    время прихода остаётся первым — иначе человек мог бы «переоткрыть» смену
    попозже и стереть своё опоздание."""
    import geo as G
    from database import (get_branch_geo, get_staff_attendance, set_staff_attendance)
    org, eid = staff["org"], staff["employee_id"]
    today = local_now().date().isoformat()

    already = [r for r in get_staff_attendance(org, today, today) if r["employee_id"] == eid]
    if already:
        r = already[0]
        return {"ok": True, "already": True, "start": r["start"], "geo_ok": r["geo_ok"]}

    branch = get_branch_geo(org)
    ok = G.within(branch, payload.lat, payload.lon)
    now_hm = local_now().strftime("%H:%M")
    set_staff_attendance(org, eid, today, now_hm, "", staff["name"], "employee",
                         payload.lat, payload.lon, ok)
    return {"ok": True, "already": False, "start": now_hm, "geo_ok": None if ok is None else int(ok)}


def _month_bounds(month: Optional[str] = None) -> tuple:
    """Границы месяца 'YYYY-MM'. Зарплату считают месяцем, а не неделей."""
    d = date.fromisoformat((month or local_now().date().isoformat()[:7]) + "-01")
    nxt = date(d.year + (d.month == 12), (d.month % 12) + 1, 1)
    return d.isoformat(), (nxt - timedelta(days=1)).isoformat()


@app.get("/api/me/pay")
async def me_pay(month: Optional[str] = Query(None), staff: Dict = Depends(get_staff)):
    """Мой расчёт за месяц: начислено сменами, выдано на руки, остаток.

    Начисление и выплата живут раздельно: «отработал» и «получил» расходятся во
    времени, и человеку важно видеть не одну цифру, а обе и разницу."""
    from database import get_staff_schedule, get_staff_payouts, staff_pay_summary
    org, eid = staff["org"], staff["employee_id"]
    d_from, d_to = _month_bounds(month)
    mine = staff_pay_summary(org, d_from, d_to).get(eid, {"earned": 0, "paid": 0,
                                                          "balance": 0, "shifts": 0})
    shifts = [{"date": r["day"], "start": r["start_time"], "end": r["end_time"] or "",
               "pay": r["pay"]}
              for r in get_staff_schedule(org, d_from, d_to)
              if r["employee_id"] == eid and r["pay"] is not None]
    shifts.sort(key=lambda x: x["date"])
    payouts = [{"day": p["day"], "amount": p["amount"], "comment": p["comment"] or ""}
               for p in get_staff_payouts(org, d_from, d_to, eid)]
    return {"month": d_from[:7], "from": d_from, "to": d_to,
            "earned": mine["earned"], "paid": mine["paid"], "balance": mine["balance"],
            "shifts": shifts, "payouts": payouts}


@app.get("/api/me/notices")
async def me_notices(staff: Dict = Depends(get_staff)):
    """Объявления смене и адресованные лично мне. Чужие личные сюда не попадают."""
    from database import get_staff_notices
    rows = get_staff_notices(staff["org"], staff["employee_id"], staff["telegram_id"])
    return {"notices": [{"id": r["id"], "text": r["text"], "created_at": r["created_at"],
                         "personal": bool(r["to_employee_id"]), "is_read": bool(r["is_read"])}
                        for r in rows],
            "unread": sum(1 for r in rows if not r["is_read"])}


@app.post("/api/me/notices/{notice_id}/read")
async def me_notice_read(notice_id: int, staff: Dict = Depends(get_staff)):
    from database import mark_notice_read, staff_notice_is_for
    if not staff_notice_is_for(notice_id, staff["org"], staff["employee_id"]):
        raise HTTPException(status_code=404, detail="Сообщение не найдено")
    mark_notice_read(notice_id, staff["telegram_id"])
    return {"ok": True}


# Где живёт мини-апп. MINIAPP_URL уважаем, только если он ЗАДАН явно: его
# дефолт в коде указывает на домен, который отдаёт 503, и кнопки, собранные по
# нему, были бы мёртвыми у всех разом. Иначе берём хост последнего запроса —
# фоновым рассылкам спросить неоткуда, а приложение всегда отвечает по тому же
# адресу, по которому его открывают.
_seen_base: str = ""


def _app_base(request: Request) -> str:
    global _seen_base
    _seen_base = str(request.base_url).rstrip("/")
    return (os.getenv("MINIAPP_URL") or _seen_base).rstrip("/")


def _miniapp_url() -> str:
    return (os.getenv("MINIAPP_URL") or _seen_base or MINIAPP_URL).rstrip("/")


def _tg_link(payload: str) -> str:
    """Ссылка для сотрудника — ТОЛЬКО через бота.

    Обычный https-адрес мини-аппа не годится: открытый даже внутри Telegram, он
    попадает во встроенный браузер, а не в Mini App, и подписи (initData) там
    нет — вход по такой ссылке невозможен в принципе. Бот же отвечает кнопкой
    web_app, и вот она открывает настоящий Mini App с подписью."""
    return f"https://t.me/{BOT_USERNAME}?start={payload}"


# ============ МИНИ-АПП СМЕН: ПОДКЛЮЧЕНИЕ ПО QR ФИЛИАЛА ============
# Основной путь входа. Личные ссылки каждому — работа, про которую управляющий
# забудет на третьем новичке, и человек тихо перестанет отмечаться. QR висит в
# подсобке: сотрудник сканирует и ОДИН РАЗ выбирает себя, дальше он привязан к
# своему телеграму. Подменить коллегу можно только в этот единственный момент,
# и это видно управляющему — в панели написано, кто и когда привязался.


async def _join_org(token: str) -> str:
    """org филиала по коду подключения. 404, а не 401: несуществующий код и
    чужой для сканирующего — одно и то же."""
    from database import org_by_join_code
    org = org_by_join_code(token or "")
    if not org:
        raise HTTPException(status_code=404, detail="Ссылка недействительна")
    return org


def _join_tid(init_data: Optional[str]) -> int:
    """Кто сканирует. Телеграм обязателен: привязка — это и есть вся защита,
    без неё QR из подсобки давал бы доступ любому, кто его сфотографировал."""
    if BOT_TOKEN:
        if not init_data:
            raise HTTPException(status_code=401, detail="Откройте ссылку в Telegram")
        return int(validate_telegram_init_data(init_data).get("id") or 0)
    return int(os.getenv("DEV_TID", "0") or 0)


@app.get("/api/join/{token}")
async def join_list(token: str,
                    x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    """Кого можно выбрать. Уже привязанных не показываем — иначе первым делом
    предложили бы занять чужое место."""
    from database import list_staff_access, staff_shift_counts, branch_name
    org = await _join_org(token)
    tid = _join_tid(x_telegram_init_data)

    taken = {a["employee_id"] for a in list_staff_access(org) if a["telegram_id"]}
    mine = [a for a in list_staff_access(org) if a["telegram_id"] == tid]
    if mine:
        # Уже подключался с этого телефона — второй раз выбирать себя не нужно.
        return {"branch": branch_name(org), "already": True,
                "code": mine[0]["code"], "employees": []}

    import mountain_routes as _mr
    owner = {"id": _mr._owner_of(org), "username": "join", "first_name": "Join"}
    info = _get_restaurant_info(org, owner)
    client = get_client(info["api_login"], info.get("iiko_server"), info.get("api_password"))
    names: Dict[str, str] = {}
    if hasattr(client, "get_employees_map"):
        try:
            names = await client.get_employees_map()
        except Exception as e:
            logger.warning(f"[join] имена {org}: {e!r}")
    counts = staff_shift_counts(org)
    # Кого чаще ставят в график — тот выше: свою фамилию человек должен видеть
    # сразу, а не листать весь сервер.
    out = [{"id": eid, "name": names.get(eid) or eid[:8], "shifts": counts.get(eid, 0)}
           for eid in (set(names) | set(counts)) - taken]
    out.sort(key=lambda e: (-e["shifts"], e["name"]))
    return {"branch": branch_name(org), "already": False, "employees": out}


class JoinIn(BaseModel):
    employee_id: str
    name: Optional[str] = None


@app.post("/api/join/{token}")
async def join_claim(token: str, payload: JoinIn,
                     x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    """Выбрал себя — привязались. Отдаём личный код: дальше человек ходит по нему,
    а QR ему больше не нужен."""
    from database import (issue_staff_access, bind_staff_access, list_staff_access)
    org = await _join_org(token)
    tid = _join_tid(x_telegram_init_data)
    if not tid:
        raise HTTPException(status_code=401, detail="Не удалось определить пользователя")
    if not payload.employee_id:
        raise HTTPException(status_code=400, detail="Выберите себя из списка")

    existing = {a["employee_id"]: a for a in list_staff_access(org)}
    mine = next((a for a in existing.values() if a["telegram_id"] == tid), None)
    if mine:
        return {"ok": True, "code": mine["code"], "already": True}
    taken = existing.get(payload.employee_id)
    if taken and taken["telegram_id"]:
        raise HTTPException(status_code=409,
                            detail="Этого сотрудника уже подключили — позовите управляющего")

    code = issue_staff_access(org, payload.employee_id, payload.name or "")
    if not bind_staff_access(code, tid):
        raise HTTPException(status_code=409, detail="Не удалось привязать, попробуйте ещё раз")
    return {"ok": True, "code": code, "already": False}


# ============ МИНИ-АПП СМЕН: СТОРОНА УПРАВЛЯЮЩЕГО ============
# Эти ручки принимают {org_id} и потому обязаны идти через _get_org_id —
# единственную точку изоляции по филиалам (её контракт стережёт test_site_isolation).


class StaffAccessIn(BaseModel):
    employee_id: str
    name: Optional[str] = None


@app.get("/api/staff-access/{org_id}")
async def staff_access_list(org_id: str, request: Request,
                            user: Dict = Depends(get_current_user)):
    """Ссылка подключения филиала (её вешают в подсобке) плюс кто уже привязался."""
    from database import list_staff_access, get_or_create_join_code
    real_org = _get_org_id(org_id, user)
    rows = list_staff_access(real_org)
    return {"join_url": _tg_link(f"join_{get_or_create_join_code(real_org)}"),
            "access": [{"employee_id": r["employee_id"], "name": r["name"] or "",
                        "bound": bool(r["telegram_id"]), "bound_at": r["bound_at"],
                        "url": _tg_link(f"emp_{r['code']}")} for r in rows]}


@app.post("/api/staff-access/{org_id}")
async def staff_access_issue(org_id: str, payload: StaffAccessIn, request: Request,
                             user: Dict = Depends(get_current_user)):
    """Выдать (или перевыпустить) личную ссылку сотруднику. Основной путь входа —
    QR филиала, а это запасной: человек сменил телефон или ссылка утекла."""
    from database import issue_staff_access
    real_org = _get_org_id(org_id, user)
    if not payload.employee_id:
        raise HTTPException(status_code=400, detail="employee_id обязателен")
    code = issue_staff_access(real_org, payload.employee_id, payload.name or "")
    return {"ok": True, "url": _tg_link(f"emp_{code}")}


@app.post("/api/staff-access/{org_id}/rotate")
async def staff_access_rotate(org_id: str, user: Dict = Depends(get_current_user)):
    """Новый QR филиала. Нужен, когда прежний сфотографировали или он уехал с
    уволенным управляющим: старый код умирает в тот же миг.

    Привязок это не трогает — кто уже подключился, ходит по личной ссылке и
    перевыпуска не замечает. Перепечатать лист в подсобке дешевле, чем
    объяснять сотрудникам, почему они вдруг выпали из мини-аппа."""
    from database import rotate_join_code
    real_org = _get_org_id(org_id, user)
    return {"ok": True, "join_url": _tg_link(f"join_{rotate_join_code(real_org)}")}


@app.post("/api/staff-access/{org_id}/revoke")
async def staff_access_revoke(org_id: str, payload: StaffAccessIn,
                              user: Dict = Depends(get_current_user)):
    from database import revoke_staff_access
    revoke_staff_access(_get_org_id(org_id, user), payload.employee_id)
    return {"ok": True}


class BranchGeoIn(BaseModel):
    lat: float
    lon: float
    radius: Optional[int] = None


@app.get("/api/geocode/{org_id}")
async def geocode(org_id: str, q: str = Query(..., min_length=3),
                  user: Dict = Depends(get_current_user)):
    """Адрес → координаты, чтобы точку филиала задавали из кабинета, а не стоя
    в заведении.

    OpenStreetMap (Nominatim): без ключей и без счетов. Он просит представляться
    и не частить — для настройки, которую делают несколько раз за жизнь филиала,
    этого хватает с запасом. Сами ничего не выбираем: «Абая 10» есть в каждом
    городе, поэтому отдаём варианты, а решает человек."""
    _get_org_id(org_id, user)          # изоляция: чужой филиал не настраиваем
    try:
        async with httpx.AsyncClient(timeout=12.0) as c:
            r = await c.get("https://nominatim.openstreetmap.org/search",
                            params={"q": q, "format": "json", "limit": 5,
                                    "accept-language": "ru"},
                            headers={"User-Agent": "iiko-analytics/1.0 (branch geo setup)"})
            r.raise_for_status()
            found = r.json()
    except Exception as e:
        logger.warning(f"[geocode] {q!r}: {e!r}")
        raise HTTPException(status_code=502, detail="Поиск адреса недоступен, попробуйте позже")
    out = []
    for item in found:
        try:
            out.append({"label": item.get("display_name") or q,
                        "lat": float(item["lat"]), "lon": float(item["lon"])})
        except (KeyError, TypeError, ValueError):
            continue
    return {"results": out}


@app.get("/api/branch-geo/{org_id}")
async def branch_geo_get(org_id: str, user: Dict = Depends(get_current_user)):
    """Точка филиала и его название. Название отдаём, чтобы панель подставила
    адрес сама: в iiko филиалы и называются адресом («Астана, ул. Г.Алиева 10»),
    и переписывать его руками — лишняя работа с опечатками."""
    from database import get_branch_geo, branch_name
    real_org = _get_org_id(org_id, user)
    name = branch_name(real_org)
    return {"geo": get_branch_geo(real_org), "name": name,
            # Без номера дома геокодер поставит точку в центр улицы, и радиус
            # промахнётся. Честно говорим об этом, а не молча ставим мимо.
            "has_house_number": bool(re.search(r"\d", re.sub(r"\(.*?\)", "", name)))}


@app.post("/api/branch-geo/{org_id}")
async def branch_geo_set(org_id: str, payload: BranchGeoIn,
                         user: Dict = Depends(get_current_user)):
    """«Филиал здесь» — управляющий сохраняет точку, стоя в заведении."""
    from database import set_branch_geo
    if not (-90 <= payload.lat <= 90) or not (-180 <= payload.lon <= 180):
        raise HTTPException(status_code=400, detail="Координаты вне диапазона")
    real_org = _get_org_id(org_id, user)
    set_branch_geo(real_org, payload.lat, payload.lon, payload.radius or 250)
    return {"ok": True, "geo": {"lat": payload.lat, "lon": payload.lon,
                                "radius": max(20, int(payload.radius or 250))}}


class PayoutIn(BaseModel):
    employee_id: str
    amount: float
    day: Optional[str] = None
    comment: Optional[str] = None


class PayoutDelIn(BaseModel):
    id: int


@app.get("/api/payouts/{org_id}")
async def payouts_list(org_id: str, month: Optional[str] = Query(None),
                       user: Dict = Depends(get_current_user)):
    """Расчёт по бригаде за месяц: кому сколько начислено, выдано и осталось."""
    from database import staff_pay_summary, get_staff_payouts, list_staff_access
    real_org = _get_org_id(org_id, user)
    d_from, d_to = _month_bounds(month)
    summary = staff_pay_summary(real_org, d_from, d_to)
    names = {a["employee_id"]: a["name"] for a in list_staff_access(real_org) if a["name"]}
    rows = [{"employee_id": eid, "name": names.get(eid, eid[:8]), **v}
            for eid, v in summary.items()]
    # Сначала те, кому должны: управляющий открывает экран ради них.
    rows.sort(key=lambda r: (-r["balance"], r["name"]))
    return {"month": d_from[:7], "rows": rows,
            "payouts": get_staff_payouts(real_org, d_from, d_to),
            "total_earned": round(sum(r["earned"] for r in rows), 2),
            "total_paid": round(sum(r["paid"] for r in rows), 2),
            "total_balance": round(sum(r["balance"] for r in rows), 2)}


@app.post("/api/payouts/{org_id}")
async def payout_add(org_id: str, payload: PayoutIn,
                     user: Dict = Depends(get_current_user)):
    """Записать выдачу. Отрицательная сумма — это удержание, и вводить его так
    мы не даём: для исправления ошибки выплату удаляют, а не гасят минусом."""
    from database import add_staff_payout
    real_org = _get_org_id(org_id, user)
    if not payload.employee_id:
        raise HTTPException(status_code=400, detail="employee_id обязателен")
    if payload.amount is None or payload.amount <= 0:
        raise HTTPException(status_code=400, detail="Сумма должна быть больше нуля")
    day = payload.day or local_now().date().isoformat()
    try:
        date.fromisoformat(day)
    except ValueError:
        raise HTTPException(status_code=400, detail="Неверная дата")
    pid = add_staff_payout(real_org, payload.employee_id, day, float(payload.amount),
                           payload.comment or "", user.get("id", 0))
    return {"ok": True, "id": pid}


@app.post("/api/payouts/{org_id}/delete")
async def payout_delete(org_id: str, payload: PayoutDelIn,
                        user: Dict = Depends(get_current_user)):
    from database import delete_staff_payout
    real_org = _get_org_id(org_id, user)
    if not delete_staff_payout(real_org, payload.id):
        raise HTTPException(status_code=404, detail="Выплата не найдена")
    return {"ok": True}


class StaffNoticeIn(BaseModel):
    text: str
    employee_id: Optional[str] = None    # None — объявление всей смене


@app.get("/api/staff-notice/{org_id}")
async def staff_notice_list(org_id: str, user: Dict = Depends(get_current_user)):
    from database import sent_staff_notices
    return {"sent": sent_staff_notices(_get_org_id(org_id, user))}


@app.post("/api/staff-notice/{org_id}")
async def staff_notice_send(org_id: str, payload: StaffNoticeIn,
                            user: Dict = Depends(get_current_user)):
    """Сообщение смене или лично сотруднику. Ложится в те же branch_notices, что
    и объявления владельца, но с audience='staff' — иначе вся переписка с
    бригадой высыпалась бы управляющим в их ленту."""
    from database import add_branch_notice
    real_org = _get_org_id(org_id, user)
    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Пустое сообщение")
    if len(text) > 2000:
        raise HTTPException(status_code=400, detail="Слишком длинное сообщение")
    nid = add_branch_notice(text, user.get("id", 0), real_org,
                            audience="staff", to_employee_id=payload.employee_id or None)
    return {"ok": True, "id": nid}




# ============ ОПОЗДАНИЯ СОТРУДНИКОВ ============
# Факт прихода берём из явок iiko (терминал), эталон — гибридный: время старта
# смены филиала, а если на сотрудника стоит график — график. Решение владельца
# 25.08: сотрудника ни о чём не спрашиваем, отметку он не ставит.

async def _attendance_rows(org_id: str, real_org: str, day_from: str, day_to: str,
                           user: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    """Факт смен филиала за период — из НАШИХ отметок, не из явок iiko.

    Явки терминала знали только тех, кто пробил чек, и приходили с задержкой на
    незакрытой смене; опоздание по ним было наполовину догадкой. Теперь источник
    один — отметка, поставленная в приложении.

    None — отметок у филиала нет вообще. Это не «все пришли вовремя»: критерий
    по такому филиалу должен выпасть, а не обнулить оценку."""
    if _use_demo_data(user, org_id):
        return _demo_attendance(day_from, day_to)["rows"]
    from database import get_staff_attendance
    return get_staff_attendance(real_org, day_from, day_to) or None


def _late_reference(real_org: str) -> Dict[str, Any]:
    """С чем сравнивать приход в этом филиале: свои старты и допуск, иначе общие."""
    from database import get_shift_starts
    cfg = get_shift_starts(real_org)
    starts = [s["time"] for s in cfg["starts"]] or list(SL.DEFAULT_STARTS)
    grace = cfg["grace_min"] if cfg["grace_min"] is not None else SL.DEFAULT_GRACE_MIN
    return {"starts": starts, "grace_min": int(grace), "configured": bool(cfg["starts"])}


async def _staff_late_for(org_id: str, real_org: str, day_from: str, day_to: str,
                          user: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Приходы и опоздания филиала за период. Результат кладём в БД: оценка
    филиала считается по нему и в iiko повторно не ходит."""
    from database import get_staff_schedule, save_staff_late
    rows = await _attendance_rows(org_id, real_org, day_from, day_to, user)
    if rows is None:
        return None
    sched = {(r["employee_id"], r["day"]): r["start_time"]
             for r in get_staff_schedule(real_org, day_from, day_to)}
    ref = _late_reference(real_org)
    calc = SL.late_rows(rows, sched, ref["starts"], ref["grace_min"])
    save_staff_late(real_org, calc)
    # top/by_day считаем здесь же: для периода это те же строки, второй запрос в
    # iiko ради рейтинга «кто чаще опаздывает» был бы платой ни за что.
    return {"rows": calc, "summary": SL.summarize(calc),
            "top": SL.by_employee(calc), "by_day": SL.by_day(calc), **ref}


@app.get("/api/staff-late/{org_id}")
async def staff_late_day(org_id: str, date: Optional[str] = None,
                         to: Optional[str] = None,
                         user: Dict = Depends(get_current_user)):
    """Кто пришёл на смену, во сколько и кто опоздал. По умолчанию — сегодня."""
    day_from = date or local_now().date().isoformat()
    day_to = to or day_from
    real_org = _get_org_id(org_id, user)
    # Экран смен просит день, месяц и историю сразу — без кеша это три похода в
    # iiko за одними и теми же явками.
    cache_key = f"staff_late:{real_org}:{day_from}:{day_to}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached
    res = await _staff_late_for(org_id, real_org, day_from, day_to, user)
    if res is None:
        return {"available": False,
                "reason": "Явки доступны только на самохостед iiko",
                "rows": [], "summary": SL.summarize([]), "top": [], "by_day": []}
    resp = {"available": True, "date": day_from, "from": day_from, "to": day_to, **res}
    _set_cached(cache_key, resp)
    return resp


class ShiftStartsIn(BaseModel):
    starts: List[Dict[str, str]]          # [{"kind": "morning", "time": "09:00"}, ...]
    grace_min: Optional[int] = None
    all_branches: bool = False            # применить ко всей сети одним действием


@app.get("/api/shift-starts/{org_id}")
async def shift_starts_get(org_id: str, user: Dict = Depends(get_current_user)):
    """Эталон старта смен филиала. configured=false — показываем значения по
    умолчанию и честно говорим, что филиал ещё не настроен."""
    return _late_reference(_get_org_id(org_id, user))


@app.post("/api/shift-starts/{org_id}")
async def shift_starts_set(org_id: str, payload: ShiftStartsIn,
                           user: Dict = Depends(get_current_user)):
    from database import set_shift_starts
    real_org = _get_org_id(org_id, user)
    clean = [{"kind": s.get("kind") or "shift", "time": s["time"]}
             for s in payload.starts if SL.to_min(s.get("time", "")) is not None]
    if not clean:
        raise HTTPException(status_code=400, detail="нужно хотя бы одно время старта")
    # Пока филиал не задал старт смены, критерий «опоздания сотрудников» молчит —
    # и восемь филиалов из девяти висели с «нет данных». Настраивать каждый
    # руками владелец не станет: даём применить на всю сеть одним действием.
    targets = [real_org]
    if payload.all_branches:
        targets = [b["organization_id"] for b in _plan_branches(org_id, True, user)] or targets
    for org in targets:
        set_shift_starts(org, clean, payload.grace_min)
    return {"ok": True, "applied": len(targets), **_late_reference(real_org)}


class ShiftPlanIn(BaseModel):
    employee_id: str
    date: str
    start: Optional[str] = None           # None → снять смену с этого дня
    end: Optional[str] = None
    pay: Optional[float] = None      # оплата за смену; ставок нет, пишется руками


@app.get("/api/schedule/{org_id}")
async def schedule_get(org_id: str, from_: str = Query(..., alias="from"),
                       to: str = Query(...), user: Dict = Depends(get_current_user)):
    """График смен филиала. Раньше жил в localStorage браузера — то есть у
    каждого свой; теперь общий, и по нему считаются опоздания."""
    from database import get_staff_schedule
    rows = get_staff_schedule(_get_org_id(org_id, user), from_, to)
    return {"shifts": [{"employeeId": r["employee_id"], "date": r["day"],
                        "start": r["start_time"], "end": r["end_time"] or "",
                        "pay": r["pay"]} for r in rows]}


@app.post("/api/schedule/{org_id}")
async def schedule_set(org_id: str, payload: ShiftPlanIn,
                       user: Dict = Depends(get_current_user)):
    from database import set_staff_shift, remove_staff_shift
    real_org = _get_org_id(org_id, user)
    if not payload.start:
        remove_staff_shift(real_org, payload.employee_id, payload.date)
        return {"ok": True, "removed": True}
    if SL.to_min(payload.start) is None:
        raise HTTPException(status_code=400, detail="bad start time")
    # Отрицательная оплата — это опечатка, а не штраф: штраф вычитают из суммы.
    if payload.pay is not None and payload.pay < 0:
        raise HTTPException(status_code=400, detail="bad pay")
    set_staff_shift(real_org, payload.employee_id, payload.date, payload.start,
                    payload.end or "", payload.pay)
    return {"ok": True}


async def _late_alerts_round() -> int:
    """Один проход: по каждому филиалу считаем сегодняшние опоздания и шлём
    управляющему то, о чём ещё не писали. Возвращает число отправленных."""
    from database import get_branch_managers, mark_late_notified
    managers = {m["organization_id"]: m for m in get_branch_managers()}
    if not managers:
        return 0
    today = local_now().date().isoformat()
    sent = 0
    for org, mgr in managers.items():
        chat = int(mgr.get("telegram_id") or 0)
        if not chat:
            continue
        owner = _owner_user_of(org)
        if not owner:
            continue
        try:
            res = await _staff_late_for(org, org, today, today, owner)
        except Exception as e:
            logger.warning(f"[late-alerts] {org}: {e!r}")
            continue
        if not res:
            continue
        for r in res["rows"]:
            if r["status"] != "late":
                continue
            if not mark_late_notified(org, today, r["employee_id"]):
                continue      # уже писали об этом человеке сегодня
            await _tg_send(chat, f"⏰ <b>{html.escape(r['name'])}</b> пришёл(ла) в "
                                 f"{r['arrival']} — опоздание {r['late_min']} мин "
                                 f"(старт {r['planned']})")
            sent += 1
    return sent


async def _late_alerts_digest() -> bool:
    """Вечерняя сводка владельцу: по каждому филиалу — кто и на сколько опоздал.
    Владельцу не сыплем по одному сообщению на человека: девять филиалов — это
    десятки сообщений в день, их перестают читать."""
    from database import get_branch_managers, get_db
    today = local_now().date().isoformat()
    by_owner: Dict[int, List[str]] = {}
    conn = get_db()
    rows = conn.execute("SELECT organization_id, organization_name, telegram_id "
                        "FROM restaurants WHERE telegram_id IS NOT NULL").fetchall()
    conn.close()
    seen: set = set()
    for r in rows:
        org, name, owner_uid = r[0], r[1] or "Филиал", int(r[2] or 0)
        if not org or not owner_uid or org in seen:
            continue
        seen.add(org)
        owner = _owner_user_of(org)
        if not owner:
            continue
        try:
            res = await _staff_late_for(org, org, today, today, owner)
        except Exception as e:
            logger.warning(f"[late-digest] {org}: {e!r}")
            continue
        if not res or not res["summary"]["total"]:
            continue
        late = [x for x in res["rows"] if x["status"] == "late"]
        if not late:
            continue
        who = ", ".join(f"{html.escape(x['name'])} +{x['late_min']}" for x in late[:6])
        by_owner.setdefault(owner_uid, []).append(
            f"• <b>{html.escape(name)}</b>: {len(late)} из {res['summary']['total']} — {who}")
    for uid, lines in by_owner.items():
        await _tg_send(uid, "⏰ <b>Опоздания за сегодня</b>\n\n" + "\n".join(lines))
    return bool(by_owner)


def _owner_user_of(org_id: str) -> Optional[Dict[str, Any]]:
    """Владелец филиала как «пользователь» для запросов в iiko. Креды берутся
    ПО ФИЛИАЛУ, но get_current_user-подобный dict всё равно нужен: без него
    _get_restaurant_info подставит демо-клиента (грабли из 234.md)."""
    from database import get_db
    conn = get_db()
    row = conn.execute("SELECT telegram_id FROM restaurants WHERE organization_id = ? LIMIT 1",
                       (org_id,)).fetchone()
    conn.close()
    if not row or not row[0]:
        return None
    # Ровно та форма, что отдаёт get_current_user: запросы к iiko читают
    # user["id"]. Строка из БД тут не годится — там ключ telegram_id, и
    # _get_restaurant_info искал бы филиалы владельца с id=0 → 404.
    return {"id": int(row[0]), "username": "system", "first_name": "Owner"}


async def _shift_reminders_round(day: Optional[str] = None) -> int:
    """Напоминание «завтра у вас смена» тем, у кого она стоит в графике.

    Пишем в телеграм, а не в мини-апп: смысл напоминания в том, чтобы человек
    УЗНАЛ о смене, не открывая приложение. Сумму в текст не кладём — уведомление
    видно на заблокированном экране, и зарплата там ни к чему; она есть внутри.

    Возвращает число отправленных."""
    from database import shifts_with_access, mark_shift_reminded, branch_name
    target = day or (local_now().date() + timedelta(days=1)).isoformat()
    sent = 0
    names: Dict[str, str] = {}
    for r in shifts_with_access(target):
        org, eid = r["organization_id"], r["employee_id"]
        if not mark_shift_reminded(org, eid, target):
            continue
        if org not in names:
            names[org] = branch_name(org)
        when = r["start_time"] + (f'–{r["end_time"]}' if r["end_time"] else "")
        text = (f'<b>Завтра смена — {when}</b>\n{names[org]}\n\n'
                f'Открыть её нужно на месте, когда придёте.')
        kb = {"inline_keyboard": [[{"text": "Мои смены",
                                    "web_app": {"url": f"{_miniapp_url()}/?e={r['code']}"}}]]}
        if await _tg_send(int(r["telegram_id"]), text, kb):
            sent += 1
    return sent


async def _shift_reminders_scheduler():
    """Вечером напоминаем про завтрашнюю смену. 20:00 — человек ещё не спит и
    уже дома, то есть успевает переспросить управляющего, если график не тот.

    Рубильника нет намеренно: телеграм берётся из привязанной личной ссылки, а
    значит рассылка молчит сама, пока сотрудники в мини-апп не пришли."""
    while True:
        try:
            now = local_now()
            if now.hour == 20:
                n = await _shift_reminders_round()
                if n:
                    logger.info(f"[shift-reminders] напомнили: {n}")
        except Exception as e:
            logger.warning(f"[shift-reminders] цикл: {e!r}")
        await asyncio.sleep(1800)


async def _late_alerts_scheduler():
    """Раз в 15 минут в рабочем окне — опоздания управляющим, вечером — сводка
    владельцу. Выключено, пока не задан LATE_ALERTS=1: без реальных telegram id
    управляющих слать некому (хвост из 234.md, п.3)."""
    while True:
        try:
            now = local_now()
            if 6 <= now.hour < 23:
                await _late_alerts_round()
            if now.hour == 21 and get_app_state("late_digest_last_sent") != now.date().isoformat():
                if await _late_alerts_digest():
                    set_app_state("late_digest_last_sent", now.date().isoformat())
        except Exception as e:
            logger.warning(f"[late-alerts] цикл: {e!r}")
        await asyncio.sleep(900)


# ============ ЕЖЕДНЕВНАЯ ОЦЕНКА И ВТОРНИЧНАЯ СРЕДНЯЯ ============

async def _send_weekly_scores() -> bool:
    """Вторник: владельцу — таблица по всей сети, каждому управляющему — его
    средняя и место. Управляющему чужие цифры не шлём: сравнение он и так
    видит на «Горе», а в личном сообщении это выглядит как разнос при всех."""
    import mountain_routes as _mr
    from database import get_branch_managers, branch_score_average, get_db
    conn = get_db()
    uids = [int(r[0]) for r in conn.execute(
        "SELECT DISTINCT telegram_id FROM restaurants WHERE telegram_id IS NOT NULL").fetchall()]
    conn.close()
    managers = {m["organization_id"]: m for m in get_branch_managers()}
    sent = False
    for uid in uids:
        sc = _mr._scope(uid)
        if sc["role"] != "owner" or not sc["branches"]:
            continue
        lines = _mr.weekly_score_lines(sc["branches"])
        if not lines:
            continue
        await _tg_send(uid, "📊 <b>Средняя оценка филиалов за неделю</b>\n\n" + "\n".join(lines))
        sent = True
        # Управляющим — своё место и своя средняя
        ids = [b["organization_id"] for b in sc["branches"]]
        today = local_now().date()
        avg = branch_score_average(ids, (today - timedelta(days=7)).isoformat(), today.isoformat())
        order = sorted(avg.items(), key=lambda kv: -kv[1]["avg"])
        place = {org: i + 1 for i, (org, _v) in enumerate(order)}
        for b in sc["branches"]:
            org = b["organization_id"]
            mgr, a = managers.get(org), avg.get(org)
            if not mgr or not a or not mgr.get("telegram_id"):
                continue
            await _tg_send(int(mgr["telegram_id"]),
                           f"📊 <b>{html.escape(b['name'])}</b> за неделю: "
                           f"<b>{a['avg']} из 100</b>\n"
                           f"Место в сети: {place.get(org, '—')} из {len(order)}.")
    return sent


async def _close_plan_day() -> int:
    """Обновляем последние семь закрытых дней по каждому филиалу.

    Одного вчерашнего дня недостаточно: iiko допускает возвраты, перепробитие
    и позднее закрытие заказа задним числом. Недельное окно исправляет такие
    изменения, но вкладка по-прежнему открывается из быстрой локальной базы.

    Строго говоря, это оптимизация: чего в снимке нет, `_orders_cached` доберёт
    на лету. Ночной проход нужен, чтобы утром никто этого добора не ждал.
    Возвращает число филиалов, по которым день закрылся."""
    from database import get_db
    conn = get_db()
    orgs = [r[0] for r in conn.execute(
        "SELECT DISTINCT organization_id FROM restaurants WHERE organization_id IS NOT NULL").fetchall()]
    conn.close()
    day = local_now().date() - timedelta(days=1)
    day_from = day - timedelta(days=6)
    done = 0
    for org in orgs:
        owner = _owner_user_of(org)
        if not owner:
            continue
        try:
            # Один филиал не должен ронять проход: у части клиентов iiko-сервер
            # может лежать, а остальные восемь закрыться обязаны.
            if await _refresh_plan_facts(org, day_from, day, owner):
                done += 1
        except Exception as e:
            logger.warning(f"[plan] не закрыл день {day} по {org}: {e!r}")
    return done


async def _daily_scores_scheduler():
    """Ночью считаем оценку каждого филиала и кладём снимок в базу, во вторник
    утром рассылаем среднюю за неделю. Раньше оценка была мгновенной: понять,
    стало лучше или хуже, было не по чему."""
    import mountain_routes as _mr
    while True:
        try:
            now = local_now()
            today = now.date().isoformat()
            if now.hour >= 3 and get_app_state("daily_scores_last") != today:
                n = await _mr.recompute_all_scores()
                set_app_state("daily_scores_last", today)
                logger.info(f"[scores] пересчитано филиалов: {n}")
            # Снимок факта заказов — своим ключом состояния: упади он, оценки
            # всё равно должны посчитаться, и наоборот.
            if now.hour >= 3 and get_app_state("plan_days_refreshed_v2") != today:
                closed = await _close_plan_day()
                set_app_state("plan_days_refreshed_v2", today)
                logger.info(f"[plan] обновлены последние 7 дней по филиалам: {closed}")
            # вторник = weekday 1
            if now.weekday() == 1 and now.hour >= 10 and get_app_state("weekly_scores_last") != today:
                if await _send_weekly_scores():
                    set_app_state("weekly_scores_last", today)
                    logger.info("[scores] вторничная средняя отправлена")
        except Exception as e:
            logger.warning(f"[scores] цикл: {e!r}")
        await asyncio.sleep(1800)


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
    # Тот же расчёт часов, что и у ФОТ во вкладке «Планы»: разойдись эти два
    # места — на собрании сравнивали бы две разные зарплаты.
    worked = _merge_attendance_hours(attendance, info.get("org_name", ""))
    from database import get_salary_overrides
    period_key = f"{real_org}:{from_}:{to}"
    overrides = get_salary_overrides(period_key)
    rates = get_salary_rates(real_org)
    rows = []
    for eid, w in worked.items():
        h, days = w["hours"], int(w["days"])
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


# Зазор закрыт: salary_rates ключуется парой (organization_id, employee_id), а
# salary_overrides — через period_key, в котором филиал уже есть. Запись чужому
# employee_id теперь может испортить только СВОЙ филиал пишущего, а на чужой не
# влияет и в чужой ФОТ не попадает.
@app.post("/api/salary/rate")
async def salary_rate(payload: SalaryRateIn, user: Dict = Depends(get_current_user)):
    real_org = _get_org_id(payload.org_id, user)  # 404, если вызывающий не владелец
    if real_org == "demo" or _is_demo_user(user):
        raise HTTPException(status_code=403, detail="Salary write not allowed in demo")
    from database import set_salary_rate
    set_salary_rate(real_org, payload.employee_id, payload.rate)
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


async def _branch_night(r: Dict[str, Any], yesterday: str, week_ago: str) -> Optional[Dict[str, Any]]:
    """Ночные цифры одного филиала: смены за вчера и выручка неделю назад."""
    name = r.get("organization_name") or "Филиал"
    try:
        client = get_client(r["api_login"], r.get("iiko_server"), r.get("api_password"))
        if not hasattr(client, "get_cashshifts"):
            return None
        sessions = await _branch_sessions(client, name, yesterday, yesterday)
    except Exception as e:
        logger.warning(f"[daily_shifts] {name}: {e}")
        return None
    prev, got_prev = 0.0, False
    try:
        prev_sessions = await _branch_sessions(client, name, week_ago, week_ago)
        prev = sum(float(s.get("payOrders") or 0) for s in prev_sessions)
        got_prev = bool(prev_sessions)
    except Exception as e:
        logger.warning(f"[daily_shifts] прошлая неделя {name}: {e}")
    return {"org": r.get("organization_id"), "name": name[:28],
            "sessions": sessions, "prev": prev, "got_prev": got_prev,
            "rev": sum(float(s.get("payOrders") or 0) for s in sessions)}


async def _send_daily_shifts_report() -> bool:
    """Сводка по сменам за вчера — в конце дня, когда цифра уже окончательная.

    Каждый telegram_id получает только свои филиалы (см. _report_recipients):
    владельцу — сеть, управляющему — его точка. Раньше группировка шла по
    restaurants.telegram_id, то есть выручку в конце дня видел только владелец,
    а управляющий — нет, хотя именно он на неё и влияет.

    Филиалы считаем ОДИН раз на всех: точка, у которой есть и владелец, и
    управляющий, иначе дёргала бы кассовые смены дважды.

    Выручка сравнивается с тем же днём недели назад (сб к сб, а не к пт —
    иначе после выходных всегда ложное «падение»).
    """
    from database import get_all_restaurants
    rests = get_all_restaurants()
    if not rests:
        return False
    yd = (local_now() - timedelta(days=1)).date()
    yesterday, week_ago = yd.isoformat(), (yd - timedelta(days=7)).isoformat()

    sem = asyncio.Semaphore(4)

    async def one(rr):
        async with sem:
            return await _branch_night(rr, yesterday, week_ago)

    computed = await asyncio.gather(*(one(r) for r in rests), return_exceptions=True)
    by_org: Dict[str, Dict[str, Any]] = {x["org"]: x for x in computed
                                         if isinstance(x, dict) and x.get("org")}

    for tg_id, own_rests in _report_recipients(rests).items():
        rows = [by_org[o] for o in
                (r.get("organization_id") for r in own_rests) if o in by_org]
        lines = [f"🌙 <b>Кассовые смены за {yesterday}</b>"]
        problems = 0
        rev_cur = rev_prev = 0.0
        got_prev = False
        for x in rows:
            rev_cur += x["rev"]
            rev_prev += x["prev"]
            got_prev = got_prev or x["got_prev"]
            for s in x["sessions"]:
                diff = float(s.get("cashDiff") or 0)
                total = float(s.get("payOrders") or 0)
                if abs(diff) > 0.01:
                    problems += 1
                    lines.append(f"❌ {x['name']}: смена №{s.get('sessionNumber')} — "
                                 f"итого {format_money(total)}, расхождение {format_money(diff)}")
                else:
                    lines.append(f"✅ {x['name']}: №{s.get('sessionNumber')} — {format_money(total)}")
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


# 11:00 по Казахстану (local_now — UTC+5): к этому часу управляющий уже на точке
# и может отреагировать, в отличие от 9 утра, когда филиал ещё закрыт.
MORNING_HOUR_LOCAL = int(os.getenv("MORNING_HOUR_LOCAL", "11"))


async def _branch_morning(r: Dict[str, Any], yd: date) -> Optional[Dict[str, Any]]:
    """Утренние цифры одного филиала: выручка по дням месяца + стоп-лист.

    Выручку берём ОДНИМ OLAP на весь месяц с группировкой по дате — из него
    достаём и вчера, и позавчера, и тот же день неделю назад, и факт месяца
    для прогноза. Раньше это были бы четыре отдельных запроса на филиал.
    """
    name = r.get("organization_name") or "Филиал"
    client = get_client(r["api_login"], r.get("iiko_server"), r.get("api_password"))
    dept = name  # iikoWeb OLAP фильтрует Department по названию, не по UUID
    month_start = yd.replace(day=1)
    # неделю назад может быть в прошлом месяце — тянем с более раннего края
    since = min(month_start, yd - timedelta(days=7))
    try:
        rep = await client.olap_report(dept, since.isoformat(), yd.isoformat(),
                                       ["OpenDate.Typed"], ["DishAmountSum"])
    except Exception as e:
        logger.warning(f"[morning] выручка {name}: {e!r}")
        return None

    by_day: Dict[str, float] = {}
    for row in rep.get("data", []):
        d = str(row.get("OpenDate.Typed") or "")[:10]
        if d:
            by_day[d] = by_day.get(d, 0.0) + float(row.get("DishAmountSum") or 0)

    rev = by_day.get(yd.isoformat(), 0.0)
    prev_day = by_day.get((yd - timedelta(days=1)).isoformat(), 0.0)
    prev_week = by_day.get((yd - timedelta(days=7)).isoformat(), 0.0)
    mtd = sum(v for d, v in by_day.items() if d >= month_start.isoformat())
    # Типичный день филиала = медиана по месяцу. Нужна, чтобы отличить настоящий
    # сбой («0 ₸ там, где обычно 3 млн») от филиала, который столько и делает.
    vals = sorted(v for d, v in by_day.items() if v > 0 and d != yd.isoformat())
    typical = vals[len(vals) // 2] if vals else 0.0
    days_done = yd.day
    days_in_month = calendar.monthrange(yd.year, yd.month)[1]
    forecast = (mtd / days_done * days_in_month) if days_done else 0.0

    stops: List[str] = []
    lows: List[str] = []
    try:
        stop_data, nomen = await asyncio.gather(
            client.get_stop_lists([dept]), client.get_nomenclature(r["organization_id"]),
            return_exceptions=True)
        if not isinstance(stop_data, Exception) and not isinstance(nomen, Exception):
            id_to_name = {p["id"]: p.get("name", "") for p in nomen.get("products", [])}
            for loc in stop_data.get("productionLocations", []):
                for item in loc.get("stopList", {}).get("items", []):
                    nm = id_to_name.get(item.get("productId", ""), "")
                    if not nm:
                        continue
                    (stops if float(item.get("balance") or 0) <= 0 else lows).append(nm)
    except Exception as e:
        logger.warning(f"[morning] стоп-лист {name}: {e!r}")

    return {"name": name, "org": r.get("organization_id", ""),
            "rev": rev, "prev_day": prev_day, "prev_week": prev_week,
            "mtd": mtd, "forecast": forecast, "typical": typical,
            "stops": sorted(set(stops)), "lows": sorted(set(lows))}


def _branch_alerts(x: Dict[str, Any]) -> List[Tuple[int, str]]:
    """Что в филиале требует внимания. (вес, строка) — вес задаёт порядок.

    Только то, на что владелец реально может отреагировать сегодня. Рост рейтинга
    на 0.2 сюда не попадает: это приятно, но это не задача на утро.
    """
    out: List[Tuple[int, str]] = []
    rev, typ = x["rev"], x["typical"]
    if typ > 0 and rev <= 0:
        out.append((100, f"🔴 <b>{x['name'][:26]}</b> — 0 ₸ в кассе "
                         f"(обычно ≈ {format_money(typ)}). Похоже на сбой, не на спад."))
    elif typ > 0 and rev < typ * 0.5:
        out.append((80, f"🔴 <b>{x['name'][:26]}</b> — {format_money(rev)} "
                        f"при обычных {format_money(typ)} (−{round((1 - rev / typ) * 100)}%)"))
    elif x["prev_week"] > 0 and rev < x["prev_week"] * 0.75:
        out.append((50, f"🟡 <b>{x['name'][:26]}</b> — {format_money(rev)}, "
                        f"{_fmt_delta(rev, x['prev_week'])} к тому же дню недели"))
    if x["stops"]:
        out.append((60, f"🟡 <b>{x['name'][:26]}</b> — на стопе {len(x['stops'])}: "
                        f"{', '.join(x['stops'][:3])}"))
    return out


def _report_recipients(rests: List[Dict[str, Any]]) -> Dict[int, List[Dict[str, Any]]]:
    """Кому какие филиалы слать: {telegram_id: [филиалы]}.

    Владельцу — все его точки, управляющему — те, к которым он привязан. Раньше
    рассылка шла только по restaurants.telegram_id, поэтому управляющие видели
    свой филиал в мини-аппе, но ни утреннего дайджеста, ни триггеров не получали.
    Управляющий двух филиалов получает одно письмо на оба — как владелец на сеть.
    """
    from database import get_branch_managers
    out: Dict[int, List[Dict[str, Any]]] = {}
    for r in rests:
        out.setdefault(int(r["telegram_id"]), []).append(r)
    by_org = {r.get("organization_id"): r for r in rests}
    for m in get_branch_managers():
        r = by_org.get(m["organization_id"])
        if not r:
            continue
        mine = out.setdefault(int(m["telegram_id"]), [])
        # владелец, назначенный управляющим сам себе, не должен получить дубль
        if not any(x.get("organization_id") == r.get("organization_id") for x in mine):
            mine.append(r)
    return out


async def _send_morning_report() -> bool:
    """Утренняя сводка: вчера + сравнения + прогноз месяца + стоп-лист.

    Каждый telegram_id получает только свои филиалы (см. _report_recipients):
    у кого один — придёт про один, у кого сеть — про сеть. Отдельной ролевой
    модели для этого не нужно. Филиалы считаем ОДИН раз на всех: иначе точка,
    у которой есть и владелец, и управляющий, дёргала бы OLAP дважды.
    """
    from database import get_all_restaurants
    rests = get_all_restaurants()
    if not rests or not BOT_TOKEN:
        return False
    yd = (local_now() - timedelta(days=1)).date()

    sem = asyncio.Semaphore(4)

    async def one(rr):
        async with sem:
            return await _branch_morning(rr, yd)

    computed = await asyncio.gather(*(one(r) for r in rests), return_exceptions=True)
    by_org: Dict[str, Dict[str, Any]] = {x["org"]: x for x in computed
                                         if isinstance(x, dict) and x.get("org")}

    sent = 0
    for tg_id, own_rests in _report_recipients(rests).items():
        results = [by_org[o] for o in
                   (r.get("organization_id") for r in own_rests) if o in by_org]
        results = [x for x in results if x["rev"] or x["stops"] or x["lows"]]
        if not results:
            continue
        results.sort(key=lambda x: x["rev"], reverse=True)
        multi = len(results) > 1

        # ДАЙДЖЕСТ, а не выгрузка. Владельцу 13 филиалов список из 40 карточек
        # нечитаем: там рост рейтинга на 0.2 стоит рядом с потерей 3 млн ₸.
        # Даём итог + только то, что требует действия; остальное — в мини-аппе.
        L = [f"☀️ <b>Утро. Вчера, {yd.strftime('%d.%m')}</b>"]
        if multi:
            tot = sum(x["rev"] for x in results)
            tot_pd = sum(x["prev_day"] for x in results)
            tot_pw = sum(x["prev_week"] for x in results)
            tot_fc = sum(x["forecast"] for x in results)
            L.append(f"\n💰 По сети: <b>{format_money(tot)}</b>")
            L.append(f"   к позавчера {_fmt_delta_money(tot, tot_pd)}")
            L.append(f"   к {RU_DOW_PREV[yd.weekday()]} {_fmt_delta_money(tot, tot_pw)}")
            L.append(f"   📈 прогноз месяца ≈ {format_money(tot_fc)}")
        else:
            x = results[0]
            L.append(f"\n<b>{x['name']}</b>")
            L.append(f"💰 {format_money(x['rev'])}")
            L.append(f"   к позавчера {_fmt_delta_money(x['rev'], x['prev_day'])}")
            L.append(f"   к {RU_DOW_PREV[yd.weekday()]} {_fmt_delta_money(x['rev'], x['prev_week'])}")
            L.append(f"   📈 прогноз месяца ≈ {format_money(x['forecast'])}")

        alerts = sorted((a for x in results for a in _branch_alerts(x)),
                        key=lambda t: -t[0])
        if alerts:
            L.append("\n<b>Требует внимания:</b>")
            L += [text for _, text in alerts[:5]]
            if len(alerts) > 5:
                L.append(f"…и ещё {len(alerts) - 5} — смотри в мини-аппе")
        elif multi:
            L.append("\nПо филиалам всё ровно — резких провалов нет.")

        if multi:
            calm = len(results) - len({t.split("<b>")[1].split("</b>")[0]
                                       for _, t in alerts if "<b>" in t})
            if calm > 0 and alerts:
                L.append(f"\nОстальные {calm} филиалов без отклонений.")

        # Управляющему — напоминание, что бот принимает накладные. Владельцу не шлём:
        # он это и так знает, а лишняя строка в сводке по сети только мешает.
        if not any(int(r["telegram_id"]) == tg_id for r in rests):
            L.append("\n📸 Накладные можно пробивать прямо здесь — "
                     "пришлите фото накладной в этот чат.")

        try:
            async with httpx.AsyncClient(timeout=15.0) as c:
                await c.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                             json={"chat_id": tg_id, "text": "\n".join(L[:80]),
                                   "parse_mode": "HTML"})
            sent += 1
        except Exception as e:
            logger.warning(f"[morning] send {tg_id}: {e!r}")
    logger.info(f"[morning] отправлено {sent} получателям")
    return True


# ── Триггерные уведомления: то, что нельзя отложить до утра ──────────────────
# Проверяем в течение дня и сравниваем «сколько филиал обычно делает К ЭТОМУ ЧАСУ
# в этот день недели» с тем, что есть сейчас. Без поправки на час и день недели
# сравнение бессмысленно: в 13:00 вторника норма совсем не та, что в 21:00 субботы.
TRIGGER_FROM_HOUR = int(os.getenv("TRIGGER_FROM_HOUR", "13"))   # раньше днём судить рано
TRIGGER_TO_HOUR = int(os.getenv("TRIGGER_TO_HOUR", "22"))
TRIGGER_MIN_TYPICAL = 50_000  # филиалы с копеечной нормой не дёргаем


async def _branch_intraday(r: Dict[str, Any], now: datetime) -> Optional[Dict[str, Any]]:
    """Выручка филиала на текущий час против нормы этого дня недели.

    Один OLAP на филиал: выручка по дате × часу за 4 недели. Из него считаем,
    сколько обычно накапливается к этому часу в этот день недели (медиана).
    """
    name = r.get("organization_name") or "Филиал"
    today = now.date()
    since = today - timedelta(days=28)
    client = get_client(r["api_login"], r.get("iiko_server"), r.get("api_password"))
    try:
        rep = await client.olap_report(name, since.isoformat(), today.isoformat(),
                                       ["OpenDate.Typed", "HourOpen"], ["DishAmountSum"])
    except Exception as e:
        logger.warning(f"[trigger] {name}: {e!r}")
        return None

    by_day_hour: Dict[str, Dict[int, float]] = {}
    for row in rep.get("data", []):
        d = str(row.get("OpenDate.Typed") or "")[:10]
        try:
            h = int(str(row.get("HourOpen", "")).split(":")[0])
        except Exception:
            continue
        if d:
            by_day_hour.setdefault(d, {})[h] = by_day_hour.get(d, {}).get(h, 0.0) \
                + float(row.get("DishAmountSum") or 0)

    hour = now.hour
    cum = lambda hrs: sum(v for h, v in hrs.items() if h <= hour)  # noqa: E731
    today_rev = cum(by_day_hour.get(today.isoformat(), {}))
    # норма — медиана по тем же дням недели за прошлые 4 недели, к этому же часу
    peers = [cum(by_day_hour.get((today - timedelta(days=7 * k)).isoformat(), {}))
             for k in range(1, 5)]
    peers = sorted(v for v in peers if v > 0)
    typical = peers[len(peers) // 2] if peers else 0.0
    return {"name": name, "org": r["organization_id"], "now": today_rev, "typical": typical}


async def _send_trigger_alerts() -> int:
    """Проверяет филиалы в течение дня и шлёт только срочное. Возвращает число писем."""
    from database import get_all_restaurants
    rests = get_all_restaurants()
    if not rests or not BOT_TOKEN:
        return 0
    now = local_now()
    today = now.date().isoformat()

    sem = asyncio.Semaphore(4)

    async def one(rr):
        async with sem:
            return await _branch_intraday(rr, now)

    computed = await asyncio.gather(*(one(r) for r in rests), return_exceptions=True)
    by_org: Dict[str, Dict[str, Any]] = {x["org"]: x for x in computed
                                         if isinstance(x, dict) and x.get("org")}

    sent = 0
    for tg_id, own_rests in _report_recipients(rests).items():
        rows = [by_org[o] for o in
                (r.get("organization_id") for r in own_rests) if o in by_org]
        lines = []
        for x in rows:
            if x["typical"] < TRIGGER_MIN_TYPICAL:
                continue
            if x["now"] <= 0:
                kind, text = "silent", (
                    f"🔴 <b>{x['name'][:28]}</b> — касса молчит. К {now:%H:%M} обычно "
                    f"уже {format_money(x['typical'])}, сейчас 0 ₸.\n"
                    f"Проверь, открылся ли филиал и синхронизируется ли касса.")
            elif x["now"] < x["typical"] * 0.4:
                kind, text = "slump", (
                    f"🟡 <b>{x['name'][:28]}</b> — к {now:%H:%M} {format_money(x['now'])} "
                    f"при обычных {format_money(x['typical'])} "
                    f"(−{round((1 - x['now'] / x['typical']) * 100)}%).")
            else:
                continue
            # один и тот же сигнал по филиалу — один раз за день, иначе это спам
            key = f"trigger:{tg_id}:{x['org']}:{kind}:{today}"
            if get_app_state(key):
                continue
            set_app_state(key, "1")
            lines.append(text)

        if not lines:
            continue
        msg = "⚡️ <b>Требует внимания сейчас</b>\n\n" + "\n\n".join(lines[:6])
        try:
            async with httpx.AsyncClient(timeout=15.0) as c:
                await c.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                             json={"chat_id": tg_id, "text": msg, "parse_mode": "HTML"})
            sent += 1
        except Exception as e:
            logger.warning(f"[trigger] send {tg_id}: {e!r}")
    return sent


async def _trigger_scheduler():
    """Днём каждые 2 часа проверяет филиалы на срочные отклонения."""
    while True:
        try:
            now = local_now()
            if TRIGGER_FROM_HOUR <= now.hour <= TRIGGER_TO_HOUR:
                n = await _send_trigger_alerts()
                if n:
                    logger.info(f"[trigger] отправлено {n} получателям")
        except Exception as e:
            logger.warning(f"[trigger] цикл: {e!r}")
        await asyncio.sleep(7200)


# Имя бота для deep-link. Задаётся переменной, чтобы демо- и боевой бот
# не разъезжались: у них разные username при одном коде.
BOT_USERNAME = os.getenv("BOT_USERNAME", "iikoanalyzebot")
# Сколько раз можно активировать ссылку-приглашение: владелец проверяет сам +
# подключается управляющий. Больше — ссылка мертва (её легко переслать).
INVITE_MAX_USES = int(os.getenv("INVITE_MAX_USES", "2"))


def _invite_sign(org_id: str) -> str:
    """Подпись филиала для ссылки-приглашения. Тот же ключ, что у виджета."""
    secret = (os.getenv("WIDGET_SECRET") or os.getenv("ENCRYPTION_KEY")
              or "iiko-widget-fallback").encode()
    mac = hmac.new(secret, f"invite:{org_id}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")[:16]


def _invite_sign_many(org_ids: List[str]) -> str:
    """Подпись ссылки на несколько филиалов: подписываем набор целиком, иначе из
    двух честных ссылок можно было бы склеить третью."""
    secret = (os.getenv("WIDGET_SECRET") or os.getenv("ENCRYPTION_KEY")
              or "iiko-widget-fallback").encode()
    mac = hmac.new(secret, ("invite:" + ",".join(org_ids)).encode(),
                   hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")[:16]


def invite_link_for(org_id: str) -> str:
    """t.me/<бот>?start=inv_<8 симв. org>_<подпись> — короче лимита deep-link."""
    return (f"https://t.me/{BOT_USERNAME}?start="
            f"inv_{org_id.replace('-', '')[:12]}_{_invite_sign(org_id)}")


def invite_link_for_many(org_ids: List[str]) -> str:
    """Одна ссылка на несколько филиалов: inv<N>_<short1>…_<подпись>.

    Число филиалов вынесено В ПРЕФИКС, а не отделено ещё одним подчёркиванием:
    в подписи «_» встречается (base64url), поэтому отделить её от хвоста можно,
    только зная заранее, сколько частей идёт до неё. Два филиала — 47 символов
    из 64 допустимых в deep-link, три — 60.
    """
    if len(org_ids) == 1:
        return invite_link_for(org_ids[0])
    shorts = "_".join(o.replace("-", "")[:12] for o in org_ids)
    return (f"https://t.me/{BOT_USERNAME}?start="
            f"inv{len(org_ids)}_{shorts}_{_invite_sign_many(org_ids)}")


def _orgs_from_invite(payload: str) -> List[str]:
    """Филиалы из ссылки с проверкой подписи. Пустой список — подделка.

    Понимает оба формата: старый inv_<short>_<подпись> на один филиал и
    inv<N>_<short>…_<подпись> на несколько. Старые ссылки работают как работали.
    """
    m = re.match(r"^inv(\d*)_", payload)
    if not m:
        return []
    n = int(m.group(1) or 1)
    parts = payload.split("_", n + 1)          # ["invN", short1, …, подпись]
    if len(parts) != n + 2:
        return []
    shorts, sig = parts[1:-1], parts[-1]
    by_short = {}
    for r in get_all_restaurants():
        oid = r.get("organization_id") or ""
        if oid:
            by_short[oid.replace("-", "")[:12]] = oid
    orgs = [by_short[s] for s in shorts if s in by_short]
    if len(orgs) != n:
        return []
    expect = _invite_sign(orgs[0]) if n == 1 else _invite_sign_many(orgs)
    return orgs if hmac.compare_digest(expect, sig) else []


async def _notify_new_tasks(org_id: str, fresh: List[tuple]) -> int:
    """Шлёт управляющим филиала задачи, появившиеся только что. Best-effort.

    Только управляющим: владелец эти задачи и так видит на экране, с которого
    они и посчитались, — дубль в чат ему не нужен. Падение телеграма не роняет
    ручку: план уже посчитан, и отдать его важнее, чем доставить уведомление.
    """
    from database import get_branch_managers, branch_name
    if not BOT_TOKEN:
        return 0
    branch = branch_name(org_id) or "Филиал"
    sent = 0
    for m in get_branch_managers(org_id):
        tg = int(m.get("telegram_id") or 0)
        if not tg:
            continue
        for metric, value, target, money in fresh:
            try:
                await _send_task(tg, org_id, branch, f"Новая задача · {metric}",
                                 f"Сейчас {value:g} при цели {target:g}. "
                                 f"Отметь кнопкой, когда разберёшься.", money)
                sent += 1
            except Exception as e:
                logger.warning(f"[director] задача {metric} → {tg}: {e!r}")
    return sent


async def _send_task(tg_id: int, org_id: str, branch: str, title: str,
                     detail: str = "", money: float = 0) -> Optional[int]:
    """Шлёт задачу с кнопками «Сделал» / «Не успел» и запоминает её.

    Уведомление без обратной связи — шум: непонятно, взялся человек за проблему
    или она висит вторую неделю. Кнопка превращает сообщение в трекер.
    """
    if not BOT_TOKEN:
        return None
    task_id = create_bot_task(tg_id, org_id, branch, title, detail, money)
    money_line = f"\n💸 На кону: <b>{format_money(money)}</b>" if money else ""
    text = (f"📌 <b>{title}</b>\n"
            f"<i>{branch}</i>\n\n{detail}{money_line}")
    kb = {"inline_keyboard": [[
        {"text": "✅ Сделал", "callback_data": f"task:done:{task_id}"},
        {"text": "⏳ Не успел", "callback_data": f"task:later:{task_id}"},
    ]]}
    try:
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                             json={"chat_id": tg_id, "text": text,
                                   "parse_mode": "HTML", "reply_markup": kb})
        if not r.json().get("ok"):
            logger.warning(f"[task] send {tg_id}: {r.text[:200]}")
            return None
    except Exception as e:
        logger.warning(f"[task] send {tg_id}: {e!r}")
        return None
    return task_id


# ── Журналы: чистота фото, температура числом ───────────────────────────────
# Главная развилка — как отличить фото чек-листа от фото накладной. Не гадаем:
# сдача начинается с кнопки, и до «Готово» чат в режиме журнала. Всё остальное
# время фото по-прежнему уходит в распознавание накладной, как и раньше.
_JOURNAL_MODE: Dict[int, Dict[str, Any]] = {}
JOURNAL_MODE_MIN = int(os.getenv("JOURNAL_MODE_MIN", "15"))
# Насколько слот может опоздать и всё ещё быть разосланным
JOURNAL_LATE_MIN = int(os.getenv("JOURNAL_LATE_MIN", "90"))


def _journal_orgs_for(telegram_id: int) -> List[Dict[str, Any]]:
    """Филиалы, за которые человек сдаёт журналы (только города из in_journals)."""
    from journal_routes import in_journals
    return [r for r in get_restaurants(int(telegram_id))
            if in_journals(r.get("organization_name"))]


def _jshort(org_id: str) -> str:
    return (org_id or "").replace("-", "")[:12]


def _org_by_short(orgs: List[Dict[str, Any]], short: str) -> Optional[Dict[str, Any]]:
    return next((r for r in orgs if _jshort(r["organization_id"]) == short), None)


def _journal_mode(chat_id: int) -> Optional[Dict[str, Any]]:
    """Активный режим сдачи, если он не протух. Иначе чат снова про накладные."""
    st = _JOURNAL_MODE.get(chat_id)
    if not st:
        return None
    if local_now().timestamp() > st["until"]:
        _JOURNAL_MODE.pop(chat_id, None)
        return None
    return st


def journal_slot_message(slot: str, missing: List[Tuple[str, str, str]]) -> str:
    """Задание на слот: что именно сдать. Критерии приходят вместе с заданием —
    человек не должен помнить наизусть десять пунктов по залу."""
    import journals as J
    zones = [m[1] for m in missing if m[0] == "clean"]
    kinds = {m[0] for m in missing}
    L = [f"🗒 <b>Слот {slot}</b> — {J.SLOT_LABEL.get(slot, '')}", ""]
    if zones:
        L.append("<b>Чистота.</b> По каждой зоне — кнопка ниже, затем фото:")
        for z in zones:
            zz = J.ZONES[z]
            L.append(f"{zz['emoji']} <b>{zz['title']}</b> — " + ", ".join(zz["shots"]))
        L.append("")
        L.append(f"<i>{J.CLEAN_PRINCIPLES}</i>")
    if "temp" in kinds:
        if zones:
            L.append("")
        L.append(f"🌡 <b>Температура</b> — {len(J.CAMERAS)} камер, ответ одним сообщением.")
    if "stock" in kinds:
        L.append("")
        L.append("📦 <b>Остатки</b> — заполняются на сайте, ссылка у владельца.")
    return "\n".join(L)


def _slot_code(slot: str) -> str:
    """«10:45» → «1045». В callback_data двоеточие — разделитель полей, и слот
    со своим двоеточием разваливал разбор: слот превращался в «45», а сдача не
    сходилась ни с одним заданием."""
    return slot.replace(":", "")


def _slot_from_code(code: str) -> Optional[str]:
    import journals as J
    return next((s for s in set(J.CLEAN_SLOTS + J.TEMP_SLOTS)
                 if _slot_code(s) == code), None)


def _journal_kb(slot: str, missing: List[Tuple[str, str, str]], org_id: str) -> dict:
    """Кнопки задания. Филиал зашит в саму кнопку — управляющий двух адресов
    получает два отдельных задания и ничего не выбирает руками."""
    import journals as J
    code, short = _slot_code(slot), _jshort(org_id)
    rows = []
    for z in [m[1] for m in missing if m[0] == "clean"]:
        rows.append([{"text": f"{J.ZONES[z]['emoji']} {J.ZONES[z]['title']}",
                      "callback_data": f"jr:c:{z}:{code}:{short}"}])
    if any(m[0] == "temp" for m in missing):
        rows.append([{"text": "🌡 Температура", "callback_data": f"jr:t:{code}:{short}"}])
    return {"inline_keyboard": rows} if rows else None


async def _journal_start(chat_id: int, telegram_id: int, org: Dict[str, Any],
                         kind: str, zone: str, slot: str):
    import journals as J
    _JOURNAL_MODE[chat_id] = {
        "kind": kind, "zone": zone, "slot": slot, "org": org["organization_id"],
        "org_name": org.get("organization_name") or "Филиал", "photos": [],
        "until": local_now().timestamp() + JOURNAL_MODE_MIN * 60,
    }
    if kind == "temp":
        await _tg_send(chat_id, J.temp_task_text(slot))
    else:
        await _tg_send(chat_id, J.clean_task_text(zone, slot) +
                       f"\n\n<i>{org.get('organization_name')}</i>",
                       {"inline_keyboard": [[{"text": "✅ Готово", "callback_data": "jr:done"},
                                             {"text": "✖️ Отмена", "callback_data": "jr:cancel"}]]})


async def _journal_take_photo(chat_id: int, telegram_id: int, st: Dict[str, Any],
                              file_id: str, image_bytes: bytes) -> None:
    """Кладём кадр в текущую сдачу. Дубликат ловим хэшем — бесплатно и без ИИ."""
    import journals as J
    sha = hashlib.sha256(image_bytes).hexdigest()
    day = local_now().date().isoformat()
    seen = seen_photo(sha, st["org"])
    if seen and seen.get("day") != day:
        await _tg_send(chat_id, f"♻️ Это фото уже присылали {seen['day']}. "
                                f"Нужен свежий кадр — сделайте новый снимок.")
        return
    if len(st["photos"]) >= J.MAX_PHOTOS:
        await _tg_send(chat_id, f"Достаточно, уже {J.MAX_PHOTOS} кадров. Жмите «Готово».")
        return
    remember_photo(sha, st["org"], st["zone"], day, file_id)
    st["photos"].append({"file_id": file_id, "sha": sha})
    st["until"] = local_now().timestamp() + JOURNAL_MODE_MIN * 60
    need = max(0, J.MIN_PHOTOS - len(st["photos"]))
    tail = f"ещё минимум {need}" if need else "можно жать «Готово»"
    await _tg_send(chat_id, f"📸 Принято {len(st['photos'])} — {tail}.")


async def _journal_finish_clean(chat_id: int, telegram_id: int, st: Dict[str, Any]) -> bool:
    import journals as J
    if len(st["photos"]) < J.MIN_PHOTOS:
        await _tg_send(chat_id, f"Пока {len(st['photos'])} из {J.MIN_PHOTOS} кадров — "
                                f"пришлите ещё.")
        return False
    day = local_now().date().isoformat()
    save_journal_entry(st["org"], "clean", st["zone"], st["slot"], day, telegram_id,
                       json.dumps({"photos": st["photos"]}, ensure_ascii=False))
    _JOURNAL_MODE.pop(chat_id, None)
    z = J.ZONES[st["zone"]]
    await _tg_send(chat_id, f"✅ {z['emoji']} <b>{z['title']}</b> · слот {st['slot']} принят "
                            f"({len(st['photos'])} фото).\n<i>{st['org_name']}</i>")
    return True


async def _journal_take_temp(chat_id: int, telegram_id: int, st: Dict[str, Any],
                             text: str) -> bool:
    import journals as J
    vals, err = J.parse_temps(text)
    if err:
        await _tg_send(chat_id, f"⚠️ {err}")
        return False
    day = local_now().date().isoformat()
    save_journal_entry(st["org"], "temp", "", st["slot"], day, telegram_id,
                       json.dumps(vals, ensure_ascii=False))
    _JOURNAL_MODE.pop(chat_id, None)
    alarms = J.temp_alarms(vals)
    if alarms:
        lines = [f"• {a['title']}: {a['value']:+g} °C при норме {a['norm']}" for a in alarms]
        await _tg_send(chat_id, "🔴 <b>Замер принят, но есть отклонения:</b>\n" + "\n".join(lines)
                       + "\n\nПроверьте камеру и сообщите управляющему.")
        # владельцу — сразу, это дороже любой пыли на полу
        rest = next((r for r in get_all_restaurants()
                     if r["organization_id"] == st["org"]), None)
        if rest:
            try:
                await _tg_send(int(rest["telegram_id"]),
                               f"🔴 <b>{rest.get('organization_name')}</b> · замер {st['slot']}\n"
                               + "\n".join(lines))
            except Exception as e:
                logger.warning(f"[journal] alarm to owner: {e!r}")
    else:
        await _tg_send(chat_id, f"✅ Замер {st['slot']} принят — все камеры в норме.\n"
                                f"<i>{st['org_name']}</i>")
    return True


async def _journal_send_slot(slot: str) -> int:
    """Рассылка задания по слоту тем, кто ещё не сдал."""
    import journals as J
    from journal_routes import in_journals
    if not BOT_TOKEN:
        return 0
    now = local_now()
    day = now.date().isoformat()
    due = [d for d in J.due_items(now) if d[2] == slot]
    if not due:
        return 0
    entries = get_journal_entries(day)
    rests = {r["organization_id"]: r for r in get_all_restaurants()
             if in_journals(r.get("organization_name"))}
    sent = 0
    for m in get_branch_managers():
        org = m["organization_id"]
        if org not in rests:
            continue
        have = {(e["kind"], e["zone"], e["slot"]) for e in entries
                if e["organization_id"] == org and e["status"] == "ok"}
        missing = [d for d in due if d not in have]
        if not missing:
            continue
        try:
            await _tg_send(int(m["telegram_id"]),
                           journal_slot_message(slot, missing) +
                           f"\n\n<i>{rests[org].get('organization_name')}</i>",
                           _journal_kb(slot, missing, org))
            sent += 1
        except Exception as e:
            logger.warning(f"[journal] slot {slot} → {m['telegram_id']}: {e!r}")
    return sent


async def _paper_journals_remind() -> int:
    """Напоминание про бумажные журналы — тем филиалам, где сегодня не отмечено.

    Шлём один раз в день в PAPER_SLOT и только по недостающим: филиалу, который
    уже всё заполнил, письмо «заполни журналы» объясняет лишь то, что система
    его не видит.
    """
    import journals as J
    from database import get_paper_journals
    from journal_routes import in_journals
    if not BOT_TOKEN:
        return 0
    day = local_now().date().isoformat()
    rests = {r["organization_id"]: r for r in get_all_restaurants()
             if in_journals(r.get("organization_name"))}
    marks = {(r["organization_id"], r["journal"])
             for r in get_paper_journals(list(rests), day, day)}
    sent = 0
    for m in get_branch_managers():
        org = m["organization_id"]
        if org not in rests:
            continue
        missing = [k for k in J.PAPER_KEYS if (org, k) not in marks]
        if not missing:
            continue
        try:
            await _tg_send(int(m["telegram_id"]),
                           J.paper_task_text(missing)
                           + f"\n\n<i>{rests[org].get('organization_name')}</i>")
            sent += 1
        except Exception as e:
            logger.warning(f"[journal] бумага → {m['telegram_id']}: {e!r}")
    return sent


async def _journal_scheduler():
    """Напоминания по слотам. Каждый слот шлём один раз в день — метка в app_state."""
    import journals as J
    while True:
        try:
            now = local_now()
            today = now.date().isoformat()
            # Бумажные журналы: один раз утром, тем же правилом «не рассылать
            # давно просроченное», что и слоты, — иначе рестарт днём поднимет
            # утреннее напоминание, когда журналы уже подписаны.
            paper_key = f"journal_paper_sent:{today}"
            if J.slot_is_due(J.PAPER_SLOT, now) and not get_app_state(paper_key):
                late = now.hour * 60 + now.minute - J._hm(J.PAPER_SLOT)
                if late > JOURNAL_LATE_MIN:
                    set_app_state(paper_key, "skipped")
                else:
                    n = await _paper_journals_remind()
                    set_app_state(paper_key, str(n))
                    if n:
                        logger.info(f"[journal] бумажные журналы: напомнили {n} филиалам")
            for slot in sorted(set(J.CLEAN_SLOTS + J.TEMP_SLOTS), key=lambda s: J._hm(s)):
                if not J.slot_is_due(slot, now):
                    continue
                key = f"journal_slot_sent:{slot}:{today}"
                if get_app_state(key):
                    continue
                # Давно просроченный слот не рассылаем: после перезапуска сервиса
                # днём человек иначе получает пачку утренних заданий разом.
                late = now.hour * 60 + now.minute - J._hm(slot)
                if late > JOURNAL_LATE_MIN:
                    set_app_state(key, "skipped")
                    logger.info(f"[journal] слот {slot} просрочен на {late} мин — пропущен")
                    continue
                n = await _journal_send_slot(slot)
                set_app_state(key, str(n))
                if n:
                    logger.info(f"[journal] слот {slot}: задание ушло {n} управляющим")
        except Exception as e:
            logger.warning(f"[journal] цикл: {e!r}")
        await asyncio.sleep(300)


# Серверы филиалов. Список выдан владельцем; какой из них какой — определяется
# запросом, а не догадкой: на каждом сервере смотрим, у какого подразделения
# были продажи. Справочник подразделений для этого не годится — корпоративная
# структура реплицирована на все серверы одинаково.
BRANCH_SERVER_HOSTS = [
    "ss-saryarka-new", "ss-turk-new", "ss-kostanai", "saya-cc", "saya-sushi-aktau",
    "ss-mira", "ss-e10", "saya-sushi-aktobe", "saya-chubar", "saya-sushi-buhar-zhyrau",
    "saya-sushi-alpamys", "saya-sushi-pavlodar", "saya-sushi-kaldayakova",
    "saya-sushi-boikehana-25a", "ss-atr", "ss-mom",
]
CENTRAL_HOST = "saya-co"


def branch_server_url(host: str) -> str:
    return f"https://{host}.iiko.it:443"


async def discover_branch_servers(login: str, password: str, day: str
                                  ) -> Dict[str, Any]:
    """Один OLAP на сервер: у кого там были продажи. Возвращает
    {dept_name: host} и список серверов без продаж/недоступных."""
    sem = asyncio.Semaphore(4)

    async def probe(host: str):
        client = get_client(login, branch_server_url(host), password)
        async with sem:
            try:
                rep = await client.olap_report(None, day, day,
                                               ["Department"], ["DishAmountSum"])
                rows = [(str(x.get("Department") or ""), float(x.get("DishAmountSum") or 0))
                        for x in rep.get("data", [])]
                return host, "ok", [(d, v) for d, v in rows if d and v > 0]
            except Exception as e:
                return host, str(e)[:80], []

    found: Dict[str, str] = {}
    idle: List[str] = []
    failed: Dict[str, str] = {}
    for host, status, rows in await asyncio.gather(
            *(probe(h) for h in BRANCH_SERVER_HOSTS)):
        if status != "ok":
            failed[host] = status
        elif not rows:
            idle.append(host)
        elif len(rows) > 1:
            # Несколько филиалов с продажами — это центральный, не филиальный.
            failed[host] = f"продажи у {len(rows)} филиалов — похоже на центральный"
        else:
            found[rows[0][0]] = host
    return {"found": found, "idle": idle, "failed": failed}


@app.get("/api/admin/branches")
async def admin_branches(user: Dict = Depends(get_current_user)):
    """Админка владельца: филиалы, их управляющие, ссылки-приглашения, задачи.

    Только владельцу: управляющий видит свой филиал в мини-аппе, но раздавать
    доступы и смотреть чужие отметки не должен.
    """
    tid = int(user.get("id", 0) or 0)
    owned = [r for r in get_all_restaurants() if r["telegram_id"] == tid]
    if not owned:
        raise HTTPException(status_code=403, detail="доступно только владельцу")

    tasks = get_bot_tasks_all(days=14)
    mgrs = get_branch_managers()
    out = []
    for r in owned:
        org, name = r["organization_id"], r.get("organization_name", "")
        t = [x for x in tasks if x.get("branch") == name]
        done = sum(1 for x in t if x.get("status") == "done")
        out.append({
            "org_id": org, "name": name,
            "invite": invite_link_for(org),
            "managers": [{"telegram_id": m["telegram_id"], "name": m.get("name"),
                          "added_at": m.get("added_at")}
                         for m in mgrs if m["organization_id"] == org],
            "tasks_total": len(t), "tasks_done": done,
            "tasks_open": sum(1 for x in t if x.get("status") == "open"),
            "money_open": round(sum(float(x.get("money") or 0)
                                    for x in t if x.get("status") != "done")),
        })
    out.sort(key=lambda x: (-x["tasks_open"], x["name"]))
    return {"branches": out,
            "total_tasks": sum(b["tasks_total"] for b in out),
            "total_done": sum(b["tasks_done"] for b in out)}


@app.get("/api/admin/test-notify/{token}")
async def admin_test_notify(token: str):
    """Разово шлёт тестовый отчёт обоим id из CHASPIK_TG_IDS.

    Нужен потому, что с рабочей машины api.telegram.org недоступен, а увидеть
    текст на живых данных надо. Доступ — по тому же подписанному токену, что и
    виджет (подпись на ENCRYPTION_KEY), и только для id из CHASPIK_TG_IDS.
    """
    uid = widget_routes._uid_from_token(token)
    if uid is None or uid not in CHASPIK_TG_IDS:
        raise HTTPException(status_code=403, detail="forbidden")
    if not BOT_TOKEN:
        raise HTTPException(status_code=503, detail="TELEGRAM_BOT_TOKEN не задан")

    from database import get_all_restaurants
    rests = [r for r in get_all_restaurants() if r["telegram_id"] == uid]
    if not rests:
        raise HTTPException(status_code=404, detail="у этого id нет филиалов")

    yd = (local_now() - timedelta(days=1)).date()
    sem = asyncio.Semaphore(4)

    async def one(rr):
        async with sem:
            return await _branch_morning(rr, yd)

    rows = [x for x in await asyncio.gather(*(one(r) for r in rests)) if isinstance(x, dict)]
    rows = [x for x in rows if x["rev"] or x["stops"] or x["lows"]]
    if not rows:
        raise HTTPException(status_code=404, detail="нет данных за вчера")
    rows.sort(key=lambda x: x["rev"], reverse=True)

    tot = sum(x["rev"] for x in rows)
    L = [f"🧪 <b>Тестовый отчёт</b> · вчера, {yd.strftime('%d.%m')}",
         f"\n💰 По сети: <b>{format_money(tot)}</b>",
         f"   к позавчера {_fmt_delta_money(tot, sum(x['prev_day'] for x in rows))}",
         f"   к {RU_DOW_PREV[yd.weekday()]} {_fmt_delta_money(tot, sum(x['prev_week'] for x in rows))}",
         f"   📈 прогноз месяца ≈ {format_money(sum(x['forecast'] for x in rows))}"]

    alerts = sorted((a for x in rows for a in _branch_alerts(x)), key=lambda t: -t[0])
    if alerts:
        L.append("\n<b>Требует внимания:</b>")
        L += [t for _, t in alerts[:5]]
    else:
        L.append("\nПо филиалам всё ровно — резких провалов нет.")

    # в тестовом отчёте даём и полный список: надо увидеть все цифры разом
    L.append(f"\n<b>Все филиалы ({len(rows)}):</b>")
    for x in rows:
        L.append(f"· {x['name'][:30]} — {format_money(x['rev'])} "
                 f"{_fmt_delta(x['rev'], x['prev_week'])}")

    text, out = "\n".join(L), {}
    async with httpx.AsyncClient(timeout=25.0) as c:
        for target in sorted(CHASPIK_TG_IDS):
            try:
                r = await c.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                                 json={"chat_id": target, "text": text, "parse_mode": "HTML"})
                out[str(target)] = "ok" if r.json().get("ok") else r.text[:160]
            except Exception as e:
                out[str(target)] = repr(e)[:160]

    # Задача с кнопками — из самой крупной проблемы, найденной в данных
    worst = max(rows, key=lambda x: x["typical"] - x["rev"]) if rows else None
    tasks = {}
    if worst:
        gap = max(0.0, worst["typical"] - worst["rev"])
        for target in sorted(CHASPIK_TG_IDS):
            tid = await _send_task(
                target, worst.get("org", ""), worst["name"],
                "Разобрать провал по выручке",
                f"Вчера {format_money(worst['rev'])} при обычных "
                f"{format_money(worst['typical'])}. Проверь смену, кассу и стоп-лист "
                f"— и отметь кнопкой, когда разберёшься.", gap)
            tasks[str(target)] = tid
    return {"sent": out, "tasks": tasks, "branches": len(rows)}


async def _morning_report_scheduler():
    """Каждое утро в MORNING_HOUR_LOCAL — вчерашняя выручка, прогноз, стоп-лист."""
    while True:
        try:
            now = local_now()
            if now.hour >= MORNING_HOUR_LOCAL:
                today_str = now.date().isoformat()
                if get_app_state("morning_report_last_sent") != today_str:
                    if await _send_morning_report():
                        set_app_state("morning_report_last_sent", today_str)
        except Exception as e:
            logger.warning(f"[morning] цикл: {e!r}")
        await asyncio.sleep(600)


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


# ── Файлы сайта ──
# Vite кладёт содержимое public/ в КОРЕНЬ сборки, а не в /assets: mountain.jpg,
# truck.png, icons.svg, favicon.svg. Примонтировав только /assets, мы отдавали
# по ним 404 — «Гора» открывалась без самой горы, а поиск цены без фуры.
#
# Маршрут объявлен последним в файле, поэтому и регистрируется последним: все
# API-пути выше выигрывают у него по приоритету. Префиксы api/ и w/ отсекаем
# явно, чтобы опечатка в адресе API давала честный 404, а не страницу сайта.
@app.get("/{path:path}", include_in_schema=False)
async def site_files(path: str):
    if path.startswith(("api/", "w/", "m/")):
        raise HTTPException(status_code=404, detail="Not Found")
    target = os.path.abspath(os.path.join(SITE_DIR, path))
    # защита от «../»: наружу из web/dist не выпускаем
    if os.path.isfile(target) and os.path.commonpath([target, SITE_DIR]) == SITE_DIR:
        return FileResponse(target)
    # неизвестный путь — это маршрут одностраничного приложения, отдаём его точку входа
    index = os.path.join(SITE_DIR, "index.html")
    if os.path.exists(index):
        return FileResponse(index, headers={"Cache-Control": "no-store, must-revalidate"})
    raise HTTPException(status_code=404, detail="Not Found")
