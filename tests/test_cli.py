import argparse
import runpy
import sys
import unittest
from unittest.mock import Mock, patch

from smart_changelog import cli


class CLIModuleTests(unittest.TestCase):
    def test_update_command_invokes_run_update(self) -> None:
        with patch("smart_changelog.cli.run_update") as run_update:
            exit_code = cli.main(["update", "--dry-run", "--verbose", "--ticket", "ABC-1"])

        self.assertEqual(exit_code, 0)
        run_update.assert_called_once_with(
            dry_run=True,
            use_ai=False,
            forced_ticket="ABC-1",
            verbose=True,
        )

    def test_ai_flag_passthrough(self) -> None:
        with patch("smart_changelog.cli.run_update") as run_update:
            cli.main(["update", "--ai"])

        run_update.assert_called_once_with(
            dry_run=False,
            use_ai=True,
            forced_ticket=None,
            verbose=False,
        )

    def test_non_update_command_shows_help(self) -> None:
        fake_args = argparse.Namespace(command="other", dry_run=False, verbose=False, ai=False, ticket=None)
        fake_parser = Mock()
        fake_parser.parse_args.return_value = fake_args

        with patch("smart_changelog.cli._build_parser", return_value=fake_parser):
            result = cli.main([])

        fake_parser.print_help.assert_called_once()
        self.assertEqual(result, 1)

    def test_module_entrypoint(self) -> None:
        argv_backup = sys.argv[:]
        sys.argv = ["smart-changelog", "update", "--dry-run"]
        try:
            with patch("smart_changelog.updater.run_update") as run_update:
                with self.assertRaises(SystemExit) as exc:
                    runpy.run_module("smart_changelog.cli", run_name="__main__", alter_sys=True)
            self.assertEqual(exc.exception.code, 0)
            run_update.assert_called_once()
        finally:
            sys.argv = argv_backup


if __name__ == "__main__":
    unittest.main()
