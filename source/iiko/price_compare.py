"""Сравнение цен по накладной: где то же самое покупали дешевле.

Здесь только арифметика — ни iiko, ни базы. На вход идут уже полученные строки
накладных (OLAP filtered), на выход — лучшая цена по каждому товару и итог
«сколько переплатили этой накладной».

Почему отдельным модулем: та же математика нужна и экрану пробития, и сводке
по филиалу, и (если понадобится) утреннему отчёту. Считать её в трёх местах
по-разному — верный способ получить три разные переплаты за одну накладную.
"""
from typing import Any, Dict, Iterable, List, Optional

MIN_OVER_PCT = 1.0        # дешевле на копейку — это не находка, а шум округления


def best_by_product(rows: Iterable[Dict[str, Any]], product_ids: Iterable[str],
                    branch: str = "") -> Dict[str, Dict[str, Any]]:
    """Самая низкая цена по каждому товару: {product_id: {...}}.

    rows — строки накладных (product_id, price_per_unit, supplier_name, date, unit).
    branch — подпись филиала, откуда строки: в сетевом сравнении без неё непонятно,
    к кому идти за этой ценой.
    """
    want = set(product_ids)
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        pid = r.get("product_id")
        if pid not in want:
            continue
        price = float(r.get("price_per_unit") or 0)
        if price <= 0:
            continue
        cur = out.get(pid)
        if cur is None or price < cur["price_per_unit"]:
            out[pid] = {
                "price_per_unit": price,
                "supplier_name": r.get("supplier_name") or "—",
                "supplier_id": r.get("supplier_id") or "",
                "date": r.get("date") or "",
                "unit": r.get("unit") or "",
                "branch": branch,
            }
    return out


def merge_best(parts: Iterable[Dict[str, Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
    """Лучшее по сети: из нескольких филиалов оставляем самую низкую цену."""
    out: Dict[str, Dict[str, Any]] = {}
    for part in parts:
        for pid, b in part.items():
            cur = out.get(pid)
            if cur is None or b["price_per_unit"] < cur["price_per_unit"]:
                out[pid] = b
    return out


def overpay(items: List[Dict[str, Any]], best: Dict[str, Dict[str, Any]],
            min_pct: float = MIN_OVER_PCT) -> Dict[str, Any]:
    """Итог по накладной: где дороже и на сколько денег.

    Переплата считается на ФАКТИЧЕСКОЕ количество: «дороже на 12 %» ни о чём не
    говорит, пока не видно, что это 12 % от одной пачки или от двадцати ящиков.
    Позиции без истории закупок молчат — это не «дёшево», это «не с чем сравнить».
    """
    lines, total, cheaper = [], 0.0, 0
    for it in items:
        pid = it.get("iiko_product_id") or ""
        b = best.get(pid)
        ours = float(it.get("price_per_unit") or 0)
        if not b or ours <= 0 or b["price_per_unit"] <= 0:
            continue
        diff_pct = (ours - b["price_per_unit"]) / b["price_per_unit"] * 100
        if diff_pct < -min_pct:
            cheaper += 1                       # взяли дешевле прошлого лучшего
            continue
        if diff_pct <= min_pct:
            continue
        qty = float(it.get("quantity") or 0)
        money = round((ours - b["price_per_unit"]) * qty, 2)
        lines.append({
            "product_id": pid,
            "name": it.get("iiko_product_name") or it.get("name") or "—",
            "quantity": qty, "unit": it.get("unit") or b.get("unit") or "",
            "our_price": ours, "best_price": b["price_per_unit"],
            "over_pct": round(diff_pct, 1), "money": money,
            "supplier_name": b["supplier_name"], "branch": b.get("branch") or "",
            "date": b.get("date") or "",
        })
        total += money
    lines.sort(key=lambda r: r["money"], reverse=True)
    return {
        "lines": lines,
        "positions": len(lines),
        "cheaper": cheaper,
        "compared": sum(1 for it in items if (it.get("iiko_product_id") or "") in best),
        "total": round(total, 2),
    }
