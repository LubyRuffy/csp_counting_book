from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import release  # noqa: E402


class VersionSelectionTests(unittest.TestCase):
    def test_without_version_immediately_increments_latest_patch(self) -> None:
        latest = {
            "tagName": "v1.2.3",
        }

        version, reason = release.choose_version(None, latest)

        self.assertEqual(version, "v1.2.4")
        self.assertEqual(reason, "automatic patch increment from v1.2.3")

    def test_without_existing_release_starts_at_v0_1_0(self) -> None:
        self.assertEqual(
            release.choose_version(None, None),
            ("v0.1.0", "first release"),
        )

    def test_explicit_version_is_preserved(self) -> None:
        self.assertEqual(
            release.choose_version("v2.0.0", {"tagName": "v1.9.9"}),
            ("v2.0.0", "explicit VERSION"),
        )

    def test_invalid_explicit_version_is_rejected(self) -> None:
        with self.assertRaisesRegex(release.ReleaseError, "Invalid version"):
            release.choose_version("1.2.3", None)


class ReleaseWorkflowTests(unittest.TestCase):
    @mock.patch.object(release, "publish", return_value="https://example.test/v1.2.4")
    @mock.patch.object(release, "build_assets", return_value=[Path("book.pdf")])
    @mock.patch.object(release, "ensure_version_available")
    @mock.patch.object(
        release,
        "latest_release",
        return_value={"tagName": "v1.2.3"},
    )
    @mock.patch.object(release, "require_pushed_head")
    @mock.patch.object(release, "run")
    @mock.patch.object(release, "ensure_gh", return_value="/usr/bin/gh")
    @mock.patch.object(release, "require_clean_worktree")
    @mock.patch.object(release.shutil, "which", return_value="/usr/bin/git")
    @mock.patch.object(release, "parse_args", return_value=mock.Mock(version=None))
    def test_main_publishes_next_patch_without_version(
        self,
        _parse_args: mock.Mock,
        _which: mock.Mock,
        _clean: mock.Mock,
        _ensure_gh: mock.Mock,
        run: mock.Mock,
        _pushed: mock.Mock,
        _latest: mock.Mock,
        available: mock.Mock,
        build: mock.Mock,
        publish: mock.Mock,
    ) -> None:
        run.return_value = mock.Mock(returncode=0)

        self.assertEqual(release.main(), 0)

        available.assert_called_once_with("/usr/bin/gh", "v1.2.4")
        self.assertEqual(build.call_args.args[0], "v1.2.4")
        publish.assert_called_once_with(
            "/usr/bin/gh",
            "v1.2.4",
            [Path("book.pdf")],
        )


if __name__ == "__main__":
    unittest.main()
