#!/usr/bin/env python3
"""Build the book as PDF, EPUB3 and MOBI.

The script uses Pandoc for Markdown parsing and EPUB generation, XeLaTeX for
the Chinese print PDF, and Calibre for MOBI output. It can run against local
tools or dispatch the same build inside the repository's Docker image.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import html
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import textwrap
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

try:
    import tomllib
except ModuleNotFoundError:
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        system_python = "/usr/bin/python3"
        if (
            os.name == "posix"
            and os.path.isfile(system_python)
            and os.path.realpath(sys.executable) != system_python
        ):
            os.execv(system_python, [system_python, *sys.argv])
        raise SystemExit(
            "Python 3.11+ is required, or install the `tomli` package for "
            "older Python versions."
        )


ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "book.toml"
BUILD_DIR = ROOT / ".build"
DIST_DIR = ROOT / "dist"
DOCKERFILE = ROOT / "docker" / "Dockerfile"
DOCKER_IMAGE = "csp-counting-book-builder:1"
DOCKERFILE_HASH_LABEL = "org.csp-counting-book.dockerfile-sha256"
WSL_APT_PACKAGES = (
    "calibre",
    "fonts-dejavu-core",
    "fonts-noto-cjk",
    "lmodern",
    "pandoc",
    "poppler-utils",
    "python3-pil",
    "texlive-fonts-recommended",
    "texlive-lang-chinese",
    "texlive-latex-extra",
    "texlive-xetex",
)

MARKDOWN_FORMAT = (
    "markdown"
    "+raw_tex"
    "+tex_math_single_backslash"
    "+pipe_tables"
    "+fenced_code_blocks"
    "+fenced_code_attributes"
    "-implicit_figures"
)


class BuildError(RuntimeError):
    """A user-facing build failure."""


def log(message: str) -> None:
    print(f"[book] {message}", flush=True)


def printable_command(command: Sequence[str]) -> str:
    return shlex.join(str(part) for part in command)


def run(
    command: Sequence[str],
    *,
    cwd: Path = ROOT,
    env: dict[str, str] | None = None,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    command = [str(part) for part in command]
    log(f"+ {printable_command(command)}")
    if capture:
        return subprocess.run(
            command,
            cwd=cwd,
            env=env,
            check=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

    process = subprocess.Popen(command, cwd=cwd, env=env)
    started = time.monotonic()
    display_name = Path(command[0]).name
    while True:
        try:
            return_code = process.wait(timeout=10)
            break
        except subprocess.TimeoutExpired:
            elapsed = round(time.monotonic() - started)
            log(f"still running {display_name}... ({elapsed}s)")
    if return_code:
        raise subprocess.CalledProcessError(return_code, command)
    return subprocess.CompletedProcess(command, return_code)


def load_config() -> dict:
    if not CONFIG_PATH.is_file():
        raise BuildError(f"Missing configuration: {CONFIG_PATH}")
    with CONFIG_PATH.open("rb") as handle:
        config = tomllib.load(handle)
    if "book" not in config:
        raise BuildError("book.toml must contain a [book] table")
    return config


def resolve_chapters(config: dict) -> list[Path]:
    names = config["book"].get("chapters", [])
    if not names:
        raise BuildError("book.toml does not list any chapters")
    return [ROOT / name for name in names]


def output_paths(config: dict) -> dict[str, Path]:
    slug = config["book"].get("slug", "book")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", slug):
        raise BuildError("book.slug must use only ASCII letters, digits, dot, dash or underscore")
    return {
        "pdf": DIST_DIR / f"{slug}.pdf",
        "epub": DIST_DIR / f"{slug}.epub",
        "mobi": DIST_DIR / f"{slug}.mobi",
    }


def command_name(env_name: str, default: str) -> str:
    return os.environ.get(env_name, default)


def detect_development_version() -> str:
    git = shutil.which("git")
    if git:
        result = subprocess.run(
            [
                git,
                "describe",
                "--tags",
                "--match",
                "v[0-9]*",
                "--always",
                "--dirty",
            ],
            cwd=ROOT,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        description = result.stdout.strip()
        if description.startswith("v"):
            return description
        if description:
            dirty = ".dirty" if description.endswith("-dirty") else ""
            commit = description.removesuffix("-dirty")
            return f"v0.1.0-dev+g{commit}{dirty}"
    return "v0.1.0-dev"


def required_tools(formats: set[str]) -> dict[str, str]:
    tools = {"pandoc": command_name("PANDOC", "pandoc")}
    if "pdf" in formats:
        tools["xelatex"] = command_name("XELATEX", "xelatex")
    if "mobi" in formats:
        tools["ebook-convert"] = command_name("EBOOK_CONVERT", "ebook-convert")
    return tools


def pillow_available() -> bool:
    try:
        import PIL  # noqa: F401
    except ImportError:
        return False
    return True


def missing_tools(formats: set[str], config: dict | None = None) -> list[str]:
    log("checking Pandoc/XeLaTeX/Calibre and image dependencies...")
    missing = []
    for display_name, executable in required_tools(formats).items():
        if shutil.which(executable) is None:
            missing.append(display_name)

    if "pdf" in formats and "xelatex" not in missing:
        kpsewhich = shutil.which("kpsewhich")
        if kpsewhich is None:
            missing.append("kpsewhich")
        else:
            result = subprocess.run(
                [kpsewhich, "pzdr.tfm"],
                cwd=ROOT,
                check=False,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            if result.returncode != 0 or not result.stdout.strip():
                missing.append("pzdr.tfm (texlive-fonts-recommended)")
    if (
        formats.intersection({"pdf", "epub", "mobi"})
        and (config or {}).get("images", {}).get("optimize", True)
        and not pillow_available()
    ):
        missing.append("Pillow (python3-pil)")
    return missing


IMAGE_PATTERN = re.compile(r"!\[[^\]]*]\((?:<)?([^)\s>]+)(?:>)?(?:\s+[^)]*)?\)")


def validate_sources(config: dict) -> list[tuple[Path, int, Path]]:
    chapters = resolve_chapters(config)
    missing = [path for path in chapters if not path.is_file()]
    cover = ROOT / config["book"].get("cover", "")
    if not cover.is_file():
        missing.append(cover)
    if missing:
        joined = "\n".join(f"  - {path.relative_to(ROOT)}" for path in missing)
        raise BuildError(f"Missing source files:\n{joined}")

    image_refs: list[tuple[Path, int, Path]] = []
    unresolved: list[str] = []
    for chapter in chapters:
        for line_number, line in enumerate(
            chapter.read_text(encoding="utf-8").splitlines(), start=1
        ):
            for match in IMAGE_PATTERN.finditer(line):
                raw_path = match.group(1)
                if re.match(r"^[a-z]+://", raw_path, re.IGNORECASE):
                    continue
                image_path = (chapter.parent / raw_path).resolve()
                image_refs.append((chapter, line_number, image_path))
                if not image_path.is_file():
                    unresolved.append(
                        f"  - {chapter.name}:{line_number}: {raw_path}"
                    )
    if unresolved:
        raise BuildError("Unresolved Markdown images:\n" + "\n".join(unresolved))
    return image_refs


def installed_font_families() -> set[str]:
    fc_list = shutil.which("fc-list")
    if fc_list is None:
        return set()
    try:
        result = subprocess.run(
            [fc_list, ":", "family"],
            check=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return set()
    families: set[str] = set()
    for line in result.stdout.splitlines():
        for family in line.split(","):
            families.add(family.strip().casefold())
    return families


def resolve_pdf_fonts(config: dict) -> dict[str, str]:
    pdf = config.get("pdf", {})
    system = platform.system()
    candidates = {
        "Darwin": {
            "main_font": ["Songti SC", "STSong", "Noto Serif CJK SC"],
            "sans_font": ["PingFang SC", "Heiti SC", "Noto Sans CJK SC"],
            "mono_font": ["SFMono-Regular", "Menlo", "Noto Sans Mono CJK SC"],
            "code_font": ["SFMono-Regular", "Menlo", "DejaVu Sans Mono"],
        },
        "Windows": {
            "main_font": ["SimSun", "Microsoft YaHei", "Noto Serif CJK SC"],
            "sans_font": ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC"],
            "mono_font": ["Microsoft YaHei", "SimSun", "Noto Sans Mono CJK SC"],
            "code_font": ["Cascadia Mono", "Consolas", "DejaVu Sans Mono"],
        },
        "Linux": {
            "main_font": ["Noto Serif CJK SC", "Source Han Serif SC"],
            "sans_font": ["Noto Sans CJK SC", "Source Han Sans SC"],
            "mono_font": ["Noto Sans Mono CJK SC", "Noto Sans CJK SC"],
            "code_font": ["DejaVu Sans Mono", "Liberation Mono"],
        },
    }
    available = installed_font_families()
    selected: dict[str, str] = {}
    defaults = candidates.get(system, candidates["Linux"])
    for key, choices in defaults.items():
        configured = pdf.get(key, "auto")
        if configured != "auto":
            selected[key] = configured
            continue
        selected[key] = next(
            (name for name in choices if name.casefold() in available), choices[-1]
        )
    log(
        "PDF fonts: "
        + ", ".join(f"{key.removesuffix('_font')}={value}" for key, value in selected.items())
    )
    return selected


def prepare_optimized_inputs(config: dict) -> tuple[dict, list[Path]]:
    chapters = resolve_chapters(config)
    image_config = config.get("images", {})
    if not image_config.get("optimize", True):
        return config, chapters
    try:
        from PIL import Image
    except ImportError as exc:
        raise BuildError(
            "Image optimization requires Pillow. Install `python3-pil` on "
            "Ubuntu/WSL, or `python3 -m pip install Pillow` on macOS."
        ) from exc

    media_dir = BUILD_DIR / "media"
    chapter_dir = BUILD_DIR / "chapters"
    media_dir.mkdir(parents=True, exist_ok=True)
    chapter_dir.mkdir(parents=True, exist_ok=True)
    max_width = int(image_config.get("max_width", 1600))
    quality = int(image_config.get("jpeg_quality", 84))
    cover_quality = int(image_config.get("cover_quality", 88))
    optimized: dict[Path, Path] = {}
    processed_count = 0

    def optimize(source: Path, *, is_cover: bool = False) -> Path:
        nonlocal processed_count
        source = source.resolve()
        if source in optimized:
            return optimized[source]
        digest = hashlib.sha1(str(source.relative_to(ROOT)).encode()).hexdigest()[:8]
        target = media_dir / f"{source.stem}-{digest}.jpg"
        with Image.open(source) as image:
            image = image.convert("RGB")
            if image.width > max_width:
                height = round(image.height * max_width / image.width)
                image = image.resize((max_width, height), Image.Resampling.LANCZOS)
            image.save(
                target,
                "JPEG",
                quality=cover_quality if is_cover else quality,
                optimize=True,
                progressive=True,
            )
        optimized[source] = target
        processed_count += 1
        if processed_count == 1 or processed_count % 5 == 0:
            log(f"image optimization progress: {processed_count} processed")
        return target

    prepared_config = copy.deepcopy(config)
    cover_source = (ROOT / config["book"]["cover"]).resolve()
    cover_target = optimize(cover_source, is_cover=True)
    prepared_config["book"]["cover"] = cover_target.relative_to(ROOT).as_posix()

    prepared_chapters: list[Path] = []
    for chapter in chapters:
        text = chapter.read_text(encoding="utf-8")

        def replace_image(match: re.Match[str]) -> str:
            raw_path = match.group(1)
            if re.match(r"^[a-z]+://", raw_path, re.IGNORECASE):
                return match.group(0)
            source = (chapter.parent / raw_path).resolve()
            target = optimize(source)
            start = match.start(1) - match.start(0)
            whole = match.group(0)
            replacement = target.relative_to(ROOT).as_posix()
            return whole[:start] + replacement + whole[start + len(raw_path):]

        prepared = chapter_dir / chapter.name
        prepared.write_text(IMAGE_PATTERN.sub(replace_image, text), encoding="utf-8")
        prepared_chapters.append(prepared)

    original_bytes = sum(path.stat().st_size for path in optimized)
    optimized_bytes = sum(path.stat().st_size for path in optimized.values())
    log(
        f"optimized {len(optimized)} images: "
        f"{original_bytes / 1048576:.1f} MiB -> {optimized_bytes / 1048576:.1f} MiB"
    )
    return prepared_config, prepared_chapters


def latex_escape(value: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "{": r"\{",
        "}": r"\}",
        "#": r"\#",
        "$": r"\$",
        "%": r"\%",
        "&": r"\&",
        "_": r"\_",
        "^": r"\textasciicircum{}",
        "~": r"\textasciitilde{}",
    }
    return "".join(replacements.get(char, char) for char in value)


def write_pdf_front_matter(config: dict) -> Path:
    book = config["book"]
    cover = book["cover"].replace("\\", "/")
    title = latex_escape(book["title"])
    subtitle = latex_escape(book.get("subtitle", ""))
    author = latex_escape(book.get("author", ""))
    version = latex_escape(config.get("_build_version", ""))
    updated = latex_escape(config.get("_build_updated", ""))
    version_line = (
        rf"\vspace{{0.9em}}" "\n"
        rf"{{\normalsize 版本号：{version}\par}}" "\n"
        rf"\vspace{{0.35em}}" "\n"
        rf"{{\normalsize 最后更新：{updated}\par}}"
        if version
        else ""
    )
    title_meta = latex_escape(book["title"])
    author_meta = latex_escape(book.get("author", ""))
    front_path = BUILD_DIR / "pdf-front.md"
    content = rf"""
```{{=latex}}
\hypersetup{{pdftitle={{{title_meta}}},pdfauthor={{{author_meta}}}}}

\thispagestyle{{empty}}
\AddToShipoutPictureBG*{{%
  \AtPageLowerLeft{{%
    \makebox[\paperwidth][c]{{%
      \includegraphics[width=\paperwidth,height=\paperheight]{{{cover}}}%
    }}%
  }}%
}}
\null
\clearpage

\begingroup
\thispagestyle{{empty}}
\centering
\vspace*{{0.23\textheight}}
{{\Huge\bfseries {title}\par}}
\vspace{{1.2em}}
{{\Large {subtitle}\par}}
\vfill
{{\large {author}\par}}
{version_line}
\vspace*{{0.10\textheight}}
\clearpage
\endgroup

\frontmatter
\pagestyle{{plain}}
\tableofcontents
\clearpage
\makeatletter
\@mainmattertrue
\makeatother
\pagenumbering{{arabic}}
\pagestyle{{fancy}}
```
"""
    front_path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")
    return front_path


def metadata_arguments(config: dict, *, include_title: bool = True) -> list[str]:
    book = config["book"]
    version = config.get("_build_version", "")
    updated = config.get("_build_updated", "")
    subtitle = book.get("subtitle", "")
    pairs = [
        ("lang", book.get("language", "zh-CN")),
        ("author", book.get("author", "")),
        ("description", book.get("description", "")),
        ("rights", book.get("rights", "")),
        ("toc-title", "目录"),
    ]
    if include_title:
        pairs[0:0] = [
            ("title", book["title"]),
            ("subtitle", subtitle),
        ]
    if version:
        pairs.extend(
            [
                ("version", version),
                ("identifier", f"{book.get('slug', 'book')}-{version}"),
            ]
        )
    if updated:
        pairs.append(("date", updated))
    arguments: list[str] = []
    for key, value in pairs:
        if value:
            arguments.extend(["--metadata", f"{key}={value}"])
    return arguments


def base_pandoc_arguments(config: dict, *, include_title: bool) -> list[str]:
    return [
        command_name("PANDOC", "pandoc"),
        f"--from={MARKDOWN_FORMAT}",
        "--resource-path=.",
        "--top-level-division=chapter",
        "--highlight-style=tango",
        "--columns=100",
        *metadata_arguments(config, include_title=include_title),
    ]


def build_pdf(config: dict, chapters: list[Path], output: Path) -> None:
    pdf = config.get("pdf", {})
    fonts = resolve_pdf_fonts(config)
    front = write_pdf_front_matter(config)
    command = [
        *base_pandoc_arguments(config, include_title=False),
        "--metadata",
        "has-frontmatter=false",
        str(front.relative_to(ROOT)),
        *(str(path.relative_to(ROOT)) for path in chapters),
        "--pdf-engine",
        command_name("XELATEX", "xelatex"),
        "--include-in-header=styles/pdf-header.tex",
        "--lua-filter=scripts/pdf_layout.lua",
        "--variable",
        "documentclass=ctexbook",
        "--variable",
        "classoption=openany",
        "--variable",
        f"papersize={pdf.get('paper', 'a5')}",
        "--variable",
        f"fontsize={pdf.get('font_size', '11pt')}",
        "--variable",
        f"CJKmainfont={fonts['main_font']}",
        "--variable",
        f"CJKsansfont={fonts['sans_font']}",
        "--variable",
        f"CJKmonofont={fonts['mono_font']}",
        "--variable",
        f"monofont={fonts['code_font']}",
        "--variable",
        "colorlinks=true",
        "--variable",
        "linkcolor=RoyalBlue",
        "--variable",
        "urlcolor=RoyalBlue",
        "--variable",
        "geometry:inner=18mm",
        "--variable",
        "geometry:outer=15mm",
        "--variable",
        "geometry:top=18mm",
        "--variable",
        "geometry:bottom=18mm",
        "--variable",
        "geometry:headheight=15pt",
        "--output",
        output,
    ]
    run(command)


def build_epub(config: dict, chapters: list[Path], output: Path) -> None:
    book = config["book"]
    command = [
        *base_pandoc_arguments(config, include_title=True),
        *(str(path.relative_to(ROOT)) for path in chapters),
        "--to=epub3",
        "--toc",
        "--toc-depth=2",
        "--split-level=1",
        "--mathml",
        "--css=styles/epub.css",
        f"--epub-cover-image={book['cover']}",
        "--output",
        output,
    ]
    run(command)
    if config.get("_build_version"):
        stamp_epub_title_page(
            output,
            config["_build_version"],
            config.get("_build_updated", ""),
        )


def stamp_epub_title_page(path: Path, version: str, updated: str) -> None:
    temporary = path.with_suffix(".stamped.epub")
    version_text = html.escape(f"版本号：{version}")
    updated_text = html.escape(f"最后更新：{updated}")
    found = False
    with zipfile.ZipFile(path, "r") as source, zipfile.ZipFile(temporary, "w") as target:
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename.endswith("title_page.xhtml"):
                text = data.decode("utf-8")
                edition = f'  <p class="edition">{version_text}</p>\n'
                date = f'  <p class="date">{updated_text}</p>'
                if re.search(r'<p class="date">.*?</p>', text, flags=re.DOTALL):
                    text = re.sub(
                        r'(\s*)<p class="date">.*?</p>',
                        lambda match: f"{match.group(1)}{edition}{date}",
                        text,
                        count=1,
                        flags=re.DOTALL,
                    )
                else:
                    text = text.replace("</section>", f"{edition}{date}\n</section>", 1)
                data = text.encode("utf-8")
                found = True
            target.writestr(info, data)
    if not found:
        temporary.unlink(missing_ok=True)
        raise BuildError(f"EPUB textual title page was not found in {path}")
    os.replace(temporary, path)
    log(f"stamped EPUB title page with version {version} and date {updated}")


def build_mobi(epub: Path, output: Path) -> None:
    command = [
        command_name("EBOOK_CONVERT", "ebook-convert"),
        epub,
        output,
        "--mobi-file-type=both",
        "--pretty-print",
    ]
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    run(command, env=env)


def validate_epub(path: Path) -> None:
    if not path.is_file() or path.stat().st_size < 1024:
        raise BuildError(f"EPUB is missing or unexpectedly small: {path}")
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            required = {"mimetype", "META-INF/container.xml"}
            if not required.issubset(names):
                raise BuildError(f"EPUB is missing required container entries: {path}")
            if archive.read("mimetype") != b"application/epub+zip":
                raise BuildError(f"EPUB has an invalid mimetype entry: {path}")
            if not any(name.endswith((".xhtml", ".html")) for name in names):
                raise BuildError(f"EPUB contains no readable HTML content: {path}")
    except zipfile.BadZipFile as exc:
        raise BuildError(f"EPUB is not a valid ZIP container: {path}") from exc


def validate_pdf(path: Path) -> None:
    if not path.is_file() or path.stat().st_size < 1024:
        raise BuildError(f"PDF is missing or unexpectedly small: {path}")
    pdfinfo = shutil.which(command_name("PDFINFO", "pdfinfo"))
    if pdfinfo:
        try:
            result = run([pdfinfo, path], capture=True)
        except (OSError, subprocess.CalledProcessError):
            log("pdfinfo is unavailable; falling back to the PDF signature check")
        else:
            if not re.search(r"^Pages:\s+[1-9]\d*$", result.stdout or "", re.MULTILINE):
                raise BuildError(f"pdfinfo could not confirm a non-empty PDF: {path}")
            return
    with path.open("rb") as handle:
        if handle.read(5) != b"%PDF-":
            raise BuildError(f"File does not have a PDF header: {path}")


def validate_mobi(path: Path) -> None:
    if not path.is_file() or path.stat().st_size < 1024:
        raise BuildError(f"MOBI is missing or unexpectedly small: {path}")
    ebook_meta = shutil.which(command_name("EBOOK_META", "ebook-meta"))
    if ebook_meta:
        run([ebook_meta, path], capture=True)
    elif path.stat().st_size < 4096:
        raise BuildError(f"MOBI is too small to be a valid book: {path}")


def validate_outputs(config: dict, formats: Iterable[str]) -> None:
    paths = output_paths(config)
    validators = {
        "pdf": validate_pdf,
        "epub": validate_epub,
        "mobi": validate_mobi,
    }
    for format_name in formats:
        validators[format_name](paths[format_name])
        size_mb = paths[format_name].stat().st_size / (1024 * 1024)
        log(f"validated {paths[format_name].relative_to(ROOT)} ({size_mb:.1f} MiB)")


def is_wsl_drvfs_path(path: Path) -> bool:
    if not is_wsl():
        return False
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path.absolute()
    parts = resolved.parts
    return (
        len(parts) >= 4
        and parts[0] == "/"
        and parts[1] == "mnt"
        and len(parts[2]) == 1
        and parts[2].isalpha()
    )


def ensure_output_files_replaceable(paths: Iterable[Path]) -> None:
    existing = [path for path in paths if path.exists()]

    def blocked_files() -> list[Path]:
        blocked: list[Path] = []
        for path in existing:
            try:
                # Opening without truncation tests both the DrvFS mode and the
                # Windows sharing mode while preserving the existing artifact.
                with path.open("r+b"):
                    pass
            except OSError:
                blocked.append(path)
        return blocked

    blocked = blocked_files()
    if blocked and is_wsl() and all(is_wsl_drvfs_path(path) for path in blocked):
        log(
            "normalizing DrvFS ownership for generated output files: "
            + ", ".join(str(path.relative_to(ROOT)) for path in blocked)
        )
        for path in blocked:
            temporary = path.with_name(f".{path.name}.permission-fix-{os.getpid()}")
            try:
                # Docker-created DrvFS files can carry root:root/0644 metadata
                # even though the Windows owner has Modify permission. Creating
                # and replacing a sibling gives the file the invoking WSL
                # user's metadata without changing its bytes.
                shutil.copyfile(path, temporary)
                os.replace(temporary, path)
            except OSError:
                # A Windows sharing lock prevents the replace. Leave the
                # original intact and let the precise error below report it.
                try:
                    temporary.unlink()
                except OSError:
                    pass
        blocked = blocked_files()

    if blocked and is_wsl() and all(is_wsl_drvfs_path(path) for path in blocked):
        sudo = shutil.which("sudo")
        chmod = shutil.which("chmod")
        if sudo and chmod:
            names = ", ".join(str(path.relative_to(ROOT)) for path in blocked)
            log(f"trying cached sudo permission repair for output files: {names}")
            try:
                run(
                    [
                        sudo,
                        "--non-interactive",
                        chmod,
                        "a+rw",
                        "--",
                        *(str(path) for path in blocked),
                    ]
                )
            except subprocess.CalledProcessError:
                # Never wait for a sudo password in a non-interactive
                # PowerShell-to-WSL build.
                pass
            blocked = blocked_files()
    if blocked:
        names = ", ".join(str(path.relative_to(ROOT)) for path in blocked)
        raise BuildError(
            f"Output file is open in Windows or remains non-writable: {names}\n"
            "Close Acrobat, Calibre, Explorer preview, or any other program "
            "using the file. If no program has it open, run "
            f"`sudo chmod a+rw {names}` in WSL and retry. Existing dist files "
            "were left untouched."
        )


def ensure_wsl_output_ownership() -> None:
    if not is_wsl() or not hasattr(os, "geteuid") or os.geteuid() == 0:
        return

    uid = os.geteuid()
    gid = os.getegid()
    mismatched: list[Path] = []

    for directory in (BUILD_DIR, DIST_DIR):
        if not directory.exists():
            continue
        if directory == DIST_DIR and is_wsl_drvfs_path(directory):
            # DrvFS reports synthetic Unix owners. A single PDF held open by a
            # Windows viewer must not trigger recursive deletion of all output.
            if not os.access(directory, os.W_OK):
                mismatched.append(directory)
            continue
        try:
            if directory.stat().st_uid != uid:
                mismatched.append(directory)
                continue
            if any(entry.stat().st_uid != uid for entry in directory.rglob("*")):
                mismatched.append(directory)
        except OSError:
            # An unreadable generated directory also needs the same ownership repair.
            mismatched.append(directory)

    if not mismatched:
        return

    sudo = shutil.which("sudo")
    chown = shutil.which("chown")
    chmod = shutil.which("chmod")
    rm = shutil.which("rm")
    relative_paths = ", ".join(str(path.relative_to(ROOT)) for path in mismatched)
    if sudo is None or chown is None or chmod is None or rm is None:
        raise BuildError(
            f"Generated directories are not owned by the current WSL user: "
            f"{relative_paths}.\n"
            f"Run `sudo chown -R {uid}:{gid} {relative_paths}` and retry."
        )

    log(f"repairing WSL ownership for generated directories: {relative_paths}")
    run(
        [
            sudo,
            chown,
            "-R",
            "--",
            f"{uid}:{gid}",
            *(str(path) for path in mismatched),
        ]
    )

    def is_writable(directory: Path) -> bool:
        try:
            return os.access(directory, os.W_OK) and all(
                os.access(entry, os.W_OK) for entry in directory.rglob("*")
            )
        except OSError:
            return False

    still_read_only = [path for path in mismatched if not is_writable(path)]
    if still_read_only:
        log(
            "WSL chown did not update DrvFS permissions; "
            "granting write access to generated files"
        )
        run(
            [
                sudo,
                chmod,
                "-R",
                "a+rwX",
                "--",
                *(str(path) for path in still_read_only),
            ]
        )
        still_read_only = [path for path in still_read_only if not is_writable(path)]

    if still_read_only:
        # DrvFS can report successful chown/chmod operations while retaining the
        # Windows ACL. These directories contain generated files only, so reset
        # exactly those directories and recreate them as the invoking WSL user.
        for path in still_read_only:
            resolved = path.resolve()
            if resolved.parent != ROOT.resolve() or path not in (BUILD_DIR, DIST_DIR):
                raise BuildError(f"Refusing to reset unexpected path: {resolved}")
        reset_paths = ", ".join(
            str(path.relative_to(ROOT)) for path in still_read_only
        )
        log(f"resetting non-writable generated directories: {reset_paths}")
        run([sudo, rm, "-rf", "--", *(str(path) for path in still_read_only)])
        for path in still_read_only:
            path.mkdir(parents=True, exist_ok=True)


def local_build(config: dict, requested: set[str]) -> None:
    effective = set(requested)
    if "mobi" in effective:
        effective.add("epub")
    missing = missing_tools(effective, config)
    if missing:
        raise BuildError(
            "Missing local build tools: "
            + ", ".join(missing)
            + ". Install them or use --engine docker."
        )

    log("validating chapters and image references...")
    validate_sources(config)
    log("checking generated-directory ownership and write access...")
    ensure_wsl_output_ownership()
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    paths = output_paths(config)
    ensure_output_files_replaceable(paths[format_name] for format_name in effective)
    log("preparing optimized ebook images...")
    prepared_config, chapters = prepare_optimized_inputs(config)

    if "pdf" in effective:
        build_pdf(prepared_config, chapters, paths["pdf"])
    if "epub" in effective:
        build_epub(prepared_config, chapters, paths["epub"])
    if "mobi" in requested:
        build_mobi(paths["epub"], paths["mobi"])

    validate_outputs(config, requested)
    log("build complete")
    for format_name in ("pdf", "epub", "mobi"):
        if format_name in requested:
            log(f"  {paths[format_name].relative_to(ROOT)}")


def docker_is_running() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            cwd=ROOT,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return False
    return True


def is_wsl() -> bool:
    if os.name != "posix":
        return False
    if os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP"):
        return True
    try:
        return "microsoft" in Path("/proc/version").read_text(
            encoding="utf-8", errors="ignore"
        ).lower()
    except OSError:
        return False


def install_wsl_toolchain() -> None:
    apt_get = shutil.which("apt-get")
    if apt_get is None:
        raise BuildError(
            "The WSL toolchain is incomplete and apt-get is unavailable.\n"
            "Install Pandoc, XeLaTeX with Chinese support, and Calibre manually."
        )

    command_prefix: list[str] = []
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        sudo = shutil.which("sudo")
        if sudo is None:
            raise BuildError(
                "The WSL toolchain is incomplete and sudo is unavailable.\n"
                "Run as root or install Pandoc, XeLaTeX with Chinese support, "
                "and Calibre manually."
            )
        command_prefix.append(sudo)

    log("installing the missing WSL build toolchain (sudo may ask for a password)")
    run([*command_prefix, apt_get, "update"], cwd=Path.cwd())
    run(
        [
            *command_prefix,
            apt_get,
            "install",
            "-y",
            "--no-install-recommends",
            *WSL_APT_PACKAGES,
        ],
        cwd=Path.cwd(),
    )


def dockerfile_digest() -> str:
    return hashlib.sha256(DOCKERFILE.read_bytes()).hexdigest()


def docker_image_is_current() -> bool:
    result = subprocess.run(
        [
            "docker",
            "image",
            "inspect",
            "--format",
            f'{{{{index .Config.Labels "{DOCKERFILE_HASH_LABEL}"}}}}',
            DOCKER_IMAGE,
        ],
        cwd=ROOT,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0 and result.stdout.strip() == dockerfile_digest()


def docker_image_is_usable() -> bool:
    log(f"checking Docker image toolchain: {DOCKER_IMAGE}")
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "/bin/sh",
            DOCKER_IMAGE,
            "-lc",
            (
                "command -v pandoc >/dev/null"
                " && command -v xelatex >/dev/null"
                " && command -v ebook-convert >/dev/null"
                " && command -v pdfinfo >/dev/null"
                " && kpsewhich pzdr.tfm >/dev/null"
                " && python3 -c 'from PIL import Image'"
            ),
        ],
        cwd=ROOT,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def remove_docker_container(container_name: str) -> None:
    result = subprocess.run(
        ["docker", "rm", "--force", container_name],
        cwd=ROOT,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode == 0:
        log(f"removed build container: {container_name}")


def docker_build(
    requested: set[str],
    *,
    rebuild_image: bool = False,
    book_version: str | None = None,
    book_updated: str | None = None,
) -> None:
    log("checking Docker CLI and daemon...")
    if shutil.which("docker") is None:
        raise BuildError("Docker is not installed")
    if not docker_is_running():
        raise BuildError("Docker is installed but its daemon is not running")
    log("Docker daemon is available")
    if not DOCKERFILE.is_file():
        raise BuildError(f"Missing Dockerfile: {DOCKERFILE}")

    image_current = docker_image_is_current()
    image_healthy = image_current and docker_image_is_usable()
    if not rebuild_image and image_current and image_healthy:
        log(f"reusing cached and healthy Docker image: {DOCKER_IMAGE}")
    else:
        if rebuild_image:
            reason = "forced rebuild"
        elif not image_current:
            reason = "image missing or Dockerfile changed"
        else:
            reason = "image health check failed"
        log(f"building Docker image ({reason}): {DOCKER_IMAGE}")
        run(
            [
                "docker",
                "build",
                "--file",
                DOCKERFILE,
                "--label",
                f"{DOCKERFILE_HASH_LABEL}={dockerfile_digest()}",
                "--tag",
                DOCKER_IMAGE,
                ROOT,
            ]
        )
        if not docker_image_is_current() or not docker_image_is_usable():
            raise BuildError(
                f"Docker image was rebuilt but failed its toolchain health check: "
                f"{DOCKER_IMAGE}"
            )

    container_name = f"csp-counting-book-build-{os.getpid()}"
    # Remove only a same-name container left by an interrupted invocation of
    # this exact process id before starting a new one.
    remove_docker_container(container_name)
    command = [
        "docker",
        "run",
        "--rm",
        "--init",
        "--name",
        container_name,
    ]
    if os.name != "nt" and hasattr(os, "getuid") and hasattr(os, "getgid"):
        command.extend(["--user", f"{os.getuid()}:{os.getgid()}"])
    command.extend(
        [
            "--env",
            "QT_QPA_PLATFORM=offscreen",
            "--volume",
            f"{ROOT}:/book",
            "--workdir",
            "/book",
            DOCKER_IMAGE,
            "python3",
            "scripts/build_book.py",
            "build",
            "--engine",
            "local",
        ]
    )
    for format_name in sorted(requested):
        command.extend(["--format", format_name])
    if book_version:
        command.extend(["--book-version", book_version])
        if book_updated:
            command.extend(["--book-updated", book_updated])
    log(f"starting disposable build container: {container_name}")
    try:
        run(command)
    finally:
        # --rm handles the normal path. This covers CLI interruption, daemon
        # errors, and other abnormal exits that can otherwise leave a container.
        remove_docker_container(container_name)


def build(
    config: dict,
    engine: str,
    requested: set[str],
    *,
    rebuild_docker_image: bool = False,
) -> None:
    effective = set(requested)
    if "mobi" in effective:
        effective.add("epub")
    if engine == "local":
        log("using explicitly selected local toolchain")
        local_build(config, requested)
        return
    if engine == "docker":
        docker_build(
            requested,
            rebuild_image=rebuild_docker_image,
            book_version=config.get("_build_version"),
            book_updated=config.get("_build_updated"),
        )
        return

    if os.name == "nt":
        raise BuildError(
            "Native Windows builds do not select Docker automatically.\n"
            "Open WSL, enter this repository, and run `make build` there.\n"
            "To explicitly use Docker instead, run `make build-docker` or "
            "`python scripts/build_book.py build --engine docker`."
        )

    local_missing = missing_tools(effective, config)
    if local_missing and is_wsl():
        log(
            "WSL toolchain is incomplete ("
            + ", ".join(local_missing)
            + "); starting one-time dependency setup"
        )
        install_wsl_toolchain()
        local_missing = missing_tools(effective, config)

    if not local_missing:
        environment = "WSL" if is_wsl() else "local"
        log(f"using {environment} Pandoc/XeLaTeX/Calibre toolchain")
        local_build(config, requested)
    else:
        environment = "WSL" if is_wsl() else "local environment"
        raise BuildError(
            f"The {environment} toolchain is incomplete ("
            + ", ".join(local_missing)
            + ").\n"
            "Install Pandoc, XeLaTeX with Chinese support, and Calibre, then "
            "rerun `make build`.\n"
            "Docker is opt-in: use `make build-docker` or `--engine docker`."
        )


def safe_remove_directory(path: Path) -> None:
    resolved = path.resolve()
    if resolved.parent != ROOT.resolve():
        raise BuildError(f"Refusing to remove unexpected path: {resolved}")
    if resolved.is_dir():
        shutil.rmtree(resolved)
        log(f"removed {resolved.relative_to(ROOT)}")


def doctor(config: dict) -> None:
    image_refs = validate_sources(config)
    chapters = resolve_chapters(config)
    log(f"source chapters: {len(chapters)}")
    log(f"local image references: {len(image_refs)}")
    for display_name, executable in {
        "pandoc": command_name("PANDOC", "pandoc"),
        "xelatex": command_name("XELATEX", "xelatex"),
        "ebook-convert": command_name("EBOOK_CONVERT", "ebook-convert"),
        "pdfinfo": command_name("PDFINFO", "pdfinfo"),
        "docker": "docker",
    }.items():
        location = shutil.which(executable)
        log(f"{display_name}: {location or 'missing'}")
    log(f"docker daemon: {'running' if docker_is_running() else 'not available'}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build", help="build one or more formats")
    build_parser.add_argument(
        "--engine",
        choices=("auto", "local", "docker"),
        default="auto",
        help="toolchain selection (default: auto)",
    )
    build_parser.add_argument(
        "--format",
        dest="formats",
        action="append",
        choices=("pdf", "epub", "mobi"),
        help="format to build; repeat as needed (default: all)",
    )
    build_parser.add_argument(
        "--rebuild-docker-image",
        action="store_true",
        help="force rebuilding the Docker image even when its Dockerfile is unchanged",
    )
    build_parser.add_argument(
        "--book-version",
        help="version embedded in title pages (default: derived from Git)",
    )
    build_parser.add_argument(
        "--book-updated",
        help="last-updated date shown on title pages (default: local current date)",
    )

    subparsers.add_parser("verify", help="validate existing dist files")
    subparsers.add_parser("doctor", help="show source and dependency diagnostics")
    subparsers.add_parser("clean", help="remove generated build directories")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "build":
        requested_formats = ", ".join(args.formats or ("pdf", "epub", "mobi"))
        log(f"starting build (engine={args.engine}, formats={requested_formats})")
    config = load_config()
    if args.command == "build":
        config["_build_version"] = args.book_version or detect_development_version()
        config["_build_updated"] = (
            args.book_updated or datetime.now().astimezone().date().isoformat()
        )
        log(f"embedding book version: {config['_build_version']}")
        log(f"embedding last-updated date: {config['_build_updated']}")
        requested = set(args.formats or ("pdf", "epub", "mobi"))
        build(
            config,
            args.engine,
            requested,
            rebuild_docker_image=args.rebuild_docker_image,
        )
    elif args.command == "verify":
        validate_sources(config)
        validate_outputs(config, {"pdf", "epub", "mobi"})
    elif args.command == "doctor":
        doctor(config)
    elif args.command == "clean":
        safe_remove_directory(BUILD_DIR)
        safe_remove_directory(DIST_DIR)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(f"[book] ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
    except subprocess.CalledProcessError as exc:
        print(
            f"[book] ERROR: command failed with exit code {exc.returncode}",
            file=sys.stderr,
        )
        raise SystemExit(exc.returncode)
