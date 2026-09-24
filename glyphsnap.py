"""GlyphSnap desktop application entry point with frozen-build diagnostics."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import sys
import traceback


def _report_startup_failure() -> None:
    details = traceback.format_exc()
    root = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "GlyphSnap"
    try:
        root.mkdir(parents=True, exist_ok=True)
        log_path = root / "glyphsnap-crash.log"
        log_path.write_text(details, encoding="utf-8")
        message = f"GlyphSnap could not start.\n\nDiagnostic log:\n{log_path}"
    except OSError:
        message = "GlyphSnap could not start.\n\n" + details[-1200:]
    if sys.platform == "win32":
        try:
            ctypes.windll.user32.MessageBoxW(None, message, "GlyphSnap startup error", 0x10)
        except (AttributeError, OSError):
            pass
    else:
        print(message, file=sys.stderr)


def run() -> None:
    try:
        from glyphsnap_app import main

        main()
    except SystemExit:
        raise
    except BaseException:
        _report_startup_failure()
        raise


if __name__ == "__main__":
    run()
