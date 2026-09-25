"""Test-only deterministic LLM double for synthetic resume fixtures."""

import json
import re
from typing import Any

from pydantic import BaseModel

from app.extraction.llm_extractor import LLMClient

class MockLLMClient(LLMClient):
    """Deterministic extractor for tests and offline demo with synthetic resumes."""

    def complete_json(
        self,
        _system_prompt: str,
        user_prompt: str,
        *,
        response_model: type[BaseModel] | None = None,
    ) -> str:
        self.last_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        self.total_usage = dict(self.last_usage)
        self.request_count = getattr(self, "request_count", 0) + 1
        if _is_segmentation_request(user_prompt, response_model):
            resume_text = _extract_resume_for_segmentation(user_prompt)
            payload = _mock_segmentation_payload(resume_text)
            return json.dumps(payload)
        resume_text = user_prompt.split("Resume text:", 1)[-1]
        sections = _sections_by_heading(resume_text)
        work_experience = _work_experience_from_sections(sections)
        profile: dict[str, Any] = {
            "full_name": _first_nonempty_line(resume_text),
            "email": _first_match(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", resume_text),
            "phone": _first_match(r"(?:\+?\d[\d\s().-]{7,}\d)", resume_text),
            "location": _value_after_label("Location", resume_text) or _value_after_label("Address", resume_text),
            "linkedin_url": _first_match(r"https?://(?:www\.)?linkedin\.com/[^\s)]+", resume_text),
            "portfolio_url": _first_match(r"https?://(?:github\.com|[\w.-]+\.[A-Za-z]{2,})/[^\s)]+", resume_text),
            "professional_summary": _section_text(sections, "professional_summary")
            or _section_after_heading("Summary", resume_text),
            "skills": _comma_section("Skills", resume_text) or _list_section(sections, "skills"),
            "languages": [{"name": lang} for lang in (_comma_section("Languages", resume_text) or _list_section(sections, "languages"))],
            "work_experience": work_experience,
            "education": _education_from_sections(sections),
            "certifications": _certifications_from_sections(sections),
            "salary_expectation": _value_after_label("Salary expectation", resume_text),
            "notice_period": _value_after_label("Notice period", resume_text),
            "work_authorization": _value_after_label("Work authorization", resume_text),
            "interview_availability": _value_after_label("Interview availability", resume_text),
            "missing_fields": [],
            "client_display_rules": {},
        }
        return json.dumps(profile)

SECTION_HEADINGS: dict[str, str] = {
    "summary": "professional_summary",
    "professional summary": "professional_summary",
    "skills": "skills",
    "languages": "languages",
    "experience": "experience",
    "work experience": "experience",
    "employment history": "experience",
    "education": "education",
    "certifications": "certifications",
    "certification": "certifications",
    "projects": "projects",
    "achievements": "achievements",
}


def _first_nonempty_line(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return None


def _first_match(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text, re.IGNORECASE)
    return match.group(0).strip() if match else None


def _value_after_label(label: str, text: str) -> str | None:
    match = re.search(rf"^{re.escape(label)}\s*:\s*(.+)$", text, re.IGNORECASE | re.MULTILINE)
    return match.group(1).strip() if match else None


def _section_after_heading(heading: str, text: str) -> str | None:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip().lower() == heading.lower() and index + 1 < len(lines):
            value = lines[index + 1].strip()
            return value or None
    return None


def _comma_section(heading: str, text: str) -> list[str]:
    value = _value_after_label(heading, text)
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _sections_by_heading(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    active_section: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        heading = SECTION_HEADINGS.get(line.rstrip(":").lower())
        if heading:
            active_section = heading
            sections.setdefault(active_section, [])
            continue
        if active_section:
            sections[active_section].append(line)
    return sections


def _section_text(sections: dict[str, list[str]], section_name: str) -> str | None:
    lines = [_clean_list_item(line) for line in sections.get(section_name, [])]
    present_lines = [line for line in lines if line]
    return " ".join(present_lines) if present_lines else None


def _list_section(sections: dict[str, list[str]], section_name: str) -> list[str]:
    items: list[str] = []
    for line in sections.get(section_name, []):
        for part in re.split(r",|;|\|", line):
            item = _clean_list_item(part)
            if item:
                items.append(item)
    return items


def _work_experience_from_sections(sections: dict[str, list[str]]) -> list[dict[str, Any]]:
    experience_lines = sections.get("experience", [])
    if not experience_lines:
        return []

    first_line = _clean_list_item(experience_lines[0])
    match = re.match(
        r"(?P<title>.+?)\s+at\s+(?P<company>.+?)(?:\s*\((?P<dates>[^)]+)\))?$",
        first_line,
        re.IGNORECASE,
    )
    if match:
        title = match.group("title").strip()
        company = match.group("company").strip()
        start_date, end_date = _split_date_range(match.group("dates"))
        description = [_clean_list_item(line) for line in experience_lines[1:]]
    else:
        title = first_line or None
        company = None
        start_date = None
        end_date = None
        description = [_clean_list_item(line) for line in experience_lines[1:]]

    description.extend(f"Project: {item}" for item in _list_section(sections, "projects"))
    description.extend(f"Achievement: {item}" for item in _list_section(sections, "achievements"))

    return [
        {
            "company": company,
            "title": title,
            "location": None,
            "start_date": start_date,
            "end_date": end_date,
            "description": [item for item in description if item],
        }
    ]


def _education_from_sections(sections: dict[str, list[str]]) -> list[dict[str, str | None]]:
    education: list[dict[str, str | None]] = []
    for line in sections.get("education", []):
        value = _clean_list_item(line)
        if not value:
            continue
        date_match = re.search(r"\((?P<date>[^)]+)\)\s*$", value)
        end_date = date_match.group("date").strip() if date_match else None
        without_date = re.sub(r"\s*\([^)]+\)\s*$", "", value).strip()
        parts = re.split(r"\s+[–—-]\s+", without_date, maxsplit=1)
        degree = parts[0].strip() if parts else without_date
        institution = parts[1].strip() if len(parts) > 1 else None
        education.append(
            {
                "institution": institution,
                "degree": degree or None,
                "field_of_study": None,
                "start_date": None,
                "end_date": end_date,
            }
        )
    return education


def _certifications_from_sections(sections: dict[str, list[str]]) -> list[dict[str, str | None]]:
    certifications: list[dict[str, str | None]] = []
    for line in sections.get("certifications", []):
        value = _clean_list_item(line)
        if value:
            certifications.append({"name": value, "issuer": None, "date": None})
    return certifications


def _split_date_range(value: str | None) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    parts = [part.strip() for part in re.split(r"\s+(?:-|–|—|to)\s+", value, maxsplit=1)]
    if len(parts) == 1:
        return parts[0], None
    return parts[0] or None, parts[1] or None


def _clean_list_item(value: str) -> str:
    return re.sub(r"^[-•*]\s*", "", value.strip()).strip()


# --- llm_segmentation/1 mock support (deterministic heading-regex) ---

_SEGMENT_HEADING_TO_TYPE: dict[str, str] = {
    "summary": "summary",
    "professional summary": "summary",
    "skills": "skills",
    "languages": "languages",
    "experience": "work_experience",
    "work experience": "work_experience",
    "employment history": "work_experience",
    "education": "education",
    "certifications": "certifications",
    "certification": "certifications",
    "projects": "projects",
    "achievements": "awards",
    "awards": "awards",
    "publications": "publications",
    "volunteering": "volunteering",
    "interests": "interests",
    # Authorized-corpus headings: the accepted A/B/C nine-section inventory
    # (additional sections -> `other` pending review; contact-only link/data
    # block -> contact) so the deterministic mock reproduces the canonical
    # structure the measured matrix expects.
    "additional sections": "other",
    "additional links or data": "contact",
}

_ADDITIONAL_SEGMENT_TYPES = {"projects", "awards", "publications", "volunteering", "interests", "other"}


def _is_segmentation_request(user_prompt: str, response_model: type[BaseModel] | None) -> bool:
    if response_model is not None and getattr(response_model, "__name__", "") == "LLMSegmentationOutput":
        return True
    return "SOURCE LINES JSON" in user_prompt and "<<<RESUME>>>" in user_prompt


def _extract_resume_for_segmentation(user_prompt: str) -> str:
    if "<<<RESUME>>>" in user_prompt and "<<<END>>>" in user_prompt:
        start = user_prompt.index("<<<RESUME>>>") + len("<<<RESUME>>>")
        end = user_prompt.index("<<<END>>>", start)
        return user_prompt[start:end].strip("\n")
    if "Resume text:" in user_prompt:
        return user_prompt.split("Resume text:", 1)[-1]
    return user_prompt


def _mock_segmentation_payload(resume_text: str) -> dict[str, Any]:
    lines_with_content: list[str] = []
    for raw in resume_text.splitlines():
        if raw.strip():
            lines_with_content.append(raw.strip())
    # Deterministic heading-regex splitting reusing SECTION_HEADINGS spirit.
    sections_raw: list[dict[str, Any]] = []
    # First section is contact until first recognized heading.
    current_heading: str | None = None
    current_type: str = "contact"
    current_items: list[str] = []

    def flush():
        nonlocal current_heading, current_type, current_items
        if current_heading is None and not current_items and sections_raw:
            return
        if current_heading is None and not current_items and not sections_raw:
            # Keep empty contact from being flushed as invalid; will be handled after loop.
            return
        if not current_items and current_heading is not None:
            # Headings with no items would violate min_length=1; skip empty heading
            # (should not happen for synthetic resumes). Treat heading as item of previous.
            if sections_raw:
                sections_raw[-1]["items"].append(current_heading)  # type: ignore[union-attr]
            else:
                current_items.append(current_heading)  # type: ignore[arg-type]
                sections_raw.append({"heading": None, "section_type": current_type, "items": list(current_items)})
            current_heading = None
            current_type = "contact"
            current_items = []
            return
        sections_raw.append({"heading": current_heading, "section_type": current_type, "items": list(current_items)})
        current_heading = None
        current_items = []

    for line in lines_with_content:
        normalized = line.rstrip(":").strip().lower()
        mapped = _SEGMENT_HEADING_TO_TYPE.get(normalized)
        if mapped is not None:
            # Heading line
            if current_items or current_heading is not None or not sections_raw:
                # Flush previous section if it has content; for initial contact also flush
                if current_items or current_heading is not None:
                    flush()
                elif not sections_raw and current_heading is None and not current_items:
                    # Edge: first heading immediately after start with empty contact — create empty contact only if needed
                    # Instead start new section directly; contact will be implicit if no leading items.
                    pass
            current_heading = line
            current_type = mapped
            current_items = []
        else:
            # Content line
            if not sections_raw and current_heading is None and current_type == "contact":
                current_items.append(line)
            elif current_heading is not None or sections_raw or current_type != "contact":
                current_items.append(line)
            else:
                current_items.append(line)
    # Flush last
    if current_heading is not None or current_items:
        # Ensure contact exists even if no heading was found
        if not sections_raw and current_heading is None:
            sections_raw.append({"heading": None, "section_type": "contact", "items": list(current_items)})
        elif current_heading is not None or current_items:
            flush()
    # If no sections (empty resume), create empty contact to satisfy min_length
    if not sections_raw:
        sections_raw.append({"heading": None, "section_type": "contact", "items": ["Untitled"]})
    # Ensure first section is contact when leading content existed
    # If first section is not contact but we had leading lines, the loop already handled it.
    # Build structured content per section
    enriched: list[dict[str, Any]] = []
    for sec in sections_raw:
        heading = sec["heading"]
        s_type = sec["section_type"]
        items: list[str] = sec["items"]
        entry: dict[str, Any] = {"heading": heading, "section_type": s_type, "items": items}
        # Per-section structured fields
        if s_type == "contact":
            contact_text = "\n".join(items)
            # Use same helpers as profile extraction but scoped to contact items + full resume for robustness
            full_text = resume_text
            entry["full_name"] = _first_nonempty_line(full_text)
            email = _first_match(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", contact_text) or _first_match(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", full_text)
            if email:
                entry["email"] = email
            phone = _first_match(r"(?:\+?\d[\d\s().-]{7,}\d)", contact_text) or _first_match(r"(?:\+?\d[\d\s().-]{7,}\d)", full_text)
            if phone:
                entry["phone"] = phone
            loc = _value_after_label("Location", contact_text) or _value_after_label("Address", contact_text) or _value_after_label("Location", full_text) or _value_after_label("Address", full_text)
            if loc:
                entry["location"] = loc
            li = _first_match(r"https?://(?:www\.)?linkedin\.com/[^\s)]+", contact_text) or _first_match(r"https?://(?:www\.)?linkedin\.com/[^\s)]+", full_text)
            if li:
                entry["linkedin_url"] = li
            pf = _first_match(r"https?://(?:github\.com|[\w.-]+\.[A-Za-z]{2,})/[^\s)]+", contact_text) or _first_match(r"https?://(?:github\.com|[\w.-]+\.[A-Za-z]{2,})/[^\s)]+", full_text)
            if pf:
                entry["portfolio_url"] = pf
            # Inline Skills/Languages that live in contact as "Skills: ..." must still populate structured fields
            # (verbatim line stays as item in contact; structured fields copy the extracted values).
            inline_skills = _comma_section("Skills", contact_text) or _comma_section("Skills", full_text)
            if inline_skills:
                entry["skills"] = inline_skills
            inline_langs = _comma_section("Languages", contact_text) or _comma_section("Languages", full_text)
            if inline_langs:
                entry["languages"] = [{"name": lang} for lang in inline_langs]
            # Also capture narrative summary lines that live in contact (LLM-first prompt keeps them there)
            # No extra professional_summary needed for contact.
        elif s_type == "summary":
            entry["professional_summary"] = " ".join(_clean_list_item(i) for i in items if _clean_list_item(i))
        elif s_type == "skills":
            skills = _skills_from_items(items)
            entry["skills"] = skills
            groups = _skill_groups_from_items(items)
            if groups:
                entry["skill_groups"] = groups
        elif s_type == "languages":
            langs = _languages_from_items(items)
            entry["languages"] = langs
        elif s_type == "work_experience":
            entry["work_experience"] = _work_experience_from_items(items)
        elif s_type == "education":
            entry["education"] = _education_from_items(items)
        elif s_type == "certifications":
            entry["certifications"] = _certifications_from_items(items)
        elif s_type in _ADDITIONAL_SEGMENT_TYPES:
            entry["additional_entries"] = _additional_entries_from_items(items)
        enriched.append(entry)
    return {"schema_version": "llm_segmentation/1", "sections": enriched}


def _skills_from_items(items: list[str]) -> list[str]:
    result: list[str] = []
    for line in items:
        if ":" in line and not line.strip().lower().startswith(("http:", "https:")):
            _, after = line.split(":", 1)
            parts = [p.strip() for p in re.split(r",|;|\|", after) if p.strip()]
            for p in parts:
                cleaned = _clean_list_item(p)
                if cleaned:
                    result.append(cleaned)
        else:
            for part in re.split(r",|;|\|", line):
                cleaned = _clean_list_item(part)
                if cleaned:
                    result.append(cleaned)
    return result


def _skill_groups_from_items(items: list[str]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for item in items:
        if ":" not in item or item.strip().lower().startswith(("http:", "https:")):
            continue
        label, values = item.split(":", 1)
        skills = [v.strip() for v in values.split(",") if v.strip()]
        if label.strip() and skills:
            groups.append({"label": label.strip(), "skills": skills})
    return groups


def _languages_from_items(items: list[str]) -> list[dict[str, Any]]:
    langs: list[str] = []
    for line in items:
        for part in re.split(r",|;|\|", line):
            cleaned = _clean_list_item(part)
            if cleaned:
                langs.append(cleaned)
    return [{"name": l} for l in langs]


def _work_experience_from_items(items: list[str]) -> list[dict[str, Any]]:
    if not items:
        return []
    first = _clean_list_item(items[0])
    m = re.match(r"(?P<title>.+?)\s+at\s+(?P<company>.+?)(?:\s*\((?P<dates>[^)]+)\))?$", first, re.IGNORECASE)
    if m:
        title = m.group("title").strip()
        company = m.group("company").strip()
        start, end = _split_date_range(m.group("dates"))
        desc = [_clean_list_item(l) for l in items[1:] if _clean_list_item(l)]
    else:
        title = first or None
        company = None
        start = end = None
        desc = [_clean_list_item(l) for l in items[1:] if _clean_list_item(l)]
    return [{"company": company, "title": title, "location": None, "start_date": start, "end_date": end, "description": [d for d in desc if d]}]


def _education_from_items(items: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in items:
        v = _clean_list_item(line)
        if not v:
            continue
        dm = re.search(r"\((?P<date>[^)]+)\)\s*$", v)
        end = dm.group("date").strip() if dm else None
        without = re.sub(r"\s*\([^)]+\)\s*$", "", v).strip()
        parts = re.split(r"\s+[–—-]\s+", without, maxsplit=1)
        degree = parts[0].strip() if parts else without
        inst = parts[1].strip() if len(parts) > 1 else None
        out.append({"institution": inst, "degree": degree or None, "field_of_study": None, "start_date": None, "end_date": end})
    return out


def _certifications_from_items(items: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in items:
        v = _clean_list_item(line)
        if v:
            out.append({"name": v, "issuer": None, "date": None})
    return out


def _additional_entries_from_items(items: list[str]) -> list[dict[str, Any]]:
    """Split a section's lines into AdditionalSectionEntry-shaped dicts.

    Matches the llm_segmentation/1 prompt contract: links go to the metadata
    tier, bullet items to the description tier, and a plain line after links
    or bullets starts a new entry title (so PROJECTS yields one entry per
    project, as the deterministic segmenter used to)."""

    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in items:
        stripped = line.strip()
        if not stripped:
            continue
        is_bullet = bool(re.match(r"^(?:[-*•▪◦]|\d+[.)])\s*", stripped))
        cleaned = _clean_list_item(stripped)
        if re.match(r"^(?:https?://|www\.)", cleaned, re.IGNORECASE):
            if current is None:
                current = {"links": []}
                entries.append(current)
            current.setdefault("links", []).append(
                cleaned if "://" in cleaned else f"https://{cleaned}"
            )
        elif is_bullet:
            if current is None:
                current = {"description": []}
                entries.append(current)
            current.setdefault("description", []).append(cleaned)
        elif current is None:
            current = {"title": cleaned}
            entries.append(current)
        elif current.get("description"):
            current = {"title": cleaned}
            entries.append(current)
        else:
            current.setdefault("description", []).append(cleaned)
    return entries

