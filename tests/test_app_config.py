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
                'openai_oauth_url = "https://example.com/oauth"\n',
                encoding="utf-8",
            )

            config = app_config.load_app_config(config_file)

        self.assertEqual(config.account_file, (config_file.parent / "accounts.txt").resolve())
        self.assertEqual(config.openai_oauth_url, "https://example.com/oauth")

    def test_main_uses_values_loaded_from_config_file(self):
        import app_config

        config = app_config.load_app_config(Path("app_config.toml"))

        self.assertEqual(main.ACCOUNT_FILE, config.account_file)
        self.assertEqual(main.OPENAI_OAUTH_URL, config.openai_oauth_url)
