"""«Что изменилось»: ручка не должна держать владельца в ожидании.

Запуск:  python3 test_digest_speed.py

Сбор показателей идёт по всем филиалам и занимает минуты. Раньше «Обновить»
держал HTTP-запрос до конца сбора: кнопка показывала «Считаю…», и понять, жив
ли запрос, было нельзя. Здесь проверяем, что ручка отвечает СРАЗУ, что второе
нажатие не запускает второй проход и что тяжёлый фудкост не считается.
"""
import os
import tempfile
import warnings

warnings.filterwarnings("ignore")
os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(prefix="digest-"), "t.db"))
os.environ.pop("TELEGRAM_BOT_TOKEN", None)

import asyncio          # noqa: E402
import time             # noqa: E402
import database as db   # noqa: E402
import mountain_routes as M  # noqa: E402
import api              # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(api.app, raise_server_exceptions=False)
OWNER = 700200


def _seed():
    db.init_db()
    conn = db.get_db()
    conn.execute("DELETE FROM restaurants WHERE telegram_id = ?", (OWNER,))
    conn.commit(); conn.close()
    db.add_restaurant(OWNER, "l", "d-org", "Астана, Филиал Д", iiko_server="", api_password="")
    api._CACHE.clear() if hasattr(api, "_CACHE") else None
    api._digest_running.clear()


def test_refresh_answers_at_once_instead_of_holding_the_request():
    """«Обновить» отвечает сразу, а сбор уходит в фон."""
    _seed()
    slow_started = asyncio.Event()

    async def slow(_user):
        slow_started.set()
        await asyncio.sleep(30)          # столько владелец ждать не должен
        return {"branches": []}

    real = api._gather_assistant_signals
    api._gather_assistant_signals = slow
    try:
        t0 = time.monotonic()
        r = client.get(f"/api/mountain/digest/{M.token_for(OWNER)}?refresh=1")
        took = time.monotonic() - t0
    finally:
        api._gather_assistant_signals = real
        api._digest_running.clear()

    assert r.status_code == 200, r.text[:200]
    assert r.json().get("pending") is True, r.json()
    assert took < 3, f"ручка держала запрос {took:.1f} с — владелец снова ждёт"
    print(f"  [OK] ответ за {took:.2f} с, сбор ушёл в фон")


def test_second_press_does_not_start_a_second_pass():
    """Второе нажатие не запускает второй проход по всем филиалам."""
    _seed()
    calls = {"n": 0}

    async def counting(_user):
        calls["n"] += 1
        await asyncio.sleep(5)
        return {"branches": []}

    real = api._gather_assistant_signals
    api._gather_assistant_signals = counting
    try:
        # ОДИН клиент как контекст: иначе на каждый запрос поднимается свой цикл
        # событий, фоновая задача умирает вместе с ним и флаг сбрасывается —
        # тест ловил бы артефакт TestClient, а не поведение сервера.
        tok = M.token_for(OWNER)
        with TestClient(api.app, raise_server_exceptions=False) as c:
            c.get(f"/api/mountain/digest/{tok}?refresh=1")
            c.get(f"/api/mountain/digest/{tok}?refresh=1")
            c.get(f"/api/mountain/digest/{tok}?refresh=1")
    finally:
        api._gather_assistant_signals = real
        api._digest_running.clear()
    assert calls["n"] <= 1, f"сбор запущен {calls['n']} раз — сервер молотит вхолостую"
    print("  [OK] повторные нажатия не плодят проходы по iiko")


def test_foodcost_is_not_computed():
    """Фудкост в сборе не участвует: самый тяжёлый показатель, а в оценку
    филиала на «Горе» он не входит вовсе (в scoring.py его нет)."""
    assert api.ASSISTANT_FOODCOST is False, "фудкост снова считается по умолчанию"
    import inspect
    src = inspect.getsource(api._gather_assistant_signals)
    assert 'if ASSISTANT_FOODCOST:' in src, "вызов фудкоста больше не под рубильником"
    print("  [OK] фудкост не считается; вернуть — ASSISTANT_FOODCOST=1")


def test_branch_signals_are_gathered_in_parallel():
    """Показатели филиала собираются разом, а не по очереди.

    Девять независимых походов в iiko шли строго друг за другом, и время
    филиала было их СУММОЙ — отсюда полторы-две минуты на сеть.
    """
    import inspect
    src = inspect.getsource(api._gather_assistant_signals)
    assert "asyncio.gather(*jobs.values())" in src, "показатели снова идут по очереди"
    assert "call_sem" in src, "нет потолка на одновременные запросы к iiko"
    print("  [OK] показатели филиала идут параллельно, под общим семафором")


if __name__ == "__main__":
    failed = 0
    for t in (test_refresh_answers_at_once_instead_of_holding_the_request,
              test_second_press_does_not_start_a_second_pass,
              test_foodcost_is_not_computed,
              test_branch_signals_are_gathered_in_parallel):
        print(f"\n>>> {t.__name__}")
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"  [FAIL] {e}")
        except Exception as e:
            failed += 1
            print(f"  [ERROR] {type(e).__name__}: {e}")
    print(f"\n{'='*50}\n{'ВСЕ ТЕСТЫ ОК' if not failed else f'ПРОВАЛЕНО: {failed}'}")
    raise SystemExit(1 if failed else 0)
