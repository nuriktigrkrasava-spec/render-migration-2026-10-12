"""Веб-регистрация по демо-ссылке и счётчик «пришло / подключили / активные».

Запуск:  python3 test_signup.py
"""
import os
import tempfile

os.environ.setdefault("DB_PATH", tempfile.mktemp(suffix=".db"))
import database as D  # noqa: E402

D.init_db()


def test_web_signup_gets_an_identity_that_cannot_collide_with_telegram():
    """Человек с сайта регистрируется ДО того, как написал боту: телеграм-id у
    него ещё нет. Выдаём свой, заведомо отрицательный — телеграм таких не
    выдаёт, значит перепутать два аккаунта невозможно."""
    uid1, code1 = D.web_signup("kolya", "Коля")
    uid2, code2 = D.web_signup("masha", "Маша")
    assert uid1 < 0 and uid2 < 0, (uid1, uid2)
    assert uid1 != uid2 and code1 != code2
    assert D.get_user(uid1)["username"] == "kolya"
    print("  [OK] веб-аккаунты получают свои отрицательные id")


def test_an_id_is_never_handed_out_twice():
    """ГЛАВНОЕ. Ссылка `?m=...` — это подпись id, без срока годности. Верни id
    в оборот — и старая закладка первого человека откроет кабинет второго со
    всеми его данными. Раньше id считался как MIN(telegram_id)-1 и после
    привязки освобождался; счётчик обязан быть монотонным."""
    seen = set()
    for i in range(6):
        uid, code = D.web_signup(f"user{i}", "")
        assert uid not in seen, f"id {uid} выдан повторно"
        seen.add(uid)
        D.claim_web_signup_by_code(code, 700000 + i)   # аккаунт уехал на телеграм
    after, _ = D.web_signup("последний", "")
    assert after not in seen, f"id {after} выдан повторно после привязок"
    print("  [OK] id не выдаётся повторно даже после переезда аккаунтов")


def test_the_old_link_of_a_moved_account_leads_nowhere():
    """Ссылка, выданная до привязки, продолжает действовать — подпись вечная.
    Она обязана вести в ПУСТОЙ аккаунт, а не в чужой."""
    uid, code = D.web_signup("ushel", "")
    D.add_restaurant(uid, "l", "org-old", "Мой", "https://s", "p")
    assert D.claim_web_signup_by_code(code, 700100) is True
    nxt, _ = D.web_signup("prishel", "")
    D.add_restaurant(nxt, "l", "org-new", "Чужой", "https://s", "p")
    assert [r["organization_id"] for r in D.get_restaurants(uid)] == [], D.get_restaurants(uid)
    print("  [OK] старая ссылка ведёт в пустой аккаунт, а не в чужой")


def test_username_is_stored_without_the_at_sign():
    """@kolya и kolya — один человек."""
    uid, _ = D.web_signup("@Petya", "Петя")
    assert D.get_user(uid)["username"] == "petya", D.get_user(uid)
    print("  [OK] @ и регистр не мешают найти человека")


def test_bot_claims_the_account_only_with_the_one_time_code():
    """Привязка по @username означала бы, что любой заводит аккаунт на чужое
    публичное имя, а владелец имени забирает его первым же /start и получает
    чужой «филиал» среди своих. Доказательство — одноразовый код из ссылки."""
    uid, code = D.web_signup("vasya", "Вася")
    D.add_restaurant(uid, "login", "org-1", "Мой ресторан", "https://s", "pwd")
    assert D.claim_web_signup_by_code(code, 555001) is True
    assert [r["organization_id"] for r in D.get_restaurants(555001)] == ["org-1"]
    assert D.get_restaurants(uid) == []
    print("  [OK] ресторан переезжает только по коду из ссылки")


def test_the_code_works_once_and_a_wrong_one_never():
    """Код погашается: ссылка могла осесть в чужой истории."""
    uid, code = D.web_signup("odin", "")
    assert D.claim_web_signup_by_code(code, 555010) is True
    assert D.claim_web_signup_by_code(code, 555011) is False, "код сработал дважды"
    assert D.claim_web_signup_by_code("подобранный", 555012) is False
    assert D.claim_web_signup_by_code("", 555013) is False
    print("  [OK] код одноразовый, подобранный не работает")


def test_an_old_code_expires():
    """Непривязанный аккаунт не должен ждать вечно."""
    import time
    uid, code = D.web_signup("staryi", "")
    conn = D.get_db()
    conn.execute("UPDATE web_signup SET created_ts = ? WHERE uid = ?",
                 (int(time.time()) - D.WEB_CLAIM_TTL - 60, uid))
    conn.commit(); conn.close()
    assert D.claim_web_signup_by_code(code, 555020) is False
    print("  [OK] просроченный код не привязывает")


def test_counter_separates_arrived_connected_and_active():
    """Три разных числа. «Пришёл» — открыл ссылку. «Подключил» — дошёл до конца
    и у него есть ресторан. «Активный» — заходил за последние 7 дней. Мешать их
    нельзя: воронка тогда врёт в обе стороны."""
    import time
    now = int(time.time())
    before = D.acquisition_stats()
    for tid, days_ago in [(900001, 0), (900002, 3), (900003, 30)]:
        D.record_acquisition(tid, f"u{tid}", "site")
        D.touch_acquisition(tid, ts=now - days_ago * 86400)
    D.add_restaurant(900001, "l", "org-c", "Есть", "https://s", "p")
    s = D.acquisition_stats()
    # Сравниваем ПРИРОСТ: таблицу наполняют и соседние тесты.
    assert s["total"] - before["total"] == 3, (before, s)
    assert s["active"] - before["active"] == 2, (before, s)   # третий заходил месяц назад
    assert s["connected"] - before["connected"] == 1, (before, s)
    print("  [OK] пришло / подключили / активные считаются раздельно")


def test_a_visit_updates_activity_but_not_the_arrival_date():
    """Дата прихода — это атрибуция источника, её переписывать нельзя."""
    D.record_acquisition(900010, "old", "instagram")
    first = D.acquisition_stats()
    D.touch_acquisition(900010)
    rows = D.acquisitions_today()
    assert any(r["telegram_id"] == 900010 and r["source"] == "instagram" for r in rows)
    assert D.acquisition_stats()["total"] == first["total"]
    print("  [OK] заход обновляет активность, но не дату прихода и не источник")


if __name__ == "__main__":
    failed = 0
    for t in (test_web_signup_gets_an_identity_that_cannot_collide_with_telegram,
              test_an_id_is_never_handed_out_twice,
              test_the_old_link_of_a_moved_account_leads_nowhere,
              test_username_is_stored_without_the_at_sign,
              test_bot_claims_the_account_only_with_the_one_time_code,
              test_the_code_works_once_and_a_wrong_one_never,
              test_an_old_code_expires,
              test_counter_separates_arrived_connected_and_active,
              test_a_visit_updates_activity_but_not_the_arrival_date):
        print(f"\n>>> {t.__name__}")
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"  [FAIL] {e}")
        except Exception as e:
            failed += 1
            print(f"  [ERROR] {type(e).__name__}: {e}")
    print("\n" + "=" * 50)
    print("ВСЕ ТЕСТЫ ОК" if not failed else f"ПРОВАЛЕНО: {failed}")
