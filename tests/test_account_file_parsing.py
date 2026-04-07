from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch

import main
from codex_manager_auth import runner as app_runner


class AccountFileParsingTests(unittest.TestCase):
    def test_normalize_password_pads_short_password_to_twelve_chars(self):
        self.assertEqual(main.normalize_password("Secret123"), "Secret123000")

    def test_normalize_password_keeps_password_with_twelve_or_more_chars(self):
        self.assertEqual(main.normalize_password("Secret123456"), "Secret123456")
        self.assertEqual(main.normalize_password("Secret123456789"), "Secret123456789")

    def test_parse_account_line_returns_structured_account(self):
        account = main.parse_account_line(
            "user@example.com----Secret123----client-1----refresh-1",
            line_number=7,
        )

        self.assertEqual(account.email, "user@example.com")
        self.assertEqual(account.password, "Secret123")
        self.assertEqual(account.client_id, "client-1")
        self.assertEqual(account.refresh_token, "refresh-1")

    def test_parse_account_line_rejects_invalid_format(self):
        with self.assertRaisesRegex(ValueError, "line 3"):
            main.parse_account_line("broken-line", line_number=3)

    def test_load_accounts_skips_blank_lines(self):
        with TemporaryDirectory() as tmpdir:
            accounts_file = Path(tmpdir) / "accounts.txt"
            accounts_file.write_text(
                "\n"
                "alpha@example.com----Alpha123----client-a----refresh-a\n"
                "\n"
                "beta@example.com----Beta123----client-b----refresh-b\n",
                encoding="utf-8",
            )

            accounts = main.load_accounts(accounts_file)

        self.assertEqual(
            [(account.email, account.password, account.client_id, account.refresh_token) for account in accounts],
            [
                ("alpha@example.com", "Alpha123", "client-a", "refresh-a"),
                ("beta@example.com", "Beta123", "client-b", "refresh-b"),
            ],
        )

    def test_iter_accounts_streams_lines_without_using_read_text(self):
        with TemporaryDirectory() as tmpdir:
            accounts_file = Path(tmpdir) / "accounts.txt"
            accounts_file.write_text(
                "alpha@example.com----Alpha123----client-a----refresh-a\n"
                "beta@example.com----Beta123----client-b----refresh-b\n",
                encoding="utf-8",
            )

            with patch.object(Path, "read_text", side_effect=AssertionError("iter_accounts should not call read_text")):
                accounts = list(main.iter_accounts(accounts_file))

        self.assertEqual([account.email for account in accounts], ["alpha@example.com", "beta@example.com"])

    def test_build_checkpoint_csv_path_is_stable_for_the_same_account_file(self):
        accounts_file = Path("/tmp/宝贝信息-985260403233027741.txt")

        first = main.build_checkpoint_csv_path(accounts_file)
        second = main.build_checkpoint_csv_path(accounts_file)

        self.assertEqual(first, second)
        self.assertEqual(first.name, "宝贝信息-985260403233027741_checkpoint.csv")

    def test_build_batch_token_output_dir_uses_account_file_stem_and_timestamp(self):
        batch_token_dir = app_runner.build_batch_token_output_dir(
            Path("/tmp/a.txt"),
            token_root=Path("/tmp/tokens"),
            started_at=datetime(2026, 4, 7, 15, 30, 45),
        )

        self.assertEqual(batch_token_dir, Path("/tmp/tokens/a-20260407-153045-tokens"))


class RunAccountsTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_accounts_delegates_to_full_chain_execution(self):
        with TemporaryDirectory() as tmpdir:
            accounts_file = Path(tmpdir) / "accounts.txt"
            accounts_file.write_text(
                "alpha@example.com----Alpha123----client-a----refresh-a\n"
                "beta@example.com----Beta123----client-b----refresh-b\n",
                encoding="utf-8",
            )
            expected_csv_path = Path(tmpdir) / "checkpoint.csv"
            with patch.object(
                app_runner,
                "run_accounts_full_chain",
                AsyncMock(return_value=expected_csv_path),
            ) as full_chain_mock:
                csv_path = await main.run_accounts(accounts_file)

        full_chain_mock.assert_awaited_once_with(accounts_file)
        self.assertEqual(csv_path, expected_csv_path)

    async def test_run_accounts_full_chain_uses_batch_token_directory_named_after_account_file(self):
        with TemporaryDirectory() as tmpdir:
            accounts_file = Path(tmpdir) / "a.txt"
            accounts_file.write_text(
                "alpha@example.com----Alpha123----client-a----refresh-a\n",
                encoding="utf-8",
            )
            expected_csv_path = Path(tmpdir) / "checkpoint.csv"
            batch_token_dir = Path(tmpdir) / "tokens" / "a-20260407-153045-tokens"
            observed = {}
            final_result = main.AccountExecutionResult(
                email="alpha@example.com",
                password="Alpha123000",
                registration_status="already_exists",
                login_status="success",
                error_reason="",
            )

            class _FakeOAuthClient:
                client_id = "client-123"
                redirect_port = 2456
                scope = "openid profile"
                token_output_dir = Path(tmpdir) / "tokens"

            async def fake_run(**kwargs):
                observed["token_output_dir"] = kwargs["oauth_client"].token_output_dir
                return final_result

            with patch.object(
                app_runner,
                "build_checkpoint_csv_path",
                return_value=expected_csv_path,
            ), patch.object(
                app_runner,
                "build_batch_token_output_dir",
                return_value=batch_token_dir,
            ) as batch_dir_mock, patch.object(
                app_runner,
                "run",
                AsyncMock(side_effect=fake_run),
            ), patch.object(
                app_runner,
                "OAUTH_CLIENT",
                _FakeOAuthClient(),
            ):
                await main.run_accounts_full_chain(accounts_file)

        batch_dir_mock.assert_called_once()
        self.assertEqual(observed["token_output_dir"], batch_token_dir)

    async def test_run_accounts_full_chain_updates_checkpoint_with_final_result(self):
        with TemporaryDirectory() as tmpdir:
            accounts_file = Path(tmpdir) / "accounts.txt"
            accounts_file.write_text(
                "alpha@example.com----Alpha123----client-a----refresh-a\n",
                encoding="utf-8",
            )
            expected_csv_path = Path(tmpdir) / "checkpoint.csv"
            final_result = main.AccountExecutionResult(
                email="alpha@example.com",
                password="Alpha123000",
                registration_status="already_exists",
                login_status="success",
                error_reason="",
            )

            with patch.object(
                app_runner,
                "build_checkpoint_csv_path",
                return_value=expected_csv_path,
            ), patch.object(
                app_runner,
                "run",
                AsyncMock(return_value=final_result),
            ):
                csv_path = await main.run_accounts_full_chain(accounts_file)

            self.assertEqual(csv_path, expected_csv_path)
            content = expected_csv_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(
                content,
                [
                    "email,password,registration_status,login_status,error_reason",
                    "alpha@example.com,Alpha123000,already_exists,success,",
                ],
            )

    async def test_run_accounts_full_chain_skips_accounts_that_already_have_token_files(self):
        with TemporaryDirectory() as tmpdir:
            accounts_file = Path(tmpdir) / "accounts.txt"
            accounts_file.write_text(
                "alpha@example.com----Alpha123----client-a----refresh-a\n",
                encoding="utf-8",
            )
            expected_csv_path = Path(tmpdir) / "checkpoint.csv"
            token_dir = Path(tmpdir) / "tokens"
            token_dir.mkdir()
            (token_dir / "alpha@example.com.json").write_text("{}", encoding="utf-8")

            class _FakeOAuthClient:
                token_output_dir = token_dir

                def build_token_filename(self, email):
                    return f"{email}.json"

            with patch.object(
                app_runner,
                "build_checkpoint_csv_path",
                return_value=expected_csv_path,
            ), patch.object(
                app_runner,
                "build_batch_token_output_dir",
                return_value=token_dir,
            ), patch.object(
                app_runner,
                "run",
                AsyncMock(),
            ) as run_mock, patch.object(
                app_runner,
                "OAUTH_CLIENT",
                _FakeOAuthClient(),
            ):
                await main.run_accounts_full_chain(accounts_file)

            run_mock.assert_not_awaited()
            content = expected_csv_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(
                content,
                [
                    "email,password,registration_status,login_status,error_reason",
                    "alpha@example.com,Alpha1230000,success,success,",
                ],
            )

    async def test_run_accounts_full_chain_stops_after_first_failure(self):
        with TemporaryDirectory() as tmpdir:
            accounts_file = Path(tmpdir) / "accounts.txt"
            accounts_file.write_text(
                "alpha@example.com----Alpha123----client-a----refresh-a\n"
                "beta@example.com----Beta123----client-b----refresh-b\n",
                encoding="utf-8",
            )
            expected_csv_path = Path(tmpdir) / "checkpoint.csv"
            failure = main.AccountExecutionResult(
                email="alpha@example.com",
                password="Alpha123000",
                registration_status="success",
                login_status="failed",
                error_reason="callback timeout",
            )

            class _FakeOAuthClient:
                token_output_dir = Path(tmpdir) / "tokens"

                def build_token_filename(self, email):
                    return f"{email}.json"

            with patch.object(
                app_runner,
                "build_checkpoint_csv_path",
                return_value=expected_csv_path,
            ), patch.object(
                app_runner,
                "run",
                AsyncMock(return_value=failure),
            ) as run_mock, patch.object(
                app_runner,
                "OAUTH_CLIENT",
                _FakeOAuthClient(),
            ):
                await main.run_accounts_full_chain(accounts_file)

            self.assertEqual(run_mock.await_count, 1)

    async def test_run_accounts_full_chain_raises_on_terminal_failure_when_requested(self):
        with TemporaryDirectory() as tmpdir:
            accounts_file = Path(tmpdir) / "accounts.txt"
            accounts_file.write_text(
                "alpha@example.com----Alpha123----client-a----refresh-a\n"
                "beta@example.com----Beta123----client-b----refresh-b\n",
                encoding="utf-8",
            )
            expected_csv_path = Path(tmpdir) / "checkpoint.csv"
            failure = main.AccountExecutionResult(
                email="alpha@example.com",
                password="Alpha123000",
                registration_status="success",
                login_status="failed",
                error_reason="callback timeout",
            )

            class _FakeOAuthClient:
                client_id = "client-123"
                redirect_port = 2456
                scope = "openid profile"
                token_output_dir = Path(tmpdir) / "tokens"

                def build_token_filename(self, email):
                    return f"{email}.json"

            with patch.object(
                app_runner,
                "build_checkpoint_csv_path",
                return_value=expected_csv_path,
            ), patch.object(
                app_runner,
                "run",
                AsyncMock(return_value=failure),
            ), patch.object(
                app_runner,
                "OAUTH_CLIENT",
                _FakeOAuthClient(),
            ):
                with self.assertRaisesRegex(RuntimeError, "alpha@example.com: callback timeout"):
                    await main.run_accounts_full_chain(accounts_file, raise_on_failure=True)

    async def test_run_accounts_full_chain_continues_past_disabled_accounts(self):
        with TemporaryDirectory() as tmpdir:
            accounts_file = Path(tmpdir) / "accounts.txt"
            accounts_file.write_text(
                "alpha@example.com----Alpha123----client-a----refresh-a\n"
                "beta@example.com----Beta123----client-b----refresh-b\n",
                encoding="utf-8",
            )
            expected_csv_path = Path(tmpdir) / "checkpoint.csv"
            disabled = main.AccountExecutionResult(
                email="alpha@example.com",
                password="Alpha123000",
                registration_status="success",
                login_status="failed",
                error_reason="OpenAI required a phone number on the add-phone page",
            )
            success = main.AccountExecutionResult(
                email="beta@example.com",
                password="Beta123000",
                registration_status="already_exists",
                login_status="success",
                error_reason="",
            )

            class _FakeOAuthClient:
                token_output_dir = Path(tmpdir) / "tokens"

                def build_token_filename(self, email):
                    return f"{email}.json"

            with patch.object(
                app_runner,
                "build_checkpoint_csv_path",
                return_value=expected_csv_path,
            ), patch.object(
                app_runner,
                "run",
                AsyncMock(side_effect=[disabled, success]),
            ) as run_mock, patch.object(
                app_runner,
                "OAUTH_CLIENT",
                _FakeOAuthClient(),
            ):
                await main.run_accounts_full_chain(accounts_file)

            self.assertEqual(run_mock.await_count, 2)
    async def test_run_accounts_full_chain_continues_past_remote_verification_blocks(self):
        with TemporaryDirectory() as tmpdir:
            accounts_file = Path(tmpdir) / "accounts.txt"
            accounts_file.write_text(
                "alpha@example.com----Alpha123----client-a----refresh-a\n"
                "beta@example.com----Beta123----client-b----refresh-b\n",
                encoding="utf-8",
            )
            expected_csv_path = Path(tmpdir) / "checkpoint.csv"
            blocked = main.AccountExecutionResult(
                email="alpha@example.com",
                password="Alpha123000",
                registration_status="already_exists",
                login_status="failed",
                error_reason="OpenAI verification session hit max_check_attempts after retries",
            )
            success = main.AccountExecutionResult(
                email="beta@example.com",
                password="Beta123000",
                registration_status="already_exists",
                login_status="success",
                error_reason="",
            )

            class _FakeOAuthClient:
                token_output_dir = Path(tmpdir) / "tokens"

                def build_token_filename(self, email):
                    return f"{email}.json"

            with patch.object(
                app_runner,
                "build_checkpoint_csv_path",
                return_value=expected_csv_path,
            ), patch.object(
                app_runner,
                "run",
                AsyncMock(side_effect=[blocked, success]),
            ) as run_mock, patch.object(
                app_runner,
                "OAUTH_CLIENT",
                _FakeOAuthClient(),
            ):
                await main.run_accounts_full_chain(accounts_file)

            self.assertEqual(run_mock.await_count, 2)
    async def test_run_accounts_full_chain_continues_past_transient_auth_navigation_errors(self):
        with TemporaryDirectory() as tmpdir:
            accounts_file = Path(tmpdir) / "accounts.txt"
            accounts_file.write_text(
                "alpha@example.com----Alpha123----client-a----refresh-a\n"
                "beta@example.com----Beta123----client-b----refresh-b\n",
                encoding="utf-8",
            )
            expected_csv_path = Path(tmpdir) / "checkpoint.csv"
            blocked = main.AccountExecutionResult(
                email="alpha@example.com",
                password="Alpha123000",
                registration_status="failed",
                login_status="skipped",
                error_reason="Page.goto: net::ERR_CONNECTION_CLOSED at https://auth.openai.com/oauth/authorize?...",
            )
            success = main.AccountExecutionResult(
                email="beta@example.com",
                password="Beta123000",
                registration_status="already_exists",
                login_status="success",
                error_reason="",
            )

            class _FakeOAuthClient:
                token_output_dir = Path(tmpdir) / "tokens"

                def build_token_filename(self, email):
                    return f"{email}.json"

            with patch.object(
                app_runner,
                "build_checkpoint_csv_path",
                return_value=expected_csv_path,
            ), patch.object(
                app_runner,
                "run",
                AsyncMock(side_effect=[blocked, success]),
            ) as run_mock, patch.object(
                app_runner,
                "OAUTH_CLIENT",
                _FakeOAuthClient(),
            ):
                await main.run_accounts_full_chain(accounts_file)

            self.assertEqual(run_mock.await_count, 2)
    async def test_run_accounts_full_chain_continues_past_account_deactivated_pages(self):
        with TemporaryDirectory() as tmpdir:
            accounts_file = Path(tmpdir) / "accounts.txt"
            accounts_file.write_text(
                "alpha@example.com----Alpha123----client-a----refresh-a\n"
                "beta@example.com----Beta123----client-b----refresh-b\n",
                encoding="utf-8",
            )
            expected_csv_path = Path(tmpdir) / "checkpoint.csv"
            blocked = main.AccountExecutionResult(
                email="alpha@example.com",
                password="Alpha123000",
                registration_status="already_exists",
                login_status="failed",
                error_reason="OpenAI reported account_deactivated during verification",
            )
            success = main.AccountExecutionResult(
                email="beta@example.com",
                password="Beta123000",
                registration_status="already_exists",
                login_status="success",
                error_reason="",
            )

            class _FakeOAuthClient:
                token_output_dir = Path(tmpdir) / "tokens"

                def build_token_filename(self, email):
                    return f"{email}.json"

            with patch.object(
                app_runner,
                "build_checkpoint_csv_path",
                return_value=expected_csv_path,
            ), patch.object(
                app_runner,
                "run",
                AsyncMock(side_effect=[blocked, success]),
            ) as run_mock, patch.object(
                app_runner,
                "OAUTH_CLIENT",
                _FakeOAuthClient(),
            ):
                await main.run_accounts_full_chain(accounts_file)

            self.assertEqual(run_mock.await_count, 2)


class MainEntrypointTests(unittest.TestCase):
    def test_main_uses_full_chain_runner(self):
        observed = {}

        async def fake_full_chain(accounts_file, raise_on_failure=False):
            observed["accounts_file"] = accounts_file
            observed["raise_on_failure"] = raise_on_failure
            return Path("/tmp/checkpoint.csv")

        with patch.object(
            app_runner,
            "run_accounts",
            AsyncMock(side_effect=AssertionError("main should not use split-stage account execution")),
        ), patch.object(
            app_runner,
            "run_accounts_full_chain",
            fake_full_chain,
        ):
            main.main()

        self.assertEqual(observed["accounts_file"], app_runner.ACCOUNT_FILE)
        self.assertTrue(observed["raise_on_failure"])
