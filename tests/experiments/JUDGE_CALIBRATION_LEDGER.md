# Judge calibration ledger

## 2026-09-10 — C1 D→E run `20260909T182649Z`

Reviewer: `qwen3-vl-plus`

The revised prompt improved one behavior: the Reviewer explicitly recognized that phone, email, and location do not exist in SOURCE and treated their absence as expected.

The rejection still contains the following calibration failures:

- It claimed the LinkedIn value showed only `USER`; the rendered output and normalized HTML show `linkedin.com/in/USER`.
- It compared the source-backed GitHub and LinkedIn values with TARGET candidate facts and called the difference an incorrect substitution.
- It treated source-verbatim heading wording, capitalization, and the ampersand in `Education & certifications` as defects, despite the supplied heading-scope fact.
- It used inherited body layouts and candidate-only sections as rejection reasons outside the C1 header pilot.
- It claimed three pages and content overflow; the generated artifact has two pages and both deterministic pagination checks report `overflow: PASS`.

No valid measured header-geometry defect was identified. The deterministic fit recorded a maximum target-role delta of 0.6 pt. Preserve the raw verdict as `accepted: false`; do not use this verdict as evidence for changing frozen body presentation or source content.

## 2026-09-10 — C1 D→E run `20260909T185635Z`

The run stopped at deterministic render gates before review. Final Reviewer calls: 0; no verdict or new calibration evidence exists for this run.

## 2026-09-10 — C1 D→E run `20260909T191925Z`

The run stopped after the bounded Filler retry, before section fitting and final rendering. Final Reviewer calls: 0; no verdict or new calibration evidence exists for this run.

## 2026-09-10 — C1 D→E run `20260909T193108Z`

The run stopped at topology validation. Final Reviewer calls: 0; no verdict or new calibration evidence exists for this run.

## 2026-09-10 — C1 D→E run `20260909T183854Z`

The run stopped at the single-Filler deterministic gate. Final Reviewer calls: 0; no verdict or new calibration evidence was produced.

## 2026-09-10 — C1 D→E run `20260909T195451Z`

Reviewer: `qwen3-vl-plus`. First bare run to complete all three exits after the approved
modifications (slot relocation normalization, Architect bounded retry, closed-loop section
indent fitting, row-overlap rejection). Call counts within approved budget: Architect 1
(no retry needed), Filler 2, Final Reviewer 1. Hard gates: PASSED. Section x0 fit: all 8
sections within 0.008 pt of target 46.909 pt in 2 renders. Verdict: `accepted: false`.

The rejection repeats known calibration failures and adds one fabrication:

- It claimed phone and email values were present as plain text without icons. Neither the
  source nor the generated header contains a phone or email value; the contact row renders
  only the source-verbatim `github.com/USER` and `linkedin.com/in/USER` with Font Awesome
  icons. The claim is fabricated.
- It called the `USER` profile paths an incomplete-personalization defect. `USER` is the
  source-verbatim value (redacted web-copy source); comparing it with target candidate
  facts is the same source/target confusion recorded for run `20260909T182649Z`.
- It again treated source-verbatim heading wording, capitalization, and the ampersand in
  `Education & certifications` as defects, despite the heading-scope instruction.
- It again used inherited body layout (multi-column skills table, right-aligned metadata),
  page count, pagination continuation, and bullet/colon entry format as rejection reasons
  outside the C1 header pilot scope; the source uses colon-separated key-skill lines
  verbatim.

No valid measured header-geometry defect was identified. Preserve the raw verdict as
`accepted: false`; do not use this verdict as evidence for changing frozen presentation or
source content. Owner review of `target.pdf` / `generated.pdf` / `side_by_side.png` remains
the acceptance decision.

## 2026-09-10 — C1 D→E run `20260910T033728Z`

Reviewer: `qwen3-vl-plus`. First bare run under the owner-approved tiered body-line
acceptance contract (all content lines vs target-measured tiers: L1 46.909 pt, bullet
dot 60.000 pt, level-2/wrapped 70.909 pt). Call counts within approved budget:
Architect 1, Filler 1, Final Reviewer 1. Hard gates: PASSED. Tiered body-line fit
converged in 2 renders; acceptance table 54 PASS / 0 FAIL. Verdict: `accepted: false`.

The rejection repeats recorded calibration failures:

- It claimed phone/email "placeholder text" in TARGET and missing icons; the source
  contains no phone or email and the header renders only the source-verbatim
  GitHub/LinkedIn values with Font Awesome icons.
- It claimed a "Page 3"; the generated document has exactly 2 pages (both
  deterministic pagination checks report `overflow: PASS`).
- It called the `→` and `⌣` glyphs non-source-backed typographic elements; they are
  verbatim source text (source lines 34-38).
- It again used inherited body presentation (bold roles, section rules, single-column
  skills layout, bullet style, page-continuation cues) as rejection reasons outside
  the C1 header pilot scope and contrary to the frozen-presentation instruction.

No valid measured header-geometry defect was identified. Preserve the raw verdict as
`accepted: false`; owner review of `target.pdf` / `generated.pdf` / `side_by_side.png`
remains the acceptance decision.

## 2026-09-10 — C1 D→E run `20260910T065420Z` (body rollout)

Reviewer: `qwen3-vl-plus`. First bare run under the C1 body rollout (heading +
entry tiers added to the tiered acceptance contract). Call counts within
approved budget: Architect 1, Filler 2 (attempt 1 failed provenance gates —
L0011 heading/body split; deterministic gates caught it; attempt 2 clean),
Final Reviewer 1. Hard gates: PASSED. Header fit max delta 0.6pt (equal to the
§9.1 terminal state). Body fit converged in 3 renders; acceptance table
62 PASS / 0 FAIL (header 0.1/0.6pt, headings 8/8, entries 5/5, existing tiers
no regression). Verdict: `accepted: false`, 11-point diagnosis.

The rejection repeats recorded calibration failures:

- Claimed the header "omits the standard contact icon set (phone, email…)
  present in TARGET" and demanded a "clean, single-line role title as in
  TARGET" — target-sample facts demanded as corrections (source has no
  phone/email/location; title and tagline are verbatim source content).
- Called the verbatim source heading casing ("Work experience" vs TARGET's
  "Experience") and the verbatim heading wording ("Education & certifications")
  defects — the Filler prompt explicitly states headings are verbatim SOURCE
  content and their wording/casing are not defects.
- Demanded single-column skills list "as in TARGET" — the multi-column skills
  grid is the TARGET template's own design carried by the frozen seed.
- Fabricated "Key skills (page 3)" — the document has exactly 2 pages (both
  deterministic pagination checks PASS; Key skills is on page 2).
- Called the `→`/`⌣` glyphs and dash separators unauthorized — verbatim source
  text (source lines 34-38), same misjudgment as the previous entry.
- Misread the measured rule design ("rule placed below the heading whereas
  TARGET places rules above"): the rendered rules sit above each heading by
  the compiled measured gaps, verified 11.2/12.3/6.1pt against target
  measurement in the acceptance table.

No valid measured defect was identified. Preserve the raw verdict as
`accepted: false`; owner review of `target.pdf` / `generated.pdf` /
`side_by_side.png` remains the acceptance decision.

## 2026-09-10 — C1 E→F run `20260910T172540Z`（泛化验证收官）

Reviewer: `qwen3-vl-plus`。首个跨 pair 泛化裸跑（源=resume_E，目标=resume_F，
版式/字体/条目结构与 D→E 完全不同）。调用额度内：Architect 1、Filler 2、
Final Reviewer 1。Hard gates: PASSED（字体目标实测过滤、渲染感知
duplicate-bullets、右缘行级修正全部生效）。分层表 17 PASS / 0 FAIL。
Verdict: `accepted: false`，6 条诊断。

拒绝重复在案误判类：

- 称姓名"未加粗"——姓名样式来自 F 目标实测（24.787pt bold），编译直接采用；
- 评源逐字标题大小写（'skills'/'experience'/'education' 为源逐字 + 冻结的
  大小写呈现规则，prompt 明文豁免）；
- 评位置放置与 TARGET 不同——位置是源逐字内容与 C1 extension 语义的产物；
- 评公司加粗"TARGET 不加粗"——继承自目标模板的冻结呈现。

无有效实测缺陷。`accepted: false` 原样保留；owner 终审以 target.pdf /
generated.pdf / side_by_side.png 为准。

## 2026-09-11 — C1 E→F run `20260910T195515Z`（收官全绿）

Reviewer: `qwen3-vl-plus`。三出口额度内：Architect 1 / Filler 1 / Final
Reviewer 1。Hard gates: PASSED；分层 26 PASS / 0 FAIL；字体 Roboto 实测嵌入
（无 Arial、无 substitution）；双渲染逐行稳定性 0.0pt。Verdict:
`accepted: false`，8 条诊断逐条归类为在案误判类：

- 称姓名加粗/间距"缺乏 TARGET 的一致性"——姓名样式来自 F 目标实测编译
  （索要目标自身设计的在案类）；
- 评 location 独立成行 vs TARGET 内联——location 是源逐字内容与 C1 extension
  语义的产物（评源逐字内容类）；
- 称 EXPERIENCE 的 rule 在标题上方而 TARGET 全部在下方——F 目标实测无标题下
  规则线（Rule rows 表实测为空集），rule 位置由目标实测声明（误读实测类）；
- 称标题缩进不一致——三个标题 x0 实测 36.000，Δ0.000（可测缺陷捏造类）；
- 称 bullet 为"标准圆形"而 TARGET 为"实心圆"——F 目标实测零 bullet 设计，
  bullet 层透明（bullet 裁决在案类）；
- 称 SKILLS 标题与首 bullet 间距过大——heading→content 间距实测 Δ≤0.223pt
  （≤1pt 门内；可测缺陷捏造类）；
- 称 EXPERIENCE 日期列右缘不一致——entry 表 7/7 PASS，日期行右缘
  575.994–575.999 ≤ 边界 576.000（overshoot 0.000pt；可测缺陷捏造类）；
- 称 rule 短于节宽非全宽——同 rule 实测项，目标证据无标题下规则线（误读实测类）。

无有效实测缺陷。`accepted: false` 原样保留；owner 终审以 target.pdf /
generated.pdf / side_by_side.png 为准。

## 2026-09-11 — C1 D→E 回归 run `20260910T200018Z`（阶段三新机具回归验证）

Reviewer: `qwen3-vl-plus`。裸跑（source=resume_D，target=resume_E）。调用额度内：
Architect 1 / Filler 1 / Final Reviewer 1。Hard gates: PASSED（0 failures，
fail_closed_font_gate 无失败；字体 PDF 实测 = Lato-Bold/Lato-Regular/Lato-Italic +
FontAwesome6Brands + ArialMT + AppleSDGothicNeo，approved_substitutions_used =
['Font Awesome 6 Free']，透明记录）。逐行稳定性 60 行 max|Δ| = 0.0pt。
分层表 80 PASS / 0 FAIL（L1 19 行 Δ0.001pt；heading case 7/7 sentence 路径；
entry 右缘 overshoot 0.000pt）。Verdict: `accepted: false`，8 条诊断。

拒绝重复在案误判类：

- 称 LinkedIn 值缺图标、缺 phone/email 图标——D 源联系行无图标声明（目标实测
  联系行有图标但源值集合不同；图标呈现属冻结呈现规则，prompt 明文豁免）；
- 称姓名字体粗细/字号与 TARGET 不一致——姓名样式来自目标实测编译，目标候选
  姓名（Daniel Phang）与源候选姓名（J. Doe）字体本就按各自目标测量；
- 评源逐字标题措辞与大小写（'Skills pool' 等）——heading case 表 7/7 PASS，
  sentence case 为编译级合同，prompt 明文豁免；
- 称 'Another section' 缺标题下规则线——rule rows 表：目标版式无标题下规则线
  （_none_，呈现跟随目标；误读实测类）；
- 称正文间距/缩进/列宽不一致——L1 全 19 行 Δ0.001pt、entry 表 9/9 PASS
  （可测缺陷捏造类）；
- 称页数与分页孤儿——页数因内容量豁免（prompt 明文）；分页 hard gate PASS。

无有效实测缺陷。`accepted: false` 原样保留；owner 终审以 target.pdf /
generated.pdf / side_by_side.png 为准。

## 2026-09-11 — C1 F→E cold-start run `20260910T200958Z`（阶段四）

Reviewer: `qwen3-vl-plus`。F→E 冷启动首轮 200414Z 在 hard gates 冻结（duplicate-bullets
按设计拒收：目标 E 有实测 bullet 设计，CSS marker + 源字面 • 双重渲染——一般规则修复
"目标声明 bullet 设计则行首列表字形转 CSS marker"后重跑全绿），未到达 Reviewer。
本轮（重跑）出口额度内：Architect 1 / Filler 1 / Final Reviewer 1；gates PASSED
（Lato 实测嵌入、substitutions=[]、逐行 0.0pt、69 PASS/0 FAIL）。Verdict:
`accepted: false`，8 条诊断逐条归类为在案误判类：

- 索要 header location 槽位（位置为源逐字内容与 C1 extension 语义的产物，同 D→E 在案类）；
- 评图标用法与 TARGET 不一致（图标呈现为目标实测派生，同在案类）;
- 评标题措辞/标点与 TARGET 不一致（prompt 明文豁免，源逐字 + 冻结呈现）;
- 评 rule 存在性与 entry 行排列（呈现跟随目标模板/源结构，冻结件）;
- 评 bullet 缩进（bullet 层几何由分层 fit 门控，全绿在表）。

无有效实测缺陷。`accepted: false` 原样保留；owner 终审以 target.pdf /
generated.pdf / side_by_side.png 为准。

## 2026-09-11 — C1 E→D cold-start run `20260911T044203Z`（Phase 1，owner 选项 B 契约）

Reviewer: `qwen3-vl-plus`。首个 E→D 全绿裸跑（选项 B：interests 条划归 section，
header 拓扑 name/contact/tagline）。调用额度内：Architect 1 / Filler 1 / Final
Reviewer 1。Hard gates: PASSED；分层 19 PASS / 0 FAIL；Charter 实测嵌入。
Verdict: `accepted: false`，9 条诊断，逐条归类为在案误判类：

1. Header: Name 'Daniel Phang' uses bold sans-serif; TARGET uses bold serif (e.g., 'J. Doe'). —— 字体/样式类——渲染字体来自目标实测编译（索要目标自身设计的在案类）
2. Header: Contact line uses monospace font for email/URLs; TARGET uses standard serif with icons. —— 字体/样式类——渲染字体来自目标实测编译（索要目标自身设计的在案类）
3. Header: Missing icon glyphs for phone, email, LinkedIn, GitHub (TARGET shows icons; C1 uses plain text). —— 图标类——图标呈现由目标实测声明（图标呈现属冻结呈现规则）
4. Section headings: 'EXPERIENCE', 'SKILLS', 'EDUCATION' are uppercase purple; TARGET uses title-case with varied colors and underlines/rule lines. —— 标题大小写——编译级合同，prompt 明文豁免
5. Section headings: Missing horizontal rule lines below headings (e.g., after 'EXPERIENCE', 'SKILLS', 'EDUCATION') as in TARGET. —— 在案误判类（源逐字/呈现跟随目标/可测缺陷无实测支撑）
6. Body typography: Bullet points use solid circular bullets; TARGET uses hyphen or arrow-style list markers. —— 在案误判类（源逐字/呈现跟随目标/可测缺陷无实测支撑）
7. Spacing: Inconsistent vertical spacing between sections and list items compared to TARGET’s structured rhythm. —— 在案误判类（源逐字/呈现跟随目标/可测缺陷无实测支撑）
8. Work experience entries: Location/date alignment differs—TARGET right-aligns dates with thin vertical separator; C1 uses inline pipe with inconsistent spacing and no separator line. —— 分隔符呈现为目标实测派生（编译所有）
9. Skills section: Uses colon-separated categories without visual grouping; TARGET uses multi-column layout with color-coded category headers and clear separation. —— 在案误判类（源逐字/呈现跟随目标/可测缺陷无实测支撑）

无有效实测缺陷。`accepted: false` 原样保留；owner 终审以 target.pdf /
generated.pdf / side_by_side.png 为准。

## 2026-09-11 — C1 F→D cold-start run `20260911T061127Z`（Phase 4，最后一对，选项 B 契约）

Reviewer: `qwen3-vl-plus`。F→D 冷启动裸跑（source=resume_F → target=resume_D），
选项 B 契约下 D 为目标（header name/contact/tagline/bar_section；源无对应内容 →
tagline/bar 渲染为空，目标样本事实零泄漏实测）。出口额度内：Architect 0（选项 C，
确定性派生）/ Filler 1（attempt 1 一次通过）/ Final Reviewer 1。Hard gates:
PASSED；分层 26 PASS / 0 FAIL；Charter-Roman/Bold/Italic 实测嵌入 + Arial-BoldMT
子集（分隔符/回退字形，与冻结 E→D 044203Z 同类透明记录）；双渲染逐行 0.0pt/46 行；
contact 分隔符 '—'×3 PASS。实现期有一个前置失败 run `20260911T060243Z`（Filler
两次保留模板静态 summary-line '—' 分隔符 span → invariant-① 无出处文本拒收）——
修复为 remove_unprovenanced_punctuation_nodes（第三类唯一无歧义规范化：内容为空、
不可标注（owner 2026-09-08 裁决剥离伪造行号）、删源逐字无涉；同
remove_unused_placeholders 静态模板残留清理家族），修复后 run 061127Z 一次通过。
Verdict: `accepted: false`，17 条诊断，逐条归类为在案误判类：

1. 索要 HIGHLIGHTS 节（源 F 无该节；按源语义确定性覆盖——在案类：把目标节目录当源目录）；
2. SUMMARY 行索要 em-dash 与红色描述（目标模板静态分隔符/样式；E→D 冻结先例同样无 dash 渲染——呈现跟随源+门禁在案类）；
3. 标题颜色/规则线颜色系列条目（渲染颜色来自目标实测编译；同 E→D 在案类"索要目标自身设计"）；
4. 标题大小写/字体族条目（编译级合同，prompt 明文豁免——在案类）；
5. 图标与 contact 分隔符对齐条目（图标/分隔符呈现为目标实测派生，编译所有——在案类）；
6. "Page 3" 条目（实测渲染 2 页——账本在案捏造页数类）；
7. 正文字体族一致性条目（字体为目标实测 Charter 家族——在案类）。

无有效实测缺陷。`accepted: false` 原样保留；owner 终审以 target.pdf /
generated.pdf / side_by_side.png 为准。
