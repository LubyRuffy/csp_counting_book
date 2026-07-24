from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import build_support  # noqa: E402


class InstallMacosToolchainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {"images": {"optimize": True}}

    @mock.patch.object(build_support, "refresh_macos_tool_path")
    @mock.patch.object(build_support, "privileged_command", side_effect=lambda value: value)
    @mock.patch.object(build_support, "run")
    @mock.patch.object(build_support.platform, "system", return_value="Darwin")
    def test_installs_complete_macos_toolchain(
        self,
        _system: mock.Mock,
        run: mock.Mock,
        _privileged: mock.Mock,
        _refresh: mock.Mock,
    ) -> None:
        locations = {
            "brew": "/opt/homebrew/bin/brew",
            "pdfinfo": None,
            "tlmgr": "/Library/TeX/texbin/tlmgr",
        }
        missing = [
            "pandoc",
            "xelatex",
            "ebook-convert",
            "Pillow (python3-pil)",
        ]
        with mock.patch.object(
            build_support.shutil,
            "which",
            side_effect=lambda name: locations.get(name),
        ):
            build_support.install_macos_toolchain(
                {"pdf", "epub", "mobi"},
                self.config,
                missing,
                root=ROOT,
            )

        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn(
            ["/opt/homebrew/bin/brew", "install", "pandoc", "poppler"],
            commands,
        )
        self.assertIn(
            [
                "/opt/homebrew/bin/brew",
                "install",
                "--cask",
                "basictex",
            ],
            commands,
        )
        self.assertIn(
            [
                "/opt/homebrew/bin/brew",
                "install",
                "--cask",
                "calibre",
            ],
            commands,
        )
        self.assertIn(
            [
                "/Library/TeX/texbin/tlmgr",
                "update",
                "--self",
                "--all",
            ],
            commands,
        )
        self.assertIn(
            [
                "/Library/TeX/texbin/tlmgr",
                "install",
                *build_support.MACOS_TEX_PACKAGES,
            ],
            commands,
        )
        self.assertIn(
            [
                sys.executable,
                "-m",
                "venv",
                ROOT / build_support.PROJECT_VENV_DIRECTORY,
            ],
            commands,
        )
        self.assertIn(
            [
                build_support.project_venv_python(root=ROOT),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "Pillow",
            ],
            commands,
        )

    def test_dependency_probe_reuses_project_venv_packages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            site_packages = (
                root
                / build_support.PROJECT_VENV_DIRECTORY
                / "lib"
                / f"python{sys.version_info.major}.{sys.version_info.minor}"
                / "site-packages"
            )
            site_packages.mkdir(parents=True)

            with mock.patch.object(sys, "path", list(sys.path)):
                build_support.activate_project_venv(root=root)
                self.assertEqual(sys.path[0], str(site_packages))

    @mock.patch.object(build_support, "refresh_macos_tool_path")
    @mock.patch.object(build_support, "run")
    @mock.patch.object(build_support.platform, "system", return_value="Darwin")
    def test_epub_setup_does_not_install_tex_or_calibre(
        self,
        _system: mock.Mock,
        run: mock.Mock,
        _refresh: mock.Mock,
    ) -> None:
        with mock.patch.object(
            build_support.shutil,
            "which",
            side_effect=lambda name: (
                "/opt/homebrew/bin/brew" if name == "brew" else None
            ),
        ):
            build_support.install_macos_toolchain(
                {"epub"},
                {"images": {"optimize": False}},
                ["pandoc"],
                root=ROOT,
            )

        run.assert_called_once_with(
            ["/opt/homebrew/bin/brew", "install", "pandoc"],
            cwd=ROOT,
        )

    @mock.patch.object(build_support.platform, "system", return_value="Darwin")
    @mock.patch.object(build_support.shutil, "which", return_value=None)
    def test_reports_missing_homebrew(
        self,
        _which: mock.Mock,
        _system: mock.Mock,
    ) -> None:
        with self.assertRaisesRegex(build_support.BuildError, "Homebrew"):
            build_support.install_macos_toolchain(
                {"pdf"},
                self.config,
                ["pandoc"],
                root=ROOT,
            )

    @mock.patch.object(build_support.platform, "system", return_value="Linux")
    def test_rejects_non_macos_host(self, _system: mock.Mock) -> None:
        with self.assertRaisesRegex(build_support.BuildError, "non-macOS"):
            build_support.install_macos_toolchain(
                {"pdf"},
                self.config,
                ["pandoc"],
                root=ROOT,
            )

    @mock.patch.object(build_support, "cache_calibre_from_github")
    @mock.patch.object(build_support, "refresh_macos_tool_path")
    @mock.patch.object(build_support.platform, "system", return_value="Darwin")
    def test_calibre_uses_verified_github_fallback(
        self,
        _system: mock.Mock,
        _refresh: mock.Mock,
        fallback: mock.Mock,
    ) -> None:
        calls = 0

        def fail_first_calibre(command: list[str], **_kwargs: object) -> mock.Mock:
            nonlocal calls
            if command[-1] == "calibre":
                calls += 1
                if calls == 1:
                    raise subprocess.CalledProcessError(1, command)
            return mock.Mock()

        locations = {
            "brew": "/opt/homebrew/bin/brew",
            "pdfinfo": "/usr/local/bin/pdfinfo",
        }
        with (
            mock.patch.object(
                build_support.shutil,
                "which",
                side_effect=lambda name: locations.get(name),
            ),
            mock.patch.object(build_support, "run", side_effect=fail_first_calibre),
        ):
            build_support.install_macos_toolchain(
                {"mobi"},
                {"images": {"optimize": False}},
                ["ebook-convert"],
                root=ROOT,
            )

        fallback.assert_called_once_with(
            brew="/opt/homebrew/bin/brew",
            root=ROOT,
        )
        self.assertEqual(calls, 2)

    def test_calibre_fallback_populates_verified_homebrew_cache(self) -> None:
        content = b"verified calibre image"
        metadata = {
            "casks": [
                {
                    "version": "9.11.0",
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "calibre.dmg"

            def fake_run(command: list[str], **_kwargs: object) -> mock.Mock:
                if "info" in command:
                    return mock.Mock(stdout=json.dumps(metadata))
                if "--cache" in command:
                    return mock.Mock(stdout=f"{cache_path}\n")
                Path(command[command.index("--output") + 1]).write_bytes(content)
                return mock.Mock(stdout="")

            with (
                mock.patch.object(build_support, "run", side_effect=fake_run),
                mock.patch.object(
                    build_support.shutil,
                    "which",
                    return_value="/usr/bin/curl",
                ),
            ):
                build_support.cache_calibre_from_github(
                    brew="/opt/homebrew/bin/brew",
                    root=ROOT,
                )

            self.assertEqual(cache_path.read_bytes(), content)

    @mock.patch.object(build_support, "pillow_available", return_value=True)
    @mock.patch.object(build_support, "required_tools")
    def test_dependency_probe_reports_missing_tex_file(
        self,
        required: mock.Mock,
        _pillow: mock.Mock,
    ) -> None:
        required.return_value = {
            "pandoc": "pandoc",
            "xelatex": "xelatex",
        }

        def probe(command: list[str], **_kwargs: object) -> mock.Mock:
            filename = command[-1]
            stdout = "" if filename == "framed.sty" else f"/tex/{filename}\n"
            return mock.Mock(returncode=0 if stdout else 1, stdout=stdout)

        with (
            mock.patch.object(
                build_support.shutil,
                "which",
                side_effect=lambda name: f"/bin/{name}",
            ),
            mock.patch.object(
                build_support.subprocess,
                "run",
                side_effect=probe,
            ),
        ):
            missing = build_support.missing_tools(
                {"pdf"},
                {"images": {"optimize": True}},
                root=ROOT,
            )

        self.assertEqual(
            missing,
            ["framed.sty (TeX Live package framed)"],
        )


if __name__ == "__main__":
    unittest.main()
