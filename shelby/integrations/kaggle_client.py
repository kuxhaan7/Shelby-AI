"""Kaggle integration — search and download real-world datasets.

CRITICAL: never `import kaggle` at module scope (or anywhere eager). The
installed `kaggle` package (v2.x) calls sys.exit(1) at import time when no
credentials are configured, which would crash the entire Shelby process.
Always shell out to the `kaggle` console script via subprocess instead — a
failure there only fails that one call.

Auth: set KAGGLE_API_TOKEN (from https://www.kaggle.com/settings/api →
"Generate New Token"). Legacy KAGGLE_USERNAME + KAGGLE_KEY / ~/.kaggle/kaggle.json
is also honoured if present.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from ..paths import kaggle_dir

DOWNLOAD_DIR = kaggle_dir()

_SETUP_HINT = (
    "Kaggle API token not configured. Get one at "
    "https://www.kaggle.com/settings/api ('Generate New Token'), then set "
    "the KAGGLE_API_TOKEN environment variable and restart Shelby."
)


def has_credentials() -> bool:
    if os.getenv("KAGGLE_API_TOKEN"):
        return True
    if (Path.home() / ".kaggle" / "access_token").exists():
        return True
    if os.getenv("KAGGLE_USERNAME") and os.getenv("KAGGLE_KEY"):
        return True
    if (Path.home() / ".kaggle" / "kaggle.json").exists():
        return True
    return False


def _run(args: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["kaggle", *args],
        capture_output=True, text=True, timeout=timeout,
    )


def search(query: str, max_results: int = 8) -> dict:
    """Search Kaggle datasets by keyword. Returns {'results': [...]} or {'error': ...}."""
    if not has_credentials():
        return {"error": _SETUP_HINT}

    try:
        proc = _run(["datasets", "list", "-s", query, "--format", "json"], timeout=60)
    except FileNotFoundError:
        return {"error": "kaggle CLI not installed on this server."}
    except subprocess.TimeoutExpired:
        return {"error": "Kaggle search timed out."}

    if proc.returncode != 0:
        return {"error": f"Kaggle search failed: {proc.stderr.strip()[:400]}"}

    try:
        results = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"error": f"Could not parse Kaggle response: {proc.stdout[:300]}"}

    if not isinstance(results, list):
        return {"error": f"Unexpected Kaggle response shape: {str(results)[:300]}"}

    return {"results": results[:max_results]}


def download(dataset_ref: str, file_name: str | None = None) -> dict:
    """Download (and unzip) a Kaggle dataset by ref ('owner/dataset-name')."""
    if not has_credentials():
        return {"error": _SETUP_HINT}

    out_dir = DOWNLOAD_DIR / dataset_ref.replace("/", "__")
    out_dir.mkdir(parents=True, exist_ok=True)

    args = ["datasets", "download", "-d", dataset_ref, "-p", str(out_dir), "--unzip", "-q"]
    if file_name:
        args += ["-f", file_name]

    try:
        proc = _run(args, timeout=180)
    except FileNotFoundError:
        return {"error": "kaggle CLI not installed on this server."}
    except subprocess.TimeoutExpired:
        return {"error": "Kaggle download timed out — dataset may be too large for a live demo."}

    if proc.returncode != 0:
        return {"error": f"Kaggle download failed: {proc.stderr.strip()[:400]}"}

    files = sorted(str(p) for p in out_dir.rglob("*") if p.is_file())
    if not files:
        return {"error": f"Download reported success but no files were found in {out_dir}."}

    return {"dataset": dataset_ref, "path": str(out_dir), "files": files}
