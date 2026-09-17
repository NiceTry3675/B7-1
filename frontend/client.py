import asyncio

import httpx

from app.errors import ERRORS


class ClientError(Exception):
    def __init__(self, code, request_id=None):
        self.code = code
        self.request_id = request_id
        self.message = ERRORS.get(code, (0, "연결이 끊겼습니다. 저장된 기록을 확인해 주세요."))[1]
        super().__init__(self.message)


class BackendClient:
    def __init__(self, base_url, timeout=160, transport=None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.transport = transport

    async def request(self, method, path, token=None, body=None, params=None):
        # No shared client credentials: each callback supplies this user's token explicitly.
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            async with asyncio.timeout(self.timeout):
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(self.timeout, connect=5),
                    transport=self.transport,
                    trust_env=False,
                ) as client:
                    response = await client.request(
                        method,
                        self.base_url + "/api/v1" + path,
                        headers=headers,
                        json=body,
                        params=params,
                    )
            if response.is_error:
                data = response.json()
                raise ClientError(data["error"]["code"], data.get("request_id"))
            return response.json() if response.status_code != 204 else None
        except (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError) as exc:
            raise ClientError("NETWORK_ERROR") from exc

    async def pages(self, path, token):
        items, offset = [], 0
        while True:
            page = await self.request("GET", path, token, params={"limit": 100, "offset": offset})
            items.extend(page["items"])
            if not page["has_more"]:
                return items
            offset += page["limit"]
