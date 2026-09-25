from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any, Callable

import httpx

from app.extraction.candidate_schema import CandidateProfile
from app.extraction.llm_extractor import CandidateProfileLLMOutput
from app.extraction.prompts import SYSTEM_PROMPT, build_candidate_extraction_prompt
from tests.helpers.adobe_evidence import build_synthetic_target_analysis
from app.template_analysis.commercial.models import (
    NormalizedBox,
    NormalizedLayoutEvidence,
    NormalizedTextBlock,
)
from app.validation.missing_fields import apply_missing_field_detection
from tests.commercial_api.models import UsageRecord


class ProviderConfigurationError(RuntimeError):
    """A selected provider is missing credentials or a runtime dependency."""


class ProviderCallError(RuntimeError):
    """A provider returned a terminal error or an invalid response."""


#: The only CandidateProfile fields typed ``HttpUrl``. URL normalization in
#: ``run_openai_extraction`` is intentionally scoped to exactly these fields;
#: raw extracted text and every other payload field are never mutated.
_CANDIDATE_URL_FIELDS = ("linkedin_url", "portfolio_url")

#: Common PDF ligature artifacts found in extracted source text. Applied only
#: inside ``_normalize_url_field`` (never to raw extracted text).
_LIGATURE_MAP = {
    "\ufb00": "ff",
    "\ufb01": "fi",
    "\ufb02": "fl",
    "\ufb03": "ffi",
    "\ufb04": "ffl",
    "\ufb05": "st",
}


def _normalize_url_field(value: str) -> str:
    """Normalize a single candidate URL for strict ``HttpUrl`` validation.

    Removes PDF ligature artifacts and prepends ``https://`` when the value is
    scheme-less (source resumes commonly carry bare ``linkedin.com/in/...``).
    Scope invariant: this is applied only to ``_CANDIDATE_URL_FIELDS`` (the
    ``HttpUrl``-typed fields of ``CandidateProfile``); it never touches raw
    extracted text or any other payload field.
    """
    normalized = str(value)
    for ligature, replacement in _LIGATURE_MAP.items():
        normalized = normalized.replace(ligature, replacement)
    if not normalized.startswith(("http://", "https://")):
        normalized = f"https://{normalized}"
    return normalized



def run_openai_extraction(resume_text: str) -> tuple[CandidateProfile, dict[str, Any], UsageRecord]:
    from openai import OpenAI

    api_key = os.getenv("OPENAI_EXTRACT_API_KEY") or os.getenv("OPENAI_API_KEY")
    model = os.getenv("OPENAI_EXTRACT_MODEL") or os.getenv("OPENAI_MODEL")
    if not api_key or not model:
        raise ProviderConfigurationError(
            "OpenAI requires OPENAI_EXTRACT_API_KEY and OPENAI_EXTRACT_MODEL."
        )

    prompt = build_candidate_extraction_prompt(
        resume_text,
        None,
        response_schema=CandidateProfileLLMOutput.model_json_schema(),
    )
    response = OpenAI(api_key=api_key).responses.parse(
        model=model,
        instructions=SYSTEM_PROMPT,
        input=prompt,
        text_format=CandidateProfileLLMOutput,
        store=False,
    )
    parsed = response.output_parsed
    if parsed is None:
        raise ProviderCallError("OpenAI returned no structured CandidateProfile payload.")
    payload = parsed.model_dump()
    # The source resumes in the authorized matrix corpus contain scheme-less
    # URLs (e.g. `linkedin.com/in/example`) and occasionally PDF ligature
    # artifacts (e.g. U+FB01 in `uniFB01le`); normalize only the fields typed
    # HttpUrl in CandidateProfile so a faithful extraction does not fail the
    # run. Raw extracted text and every other payload field are never touched.
    for field in _CANDIDATE_URL_FIELDS:
        value = payload.get(field)
        if value:
            payload[field] = _normalize_url_field(value)
    profile = apply_missing_field_detection(CandidateProfile.model_validate(payload))
    usage_obj = getattr(response, "usage", None)
    usage = UsageRecord(
        input_tokens=_int_attr(usage_obj, "input_tokens"),
        output_tokens=_int_attr(usage_obj, "output_tokens"),
        total_tokens=_int_attr(usage_obj, "total_tokens"),
        transactions=1,
    )
    raw = {
        "provider": "openai",
        "model": model,
        "response_id": getattr(response, "id", None),
        "profile": parsed.model_dump(mode="json"),
        "usage": usage.model_dump(mode="json"),
    }
    return profile, raw, usage


def run_baseline_layout(pdf_path: Path, artifact_dir: Path) -> tuple[NormalizedLayoutEvidence, dict[str, Any]]:
    analysis = build_synthetic_target_analysis(pdf_path, artifact_dir)
    blocks: list[NormalizedTextBlock] = []
    for page in analysis.pages:
        for block in page.text_blocks:
            blocks.append(
                NormalizedTextBlock(
                    text=block.text,
                    page_number=page.page_number,
                    bbox=_box_from_top_left(
                        block.bbox.x0,
                        block.bbox.top,
                        block.bbox.x1,
                        block.bbox.bottom,
                        page.width_pt,
                        page.height_pt,
                    ),
                    role=block.role,
                    font_family=block.font_family,
                    font_size=block.font_size_pt,
                    bold=block.bold,
                    italic=block.italic,
                    color=block.color_hex,
                )
            )
    evidence = NormalizedLayoutEvidence(
        provider="baseline",
        provider_version=analysis.analysis_version,
        page_count=analysis.page_count,
        full_text="\n".join(block.text for block in blocks),
        text_blocks=blocks,
        figure_count=sum(
            graphic.kind == "image" for page in analysis.pages for graphic in page.graphics
        ),
        graphic_count=sum(len(page.graphics) for page in analysis.pages),
        style_record_count=len(
            {
                (
                    block.font_family,
                    block.font_size_pt,
                    block.bold,
                    block.italic,
                    block.color_hex,
                )
                for page in analysis.pages
                for block in page.text_blocks
            }
        ),
        warnings=analysis.warnings,
    )
    return evidence, analysis.model_dump(mode="json")


def render_with_adobe(
    docx_path: Path,
    output_path: Path,
    *,
    evidence_sink: Callable[[dict[str, Any]], None] | None = None,
) -> Path:
    client_id = os.getenv("ADOBE_PDF_SERVICES_CLIENT_ID")
    client_secret = os.getenv("ADOBE_PDF_SERVICES_CLIENT_SECRET")
    region = os.getenv("ADOBE_PDF_SERVICES_REGION", "US").upper()
    if not client_id or not client_secret:
        raise ProviderConfigurationError(
            "Adobe requires ADOBE_PDF_SERVICES_CLIENT_ID and "
            "ADOBE_PDF_SERVICES_CLIENT_SECRET."
        )
    base_url = (
        "https://pdf-services-ew1.adobe.io"
        if region in {"EU", "EUROPE"}
        else "https://pdf-services.adobe.io"
    )
    timeout = _timeout_seconds()
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        token_response = client.post(
            "https://pdf-services.adobe.io/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={"client_id": client_id, "client_secret": client_secret},
        )
        _raise_http_error(token_response, "Adobe token request")
        access_token = token_response.json().get("access_token")
        if not access_token:
            raise ProviderCallError("Adobe token response contained no access_token.")
        auth_headers = {"Authorization": f"Bearer {access_token}", "x-api-key": client_id}

        asset_response = client.post(
            f"{base_url}/assets",
            headers={**auth_headers, "Content-Type": "application/json"},
            json={
                "mediaType": (
                    "application/vnd.openxmlformats-officedocument."
                    "wordprocessingml.document"
                )
            },
        )
        _raise_http_error(asset_response, "Adobe DOCX asset creation")
        asset_payload = asset_response.json()
        input_asset_id = asset_payload.get("assetID")
        upload_uri = asset_payload.get("uploadUri")
        if not input_asset_id or not upload_uri:
            raise ProviderCallError("Adobe asset response omitted assetID or uploadUri.")

        upload_response = client.put(
            upload_uri,
            headers={
                "Content-Type": (
                    "application/vnd.openxmlformats-officedocument."
                    "wordprocessingml.document"
                )
            },
            content=docx_path.read_bytes(),
        )
        _raise_http_error(upload_response, "Adobe DOCX asset upload")

        job_response = client.post(
            f"{base_url}/operation/createpdf",
            headers={**auth_headers, "Content-Type": "application/json"},
            json={"assetID": input_asset_id},
        )
        _raise_http_error(job_response, "Adobe Create PDF submission")
        status_url = job_response.headers.get("location")
        if not status_url:
            raise ProviderCallError("Adobe did not return a Create PDF status Location header.")
        status_payload = _poll_json_operation(
            client,
            status_url,
            headers=auth_headers,
            success_statuses={"done"},
            failure_statuses={"failed"},
            provider="Adobe Create PDF",
        )
        download_uri, result_asset_id = _adobe_result_location(status_payload)
        # The meaningful Adobe job identifier is the status-URL job token (or
        # the result asset ID as fallback); the trailing 'status' segment is
        # never an identity. Capture x-request-id when the API exposes it.
        operation_id = _adobe_operation_id(status_url, result_asset_id)
        request_id = job_response.headers.get("x-request-id") or None
        if evidence_sink is not None:
            evidence_sink(
                {
                    "provider": "adobe_pdf_services",
                    "operation": "create_pdf",
                    "status_url": status_url,
                    "operation_id": operation_id,
                    "request_id": request_id,
                    "status": status_payload.get("status"),
                    "input_asset_id": input_asset_id,
                    "result_asset_id": result_asset_id,
                }
            )
        if not download_uri:
            raise ProviderCallError("Adobe Create PDF completed without a download URI.")
        download_response = client.get(download_uri)
        _raise_http_error(download_response, "Adobe Create PDF download")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(download_response.content)

        for asset_id in (input_asset_id, result_asset_id):
            if asset_id:
                try:
                    client.delete(f"{base_url}/assets/{asset_id}", headers=auth_headers)
                except httpx.HTTPError:
                    pass
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise ProviderCallError("Adobe Create PDF did not create the expected PDF.")
    return output_path


def render_html_with_chromium(html_path: Path, output_path: Path) -> Path:
    """Render deterministic HTML locally without altering the authored layout."""
    candidates = [
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
        Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
    ]
    binary = next((str(candidate) for candidate in candidates if candidate.is_file()), None)
    binary = binary or shutil.which("chromium") or shutil.which("google-chrome")
    if not binary:
        raise ProviderConfigurationError("Chromium is required for the local HTML baseline.")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="target-replica-chrome-") as profile_dir:
        command = [
            binary,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--disable-background-networking",
            "--disable-extensions",
            "--no-pdf-header-footer",
            f"--user-data-dir={profile_dir}",
            f"--print-to-pdf={output_path.resolve()}",
            html_path.resolve().as_uri(),
        ]
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + _timeout_seconds()
        while time.monotonic() < deadline:
            if output_path.exists() and output_path.stat().st_size > 0:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                return output_path
            if process.poll() is not None:
                break
            time.sleep(0.25)
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise ProviderCallError("Chromium HTML-to-PDF failed before producing output.")
    return output_path


def render_html_with_adobe(
    html_path: Path,
    output_path: Path,
    *,
    evidence_sink: Callable[[dict[str, Any]], None] | None = None,
) -> Path:
    """Use Adobe Create PDF's documented HTML ZIP input lane."""
    client_id = os.getenv("ADOBE_PDF_SERVICES_CLIENT_ID")
    client_secret = os.getenv("ADOBE_PDF_SERVICES_CLIENT_SECRET")
    region = os.getenv("ADOBE_PDF_SERVICES_REGION", "US").upper()
    if not client_id or not client_secret:
        raise ProviderConfigurationError(
            "Adobe requires ADOBE_PDF_SERVICES_CLIENT_ID and ADOBE_PDF_SERVICES_CLIENT_SECRET."
        )
    base_url = (
        "https://pdf-services-ew1.adobe.io"
        if region in {"EU", "EUROPE"}
        else "https://pdf-services.adobe.io"
    )
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for asset in sorted(html_path.parent.iterdir()):
            if asset.is_file():
                archive.write(asset, asset.name)
    archive_bytes = archive_buffer.getvalue()
    input_asset_id: str | None = None
    result_asset_id: str | None = None
    with httpx.Client(timeout=_timeout_seconds(), follow_redirects=True) as client:
        token_response = client.post(
            "https://pdf-services.adobe.io/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={"client_id": client_id, "client_secret": client_secret},
        )
        _raise_http_error(token_response, "Adobe token request")
        access_token = token_response.json().get("access_token")
        if not access_token:
            raise ProviderCallError("Adobe token response contained no access_token.")
        auth_headers = {"Authorization": f"Bearer {access_token}", "x-api-key": client_id}
        asset_response = client.post(
            f"{base_url}/assets",
            headers={**auth_headers, "Content-Type": "application/json"},
            json={"mediaType": "application/zip"},
        )
        _raise_http_error(asset_response, "Adobe HTML ZIP asset creation")
        asset_payload = asset_response.json()
        input_asset_id = asset_payload.get("assetID")
        upload_uri = asset_payload.get("uploadUri")
        if not input_asset_id or not upload_uri:
            raise ProviderCallError("Adobe asset response omitted assetID or uploadUri.")
        upload_response = client.put(
            upload_uri, headers={"Content-Type": "application/zip"}, content=archive_bytes
        )
        _raise_http_error(upload_response, "Adobe HTML ZIP upload")
        job_response = client.post(
            f"{base_url}/operation/htmltopdf",
            headers={**auth_headers, "Content-Type": "application/json"},
            json={
                "assetID": input_asset_id,
                "json": "{}",
                "includeHeaderFooter": False,
                "pageLayout": {"pageWidth": 8.5, "pageHeight": 11.0},
            },
        )
        _raise_http_error(job_response, "Adobe HTML Create PDF submission")
        status_url = job_response.headers.get("location")
        if not status_url:
            raise ProviderCallError("Adobe did not return a Create PDF status Location header.")
        status_payload = _poll_json_operation(
            client,
            status_url,
            headers=auth_headers,
            success_statuses={"done"},
            failure_statuses={"failed"},
            provider="Adobe HTML Create PDF",
        )
        download_uri, result_asset_id = _adobe_result_location(status_payload)
        if not download_uri:
            raise ProviderCallError("Adobe HTML Create PDF completed without a download URI.")
        download_response = client.get(download_uri)
        _raise_http_error(download_response, "Adobe HTML Create PDF download")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(download_response.content)
        if evidence_sink is not None:
            evidence_sink(
                {
                    "provider": "adobe_pdf_services",
                    "operation": "create_pdf_from_html_zip",
                    "operation_id": _adobe_operation_id(status_url, result_asset_id),
                    "request_id": job_response.headers.get("x-request-id"),
                    "status": status_payload.get("status"),
                }
            )
        for asset_id in (input_asset_id, result_asset_id):
            if asset_id:
                try:
                    client.delete(f"{base_url}/assets/{asset_id}", headers=auth_headers)
                except httpx.HTTPError:
                    pass
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise ProviderCallError("Adobe HTML Create PDF did not create the expected PDF.")
    return output_path


def _adobe_operation_id(status_url: str | None, result_asset_id: str | None) -> str:
    """Derive a meaningful Adobe job identifier from the Create PDF status URL.

    Adobe status URLs look like ``.../operation/createpdf/<job-token>/status``:
    the job token is the second-to-last segment. Falls back to the result
    asset ID when the URL shape is unexpected, and to the documented literal
    ``"unavailable"`` when neither is available (never the literal
    ``"status"`` segment, which carries no identity).
    """
    if status_url:
        segments = status_url.rstrip("/").split("/")
        if len(segments) >= 2 and "/operation/" in status_url:
            candidate = segments[-2]
            if candidate and candidate != "status":
                return candidate
    if result_asset_id:
        return result_asset_id
    return "unavailable"


def _poll_json_operation(
    client: httpx.Client,
    url: str,
    *,
    headers: dict[str, str],
    success_statuses: set[str],
    failure_statuses: set[str],
    provider: str,
) -> dict[str, Any]:
    deadline = time.monotonic() + _timeout_seconds()
    while time.monotonic() < deadline:
        response = client.get(url, headers=headers)
        _raise_http_error(response, f"{provider} status polling")
        payload = response.json()
        status = str(payload.get("status") or "").strip().lower().replace(" ", "")
        normalized_success = {item.lower().replace(" ", "") for item in success_statuses}
        normalized_failure = {item.lower().replace(" ", "") for item in failure_statuses}
        if status in normalized_success:
            return payload
        if status in normalized_failure:
            error = payload.get("error") or payload
            raise ProviderCallError(f"{provider} job failed: {_safe_error(error)}")
        time.sleep(_poll_seconds())
    raise ProviderCallError(f"{provider} operation timed out after {_timeout_seconds()} seconds.")


def _adobe_result_location(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    resource = payload.get("asset") or payload.get("result") or payload.get("resource") or {}
    if isinstance(resource, dict) and isinstance(resource.get("asset"), dict):
        resource = resource["asset"]
    download_uri = (
        payload.get("downloadUri")
        or payload.get("dowloadUri")
        or (resource.get("downloadUri") if isinstance(resource, dict) else None)
        or (resource.get("dowloadUri") if isinstance(resource, dict) else None)
    )
    asset_id = (
        payload.get("assetID")
        or (resource.get("assetID") if isinstance(resource, dict) else None)
    )
    return download_uri, asset_id


def _box_from_top_left(
    x0: float, top: float, x1: float, bottom: float, width: float, height: float
) -> NormalizedBox:
    return NormalizedBox(
        x0=_clamp(min(x0, x1) / max(width, 1e-9)),
        top=_clamp(min(top, bottom) / max(height, 1e-9)),
        x1=_clamp(max(x0, x1) / max(width, 1e-9)),
        bottom=_clamp(max(top, bottom) / max(height, 1e-9)),
    )


def _raise_http_error(response: httpx.Response, operation: str) -> None:
    if response.is_success:
        return
    try:
        detail = _safe_error(response.json())
    except (ValueError, json.JSONDecodeError):
        detail = response.text[:500]
    raise ProviderCallError(f"{operation} failed with HTTP {response.status_code}: {detail}")


def _safe_error(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=True) if not isinstance(value, str) else value
    for secret_name in (
        "OPENAI_EXTRACT_API_KEY",
        "ADOBE_PDF_SERVICES_CLIENT_SECRET",
    ):
        secret = os.getenv(secret_name)
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text[:1000]


def _timeout_seconds() -> float:
    return max(float(os.getenv("COMMERCIAL_BAKEOFF_TIMEOUT_SECONDS", "180")), 1.0)


def _poll_seconds() -> float:
    return max(float(os.getenv("COMMERCIAL_BAKEOFF_POLL_SECONDS", "2")), 0.25)


def _clamp(value: float) -> float:
    return round(min(max(value, 0.0), 1.0), 6)


def _int_attr(value: Any, name: str) -> int | None:
    raw = getattr(value, name, None)
    return int(raw) if raw is not None else None
