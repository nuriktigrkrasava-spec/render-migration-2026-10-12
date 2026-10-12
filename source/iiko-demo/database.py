import sqlite3
import os
import json
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from cryptography.fernet import Fernet

# На Render persistent disk обычно монтируется в /data. Если он есть и DB_PATH
# не задан явно — храним БД там, чтобы ссылки/накладные/расходы переживали редеплой.
_DEFAULT_DB_DIR = "/data" if os.path.isdir("/data") else "."
DB_PATH = os.getenv("DB_PATH", os.path.join(_DEFAULT_DB_DIR, "iiko_boss.db"))

def _get_cipher() -> Optional[Fernet]:
    key = os.getenv("ENCRYPTION_KEY")
    if not key:
        return None
    return Fernet(key.encode())

def encrypt_password(password: str) -> str:
    cipher = _get_cipher()
    if not cipher or not password:
        return password
    return cipher.encrypt(password.encode()).decode()

def decrypt_password(encrypted: str) -> str:
    cipher = _get_cipher()
    if not cipher or not encrypted:
        return encrypted
    try:
        return cipher.decrypt(encrypted.encode()).decode()
    except Exception:
        return encrypted

def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10.0)
    conn.row_factory = sqlite3.Row
    # WAL: читатели не блокируют писателя и наоборот → лучше параллельность под нагрузкой.
    # busy_timeout: вместо мгновенной ошибки "database is locked" ждём до 5с.
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
    except Exception:
        pass
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        telegram_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        created_at TEXT,
        is_demo INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS restaurants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER,
        api_login TEXT,
        organization_id TEXT,
        organization_name TEXT,
        iiko_server TEXT DEFAULT NULL,
        api_password TEXT DEFAULT NULL,
        created_at TEXT
    );

    CREATE TABLE IF NOT EXISTS ai_cache (
        query_hash TEXT PRIMARY KEY,
        response TEXT,
        date TEXT,
        organization_id TEXT,
        created_at TEXT
    );

    CREATE TABLE IF NOT EXISTS bot_invoices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER,
        telegram_user TEXT,
        raw_text TEXT,
        supplier TEXT,
        branch TEXT,
        items TEXT,
        total_amount REAL,
        date TEXT,
        status TEXT DEFAULT 'pending',
        created_at TEXT
    );

    CREATE TABLE IF NOT EXISTS expenses_manual (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        organization_id TEXT,
        name TEXT,
        category TEXT,
        amount REAL,
        kassa TEXT,
        date TEXT,
        created_at TEXT
    );

    CREATE TABLE IF NOT EXISTS pending_invoices (
        pending_id TEXT PRIMARY KEY,
        chat_id INTEGER,
        telegram_id INTEGER,
        data TEXT,
        updated_at TEXT
    );

    CREATE TABLE IF NOT EXISTS plan_links (
        organization_id TEXT PRIMARY KEY,
        url TEXT,
        updated_at TEXT
    );

    CREATE TABLE IF NOT EXISTS client_links (
        organization_id TEXT PRIMARY KEY,
        url TEXT,
        updated_at TEXT
    );

    CREATE TABLE IF NOT EXISTS app_state (
        key TEXT PRIMARY KEY,
        value TEXT
    );

    -- Демо-бот: сколько накладных юзер уже распознал (лимит 1, дальше апселл)
    CREATE TABLE IF NOT EXISTS demo_invoice_usage (
        telegram_id INTEGER PRIMARY KEY,
        count INTEGER DEFAULT 0,
        updated_at TEXT
    );

    -- Демо-бот: 24ч триал с реальными данными (старт при подключении iiko)
    CREATE TABLE IF NOT EXISTS demo_trial (
        telegram_id INTEGER PRIMARY KEY,
        org_id      TEXT,
        started_ts  INTEGER
    );

    -- Атрибуция: первый контакт юзера с демо-ботом и источник (deep-link ?start=)
    CREATE TABLE IF NOT EXISTS user_acquisition (
        telegram_id   INTEGER PRIMARY KEY,
        username      TEXT,
        source        TEXT,
        first_seen_ts INTEGER
    );

    -- Лимит распознавания накладных (основной бот).
    -- extra — несгораемый докупленный баланс; used — расход месячного лимита.
    -- period_recognized — несрезаемый счётчик реального трафика за период (не клипается у base_limit).
    CREATE TABLE IF NOT EXISTS invoice_quota (
        telegram_id       INTEGER PRIMARY KEY,
        period_start_ts   INTEGER,
        used              INTEGER DEFAULT 0,
        base_limit        INTEGER DEFAULT 130,
        extra             INTEGER DEFAULT 0,
        plan_active       INTEGER DEFAULT 0,
        limit_notified_ts INTEGER DEFAULT 0,
        period_recognized INTEGER DEFAULT 0
    );
    """)
    conn.commit()
    # Миграция для существующих БД
    for col, definition in [("iiko_server", "TEXT DEFAULT NULL"), ("api_password", "TEXT DEFAULT NULL")]:
        try:
            conn.execute(f"ALTER TABLE restaurants ADD COLUMN {col} {definition}")
            conn.commit()
        except Exception:
            pass
    # Миграция bot_invoices: новые поля для интеграции с iiko
    for col, definition in [
        ("vat_amount", "REAL DEFAULT 0"),
        ("supplier_id", "TEXT"),
        ("organization_id", "TEXT"),
        ("iiko_doc_id", "TEXT"),
        ("iiko_doc_number", "TEXT"),
        ("dedup_hash", "TEXT"),
        ("photo_paths", "TEXT"),
        ("updated_at", "TEXT"),
        ("comment", "TEXT"),
    ]:
        try:
            conn.execute(f"ALTER TABLE bot_invoices ADD COLUMN {col} {definition}")
            conn.commit()
        except Exception:
            pass
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_bot_invoices_dedup ON bot_invoices(dedup_hash)")
        conn.commit()
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE demo_trial ADD COLUMN sales_sent INTEGER DEFAULT 0")
        conn.commit()
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE invoice_quota ADD COLUMN limit_notified_ts INTEGER DEFAULT 0")
        conn.commit()
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE invoice_quota ADD COLUMN period_recognized INTEGER DEFAULT 0")
        conn.commit()
    except Exception:
        pass
    conn.close()

def get_user(telegram_id: int) -> Optional[Dict[str, Any]]:
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None

def create_user(telegram_id: int, username: str = None, first_name: str = None):
    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO users (telegram_id, username, first_name, created_at) VALUES (?, ?, ?, ?)",
        (telegram_id, username, first_name, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()

def set_demo_mode(telegram_id: int, is_demo: int):
    conn = get_db()
    conn.execute(
        "UPDATE users SET is_demo = ? WHERE telegram_id = ?",
        (is_demo, telegram_id)
    )
    conn.commit()
    conn.close()

def get_demo_invoice_count(telegram_id: int) -> int:
    """Сколько накладных распознал юзер на демо-боте (для лимита 1)."""
    conn = get_db()
    row = conn.execute(
        "SELECT count FROM demo_invoice_usage WHERE telegram_id = ?", (telegram_id,)
    ).fetchone()
    conn.close()
    return int(row["count"]) if row else 0

def inc_demo_invoice_count(telegram_id: int) -> int:
    """Увеличивает счётчик распознанных накладных на демо-боте, возвращает новое значение."""
    conn = get_db()
    conn.execute(
        "INSERT INTO demo_invoice_usage (telegram_id, count, updated_at) VALUES (?, 1, ?) "
        "ON CONFLICT(telegram_id) DO UPDATE SET count = count + 1, updated_at = excluded.updated_at",
        (telegram_id, datetime.now().isoformat())
    )
    conn.commit()
    row = conn.execute(
        "SELECT count FROM demo_invoice_usage WHERE telegram_id = ?", (telegram_id,)
    ).fetchone()
    conn.close()
    return int(row["count"]) if row else 1


# === Атрибуция юзеров демо-бота ===

def record_acquisition(telegram_id: int, username: str, source: str) -> bool:
    """Фиксирует первый контакт юзера с демо-ботом. True, если строка создана (новый юзер)."""
    conn = get_db()
    cur = conn.execute(
        "INSERT OR IGNORE INTO user_acquisition (telegram_id, username, source, first_seen_ts) "
        "VALUES (?, ?, ?, ?)",
        (telegram_id, username, source, int(datetime.now().timestamp()))
    )
    conn.commit()
    created = cur.rowcount > 0
    conn.close()
    return created


def acquisition_stats() -> dict:
    conn = get_db()
    now = int(datetime.now().timestamp())
    total = conn.execute("SELECT COUNT(*) c FROM user_acquisition").fetchone()["c"]
    today = conn.execute("SELECT COUNT(*) c FROM user_acquisition WHERE first_seen_ts >= ?",
                         (now - 86400,)).fetchone()["c"]
    week = conn.execute("SELECT COUNT(*) c FROM user_acquisition WHERE first_seen_ts >= ?",
                        (now - 7 * 86400,)).fetchone()["c"]
    rows = conn.execute(
        "SELECT source, COUNT(*) c FROM user_acquisition GROUP BY source ORDER BY c DESC"
    ).fetchall()
    conn.close()
    return {"total": int(total), "today": int(today), "week": int(week),
            "by_source": [(r["source"] or "direct", int(r["c"])) for r in rows]}


def acquisitions_today() -> list:
    """Юзеры за последние 24ч: [{telegram_id, username, source, ts}], новые сверху."""
    conn = get_db()
    since = int(datetime.now().timestamp()) - 86400
    rows = conn.execute(
        "SELECT telegram_id, username, source, first_seen_ts FROM user_acquisition "
        "WHERE first_seen_ts >= ? ORDER BY first_seen_ts DESC", (since,)
    ).fetchall()
    conn.close()
    return [{"telegram_id": int(r["telegram_id"]), "username": r["username"] or "",
             "source": r["source"] or "direct", "ts": int(r["first_seen_ts"])} for r in rows]


# === Квота накладных (основной бот) ===

def get_quota(telegram_id: int) -> dict:
    """Возвращает квоту аккаунта, создавая строку с дефолтами при первом обращении."""
    conn = get_db()
    row = conn.execute("SELECT * FROM invoice_quota WHERE telegram_id = ?", (telegram_id,)).fetchone()
    if not row:
        conn.execute(
            "INSERT OR IGNORE INTO invoice_quota (telegram_id, period_start_ts) VALUES (?, ?)",
            (telegram_id, int(datetime.now().timestamp()))
        )
        conn.commit()
        row = conn.execute("SELECT * FROM invoice_quota WHERE telegram_id = ?", (telegram_id,)).fetchone()
    conn.close()
    return {"telegram_id": telegram_id, "period_start_ts": int(row["period_start_ts"]),
            "used": int(row["used"]), "base_limit": int(row["base_limit"]),
            "extra": int(row["extra"]), "plan_active": int(row["plan_active"]),
            "limit_notified_ts": int(row["limit_notified_ts"]),
            "period_recognized": int(row["period_recognized"])}


def reset_quota_if_due(telegram_id: int) -> None:
    """Ленивый месячный сброс: обнуляет used и period_recognized, сдвигает период. extra НЕ трогаем."""
    q = get_quota(telegram_id)
    now = int(datetime.now().timestamp())
    if now > q["period_start_ts"] + 30 * 86400:
        conn = get_db()
        conn.execute(
            "UPDATE invoice_quota SET used = 0, period_recognized = 0, period_start_ts = ?, limit_notified_ts = 0 "
            "WHERE telegram_id = ?",
            (now, telegram_id)
        )
        conn.commit()
        conn.close()


def quota_available(telegram_id: int) -> int:
    q = get_quota(telegram_id)
    return (q["base_limit"] - q["used"]) + q["extra"]


def consume_quota(telegram_id: int) -> None:
    """Списывает 1 накладную: сперва месячный лимит (used), по исчерпании — extra.
    period_recognized инкрементируется безусловно (несрезаемый счётчик реального трафика)."""
    get_quota(telegram_id)  # гарантирует наличие строки
    conn = get_db()
    cur = conn.execute(
        "UPDATE invoice_quota SET used = used + 1 "
        "WHERE telegram_id = ? AND used < base_limit",
        (telegram_id,)
    )
    if cur.rowcount == 0:
        conn.execute(
            "UPDATE invoice_quota SET extra = extra - 1 "
            "WHERE telegram_id = ? AND extra > 0",
            (telegram_id,)
        )
    # Безусловный счётчик — не клипается у base_limit, отражает реальный трафик.
    conn.execute(
        "UPDATE invoice_quota SET period_recognized = period_recognized + 1 WHERE telegram_id = ?",
        (telegram_id,)
    )
    conn.commit()
    conn.close()


def add_quota_extra(telegram_id: int, n: int) -> int:
    """Начисляет n накладных в несгораемый extra. Возвращает новый extra.
    Отрицательный n (коррекция) не может опустить баланс ниже нуля."""
    get_quota(telegram_id)
    conn = get_db()
    conn.execute("UPDATE invoice_quota SET extra = extra + ? WHERE telegram_id = ?", (n, telegram_id))
    # Защита от отрицательного баланса при коррекциях.
    conn.execute("UPDATE invoice_quota SET extra = 0 WHERE telegram_id = ? AND extra < 0", (telegram_id,))
    conn.commit()
    row = conn.execute("SELECT extra FROM invoice_quota WHERE telegram_id = ?", (telegram_id,)).fetchone()
    conn.close()
    return int(row["extra"])


def should_notify_limit(telegram_id: int) -> bool:
    """True, если владельцу не слали пуш о лимите последние 24ч; помечает время отправки."""
    get_quota(telegram_id)
    conn = get_db()
    row = conn.execute("SELECT limit_notified_ts FROM invoice_quota WHERE telegram_id = ?",
                       (telegram_id,)).fetchone()
    now = int(datetime.now().timestamp())
    if now - int(row["limit_notified_ts"]) < 86400:
        conn.close()
        return False
    conn.execute("UPDATE invoice_quota SET limit_notified_ts = ? WHERE telegram_id = ?", (now, telegram_id))
    conn.commit()
    conn.close()
    return True


def quota_stats() -> dict:
    conn = get_db()
    accounts = conn.execute("SELECT COUNT(*) c FROM invoice_quota").fetchone()["c"]
    # "used" здесь — реальный трафик за период (несрезаемый period_recognized), не клипированный used.
    used = conn.execute("SELECT COALESCE(SUM(period_recognized),0) s FROM invoice_quota").fetchone()["s"]
    extra = conn.execute("SELECT COALESCE(SUM(extra),0) s FROM invoice_quota").fetchone()["s"]
    at_limit = conn.execute(
        "SELECT COUNT(*) c FROM invoice_quota WHERE (base_limit - used) + extra <= 0"
    ).fetchone()["c"]
    conn.close()
    return {"accounts": int(accounts), "used": int(used), "extra": int(extra), "at_limit": int(at_limit)}


def start_demo_trial(telegram_id: int, org_id: str, started_ts: int) -> None:
    """Фиксирует старт триала. INSERT OR IGNORE — первое подключение побеждает,
    повторное НЕ перезапускает (нет вечного бесплатного доступа)."""
    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO demo_trial (telegram_id, org_id, started_ts) VALUES (?, ?, ?)",
        (telegram_id, org_id, started_ts)
    )
    conn.commit()
    conn.close()


def get_demo_trial(telegram_id: int):
    conn = get_db()
    row = conn.execute(
        "SELECT org_id, started_ts FROM demo_trial WHERE telegram_id = ?", (telegram_id,)
    ).fetchone()
    conn.close()
    return {"org_id": row["org_id"], "started_ts": int(row["started_ts"])} if row else None


def get_trials_due_for_sales(now_ts: int, trial_seconds: int):
    """Триалы, которым стукнуло trial_seconds и оффер ещё не слали."""
    conn = get_db()
    rows = conn.execute(
        "SELECT telegram_id FROM demo_trial "
        "WHERE started_ts + ? <= ? AND COALESCE(sales_sent, 0) = 0",
        (trial_seconds, now_ts)
    ).fetchall()
    conn.close()
    return [int(r["telegram_id"]) for r in rows]


def mark_trial_sales_sent(telegram_id: int) -> None:
    conn = get_db()
    conn.execute("UPDATE demo_trial SET sales_sent = 1 WHERE telegram_id = ?", (telegram_id,))
    conn.commit()
    conn.close()


def get_restaurants(telegram_id: int) -> List[Dict[str, Any]]:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM restaurants WHERE telegram_id = ? ORDER BY id", (telegram_id,)
    ).fetchall()
    conn.close()
    result = []
    seen_org_ids = set()
    for r in rows:
        d = dict(r)
        org_id = d.get("organization_id", "")
        if org_id in seen_org_ids:
            continue  # дедупликация по organization_id
        seen_org_ids.add(org_id)
        if d.get("api_password"):
            d["api_password"] = decrypt_password(d["api_password"])
        result.append(d)
    return result

def get_all_restaurants() -> List[Dict[str, Any]]:
    """Все филиалы сети (с расшифрованными паролями), дедуп по organization_id.
    Для воскресного отчёта по всем точкам. ВНИМАНИЕ: при настоящем мульти-тенанте
    (несколько независимых сетей) нужно будет скоупить по владельцу."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM restaurants WHERE iiko_server IS NOT NULL ORDER BY organization_name"
    ).fetchall()
    conn.close()
    result, seen = [], set()
    for r in rows:
        d = dict(r)
        oid = d.get("organization_id", "")
        if oid in seen:
            continue
        seen.add(oid)
        if d.get("api_password"):
            d["api_password"] = decrypt_password(d["api_password"])
        result.append(d)
    return result


def get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    """Поиск пользователя по @username (без учёта регистра). Нужен chat_id получателя."""
    uname = (username or "").lstrip("@").strip().lower()
    if not uname:
        return None
    conn = get_db()
    row = conn.execute(
        "SELECT telegram_id, username, first_name FROM users WHERE LOWER(username) = ?",
        (uname,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_app_state(key: str) -> Optional[str]:
    conn = get_db()
    row = conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else None


def set_app_state(key: str, value: str):
    conn = get_db()
    conn.execute(
        "INSERT INTO app_state (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value)
    )
    conn.commit()
    conn.close()


def add_restaurant(telegram_id: int, api_login: str, org_id: str, org_name: str,
                   iiko_server: str = None, api_password: str = None):
    conn = get_db()
    conn.execute(
        """INSERT INTO restaurants
           (telegram_id, api_login, organization_id, organization_name, iiko_server, api_password, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (telegram_id, api_login, org_id, org_name, iiko_server,
         encrypt_password(api_password) if api_password else None,
         datetime.now().isoformat())
    )
    conn.commit()
    conn.close()

def delete_restaurant(restaurant_id: int, telegram_id: int):
    conn = get_db()
    conn.execute(
        "DELETE FROM restaurants WHERE id = ? AND telegram_id = ?",
        (restaurant_id, telegram_id)
    )
    conn.commit()
    conn.close()

def delete_all_restaurants(telegram_id: int) -> int:
    """Удалить все рестораны пользователя. Возвращает кол-во удалённых строк."""
    conn = get_db()
    cur = conn.execute(
        "DELETE FROM restaurants WHERE telegram_id = ?",
        (telegram_id,)
    )
    conn.commit()
    n = cur.rowcount
    conn.close()
    return n


def wipe_demo_user(telegram_id: int) -> dict:
    """Полностью удалить юзера из всех таблиц — для чистого перетеста демо
    (триал/подключение/лимит/накладные начинаются с нуля). Важно чистить и
    bot_invoices, иначе повторно загруженная тестовая накладная посчитается
    дублем (find_invoice_by_dedup, окно 30 дней).
    Возвращает {таблица: кол-во удалённых строк}."""
    conn = get_db()
    result = {}
    # Орг-иды юзера собираем ДО удаления restaurants — по ним чистим expenses_manual.
    org_ids = [r["organization_id"] for r in conn.execute(
        "SELECT organization_id FROM restaurants WHERE telegram_id = ?", (telegram_id,)
    ).fetchall()]
    tg_tables = [
        "users", "restaurants", "demo_invoice_usage", "demo_trial",
        "user_acquisition", "invoice_quota", "bot_invoices", "pending_invoices",
    ]
    for t in tg_tables:
        cur = conn.execute(f"DELETE FROM {t} WHERE telegram_id = ?", (telegram_id,))
        result[t] = cur.rowcount
    if org_ids:
        # Тот же iiko-сервер может быть подключён у другого telegram-аккаунта
        # (владелец + управляющий) — его ручные расходы трогать нельзя. Чистим
        # только орги, на которые после удаления restaurants никто не ссылается.
        placeholders = ",".join("?" * len(org_ids))
        still_used = {r["organization_id"] for r in conn.execute(
            f"SELECT DISTINCT organization_id FROM restaurants "
            f"WHERE organization_id IN ({placeholders})", org_ids
        ).fetchall()}
        orphan = [o for o in org_ids if o not in still_used]
        if orphan:
            ph = ",".join("?" * len(orphan))
            cur = conn.execute(
                f"DELETE FROM expenses_manual WHERE organization_id IN ({ph})", orphan
            )
            result["expenses_manual"] = cur.rowcount
    conn.commit()
    conn.close()
    return result

def get_ai_cache(query_hash: str, org_id: str, date_str: str) -> Optional[str]:
    conn = get_db()
    row = conn.execute(
        "SELECT response FROM ai_cache WHERE query_hash = ? AND organization_id = ? AND date = ?",
        (query_hash, org_id, date_str)
    ).fetchone()
    conn.close()
    return row["response"] if row else None

def set_ai_cache(query_hash: str, response: str, org_id: str, date_str: str):
    conn = get_db()
    conn.execute(
        """INSERT OR REPLACE INTO ai_cache (query_hash, response, date, organization_id, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (query_hash, response, date_str, org_id, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()

def add_manual_expense(org_id: str, name: str, category: str, amount: float, kassa: str, date: str):
    conn = get_db()
    conn.execute(
        """INSERT INTO expenses_manual (organization_id, name, category, amount, kassa, date, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (org_id, name, category, amount, kassa, date, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()

def get_manual_expenses(org_id: str, date_from: str, date_to: str) -> List[Dict[str, Any]]:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM expenses_manual WHERE organization_id = ? AND date >= ? AND date <= ? ORDER BY date DESC",
        (org_id, date_from, date_to)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def delete_manual_expense(expense_id: int, org_id: str):
    conn = get_db()
    conn.execute(
        "DELETE FROM expenses_manual WHERE id = ? AND organization_id = ?",
        (expense_id, org_id)
    )
    conn.commit()
    conn.close()


def save_bot_invoice(telegram_id: int, telegram_user: str, raw_text: str,
                     supplier: str, branch: str, items_json: str,
                     total_amount: float, date: str,
                     *,
                     vat_amount: float = 0.0,
                     supplier_id: Optional[str] = None,
                     organization_id: Optional[str] = None,
                     iiko_doc_id: Optional[str] = None,
                     iiko_doc_number: Optional[str] = None,
                     dedup_hash: Optional[str] = None,
                     photo_paths: Optional[str] = None,
                     status: str = "pending") -> int:
    now = datetime.now().isoformat()
    conn = get_db()
    cur = conn.execute(
        """INSERT INTO bot_invoices
           (telegram_id, telegram_user, raw_text, supplier, branch, items, total_amount, date,
            vat_amount, supplier_id, organization_id, iiko_doc_id, iiko_doc_number,
            dedup_hash, photo_paths, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (telegram_id, telegram_user, raw_text, supplier, branch, items_json,
         total_amount, date, vat_amount, supplier_id, organization_id,
         iiko_doc_id, iiko_doc_number, dedup_hash, photo_paths, status, now, now)
    )
    conn.commit()
    inv_id = cur.lastrowid
    conn.close()
    return inv_id


def get_bot_invoices(limit: int = 50, organization_id: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = get_db()
    if organization_id:
        rows = conn.execute(
            "SELECT * FROM bot_invoices WHERE organization_id = ? ORDER BY created_at DESC LIMIT ?",
            (organization_id, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM bot_invoices ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_bot_invoice(invoice_id: int) -> Optional[Dict[str, Any]]:
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM bot_invoices WHERE id = ?", (invoice_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def update_bot_invoice(invoice_id: int, **fields) -> None:
    """Обновляет произвольные поля накладной. updated_at выставляется автоматически."""
    if not fields:
        return
    allowed = {
        "supplier", "branch", "items", "total_amount", "date",
        "vat_amount", "supplier_id", "organization_id",
        "iiko_doc_id", "iiko_doc_number", "dedup_hash", "photo_paths", "status",
        "comment",
    }
    safe = {k: v for k, v in fields.items() if k in allowed}
    if not safe:
        return
    safe["updated_at"] = datetime.now().isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in safe)
    params = list(safe.values()) + [invoice_id]
    conn = get_db()
    conn.execute(f"UPDATE bot_invoices SET {set_clause} WHERE id = ?", params)
    conn.commit()
    conn.close()


def find_invoice_by_dedup(dedup_hash: str, telegram_id: int,
                          days: int = 30) -> Optional[Dict[str, Any]]:
    """Ищет накладную с таким же хешем у того же пользователя за последние N дней."""
    if not dedup_hash:
        return None
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    conn = get_db()
    row = conn.execute(
        """SELECT * FROM bot_invoices
           WHERE dedup_hash = ? AND telegram_id = ? AND created_at >= ?
           ORDER BY created_at DESC LIMIT 1""",
        (dedup_hash, telegram_id, cutoff)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# ---- Pending invoices (живут в RAM бота, дублируются в БД чтобы пережить рестарт) ----

def save_pending_invoice(pending_id: str, chat_id: int, telegram_id: int,
                         data_json: str) -> None:
    """Сохраняет/обновляет pending-накладную (JSON без сырых байт фото)."""
    conn = get_db()
    conn.execute(
        """INSERT OR REPLACE INTO pending_invoices
           (pending_id, chat_id, telegram_id, data, updated_at)
           VALUES (?, ?, ?, ?, ?)""",
        (pending_id, chat_id, telegram_id, data_json, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


def get_pending_invoice(pending_id: str) -> Optional[Dict[str, Any]]:
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM pending_invoices WHERE pending_id = ?", (pending_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_pending_invoice_by_chat(chat_id: int) -> Optional[Dict[str, Any]]:
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM pending_invoices WHERE chat_id = ? ORDER BY updated_at DESC LIMIT 1",
        (chat_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def delete_pending_invoice_by_chat(chat_id: int) -> None:
    conn = get_db()
    conn.execute("DELETE FROM pending_invoices WHERE chat_id = ?", (chat_id,))
    conn.commit()
    conn.close()


def get_price_history(telegram_id: int, days: int = 90) -> List[Dict[str, Any]]:
    """Все позиции из сохранённых накладных пользователя за N дней.

    Возвращает плоский список: [{key, name, price_per_unit, supplier, date, unit}, ...]
    где key = iiko_product_id (если есть) или name.strip().lower().
    Используется для построения сравнения цен в боте (кнопка 📊 Сравнить цены).
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(
        """SELECT items, supplier, date, created_at FROM bot_invoices
           WHERE telegram_id = ?
             AND date(COALESCE(date, substr(created_at, 1, 10))) >= date('now', ?)
             AND items IS NOT NULL AND items != ''""",
        (telegram_id, f"-{int(days)} days")
    )
    out: List[Dict[str, Any]] = []
    for row in cursor.fetchall():
        try:
            items = json.loads(row["items"]) or []
        except Exception:
            continue
        for it in items:
            name = (it.get("name") or "").strip()
            if not name:
                continue
            price = it.get("price_per_unit")
            if price is None or float(price) <= 0:
                continue
            key = it.get("iiko_product_id") or name.lower()
            out.append({
                "key": str(key),
                "name": name,
                "price_per_unit": float(price),
                "supplier": row["supplier"] or "—",
                "date": row["date"] or (row["created_at"] or "")[:10],
                "unit": it.get("unit") or "",
            })
    conn.close()
    return out


# ── План продаж (ссылка на Google-таблицу с планом по филиалу) ──────────────
def get_plan_url(organization_id: str) -> Optional[str]:
    conn = get_db()
    row = conn.execute(
        "SELECT url FROM plan_links WHERE organization_id = ?", (organization_id,)
    ).fetchone()
    conn.close()
    return row["url"] if row else None

def set_plan_url(organization_id: str, url: str):
    conn = get_db()
    conn.execute(
        "INSERT INTO plan_links (organization_id, url, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(organization_id) DO UPDATE SET url = excluded.url, updated_at = excluded.updated_at",
        (organization_id, url, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


def get_client_url(organization_id: str) -> Optional[str]:
    conn = get_db()
    row = conn.execute(
        "SELECT url FROM client_links WHERE organization_id = ?", (organization_id,)
    ).fetchone()
    conn.close()
    return row["url"] if row else None

def set_client_url(organization_id: str, url: str):
    conn = get_db()
    conn.execute(
        "INSERT INTO client_links (organization_id, url, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(organization_id) DO UPDATE SET url = excluded.url, updated_at = excluded.updated_at",
        (organization_id, url, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


# ==== Отзывы с внешних площадок (2ГИС, Wolt) ====

def init_reviews_db():
    conn = get_db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS review_links (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        org_id TEXT NOT NULL,
        platform TEXT NOT NULL,
        external_id TEXT NOT NULL,
        title TEXT,
        extra TEXT,
        UNIQUE(org_id, platform)
    );
    CREATE TABLE IF NOT EXISTS rating_snapshots (
        org_id TEXT NOT NULL,
        platform TEXT NOT NULL,
        date TEXT NOT NULL,
        rating REAL,
        reviews_count INTEGER,
        PRIMARY KEY(org_id, platform, date)
    );
    """)
    conn.commit()
    conn.close()


def get_review_link(org_id: str, platform: str):
    conn = get_db()
    row = conn.execute("SELECT * FROM review_links WHERE org_id=? AND platform=?",
                       (org_id, platform)).fetchone()
    conn.close()
    return dict(row) if row else None


def set_review_link(org_id: str, platform: str, external_id: str,
                    title: str = "", extra: str = ""):
    conn = get_db()
    conn.execute("""INSERT OR REPLACE INTO review_links(org_id, platform, external_id, title, extra)
                    VALUES(?,?,?,?,?)""", (org_id, platform, external_id, title, extra))
    conn.commit()
    conn.close()


def save_rating_snapshot(org_id: str, platform: str, date: str,
                         rating, reviews_count):
    conn = get_db()
    conn.execute("""INSERT OR REPLACE INTO rating_snapshots(org_id, platform, date, rating, reviews_count)
                    VALUES(?,?,?,?,?)""", (org_id, platform, date, rating, reviews_count))
    conn.commit()
    conn.close()


def get_rating_history(org_id: str, platform: str, days: int = 30):
    conn = get_db()
    rows = conn.execute("""SELECT * FROM rating_snapshots
                           WHERE org_id=? AND platform=? AND date >= date('now', ?)
                           ORDER BY date""",
                        (org_id, platform, f"-{days} days")).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ==== Ставки для расчёта зарплаты (вкладка «Смены») ====

def init_salary_db():
    conn = get_db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS salary_rates (
        employee_id TEXT PRIMARY KEY,
        rate REAL NOT NULL DEFAULT 0
    );
    """)
    conn.commit()
    conn.close()


def get_salary_rates() -> dict:
    conn = get_db()
    rows = conn.execute("SELECT employee_id, rate FROM salary_rates").fetchall()
    conn.close()
    return {r["employee_id"]: r["rate"] for r in rows}


def set_salary_rate(employee_id: str, rate: float):
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO salary_rates(employee_id, rate) VALUES(?,?)",
                 (employee_id, rate))
    conn.commit()
    conn.close()


def init_salary_overrides_db():
    conn = get_db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS salary_overrides (
        employee_id TEXT NOT NULL,
        period_key TEXT NOT NULL,
        amount REAL NOT NULL,
        PRIMARY KEY(employee_id, period_key)
    );
    """)
    conn.commit()
    conn.close()


def get_salary_overrides(period_key: str) -> dict:
    conn = get_db()
    rows = conn.execute("SELECT employee_id, amount FROM salary_overrides WHERE period_key=?",
                        (period_key,)).fetchall()
    conn.close()
    return {r["employee_id"]: r["amount"] for r in rows}


def set_salary_override(employee_id: str, period_key: str, amount):
    conn = get_db()
    if amount is None:
        conn.execute("DELETE FROM salary_overrides WHERE employee_id=? AND period_key=?",
                     (employee_id, period_key))
    else:
        conn.execute("INSERT OR REPLACE INTO salary_overrides(employee_id, period_key, amount) "
                     "VALUES(?,?,?)", (employee_id, period_key, amount))
    conn.commit()
    conn.close()
