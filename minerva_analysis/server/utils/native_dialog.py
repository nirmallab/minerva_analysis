# Native OS file/folder picker for the "Browse..." buttons in the quick-view
# and import UIs. Runs out-of-process (rather than importing tkinter into the
# Flask process directly) for two reasons: Tk needs to run on the process's
# *main* thread on macOS, but a request handled by waitress lands on an
# arbitrary worker thread; and a subprocess crash/hang (e.g. no display on a
# headless box) can't take the server down with it.
#
# Only meant for the terminal/`python run.py` and Jupyter (`server_cli.py`)
# launch paths, where sys.executable is a real Python interpreter. It is not
# safe to use as-is from a frozen/packaged build, where sys.executable is the
# packaged exe itself.

import json
import subprocess
import sys

_DIALOG_SCRIPT = r"""
import json, sys
import tkinter as tk
from tkinter import filedialog

mode = sys.argv[1]
root = tk.Tk()
root.withdraw()
root.attributes("-topmost", True)
if mode == "directory":
    path = filedialog.askdirectory(parent=root)
else:
    path = filedialog.askopenfilename(
        parent=root,
        filetypes=[
            ("Image files", "*.tif *.tiff *.png *.jpg *.jpeg"),
            ("All files", "*.*"),
        ],
    )
root.destroy()
print(json.dumps({"path": path or None}))
"""


def browse_for_path(mode="file", timeout=300):
    """Opens a native file/folder picker and returns the chosen absolute
    path, or None if the user cancelled.

    Raises RuntimeError with a user-facing message if no picker could be
    shown at all (no display, tkinter not installed, timed out, ...) so
    callers can fall back to a manual path input.
    """
    try:
        result = subprocess.run(
            [sys.executable, "-c", _DIALOG_SCRIPT, mode],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("Timed out waiting for the file browser.")
    except OSError as exc:
        raise RuntimeError(str(exc))

    if result.returncode != 0:
        message = (result.stderr or "").strip().splitlines()
        raise RuntimeError(message[-1] if message else "Could not open the file browser.")

    try:
        return json.loads(result.stdout.strip().splitlines()[-1])["path"]
    except (ValueError, IndexError, KeyError):
        raise RuntimeError("Unexpected response from the file browser.")
