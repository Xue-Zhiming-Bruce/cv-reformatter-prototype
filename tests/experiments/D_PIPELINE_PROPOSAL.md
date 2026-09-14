# Pipeline D: Agentic Document Workflow Proposal

Status: `Proposed`

Date: September 14, 2026

Scope: experimental decomposition of useful C1/C2 mechanisms into callable
tools and bounded agent roles. This document does not change the active product
or architecture contracts.

## 1. Objective

Make the document workflow more adaptive without giving models uncontrolled
ownership of candidate facts, templates, or generated files.

The proposed system uses:

- a main agent to diagnose the current artifact state and choose the next
  bounded action;
- specialist agents for tasks that require semantic or visual reasoning;
- deterministic tools for extraction, mutation, compilation, rendering,
  validation, versioning, and rollback;
- immutable raw and normalized evidence that agents can inspect on demand.

The intended product behavior remains:

```text
good first draft
-> recruiter identifies a remaining defect
-> chat instruction becomes a scoped edit
-> deterministic re-render and validation
-> recruiter accepts or rejects the new version
```

This proposal does not replace `CandidateProfile`, `TargetLayoutEvidence`,
`LayoutTemplateSpec`, `RenderPlan`, or the format-preserving PDF/DOCX lanes.

## 2. Core Principle

Agents reason and propose. Tools mutate and verify.

An agent may decide which evidence to inspect, classify a defect, or propose a
typed repair. It must not directly overwrite canonical HTML, layout state,
candidate data, or published output.

Every mutation follows this boundary:

```text
Agent or sub-agent
-> typed PatchProposal / EditAction
-> schema and policy validation
-> deterministic application to a candidate version
-> compilation and rendering
-> independent quality gates
-> promote or discard
```

## 3. Proposed Runtime Shape

```text
Deterministic workflow shell
        |
        v
Main Orchestrator Agent
        |
        +-- inspect artifact state and evidence
        +-- delegate bounded reasoning tasks
        +-- select an allowed tool call
        +-- stop, retry, or request recruiter input
        |
        +--> Layout Planner / Repair Agent
        +--> Evidence Investigator Agent
        +--> Visual Reviewer Agent
        |
        v
Deterministic tools
        |
        +-- normalize evidence
        +-- apply edit actions
        +-- compile HTML or DOCX
        +-- render
        +-- validate
        +-- version and rollback
```

The outer workflow remains deterministic. The main agent operates only at
explicit reasoning checkpoints; it does not invent an unbounded execution
graph.

## 4. Implementation Decision

Pipeline D should not start by building a general-purpose agent framework. It
should combine an existing typed agent SDK with a product-owned workflow and
artifact runtime.

```text
Use an existing SDK for:
- model tool-calling loops
- structured agent outputs
- bounded sub-agent invocation
- context propagation
- tracing hooks

Build and own in this product:
- PipelineState and transition rules
- artifact and version records
- tool authorization
- retry and stop budgets
- validation and rollback
- recruiter approval
```

The agent SDK is replaceable infrastructure. Conversation history, SDK session
state, or a provider response must never become the authoritative document or
workflow state.

### 4.1 Recommended D0 stack

The recommended first implementation is:

```text
FastAPI
+ Pydantic schemas
+ plain Python deterministic state machine
+ PydanticAI as the candidate agent/tool layer
+ existing compiler, renderer, and validator functions
+ local versioned experiment artifacts
```

PydanticAI is the leading D0 candidate because the repository already uses
Pydantic at trust boundaries, Pipeline D requires strict structured outputs,
and the experiment should retain the ability to compare model providers. Its
tools are normal Python functions with schema generation and validation, and a
specialist agent can be exposed to the manager as a bounded delegation tool.

Relevant primary documentation:

- [PydanticAI overview](https://pydantic.dev/docs/ai/overview/)
- [PydanticAI function tools](https://github.com/pydantic/pydantic-ai/blob/main/docs/tools.md)
- [PydanticAI multi-agent applications](https://pydantic.dev/docs/ai/guides/multi-agent-applications/)

This is a candidate implementation choice, not provider approval. D0 must not
add the dependency until the experiment work item and model/provider use are
explicitly approved.

### 4.2 Alternative: OpenAI Agents SDK

The OpenAI Agents SDK is a credible alternative for a fast D0 spike. Its
manager pattern maps directly to Pipeline D: the main agent retains control and
calls specialists through agents-as-tools instead of handing the whole run to
them. It also provides function tools, Pydantic-backed validation, guardrails,
sessions, and tracing.

- [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/)
- [Agent orchestration](https://openai.github.io/openai-agents-python/multi_agent/)
- [Tool guardrails](https://openai.github.io/openai-agents-python/guardrails/)

It should still sit behind an internal model/agent adapter. Pipeline artifacts,
versions, approvals, and retries must not depend on an SDK `Runner` or session
format. Tracing must remain disabled or redacted until candidate-data privacy,
retention, and provider policy are explicitly approved; the SDK documents that
tracing is enabled by default.

### 4.3 Why not LangGraph in D0

LangGraph provides checkpoints, pause/resume, human-in-the-loop interrupts,
time-travel debugging, and fault recovery. Those capabilities may become useful
when a recruiter can leave a run and resume it later.

- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- [LangGraph human-in-the-loop](https://docs.langchain.com/oss/python/langchain/human-in-the-loop)

D0 has only a small fixed flow:

```text
inspect -> diagnose -> propose -> render -> validate -> accept or reject
```

Adding a graph checkpoint store now would create a second state model beside
the product's artifact/version state before the need is demonstrated. Start
with explicit Python transitions and reconsider LangGraph only when branching,
interrupts, or resumable execution become materially difficult.

### 4.4 Why not Temporal in D0

Temporal is a durable workflow runtime, not the semantic agent layer. It may be
appropriate later when Adobe calls, rendering, asynchronous review, or human
approval must survive process and network failures.

- [Temporal documentation](https://docs.temporal.io/)

A later product workflow could run Adobe analysis, agent reasoning, rendering,
validation, and recruiter approval as durable activities and waits. That is an
operational D2 concern, not required to test whether agent-directed diagnosis
improves document repair.

### 4.5 What an agent means in Pipeline D

Pipeline D does not require training a new model. An agent is a configured
runtime unit:

```text
model
+ role instructions
+ allowed tools
+ structured output schema
+ bounded run context
+ token, tool-call, retry, and time budgets
```

A run context should contain references and authority, not uncontrolled file
access:

```python
class PipelineAgentContext:
    artifact_id: str
    active_layout_version_id: str
    remaining_repairs: int
    allowed_node_ids: set[str]
```

Specialists initially run as ordinary in-process asynchronous agent calls.
They do not need separate services, processes, queues, or databases. The main
agent invokes them through named tools and receives a schema-validated result.

The first implementation should use local Python function tools, not MCP. MCP
becomes useful only if these capabilities later need to cross a process or
service boundary.

## 5. Artifact and Evidence Access

Adobe responses, page images, normalized evidence, layout state, renders, and
validation reports are stored as versioned artifacts. Raw provider output is
immutable evidence, not product state.

Agents should not receive the entire Adobe response by default. They first
inspect an artifact manifest and normalized evidence, then request a narrow raw
slice only when necessary.

Candidate inspection tools include:

```text
get_artifact_manifest(artifact_id)
get_normalized_layout(artifact_id)
inspect_layout_node(artifact_id, node_id)
inspect_adobe_elements(artifact_id, element_ids)
inspect_page_region(artifact_id, page, bbox)
inspect_target_image(artifact_id, page, bbox)
inspect_render_image(artifact_id, render_version, page, bbox)
compare_target_and_render(artifact_id, render_version, node_id)
```

Raw-evidence inspection is justified when:

- normalized evidence is missing or contradictory;
- a reviewer finds a target/render mismatch;
- confidence is below a defined threshold;
- the compiler reports that the current layout vocabulary cannot express a
  measured feature;
- a deterministic validation result conflicts with the current diagnosis.

Every evidence-derived claim records artifact IDs, element IDs or page regions,
and provenance.

## 6. Agent Responsibilities

### 6.1 Main Orchestrator

The orchestrator reads the current workflow state and chooses one allowed next
step. It may:

- request targeted evidence;
- delegate one bounded investigation or repair proposal;
- invoke deterministic tools;
- compare results with the previous version;
- stop automatic repair and ask the recruiter for clarification.

It may not bypass content, privacy, structural, visual, approval, or
same-format lane gates.

### 6.2 Evidence Investigator

The investigator determines which layer most likely owns a defect:

```text
provider extraction
normalization
semantic layout interpretation
LayoutTemplateSpec expressiveness
compiler behavior
renderer behavior
```

Its output is a diagnosis with cited evidence and, when required, a request for
additional evidence. It does not modify an artifact.

### 6.3 Layout Planner / Repair Agent

The layout agent converts evidence and a confirmed defect into a typed layout
proposal. Example:

```json
{
  "type": "SetHeadingRule",
  "target_node_id": "section.experience.heading",
  "base_layout_version_id": "layout_v12",
  "changes": {
    "placement": "inline_after",
    "gap_pt": 6
  },
  "evidence_ids": ["adobe.element.34", "comparison.finding.7"]
}
```

The action vocabulary must be bounded and schema-validated. Unsupported
requests return an explicit capability gap rather than escaping into arbitrary
HTML or CSS.

### 6.4 Visual Reviewer

The visual reviewer is a defect detector, not an acceptance authority. It
compares the target and candidate render and returns localized hypotheses:

```json
{
  "node_id": "section.experience.heading",
  "problem": "heading_rule_position_mismatch",
  "severity": "medium",
  "confidence": 0.82,
  "region": {"page": 1, "bbox": [46, 220, 548, 246]}
}
```

Deterministic measurements confirm or reject measurable findings. The product
owner remains the final judge of visual quality.

The reviewer should be independent of the repair agent's reasoning trace. An
agent must not approve its own change merely because the rendered file exists.

## 7. C1 and C2 as Callable Capabilities

C1 and C2 should not become two autonomous agents. Their useful mechanisms
become tools behind the same artifact and validation contracts.

### 7.1 C1-derived tools

```text
generate_html_candidate()
inspect_html_element()
propose_scoped_html_patch()
apply_scoped_html_patch()
render_html()
```

C1 is suitable for rapid candidates, experiments, and diagnosing a layout
feature that the structured vocabulary cannot yet express. A C1 HTML result is
a candidate artifact, not durable product state.

### 7.2 C2-derived tools

```text
create_layout_state()
inspect_layout_node()
apply_edit_action()
compile_layout_state()
render_compiled_output()
```

C2 is the preferred durable path because it supports stable node identities,
localized edits, reproducible compilation, version comparison, and rollback.

Both paths reuse the same evidence, rendering, comparison, content-safety, and
validation tools. There must not be two independent stores of candidate facts
or layout history.

If a C1 candidate cannot be translated into the shared structured layout state
without another unreliable interpretation step, it remains experimental. The
production workflow must not preserve C1 merely to preserve sunk engineering
work.

## 8. Example Repair Loop

Suppose the reviewer reports that the Experience bullets are too far to the
right.

```text
1. Orchestrator receives a localized reviewer finding.
2. inspect_layout_node("section.experience.entries")
3. compare_target_and_render(..., node_id="section.experience.entries")
4. If evidence conflicts, delegate to Evidence Investigator.
5. Delegate a bounded repair proposal to Layout Repair Agent.
6. Validate SetListStyle(target_node_id, indent_delta_pt=-3).
7. Apply it to layout_v13_candidate; layout_v12 remains immutable.
8. Compile, render, and run content/privacy/structure checks.
9. Ask Visual Reviewer to inspect the new render independently.
10. Promote layout_v13 or discard it and keep layout_v12.
```

The sub-agent never edits the canonical file directly.

## 9. Versioning and Rollback

Each attempted change produces a candidate transaction:

```text
layout_v12.json                 current accepted state
layout_v13_candidate.json       proposed state
layout_v13_render.pdf           proposed output
layout_v13_validation.json      deterministic gate results
layout_v13_review.json          reviewer findings
```

Promotion changes the artifact manifest's active layout version. Rejection or
failure leaves the previous version unchanged. Candidate facts and target
evidence are never rewritten by a layout repair.

## 10. Control and Stop Rules

The first experiment should use fixed limits:

- no more than two automatic repair attempts per finding;
- no more than one raw-evidence expansion per attempt;
- each repair may touch only the diagnosed node and declared dependent nodes;
- any candidate-content or privacy regression causes immediate rejection;
- a stale base version causes rejection rather than an implicit rebase;
- no improvement, repeated repair fingerprints, or conflicting reviewers stop
  the loop and request human review;
- an unsupported layout feature is recorded explicitly and never triggers a
  silent default-template fallback.

These are workflow rules enforced by code, not suggestions embedded only in an
agent prompt.

## 11. Minimal Experimental Slice

Do not build a general-purpose multi-agent platform first. The smallest useful
D0 experiment has:

1. one deterministic orchestrator state machine;
2. one main reasoning agent at explicit checkpoints, implemented with
   PydanticAI or an isolated OpenAI Agents SDK comparison spike;
3. three fixed roles: evidence investigator, layout repair, visual reviewer;
4. read-only evidence tools;
5. one bounded `EditAction` type, initially `SetListStyle` or
   `SetHeadingRule`;
6. candidate-version application, render, validation, accept, and rollback;
7. one known C1/C2 matrix defect with deterministic ground truth.

The experiment succeeds only if it repairs the known defect without changing
candidate content or regressing unaffected nodes. It fails if the same result
requires unrestricted HTML rewriting, hidden fallback, repeated oscillation,
or manual artifact repair.

Only after this slice demonstrates value should agents receive more tools,
additional edit actions, parallel execution, or permission to delegate further
sub-agents.

## 12. Proposed Evolution

### D0: prove agent-directed routing

- Use a plain Python state machine and local typed tools.
- Start with PydanticAI; run at most one isolated OpenAI Agents SDK comparison
  spike if the first choice creates a concrete blocker.
- Use one main agent and the three fixed specialist roles.
- Repair one known C1/C2 matrix defect with deterministic ground truth.
- Keep all state in versioned experiment artifacts.

### D1: connect recruiter chat editing

- Add stable node selection in the interface.
- Translate a recruiter instruction into a typed `EditAction`.
- Produce a candidate preview without changing the accepted version.
- Support explicit accept/reject and bounded automatic review.
- Record agent decisions and tool calls in the artifact manifest.

### D2: production durability

- Move product state to the approved PostgreSQL/artifact-storage foundation.
- Add durable jobs only after real latency and interruption evidence exists.
- Evaluate LangGraph for graph-level human interrupts or Temporal for durable
  operational execution; do not adopt both for the same responsibility.
- Apply provider policy, privacy-safe tracing, authentication, authorization,
  retention, and deletion controls before processing real candidate data.

## 13. Open Decisions

The experiment should answer, rather than assume:

- whether a main reasoning agent improves routing over a deterministic rule
  table for observed failures;
- whether raw Adobe evidence materially improves repair quality after
  normalized evidence is available;
- which repair classes require a specialist sub-agent instead of a single
  structured model call;
- whether C1 HTML candidates contribute useful evidence to C2 or merely add a
  lossy translation step;
- how often the layout vocabulary reports a genuine capability gap;
- whether the visual reviewer detects enough confirmed defects to justify its
  latency and cost;
- where recruiter confirmation is required before promotion.

Until those questions have measured answers, this remains an experiment and
must not replace the active document-pipeline contract.
