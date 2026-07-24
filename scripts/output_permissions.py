#!/usr/bin/env python3
"""Generated-output permission repair for WSL and DrvFS."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Iterable

from build_support import BuildError, is_wsl, log, run


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


def ensure_output_files_replaceable(
    paths: Iterable[Path],
    *,
    root: Path,
) -> None:
    existing = [path for path in paths if path.exists()]

    def blocked_files() -> list[Path]:
        blocked: list[Path] = []
        for path in existing:
            try:
                with path.open("r+b"):
                    pass
            except OSError:
                blocked.append(path)
        return blocked

    blocked = blocked_files()
    if blocked and is_wsl() and all(is_wsl_drvfs_path(path) for path in blocked):
        log(
            "normalizing DrvFS ownership for generated output files: "
            + ", ".join(str(path.relative_to(root)) for path in blocked)
        )
        for path in blocked:
            temporary = path.with_name(f".{path.name}.permission-fix-{os.getpid()}")
            try:
                shutil.copyfile(path, temporary)
                os.replace(temporary, path)
            except OSError:
                try:
                    temporary.unlink()
                except OSError:
                    pass
        blocked = blocked_files()

    if blocked and is_wsl() and all(is_wsl_drvfs_path(path) for path in blocked):
        sudo = shutil.which("sudo")
        chmod = shutil.which("chmod")
        if sudo and chmod:
            names = ", ".join(str(path.relative_to(root)) for path in blocked)
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
                    ],
                    cwd=root,
                )
            except subprocess.CalledProcessError:
                pass
            blocked = blocked_files()
    if blocked:
        names = ", ".join(str(path.relative_to(root)) for path in blocked)
        raise BuildError(
            f"Output file is open in Windows or remains non-writable: {names}\n"
            "Close Acrobat, Calibre, Explorer preview, or any other program "
            "using the file. If no program has it open, run "
            f"`sudo chmod a+rw {names}` in WSL and retry. Existing dist files "
            "were left untouched."
        )


def ensure_wsl_output_ownership(
    *,
    root: Path,
    build_dir: Path,
    dist_dir: Path,
) -> None:
    if not is_wsl() or not hasattr(os, "geteuid") or os.geteuid() == 0:
        return

    uid = os.geteuid()
    gid = os.getegid()
    mismatched: list[Path] = []

    for directory in (build_dir, dist_dir):
        if not directory.exists():
            continue
        if directory == dist_dir and is_wsl_drvfs_path(directory):
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
            mismatched.append(directory)

    if not mismatched:
        return

    sudo = shutil.which("sudo")
    chown = shutil.which("chown")
    chmod = shutil.which("chmod")
    rm = shutil.which("rm")
    relative_paths = ", ".join(str(path.relative_to(root)) for path in mismatched)
    if sudo is None or chown is None or chmod is None or rm is None:
        raise BuildError(
            "Generated directories are not owned by the current WSL user: "
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
        ],
        cwd=root,
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
            ],
            cwd=root,
        )
        still_read_only = [path for path in still_read_only if not is_writable(path)]

    if still_read_only:
        for path in still_read_only:
            resolved = path.resolve()
            if resolved.parent != root.resolve() or path not in (build_dir, dist_dir):
                raise BuildError(f"Refusing to reset unexpected path: {resolved}")
        reset_paths = ", ".join(
            str(path.relative_to(root)) for path in still_read_only
        )
        log(f"resetting non-writable generated directories: {reset_paths}")
        run(
            [sudo, rm, "-rf", "--", *(str(path) for path in still_read_only)],
            cwd=root,
        )
        for path in still_read_only:
            path.mkdir(parents=True, exist_ok=True)
