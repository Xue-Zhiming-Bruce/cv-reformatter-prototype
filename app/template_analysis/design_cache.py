"""Filesystem cache for design proposals.

Design results are cached by a composite key that includes the target
checksum, evidence schema/version/checksum, designer provider/model, prompt
version, request schema version, and compiler version. Caching stores only the
*draft* proposal: approval is always re-evaluated against the persisted design
artifact, so a changed model or prompt can never silently replace an approved
design.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from app.template_analysis.design_schemas import (
    DESIGN_EVIDENCE_SCHEMA_VERSION,
    DESIGN_REQUEST_SCHEMA_VERSION,
    DesignProposal,
    DesignRequest,
)

DEFAULT_DESIGN_CACHE_DIR = Path("data/design_cache")


class DesignCacheKey:
    """Composite, versioned cache key. All versions participate in the ID."""

    def __init__(
        self,
        *,
        strategy: str,
        target_checksum: str,
        evidence_schema_version: str = DESIGN_EVIDENCE_SCHEMA_VERSION,
        evidence_version: str,
        evidence_checksum: str,
        designer_provider: str,
        designer_model: str | None,
        prompt_version: str,
        request_schema_version: str = DESIGN_REQUEST_SCHEMA_VERSION,
        compiler_version: str,
        inventory_sha256: str,
    ) -> None:
        self.strategy = strategy
        self.target_checksum = target_checksum
        self.evidence_schema_version = evidence_schema_version
        self.evidence_version = evidence_version
        self.evidence_checksum = evidence_checksum
        self.designer_provider = designer_provider
        self.designer_model = designer_model
        self.prompt_version = prompt_version
        self.request_schema_version = request_schema_version
        self.compiler_version = compiler_version
        self.inventory_sha256 = inventory_sha256

    def cache_id(self) -> str:
        payload = json.dumps(self._payload(), sort_keys=True, ensure_ascii=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def as_json(self) -> dict[str, Any]:
        return self._payload()

    def _payload(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "target_checksum": self.target_checksum,
            "evidence_schema_version": self.evidence_schema_version,
            "evidence_version": self.evidence_version,
            "evidence_checksum": self.evidence_checksum,
            "designer_provider": self.designer_provider,
            "designer_model": self.designer_model,
            "prompt_version": self.prompt_version,
            "request_schema_version": self.request_schema_version,
            "compiler_version": self.compiler_version,
            "inventory_sha256": self.inventory_sha256,
        }


class DesignCache:
    """Stores sanitized (request, proposal, trace) drafts keyed by cache ID."""

    def __init__(self, cache_dir: str | Path = DEFAULT_DESIGN_CACHE_DIR) -> None:
        self.cache_dir = Path(cache_dir)

    def get(
        self, key: DesignCacheKey
    ) -> tuple[DesignRequest, DesignProposal, dict[str, Any]] | None:
        path = self._path(key)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            request = DesignRequest.model_validate(payload["request"])
            proposal = DesignProposal.model_validate(payload["proposal"])
            return request, proposal, dict(payload.get("trace", {}))
        except (OSError, json.JSONDecodeError, ValueError):
            return None

    def put(
        self,
        key: DesignCacheKey,
        *,
        request: DesignRequest,
        proposal: DesignProposal,
        trace: dict[str, Any],
    ) -> Path:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self._path(key)
        payload = {
            "cache_key": key.as_json(),
            "request": request.model_dump(mode="json"),
            "proposal": proposal.model_dump(mode="json"),
            "trace": trace,
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    def _path(self, key: DesignCacheKey) -> Path:
        return self.cache_dir / f"{key.cache_id()}.json"


def evidence_checksum(evidence: Any) -> str:
    """Checksum of the normalized evidence record (excluding raw pages bytes)."""
    payload = json.dumps(
        evidence.model_dump(mode="json", exclude={"pages"}),
        sort_keys=True,
        ensure_ascii=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def default_cache_dir() -> Path:
    return Path(os.getenv("DESIGN_CACHE_DIR", str(DEFAULT_DESIGN_CACHE_DIR)))
