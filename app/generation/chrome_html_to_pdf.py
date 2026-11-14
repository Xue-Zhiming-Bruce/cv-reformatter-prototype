"""Headless-Chrome adapter: the one product HTML-to-PDF export contract.

Native generation, the Agentic/pipeline lane and the offline job worker all
render through :func:`export_html_to_pdf`, so they share one page/background/
header-footer/locale/font-readiness/timeout/cleanup contract instead of
divergent implicit defaults. Page size and margins come only from the
renderer's ``@page`` CSS (the renderer is the single source of page truth): no
paper size, margin or scale flag is passed, header/footer printing is off, and
backgrounds/colors print because the HTML declares ``print-color-adjust:
exact``.

``PROFILE`` names and versions that shared contract. It is deliberately one
profile; a lane that must behave differently gets a named profile here rather
than an implicit default. The virtual-time budget is the font/image readiness
rule: it blocks the print until pending work (including asynchronously decoded
data-URI web fonts) settles. Without it, date-column widths varied up to ~3.7pt
between renders of the same artifact (owner ruling 2026-09-10, E→F thirteenth
freeze).

:func:`chrome_environment` records the export identity: profile, browser
version, flags, locale/timezone, bundled font assets and installed fallback-font
package versions. The agentic renderer identity hashes
:func:`chrome_export_identity` (the identity fields only), so a browser or font
change invalidates incompatible proof approval through the existing identity
check. The executable path and how it was resolved are provenance only: the same
build renders the same PDF whether it was found through CHROME_BIN or PATH.

The version probe is cached per executable file state (path, mtime, size,
inode), so a browser replaced or upgraded in place is re-probed instead of
keeping the previous version for the rest of the process.
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, NamedTuple


class HtmlToPdfExportError(RuntimeError):
    pass


CONTRACT_VERSION = "chrome-export/1"
FONT_ASSET_DIR = Path(__file__).resolve().parents[1] / "pipeline" / "assets" / "fonts"

_CHROME_CANDIDATES = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "chrome",
    "chrome-headless-shell",
)
_MACOS_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
_FALLBACK_FONT_PACKAGES = ("fonts-dejavu-core", "fonts-liberation")
_MISSING_BROWSER = "No Chrome/Chromium binary found for HTML-to-PDF export. Install Chrome/Chromium or set CHROME_BIN."
#: Fields that change rendered output, i.e. that must invalidate a proof.
#: ``executable``, ``resolution`` and ``timeout_seconds`` are provenance only:
#: the same browser build renders identically whether it was found through
#: CHROME_BIN or PATH, so the path and how it was discovered are recorded but
#: are not identity.
_IDENTITY_FIELDS = (
    "contract", "profile", "page_contract", "background_contract",
    "font_readiness", "version", "flags", "locale", "timezone",
    "fonts", "fallback_fonts",
)


class ChromeBinary(NamedTuple):
    path: str
    #: ``override`` (CHROME_BIN/CHROME_EXECUTABLE), ``macos_default`` (desktop
    #: app) or ``system`` (first candidate on PATH).
    resolution: str


@dataclass(frozen=True)
class ChromeExportProfile:
    """One named, versioned export profile; everything here is shared by every
    Chrome caller unless a separate profile says otherwise."""

    name: str
    page_contract: str
    background_contract: str
    flags: tuple[str, ...]
    font_readiness: str
    virtual_time_budget_ms: int
    locale: str
    timezone: str


PROFILE = ChromeExportProfile(
    name="product-html-chrome/1",
    page_contract="css-@page/1",
    background_contract="css-print-color-adjust-exact/1",
    flags=(
        "--headless=new",
        "--disable-gpu",
        "--disable-extensions",
        "--disable-background-networking",
        "--no-pdf-header-footer",
        "--print-to-pdf-no-header",
        "--window-size=1280,1600",
        "--force-device-scale-factor=1",
        "--lang=en-US",
    ),
    font_readiness="virtual-time-budget/10000",
    virtual_time_budget_ms=10000,
    locale="en-US",
    timezone="UTC",
)


def _find_chrome() -> ChromeBinary:
    override = os.getenv("CHROME_BIN") or os.getenv("CHROME_EXECUTABLE")
    if override:
        if not Path(override).is_file():
            raise HtmlToPdfExportError(
                f"CHROME_BIN/CHROME_EXECUTABLE points to {override}, which does "
                f"not exist. {_MISSING_BROWSER}"
            )
        return ChromeBinary(override, "override")
    if Path(_MACOS_CHROME).exists():
        return ChromeBinary(_MACOS_CHROME, "macos_default")
    for name in _CHROME_CANDIDATES:
        found = shutil.which(name)
        if found:
            return ChromeBinary(found, "system")
    raise HtmlToPdfExportError(_MISSING_BROWSER)


def require_chrome_binary() -> ChromeBinary:
    """Resolve the browser or raise the actionable error.

    Lanes call this before provider work: a missing browser is a precondition,
    and waiting until the lane wants a PDF spends paid model requests first.
    """
    return _find_chrome()


def _binary_file_state(executable: str) -> tuple[int, int, int] | None:
    """The file state a probed version is valid for. An in-place upgrade or
    replacement rewrites the file, so a cached version must not outlive it."""
    try:
        stat = os.stat(executable)
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size, stat.st_ino)


@functools.lru_cache(maxsize=8)
def _probe_binary_version(
    executable: str, _file_state: tuple[int, int, int]
) -> str | None:
    try:
        result = subprocess.run(
            [executable, "--version"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() or None


def _binary_version(executable: str) -> str | None:
    """The installed browser version, re-probed whenever the file state changes."""
    file_state = _binary_file_state(executable)
    if file_state is None:
        return None
    return _probe_binary_version(executable, file_state)


def font_asset_identity() -> dict[str, Any]:
    """The bundled webfont/CSS assets Chrome embeds, by content."""
    files = sorted(
        path for path in FONT_ASSET_DIR.glob("*") if path.suffix in {".woff2", ".css"}
    )
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    directory = FONT_ASSET_DIR.relative_to(Path(__file__).resolve().parents[2])
    return {"directory": directory.as_posix(), "sha256": digest.hexdigest(), "files": [path.name for path in files]}


@functools.lru_cache(maxsize=1)
def fallback_font_identity() -> dict[str, str]:
    """Installed fallback-font package versions, where the host reports them.

    Chrome draws glyphs the bundled woff2 assets do not cover from these fonts,
    so a version change changes rendered glyphs and must invalidate a proof.
    Hosts without dpkg (macOS) report none and are outside this identity.
    """
    try:
        result = subprocess.run(
            ["dpkg-query", "-W", "-f=${Package}=${Version}\n", *_FALLBACK_FONT_PACKAGES],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    return dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )


def timeout_seconds() -> float:
    try:
        return max(float(os.getenv("CHROME_EXPORT_TIMEOUT_SECONDS", "120")), 1.0)
    except ValueError as exc:
        raise HtmlToPdfExportError(
            "CHROME_EXPORT_TIMEOUT_SECONDS must be a number of seconds"
        ) from exc


def _environment(binary: ChromeBinary | None) -> dict[str, Any]:
    version = _binary_version(binary.path) if binary is not None else None
    record: dict[str, Any] = {
        "contract": CONTRACT_VERSION,
        "profile": PROFILE.name,
        "page_contract": PROFILE.page_contract,
        "background_contract": PROFILE.background_contract,
        "font_readiness": PROFILE.font_readiness,
        "executable": binary.path if binary is not None else None,
        "resolution": binary.resolution if binary is not None else "unresolved",
        "version": version,
        "flags": list(PROFILE.flags),
        "locale": PROFILE.locale,
        "timezone": PROFILE.timezone,
        "timeout_seconds": timeout_seconds(),
        "fonts": font_asset_identity(),
        "fallback_fonts": fallback_font_identity(),
    }
    identity = {field: record[field] for field in _IDENTITY_FIELDS}
    record["digest"] = hashlib.sha256(
        json.dumps(identity, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    return record


def chrome_environment() -> dict[str, Any]:
    """The recorded export environment. Recording never fails: a host without
    Chrome records ``resolution: unresolved``, which no previously approved
    proof can match, so reuse fails closed instead of inheriting approval."""
    try:
        binary: ChromeBinary | None = _find_chrome()
    except HtmlToPdfExportError:
        binary = None
    return _environment(binary)


def chrome_export_identity() -> dict[str, Any]:
    """The part of :func:`chrome_environment` that identifies renders.

    Proof identity hashes only this, so recording where the same browser build
    was found (or the export timeout) never invalidates an approved proof."""
    environment = chrome_environment()
    return {field: environment[field] for field in _IDENTITY_FIELDS}


def _process_environment(environment: Mapping[str, Any]) -> dict[str, str]:
    locale = str(environment["locale"]).replace("-", "_")
    return {
        **os.environ,
        "LANG": f"{locale}.UTF-8",
        "TZ": str(environment["timezone"]),
    }


def _print_to_pdf(
    environment: Mapping[str, Any], html_path: Path, staged: Path
) -> None:
    timeout = float(environment["timeout_seconds"])
    if not environment.get("executable"):
        raise HtmlToPdfExportError(_MISSING_BROWSER)
    command = [
        str(environment["executable"]),
        *environment["flags"],
        f"--virtual-time-budget={PROFILE.virtual_time_budget_ms}",
        f"--print-to-pdf={staged}",
        html_path.resolve().as_uri(),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_process_environment(environment),
        )
    except subprocess.TimeoutExpired as exc:
        raise HtmlToPdfExportError(
            f"Chrome print-to-pdf timed out after {timeout} seconds."
        ) from exc
    except OSError as exc:
        raise HtmlToPdfExportError(f"Chrome print-to-pdf could not start: {exc}") from exc
    if result.returncode != 0:
        raise HtmlToPdfExportError(
            f"Chrome print-to-pdf failed (exit {result.returncode}): "
            f"{result.stderr.strip()[-500:]}"
        )
    if not staged.exists() or staged.stat().st_size == 0:
        raise HtmlToPdfExportError("Chrome print-to-pdf produced no PDF.")


def export_html_to_pdf(
    html_path: str | Path,
    output_path: str | Path,
    *,
    environment: Mapping[str, Any] | None = None,
) -> Path:
    """Print one HTML file to PDF under the shared export contract.

    Chrome writes a new staging file that only replaces ``output_path`` on
    success, so a failed or timed-out export preserves the previous PDF and a
    pre-existing file is never mistaken for this export's output.
    """
    html_path, output_path = Path(html_path), Path(output_path)
    if not html_path.exists():
        raise HtmlToPdfExportError(f"HTML file does not exist: {html_path}")
    environment = environment or _environment(_find_chrome())
    output_path.parent.mkdir(parents=True, exist_ok=True)
    staged = output_path.with_name(f".{output_path.stem}.{uuid.uuid4().hex}.tmp.pdf")
    try:
        _print_to_pdf(environment, html_path, staged)
        os.replace(staged, output_path)
    finally:
        staged.unlink(missing_ok=True)
    return output_path
