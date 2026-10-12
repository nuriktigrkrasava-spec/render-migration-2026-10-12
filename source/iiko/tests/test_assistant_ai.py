from assistant_ai import rules_advice, _parse_advice


def test_rules_advice_flags_high_foodcost_with_money_and_sorts():
    signals = {"branches": [
        {"name": "Актобе", "foodcost": {"fact_pct": 56.0, "norm_pct": 30, "fact_sum": 500000},
         "revenue": 900000, "cancels": 40000, "writeoffs_pct": 3.0,
         "reviews": [], "peak": None, "has_recipes": True, "has_socials": False},
    ]}
    out = rules_advice(signals)
    # фудкост 56% при норме 30% и отмены 40k → минимум два совета, самый денежный первым
    assert len(out) >= 2
    assert out[0]["money"] >= out[1]["money"]
    fc = next(a for a in out if "удкост" in a["title"] or "удкост" in a["detail"])
    assert fc["branch"] == "Актобе"
    assert fc["action"] == {"type": "navigate", "tab": "fc", "hint": None}


def test_rules_advice_socials_plan_when_not_connected():
    signals = {"branches": [
        {"name": "Актау", "foodcost": None, "revenue": 800000, "cancels": 0,
         "writeoffs_pct": 0, "reviews": [], "peak": None,
         "has_recipes": False, "has_socials": False},
    ]}
    out = rules_advice(signals)
    assert any(a["action"] == {"type": "navigate", "tab": "rep", "hint": None} for a in out)


def test_rules_advice_low_rating_review_flags_rep():
    signals = {"branches": [
        {"name": "Алматы", "foodcost": None, "revenue": 700000, "cancels": 0,
         "writeoffs_pct": 0, "peak": None, "has_recipes": True, "has_socials": True,
         "reviews": [{"label": "2ГИС", "rating": 3.4, "delta": -0.2},
                     {"label": "Wolt", "rating": 4.8, "delta": 0.1}]},
    ]}
    out = rules_advice(signals)
    rep = [a for a in out if a["action"] == {"type": "navigate", "tab": "rep", "hint": None}]
    assert len(rep) == 1  # только худшая площадка
    assert "2ГИС" in rep[0]["title"]
    assert "падает" in rep[0]["detail"]


def test_rules_advice_incomplete_recipes_points_to_director():
    signals = {"branches": [
        {"name": "Астана", "foodcost": {"fact_pct": 28.0, "norm_pct": 30,
                                        "fact_sum": 200000, "coverage": 30},
         "revenue": 900000, "cancels": 0, "writeoffs_pct": 0, "reviews": [],
         "peak": None, "has_recipes": True, "has_socials": True},
    ]}
    out = rules_advice(signals)
    tk = [a for a in out if a["action"] == {"type": "navigate", "tab": "director", "hint": None}]
    assert len(tk) == 1
    assert "30%" in tk[0]["detail"]


def test_rules_advice_peak_points_to_sched():
    signals = {"branches": [
        {"name": "Шымкент", "foodcost": None, "revenue": 600000, "cancels": 0,
         "writeoffs_pct": 0, "reviews": [], "peak": {"day": "Пт", "hour": 20},
         "has_recipes": True, "has_socials": True},
    ]}
    out = rules_advice(signals)
    sched = [a for a in out if a["action"] == {"type": "navigate", "tab": "sched", "hint": None}]
    assert len(sched) == 1
    assert "Пт" in sched[0]["detail"] and "20" in sched[0]["detail"]


def test_parse_advice_strips_markdown():
    raw = '```json\n[{"branch":"X","title":"t","detail":"d","money":10,"severity":"low"}]\n```'
    out = _parse_advice(raw)
    assert out[0]["branch"] == "X"


def test_parse_advice_drops_non_dict_elements():
    # Claude может вернуть массив с мусором (null/строка/число) — рендер не должен падать
    raw = '[{"branch":"X","title":"t","detail":"d","money":1,"severity":"low"}, null]'
    out = _parse_advice(raw)
    assert out == [{"branch": "X", "title": "t", "detail": "d", "money": 1, "severity": "low"}]
    assert all(isinstance(x, dict) for x in out)
    assert None not in out
