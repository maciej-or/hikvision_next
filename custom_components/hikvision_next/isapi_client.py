import httpx
from typing import Any, AsyncIterator, List, Union
from urllib.parse import urljoin
import json
import xmltodict
from dataclasses import dataclass


def response_parser(response, present="dict"):
    """Parse Hikvision results."""
    if isinstance(response, (list,)):
        result = "".join(response)
    elif isinstance(response, str):
        result = response
    else:
        result = response.text

    if present is None or present == "dict":
        if isinstance(response, (list,)):
            events = []
            for event in response:
                e = json.loads(json.dumps(xmltodict.parse(event)))
                events.append(e)
            return events
        return json.loads(json.dumps(xmltodict.parse(result)))
    else:
        return result


@dataclass
class ISAPI_Client:
    host: str
    username: str
    password: str
    session: httpx.AsyncClient | None = None
    timeout: float = 3
    isapi_prefix: str = "ISAPI"
    _auth_method: httpx._auth.Auth = None

    async def _detect_auth_method(self):
        """Establish the connection with device."""
        if not self.session:
            self.session = httpx.AsyncClient(timeout=self.timeout)

        url = urljoin(self.host, self.isapi_prefix + "/System/status")
        for method in [
            httpx.BasicAuth(self.username, self.password),
            httpx.DigestAuth(self.username, self.password),
        ]:
            response = await self.session.get(url, auth=method)
            if response.status_code == 200:
                self._auth_method = method

        if not self._auth_method:
            response.raise_for_status()

    def get_url(self, relative_url: str) -> str:
        return f"{self.host}/{self.isapi_prefix}/{relative_url}"

    async def request(
        self,
        method: str,
        full_url: str,
        present: str = "dict",
        data: dict[str, Any] | None = None,
    ) -> Union[List[str], str]:
        """Send request to the device."""
        if not self._auth_method:
            await self._detect_auth_method()

        response = await self.session.request(method, full_url, auth=self._auth_method, data=data, timeout=self.timeout)
        response.raise_for_status()
        return response_parser(response, present)

    async def request_bytes(
        self,
        method: str,
        full_url: str,
        **data,
    ) -> AsyncIterator[bytes]:
        if not self._auth_method:
            await self._detect_auth_method()

        async with self.session.stream(method, full_url, auth=self._auth_method, **data) as response:
            async for chunk in response.aiter_bytes():
                yield chunk
