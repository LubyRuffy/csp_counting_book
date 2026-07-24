from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import build_book  # noqa: E402


class AutoEngineTests(unittest.TestCase):
    @mock.patch.object(
        build_book,
        "installed_font_families",
        return_value={"songti sc", "pingfang sc", "menlo"},
    )
    @mock.patch.object(build_book.platform, "system", return_value="Darwin")
    def test_macos_uses_cjk_capable_monospace_font(
        self,
        _system: mock.Mock,
        _fonts: mock.Mock,
    ) -> None:
        selected = build_book.resolve_pdf_fonts({"pdf": {}})

        self.assertEqual(selected["mono_font"], "PingFang SC")
        self.assertEqual(selected["code_font"], "Menlo")

    @mock.patch.object(build_book, "local_build")
    @mock.patch.object(build_book, "install_macos_toolchain")
    @mock.patch.object(build_book, "refresh_macos_tool_path")
    @mock.patch.object(
        build_book,
        "missing_tools",
        side_effect=[["pandoc", "xelatex"], []],
    )
    @mock.patch.object(build_book, "is_wsl", return_value=False)
    @mock.patch.object(build_book.platform, "system", return_value="Darwin")
    def test_auto_engine_bootstraps_macos_then_builds(
        self,
        _system: mock.Mock,
        _wsl: mock.Mock,
        missing: mock.Mock,
        _refresh: mock.Mock,
        install: mock.Mock,
        local_build: mock.Mock,
    ) -> None:
        config = {"images": {"optimize": True}}
        requested = {"pdf"}

        build_book.build(config, "auto", requested)

        install.assert_called_once_with(
            {"pdf"},
            config,
            ["pandoc", "xelatex"],
            root=build_book.ROOT,
        )
        local_build.assert_called_once_with(config, requested)
        self.assertEqual(missing.call_count, 2)

    @mock.patch.object(build_book, "install_macos_toolchain")
    @mock.patch.object(build_book, "refresh_macos_tool_path")
    @mock.patch.object(
        build_book,
        "missing_tools",
        side_effect=[["pandoc"], ["pandoc"]],
    )
    @mock.patch.object(build_book, "is_wsl", return_value=False)
    @mock.patch.object(build_book.platform, "system", return_value="Darwin")
    def test_auto_engine_reports_dependency_still_missing(
        self,
        _system: mock.Mock,
        _wsl: mock.Mock,
        _missing: mock.Mock,
        _refresh: mock.Mock,
        _install: mock.Mock,
    ) -> None:
        with self.assertRaisesRegex(build_book.BuildError, "toolchain is incomplete"):
            build_book.build(
                {"images": {"optimize": True}},
                "auto",
                {"epub"},
            )


class EpubSyntaxHighlightingTests(unittest.TestCase):
    @mock.patch.object(
        build_book,
        "syntax_highlighting_argument",
        return_value="--syntax-highlighting=tango",
    )
    @mock.patch.object(build_book, "run")
    def test_epub_build_uses_apple_books_dark_theme_filter(
        self,
        run: mock.Mock,
        _highlighting: mock.Mock,
    ) -> None:
        config = {
            "book": {
                "cover": "imgs/cover.png",
                "title": "Test Book",
            },
        }

        build_book.build_epub(
            config,
            [build_book.ROOT / "01.md"],
            build_book.DIST_DIR / "test.epub",
        )

        command = run.call_args.args[0]
        self.assertIn("--syntax-highlighting=tango", command)
        self.assertIn("--data-dir=scripts/pandoc-data", command)
        self.assertIn("--lua-filter=scripts/epub.lua", command)
        self.assertIn("--css=styles/epub.css", command)

    @mock.patch.object(build_book, "run")
    def test_mobi_build_restores_reader_control_of_theme_sensitive_colors(
        self,
        run: mock.Mock,
    ) -> None:
        build_book.build_mobi(
            build_book.DIST_DIR / "test.epub",
            build_book.DIST_DIR / "test.mobi",
        )

        command = run.call_args.args[0]
        self.assertIn("--extra-css=styles/mobi.css", command)
        mobi_css = (build_book.ROOT / "styles" / "mobi.css").read_text(
            encoding="utf-8"
        )
        for selector in (".book-emphasis", "h1", "h2", "h3", "nav#toc a"):
            self.assertIn(selector, mobi_css)
        self.assertRegex(mobi_css, r"color:\s*inherit\s*!important")

    @mock.patch.object(build_book.subprocess, "run")
    def test_uses_current_syntax_highlighting_option(
        self,
        run: mock.Mock,
    ) -> None:
        run.return_value = mock.Mock(
            stdout="--syntax-highlighting=STYLE",
        )

        self.assertEqual(
            build_book.syntax_highlighting_argument(root=build_book.ROOT),
            "--syntax-highlighting=tango",
        )

    @mock.patch.object(build_book.subprocess, "run")
    def test_falls_back_for_older_pandoc(self, run: mock.Mock) -> None:
        run.return_value = mock.Mock(stdout="--highlight-style=STYLE")

        self.assertEqual(
            build_book.syntax_highlighting_argument(root=build_book.ROOT),
            "--highlight-style=tango",
        )

    @unittest.skipUnless(shutil.which("pandoc"), "Pandoc is required")
    def test_chinese_translation_data_eliminates_writer_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "book.epub"
            result = subprocess.run(
                [
                    shutil.which("pandoc") or "pandoc",
                    "--from=markdown",
                    "--to=epub3",
                    "--data-dir=scripts/pandoc-data",
                    "--metadata=lang=zh-CN",
                    "--metadata=toc-title=目录",
                    "--output",
                    output,
                ],
                cwd=build_book.ROOT,
                input="# 测试\n",
                check=True,
                text=True,
                encoding="utf-8",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

        self.assertNotIn("Could not load translations", result.stderr)
        self.assertNotIn("has no translation defined", result.stderr)

    @unittest.skipUnless(shutil.which("pandoc"), "Pandoc is required")
    def test_epub_filter_wraps_custom_colors_for_apple_books(self) -> None:
        result = subprocess.run(
            [
                shutil.which("pandoc") or "pandoc",
                "--from=markdown",
                "--to=html",
                "--syntax-highlighting=tango",
                "--lua-filter=scripts/epub.lua",
            ],
            cwd=build_book.ROOT,
            input=(
                "**Important rule.**\n\n"
                "```cpp\nint main() { return 0; }\n```\n"
            ),
            check=True,
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE,
        )

        self.assertIn(
            'class="ibooks-dark-theme-use-custom-text-color"',
            result.stdout,
        )
        self.assertIn('class="dt"', result.stdout)
        self.assertIn('class="cf"', result.stdout)
        self.assertIn(
            'class="book-emphasis ibooks-dark-theme-use-custom-text-color"',
            result.stdout,
        )
        self.assertRegex(
            result.stdout,
            r"<strong>Important\s+rule\.</strong>",
        )

    def test_epub_css_defines_dark_syntax_palette(self) -> None:
        css = (build_book.ROOT / "styles" / "epub.css").read_text(encoding="utf-8")

        self.assertIn("color-scheme: light dark", css)
        self.assertIn("@media (prefers-color-scheme: dark)", css)
        self.assertRegex(
            css,
            r"@media \(prefers-color-scheme: dark\)[\s\S]*?"
            r"body\s*\{[^}]*color:\s*#e6edf3;[^}]*"
            r"background:\s*#0d1117;",
        )
        self.assertRegex(
            css,
            r"@media \(prefers-color-scheme: dark\)[\s\S]*?"
            r"blockquote\s*\{[^}]*background:\s*#161b22;",
        )
        self.assertIn(".ibooks-dark-theme-use-custom-text-color", css)
        for token in ("al", "co", "dt", "kw", "op", "pp", "st", "va"):
            self.assertIn(f"code span.{token}", css)

    def test_epub_headings_and_toc_inherit_reader_color_outside_light_theme(
        self,
    ) -> None:
        css = (build_book.ROOT / "styles" / "epub.css").read_text(encoding="utf-8")
        heading_rule = re.search(r"h1,\s*h2,\s*h3\s*\{([^}]*)\}", css)
        toc_rule = re.search(r"nav#toc a\s*\{([^}]*)\}", css)

        self.assertIsNotNone(heading_rule)
        self.assertIsNotNone(toc_rule)
        self.assertIn("color: inherit", heading_rule.group(1) if heading_rule else "")
        self.assertIn("color: inherit", toc_rule.group(1) if toc_rule else "")
        self.assertRegex(
            css,
            r"@media \(prefers-color-scheme: light\)\s*\{"
            r"\s*h1,\s*h2,\s*h3,\s*nav#toc a,"
            r"[\s\S]*?color:\s*#173f46",
        )

    def test_epub_emphasis_is_readable_with_and_without_theme_queries(
        self,
    ) -> None:
        css = (build_book.ROOT / "styles" / "epub.css").read_text(encoding="utf-8")
        default_rule = re.search(
            r"span\.book-emphasis\.ibooks-dark-theme-use-custom-text-color"
            r"\s*\{.*?color:\s*inherit",
            css,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(default_rule)
        self.assertIn(
            "p > span.book-emphasis."
            "ibooks-dark-theme-use-custom-text-color:only-child",
            css,
        )

        colors = re.findall(
            r"span\.book-emphasis\.ibooks-dark-theme-use-custom-text-color"
            r"\s*\{.*?color:\s*(#[0-9a-fA-F]{6})",
            css,
            flags=re.DOTALL,
        )
        self.assertEqual(len(colors), 2)

        luminances = []
        for color in colors:
            channels = [
                int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)
            ]
            linear = [
                channel / 12.92
                if channel <= 0.04045
                else ((channel + 0.055) / 1.055) ** 2.4
                for channel in channels
            ]
            luminances.append(
                0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]
            )

        self.assertGreaterEqual(1.05 / (luminances[0] + 0.05), 4.5)
        self.assertGreaterEqual((luminances[1] + 0.05) / 0.05, 4.5)


if __name__ == "__main__":
    unittest.main()
