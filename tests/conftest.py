import httpx
import pytest

from app.config import Settings
from app.main import create_app


class FakeLLM:
    def __init__(self):
        self.calls = []
        self.error = None
        self.gate = None

    async def generate(self, system, messages, request_id):
        self.calls.append((system, messages, request_id))
        if self.gate:
            await self.gate.wait()
        if self.error:
            raise self.error
        return "가장 중요하게 생각하는 가치는 무엇인가요?"

    async def close(self):
        pass


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, database_url=f"sqlite:///{tmp_path / 'test.db'}")


@pytest.fixture
def fake_llm():
    return FakeLLM()


@pytest.fixture
async def app(settings, fake_llm):
    application = create_app(settings, llm=fake_llm, token_counter=lambda system, messages: 20)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


async def account(client, email="student@example.com"):
    body = {"email": email, "password": "  Test-password-123  "}
    response = await client.post("/api/v1/auth/signup", json=body)
    assert response.status_code == 201, response.text
    response = await client.post("/api/v1/auth/login", json=body)
    assert response.status_code == 200
    return {"Authorization": "Bearer " + response.json()["access_token"]}


@pytest.fixture
async def auth(client):
    return await account(client)


@pytest.fixture
async def cid(client, auth):
    response = await client.post(
        "/api/v1/conversations", headers=auth, json={"persona_id": "socrates"}
    )
    assert response.status_code == 201
    return response.json()["id"]
