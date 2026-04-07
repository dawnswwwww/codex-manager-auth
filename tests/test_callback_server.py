import unittest

import httpx

from codex_manager_auth.callback_server import LocalOAuthCallbackServer


class LocalOAuthCallbackServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_server_captures_callback_and_returns_success_page(self):
        server = LocalOAuthCallbackServer("http://127.0.0.1:0/auth/callback")
        await server.start()
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(f"{server.callback_url}?code=abc123&state=state-1")

            callback_url = await server.wait_for_callback(timeout_s=1.0)
        finally:
            await server.close()

        self.assertEqual(response.status_code, 200)
        self.assertIn("Authorization complete", response.text)
        self.assertIn("code=abc123", callback_url)
        self.assertIn("state=state-1", callback_url)
