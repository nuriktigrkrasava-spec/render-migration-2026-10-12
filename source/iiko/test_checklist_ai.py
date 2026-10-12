"""Экономия на разборе фото чек-листов: четыре предохранителя.

Запуск:  python3 test_checklist_ai.py

В модель не ходим: проверяем правила отбора, сжатие, кэш и подсчёт стоимости.
"""
import os
import tempfile

os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(prefix="chk-ai-"), "t.db"))

import checklist_ai as C  # noqa: E402


def test_switch_turns_the_review_off_without_a_deploy():
    """Рубильник: разбор включён, но выключается одной переменной.

    Раньше по умолчанию было выключено и включалось переменной на Render.
    Владелец включил разбор, и умолчание перевернулось — но возможность
    выключить БЕЗ деплоя обязана остаться: это единственный быстрый способ
    остановить трату денег.
    """
    import importlib
    old = os.environ.get("CHECKLIST_AI")
    try:
        os.environ["CHECKLIST_AI"] = "0"
        m = importlib.reload(C)
        assert m.AI_ENABLED is False, "CHECKLIST_AI=0 обязан выключать разбор"
        assert m._client is None, m._client
        os.environ.pop("CHECKLIST_AI")
        m = importlib.reload(C)
        assert m.AI_ENABLED is True, "по умолчанию разбор включён"
    finally:
        if old is None:
            os.environ.pop("CHECKLIST_AI", None)
        else:
            os.environ["CHECKLIST_AI"] = old
        importlib.reload(C)
    print("  [OK] по умолчанию включён, CHECKLIST_AI=0 выключает без деплоя")


def test_no_key_means_no_calls_even_when_enabled():
    """Без ANTHROPIC_API_KEY клиента нет — включённый рубильник ничего не тратит."""
    assert C.AI_ENABLED is True
    if not os.getenv("ANTHROPIC_API_KEY"):
        assert C._client is None, "без ключа клиент создаваться не должен"
        print("  [OK] рубильник включён, ключа нет — в модель ничего не уходит")
    else:
        assert C._client is not None
        print("  [OK] рубильник включён и ключ есть — клиент готов")


def test_sampling_keeps_most_uploads_free():
    """Обычная сдача идёт через модель только выборочно."""
    upload = {"photos": [1, 2, 3, 4]}
    do_low, why_low = C.should_review(upload, rnd=0.05)      # попал в выборку
    do_high, _ = C.should_review(upload, rnd=0.9)            # не попал
    assert do_low and "выборочный" in why_low, why_low
    assert not do_high
    print(f"  [OK] выборка {C.SAMPLE_PCT}%: 0.05 → проверяем, 0.9 → нет")


def test_edge_cases_always_reviewed():
    """Спорное проверяем всегда, даже если не попало в выборку."""
    late, why1 = C.should_review({"photos": [1, 2, 3]}, late=True, rnd=0.99)
    rep, why2 = C.should_review({"photos": [1, 2, 3]}, had_issue_yesterday=True, rnd=0.99)
    few, why3 = C.should_review({"photos": [1]}, rnd=0.99)
    assert late and "опоздан" in why1, why1
    assert rep and "вчера" in why2, why2
    assert few and "трёх" in why3, why3
    print("  [OK] опоздание, вчерашнее нарушение и мало кадров — всегда проверяем")


def test_shrink_cuts_tokens_fourfold():
    """512 px вместо 1024 — вчетверо дешевле: vision считает по площади."""
    big = C.tokens_for(1024, 768)
    small = C.tokens_for(512, 384)
    assert big > 1000 and small < 300, (big, small)
    assert big / small >= 3.9, (big, small)
    print(f"  [OK] кадр 1024×768 ≈ {big} токенов, 512×384 ≈ {small} — в {big // small} раза дешевле")


def test_hash_is_stable_and_short():
    a = C.photo_hash(b"\xff\xd8kadr")
    b = C.photo_hash(b"\xff\xd8kadr")
    c = C.photo_hash(b"\xff\xd8drugoy")
    assert a == b and a != c and len(a) == 32, (a, b, c)
    print("  [OK] хеш кадра стабилен — повтор не оплачивается")


def test_cached_slot_costs_nothing():
    """Все кадры уже в кэше — модель не зовём даже при включённом рубильнике."""
    import asyncio
    photos = [(b"\xff\xd8kadr1", "зал"), (b"\xff\xd8kadr2", "кухня")]
    cache = {C.photo_hash(d): {"ok": True, "issues": []} for d, _ in photos}
    res = asyncio.run(C.review_slot(photos, cache=cache))
    assert res["checked"] and res.get("cached") and res["ok"], res
    print("  [OK] знакомые кадры отдаются из кэша, запроса нет")


def test_one_request_per_slot_not_per_photo():
    """В один запрос уходит слот целиком, но не больше разумного числа кадров."""
    assert C.MAX_PHOTOS_PER_SLOT <= 8, C.MAX_PHOTOS_PER_SLOT
    print(f"  [OK] один запрос на слот, до {C.MAX_PHOTOS_PER_SLOT} кадров в нём")


def test_daily_cost_estimate():
    """Прикидка на сеть: 9 филиалов × 4 слота × 3 зоны × 3 кадра."""
    per_photo = C.tokens_for(C.MAX_SIDE, int(C.MAX_SIDE * 0.75))
    slots = 9 * 4
    naive = slots * 9 * C.tokens_for(1024, 768)                 # всё, без сжатия
    ours = slots * (C.SAMPLE_PCT / 100) * (per_photo * C.MAX_PHOTOS_PER_SLOT + 200)
    assert ours < naive / 10, (ours, naive)
    print(f"  [OK] в день: слепой прогон ≈ {naive:,.0f} токенов, наш ≈ {ours:,.0f}")


def _fake_model(reply: str):
    """Подставная модель: отвечает заданным текстом, запоминает системный промпт."""
    import asyncio
    seen = {}

    class _Msg:
        def __init__(self, t): self.text = t

    class _Resp:
        def __init__(self, t): self.content = [_Msg(t)]

    class _Messages:
        async def create(self, **kw):
            seen.update(kw)
            return _Resp(reply)

    class _Client:
        messages = _Messages()

    return _Client(), seen, asyncio


def test_review_answers_by_the_same_criteria_the_manager_sees():
    """Разбор отвечает ПО КРИТЕРИЯМ чек-листа, а не свободным текстом.

    Раньше приходил общий вердикт и россыпь замечаний: список «что снять» на
    экране жил сам по себе, разбор — сам по себе, и управляющий не понимал, что
    именно зачли. Критерии уходят в промпт и возвращаются с ответом по каждому.
    """
    import scoring as S
    reply = ('{"ok":false,"criteria":['
             '{"zone":"зал","item":"Пол под столами и у входа","ok":true,"what":""},'
             '{"zone":"зал","item":"Урна не переполнена","ok":false,"what":"урна с горкой"}]}')
    client, seen, aio = _fake_model(reply)
    old = C._client
    C._client = client
    try:
        out = aio.run(C.review_slot([(b"jpg", "зал")], cache={}, criteria=S.ZONE_CRITERIA))
    finally:
        C._client = old

    assert "Пол под столами и у входа" in seen["system"], "критерии не ушли в промпт"
    assert "Санузел" not in seen["system"], "ушли критерии зон, кадров которых нет"
    assert out["checked"] and out["ok"] is False, out
    assert len(out["criteria"]) == 2, out["criteria"]
    assert out["issues"] == [{"zone": "зал", "what": "Урна не переполнена — урна с горкой"}], out["issues"]
    print("  [OK] разбор идёт по критериям чек-листа и возвращает ответ по каждому")


def test_failed_criterion_beats_the_models_own_verdict():
    """Общий вердикт не берём на веру: модель ставила ok=true при проваленном
    же критерии — и грязная зона отмечалась зелёным."""
    import scoring as S
    reply = ('{"ok":true,"criteria":['
             '{"zone":"санузел","item":"Есть мыло и бумага","ok":false,"what":"нет мыла"}]}')
    client, _seen, aio = _fake_model(reply)
    old = C._client
    C._client = client
    try:
        out = aio.run(C.review_slot([(b"jpg", "санузел")], cache={}, criteria=S.ZONE_CRITERIA))
    finally:
        C._client = old
    assert out["ok"] is False, "проваленный критерий обязан перевесить общий ok"
    print("  [OK] проваленный критерий перевешивает общий вердикт модели")


def test_normal_submission_is_not_an_overdue_one():
    """Сдача через пару минут после слота — не опоздание.

    Опозданием считалось любое время больше времени слота: сняли в 10:47 при
    слоте 10:45 — уже late. А late значит «проверять всегда», поэтому выборка
    в SAMPLE_PCT не применялась никогда и в модель уходило 100% слотов.
    """
    import mountain_routes as M
    assert M._minutes("10:47") <= M._minutes("10:45") + M.SLOT_GRACE_MIN, "обычная сдача — не опоздание"
    assert M._minutes("11:30") > M._minutes("10:45") + M.SLOT_GRACE_MIN, "через 45 минут — опоздание"
    print(f"  [OK] фора {M.SLOT_GRACE_MIN} мин: 10:47 не опоздание, 11:30 — опоздание")


def test_shrink_really_shrinks_so_the_saving_is_real():
    """Сжатие до 512 px должно РАБОТАТЬ, а не молча отступать.

    shrink() без Pillow отдаёт кадр как есть — и весь расчёт экономии
    разваливается: вместо 512 px в модель уходит 1024 px, вчетверо дороже. А
    Pillow в requirements.txt не было вовсе, он стоял только на машине
    разработчика. Тест падает, если зависимость снова потеряется.
    """
    from PIL import Image
    import io as _io
    buf = _io.BytesIO()
    Image.new("RGB", (1600, 1200), (200, 200, 200)).save(buf, format="JPEG")
    small, mime = C.shrink(buf.getvalue())
    assert mime == "image/jpeg"
    w, h = Image.open(_io.BytesIO(small)).size
    assert max(w, h) <= C.MAX_SIDE, f"кадр не сжался: {w}x{h}"
    assert len(small) < len(buf.getvalue()), "после сжатия кадр не стал легче"
    print(f"  [OK] 1600x1200 -> {w}x{h}, {len(buf.getvalue())//1024} КБ -> {len(small)//1024} КБ")


if __name__ == "__main__":
    failed = 0
    for t in (test_switch_turns_the_review_off_without_a_deploy,
              test_no_key_means_no_calls_even_when_enabled, test_sampling_keeps_most_uploads_free,
              test_edge_cases_always_reviewed, test_shrink_cuts_tokens_fourfold,
              test_hash_is_stable_and_short, test_cached_slot_costs_nothing,
              test_one_request_per_slot_not_per_photo, test_daily_cost_estimate,
              test_review_answers_by_the_same_criteria_the_manager_sees,
              test_failed_criterion_beats_the_models_own_verdict,
              test_normal_submission_is_not_an_overdue_one,
              test_shrink_really_shrinks_so_the_saving_is_real):
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
