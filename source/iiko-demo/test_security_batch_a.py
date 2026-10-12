"""Батч A — тесты безопасности: initData-expiry/HMAC (S4), fail-closed guards (S5),
доступ к weekly-report только оператору + токен fail-closed (S1), salary-write org-скоуп (S2).
Запуск: python3 test_security_batch_a.py
"""
import os, asyncio, tempfile, json, time, hmac, hashlib, urllib.parse
os.environ.setdefault("DB_PATH", tempfile.mktemp(suffix="_sec_test.db"))
os.environ.setdefault("ENCRYPTION_KEY", "")  # для S5-теста переопределим

import database; database.init_db()
import api
from fastapi import HTTPException


def _make_init_data(token, user, auth_date):
    data = {"auth_date": str(auth_date), "user": json.dumps(user, separators=(",", ":"))}
    dcs = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    h = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode({**data, "hash": h})


# ---------- S4: initData ----------
def test_initdata_valid_fresh():
    api.BOT_TOKEN = "test-token"; api.INIT_DATA_MAX_AGE = 86400
    idata = _make_init_data("test-token", {"id": 123, "username": "u"}, int(time.time()))
    u = api.validate_telegram_init_data(idata)
    assert u.get("id") == 123, u
    print("  [OK] свежая валидная initData принимается")


def test_initdata_expired():
    api.BOT_TOKEN = "test-token"; api.INIT_DATA_MAX_AGE = 86400
    idata = _make_init_data("test-token", {"id": 123}, int(time.time()) - 200000)
    try:
        api.validate_telegram_init_data(idata)
    except HTTPException as e:
        assert e.status_code == 401, e.status_code
        print("  [OK] протухшая initData → 401")
        return
    raise AssertionError("ожидался 401 на старый auth_date")


def test_initdata_bad_hash():
    api.BOT_TOKEN = "test-token"; api.INIT_DATA_MAX_AGE = 86400
    idata = _make_init_data("OTHER-token", {"id": 123}, int(time.time()))
    try:
        api.validate_telegram_init_data(idata)
    except HTTPException as e:
        assert e.status_code == 403, e.status_code
        print("  [OK] поддельный hash → 403")
        return
    raise AssertionError("ожидался 403 на чужой hash")


# ---------- S5: fail-closed ----------
def test_failclosed_prod_missing_bot_token():
    saved_prod, saved_tok = api.IS_PROD, api.BOT_TOKEN
    api.IS_PROD = True; api.BOT_TOKEN = ""
    os.environ["ENCRYPTION_KEY"] = "x"
    try:
        api._check_required_secrets()
    except RuntimeError as e:
        print("  [OK] prod без BOT_TOKEN → отказ запуска")
        return
    finally:
        api.IS_PROD, api.BOT_TOKEN = saved_prod, saved_tok
    raise AssertionError("ожидался RuntimeError")


def test_failclosed_dev_ok():
    saved = api.IS_PROD
    api.IS_PROD = False
    try:
        api._check_required_secrets()  # в dev не должно падать
        print("  [OK] dev — запуск без секретов разрешён")
    finally:
        api.IS_PROD = saved


# ---------- S1: weekly-report только оператору ----------
def test_weekly_report_denies_no_auth():
    api.BOT_TOKEN = "test-token"
    api.REPORT_PREVIEW_TOKEN = ""  # токен-доступ выключен
    try:
        asyncio.run(api.weekly_report_preview(send=False, token="anything", x_telegram_init_data=None))
    except HTTPException as e:
        assert e.status_code == 404, e.status_code
        print("  [OK] без авторизации и с выключенным токеном → 404")
        return
    raise AssertionError("ожидался 404")


def test_weekly_report_denies_non_operator():
    api.REPORT_PREVIEW_TOKEN = ""
    real_gcu, real_gubu, real_build = api.get_current_user, api.get_user_by_username, api._build_weekly_report_text
    async def fake_user(x): return {"id": 555, "username": "client"}
    api.get_current_user = fake_user
    api.get_user_by_username = lambda u: {"telegram_id": 999}  # оператор — другой id
    try:
        asyncio.run(api.weekly_report_preview(send=False, token=None, x_telegram_init_data="x"))
    except HTTPException as e:
        assert e.status_code == 404, e.status_code
        print("  [OK] обычный клиент (не оператор) → 404 (нет утечки чужой выручки)")
        return
    finally:
        api.get_current_user, api.get_user_by_username, api._build_weekly_report_text = real_gcu, real_gubu, real_build
    raise AssertionError("ожидался 404 для не-оператора")


def test_weekly_report_allows_operator():
    api.REPORT_PREVIEW_TOKEN = ""
    real_gcu, real_gubu, real_build = api.get_current_user, api.get_user_by_username, api._build_weekly_report_text
    async def fake_user(x): return {"id": 999, "username": "boss"}
    async def fake_build(): return "REPORT"
    api.get_current_user = fake_user
    api.get_user_by_username = lambda u: {"telegram_id": 999}  # совпадает с вызывающим
    api._build_weekly_report_text = fake_build
    try:
        out = asyncio.run(api.weekly_report_preview(send=False, token=None, x_telegram_init_data="x"))
        assert out.get("ok") and out.get("text") == "REPORT", out
        print("  [OK] оператор → отчёт отдаётся")
    finally:
        api.get_current_user, api.get_user_by_username, api._build_weekly_report_text = real_gcu, real_gubu, real_build


# ---------- S2: salary-write org-скоуп ----------
def test_salary_rate_denies_non_owner():
    payload = api.SalaryRateIn(org_id="org-not-mine", employee_id="emp1", rate=5.0)
    try:
        asyncio.run(api.salary_rate(payload, user={"id": 4242}))
    except HTTPException as e:
        assert e.status_code == 404, e.status_code
        print("  [OK] salary/rate для чужого/несуществующего филиала → 404")
        return
    raise AssertionError("ожидался 404")


def test_salary_rate_blocks_demo_org_bypass():
    # Регрессия: раньше org_id="demo-org-1" проскакивал _get_org_id без проверки.
    payload = api.SalaryRateIn(org_id="demo-org-1", employee_id="victim-emp", rate=777.0)
    try:
        asyncio.run(api.salary_rate(payload, user={"id": 4242}))
    except HTTPException as e:
        assert e.status_code == 403, e.status_code
        print("  [OK] salary/rate через demo-org-1 → 403 (обход закрыт)")
        return
    raise AssertionError("ожидался 403 на demo-org-1 bypass")


def test_salary_override_blocks_demo_org_bypass():
    payload = api.SalaryOverrideIn(org_id="demo-org-1", employee_id="victim",
                                   period_key="2026-06", amount=999999.0)
    try:
        asyncio.run(api.salary_override(payload, user={"id": 4242}))
    except HTTPException as e:
        assert e.status_code == 403, e.status_code
        print("  [OK] salary/override через demo-org-1 → 403")
        return
    raise AssertionError("ожидался 403")


def test_salary_blocks_demo_user():
    saved = api.DEMO_ONLY
    api.DEMO_ONLY = True  # на демо-боте каждый юзер демо → запись зарплат запрещена
    try:
        payload = api.SalaryRateIn(org_id="whatever", employee_id="e", rate=1.0)
        try:
            asyncio.run(api.salary_rate(payload, user={"id": 4242}))
        except HTTPException as e:
            assert e.status_code == 403, e.status_code
            print("  [OK] demo-юзер не может писать зарплаты → 403")
            return
        raise AssertionError("ожидался 403 для демо-юзера")
    finally:
        api.DEMO_ONLY = saved


def test_iiko_unavailable_no_fake_data():
    # A1: при сбое iiko реальному клиенту — флаг ошибки, а НЕ фейковые DEMO-данные.
    r = api._iiko_unavailable()
    assert r.get("_iiko_error"), r
    for fake_key in ("revenue_today", "employees", "channels", "fact_pct", "categories"):
        assert fake_key not in r, f"в ответе ошибки не должно быть {fake_key}"
    print("  [OK] _iiko_unavailable: флаг ошибки, без фейковых метрик/ФИО")


if __name__ == "__main__":
    tests = [test_initdata_valid_fresh, test_initdata_expired, test_initdata_bad_hash,
             test_failclosed_prod_missing_bot_token, test_failclosed_dev_ok,
             test_weekly_report_denies_no_auth, test_weekly_report_denies_non_operator,
             test_weekly_report_allows_operator, test_salary_rate_denies_non_owner,
             test_salary_rate_blocks_demo_org_bypass, test_salary_override_blocks_demo_org_bypass,
             test_salary_blocks_demo_user, test_iiko_unavailable_no_fake_data]
    failed = 0
    for t in tests:
        print(f"\n>>> {t.__name__}")
        try:
            t()
        except AssertionError as e:
            failed += 1; print(f"  [FAIL] {e}")
        except Exception as e:
            failed += 1; print(f"  [ERROR] {type(e).__name__}: {e}")
    try:
        os.remove(os.environ["DB_PATH"])
    except OSError:
        pass
    print(f"\n{'='*50}\n{'ВСЕ ТЕСТЫ ОК' if not failed else f'ПРОВАЛЕНО: {failed}'}")
