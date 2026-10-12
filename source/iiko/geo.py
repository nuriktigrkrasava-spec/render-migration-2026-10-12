"""Проверка «сотрудник на месте»: расстояние от филиала до точки отметки.

Чистые функции без сети и БД — их можно гонять тестами, а решение о том, что
делать с результатом, принимает вызывающий. Решение владельца: несовпадение
смену НЕ блокирует, а помечает её — GPS в помещении врёт на сотни метров, и
повар, который не может начать работу из-за спутников, пойдёт звонить
управляющему вместо того, чтобы готовить.
"""
from math import asin, cos, radians, sin, sqrt
from typing import Optional

EARTH_R_M = 6_371_000.0
DEFAULT_RADIUS_M = 250


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Расстояние по большому кругу в метрах (гаверсинус).

    Плоской формулой не считаем: на широте Астаны градус долготы почти вдвое
    короче градуса широты, и радиус получился бы вытянутым эллипсом.
    """
    p1, p2 = radians(lat1), radians(lat2)
    dp, dl = p2 - p1, radians(lon2 - lon1)
    h = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * EARTH_R_M * asin(min(1.0, sqrt(h)))


def within(branch: Optional[dict], lat: Optional[float], lon: Optional[float]
           ) -> Optional[bool]:
    """На месте ли человек. None — «проверить нечем»: точка филиала не задана
    или сотрудник не дал геопозицию. Это не то же самое, что False («пришёл
    не туда»), и в интерфейсе выглядеть должно по-разному.
    """
    if not branch or branch.get("lat") is None or lat is None or lon is None:
        return None
    radius = int(branch.get("radius") or DEFAULT_RADIUS_M)
    return distance_m(branch["lat"], branch["lon"], lat, lon) <= radius
