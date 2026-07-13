"""Convert a .docx to a .pdf using LibreOffice in headless mode.

LibreOffice is preferred over MS Word automation because:
  - it's free and cross-platform
  - it has a stable CLI (`soffice --headless --convert-to pdf`)

This module wraps the `soffice` CLI with:
  - automatic detection of the LibreOffice binary on macOS (in /Applications)
  - a per-user lock file so concurrent conversions don't step on each other
    (soffice can fail if two headless instances share a profile dir)
  - sane timeout handling
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

from src.config import settings
from src.utils.logging import get_logger

log = get_logger(__name__)


MACOS_SOFFICE_PATHS = (
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    "/usr/local/bin/soffice",
    "/opt/homebrew/bin/soffice",
)


@dataclass
class ConversionResult:
    pdf_path: Optional[Path]
    success: bool
    elapsed_seconds: float
    error: str = ""


def find_soffice(executable: Optional[str] = None) -> Optional[str]:
    """Return the path to the soffice binary, or None if not found."""
    if executable and executable != "soffice":
        return executable if Path(executable).exists() else None
    # First, look on PATH (e.g. brew symlink)
    found = shutil.which("soffice")
    if found:
        return found
    # Fallback to macOS known install locations
    for p in MACOS_SOFFICE_PATHS:
        if Path(p).exists():
            return p
    return None


@contextmanager
def _profile_lock(profile_dir: Path, timeout_seconds: int = 30) -> Iterator[Path]:
    """Ensure only one soffice headless instance uses a given profile dir at a time."""
    profile_dir.mkdir(parents=True, exist_ok=True)
    lock_file = profile_dir / ".lock"
    waited = 0.0
    while lock_file.exists():
        if waited >= timeout_seconds:
            log.warning("soffice lock waited %ds; proceeding and replacing", waited)
            break
        time.sleep(0.5)
        waited += 0.5
    try:
        lock_file.write_text(str(os.getpid()), encoding="utf-8")
        yield profile_dir
    finally:
        try:
            lock_file.unlink(missing_ok=True)  # type: ignore[arg-type]
        except Exception:
            pass


def convert_docx_to_pdf(
    docx_path: Path,
    output_dir: Optional[Path] = None,
    executable: Optional[str] = None,
    timeout_seconds: int = 120,
) -> ConversionResult:
    """Convert a .docx file to a .pdf with the same stem in `output_dir`.

    If output_dir is None, the PDF is written next to the .docx.
    """
    docx_path = Path(docx_path).resolve()
    if not docx_path.exists():
        return ConversionResult(pdf_path=None, success=False, error="input docx not found",
                                elapsed_seconds=0.0)
    output_dir = (output_dir or docx_path.parent).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    soffice = find_soffice(executable or settings.soffice_path)
    if not soffice:
        return ConversionResult(
            pdf_path=None,
            success=False,
            error="LibreOffice (`soffice`) not found. Install it or set SOFFICE_PATH in .env.",
            elapsed_seconds=0.0,
        )

    # Use a per-user profile dir to avoid conflicts with any running LibreOffice GUI.
    profile_dir = Path(tempfile.gettempdir()) / "auto-tailored-cv-soffice-profile"

    start = time.time()
    with _profile_lock(profile_dir):
        cmd = [
            soffice,
            "--headless",
            "--norestore",
            "--nodefault",
            "--nologo",
            "--nofirststartwizard",
            f"-env:UserInstallation=file://{profile_dir}",
            "--convert-to", "pdf",
            "--outdir", str(output_dir),
            str(docx_path),
        ]
        try:
            log.info("soffice: %s", " ".join(cmd[:1]) + " " + " ".join(cmd[1:]))
            proc = subprocess.run(
                cmd,
                timeout=timeout_seconds,
                capture_output=True,
                text=True,
            )
        except subprocess.TimeoutExpired:
            elapsed = time.time() - start
            log.error("soffice timed out after %ds", elapsed)
            return ConversionResult(pdf_path=None, success=False, error="timeout", elapsed_seconds=elapsed)
        except FileNotFoundError as e:
            return ConversionResult(pdf_path=None, success=False, error=str(e), elapsed_seconds=time.time() - start)

    elapsed = time.time() - start
    expected_pdf = output_dir / (docx_path.stem + ".pdf")
    if proc.returncode != 0 or not expected_pdf.exists():
        stderr = (proc.stderr or "").strip()
        stdout = (proc.stdout or "").strip()
        msg = f"soffice exit {proc.returncode}. stderr={stderr[:200]} stdout={stdout[:200]}"
        log.error(msg)
        return ConversionResult(pdf_path=None, success=False, error=msg, elapsed_seconds=elapsed)
    log.info("pdf written: %s in %.2fs", expected_pdf, elapsed)
    return ConversionResult(pdf_path=expected_pdf, success=True, elapsed_seconds=elapsed)


__all__ = ["ConversionResult", "convert_docx_to_pdf", "find_soffice"]