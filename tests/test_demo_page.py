from fastapi.testclient import TestClient

from app.api.main import app


def test_demo_page_serves_html() -> None:
    client = TestClient(app)
    r = client.get("/demo")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    assert "Problems we are solving" in r.text
    assert "Business outcomes" in r.text
