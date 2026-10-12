import sqlite3
import os
import json
from datetime import datetime, timedelta, timezone
import secrets
from typing import Optional, List, Dict, Any, Tuple
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

# Часовой пояс ресторанов. Казахстан с 2024 — единый UTC+5 (Asia/Almaty).
# Render крутится в UTC, и голый datetime.now() писал в базу время на пять часов
# назад: чек-лист, сданный в 10:47 по Астане, показывался сданным в 05:47, день
# переворачивался в 05:00, а «сколько слотов уже прошло» считалось по UTC — из-за
# чего филиалу насчитывались пропуски за слоты, которые ещё не наступили.
# Возвращаем НАИВНОЕ время: формат хранения тот же, меняются только стрелки.
LOCAL_TZ = timezone(timedelta(hours=int(os.getenv("TZ_OFFSET_HOURS", "5"))))


def local_now() -> datetime:
    """Стенные часы ресторана (UTC+5), не зависят от часового пояса сервера."""
    return datetime.now(LOCAL_TZ).replace(tzinfo=None)


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
    # geo_* — точка филиала для проверки «сотрудник на месте». Радиус по умолчанию
    # 250 м: GPS в помещении врёт на сотни метров, и меньший радиус ругался бы
    # на человека, который стоит у плиты.
    for col, definition in [("iiko_server", "TEXT DEFAULT NULL"), ("api_password", "TEXT DEFAULT NULL"),
                            ("cloud_api_login", "TEXT DEFAULT NULL"),
                            ("geo_lat", "REAL"), ("geo_lon", "REAL"),
                            ("geo_radius", "INTEGER DEFAULT 250")]:
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

def _ensure_acquisition(conn):
    """`last_seen_ts` добавлен позже: сначала считали только приход. «Пришёл» и
    «пользуется» — разные вещи, и по одному приходу воронку не построить."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(user_acquisition)")}
    if "last_seen_ts" not in cols:
        conn.execute("ALTER TABLE user_acquisition ADD COLUMN last_seen_ts INTEGER")
        # У пришедших до этой правки за последний заход считаем приход: другого
        # свидетельства нет, а обнулять их активность — врать в другую сторону.
        conn.execute("UPDATE user_acquisition SET last_seen_ts = first_seen_ts "
                     "WHERE last_seen_ts IS NULL")


def normalize_username(username: Optional[str]) -> str:
    """@Kolya, Kolya и kolya — один человек. Иначе бот не найдёт того, кто
    зарегистрировался на сайте."""
    return (username or "").strip().lstrip("@").lower()


def touch_acquisition(telegram_id: int, ts: Optional[int] = None):
    """Отмечает заход. Дату прихода и источник НЕ трогает: это атрибуция, по ней
    считают, откуда пришли люди, и переписать её значит потерять её навсегда."""
    conn = get_db()
    _ensure_acquisition(conn)
    conn.execute("UPDATE user_acquisition SET last_seen_ts = ? WHERE telegram_id = ?",
                 (int(ts if ts is not None else datetime.now().timestamp()), telegram_id))
    conn.commit()
    conn.close()


# Телеграм выдаёт только положительные id, поэтому веб-аккаунтам раздаём
# отрицательные: перепутать «человека с сайта» и «человека из бота» нельзя даже
# случайно, а когда он подтвердит телеграм — аккаунт переезжает на настоящий id.
WEB_UID_BASE = -1000


def _ensure_web_signup(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS web_signup (
        uid         INTEGER PRIMARY KEY,
        username    TEXT,
        claim_code  TEXT UNIQUE,
        created_ts  INTEGER,
        claimed_ts  INTEGER,
        claimed_tid INTEGER
    );
    """)


def _next_web_uid(conn) -> int:
    """Выдаёт СЛЕДУЮЩИЙ веб-id и никогда не выдаёт его повторно.

    Раньше id считался как MIN(telegram_id) - 1, то есть выводился из текущего
    содержимого таблицы. Стоило аккаунту уехать на настоящий телеграм-id — и
    освободившийся id доставался следующему зарегистрировавшемуся. А ссылка
    `?m=...` — это HMAC от id, без срока и без привязки к строке в базе: старая
    закладка первого человека открывала кабинет второго, со всеми его данными и
    правом записи. Поэтому счётчик монотонный и назад не откатывается.
    """
    _ensure_app_state(conn)
    row = conn.execute("SELECT value FROM app_state WHERE key = 'web_uid_seq'").fetchone()
    seq = int(row["value"]) if row and str(row["value"]).lstrip("-").isdigit() else 0
    # Подстраховка на случай базы, заведённой до появления счётчика.
    low = conn.execute("SELECT MIN(telegram_id) m FROM users").fetchone()["m"]
    seq = min(seq, int(low or 0), WEB_UID_BASE) - 1
    conn.execute("INSERT OR REPLACE INTO app_state (key, value) VALUES ('web_uid_seq', ?)",
                 (str(seq),))
    return seq


def normalize_username(username: Optional[str]) -> str:
    """@Kolya, Kolya и kolya — один человек."""
    return (username or "").strip().lstrip("@").lower()


def web_signup(username: str, first_name: str = "") -> Tuple[int, str]:
    """Заводит аккаунт человеку, который регистрируется на сайте до бота.

    Возвращает (uid, код подтверждения). Код одноразовый и случайный: телеграм
    привязывается по нему, а НЕ по @username. Имя пользователя публично — его
    печатают на меню и в инстаграме, — и привязка по нему означала бы, что любой
    может завести аккаунт на чужое имя, а жертва заберёт его себе первым же
    /start и увидит чужой «филиал» среди своих.
    """
    conn = get_db()
    _ensure_web_signup(conn)
    uid = _next_web_uid(conn)
    code = secrets.token_urlsafe(18)
    conn.execute("INSERT INTO users (telegram_id, username, first_name, created_at) "
                 "VALUES (?,?,?,?)",
                 (uid, normalize_username(username), first_name or "",
                  datetime.now().isoformat()))
    conn.execute("INSERT INTO web_signup (uid, username, claim_code, created_ts) "
                 "VALUES (?,?,?,?)",
                 (uid, normalize_username(username), code,
                  int(datetime.now().timestamp())))
    conn.commit()
    conn.close()
    record_acquisition(uid, normalize_username(username), "site")
    return uid, code


# Сколько живёт код подтверждения. Непривязанный аккаунт вечно ждать не должен:
# код уходит по ссылке и может осесть в чужой истории.
WEB_CLAIM_TTL = 7 * 86400


def claim_web_signup_by_code(code: str, telegram_id: int) -> bool:
    """Телеграм подтверждён кодом — переносим рестораны на настоящий id.

    Строку веб-аккаунта НЕ удаляем: удаление вернуло бы его id в оборот, а
    выданная по нему ссылка продолжает действовать. Оставляем её пустой меткой —
    старая ссылка ведёт в аккаунт без единого ресторана.
    """
    code = (code or "").strip()
    if not code or telegram_id <= 0:
        return False
    conn = get_db()
    _ensure_web_signup(conn)
    _ensure_acquisition(conn)
    row = conn.execute(
        "SELECT uid, username, created_ts FROM web_signup "
        "WHERE claim_code = ? AND claimed_ts IS NULL", (code,)).fetchone()
    now = int(datetime.now().timestamp())
    if not row or now - int(row["created_ts"] or 0) > WEB_CLAIM_TTL:
        conn.close()
        return False
    web_uid, uname = int(row["uid"]), row["username"] or ""
    conn.execute("INSERT OR IGNORE INTO users (telegram_id, username, first_name, created_at) "
                 "VALUES (?,?,?,?)", (telegram_id, uname, "", datetime.now().isoformat()))
    conn.execute("UPDATE restaurants SET telegram_id = ? WHERE telegram_id = ?",
                 (telegram_id, web_uid))
    src = conn.execute("SELECT source, first_seen_ts FROM user_acquisition WHERE telegram_id = ?",
                       (web_uid,)).fetchone()
    if src:
        conn.execute("INSERT OR IGNORE INTO user_acquisition "
                     "(telegram_id, username, source, first_seen_ts, last_seen_ts) "
                     "VALUES (?,?,?,?,?)",
                     (telegram_id, uname, src["source"], src["first_seen_ts"], now))
        conn.execute("DELETE FROM user_acquisition WHERE telegram_id = ?", (web_uid,))
    # Метка: код погашен, имя снято — второй раз по нему не привяжешься.
    conn.execute("UPDATE web_signup SET claimed_ts = ?, claimed_tid = ?, claim_code = NULL, "
                 "username = NULL WHERE uid = ?", (now, telegram_id, web_uid))
    conn.execute("UPDATE users SET username = NULL WHERE telegram_id = ?", (web_uid,))
    conn.commit()
    conn.close()
    return True


def record_acquisition(telegram_id: int, username: str, source: str) -> bool:
    """Фиксирует первый контакт юзера с демо-ботом. True, если строка создана (новый юзер)."""
    conn = get_db()
    _ensure_acquisition(conn)
    ts = int(datetime.now().timestamp())
    cur = conn.execute(
        "INSERT OR IGNORE INTO user_acquisition "
        "(telegram_id, username, source, first_seen_ts, last_seen_ts) VALUES (?, ?, ?, ?, ?)",
        (telegram_id, username, source, ts, ts)
    )
    conn.commit()
    created = cur.rowcount > 0
    conn.close()
    return created


def acquisition_stats() -> dict:
    conn = get_db()
    _ensure_acquisition(conn)
    now = int(datetime.now().timestamp())
    total = conn.execute("SELECT COUNT(*) c FROM user_acquisition").fetchone()["c"]
    today = conn.execute("SELECT COUNT(*) c FROM user_acquisition WHERE first_seen_ts >= ?",
                         (now - 86400,)).fetchone()["c"]
    week = conn.execute("SELECT COUNT(*) c FROM user_acquisition WHERE first_seen_ts >= ?",
                        (now - 7 * 86400,)).fetchone()["c"]
    rows = conn.execute(
        "SELECT source, COUNT(*) c FROM user_acquisition GROUP BY source ORDER BY c DESC"
    ).fetchall()
    # Три РАЗНЫХ числа, мешать их нельзя: «пришёл» — открыл ссылку, «подключил» —
    # дошёл до конца и завёл ресторан, «активный» — заходил за последние 7 дней.
    # По одному приходу воронка врёт в обе стороны.
    active = conn.execute("SELECT COUNT(*) c FROM user_acquisition WHERE last_seen_ts >= ?",
                          (now - 7 * 86400,)).fetchone()["c"]
    connected = conn.execute(
        "SELECT COUNT(*) c FROM user_acquisition a WHERE EXISTS "
        "(SELECT 1 FROM restaurants r WHERE r.telegram_id = a.telegram_id)").fetchone()["c"]
    conn.close()
    return {"total": int(total), "today": int(today), "week": int(week),
            "active": int(active), "connected": int(connected),
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


# ── Управляющие филиалов ──
# Раньше доступ был только «владелец → все его рестораны». Управляющему нужен
# ровно его филиал, поэтому заводим отдельную привязку человек↔филиал. Одна
# точка подключения — get_restaurants ниже: правим её, и весь мини-апп начинает
# показывать управляющему только его данные, без правок в каждом эндпоинте.
def _ensure_branch_managers(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS branch_managers (
        organization_id TEXT NOT NULL,
        telegram_id INTEGER NOT NULL,
        name TEXT,
        added_at TEXT,
        active INTEGER DEFAULT 1,
        PRIMARY KEY (organization_id, telegram_id)
    )""")


def add_branch_manager(org_id, telegram_id: int, name: str = ""):
    """Привязывает человека к филиалам ссылки. Набор задаёт ССЫЛКА, а не история.

    Прежние привязки этого telegram_id снимаются и ставятся ровно те, что пришли:
    открыв вторую ссылку, управляющий не копит доступы — а одна ссылка при этом
    может открывать сразу несколько филиалов (один управляющий на два адреса).

    org_id — строка или список строк.
    """
    org_ids = [org_id] if isinstance(org_id, str) else list(org_id)
    if not org_ids:
        return
    conn = get_db()
    _ensure_branch_managers(conn)
    conn.execute("UPDATE branch_managers SET active = 0 WHERE telegram_id = ?",
                 (int(telegram_id),))
    now = datetime.now().isoformat()
    for oid in org_ids:
        conn.execute(
            """INSERT INTO branch_managers (organization_id, telegram_id, name, added_at, active)
               VALUES (?, ?, ?, ?, 1)
               ON CONFLICT(organization_id, telegram_id)
               DO UPDATE SET name = excluded.name, active = 1, added_at = excluded.added_at""",
            (oid, int(telegram_id), name, now))
    conn.commit()
    conn.close()


def revoke_branch_manager(org_id: str, telegram_id: int):
    conn = get_db()
    _ensure_branch_managers(conn)
    conn.execute(
        "UPDATE branch_managers SET active = 0 WHERE organization_id = ? AND telegram_id = ?",
        (org_id, int(telegram_id)))
    conn.commit()
    conn.close()


def get_branch_managers(org_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Действующие управляющие: отключённых ни в боте, ни в «Горе» быть не должно.

    Единственная версия на весь проект. После слияния веток их было две с
    разными сигнатурами — та, что определена ниже, молча перебивала первую, и
    вызовы с org_id падали бы TypeError.

    Колонок active/added_at у старых баз может не быть: «Гора» заводила таблицу
    своим, более узким CREATE. Поэтому смотрим, что реально есть.
    """
    conn = get_db()
    _ensure_branch_managers(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(branch_managers)").fetchall()}
    q = "SELECT * FROM branch_managers"
    where, args = [], []
    if "active" in cols:
        where.append("COALESCE(active, 1) = 1")
    if org_id:
        where.append("organization_id = ?")
        args.append(org_id)
    if where:
        q += " WHERE " + " AND ".join(where)
    if "added_at" in cols:
        q += " ORDER BY added_at DESC"
    rows = conn.execute(q, tuple(args)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _managed_org_ids(conn, telegram_id: int) -> List[str]:
    _ensure_branch_managers(conn)
    rows = conn.execute(
        "SELECT organization_id FROM branch_managers WHERE telegram_id = ? AND active = 1",
        (int(telegram_id),)).fetchall()
    return [r["organization_id"] for r in rows]


def get_owned_restaurants(telegram_id: int) -> List[Dict[str, Any]]:
    """Только СВОИ рестораны — где человек владелец, а не управляющий.

    get_restaurants намеренно отдаёт и те филиалы, где человек назначен
    управляющим: боту так удобнее. Но «Гора» по этой же функции отличала
    владельца от управляющего — и после слияния веток управляющий получал роль
    владельца: видел всю сеть, мог ставить задачи и рассылать сообщения.
    Владение спрашиваем отдельно и прямо.
    """
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM restaurants WHERE telegram_id = ? ORDER BY id",
        (int(telegram_id),)).fetchall()
    conn.close()
    out, seen = [], set()
    for r in rows:
        d = dict(r)
        org = d.get("organization_id", "")
        if org in seen:
            continue
        seen.add(org)
        out.append(d)
    return out


def get_restaurants(telegram_id: int) -> List[Dict[str, Any]]:
    conn = get_db()
    # свои рестораны (владелец) + те, где человек назначен управляющим
    managed = _managed_org_ids(conn, telegram_id)
    if managed:
        marks = ",".join("?" * len(managed))
        rows = conn.execute(
            f"""SELECT * FROM restaurants WHERE telegram_id = ?
                   OR organization_id IN ({marks}) ORDER BY id""",
            (telegram_id, *managed)).fetchall()
    else:
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


# ── Журналы филиала: чистота, температура, остатки ──────────────────────────
# Одна таблица на все три журнала: у них общий скелет (филиал + слот + дата +
# кто сдал), а различия целиком в payload. Три почти одинаковые таблицы пришлось
# бы джойнить в каждом отчёте.
def _ensure_journals(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS journal_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        organization_id TEXT NOT NULL,
        telegram_id INTEGER,
        kind TEXT NOT NULL,          -- clean | temp | stock
        zone TEXT NOT NULL DEFAULT '',-- hall/kitchen/wc для чистоты, иначе ''
        slot TEXT NOT NULL,
        day TEXT NOT NULL,           -- YYYY-MM-DD, локальная дата филиала
        payload TEXT,                -- JSON: file_id фото / показания / остатки
        status TEXT DEFAULT 'ok',    -- ok | dup
        note TEXT,
        created_at TEXT
    )""")
    # Пересдача заменяет предыдущую попытку, а не плодит строки.
    conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS ix_journal_slot
        ON journal_entries (organization_id, kind, zone, slot, day)""")
    conn.execute("""CREATE INDEX IF NOT EXISTS ix_journal_day
        ON journal_entries (day, organization_id)""")
    # Хэши присланных фото: ловим пересланное вчерашнее фото без ИИ и бесплатно.
    conn.execute("""CREATE TABLE IF NOT EXISTS journal_photos (
        sha TEXT NOT NULL,
        organization_id TEXT NOT NULL,
        zone TEXT NOT NULL,
        day TEXT NOT NULL,
        file_id TEXT,
        PRIMARY KEY (sha, organization_id)
    )""")


def save_journal_entry(org_id: str, kind: str, zone: str, slot: str, day: str,
                       telegram_id: int, payload: str, status: str = "ok",
                       note: str = "") -> int:
    conn = get_db()
    _ensure_journals(conn)
    cur = conn.execute(
        """INSERT INTO journal_entries
             (organization_id, telegram_id, kind, zone, slot, day, payload,
              status, note, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(organization_id, kind, zone, slot, day) DO UPDATE SET
             telegram_id = excluded.telegram_id, payload = excluded.payload,
             status = excluded.status, note = excluded.note,
             created_at = excluded.created_at""",
        (org_id, int(telegram_id or 0), kind, zone, slot, day, payload,
         status, note, datetime.now().isoformat()))
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    return rid


def get_journal_entries(day: str, org_id: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = get_db()
    _ensure_journals(conn)
    q = "SELECT * FROM journal_entries WHERE day = ?"
    args: tuple = (day,)
    if org_id:
        q += " AND organization_id = ?"
        args = (day, org_id)
    rows = conn.execute(q, args).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_journal_range(since: str, until: str) -> List[Dict[str, Any]]:
    """Записи за период — для штрафных баллов и дисциплины."""
    conn = get_db()
    _ensure_journals(conn)
    rows = conn.execute(
        "SELECT * FROM journal_entries WHERE day >= ? AND day <= ?",
        (since, until)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_paper_journal(org_id: str, day: str, key: str, telegram_id: int,
                       on: bool = True, slot: str = "11:00") -> bool:
    """Галочка «бумажный журнал заполнен». Живёт в journal_entries как
    kind='paper': скелет тот же (филиал + день + слот + кто), а заводить рядом
    четвёртую почти такую же таблицу — потом джойнить её в каждом отчёте.

    Снятие галочки — удаление строки, а не status='off': «отметку поставили и
    сняли» в истории значит ровно то же, что «не отмечали», и лишний статус
    только заставил бы каждый отчёт про него помнить.
    """
    conn = get_db()
    _ensure_journals(conn)
    if on:
        conn.execute(
            """INSERT INTO journal_entries
                 (organization_id, telegram_id, kind, zone, slot, day, payload,
                  status, note, created_at)
               VALUES (?, ?, 'paper', ?, ?, ?, '', 'ok', '', ?)
               ON CONFLICT(organization_id, kind, zone, slot, day) DO UPDATE SET
                 telegram_id = excluded.telegram_id, created_at = excluded.created_at""",
            (org_id, int(telegram_id or 0), key, slot, day, datetime.now().isoformat()))
    else:
        conn.execute("DELETE FROM journal_entries WHERE organization_id = ? AND kind = 'paper' "
                     "AND zone = ? AND slot = ? AND day = ?", (org_id, key, slot, day))
    conn.commit()
    conn.close()
    return bool(on)


def get_paper_journals(org_ids: List[str], since: str, until: str) -> List[Dict[str, Any]]:
    """Отметки по бумажным журналам за период — для панели и истории недели."""
    if not org_ids:
        return []
    conn = get_db()
    _ensure_journals(conn)
    rows = conn.execute(
        f"SELECT organization_id, zone AS journal, day, telegram_id, created_at "
        f"FROM journal_entries WHERE kind = 'paper' AND day >= ? AND day <= ? "
        f"AND organization_id IN ({','.join('?' * len(org_ids))})",
        [since, until] + list(org_ids)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def seen_photo(sha: str, org_id: str) -> Optional[Dict[str, Any]]:
    """Присылали ли уже это фото. Совпадение = попытка сдать старый кадр."""
    conn = get_db()
    _ensure_journals(conn)
    row = conn.execute(
        "SELECT * FROM journal_photos WHERE sha = ? AND organization_id = ?",
        (sha, org_id)).fetchone()
    conn.close()
    return dict(row) if row else None


def remember_photo(sha: str, org_id: str, zone: str, day: str, file_id: str):
    conn = get_db()
    _ensure_journals(conn)
    conn.execute(
        """INSERT INTO journal_photos (sha, organization_id, zone, day, file_id)
           VALUES (?, ?, ?, ?, ?) ON CONFLICT(sha, organization_id) DO NOTHING""",
        (sha, org_id, zone, day, file_id))
    conn.commit()
    conn.close()


def set_restaurant_server(org_id: str, server: str) -> int:
    """Прописывает филиалу его собственный сервер iiko. Возвращает число строк.

    Обновляем ВСЕ строки организации, а не только строку владельца: на проде одна
    и та же точка заведена под несколькими аккаунтами, и сервер — свойство точки,
    а не аккаунта. Иначе часть пользователей осталась бы на центральном.
    """
    conn = get_db()
    cur = conn.execute(
        "UPDATE restaurants SET iiko_server = ? WHERE organization_id = ?",
        (server, org_id))
    conn.commit()
    n = cur.rowcount
    conn.close()
    return n
def _ensure_app_state(conn):
    """Таблица создаётся в init_db, но app_state читают и фоновые задачи, и
    миграции — а они могут стартовать раньше. Без этого «no such table»."""
    conn.execute("CREATE TABLE IF NOT EXISTS app_state ("
                 "key TEXT PRIMARY KEY, value TEXT)")


def get_app_state(key: str) -> Optional[str]:
    conn = get_db()
    _ensure_app_state(conn)
    row = conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else None


def set_app_state(key: str, value: str):
    conn = get_db()
    _ensure_app_state(conn)
    conn.execute(
        "INSERT INTO app_state (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value)
    )
    conn.commit()
    conn.close()


def add_restaurant(telegram_id: int, api_login: str, org_id: str, org_name: str,
                   iiko_server: str = None, api_password: str = None, cloud_api_login: str = None):
    conn = get_db()
    conn.execute(
        """INSERT INTO restaurants
           (telegram_id, api_login, organization_id, organization_name, iiko_server, api_password, cloud_api_login, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (telegram_id, api_login, org_id, org_name, iiko_server,
         encrypt_password(api_password) if api_password else None,
         encrypt_password(cloud_api_login) if cloud_api_login else None,
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


# ── Задачи с отметкой выполнения (трекер в боте) ──
# Уведомление без обратной связи — просто шум: непонятно, взялся кто-то за проблему
# или она висит вторую неделю. Здесь храним, что отправили и что человек ответил.
# ── Недельный план по типам каналов (вкладка «План») ──
# Хранение в БД (план доступен на всех устройствах владельца), изоляция по org.
def _ensure_weekly_plans(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS weekly_plans (
        organization_id TEXT NOT NULL,
        ym TEXT NOT NULL,            -- 'YYYY-MM'
        widx INTEGER NOT NULL,       -- индекс недели в месяце (1..5)
        ch_type TEXT NOT NULL,       -- 'hall' | 'delivery' | 'agg'
        planned INTEGER NOT NULL,    -- план по КОЛИЧЕСТВУ заказов
        updated_at TEXT,
        PRIMARY KEY (organization_id, ym, widx, ch_type)
    )""")


def set_weekly_plan(org_id: str, ym: str, widx: int, ch_type: str, planned: int):
    conn = get_db()
    _ensure_weekly_plans(conn)
    conn.execute(
        """INSERT INTO weekly_plans (organization_id, ym, widx, ch_type, planned, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(organization_id, ym, widx, ch_type)
           DO UPDATE SET planned = excluded.planned, updated_at = excluded.updated_at""",
        (org_id, ym, int(widx), ch_type, int(planned), datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


def get_weekly_plans(org_id: str, ym: str) -> List[Dict[str, Any]]:
    conn = get_db()
    _ensure_weekly_plans(conn)
    rows = conn.execute(
        "SELECT widx, ch_type, planned FROM weekly_plans WHERE organization_id = ? AND ym = ?",
        (org_id, ym)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Дневной план недели (вкладка «План» = таблица владельца) ──
# Неделя ISO ('2026-W35' — та самая «Неделя 35»), день недели 1..7 (Пн..Вс),
# канал hall | delivery | agg. Старая weekly_plans (план на неделю целиком)
# осталась: на ней стоит похвала ассистента «план недели выполнен».
def _ensure_week_plan_day(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS week_plan_day (
        organization_id TEXT NOT NULL,
        iso_week TEXT NOT NULL,      -- 'YYYY-Www'
        dow INTEGER NOT NULL,        -- 1..7, понедельник первый
        ch_type TEXT NOT NULL,       -- 'hall' | 'delivery' | 'agg'
        planned INTEGER NOT NULL,    -- план по КОЛИЧЕСТВУ заказов
        updated_at TEXT,
        PRIMARY KEY (organization_id, iso_week, dow, ch_type)
    )""")


def set_day_plan(org_id: str, iso_week: str, dow: int, ch_type: str, planned: int):
    conn = get_db()
    _ensure_week_plan_day(conn)
    conn.execute(
        """INSERT INTO week_plan_day (organization_id, iso_week, dow, ch_type, planned, updated_at)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(organization_id, iso_week, dow, ch_type)
           DO UPDATE SET planned = excluded.planned, updated_at = excluded.updated_at""",
        (org_id, iso_week, int(dow), ch_type, int(planned), datetime.now().isoformat()))
    conn.commit()
    conn.close()


def set_day_plans(cells: List[Dict[str, Any]]):
    """Пачкой — «поставить план на всю неделю по средней»: одна транзакция
    вместо двадцати одного запроса подряд."""
    conn = get_db()
    _ensure_week_plan_day(conn)
    now = datetime.now().isoformat()
    conn.executemany(
        """INSERT INTO week_plan_day (organization_id, iso_week, dow, ch_type, planned, updated_at)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(organization_id, iso_week, dow, ch_type)
           DO UPDATE SET planned = excluded.planned, updated_at = excluded.updated_at""",
        [(c["org"], c["week"], int(c["dow"]), c["ch_type"], int(c["planned"]), now) for c in cells])
    conn.commit()
    conn.close()


def get_day_plans(org_ids: List[str], iso_week: str) -> Dict[str, Dict[int, Dict[str, int]]]:
    """{org: {dow: {ch_type: planned}}} за одну неделю по всем филиалам сразу."""
    if not org_ids:
        return {}
    conn = get_db()
    _ensure_week_plan_day(conn)
    rows = conn.execute(
        f"SELECT organization_id, dow, ch_type, planned FROM week_plan_day "
        f"WHERE iso_week = ? AND organization_id IN ({','.join('?' * len(org_ids))})",
        [iso_week] + list(org_ids)).fetchall()
    conn.close()
    out: Dict[str, Dict[int, Dict[str, int]]] = {}
    for r in rows:
        out.setdefault(r["organization_id"], {}).setdefault(int(r["dow"]), {})[r["ch_type"]] = int(r["planned"])
    return out


# ── Снимок факта заказов по дням (для вкладки «План») ──
# Факт берётся из iiko OLAP, но ЗАКРЫТЫЙ день больше не меняется — тянуть его
# заново при каждом открытии вкладки незачем: девять филиалов на девяти
# серверах, это десятки секунд на ровном месте. Ночью день закрывается и
# ложится сюда, дальше вкладка читает базу.
def _ensure_plan_day_facts(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS plan_day_facts (
        organization_id TEXT NOT NULL,
        day TEXT NOT NULL,              -- 'YYYY-MM-DD'
        ch_type TEXT NOT NULL,          -- hall / delivery / agg
        orders INTEGER NOT NULL,
        updated_at TEXT,
        PRIMARY KEY (organization_id, day, ch_type)
    )""")


def drop_plan_day_facts():
    """Выбрасывает снимок факта целиком. Нужен при смене классификатора
    каналов: строки, посчитанные по старому правилу, нельзя сравнивать с
    новыми — «эта неделя против прошлой» тихо смешала бы два разных смысла.
    Данные восстановимы из iiko, поэтому чистим без сожалений."""
    conn = get_db()
    _ensure_plan_day_facts(conn)
    conn.execute("DELETE FROM plan_day_facts")
    conn.commit()
    conn.close()


def save_plan_day_facts(org_id: str, days: Dict[str, Dict[str, int]]):
    """{день: {канал: заказов}} → в базу. Перезапись осознанная: iiko может
    поправить закрытый день задним числом (возвраты, перепробитие смены),
    и снимок должен догонять, а не спорить."""
    if not days:
        return
    conn = get_db()
    _ensure_plan_day_facts(conn)
    now = datetime.now().isoformat()
    conn.executemany(
        """INSERT INTO plan_day_facts (organization_id, day, ch_type, orders, updated_at)
           VALUES (?,?,?,?,?)
           ON CONFLICT(organization_id, day, ch_type)
           DO UPDATE SET orders = excluded.orders, updated_at = excluded.updated_at""",
        [(org_id, day, ch, int(n), now)
         for day, cells in days.items() for ch, n in cells.items()])
    conn.commit()
    conn.close()


def get_plan_day_facts(org_id: str, d_from: str, d_to: str) -> Dict[str, Dict[str, int]]:
    """{день: {канал: заказов}} за период по одному филиалу."""
    conn = get_db()
    _ensure_plan_day_facts(conn)
    rows = conn.execute(
        "SELECT day, ch_type, orders FROM plan_day_facts "
        "WHERE organization_id = ? AND day >= ? AND day <= ?",
        (org_id, d_from, d_to)).fetchall()
    conn.close()
    out: Dict[str, Dict[str, int]] = {}
    for r in rows:
        out.setdefault(r["day"], {})[r["ch_type"]] = int(r["orders"])
    return out


# ── История оценок филиалов ──
# Оценка считается каждую ночь и складывается сюда. Нужна для двух вещей:
# вторничной средней за неделю и динамики «было 44 — стало 61». Без истории
# оценка была мгновенным снимком, по которому нельзя понять, стало лучше или хуже.
def _ensure_branch_scores(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS branch_scores (
        organization_id TEXT NOT NULL,
        day TEXT NOT NULL,              -- 'YYYY-MM-DD'
        score INTEGER,                  -- NULL = в тот день данных не хватило
        counted INTEGER,                -- сколько критериев посчиталось
        criteria TEXT,                  -- JSON разбора, чтобы видеть, что просело
        PRIMARY KEY (organization_id, day)
    )""")


def save_branch_score(org_id: str, day: str, score: Optional[int],
                      counted: int = 0, criteria: Optional[List[Dict[str, Any]]] = None):
    conn = get_db()
    _ensure_branch_scores(conn)
    conn.execute("INSERT OR REPLACE INTO branch_scores "
                 "(organization_id, day, score, counted, criteria) VALUES (?,?,?,?,?)",
                 (org_id, day, score, int(counted), json.dumps(criteria or [], ensure_ascii=False)))
    conn.commit()
    conn.close()


def get_branch_scores(org_ids: List[str], day_from: str, day_to: str) -> Dict[str, List[Dict[str, Any]]]:
    """{org: [{day, score}, …]} за период, по возрастанию дат."""
    if not org_ids:
        return {}
    conn = get_db()
    _ensure_branch_scores(conn)
    rows = conn.execute(
        f"SELECT organization_id, day, score, counted FROM branch_scores "
        f"WHERE day BETWEEN ? AND ? AND organization_id IN ({','.join('?' * len(org_ids))}) "
        f"ORDER BY day", [day_from, day_to] + list(org_ids)).fetchall()
    conn.close()
    out: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(r["organization_id"], []).append(
            {"day": r["day"], "score": r["score"], "counted": r["counted"]})
    return out


def branch_score_average(org_ids: List[str], day_from: str, day_to: str) -> Dict[str, Dict[str, Any]]:
    """Средняя оценка за период по филиалам. Дни без оценки в среднее не идут:
    иначе филиал, у которого один день не собрался, «падал» без причины."""
    hist = get_branch_scores(org_ids, day_from, day_to)
    out: Dict[str, Dict[str, Any]] = {}
    for org, rows in hist.items():
        vals = [r["score"] for r in rows if r["score"] is not None]
        if not vals:
            continue
        out[org] = {"avg": round(sum(vals) / len(vals)), "days": len(vals),
                    "last": vals[-1], "first": vals[0]}
    return out


# ── Смены персонала: эталон старта, график и опоздания ──
# Факт (во сколько пришёл) живёт в iiko, у нас — только то, чего там нет:
# с чем сравнивать, что уже посчитано и о чём уже написали в телеграм.
def _ensure_staff_shifts(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS branch_shift_starts (
        organization_id TEXT NOT NULL,
        kind TEXT NOT NULL,             -- 'morning' | 'evening'
        start_time TEXT NOT NULL,       -- 'HH:MM'
        PRIMARY KEY (organization_id, kind)
    );
    CREATE TABLE IF NOT EXISTS branch_shift_grace (
        organization_id TEXT PRIMARY KEY,
        grace_min INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS staff_schedule (
        organization_id TEXT NOT NULL,
        employee_id TEXT NOT NULL,
        day TEXT NOT NULL,              -- 'YYYY-MM-DD'
        start_time TEXT NOT NULL,
        end_time TEXT,
        pay REAL,                       -- оплата за эту смену; NULL — не проставлена
        updated_at TEXT,
        PRIMARY KEY (organization_id, employee_id, day)
    );
    CREATE TABLE IF NOT EXISTS staff_attendance (
        organization_id TEXT NOT NULL,
        employee_id TEXT NOT NULL,
        day TEXT NOT NULL,              -- 'YYYY-MM-DD'
        start_time TEXT NOT NULL,       -- 'HH:MM' приход
        end_time TEXT,                  -- 'HH:MM' уход; пусто — человек ещё на смене
        name TEXT,                      -- имя на момент отметки: iiko может уволить
        source TEXT,                    -- кто поставил отметку
        updated_at TEXT,
        PRIMARY KEY (organization_id, employee_id, day)
    );
    /* Код подключения филиала — тот, что в QR из подсобки. Живёт в базе, а не
       в подписи: так его можно перевыпустить, если QR сфотографировали, не
       трогая общий секрет приложения. */
    CREATE TABLE IF NOT EXISTS branch_join (
        organization_id TEXT PRIMARY KEY,
        code TEXT NOT NULL,
        created_at TEXT
    );
    CREATE UNIQUE INDEX IF NOT EXISTS branch_join_code ON branch_join(code);
    /* Личная ссылка сотрудника. Управляющий выдаёт её напротив человека в
       расписании; code — сам секрет. Первый, кто открыл ссылку в телеграме,
       привязывается telegram_id, дальше она работает только ему: иначе
       пересланная в общий чат ссылка отмечала бы смены за всю бригаду. */
    CREATE TABLE IF NOT EXISTS staff_access (
        organization_id TEXT NOT NULL,
        employee_id TEXT NOT NULL,
        code TEXT NOT NULL,
        name TEXT,
        telegram_id INTEGER,            -- NULL — ссылку ещё не открывали
        created_at TEXT,
        bound_at TEXT,
        active INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY (organization_id, employee_id)
    );
    CREATE UNIQUE INDEX IF NOT EXISTS staff_access_code ON staff_access(code);
    /* Выплаты сотруднику. Начисление живёт в staff_schedule.pay (сколько стоит
       смена), выплата — здесь. Держим раздельно намеренно: «отработал» и
       «получил на руки» расходятся во времени, и вычитать одно из другого
       задним числом в той же строке значило бы терять историю. */
    CREATE TABLE IF NOT EXISTS staff_payouts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        organization_id TEXT NOT NULL,
        employee_id TEXT NOT NULL,
        day TEXT NOT NULL,              -- 'YYYY-MM-DD' когда выдали
        amount REAL NOT NULL,
        comment TEXT,
        created_at TEXT,
        created_by INTEGER
    );
    CREATE INDEX IF NOT EXISTS staff_payouts_org_day
        ON staff_payouts(organization_id, day);
    CREATE TABLE IF NOT EXISTS staff_late (
        organization_id TEXT NOT NULL,
        day TEXT NOT NULL,
        employee_id TEXT NOT NULL,
        name TEXT,
        planned TEXT,
        arrival TEXT,
        late_min INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (organization_id, day, employee_id)
    );
    /* Кому уже напомнили про завтрашнюю смену. Отдельная таблица, а не флаг в
       app_state: ключ на человека и день делает вставку атомарной, и перезапуск
       сервиса вечером не рассылает напоминания по второму разу. */
    CREATE TABLE IF NOT EXISTS staff_shift_notified (
        organization_id TEXT NOT NULL,
        employee_id TEXT NOT NULL,
        day TEXT NOT NULL,
        sent_at TEXT,
        PRIMARY KEY (organization_id, employee_id, day)
    );
    CREATE TABLE IF NOT EXISTS staff_late_notified (
        organization_id TEXT NOT NULL,
        day TEXT NOT NULL,
        employee_id TEXT NOT NULL,
        sent_at TEXT,
        PRIMARY KEY (organization_id, day, employee_id)
    );
    """)
    # pay добавлен позже: ставок у нас нет, сумма пишется прямо на смене.
    # NULL и 0 значат разное — «не проставили» против «этот выход бесплатный».
    cols = {r[1] for r in conn.execute("PRAGMA table_info(staff_schedule)").fetchall()}
    if "pay" not in cols:
        conn.execute("ALTER TABLE staff_schedule ADD COLUMN pay REAL")
    # Гео добавлено вместе с мини-аппом смен. geo_ok: 1 — в радиусе, 0 — вне
    # или геопозиции не дали. Смену это не блокирует (решение владельца), но
    # управляющий должен видеть, что человек отметился не на месте.
    acols = {r[1] for r in conn.execute("PRAGMA table_info(staff_attendance)").fetchall()}
    for col, definition in (("lat", "REAL"), ("lon", "REAL"), ("geo_ok", "INTEGER")):
        if col not in acols:
            conn.execute(f"ALTER TABLE staff_attendance ADD COLUMN {col} {definition}")


def get_shift_starts(org_id: str) -> Dict[str, Any]:
    """Эталон старта смен филиала. Пусто — значит филиал ещё не настраивали:
    решение, чем это заменить, принимает вызывающий (см. staff_late.DEFAULT_STARTS)."""
    conn = get_db()
    _ensure_staff_shifts(conn)
    rows = conn.execute("SELECT kind, start_time FROM branch_shift_starts "
                        "WHERE organization_id = ? ORDER BY start_time", (org_id,)).fetchall()
    g = conn.execute("SELECT grace_min FROM branch_shift_grace WHERE organization_id = ?",
                     (org_id,)).fetchone()
    conn.close()
    return {"starts": [{"kind": r["kind"], "time": r["start_time"]} for r in rows],
            "grace_min": int(g["grace_min"]) if g else None}


def set_shift_starts(org_id: str, starts: List[Dict[str, str]],
                     grace_min: Optional[int] = None):
    """Перезаписывает набор стартов целиком: их два-три, редактируются вместе."""
    conn = get_db()
    _ensure_staff_shifts(conn)
    conn.execute("DELETE FROM branch_shift_starts WHERE organization_id = ?", (org_id,))
    for s in starts:
        if s.get("time"):
            conn.execute("INSERT OR REPLACE INTO branch_shift_starts "
                         "(organization_id, kind, start_time) VALUES (?,?,?)",
                         (org_id, s.get("kind") or s["time"], s["time"]))
    if grace_min is not None:
        conn.execute("INSERT OR REPLACE INTO branch_shift_grace (organization_id, grace_min) "
                     "VALUES (?,?)", (org_id, max(0, int(grace_min))))
    conn.commit()
    conn.close()


def get_staff_schedule(org_id: str, day_from: str, day_to: str) -> List[Dict[str, Any]]:
    conn = get_db()
    _ensure_staff_shifts(conn)
    rows = conn.execute(
        "SELECT employee_id, day, start_time, end_time, pay FROM staff_schedule "
        "WHERE organization_id = ? AND day BETWEEN ? AND ?", (org_id, day_from, day_to)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def branch_fot_summary(org_id: str, day_from: str, day_to: str) -> Dict[str, Any]:
    """ФОТ филиала за период из оплаты смен.

    NULL у смены означает «оплату ещё не заполнили», а не ноль. Поэтому рядом
    с суммой всегда отдаём покрытие: сколько смен имеют сумму из общего числа.
    """
    conn = get_db()
    _ensure_staff_shifts(conn)
    row = conn.execute(
        "SELECT COUNT(*) AS shifts_total, COUNT(pay) AS shifts_priced, "
        "COALESCE(SUM(pay), 0) AS amount FROM staff_schedule "
        "WHERE organization_id = ? AND day BETWEEN ? AND ?",
        (org_id, day_from, day_to)).fetchone()
    conn.close()
    total = int(row["shifts_total"] or 0)
    priced = int(row["shifts_priced"] or 0)
    return {"amount": round(float(row["amount"] or 0)),
            "shifts_priced": priced, "shifts_total": total,
            "complete": total > 0 and priced == total}


def set_staff_shift(org_id: str, employee_id: str, day: str, start: str, end: str = "",
                    pay: Optional[float] = None):
    conn = get_db()
    _ensure_staff_shifts(conn)
    conn.execute("INSERT OR REPLACE INTO staff_schedule "
                 "(organization_id, employee_id, day, start_time, end_time, pay, updated_at) "
                 "VALUES (?,?,?,?,?,?,?)",
                 (org_id, employee_id, day, start, end or "", pay,
                  datetime.now().isoformat()))
    conn.commit()
    conn.close()


def remove_staff_shift(org_id: str, employee_id: str, day: str):
    conn = get_db()
    _ensure_staff_shifts(conn)
    conn.execute("DELETE FROM staff_schedule WHERE organization_id = ? AND employee_id = ? "
                 "AND day = ?", (org_id, employee_id, day))
    conn.commit()
    conn.close()


def staff_shift_counts(org_id: str, since: Optional[str] = None) -> Dict[str, int]:
    """Сколько раз сотрудника ставили в график. Нужно, чтобы в списке для
    расписания сверху шли те, кого ставят каждую неделю: имена приезжают из
    iiko пачкой на весь сервер, и без этого управляющий листал бы чужие точки."""
    conn = get_db()
    _ensure_staff_shifts(conn)
    sql = "SELECT employee_id, COUNT(*) AS n FROM staff_schedule WHERE organization_id = ?"
    args: List[Any] = [org_id]
    if since:
        sql += " AND day >= ?"
        args.append(since)
    rows = conn.execute(sql + " GROUP BY employee_id", args).fetchall()
    conn.close()
    return {r["employee_id"]: int(r["n"]) for r in rows}


# ── Факт смены. Наш, не iiko: явки терминала показывали только тех, кто пробил
# чек, и молчали про весь зал. Отметку кладёт тот, кто её видит. ──

def get_staff_attendance(org_id: str, day_from: str, day_to: str) -> List[Dict[str, Any]]:
    """Отметки филиала за период в формате строк staff_late.late_rows:
    employee_id, name, date, start, end, hours, open."""
    conn = get_db()
    _ensure_staff_shifts(conn)
    rows = conn.execute(
        "SELECT employee_id, day, start_time, end_time, name, source, geo_ok FROM staff_attendance "
        "WHERE organization_id = ? AND day BETWEEN ? AND ? ORDER BY day, start_time",
        (org_id, day_from, day_to)).fetchall()
    conn.close()
    out: List[Dict[str, Any]] = []
    for r in rows:
        start, end = r["start_time"], r["end_time"] or ""
        # Смена без времени ухода — человек ещё на смене (см. open в late_rows).
        hours = 0.0
        if end:
            try:
                sh, sm = (int(x) for x in start.split(":"))
                eh, em = (int(x) for x in end.split(":"))
                mins = (eh * 60 + em) - (sh * 60 + sm)
                if mins <= 0:
                    mins += 24 * 60          # смена через полночь
                hours = round(mins / 60, 2)
            except (ValueError, TypeError):
                hours = 0.0
        out.append({"employee_id": r["employee_id"], "name": r["name"] or "",
                    "date": r["day"], "start": start, "end": end,
                    "hours": hours, "open": not end,
                    "source": r["source"] or "", "geo_ok": r["geo_ok"]})
    return out


def set_staff_attendance(org_id: str, employee_id: str, day: str, start: str,
                         end: str = "", name: str = "", source: str = "manager",
                         lat: Optional[float] = None, lon: Optional[float] = None,
                         geo_ok: Optional[bool] = None):
    conn = get_db()
    _ensure_staff_shifts(conn)
    conn.execute("INSERT OR REPLACE INTO staff_attendance "
                 "(organization_id, employee_id, day, start_time, end_time, name, source, "
                 " lat, lon, geo_ok, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                 (org_id, employee_id, day, start, end or "", name or "", source,
                  lat, lon, None if geo_ok is None else int(geo_ok),
                  datetime.now().isoformat()))
    conn.commit()
    conn.close()


def remove_staff_attendance(org_id: str, employee_id: str, day: str):
    conn = get_db()
    _ensure_staff_shifts(conn)
    conn.execute("DELETE FROM staff_attendance WHERE organization_id = ? "
                 "AND employee_id = ? AND day = ?", (org_id, employee_id, day))
    conn.commit()
    conn.close()


# ── Точка филиала. Нужна, чтобы сверять, откуда сотрудник открывает смену. ──

def branch_name(org_id: str) -> str:
    """Как называется филиал. Филиал бывает зарегистрирован несколько раз
    (владелец плюс управляющие) — берём первую запись, как scope_of_token."""
    conn = get_db()
    row = conn.execute("SELECT organization_name FROM restaurants WHERE organization_id = ? "
                       "ORDER BY id LIMIT 1", (org_id,)).fetchone()
    conn.close()
    return (row[0] if row else "") or "Филиал"


def get_branch_geo(org_id: str) -> Optional[Dict[str, Any]]:
    """Координаты филиала или None, если их ещё не задали. None значит «сверять
    не с чем» — открытие смены при этом не блокируется, просто не проверяется."""
    conn = get_db()
    row = conn.execute("SELECT geo_lat, geo_lon, geo_radius FROM restaurants "
                       "WHERE organization_id = ? AND geo_lat IS NOT NULL "
                       "ORDER BY id LIMIT 1", (org_id,)).fetchone()
    conn.close()
    if not row:
        return None
    return {"lat": row["geo_lat"], "lon": row["geo_lon"],
            "radius": int(row["geo_radius"] or 250)}


def set_branch_geo(org_id: str, lat: float, lon: float, radius: int = 250):
    """Филиал регистрируют несколько раз (владелец + управляющие), поэтому пишем
    во ВСЕ его строки: иначе точка была бы видна одному из них."""
    conn = get_db()
    conn.execute("UPDATE restaurants SET geo_lat = ?, geo_lon = ?, geo_radius = ? "
                 "WHERE organization_id = ?", (lat, lon, max(20, int(radius)), org_id))
    conn.commit()
    conn.close()


# ── Личный доступ сотрудника: ссылка от управляющего. ──

def get_or_create_join_code(org_id: str) -> str:
    """Код подключения филиала. Короткий и из безопасного алфавита — он едет в
    deep-link телеграма, где разрешены только буквы, цифры, дефис и подчёркивание."""
    import secrets
    conn = get_db()
    _ensure_staff_shifts(conn)
    row = conn.execute("SELECT code FROM branch_join WHERE organization_id = ?",
                       (org_id,)).fetchone()
    if row:
        conn.close()
        return row["code"]
    code = secrets.token_urlsafe(9).replace("-", "_")
    conn.execute("INSERT OR REPLACE INTO branch_join (organization_id, code, created_at) "
                 "VALUES (?,?,?)", (org_id, code, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return code


def rotate_join_code(org_id: str) -> str:
    """Новый QR. Нужен, когда прежний сфотографировали: старый сразу мёртв."""
    conn = get_db()
    _ensure_staff_shifts(conn)
    conn.execute("DELETE FROM branch_join WHERE organization_id = ?", (org_id,))
    conn.commit()
    conn.close()
    return get_or_create_join_code(org_id)


def org_by_join_code(code: str) -> Optional[str]:
    if not code:
        return None
    conn = get_db()
    _ensure_staff_shifts(conn)
    row = conn.execute("SELECT organization_id FROM branch_join WHERE code = ?",
                       (code,)).fetchone()
    conn.close()
    return row["organization_id"] if row else None


def issue_staff_access(org_id: str, employee_id: str, name: str = "") -> str:
    """Выдаёт (или перевыпускает) личную ссылку. Перевыпуск сбрасывает привязку:
    им пользуются, когда человек сменил телефон или ссылка утекла."""
    import secrets
    code = secrets.token_urlsafe(12)
    conn = get_db()
    _ensure_staff_shifts(conn)
    conn.execute("INSERT OR REPLACE INTO staff_access "
                 "(organization_id, employee_id, code, name, telegram_id, created_at, bound_at, active) "
                 "VALUES (?,?,?,?,NULL,?,NULL,1)",
                 (org_id, employee_id, code, name or "", datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return code


def staff_access_by_code(code: str) -> Optional[Dict[str, Any]]:
    if not code:
        return None
    conn = get_db()
    _ensure_staff_shifts(conn)
    row = conn.execute("SELECT organization_id, employee_id, code, name, telegram_id, active "
                       "FROM staff_access WHERE code = ?", (code,)).fetchone()
    conn.close()
    if not row or not row["active"]:
        return None
    return dict(row)


def bind_staff_access(code: str, telegram_id: int) -> bool:
    """Привязывает ссылку к первому открывшему. True — привязали или это он же.
    False — ссылка уже чужая: пересланная в общий чат, она иначе позволила бы
    отмечать смены за товарища."""
    row = staff_access_by_code(code)
    if not row:
        return False
    if row["telegram_id"]:
        return int(row["telegram_id"]) == int(telegram_id)
    conn = get_db()
    conn.execute("UPDATE staff_access SET telegram_id = ?, bound_at = ? "
                 "WHERE code = ? AND telegram_id IS NULL",
                 (int(telegram_id), datetime.now().isoformat(), code))
    conn.commit()
    changed = conn.total_changes
    conn.close()
    # Гонка двух одновременных открытий: победил тот, чей UPDATE прошёл первым.
    return bool(changed) or (staff_access_by_code(code) or {}).get("telegram_id") == telegram_id


def list_staff_access(org_id: str) -> List[Dict[str, Any]]:
    """Кому филиал выдал ссылки и кто ими уже воспользовался."""
    conn = get_db()
    _ensure_staff_shifts(conn)
    rows = conn.execute("SELECT employee_id, code, name, telegram_id, bound_at "
                        "FROM staff_access WHERE organization_id = ? AND active = 1",
                        (org_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def revoke_staff_access(org_id: str, employee_id: str):
    conn = get_db()
    _ensure_staff_shifts(conn)
    conn.execute("UPDATE staff_access SET active = 0 WHERE organization_id = ? "
                 "AND employee_id = ?", (org_id, employee_id))
    conn.commit()
    conn.close()


def shifts_with_access(day: str) -> List[Dict[str, Any]]:
    """Смены на день у тех, кто уже привязал личную ссылку — только им есть куда
    писать. Кто ссылку не открывал, в выборку не попадает: это не ошибка, а
    «человека в мини-аппе ещё нет»."""
    conn = get_db()
    _ensure_staff_shifts(conn)
    rows = conn.execute(
        "SELECT s.organization_id, s.employee_id, s.start_time, s.end_time, s.pay, "
        "       a.name, a.telegram_id, a.code "
        "FROM staff_schedule s JOIN staff_access a "
        "  ON a.organization_id = s.organization_id AND a.employee_id = s.employee_id "
        "WHERE s.day = ? AND a.active = 1 AND a.telegram_id IS NOT NULL",
        (day,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_shift_reminded(org_id: str, employee_id: str, day: str) -> bool:
    """True — напоминаем впервые. False — уже писали, второй раз не надо."""
    conn = get_db()
    _ensure_staff_shifts(conn)
    try:
        conn.execute("INSERT INTO staff_shift_notified "
                     "(organization_id, employee_id, day, sent_at) VALUES (?,?,?,?)",
                     (org_id, employee_id, day, datetime.now().isoformat()))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


# ── Выплаты: начислено по сменам, выдано на руки, остаток ──

def add_staff_payout(org_id: str, employee_id: str, day: str, amount: float,
                     comment: str = "", created_by: int = 0) -> int:
    conn = get_db()
    _ensure_staff_shifts(conn)
    cur = conn.execute("INSERT INTO staff_payouts "
                       "(organization_id, employee_id, day, amount, comment, created_at, created_by) "
                       "VALUES (?,?,?,?,?,?,?)",
                       (org_id, employee_id, day, float(amount), comment or "",
                        datetime.now().isoformat(), int(created_by or 0)))
    conn.commit()
    pid = cur.lastrowid
    conn.close()
    return pid


def delete_staff_payout(org_id: str, payout_id: int) -> bool:
    """Удаляем с проверкой филиала: id угадать легко, и без неё чужую выплату
    можно было бы стереть по номеру."""
    conn = get_db()
    _ensure_staff_shifts(conn)
    cur = conn.execute("DELETE FROM staff_payouts WHERE id = ? AND organization_id = ?",
                       (int(payout_id), org_id))
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def get_staff_payouts(org_id: str, day_from: str, day_to: str,
                      employee_id: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = get_db()
    _ensure_staff_shifts(conn)
    q = ("SELECT id, employee_id, day, amount, comment FROM staff_payouts "
         "WHERE organization_id = ? AND day BETWEEN ? AND ?")
    args: List[Any] = [org_id, day_from, day_to]
    if employee_id:
        q += " AND employee_id = ?"
        args.append(employee_id)
    rows = conn.execute(q + " ORDER BY day DESC, id DESC", args).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def staff_pay_summary(org_id: str, day_from: str, day_to: str) -> Dict[str, Dict[str, float]]:
    """По каждому сотруднику: начислено сменами и выплачено за период.

    Смены без проставленной оплаты в начисление не идут — NULL значит «ещё не
    решили, сколько стоит», и считать её нулём означало бы занизить долг."""
    conn = get_db()
    _ensure_staff_shifts(conn)
    out: Dict[str, Dict[str, float]] = {}
    for r in conn.execute(
        "SELECT employee_id, SUM(pay) AS s, COUNT(pay) AS n FROM staff_schedule "
        "WHERE organization_id = ? AND day BETWEEN ? AND ? AND pay IS NOT NULL "
        "GROUP BY employee_id", (org_id, day_from, day_to)).fetchall():
        out.setdefault(r["employee_id"], {"earned": 0.0, "paid": 0.0, "shifts": 0})
        out[r["employee_id"]]["earned"] = float(r["s"] or 0)
        out[r["employee_id"]]["shifts"] = int(r["n"] or 0)
    for r in conn.execute(
        "SELECT employee_id, SUM(amount) AS s FROM staff_payouts "
        "WHERE organization_id = ? AND day BETWEEN ? AND ? GROUP BY employee_id",
        (org_id, day_from, day_to)).fetchall():
        out.setdefault(r["employee_id"], {"earned": 0.0, "paid": 0.0, "shifts": 0})
        out[r["employee_id"]]["paid"] = float(r["s"] or 0)
    conn.close()
    for v in out.values():
        v["balance"] = round(v["earned"] - v["paid"], 2)
    return out


def save_staff_late(org_id: str, rows: List[Dict[str, Any]]):
    """Кладём посчитанные приходы, чтобы оценка филиала не ходила в iiko заново.
    Строки без эталона ('noplan') не храним — они ничего не значат."""
    conn = get_db()
    _ensure_staff_shifts(conn)
    for r in rows:
        if r.get("status") not in ("late", "ontime"):
            continue
        conn.execute("INSERT OR REPLACE INTO staff_late "
                     "(organization_id, day, employee_id, name, planned, arrival, late_min) "
                     "VALUES (?,?,?,?,?,?,?)",
                     (org_id, r["date"], r["employee_id"], r.get("name") or "",
                      r.get("planned") or "", r.get("arrival") or "", int(r.get("late_min") or 0)))
    conn.commit()
    conn.close()


def staff_late_progress(org_ids: List[str], day_from: str, day_to: str) -> Dict[str, Dict[str, Any]]:
    """Опоздания сотрудников за период по филиалам — для оценки филиала.
    Филиал без единой посчитанной явки в ответ не попадает: критерий выпадает,
    а не обнуляет оценку."""
    if not org_ids:
        return {}
    conn = get_db()
    _ensure_staff_shifts(conn)
    rows = conn.execute(
        f"SELECT organization_id, COUNT(*) AS total, "
        f"       SUM(CASE WHEN late_min > 0 THEN 1 ELSE 0 END) AS late, "
        f"       SUM(late_min) AS minutes "
        f"FROM staff_late WHERE day BETWEEN ? AND ? AND organization_id IN "
        f"({','.join('?' * len(org_ids))}) GROUP BY organization_id",
        [day_from, day_to] + list(org_ids)).fetchall()
    conn.close()
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        total, late = int(r["total"] or 0), int(r["late"] or 0)
        if not total:
            continue
        out[r["organization_id"]] = {"total": total, "late": late,
                                     "late_pct": round(late / total * 100, 1),
                                     "late_minutes": int(r["minutes"] or 0)}
    return out


def mark_late_notified(org_id: str, day: str, employee_id: str) -> bool:
    """True — отметили впервые (можно слать). False — уже писали, второй раз не надо.
    Гонки нет: PRIMARY KEY делает вставку атомарной."""
    conn = get_db()
    _ensure_staff_shifts(conn)
    try:
        conn.execute("INSERT INTO staff_late_notified "
                     "(organization_id, day, employee_id, sent_at) VALUES (?,?,?,?)",
                     (org_id, day, employee_id, datetime.now().isoformat()))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


# ── Трекер задач из бота: кто отметил выполнение, кто нет ──
# Таблицу заводит бот (deploy-cashshifts), сайт её только читает. CREATE тут
# нужен, чтобы сайт не падал на чистой базе, где бот ещё ничего не создал.
def _ensure_bot_tasks(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS bot_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER NOT NULL,
        organization_id TEXT,
        branch TEXT,
        title TEXT NOT NULL,
        detail TEXT,
        money REAL,
        created_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'open',   -- open | done | later
        answered_at TEXT,
        answered_by TEXT
    )""")


def create_bot_task(telegram_id: int, org_id: str, branch: str, title: str,
                    detail: str = "", money: float = 0) -> int:
    conn = get_db()
    _ensure_bot_tasks(conn)
    cur = conn.execute(
        """INSERT INTO bot_tasks (telegram_id, organization_id, branch, title,
               detail, money, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (int(telegram_id), org_id, branch, title, detail, float(money or 0),
         datetime.now().isoformat()))
    conn.commit()
    task_id = cur.lastrowid
    conn.close()
    return int(task_id)


def answer_bot_task(task_id: int, status: str, who: str = "") -> Optional[Dict[str, Any]]:
    """Отмечает задачу и возвращает её. None — если такой задачи нет."""
    conn = get_db()
    _ensure_bot_tasks(conn)
    conn.execute(
        """UPDATE bot_tasks SET status = ?, answered_at = ?, answered_by = ?
           WHERE id = ?""",
        (status, datetime.now().isoformat(), who, int(task_id)))
    conn.commit()
    row = conn.execute("SELECT * FROM bot_tasks WHERE id = ?", (int(task_id),)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_bot_tasks(days: int = 14) -> List[Dict[str, Any]]:
    """Все задачи трекера за период — свежие сверху.

    Одна реализация на два имени: ветки называли её по-разному
    (`get_bot_tasks_all` в боте, `get_bot_tasks` на сайте), и при слиянии
    первая осталась с одним докстрингом — то есть возвращала None, а вызывающий
    код по ней итерировал и падал 500.
    """
    conn = get_db()
    _ensure_bot_tasks(conn)
    since = (datetime.now() - timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT * FROM bot_tasks WHERE created_at >= ? ORDER BY created_at DESC",
        (since,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# Имя из бота — тот же список задач.
get_bot_tasks_all = get_bot_tasks


def get_open_bot_tasks(telegram_id: int, days: int = 14) -> List[Dict[str, Any]]:
    """Незакрытые задачи владельца за последние дни — для напоминаний."""
    conn = get_db()
    _ensure_bot_tasks(conn)
    since = (datetime.now() - timedelta(days=days)).isoformat()
    rows = conn.execute(
        """SELECT * FROM bot_tasks WHERE telegram_id = ? AND status = 'open'
           AND created_at >= ? ORDER BY money DESC""",
        (int(telegram_id), since)).fetchall()
# ── План ИИ-директора: задачи, которые он нашёл сам ──
# Задачи считаются из живых данных при каждом открытии, поэтому «прогресс» без
# хранилища не посчитать: неизвестно, каким показатель был, когда задачу завели.
# Здесь держим момент появления задачи и её базовое значение — от него и меряем.
def _ensure_director_tasks(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS director_tasks (
        organization_id TEXT NOT NULL,
        task_key TEXT NOT NULL,      -- fc | chk | rev | stock | wo | cn
        metric TEXT,                 -- человекочитаемое имя показателя
        baseline REAL,               -- значение в момент появления задачи
        target REAL,                 -- к чему идём
        money REAL,                  -- сколько на кону, ₸/мес
        created_at TEXT,
        status TEXT DEFAULT 'open',  -- open | done
        done_at TEXT,
        last_value REAL,             -- последнее увиденное значение
        updated_at TEXT,
        PRIMARY KEY (organization_id, task_key)
    )""")


def upsert_director_task(org_id: str, key: str, metric: str, value: float,
                         target: float, money: float) -> Dict[str, Any]:
    """Заводит задачу или обновляет текущее значение. Возвращает строку задачи.

    baseline пишется ОДИН раз — при первом появлении. Иначе прогресс всегда был бы
    нулевым: показатель уехал бы вместе с точкой отсчёта.

    В ответе есть `is_new`: True ровно один раз — в тот заход, когда задача
    появилась. По нему шлётся уведомление управляющему. Без этого признака
    уведомление уходило бы на КАЖДЫЙ пересчёт, а пересчёт идёт при каждом
    открытии экрана — человек получал бы одну и ту же задачу десять раз за день.
    """
    conn = get_db()
    _ensure_director_tasks(conn)
    now = datetime.now().isoformat()
    row = conn.execute(
        "SELECT * FROM director_tasks WHERE organization_id = ? AND task_key = ?",
        (org_id, key)).fetchone()
    is_new = row is None
    if is_new:
        conn.execute(
            """INSERT INTO director_tasks (organization_id, task_key, metric, baseline,
                   target, money, created_at, status, last_value, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)""",
            (org_id, key, metric, value, target, money, now, value, now))
    else:
        conn.execute(
            """UPDATE director_tasks SET metric = ?, target = ?, money = ?,
                   last_value = ?, updated_at = ?
               WHERE organization_id = ? AND task_key = ?""",
            (metric, target, money, value, now, org_id, key))
    conn.commit()
    out = conn.execute(
        "SELECT * FROM director_tasks WHERE organization_id = ? AND task_key = ?",
        (org_id, key)).fetchone()
    conn.close()
    return {**dict(out), "is_new": is_new} if out else {}


def close_director_task(org_id: str, key: str, value: float):
    """Задача закрыта: показатель дошёл до цели."""
    conn = get_db()
    _ensure_director_tasks(conn)
    now = datetime.now().isoformat()
    conn.execute(
        """UPDATE director_tasks SET status = 'done', done_at = ?, last_value = ?,
               updated_at = ? WHERE organization_id = ? AND task_key = ? AND status != 'done'""",
        (now, value, now, org_id, key))
    conn.commit()
    conn.close()


def get_director_tasks(org_id: str) -> List[Dict[str, Any]]:
    conn = get_db()
    _ensure_director_tasks(conn)
    rows = conn.execute(
        "SELECT * FROM director_tasks WHERE organization_id = ? ORDER BY money DESC",
        (org_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Снимки показателей филиала (для похвалы ИИ-директора) ──
# Чтобы сказать «фудкост упал с 36% до 32%», нужно чем-то помнить вчерашние 36%.
# Держим последний снимок на (владелец, филиал): сравниваем текущие сигналы с ним.
# Снимок пишется не чаще раза в SNAPSHOT_MIN_HOURS — иначе за день накопится
# десяток одинаковых, и «улучшение с прошлого раза» схлопнется в шум.
SNAPSHOT_MIN_HOURS = 20


def _ensure_signal_snapshots(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS signal_snapshots (
        telegram_id INTEGER NOT NULL,
        branch TEXT NOT NULL,        -- имя филиала (ключ сигналов ассистента)
        taken_at TEXT NOT NULL,      -- ISO-время снимка
        payload TEXT NOT NULL,       -- JSON показателей филиала
        PRIMARY KEY (telegram_id, branch)
    )""")


def get_signal_snapshot(telegram_id: int, branch: str) -> Optional[Dict[str, Any]]:
    """Последний снимок филиала: {taken_at, payload} или None."""
    conn = get_db()
    _ensure_signal_snapshots(conn)
    row = conn.execute(
        "SELECT taken_at, payload FROM signal_snapshots WHERE telegram_id = ? AND branch = ?",
        (int(telegram_id), branch)
    ).fetchone()
    conn.close()
    if not row:
        return None
    try:
        return {"taken_at": row["taken_at"], "payload": json.loads(row["payload"])}
    except (ValueError, TypeError):
        return None  # битый JSON — как будто снимка нет, похвалы просто не будет


def save_signal_snapshot(telegram_id: int, branch: str, payload: Dict[str, Any]) -> bool:
    """Пишет снимок, если прошлый старше SNAPSHOT_MIN_HOURS. True — записали."""
    conn = get_db()
    _ensure_signal_snapshots(conn)
    row = conn.execute(
        "SELECT taken_at FROM signal_snapshots WHERE telegram_id = ? AND branch = ?",
        (int(telegram_id), branch)
    ).fetchone()
    if row:
        try:
            age = datetime.now() - datetime.fromisoformat(row["taken_at"])
            if age.total_seconds() < SNAPSHOT_MIN_HOURS * 3600:
                conn.close()
                return False
        except (ValueError, TypeError):
            pass  # непарсимая дата — перезапишем свежей
    conn.execute(
        """INSERT INTO signal_snapshots (telegram_id, branch, taken_at, payload)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(telegram_id, branch)
           DO UPDATE SET taken_at = excluded.taken_at, payload = excluded.payload""",
        (int(telegram_id), branch, datetime.now().isoformat(),
         json.dumps(payload, ensure_ascii=False))
    )
    conn.commit()
    conn.close()
    return True


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


# ==== Подключённые соцсети (OAuth-токены + снапшоты подписчиков) ====
# Токен хранится зашифрованным (encrypt_password/ENCRYPTION_KEY), как пароли iiko.

def _ensure_social(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS social_accounts (
        org_id TEXT NOT NULL,
        platform TEXT NOT NULL,
        account_id TEXT,
        token TEXT,
        username TEXT,
        updated_at TEXT,
        PRIMARY KEY(org_id, platform)
    );
    CREATE TABLE IF NOT EXISTS social_snapshots (
        org_id TEXT NOT NULL,
        platform TEXT NOT NULL,
        date TEXT NOT NULL,
        followers INTEGER,
        PRIMARY KEY(org_id, platform, date)
    );
    """)


def set_social_account(org_id: str, platform: str, account_id: str,
                       token: str, username: str = ""):
    conn = get_db()
    _ensure_social(conn)
    conn.execute("""INSERT OR REPLACE INTO social_accounts
                    (org_id, platform, account_id, token, username, updated_at)
                    VALUES(?,?,?,?,?,?)""",
                 (org_id, platform, account_id, encrypt_password(token),
                  username, datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()


def get_social_account(org_id: str, platform: str) -> Optional[Dict[str, Any]]:
    """Возвращает {account_id, token(расшифрован), username} или None."""
    conn = get_db()
    _ensure_social(conn)
    row = conn.execute("SELECT * FROM social_accounts WHERE org_id=? AND platform=?",
                       (org_id, platform)).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d["token"] = decrypt_password(d.get("token") or "")
    return d


def delete_social_account(org_id: str, platform: str):
    conn = get_db()
    _ensure_social(conn)
    conn.execute("DELETE FROM social_accounts WHERE org_id=? AND platform=?", (org_id, platform))
    conn.commit()
    conn.close()


def save_social_snapshot(org_id: str, platform: str, date: str, followers):
    conn = get_db()
    _ensure_social(conn)
    conn.execute("""INSERT OR REPLACE INTO social_snapshots(org_id, platform, date, followers)
                    VALUES(?,?,?,?)""", (org_id, platform, date, followers))
    conn.commit()
    conn.close()


def get_social_growth(org_id: str, platform: str, days: int = 30):
    conn = get_db()
    _ensure_social(conn)
    rows = conn.execute("""SELECT date, followers FROM social_snapshots
                           WHERE org_id=? AND platform=? AND date >= date('now', ?)
                           ORDER BY date""",
                        (org_id, platform, f"-{days} days")).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ==== Ставки для расчёта зарплаты (вкладка «Смены») ====

# Ставка принадлежит ФИЛИАЛУ. Первая версия таблицы ключевалась одним
# employee_id, то есть была общей на всю базу: владелец org A, узнавший
# employee_id из org B (это iiko-UUID), мог перебить чужую ставку, а у двух
# сетей на одном iiko-сервере ставки просто смешивались.
# Строки из той версии складываем сюда: чей это филиал, узнать уже нельзя.
# В расчёт они НЕ идут (см. get_salary_rates) — лежат как след, а не как данные.
LEGACY_RATE_ORG = ""


def _ensure_salary(conn):
    """Схема ставок. Вызывается из КАЖДОГО обращения, а не только из startup.

    ФОТ во вкладке «Планы» обёрнут в try/except: упади здесь запрос — исключение
    проглотится, и вместо суммы молча встанет «нет данных». Такую потерю никто
    не заметит, поэтому на порядок инициализации не закладываемся.
    """
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS salary_rates (
        employee_id TEXT PRIMARY KEY,
        rate REAL NOT NULL DEFAULT 0
    );
    """)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(salary_rates)")}
    if "organization_id" not in cols:
        # Переносим, а не пересоздаём с нуля: старые строки писал владелец для
        # СВОИХ сотрудников, просто филиал тогда не сохранялся. Оставляем их
        # общим запасным вариантом (organization_id = ''), чтобы уже заведённые
        # ставки не пропали; новые пишутся сразу в свой филиал.
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS salary_rates_v2 (
            organization_id TEXT NOT NULL,
            employee_id TEXT NOT NULL,
            rate REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (organization_id, employee_id)
        );
        """)
        conn.execute("INSERT OR IGNORE INTO salary_rates_v2 (organization_id, employee_id, rate) "
                     "SELECT ?, employee_id, rate FROM salary_rates", (LEGACY_RATE_ORG,))
        conn.execute("DROP TABLE salary_rates")
        conn.execute("ALTER TABLE salary_rates_v2 RENAME TO salary_rates")
        conn.commit()


def init_salary_db():
    conn = get_db()
    _ensure_salary(conn)
    conn.commit()
    conn.close()


def get_salary_rates(org_id: str) -> dict:
    """Ставки ТОЛЬКО своего филиала: {employee_id: rate}.

    Строки старой схемы (LEGACY_RATE_ORG) раньше подмешивались сюда запасным
    вариантом, чтобы уже заведённые ставки не пропали. Мостик убран по решению
    владельца: у двух сетей на одном iiko-сервере employee_id общий, и такая
    строка считалась бы в ФОТ чужого филиала. Изоляция важнее переносимости —
    непереехавшая ставка просто заводится заново в своём филиале.
    """
    conn = get_db()
    _ensure_salary(conn)
    rows = conn.execute(
        "SELECT employee_id, rate FROM salary_rates WHERE organization_id = ?",
        (org_id,)).fetchall()
    conn.close()
    return {r["employee_id"]: r["rate"] for r in rows}


def set_salary_rate(org_id: str, employee_id: str, rate: float):
    conn = get_db()
    _ensure_salary(conn)
    conn.execute("INSERT OR REPLACE INTO salary_rates(organization_id, employee_id, rate) "
                 "VALUES(?,?,?)", (org_id, employee_id, rate))
    conn.commit()
    conn.close()


def _ensure_salary_overrides(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS salary_overrides (
        employee_id TEXT NOT NULL,
        period_key TEXT NOT NULL,
        amount REAL NOT NULL,
        PRIMARY KEY(employee_id, period_key)
    );
    """)


def init_salary_overrides_db():
    conn = get_db()
    _ensure_salary_overrides(conn)
    conn.commit()
    conn.close()


def get_salary_overrides(period_key: str) -> dict:
    conn = get_db()
    _ensure_salary_overrides(conn)
    rows = conn.execute("SELECT employee_id, amount FROM salary_overrides WHERE period_key=?",
                        (period_key,)).fetchall()
    conn.close()
    return {r["employee_id"]: r["amount"] for r in rows}


def set_salary_override(employee_id: str, period_key: str, amount):
    conn = get_db()
    _ensure_salary_overrides(conn)
    if amount is None:
        conn.execute("DELETE FROM salary_overrides WHERE employee_id=? AND period_key=?",
                     (employee_id, period_key))
    else:
        conn.execute("INSERT OR REPLACE INTO salary_overrides(employee_id, period_key, amount) "
                     "VALUES(?,?,?)", (employee_id, period_key, amount))
    conn.commit()
    conn.close()


# ── Локальная база номенклатуры iiko (для сопоставления накладных) ──
# Зачем БД, а не кэш в памяти: (1) на Render процесс перезапускается и кэш
# умирает — каждый рестарт снова тянул бы весь справочник; (2) в справочнике
# тысячи позиций и большинство из них блюда/модификаторы, которые в накладной
# не встречаются никогда — их фильтруем один раз при загрузке, а не на каждую
# накладную. source_key = справочник (у самохостед iiko он общий на сервер,
# у облака — на организацию), см. catalog_key() у клиентов в iiko.py.
def _ensure_nomenclature(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS iiko_products (
        source_key TEXT NOT NULL,
        product_id TEXT NOT NULL,
        name TEXT,
        num TEXT,
        code TEXT,
        unit TEXT,
        type TEXT,
        containers TEXT,          -- JSON фасовок
        PRIMARY KEY (source_key, product_id)
    );
    CREATE TABLE IF NOT EXISTS iiko_products_sync (
        source_key TEXT PRIMARY KEY,
        synced_at TEXT,
        fetched INTEGER,          -- сколько отдал iiko
        kept INTEGER              -- сколько осталось после фильтра типов
    );
    CREATE TABLE IF NOT EXISTS invoice_match_memory (
        source_key TEXT NOT NULL,
        name_norm TEXT NOT NULL,  -- нормализованное имя строки накладной
        product_id TEXT NOT NULL,
        product_name TEXT,
        hits INTEGER DEFAULT 1,
        updated_at TEXT,
        PRIMARY KEY (source_key, name_norm)
    );
    """)
    # name_raw добавлен позже: ключ name_norm — это токены («м кор»), и показывать
    # его оператору бессмысленно. Держим рядом исходную строку накладной.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(invoice_match_memory)").fetchall()}
    if "name_raw" not in cols:
        conn.execute("ALTER TABLE invoice_match_memory ADD COLUMN name_raw TEXT")


def save_iiko_products(source_key: str, products: List[Dict[str, Any]], fetched: int = 0):
    """Полностью заменяет справочник для source_key (снимок, не докладываем)."""
    conn = get_db()
    _ensure_nomenclature(conn)
    try:
        conn.execute("BEGIN")
        conn.execute("DELETE FROM iiko_products WHERE source_key = ?", (source_key,))
        conn.executemany(
            "INSERT OR REPLACE INTO iiko_products "
            "(source_key, product_id, name, num, code, unit, type, containers) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(source_key, p.get("id"), p.get("name") or "", p.get("num") or "",
              p.get("code") or "", p.get("unit") or "", p.get("type") or "",
              json.dumps(p.get("containers") or [], ensure_ascii=False))
             for p in products if p.get("id")]
        )
        conn.execute(
            "INSERT INTO iiko_products_sync (source_key, synced_at, fetched, kept) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(source_key) DO UPDATE SET "
            "synced_at = excluded.synced_at, fetched = excluded.fetched, kept = excluded.kept",
            (source_key, datetime.now().isoformat(), int(fetched or 0), len(products))
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def load_iiko_products(source_key: str) -> List[Dict[str, Any]]:
    conn = get_db()
    _ensure_nomenclature(conn)
    rows = conn.execute(
        "SELECT product_id, name, num, code, unit, type, containers "
        "FROM iiko_products WHERE source_key = ?", (source_key,)
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        try:
            containers = json.loads(r["containers"] or "[]")
        except Exception:
            containers = []
        out.append({"id": r["product_id"], "name": r["name"], "num": r["num"],
                    "code": r["code"], "unit": r["unit"], "type": r["type"],
                    "containers": containers})
    return out


def iiko_products_synced_at(source_key: str) -> Optional[datetime]:
    conn = get_db()
    _ensure_nomenclature(conn)
    row = conn.execute("SELECT synced_at FROM iiko_products_sync WHERE source_key = ?",
                       (source_key,)).fetchone()
    conn.close()
    if not row or not row["synced_at"]:
        return None
    try:
        return datetime.fromisoformat(row["synced_at"])
    except Exception:
        return None


def iiko_products_stats(source_key: str) -> Dict[str, Any]:
    conn = get_db()
    _ensure_nomenclature(conn)
    row = conn.execute("SELECT synced_at, fetched, kept FROM iiko_products_sync "
                       "WHERE source_key = ?", (source_key,)).fetchone()
    conn.close()
    return dict(row) if row else {}


def touch_iiko_products_sync(source_key: str, stale: bool = False,
                             age_minutes: float = 0):
    """Состаривает отметку синхронизации (ручное обновление и тесты)."""
    conn = get_db()
    _ensure_nomenclature(conn)
    if stale:
        when = datetime.now() - timedelta(days=365)
    else:
        when = datetime.now() - timedelta(minutes=age_minutes or 0)
    conn.execute("UPDATE iiko_products_sync SET synced_at = ? WHERE source_key = ?",
                 (when.isoformat(), source_key))
    conn.commit()
    conn.close()


def remember_invoice_match(source_key: str, name_norm: str, product_id: str,
                           product_name: str = "", name_raw: str = ""):
    """Запоминает выбор оператора: строка накладной → товар iiko.

    Повторный вызов с тем же ключом ПЕРЕПРИВЯЗЫВАЕТ запись, а не плодит вторую:
    оператор, поправивший ошибочную привязку, ждёт именно этого.
    """
    if not (source_key and name_norm and product_id):
        return
    conn = get_db()
    _ensure_nomenclature(conn)
    conn.execute(
        "INSERT INTO invoice_match_memory (source_key, name_norm, product_id, product_name, "
        "name_raw, hits, updated_at) VALUES (?, ?, ?, ?, ?, 1, ?) "
        "ON CONFLICT(source_key, name_norm) DO UPDATE SET "
        "  product_id = excluded.product_id, product_name = excluded.product_name, "
        "  name_raw = COALESCE(NULLIF(excluded.name_raw, ''), invoice_match_memory.name_raw), "
        "  hits = invoice_match_memory.hits + 1, updated_at = excluded.updated_at",
        (source_key, name_norm, product_id, product_name or "", name_raw or "",
         datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


def list_invoice_match_memory(source_key: str) -> List[Dict[str, Any]]:
    """Справочник соответствий одного iiko-сервера, свежие сверху."""
    conn = get_db()
    _ensure_nomenclature(conn)
    rows = conn.execute(
        "SELECT name_norm, name_raw, product_id, product_name, hits, updated_at "
        "FROM invoice_match_memory WHERE source_key = ? "
        "ORDER BY updated_at DESC", (source_key,)).fetchall()
    conn.close()
    return [{"name_norm": r["name_norm"], "name_raw": r["name_raw"] or r["name_norm"],
             "product_id": r["product_id"], "product_name": r["product_name"] or "",
             "hits": int(r["hits"] or 0), "updated_at": r["updated_at"] or ""} for r in rows]


def forget_invoice_match(source_key: str, name_norm: str) -> bool:
    """Убирает запись. False — записи не было (повторное удаление не ошибка)."""
    conn = get_db()
    _ensure_nomenclature(conn)
    cur = conn.execute("DELETE FROM invoice_match_memory "
                       "WHERE source_key = ? AND name_norm = ?", (source_key, name_norm))
    conn.commit()
    n = cur.rowcount
    conn.close()
    return bool(n)


def get_invoice_match_memory(source_key: str) -> Dict[str, Dict[str, Any]]:
    conn = get_db()
    _ensure_nomenclature(conn)
    rows = conn.execute(
        "SELECT name_norm, product_id, product_name, hits FROM invoice_match_memory "
        "WHERE source_key = ?", (source_key,)
    ).fetchall()
    conn.close()
    return {r["name_norm"]: {"product_id": r["product_id"],
                            "product_name": r["product_name"],
                            "hits": r["hits"]} for r in rows}


# ── Задачи филиалам и баллы («Гора») ──
# Задачу ставит ТОЛЬКО владелец из своего кабинета: ИИ предлагает, владелец
# подтверждает. Управляющий задачу себе не создаёт — только закрывает.
# Баллы: быстро 10, средне 25, долго 50 за задачу; чек-лист +1 за сданный слот,
# −1 за пропущенный (штрафные тянут филиал вниз по горе).
TASK_POINTS = {"быстро": 10, "средне": 25, "долго": 50}


def _ensure_mountain(conn):
    """Таблицы «Горы».

    branch_managers здесь СПЕЦИАЛЬНО не создаётся: её заводит бот
    (`_ensure_branch_managers`) — с составным ключом и колонкой active. У
    «Горы» когда-то был свой, более узкий CREATE, и побеждала та схема, чей
    код успел выполниться первым: если первой оказывалась «Гора», запросы бота
    падали с «no such column: active». Одна таблица — один владелец схемы."""
    _ensure_branch_managers(conn)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS branch_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        organization_id TEXT NOT NULL,
        branch TEXT,
        task_key TEXT,              -- fc | cn | wo | stock | rev | chk
        title TEXT NOT NULL,
        detail TEXT,
        money REAL,                 -- ₸ на кону
        effort TEXT,                -- быстро | средне | долго
        points INTEGER,
        status TEXT DEFAULT 'open', -- open | done
        created_at TEXT,
        created_by INTEGER,         -- владелец, который поставил
        done_at TEXT,
        done_by INTEGER
    );
    CREATE INDEX IF NOT EXISTS ix_branch_tasks_org ON branch_tasks(organization_id, status);
    """)


def _manager_cols(conn) -> set:
    """Таблицу заводил бот раньше нас: у неё составной ключ и колонка active.
    Работаем по факту, а не по своему CREATE — иначе ON CONFLICT падает."""
    return {r[1] for r in conn.execute("PRAGMA table_info(branch_managers)").fetchall()}


def set_branch_manager(org_id: str, telegram_id: int, name: str = ""):
    conn = get_db()
    _ensure_mountain(conn)
    cols = _manager_cols(conn)
    if "active" in cols:
        conn.execute("INSERT OR REPLACE INTO branch_managers "
                     "(organization_id, telegram_id, name, added_at, active) VALUES (?,?,?,?,1)",
                     (org_id, int(telegram_id), name or "", datetime.now().isoformat()))
    else:
        conn.execute("INSERT OR REPLACE INTO branch_managers "
                     "(organization_id, telegram_id, name, added_at) VALUES (?,?,?,?)",
                     (org_id, int(telegram_id), name or "", datetime.now().isoformat()))
    conn.commit()
    conn.close()


def add_branch_task(org_id: str, branch: str, title: str, *, detail: str = "",
                    money: float = 0, effort: str = "средне", task_key: str = "",
                    created_by: int = 0) -> int:
    conn = get_db()
    _ensure_mountain(conn)
    cur = conn.execute(
        "INSERT INTO branch_tasks (organization_id, branch, task_key, title, detail, money, "
        "effort, points, status, created_at, created_by) VALUES (?,?,?,?,?,?,?,?,'open',?,?)",
        (org_id, branch or "", task_key or "", title, detail or "", float(money or 0),
         effort, TASK_POINTS.get(effort, 25), datetime.now().isoformat(), int(created_by or 0)))
    conn.commit()
    tid = cur.lastrowid
    conn.close()
    return tid


def close_branch_task(task_id: int, done_by: int = 0) -> bool:
    conn = get_db()
    _ensure_mountain(conn)
    cur = conn.execute("UPDATE branch_tasks SET status='done', done_at=?, done_by=? "
                       "WHERE id=? AND status='open'",
                       (datetime.now().isoformat(), int(done_by or 0), int(task_id)))
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def get_branch_tasks(org_ids: Optional[List[str]] = None,
                     since: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = get_db()
    _ensure_mountain(conn)
    q = "SELECT * FROM branch_tasks WHERE 1=1"
    args: List[Any] = []
    if org_ids:
        q += f" AND organization_id IN ({','.join('?' * len(org_ids))})"
        args += list(org_ids)
    if since:
        q += " AND created_at >= ?"
        args.append(since)
    rows = conn.execute(q + " ORDER BY id DESC", args).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Сообщения владельца управляющим (уведомления на сайте) ──
def _ensure_notices(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS branch_notices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        organization_id TEXT,        -- NULL/'' = всем филиалам
        text TEXT NOT NULL,
        created_at TEXT,
        created_by INTEGER,
        audience TEXT,               -- 'managers' (владелец→управляющим) | 'staff'
        to_employee_id TEXT          -- при audience='staff': NULL — всей смене, иначе лично
    );
    CREATE TABLE IF NOT EXISTS branch_notice_reads (
        notice_id INTEGER NOT NULL,
        telegram_id INTEGER NOT NULL,
        read_at TEXT,
        PRIMARY KEY (notice_id, telegram_id)
    );
    """)
    # Аудитория добавлена вместе с мини-аппом смен. Старые строки — сообщения
    # владельца управляющим, поэтому пустое значение читаем как 'managers':
    # иначе вся история разом высыпалась бы сотрудникам.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(branch_notices)").fetchall()}
    for col, definition in (("audience", "TEXT"), ("to_employee_id", "TEXT")):
        if col not in cols:
            conn.execute(f"ALTER TABLE branch_notices ADD COLUMN {col} {definition}")


def add_branch_notice(text: str, created_by: int, org_id: str = "",
                      audience: str = "managers",
                      to_employee_id: Optional[str] = None) -> int:
    conn = get_db()
    _ensure_notices(conn)
    cur = conn.execute("INSERT INTO branch_notices "
                       "(organization_id, text, created_at, created_by, audience, to_employee_id) "
                       "VALUES (?,?,?,?,?,?)",
                       (org_id or "", text, datetime.now().isoformat(), int(created_by or 0),
                        audience, to_employee_id))
    conn.commit()
    nid = cur.lastrowid
    conn.close()
    return nid


def get_branch_notices(org_ids: Optional[List[str]] = None, reader: int = 0,
                       limit: int = 20) -> List[Dict[str, Any]]:
    """Сообщения филиалам + отметка, прочитал ли их этот человек."""
    conn = get_db()
    _ensure_notices(conn)
    q = ("SELECT n.*, (SELECT 1 FROM branch_notice_reads r "
         "            WHERE r.notice_id = n.id AND r.telegram_id = ?) AS is_read "
         "FROM branch_notices n WHERE COALESCE(n.audience,'managers') = 'managers'")
    args: List[Any] = [int(reader or 0)]
    if org_ids is not None:
        # всем (пустой organization_id) плюс адресные для этих филиалов
        q += f" AND (COALESCE(n.organization_id,'') = '' OR n.organization_id IN ({','.join('?' * len(org_ids))}))"
        args += list(org_ids)
    rows = conn.execute(q + " ORDER BY n.id DESC LIMIT ?", args + [int(limit)]).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_staff_notices(org_id: str, employee_id: str, reader: int = 0,
                      limit: int = 20) -> List[Dict[str, Any]]:
    """Лента сотрудника: объявления всей смене филиала плюс адресованные лично.

    Чужие личные сообщения сюда не попадают — это первое, что человек проверит,
    открыв мини-апп, и утечка чужого разговора убила бы доверие к нему."""
    conn = get_db()
    _ensure_notices(conn)
    rows = conn.execute(
        "SELECT n.*, (SELECT 1 FROM branch_notice_reads r "
        "             WHERE r.notice_id = n.id AND r.telegram_id = ?) AS is_read "
        "FROM branch_notices n "
        "WHERE COALESCE(n.audience,'') = 'staff' AND n.organization_id = ? "
        "  AND (n.to_employee_id IS NULL OR n.to_employee_id = ?) "
        "ORDER BY n.id DESC LIMIT ?",
        (int(reader or 0), org_id, employee_id, int(limit))).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def staff_notice_is_for(notice_id: int, org_id: str, employee_id: str) -> bool:
    """Можно ли этому сотруднику пометить сообщение прочитанным. Без проверки
    счётчик прочтений врал бы: отметиться мог бы кто угодно по чужому id."""
    conn = get_db()
    _ensure_notices(conn)
    row = conn.execute("SELECT COALESCE(organization_id,''), COALESCE(audience,''), "
                       "to_employee_id FROM branch_notices WHERE id = ?",
                       (int(notice_id),)).fetchone()
    conn.close()
    if row is None or row[1] != "staff" or row[0] != org_id:
        return False
    return row[2] is None or row[2] == employee_id


def sent_staff_notices(org_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Что филиал отправил смене — с числом прочитавших, для управляющего."""
    conn = get_db()
    _ensure_notices(conn)
    rows = conn.execute(
        "SELECT n.id, n.text, n.created_at, n.to_employee_id, "
        "       (SELECT COUNT(*) FROM branch_notice_reads r WHERE r.notice_id = n.id) AS read_by "
        "FROM branch_notices n WHERE COALESCE(n.audience,'') = 'staff' "
        "  AND n.organization_id = ? ORDER BY n.id DESC LIMIT ?",
        (org_id, int(limit))).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def notice_is_for(notice_id: int, org_ids: List[str]) -> bool:
    """Адресовано ли сообщение хоть одному из этих филиалов. Пустой
    organization_id — сообщение всем, оно годится любому филиалу."""
    conn = get_db()
    _ensure_notices(conn)
    row = conn.execute("SELECT COALESCE(organization_id,'') FROM branch_notices WHERE id = ?",
                       (int(notice_id),)).fetchone()
    conn.close()
    if row is None:
        return False
    return row[0] == "" or row[0] in set(org_ids)


def mark_notice_read(notice_id: int, telegram_id: int):
    conn = get_db()
    _ensure_notices(conn)
    conn.execute("INSERT OR REPLACE INTO branch_notice_reads (notice_id, telegram_id, read_at) "
                 "VALUES (?,?,?)", (int(notice_id), int(telegram_id), datetime.now().isoformat()))
    conn.commit()
    conn.close()


def notice_read_stats(notice_id: int) -> int:
    conn = get_db()
    _ensure_notices(conn)
    n = conn.execute("SELECT COUNT(*) FROM branch_notice_reads WHERE notice_id = ?",
                     (int(notice_id),)).fetchone()[0]
    conn.close()
    return int(n)


# ── Разборы лосося: журнал филиала и свод за период ──
# Каждый разбор фиксируется здесь и НЕ уходит в iiko поштучно: администратор
# заносит один сводный акт за период руками. Поэтому храним и веса, и цены —
# по ним свод считается задним числом, даже если накладные с тех пор изменились.
def _ensure_salmon(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS salmon_acts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        organization_id TEXT NOT NULL,
        created_at TEXT,             -- ISO, по нему же фильтр периода
        created_by INTEGER,
        weight_raw REAL, weight_slice REAL, weight_trim REAL, weight_waste REAL,
        price_per_kg REAL,           -- цена входа на момент разбора
        trim_price REAL,             -- константа обрезков на момент разбора
        slice_price REAL,            -- себестоимость нарезки, ₸/кг
        note TEXT,
        posted_at TEXT,              -- когда сводный акт занесли в iiko руками
        posted_by INTEGER
    );
    CREATE INDEX IF NOT EXISTS idx_salmon_org_date
        ON salmon_acts (organization_id, created_at);
    """)
    # Дописано позже, поэтому колонками, а не новой таблицей:
    #  • терияки — часть обрезков, обжаренная с соусом (weight_fry — сколько
    #    обрезков положили жарить, weight_teriyaki — сколько сняли);
    #  • weight_sauce — ГРАММЫ соуса терияки на эту жарку, sauce_price — его цена
    #    из накладных на тот день: свод пересчитывается по сохранённой цене,
    #    иначе новая накладная сдвинула бы уже занесённый в iiko акт;
    #  • kind — вид разбора: лосось был первым и в старых строках не записан,
    #    поэтому пустой kind читается как 'salmon'.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(salmon_acts)").fetchall()}
    for col, decl in (("weight_teriyaki", "REAL"), ("weight_fry", "REAL"),
                      ("weight_sauce", "REAL"), ("sauce_price", "REAL"),
                      ("kind", "TEXT")):
        if col not in cols:
            conn.execute(f"ALTER TABLE salmon_acts ADD COLUMN {col} {decl}")
    # Жарки, записанные ДО выноса терияки в свой документ, лежат без kind, и
    # пустой kind читается как 'salmon'. Из-за этого они не попадали в свод
    # жарок вовсе — ужарка за период недосчитывалась ровно на них, — а в свод
    # разбора попадали строкой без входа и портили обрезки. Узнаём их по тому,
    # чего у разбора не бывает: вес в жарку есть, а входа нет.
    conn.execute(
        "UPDATE salmon_acts SET kind = 'teriyaki' "
        "WHERE COALESCE(NULLIF(kind,''),'') = '' "
        "AND COALESCE(weight_fry,0) > 0 AND COALESCE(weight_raw,0) <= 0")
    conn.execute(
        "UPDATE salmon_acts SET kind = 'salmon' WHERE COALESCE(NULLIF(kind,''),'') = ''")


def add_salmon_act(org_id: str, created_by: int, calc: Dict[str, Any],
                   price_per_kg: float, trim_price: float, note: str = "",
                   kind: str = "salmon") -> int:
    """Фиксируем разбор. calc — результат acts.split_act."""
    conn = get_db()
    _ensure_salmon(conn)
    cur = conn.execute(
        "INSERT INTO salmon_acts (organization_id, kind, created_at, created_by, weight_raw, "
        "weight_slice, weight_trim, weight_waste, price_per_kg, trim_price, slice_price, note) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (org_id, kind or "salmon", datetime.now().isoformat(), int(created_by or 0),
         float(calc["raw"]["weight"]), float(calc["slice"]["weight"]),
         float(calc["trim"]["weight"]), float(calc["waste"]["weight"]),
         float(price_per_kg or 0), float(trim_price or 0),
         float(calc["slice"]["price_per_kg"] or 0), (note or "")[:300]))
    conn.commit()
    aid = cur.lastrowid
    conn.close()
    return aid


def add_teriyaki_act(org_id: str, created_by: int, calc: Dict[str, Any],
                     note: str = "") -> int:
    """Фиксируем жарку. calc — результат acts.fry_teriyaki.

    Живёт в той же таблице под kind='teriyaki': период, проводка и удаление у
    них общие, а вот колонки разные — у жарки заняты weight_fry/weight_teriyaki/
    weight_sauce, а весов разбора нет вовсе. Поэтому и свод свой:
    acts.summarize_teriyaki, а не acts.summarize.
    """
    ter = calc.get("teriyaki") or {}
    sauce = calc.get("sauce") or {}
    conn = get_db()
    _ensure_salmon(conn)
    cur = conn.execute(
        "INSERT INTO salmon_acts (organization_id, kind, created_at, created_by, "
        "weight_fry, weight_teriyaki, weight_sauce, trim_price, sauce_price, slice_price, note) "
        "VALUES (?,'teriyaki',?,?,?,?,?,?,?,?,?)",
        (org_id, datetime.now().isoformat(), int(created_by or 0),
         float(ter.get("from_trim") or 0), float(ter.get("weight") or 0),
         float(sauce.get("grams") or 0),
         float(calc.get("trim_price") or 0), float(sauce.get("price_per_kg") or 0),
         float(ter.get("price_per_kg") or 0), (note or "")[:300]))
    conn.commit()
    aid = cur.lastrowid
    conn.close()
    return aid


def get_salmon_acts(org_ids: List[str], date_from: str = "", date_to: str = "",
                    only_open: bool = False, limit: int = 500,
                    kind: str = "salmon") -> List[Dict[str, Any]]:
    """Разборы филиалов за период. only_open — ещё не занесённые в iiko.

    Виды разбора не смешиваем: свод лосося и свод угря заносят в iiko разными
    актами, и средний отход по двум рыбам сразу не значит ничего.
    """
    if not org_ids:
        return []
    conn = get_db()
    _ensure_salmon(conn)
    q = (f"SELECT * FROM salmon_acts WHERE organization_id IN ({','.join('?' * len(org_ids))})"
         " AND COALESCE(NULLIF(kind,''),'salmon') = ?")
    args: List[Any] = list(org_ids) + [kind or "salmon"]
    if date_from:
        q += " AND created_at >= ?"
        args.append(date_from)
    if date_to:
        # даты приходят как YYYY-MM-DD, а created_at — с временем: берём день целиком
        q += " AND created_at <= ?"
        args.append(date_to + "T23:59:59" if len(date_to) == 10 else date_to)
    if only_open:
        q += " AND COALESCE(posted_at,'') = ''"
    rows = conn.execute(q + " ORDER BY id DESC LIMIT ?", args + [int(limit)]).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_salmon_act(act_id: int, org_ids: List[str]) -> bool:
    """Удалить можно только свой филиал и только пока свод не занесён в iiko."""
    if not org_ids:
        return False
    conn = get_db()
    _ensure_salmon(conn)
    cur = conn.execute(
        f"DELETE FROM salmon_acts WHERE id = ? AND COALESCE(posted_at,'') = '' "
        f"AND organization_id IN ({','.join('?' * len(org_ids))})",
        [int(act_id)] + list(org_ids))
    conn.commit()
    n = cur.rowcount
    conn.close()
    return n > 0


def mark_salmon_posted(act_ids: List[int], org_ids: List[str], by: int) -> int:
    """Отметка «свод занесён в iiko». Закрывает именно те разборы, которые
    человек видел на экране, — иначе в пачку попадёт разбор, добавленный, пока
    администратор переписывал числа в iikoOffice."""
    ids = [int(a) for a in act_ids or []]
    if not ids or not org_ids:
        return 0
    conn = get_db()
    _ensure_salmon(conn)
    cur = conn.execute(
        f"UPDATE salmon_acts SET posted_at = ?, posted_by = ? "
        f"WHERE id IN ({','.join('?' * len(ids))}) "
        f"AND organization_id IN ({','.join('?' * len(org_ids))}) "
        f"AND COALESCE(posted_at,'') = ''",
        [datetime.now().isoformat(), int(by or 0)] + ids + list(org_ids))
    conn.commit()
    n = cur.rowcount
    conn.close()
    return int(n)


def all_salmon_orgs(kind: str = "salmon") -> List[str]:
    """Филиалы, где вообще были разборы, — для публичной таблицы по сети."""
    conn = get_db()
    _ensure_salmon(conn)
    rows = conn.execute(
        "SELECT DISTINCT organization_id FROM salmon_acts "
        "WHERE COALESCE(NULLIF(kind,''),'salmon') = ?", (kind or "salmon",)).fetchall()
    conn.close()
    return [r[0] for r in rows if r[0]]


# ── Чек-листы, сданные с сайта ──
# Фото кладём файлами, в базе только пути: блобы раздувают SQLite и ломают
# бэкапы. Каталог — рядом с базой (на Render это /data, переживает редеплой).
def _ensure_checklist(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS checklist_uploads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        organization_id TEXT NOT NULL,
        day TEXT NOT NULL,
        slot TEXT NOT NULL,          -- 10:45 / 15:00 / 20:45 / 23:45
        zone TEXT NOT NULL,          -- hall | kitchen | wc
        photos TEXT,                 -- JSON со списком путей
        note TEXT,
        created_at TEXT,
        created_by INTEGER
    );
    CREATE INDEX IF NOT EXISTS ix_checklist_day ON checklist_uploads(organization_id, day);
    """)


def checklist_dir() -> str:
    d = os.path.join(os.path.dirname(os.path.abspath(DB_PATH)) or ".", "checklists")
    os.makedirs(d, exist_ok=True)
    return d


def save_checklist_upload(org_id: str, day: str, slot: str, zone: str,
                          photos: List[str], note: str = "", by: int = 0) -> int:
    conn = get_db()
    _ensure_checklist(conn)
    cur = conn.execute(
        "INSERT INTO checklist_uploads (organization_id, day, slot, zone, photos, note, "
        "created_at, created_by) VALUES (?,?,?,?,?,?,?,?)",
        (org_id, day, slot, zone, json.dumps(photos, ensure_ascii=False), note or "",
         local_now().isoformat(), int(by or 0)))
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    return rid


def get_checklist_uploads(org_ids: List[str], day: str) -> List[Dict[str, Any]]:
    conn = get_db()
    _ensure_checklist(conn)
    if not org_ids:
        conn.close()
        return []
    rows = conn.execute(
        f"SELECT * FROM checklist_uploads WHERE day = ? AND organization_id IN "
        f"({','.join('?' * len(org_ids))}) ORDER BY id DESC", [day] + list(org_ids)).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["photos"] = json.loads(d.get("photos") or "[]")
        except Exception:
            d["photos"] = []
        out.append(d)
    return out


# ── Вердикты по фото чек-листов (когда включён CHECKLIST_AI) ──
def _ensure_checklist_ai(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS checklist_reviews (
        organization_id TEXT NOT NULL,
        day TEXT NOT NULL,
        slot TEXT NOT NULL,
        ok INTEGER,
        issues TEXT,
        reason TEXT,             -- почему проверяли: выборка / опоздание / повтор
        created_at TEXT,
        PRIMARY KEY (organization_id, day, slot)
    );
    CREATE TABLE IF NOT EXISTS checklist_photo_cache (
        photo_hash TEXT PRIMARY KEY,
        verdict TEXT,
        created_at TEXT
    );
    """)
    # Разбор по критериям добавился позже общего вердикта — дотягиваем колонку
    # в уже созданных базах, иначе прод падал бы на первом же сохранении.
    try:
        conn.execute("ALTER TABLE checklist_reviews ADD COLUMN criteria TEXT")
        conn.commit()
    except Exception:
        pass


def merge_checklist_review(org_id: str, day: str, slot: str, ok: bool,
                           issues: List[Dict[str, Any]], reason: str = "",
                           criteria: Optional[List[Dict[str, Any]]] = None):
    """Дописать разбор ОДНОЙ зоны к вердикту слота.

    Зоны сдаются по одной, а вердикт в базе один на слот. Простое сохранение
    затирало бы разбор зала, когда приедет кухня. Сливаем по паре (зона, пункт):
    новый ответ по тому же пункту побеждает, остальное сохраняется. Общий ok —
    произведение: слот чист, только если чиста каждая разобранная зона.
    """
    prev = {}
    for r in get_checklist_reviews([org_id], day):
        if r["slot"] == slot:
            prev = r
            break
    by_key = {(c.get("zone"), c.get("item")): c for c in (prev.get("criteria") or [])}
    for c in criteria or []:
        by_key[(c.get("zone"), c.get("item"))] = c
    merged = list(by_key.values())

    seen, all_issues = set(), []
    for i in (prev.get("issues") or []) + (issues or []):
        key = json.dumps(i, ensure_ascii=False, sort_keys=True) if isinstance(i, dict) else str(i)
        if key not in seen:
            seen.add(key)
            all_issues.append(i)
    # Замечание, которое ушло из свежего разбора зоны, тянуть не надо: если по
    # этой зоне пришёл новый ответ, старые её замечания вытесняются.
    zones_now = {c.get("zone") for c in (criteria or [])}
    if zones_now:
        all_issues = [i for i in all_issues
                      if not (isinstance(i, dict) and i.get("zone") in zones_now)] + list(issues or [])

    total_ok = bool(ok) and not any(c.get("ok") is False for c in merged)
    save_checklist_review(org_id, day, slot, total_ok, all_issues,
                          reason or prev.get("reason") or "", merged)


def save_checklist_review(org_id: str, day: str, slot: str, ok: bool,
                          issues: List[Dict[str, Any]], reason: str = "",
                          criteria: Optional[List[Dict[str, Any]]] = None):
    conn = get_db()
    _ensure_checklist_ai(conn)
    conn.execute("INSERT OR REPLACE INTO checklist_reviews "
                 "(organization_id, day, slot, ok, issues, reason, criteria, created_at) "
                 "VALUES (?,?,?,?,?,?,?,?)",
                 (org_id, day, slot, 1 if ok else 0,
                  json.dumps(issues or [], ensure_ascii=False), reason,
                  json.dumps(criteria or [], ensure_ascii=False),
                  local_now().isoformat()))
    conn.commit()
    conn.close()


def get_checklist_reviews(org_ids: List[str], day: str) -> List[Dict[str, Any]]:
    if not org_ids:
        return []
    conn = get_db()
    _ensure_checklist_ai(conn)
    rows = conn.execute(
        f"SELECT * FROM checklist_reviews WHERE day = ? AND organization_id IN "
        f"({','.join('?' * len(org_ids))})", [day] + list(org_ids)).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        for fld in ("issues", "criteria"):
            try:
                d[fld] = json.loads(d.get(fld) or "[]")
            except Exception:
                d[fld] = []
        out.append(d)
    return out


def photo_verdict_cache(hashes: List[str]) -> Dict[str, Any]:
    """Кэш по хешу кадра: тот же снимок второй раз не оплачиваем."""
    if not hashes:
        return {}
    conn = get_db()
    _ensure_checklist_ai(conn)
    rows = conn.execute(
        f"SELECT photo_hash, verdict FROM checklist_photo_cache WHERE photo_hash IN "
        f"({','.join('?' * len(hashes))})", list(hashes)).fetchall()
    conn.close()
    out = {}
    for r in rows:
        try:
            out[r["photo_hash"]] = json.loads(r["verdict"] or "{}")
        except Exception:
            pass
    return out


def save_photo_verdict(photo_hash: str, verdict: Dict[str, Any]):
    conn = get_db()
    _ensure_checklist_ai(conn)
    conn.execute("INSERT OR REPLACE INTO checklist_photo_cache (photo_hash, verdict, created_at) "
                 "VALUES (?,?,?)", (photo_hash, json.dumps(verdict, ensure_ascii=False),
                                    datetime.now().isoformat()))
    conn.commit()
    conn.close()


# С какого дня чек-листы идут в зачёт. Пусто — НЕ идут вовсе: ни баллов, ни
# штрафов. Это и режим тестирования, и честная раскатка.
CHECKLIST_FROM = (os.getenv("CHECKLIST_FROM") or "").strip()


def checklist_progress(org_ids: List[str], day_from: str, day_to: str,
                       slots_passed_today: int = 4, zones: int = 3,
                       slots_per_day: int = 4,
                       start: Optional[str] = None) -> Dict[str, Dict[str, int]]:
    """Сдачи чек-листов и сколько их ждали, по каждому филиалу.

    Ожидание идёт от ДАТЫ СТАРТА, общей для всей сети, а не от первой сдачи
    филиала. Это принципиально.

    Раньше отсчёт начинался с первой сдачи — по-своему логично («до этого сайта
    у него не было»), но следствие вышло обратное задуманному: первая же
    попытка ВЗВОДИЛА счётчик долга, и филиал сразу отвечал за все слоты с того
    дня. Кто не пробовал ни разу, в выборку не попадал и не получал ничего.
    Попытаться становилось строго хуже, чем не трогать: филиал, сдавший один
    слот, уходил в −29, а восемь молчащих стояли на нуле.

    Теперь спрашивают со всех одинаково и с одного дня:
      • старт не задан → пустой результат: чек-листы не в зачёте (тесты, раскатка);
      • старт задан → ожидание считается для КАЖДОГО филиала, сдавал он хоть раз
        или нет: игнорировать чек-листы больше не выгоднее, чем делать их плохо;
      • у сегодняшнего дня учитываем только прошедшие слоты;
      • сдал больше ожидаемого — не штрафуем.
    """
    start = (start if start is not None else CHECKLIST_FROM) or ""
    if not org_ids or not start:
        return {}
    # старт раньше начала периода не тянем: месяц считается помесячно
    begin = max(start, day_from)
    if begin > day_to:
        return {}

    conn = get_db()
    _ensure_checklist(conn)
    rows = conn.execute(
        f"SELECT organization_id, "
        f"       COUNT(DISTINCT day || '|' || slot || '|' || zone) AS done "
        f"FROM checklist_uploads WHERE day BETWEEN ? AND ? AND organization_id IN "
        f"({','.join('?' * len(org_ids))}) GROUP BY organization_id",
        [begin, day_to] + list(org_ids)).fetchall()
    conn.close()
    done_by = {r["organization_id"]: int(r["done"] or 0) for r in rows}

    full_days = max(0, (datetime.fromisoformat(day_to) - datetime.fromisoformat(begin)).days)
    expect_base = full_days * slots_per_day * zones + max(0, slots_passed_today) * zones

    out: Dict[str, Dict[str, int]] = {}
    for org in org_ids:
        done = done_by.get(org, 0)
        expect = max(expect_base, done)     # сдал больше ожидаемого — не штрафуем
        out[org] = {"done": done, "expect": expect,
                    "missed": -max(0, expect - done)}
    return out


# ── Демо-ссылки: показ продукта новым клиентам ──
# Ссылка живёт вечно, но со счётчиком: продавцу важно знать не «сколько всего
# открытий», а «смотрел ли ЭТОТ клиент и когда». Поэтому ссылка генерится под
# клиента с меткой, а не одна на всех.
def _ensure_demo_links(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS demo_links (
        code TEXT PRIMARY KEY,
        label TEXT,                  -- под кого выдана: «Сеть Ромашка»
        created_at TEXT,
        created_by INTEGER,
        opens INTEGER DEFAULT 0,
        first_open_at TEXT,          -- когда посмотрели впервые
        last_open_at TEXT
    )""")


def add_demo_link(code: str, label: str, created_by: int = 0) -> None:
    conn = get_db()
    _ensure_demo_links(conn)
    conn.execute(
        "INSERT OR IGNORE INTO demo_links (code, label, created_at, created_by, opens) "
        "VALUES (?,?,?,?,0)",
        (code, (label or "")[:120], datetime.now().isoformat(), int(created_by or 0)))
    conn.commit()
    conn.close()


def touch_demo_link(code: str) -> None:
    """Отметить открытие. Первое открытие запоминаем отдельно: «посмотрел через
    час» и «посмотрел через неделю» — разные разговоры с клиентом.

    Строку заводим, если её нет: ссылка действительна по ПОДПИСИ, а не по
    записи в базе, и выпущенная в обход кабинета иначе открывалась бы молча —
    счётчик показывал бы ноль там, где смотрели.
    """
    conn = get_db()
    _ensure_demo_links(conn)
    now = datetime.now().isoformat()
    conn.execute(
        "INSERT OR IGNORE INTO demo_links (code, label, created_at, created_by, opens) "
        "VALUES (?,?,?,0,0)", (code, "", now))
    conn.execute(
        "UPDATE demo_links SET opens = opens + 1, last_open_at = ?, "
        "first_open_at = COALESCE(first_open_at, ?) WHERE code = ?", (now, now, code))
    conn.commit()
    conn.close()


def get_demo_links(created_by: int = 0) -> List[Dict[str, Any]]:
    conn = get_db()
    _ensure_demo_links(conn)
    rows = conn.execute(
        "SELECT * FROM demo_links WHERE created_by = ? ORDER BY created_at DESC LIMIT 200",
        (int(created_by or 0),)).fetchall()
    conn.close()
    return [dict(r) for r in rows]
