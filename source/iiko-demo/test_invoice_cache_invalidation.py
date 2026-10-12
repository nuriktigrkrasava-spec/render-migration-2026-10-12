"""Тест: мутации накладной (создать черновик / провести / распровести) ОБЯЗАНЫ
сбрасывать кэш /api/invoices. Иначе список отдаёт устаревший статус и кнопка
действия (Провести/Распровести/Редактировать) не обновляется — баг «нажал
создать черновик, а кнопка Провести не появилась».
Запуск: python3 test_invoice_cache_invalidation.py
Использует ВРЕМЕННУЮ БД, боевую не трогает.
"""
import os, tempfile

os.environ["DB_PATH"] = tempfile.mktemp(suffix="_cache_inval_test.db")
import database
database.init_db()
import api


def test_invalidate_clears_org_across_users_and_periods():
    api._cache.clear(); api._cache_time.clear()
    # Один и тот же org закэширован у двух операторов и на трёх периодах —
    # после мутации все эти срезы устарели и должны быть снесены.
    for uid in (10, 20):
        for period in ("day", "week", "month"):
            api._set_cached(api._cache_key(uid, "invoices", "org-1", period), "stale")
    # Посторонние ключи, которые трогать НЕЛЬЗЯ:
    api._set_cached(api._cache_key(10, "invoices", "org-2", "week"), "keep")  # другой org
    api._set_cached(api._cache_key(10, "expenses", "org-1", "week"), "keep")  # другой эндпойнт
    api._set_cached(api._cache_key(10, "invoices", "org-12", "week"), "keep")  # org-12 ≠ org-1 (префикс)

    api._invalidate_cache("invoices", "org-1")

    for uid in (10, 20):
        for period in ("day", "week", "month"):
            k = api._cache_key(uid, "invoices", "org-1", period)
            assert api._get_cached(k) is None, f"не сброшен {k}"
    assert api._get_cached(api._cache_key(10, "invoices", "org-2", "week")) == "keep", "снёс чужой org"
    assert api._get_cached(api._cache_key(10, "expenses", "org-1", "week")) == "keep", "снёс другой эндпойнт"
    assert api._get_cached(api._cache_key(10, "invoices", "org-12", "week")) == "keep", "снёс org-12 по префиксу"
    print("  [OK] invalidate: org-1/invoices снесён у всех юзеров и периодов, остальное цело")


if __name__ == "__main__":
    failed = 0
    for t in (test_invalidate_clears_org_across_users_and_periods,):
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
    raise SystemExit(1 if failed else 0)
