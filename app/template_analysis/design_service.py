"""Design lifecycle service.

Chooses the configured designer, runs strict validation and deterministic
compilation, persists design artifacts under the existing artifact-store
boundary (filesystem; SQLite is not expanded), and manages approval and
superseding. Live Claude use stays disabled by default because ``ProviderPolicy``
does not exist yet.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.template_analysis.design_cache import (
    DesignCache,
    DesignCacheKey,
    default_cache_dir,
    evidence_checksum,
)
from app.template_analysis.design_compiler import (
    DesignCompilerError,
    UnsupportedPlacementError,
    compile_layout_template_spec,
    proposal_checksum,
)
from app.template_analysis.design_evidence import (
    build_design_request,
    inventory_sha256,
)
from app.template_analysis.design_schemas import (
    APPROVABLE_DESIGN_STATES,
    DESIGN_COMPILER_VERSION,
    DESIGN_REQUEST_SCHEMA_VERSION,
    CandidateSectionRole,
    DesignArtifactState,
    DesignDecisionTrace,
    DesignerCallMetadata,
    DesignProposal,
    DesignRequest,
    DesignValidationResult,
    MappingPlan,
    TargetLayoutEvidence,
)
from app.template_analysis.design_validator import validate_design_proposal
from app.template_analysis.designer import (
    AnthropicClaudeDesigner,
    DesignerError,
    DesignerInvalidProposal,
    DesignerLiveDisabled,
    DesignerNotConfigured,
    DesignerProviderCallError,
    MockTemplateDesigner,
    OpenAITemplateDesigner,
    TemplateDesigner,
)
from app.template_analysis.schemas import TemplateStyleSpec

DESIGN_STRATEGY_EXISTING = "existing_template"
DESIGN_STRATEGY_MEASURED = "measured_template"
DESIGN_STRATEGY_CLAUDE = "claude_designer"
DESIGN_STRATEGY_OPENAI = "openai_designer"
SUPPORTED_STRATEGIES = (
    DESIGN_STRATEGY_MEASURED,
    DESIGN_STRATEGY_EXISTING,
    DESIGN_STRATEGY_CLAUDE,
    DESIGN_STRATEGY_OPENAI,
)

DESIGNER_DETERMINISTIC = "deterministic"
DESIGNER_CLAUDE = "claude"
DESIGNER_OPENAI = "openai"
SUPPORTED_DESIGNERS = (DESIGNER_DETERMINISTIC, DESIGNER_CLAUDE, DESIGNER_OPENAI)


class DesignServiceError(RuntimeError):
    """Raised for invalid design workflow operations."""


def get_design_strategy() -> str:
    value = os.getenv("TEMPLATE_DESIGN_STRATEGY", DESIGN_STRATEGY_MEASURED).strip().lower()
    return value if value in SUPPORTED_STRATEGIES else DESIGN_STRATEGY_MEASURED


class DesignService:
    def __init__(
        self,
        *,
        strategy: str | None = None,
        cache: DesignCache | None = None,
        designer: TemplateDesigner | None = None,
        claude_designer: AnthropicClaudeDesigner | None = None,
        openai_designer: OpenAITemplateDesigner | None = None,
    ) -> None:
        self.strategy = strategy or get_design_strategy()
        self.cache = cache or DesignCache(default_cache_dir())
        self._designer_override = designer
        self._claude_designer = claude_designer or AnthropicClaudeDesigner()
        self._openai_designer = openai_designer or OpenAITemplateDesigner()

    # -- public API -----------------------------------------------------------

    def create_design(
        self,
        *,
        artifact_id: str,
        artifact_dir: str | Path,
        evidence: TargetLayoutEvidence,
        candidate_sections: list[CandidateSectionRole],
        style_spec: TemplateStyleSpec,
        designer: str | None = None,
        organization_policy_ref: str | None = None,
        profile_sha256: str | None = None,
        approved_profile_version_id: str | None = None,
        retry_count: int = 0,
    ) -> DesignArtifactState:
        resolved_designer_name = designer or self._default_designer_name()
        if resolved_designer_name not in SUPPORTED_DESIGNERS:
            raise DesignServiceError(
                f"Unknown designer '{resolved_designer_name}'. "
                f"Supported: {', '.join(SUPPORTED_DESIGNERS)}."
            )
        design_id = f"design_{uuid.uuid4().hex}"
        design_dir = Path(artifact_dir) / "designs" / design_id
        design_dir.mkdir(parents=True, exist_ok=True)

        request = build_design_request(
            evidence,
            candidate_sections,
            organization_policy_ref=organization_policy_ref,
        )
        designer_instance = self._resolve_designer(resolved_designer_name)
        cache_key = self._cache_key(resolved_designer_name, designer_instance, request, evidence)

        started = time.perf_counter()
        proposal: DesignProposal | None = None
        metadata: DesignerCallMetadata | None = None
        safe_error_code: str | None = None
        error_message: str | None = None

        cached = self.cache.get(cache_key)
        if cached is not None:
            cached_request, proposal, cached_trace = cached
            request = cached_request
        else:
            try:
                proposal = designer_instance.design(request)
            except DesignerLiveDisabled as exc:
                safe_error_code = "live_design_disabled"
                error_message = str(exc)
            except DesignerNotConfigured as exc:
                safe_error_code = "not_configured"
                error_message = str(exc)
            except DesignerProviderCallError as exc:
                safe_error_code = "provider_call_failed"
                error_message = str(exc)
            except DesignerInvalidProposal as exc:
                safe_error_code = "invalid_proposal"
                error_message = str(exc)
            except DesignerError as exc:
                safe_error_code = "designer_failed"
                error_message = str(exc)
            except Exception as exc:  # defensive: internal designer bug
                safe_error_code = "internal_error"
                error_message = f"{type(exc).__name__}: {exc}"

        latency = round(time.perf_counter() - started, 4)
        if cached is not None:
            # Cache hit: zero new provider requests; token usage references the
            # cached execution and is never overwritten with live data.
            metadata = _metadata_from_cached(
                cached_trace, request, latency=latency
            )
        elif safe_error_code is not None:
            metadata = _designer_metadata(
                designer_instance,
                request,
                retry_count=retry_count,
                latency=latency,
                terminal_error_code=safe_error_code,
                provider_request_count=_requests_attempted(safe_error_code),
            )
        else:
            metadata = _designer_metadata(
                designer_instance, request, retry_count=retry_count, latency=latency
            )

        if proposal is None:
            state = self._failed_state(
                design_id=design_id,
                artifact_id=artifact_id,
                artifact_dir=design_dir,
                request=request,
                evidence=evidence,
                designer=resolved_designer_name,
                designer_instance=designer_instance,
                candidate_sections=candidate_sections,
                safe_error_code=safe_error_code or "provider_failed",
                error_message=error_message,
                metadata=metadata,
                profile_sha256=profile_sha256,
                approved_profile_version_id=approved_profile_version_id,
            )
            return state

        validation = validate_design_proposal(request, proposal, evidence)
        layout_spec_json: dict[str, Any] | None = None
        mapping_plan_json: dict[str, Any] | None = None
        template_version: str | None = None
        if validation.ok and validation.state != "unsupported":
            try:
                layout_spec = compile_layout_template_spec(
                    request, proposal, evidence, style_spec
                )
                layout_spec_json = layout_spec.model_dump(mode="json")
                template_version = layout_spec.template_version
                mapping_plan_json = MappingPlan(
                    proposal_id=proposal.proposal_id,
                    layout_class=proposal.layout_class,
                    mappings=proposal.mappings,
                    overflow_policy=proposal.overflow_policy,
                ).model_dump(mode="json")
            except UnsupportedPlacementError as exc:
                # A validated placement the current renderer cannot represent
                # is an explicit `unsupported` state, never a silent default.
                validation = validation.model_copy(
                    update={
                        "ok": False,
                        "state": "unsupported",
                        "errors": [*validation.errors, f"compilation failed: {exc}"],
                    }
                )
            except (DesignCompilerError, ValidationError) as exc:
                validation = validation.model_copy(
                    update={
                        "ok": False,
                        "state": "invalid_proposal",
                        "errors": [*validation.errors, f"compilation failed: {exc}"],
                    }
                )

        # Cache only proposals that passed deterministic validation: failed
        # proposals are never stored, so a retry can never replay an invalid
        # proposal (previously the raw model proposal was cached before
        # validation, making the retry loop deterministic-fail). The cached
        # proposal is re-validated and re-compiled on every cache hit.
        if validation.ok and cached is None:
            self.cache.put(
                cache_key,
                request=request,
                proposal=proposal,
                trace=_cache_trace(metadata),
            )

        trace = DesignDecisionTrace(
            provider=metadata.provider,
            model=metadata.model,
            prompt_version=metadata.prompt_version,
            input_checksum=_request_checksum(request),
            evidence_version=evidence.evidence_version,
            template_version=template_version,
            reason_codes=list(proposal.reason_codes),
            confidence=proposal.confidence,
            warnings=list(proposal.warnings),
            latency_seconds=metadata.latency_seconds,
            usage=_usage_dict(metadata),
            retry_count=metadata.retry_count,
            safe_error_code=metadata.terminal_error_code,
            proposal_checksum=proposal_checksum(proposal),
            cached=metadata.cached,
        )
        state = DesignArtifactState(
            design_id=design_id,
            artifact_id=artifact_id,
            state=validation.state,
            designer=resolved_designer_name,
            model=_designer_model(designer_instance),
            prompt_version=request.prompt_version,
            request=request,
            proposal=proposal,
            trace=trace,
            validation=validation,
            layout_spec_json=layout_spec_json,
            mapping_plan_json=mapping_plan_json,
            validation_errors=list(validation.errors),
            profile_sha256=profile_sha256,
            approved_profile_version_id=approved_profile_version_id,
            inventory_sha256=inventory_sha256(candidate_sections),
            template_version=template_version,
            created_at=_now_iso(),
            updated_at=_now_iso(),
        )
        self._persist(design_dir, state)
        return state

    def approve_design(
        self,
        *,
        design_dir: str | Path,
        artifact_dir: str | Path,
        reviewer_note: str | None = None,
    ) -> DesignArtifactState:
        state = load_design_state(design_dir)
        if state.state not in APPROVABLE_DESIGN_STATES:
            raise DesignServiceError(
                f"Design '{state.design_id}' is in state '{state.state}' and cannot be approved. "
                "Only ready or ready_with_review designs can be approved."
            )
        if state.layout_spec_json is None or state.template_version is None:
            raise DesignServiceError(
                f"Design '{state.design_id}' has no compiled LayoutTemplateSpec to approve."
            )
        approved = state.model_copy(
            update={
                "state": "approved",
                "approved_at": _now_iso(),
                "reviewer_note": reviewer_note,
                "updated_at": _now_iso(),
            }
        )
        self._persist(design_dir, approved)
        _supersede_previous(Path(artifact_dir), approved)
        return approved

    def is_approved(self, state: DesignArtifactState) -> bool:
        return state.state == "approved"

    # -- internals ------------------------------------------------------------

    def _default_designer_name(self) -> str:
        if self.strategy == DESIGN_STRATEGY_CLAUDE:
            return DESIGNER_CLAUDE
        if self.strategy == DESIGN_STRATEGY_OPENAI:
            return DESIGNER_OPENAI
        return DESIGNER_DETERMINISTIC

    def _resolve_designer(self, name: str) -> TemplateDesigner:
        if self._designer_override is not None:
            return self._designer_override
        if name == DESIGNER_DETERMINISTIC:
            return MockTemplateDesigner()
        if name == DESIGNER_OPENAI:
            return self._openai_designer
        return self._claude_designer

    def _cache_key(
        self,
        designer_name: str,
        designer_instance: TemplateDesigner,
        request: DesignRequest,
        evidence: TargetLayoutEvidence,
    ) -> DesignCacheKey:
        return DesignCacheKey(
            strategy=self.strategy,
            target_checksum=request.target_checksum,
            evidence_version=evidence.evidence_version,
            evidence_checksum=evidence_checksum(evidence),
            designer_provider=designer_instance.provider,
            designer_model=_designer_model(designer_instance),
            prompt_version=request.prompt_version,
            compiler_version=DESIGN_COMPILER_VERSION,
            inventory_sha256=inventory_sha256(request.available_candidate_sections),
        )

    def _failed_state(
        self,
        *,
        design_id: str,
        artifact_id: str,
        artifact_dir: Path,
        request: DesignRequest,
        evidence: TargetLayoutEvidence,
        designer: str,
        designer_instance: TemplateDesigner,
        candidate_sections: list[CandidateSectionRole],
        safe_error_code: str,
        error_message: str | None,
        metadata: DesignerCallMetadata,
        profile_sha256: str | None = None,
        approved_profile_version_id: str | None = None,
    ) -> DesignArtifactState:
        state_value = (
            "invalid_proposal" if safe_error_code == "invalid_proposal" else "provider_failed"
        )
        trace = DesignDecisionTrace(
            provider=metadata.provider,
            model=metadata.model,
            prompt_version=metadata.prompt_version,
            input_checksum=_request_checksum(request),
            evidence_version=evidence.evidence_version,
            latency_seconds=metadata.latency_seconds,
            usage=_usage_dict(metadata),
            retry_count=metadata.retry_count,
            safe_error_code=metadata.terminal_error_code,
            cached=metadata.cached,
        )
        state = DesignArtifactState(
            design_id=design_id,
            artifact_id=artifact_id,
            state=state_value,
            designer=designer,
            model=_designer_model(designer_instance),
            prompt_version=request.prompt_version,
            request=request,
            trace=trace,
            validation=DesignValidationResult(
                ok=False, state=state_value, errors=[error_message] if error_message else []
            ),
            validation_errors=[error_message] if error_message else [],
            profile_sha256=profile_sha256,
            approved_profile_version_id=approved_profile_version_id,
            inventory_sha256=inventory_sha256(candidate_sections),
            created_at=_now_iso(),
            updated_at=_now_iso(),
        )
        self._persist(artifact_dir, state)
        return state

    def _persist(self, design_dir: str | Path, state: DesignArtifactState) -> None:
        directory = Path(design_dir)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "design_state.json").write_text(
            json.dumps(state.model_dump(mode="json"), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        (directory / "design_request.json").write_text(
            json.dumps(state.request.model_dump(mode="json"), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        if state.proposal is not None:
            (directory / "design_proposal.json").write_text(
                json.dumps(state.proposal.model_dump(mode="json"), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        if state.trace is not None:
            (directory / "design_trace.json").write_text(
                json.dumps(state.trace.model_dump(mode="json"), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        if state.layout_spec_json is not None:
            (directory / "layout_template_spec.json").write_text(
                json.dumps(state.layout_spec_json, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        if state.mapping_plan_json is not None:
            (directory / "mapping_plan.json").write_text(
                json.dumps(state.mapping_plan_json, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )


def load_design_state(design_dir: str | Path) -> DesignArtifactState:
    path = Path(design_dir) / "design_state.json"
    if not path.exists():
        raise DesignServiceError(f"No design artifact found at {design_dir}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return DesignArtifactState.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise DesignServiceError(f"Design artifact is invalid: {exc}") from exc


def design_dir_for(artifact_dir: str | Path, design_id: str) -> Path:
    return Path(artifact_dir) / "designs" / design_id


def _supersede_previous(artifact_dir: Path, approved: DesignArtifactState) -> None:
    designs_root = artifact_dir / "designs"
    if not designs_root.exists():
        return
    for design_dir in designs_root.iterdir():
        if not design_dir.is_dir() or design_dir.name == approved.design_id:
            continue
        try:
            other = load_design_state(design_dir)
        except DesignServiceError:
            continue
        same_target = (
            other.request.target_checksum == approved.request.target_checksum
            and other.inventory_sha256 == approved.inventory_sha256
        )
        if other.state == "approved" and same_target:
            superseded = other.model_copy(
                update={"state": "superseded", "updated_at": _now_iso()}
            )
            (design_dir / "design_state.json").write_text(
                json.dumps(superseded.model_dump(mode="json"), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )


def _designer_model(designer_instance: TemplateDesigner) -> str | None:
    return getattr(designer_instance, "model", None)


def _usage_dict(metadata: DesignerCallMetadata) -> dict[str, int | None]:
    return {
        "input_tokens": metadata.input_tokens,
        "output_tokens": metadata.output_tokens,
        "total_tokens": metadata.total_tokens,
        "provider_request_count": metadata.provider_request_count,
    }


def _requests_attempted(safe_error_code: str) -> int:
    """External requests made before a terminal failure. The provider call
    itself happened for call/invalid-result errors; gating errors (missing
    permission/credentials) made no request."""
    if safe_error_code in {"provider_call_failed", "invalid_proposal"}:
        return 1
    return 0


def _designer_metadata(
    designer_instance: TemplateDesigner,
    request: DesignRequest,
    *,
    retry_count: int,
    latency: float,
    terminal_error_code: str | None = None,
    provider_request_count: int | None = None,
) -> DesignerCallMetadata:
    """Build safe metadata from a designer execution. Token counts come from
    the designer's ``last_usage``; the request/proposal payloads are never
    included."""
    usage = dict(getattr(designer_instance, "last_usage", {}) or {})
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    total_tokens = usage.get("total_tokens")
    if total_tokens is None and (input_tokens is not None or output_tokens is not None):
        total_tokens = (input_tokens or 0) + (output_tokens or 0)
    return DesignerCallMetadata(
        provider=designer_instance.provider,
        model=_designer_model(designer_instance),
        prompt_version=request.prompt_version,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        provider_request_count=(
            provider_request_count
            if provider_request_count is not None
            else int(getattr(designer_instance, "last_request_count", 0) or 0)
        ),
        retry_count=retry_count,
        latency_seconds=latency,
        terminal_error_code=terminal_error_code,
        cached=False,
    )


def _metadata_from_cached(
    cached_trace: dict[str, Any],
    request: DesignRequest,
    *,
    latency: float,
) -> DesignerCallMetadata:
    """Metadata for a cache hit: zero new provider requests; token usage and
    retry count reference the cached execution (never overwritten by any
    live data)."""
    usage = dict(cached_trace.get("usage", {}) or {})
    return DesignerCallMetadata(
        provider=str(cached_trace.get("provider", "")),
        model=cached_trace.get("model"),
        prompt_version=request.prompt_version,
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        total_tokens=usage.get("total_tokens"),
        provider_request_count=0,
        retry_count=int(cached_trace.get("retry_count", 0) or 0),
        latency_seconds=latency,
        cached=True,
    )


def _cache_trace(metadata: DesignerCallMetadata) -> dict[str, Any]:
    return {
        "usage": _usage_dict(metadata),
        "retry_count": metadata.retry_count,
        "provider": metadata.provider,
        "model": metadata.model,
        "cached": False,
    }


def _request_checksum(request: DesignRequest) -> str:
    import hashlib

    payload = json.dumps(request.model_dump(mode="json"), sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
