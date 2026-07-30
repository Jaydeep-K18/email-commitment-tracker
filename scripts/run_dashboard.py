"""Launch the Streamlit dashboard.

    python -m scripts.run_dashboard

A thin convenience wrapper so the dashboard starts the same way as the other
components. ``streamlit run dashboard/app.py`` does exactly the same thing.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP = PROJECT_ROOT / "dashboard" / "app.py"


def main() -> int:
    print("Email Commitment Tracker - dashboard")
    print("  http://127.0.0.1:8501   (this machine only)")
    print("  Press Ctrl+C to stop.\n")
    # Run streamlit through this interpreter so it uses the same virtualenv.
    # The loopback bind is also in .streamlit/config.toml; it is repeated here
    # so the dashboard is never network-exposed even if that file is missing.
    return subprocess.call(
        [
            sys.executable, "-m", "streamlit", "run", str(APP),
            "--server.address", "127.0.0.1",
            "--browser.gatherUsageStats", "false",
        ],
        cwd=str(PROJECT_ROOT),
    )


if __name__ == "__main__":
    raise SystemExit(main())
