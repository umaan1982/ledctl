"""Windowless launcher for the tray app (run with the venv's pythonw.exe).
Errors go to %APPDATA%\\ledctl\\ledctl.log since there is no console."""

import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
log = Path(os.environ.get("APPDATA", Path.home())) / "ledctl" / "ledctl.log"
log.parent.mkdir(parents=True, exist_ok=True)
sys.stdout = sys.stderr = open(log, "a", buffering=1, encoding="utf-8")

try:
    from ledctl.cli import main
    sys.exit(main(["tray", *sys.argv[1:]]))
except SystemExit:
    raise
except BaseException:
    traceback.print_exc()
    raise
