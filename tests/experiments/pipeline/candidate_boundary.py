"""Candidate-content shape and reviewer-boundary checks for Pipeline stage 5."""

from __future__ import annotations

import re
from typing import Any

from tests.experiments.pipeline.shared import PresentationLabel
from tests.experiments.resume_layout.candidates import UnroutableContent

_SECTION_ROLE_ALIASES = {
    "contact": "contact", "contact info": "contact",
    "about me": "summary", "profile": "summary", "summary": "summary",
    "professional summary": "summary",
    "experience": "work_experience", "work experience": "work_experience",
    "business experience": "work_experience",
    "education": "education", "skills": "skills", "languages": "languages",
    "certifications": "certifications",
}


def _normalized_heading(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def _candidate_review_scope(candidate: Any, labels: list[PresentationLabel]) -> dict[str, Any]:
    """Content-free scope of candidate roles and target-only section labels."""
    available = {section.source for section in candidate.sections}
    if any(leaf.kind == "header_field" for leaf in candidate.leaves):
        available.add("contact")
    candidate_headings = {
        _normalized_heading(section.heading or "")
        for section in candidate.sections if section.heading
    }
    unavailable = []
    for label in labels:
        if label.kind != "section_heading":
            continue
        normalized = _normalized_heading(label.text)
        role = _SECTION_ROLE_ALIASES.get(normalized)
        if normalized not in candidate_headings and (role is None or role not in available):
            unavailable.append(label.text)
    leaf_kinds_by_role: dict[str, set[str]] = {}
    for leaf in candidate.leaves:
        role = "contact" if leaf.kind == "header_field" else leaf.source
        if role:
            leaf_kinds_by_role.setdefault(role, set()).add(leaf.kind)
    content_kinds_by_role: dict[str, set[str]] = {}
    for section in candidate.sections:
        content_kinds_by_role.setdefault(section.source, set()).add(section.content_kind)
    return {
        "verified_split_name_available": bool(
            candidate.profile
            and candidate.profile.full_name
            and candidate.profile.given_name
            and candidate.profile.family_name
            and f"{candidate.profile.given_name} {candidate.profile.family_name}" == candidate.profile.full_name
        ),
        "candidate_only_skills_heading_available": any(
            section.source == "skills" and section.heading
            for section in candidate.sections
        ) and not any(
            _SECTION_ROLE_ALIASES.get(_normalized_heading(label.text)) == "skills"
            for label in labels if label.kind == "section_heading"
        ),
        "available_section_roles": sorted(available),
        "content_kinds_by_role": {
            role: sorted(kinds) for role, kinds in sorted(content_kinds_by_role.items())
        },
        "available_leaf_kinds_by_role": {
            role: sorted(kinds) for role, kinds in sorted(leaf_kinds_by_role.items())
        },
        "has_language_proficiency_entries": any(
            leaf.kind == "language" for leaf in candidate.leaves
        ),
        "skills_only_skill_group": set(leaf_kinds_by_role.get("skills", ())) == {"skill_group"},
        "target_labels_without_candidate_content": unavailable,
    }


def _slot_shape_contract() -> dict[str, Any]:
    """Content-free description of the authored shell's existing bindings."""
    return {
        "candidate:name": {"type": "string", "atomic": True},
        "candidate:given_name": {"type": "string", "source": "CandidateProfile.given_name", "requires": "both_verified_parts"},
        "candidate:family_name": {"type": "string", "source": "CandidateProfile.family_name", "requires": "both_verified_parts"},
        "candidate:contact": {
            "type": "string", "shape": "flat_shell_joined_string",
            "cannot_reconstruct": "label_value_table",
        },
        "candidate:skills_heading": {
            "type": "string", "source": "CandidateDocument.skills.heading",
            "use": "candidate-only skills section when no target skills heading is mapped",
        },
        "experience": {
            "item_head": "work_entry", "item_detail": "entry_detail",
            "item_meta": "entry_meta",
            "item_bullets": {
                "leaf_kind": "work_bullet", "dom": "shell_generated_li",
                "text_owns_bullet_glyph": True,
            },
        },
        "education": {
            "item_head": "education_entry", "item_detail": "entry_detail",
            "item_meta": "entry_meta",
        },
        "skills": {
            "item": "skill_group", "item_head": "skill_group",
            "item_text": "skill_children_only",
        },
    }


def _length_band(length: int) -> str:
    return "short" if length < 80 else "medium" if length < 240 else "long"


def _candidate_layout_footprint(candidate: Any) -> dict[str, Any]:
    """Counts and lengths only; never candidate values."""
    by_id = {leaf.leaf_id: leaf for leaf in candidate.leaves}
    result: dict[str, Any] = {}
    for section in candidate.sections:
        section_leaves = [by_id[leaf_id] for leaf_id in section.leaf_ids]
        roots = [leaf for leaf in section_leaves if leaf.parent_leaf_id is None]
        items = []
        for root in roots:
            children = [leaf for leaf in section_leaves if leaf.parent_leaf_id == root.leaf_id]
            length = sum(len(leaf.text or "") for leaf in [root, *children])
            items.append({
                "child_count": len(children),
                "bullet_count": sum(child.kind == "work_bullet" for child in children),
                "character_length": length,
                "length_band": _length_band(length),
            })
        total = sum(item["character_length"] for item in items)
        result[section.source] = {
            "entry_count": len(roots), "items": items,
            "total_character_length": total,
            "may_cross_page": total > 1800 or len(roots) > 4,
        }
    return result


def _candidate_compatible_labels(
    labels: list[PresentationLabel], review_scope: dict[str, Any]
) -> dict[str, str]:
    unavailable = set(review_scope.get("target_labels_without_candidate_content", []))
    return {
        label.text: (
            "omit_entire_section" if label.text in unavailable
            else _SECTION_ROLE_ALIASES.get(
                _normalized_heading(label.text), "candidate_compatible"
            )
        )
        for label in labels if label.kind == "section_heading"
    }


def _apply_header_overflow_disposition(candidate: Any, state: Any) -> Any:
    """Generic E3 shell transition, shared by the main render and every
    content-shape probe: header fields with no measured home in the compiled
    state route through the EXPLICIT candidate-only header-overflow node
    (nothing silently dropped, nothing invented)."""

    header_slots_in_state = {
        slot for node in state.nodes if node.kind == "header_row" for slot in node.slots
    }
    unhomed = [
        leaf for leaf in candidate.leaves
        if leaf.kind == "header_field" and leaf.slot not in header_slots_in_state
    ]
    if not unhomed:
        return candidate
    return candidate.model_copy(
        update={
            "unroutable": [
                *candidate.unroutable,
                *(
                    UnroutableContent(
                        text=leaf.text or "",
                        reason=(
                            "the compiled target header region carries no measured "
                            f"row with slot {leaf.slot!r}; routes through the explicit "
                            "candidate-only header-overflow node"
                        ),
                        slot=leaf.slot,
                    )
                    for leaf in unhomed
                ),
            ]
        }
    )
