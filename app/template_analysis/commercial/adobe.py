"""Adobe PDF Extract adapter and deterministic offline response normalizer."""

from __future__ import annotations

import io
import json
import os
import re
import time
import zipfile
from pathlib import Path
from typing import Any

import httpx

from app.template_analysis.commercial.models import (
    MeasurementProvenance,
    NormalizedBox,
    NormalizedLayoutEvidence,
    NormalizedPage,
    NormalizedTextBlock,
)


class AdobeConfigurationError(RuntimeError):
    pass


class AdobeAdapterError(RuntimeError):
    pass


# Cleanup evidence carried by any exception raised from ``run_adobe_layout``.
CLEANUP_EVIDENCE_ATTRIBUTE = "adobe_asset_cleanup"

# A retry-later answer means the DELETE was not applied, not that the server
# refused the asset: the aggregate state is ``pending`` for these.
RETRY_LATER_STATUSES = frozenset({408, 425, 429})

# Adobe asset ids are opaque provider tokens (URN or UUID shaped). Anything
# else, for example a URL-shaped value carrying a signed token, is recorded
# redacted instead of verbatim (issue #54 AC4).
ASSET_ID_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,200}")
REDACTED_ASSET_ID = "redacted"


def normalize_adobe_layout(raw: dict[str, Any]) -> NormalizedLayoutEvidence:
    """Normalize a direct Adobe payload or archived ``structured_data`` wrapper.

    Missing measurements remain ``None`` and are rejected by the compile
    bridge. No local analyzer enriches or repairs Adobe evidence.
    """
    structured = raw.get("structured_data") if isinstance(raw.get("structured_data"), dict) else raw
    page_rows = structured.get("Pages") or structured.get("pages") or []
    pages = [
        NormalizedPage(
            page_number=index + 1,
            width_pt=float(page.get("Width") or page.get("width") or 1),
            height_pt=float(page.get("Height") or page.get("height") or 1),
        )
        for index, page in enumerate(page_rows)
    ]
    dimensions = {page.page_number - 1: (page.width_pt, page.height_pt) for page in pages}
    blocks: list[NormalizedTextBlock] = []
    table_paths: set[str] = set()
    figure_paths: set[str] = set()
    styles: set[tuple[Any, ...]] = set()
    source_elements = structured.get("elements") or structured.get("Elements") or []
    for order, element in enumerate(_flatten_text_elements(source_elements)):
        path = str(element.get("Path") or element.get("path") or "")
        if "/Table" in path:
            table_paths.add(path.split("/Table", 1)[0] + "/Table")
        if "/Figure" in path:
            figure_paths.add(path.split("/Figure", 1)[0] + "/Figure")
        text = element.get("Text") or element.get("text")
        if not text:
            continue
        zero_page = int(element.get("Page") or element.get("page") or 0)
        width, height = dimensions.get(zero_page, (1.0, 1.0))
        bbox = _adobe_box(element.get("Bounds") or element.get("bounds"), width, height)
        font = element.get("Font") or element.get("font") or {}
        attributes = element.get("attributes") or element.get("Attributes") or {}
        family = _first(font, "family_name", "FamilyName", "alt_family_name", "name", "Name")
        postscript = _first(font, "name", "Name")
        size = _float(element.get("TextSize") or element.get("textSize") or _first(font, "size", "Size"))
        weight = _first(font, "weight", "Weight")
        italic_value = _first(font, "italic", "Italic")
        provider_color = _color_hex(_first(element, "Color", "color") or _first(font, "Color", "color"))
        element_id = f"adobe.page.{zero_page + 1}.element.{order}"
        color = provider_color
        provenance: dict[str, MeasurementProvenance] = {}
        for field, present in (("font_family", family), ("font_size_pt", size)):
            if present is not None:
                provenance[field] = MeasurementProvenance(
                    source="provider", provider="adobe", source_element_id=element_id,
                    method="adobe_extract_styling",
                )
        if color is not None:
            provenance["color_hex"] = MeasurementProvenance(
                source="provider",
                provider="adobe",
                source_element_id=element_id,
                method="adobe_extract_styling",
            )
        role = _structural_role(path)
        bold = _bold(weight)
        italic = _italic(italic_value)
        style_class = _typography_class(size, bold, italic, attributes)
        styles.add((family, size, bold, italic, color, style_class))
        blocks.append(
            NormalizedTextBlock(
                element_id=element_id,
                text=str(text),
                page_number=zero_page + 1,
                reading_order=order,
                bbox=bbox,
                structural_role=role,
                font_family=str(family) if family else None,
                font_size_pt=size,
                bold=bold,
                italic=italic,
                color_hex=color,
                line_height_pt=_float(attributes.get("LineHeight") or attributes.get("lineHeight")),
                spacing_before_pt=_float(attributes.get("SpaceBefore") or attributes.get("spaceBefore")),
                spacing_after_pt=_float(attributes.get("SpaceAfter") or attributes.get("spaceAfter")),
                text_align=str(attributes.get("TextAlign") or attributes.get("textAlign") or "") or None,
                postscript_name=str(postscript) if postscript else None,
                char_bounds=[
                    box for item in (element.get("CharBounds") or element.get("charBounds") or [])
                    if (box := _adobe_box(item, width, height)) is not None
                ],
                granularity="element",
                typography_class=style_class,
                provenance=provenance,
            )
        )
    return NormalizedLayoutEvidence(
        provider="adobe",
        provider_version=str(structured.get("version") or structured.get("Version") or "unknown"),
        pages=pages,
        page_count=len(pages),
        full_text="\n".join(block.text for block in blocks),
        text_blocks=blocks,
        table_count=len(table_paths),
        figure_count=len(figure_paths),
        style_record_count=len(styles),
        warnings=[],
    )


def _flatten_text_elements(
    elements: list[dict[str, Any]],
    *,
    inherited_attributes: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return text-bearing leaves while retaining parent paragraph metrics."""

    flattened: list[dict[str, Any]] = []
    for element in elements:
        attributes = {
            **(inherited_attributes or {}),
            **(element.get("attributes") or element.get("Attributes") or {}),
        }
        children = element.get("Kids") or element.get("kids") or []
        if element.get("Text") or element.get("text"):
            flattened.append({**element, "attributes": attributes})
        elif children and _structural_children_are_supported(element, children):
            leaf = str(element.get("Path") or element.get("path") or "").rsplit("/", 1)[-1]
            role = leaf.split("[", 1)[0]
            selected_children = (
                [
                    child
                    for child in children
                    if str(child.get("Path") or child.get("path") or "")
                    .rsplit("/", 1)[-1]
                    .split("[", 1)[0] == "LBody"
                ]
                if role in {"L", "LI"}
                else children
                if role == "P"
                else [
                    child
                    for child in children
                    if any(
                        character.isalnum()
                        for character in str(child.get("Text") or "")
                    )
                ]
            )
            flattened.extend(
                _flatten_text_elements(
                    selected_children,
                    inherited_attributes=attributes,
                )
            )
    return flattened


def _structural_children_are_supported(
    parent: dict[str, Any],
    children: list[dict[str, Any]],
) -> bool:
    leaf = str(parent.get("Path") or parent.get("path") or "").rsplit("/", 1)[-1]
    role = leaf.split("[", 1)[0]
    if role in {"H1", "H2", "H3", "H4", "H5", "H6"}:
        return True
    text = " ".join(str(child.get("Text") or "") for child in children)
    if role in {"L", "LI"}:
        return any(
            str(child.get("Path") or child.get("path") or "")
            .rsplit("/", 1)[-1]
            .split("[", 1)[0] == "LBody"
            and str(child.get("Text") or "").strip()
            for child in children
        )
    return (
        role == "P"
        and "/Table/" not in str(parent.get("Path") or parent.get("path") or "")
        and (
            ("|" in text and ("@" in text or "http" in text.casefold()))
            or (len(children) <= 4 and all(child.get("Text") or child.get("text") for child in children))
        )
    )


def run_adobe_layout(pdf_path: Path) -> tuple[NormalizedLayoutEvidence, dict[str, Any], bytes]:
    """Execute one live Adobe Extract operation. Callers own live-policy gating."""
    client_id = os.getenv("ADOBE_PDF_SERVICES_CLIENT_ID")
    client_secret = os.getenv("ADOBE_PDF_SERVICES_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise AdobeConfigurationError("Adobe PDF Services credentials are not configured.")
    region = os.getenv("ADOBE_PDF_SERVICES_REGION", "US").upper()
    base_url = "https://pdf-services-ew1.adobe.io" if region in {"EU", "EUROPE"} else "https://pdf-services.adobe.io"
    timeout = float(os.getenv(
        "ADOBE_PDF_SERVICES_TIMEOUT_SECONDS",
        os.getenv("COMMERCIAL_BAKEOFF_TIMEOUT_SECONDS", "180"),  # existing local configuration
    ))
    # Every asset identity survives a later failure so the cleanup boundary
    # below can attempt bounded deletion while the client stays authorized.
    known_assets: list[dict[str, str]] = []
    cleanup: dict[str, Any] = {"cleanup_state": "unknown", "assets": [], "limitations": []}
    headers: dict[str, str] = {}
    # Tri-state: an unanswered submission is ambiguous, never "not submitted".
    submission_state = "not_attempted"
    recorded_input_id: str | None = None
    recorded_result_id: str | None = None
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            try:
                token = client.post(
                    "https://pdf-services.adobe.io/token",
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    data={"client_id": client_id, "client_secret": client_secret},
                )
                _raise_http(token, "Adobe token request")
                access_token = token.json().get("access_token")
                if not access_token:
                    raise AdobeAdapterError("Adobe token response omitted access_token.")
                headers = {"Authorization": f"Bearer {access_token}", "x-api-key": client_id}
                created = client.post(f"{base_url}/assets", headers={**headers, "Content-Type": "application/json"}, json={"mediaType": "application/pdf"})
                _raise_http(created, "Adobe asset creation")
                asset = created.json()
                asset_id, upload_uri = asset.get("assetID"), asset.get("uploadUri")
                if asset_id:
                    identity = _asset_identity("input", asset_id)
                    known_assets.append(identity)
                    recorded_input_id = identity["asset_id"]
                if not asset_id or not upload_uri:
                    raise AdobeAdapterError("Adobe asset response omitted assetID or uploadUri.")
                uploaded = client.put(upload_uri, headers={"Content-Type": "application/pdf"}, content=pdf_path.read_bytes())
                _raise_http(uploaded, "Adobe asset upload")
                # The paid Extract job may start even when the answer is lost, so
                # the attempt is recorded before the request is sent.
                submission_state = "attempted_unknown"
                submitted = client.post(
                    f"{base_url}/operation/extractpdf", headers={**headers, "Content-Type": "application/json"},
                    json={"assetID": asset_id, "elementsToExtract": ["text", "tables"], "renditionsToExtract": ["tables", "figures"], "getCharBounds": True, "includeStyling": True},
                )
                _raise_http(submitted, "Adobe extract submission")
                submission_state = "accepted"
                status_url = submitted.headers.get("location")
                if not status_url:
                    raise AdobeAdapterError("Adobe did not return a job status URL.")
                status = _poll(client, status_url, headers, timeout)
                download_uri, result_id = _result_location(status)
                if result_id:
                    identity = _asset_identity("result", result_id)
                    known_assets.append(identity)
                    recorded_result_id = identity["asset_id"]
                if not download_uri:
                    raise AdobeAdapterError("Adobe completed without a result URI.")
                downloaded = client.get(download_uri)
                _raise_http(downloaded, "Adobe result download")
                zip_bytes = downloaded.content
            finally:
                cleanup = _cleanup_remote_assets(client, base_url, headers, known_assets, submission_state)
        structured = _structured_json(zip_bytes)
        raw = {"status": status, "input_asset_id": recorded_input_id, "result_asset_id": recorded_result_id, "asset_cleanup": cleanup, "structured_data": structured}
        return normalize_adobe_layout(raw), raw, zip_bytes
    except Exception as exc:
        # The provider failure stays primary; cleanup evidence only rides along.
        setattr(exc, CLEANUP_EVIDENCE_ATTRIBUTE, cleanup)
        raise


def _cleanup_remote_assets(
    client: httpx.Client,
    base_url: str,
    headers: dict[str, str],
    known_assets: list[dict[str, str]],
    submission_state: str,
) -> dict[str, Any]:
    """Attempt one bounded DELETE per known asset and classify the outcome.

    Never raises and never retries: an unresolved deletion is reported, not
    repaired, so the primary provider result or failure stays attributable.
    An ambiguous submission is reported as a limitation, never as confirmed.
    """
    assets: list[dict[str, Any]] = []
    for known in known_assets:
        record: dict[str, Any] = {**known, "outcome": "unknown", "http_status": None}
        if known.get("asset_id_kind"):
            record["reason"] = "asset identity was not a bounded opaque token; deletion not attempted"
        elif not headers:
            record["reason"] = "no authenticated client at cleanup time"
        else:
            try:
                response = client.delete(f"{base_url}/assets/{known['asset_id']}", headers=headers)
            except Exception:  # cleanup must never mask the primary provider outcome (issue #54)
                record["outcome"] = "transient"
            else:
                record["http_status"] = response.status_code
                record["outcome"] = _delete_outcome(response.status_code)
        assets.append(record)
    limitations: list[str] = []
    if submission_state == "attempted_unknown":
        limitations.append(
            "Extract submission outcome is unknown: the request was sent but Adobe's answer was "
            "never received, so the paid Extract job may still be running remotely. Discovering "
            "or cancelling it would require further paid work, which is not done automatically."
        )
    elif submission_state == "accepted" and not any(record["role"] == "result" for record in assets):
        limitations.append(
            "Extract job was submitted but Adobe returned no result asset identity; "
            "that asset's remote state is unknown and the paid operation was not resubmitted."
        )
    if any(record["asset_id"] == REDACTED_ASSET_ID for record in assets):
        limitations.append(
            "An Adobe asset identity was not a bounded opaque token, so it is recorded redacted; "
            "its deletion was not attempted and it may still exist remotely."
        )
    outcomes = {record["outcome"] for record in assets}
    # An unanswered DELETE (transport failure) or 5xx may have been applied and
    # stays ``unknown``; a retry-later answer is still ``pending`` work.
    unanswered = any(
        record["outcome"] in {"transient", "unknown"}
        and record["http_status"] not in RETRY_LATER_STATUSES
        for record in assets
    )
    # Worst outcome wins, so confirmed cleanup requires every asset to be gone.
    if not assets or limitations or unanswered:
        state = "unknown"
    elif outcomes - {"deleted", "already_deleted"}:
        state = "pending"
    else:
        state = "confirmed"
    return {"cleanup_state": state, "assets": assets, "limitations": limitations}


def _asset_identity(role: str, value: Any) -> dict[str, Any]:
    """Record a provider identity only when it is a bounded opaque token."""
    text = str(value)
    if ASSET_ID_PATTERN.fullmatch(text):
        return {"role": role, "asset_id": text}
    return {
        "role": role,
        "asset_id": REDACTED_ASSET_ID,
        "asset_id_kind": "url" if "://" in text else "unrecognized",
        "asset_id_length": len(text),
    }


def _delete_outcome(status_code: int) -> str:
    """Classify one DELETE response; 404/410 mean absent, not deletion by us."""
    if 200 <= status_code < 300:
        return "deleted"
    if status_code in {404, 410}:
        return "already_deleted"
    if 400 <= status_code < 500 and status_code not in RETRY_LATER_STATUSES:
        return "rejected"
    return "transient"


def _display_token(value: Any) -> str:
    """Show a value only when it is a bounded opaque token, else a marker."""
    text = str(value)
    return text if ASSET_ID_PATTERN.fullmatch(text) else REDACTED_ASSET_ID


def describe_asset_cleanup(cleanup: dict[str, Any] | None) -> str:
    """Render cleanup evidence as one safe line for an error or warning surface.

    Only role/identity/outcome/status and the recorded limitations are
    included: never credentials, authorization headers or signed URLs. Every
    rendered value is re-validated here, so a hostile identity handed in
    directly cannot reach a caller's output.
    """
    if not cleanup:
        return "Adobe asset cleanup: unknown (no cleanup evidence recorded)."
    assets = cleanup.get("assets") or []
    known = ", ".join(
        f"{_display_token(item.get('role'))} asset {_display_token(item.get('asset_id'))} "
        f"{_display_token(item.get('outcome'))}"
        + (f" (HTTP {item['http_status']})" if isinstance(item.get("http_status"), int) else "")
        for item in assets
    ) or "none"
    parts = [f"Adobe asset cleanup: {_display_token(cleanup.get('cleanup_state', 'unknown'))}; known assets: {known}"]
    parts.extend(cleanup.get("limitations") or [])
    return "; ".join(parts)


def _structural_role(path: str) -> str:
    leaf = path.rsplit("/", 1)[-1].split("[", 1)[0]
    if leaf == "Title":
        return "title"
    if leaf in {"H1", "H2", "H3", "H4", "H5", "H6"}:
        return "heading_candidate"
    if leaf in {"L", "LI", "Lbl", "LBody"}:
        return "list"
    if leaf in {"Table", "TR", "TH", "TD"}:
        return "table"
    if leaf in {"P", "Span"}:
        return "body"
    return "other"


def _typography_class(size: float | None, bold: bool | None, italic: bool | None, attributes: dict[str, Any]) -> str | None:
    if size is None:
        return None
    decoration = "rule" if attributes.get("TextDecoration") else "plain"
    return f"size:{round(size, 2):.2f}|weight:{'bold' if bold else 'regular'}|style:{'italic' if italic else 'normal'}|decoration:{decoration}"


def _adobe_box(value: Any, width: float, height: float) -> NormalizedBox | None:
    if not isinstance(value, list) or len(value) < 4:
        return None
    x0, y0, x1, y1 = (float(item) for item in value[:4])
    return NormalizedBox(x0=max(0,min(1,min(x0,x1)/width)), top=max(0,min(1,(height-max(y0,y1))/height)), x1=max(0,min(1,max(x0,x1)/width)), bottom=max(0,min(1,(height-min(y0,y1))/height)))


def _pdf_color(value: Any) -> str | None:
    if value is None:
        return None
    values = list(value) if isinstance(value, (tuple, list)) else [value]
    try:
        if len(values) == 1:
            rgb = [float(values[0])] * 3
        elif len(values) == 3:
            rgb = [float(item) for item in values]
        elif len(values) == 4:
            c, m, y, k = (float(item) for item in values)
            rgb = [(1-c)*(1-k), (1-m)*(1-k), (1-y)*(1-k)]
        else:
            return None
    except (TypeError, ValueError):
        return None
    return "#" + "".join(f"{round(max(0,min(1,item))*255):02X}" for item in rgb)


def _color_hex(value: Any) -> str | None:
    if isinstance(value, str) and value.startswith("#") and len(value) == 7:
        return value.upper()
    return _pdf_color(value)


def _first(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if mapping.get(key) is not None:
            return mapping[key]
    return None


def _float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _bold(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) >= 600
    return any(token in str(value).lower() for token in ("bold", "semibold", "demi", "700", "800", "900"))


def _italic(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return any(token in str(value).lower() for token in ("italic", "oblique"))


def _raise_http(response: httpx.Response, operation: str) -> None:
    if not response.is_success:
        raise AdobeAdapterError(f"{operation} failed with HTTP {response.status_code}.")


def _poll(client: httpx.Client, url: str, headers: dict[str, str], timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(url, headers=headers)
        _raise_http(response, "Adobe status polling")
        payload = response.json()
        status = str(payload.get("status") or "").lower()
        if status == "done":
            return payload
        if status == "failed":
            raise AdobeAdapterError("Adobe extract job failed.")
        time.sleep(1.0)
    raise AdobeAdapterError("Adobe extract operation timed out.")


def _result_location(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    resource = payload.get("asset") or payload.get("result") or payload.get("resource") or {}
    if isinstance(resource, dict) and isinstance(resource.get("asset"), dict):
        resource = resource["asset"]
    return ((payload.get("downloadUri") or (resource.get("downloadUri") if isinstance(resource, dict) else None)), (payload.get("assetID") or (resource.get("assetID") if isinstance(resource, dict) else None)))


def _structured_json(zip_bytes: bytes) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
            name = next(item for item in archive.namelist() if Path(item).name.lower() == "structureddata.json")
            return json.loads(archive.read(name).decode("utf-8"))
    except (StopIteration, zipfile.BadZipFile, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdobeAdapterError("Adobe result omitted valid structuredData.json.") from exc
