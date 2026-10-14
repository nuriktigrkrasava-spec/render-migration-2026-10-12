"""Rate limits use forwarded addresses only from explicitly trusted proxies."""
import ipaddress, os
from functools import lru_cache
from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

@lru_cache(maxsize=8)
def _trusted(value):
    return tuple(ipaddress.ip_network(item.strip()) for item in value.split(",") if item.strip())


def _client_ip(request: Request) -> str:
    peer = get_remote_address(request)
    try:
        trusted = any(ipaddress.ip_address(peer) in network for network in _trusted(os.getenv("TRUSTED_PROXY_CIDRS", "")))
        parts = [p.strip() for p in request.headers.get("X-Forwarded-For", "").split(",") if p.strip()]
        if trusted and parts:
            return str(ipaddress.ip_address(parts[-1]))
    except ValueError:
        pass
    return peer

limiter = Limiter(key_func=_client_ip)
