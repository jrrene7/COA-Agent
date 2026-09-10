import logging
import re
from pathlib import Path
from typing import Dict

from lib.logging_config import redact_text
from lib.tooling import tool

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).parent.parent / "output"

_MAX_FILENAME_LENGTH = 100
_SAFE_FILENAME_RE = re.compile(r"[^a-zA-Z0-9_\-.]")


def _sanitize_filename(name: str) -> str:
    """Return a filesystem-safe filename."""
    name = _SAFE_FILENAME_RE.sub("_", name)
    name = name[:_MAX_FILENAME_LENGTH]
    if not name.endswith(".md"):
        name += ".md"
    return name


@tool
def write_report(filename: str, content: str) -> Dict:
    """Write a Markdown report to the output directory.

    args:
        filename (str): file name, e.g. 'acme_corp_lead.md'
        content (str): full Markdown content to write
    """
    safe_name = _sanitize_filename(filename)

    # Prevent path traversal
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = (OUTPUT_DIR / safe_name).resolve()
    if not str(path).startswith(str(OUTPUT_DIR.resolve())):
        return {"path": "", "bytes": 0, "status": "error: path traversal detected"}

    try:
        path.write_text(content, encoding="utf-8")
        byte_count = len(content.encode("utf-8"))
        logger.info("Report written: %s (%d bytes)", path, byte_count)
        return {"path": str(path), "bytes": byte_count, "status": "written"}
    except OSError as exc:
        message = redact_text(str(exc))
        logger.error("write_report failed: %s", message)
        return {"path": "", "bytes": 0, "status": f"error: {message}"}
