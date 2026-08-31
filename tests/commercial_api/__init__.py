"""Versioned, offline-first evaluation harness for commercial provider APIs.

This package reforms the commercial-provider test workflow:

- offline pytest tests that never require credentials or network access;
- deterministic baseline evaluations against the versioned synthetic corpus;
- explicit live-provider evaluations that are skipped unless ``--live`` is
  passed and credentials are configured;
- result comparison between two completed runs without calling providers again.

It intentionally does not select providers, create ADRs, or change the
production document pipeline. See ``tests/commercial_api/README.md``.
"""

SCHEMA_ROOT = "commercial_api"
RUN_MANIFEST_SCHEMA = "commercial_api/run-manifest/1"
RESULT_SCHEMA = "commercial_api/result/1"
CORPUS_SCHEMA = "commercial_api/corpus/1"
SUMMARY_SCHEMA = "commercial_api/summary/1"
COMPARE_SCHEMA = "commercial_api/compare/1"

__all__ = [
    "SCHEMA_ROOT",
    "RUN_MANIFEST_SCHEMA",
    "RESULT_SCHEMA",
    "CORPUS_SCHEMA",
    "SUMMARY_SCHEMA",
    "COMPARE_SCHEMA",
]
