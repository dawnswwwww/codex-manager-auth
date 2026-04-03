from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from codex_manager_auth.openai_oauth import OpenAIOAuthClient


class OpenAIOAuthClientTests(unittest.IsolatedAsyncioTestCase):
    def test_build_auth_url_contains_dynamic_pkce_and_redirect_uri(self):
        client = OpenAIOAuthClient(client_id="client-123", redirect_port=2456, token_output_dir=Path("tokens"))
        session = client.create_session()

        auth_url = client.build_auth_url(session)
        query = parse_qs(urlparse(auth_url).query)

        self.assertEqual(query["response_type"], ["code"])
        self.assertEqual(query["client_id"], ["client-123"])
        self.assertEqual(query["redirect_uri"], ["http://localhost:2456/auth/callback"])
        self.assertEqual(query["scope"], ["openid profile email offline_access api.connectors.read api.connectors.invoke"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["id_token_add_organizations"], ["true"])
        self.assertEqual(query["codex_cli_simplified_flow"], ["true"])
        self.assertEqual(query["originator"], ["codex_cli_rs"])
        self.assertEqual(query["state"], [session.state])
        self.assertEqual(query["code_challenge"], [session.code_challenge])

    def test_extract_callback_params_returns_code_for_matching_state(self):
        client = OpenAIOAuthClient(client_id="client-123", redirect_port=2456, token_output_dir=Path("tokens"))
        session = client.create_session()

        params = client.extract_callback_params(
            f"{client.redirect_uri}?code=abc123&state={session.state}",
            session,
        )

        self.assertIsNotNone(params)
        self.assertEqual(params["code"], "abc123")

    async def test_exchange_token_and_save_writes_token_file(self):
        with TemporaryDirectory() as tmpdir:
            client = OpenAIOAuthClient(
                client_id="client-123",
                redirect_port=2456,
                token_output_dir=Path(tmpdir),
            )
            session = client.create_session()
            response_payload = {
                "access_token": "header.eyJodHRwczovL2FwaS5vcGVuYWkuY29tL2F1dGgiOiB7ImNoYXRncHRfYWNjb3VudF9pZCI6ICJhY2N0LTEyMyJ9fQ.sig",
                "expires_in": 3600,
                "id_token": "id-token",
                "refresh_token": "refresh-token",
            }

            class _FakeResponse:
                status_code = 200

                def json(self):
                    return response_payload

            class _FakeClient:
                async def __aenter__(self):
                    return self

                async def __aexit__(self, exc_type, exc, tb):
                    return False

                async def post(self, url, data=None, headers=None):
                    return _FakeResponse()

            with patch("codex_manager_auth.openai_oauth.httpx.AsyncClient", return_value=_FakeClient()):
                token_data = await client.exchange_token_and_save("code-123", "user@example.com", session)

            self.assertEqual(token_data["account_id"], "acct-123")
            token_path = Path(tmpdir) / "user@example.com.json"
            self.assertTrue(token_path.exists())
            self.assertIn("user@example.com", token_path.read_text(encoding="utf-8"))
