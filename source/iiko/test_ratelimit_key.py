"""Ключ лимитера: считаем посетителя, а не прокси Render.

Запуск:  python3 test_ratelimit_key.py
"""
import os
import tempfile

os.environ.setdefault("DB_PATH", tempfile.mktemp(suffix=".db"))
import api  # noqa: E402
os.environ["TRUSTED_PROXY_CIDRS"] = "10.0.0.7/32"


class Req:
    def __init__(self, xff=None, host="10.0.0.7"):
        self.headers = {"X-Forwarded-For": xff} if xff else {}
        self.client = type("C", (), {"host": host})()


def test_two_visitors_behind_one_proxy_are_counted_apart():
    """Главное. uvicorn на проде без --proxy-headers, и request.client.host у
    всех один — внутренний адрес прокси. На нём пять попыток кем угодно
    закрывали форму регистрации для всего мира."""
    a = api._client_ip(Req(xff="203.0.113.5"))
    b = api._client_ip(Req(xff="198.51.100.9"))
    assert a != b, (a, b)
    assert a == "203.0.113.5" and b == "198.51.100.9", (a, b)
    print("  [OK] два посетителя за одним прокси считаются порознь")


def test_a_forged_header_does_not_become_the_key():
    """Заголовок подделать можно, но прокси дописывает настоящий адрес СПРАВА.
    Берём последнюю запись — подделка остаётся левее и в ключ не попадает."""
    assert api._client_ip(Req(xff="1.2.3.4, 203.0.113.5")) == "203.0.113.5"
    assert api._client_ip(Req(xff="чужой-мусор, 203.0.113.5")) == "203.0.113.5"
    print("  [OK] подделанный X-Forwarded-For в ключ не попадает")


def test_without_the_header_we_fall_back_to_the_socket():
    """Локальный запуск и тесты идут без прокси — там адрес сокета и есть
    адрес посетителя."""
    assert api._client_ip(Req(host="127.0.0.1")) == "127.0.0.1"
    assert api._client_ip(Req(xff="   ", host="10.0.0.7")) == "10.0.0.7"
    print("  [OK] без заголовка берётся адрес сокета")


if __name__ == "__main__":
    failed = 0
    for t in (test_two_visitors_behind_one_proxy_are_counted_apart,
              test_a_forged_header_does_not_become_the_key,
              test_without_the_header_we_fall_back_to_the_socket):
        print(f"\n>>> {t.__name__}")
        try:
            t()
        except AssertionError as e:
            failed += 1; print(f"  [FAIL] {e}")
        except Exception as e:
            failed += 1; print(f"  [ERROR] {type(e).__name__}: {e}")
    print("\n" + "=" * 50)
    print("ВСЕ ТЕСТЫ ОК" if not failed else f"ПРОВАЛЕНО: {failed}")


def test_untrusted_client_cannot_choose_its_rate_limit_bucket():
    assert api._client_ip(Req(xff="1.2.3.4", host="203.0.113.99")) == "203.0.113.99"
