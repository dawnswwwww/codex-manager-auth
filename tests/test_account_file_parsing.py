from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch, call

import main


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


class RunAccountsTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_accounts_registers_all_accounts_then_logs_in_eligible_rows_from_csv(self):
        accounts = [
            main.AccountRecord(
                email="alpha@example.com",
                password="Alpha123",
                client_id="client-a",
                refresh_token="refresh-a",
            ),
            main.AccountRecord(
                email="beta@example.com",
                password="Beta123",
                client_id="client-b",
                refresh_token="refresh-b",
            ),
        ]

        with TemporaryDirectory() as tmpdir:
            expected_csv_path = Path(tmpdir) / "results.csv"
            with patch.object(main, "load_accounts", return_value=accounts), patch.object(
                main,
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
                main,
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
                main,
                "build_results_csv_path",
                return_value=expected_csv_path,
            ):
                csv_path = await main.run_accounts(Path("accounts.txt"))

        self.assertEqual(
            registration_mock.await_args_list,
            [
                call(accounts[0]),
                call(accounts[1]),
            ],
        )
        login_mock.assert_awaited_once()
        self.assertEqual(login_mock.await_args.args[0], accounts[0])
        self.assertEqual(login_mock.await_args.args[1].email, "alpha@example.com")
        self.assertEqual(login_mock.await_args.args[1].registration_status, "success")
        self.assertEqual(csv_path, expected_csv_path)
