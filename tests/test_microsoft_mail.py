import unittest
from unittest.mock import patch

from codex_manager_auth import microsoft_mail_api
from codex_manager_auth import microsoft_oauth


class MicrosoftOAuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_exchange_refresh_token_omits_scope_when_not_provided(self):
        captured = {}

        class _FakeResponse:
            status_code = 200

            def json(self):
                return {"access_token": "access-token"}

        class _FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def post(self, url, data=None):
                captured["url"] = url
                captured["data"] = dict(data)
                return _FakeResponse()

        with patch("codex_manager_auth.microsoft_oauth.httpx.AsyncClient", return_value=_FakeClient()):
            token = await microsoft_oauth.exchange_refresh_token("refresh-token", "client-123")

        self.assertEqual(token, "access-token")
        self.assertEqual(captured["url"], microsoft_oauth.TOKEN_URL)
        self.assertEqual(captured["data"]["client_id"], "client-123")
        self.assertNotIn("scope", captured["data"])

    async def test_exchange_refresh_token_includes_scope_when_provided(self):
        captured = {}

        class _FakeResponse:
            status_code = 200

            def json(self):
                return {"access_token": "access-token"}

        class _FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def post(self, url, data=None):
                captured["data"] = dict(data)
                return _FakeResponse()

        with patch("codex_manager_auth.microsoft_oauth.httpx.AsyncClient", return_value=_FakeClient()):
            await microsoft_oauth.exchange_refresh_token(
                "refresh-token",
                "client-123",
                scope="https://graph.microsoft.com/Mail.Read offline_access",
            )

        self.assertEqual(
            captured["data"]["scope"],
            "https://graph.microsoft.com/Mail.Read offline_access",
        )


class MicrosoftMailApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_verification_code_uses_graph_provider_shape(self):
        captured = {}

        class _FakeResponse:
            status_code = 200

            def json(self):
                return {
                    "value": [
                        {
                            "subject": "Your verification code",
                            "bodyPreview": "code is 654321",
                        }
                    ]
                }

        class _FakeClient:
            def __init__(self, *args, **kwargs):
                captured["base_url"] = kwargs["base_url"]
                captured["headers"] = kwargs["headers"]

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def get(self, path):
                captured["path"] = path
                return _FakeResponse()

        with patch("codex_manager_auth.microsoft_mail_api.httpx.AsyncClient", _FakeClient):
            code = await microsoft_mail_api.fetch_verification_code(
                "access-token",
                provider="graph",
                max_retries=1,
            )

        self.assertEqual(code, "654321")
        self.assertEqual(captured["base_url"], microsoft_mail_api.GRAPH_BASE)
        self.assertEqual(captured["path"], "/me/messages?$top=10&$orderby=receivedDateTime desc")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer access-token")

