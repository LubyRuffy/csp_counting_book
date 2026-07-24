#!/usr/bin/env python3
"""Build and publish the book files as a GitHub Release."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import build_book


ROOT = Path(__file__).resolve().parent.parent
SEMVER = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
COMMIT_THRESHOLD = 10
HOUR_THRESHOLD = 24


class ReleaseError(RuntimeError):
    """A user-facing release failure."""


def log(message: str) -> None:
    print(f"[release] {message}", flush=True)


def run(
    command: Sequence[str],
    *,
    capture: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    command = [str(part) for part in command]
    log("+ " + subprocess.list2cmdline(command))
    return subprocess.run(
        command,
        cwd=ROOT,
        check=check,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
    )


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


def ensure_gh() -> str:
    gh = shutil.which("gh")
    if gh:
        return gh
    if not is_wsl():
        raise ReleaseError(
            "GitHub CLI (`gh`) is required. Install it and run `gh auth login`."
        )

    apt_get = shutil.which("apt-get")
    sudo = shutil.which("sudo")
    if not apt_get or (os.geteuid() != 0 and not sudo):
        raise ReleaseError(
            "GitHub CLI (`gh`) is missing and cannot be installed automatically."
        )
    prefix = [] if os.geteuid() == 0 else [sudo, "--non-interactive"]
    log("installing GitHub CLI")
    try:
        run([*prefix, apt_get, "update"])
        run([*prefix, apt_get, "install", "-y", "gh"])
    except subprocess.CalledProcessError as exc:
        raise ReleaseError(
            "GitHub CLI is missing and sudo needs an interactive password.\n"
            "Run `wsl sudo apt-get update && wsl sudo apt-get install -y gh` "
            "once, then rerun the release."
        ) from exc
    gh = shutil.which("gh")
    if not gh:
        raise ReleaseError("GitHub CLI installation completed but `gh` is unavailable.")
    return gh


def parse_version(version: str) -> tuple[int, int, int]:
    match = SEMVER.fullmatch(version)
    if not match:
        raise ReleaseError(
            f"Invalid version `{version}`; expected a value such as v0.2.0."
        )
    return tuple(int(part) for part in match.groups())


def next_patch(version: str) -> str:
    major, minor, patch = parse_version(version)
    return f"v{major}.{minor}.{patch + 1}"


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def require_clean_worktree() -> None:
    result = run(["git", "status", "--porcelain"], capture=True)
    if result.stdout.strip():
        raise ReleaseError(
            "The Git worktree is not clean. Commit or stash source changes before "
            "creating a release."
        )


def require_pushed_head() -> None:
    run(["git", "fetch", "origin", "--tags"])
    result = run(
        ["git", "branch", "--remotes", "--contains", "HEAD"],
        capture=True,
    )
    if not any(
        line.strip().startswith("origin/") for line in result.stdout.splitlines()
    ):
        raise ReleaseError(
            "The current commit is not present on origin. Push it before releasing."
        )


def latest_release(gh: str) -> dict | None:
    result = run(
        [
            gh,
            "release",
            "list",
            "--limit",
            "1",
            "--exclude-drafts",
            "--json",
            "tagName,publishedAt",
        ],
        capture=True,
    )
    releases = json.loads(result.stdout or "[]")
    return releases[0] if releases else None


def commit_count_since(tag: str | None) -> int:
    revision = "HEAD" if tag is None else f"{tag}..HEAD"
    result = run(["git", "rev-list", "--count", revision], capture=True)
    return int(result.stdout.strip())


def choose_version(
    requested: str | None,
    latest: dict | None,
    *,
    now: datetime | None = None,
) -> tuple[str | None, str]:
    if requested:
        parse_version(requested)
        return requested, "explicit VERSION"

    if latest is None:
        return "v0.1.0", "first release"

    tag = latest["tagName"]
    parse_version(tag)
    commits = commit_count_since(tag)
    published = parse_timestamp(latest["publishedAt"])
    elapsed_hours = ((now or datetime.now(timezone.utc)) - published).total_seconds() / 3600
    reason = f"{commits} commits since {tag}, {elapsed_hours:.1f} hours elapsed"
    if commits > COMMIT_THRESHOLD or elapsed_hours > HOUR_THRESHOLD:
        return next_patch(tag), reason
    return None, reason


def ensure_version_available(gh: str, version: str) -> None:
    tag = run(
        ["git", "rev-parse", "--verify", "--quiet", f"refs/tags/{version}"],
        capture=True,
        check=False,
    )
    if tag.returncode == 0:
        raise ReleaseError(f"Git tag `{version}` already exists.")
    release = run(
        [gh, "release", "view", version],
        capture=True,
        check=False,
    )
    if release.returncode == 0:
        raise ReleaseError(f"GitHub Release `{version}` already exists.")


def build_assets(version: str, updated: str) -> list[Path]:
    run(
        [
            sys.executable,
            "scripts/build_book.py",
            "build",
            "--engine",
            "auto",
            "--book-version",
            version,
            "--book-updated",
            updated,
        ]
    )
    config = build_book.load_config()
    paths = build_book.output_paths(config)
    assets = [paths[name] for name in ("pdf", "epub", "mobi")]
    missing = [path for path in assets if not path.is_file()]
    if missing:
        raise ReleaseError(
            "Build completed without all release assets: "
            + ", ".join(str(path.relative_to(ROOT)) for path in missing)
        )
    build_book.validate_outputs(config, {"pdf", "epub", "mobi"})
    return assets


def publish(gh: str, version: str, assets: list[Path]) -> str:
    head = run(["git", "rev-parse", "HEAD"], capture=True).stdout.strip()
    run(
        [
            gh,
            "release",
            "create",
            version,
            *(str(path) for path in assets),
            "--title",
            version,
            "--generate-notes",
            "--target",
            head,
        ]
    )
    result = run(
        [gh, "release", "view", version, "--json", "url", "--jq", ".url"],
        capture=True,
    )
    return result.stdout.strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", help="explicit release version, for example v0.2.0")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if shutil.which("git") is None:
        raise ReleaseError("Git is required to create a release.")
    require_clean_worktree()
    gh = ensure_gh()
    auth = run([gh, "auth", "status"], capture=True, check=False)
    if auth.returncode != 0:
        raise ReleaseError("GitHub CLI is not authenticated. Run `gh auth login` first.")

    require_pushed_head()
    latest = latest_release(gh)
    version, reason = choose_version(args.version, latest)
    if version is None:
        log(f"release skipped: {reason}; thresholds are >10 commits or >24 hours")
        return 0

    log(f"preparing {version}: {reason}")
    ensure_version_available(gh, version)
    updated = datetime.now().astimezone().date().isoformat()
    log(f"ebook title-page date: {updated}")
    assets = build_assets(version, updated)
    url = publish(gh, version, assets)
    log(f"published {version}: {url}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ReleaseError as exc:
        print(f"[release] ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
    except (subprocess.CalledProcessError, json.JSONDecodeError, ValueError) as exc:
        print(f"[release] ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
