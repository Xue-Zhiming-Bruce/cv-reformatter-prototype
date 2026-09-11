# C1 全矩阵收敛 · 完成判定总报告（交 owner 矩阵终审）

> 日期：2026-09-11。维护：全矩阵收敛 Orchestrator（第二任期）。
> 本报告 = 六对矩阵的最终交付与判定汇总；终审材料 = 本目录六对 side-by-side + 终版记分板。

## 1. 判定：六对全绿，方法收敛成立

| pair | 冷启动冻结 | 新一般规则 | 终态 run | 判定 |
|---|---|---:|---|---|
| D→E | 0 | 0 | c_pipeline_D_to_E_20260910T200018Z | ✅（阶段三回归 + orchestrator 核验） |
| E→F | 14（历史） | 12+9 | c_pipeline_D_to_E_20260910T195515Z | ✅（owner 终审待本次矩阵终审） |
| F→E | 1 | 1（bullet 字形结构判定） | c1_matrix_FE2_20260910T200958Z | ✅ 0-1 判据成立 |
| E→D | 5（实现期冻结，逐层确定性闭环） | +1 = #8 选项 B 契约 | c1_matrix_ED_B_20260911T044203Z | ✅ 全绿 |
| D→F | 3 | 0 | c1_matrix_DF_B | ✅ 全绿 |
| F→D | 1 | 0 | c1_matrix_FD_B_20260911T061127Z | ✅ 全绿 |

**止损线**：四对冷启动合计新一般规则 = 8/8 **到线未越**（owner 预批准线内最后 1 次额度
= E→D 的 #8；D→F/F→D 均零新规则）。**未触发止损线上报**。

## 2. 本任期四项 Phase 交付（全部经 orchestrator 独立核验）

### Phase 0 · a_pipeline.py 源码重建（owner 预批）
- 事故机制定案（proposal §15）：18:48:45Z heredoc 写盘语句误写 `s`（c_pipeline 内容）
  覆盖 a_pipeline.py；真实终态 = pyc 状态 + 两个 owner 批准编辑（其一已落盘、
  其二载荷逐字在同 heredoc NEW 文本中恢复）。
- 验收：**72/74 顶层函数与 pyc 逐字节一致**（含全部嵌套闭包/常量）；
  `_export_pinned_html_to_pdf` = pyc+编辑一（预期差异）；
  `run_builder_repair_loop` 残差 = comprehension 过滤器跳转编码极性（3.12 beta 期
  编译器；两形状行为等价，如实入档）；+`_per_line_edges`/`_line_stability`（编辑二）。
  测试 121/121 → dir()/签名对照表 runs/a_pipeline_reconstruction_20260911/。

### Phase 1 · E→D（选项 B 契约）
- owner 预批选项 B 落地：多 item 条状行→bar_section 划归 section（规则 #8）、
  header 收窄 name/contact/tagline、location 并入 contact 行。
- 终态 run 044203Z：三出口额度内、hard gates PASSED、分层 19 PASS/0 FAIL、
  Charter 家族实测嵌入、**目标条状文本零泄漏实测**、123/123 回归。

### Phase 2 · Architect 消融（owner 裁决执行）
- 四对（owner 三对 + F→E 对照）确定性派生拓扑 ≡ 实际采用 Architect 拓扑
  （fingerprint 全等、validate 0 failures、其余候选冗余/确定性拒收）→ **选项 C 落地**：
  Architect LLM 调用从 run() 删除（确定性派生 + --topology override 保留）；
  ADR 草稿 runs/architect_ablation_20260911T051500Z/ADR_DRAFT.md **待 owner 终审**。
  新单测四对离线对照防漂移；124/124。

### Phase 3/4 · D→F、F→D 冷启动（选项 B 契约下）
- D→F：3 次冻结（渲染侧联系行结构判定=债务族、分隔符门禁对齐自述契约=勘误、
  连字符断词延续锚=测量层），修复后一次通过；47 PASS/0 FAIL、Roboto 嵌入、
  双渲染 0.0pt/55 行；源 tagline/bar 内容为源逐字必渲（零污染）。
- F→D：1 次冻结（静态模板 dash 无出处文本 invariant-① 拒收判得对→第三类规范化
  remove_unprovenanced_punctuation_nodes）后一次通过；26 PASS/0 FAIL、Charter 嵌入、
  双渲染 0.0pt/46 行；**D 目标样本事实零泄漏实测**（4 标记 0 命中）；
  tagline/bar 行按选项 B 规则 4 渲染为空。
- 全量测试终态：**126/126**（124 基线 + 2）；仓库全量 625 passed/1 skipped。

### 基础设施修复（随行）
- `main(); main()` 双跑缺陷修复（corrupt 取证证实为遗留潜伏）；124/124。
- orchestrator 记分板非原子写截空事故：按写序重放恢复（基线取证 + 5 载荷）；
  教训升级为硬纪律：**仓库文书一律原子写（temp+rename）**。

## 3. 校准账本状态

Final Reviewer（qwen3-vl-plus）在全部新 run 的 verdict 均为 accepted=false，
逐条归类为在案误判类（捏造缺陷/索要目标自身设计/误读实测），已入
JUDGE_CALIBRATION_LEDGER.md，未驱动任何渲染改动——与 §13 文献结论一致
（VLM-as-Reviewer 仍为诊断参考，不作为验收依据）。

## 4. 待 owner 终审项
1. **六对矩阵终审**：本目录各对 target.pdf / generated.pdf / side_by_side.png；
2. **ADR 草稿终审**：Architect 删除（选项 C）是否进 docs/decisions/；
3. 记分板两笔在案裁决确认：右缘门禁勘误（已按注释视为批准）、CMS Y 9 符号字
   vendor（许可决定，不在 orchestrator 权限内）；
4. E→D 冷启动收敛成本定性：5 次实现期冻结（选项 B 契约实现本身所需），
   "0-1 冻结判据"在该对以"实现完成后首个完整 run 一次通过"为准——如 owner
   认为应按 5 计，F→E 判据的表述需在下一版本记分板中区分"实现期"与"收敛期"。

## 5. 遗留与风险
- `run_builder_repair_loop` 跳转编码残差（Phase 0 入档，行为等价）；
- Architect 删除的 live 单对重放未执行（离线四对证据充分）；
- a_pipeline 重建的两处文档化残差（见 §15）。
