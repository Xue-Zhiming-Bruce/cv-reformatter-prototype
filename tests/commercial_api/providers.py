from __future__ import annotations

import base64
import io
import json
import os
import shutil
import subprocess
import tempfile
import time
import zipfile
import xml.etree.ElementTree as ET
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


_APRYSE_INITIALIZED = False


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


def run_azure_layout(
    pdf_path: Path,
    *,
    evidence_sink: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[NormalizedLayoutEvidence, dict[str, Any]]:
    endpoint = (os.getenv("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT") or "").rstrip("/")
    api_key = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_KEY")
    api_version = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_API_VERSION", "2024-11-30")
    if not endpoint or not api_key:
        raise ProviderConfigurationError(
            "Azure requires AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT and "
            "AZURE_DOCUMENT_INTELLIGENCE_KEY."
        )

    timeout = _timeout_seconds()
    url = f"{endpoint}/documentintelligence/documentModels/prebuilt-layout:analyze"
    headers = {"Ocp-Apim-Subscription-Key": api_key, "Content-Type": "application/json"}
    payload = {"base64Source": base64.b64encode(pdf_path.read_bytes()).decode("ascii")}
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        response = client.post(
            url,
            params={"api-version": api_version, "features": "styleFont"},
            headers=headers,
            json=payload,
        )
        _raise_http_error(response, "Azure layout submission")
        operation_url = response.headers.get("operation-location")
        if not operation_url:
            raise ProviderCallError("Azure did not return an Operation-Location header.")
        raw = _poll_json_operation(
            client,
            operation_url,
            headers={"Ocp-Apim-Subscription-Key": api_key},
            success_statuses={"succeeded"},
            failure_statuses={"failed", "canceled"},
            provider="Azure",
        )
    if evidence_sink is not None:
        evidence_sink(
            {
                "provider": "azure_document_intelligence",
                "operation": "prebuilt-layout:analyze",
                "api_version": api_version,
                "operation_url": operation_url,
                "operation_id": operation_url.rstrip("/").rsplit("/", 1)[-1],
                "status": raw.get("status"),
            }
        )
    return _normalize_azure(raw), raw


def run_pdfrest_layout(pdf_path: Path) -> tuple[NormalizedLayoutEvidence, dict[str, Any]]:
    api_key = os.getenv("PDFREST_API_KEY")
    base_url = (os.getenv("PDFREST_API_BASE_URL") or "https://api.pdfrest.com").rstrip("/")
    if not api_key:
        raise ProviderConfigurationError("pdfRest requires PDFREST_API_KEY.")

    with httpx.Client(timeout=_timeout_seconds(), follow_redirects=True) as client:
        response = client.post(
            f"{base_url}/extracted-text",
            headers={"Api-Key": api_key, "Accept": "application/json"},
            files={"file": (pdf_path.name, pdf_path.read_bytes(), "application/pdf")},
            data={
                "full_text": "document",
                "preserve_line_breaks": "on",
                "word_style": "on",
                "word_coordinates": "on",
                "output_type": "json",
            },
        )
        _raise_http_error(response, "pdfRest Extract Text")
        raw = response.json()

        deletion_warnings: list[str] = []
        input_id = raw.get("inputId")
        if input_id:
            try:
                delete_response = client.delete(
                    f"{base_url}/resource/{input_id}",
                    headers={"Api-Key": api_key, "Accept": "application/json"},
                )
                if delete_response.status_code not in {200, 202, 204, 404}:
                    deletion_warnings.append(
                        f"pdfRest input deletion returned HTTP {delete_response.status_code}."
                    )
            except httpx.HTTPError as exc:
                deletion_warnings.append(
                    f"pdfRest input deletion could not be confirmed: {type(exc).__name__}."
                )

    evidence = _normalize_pdfrest(raw, _pdf_page_dimensions(pdf_path))
    evidence.warnings.extend(deletion_warnings)
    return evidence, raw


def run_foxit_structural_layout(
    pdf_path: Path,
) -> tuple[NormalizedLayoutEvidence, dict[str, Any], bytes]:
    client_id = os.getenv("FOXIT_PDF_SERVICES_CLIENT_ID")
    client_secret = os.getenv("FOXIT_PDF_SERVICES_CLIENT_SECRET")
    base_url = (
        os.getenv("FOXIT_PDF_SERVICES_BASE_URL")
        or "https://na1.fusion.foxit.com/pdf-services/api"
    ).rstrip("/")
    if not client_id or not client_secret:
        raise ProviderConfigurationError(
            "Foxit requires FOXIT_PDF_SERVICES_CLIENT_ID and "
            "FOXIT_PDF_SERVICES_CLIENT_SECRET."
        )

    headers = {"client_id": client_id, "client_secret": client_secret}
    with httpx.Client(timeout=_timeout_seconds(), follow_redirects=True) as client:
        upload_response = client.post(
            f"{base_url}/documents/upload",
            headers=headers,
            files={"file": (pdf_path.name, pdf_path.read_bytes(), "application/pdf")},
        )
        _raise_http_error(upload_response, "Foxit document upload")
        document_id = upload_response.json().get("documentId")
        if not document_id:
            raise ProviderCallError("Foxit upload response omitted documentId.")

        job_response = client.post(
            f"{base_url}/documents/pdf-structural-extract",
            headers={**headers, "Content-Type": "application/json"},
            json={"documentId": document_id},
        )
        _raise_http_error(job_response, "Foxit structural extraction submission")
        task_id = job_response.json().get("taskId")
        if not task_id:
            raise ProviderCallError("Foxit extraction response omitted taskId.")

        status_payload = _poll_json_operation(
            client,
            f"{base_url}/tasks/{task_id}",
            headers=headers,
            success_statuses={"COMPLETED"},
            failure_statuses={"FAILED"},
            provider="Foxit",
        )
        result_document_id = status_payload.get("resultDocumentId")
        if not result_document_id:
            raise ProviderCallError("Foxit completed without resultDocumentId.")
        download_response = client.get(
            f"{base_url}/documents/{result_document_id}/download", headers=headers
        )
        _raise_http_error(download_response, "Foxit structural result download")
        zip_bytes = download_response.content

    structured = _foxit_structure_json_from_zip(zip_bytes)
    evidence = _normalize_foxit(structured, _pdf_page_dimensions(pdf_path))
    evidence.warnings.append(
        "Foxit Structural Extraction is a trial schema; uploaded documents expire "
        "after the provider retention window."
    )
    raw = {
        "task": status_payload,
        "input_document_id": document_id,
        "result_document_id": result_document_id,
        "structured_data": structured,
    }
    return evidence, raw, zip_bytes


def run_apryse_layout(pdf_path: Path) -> tuple[NormalizedLayoutEvidence, dict[str, Any]]:
    license_key = os.getenv("APRYSE_LICENSE_KEY")
    if not license_key:
        raise ProviderConfigurationError("Apryse requires APRYSE_LICENSE_KEY.")
    try:
        from apryse_sdk import Element, ElementReader, PDFDoc, PDFNet, TextExtractor
    except ImportError as exc:
        raise ProviderConfigurationError(
            "Apryse SDK is not installed. See requirements-optional.txt and README.md."
        ) from exc

    _initialize_apryse(PDFNet, license_key)
    document = PDFDoc(str(pdf_path))
    document.InitSecurityHandler()
    blocks: list[NormalizedTextBlock] = []
    raw_elements: list[dict[str, Any]] = []
    raw_text_xml: list[str] = []
    page_count = document.GetPageCount()
    figure_count = 0
    graphic_count = 0
    reader = ElementReader()
    try:
        page_iterator = document.GetPageIterator()
        page_number = 1
        while page_iterator.HasNext():
            page = page_iterator.Current()
            width = float(page.GetPageWidth())
            height = float(page.GetPageHeight())
            extractor = TextExtractor()
            extractor.Begin(page)
            xml_flags = (
                TextExtractor.e_words_as_elements
                | TextExtractor.e_output_bbox
                | TextExtractor.e_output_style_info
            )
            page_xml = str(extractor.GetAsXML(xml_flags))
            raw_text_xml.append(page_xml)
            root = ET.fromstring(page_xml)
            for line in root.iter("Line"):
                box = _apryse_xml_box(line.get("box"), width, height)
                words = [str(word.text or "") for word in line.findall("Word")]
                text = _join_apryse_words(words).strip()
                if not text:
                    continue
                style = _parse_apryse_style(line.get("style") or "")
                block = NormalizedTextBlock(
                    text=text,
                    page_number=page_number,
                    bbox=box,
                    font_family=style.get("font-family"),
                    font_size=_float_or_none(style.get("font-size")),
                    bold=_weight_to_bold(style.get("font-weight") or style.get("font-family")),
                    italic=_style_to_italic(style.get("font-style") or style.get("font-family")),
                    color=style.get("color"),
                    granularity="line",
                )
                blocks.append(block)
            reader.Begin(page)
            while True:
                element = reader.Next()
                if element is None:
                    break
                element_type = element.GetType()
                if element_type == Element.e_image:
                    figure_count += 1
                elif element_type == Element.e_path:
                    graphic_count += 1
            reader.End()
            page_iterator.Next()
            page_number += 1
    finally:
        document.Close()

    evidence = NormalizedLayoutEvidence(
        provider="apryse",
        page_count=page_count,
        full_text="\n".join(block.text for block in blocks),
        text_blocks=blocks,
        figure_count=figure_count,
        graphic_count=graphic_count,
        style_record_count=len(
            {(block.font_family, block.font_size) for block in blocks}
        ),
    )
    return evidence, {
        "provider": "apryse",
        "text_extractor_xml": raw_text_xml,
        "graphic_summary": {"figures": figure_count, "graphics": graphic_count},
        "elements": raw_elements,
    }


def _apryse_xml_box(value: str | None, width: float, height: float) -> NormalizedBox | None:
    if not value:
        return None
    try:
        x, y, box_width, box_height = (float(item.strip()) for item in value.split(",")[:4])
    except (TypeError, ValueError):
        return None
    return _box_from_bottom_left(x, y, x + box_width, y + box_height, width, height)


def _parse_apryse_style(value: str) -> dict[str, str]:
    return {
        key.strip().lower(): raw_value.strip().removesuffix("pt")
        for declaration in value.split(";")
        if ":" in declaration
        for key, raw_value in [declaration.split(":", 1)]
    }


def _join_apryse_words(words: list[str]) -> str:
    no_space_before = {",", ".", ":", ";", ")", "]", "%", "’", "'"}
    no_space_after = {"(", "[", "/", "-", "–", "—", "’", "'"}
    result = ""
    for word in words:
        if not result or word in no_space_before or result[-1:] in no_space_after:
            result += word
        else:
            result += " " + word
    return result


def render_with_libreoffice(docx_path: Path, output_dir: Path) -> Path:
    raise ProviderConfigurationError(
        "LibreOffice rendering was retired by ADR 0006; use the HTML/Adobe lane."
    )


def render_with_aspose(docx_path: Path, output_path: Path) -> Path:
    try:
        import aspose.words as aw
    except ImportError as exc:
        raise ProviderConfigurationError(
            "Aspose.Words is not installed. Install requirements-optional.txt."
        ) from exc

    license_path = os.getenv("ASPOSE_WORDS_LICENSE_PATH")
    metered_public = os.getenv("ASPOSE_WORDS_METERED_PUBLIC_KEY")
    metered_private = os.getenv("ASPOSE_WORDS_METERED_PRIVATE_KEY")
    if license_path:
        license_file = Path(license_path).expanduser()
        if not license_file.is_file():
            raise ProviderConfigurationError(
                f"ASPOSE_WORDS_LICENSE_PATH does not exist: {license_file}"
            )
        aw.License().set_license(str(license_file))
    elif metered_public and metered_private:
        aw.Metered().set_metered_key(metered_public, metered_private)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    document = aw.Document(str(docx_path))
    document.save(str(output_path), aw.SaveFormat.PDF)
    if not output_path.exists():
        raise ProviderCallError("Aspose.Words did not create the expected PDF.")
    return output_path


def render_with_apryse(docx_path: Path, output_path: Path) -> Path:
    license_key = os.getenv("APRYSE_LICENSE_KEY")
    if not license_key:
        raise ProviderConfigurationError("Apryse requires APRYSE_LICENSE_KEY.")
    try:
        from apryse_sdk import Convert, PDFDoc, PDFNet, SDFDoc
    except ImportError as exc:
        raise ProviderConfigurationError(
            "Apryse SDK is not installed. See the bake-off README."
        ) from exc

    _initialize_apryse(PDFNet, license_key)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_doc = PDFDoc()
    try:
        Convert.OfficeToPDF(pdf_doc, str(docx_path), None)
        pdf_doc.Save(str(output_path), SDFDoc.e_linearized)
    finally:
        pdf_doc.Close()
    if not output_path.exists():
        raise ProviderCallError("Apryse did not create the expected PDF.")
    return output_path


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


def render_html_with_apryse(html_path: Path, output_path: Path) -> Path:
    license_key = os.getenv("APRYSE_LICENSE_KEY")
    if not license_key:
        raise ProviderConfigurationError("Apryse requires APRYSE_LICENSE_KEY.")
    try:
        from apryse_sdk import HTML2PDF, PDFDoc, PDFNet, SDFDoc
    except ImportError as exc:
        raise ProviderConfigurationError("Apryse SDK is not installed.") from exc
    _initialize_apryse(PDFNet, license_key)
    if not HTML2PDF.IsModuleAvailable():
        raise ProviderConfigurationError("Apryse HTML2PDF optional module is unavailable.")
    converter = HTML2PDF()
    document = PDFDoc()
    try:
        converter.InsertFromURL(html_path.resolve().as_uri())
        if not converter.Convert(document):
            raise ProviderCallError(f"Apryse HTML2PDF failed: {converter.GetLog()}")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        document.Save(str(output_path), SDFDoc.e_linearized)
    finally:
        document.Close()
        converter.Destroy()
    return output_path


def render_html_with_aspose(html_path: Path, output_path: Path) -> Path:
    try:
        import aspose.words as aw
    except ImportError as exc:
        raise ProviderConfigurationError("Aspose.Words is not installed.") from exc
    output_path.parent.mkdir(parents=True, exist_ok=True)
    document = aw.Document(str(html_path))
    document.save(str(output_path), aw.SaveFormat.PDF)
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise ProviderCallError("Aspose HTML-to-PDF did not create the expected PDF.")
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


def _normalize_azure(raw: dict[str, Any]) -> NormalizedLayoutEvidence:
    result = raw.get("analyzeResult") or raw
    pages = result.get("pages") or []
    page_dimensions: dict[int, tuple[float, float]] = {
        index: (float(page.get("width") or 1), float(page.get("height") or 1))
        for index, page in enumerate(pages, start=1)
    }
    styles = result.get("styles") or []
    blocks: list[NormalizedTextBlock] = []
    normalized_paragraphs: list[NormalizedTextBlock] = []
    paragraphs = result.get("paragraphs") or []
    for paragraph in paragraphs:
        regions = paragraph.get("boundingRegions") or []
        region = regions[0] if regions else {}
        page_number = int(region.get("pageNumber") or 1)
        width, height = page_dimensions.get(page_number, (1, 1))
        style = _azure_style_for_spans(paragraph.get("spans") or [], styles)
        normalized_paragraphs.append(
            NormalizedTextBlock(
                text=str(paragraph.get("content") or ""),
                page_number=page_number,
                bbox=_polygon_box(region.get("polygon"), width, height),
                role=paragraph.get("role"),
                font_family=style.get("similarFontFamily"),
                bold=_weight_to_bold(style.get("fontWeight")),
                italic=_style_to_italic(style.get("fontStyle")),
                color=style.get("color"),
                granularity="paragraph",
            )
        )
    # Geometry comparisons use Azure's lines, never its broader paragraph
    # regions. Paragraphs remain available for semantic-role diagnostics.
    for page_number, page in enumerate(pages, start=1):
        width, height = page_dimensions[page_number]
        for line in page.get("lines") or []:
            blocks.append(
                NormalizedTextBlock(
                    text=str(line.get("content") or ""),
                    page_number=page_number,
                    bbox=_polygon_box(line.get("polygon"), width, height),
                    granularity="line",
                )
            )
    if not blocks:
        # Some synthetic fixtures and older API payloads omit page lines. Keep
        # paragraphs usable while marking their actual granularity explicitly.
        blocks = list(normalized_paragraphs)
    return NormalizedLayoutEvidence(
        provider="azure",
        provider_version=str(raw.get("apiVersion") or result.get("apiVersion") or "2024-11-30"),
        page_count=len(pages),
        full_text=str(result.get("content") or "\n".join(block.text for block in blocks)),
        text_blocks=blocks,
        paragraphs=normalized_paragraphs,
        table_count=len(result.get("tables") or []),
        figure_count=len(result.get("figures") or []),
        style_record_count=len(styles),
    )


def _initialize_apryse(pdfnet: Any, license_key: str) -> None:
    global _APRYSE_INITIALIZED
    if _APRYSE_INITIALIZED:
        return
    pdfnet.Initialize(license_key)
    _APRYSE_INITIALIZED = True


def _normalize_pdfrest(
    raw: dict[str, Any], page_dimensions: dict[int, tuple[float, float]]
) -> NormalizedLayoutEvidence:
    blocks: list[NormalizedTextBlock] = []
    styles: set[tuple[Any, ...]] = set()
    for word in raw.get("words") or []:
        if not isinstance(word, dict) or not word.get("text"):
            continue
        page_number = int(word.get("page") or 1)
        width, height = page_dimensions.get(page_number, (1.0, 1.0))
        style = word.get("style") if isinstance(word.get("style"), dict) else {}
        font = style.get("font") if isinstance(style.get("font"), dict) else {}
        color = style.get("color") if isinstance(style.get("color"), dict) else {}
        font_name = font.get("name")
        font_size = _float_or_none(font.get("size"))
        color_value = _pdfrest_color(color)
        styles.add((font_name, font_size, color_value))
        blocks.append(
            NormalizedTextBlock(
                text=str(word["text"]),
                page_number=page_number,
                bbox=_pdfrest_coordinates_box(word.get("coordinates"), width, height),
                font_family=str(font_name) if font_name else None,
                font_size=font_size,
                bold=_weight_to_bold(font_name),
                italic=_style_to_italic(font_name),
                color=color_value,
                granularity="element",
            )
        )
    full_text = raw.get("fullText")
    if isinstance(full_text, dict):
        full_text = "\n".join(
            str(page.get("text") or "")
            for page in full_text.get("pages") or []
            if isinstance(page, dict)
        )
    return NormalizedLayoutEvidence(
        provider="pdfrest",
        provider_version=str(raw.get("version") or "Extract Text"),
        page_count=max(
            page_dimensions.keys(),
            default=max((block.page_number for block in blocks), default=0),
        ),
        full_text=str(full_text or " ".join(block.text for block in blocks)),
        text_blocks=blocks,
        style_record_count=len(styles),
    )


def _normalize_foxit(
    raw: dict[str, Any], fallback_dimensions: dict[int, tuple[float, float]]
) -> NormalizedLayoutEvidence:
    result = raw.get("analyzeResult") or raw
    page_dimensions = dict(fallback_dimensions)
    for index, page in enumerate(result.get("pages") or [], start=1):
        size = page.get("size") if isinstance(page.get("size"), dict) else {}
        page_number = int(page.get("pageNumber") or index)
        width = _float_or_none(size.get("width"))
        height = _float_or_none(size.get("height"))
        if width and height:
            page_dimensions[page_number] = (width, height)

    blocks: list[NormalizedTextBlock] = []
    styles: set[tuple[Any, ...]] = set()
    table_count = 0
    figure_count = 0
    for element in result.get("elements") or []:
        if not isinstance(element, dict):
            continue
        element_type = str(element.get("type") or "")
        table_count += int(element_type == "table")
        figure_count += int(element_type == "image")
        content = element.get("content") if isinstance(element.get("content"), dict) else {}
        text = content.get("text") or _foxit_table_text(content)
        if not text:
            continue
        style = content.get("style") if isinstance(content.get("style"), dict) else {}
        region = element.get("region") if isinstance(element.get("region"), dict) else {}
        page_number = int(region.get("page") or 1)
        width, height = page_dimensions.get(page_number, (1.0, 1.0))
        font_name = style.get("fontFamilyName")
        font_size = _float_or_none(style.get("fontSize"))
        color = style.get("fontColor") or style.get("color")
        styles.add((font_name, font_size, str(color), style.get("fontWeight")))
        blocks.append(
            NormalizedTextBlock(
                text=str(text),
                page_number=page_number,
                bbox=_polygon_box(region.get("boundingBox"), width, height),
                role=element_type or None,
                font_family=str(font_name) if font_name else None,
                font_size=font_size,
                bold=_weight_to_bold(style.get("fontWeight") or font_name),
                italic=_style_to_italic(style.get("fontStyle") or font_name),
                color=str(color) if color is not None else None,
                granularity="element",
            )
        )
    version = result.get("version") if isinstance(result.get("version"), dict) else {}
    return NormalizedLayoutEvidence(
        provider="foxit_structural",
        provider_version=str(version.get("schema") or "unknown"),
        page_count=max(page_dimensions.keys(), default=0),
        full_text="\n".join(block.text for block in blocks),
        text_blocks=blocks,
        paragraphs=[block for block in blocks if block.role in {"title", "head", "paragraph"}],
        table_count=table_count,
        figure_count=figure_count,
        style_record_count=len(styles),
    )


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


def _foxit_structure_json_from_zip(zip_bytes: bytes) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
            candidates = [
                name
                for name in archive.namelist()
                if Path(name).name.lower() == "structureinfo.json"
            ]
            if not candidates:
                raise ProviderCallError("Foxit result ZIP omitted StructureInfo.json.")
            return json.loads(archive.read(candidates[0]).decode("utf-8"))
    except (zipfile.BadZipFile, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProviderCallError(
            "Foxit result could not be parsed as a Structural Extraction ZIP."
        ) from exc


def _pdf_page_dimensions(pdf_path: Path) -> dict[int, tuple[float, float]]:
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    return {
        index: (float(page.mediabox.width), float(page.mediabox.height))
        for index, page in enumerate(reader.pages, start=1)
    }


def _pdfrest_coordinates_box(
    coordinates: Any, width: float, height: float
) -> NormalizedBox | None:
    if not isinstance(coordinates, dict):
        return None
    points = [
        point
        for key in ("topLeft", "topRight", "bottomLeft", "bottomRight")
        if isinstance((point := coordinates.get(key)), dict)
    ]
    if not points:
        return None
    try:
        xs = [float(point["x"]) for point in points]
        ys = [float(point["y"]) for point in points]
    except (KeyError, TypeError, ValueError):
        return None
    return _box_from_bottom_left(min(xs), min(ys), max(xs), max(ys), width, height)


def _pdfrest_color(color: Any) -> str | None:
    if not isinstance(color, dict):
        return None
    values = color.get("values")
    if not isinstance(values, list):
        return None
    return f"{color.get('space') or 'unknown'}({','.join(str(value) for value in values)})"


def _foxit_table_text(content: dict[str, Any]) -> str:
    body = content.get("body") if isinstance(content.get("body"), dict) else {}
    cells = body.get("cells") if isinstance(body.get("cells"), list) else []
    text_parts: list[str] = []
    for cell in cells:
        if not isinstance(cell, dict):
            continue
        paragraph = cell.get("paragraph") if isinstance(cell.get("paragraph"), dict) else {}
        paragraph_content = (
            paragraph.get("content") if isinstance(paragraph.get("content"), dict) else {}
        )
        if paragraph_content.get("text"):
            text_parts.append(str(paragraph_content["text"]))
    return " ".join(text_parts)


def _azure_style_for_spans(
    paragraph_spans: list[dict[str, Any]], styles: list[dict[str, Any]]
) -> dict[str, Any]:
    merged_style: dict[str, Any] = {}
    for paragraph_span in paragraph_spans:
        paragraph_start = int(paragraph_span.get("offset") or 0)
        paragraph_end = paragraph_start + int(paragraph_span.get("length") or 0)
        for style in styles:
            for style_span in style.get("spans") or []:
                style_start = int(style_span.get("offset") or 0)
                style_end = style_start + int(style_span.get("length") or 0)
                if max(paragraph_start, style_start) < min(paragraph_end, style_end):
                    for key, value in style.items():
                        if key not in {"spans", "confidence"} and value is not None:
                            merged_style[key] = value
                    break
    return merged_style


def _polygon_box(polygon: Any, width: float, height: float) -> NormalizedBox | None:
    if not isinstance(polygon, list) or len(polygon) < 8:
        return None
    try:
        xs = [float(polygon[index]) for index in range(0, len(polygon), 2)]
        ys = [float(polygon[index]) for index in range(1, len(polygon), 2)]
    except (TypeError, ValueError):
        return None
    return _box_from_top_left(min(xs), min(ys), max(xs), max(ys), width, height)


def _box_from_top_left(
    x0: float, top: float, x1: float, bottom: float, width: float, height: float
) -> NormalizedBox:
    return NormalizedBox(
        x0=_clamp(min(x0, x1) / max(width, 1e-9)),
        top=_clamp(min(top, bottom) / max(height, 1e-9)),
        x1=_clamp(max(x0, x1) / max(width, 1e-9)),
        bottom=_clamp(max(top, bottom) / max(height, 1e-9)),
    )


def _box_from_bottom_left(
    x0: float, y0: float, x1: float, y1: float, width: float, height: float
) -> NormalizedBox:
    low_x, high_x = min(x0, x1), max(x0, x1)
    low_y, high_y = min(y0, y1), max(y0, y1)
    return _box_from_top_left(low_x, height - high_y, high_x, height - low_y, width, height)


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
        "AZURE_DOCUMENT_INTELLIGENCE_KEY",
        "ADOBE_PDF_SERVICES_CLIENT_SECRET",
        "PDFREST_API_KEY",
        "FOXIT_PDF_SERVICES_CLIENT_ID",
        "FOXIT_PDF_SERVICES_CLIENT_SECRET",
        "APRYSE_LICENSE_KEY",
        "ASPOSE_WORDS_METERED_PRIVATE_KEY",
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


def _first_value(mapping: Any, *keys: str) -> Any:
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        if mapping.get(key) is not None:
            return mapping[key]
    return None


def _float_or_none(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _weight_to_bold(value: Any) -> bool | None:
    if value is None:
        return None
    text = str(value).lower()
    if any(token in text for token in ("bold", "black", "heavy", "semibold", "700", "800", "900")):
        return True
    if any(token in text for token in ("normal", "regular", "400")):
        return False
    return None


def _style_to_italic(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).lower()
    if "italic" in text or "oblique" in text:
        return True
    if "normal" in text or "regular" in text or text == "false":
        return False
    return None
