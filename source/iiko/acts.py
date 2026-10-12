"""Акты разбора: П/Ф из ЦЕХа → нарезка + обрезки + отход. Лосось и угорь.

Разбор всегда одной формы, меняется только рыба:
  • ВХОД приходит в филиал накладной из ЦЕХа («П/Ф ЛОСОСЬ СТЕЙК» на Алиева
    9 300 ₸/кг, «Пласт угря»). Только его цена берётся из накладных;
  • «нарезка» — главный выход, на него ложится ВЕСЬ остаток стоимости;
  • «обрезки» по накладным не приходят ни разу — второй выход, оценивается
    фиксированной ценой учёта (trim_price);
  • «отход» — кости и кожа, себестоимость 0 ₸.

Оператор взвешивает то, что реально держит в руках: вход, нарезку и обрезки.
Отход не взвешивают: он остаток веса, и именно он говорит, насколько аккуратно
разобрали рыбу.

ТЕРИЯКИ ЗДЕСЬ НЕТ. Это не выход разбора, а ПЕРЕДЕЛ обрезков на сковороде, и
живёт он отдельно — fry_teriyaki() ниже. Пока они считались одним актом, экран
путал два разных вопроса: «насколько аккуратно разделали рыбу» и «сколько дала
сковорода». Разбор заканчивается, когда рыба разложена на три позиции; всё,
что с ними делают дальше, — другой документ и другой свод.
"""
from typing import Any, Dict, Iterable, List, Optional

TRIM_PRICE = 8800.0          # ₸/кг, себестоимость обрезков лосося — константа учёта
# Угорь считается по константе целиком: и вход, и обрезки по 9 300 ₸/кг. Акты
# разбора живут у нас, с iiko по угрю интеграции нет, и ходить туда за его
# ценой незачем — цена задана здесь и одна на всю сеть.
EEL_PRICE = 9300.0           # ₸/кг, цена входа угря
EEL_TRIM_PRICE = 9300.0      # ₸/кг, цена обрезков угря — та же

# ── Виды разбора ─────────────────────────────────────────────────────────────
# names — как позиция называется в номенклатуре (проверено на сервере Алиева);
# words — по каким словам искать, если филиал назвал её по-своему: у каждого
# филиала свой сервер и свой справочник, а разбор один и тот же;
# avoid  — слова, по которым похожая позиция отсеивается: «Соус терияки» — не
# «П/Ф Лосось терияки», а спутать их значит списать соус вместо рыбы.
KINDS: Dict[str, Dict[str, Any]] = {
    "salmon": {
        "key": "salmon",
        "title": "Разбор лосося",
        "tab": "Лосось",                     # как рыба подписана в переключателе
        "raw_label": "Стейк в разбор",
        "raw_gen": "стейка",                 # «Вес стейка должен быть…»
        "raw_short": "стейк",                # «стейк − нарезка − обрезки»
        "trim_price": TRIM_PRICE,
        "names": {
            "raw": ["П/Ф ЛОСОСЬ СТЕЙК"],
            "slice": ["П/Ф НАРЕЗКА ЛОСОСЬ"],
            "trim": ["П/Ф ОБРЕЗКИ ЛОСОСЬ"],
            "waste": ["Отход лосося"],
        },
        "words": {
            "raw": [["лосос"], ["стейк"]],
            "slice": [["лосос"], ["нарезк"]],
            "trim": [["лосос"], ["обрезк"]],
            "waste": [["лосос"], ["отход"]],
        },
    },
    "eel": {
        "key": "eel",
        "title": "Разбор угря",
        "tab": "Угорь",
        "raw_label": "Пласт угря",
        "raw_gen": "пласта",                 # «Вес пласта должен быть…»
        "raw_short": "пласт",                # «пласт − нарезка − обрезки»
        "raw_price": EEL_PRICE,              # цена входа — константа, не из iiko
        "trim_price": EEL_TRIM_PRICE,
        # За ценами угря в iiko не ходим вовсе: они заданы константами выше.
        "price_const": True,
        "names": {
            # «УГОРЬ» стоит первым: это закупаемая рыба, и цена входа берётся
            # с неё. В номенклатуре сети по угрю всего две строки — «угорь» и
            # «П/Ф угорь», пласта отдельной позицией нет, и без этой строки
            # вход подбирался запасным поиском: на половине филиалов в него
            # вставал «П/Ф угорь», то есть выход, и цена приходила с него.
            "raw": ["УГОРЬ", "ПЛАСТ УГРЯ", "П/Ф ПЛАСТ УГРЯ", "УГОРЬ ПЛАСТ",
                    "УГОРЬ ЦЕЛЫЙ", "П/Ф УГОРЬ ЦЕЛЫЙ"],
            "slice": ["П/Ф НАРЕЗКА УГОРЬ", "УГОРЬ НАРЕЗКА", "П/Ф УГОРЬ НАРЕЗКА"],
            "trim": ["П/Ф ОБРЕЗКИ УГОРЬ", "УГОРЬ ОБРЕЗКИ", "П/Ф УГОРЬ ОБРЕЗКИ"],
            "waste": ["Отход угря", "Отход угорь"],
        },
        "words": {
            "raw": [["угор", "угр"], ["пласт", "цел"]],
            "slice": [["угор", "угр"], ["нарезк"]],
            "trim": [["угор", "угр"], ["обрезк"]],
            "waste": [["угор", "угр"], ["отход"]],
        },
        # «П/Ф угорь» — уже выход, а не рыба в разбор. Спутать их значит взять
        # цену с того, что из угря сделали, вместо цены самого угря.
        "avoid": {
            "raw": ["п/ф угорь"],
        },
    },
}

DEFAULT_KIND = "salmon"

# ── Передел: обрезки на сковороду ────────────────────────────────────────────
# Не разбор и не рыба: отдельный документ со своим сводом. Терияки делают только
# из лосося — у угря обрезки уходят иначе, и вкладки у него нет.
TERIYAKI_SPEC: Dict[str, Any] = {
    "key": "teriyaki",
    "title": "Терияки из обрезков",
    "tab": "Терияки",
    "trim_price": TRIM_PRICE,                # обрезки лосося — константа учёта
    "roles": ["trim", "teriyaki", "sauce"],
    "names": {
        "trim": ["П/Ф ОБРЕЗКИ ЛОСОСЬ"],
        "teriyaki": ["П/Ф ЛОСОСЬ ТЕРИЯКИ"],
        "sauce": ["СОУС ТЕРИЯКИ", "ТЕРИЯКИ СОУС", "Соус Терияки"],
    },
    "words": {
        "trim": [["лосос"], ["обрезк"]],
        "teriyaki": [["лосос"], ["терияк"]],
        "sauce": [["терияк"], ["соус"]],
    },
    "avoid": {
        "teriyaki": ["соус"],                # соус терияки — не П/Ф из обрезков
    },
}

# Совместимость с прежним кодом: разбор был только лососёвый.
RAW = KINDS["salmon"]["names"]["raw"][0]
TRIM = KINDS["salmon"]["names"]["trim"][0]
SLICE = KINDS["salmon"]["names"]["slice"][0]
WASTE = KINDS["salmon"]["names"]["waste"][0]
TERIYAKI = TERIYAKI_SPEC["names"]["teriyaki"][0]

# Слова выходов: по ним «просто рыба» отличается от нарезки и обрезков, когда
# вход назван не по шаблону («Угорь unagi 1 кг» вместо «Угорь целый»).
_OUTPUT_WORDS = ("нарезк", "обрезк", "отход", "терияк", "соус", "бонито")


def kind_spec(kind: Optional[str]) -> Dict[str, Any]:
    """Вид разбора по ключу. Неизвестный ключ — лосось: он был первым и он же
    остаётся тем, что открывается по старым ссылкам без ключа.

    Передел сюда не попадает намеренно: `teriyaki` — не рыба и не разбор, у него
    свой спек и свои маршруты, и подставлять вместо него лосося нельзя.
    """
    return KINDS.get((kind or "").strip().lower() or DEFAULT_KIND, KINDS[DEFAULT_KIND])


def roles_of(spec: Dict[str, Any]) -> List[str]:
    """Позиции, которые ищем в номенклатуре: у разбора свои, у передела свои."""
    return spec.get("roles") or ["raw", "slice", "trim", "waste"]


def _norm(s: str) -> str:
    return " ".join((s or "").lower().replace("ё", "е").split())


def _prod(p: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": p.get("id"), "name": p.get("name"), "unit": p.get("unit")}


def find_products(catalog: list, kind: str = DEFAULT_KIND) -> Dict[str, Optional[Dict[str, Any]]]:
    """Ищем позиции разбора по имени: id у каждого филиала свой (свой сервер).

    Три захода, от точного к терпимому: имя из справочника Алиева, затем набор
    слов в любом порядке («Угорь нарезка» и «П/Ф НАРЕЗКА УГОРЬ» — одно и то же),
    и только для входа — «рыба без слов выхода», иначе новый филиал с непривычным
    названием не соберёт акт вообще.
    """
    spec = TERIYAKI_SPEC if (kind or "").strip().lower() == "teriyaki" else kind_spec(kind)
    roles = roles_of(spec)
    out: Dict[str, Optional[Dict[str, Any]]] = {r: None for r in roles}
    items = [(_norm(p.get("name") or ""), p) for p in (catalog or [])]

    def _ok(role: str, n: str) -> bool:
        return not any(b in n for b in spec.get("avoid", {}).get(role, []))

    for role in roles:
        for n, p in items:
            if _ok(role, n) and any(n.startswith(_norm(x)) for x in spec["names"].get(role, [])):
                out[role] = _prod(p)
                break

    for role in roles:
        if out[role] is not None:
            continue
        for n, p in items:
            if _ok(role, n) and all(any(w in n for w in grp)
                                    for grp in spec["words"].get(role, [])):
                out[role] = _prod(p)
                break

    if out.get("raw", "нет такой роли") is None:
        fish = spec["words"]["raw"][0]           # слова самой рыбы: «лосос», «угор»
        for n, p in items:
            # `avoid` слушаем и здесь: без него «П/Ф угорь» проходил как рыба в
            # разбор — слов выхода в имени нет, а позиция всё равно выходная.
            if (_ok("raw", n) and any(w in n for w in fish)
                    and not any(b in n for b in _OUTPUT_WORDS)):
                out["raw"] = _prod(p)
                break
    return out


def split_act(price_per_kg: float, w_raw: float, w_slice: float,
              w_trim: float = 0.0, trim_price: Optional[float] = None,
              kind: str = DEFAULT_KIND) -> Dict[str, Any]:
    """Разбор в весах и деньгах. Только арифметика, в iiko не ходит.

    price_per_kg — себестоимость входа;
    w_raw        — сколько входа пошло в разбор;
    w_slice      — сколько получилось нарезки (её и взвешивают);
    w_trim       — сколько получилось обрезков;
    отход        — остаток веса: вход − нарезка − обрезки. Себестоимость 0 ₸,
                   поэтому отход НЕ уменьшает стоимость нарезки — только вес.

    ВЕСА СЧИТАЮТСЯ ВСЕГДА, деньги — только когда цены известны. Раньше расчёт
    упирался в ненайденную цену и возвращал ошибку целиком: у угря, где
    константы обрезков нет, экран не показывал ни отхода, ни процентов — при
    том что человек всё взвесил. Отход — это вес, и к цене он не относится.

    Ноль вместо неизвестной цены подставить нельзя: обрезки «бесплатно» молча
    отдают свои деньги нарезке, и она дорожает на ровном месте. Поэтому
    money=False и денежные поля пустые, а не нулевые — «неизвестно» и
    «бесплатно» на экране должны выглядеть по-разному.

    Что с обрезками делают дальше — не дело этого расчёта: жарка идёт ПОСЛЕ
    разбора, отдельным документом, и улучшить ею разделку рыбы нельзя.
    """
    spec = kind_spec(kind)
    trim_price = float(spec["trim_price"] if trim_price is None else trim_price or 0)
    price_per_kg = float(price_per_kg or 0)
    w_raw, w_slice, w_trim = float(w_raw or 0), float(w_slice or 0), float(w_trim or 0)

    if w_raw <= 0:
        return {"ok": False, "error": f"Вес {spec['raw_gen']} должен быть больше нуля"}
    if w_slice < 0 or w_trim < 0:
        return {"ok": False, "error": "Вес не может быть отрицательным"}
    w_waste = round(w_raw - w_slice - w_trim, 3)
    if w_waste < 0:
        return {"ok": False,
                "error": f"Нарезка и обрезки вместе тяжелее {spec['raw_gen']} — проверь весы"}

    # Цена обрезков нужна ровно тогда, когда обрезки есть: разбор без них
    # не должен упираться в цену того, чего в нём нет.
    need = ("price" if price_per_kg <= 0 else
            "trim_price" if (w_trim > 0 and trim_price <= 0) else "")
    money = not need

    weights = {
        "ok": True,
        "kind": spec["key"],
        "slice_pct": round(w_slice / w_raw * 100, 1),
        "trim_pct": round(w_trim / w_raw * 100, 1),
        "waste_pct": round(w_waste / w_raw * 100, 1),
        "money": money,
        "need": need,
        "trim_price": trim_price or None,
    }
    if not money:
        # Цена входа могла найтись, даже если не нашлась цена обрезков, —
        # показываем то, что знаем, и молчим о том, чего не знаем.
        raw_sum = round(price_per_kg * w_raw, 2) if price_per_kg > 0 else None
        return {
            **weights,
            "raw": {"weight": w_raw, "price_per_kg": price_per_kg or None, "sum": raw_sum},
            "slice": {"weight": w_slice, "price_per_kg": None, "sum": None},
            "trim": {"weight": w_trim, "price_per_kg": trim_price or None, "sum": None},
            "waste": {"weight": w_waste, "price_per_kg": 0.0, "sum": 0.0},
            "check": None,
        }

    raw_sum = round(price_per_kg * w_raw, 2)
    trim_sum = round(trim_price * w_trim, 2)
    waste_sum = 0.0                       # отход по нулю: так ведётся учёт
    slice_sum = round(raw_sum - trim_sum, 2)
    if slice_sum < 0:
        return {"ok": False,
                "error": f"Обрезки по {trim_price:.0f} ₸ дороже всего {spec['raw_gen']} — проверь цену"}
    per_kg = round(slice_sum / w_slice, 2) if w_slice > 0 else 0.0

    return {
        **weights,
        "raw": {"weight": w_raw, "price_per_kg": price_per_kg, "sum": raw_sum},
        "slice": {"weight": w_slice, "price_per_kg": per_kg, "sum": slice_sum},
        "trim": {"weight": w_trim, "price_per_kg": trim_price, "sum": trim_sum},
        "waste": {"weight": w_waste, "price_per_kg": 0.0, "sum": waste_sum},
        "trim_price": trim_price,
        # Сумма разложенных строк. Оператору её показывать нельзя — она равна
        # стоимости входа тождественно (нарезка и есть остаток), и зелёная
        # «сходимость» на экране горела бы даже когда всё ушло в отход. А вот
        # как инвариант в тестах она рабочая: поймает, если отход вдруг начнёт
        # стоить денег или в разложение заедет чужое слагаемое.
        "check": round(trim_sum + waste_sum + slice_sum, 2),
    }


# Разбор был только лососёвым — старое имя осталось рабочим.
split_salmon = split_act


# ── Передел: обрезки + соус → терияки ────────────────────────────────────────
# Отдельный документ, а не строка разбора. На сковороду кладут обрезки, льют
# соус и снимают терияки. Вход жарки — оба веса сразу: соус тоже лёг на
# сковороду, и выход считается от суммы, а не от одних обрезков.
#
# Формулы заданы владельцем и записаны буквально:
#   выход, %      = вес терияки / (вес обрезков + вес соуса) × 100
#   себестоимость = (сумма обрезков + сумма соуса) × (1 + отход),
#                   где отход — доля ужарки во входе, то есть 1 − выход.
# Вторая формула не переносит деньги, а наращивает их на ужарку: сумма
# оприходования получается БОЛЬШЕ суммы списания, и свод на эту разницу с iiko
# не сойдётся. Так решено намеренно — см. `check`, где лежат вложенные деньги.

def fry_teriyaki(w_fry: float, w_out: float, trim_price: Optional[float] = None,
                 g_sauce: float = 0.0, sauce_price: float = 0.0) -> Dict[str, Any]:
    """Жарка в весах и деньгах.

    w_fry       — сколько килограммов обрезков положили на сковороду;
    w_out       — сколько килограммов терияки с неё сняли. Разница — ужарка;
    trim_price  — себестоимость обрезков;
    g_sauce     — сколько ГРАММОВ соуса ушло. Граммы, а не килограммы: соус
                  льют половником, и в килограммах повар ошибается на порядок;
    sauce_price — себестоимость соуса, ₸ за килограмм.

    Ужарка и выход жарки — это веса, и считаются они всегда. Пока расчёт
    падал на ненайденной цене обрезков, главная цифра сковороды не доезжала
    до экрана из-за того, что к ней не относится.
    """
    trim_price = float(TRIM_PRICE if trim_price is None else trim_price or 0)
    w_fry, w_out = float(w_fry or 0), float(w_out or 0)
    g_sauce, sauce_price = float(g_sauce or 0), float(sauce_price or 0)

    if w_fry <= 0:
        return {"ok": False, "error": "Вес обрезков в жарку должен быть больше нуля"}
    if w_out <= 0:
        return {"ok": False, "error": "Впиши, сколько терияки сняли со сковороды"}
    if g_sauce < 0:
        return {"ok": False, "error": "Вес не может быть отрицательным"}

    # Вход жарки — всё, что легло на сковороду. Соус меряют граммами, вход —
    # килограммами, поэтому граммы делятся на тысячу ровно здесь и больше нигде.
    w_sauce = g_sauce / 1000
    w_in = w_fry + w_sauce
    if w_out > w_in + 1e-9:
        return {"ok": False,
                "error": "Терияки тяжелее того, что пошло в жарку — при жарке вес уходит"}

    w_shrink = round(w_in - w_out, 3)
    shrink = (w_in - w_out) / w_in          # доля отхода: ею и дорожает терияки
    need = ("trim_price" if trim_price <= 0 else
            "sauce_price" if (g_sauce > 0 and sauce_price <= 0) else "")
    money = not need

    weights = {
        "ok": True,
        "kind": "teriyaki",
        "trim_price": trim_price or None,
        "sauce_price": sauce_price or None,
        "input_weight": round(w_in, 4),      # обрезки + соус, вход жарки
        # выход жарки: сколько процентов сковорода вернула. Ниже — больше ужарка
        "yield_pct": round(w_out / w_in * 100, 1),
        "shrink_pct": round(shrink * 100, 1),
        # соуса на килограмм терияки: по этой цифре и ловится перелив
        "sauce_g_per_kg": round(g_sauce / w_out, 1),
        "money": money,
        "need": need,
    }
    if not money:
        return {
            **weights,
            "trim": {"weight": w_fry, "price_per_kg": trim_price or None, "sum": None},
            "sauce": {"grams": round(g_sauce, 1), "weight": round(w_sauce, 4),
                      "price_per_kg": sauce_price or None, "sum": None},
            "teriyaki": {"weight": w_out, "price_per_kg": None, "sum": None,
                         "from_trim": w_fry, "from_input": round(w_in, 4),
                         "shrink": w_shrink},
            "check": None,
        }

    trim_sum = round(trim_price * w_fry, 2)
    sauce_sum = round(sauce_price * g_sauce / 1000, 2)
    spent = round(trim_sum + sauce_sum, 2)           # столько списываем со склада
    # (сумма обрезков + сумма соуса) × (1 + отход) — формула владельца
    out_sum = round(spent * (1 + shrink), 2)
    return {
        **weights,
        "trim": {"weight": w_fry, "price_per_kg": trim_price, "sum": trim_sum},
        "sauce": {"grams": round(g_sauce, 1), "weight": round(w_sauce, 4),
                  "price_per_kg": sauce_price, "sum": sauce_sum},
        "teriyaki": {"weight": w_out, "price_per_kg": round(out_sum / w_out, 2),
                     "sum": out_sum, "from_trim": w_fry, "from_input": round(w_in, 4),
                     "shrink": w_shrink},
        "spent": spent,                      # обрезки + соус, вложенные деньги
        "markup": round(out_sum - spent, 2), # на столько приход больше списания
        "check": spent,                      # = обрезки + соус, всё что вложили
    }


# ── Свод за период ───────────────────────────────────────────────────────────
# Отдельно от split_act и без базы: складывать зафиксированные разборы —
# другая задача, и проверять её надо на голых числах.

def summarize(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Итог по пачке разборов — то, что администратор заносит в iiko одним актом.

    Проценты и себестоимость — средневзвешенные, а не среднее по актам: разбор
    на 40 кг и разбор на 2 кг не имеют одинакового веса в оценке филиала.
    """
    n = 0
    raw = slice_w = trim = waste = 0.0
    raw_sum = slice_sum = trim_sum = 0.0
    for r in rows:
        n += 1
        w_raw = float(r.get("weight_raw") or 0)
        w_slice = float(r.get("weight_slice") or 0)
        # Разборы, записанные ДО выноса жарки, держат обрезки в двух колонках:
        # weight_trim — остаток, weight_fry — то, что ушло на сковороду; при
        # записи второе вычиталось из первого. Обрезки есть обрезки, и в своде
        # их столько, сколько вышло из рыбы. Не сложить их обратно — значит
        # потерять килограммы вместе с деньгами и завысить нарезку. У новых
        # разборов weight_fry пуст, и слагаемое ничего не меняет.
        w_trim = float(r.get("weight_trim") or 0) + float(r.get("weight_fry") or 0)
        raw += w_raw
        slice_w += w_slice
        trim += w_trim
        waste += float(r.get("weight_waste") or 0)
        price = float(r.get("price_per_kg") or 0)
        tprice = float(r.get("trim_price") or TRIM_PRICE)
        raw_sum += price * w_raw
        trim_sum += tprice * w_trim
        slice_sum += price * w_raw - tprice * w_trim
    return {
        "acts": n,
        "raw": round(raw, 3), "slice": round(slice_w, 3),
        "trim": round(trim, 3), "waste": round(waste, 3),
        "raw_sum": round(raw_sum, 2), "slice_sum": round(slice_sum, 2),
        "trim_sum": round(trim_sum, 2),
        "slice_price": round(slice_sum / slice_w, 2) if slice_w > 0 else 0.0,
        "raw_price": round(raw_sum / raw, 2) if raw > 0 else 0.0,
        "slice_pct": round(slice_w / raw * 100, 1) if raw > 0 else 0.0,
        "trim_pct": round(trim / raw * 100, 1) if raw > 0 else 0.0,
        "waste_pct": round(waste / raw * 100, 1) if raw > 0 else 0.0,
    }


def summarize_teriyaki(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Итог по жаркам за период — второй акт, который заносят в iiko.

    Цены берём те, что записаны в самой жарке: пересчёт задним числом по
    сегодняшней накладной сдвинул бы уже проведённый документ.

    Формулы те же, что в fry_teriyaki: вход — обрезки вместе с соусом, а сумма
    прихода наращена на отход. Считать свод иначе, чем жарки, нельзя — он их
    складывает, и расхождение вылезло бы прямо в акте для iiko.
    """
    n = 0
    fry = out = sauce_g = 0.0
    trim_sum = sauce_sum = 0.0
    for r in rows:
        n += 1
        w_fry = float(r.get("weight_fry") or 0)
        w_out = float(r.get("weight_teriyaki") or 0)
        g_s = float(r.get("weight_sauce") or 0)          # граммы, а не килограммы
        fry += w_fry
        out += w_out
        sauce_g += g_s
        trim_sum += float(r.get("trim_price") or TRIM_PRICE) * w_fry
        sauce_sum += float(r.get("sauce_price") or 0) * g_s / 1000
    w_in = fry + sauce_g / 1000                  # вход жарок за период
    shrink = (w_in - out) / w_in if w_in > 0 else 0.0
    spent = trim_sum + sauce_sum                 # столько списываем со склада
    total = spent * (1 + shrink)                 # столько приходуем — формула владельца
    return {
        "acts": n,
        "fry": round(fry, 3), "teriyaki": round(out, 3),
        "input": round(w_in, 3),
        "shrink": round(w_in - out, 3),
        "sauce": round(sauce_g, 1), "sauce_sum": round(sauce_sum, 2),
        "trim_sum": round(trim_sum, 2), "teriyaki_sum": round(total, 2),
        "spent": round(spent, 2), "markup": round(total - spent, 2),
        "teriyaki_price": round(total / out, 2) if out > 0 else 0.0,
        "trim_price": round(trim_sum / fry, 2) if fry > 0 else 0.0,
        "sauce_price": round(sauce_sum / sauce_g * 1000, 2) if sauce_g > 0 else 0.0,
        "sauce_g_per_kg": round(sauce_g / out, 1) if out > 0 else 0.0,
        "yield_pct": round(out / w_in * 100, 1) if w_in > 0 else 0.0,
        "shrink_pct": round(shrink * 100, 1) if w_in > 0 else 0.0,
    }


def by_branch(rows: Iterable[Dict[str, Any]],
              names: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """Свод по филиалам — публичная таблица «где нарезка дешевле и отхода меньше».

    Сортируем по % отхода: у кого рыба уходит в кости меньше всех, тот и наверху.
    """
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        groups.setdefault(r.get("organization_id") or "", []).append(r)
    out = []
    for org, rs in groups.items():
        s = summarize(rs)
        s["organization_id"] = org
        s["name"] = (names or {}).get(org, org)
        out.append(s)
    out.sort(key=lambda s: (s["waste_pct"], s["slice_price"]))
    return out
