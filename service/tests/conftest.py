import pytest
from fastapi.testclient import TestClient

from catdetect.main import create_app
from catdetect.settings import Settings


@pytest.fixture()
def app(tmp_path):
    s = Settings(data_dir=tmp_path / "data", web_dir=tmp_path / "noweb", secret_key="test", run_pipeline=False)
    return create_app(s)


@pytest.fixture()
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def authed(client):
    r = client.post("/api/auth/setup", json={"username": "admin", "password": "password123"})
    assert r.status_code == 200
    return client
