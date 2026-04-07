import asyncio
from urllib.parse import urlparse

from aiohttp import web


class LocalOAuthCallbackServer:
    def __init__(self, callback_url: str):
        parsed = urlparse(callback_url)
        if not parsed.scheme or not parsed.hostname or not parsed.path:
            raise ValueError(f"Invalid callback URL: {callback_url}")

        self._scheme = parsed.scheme
        self._host = parsed.hostname
        self._port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)
        self._path = parsed.path
        self._runner: web.AppRunner | None = None
        self._site: web.BaseSite | None = None
        self._callback_future: asyncio.Future[str] = asyncio.get_running_loop().create_future()

    @property
    def callback_url(self) -> str:
        return f"{self._scheme}://{self._host}:{self._port}{self._path}"

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()
        return False

    async def start(self):
        app = web.Application()
        app.router.add_get(self._path, self._handle_callback)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host=self._host, port=self._port)
        await self._site.start()

        sockets = getattr(getattr(self._site, "_server", None), "sockets", None) or []
        if sockets:
            self._port = sockets[0].getsockname()[1]

    async def close(self):
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
            self._site = None

    def get_callback_url(self) -> str | None:
        if self._callback_future.done():
            return self._callback_future.result()
        return None

    async def wait_for_callback(self, timeout_s: float) -> str:
        return await asyncio.wait_for(asyncio.shield(self._callback_future), timeout=timeout_s)

    async def _handle_callback(self, request: web.Request) -> web.Response:
        callback_url = str(request.url)
        if not self._callback_future.done():
            self._callback_future.set_result(callback_url)
        return web.Response(
            text="Authorization complete. You can close this page.",
            content_type="text/plain",
        )
