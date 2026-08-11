"""Build the desktop application (Phase 8).

    python -m scripts.build_desktop

Wraps ``pyinstaller EmailCommitmentTracker.spec`` with the checks that turn the
two most common build failures into a sentence instead of a stack trace: a
missing build dependency, and a stale ``dist`` left behind by an earlier run.

The build takes several minutes — most of it spent analysing Streamlit — and
produces a single executable of roughly 150-250MB, because the bundle carries a
Python runtime, Streamlit's compiled frontend, and every library the pipeline
imports.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SPEC = PROJECT_ROOT / "EmailCommitmentTracker.spec"
DIST = PROJECT_ROOT / "dist"
BUILD = PROJECT_ROOT / "build"


def _check_dependencies() -> list[str]:
    missing = []
    for module, package in (
        ("PyInstaller", "pyinstaller"),
        ("pystray", "pystray"),
        ("PIL", "pillow"),
        ("streamlit", "streamlit"),
    ):
        try:
            __import__(module)
        except ImportError:
            missing.append(package)
    return missing


def main() -> int:
    missing = _check_dependencies()
    if missing:
        print("Missing build dependencies:", ", ".join(missing))
        print("Install them with:  pip install -r requirements.txt")
        return 1

    if not SPEC.exists():
        print(f"Spec file not found: {SPEC}")
        return 1

    # A stale dist is worse than no dist: the build would appear to succeed
    # while the executable left behind is the previous one.
    for path in (BUILD, DIST):
        if path.exists():
            print(f"Removing previous {path.name}/ …")
            shutil.rmtree(path, ignore_errors=True)

    print("Building — this takes a few minutes.\n")
    result = subprocess.call(
        [sys.executable, "-m", "PyInstaller", str(SPEC), "--noconfirm"],
        cwd=str(PROJECT_ROOT),
    )
    if result != 0:
        print("\nBuild failed.")
        return result

    built = list(DIST.glob("EmailCommitmentTracker*"))
    if not built:
        print("\nPyInstaller reported success but produced no executable.")
        return 1

    exe = built[0]
    print(f"\nBuilt {exe}  ({exe.stat().st_size / 1_048_576:.0f} MB)")
    print(
        "\nRun it by double-clicking. It starts in the system tray; the first "
        "run asks for a mailbox and an app password."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
