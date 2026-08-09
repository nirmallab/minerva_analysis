import os
import sys


def setup_minerva_analysis():
    data_dir = os.environ.get("MINERVA_DATA_PATH", "")
    base_url_template = "{base_url}minerva-analysis"
    command = [
        sys.executable,
        "-m",
        "minerva_analysis.server_cli",
        "--host",
        "127.0.0.1",
        "--port",
        "{port}",
        "--base-url",
        base_url_template,
        "--notebook-mode",
    ]
    if data_dir:
        command.extend(["--data-dir", data_dir])

    # jupyter_server_proxy sets these as real OS env vars before the child
    # process starts (handlers.py: ensure_process's server_env.update(get_env())),
    # which is what actually reaches minerva_analysis/__init__.py's import-time
    # env snapshot -- the --base-url/--data-dir CLI flags above are consumed too
    # late relative to that import. Keep the two in sync if you change one.
    environment = {
        "MINERVA_BASE_URL": base_url_template,
        "MINERVA_NOTEBOOK_MODE": "1",
    }
    if data_dir:
        environment["MINERVA_DATA_PATH"] = data_dir

    return {
        "command": command,
        "environment": environment,
        "absolute_url": False,
        "new_browser_tab": False,
        "timeout": 30,
        "launcher_entry": {
            "enabled": True,
            "title": "Minerva Analysis",
            "path_info": "",
        },
    }
