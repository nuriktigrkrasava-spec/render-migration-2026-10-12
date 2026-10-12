"""Отзывы и рейтинги филиалов с внешних площадок (2ГИС, Wolt).

Автопоиск точек по названию/адресу, кэш привязок в БД, ежедневные
снапшоты рейтинга для определения динамики (упал/вырос).
"""
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
BRAND_QUERY = "Saya Sushi"

# Ключ публичного API отзывов 2ГИС (используется их собственным виджетом)
REVIEWS_2GIS_KEY = os.getenv("REVIEWS_2GIS_KEY", "")
# Каталожный ключ — вытаскивается со страницы 2gis.kz; этот служит запасным
_catalog_key_cache: Dict[str, Any] = {"key": os.getenv("CATALOG_2GIS_KEY", ""), "ts": 0}

CITY_COORDS = {
    "астана": (51.1605, 71.4704),
    "актау": (43.6481, 51.1722),
    "актобе": (50.2839, 57.1670),
    "атырау": (47.0945, 51.9238),
    "костанай": (53.2198, 63.6354),
    "алматы": (43.2389, 76.8897),
}

# казахские буквы → русские аналоги для сравнения адресов
_KZ = str.maketrans("әғқңөұүhіһ", "агкноууhих")

_TRANSLIT = {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh",
             "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n",
             "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f",
             "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch", "ы": "y",
             "э": "e", "ю": "yu", "я": "ya", "ь": "", "ъ": ""}


def _norm(s: str) -> str:
    return re.sub(r"[^а-яa-z0-9ё]", "", (s or "").lower().translate(_KZ))


def _translit(s: str) -> str:
    return "".join(_TRANSLIT.get(ch, ch) for ch in (s or "").lower())


def parse_org_name(org_name: str) -> Tuple[str, str, str, List[str]]:
    """'Астана, ул.Достык 13 (бывш. Чубары)' → (город, улица, номер, [подсказки])."""
    city = org_name.split(",")[0].strip().lower()
    hints = re.findall(r"\(([^)]+)\)", org_name)
    hint_words = []
    for h in hints:
        hint_words += [w for w in re.findall(r"[А-Яа-яЁёA-Za-z]{4,}", h)
                       if w.lower() not in ("бывш",)]
    rest = org_name.split(",", 1)[1] if "," in org_name else org_name
    rest = re.sub(r"\([^)]*\)", "", rest).strip()
    # последняя числовая группа = номер дома, всё до неё = улица/микрорайон
    m = re.search(r"^(.*?)[\s,]*(\d+[а-яa-zА-ЯёЁ/]*)\s*$", rest)
    if m and m.group(1).strip():
        street = re.sub(r"\b(ул\.?|улица|мкр\.?|микрорайон|пр\.?|проспект)\s*", "",
                        m.group(1).strip(), flags=re.I).strip(" .,")
        return city, street, m.group(2).lower(), hint_words
    return city, rest, "", hint_words


def _street_match(street: str, number: str, hint_words: List[str],
                  candidate_addr: str, candidate_name: str = "") -> bool:
    """Совпадает ли адрес кандидата с улицей/номером/подсказками филиала."""
    cand = _norm(candidate_addr)
    num_ok = not number or bool(re.search(rf"(?<!\d){re.escape(number)}(?!\d)", cand))
    # улица: пробуем целиком и самое длинное слово («Г.Алиева» → «алиева»,
    # совпадает с «проспект Гейдара Алиева»)
    words = re.findall(r"[А-Яа-яЁёІіҢңҒғҮүҰұҚқӨөӘәA-Za-z]{4,}", street) or [street]
    stems = [street] + sorted(words, key=len, reverse=True)
    for s in stems:
        st = _norm(s)
        stem = st[:max(6, len(st) - 2)] if len(st) >= 6 else st
        if stem and stem in cand and num_ok:
            return True
    # запасной вариант: подсказка из скобок транслитом в названии (Чубары → chubary,
    # EXPO → expo)
    name_l = (candidate_name or "").lower()
    for w in hint_words + words:
        wl = w.lower()
        tr = _translit(wl)
        if (len(tr) >= 4 and tr in name_l) or (len(wl) >= 4 and wl in name_l):
            return True
    return False


async def _get_2gis_catalog_key(client: httpx.AsyncClient) -> str:
    """Рабочий ключ каталога 2ГИС: из кэша или со страницы 2gis.kz (раз в сутки)."""
    if time.time() - _catalog_key_cache["ts"] < 86400 and _catalog_key_cache["key"]:
        return _catalog_key_cache["key"]
    try:
        r = await client.get("https://2gis.kz/astana", headers=UA, timeout=15.0,
                             follow_redirects=True)
        m = re.search(r'webApiOutsourceKey":"([a-f0-9-]{36})"', r.text)
        if m:
            _catalog_key_cache.update(key=m.group(1), ts=time.time())
    except Exception as e:
        logger.warning(f"2gis key refresh failed: {e}")
    return _catalog_key_cache["key"]


async def discover_2gis(org_name: str, brand: str = BRAND_QUERY) -> Optional[Dict[str, Any]]:
    """Найти филиал в 2ГИС по названию бренда и адресу → {external_id, title}."""
    city, street, number, hints = parse_org_name(org_name)
    coords = CITY_COORDS.get(city)
    if not coords:
        return None
    lat, lon = coords
    async with httpx.AsyncClient(timeout=20.0) as c:
        key = await _get_2gis_catalog_key(c)
        # два прохода: по бренду, затем по бренду+улице (для редких адресов)
        brand_token = brand.split()[0].lower()
        for query in (brand, f"{brand} {street}"):
            r = await c.get("https://catalog.api.2gis.com/3.0/items",
                            params={"q": query, "location": f"{lon},{lat}",
                                    "key": key, "page_size": 12,
                                    "fields": "items.reviews,items.address_name"},
                            headers=UA)
            items = r.json().get("result", {}).get("items", [])
            for it in items:
                if brand_token not in it.get("name", "").lower():
                    continue
                if _street_match(street, number, hints,
                                 it.get("address_name", ""), it.get("name", "")):
                    return {"external_id": it["id"],
                            "title": f"{it.get('name', '')} — {it.get('address_name', '')}"}
    return None


async def fetch_2gis(branch_id: str) -> Optional[Dict[str, Any]]:
    """Рейтинг + свежие отзывы точки 2ГИС."""
    async with httpx.AsyncClient(timeout=20.0) as c:
        r = await c.get(f"https://public-api.reviews.2gis.com/2.0/branches/{branch_id}/reviews",
                        params={"limit": 10, "key": REVIEWS_2GIS_KEY,
                                "fields": "meta.branch_rating,meta.branch_reviews_count"},
                        headers=UA)
        if r.status_code != 200:
            return None
        d = r.json()
    meta = d.get("meta", {})
    reviews = [{
        "rating": rv.get("rating"),
        "text": (rv.get("text") or "").strip(),
        "date": (rv.get("date_created") or "")[:10],
        "author": (rv.get("user") or {}).get("name", ""),
    } for rv in d.get("reviews", [])]
    return {"rating": meta.get("branch_rating"),
            "reviews_count": meta.get("branch_reviews_count"),
            "reviews": reviews}


async def discover_wolt(org_name: str, brand: str = BRAND_QUERY) -> Optional[Dict[str, Any]]:
    """Найти точку в Wolt → {external_id: slug, title}."""
    brand_token = brand.split()[0].lower()
    city, street, number, hints = parse_org_name(org_name)
    coords = CITY_COORDS.get(city)
    if not coords:
        return None
    lat, lon = coords
    async with httpx.AsyncClient(timeout=20.0) as c:
        r = await c.post("https://consumer-api.wolt.com/v1/pages/search",
                         json={"q": brand_token, "target": "venues", "lat": lat, "lon": lon},
                         headers={**UA, "Content-Type": "application/json"})
        if r.status_code != 200:
            return None
        d = r.json()
    venues = []
    for sec in d.get("sections", []):
        for it in sec.get("items", []):
            v = it.get("venue")
            if v and brand_token in (v.get("name", "") + v.get("slug", "")).lower():
                venues.append(v)
    for v in venues:
        if _street_match(street, number, hints, v.get("address", ""),
                         v.get("name", "") + " " + v.get("slug", "")):
            return {"external_id": v["slug"], "title": v.get("name", ""), "extra": city}
    return None


async def fetch_wolt(slug: str, city: str = "", brand: str = BRAND_QUERY) -> Optional[Dict[str, Any]]:
    """Рейтинг точки Wolt (шкала 10) через публичную выдачу поиска по слагу."""
    brand_token = brand.split()[0].lower()
    cities = ([city] if city in CITY_COORDS else list(CITY_COORDS))
    async with httpx.AsyncClient(timeout=20.0) as c:
        # у Wolt нет публичной карточки по слагу без токена — ищем заново и фильтруем
        for ct in cities:
            lat, lon = CITY_COORDS[ct]
            r = await c.post("https://consumer-api.wolt.com/v1/pages/search",
                             json={"q": brand_token, "target": "venues", "lat": lat, "lon": lon},
                             headers={**UA, "Content-Type": "application/json"})
            if r.status_code != 200:
                continue
            for sec in r.json().get("sections", []):
                for it in sec.get("items", []):
                    v = it.get("venue")
                    if v and v.get("slug") == slug:
                        rt = v.get("rating") or {}
                        return {"rating": rt.get("score"), "reviews_count": None}
    return None
