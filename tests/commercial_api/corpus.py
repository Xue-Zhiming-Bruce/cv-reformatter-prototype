from __future__ import annotations

import hashlib
import importlib
from pathlib import Path
from typing import Any

from tests.commercial_api.models import CorpusCase, CorpusManifest

CORPUS_DIR = Path(__file__).resolve().parent / "corpus"
MANIFEST_PATH = CORPUS_DIR / "manifest.json"
DEFAULT_CORPUS_VERSION = "1.0"


class CorpusError(RuntimeError):
    """The corpus manifest is invalid or an input drifted from its pin."""


def load_corpus_manifest(path: Path = MANIFEST_PATH) -> CorpusManifest:
    payload = _read_json(path)
    return CorpusManifest.model_validate(payload)


def case_by_id(manifest: CorpusManifest, case_id: str) -> CorpusCase:
    for case in manifest.cases:
        if case.case_id == case_id:
            return case
    raise CorpusError(f"Unknown corpus case: {case_id}")


def cases_for_lane(manifest: CorpusManifest, lane: str) -> list[CorpusCase]:
    return [case for case in manifest.cases if lane in case.lanes]


def materialize_case_input(case: CorpusCase, dest_dir: Path) -> Path:
    """Materialize a case input into ``dest_dir`` and verify its checksum.

    Text inputs are copied from the corpus directory. PDF inputs are generated
    deterministically by the referenced builder in ``tests.helpers.synthetic_pdf``.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    if case.input:
        source = CORPUS_DIR / case.input
        if not source.is_file():
            raise CorpusError(f"Corpus input missing: {source}")
        content = source.read_bytes()
        if case.expected_sha256 and hashlib.sha256(content).hexdigest() != case.expected_sha256:
            raise CorpusError(
                f"Corpus input {case.input} drifted from its pinned checksum. "
                "Update the manifest only after a deliberate corpus change."
            )
        dest = dest_dir / case.input
        dest.write_bytes(content)
        return dest
    if case.builder:
        module = importlib.import_module("tests.helpers.synthetic_pdf")
        builder = getattr(module, case.builder)
        dest = dest_dir / f"{case.case_id}.pdf"
        builder(dest)
        if case.expected_sha256 and hashlib.sha256(dest.read_bytes()).hexdigest() != case.expected_sha256:
            raise CorpusError(
                f"Builder {case.builder} output drifted from its pinned checksum. "
                "Update the manifest only after a deliberate corpus change."
            )
        return dest
    raise CorpusError(f"Case {case.case_id} declares neither input nor builder.")


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    import json

    return json.loads(path.read_text(encoding="utf-8"))
