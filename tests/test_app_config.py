from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import main


class AppConfigTests(unittest.TestCase):
    def test_load_app_config_reads_values_from_toml(self):
        import app_config

        with TemporaryDirectory() as tmpdir:
            config_file = Path(tmpdir) / "app_config.toml"
            config_file.write_text(
                'account_file = "accounts.txt"\n'
                'oauth_client_id = "client-123"\n'
                'oauth_redirect_port = 2456\n',
                encoding="utf-8",
            )

            config = app_config.load_app_config(config_file)

        self.assertEqual(config.account_file, (config_file.parent / "accounts.txt").resolve())
        self.assertEqual(config.oauth_client_id, "client-123")
        self.assertEqual(config.oauth_redirect_port, 2456)
        self.assertEqual(config.mail_api_provider, "outlook_rest")
        self.assertEqual(config.mail_refresh_scope, "")

    def test_load_app_config_normalizes_mail_provider_and_scope(self):
        import app_config

        with TemporaryDirectory() as tmpdir:
            config_file = Path(tmpdir) / "app_config.toml"
            config_file.write_text(
                'account_file = "accounts.txt"\n'
                'mail_api_provider = "oauth"\n'
                'mail_refresh_scope = "https://graph.microsoft.com/Mail.Read offline_access"\n',
                encoding="utf-8",
            )

            config = app_config.load_app_config(config_file)

        self.assertEqual(config.mail_api_provider, "outlook_rest")
        self.assertEqual(config.mail_refresh_scope, "https://graph.microsoft.com/Mail.Read offline_access")

    def test_main_uses_values_loaded_from_config_file(self):
        import app_config

        config = app_config.load_app_config(Path("app_config.toml"))

        self.assertEqual(main.ACCOUNT_FILE, config.account_file)
        self.assertEqual(main.OAUTH_CLIENT.client_id, config.oauth_client_id)
        self.assertEqual(main.OAUTH_CLIENT.redirect_port, config.oauth_redirect_port)
