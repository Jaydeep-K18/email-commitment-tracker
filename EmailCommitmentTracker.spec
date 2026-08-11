# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Email Commitment Tracker (Phase 8).

Build with::

    pyinstaller EmailCommitmentTracker.spec --noconfirm

Streamlit is the awkward dependency here, for three reasons:

* it serves a compiled frontend from inside its own package, so that whole
  static tree has to be carried along as data;
* it reads its version through ``importlib.metadata``, which needs the
  ``.dist-info`` directory, not just the importable module; and
* ``st.navigation`` loads each page by *path* at runtime, so the dashboard's
  .py files must be bundled as data files. Bundled only as imported modules
  they would be compiled into the archive and unreadable as source.
"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

PROJECT = Path(SPECPATH)

# --- Data ------------------------------------------------------------------

datas = []

# Streamlit's compiled frontend, plus the packages it introspects at runtime.
datas += collect_data_files("streamlit", include_py_files=True)
# pyvis renders the network graph from Jinja templates shipped in its package.
datas += collect_data_files("pyvis")
datas += collect_data_files("apscheduler")

for dist in (
    "streamlit",       # version check on startup
    "pyvis",
    "networkx",
    "apscheduler",     # entry points for triggers/executors
    "keyring",         # entry points for the OS backends
    "altair",          # streamlit imports it eagerly and checks its version
):
    try:
        datas += copy_metadata(dist)
    except Exception:  # noqa: BLE001 - an absent optional dist is not fatal
        pass

# Our own source, as data. The dashboard package is executed by path by
# Streamlit, and config.BASE_DIR resolves to the unpack directory at runtime.
for source in ("dashboard", "src"):
    for path in (PROJECT / source).rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        datas.append((str(path), str(path.parent.relative_to(PROJECT))))

# Pinned Streamlit settings. desktop/main.py passes the same values as explicit
# flags, because this file is only found when it sits in the working directory.
config_toml = PROJECT / ".streamlit" / "config.toml"
if config_toml.exists():
    datas.append((str(config_toml), ".streamlit"))

# --- Imports PyInstaller cannot see ----------------------------------------

hiddenimports = [
    # keyring picks its backend by entry point, so nothing references these.
    "keyring.backends.Windows",
    "keyring.backends.macOS",
    "keyring.backends.SecretService",
    "keyring.backends.chainer",
    "keyring.backends.fail",
    # uvicorn resolves its loop and protocol implementations by name.
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    # APScheduler loads triggers and executors through its own registry.
    "apscheduler.triggers.interval",
    "apscheduler.executors.pool",
    "apscheduler.jobstores.memory",
    "apscheduler.schedulers.background",
    # pystray selects a platform backend at import time.
    "pystray._win32",
    "PIL._tkinter_finder",
    # Imported by name from the dashboard's page loader.
    "dashboard.styles",
    "dashboard.notifications",
    "src.first_run",
]

a = Analysis(
    ["desktop/main.py"],
    pathex=[str(PROJECT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # Trimming what Streamlit pulls in transitively but never uses here.
    excludes=["tkinter", "matplotlib", "pytest", "PyInstaller"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="EmailCommitmentTracker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    # A tray application: no console window. Startup problems are written to
    # the log file rather than a terminal nobody would see.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
