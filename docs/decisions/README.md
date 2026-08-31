# Architecture Decision Records

Status: `Active process`

Architecture Decision Records capture consequential technical decisions after
the required evidence and product approval exist.

Create an ADR when selecting or materially changing:

- a commercial document analyzer;
- a renderer or PDF exporter;
- a persistence or durable-job implementation;
- private artifact storage;
- authentication or authorization architecture;
- a provider-routing or fallback policy;
- a schema decision with significant migration consequences.

Research documents and benchmark reports do not select an architecture. Until
an ADR is approved, candidates remain options.

## Naming

```text
NNNN-short-decision-title.md
```

Example:

```text
0001-target-layout-analyzer.md
```

## Statuses

- `Proposed`;
- `Accepted`;
- `Superseded`;
- `Rejected`.

Copy [ADR Template](ADR_TEMPLATE.md) for a new record. Link the supporting
corpus, evaluation reports, privacy review, and rollback plan.
