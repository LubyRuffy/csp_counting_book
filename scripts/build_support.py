#!/usr/bin/env python3
"""Shared process and platform-toolchain helpers for the book builder."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Sequence


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

MACOS_TEX_PACKAGES = (
    "bookmark",
    "booktabs",
    "ctex",
    "enumitem",
    "eso-pic",
    "fancyhdr",
    "fancyvrb",
    "framed",
    "fvextra",
    "geometry",
    "hyperref",
    "microtype",
    "needspace",
    "upquote",
    "xcolor",
    "xurl",
    "zapfding",
)

REQUIRED_TEX_FILES = {
    "booktabs.sty": "booktabs",
    "ctexbook.cls": "ctex",
    "enumitem.sty": "enumitem",
    "eso-pic.sty": "eso-pic",
    "fancyhdr.sty": "fancyhdr",
    "fancyvrb.sty": "fancyvrb",
    "framed.sty": "framed",
    "fvextra.sty": "fvextra",
    "longtable.sty": "tools",
    "microtype.sty": "microtype",
    "needspace.sty": "needspace",
    "pzdr.tfm": "zapfding",
    "xcolor.sty": "xcolor",
}

MACOS_EXTRA_PATHS = (
    Path("/Library/TeX/texbin"),
    Path("/Applications/calibre.app/Contents/MacOS"),
)
PROJECT_VENV_DIRECTORY = Path(".build") / "python-build-venv"


class BuildError(RuntimeError):
    """A user-facing build failure."""


def log(message: str) -> None:
    print(f"[book] {message}", flush=True)


def printable_command(command: Sequence[str]) -> str:
    return shlex.join(str(part) for part in command)


def run(
    command: Sequence[str],
    *,
    cwd: Path,
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


def command_name(env_name: str, default: str) -> str:
    return os.environ.get(env_name, default)


def syntax_highlighting_argument(*, root: Path) -> str:
    pandoc = command_name("PANDOC", "pandoc")
    result = subprocess.run(
        [pandoc, "--help"],
        cwd=root,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    option = (
        "--syntax-highlighting"
        if "--syntax-highlighting" in result.stdout
        else "--highlight-style"
    )
    return f"{option}=tango"


def detect_development_version(*, root: Path) -> str:
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
            cwd=root,
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


def project_venv_python(*, root: Path) -> Path:
    return root / PROJECT_VENV_DIRECTORY / "bin" / "python"


def activate_project_venv(*, root: Path) -> None:
    site_packages = (
        root
        / PROJECT_VENV_DIRECTORY
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )
    site_packages_text = str(site_packages)
    if site_packages.is_dir() and site_packages_text not in sys.path:
        sys.path.insert(0, site_packages_text)
        importlib.invalidate_caches()


def missing_tools(
    formats: set[str],
    config: dict | None = None,
    *,
    root: Path,
) -> list[str]:
    log("checking Pandoc/XeLaTeX/Calibre and image dependencies...")
    activate_project_venv(root=root)
    missing = []
    for display_name, executable in required_tools(formats).items():
        if shutil.which(executable) is None:
            missing.append(display_name)

    if "pdf" in formats and "xelatex" not in missing:
        kpsewhich = shutil.which("kpsewhich")
        if kpsewhich is None:
            missing.append("kpsewhich")
        else:
            for filename, package in REQUIRED_TEX_FILES.items():
                result = subprocess.run(
                    [kpsewhich, filename],
                    cwd=root,
                    check=False,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )
                if result.returncode != 0 or not result.stdout.strip():
                    missing.append(
                        f"{filename} (TeX Live package {package})"
                    )
    if (
        formats.intersection({"pdf", "epub", "mobi"})
        and (config or {}).get("images", {}).get("optimize", True)
        and not pillow_available()
    ):
        missing.append("Pillow (python3-pil)")
    return missing


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


def docker_is_running(*, root: Path) -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            cwd=root,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return False
    return True


def install_wsl_toolchain(*, cwd: Path) -> None:
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
    run([*command_prefix, apt_get, "update"], cwd=cwd)
    run(
        [
            *command_prefix,
            apt_get,
            "install",
            "-y",
            "--no-install-recommends",
            *WSL_APT_PACKAGES,
        ],
        cwd=cwd,
    )


def refresh_macos_tool_path() -> None:
    current = os.environ.get("PATH", "").split(os.pathsep)
    additions = [str(path) for path in MACOS_EXTRA_PATHS if path.is_dir()]
    os.environ["PATH"] = os.pathsep.join(
        [*additions, *(entry for entry in current if entry not in additions)]
    )


def privileged_command(command: Sequence[str]) -> list[str]:
    if not hasattr(os, "geteuid") or os.geteuid() == 0:
        return [str(part) for part in command]
    sudo = shutil.which("sudo")
    if sudo is None:
        raise BuildError(
            "The macOS TeX setup needs administrator access, but sudo is unavailable."
        )
    return [sudo, *(str(part) for part in command)]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cache_calibre_from_github(*, brew: str, root: Path) -> None:
    info = run(
        [brew, "info", "--json=v2", "--cask", "calibre"],
        cwd=root,
        capture=True,
    )
    try:
        cask = json.loads(info.stdout)["casks"][0]
        version = str(cask["version"])
        expected_sha256 = str(cask["sha256"])
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise BuildError("Homebrew returned invalid Calibre cask metadata") from exc

    cache = run(
        [brew, "--cache", "--cask", "calibre"],
        cwd=root,
        capture=True,
    )
    cache_path = Path((cache.stdout or "").strip())
    if not cache_path.is_absolute():
        raise BuildError("Homebrew returned an invalid Calibre cache path")

    curl = shutil.which("curl")
    if curl is None:
        raise BuildError("Calibre download failed and curl is unavailable")
    temporary = cache_path.with_name(f".{cache_path.name}.github-download")
    url = (
        "https://github.com/kovidgoyal/calibre/releases/download/"
        f"v{version}/calibre-{version}.dmg"
    )
    log("Calibre primary download failed; retrying from its official GitHub release")
    try:
        run(
            [
                curl,
                "--fail",
                "--location",
                "--retry",
                "3",
                "--output",
                temporary,
                url,
            ],
            cwd=root,
        )
        actual_sha256 = file_sha256(temporary)
        if actual_sha256 != expected_sha256:
            raise BuildError(
                "The Calibre GitHub download did not match Homebrew's checksum"
            )
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temporary, cache_path)
    finally:
        temporary.unlink(missing_ok=True)


def install_macos_toolchain(
    formats: set[str],
    config: dict,
    missing: Sequence[str],
    *,
    root: Path,
) -> None:
    if platform.system() != "Darwin":
        raise BuildError("macOS dependency setup was requested on a non-macOS system")
    brew = shutil.which("brew")
    if brew is None:
        raise BuildError(
            "The macOS toolchain is incomplete and Homebrew is unavailable.\n"
            "Install Homebrew once from https://brew.sh, then rerun `make build`."
        )

    formulae: list[str] = []
    casks: list[str] = []
    if "pandoc" in missing:
        formulae.append("pandoc")
    if "pdf" in formats and shutil.which(command_name("PDFINFO", "pdfinfo")) is None:
        formulae.append("poppler")
    if any(name in missing for name in ("xelatex", "kpsewhich")):
        casks.append("basictex")
    if "ebook-convert" in missing:
        casks.append("calibre")

    log("installing the missing macOS build toolchain (sudo may ask for a password)")
    if formulae:
        run([brew, "install", *formulae], cwd=root)
    for cask in casks:
        try:
            run([brew, "install", "--cask", cask], cwd=root)
        except subprocess.CalledProcessError as exc:
            if cask != "calibre":
                raise BuildError(
                    f"Homebrew could not install the {cask} cask"
                ) from exc
            cache_calibre_from_github(brew=brew, root=root)
            run([brew, "install", "--cask", cask], cwd=root)
    refresh_macos_tool_path()

    needs_tex_packages = "pdf" in formats and (
        any(name in missing for name in ("xelatex", "kpsewhich"))
        or any("TeX Live package" in name for name in missing)
    )
    if needs_tex_packages:
        tlmgr = shutil.which("tlmgr")
        if tlmgr is None:
            raise BuildError(
                "BasicTeX was installed, but tlmgr is not available. Open a new "
                "terminal and rerun `make build`."
            )
        log("updating the installed TeX Live base before adding book packages")
        run(
            privileged_command([tlmgr, "update", "--self", "--all"]),
            cwd=root,
        )
        run(
            privileged_command([tlmgr, "install", *MACOS_TEX_PACKAGES]),
            cwd=root,
        )

    if (
        formats.intersection({"pdf", "epub", "mobi"})
        and config.get("images", {}).get("optimize", True)
        and any(name.startswith("Pillow") for name in missing)
    ):
        venv_python = project_venv_python(root=root)
        log("installing Pillow into the isolated project build environment")
        try:
            if not venv_python.is_file():
                run(
                    [
                        sys.executable,
                        "-m",
                        "venv",
                        root / PROJECT_VENV_DIRECTORY,
                    ],
                    cwd=root,
                )
            run(
                [
                    venv_python,
                    "-m",
                    "pip",
                    "install",
                    "--disable-pip-version-check",
                    "Pillow",
                ],
                cwd=root,
            )
        except subprocess.CalledProcessError as exc:
            raise BuildError(
                "Pillow could not be installed into the isolated project "
                f"environment at {root / PROJECT_VENV_DIRECTORY}."
            ) from exc
        activate_project_venv(root=root)

    refresh_macos_tool_path()
