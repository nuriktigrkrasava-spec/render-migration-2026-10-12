"""Кэш выручки: сегодняшний день живёт секунды, прошлые — минуты.

Запуск:  python3 test_revenue_cache.py
"""
import os
import tempfile
from datetime import timedelta

os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(prefix="rev-cache-"), "t.db"))
import api  # noqa: E402


def test_today_is_live_period():
    today = api.local_now().date().isoformat()
    yest = (api.local_now().date() - timedelta(days=1)).isoformat()
    assert api._is_live_period(today, today), "сегодня — живой период"
    assert api._is_live_period(yest, today), "неделя/месяц по сегодня — живой период"
    assert not api._is_live_period(yest, yest), "вчера уже не меняется"
    print("  [OK] живым считается только период, захватывающий сегодня")


def test_live_ttl_expires_fast():
    """Ответ за сегодня протухает за секунды, за прошлый день — нет."""
    api._cache.clear(); api._cache_time.clear(); api._cache_ttl.clear()
    api._set_cached("live", {"revenue": 1}, ttl=timedelta(seconds=45))
    api._set_cached("old", {"revenue": 2})
    assert api._get_cached("live") == {"revenue": 1}
    assert api._get_cached("old") == {"revenue": 2}
    # состариваем обе записи на минуту
    for k in ("live", "old"):
        api._cache_time[k] = api._cache_time[k] - timedelta(seconds=60)
    assert api._get_cached("live") is None, "сегодняшняя выручка должна протухнуть за 45с"
    assert api._get_cached("old") == {"revenue": 2}, "прошлый день живёт 3 минуты"
    print("  [OK] TTL сегодняшней выручки 45с, обычного ответа — 3 минуты")


if __name__ == "__main__":
    failed = 0
    for t in (test_today_is_live_period, test_live_ttl_expires_fast):
        print(f"\n>>> {t.__name__}")
        try:
            t()
        except AssertionError as e:
            failed += 1; print(f"  [FAIL] {e}")
        except Exception as e:
            failed += 1; print(f"  [ERROR] {type(e).__name__}: {e}")
    print(f"\n{'='*50}\n{'ВСЕ ТЕСТЫ ОК' if not failed else f'ПРОВАЛЕНО: {failed}'}")
    raise SystemExit(1 if failed else 0)
