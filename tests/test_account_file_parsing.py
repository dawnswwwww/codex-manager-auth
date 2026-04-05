from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch, call

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


class RunAccountsTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_accounts_streams_account_file_and_logs_in_eligible_rows_from_checkpoint(self):
        with TemporaryDirectory() as tmpdir:
            accounts_file = Path(tmpdir) / "accounts.txt"
            accounts_file.write_text(
                "alpha@example.com----Alpha123----client-a----refresh-a\n"
                "beta@example.com----Beta123----client-b----refresh-b\n",
                encoding="utf-8",
            )
            expected_csv_path = Path(tmpdir) / "checkpoint.csv"
            alpha_account = main.AccountRecord(
                email="alpha@example.com",
                password="Alpha123",
                client_id="client-a",
                refresh_token="refresh-a",
            )
            beta_account = main.AccountRecord(
                email="beta@example.com",
                password="Beta123",
                client_id="client-b",
                refresh_token="refresh-b",
            )

            with patch.object(
                main,
                "load_accounts",
                side_effect=AssertionError("run_accounts should stream the account file instead of calling load_accounts"),
            ) as load_accounts_mock, patch.object(
                app_runner,
                "run_registration_stage",
                AsyncMock(side_effect=[
                    main.AccountExecutionResult(
                        email="alpha@example.com",
                        password="Alpha123000",
                        registration_status="success",
                        login_status="pending",
                        error_reason="",
                    ),
                    main.AccountExecutionResult(
                        email="beta@example.com",
                        password="Beta123000",
                        registration_status="failed",
                        login_status="skipped",
                        error_reason="",
                    ),
                ]),
            ) as registration_mock, patch.object(
                app_runner,
                "run_login_stage",
                AsyncMock(
                    return_value=main.AccountExecutionResult(
                        email="alpha@example.com",
                        password="Alpha123000",
                        registration_status="success",
                        login_status="success",
                        error_reason="",
                    )
                ),
            ) as login_mock, patch.object(
                app_runner,
                "build_checkpoint_csv_path",
                return_value=expected_csv_path,
            ):
                csv_path = await main.run_accounts(accounts_file)

        load_accounts_mock.assert_not_called()
        self.assertEqual(
            registration_mock.await_args_list,
            [
                call(alpha_account),
                call(beta_account),
            ],
        )
        login_mock.assert_awaited_once()
        self.assertEqual(login_mock.await_args.args[0], alpha_account)
        self.assertEqual(login_mock.await_args.args[1].email, "alpha@example.com")
        self.assertEqual(login_mock.await_args.args[1].registration_status, "success")
        self.assertEqual(csv_path, expected_csv_path)

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
