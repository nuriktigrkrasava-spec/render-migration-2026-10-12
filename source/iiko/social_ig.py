"""Instagram Business через Meta Graph API — OAuth + профиль/посты.

Каркас: заработает, как только в окружении появятся META_APP_ID и META_APP_SECRET
(зарегистрировать приложение на developers.facebook.com, тип Business).
Instagram-аккаунт должен быть Business/Creator и привязан к Facebook-странице."""
import os
from urllib.parse import urlencode
from typing import Optional, Dict, Any, List

import httpx

APP_ID = os.getenv("META_APP_ID")
APP_SECRET = os.getenv("META_APP_SECRET")
GRAPH = "https://graph.facebook.com/v21.0"
# Разрешения для чтения профиля/постов/охватов бизнес-аккаунта Instagram
SCOPES = "instagram_basic,pages_show_list,instagram_manage_insights,business_management"


def is_configured() -> bool:
    """Есть ли App ID/Secret — без них OAuth невозможен."""
    return bool(APP_ID and APP_SECRET)


def authorize_url(redirect_uri: str, state: str) -> str:
    """URL входа через Facebook (пользователя редиректим сюда). state = org_id."""
    q = urlencode({
        "client_id": APP_ID, "redirect_uri": redirect_uri, "scope": SCOPES,
        "response_type": "code", "state": state,
    })
    return f"https://www.facebook.com/v21.0/dialog/oauth?{q}"


async def exchange_code(code: str, redirect_uri: str) -> str:
    """code → короткий токен → долгоживущий токен (~60 дней)."""
    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.get(f"{GRAPH}/oauth/access_token", params={
            "client_id": APP_ID, "client_secret": APP_SECRET,
            "redirect_uri": redirect_uri, "code": code})
        r.raise_for_status()
        short = r.json()["access_token"]
        r2 = await c.get(f"{GRAPH}/oauth/access_token", params={
            "grant_type": "fb_exchange_token", "client_id": APP_ID,
            "client_secret": APP_SECRET, "fb_exchange_token": short})
        r2.raise_for_status()
        return r2.json()["access_token"]


async def get_ig_account(token: str) -> Optional[Dict[str, str]]:
    """Находит Instagram Business-аккаунт, привязанный к странице пользователя."""
    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.get(f"{GRAPH}/me/accounts", params={
            "access_token": token, "fields": "instagram_business_account{id,username}"})
        r.raise_for_status()
        for page in r.json().get("data", []):
            iga = page.get("instagram_business_account")
            if iga and iga.get("id"):
                return {"id": iga["id"], "username": iga.get("username", "")}
    return None


async def fetch_profile(ig_id: str, token: str) -> Dict[str, Any]:
    """Подписчики, число постов и последние посты (лайки/комменты)."""
    async with httpx.AsyncClient(timeout=30.0) as c:
        prof = (await c.get(f"{GRAPH}/{ig_id}", params={
            "access_token": token, "fields": "username,followers_count,media_count"})).json()
        media = (await c.get(f"{GRAPH}/{ig_id}/media", params={
            "access_token": token, "limit": 6,
            "fields": "caption,like_count,comments_count,media_url,permalink,timestamp"})).json()
        posts: List[Dict[str, Any]] = [{
            "caption": (m.get("caption") or "")[:90],
            "likes": m.get("like_count"), "comments": m.get("comments_count"),
            "url": m.get("media_url"), "link": m.get("permalink"),
            "date": (m.get("timestamp") or "")[:10],
        } for m in media.get("data", [])]
        return {"username": prof.get("username"), "followers": prof.get("followers_count"),
                "media_count": prof.get("media_count"), "posts": posts}
