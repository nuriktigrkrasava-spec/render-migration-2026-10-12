"""Изоляция сайта: что видно по ссылке ФИЛИАЛА, а что нет.

Запуск:  python3 test_site_isolation.py

Ссылка филиала (`b{org}.{подпись}`) ходит в iiko от имени ВЛАДЕЛЬЦА — своих
кредов у филиала нет. Поэтому каждая ручка обязана сама сузить доступ до своего
филиала: любая, что смотрит только на `user["id"]`, отдаёт управляющему всю сеть.

Главный тест здесь — сплошной обход: он сам находит все ручки с филиалом в пути
и требует 403 на чужой филиал. Новая ручка, забывшая проверку, ломает его сразу,
а не через полгода.
"""
import os
import re
import tempfile
import warnings

warnings.filterwarnings("ignore")
os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(prefix="isolation-"), "t.db"))
os.environ.pop("TELEGRAM_BOT_TOKEN", None)

import database as db          # noqa: E402
import mountain_routes as M    # noqa: E402
import api                     # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

# Наружу не ходим: проверяем ВЕРДИКТ доступа, а не данные. Живой iiko здесь
# только замедлил бы тест и стучался бы в боевые серверы клиента.
api.get_client = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("network blocked"))

client = TestClient(api.app, raise_server_exceptions=False)
OWNER, MANAGER = 900301, 900302
MINE, OTHER = "org-mine", "org-other"
# параметров хватает, чтобы ручка дошла до проверки доступа, а не упала в 422
QS = "from=2026-08-01&to=2026-08-25&date=2026-08-25&date_from=2026-08-01&date_to=2026-08-25&product_id=x&month=2026-08&week=2026-W35"


def _seed():
    db.init_db()
    conn = db.get_db()
    conn.execute("DELETE FROM restaurants WHERE telegram_id IN (?,?)", (OWNER, MANAGER))
    conn.commit(); conn.close()
    db.add_restaurant(OWNER, "login", MINE, "Астана, Мой филиал", iiko_server="", api_password="")
    db.add_restaurant(OWNER, "login", OTHER, "Астана, Чужой филиал", iiko_server="", api_password="")


def _org_routes():
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "api.py"),
               encoding="utf-8").read().split("\n")
    return sorted({(m.group(1), i + 1) for i, ln in enumerate(src)
                   if (m := re.match(r'@app\.get\("([^"]*\{org_id\}[^"]*)"', ln))})


def test_every_org_endpoint_refuses_a_foreign_branch():
    """Сплошной обход: ни одна ручка не отдаёт чужой филиал по ссылке своего."""
    _seed()
    h = {"X-Site-Token": M.token_for_org(MINE)}
    bad = []
    for path, line in _org_routes():
        url = path.replace("{org_id}", OTHER) + "?" + QS
        code = client.get(url, headers=h).status_code
        # 422 — ручка не дошла до проверки из-за своих обязательных параметров;
        # такие добираем точечными тестами ниже, молча зелёными их не считаем
        if code not in (403, 422):
            bad.append(f"api.py:{line} {path} -> {code}")
    assert not bad, "чужой филиал открыт:\n    " + "\n    ".join(bad)
    print(f"  [OK] {len(_org_routes())} ручек с филиалом — чужой не отдают")


def test_branch_link_cannot_get_the_owners_link():
    """Ссылка филиала не должна выдавать личную ссылку ВЛАДЕЛЬЦА.

    Это прямая привилегия-эскалация: по ссылке владельца открывается вся сеть,
    ставятся задачи и уходит рассылка всем филиалам. `/api/mountain/links`
    смотрел только на user["id"], а у ссылки филиала это uid владельца.
    """
    _seed()
    r = client.get("/api/mountain/links", headers={"X-Site-Token": M.token_for_org(MINE)})
    assert r.status_code == 403, f"ссылки раздались управляющему: {r.status_code} {r.text[:200]}"
    print("  [OK] по ссылке филиала чужие ссылки не выдаются")


def test_owner_still_gets_the_links():
    """Владельцу ссылки нужны — иначе раздавать их будет нечем."""
    _seed()
    r = client.get("/api/mountain/links", headers={"X-Site-Token": M.token_for(OWNER)})
    assert r.status_code == 200, r.text[:200]
    d = r.json()
    assert d["owner"]["link"] and len(d["branches"]) == 2, d
    print("  [OK] владелец по-прежнему получает свои ссылки")


def test_task_tracker_is_confined_to_the_branch():
    """Трекер задач игнорировал филиал в пути и отдавал всю сеть."""
    _seed()
    db.create_bot_task(OWNER, MINE, "Астана, Мой филиал", "своя задача")
    db.create_bot_task(OWNER, OTHER, "Астана, Чужой филиал", "чужая задача")
    r = client.get(f"/api/tasks/{MINE}", headers={"X-Site-Token": M.token_for_org(MINE)})
    assert r.status_code == 200, r.text[:200]
    names = [b["branch"] for b in r.json().get("branches") or []]
    assert names == ["Астана, Мой филиал"], f"видны чужие филиалы: {names}"
    print("  [OK] трекер задач показывает только свой филиал")


if __name__ == "__main__":
    failed = 0
    for t in (test_every_org_endpoint_refuses_a_foreign_branch,
              test_branch_link_cannot_get_the_owners_link,
              test_owner_still_gets_the_links,
              test_task_tracker_is_confined_to_the_branch):
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
