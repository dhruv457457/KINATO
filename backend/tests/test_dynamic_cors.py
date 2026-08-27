"""Tests app/core/dynamic_cors.py's OPTIONS preflight handling for
/api/events - the global CORSMiddleware's fixed origin list can't express
per-merchant storefront origins, so this must intercept and answer first."""
from fastapi.testclient import TestClient
from app.main import app
from app.db.repositories import merchants as merchants_repo

client = TestClient(app)


def test_preflight_from_a_registered_origin_is_accepted(real_merchant_id):
    merchants_repo.set_allowed_origins(real_merchant_id, ["https://registered-store.example"])
    r = client.options(
        "/api/events",
        headers={
            "Origin": "https://registered-store.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert r.status_code == 204
    assert r.headers.get("access-control-allow-origin") == "https://registered-store.example"


def test_preflight_from_an_unregistered_origin_gets_no_cors_headers(real_merchant_id):
    merchants_repo.set_allowed_origins(real_merchant_id, ["https://registered-store.example"])
    r = client.options(
        "/api/events",
        headers={
            "Origin": "https://never-registered.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers.keys()} or \
        r.headers.get("access-control-allow-origin") != "https://never-registered.example"
