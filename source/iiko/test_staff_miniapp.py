"""Мини-апп смен: личная ссылка сотрудника, гео и его лента сообщений.

Главное, что здесь проверяется, — сотрудник НЕ получает прав управляющего и не
видит чужого. Вход сотрудника сделан отдельным от get_current_user именно
поэтому: ветка «роль + филиал» там отдаёт дашборд филиала целиком.

Запуск:  python3 test_staff_miniapp.py
"""
import os
import tempfile
import warnings

warnings.filterwarnings("ignore")
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="staff-app-"), "t.db")
os.environ.pop("TELEGRAM_BOT_TOKEN", None)   # dev-режим: личность берётся из DEV_TID

import database as D          # noqa: E402
import geo as G               # noqa: E402
import api                    # noqa: E402
from fastapi.testclient import TestClient   # noqa: E402

# database.DB_PATH и api.BOT_TOKEN читаются ОДИН РАЗ при импорте модуля, а pytest
# импортирует все тест-файлы до запуска. Значит, наша база зависит от того, кто
# импортировался первым, — а если прибить их прямо здесь, мы сломаем чужие тесты
# (так и вышло с test_site_auth). Поэтому подменяем ТОЛЬКО на время своих тестов
# и возвращаем как было.
MY_DB = os.environ["DB_PATH"]
_saved: dict = {}


def _enter():
    _saved["db"], _saved["tok"] = D.DB_PATH, api.BOT_TOKEN
    D.DB_PATH, api.BOT_TOKEN = MY_DB, None      # dev-режим: личность из DEV_TID


def _leave():
    D.DB_PATH, api.BOT_TOKEN = _saved["db"], _saved["tok"]


try:
    import pytest

    @pytest.fixture(autouse=True)
    def _isolate():
        _enter()
        yield
        _leave()
except ImportError:      # запуск файлом, без pytest
    pass

api.get_client = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("network blocked"))
client = TestClient(api.app, raise_server_exceptions=False)

OWNER = 900401
ORG, OTHER = "org-mine", "org-other"
KIM, VOLKOV = "e-kim", "e-volkov"
TID_KIM, TID_VOLKOV = 111, 222


def _seed():
    D.init_db()
    # Таблицы смен и сообщений создаются лениво, при первом обращении. Дёргаем
    # их чтением, иначе чистка ниже падает на «no such table» и роняет всё.
    D.get_staff_schedule(ORG, "2000-01-01", "2000-01-02")
    D.get_branch_notices([ORG], 0)
    conn = D.get_db()
    try:
        conn.execute("DELETE FROM restaurants WHERE telegram_id = ?", (OWNER,))
        for t in ("staff_access", "branch_notices", "branch_notice_reads",
                  "staff_attendance", "staff_schedule", "staff_shift_notified",
                  "branch_join", "staff_payouts"):
            conn.execute(f"DELETE FROM {t}")
        conn.commit()
    finally:
        conn.close()
    D.add_restaurant(OWNER, "login", ORG, "Астана, Мой филиал", iiko_server="", api_password="")
    D.add_restaurant(OWNER, "login", OTHER, "Астана, Чужой", iiko_server="", api_password="")


def _as(code, tid):
    os.environ["DEV_TID"] = str(tid)
    return {"X-Staff-Code": code}


# ── Личная ссылка ──

def test_link_binds_to_the_first_person_who_opens_it():
    _seed()
    code = D.issue_staff_access(ORG, KIM, "Ким")
    assert D.bind_staff_access(code, TID_KIM) is True
    assert D.bind_staff_access(code, TID_KIM) is True, "свой же телефон должен пускаться"
    print("  [OK] первый открывший привязался, он же заходит повторно")


def test_forwarded_link_does_not_work_for_a_colleague():
    """Ссылку пересылают в общий чат. Без привязки товарищ отмечал бы смены за
    человека — и опоздания превратились бы в фикцию."""
    _seed()
    code = D.issue_staff_access(ORG, KIM, "Ким")
    D.bind_staff_access(code, TID_KIM)
    assert D.bind_staff_access(code, TID_VOLKOV) is False
    r = client.get("/api/me", headers=_as(code, TID_VOLKOV))
    assert r.status_code == 403, r.text[:200]
    print("  [OK] чужой телефон получает 403, а не чужую смену")


def test_reissue_frees_the_link_for_a_new_phone():
    _seed()
    code1 = D.issue_staff_access(ORG, KIM, "Ким")
    D.bind_staff_access(code1, TID_KIM)
    code2 = D.issue_staff_access(ORG, KIM, "Ким")
    assert code2 != code1
    assert D.bind_staff_access(code2, TID_VOLKOV) is True
    assert D.staff_access_by_code(code1) is None, "старая ссылка обязана умереть"
    print("  [OK] перевыпуск сбрасывает привязку и убивает прежнюю ссылку")


def test_revoked_link_stops_working():
    _seed()
    code = D.issue_staff_access(ORG, KIM, "Ким")
    D.revoke_staff_access(ORG, KIM)
    assert D.staff_access_by_code(code) is None
    assert client.get("/api/me", headers=_as(code, TID_KIM)).status_code == 401
    print("  [OK] отозванная ссылка больше не пускает")


def test_staff_code_does_not_open_the_branch_dashboard():
    """Ради этого вход сотрудника и сделан отдельным.

    Отказ бывает разного цвета: 401 (личности нет), 403 (не твой филиал), 404
    (у этой личности нет такого филиала). Важно, что данных не отдают ни в одном
    случае: код сотрудника — не пропуск в кабинет.
    """
    _seed()
    code = D.issue_staff_access(ORG, KIM, "Ким")
    for url in (f"/api/dashboard/{ORG}", f"/api/schedule/{ORG}?from=2026-09-01&to=2026-09-07",
                f"/api/salary/{ORG}?from=2026-09-01&to=2026-09-07",
                f"/api/staff-access/{ORG}", f"/api/staff-notice/{ORG}"):
        r = client.get(url, headers=_as(code, TID_KIM))
        assert r.status_code in (401, 403, 404), f"{url} -> {r.status_code} {r.text[:120]}"
    print("  [OK] дашборд, расписание, зарплата, выдача ссылок и рассылка закрыты")


def test_staff_code_is_not_a_site_token():
    """Прямая попытка эскалации: подсунуть код сотрудника туда, где ждут ссылку
    филиала. Подпись не сойдётся — иначе сотрудник стал бы управляющим."""
    _seed()
    code = D.issue_staff_access(ORG, KIM, "Ким")
    r = client.get(f"/api/dashboard/{ORG}", headers={"X-Site-Token": code})
    assert r.status_code == 401, f"{r.status_code} {r.text[:160]}"
    print("  [OK] код сотрудника в роли ссылки филиала даёт 401")


# ── Гео ──

def test_geo_distance_and_radius():
    b = {"lat": 51.128, "lon": 71.430, "radius": 250}
    assert G.within(b, 51.128, 71.430) is True
    assert round(G.distance_m(51.128, 71.430, 51.146, 71.430)) == 2002
    assert G.within(b, 51.146, 71.430) is False
    print("  [OK] 0 м — на месте, 2 км — нет")


def test_no_geo_is_not_the_same_as_wrong_place():
    """None и False должны различаться: «не дал геопозицию» и «пришёл не туда» —
    разные разговоры с человеком."""
    b = {"lat": 51.128, "lon": 71.430, "radius": 250}
    assert G.within(b, None, None) is None
    assert G.within(None, 51.128, 71.430) is None
    print("  [OK] нечем проверить — None, а не False")


# ── Открытие смены ──

def test_open_shift_marks_geo_and_is_idempotent():
    """Повторное нажатие не должно стирать время прихода: иначе опоздавший
    «переоткрыл» бы смену попозже и опоздание исчезло."""
    _seed()
    D.set_branch_geo(ORG, 51.128, 71.430, 250)
    code = D.issue_staff_access(ORG, KIM, "Ким")
    h = _as(code, TID_KIM)

    r1 = client.post("/api/me/open-shift", json={"lat": 51.128, "lon": 71.430}, headers=h)
    assert r1.status_code == 200 and r1.json()["geo_ok"] == 1, r1.text[:200]
    first = r1.json()["start"]

    r2 = client.post("/api/me/open-shift", json={"lat": 51.9, "lon": 71.9}, headers=h)
    assert r2.json()["already"] is True and r2.json()["start"] == first, r2.text[:200]
    print(f"  [OK] смена открыта в {first} с гео, повтор её не переписал")


def test_shift_opens_even_outside_the_radius_but_is_flagged():
    """Решение владельца: не блокируем, помечаем."""
    _seed()
    D.set_branch_geo(ORG, 51.128, 71.430, 250)
    code = D.issue_staff_access(ORG, VOLKOV, "Волков")
    r = client.post("/api/me/open-shift", json={"lat": 51.9, "lon": 71.9},
                    headers=_as(code, TID_VOLKOV))
    assert r.status_code == 200 and r.json()["geo_ok"] == 0, r.text[:200]
    rows = D.get_staff_attendance(ORG, r.json().get("date", ""), "9999-12-31")
    print("  [OK] вне радиуса смена открылась, но помечена geo_ok=0")


def test_week_shows_my_shifts_and_pay_only():
    _seed()
    code = D.issue_staff_access(ORG, KIM, "Ким")
    r = client.get("/api/me/week", headers=_as(code, TID_KIM))
    assert r.status_code == 200, r.text[:200]
    d = r.json()
    day = d["days"][0]["date"]
    D.set_staff_shift(ORG, KIM, day, "10:00", "18:00", 12000)
    D.set_staff_shift(ORG, VOLKOV, day, "10:00", "18:00", 99000)
    d = client.get("/api/me/week", headers=_as(code, TID_KIM)).json()
    assert len(d["days"]) == 7 and d["pay_total"] == 12000, d
    print("  [OK] неделя из 7 дней, в сумме только своя оплата — 12 000, не 111 000")


# ── Сообщения ──

def test_employee_sees_broadcast_and_own_message_but_not_a_colleagues():
    _seed()
    D.add_branch_notice("Завтра поставка в 8", OWNER, ORG, audience="staff")
    D.add_branch_notice("Ким, забери форму", OWNER, ORG, audience="staff", to_employee_id=KIM)
    D.add_branch_notice("Волков, зайди ко мне", OWNER, ORG, audience="staff", to_employee_id=VOLKOV)
    D.add_branch_notice("Чужой филиал", OWNER, OTHER, audience="staff")
    texts = [n["text"] for n in D.get_staff_notices(ORG, KIM, TID_KIM)]
    assert "Завтра поставка в 8" in texts and "Ким, забери форму" in texts, texts
    assert "Волков, зайди ко мне" not in texts, "чужое личное сообщение утекло"
    assert "Чужой филиал" not in texts, "сообщение чужого филиала утекло"
    print("  [OK] видно объявление и своё личное; чужое личное и чужой филиал — нет")


def test_staff_messages_do_not_leak_into_the_managers_feed():
    """Регрессия: лента управляющих не должна наполниться перепиской с бригадой."""
    _seed()
    D.add_branch_notice("Сообщение владельца управляющим", OWNER, ORG)
    D.add_branch_notice("Завтра поставка в 8", OWNER, ORG, audience="staff")
    texts = [n["text"] for n in D.get_branch_notices([ORG], OWNER)]
    assert "Сообщение владельца управляющим" in texts, texts
    assert "Завтра поставка в 8" not in texts, texts
    print("  [OK] у управляющих своя лента, у смены своя")


def test_employee_cannot_mark_a_colleagues_message_read():
    _seed()
    nid = D.add_branch_notice("Волков, зайди", OWNER, ORG, audience="staff", to_employee_id=VOLKOV)
    assert D.staff_notice_is_for(nid, ORG, VOLKOV) is True
    assert D.staff_notice_is_for(nid, ORG, KIM) is False
    code = D.issue_staff_access(ORG, KIM, "Ким")
    r = client.post(f"/api/me/notices/{nid}/read", headers=_as(code, TID_KIM))
    assert r.status_code == 404, r.text[:200]
    print("  [OK] чужое сообщение не пометить прочитанным — счётчик не соврёт")


# ── Сквозной круг: сайт ↔ мини-апп ──

def test_shift_written_on_the_site_shows_up_in_the_mini_app():
    """Управляющий ставит смену и оплату в расписании на сайте — сотрудник видит
    ровно их. Таблица одна (staff_schedule), копий и синхронизации нет."""
    _seed()
    code = D.issue_staff_access(ORG, KIM, "Ким")
    day = client.get("/api/me/week", headers=_as(code, TID_KIM)).json()["days"][2]["date"]

    # сайт: управляющий пишет смену через ту же ручку, что и экран «Расписание»
    os.environ["DEV_TID"] = str(OWNER)
    r = client.post(f"/api/schedule/{ORG}",
                    json={"employee_id": KIM, "date": day, "start": "10:00",
                          "end": "18:00", "pay": 12000})
    assert r.status_code == 200, r.text[:200]

    # мини-апп: сотрудник видит её же
    d = client.get("/api/me/week", headers=_as(code, TID_KIM)).json()
    mine = next(x for x in d["days"] if x["date"] == day)
    assert (mine["start"], mine["end"], mine["pay"]) == ("10:00", "18:00", 12000), mine
    assert d["pay_total"] == 12000, d
    print("  [OK] смена 10:00–18:00 за 12 000 с сайта видна сотруднику")


def test_opened_shift_comes_back_to_the_managers_grid():
    """Обратный ход: сотрудник открыл смену в мини-аппе — отметка появляется там,
    откуда её читает сетка расписания, вместе с признаком «был ли на месте»."""
    _seed()
    D.set_branch_geo(ORG, 51.128, 71.430, 250)
    code = D.issue_staff_access(ORG, KIM, "Ким")
    opened = client.post("/api/me/open-shift", json={"lat": 51.9, "lon": 71.9},
                         headers=_as(code, TID_KIM)).json()

    today = opened_date = __import__("datetime").date.today().isoformat()
    os.environ["DEV_TID"] = str(OWNER)
    rows = client.get(f"/api/attendance/{ORG}?from={today}&to={opened_date}").json()["rows"]
    mine = [r for r in rows if r["employee_id"] == KIM]
    assert len(mine) == 1, rows
    assert mine[0]["start"] == opened["start"], mine
    assert mine[0]["geo_ok"] == 0, "управляющий должен видеть, что открыли не на месте"
    print(f"  [OK] отметка {opened['start']} с пометкой «вне заведения» видна управляющему")


# ── Напоминание «завтра смена» ──

def _run_reminders(day):
    """Отправку подменяем: наружу в телеграм тесты ходить не должны."""
    import asyncio
    outbox = []

    async def fake_send(chat_id, text, reply_markup=None):
        outbox.append({"chat": chat_id, "text": text, "markup": reply_markup})
        return 1

    real, api._tg_send = api._tg_send, fake_send
    try:
        sent = asyncio.run(api._shift_reminders_round(day))
    finally:
        api._tg_send = real
    return sent, outbox


def test_reminder_goes_only_to_those_who_opened_their_link():
    """Кому писать, мы знаем только из привязки. Ким ссылку открыл, Волкову её
    выдали, но он не заходил — ему писать некуда, и это не ошибка."""
    _seed()
    tomorrow = (__import__("datetime").date.today() + __import__("datetime").timedelta(days=1)).isoformat()
    D.set_staff_shift(ORG, KIM, tomorrow, "10:00", "18:00", 12000)
    D.set_staff_shift(ORG, VOLKOV, tomorrow, "12:00", "20:00")
    D.bind_staff_access(D.issue_staff_access(ORG, KIM, "Ким"), TID_KIM)
    D.issue_staff_access(ORG, VOLKOV, "Волков")        # выдана, но не открыта

    sent, out = _run_reminders(tomorrow)
    assert sent == 1 and len(out) == 1, out
    assert out[0]["chat"] == TID_KIM, out
    assert "10:00–18:00" in out[0]["text"], out[0]["text"]
    print("  [OK] написали одному Киму, Волкову без привязки — некуда")


def test_reminder_does_not_leak_the_pay_into_the_notification():
    """Уведомление видно на заблокированном экране — зарплате там не место."""
    _seed()
    tomorrow = (__import__("datetime").date.today() + __import__("datetime").timedelta(days=1)).isoformat()
    D.set_staff_shift(ORG, KIM, tomorrow, "10:00", "18:00", 12000)
    D.bind_staff_access(D.issue_staff_access(ORG, KIM, "Ким"), TID_KIM)
    _, out = _run_reminders(tomorrow)
    assert "12000" not in out[0]["text"] and "12 000" not in out[0]["text"], out[0]["text"]
    print("  [OK] сумма в текст не попала — она внутри приложения")


def test_reminder_is_not_sent_twice():
    """Перезапуск сервиса вечером не должен рассылать по второму разу."""
    _seed()
    tomorrow = (__import__("datetime").date.today() + __import__("datetime").timedelta(days=1)).isoformat()
    D.set_staff_shift(ORG, KIM, tomorrow, "10:00", "18:00")
    D.bind_staff_access(D.issue_staff_access(ORG, KIM, "Ким"), TID_KIM)
    assert _run_reminders(tomorrow)[0] == 1
    assert _run_reminders(tomorrow)[0] == 0, "напомнили дважды"
    print("  [OK] второй проход того же дня молчит")


def test_reminder_takes_only_the_asked_day():
    _seed()
    import datetime as _dt
    today = _dt.date.today().isoformat()
    tomorrow = (_dt.date.today() + _dt.timedelta(days=1)).isoformat()
    D.set_staff_shift(ORG, KIM, today, "09:00", "17:00")
    D.set_staff_shift(ORG, KIM, tomorrow, "10:00", "18:00")
    D.bind_staff_access(D.issue_staff_access(ORG, KIM, "Ким"), TID_KIM)
    _, out = _run_reminders(tomorrow)
    assert len(out) == 1 and "10:00–18:00" in out[0]["text"], out
    print("  [OK] взяли завтрашнюю смену, сегодняшнюю не трогали")


# ── Подключение по QR филиала ──

class _FakeIiko:
    """Имена сотрудников без похода в iiko: тесты наружу не ходят."""
    async def get_employees_map(self):
        return {KIM: "Ким Виктория", VOLKOV: "Волков Артём", "e-third": "Нуров Асан"}


def _with_iiko(fn):
    real, api.get_client = api.get_client, lambda *a, **k: _FakeIiko()
    try:
        return fn()
    finally:
        api.get_client = real


def test_join_code_is_not_a_branch_link():
    """QR из подсобки не должен оказаться пропуском управляющего."""
    import mountain_routes as M
    _seed()
    code = D.get_or_create_join_code(ORG)
    assert M.org_from_token(code) is None, "код подключения прочитался как ссылка филиала"
    assert D.org_by_join_code(M.token_for_org(ORG)) is None, "ссылка филиала прошла как код"
    assert D.org_by_join_code(code) == ORG
    print("  [OK] код подключения и ссылка филиала не взаимозаменяемы")


def test_rotating_the_code_kills_the_old_qr():
    """QR сфотографировали — управляющий печатает новый, старый умирает."""
    _seed()
    old = D.get_or_create_join_code(ORG)
    new = D.rotate_join_code(ORG)
    assert new != old
    assert D.org_by_join_code(old) is None, "старый QR всё ещё работает"
    assert D.org_by_join_code(new) == ORG
    print("  [OK] перевыпуск QR убивает прежний код")


def test_join_code_is_safe_for_a_telegram_deep_link():
    """Код едет в t.me/...?start=join_<код>: там можно только буквы, цифры,
    дефис и подчёркивание, и не длиннее 64 символов вместе с префиксом."""
    import re
    _seed()
    code = D.get_or_create_join_code(ORG)
    assert re.fullmatch(r"[A-Za-z0-9_-]+", code), code
    assert len(f"join_{code}") <= 64, code
    print(f"  [OK] код {code} проходит в deep-link телеграма")


def test_bad_join_code_is_not_found():
    _seed()
    os.environ["DEV_TID"] = str(TID_KIM)
    assert client.get("/api/join/nosuchcode").status_code == 404
    print("  [OK] несуществующий код — 404")


def test_scanning_lets_you_pick_yourself_and_binds():
    _seed()
    t = D.get_or_create_join_code(ORG)
    os.environ["DEV_TID"] = str(TID_KIM)

    d = _with_iiko(lambda: client.get(f"/api/join/{t}").json())
    assert d["already"] is False, d
    assert {e["id"] for e in d["employees"]} == {KIM, VOLKOV, "e-third"}, d

    r = client.post(f"/api/join/{t}", json={"employee_id": KIM, "name": "Ким Виктория"})
    assert r.status_code == 200, r.text[:200]
    code = r.json()["code"]
    me = client.get("/api/me", headers=_as(code, TID_KIM))
    assert me.status_code == 200 and me.json()["employee_id"] == KIM, me.text[:200]
    print("  [OK] выбрал себя — привязался, личная ссылка сразу работает")


def test_taken_person_is_not_offered_and_cannot_be_claimed():
    """Второй сканирующий не должен занять чужое место."""
    _seed()
    t = D.get_or_create_join_code(ORG)
    D.bind_staff_access(D.issue_staff_access(ORG, KIM, "Ким"), TID_KIM)

    os.environ["DEV_TID"] = str(TID_VOLKOV)
    d = _with_iiko(lambda: client.get(f"/api/join/{t}").json())
    assert KIM not in {e["id"] for e in d["employees"]}, "занятого предложили в списке"

    r = client.post(f"/api/join/{t}", json={"employee_id": KIM})
    assert r.status_code == 409, f"{r.status_code} {r.text[:160]}"
    print("  [OK] занятого нет в списке, и напрямую его не забрать")


def test_second_scan_from_the_same_phone_returns_the_same_link():
    """Повторный скан не должен плодить вторую привязку тому же человеку."""
    _seed()
    t = D.get_or_create_join_code(ORG)
    os.environ["DEV_TID"] = str(TID_KIM)
    code1 = client.post(f"/api/join/{t}", json={"employee_id": KIM}).json()["code"]
    d = _with_iiko(lambda: client.get(f"/api/join/{t}").json())
    assert d["already"] is True and d["code"] == code1, d
    assert client.post(f"/api/join/{t}", json={"employee_id": VOLKOV}).json()["code"] == code1, \
        "со второго скана человек сменил личность"
    print("  [OK] второй скан возвращает ту же ссылку, личность не меняется")


# ── Перевыпуск QR и отвязка (сторона управляющего) ──

def test_manager_prints_a_new_qr_and_the_old_one_dies():
    """QR сфотографировали или он уехал с уволенным управляющим. Перепечатать
    его должны из кабинета, не дожидаясь разработчика."""
    _seed()
    old = D.get_or_create_join_code(ORG)
    os.environ["DEV_TID"] = str(OWNER)

    r = client.post(f"/api/staff-access/{ORG}/rotate")
    assert r.status_code == 200, r.text[:200]
    url = r.json()["join_url"]
    assert url.startswith("https://t.me/") and "?start=join_" in url, url
    new_code = url.split("?start=join_", 1)[1]
    assert new_code != old

    os.environ["DEV_TID"] = str(TID_KIM)
    assert client.get(f"/api/join/{old}").status_code == 404, "старый QR всё ещё пускает"
    assert D.org_by_join_code(new_code) == ORG
    print("  [OK] управляющий печатает новый QR, прежний умирает")


def test_reprinting_the_qr_does_not_unbind_anyone():
    """Перевыпуск — про будущих новичков. Кто уже подключился, ходит по своей
    ссылке и не должен ничего заметить."""
    _seed()
    code = D.issue_staff_access(ORG, KIM, "Ким")
    D.bind_staff_access(code, TID_KIM)

    os.environ["DEV_TID"] = str(OWNER)
    assert client.post(f"/api/staff-access/{ORG}/rotate").status_code == 200

    me = client.get("/api/me", headers=_as(code, TID_KIM))
    assert me.status_code == 200 and me.json()["employee_id"] == KIM, me.text[:200]
    print("  [OK] новый QR не выбил из мини-аппа тех, кто уже привязан")


def test_a_stranger_cannot_reprint_the_branch_qr():
    """Иначе чужой человек одним запросом обнулял бы QR в подсобке: висящий на
    стене код мёртв, а сотрудники молча перестают отмечаться."""
    _seed()
    old = D.get_or_create_join_code(ORG)
    os.environ["DEV_TID"] = str(TID_VOLKOV)          # не владелец филиала
    r = client.post(f"/api/staff-access/{ORG}/rotate")
    assert r.status_code in (401, 403, 404), f"{r.status_code} {r.text[:160]}"
    assert D.org_by_join_code(old) == ORG, "чужой перевыпустил QR филиала"
    print("  [OK] перевыпуск QR закрыт для чужих")


def test_unbinding_frees_the_place_for_a_new_scan():
    """Человек выбрал не себя — это единственная ошибка, которую даёт сделать
    QR. Управляющий отвязывает, и место снова свободно."""
    _seed()
    t = D.get_or_create_join_code(ORG)
    os.environ["DEV_TID"] = str(TID_KIM)
    wrong = client.post(f"/api/join/{t}", json={"employee_id": VOLKOV}).json()["code"]

    os.environ["DEV_TID"] = str(OWNER)
    r = client.post(f"/api/staff-access/{ORG}/revoke", json={"employee_id": VOLKOV})
    assert r.status_code == 200, r.text[:200]

    assert client.get("/api/me", headers=_as(wrong, TID_KIM)).status_code in (401, 403, 404), \
        "отвязанная ссылка всё ещё пускает"

    os.environ["DEV_TID"] = str(TID_KIM)
    d = _with_iiko(lambda: client.get(f"/api/join/{t}").json())
    assert d["already"] is False, "телефон остался привязанным после отвязки"
    assert VOLKOV in {e["id"] for e in d["employees"]}, "отвязанного не вернули в список"
    assert client.post(f"/api/join/{t}", json={"employee_id": KIM}).status_code == 200
    print("  [OK] отвязка убивает ссылку и возвращает человека в список выбора")


def test_a_stranger_cannot_unbind_an_employee():
    _seed()
    code = D.issue_staff_access(ORG, KIM, "Ким")
    D.bind_staff_access(code, TID_KIM)
    os.environ["DEV_TID"] = str(TID_VOLKOV)
    r = client.post(f"/api/staff-access/{ORG}/revoke", json={"employee_id": KIM})
    assert r.status_code in (401, 403, 404), f"{r.status_code} {r.text[:160]}"
    assert client.get("/api/me", headers=_as(code, TID_KIM)).status_code == 200, \
        "чужой отвязал сотрудника"
    print("  [OK] отвязка закрыта для чужих")


# ── Точка филиала из его названия ──

def test_branch_name_is_offered_as_the_address():
    """Филиалы в iiko называются адресом — панель подставляет его сама."""
    _seed()
    os.environ["DEV_TID"] = str(OWNER)
    d = client.get(f"/api/branch-geo/{ORG}").json()
    assert d["name"] == "Астана, Мой филиал", d
    assert d["geo"] is None, d
    print(f"  [OK] панели отдано название «{d['name']}» для подстановки")


def test_missing_house_number_is_reported_not_guessed():
    """Без номера дома геокодер поставит точку в центр улицы, и радиус
    промахнётся. Честно предупреждаем, а не ставим молча мимо."""
    _seed()
    conn = D.get_db()
    conn.execute("UPDATE restaurants SET organization_name = ? WHERE organization_id = ?",
                 ("Астана, проспект Кабанбай Батыра", ORG))
    conn.commit(); conn.close()
    os.environ["DEV_TID"] = str(OWNER)
    assert client.get(f"/api/branch-geo/{ORG}").json()["has_house_number"] is False

    conn = D.get_db()
    conn.execute("UPDATE restaurants SET organization_name = ? WHERE organization_id = ?",
                 ("Астана, проспект Кабанбай Батыра 13 (EXPO)", ORG))
    conn.commit(); conn.close()
    assert client.get(f"/api/branch-geo/{ORG}").json()["has_house_number"] is True
    print("  [OK] «без дома» отличаем от «дом есть», скобки в счёт не идут")


# ── Зарплата: начислено, выдано, остаток ──

def _month():
    import datetime as _dt
    return _dt.date.today().replace(day=1).isoformat()


def test_only_shifts_with_pay_are_earned():
    """Смена без проставленной оплаты — «ещё не решили, сколько стоит». Считать
    её нулём значило бы занизить долг перед человеком."""
    _seed()
    d = _month()
    D.set_staff_shift(ORG, KIM, d, "10:00", "18:00", 12000)
    D.set_staff_shift(ORG, KIM, d[:8] + "02", "10:00", "18:00")          # без оплаты
    got = D.staff_pay_summary(ORG, d, d[:8] + "28")[KIM]
    assert got["earned"] == 12000 and got["shifts"] == 1, got
    print("  [OK] начислено 12 000 по одной смене, пустая в счёт не пошла")


def test_payout_reduces_the_balance():
    _seed()
    d = _month()
    D.set_staff_shift(ORG, KIM, d, "10:00", "18:00", 12000)
    D.add_staff_payout(ORG, KIM, d, 5000, "аванс", OWNER)
    got = D.staff_pay_summary(ORG, d, d[:8] + "28")[KIM]
    assert (got["earned"], got["paid"], got["balance"]) == (12000, 5000, 7000), got
    print("  [OK] 12 000 начислено − 5 000 выдано = 7 000 остаток")


def test_deleting_a_payout_returns_the_debt():
    """Опечатку исправляют удалением, а не минусом: минус в истории выглядел бы
    как штраф сотруднику."""
    _seed()
    d = _month()
    D.set_staff_shift(ORG, KIM, d, "10:00", "18:00", 12000)
    pid = D.add_staff_payout(ORG, KIM, d, 5000, "ошибка", OWNER)
    assert D.delete_staff_payout(ORG, pid) is True
    got = D.staff_pay_summary(ORG, d, d[:8] + "28")[KIM]
    assert got["paid"] == 0 and got["balance"] == 12000, got
    print("  [OK] удалённая выплата вернула долг целиком")


def test_a_payout_of_another_branch_cannot_be_deleted_by_id():
    """id угадать легко — без проверки филиала чужую выплату стёрли бы по номеру."""
    _seed()
    d = _month()
    pid = D.add_staff_payout(OTHER, KIM, d, 5000, "чужая", OWNER)
    assert D.delete_staff_payout(ORG, pid) is False, "стёрли выплату чужого филиала"
    assert len(D.get_staff_payouts(OTHER, d, d[:8] + "28")) == 1
    print("  [OK] чужую выплату по номеру не удалить")


def test_employee_sees_own_settlement_only():
    _seed()
    d = _month()
    D.set_staff_shift(ORG, KIM, d, "10:00", "18:00", 12000)
    D.set_staff_shift(ORG, VOLKOV, d, "10:00", "18:00", 99000)
    D.add_staff_payout(ORG, KIM, d, 5000, "аванс", OWNER)
    D.add_staff_payout(ORG, VOLKOV, d, 40000, "аванс", OWNER)
    code = D.issue_staff_access(ORG, KIM, "Ким")
    r = client.get("/api/me/pay", headers=_as(code, TID_KIM))
    assert r.status_code == 200, r.text[:200]
    d2 = r.json()
    assert (d2["earned"], d2["paid"], d2["balance"]) == (12000, 5000, 7000), d2
    assert len(d2["payouts"]) == 1 and d2["payouts"][0]["comment"] == "аванс", d2
    print("  [OK] сотрудник видит свои 12 000 / 5 000 / 7 000 и одну свою выплату")


def test_manager_cannot_record_a_zero_or_negative_payout():
    """Удержание вводить так нельзя: для исправления есть удаление."""
    _seed()
    os.environ["DEV_TID"] = str(OWNER)
    for bad in (0, -100):
        r = client.post(f"/api/payouts/{ORG}", json={"employee_id": KIM, "amount": bad})
        assert r.status_code == 400, f"{bad} -> {r.status_code}"
    print("  [OK] ноль и минус отвергаются")


def test_manager_sees_who_is_owed_first():
    _seed()
    d = _month()
    D.set_staff_shift(ORG, KIM, d, "10:00", "18:00", 12000)
    D.set_staff_shift(ORG, VOLKOV, d, "10:00", "18:00", 30000)
    D.add_staff_payout(ORG, KIM, d, 12000, "полностью", OWNER)
    os.environ["DEV_TID"] = str(OWNER)
    body = client.get(f"/api/payouts/{ORG}").json()
    assert body["rows"][0]["employee_id"] == VOLKOV, body["rows"]
    assert body["total_balance"] == 30000, body
    print("  [OK] сверху тот, кому должны 30 000; рассчитанный — ниже")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        print(f"\n>>> {t.__name__}")
        try:
            _enter()
            t()
        except AssertionError as e:
            failed += 1; print(f"  [FAIL] {e}")
        except Exception as e:
            failed += 1; print(f"  [ERROR] {type(e).__name__}: {e}")
        finally:
            _leave()
    print(f"\n{'='*50}\n{'ВСЕ ТЕСТЫ ОК' if not failed else f'ПРОВАЛЕНО: {failed} из {len(tests)}'}")
    raise SystemExit(1 if failed else 0)
