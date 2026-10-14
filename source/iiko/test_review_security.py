import base64, io, os, tempfile
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
import pytest
from fastapi.testclient import TestClient
from PIL import Image
import api, checklist_ai as C, mountain_routes as M


def test_decoder_accepts_only_supported_valid_images():
    for format in ["JPEG", "PNG", "WEBP"]:
        buf=io.BytesIO(); Image.new("RGB",(4,4),"red").save(buf,format=format)
        data,mime=C.shrink(buf.getvalue());assert mime=="image/jpeg" and data.startswith(b"\xff\xd8")
    buf=io.BytesIO();Image.new("RGB",(4,4)).save(buf,format="GIF")
    for invalid in [b"not an image",buf.getvalue()]:
        with pytest.raises(ValueError): C.shrink(invalid)


def test_demo_upload_cannot_write_or_call_paid_ai(monkeypatch):
    api.limiter.reset()
    monkeypatch.setattr(M,"scope_of_token",lambda _:dict(valid=True,uid=1,demo=True))
    monkeypatch.setattr(M,"_branch_or_403",lambda *a:pytest.fail("demo must stop before database access"))
    client=TestClient(api.app)
    r=client.post("/api/mountain/checklist/test",json={"org":"org","slot":"10:45","zone":"hall","photos":["anything"]})
    assert r.status_code==403


def test_invalid_upload_is_rejected_before_database_write(monkeypatch):
    api.limiter.reset()
    monkeypatch.setattr(M,"scope_of_token",lambda _:dict(valid=True,uid=1,demo=False))
    monkeypatch.setattr(M,"_branch_or_403",lambda *a:dict(organization_id="test-org"))
    monkeypatch.setattr(M,"save_checklist_upload",lambda *a:pytest.fail("must not write invalid image"))
    client=TestClient(api.app)
    r=client.post("/api/mountain/checklist/test",json={"org":"org","slot":"10:45","zone":"hall","photos":[base64.b64encode(b"invalid").decode()]})
    assert r.status_code==400


def test_upload_rate_limit_is_enforced(monkeypatch):
    api.limiter.reset()
    monkeypatch.setattr(M,"scope_of_token",lambda _:dict(valid=True,uid=1,demo=True))
    client=TestClient(api.app)
    payload={"org":"org","slot":"10:45","zone":"hall","photos":["anything"]}
    codes=[client.post("/api/mountain/checklist/test",json=payload).status_code for _ in range(7)]
    assert codes[:6]==[403]*6 and codes[6]==429


def test_migration_routes_are_removed():
    assert not any(getattr(r,"path","").startswith("/api/admin/migrate/") for r in api.app.routes)


def test_production_requires_webhook_secret(monkeypatch):
    monkeypatch.setattr(api,"IS_PROD",True)
    monkeypatch.setattr(api,"BOT_TOKEN","test-token")
    monkeypatch.setenv("ENCRYPTION_KEY","test-key")
    monkeypatch.setattr(api,"TELEGRAM_WEBHOOK_SECRET",None)
    with pytest.raises(RuntimeError,match="TELEGRAM_WEBHOOK_SECRET"): api._check_required_secrets()
