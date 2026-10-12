"""Опоздания сотрудников: эталон, допуск, ночная смена, сводка.

Запуск:  python3 test_staff_late.py
"""
import staff_late as SL

DAY = "2026-08-25"


def att(eid, name, start, day=DAY):
    return {"employee_id": eid, "name": name, "date": day, "start": start, "end": "22:00"}


def test_grace_does_not_make_a_latecomer():
    """Пришёл в пределах допуска — вовремя. Иначе «опоздал на 3 минуты» каждый день."""
    rows = SL.late_rows([att("e1", "Ким", "09:08")], branch_starts=["09:00"], grace_min=10)
    assert rows[0]["status"] == "ontime" and rows[0]["late_min"] == 0, rows
    print("  [OK] 09:08 при старте 09:00 и допуске 10 — вовремя")


def test_late_beyond_grace_is_counted_in_full():
    """Опоздание считается от плана, а не от границы допуска."""
    rows = SL.late_rows([att("e1", "Ким", "09:24")], branch_starts=["09:00"], grace_min=10)
    assert rows[0]["status"] == "late" and rows[0]["late_min"] == 24, rows
    print("  [OK] 09:24 при старте 09:00 — опоздание 24 мин, а не 14")


def test_evening_shift_matches_evening_start():
    """Вечерний сотрудник сверяется с 16:00, а не с 09:00 — иначе +7 часов опоздания."""
    rows = SL.late_rows([att("e2", "Волков", "15:52")], branch_starts=["09:00", "16:00"])
    assert rows[0]["planned"] == "16:00" and rows[0]["status"] == "ontime", rows
    print("  [OK] приход 15:52 привязан к старту 16:00, опоздания нет")


def test_schedule_overrides_branch_start():
    """График сотрудника сильнее времени филиала: у повара свой старт."""
    rows = SL.late_rows([att("e3", "Повар", "07:30")],
                        schedule={("e3", DAY): "07:00"}, branch_starts=["09:00"])
    assert rows[0]["planned"] == "07:00" and rows[0]["late_min"] == 30, rows
    print("  [OK] график 07:00 перебил старт филиала 09:00")


def test_earliest_arrival_wins():
    """Терминал пишет несколько интервалов за день — опоздание считаем по первому."""
    rows = SL.late_rows([att("e1", "Ким", "13:00"), att("e1", "Ким", "08:55")],
                        branch_starts=["09:00"])
    assert len(rows) == 1 and rows[0]["arrival"] == "08:55" and rows[0]["status"] == "ontime", rows
    print("  [OK] из двух интервалов взят ранний — вовремя, а не «опоздал на 4 часа»")


def test_early_arrival_is_never_late():
    rows = SL.late_rows([att("e1", "Ким", "08:30")], branch_starts=["09:00"])
    assert rows[0]["status"] == "ontime" and rows[0]["late_min"] == 0, rows
    print("  [OK] пришёл раньше плана — не опоздание")


def test_night_shift_across_midnight():
    """План 23:00, приход 23:25 — 25 минут, а не 1415 (круг суток)."""
    rows = SL.late_rows([att("e4", "Ночной", "23:25")], branch_starts=["23:00"])
    assert rows[0]["late_min"] == 25, rows
    print("  [OK] ночная смена не даёт опоздания в сутки")


def test_evening_staff_are_not_ten_hours_late():
    """Живой случай Алиева 24.08: при одном старте 11:00 приходы в 19:11 и 21:47
    считались опозданием на 8-10 часов и в одиночку топили оценку филиала.
    Это не опоздание, а вторая смена - она уходит «вне графика»."""
    rows = SL.late_rows([att("e1", "Мейіржан", "19:11"), att("e2", "Сушист", "21:47"),
                         att("e3", "Қанабек", "11:07"), att("e4", "Опоздун", "11:55")],
                        branch_starts=["11:00"])
    st = {r["name"]: r for r in rows}
    assert st["Мейіржан"]["status"] == "noplan" and st["Сушист"]["status"] == "noplan", rows
    assert st["Опоздун"]["late_min"] == 55 and st["Қанабек"]["status"] == "ontime", rows
    s = SL.summarize(rows)
    assert s["total"] == 2 and s["late"] == 1 and s["late_minutes"] == 55, s
    print(f"  [OK] вечерние вне графика, реальное опоздание 55 мин осталось ({s['late']} из {s['total']})")


def test_second_branch_start_catches_the_evening_shift():
    """Настоящее лечение - второй старт смены: тогда вечерний приход снова судится."""
    rows = SL.late_rows([att("e1", "Вечерний", "16:40")], branch_starts=["11:00", "16:00"])
    assert rows[0]["planned"] == "16:00" and rows[0]["late_min"] == 40, rows
    print("  [OK] со стартом 16:00 вечерний приход 16:40 - опоздание 40 мин")


def test_no_reference_means_silence_not_ontime():
    """Старты филиала не заданы и графика нет — человек не «вовремя», он вне счёта."""
    rows = SL.late_rows([att("e1", "Ким", "11:00")], branch_starts=[])
    s = SL.summarize(rows)
    assert rows[0]["status"] == "noplan", rows
    assert s["total"] == 0 and s["late_pct"] is None and s["noplan"] == 1, s
    print("  [OK] без эталона критерий молчит, а не рисует идеальный филиал")


def test_summary_counts_people_not_intervals():
    rows = SL.late_rows(
        [att("e1", "Ким", "09:30"), att("e2", "Волков", "08:58"),
         att("e3", "Ли", "09:45"), att("e1", "Ким", "18:00")],
        branch_starts=["09:00"])
    s = SL.summarize(rows)
    assert s == {"total": 3, "late": 2, "late_pct": 66.7, "late_minutes": 75, "noplan": 0}, s
    print(f"  [OK] сводка: опоздали {s['late']} из {s['total']} ({s['late_pct']}%), {s['late_minutes']} мин")


def test_broken_time_is_skipped_not_crashed():
    """Явки — чужие данные. Мусор пропускаем, сервер не роняем."""
    rows = SL.late_rows([{"employee_id": "e1", "name": "Ким", "date": DAY, "start": "—"},
                         att("e2", "Волков", "09:05")], branch_starts=["09:00"])
    assert [r["employee_id"] for r in rows] == ["e2"], rows
    print("  [OK] строка с битым временем выброшена, остальные посчитаны")


def test_ranking_puts_the_regular_latecomer_first():
    """Кто чаще опаздывает: важна регулярность, а не один рекордный час."""
    rows = SL.late_rows(
        [att("e1", "Регулярный", "11:30", "2026-08-20"),
         att("e1", "Регулярный", "11:25", "2026-08-21"),
         att("e1", "Регулярный", "11:20", "2026-08-22"),
         att("e2", "Разовый", "12:30", "2026-08-21"),
         att("e3", "Пунктуальный", "10:55", "2026-08-21")],
        branch_starts=("11:00",))
    top = SL.by_employee(rows)
    assert [e["name"] for e in top[:2]] == ["Регулярный", "Разовый"]
    first = top[0]
    assert first["late_days"] == 3 and first["days"] == 3 and first["late_pct"] == 100.0
    assert first["late_minutes"] == 30 + 25 + 20 and first["worst_min"] == 30
    assert first["avg_late_min"] == 25 and first["last_late"] == "2026-08-22"
    punctual = [e for e in top if e["name"] == "Пунктуальный"][0]
    assert punctual["late_days"] == 0 and punctual["late_pct"] == 0.0


def test_ranking_ignores_offschedule_arrivals():
    """«Вне графика» не делает человека ни хорошим, ни плохим — он вне знаменателя."""
    rows = SL.late_rows([att("e1", "Вечерний", "19:30", "2026-08-20")],
                        branch_starts=("11:00",))
    e = SL.by_employee(rows)[0]
    assert e["days"] == 0 and e["noplan"] == 1 and e["late_pct"] is None


def test_history_by_day_is_sorted_and_counts_people():
    rows = SL.late_rows(
        [att("e1", "А", "11:30", "2026-08-21"), att("e2", "Б", "10:50", "2026-08-21"),
         att("e1", "А", "10:59", "2026-08-20")],
        branch_starts=("11:00",))
    hist = SL.by_day(rows)
    assert [d["date"] for d in hist] == ["2026-08-20", "2026-08-21"]
    assert hist[1] == {"date": "2026-08-21", "total": 2, "late": 1, "late_minutes": 30}


def test_on_shift_flag_survives_to_the_row():
    """Незакрытая явка = человек сейчас на смене; на опоздание это не влияет."""
    a = att("e1", "На смене", "11:30")
    a["open"] = True
    row = SL.late_rows([a], branch_starts=("11:00",))[0]
    assert row["open"] is True and row["late_min"] == 30


if __name__ == "__main__":
    failed = 0
    for t in (test_grace_does_not_make_a_latecomer, test_late_beyond_grace_is_counted_in_full,
              test_evening_staff_are_not_ten_hours_late, test_second_branch_start_catches_the_evening_shift,
              test_evening_shift_matches_evening_start, test_schedule_overrides_branch_start,
              test_earliest_arrival_wins, test_early_arrival_is_never_late,
              test_night_shift_across_midnight, test_no_reference_means_silence_not_ontime,
              test_summary_counts_people_not_intervals, test_broken_time_is_skipped_not_crashed,
              test_ranking_puts_the_regular_latecomer_first, test_ranking_ignores_offschedule_arrivals,
              test_history_by_day_is_sorted_and_counts_people, test_on_shift_flag_survives_to_the_row):
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
