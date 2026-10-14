import asyncio
import pytest
from fastapi import HTTPException
from starlette.requests import Request
import api


def test_database_export_and_restore_removed():
    assert not any('/admin/migrate/' in getattr(r, 'path', '') for r in api.app.routes)


def test_empty_webhook_secret_denies_request(monkeypatch):
    monkeypatch.setattr(api, 'TELEGRAM_WEBHOOK_SECRET', '')
    with pytest.raises(HTTPException) as e:
        asyncio.run(api.telegram_webhook(Request({'type':'http','headers':[]})))
    assert e.value.status_code == 403


def test_production_requires_webhook_secret(monkeypatch):
    monkeypatch.setattr(api,'IS_PROD',True)
    monkeypatch.setattr(api,'BOT_TOKEN','configured')
    monkeypatch.setenv('ENCRYPTION_KEY','configured')
    monkeypatch.setattr(api,'TELEGRAM_WEBHOOK_SECRET','')
    with pytest.raises(RuntimeError, match='TELEGRAM_WEBHOOK_SECRET'): api._check_required_secrets()
