"""Тест прокси /api/chaspik: гейт по tg-id, 503 без конфигурации,
прозрачная передача с заголовком X-API-Key и org_id из env.
Запуск: python3 test_chaspik_proxy.py
"""
import os, asyncio, tempfile
os.environ.setdefault("DB_PATH", tempfile.mktemp(suffix="_chaspik_test.db"))
# Бэкенд читает эти env при импорте — задаём ДО import api.
os.environ["ANALYTICS_API_KEY"] = "secret-test-key"
os.environ["CHASPIK_ORG_ID"] = "org-alieva"
os.environ["CHASPIK_BASE_URL"] = "https://saya-kds.example"

import database; database.init_db()
import api
from fastapi import HTTPException

_REAL_ASYNC_CLIENT = api.httpx.AsyncClient

ALLOWED = {"id": 355897224, "username": "boss", "first_name": "B"}
OTHER = {"id": 111, "username": "x", "first_name": "X"}


class FakeResp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.text = text
    def json(self):
        return self._payload


class FakeClient:
    captured = {}
    next_resp = None
    def __init__(self, *a, **k): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def get(self, url, params=None, headers=None):
        FakeClient.captured = {"url": url, "params": dict(params or {}),
                               "headers": dict(headers or {})}
        return FakeClient.next_resp or FakeResp(
            200, {"ok": True, "org_id": (params or {}).get("org_id")})


def _call(**kw):
    return asyncio.run(api.chaspik_export(**kw))


def test_forbidden_for_other_id():
    try:
        _call(from_=None, to=None, sla_minutes=None, user=OTHER)
    except HTTPException as e:
        assert e.status_code == 403, f"ждали 403, got {e.status_code}"
        print("  [OK] чужой id → 403")
        return
    raise AssertionError("ожидался HTTPException 403")


def test_503_when_unconfigured():
    saved = api.ANALYTICS_API_KEY
    api.ANALYTICS_API_KEY = ""
    try:
        _call(from_=None, to=None, sla_minutes=None, user=ALLOWED)
    except HTTPException as e:
        assert e.status_code == 503, f"ждали 503, got {e.status_code}"
        print("  [OK] нет ANALYTICS_API_KEY → 503")
        return
    finally:
        api.ANALYTICS_API_KEY = saved
    raise AssertionError("ожидался HTTPException 503")


def test_proxies_with_key_and_org():
    api.httpx.AsyncClient = FakeClient
    try:
        FakeClient.next_resp = FakeResp(200, {"hello": "world", "org_id": "org-alieva"})
        out = _call(from_="2026-06-01", to="2026-06-28", sla_minutes=30, user=ALLOWED)
        assert out == {"hello": "world", "org_id": "org-alieva"}, out
        cap = FakeClient.captured
        assert cap["headers"].get("X-API-Key") == "secret-test-key", cap["headers"]
        assert cap["params"].get("org_id") == "org-alieva", cap["params"]
        assert cap["params"].get("from") == "2026-06-01", cap["params"]
        assert cap["params"].get("to") == "2026-06-28", cap["params"]
        assert cap["params"].get("sla_minutes") == 30, cap["params"]
        assert cap["url"].endswith("/admin/analytics/export"), cap["url"]
        print("  [OK] проксирует с X-API-Key и org_id из env")
    finally:
        api.httpx.AsyncClient = _REAL_ASYNC_CLIENT


def test_upstream_error_becomes_502():
    api.httpx.AsyncClient = FakeClient
    try:
        FakeClient.next_resp = FakeResp(401, None, "bad key")
        try:
            _call(from_=None, to=None, sla_minutes=None, user=ALLOWED)
        except HTTPException as e:
            assert e.status_code == 502, f"ждали 502, got {e.status_code}"
            print("  [OK] upstream 401 → 502")
            return
        raise AssertionError("ожидался HTTPException 502")
    finally:
        api.httpx.AsyncClient = _REAL_ASYNC_CLIENT


if __name__ == "__main__":
    failed = 0
    for t in (test_forbidden_for_other_id, test_503_when_unconfigured,
              test_proxies_with_key_and_org, test_upstream_error_becomes_502):
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
