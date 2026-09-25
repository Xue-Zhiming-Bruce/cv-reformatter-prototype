# Pipeline D: Agentic Document Workflow Proposal

Status: `Proposed; resumed for pre-template target-understanding design after accepted D1-0. No new implementation or product approval.`

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

The visual reviewer is an observation source, not an acceptance authority and
not a geometry oracle. It compares the target and candidate render and must
separate what it visibly observes from what it thinks caused the difference.
Its output must not collapse observation, structural attribution, and verdict
into one `problem` string.

The reviewer returns an observation-first record:

```json
{
  "finding_id": "review.finding.7",
  "observation": "Two horizontal lines are visible above the EXPERIENCE heading in the generated page.",
  "location": {
    "page": 1,
    "bbox": [46, 220, 548, 246],
    "node_id": "section.experience.heading",
    "unresolved_region": null
  },
  "comparison": {
    "target": "No equivalent double line is visible in the same heading region.",
    "generated": "Two full-width lines appear immediately above EXPERIENCE."
  },
  "hypothesis": {
    "kind": "repaired_defect_persists",
    "suspected_owner": "section_rule",
    "explanation": "The lines may belong to the section heading rule."
  },
  "severity": "medium",
  "confidence": 0.6
}
```

`observation` and `comparison` describe visible evidence. `hypothesis` is only
a proposed structural interpretation. A stable `node_id` is preferred, but a
reviewer that cannot resolve the node must return an explicit region instead
of inventing an identity.

Deterministic tools resolve the hypothesis in a separate record:

```json
{
  "finding_id": "review.finding.7",
  "observation_status": "recorded",
  "hypothesis_status": "rejected",
  "reason": "The measured section rule is below the heading within tolerance; the visible lines may belong to the header.",
  "evidence_ids": ["measure.section.experience.rule", "render.region.p1.220.246"],
  "follow_up": "inspect_region"
}
```

Allowed `hypothesis_status` values are `confirmed`, `rejected`, `unresolved`,
and `not_tested`. Rejecting a hypothesis must never delete or label the raw
observation as false. The observation may still identify a different defect,
as when a line is real but belongs to the header rather than the section.

The owner-facing review artifact and eventual interface must show, side by
side:

```text
visible observation
target-versus-generated comparison
reviewer hypothesis and confidence
deterministic resolution and evidence
recommended follow-up
```

Deterministic measurements confirm or reject only measurable hypotheses. The
product owner remains the final judge of visual quality.

The reviewer should be independent of the repair agent's reasoning trace. An
agent must not approve its own change merely because the rendered file exists.

#### D0-R evidence motivating this contract

The real E→D D0-R run repaired all three measured section-rule placements, but
the live visual reviewer reported all three as still broken. For EXPERIENCE,
the page did contain separate header lines above the heading; the reviewer saw
a real visual feature but assigned it to the wrong structural owner. The D0
resolver correctly rejected the `section_rule` hypothesis, yet its single
`falsified` verdict made the useful observation look false as well.

This is positive evidence for keeping the reviewer, but negative evidence for
using it as a judge. D1 must preserve the observation, reject only the bad
hypothesis, and permit a later investigator to remap the same observation to a
different node or defect class.

## 7. C1-to-C2 Evolution and Renderer Boundaries

C1 and C2 should not become two autonomous agents, and they are not two stages
that every document passes through at runtime. They represent an architectural
evolution: C1 is the measured HTML-authoring baseline; C2 is the preferred
target in which provider-neutral JSON becomes the authoritative layout state.

Every run selects exactly one authoring lane before generating its first
candidate:

```text
C1: HTML is the authored template and editable state
                         |
                         | preserve evidence, fitting, and gate lessons
                         v
C2: LayoutTemplateSpec JSON is the authored state
                         |
                         +--> HTML RenderPlan --> Chrome --> PDF
                         +--> DOCX RenderPlan --> OOXML --> DOCX
```

C1's HTML compiler and renderer work are not discarded: under C2, HTML remains
the compiled PDF-renderer surface rather than the stored product template.

During migration only, separate evaluation runs may apply C1 and C2 to the same
source/target pair. The manifest records `authoring_lane` so evidence cannot be
mixed. This temporary parallel comparison does not imply a permanent two-lane
product: once C2 reaches the approved C1 parity and capability gates, the owner
selects the canonical state model. A model must never switch lanes after a
failure or use cross-lane translation as a silent fallback.

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

D0, D0-R, and D1-0 exercised only the C1 HTML lane. They validate the bounded
agent control, repair, review, version, and approval mechanisms around that
lane; they provide no evidence yet that the C2 lane works or that C1 is the
production choice.

### 7.3 Cross-format rendering hypothesis for C2

The active product contract still supports same-format lanes only (`PDF → PDF`,
`DOCX → DOCX`). C2 will experimentally test both cross-format directions
before any product-contract or ADR change:

```text
PDF source  -> CandidateProfile + LayoutTemplateSpec -> DOCX
DOCX source -> CandidateProfile + LayoutTemplateSpec -> PDF
```

Cross-format support does not require every decoration to survive unchanged.
It requires complete candidate content, correct reading order, usable native
structure, and explicit renderer-compatibility evidence. The deterministic
renderer/compiler—not an agent—classifies each relevant feature as:

```text
exact                 render faithfully
adjusted              use an approved content-preserving fallback and disclose it
unsupported           material content/structure risk; require confirmation or stop
```

For example, PDF skill pills may become editable inline skill text in DOCX.
The skill values, order, and grouping remain while rounded backgrounds may be
omitted. A renderer must not use an ugly or fragile approximation merely to
claim parity, and it must never drop content silently.

Each cross-format candidate produces a `ConversionCompatibilityReport` that
records source/output formats, exact features, adjustments, unsupported
features, content-loss risk, fallback applied, and whether owner confirmation
is required. `adjusted` results receive a visible non-blocking warning;
`unsupported` results require an explicit decision or fail closed. The two
conversion directions remain available for evaluation even when individual
features degrade.

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

### D1: improve the unattended first draft

D1 first improves the result produced from one recruiter submission, before
building chat editing. "One-shot" here means one user submission and no user
intervention during generation; the backend may still run bounded internal
analysis, review, and repair steps.

**Owner sequencing decision (2026-09-15): Pipeline D pauses after accepted
D1-0. D1-1 through D1-4 are deferred until Pipeline C2 establishes whether the
structured state and renderer boundary work on real resumes.** Pipeline D
should resume on the selected canonical state model instead of deepening its
C1-specific integration prematurely.

Each D1 evaluation run must declare one `authoring_lane` as defined in §7.
Results from a C1 run and a C2 run must be reported separately even when they
use the same source/target pair.

#### D1-0: observation-first review contract

- Replace the overloaded reviewer `problem`/`verdict` representation with the
  observation, comparison, hypothesis, and resolution records defined in
  §6.4.
- Preserve every raw reviewer observation in the run artifact and trace.
- Resolve node ownership and measurable hypotheses deterministically where a
  verifier exists.
- A rejected hypothesis may trigger `inspect_region`; it must not erase the
  observation or silently convert the run into a visual pass.
- Generate an owner-readable review table containing all five fields listed in
  §6.4, rather than reporting only a verdict.
- Add a regression case based on the D0-R distinction between a real header
  line observation and an incorrect `section_rule` attribution.
- Keep the D0 model/tool budgets, inactive candidate behavior, and explicit
  owner accept/reject boundary.

D1-0 succeeds when the same visible observation survives an incorrect
hypothesis and remains available for a later tool call or owner decision. It
does not need to repair the newly discovered defect.

Status: completed and accepted on the C1 HTML lane. This establishes reviewer
evidence handling, not general one-shot quality.

#### D1-1: measure the real one-shot baseline

- Run the selected authoring lane on the authorized Resume A-F matrix without
  recruiter instructions or manual artifact repair.
- Preserve content and privacy as hard gates; do not trade either for visual
  fidelity.
- Classify remaining defects by frequency, severity, deterministic
  measurability, owning layer, and whether the current layout vocabulary can
  express the needed behavior.
- Produce owner-reviewable target/generated PDFs and comparison images. The
  owner, not the VLM reviewer or a composite score, labels each output as
  usable, minor-repair, or major-repair.
- Do not compare C1 and C2 inside one run. If both are evaluated, run the same
  case once per lane with separate manifests and artifacts.

#### D1-2: fix systematic one-shot causes

- Fix repeated defects in evidence normalization, template compilation, or
  rendering at their shared deterministic owner instead of teaching an agent
  to repair the same mistake document by document.
- Add a new `EditAction` only for a verified long-tail defect that cannot be
  eliminated reliably in the compiler or renderer.
- Re-run the same real matrix after each bounded change and compare with the
  frozen D1-1 baseline.

#### D1-3: bounded automatic refinement

- Feed confirmed reviewer observations into the investigator after the first
  candidate render.
- Route systematic failures back to their owning component and supported
  long-tail failures to a typed repair action.
- Re-render and re-run independent gates within fixed request, tool, repair,
  and time budgets; unsupported or unresolved findings stop for owner review.
- Measure whether this internal loop improves the owner-rated first draft over
  the no-refinement D1-1 baseline.

#### D1-4: recruiter chat editing

- Add stable node selection in the interface-facing contract.
- Translate a recruiter instruction plus selected node/region into an existing
  typed `EditAction`.
- Produce a candidate preview without changing the accepted version.
- Support explicit accept/reject and bounded automatic review.
- Record the instruction, agent decisions, and tool calls in the artifact
  manifest.

Chat editing is the last-mile fallback after the one-shot baseline and bounded
automatic refinement have been measured. It must not become a substitute for
fixing repeated compiler or renderer defects.

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
- whether C1 or C2 produces the stronger owner-rated one-shot baseline under
  the same corpus and gates;
- how often the layout vocabulary reports a genuine capability gap;
- whether the visual reviewer detects enough confirmed defects to justify its
  latency and cost;
- where recruiter confirmation is required before promotion.

Until those questions have measured answers, this remains an experiment and
must not replace the active document-pipeline contract.

## 14. Owner direction (2026-09-17): understand the target before building a template

The next Pipeline D question is upstream of the D0/D1 repair loop: can an
agent understand the structure of an unfamiliar target resume without
template-specific production code? The earlier §12 sequencing pause is lifted
for this **design and evaluation question**. D0, D0-R, and D1-0 remain bounded
C1 repair/review evidence; they do not answer it. C2 remains an unaccepted
experiment and a comparison source, not a route declared impossible by one
unfamiliar target's failure. Do not start D1-1 through D1-4 or promote a
renderer on the strength of this direction alone.

### Target-understanding checkpoint (before candidate conversion)

```text
Target PDF + page images + raw Adobe response + normalized evidence
-> evidence-coverage check
-> target-understanding agent: inspect pages/regions, query raw elements,
   propose and revise section/entry/group/inline-role relationships
-> independently checked, evidence-linked target-structure draft
-> template builder + render/review loop (later checkpoint)
-> candidate conversion from a reviewed CandidateProfile (later checkpoint)
```

The orchestrator schedules bounded investigation and records each attempted
structure version. An investigator may request page or region crops and raw
Adobe elements; it must distinguish visible observations from role/ownership
hypotheses, cite element IDs or image regions, and leave ambiguity unresolved.
Measurement code owns coordinates, typography, rules, and evidence coverage.
The model must not infer a complete structure from evidence known to be
incomplete. In particular, Resume I's raw-to-normalized loss and its one
raw-extraction miss are separate failure classes to report, not facts a larger
context window can repair.

The checkpoint output is a **target-structure draft**, not HTML, a render plan,
or an approved reusable template. A target-content reconstruction may help
diagnose structure and measurement, but stays an analysis artifact; it cannot
be used to fill a candidate document. Reflow behavior for different candidate
lengths is tested only when the builder compiles a reusable template.

### Reviewer input and decision boundary

For later rendered candidates, compare a full-page overview and matched
target/generated region crops, accompanied by measured facts, provenance,
and an overlay or difference map where useful. Because target and candidate
wording differ, raw pixel differences alone cannot identify layout defects.
The reviewer reports localized observations and hypotheses; measurable claims
are checked by tools, content/privacy gates remain independent, and the owner
judges the final visual result. Before using reviewer findings to drive an
automatic repair loop, compare full pages, crops, and crops plus measured facts
on a fixed set of known true defects and false accusations as proposed in §13
of `PIPELINE_EVOLUTION_PROPOSAL.md`. Cropping is a candidate input technique,
not an assumed solution.

### Decision gate

Evaluate the understanding checkpoint on target families frozen before
inspection, with human-annotated section, entry, subgroup, and attribution
truth. Resume I is a known diagnostic case, not the sole blind generalization
test. Record evidence coverage, correct and incorrect role/ownership claims,
unresolved cases, model/tool/time cost, and whether a new target required a
production rule or renderer change. A confident wrong `ok` is a failure.
Proceed to template building only when the structure is reviewable and its
material uncertainties are resolved or explicitly marked unsupported. Stop
the loop on exhausted budget, repeated unchanged hypotheses, missing source
evidence, or contradictions that cannot be resolved from available inputs.

Giving a VLM page images or raw target text, or storing renderer-specific
template artifacts as product state, exceeds the current ADR 0001 / ADR 0006
boundaries. This experiment proposal grants no production exception; those
contracts require owner-approved revision before integration.
