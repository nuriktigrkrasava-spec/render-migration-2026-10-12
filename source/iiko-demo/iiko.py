
import httpx
import asyncio
import hashlib
import re
import secrets
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, date
from typing import List, Dict, Any, Optional, Tuple
from urllib.parse import urlparse

IIKO_CLOUD_BASE = "https://api-ru.iiko.services"

_client_cache: Dict[str, Any] = {}


def _normalize_iiko_url(url: str) -> str:
    """Allow user-entered URLs like 'saya-sushi-aktobe.iiko.it' or
    'saya-sushi-aktobe.iiko.it/resto' → coerce to canonical https://host/resto."""
    u = (url or "").strip()
    if not u:
        return u
    if not u.startswith(("http://", "https://")):
        u = "https://" + u
    u = u.rstrip("/")
    # iikoServer endpoints always live under /resto
    if not u.endswith("/resto"):
        u = u + "/resto"
    return u


def parse_containers(p: dict) -> list:
    """Толерантно достаёт фасовки (контейнеры) товара из ответа iiko:
    [{'name': <название>, 'factor': <сколько БАЗОВЫХ единиц товара в 1 фасовке>}].
    Имена полей у разных установок iiko могут отличаться, поэтому пробуем несколько.
    Если ничего не нашли — для диагностики кладём сырые ключи в _raw (видно на деплое)."""
    raw = (p.get("containers") or p.get("productContainers")
           or p.get("packagings") or p.get("measureUnits") or [])
    out = []
    if isinstance(raw, list):
        for c in raw:
            if not isinstance(c, dict):
                continue
            name = c.get("name") or c.get("shortName") or c.get("fullName") or ""
            factor = (c.get("count") or c.get("fullCount") or c.get("containerWeight")
                      or c.get("weight") or c.get("amount") or c.get("coefficient")
                      or c.get("factor") or 0)
            try:
                factor = float(factor)
            except (TypeError, ValueError):
                factor = 0.0
            if name and factor:
                out.append({"name": str(name), "factor": factor})
    # Диагностика структуры: какие container-подобные ключи реально есть у товара
    if not out:
        hints = [k for k in p.keys()
                 if any(s in k.lower() for s in ("contain", "pack", "unit", "фасов"))]
        if hints:
            out = []  # пусто, но ключи покажем через _raw на product-уровне
            p["_container_keys"] = hints
    return out


def get_client(api_login: str, iiko_server: str = None, api_password: str = None):
    if iiko_server:
        iiko_server = _normalize_iiko_url(iiko_server)
        key = f"web:{api_login}@{iiko_server}"
        cached = _client_cache.get(key)
        # Если пароль изменился — пересоздать клиент
        if cached and api_password and getattr(cached, "password", None) != api_password:
            cached = None
        if cached is None:
            _client_cache[key] = IikoWebClient(iiko_server, api_login, api_password or api_login)
        return _client_cache[key]
    key = f"cloud:{api_login}"
    if key not in _client_cache:
        _client_cache[key] = IikoClient(api_login)
    return _client_cache[key]


def _dt(date_str: str, end: bool = False) -> str:
    if "T" in date_str:
        return date_str
    if " " in date_str:
        return date_str.replace(" ", "T")
    return f"{date_str}T{'23:59:59' if end else '00:00:00'}"


# ============ iiko CLOUD CLIENT ============

class IikoClient:
    supports_cost = True  # облачный API отдаёт себестоимость (DishCostSumInt)

    def __init__(self, api_login: str):
        self.api_login = api_login
        self.token: Optional[str] = None
        self.token_expires: Optional[datetime] = None
        self._lock = asyncio.Lock()
        self._columns_cache: Dict[str, List[str]] = {}  # org_id+type → доступные поля

    async def _ensure_token(self):
        async with self._lock:
            if self.token and self.token_expires and datetime.now() < self.token_expires:
                return self.token
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{IIKO_CLOUD_BASE}/api/1/access_token",
                    headers={"Content-Type": "application/json"},
                    json={"apiLogin": self.api_login}
                )
                resp.raise_for_status()
                data = resp.json()
                self.token = data["token"]
                self.token_expires = datetime.now() + timedelta(minutes=14)
                return self.token

    async def _invalidate_token(self, only_if: str):
        async with self._lock:
            if self.token == only_if:
                self.token = None
                self.token_expires = None

    async def get_organizations(self) -> List[Dict[str, Any]]:
        token = await self._ensure_token()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{IIKO_CLOUD_BASE}/api/1/organizations",
                headers={"Authorization": f"Bearer {token}"},
                json={"returnAdditionalInfo": True}
            )
            resp.raise_for_status()
            return resp.json().get("organizations", [])

    async def get_nomenclature(self, org_id: str) -> Dict[str, Any]:
        token = await self._ensure_token()
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{IIKO_CLOUD_BASE}/api/1/nomenclature",
                headers={"Authorization": f"Bearer {token}"},
                json={"organizationId": org_id}
            )
            resp.raise_for_status()
            return resp.json()

    async def get_stop_lists(self, org_ids: List[str]) -> Dict[str, Any]:
        token = await self._ensure_token()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{IIKO_CLOUD_BASE}/api/1/stop_lists",
                headers={"Authorization": f"Bearer {token}"},
                json={"organizationIds": org_ids}
            )
            resp.raise_for_status()
            return resp.json()

    async def get_olap_columns(self, org_id: str, report_type: str = "SALES") -> List[str]:
        """Получает список доступных OLAP-полей для организации."""
        cache_key = f"{org_id}:{report_type}"
        if cache_key in self._columns_cache:
            return self._columns_cache[cache_key]
        try:
            token = await self._ensure_token()
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{IIKO_CLOUD_BASE}/api/1/reports/olap/columns",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"organizationId": org_id, "reportType": report_type}
                )
                if resp.status_code == 200:
                    data = resp.json()
                    columns = [c.get("name", "") for c in data.get("columnList", [])]
                    self._columns_cache[cache_key] = columns
                    return columns
        except Exception:
            pass
        return []  # если не удалось — не фильтруем

    def _filter_fields(self, fields: List[str], available: List[str]) -> List[str]:
        if not available:
            return fields
        return [f for f in fields if f in available]

    async def olap_report(
        self,
        org_id: str,
        date_from: str,
        date_to: str,
        group_by: List[str],
        aggregates: List[str]
    ) -> Dict[str, Any]:
        # Фильтруем поля по доступным для этой организации
        available = await self.get_olap_columns(org_id)
        if available:
            group_by = self._filter_fields(group_by, available)
            aggregates = self._filter_fields(aggregates, available)

        payload = {
            "organizationId": org_id,
            "settings": {
                "queryFilter": {
                    "dateFrom": _dt(date_from),
                    "dateTo": _dt(date_to, end=True)
                },
                "groupByRowFields": group_by,
                "aggregateFields": aggregates
            }
        }
        for attempt in range(2):
            token = await self._ensure_token()
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    f"{IIKO_CLOUD_BASE}/api/1/reports/olap",
                    headers={"Authorization": f"Bearer {token}"},
                    json=payload
                )
                if resp.status_code == 401 and attempt == 0:
                    await self._invalidate_token(only_if=token)
                    continue
                resp.raise_for_status()
                return resp.json()
        raise Exception("OLAP 401: токен обновлён, но доступ отклонён.")

    async def get_orders_with_dishes(self, org_id: str, date_from: str, date_to: str) -> Dict[str, Any]:
        return await self.olap_report(
            org_id, date_from, date_to,
            ["OrderNum", "DishName"], ["DishCount"]
        )

    async def get_deliveries(self, org_id: str, date_from: str, date_to: str) -> List[Dict[str, Any]]:
        token = await self._ensure_token()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{IIKO_CLOUD_BASE}/api/1/deliveries/by_delivery_date_and_status",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "organizationIds": [org_id],
                    "deliveryDateFrom": _dt(date_from),
                    "deliveryDateTo": _dt(date_to, end=True)
                }
            )
            resp.raise_for_status()
            return resp.json().get("deliveries", [])

    async def get_invoices(self, org_ids: List[str], date_from: str, date_to: str) -> List[Dict[str, Any]]:
        token = await self._ensure_token()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{IIKO_CLOUD_BASE}/api/1/documents/export/invoice_incoming",
                headers={"Authorization": f"Bearer {token}"},
                json={"organizationIds": org_ids, "dateFrom": _dt(date_from), "dateTo": _dt(date_to, end=True)}
            )
            resp.raise_for_status()
            return resp.json()

    async def get_expense_orders(self, org_ids: List[str], date_from: str, date_to: str) -> List[Dict[str, Any]]:
        token = await self._ensure_token()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{IIKO_CLOUD_BASE}/api/1/documents/export/expense_invoice",
                headers={"Authorization": f"Bearer {token}"},
                json={"organizationIds": org_ids, "dateFrom": _dt(date_from), "dateTo": _dt(date_to, end=True)}
            )
            resp.raise_for_status()
            return resp.json()

    async def get_writeoffs(self, org_ids: List[str], date_from: str, date_to: str) -> Dict[str, Any]:
        token = await self._ensure_token()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{IIKO_CLOUD_BASE}/api/1/documents/export/write_off",
                headers={"Authorization": f"Bearer {token}"},
                json={"organizationIds": org_ids, "dateFrom": _dt(date_from), "dateTo": _dt(date_to, end=True)}
            )
            resp.raise_for_status()
            return resp.json()

    async def get_expense_acts(self, org_ids: List[str], date_from: str, date_to: str) -> Dict[str, Any]:
        token = await self._ensure_token()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{IIKO_CLOUD_BASE}/api/1/documents/export/expense",
                headers={"Authorization": f"Bearer {token}"},
                json={"organizationIds": org_ids, "dateFrom": _dt(date_from), "dateTo": _dt(date_to, end=True)}
            )
            resp.raise_for_status()
            return resp.json()

    # ============ SUPPLIERS / NOMENCLATURE / DOCUMENTS — invoice flow ============

    async def get_suppliers(self, org_id: str) -> List[Dict[str, Any]]:
        """Список поставщиков организации. Cloud API: POST /api/1/suppliers."""
        token = await self._ensure_token()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{IIKO_CLOUD_BASE}/api/1/suppliers",
                headers={"Authorization": f"Bearer {token}"},
                json={"organizationId": org_id}
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            # iiko возвращает либо {"employees": [...]}, либо {"suppliers": [...]} — поддерживаем оба
            return data.get("suppliers") or data.get("employees") or []

    async def search_nomenclature(self, org_id: str, query: str,
                                  limit: int = 30) -> List[Dict[str, Any]]:
        """Поиск товаров по имени. Загружает полную номенклатуру (кешируется на 30 мин),
        потом фильтрует локально."""
        cache = getattr(self, "_nomenclature_cache", None)
        if cache is None:
            self._nomenclature_cache: Dict[str, Tuple[datetime, List[Dict]]] = {}
            cache = self._nomenclature_cache

        cached = cache.get(org_id)
        if cached and (datetime.now() - cached[0]) < timedelta(minutes=30):
            products = cached[1]
        else:
            try:
                full = await self.get_nomenclature(org_id)
                products = full.get("products", []) or []
                cache[org_id] = (datetime.now(), products)
            except Exception:
                return []

        q = (query or "").strip().lower()
        if not q:
            return products[:limit]
        out = []
        for p in products:
            name = (p.get("name") or "").lower()
            num = (p.get("num") or "").lower()
            code = (p.get("code") or "").lower()
            if q in name or q in num or q in code:
                out.append(p)
                if len(out) >= limit:
                    break
        return out

    async def get_invoice_items_olap(
        self, org_ids: List[str], date_from: str, date_to: str
    ) -> List[Dict[str, Any]]:
        """Flat per-line-item rows from incoming invoices, used by /api/price-stats.
        Cloud-path: reuses invoice_incoming export and flattens (supplier, date, item) → row."""
        raw = await self.get_invoices(org_ids, date_from, date_to)
        invoices = raw if isinstance(raw, list) else raw.get("invoiceIncomings", []) if isinstance(raw, dict) else []
        out: List[Dict[str, Any]] = []
        for inv in invoices:
            sup = inv.get("supplier")
            if isinstance(sup, dict):
                supplier_name = sup.get("name") or "—"
                supplier_id = sup.get("id") or supplier_name
            else:
                supplier_name = sup or inv.get("supplierName") or "—"
                supplier_id = inv.get("supplierId") or supplier_name
            inv_date = (inv.get("dateIncoming") or inv.get("documentDate") or "")[:10]
            for it in (inv.get("items") or []):
                pid = it.get("productId") or it.get("product")
                if not pid:
                    continue
                amount = float(it.get("amount") or 0)
                if amount <= 0:
                    continue
                price = it.get("price")
                total = float(it.get("sum") or 0)
                if price is None:
                    price = total / amount if amount else 0
                price = float(price)
                if price <= 0:
                    continue
                out.append({
                    "product_id": pid,
                    "product_name": it.get("productName") or it.get("name") or "—",
                    "unit": it.get("amountUnit") or it.get("unit") or "",
                    "supplier_id": supplier_id,
                    "supplier_name": supplier_name,
                    "date": inv_date,
                    "price_per_unit": price,
                    "amount": amount,
                    "sum": total,
                })
        return out

    async def create_incoming_invoice(
        self, org_id: str, supplier_id: str, items: List[Dict[str, Any]],
        date: str, document_number: Optional[str] = None,
        store_id: Optional[str] = None, comment: Optional[str] = None
    ) -> Dict[str, Any]:
        """Создать ВХОДЯЩУЮ накладную в iiko (черновик, status=NEW).
        Cloud API: POST /api/1/documents/create/incomingInvoice

        items[] — список словарей с полями:
            product (str, iiko product id), amount (float),
            sum (float, общая сумма по позиции), price (float, цена за единицу)
        comment — текст в поле «Комментарий» iikoOffice.
        """
        token = await self._ensure_token()
        body = {
            "organizationId": org_id,
            "supplier": supplier_id,
            "dateIncoming": _dt(date),
            "useDefaultDocumentTime": True,
            "items": items,
        }
        if document_number:
            body["documentNumber"] = document_number
        if comment:
            body["comment"] = comment
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{IIKO_CLOUD_BASE}/api/1/documents/create/incomingInvoice",
                headers={"Authorization": f"Bearer {token}"},
                json=body
            )
            if resp.status_code != 200:
                import logging
                logging.getLogger(__name__).warning(
                    f"iiko create/incomingInvoice {resp.status_code}: "
                    f"body={resp.text[:1000]} | request={body}"
                )
                return {"ok": False, "error": f"HTTP {resp.status_code}", "body": resp.text[:500]}
            data = resp.json()
            return {"ok": True, "id": data.get("id") or data.get("documentId"),
                    "number": data.get("documentNumber") or data.get("number"),
                    "raw": data}

    async def set_document_status(self, org_id: str, document_id: str,
                                   status: str, doc_number: str = None,
                                   date: str = None, fallback: dict = None) -> Dict[str, Any]:
        """Изменить статус документа (NEW / PROCESSED / DELETED).
        Cloud API: POST /api/1/documents/setStatus
        Для проведения передать status="PROCESSED", для распроведения — "NEW".
        doc_number/date/fallback игнорируются (нужны только iikoWeb-клиенту,
        сигнатуры выровнены, чтобы вызывающий код не различал клиентов).
        """
        token = await self._ensure_token()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{IIKO_CLOUD_BASE}/api/1/documents/setStatus",
                headers={"Authorization": f"Bearer {token}"},
                json={"organizationId": org_id, "documentId": document_id, "status": status}
            )
            if resp.status_code != 200:
                return {"ok": False, "error": f"HTTP {resp.status_code}", "body": resp.text[:500]}
            return {"ok": True, "raw": resp.json()}


# ============ iikoWEB CLIENT (self-hosted) ============

class IikoWebClient:
    """Клиент для самохостед iikoWeb 9.x через REST API /resto/api/v2/"""

    supports_cost = False  # DishCostSumInt нет в iikoWeb без отдельной лицензии

    def __init__(self, base_url: str, login: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.login = login
        self.password = password
        self.token: Optional[str] = None
        self.token_expires: Optional[datetime] = None
        self._lock = asyncio.Lock()

    @staticmethod
    def _sha1(s: str) -> str:
        return hashlib.sha1(s.encode()).hexdigest()

    async def _ensure_token(self) -> str:
        async with self._lock:
            if self.token and self.token_expires and datetime.now() < self.token_expires:
                return self.token
            host = urlparse(self.base_url).hostname or self.base_url
            try:
                async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
                    resp = await client.get(
                        f"{self.base_url}/api/auth",
                        params={"login": self.login, "pass": self._sha1(self.password)}
                    )
            except (httpx.ConnectError, httpx.TimeoutException):
                raise Exception(f"Не удалось подключиться к {host}. Проверь адрес сервера.")
            except httpx.RemoteProtocolError:
                raise Exception(f"Сервер {host} отвечает некорректно. Проверь URL.")

            if resp.status_code == 401:
                raise Exception(f"Неверный логин/пароль для {host}. Проверь данные iiko-сервера.")
            if resp.status_code == 404:
                raise Exception(f"На {host} нет /resto/api/auth. Это точно iikoServer? Проверь URL.")
            if resp.status_code >= 400:
                raise Exception(f"iiko {host}: HTTP {resp.status_code}")

            token = resp.text.strip().strip('"')
            if not token or len(token) < 10:
                raise Exception(f"iiko {host}: пустой ответ авторизации")
            self.token = token
            self.token_expires = datetime.now() + timedelta(minutes=25)
            return self.token

    async def _invalidate_token(self):
        async with self._lock:
            self.token = None
            self.token_expires = None

    def _date_filter(self, date_from: str, date_to: str) -> Dict[str, Any]:
        d_from = date_from[:10]
        d_to = date_to[:10]
        include_high = "true"
        if d_from == d_to:
            # Сдвигаем to на +1 день, но делаем его НЕвключительным
            dt = datetime.strptime(d_to, "%Y-%m-%d") + timedelta(days=1)
            d_to = dt.strftime("%Y-%m-%d")
            include_high = "false"
        return {
            "filterType": "DateRange", "periodType": "CUSTOM",
            "from": d_from, "to": d_to,
            "includeLow": "true", "includeHigh": include_high
        }

    # Маппинг: поля облачного API → поля iikoWeb REST API
    # None = поле не существует в iikoWeb, пропускается
    _TO_WEB = {
        "DishAmountSum":        "DishSumInt",
        "OrdersCount":          "UniqOrderId",
        "DishCount":            "DishAmountInt",  # в iikoWeb = кол-во × 1000
        "ProductCategory.Name": "DishCategory",
        "OrderType.Name":       "OrderType",      # в iikoWeb без .Name
        "DishCostSumInt":       None,              # нет в iikoWeb — нужна отдельная лицензия
    }
    _FROM_WEB_EXTRA = {
        "OrderType":     "OrderType.Name",
        "DishAmountInt": "DishCount",
    }
    _FROM_WEB = {v: k for k, v in _TO_WEB.items() if v is not None}



    def _map_fields(self, fields: List[str]) -> List[str]:
        result = []
        for f in fields:
            mapped = self._TO_WEB.get(f, f)
            if mapped is not None:
                result.append(mapped)
        return result

    def _normalize_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        combined = {**self._FROM_WEB, **self._FROM_WEB_EXTRA}
        result = {}
        for k, v in row.items():
            new_key = combined.get(k, k)
            # DishAmountInt — кол-во порций (целое или дробное, не нужно делить)
            if k == "DishAmountInt" and isinstance(v, (int, float)):
                v = round(v, 2)
            result[new_key] = v
        return result

    async def olap_report(
        self,
        org_id: str,
        date_from: str,
        date_to: str,
        group_by: List[str],
        aggregates: List[str],
        deleted_only: bool = False
    ) -> Dict[str, Any]:
        """Выполняет OLAP через iikoWeb REST API, возвращает {"data": [...]} как облако."""
        if deleted_only:
            filters: Dict[str, Any] = {
                "OpenDate.Typed": self._date_filter(date_from, date_to),
                "OrderDeleted": {"filterType": "IncludeValues", "values": ["DELETED"]},
            }
        else:
            filters = {
                "OpenDate.Typed": self._date_filter(date_from, date_to),
                "OrderDeleted": {"filterType": "IncludeValues", "values": ["NOT_DELETED"]},
            }
        if org_id and org_id != "demo":
            filters["Department"] = {"filterType": "IncludeValues", "values": [org_id]}

        payload = {
            "reportType": "SALES",
            "buildSummary": "false",
            "groupByRowFields": self._map_fields(group_by),
            "aggregateFields": self._map_fields(aggregates),
            "filters": filters,
        }

        for attempt in range(2):
            token = await self._ensure_token()
            async with httpx.AsyncClient(timeout=60.0, verify=False) as client:
                resp = await client.post(
                    f"{self.base_url}/api/v2/reports/olap",
                    params={"key": token},
                    json=payload
                )
                if resp.status_code == 401 and attempt == 0:
                    await self._invalidate_token()
                    continue
                if not resp.is_success:
                    import logging
                    logging.getLogger(__name__).warning(f"iikoWeb OLAP 400: {resp.text[:300]} | payload={payload}")
                resp.raise_for_status()
                raw = resp.json()
                rows = raw if isinstance(raw, list) else raw.get("data", [])
                return {"data": [self._normalize_row(r) for r in rows]}

        raise Exception("iikoWeb OLAP: не удалось получить данные.")

    async def olap_couriers(self, org_id: str, date_from: str, date_to: str) -> List[Dict[str, Any]]:
        """DELIVERIES OLAP по курьерам (группировка курьер×опоздание).
        Возвращает сырые строки; агрегацию (ср. путь, ср. опоздание, % опозданий)
        делает вызывающий код. Самовывоз/не-курьеров отсекаем по имени там же."""
        filters: Dict[str, Any] = {"OpenDate.Typed": self._date_filter(date_from, date_to)}
        if org_id and org_id != "demo":
            filters["Department"] = {"filterType": "IncludeValues", "values": [org_id]}
        payload = {
            "reportType": "DELIVERIES",
            "buildSummary": "false",
            "groupByRowFields": ["Delivery.Courier", "Delivery.Delay"],
            "aggregateFields": ["UniqOrderId.OrdersCount", "Delivery.WayDurationSum"],
            "filters": filters,
        }
        for attempt in range(2):
            token = await self._ensure_token()
            async with httpx.AsyncClient(timeout=60.0, verify=False) as client:
                resp = await client.post(
                    f"{self.base_url}/api/v2/reports/olap",
                    params={"key": token}, json=payload
                )
                if resp.status_code == 401 and attempt == 0:
                    await self._invalidate_token()
                    continue
                if not resp.is_success:
                    import logging
                    logging.getLogger(__name__).warning(f"iikoWeb DELIVERIES 400: {resp.text[:200]}")
                    return []
                raw = resp.json()
                return raw if isinstance(raw, list) else raw.get("data", [])
        return []

    async def olap_clients(self, org_id: str, date_from: str, date_to: str) -> List[Dict[str, Any]]:
        """DELIVERIES OLAP по телефону клиента: заказов + выручка на номер.
        Для вкладки «Клиенты» (лояльность напрямую из iiko, без ручной таблицы)."""
        filters: Dict[str, Any] = {"OpenDate.Typed": self._date_filter(date_from, date_to)}
        if org_id and org_id != "demo":
            filters["Department"] = {"filterType": "IncludeValues", "values": [org_id]}
        payload = {
            "reportType": "DELIVERIES",
            "buildSummary": "false",
            "groupByRowFields": ["Delivery.CustomerPhone"],
            "aggregateFields": ["UniqOrderId.OrdersCount", "DishDiscountSumInt"],
            "filters": filters,
        }
        for attempt in range(2):
            token = await self._ensure_token()
            async with httpx.AsyncClient(timeout=90.0, verify=False) as client:
                resp = await client.post(
                    f"{self.base_url}/api/v2/reports/olap",
                    params={"key": token}, json=payload
                )
                if resp.status_code == 401 and attempt == 0:
                    await self._invalidate_token()
                    continue
                if not resp.is_success:
                    import logging
                    logging.getLogger(__name__).warning(f"iikoWeb DELIVERIES(clients) 400: {resp.text[:200]}")
                    return []
                raw = resp.json()
                return raw if isinstance(raw, list) else raw.get("data", [])
        return []

    async def olap_clients_rfm(self, org_id: str, date_from: str, date_to: str) -> List[Dict[str, Any]]:
        """DELIVERIES OLAP: телефон × дата открытия — для RFM (recency/frequency/monetary).
        Отличие от olap_clients: добавлена группировка по OpenDate.Typed, чтобы знать
        дату последнего заказа клиента (R)."""
        filters: Dict[str, Any] = {"OpenDate.Typed": self._date_filter(date_from, date_to)}
        if org_id and org_id != "demo":
            filters["Department"] = {"filterType": "IncludeValues", "values": [org_id]}
        payload = {
            "reportType": "DELIVERIES",
            "buildSummary": "false",
            "groupByRowFields": ["Delivery.CustomerPhone", "OpenDate.Typed"],
            "aggregateFields": ["UniqOrderId.OrdersCount", "DishDiscountSumInt"],
            "filters": filters,
        }
        for attempt in range(2):
            token = await self._ensure_token()
            async with httpx.AsyncClient(timeout=90.0, verify=False) as client:
                resp = await client.post(
                    f"{self.base_url}/api/v2/reports/olap",
                    params={"key": token}, json=payload
                )
                if resp.status_code == 401 and attempt == 0:
                    await self._invalidate_token()
                    continue
                if not resp.is_success:
                    import logging
                    logging.getLogger(__name__).warning(f"iikoWeb DELIVERIES(rfm) {resp.status_code}: {resp.text[:200]}")
                    return []
                raw = resp.json()
                return raw if isinstance(raw, list) else raw.get("data", [])
        return []

    async def get_organizations(self) -> List[Dict[str, Any]]:
        """Список торговых точек (DEPARTMENT) из iikoWeb."""
        import xml.etree.ElementTree as ET
        token = await self._ensure_token()
        async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
            resp = await client.get(
                f"{self.base_url}/api/corporation/departments",
                params={"key": token}
            )
            resp.raise_for_status()
            root = ET.fromstring(resp.text)
            return [
                {"id": e.find("id").text, "name": e.find("name").text}
                for e in root
                if e.find("type") is not None and e.find("type").text == "DEPARTMENT"
            ]

    async def get_stop_lists(self, org_ids: List[str]) -> Dict[str, Any]:
        return {"productionLocations": []}

    async def get_nomenclature(self, org_id: str) -> Dict[str, Any]:
        return {"products": []}

    @staticmethod
    def _aggregate_stock_prices(rows: List[Dict[str, Any]]) -> Dict[str, float]:
        """Из строк balance/stores → {productId: средняя себестоимость = Σsum/Σamount}."""
        acc: Dict[str, List[float]] = {}
        for r in rows or []:
            pid = r.get("product")
            amt = float(r.get("amount", 0) or 0)
            summ = float(r.get("sum", 0) or 0)
            if not pid or amt <= 0:
                continue
            a = acc.setdefault(pid, [0.0, 0.0])
            a[0] += summ
            a[1] += amt
        return {pid: (s / a) for pid, (s, a) in acc.items() if a > 0}

    @staticmethod
    def _products_name_map(products: List[Dict[str, Any]]) -> Dict[str, str]:
        """{имя блюда в нижнем регистре: productId} только для type==DISH.
        Дубликаты имён схлопываются (последний в списке побеждает); правильнее было бы
        сопоставлять по DishId измерению OLAP-отчёта, когда оно станет доступно."""
        out: Dict[str, str] = {}
        for p in products or []:
            if (p.get("type") == "DISH") and p.get("id") and p.get("name"):
                out[str(p["name"]).strip().lower()] = p["id"]
        return out

    async def get_products(self) -> List[Dict[str, Any]]:
        for attempt in range(2):
            token = await self._ensure_token()
            async with httpx.AsyncClient(timeout=60.0, verify=False) as client:
                resp = await client.get(
                    f"{self.base_url}/api/v2/entities/products/list",
                    params={"key": token, "includeDeleted": "false"}
                )
                if resp.status_code == 401 and attempt == 0:
                    await self._invalidate_token()
                    continue
                resp.raise_for_status()
                data = resp.json()
                return data if isinstance(data, list) else data.get("data", [])
        return []

    async def get_dish_name_map(self) -> Dict[str, str]:
        return self._products_name_map(await self.get_products())

    async def get_assembly_prepared(self, product_id: str, date: str) -> List[Dict[str, Any]]:
        """Развёрнутая техкарта блюда на дату → [{productId, amount(норма на 1 порцию)}]."""
        for attempt in range(2):
            token = await self._ensure_token()
            async with httpx.AsyncClient(timeout=60.0, verify=False) as client:
                resp = await client.get(
                    f"{self.base_url}/api/v2/assemblyCharts/getPrepared",
                    params={"key": token, "date": date, "productId": product_id}
                )
                if resp.status_code == 401 and attempt == 0:
                    await self._invalidate_token()
                    continue
                if resp.status_code == 404:
                    return []
                resp.raise_for_status()
                charts = (resp.json() or {}).get("preparedCharts") or []
                if not charts:
                    return []
                items = charts[0].get("items") or []
                return [{"productId": it.get("productId"), "amount": float(it.get("amount", 0) or 0)}
                        for it in items if it.get("productId")]
        return []

    async def get_stock_prices(self) -> Dict[str, float]:
        """Учётная себестоимость по ингредиентам из остатков складов."""
        ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S.000")
        for attempt in range(2):
            token = await self._ensure_token()
            async with httpx.AsyncClient(timeout=60.0, verify=False) as client:
                resp = await client.get(
                    f"{self.base_url}/api/v2/reports/balance/stores",
                    params={"key": token, "timestamp": ts}
                )
                if resp.status_code == 401 and attempt == 0:
                    await self._invalidate_token()
                    continue
                resp.raise_for_status()
                data = resp.json()
                rows = data if isinstance(data, list) else data.get("data", [])
                return self._aggregate_stock_prices(rows)
        return {}

    async def get_expense_acts(self, org_ids: List[str], date_from: str, date_to: str) -> Dict[str, Any]:
        """Расходы через OLAP TRANSACTIONS — INCOME_EXPENSES счета."""
        filters: Dict[str, Any] = {
            "DateTime.OperDayFilter": self._tx_date_filter(date_from, date_to),
            "Account.Group": {"filterType": "IncludeValues", "values": ["INCOME_EXPENSES"]},
        }
        if org_ids and org_ids[0] and org_ids[0] != "demo":
            filters["Department"] = {"filterType": "IncludeValues", "values": org_ids}

        payload = {
            "reportType": "TRANSACTIONS",
            "buildSummary": "false",
            "groupByRowFields": ["Account.Name", "Department"],
            "aggregateFields": ["Sum.ResignedSum"],
            "filters": filters,
        }
        token = await self._ensure_token()
        async with httpx.AsyncClient(timeout=60.0, verify=False) as client:
            resp = await client.post(
                f"{self.base_url}/api/v2/reports/olap",
                params={"key": token},
                json=payload
            )
            resp.raise_for_status()
            raw = resp.json()
            rows = raw if isinstance(raw, list) else raw.get("data", [])
            # Формат для api.py: {"expenses": [{"items": [{"comment": name, "sum": amount, "category": {"name": cat}}], "dateIncoming": date, "paymentType": {"name": dept}}]}
            expenses = []
            for r in rows:
                amount = float(r.get("Sum.ResignedSum") or 0)
                if amount <= 0:
                    continue
                expenses.append({
                    "id": "",
                    "dateIncoming": f"{date_to}T00:00:00",
                    "paymentType": {"name": r.get("Department", "")},
                    "items": [{"comment": r.get("Account.Name", "Расход"), "sum": amount,
                               "category": {"name": r.get("Account.Name", "Прочее")}}],
                    "sum": amount,
                })
            return {"expenses": expenses}

    def _tx_date_filter(self, date_from: str, date_to: str) -> Dict[str, Any]:
        d_from = date_from[:10]
        d_to = date_to[:10]
        include_high = "true"
        if d_from == d_to:
            # Единственный день: сдвигаем to на +1 и делаем НЕвключительным (как _date_filter).
            dt = datetime.strptime(d_to, "%Y-%m-%d") + timedelta(days=1)
            d_to = dt.strftime("%Y-%m-%d")
            include_high = "false"
        return {"filterType": "DateRange", "periodType": "CUSTOM",
                "from": d_from, "to": d_to,
                "includeLow": "true", "includeHigh": include_high}

    async def get_writeoffs(self, org_ids: List[str], date_from: str, date_to: str) -> Dict[str, Any]:
        """Списания через OLAP TRANSACTIONS — возвращает {"writeoffs": [{"items": [...]}]}"""
        filters: Dict[str, Any] = {
            "DateTime.OperDayFilter": self._tx_date_filter(date_from, date_to),
            "TransactionType": {"filterType": "IncludeValues", "values": ["SESSION_WRITEOFF", "WRITEOFF"]},
        }
        if org_ids and org_ids[0] and org_ids[0] != "demo":
            filters["Department"] = {"filterType": "IncludeValues", "values": org_ids}

        payload = {
            "reportType": "TRANSACTIONS",
            "buildSummary": "false",
            "groupByRowFields": ["Product.Category"],
            "aggregateFields": ["Sum.Outgoing"],
            "filters": filters,
        }
        token = await self._ensure_token()
        async with httpx.AsyncClient(timeout=60.0, verify=False) as client:
            resp = await client.post(
                f"{self.base_url}/api/v2/reports/olap",
                params={"key": token},
                json=payload
            )
            resp.raise_for_status()
            raw = resp.json()
            rows = raw if isinstance(raw, list) else raw.get("data", [])
            # Преобразуем в формат который ожидает api.py
            items = [
                {
                    "product": {"category": {"name": r.get("Product.Category") or "Прочее"}},
                    "sum": float(r.get("Sum.Outgoing") or 0)
                }
                for r in rows
            ]
            return {"writeoffs": [{"items": items}]}

    # Типы транзакций изъятия наличных из кассы (подтверждены на live iikoServer):
    # PAY_COLLECTION — инкассация, PAYOUT — выплаты/изъятия из кассы.
    _WITHDRAWAL_TYPES = ["PAY_COLLECTION", "PAYOUT"]
    _WITHDRAWAL_TYPE_LABEL = {"PAY_COLLECTION": "Инкассация", "PAYOUT": "Выплата из кассы"}

    async def get_cash_withdrawals(self, org_ids: List[str], date_from: str, date_to: str) -> Dict[str, Any]:
        """Изъятия наличных из кассы через OLAP TRANSACTIONS.

        Фильтр по типам PAY_COLLECTION (инкассация) и PAYOUT (выплаты из кассы).
        Изоляция по филиалу — через Department.
        """
        filters: Dict[str, Any] = {
            "DateTime.OperDayFilter": self._tx_date_filter(date_from, date_to),
            "TransactionType": {"filterType": "IncludeValues", "values": self._WITHDRAWAL_TYPES},
        }
        if org_ids and org_ids[0] and org_ids[0] != "demo":
            filters["Department"] = {"filterType": "IncludeValues", "values": org_ids}

        payload = {
            "reportType": "TRANSACTIONS",
            "buildSummary": "false",
            "groupByRowFields": ["DateTime.DateTyped", "Account.Name", "TransactionType"],
            "aggregateFields": ["Sum.Outgoing"],
            "filters": filters,
        }
        token = await self._ensure_token()
        async with httpx.AsyncClient(timeout=60.0, verify=False) as client:
            resp = await client.post(
                f"{self.base_url}/api/v2/reports/olap",
                params={"key": token},
                json=payload
            )
            resp.raise_for_status()
            raw = resp.json()
            rows = raw if isinstance(raw, list) else raw.get("data", [])
            withdrawals = []
            for r in rows:
                amount = float(r.get("Sum.Outgoing") or 0)
                if amount <= 0:
                    continue
                tt = r.get("TransactionType") or ""
                label = self._WITHDRAWAL_TYPE_LABEL.get(tt, "Изъятие")
                account = str(r.get("Account.Name") or "")
                withdrawals.append({
                    "date": str(r.get("DateTime.DateTyped") or "")[:10],
                    "sum": amount,
                    "comment": f"{label}" + (f" · {account}" if account else ""),
                })
            return {"withdrawals": withdrawals}

    async def get_cashshifts(self, date_from: str, date_to: str,
                             status: str = "ANY") -> List[Dict[str, Any]]:
        """Кассовые смены (cashshifts/list v2) — выручка по ОПЕРАЦИОННОМУ дню.

        Это эталон, который видит ресторатор в кассе: SALES OLAP режет по дате
        ОТКРЫТИЯ заказа, а касса — по учётному дню смены, поэтому только отсюда
        цифра совпадает 1:1. Выручка смены = поле `payOrders` (проверено:
        payOrders == salesCash+salesCard+salesCredit). Возвращает список смен.

        Эндпоинт ждёт openDateFrom/openDateTo в формате yyyy-MM-dd (подтверждено
        пробами: closeSession/list даёт пустой XML, v2 cashshifts/list — JSON).
        """
        iso_from, iso_to = date_from[:10], date_to[:10]
        token = await self._ensure_token()
        async with httpx.AsyncClient(timeout=60.0, verify=False) as client:
            resp = await client.get(
                f"{self.base_url}/api/v2/cashshifts/list",
                params={"key": token, "openDateFrom": iso_from,
                        "openDateTo": iso_to, "status": status},
            )
            resp.raise_for_status()
            raw = resp.json()
            return raw if isinstance(raw, list) else raw.get("data", raw.get("sessions", []))

    async def get_payment_types_map(self) -> Dict[str, str]:
        """id → название типа оплаты (entities/list PaymentType)."""
        token = await self._ensure_token()
        async with httpx.AsyncClient(timeout=60.0, verify=False) as client:
            resp = await client.get(f"{self.base_url}/api/v2/entities/list",
                                    params={"key": token, "rootType": "PaymentType",
                                            "includeDeleted": "false"})
            resp.raise_for_status()
            return {x["id"]: x.get("name", "") for x in resp.json() if x.get("id")}

    async def get_cashshift_payments(self, session_id: str) -> Dict[str, Any]:
        """Платежи кассовой смены: безнал по записям, original vs actual/edited
        (следы исправлений типов оплат при принятии смены в офисе)."""
        token = await self._ensure_token()
        async with httpx.AsyncClient(timeout=60.0, verify=False) as client:
            resp = await client.get(
                f"{self.base_url}/api/v2/cashshifts/payments/list/{session_id}",
                params={"key": token, "hideAccepted": "false"},
            )
            resp.raise_for_status()
            return resp.json()

    async def get_employees_map(self) -> Dict[str, str]:
        """id → имя сотрудника (XML /api/employees)."""
        import xml.etree.ElementTree as ET
        token = await self._ensure_token()
        async with httpx.AsyncClient(timeout=60.0, verify=False) as client:
            resp = await client.get(f"{self.base_url}/api/employees",
                                    params={"key": token})
            resp.raise_for_status()
        out: Dict[str, str] = {}
        for e in ET.fromstring(resp.text).findall("employee"):
            eid = e.findtext("id")
            full = " ".join(x for x in (e.findtext("lastName"), e.findtext("firstName"))
                            if x) or (e.findtext("name") or "")
            if eid:
                out[eid] = full.strip()
        return out

    async def get_attendance(self, date_from: str, date_to: str) -> List[Dict[str, Any]]:
        """Явки сотрудников за период (XML), для расчёта часов и зарплаты."""
        import xml.etree.ElementTree as ET
        token = await self._ensure_token()
        async with httpx.AsyncClient(timeout=60.0, verify=False) as client:
            resp = await client.get(f"{self.base_url}/api/employees/attendance",
                                    params={"key": token, "from": date_from[:10],
                                            "to": date_to[:10]})
            resp.raise_for_status()
        out = []
        for a in ET.fromstring(resp.text).findall("attendance"):
            out.append({
                "employeeId": a.findtext("employeeId"),
                "dateFrom": a.findtext("dateFrom"),
                "dateTo": a.findtext("dateTo"),
                "departmentName": a.findtext("departmentName") or "",
            })
        return out

    @staticmethod
    def cashshifts_revenue(sessions: List[Dict[str, Any]]) -> float:
        """Сумма выручки по сменам = Σ payOrders (как в кассе)."""
        total = 0.0
        for s in sessions:
            try:
                total += float(s.get("payOrders") or 0)
            except (TypeError, ValueError):
                pass
        return round(total, 2)

    _conceptions_cache: Optional[Tuple[datetime, List[Dict[str, Any]]]] = None

    async def get_conceptions(self) -> List[Dict[str, Any]]:
        """Список концепций сервера: [{id, code, name}] через entities/list.
        Нужен, чтобы по Conception.Code филиала (из OLAP) найти conceptionId смены.
        Кеш на 6 часов — справочник статичный."""
        if self._conceptions_cache and \
                (datetime.now() - self._conceptions_cache[0]) < timedelta(hours=6):
            return self._conceptions_cache[1]
        token = await self._ensure_token()
        async with httpx.AsyncClient(timeout=40.0, verify=False) as client:
            resp = await client.get(
                f"{self.base_url}/api/v2/entities/list",
                params={"key": token, "rootType": "Conception", "includeDeleted": "false"},
            )
            resp.raise_for_status()
            data = resp.json()
            items = data if isinstance(data, list) else data.get("data", [])
            out = [{"id": c.get("id"),
                    "code": str(c.get("code")) if c.get("code") is not None else None,
                    "name": c.get("name")}
                   for c in items if isinstance(c, dict) and c.get("id")]
            self._conceptions_cache = (datetime.now(), out)
            return out

    async def get_invoices(self, org_ids: List[str], date_from: str, date_to: str) -> Dict[str, Any]:
        """Накладные через OLAP TRANSACTIONS INVOICE — возвращает {"invoiceIncomings": [...]}.

        Группируем по дате + поставщику, чтобы каждая строка имела РЕАЛЬНУЮ дату прихода
        (а не конец периода) — иначе фильтр по периоду визуально не работает.
        """
        filters: Dict[str, Any] = {
            "DateTime.OperDayFilter": self._tx_date_filter(date_from, date_to),
            "TransactionType": {"filterType": "IncludeValues", "values": ["INVOICE", "SESSION_ACCEPTANCE"]},
        }
        if org_ids and org_ids[0] and org_ids[0] != "demo":
            filters["Department"] = {"filterType": "IncludeValues", "values": org_ids}

        payload = {
            "reportType": "TRANSACTIONS",
            "buildSummary": "false",
            "groupByRowFields": ["DateTime.DateTyped", "Counteragent.Id"],
            "aggregateFields": ["Sum.Incoming"],
            "filters": filters,
        }
        token = await self._ensure_token()
        async with httpx.AsyncClient(timeout=60.0, verify=False) as client:
            resp = await client.post(
                f"{self.base_url}/api/v2/reports/olap",
                params={"key": token},
                json=payload
            )
            if resp.status_code >= 400:
                import logging
                logging.getLogger(__name__).warning(
                    f"iikoWeb get_invoices OLAP {resp.status_code}: {resp.text[:400]} | payload={payload}")
                resp.raise_for_status()
            raw = resp.json()
            rows = raw if isinstance(raw, list) else raw.get("data", [])

        # Резолв имён поставщиков по Counteragent.Id (как в get_invoice_items_olap)
        suppliers_by_id: Dict[str, str] = {}
        if rows and org_ids and org_ids[0] and org_ids[0] != "demo":
            try:
                for s in await self.get_suppliers(org_ids[0]):
                    if s.get("id"):
                        suppliers_by_id[s["id"]] = s.get("name") or s["id"]
            except Exception:
                pass

        result = []
        for r in rows:
            amount = float(r.get("Sum.Incoming") or 0)
            if amount <= 0:
                continue
            day = str(r.get("DateTime.DateTyped") or "")[:10]
            cid = r.get("Counteragent.Id") or ""
            result.append({
                "documentNumber": "",
                "status": "Processed",
                "supplier": {"name": suppliers_by_id.get(cid) or "—"},
                "dateIncoming": f"{day}T00:00:00" if day else f"{date_to}T00:00:00",
                "items": [],
                "sum": amount,
            })
        return {"invoiceIncomings": result}

    async def get_olap_columns(self, report_type: str = "TRANSACTIONS") -> List[Dict[str, Any]]:
        """Список доступных OLAP-полей для report_type на этом iikoServer.
        Используется в диагностике при ошибках 'Unknown OLAP field'."""
        token = await self._ensure_token()
        try:
            async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
                # iikoServer 9.x использует query-style: ?reportType=…
                resp = await client.get(
                    f"{self.base_url}/api/v2/reports/olap/columns",
                    params={"key": token, "reportType": report_type}
                )
                if resp.status_code == 200:
                    data = resp.json()
                    # Формат: dict с ключами-именами полей или list
                    if isinstance(data, list):
                        return data
                    if isinstance(data, dict):
                        # На iikoServer 9.x — dict {fieldCode: {name, type, …}};
                        # ключ — это реальный OLAP-код, value.name — человеческое имя
                        if "columnList" in data or "columns" in data:
                            return data.get("columnList") or data.get("columns") or []
                        return [
                            {"name": k, "display": (v.get("name") if isinstance(v, dict) else "")}
                            for k, v in data.items()
                        ]
        except Exception:
            pass
        return []

    async def get_invoice_items_olap(
        self, org_ids: List[str], date_from: str, date_to: str
    ) -> List[Dict[str, Any]]:
        """Per-line-item invoice rows via OLAP TRANSACTIONS, grouped by product/supplier/day.
        Self-hosted iikoServer не отдаёт /documents/export/invoice_incoming, поэтому строим
        построчную картину через OLAP (это единственный способ для price-stats).

        Имена товаров/поставщиков НЕ запрашиваем в OLAP — поднимаем из локальных
        кешей (search_nomenclature, get_suppliers), чтобы не зависеть от
        непроверенных `.Name`-полей."""
        filters: Dict[str, Any] = {
            "DateTime.OperDayFilter": self._tx_date_filter(date_from, date_to),
            "TransactionType": {"filterType": "IncludeValues", "values": ["INVOICE", "SESSION_ACCEPTANCE"]},
        }
        if org_ids and org_ids[0] and org_ids[0] != "demo":
            filters["Department"] = {"filterType": "IncludeValues", "values": org_ids}

        payload = {
            "reportType": "TRANSACTIONS",
            "buildSummary": "false",
            "groupByRowFields": ["Counteragent.Id", "Product.Id", "Product.MeasureUnit"],
            "aggregateFields": ["Sum.Incoming", "Amount.In"],
            "filters": filters,
        }
        token = await self._ensure_token()
        async with httpx.AsyncClient(timeout=60.0, verify=False) as client:
            resp = await client.post(
                f"{self.base_url}/api/v2/reports/olap",
                params={"key": token},
                json=payload
            )
            if resp.status_code >= 400:
                # iiko обычно возвращает JSON с описанием — отдаём в except-каскад.
                # Дополнительно тянем список валидных OLAP-полей для точечной диагностики.
                body = resp.text[:300]
                cols = await self.get_olap_columns("TRANSACTIONS")
                names = []
                for c in cols:
                    if isinstance(c, dict):
                        n = c.get("name") or c.get("field") or c.get("id")
                        if n:
                            names.append(str(n))
                    elif isinstance(c, str):
                        names.append(c)
                fields_preview = ", ".join(names[:120]) if names else "<columns endpoint empty/404>"
                raise Exception(f"OLAP {resp.status_code} [diag-v8]: {body} | fields: {fields_preview}")
            raw = resp.json()
            rows = raw if isinstance(raw, list) else raw.get("data", [])

        names_by_id: Dict[str, str] = {}
        units_by_id: Dict[str, str] = {}
        suppliers_by_id: Dict[str, str] = {}
        if rows and org_ids and org_ids[0] and org_ids[0] != "demo":
            try:
                products = await self.search_nomenclature(org_ids[0], "", limit=10000)
                for p in products:
                    pid = p.get("id")
                    if pid:
                        names_by_id[pid] = p.get("name") or ""
                        units_by_id[pid] = p.get("unit") or ""
            except Exception:
                pass
            try:
                suppliers = await self.get_suppliers(org_ids[0])
                for s in suppliers:
                    sid = s.get("id")
                    if sid:
                        suppliers_by_id[sid] = s.get("name") or sid
            except Exception:
                pass

        out: List[Dict[str, Any]] = []
        for r in rows:
            pid = r.get("Product.Id") or r.get("Product") or ""
            if not pid:
                continue
            amount = float(r.get("Amount.In") or 0)
            total = float(r.get("Sum.Incoming") or 0)
            if amount <= 0 or total <= 0:
                continue
            price = total / amount
            cid = r.get("Counteragent.Id") or ""
            unit = r.get("Product.MeasureUnit") or units_by_id.get(pid, "") or ""
            out.append({
                "product_id": pid,
                "product_name": names_by_id.get(pid) or "—",
                "unit": unit,
                "supplier_id": cid or "—",
                "supplier_name": suppliers_by_id.get(cid) or cid or "—",
                "date": "",
                "price_per_unit": price,
                "amount": amount,
                "sum": total,
            })
        return out

    # ============ SUPPLIERS / NOMENCLATURE / DOCUMENTS — self-hosted iikoServer 9.x ============
    # iikoServer возвращает XML по умолчанию. Auth: ?key=<token> в query.
    # Кеш номенклатуры/складов хранится на инстансе на 30 минут.

    def _ensure_caches(self):
        if not hasattr(self, "_nomenclature_cache"):
            self._nomenclature_cache: Dict[str, Tuple[datetime, List[Dict]]] = {}
        if not hasattr(self, "_stores_cache"):
            self._stores_cache: Dict[str, Tuple[datetime, List[Dict]]] = {}
        if not hasattr(self, "_suppliers_cache"):
            self._suppliers_cache: Dict[str, Tuple[datetime, List[Dict]]] = {}

    @staticmethod
    def _xml_to_list_of_dicts(xml_text: str, item_tag: str) -> List[Dict[str, Any]]:
        """Парсит XML вида <list><item>...</item><item>...</item></list> в список dict'ов
        с строковыми значениями. item_tag — имя дочернего тега (e.g. 'employee', 'product')."""
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return []
        # Корень может быть list-like; ищем все item_tag на любой глубине первого уровня
        items = []
        # Сначала ищем напрямую как дети корня
        children = root.findall(item_tag) or root.findall(f".//{item_tag}")
        for el in children:
            d: Dict[str, Any] = {}
            for child in el:
                tag = child.tag
                # учитываем namespace при наличии
                if "}" in tag:
                    tag = tag.split("}", 1)[1]
                d[tag] = (child.text or "").strip() if (child.text is not None) else ""
            items.append(d)
        return items

    async def get_suppliers(self, org_id: str) -> List[Dict[str, Any]]:
        """Список поставщиков. iikoServer: GET /resto/api/suppliers — возвращает XML
        с элементами <employee> (поставщик — это employee со специальной ролью)."""
        self._ensure_caches()
        cached = self._suppliers_cache.get(org_id)
        if cached and (datetime.now() - cached[0]) < timedelta(minutes=30):
            return cached[1]
        token = await self._ensure_token()
        try:
            async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
                resp = await client.get(
                    f"{self.base_url}/api/suppliers",
                    params={"key": token}
                )
                if resp.status_code != 200:
                    return []
                raw = resp.text
                items = self._xml_to_list_of_dicts(raw, "employee")
                if not items:
                    # запасной вариант — попробовать <supplier>
                    items = self._xml_to_list_of_dicts(raw, "supplier")
                # Нормализуем под формат Cloud-варианта: {id, name}
                out = []
                for it in items:
                    sid = it.get("id") or it.get("supplierId") or ""
                    name_parts = [it.get(k, "") for k in ("name", "firstName", "lastName") if it.get(k)]
                    name = " ".join(name_parts).strip() or sid
                    if sid:
                        out.append({"id": sid, "name": name, "_raw": it})
                self._suppliers_cache[org_id] = (datetime.now(), out)
                return out
        except Exception:
            return []

    async def get_measure_units_map(self) -> Dict[str, str]:
        """id → название единицы измерения (кг/шт/л...), кэш 30 мин."""
        self._ensure_caches()
        cached = getattr(self, "_units_cache", None)
        if cached and (datetime.now() - cached[0]) < timedelta(minutes=30):
            return cached[1]
        token = await self._ensure_token()
        units: Dict[str, str] = {}
        try:
            async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
                resp = await client.get(
                    f"{self.base_url}/api/v2/entities/list",
                    params={"key": token, "rootType": "MeasureUnit", "includeDeleted": "false"})
                if resp.status_code == 200:
                    units = {x["id"]: x.get("name", "") for x in resp.json() if x.get("id")}
        except Exception:
            pass
        self._units_cache = (datetime.now(), units)
        return units

    async def get_stores(self, org_name: str = "") -> List[Dict[str, Any]]:
        """Склады; если передано имя филиала — только его склады (по parentId
        через corporation/departments), иначе все."""
        token = await self._ensure_token()
        async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
            resp = await client.get(f"{self.base_url}/api/corporation/stores",
                                    params={"key": token})
            items = self._xml_to_list_of_dicts(resp.text, "corporateItemDto") or                     self._xml_to_list_of_dicts(resp.text, "store")
            stores = [{"id": it.get("id", ""), "name": it.get("name", ""),
                       "parentId": it.get("parentId", "")} for it in items if it.get("id")]
            if org_name:
                resp = await client.get(f"{self.base_url}/api/corporation/departments",
                                        params={"key": token})
                deps = self._xml_to_list_of_dicts(resp.text, "corporateItemDto") or                        self._xml_to_list_of_dicts(resp.text, "department")
                dep_ids = {d.get("id") for d in deps if (d.get("name") or "").strip() == org_name.strip()}
                if dep_ids:
                    own = [s for s in stores if s["parentId"] in dep_ids]
                    if own:
                        return own
        return stores

    async def _get_default_store(self, org_id: str) -> Optional[str]:
        """Берёт первый склад из corporation/stores (нужно для incomingInvoice)."""
        self._ensure_caches()
        cached = self._stores_cache.get(org_id)
        if cached and (datetime.now() - cached[0]) < timedelta(minutes=30):
            stores = cached[1]
            return stores[0]["id"] if stores else None
        token = await self._ensure_token()
        try:
            async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
                resp = await client.get(
                    f"{self.base_url}/api/corporation/stores",
                    params={"key": token}
                )
                if resp.status_code != 200:
                    return None
                items = self._xml_to_list_of_dicts(resp.text, "corporateItemDto")
                if not items:
                    items = self._xml_to_list_of_dicts(resp.text, "store")
                stores = [{"id": it.get("id", ""), "name": it.get("name", "")}
                          for it in items if it.get("id")]
                self._stores_cache[org_id] = (datetime.now(), stores)
                return stores[0]["id"] if stores else None
        except Exception:
            return None

    async def search_nomenclature(self, org_id: str, query: str,
                                  limit: int = 30) -> List[Dict[str, Any]]:
        """Поиск товаров. iikoServer: GET /resto/api/v2/entities/products/list — JSON,
        либо /resto/api/products — XML. Кеш на 30 минут, фильтрация локальная."""
        self._ensure_caches()
        cached = self._nomenclature_cache.get(org_id)
        if cached and (datetime.now() - cached[0]) < timedelta(minutes=30):
            products = cached[1]
        else:
            token = await self._ensure_token()
            products = []
            try:
                async with httpx.AsyncClient(timeout=60.0, verify=False) as client:
                    resp = await client.get(
                        f"{self.base_url}/api/v2/entities/products/list",
                        params={"key": token, "includeDeleted": "false"}
                    )
                    if resp.status_code == 200:
                        try:
                            data = resp.json()
                            if isinstance(data, list):
                                products = []
                                for p in data:
                                    if not (p.get("id") and p.get("name")):
                                        continue
                                    mu = p.get("mainUnit")
                                    products.append({
                                        "id": p.get("id"),
                                        "name": p.get("name"),
                                        "num": p.get("num"),
                                        "code": p.get("code"),
                                        "unit": mu.get("name") if isinstance(mu, dict) else mu,
                                        "containers": parse_containers(p),
                                        # диагностика структуры фасовок (видно на деплое)
                                        "_container_keys": p.get("_container_keys") or [],
                                    })
                        except Exception:
                            pass
                    # Fallback: XML на /api/products
                    if not products:
                        resp = await client.get(
                            f"{self.base_url}/api/products",
                            params={"key": token}
                        )
                        if resp.status_code == 200:
                            items = self._xml_to_list_of_dicts(resp.text, "productDto")
                            if not items:
                                items = self._xml_to_list_of_dicts(resp.text, "product")
                            products = [
                                {"id": it.get("id"), "name": it.get("name"),
                                 "num": it.get("num"), "code": it.get("code"),
                                 "unit": it.get("mainUnit")}
                                for it in items
                                if it.get("id") and it.get("name")
                            ]
            except Exception:
                products = []
            self._nomenclature_cache[org_id] = (datetime.now(), products)

        q = (query or "").strip().lower()
        if not q:
            return products[:limit]
        out = []
        for p in products:
            name = (p.get("name") or "").lower()
            num = (p.get("num") or "").lower()
            code = (p.get("code") or "").lower()
            if q in name or q in num or q in code:
                out.append(p)
                if len(out) >= limit:
                    break
        return out

    async def create_incoming_invoice(self, org_id: str, supplier_id: str,
                                       items: List[Dict[str, Any]], date: str,
                                       document_number: Optional[str] = None,
                                       store_id: Optional[str] = None,
                                       comment: Optional[str] = None) -> Dict[str, Any]:
        """Создаёт входящую накладную в iikoServer 9.x как ЧЕРНОВИК (status=NEW).
        Endpoint: POST /resto/api/documents/import/incomingInvoice (тело — XML).

        items[] — список словарей с productId, amount, price, sum (как в Cloud-варианте).
        comment — текст комментария оператора, попадает в поле «Комментарий» iikoOffice.
        """
        store_id = store_id or await self._get_default_store(org_id)
        if not store_id:
            return {"ok": False, "error": "Не найден склад (defaultStore) — настрой в iikoOffice"}

        dt_iso = date if "T" in date else f"{date}T10:00:00"
        doc_num = document_number or (
            datetime.now().strftime("BOT-%y%m%d-%H%M%S-") + secrets.token_hex(2)
        )

        def _build_xml(include_comment: bool) -> bytes:
            root = ET.Element("document")
            ET.SubElement(root, "documentNumber").text = str(doc_num)
            ET.SubElement(root, "dateIncoming").text = dt_iso
            ET.SubElement(root, "supplier").text = str(supplier_id)
            ET.SubElement(root, "defaultStore").text = str(store_id)
            ET.SubElement(root, "status").text = "NEW"
            if include_comment and comment:
                ET.SubElement(root, "comment").text = str(comment)
            items_el = ET.SubElement(root, "items")
            for idx, it in enumerate(items or [], start=1):
                ie = ET.SubElement(items_el, "item")
                ET.SubElement(ie, "num").text = str(idx)
                # iikoServer import ждёт <product> и <store> (НЕ <productId>/<storeId>),
                # иначе 409 "Product is not found id=null" / 500 "склад позиции отличается".
                # Подтверждено verify-curl к saya-co.iiko.it (2026-05-27): valid=true.
                ET.SubElement(ie, "product").text = str(it.get("productId") or "")
                ET.SubElement(ie, "amount").text = str(it.get("amount") or 0)
                ET.SubElement(ie, "price").text = str(it.get("price") or 0)
                ET.SubElement(ie, "sum").text = str(it.get("sum") or 0)
                ET.SubElement(ie, "store").text = str(store_id)
            return b'<?xml version="1.0" encoding="UTF-8"?>' + ET.tostring(root, encoding="utf-8")

        token = await self._ensure_token()

        async def _post(xml_body: bytes) -> Dict[str, Any]:
            import logging
            # На 401 (протухшая сессия iikoServer) — сбрасываем токен и повторяем 1 раз.
            for attempt in range(2):
                tok = await self._ensure_token()
                async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
                    resp = await client.post(
                        f"{self.base_url}/api/documents/import/incomingInvoice",
                        params={"key": tok},
                        headers={"Content-Type": "application/xml"},
                        content=xml_body)
                if resp.status_code == 401 and attempt == 0:
                    await self._invalidate_token()
                    continue
                break
            if resp.status_code != 200:
                pids = [str(it.get("productId") or "") for it in (items or [])]
                empty = sum(1 for p in pids if not p)
                logging.getLogger(__name__).warning(
                    f"iikoWeb import/incomingInvoice {resp.status_code}: "
                    f"body={resp.text[:500]} | items={len(items or [])} empty_productId={empty} "
                    f"productIds={pids} | xml={xml_body[:3000]!r}")
                return {"ok": False, "error": f"HTTP {resp.status_code}", "body": resp.text[:500]}
            # Ответ — XML <documentValidationResult>: <valid> + (на успехе) documentId.
            doc_id, num_out, valid_txt, err_txt = "", doc_num, "", ""
            try:
                rroot = ET.fromstring(resp.text)
                for el in rroot.iter():
                    tag = el.tag.split("}", 1)[-1]
                    txt = (el.text or "").strip()
                    if tag in ("documentId", "id") and txt:
                        doc_id = txt
                    elif tag == "documentNumber" and txt:
                        num_out = txt
                    elif tag == "valid" and txt:
                        valid_txt = txt
                    elif tag in ("errorMessage", "error", "message") and txt:
                        err_txt = txt
            except Exception:
                pass
            if valid_txt.lower() == "false" or err_txt:
                logging.getLogger(__name__).warning(
                    f"iikoWeb incomingInvoice отклонена: valid={valid_txt!r} "
                    f"err={err_txt!r} body={resp.text[:800]}")
                return {"ok": False, "error": err_txt or "документ не прошёл валидацию",
                        "body": resp.text[:500]}
            if not doc_id:
                doc_id = await self._resolve_incoming_doc_id(num_out, dt_iso[:10]) or ""
            return {"ok": True, "id": doc_id, "number": num_out, "raw": resp.text[:500]}

        try:
            # Сначала пробуем С комментарием; если iiko отклонил (его XSD может не
            # принимать <comment> здесь) — повторяем ТЕМ ЖЕ номером БЕЗ комментария.
            # Накладная важнее комментария; дублей нет (при valid=false документ не
            # создаётся). comment_applied показывает, попал ли комментарий в iiko.
            attempts = [True, False] if comment else [False]
            last: Dict[str, Any] = {"ok": False, "error": "no attempt"}
            for include_comment in attempts:
                last = await _post(_build_xml(include_comment))
                if last.get("ok"):
                    last["comment_applied"] = bool(include_comment and comment)
                    return last
            return last
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def _resolve_incoming_doc_id(self, number: str, date: str) -> Optional[str]:
        """Находит documentId входящей накладной по её номеру за дату через
        documents/export (export отдаёт и черновики со <id>, в отличие от OLAP).
        Нужно потому, что import/incomingInvoice не возвращает id в ответе."""
        if not number:
            return None
        try:
            token = await self._ensure_token()
            async with httpx.AsyncClient(timeout=40.0, verify=False) as client:
                resp = await client.get(
                    f"{self.base_url}/api/documents/export/incomingInvoice",
                    params={"key": token, "from": date, "to": date})
                if resp.status_code != 200:
                    return None
                for block in re.findall(r"<document>(.*?)</document>", resp.text, re.S):
                    if f"<documentNumber>{number}</documentNumber>" in block:
                        m = re.search(r"<id>(.*?)</id>", block)
                        return m.group(1) if m else None
        except Exception:
            return None
        return None

    async def _export_incoming_blocks(self, date: str) -> List[str]:
        """Сырые <document>-блоки экспорта входящих накладных за дату."""
        for attempt in range(2):
            token = await self._ensure_token()
            async with httpx.AsyncClient(timeout=40.0, verify=False) as client:
                resp = await client.get(
                    f"{self.base_url}/api/documents/export/incomingInvoice",
                    params={"key": token, "from": date, "to": date})
            if resp.status_code == 401 and attempt == 0:
                await self._invalidate_token()
                continue
            if resp.status_code != 200:
                return []
            return re.findall(r"<document>(.*?)</document>", resp.text, re.S)
        return []

    @staticmethod
    def _docs_by_number(blocks: List[str], number: str) -> List[Dict[str, str]]:
        """Документы с данным номером: [{id, status, block}].
        Первый <id> в блоке — id документа (поля документа идут до <items>,
        на этом же держится _resolve_incoming_doc_id)."""
        out = []
        for b in blocks:
            if f"<documentNumber>{number}</documentNumber>" not in b:
                continue
            mid = re.search(r"<id>(.*?)</id>", b)
            mst = re.search(r"<status>(.*?)</status>", b)
            out.append({"id": mid.group(1).strip() if mid else "",
                        "status": (mst.group(1).strip().upper() if mst else ""),
                        "block": b})
        return out

    @staticmethod
    def _items_from_export_block(block: str) -> List[Dict[str, Any]]:
        """Позиции документа из export-блока (product|productId, store|storeId)."""
        items = []
        for ib in re.findall(r"<item>(.*?)</item>", block, re.S):
            def g(tag):
                m = re.search(rf"<{tag}>(.*?)</{tag}>", ib, re.S)
                return m.group(1).strip() if m else ""
            pid = g("product") or g("productId")
            if not pid:
                continue
            items.append({"productId": pid,
                          "amount": float(g("amount") or 0),
                          "price": float(g("price") or 0),
                          "sum": float(g("sum") or 0),
                          "store": g("store") or g("storeId")})
        return items

    async def set_document_status(self, org_id: str, document_id: str,
                                   status: str, doc_number: str = None,
                                   date: str = None, fallback: dict = None) -> Dict[str, Any]:
        """Проводка/распроведение документа в iikoServer через РЕ-ИМПОРТ.

        Прямых эндпоинтов смены статуса в этой версии iikoServer НЕТ:
        POST /documents/{id}/setStatus и PUT /documents/{id}/status — 404
        (подтверждено логами прод-сервера 2026-07-04). Единственный рабочий
        write-путь — import/incomingInvoice, и импорт со <status> применяет
        статус сразу (подтверждено 2026-06-13). Передаём <id> существующего
        документа — iikoServer трактует это как обновление.

        Защита от квирка «повторный импорт = дубль»: перед PROCESSED делаем
        пробный ре-импорт со status=NEW (если сервер проигнорирует <id>, дубль
        будет безвредным черновиком) и проверяем через export, что документ
        остался один. Только после этого шлём PROCESSED. Каждый шаг
        верифицируется повторным export.

        fallback: {supplier, items, date} из данных бота — если export не отдал
        содержимое документа (страховка от другого формата тегов).
        """
        if not doc_number or not date:
            return {"ok": False, "error": "нет номера/даты документа для ре-импорта"}
        date = date[:10]
        docs = self._docs_by_number(await self._export_incoming_blocks(date), doc_number)
        if not docs:
            return {"ok": False,
                    "error": f"документ №{doc_number} за {date} не найден в iiko (export)"}
        if len(docs) > 1:
            return {"ok": False,
                    "error": f"в iiko {len(docs)} документа №{doc_number} — "
                             f"удалите дубли в iikoOffice и повторите"}
        cur = docs[0]
        doc_id = cur["id"] or document_id

        def gtag(tag):
            m = re.search(rf"<{tag}>(.*?)</{tag}>", cur["block"], re.S)
            return m.group(1).strip() if m else ""

        fb = fallback or {}
        supplier = gtag("supplier") or gtag("counteragentId") or fb.get("supplier") or ""
        dt_iso = gtag("dateIncoming") or fb.get("date") or f"{date}T10:00:00"
        comment = gtag("comment")
        items = self._items_from_export_block(cur["block"]) or fb.get("items") or []
        store = gtag("defaultStore") or next(
            (i["store"] for i in items if i.get("store")), "")
        if not store:
            store = await self._get_default_store(org_id) or ""
        if not supplier or not items or not store:
            return {"ok": False,
                    "error": "не удалось прочитать содержимое документа из export "
                             "(поставщик/позиции/склад) — проведите в iikoOffice"}

        async def _reimport(target: str) -> Dict[str, Any]:
            root = ET.Element("document")
            ET.SubElement(root, "id").text = doc_id
            ET.SubElement(root, "documentNumber").text = str(doc_number)
            ET.SubElement(root, "dateIncoming").text = dt_iso
            ET.SubElement(root, "supplier").text = supplier
            ET.SubElement(root, "defaultStore").text = store
            ET.SubElement(root, "status").text = target
            if comment:
                ET.SubElement(root, "comment").text = comment
            items_el = ET.SubElement(root, "items")
            for idx, it in enumerate(items, start=1):
                ie = ET.SubElement(items_el, "item")
                ET.SubElement(ie, "num").text = str(idx)
                ET.SubElement(ie, "product").text = str(it["productId"])
                ET.SubElement(ie, "amount").text = str(it["amount"])
                ET.SubElement(ie, "price").text = str(it["price"])
                ET.SubElement(ie, "sum").text = str(it["sum"])
                ET.SubElement(ie, "store").text = str(it.get("store") or store)
            xml = b'<?xml version="1.0" encoding="UTF-8"?>' + ET.tostring(root, encoding="utf-8")
            for attempt in range(2):
                tok = await self._ensure_token()
                async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
                    resp = await client.post(
                        f"{self.base_url}/api/documents/import/incomingInvoice",
                        params={"key": tok},
                        headers={"Content-Type": "application/xml"},
                        content=xml)
                if resp.status_code == 401 and attempt == 0:
                    await self._invalidate_token()
                    continue
                break
            if resp.status_code != 200:
                import logging
                logging.getLogger(__name__).warning(
                    f"iikoWeb reimport({target}) №{doc_number} HTTP {resp.status_code}: "
                    f"{resp.text[:400]}")
                return {"ok": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
            if re.search(r"<valid>\s*false\s*</valid>", resp.text, re.I):
                m = re.search(r"<errorMessage>(.*?)</errorMessage>", resp.text, re.S)
                return {"ok": False,
                        "error": (m.group(1).strip() if m else "документ не прошёл валидацию")}
            return {"ok": True}

        async def _verify(expected: str) -> Dict[str, Any]:
            ds = self._docs_by_number(await self._export_incoming_blocks(date), doc_number)
            if len(ds) != 1:
                return {"ok": False,
                        "error": f"после ре-импорта документов №{doc_number} стало {len(ds)} — "
                                 f"сервер создал дубль вместо обновления; удалите дубль "
                                 f"№{doc_number} в iikoOffice"}
            st = ds[0]["status"]
            if st and st != expected:
                return {"ok": False,
                        "error": f"статус после ре-импорта: {st} (ожидали {expected})"}
            return {"ok": True}

        target = (status or "").upper()
        if target == "PROCESSED" and cur["status"] != "PROCESSED":
            # Фаза-проба: убеждаемся, что сервер ОБНОВЛЯЕТ по <id>, а не дублирует.
            r = await _reimport("NEW")
            if not r["ok"]:
                return r
            v = await _verify("NEW")
            if not v["ok"]:
                return v
        r = await _reimport(target)
        if not r["ok"]:
            return r
        return await _verify(target)
