# Area: ideas-catalog

Status: `current` (idea preservation ledger, read-only)

## Purpose

Preserves the ideas embedded in the pruned experimental branches so nothing
good is lost when the pipeline shells are deleted. Re-test ideas through the
surviving commercial harness (`tests/commercial_api/` + `commercial_bakeoff/`),
never by rebuilding a parallel pipeline.

## Ideas from target_replica (branch E1-E6) — approved for deletion 2026-08-21

| Idea | Origin file(s) | What it does | Why it matters | Re-test via |
|---|---|---|---|---|
| LLM-as-judge (visual critique) | `block_pipeline.py` (`_critique_layout`, `VisualCritique` in `block_models.py`, `_decide_critique_operations`) | LLM critiques target-vs-generated + diff image; emits operations that gate the render | strongest idea; a real visual-QA check once target fidelity is core | harness `rendering`/visual lane with --live |
| Semantic blocks (LLM) | `block_models.py` (`SemanticLayoutDocument`, `BlockInterpretation`), `block_pipeline.py` | typed semantic blocks (experience roles, skills groups…) instead of raw lines | better structure for reflow; aligns with CONTENT_AND_LAYOUT_MODEL | harness `layout` lane |
| Semantic-block v3 experiment | target_replica (LLM-assisted v3) | employers/roles/responsibilities as semantic blocks | same idea, later iteration | harness layout lane |
| Deterministic region/block detection | `detection_pipeline.py`, `detection_models.py` | rule-based column/region/block detection | cheap deterministic geometry (no provider) | harness `layout` lane as `baseline` |
| Color-palette/typography extraction | `skeleton_compiler.py`, `models.py` (`ColorPaletteSpec`, `ColorToken`) | dominant colors + heading aliases from target | feeds the fixed LayoutTemplateSpec (the typography fix) | harness layout lane |
| Image-grounded interpretation | `multimodal_interpreter.py`, `multimodal_models.py` | OpenAI vision reads the target image for semantic interpretation | better layout-class/role inference on styled targets | harness layout lane with image mode |
| One-HTML-bundle → all renderers | `pipeline.py`, `html_renderer.py`, `generic_html_renderer.py` | single HTML bundle rendered by every engine for fair comparison | **ADOPTED**: this is the seed of the locked Option-B HTML-source-of-truth renderer | becomes the product render path |
| Font embedding in DOCX | `docx_font_embedder.py` | embeds Lato etc. into generated DOCX | DOCX nice-to-have leg; embed fonts so Word shows the right face | DOCX export leg |

## Other preserved ideas (from other pruned candidates)

| Idea | Origin | Status |
|---|---|---|
| Preserve optional/custom source sections (no silent loss) | `block_aware_mapper.py` and `candidate_document_analyzer.build_candidate_block_binding_plan` review_required machinery | partially implemented in analyzer; block-aware shell deletable |
| Provenance/review-required mapping audit | `render_plan.py` ProductionRenderPlan | optional debug artifact; idea = inspectable mapping provenance |

## External references (filtered 2026-08-24 — only genuine differences vs this repo)

| Project | The one line that differs from what we have |
|---|---|
| [JSON Resume](https://github.com/jsonresume) | open-standard resume schema + theme registry + **ATS-readability validation** (we lack ATS checks) |
| [alibaba/SmartResume](https://github.com/alibaba/SmartResume) | ready resume-tuned **layout-detection model** (mAP 92.1%) + eval methodology — upgrade asset for our weak local analyzer; complements PDFMathTranslate/BabelDOC below |
| [DullyPDF](https://github.com/justin-thakral/DullyPDF) | confidence-scored AI field detection via the open-source **CommonForms** model — our backend confidence scoring already exists; only frontend UX differs |
| [template-goblin](https://github.com/JaiminPatel345/template-goblin) | `.tgbl` **portable template package**: ZIP of manifest + fonts + images, real binaries, font subsetting |
| [ResumeForge](https://github.com/Suhas-Koheda/ResumeForge) | **Tectonic/LaTeX render engine** — recorded fallback only if the HTML-master decision gate fails |
| [Byaidu/PDFMathTranslate](https://github.com/Byaidu/PDFMathTranslate) + [funstory-ai/BabelDOC](https://github.com/funstory-ai/BabelDOC) | DocLayout-YOLO ONNX local layout detection (CPU-runnable) + `char_width/char_disp` glyph-width reflow math + BabelDOC intermediate-representation paper (arXiv 2605.10845) = authoritative fallback if re-rendering fails the decision gate (moved from COMPETITIVE_LANDSCAPE 2026-08-24) |
| [zai-org/UI2Code_N](https://github.com/zai-org/UI2Code_N) | ICML 2026: replication as **closed-loop visual optimization** (render → inspect → refine); open source — paradigm validation for our inner loop |
| [Ivkalu/agentic-pdf-reconstructor](https://github.com/Ivkalu/agentic-pdf-reconstructor) | multi-agent rebuilds **pixel-accurate PDFs from screenshots** (write → compile → visually evaluate → refine until match) — closest existing artifact to this product; differs only in that it copies content too |
| PaperFit (arXiv 2605.10341) | vision-in-the-loop typesetting optimization: overflow severity / whitespace are 2D judgments rule tools cannot see — academic basis for render-precheck step ⑤ |

Excluded as identical-in-kind: YData `generate_from_template` (same reference->skeleton->fill shape as our pipeline; YData steal-list entry removed from COMPETITIVE_LANDSCAPE 2026-08-24).

### Implementation-status audit (verified against code, 2026-08-24)

Already partially implemented — do NOT re-treat as gaps:
- DullyPDF confidence UX → backend done (`candidate_document_analyzer`
  per-field confidence; `design_schemas` mapping-confidence + human-review
  threshold + `shrink_to_fit` policy); only frontend UX missing.
- PaperFit/UI2Code^N deterministic half → done (`layout_proof_validation.py`:
  `compare_pdf_layouts` pixel/structure compare, `validate_proof_structure`,
  `build_visual_comparison_evidence`). Missing half is only the VLM-judgment
  inner loop.
- pdf-template-engine normalized coords → deliberate absolute-pt schema;
  normalization only matters for cross-page-size portability (YAGNI).
Deliberately opposite architecture (decisions, not gaps): SatvikPraveen
browser-side processing (we are server-side by privacy design); ResumeForge
Tectonic/LaTeX (HTML master LOCKED; LaTeX = recorded fallback only).

Genuine open gaps (4 clusters):
- A. Vision-closed-loop AI half (UI2Code^N / agentic-pdf-reconstructor /
     PaperFit) → tracked as the unverified proposal in `active.md`.
- B. Reflow math + local ML detection + IR fallback (PDFMathTranslate/
     BabelDOC, SmartResume): zero glyph-width fitting code, zero ONNX/YOLO
     models, no IR-style degradation path.
- C. Portable template package (template-goblin `.tgbl`, JSON Resume themes):
     `design_cache` stores request/proposal/trace JSON only — no fonts or
     assets bundled. Needed only for cross-org template sharing.
- D. ATS readability checks (JSON Resume): PRODUCT_SPEC declares ATS out of
     scope — reopening is an owner decision, not an oversight.

Code-level verification (2026-08-24): all 11 external references cloned and
confirmed against source. Two additional stealable patterns found:
- Analyzer feedback-history + escalating specificity (agentic-pdf-
  reconstructor): the critique agent tracks fixed/persisting issues, raises
  suggestion specificity when ignored ("fix margins" → concrete parameter),
  ranks by impact, and judges good-enough — adopt as the inner-loop critic
  behavior spec instead of repeating vague complaints each round.
- Render-then-parse-back test (@jsonresume/ats-validator phase 2): after
  rendering, feed the output back through a text extractor as a deterministic
  guard (completeness, order, clipped text) — zero-AI check to run before the
  visual critic in step ⑤.
Also noted: UI2Code^N paper shows quality scaling with refinement steps
(informs inner-loop round budget); DullyPDF uses the open-source CommonForms
detection model (another local-detector candidate); pdf-template-engine is a
form-field filler on fixed PDFs — closer inspection downgrades its relevance.

## Market landscape (merged from deleted `docs/research/COMPETITIVE_LANDSCAPE.md`, 2026-08-24)

Validated pain across all competitors: recruiters burn ~20 minutes per CV on
manual reformatting; agencies want branded consistency.

| Product | One line |
|---|---|
| [iflock.io](https://www.iflock.io/) | AI CV formatter for agency recruiters; formats into your custom template; anonymisation — closest peer by audience and workflow |
| [GorillaWorks Gorilla Resume](https://gorillaworks.io/resume-formatting-software) | branded resume formatting for staffing firms; proprietary parser extracts work history/education/skills then restructures into your template; 500+ firms since 2018 |
| [Saply.ai](https://www.saply.ai/executive-search/) | executive-search CV automation (10s formatting) + agentic AI editor inside Word/Google Docs + candidate matching scores — high-end segment, feature-expanding |
| [Resumaro](https://resumaro.com/ai-recruiting-software/) / [Resume Optimizer Pro](https://resumeoptimizerpro.com/Home/Recruiters) | branded resumes + versioning + ATS integrations / white-label resumes via UI or API |

**Key observation:** every competitor ships a curated brand-template library
("pick your template"). None attempts pixel-exact replication of an arbitrary
client-supplied target. Open source is the same shape (structured data + fixed
template → PDF/DOCX: JSON Resume ecosystem, md2cv, resume2latex). Exact
replication remains unproven commercially and technically anywhere else — it
is this product's core invariant.

**Pricing is dual-track** (verified 2026-08-24): seat subscriptions (~£20–£950
per month published) coexist with per-CV pricing ($0.99 FormaCV — used by HAYS
at scale, $1 Candidately); "no per-seat fees" is itself marketed as a
differentiator. Other active players: RemakeCV, iReformat (Bullhorn-first),
CVFormatter, HireAra, Daxtra Styler, Allsorter, Govidis.

## Deletion record

- `target_replica/` (tests/commercial_api/target_replica) — deleted 2026-08-22
  after the read-only boundary was lifted, the ideas were catalogued, imports
  were re-verified, and a clean 469-passed pre-deletion baseline was recorded.

## Coverage

- Deep: ideas inventoried from file contents read during orientation.
- Skipped: full bodies of each pipeline (not needed for the catalog).
