# C1 Header 试点 · 状态快照（供新会话/goal 接手）

> **✅ 已完成并归档（2026-09-10）：owner 终审通过，试点完成判定已写入
> PIPELINE_EVOLUTION_PROPOSAL.md §9。本文件仅作历史交接记录保留，
> 不是活任务——后续工作见 §9.6 下一步清单。**
>
> 更新：2026-09-10。本文件曾是 C1 D→E 试点的权威状态。
> 背景文档：`PIPELINE_EVOLUTION_PROPOSAL.md` §4/§7（试点规格与决策门）、
> `JUDGE_CALIBRATION_LEDGER.md`（评审校准账本）、`runs/c_pipeline_*`（历次 run 证据）。

## 任务

按下方"待执行指令"实现修改，然后重跑裸跑 C1 D→E
（`python -m tests.experiments.c_pipeline --live`，不带 topology/filler override）。

## 三出口协议（不可违反）

- Architect / Filler / Final Reviewer 各按批准的次数调用（当前批准：Architect 含
  1 次重试上限共 2 次；Filler 含 1 次重试上限共 2 次；Final Reviewer 1 次）；
- 任何出口失败 → 冻结，写失败报告（失败模式 + 归因 + 证据），**不重试到底、
  不追加修改、不迭代 prompt 硬凑通过**；
- Final Reviewer 的 verdict 只记入校准账本，**不作为改动依据**（其历史误判见账本：
  把 D 缺失字段当缺陷、把源逐字标题当错误、捏造三页溢出等）；
- 到达渲染时必须交付逐 section 首行 x0 对照表（目标 46.909pt，验收 |Δ| ≤ 1pt）。

## 已冻结 / 已关闭的线（不要重开）

- **视觉抛光全部冻结**：标题大小写、bullet 样式、横杠、缩进呈现——不再因评审意见改动；
- 章节标题文本是源内容逐字文本：不得改名、改名意；**视觉大小写转换
  （Title Case 显示）经 owner 裁决（2026-09-10，选项 A）保留为冻结规则**，
  早期“已撤销”的说法作废，实际从未撤销；
- **Filler 链已闭环**（两次裸跑验证）：data-section 语义由源确定性覆盖；block owner
  由 data-source-line 确定性推导；标题缺失确定性注入；provenance 三不变量已落门禁：
  ① 文本片段归属唯一已标注源行（节点直标，或最近祖先容器继承——继承条件：
  容器文本 DOM 序拼接=唯一源行逐字、已标注后代行号一致、深层未标注仍拒、
  无裸文本段的容器不继承；未标注的直接文本叶子随容器同批标注。见
  c_pipeline.inherit_container_source_line，2026-09-10 owner 两轮批准）；② 全部带标注文本按序拼接 = 源文逐字；
  ③ 顺序一致。**同一源行拆到多个带标注节点是合法行为**（skills 表格场景），勿再当缺口；
- **dedup 已修复**（根因：缓存绝对 XPath 删节点后索引漂移；现直接操作 DOM 节点，
  全局"每源行恰好一次"，重放验证 4 删 0 剩）；
- **横杠规则**：行首列表符转为 CSS bullet（::before 圆点）；因结构拆分失去对象的
  行内分隔符删除；正文文本保持逐字。**精化（owner 2026-09-10 复测后批准）**：
  "行首横杠转 CSS bullet"仅适用于目标结构声明了列表槽位的 section（模板中该
  section 类含 entry-list）；目标无列表结构的 section（如 Education）行首横杠
  直接消失，呈现跟随目标。已交付渲染（`20260910T033728Z`）即为精化后正确行为；
  字面横杠在任何情况下不得存活；
- Architect 单发方差实测存在（曾 3 候选全把 title/tagline 塞进受保护行），
  处置见"待执行指令"，**不要用改 prompt 的方式解决**。

## 待执行指令（owner 已批准）

1. **确定性 slot 归位归一化**（validate_topology 之前）：title/tagline slot 出现在
   非 extension 行时，确定性移入 extension 行（extension 行由目标 scaffold 派生；
   scaffold 无 extension 行则保留拒收，不得凭空造行）。归位动作 + 原错误位置记入
   run 日志。contact 类 slot 不适用（错行仍拒收）。按 schema 一般化，禁止针对本
   pair/源文本特判。
2. **Architect 有界重试**：三候选全拒时允许 1 次重试（共 2 次调用），重试上下文附
   全部拒收明细。仍全拒 → 冻结报告。
3. **正文缩进 = 闭环拟合**（不是推导常数）：编译后探针渲染量每个 section 首个正文行
   实际 x0，与 46.909 求差调整该 section padding-left，重渲染至全部 |Δ| ≤ 1pt，
   渲染预算 ≤ 6 次。已知三组异常待闭环吸收：全体 -1.174pt（box model 折损，根因
   未明）、Summary 完全未命中选择器、Highlights/Key skills +8.561（疑似双重缩进）。
   若某 section 调 padding 无法影响其 x0，单独报告该 section 的 DOM 结构，禁止写
   特例 CSS。最终交付逐 section x0 对照表。
4. **拓扑校准行间重叠拒收**：fit 探针已量出各 role 行包围盒——接受条件加"任意两行
   bbox 不相交"（曾出现 extension row 与姓名重叠导致渲染硬门失败）。违者进
   topology_rejections（附重叠坐标）。稀疏页门禁保持现状，观察是否自然消失。

## 硬约束（沿袭项目 AGENTS.md + 试点铁律）

- 只改 `tests/experiments/`；不碰 app/、docs/、frontend/；
- 不引入新依赖；一切数值必须从目标测量派生，禁止硬编码本 pair 专属数字；
- 候选人事实逐字恰好一次、永不进模板；目标样本事实永不进输出；
- 失败报告是审计文书，内容必须干净（曾出现乱码混入，勿复现）；
- 每次修改后跑 `tests/experiments/test_c_pipeline.py` 及相关回归（当前基线 47/47）；
  pytest 输出存 `tests/test_results/pytest/`（带时间戳文件名），不提交。

## 当前验收标准（试点完成条件）

x0 对照表全绿（|Δ| ≤ 1pt）+ hard gates 通过 + Final Reviewer verdict 入账本
+ **owner 亲眼看 target.pdf / generated.pdf / side-by-side 并拍板**。
自动化全绿不等于完成——owner 终审是最后一关。

## 2026-09-10 增补（E→F 泛化，owner 裁决记录）

- **bullet 字形裁决**：目标零 bullet 设计（resume_F 实测：零字形、零矢量
  mark）时，源行首 `•` 字形逐字保留为正文文本，不转换——与横杠规则精化
  （"目标声明列表槽位才转换"）前提一致。横杠规则本身不变。
- **bullet-unit 发现结构化**：section 内任何 ul（li 后代携带
  data-source-line）即 bullet unit，不依赖 entry-list/entry-item class 词汇；
  无实测 bullet 目标的层不门控、透明入表（"无目标依据"，不计 PASS/FAIL
  分母）。
- 详见 PIPELINE_EVOLUTION_PROPOSAL.md §10 及历次 E→F 冻结报告
  （runs/c_pipeline_D_to_E_20260910T094706Z / 095430Z / 100305Z / 101139Z）。
