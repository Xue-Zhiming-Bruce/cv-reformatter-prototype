# C2 Resume I — Blind Target-Structure Audit (frozen recognizer `734ae55`)

Status: `Research / experiment evidence — awaiting owner review. Pipeline C2
remains experimental and NOT accepted.`

Date: 2026-09-18. Branch `experiment/pipeline-c2`, worktree
`/private/tmp/cv-converter-c2`. Recognizer and all C2 code frozen at commit
**`734ae55`** (verified before and after; nothing modified for this audit
except the local provenance README and this report). No LLM/VLM call; no
candidate conversion; no rendering; no rule tuning after seeing the pages.

Authorized inputs (owner confirmation 2026-09-18, recorded in
`tests/local_datasets/resume_matrix/README.md`): Resume I is a FAKE resume;
exactly ONE Adobe layout-analysis call was permitted and made; raw + normalized
results cached at `tests/experiments/runs/target_cache/af6b9234ca96948b7b34de9e535c452ef26a60b7508cfc3a8189fe56e5b1b3d8/`.

- Input: `tests/local_datasets/resume_matrix/resume_I.pdf`,
  sha256 `af6b9234ca96948b7b34de9e535c452ef26a60b7508cfc3a8189fe56e5b1b3d8`,
  50,105 bytes, LuaTeX 1.15.0, 2 pages (595.3×841.9 pt).
- Blind run (prediction written BEFORE any page/content viewing):
  `tests/experiments/runs/c2_resume_i_blind_20260917T115551Z/`
  (`recognition_I.json` write-once, `manifest.json` with commit + command +
  input hash, Adobe raw/normalized copies, page PNGs). One Adobe call total;
  a first attempt aborted with a configuration error BEFORE any network
  request (worktree had no credentials; credentials were sourced at runtime
  from the main checkout environment; no secret copied into the worktree).

Headline: the recognizer reported **`status=ok`, 0 unresolved** for a document
where (a) over half of the raw text leaves never reached it and (b) almost
every predicted role is wrong. This audit is therefore primarily a record of
**confidently wrong** output.

---

## 1. Ground truth (human annotation, from the two page images)

Template: two-column LuaTeX layout — a narrow LEFT sidebar whose all-caps
section labels are RIGHT-aligned against a long horizontal rule; all content
sits in the RIGHT main column. NOT the G/H template family (G/H: PDFium,
single-column, Penn State Behrend). Page 2 repeats the sidebar pattern.

- **Sections (9):** CONTACT INFO, ABOUT ME, EXPERIENCE, EDUCATION (p1);
  ACHIEVEMENTS, PUBLICATIONS, CONFERENCES, SKILLS, REFERENCES (p2). Sidebar
  labels are all-caps 12pt bold; 9 measured rules match them 1:1.
- **Dated entry heads (6):** EXPERIENCE ×2 (`JOB TITLE`+2020-2023;
  `ANOTHER JOB TITLE`+2016-2019), EDUCATION ×2 (`MSc IN BREWING BEER`+2020-2023;
  `BA IN PROCRASTINATING`+2016-2019), CONFERENCES ×2 (`SPACE SUMMIT`+2022;
  `COOL FANCY CONFERENCE IN HYPE WORDS`+2019). Bold title + right-aligned date
  on one line; a second line with the organization/location in italic.
- **Detail roles:** italic org/location lines under each head; one plain
  parenthetical detail line (`(the most delicious division)`); two reference
  blocks (bold two-line name + regular multi-line address).
- **Hierarchy:** each dated entry owns its bullets (9 entry bullets:
  2+1 experience, 2+2 education, 1+1 conferences). ACHIEVEMENTS has TWO
  label-column groups (`Awards` 2 bullets, `Scholarships` 3) — labels in the
  main column at x≈178, their bullets indented further at x≈270. SKILLS has a
  `Languages` label + 3 bullets, plus 3 skill RATING rows (●●●/●●○/●○○ dot
  glyphs + text) that are not bullets. PUBLICATIONS has `Articles` (2 entries)
  and `Posters` (1) groups with inline bold author names inside the text.
- **Same-line mixed styles:** bold label + regular value rows in CONTACT INFO;
  regular date next to bold title; italic runs inside org lines; bold author
  names inside regular publication text.
- **Bullets:** 17 visible round bullet markers (9 entry + 8 group bullets).

## 2. Evidence inventory (what the recognizer actually received)

Raw Adobe response: 122 text leaves. Normalized evidence: 58 text blocks.
**64 raw text leaves (52%) were dropped by normalization.** The drop filter is
`_structural_children_are_supported` in `app/template_analysis/commercial/adobe.py`:
a `P` paragraph whose path contains `/Table/` is excluded, and this LuaTeX
template puts nearly all main-column content inside table cells. Dropped:
both EXPERIENCE org lines, `JOB TITLE` head (block level; string survives in
`full_text`), `(the most delicious division)`, EDUCATION org line, one
conference bullet, both PUBLICATIONS contents, all 3 SKILLS rating rows, both
REFERENCES address blocks, plus LI/Lbl bullet markers (same filter family as
the G/H marker loss). One item is missing from the RAW Adobe output itself:
the bullet `Graduated with summa cum laude`.

## 3. Per-item audit (prediction recorded blind → annotation)

Role abbreviations: P1/P2 = page; el.N = `adobe.page.N.element.N`. Every
prediction below is from the frozen run.

### 3.1 Section boundaries — 1/9 correct

| True section | Region / evidence | Blind prediction | Judgment |
| --- | --- | --- | --- |
| CONTACT INFO | p1 y179.4, el.2 (p1) | `page_header` | ✗ wrong |
| ABOUT ME | p1 y308.3, el.11 (p1) | `page_header` | ✗ wrong |
| EXPERIENCE | p1 y432.5, el.13 (p1) | `page_header` | ✗ wrong |
| EDUCATION | p1 y614.1, el.16 (p1) | `page_header` | ✗ wrong |
| ACHIEVEMENTS | p2 y42.2, el.30 (p2) | `section_heading` | ✓ correct |
| PUBLICATIONS | p2 y197.9, el.31 (p2) | `continuation` (silent) | ✗ wrong |
| CONFERENCES | p2 y359.7, el.42 (p2) | `continuation` (silent) | ✗ wrong |
| SKILLS | p2 y506.9, el.50 (p2) | `bullet` | ✗ wrong |
| REFERENCES | p2 y663.9, el.55 (p2) | `bullet` | ✗ wrong |

Mechanism (recognition error, code-frozen): `left_margin` is the GLOBAL
minimum x0 across pages (48.0 = ACHIEVEMENTS itself); sidebar labels are
RIGHT-aligned so their x0 varies (48–110 pt) and only labels within 3 pt of
that global minimum pass the heading rule. ACHIEVEMENTS was detected first —
on PAGE 2 — so all of page 1 became `page_header`, and the remaining labels
fell to weaker rules.

### 3.2 Dated entry boundaries and title/date roles — 0/6 correct

| True entry (title + date) | Evidence | Blind prediction | Judgment |
| --- | --- | --- | --- |
| EXPERIENCE e1 `JOB TITLE`+2020-2023 | head NOT in normalized blocks (normalization loss) | none possible | ✗ missed — attribution: normalization |
| EXPERIENCE e2 `ANOTHER JOB TITLE`+2016-2019 | el.17+18 (p1), same line correctly grouped | `page_header` | ✗ wrong (pre-first-section gating) |
| EDUCATION e1 `MSc IN BREWING BEER`+2020-2023 | el.19+20 (p1) | `page_header` | ✗ wrong |
| EDUCATION e2 `BA IN PROCRASTINATING`+2016-2019 | el.23+27 (p1) | `page_header` | ✗ wrong |
| CONFERENCES e1 `SPACE SUMMIT`+2022 | el.43+45 (p2) | `bullet` | ✗ wrong |
| CONFERENCES e2 `COOL FANCY…`+2019 | el.46+48 (p2) | `bullet` | ✗ wrong |

No right-column date was ever given a date role. The line GROUPING correctly
merged title+date into one visual line in every surviving case — the failure
is purely role assignment.

### 3.3 Detail roles

- Italic org/location lines (`The Cool Company | Earth, Alpha quadrant`,
  `SweetWorld Technologies | Santiago, Chile`, `(the most delicious division)`,
  `Dutch Beer University | Urk, The Netherlands`): **absent from normalized
  evidence** (raw has them) → no prediction. Attribution: normalization loss.
- Reference address blocks (both), SKILLS rating rows (3), PUBLICATIONS
  contents (3 entries incl. inline-bold author runs): absent from normalized
  evidence → no prediction. Attribution: normalization loss.
- `Graduated with summa cum laude`: absent from RAW Adobe output.
  Attribution: raw extraction missing (the only category-1 item).

### 3.4 Hierarchy, bullets and attribution — 6/17 bullet lines correctly identified, 0 with correct parents

| True structure | Blind prediction | Judgment |
| --- | --- | --- |
| EXPERIENCE 2 entry bullets (el.14/15, p1) | `page_header` lines | ✗ no hierarchy predicted |
| EDUCATION 4 entry bullets (el.21/22/28/29, p1) | `page_header` lines | ✗ |
| ACHIEVEMENTS `Awards` group: label + 2 bullets | label MERGED into its first bullet line → one `bullet`; 2nd bullet `bullet` | ✗ group hierarchy destroyed |
| ACHIEVEMENTS `Scholarships` group: label + 3 bullets | same merge pattern → 1 merged + 2 plain `bullet` | ✗ |
| CONFERENCES e2 bullet (`• Gave a presentation…`, el.49) | `bullet` ✓ but attributed to nothing (its entry head was itself classified a bullet) | ◐ identity right, attribution meaningless |
| SKILLS `Languages` label + 3 bullets | label merged with first bullet → `bullet`; other two `bullet` | ✗ |
| 3 SKILLS rating rows (not bullets) | absent from evidence | ✗ (normalization) |
| 17 bullet markers | markers absent from normalized text (standalone `•` block el.21 survives once, inconsistently) | ✗ marker identity not derivable — normalization loss (same LI/Lbl filter as G/H) |

### 3.5 Same-line mixed styles

- Line grouping itself worked: title+date merged (5/5 surviving pairs), bold
  label + regular value merged (CONTACT rows), bold label + first bullet
  merged. The `mixed_weight` flag was recorded on those lines.
- No downstream meaning was ever assigned: every one of those lines ended in
  `page_header` or `bullet`. C2 expression is moot at this stage (no structure
  predicted to express); the G/H finding (single-style nodes cannot express
  mixed-weight runs) still stands unchanged.

## 4. "Confidently wrong" list — `status=ok`, `unresolved=0` throughout

1. Exactly ONE section predicted (ACHIEVEMENTS); 8 sections missed. The one
   hit is self-confirming: ACHIEVEMENTS's own x0 (48.0) defined the left
   margin the heading rule then required.
2. The ENTIRE page 1 — name, contact table, about paragraph, two full
   EXPERIENCE entries, two EDUCATION entries, 9 bullets — was classified
   `page_header` and silently accepted.
3. Five sidebar headings (`ABOUT ME`, `EXPERIENCE`, `EDUCATION`, `REFERENCES`,
   `SKILLS`) were classified `bullet` by the indentation fallback (its window
   `left_margin+1.5·body … left_margin+0.25·width` ≈ 64.5–196.7 pt swallows
   the whole main-column label edge at x≈178 and the right-aligned sidebar).
4. Two conference entry heads WITH right-column dates were classified
   `bullet` — the bullet fallback fires BEFORE the dated-head rule.
5. `Articles` and `Posters` were classified `bullet` although they have NO
   content lines at all in the evidence (their contents were dropped) —
   evidence-thin lines confidently role-assigned, no `unresolved`.
6. `PUBLICATIONS`, `CONFERENCES` labels were absorbed as silent
   `continuation` lines.
7. Reference names (`Capt. Jack Sparrow`, `Kathryn Janeway`) were classified
   `bullet`.
8. `unresolved=0` and `status=ok` were reported while 52% of raw text leaves
   never reached the recognizer. Fail-closed did not trigger once.

## 5. Strict failure attribution (no mixing)

| Category | Items |
| --- | --- |
| **原始提取缺失** (raw extraction missing) | 1 bullet: `Graduated with summa cum laude` (not in raw Adobe response). |
| **归一化丢失** (normalization loss) | 64/122 raw text leaves dropped by the `/Table/` P-filter in `app/template_analysis/commercial/adobe.py` (`_structural_children_are_supported`): EXPERIENCE e1 head (block level) + both org lines + division line, EDUCATION org line, 1 conference bullet, all PUBLICATIONS contents, all 3 SKILLS rating rows, both REFERENCES address blocks; plus LI/Lbl bullet markers (same filter family already recorded for G/H). Consequence: recognition was BLINDED to >half the document before it ran. |
| **结构识别错误** (recognition error, on the surviving evidence) | Global-min-x0 margin anchoring vs right-aligned sidebar labels (8/9 headings missed; page-1 wholesale `page_header`); no cross-page section gating; bullet-fallback window overreach (labels, entry heads, reference names → `bullet`); bullet rule precedence over dated-head rule; label-into-first-bullet merging destroying group hierarchy; content-free labels role-assigned instead of `unresolved`; `status=ok` with zero fail-closed. |
| **C2 无法表达** (recognized-but-inexpressible) | Not reached for most items (no structure predicted). Already-visible vocabulary gaps for THIS template should it ever be recognized: sidebar-label + rule section geometry (heading in a separate column from its section content), two-column label/content groups (`Awards`/`Scholarships`/`Languages` labels owning indented bullet columns), skill RATING rows (dot-glyph scales), mixed-weight runs (unchanged G/H finding). |
| **渲染问题** (rendering) | None observed — nothing was rendered in this round. Explicitly empty. |

## 6. What Resume I does and does not prove

- I is a **different template family** from G/H (LuaTeX two-column sidebar vs
  PDFium single-column Behrend). It is therefore neither same-family
  validation NOR valid cross-template generalization evidence — it is one new
  family sample on which the pipeline failed upstream of recognition.
- What it DOES show:
  1. Normalization's table-content filter is the dominant blocker for
     table-structured templates: recognition never saw >half the document.
     This is a deterministic pipeline defect with a deterministic fix
     (retain text leaves inside `/Table/` paths; retain LI/Lbl markers), not a
     reasoning problem.
  2. Two recognizer rules are not layout-robust even on surviving evidence:
     global-min-x0 margin anchoring breaks on right-aligned heading columns,
     and the bullet indentation fallback captures non-bullet content. Both
     are bounded, testable deterministic fixes (per-page/column margin
     estimation; fallback upper bound tied to the measured bullet column).
  3. The recognizer's fail-closed discipline did not fire where it must: a
     document with a majority of content missing and roles contradicting
     geometry still returned `ok`. A "recognition confidence gate" (e.g.
     share of text mass left unclassified or classified without an anchor
     section) is a deterministic safeguard worth designing.
- What it does NOT prove: that the recognizer can handle this template after
  fixes (untested), that fixes generalize (n=1), or anything about conversion
  quality (no conversion was run).
- Deterministic vs constrained VLM: I **strengthens** the case for staying
  deterministic first — the dominant failure is evidence destruction in
  normalization, and a VLM reading the current normalized evidence would be
  labeling a damaged input; the recognition-side errors are rule bugs with
  obvious deterministic repairs. A constrained VLM becomes a candidate only
  AFTER (a) normalization retains table content and markers and (b) the two
  rule fixes are re-audited blind; its plausible bounded role then would be
  labeling the two-column label/content group roles — which the retained
  x-column geometry may well decide deterministically anyway.

## 7. Artifacts

- Blind run (ignored): `tests/experiments/runs/c2_resume_i_blind_20260917T115551Z/`
  — `recognition_I.json` (write-once), `manifest.json` (commit `734ae55`,
  input sha256, command, one-Adobe-call record), `adobe_raw.json`,
  `enriched_evidence.json`, `page_I_page_1.png`, `page_I_page_2.png`.
- Target cache (ignored): `tests/experiments/runs/target_cache/af6b92…b3d8/`.
- Provenance/authorization: `tests/local_datasets/resume_matrix/README.md`
  (local, git-ignored by repository convention, like the G/H entry).
- Committed with this report: this file only. No PDF, cache, or images
  committed.
