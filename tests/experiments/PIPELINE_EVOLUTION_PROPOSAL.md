# B/C-pipeline 演进提案:从"看图手调 CSS"到"结构 + 测量"

> **当前状态（2026-09-11 更新，以此为准；下方历史章节仅按日期追加，顶部不再回写）**
>
> | 阶段 | 状态 |
> |---|---|
> | A 管线 | 已接受实验基线（冻结归档，产品线改走 C1 架构，见 §11/§12 评审） |
> | B 管线 | 已关闭（假设证伪，零件经 §7.3 移植） |
> | C1 D→E（header + body） | owner 终审通过，完成（§9/§11） |
> | C1 E→F 泛化 | **调试中，尚未通过**（十次冻结，缺口 #1–#8 已闭；#9 右缘杠杆失效、#10 种子行宽派生假设待验证，见 run 20260910T161228Z） |
> | C2 | 未满足启动条件（E→F 通过 + 反向 pair 验证 + Architect 消融后方可评估，见 §12） |
>
> 日期:2026-09-08 初版;2026-09-09 §7–§8;2026-09-10 §9–§12;
> 2026-09-11 §13–§14。各增补章节的
> 状态陈述以日期为准，可能与本横幅不一致——以本横幅为最新。
> 来源:owner 与 Codex 脑爆,agent 评审并补充
> 适用范围:仅 `tests/experiments/`。主架构 `app/`、`docs/`、`frontend/` 不动。

## 0. 实证基础:为什么方向是这个

2026-09-07/08 的 D→E / E→F 调试中,每一个真正修复问题的改动,都是"把判断从模型
挪进确定性代码":

- 内容重复 → `deduplicate_candidate_html`(确定性去重)
- 私改 CSS/样式 → `_restore_template_presentation`(恢复模板权威)
- 章节乱序 → `_normalize_section_order`(DOM 重排)
- 假行号/裸行号 → `_normalize_provenance_annotations`(注解归一化)
- 字体替换 → 本地 woff2 注入 + typography 对比
- 排版差异 → `typography_delta`(目标测量值 vs 生成实测值)

反复失败的地方(Builder 幻觉 selector、两行 tagline 钟摆、评审漏报)共同指向一个
待验证假设:模型不适合独自承担精确几何测量和数值收敛。现有证据已经证明旧循环
不可靠,但尚未证明拿到版本对齐实测反馈的 B Reviewer 仍然不能收敛。

**核心论点:模型擅长决定"应该是什么结构",确定性代码擅长测量"现在差多少"。
B 的下一次 D→E 重跑先验证结构化测量反馈是否足够;若仍失败,C1 再把数值求解
完全移出模型。**

## 1. 十二项提案裁决

| # | 提案 | 裁决 | 说明 |
|---|------|------|------|
| 1 | LLM 只定拓扑,确定性几何搜索 | **条件主赌注,C1** | measured-feedback B 仍失败后启动;模型输出页面结构 JSON,已测得的目标值直接采用,只有未解析的渲染残差由代码拟合 |
| 2 | measured discrepancy graph | **部分采纳,B follow-up** | `_render_diagnostic` 只是事实输入层;完整 graph 仍需 evidence_ids、显式 delta、kind 和 `measured\|visual_inference` |
| 3 | Calibration probes(风洞测试) | **采纳,C1** | 模板定型前用确定性假内容拷问泛化性,但不阻塞眼前的 measured B 对照 |
| 4 | Candidate-blind 构造 | **采纳,C1** | B Reviewer 会看到 candidate render,并非 candidate-blind;C1 用 probes 选拓扑后才调用真实 Filler |
| 5 | 阶段冻结(拓扑→几何→字形→polish) | 缓 | 内核(禁止 CSS hack 糊结构错误)由 #2+#11 廉价实现;显式 phase/rollback 状态机是复杂度陷阱 |
| 6 | Reviewer 主动查询证据(tool loop) | 缓,C 阶段 | 需要 tool-call 基建 |
| 7 | 反事实修改验证 | 缓 | #1 落地后"诊断"环节消失,差异是量出来的;留给残余结构争议 |
| 8 | 多拓扑候选并行 | 采纳,C1 | 多个 topology 先经 compiler + 确定性 probe fill + render;选定后只调用一次真实 Filler |
| 9 | 双盲视觉裁判 | 低成本采纳 | 已有换序+降信机制,补盲化(评审不知道哪版更新/改了几轮) |
| 10 | 分区误差预算 | **采纳,C1 验收层** | 章节顺序/头部结构必须精确匹配;字号小误差可容忍;装饰留人工——治"五轮修图标间距"病 |
| 11 | Template DNA 结构指纹 | **采纳,C1 验收层** | 归一化 DOM 形状指纹(去文字/数值);连续报结构问题但指纹未变 = 假修复,强制 topology rewrite 或停止 |
| 12 | 布局 DSL + compiler | 方向正确,缓,C2 | 见 §3:DSL 必须按主线 `LayoutTemplateSpec` 契约设计,不做一次性发明 |

## 2. C1 的关键设计约束:topology schema 从 provider-neutral evidence 派生

#1 最大的坑是 schema 表达力:真实简历怪癖无穷(两行 tagline、右列日期、跨页条目、
无职位条目)。schema 太糙则模型表达不了目标设计;太细则退化成另一种 HTML。

缓解:**topology schema 保持稳定且 provider-neutral,实例从规范化后的
`TargetLayoutEvidence` 派生**。Adobe raw response 必须先经过现有 adapter;
`format_summary` 的 visual groups、行聚类和层级路径只能作为带 analyzer provenance
的 evidence,不能成为内部 schema。LLM 在测量出的脚手架里选择、命名、分组,
而不是自由发明结构。schema 做错了,#1 会卡住;这是 C1 的核心设计题。

同时,**保留 2026-09-08 已建的内容安全层**:`provenance`/`coverage`/`block cohesion`
是地基,拓扑世界照样需要 Filler 落点校验;`typography_delta` 升级为几何拟合器的
目标函数。

## 3. C2 与主架构的关系:DSL = LayoutTemplateSpec 原型

主架构文档已规划 `TargetLayoutEvidence → LayoutTemplateSpec`,
`LayoutTemplateSpec + ClientFacingRenderContext → RenderPlan → output`。

C2 实验 DSL 的最大价值是**替主线验证该契约的表达力**(真实简历的怪癖能否被
renderer-neutral 契约表达)。因此:

- DSL schema 按 `LayoutTemplateSpec` 设计,compiler 原型即未来 mainline
  renderer 的雏形;
- 若 B 单独发明一个 DSL,验证完即扔,则实验与产品脱节,不做。

已知风险:DSL 刚性足以防 hack 时,可能表达不了目标设计的全部怪癖;逃生舱口
会重新引入 hack 面。由 #1 的几何拟合承担数值层、DSL 只锁结构层,可以收窄该面。

## 4. 实验排序

### B-pipeline measured follow-up(下一道决策门;不跑 full matrix)

1. 保留已加入的 reviewed/produced render/template 版本戳和 budget-exhausted final audit;
2. 先闭合 semantic slot compatibility:每个候选内容单位必须有合法落点、声明的
   candidate-only extension region,或显式 recruiter disposition;不得把 title 填进
   location,也不得静默丢 tagline;
3. 让 content/provenance gates 通过并实际产出 candidate render;
4. 将 `_render_diagnostic` 的版本对齐 target/current facts 送给下一轮 Reviewer;
5. 只重跑 D→E,保存直接可审阅 PDF、图片、side-by-side 和 final audit。

决策门:

- D→E 成功:保留 B,下一步才运行 E→F;
- gates 已通过、Reviewer 确实收到 version-aligned measured facts,但仍发生结构或
  数值钟摆:C1 的必要性得到支持,冻结 B 的新增设计工作;
- 未产生有效 candidate render:仍是 slot/Filler/gate blocker,不能用于证伪 B 的
  几何反馈假设。

### C1(下一个大赌注)

1. topology JSON schema 从 provider-neutral `TargetLayoutEvidence` 脚手架派生(§2);
2. LLM Layout Architect 输出拓扑(可并行 3 个候选,#8);
3. 确定性 template compiler:拓扑 → 结构固定 HTML(placeholder,无内容);
4. compiler 先验证 topology/slot inventory 完整,再用确定性 probe fill 渲染;
5. **几何参数拟合器**:已测得的 typography/geometry 直接采用;对未解析的 renderer
   residual 做有范围、有来源、按布局角色限定的小范围搜索。目标函数同时约束
   baseline、row count、alignment、wrap、overflow 和 probe 稳定性,不把目标样本
   文字宽度固化成 slot width;
6. calibration probes 通过后,才允许 Filler 填真实候选人(纯内容放置,已有
   provenance gate 兜底);
7. 误差预算 + 双盲评审验收。

### C2(主线对齐后)

DSL schema 按 `LayoutTemplateSpec` 设计;compiler 成为 mainline renderer 原型;
calibration probes 进入正式产品测试流程。任何从实验向主线的提升都需要 owner
评审、相应 ADR/架构文档更新和基线对比,不能因为实验成功自动进入 production。

## 5. 铁律(沿袭,不放松)

- 只改 `tests/experiments/`;不碰 `app/`、`docs/`、`frontend/`;
- 内容逐字恰好一次;候选人事实永不进入模板;
- 确定性 gates 是内容权威;Template Reviewer/Layout Architect 只拥有实验模板;
  最终视觉 acceptance Reviewer 只读、盲化,owner 是最终裁决;
- 模板候选不自动写入持久缓存;
- 不引入新依赖(几何拟合用现有 pdfplumber/PIL/Chrome 渲染栈)。

## 6. 与现有实验的关系

- 现有 rev-6 loop(30 轮/12 评审/repairable 集/归一化器族)属于 A-pipeline,
  是实验比较基线;B 是独立的 Template Reviewer 直接重写循环,没有 Builder;
- 三份 CODEX_*.md 描述的 rev-5/6 细节已落后于代码,以代码内 owner 决策注释
  (标注日期)为准;
- 视觉评审已可独立切换 provider(`VISUAL_API_*` 环境变量,Qwen3-VL-Plus 已验证),
  B measured follow-up/C1 的视觉验收沿用该通道。

## 7. 增补(2026-09-09):B-pipeline 实验结论与排序修订

B-pipeline(`b_pipeline.py`,Codex 实现)在 A 之外平行验证了"模板先行评审循环":
空模板对照目标 PDF 由 Reviewer/Builder 迭代修订,再交给 Filler。一轮 D→E 实测
(5 轮修订、全部 header 相关、`max_template_reviewer_revisions` 停止)给出了
决定性证据。

### 7.1 实证结论

1. **旧 B 未收敛。** 早期五轮出现字号钟摆和结构往返,说明只让模型根据图片、
   raw template 和历史自行推导 box model 不可靠;但 measured-feedback B 是否仍
   不收敛尚待有效 D→E 重跑。
2. **诊断时序曾错位且 artifact 容易被误读。** round N 的 diagnosis 描述的是
   round N-1 render,同时产出 template N;现有 reviewed/produced 版本戳是必要修复。
   是否仍复述旧 history,必须在 Reviewer 实际收到新版 render facts 后判断。
3. **约定桥接是一类新 bug 模式。** 图标 tofu 的隔离复现显示模板按旧 FA CSS 惯例
   (`.fab`)写模板、Filler 按 FA6 元素惯例(`fa-brands`)填内容,选择器永不
   匹配(离线 A/B/C 复现,已用 inert family 规则修复)。多 AI 角色共写同一份
   文档时,必须审计角色间的产出约定差异。`_ensure_icon_font_family` 只作为 B 的
   兼容 shim;C1 compiler 必须统一拥有 icon DOM/class/glyph/font manifest,Filler
   只提供 contact kind/value,不能把 shim 当成目标架构组件。
4. **模板缺落点**:Reviewer 重写的模板 header 没有 title/tagline 落点,Filler
   被迫错误映射或丢失内容,B-pipeline 的 Filler/gate 尝试耗尽。模板缺陷应该在
   B(或 C1 compiler)出栈口被拦住,而不是进入几何循环。

### 7.2 定位修订:A/B/C 不是三个平行赌注

- **A 保持为基线**:当前可端到端产出合格产物的比较基线,除回归外不改行为;
- **B 先完成 measured D→E 对照**:修通 semantic slots,确保 Reviewer 真正收到
  current render facts;在此之前既不宣告成功,也不以几何原因冻结;
- **C1 条件启动**:只有新版 B 在上述前提下仍发生结构/数值钟摆,才把模型从数值
  循环拿掉。届时 B 的版本戳、facts、终审和边界检查成为 C1 可复用组件。

### 7.3 B 组件 → C1 的移植表

| B 的产出 | 在 C1 中的角色 |
|---|---|
| `_render_diagnostic`(实测行 top/x0/x1/height、header DOM、CSS facts) | discrepancy graph/几何拟合器的事实输入;它本身还不是 resolved computed-style diff |
| 版本戳(reviewed/produced render/template) + final audit | 拟合收敛后的验收层 |
| `validate_filler_header_structure` | 拓扑冻结后的结构边界 |
| 图标约定桥(`_ensure_icon_font_family`) | 仅保留为 B compatibility shim;C1 由 compiler 统一约定 |
| "模板缺落点"失败用例 | 拓扑完整性探针的用例 |

### 7.4 C1 header 试点规格(先行)

若 measured B 决策门支持 C1,首个试点只锁 header。D→E 的目标 E 有三行实测
基线(约 34.6 / 53.5 / 67.4),失败模式清楚、拓扑有限、目标测量值现成:

1. LLM 一次输出 header 拓扑 JSON(3 行、每行字段、对齐方式)——模型只做这个;
2. 确定性 compiler:拓扑 → 结构固定 HTML;
3. 已测得的 typography 值直接编译;只对仍未解析的 container spacing/width 等
   renderer residual 做有界搜索,每次调整 → Chrome 渲染 → pdfplumber 量角色基线;
4. 验收从 target evidence 动态读取三行位置,每个对应角色差 ≤ 1pt,同时约束
   row count、alignment、wrap、overflow、双渲染一致和 calibration probes;
5. 几何拟合循环零 API 调用并设置 render/time budget;具体耗时由试点记录,不预设
   "几十次、几分钟"为事实。

试点成功 → 推广到 section 标题、经历条目;失败 → 精确定位是拓扑表达不够还是
参数维度不对,损失可控。

### 7.5 A/B 接口决议:slot-inventory 契约

模板缺落点的检查放在 **B(或 C1 compiler)的出栈口**:导出基于现有
`ClientFacingRenderContext`/`LayoutTemplateSpec` 语义词汇的类型化 slot 清单
(name/tagline/location/contact-kind/section…),与源内容清单做兼容性检查。每个必须
保留的内容单位必须有目标 slot、声明的 candidate-only extension region,或显式
recruiter disposition;不能用简单清单相等,也不能把 title 填入 location。缺合法
落点的模板不得进入填充。A 作为基线保持现状;若将来改变 A 接口,必须另行批准。

### 7.6 早期提案项句勘误

- measured discrepancy graph:B 的 `_render_diagnostic` 已提供事实输入,但 graph
  的 evidence_ids、角色对齐、显式 delta、kind/confidence 尚未实现;
- 误差预算(#10)、DNA 指纹(#11)、双盲(#9):未实现,保留为 C1 验收层组件;
- probes(#3):保留为 C1 拓扑完整性和泛化检查(§7.5 的清单兼容只是其子集)。

## 8. 增补(2026-09-09):LLM-as-Judge 文献映射

来源:owner 收藏 Josh Rosen《LLM-as-Judge Architectures》、loop engineering 笔记,
以及 2026 年 bias mitigation 文献(LatentEval bias checklist、arXiv《Judging the
Judges》、《From Rubrics to Reliable Scores》、DnA-Eval)。Reviewer 就是一个
runtime LLM-as-judge,文献模式与我们的管线逐条映射如下:

| 模式 | 现状 | 结论 |
|---|---|---|
| 窄判断不需要更强的模型 | 评审只判"像不像目标" | 已符合 |
| **拆分判断(G-Eval/DAG)** | 反例:一次调用塞 12 rubric+issues+schema | **最大借鉴点** |
| pairwise 替代打分 | champion/challenger + 换序 | 已符合 |
| 判过程而非只判终品(trace checkpoints) | 语义映射决策无评审检查点 | 可借鉴 |
| 多评审 + 分歧即信号 | 单评审;换序不一致只降 tie | 廉价可加 |
| judge the judge(偏置审计、人类校准) | 完全没有 | 需要建账本 |
| 确定性包住评审 | 全部归一化器+过滤逻辑 | 已符合且被文献验证 |
| judge in loop → 评审错误=应用故障 | 12 次重试卡死 champion 的教训 | 已符合 |

文献补充的关键细节:

- 五种具名偏置(position/verbosity/self-preference/format/calibration drift),
  各有独立测量与缓解;"更好的 prompt"不是解药;
- **位置偏置在两候选质量接近时最强——正是 champion/challenger 的场景**;
  换序应每次 pairwise 都做,而非只在低置信时;
- 可靠评分瓶颈是 "rubric 执行漂移"(同一 rubric 被反复重新解释),解法是
  evidence-grounded 引用(即 §4 的 measured/visual_inference 分级);
- 多评委集成的价值不在投票,而在**分歧本身是信号**:分歧 → 重试更强模型、
  补证据、或升级人工。

### 8.1 落地项(按性价比)

1. **拆评审为一锅 → 三个聚焦微评审 + 确定性聚合**:
   typography judge(typography_delta 表 + 标题区域裁剪图)、
   structure judge(章节顺序图 + header 区域)、
   density judge(整页)。区域裁剪顺带解决 DeepSeek 384-token/图看不清的问题;
   聚合由确定性代码完成(DAG);
2. **双评审分歧即信号**:同一对比发 DeepSeek 视觉与 Qwen 两个 provider,
   preference 一致 → high;不一致 → 按 tie/升级处理(基础设施已就绪);
3. **每次 pairwise 都换序**(去掉低置信条件);
4. **评审-人类校准账本**:owner 目测结论与评审 verdict 记入 JSONL 账本,
   定期算一致率(kappa ≥ 0.61 暂作本实验阈值,不是跨任务通用文献门槛);
   prompt 变更后对比漂移;
5. **fill plan 评审检查点**:语义映射决策在渲染前由文本-only 轻量评审核对
   (源块清单 vs 模板契约),与 B 侧 slot-inventory 契约检查互补
   (确定性查结构、评审查语义)。

其中 1+2 归入 C1 评审层;3+4+5 可先行于 C1,在现有 A/B 管线上生效。

### 8.2 评审层升级阶梯与终审模型选型试验（2026-09-10 增补）

触发背景：qwen3-vl-plus 在 C1 全部裸跑（D→E 七轮 + E→F 六轮）中累计
10+ 项误判、零有效捕获。文献核实（见下）表明细粒度视觉差异识别是
当前整个 MLLM 模型类的系统性弱项，非单模型或 prompt 问题。

#### 文献依据（2026-09-10 勘误修订，原表述有实质性错误，以本版为准）

- **VDiff-Bench**（arXiv 2609.06245，2026-09）：与终审任务同构
  （两幅近似图像间的细粒度差异识别）。**评测 11 个模型**——商业 6：
  GPT-5.4、Gemini 2.5 Flash、Gemini 3.1 Pro (Preview)、Gemini 3.5 Flash、
  Grok 4.3、Doubao Seed 1.6 Vision；开源 5：Qwen3-VL-8B Thinking、
  InternVL3.5-8B、LLaVA-OneVision-Qwen2-7B、Kimi K2.5、Kimi K3。
  **勘误：此前所写“14 个模型（GPT-5.2/Qwen3VL-Plus/Kimi 2.5/Grok 4.2）”
  系误引另一篇 DiffCap-Bench（arXiv 2605.04503）的模型清单，两者混濂。**
  结论：语义-低层性能鸿沟显著。**勘误：原“低层准确率低至 8.7–33.3%”
  的真实范围是三个 7–8B 开源小模型在低层变化组（全图颜色/噪声分辨率/
  纹理/光照）上的区间（LLaVA-OneVision-Qwen2-7B 8.7%、InternVL3.5-8B
  16.0%、Qwen3-VL-8B Thinking 33.3%），对照组为它们自身语义类的
  52.5–70.6%；不可概括为整个 MLLM 类。**对选型的正确推论：细粒度
  差异识别是相对弱项，但商业模型显著好于小开源模型——选型试验
  （Claude/Gemini/GPT 候选）的理由反而更充分；
- **OddGridBench**（arXiv 2603.09326）：MLLM 视觉差异敏感性
  系统性缺失，同类佐证；
- **DiffSpot**（arXiv 2605.29615）：网页渲染前后截图对比（与本项目
  场景同构），VLM 同样挣扎。**勘误：其 grounding gate 是用已知
  DOM/CSS 变更 + 像素差过滤基准样本，并非“模型给 bbox 即可机检”
  的证明——后者是本项目的工程推论，应标注为推论而非文献结论。**

#### 升级阶梯（预批准应急方案，触发即执行、无需另行冻结裁决）

```text
L0（现状）：单评审 = 假说发生器
  verdict → 可测量性标注 → 测量证实/证伪 → 账本

L1 触发：连续 2 个 run，评审假说 0 条被测量证实，
        而 owner 眼睛或验收表同期发现 ≥1 真实缺陷
  → §8.1-1：一锅拆三（typography / structure / density 三个聚焦
    微评审，各自只看区域裁剪图），确定性 DAG 聚合

L2 触发：L1 后仍 0 证实
  → §8.1-2/3：双评审分歧即信号（DeepSeek 视觉 + Qwen 并行），
    一致 = 高置信假说，分歧 = tie/升级人工；每次 pairwise 换序

旁路触发：账本 kappa 追踪（§8.1-4，已上线）低于本实验暂定阈值 0.61 或连续下降
  → 跳过 L1 直接进 L2

终止条件：L2 后仍 0 证实
  → 评审层整体移出循环。保留 owner 终审 + 分层验收表作为全部验收。
    这不是失败——§8 文献结论“确定性包住评审”占优，实验用数据
    确认了它在本场景成立。
```

#### 终审模型选型试验（管线外 A/B；不跑管线渲染，但调用
Claude/Gemini/GPT 候选模型为 live API 调用，成本另计）

利用已有带真值的测试集：历轮账本记录了哪些是真缺陷
（如 ANOTHER SECTION 正文 38.6pt 缩进）、哪些是评审噪声
（捏造页数、索要不存在的字段）。已有响应可零调用离线回放；将同几组
target/generated 图提交给新候选模型仍是 live API 盲测，必须单列预算：

- 候选不按“公认最强”预选赢家；可比较试验时实际可用的 Claude、Gemini、
  GPT 与现役 Qwen/DeepSeek，具体版本、能力和成本在执行前重新核实；
- 现役 qwen3-vl-plus 降级为 L2 双评审分歧信号的便宜第二意见；
- 评分指标：真缺陷捕获率（对账本真值）、噪声率（对账本误判类）、
  指控可验证率（带 bbox 比例）；
- 入选者按 §8.1-4 账本流程上岗，跑若干 run 积累证实率后再评估
  是否触发 L1。

架构不变：评审层始终是假说发生器 + 可测量性标注，模型更替不改变
其与确定性门禁/验收表的分工。

#### L1 提前激活与岗位重定义（2026-09-10 矩阵终审增补）

矩阵终审实证改变了本节的裁决基础：六对产物中五个 pair 存在呈现缺陷
（rule 位置、联系行呈现、三栏劈段落、空行夹线、孤标题分页），全部由
owner 视觉发现、多模态模型（orchestrator 会话）读 side-by-side 独立
复核后经确定性测量 100% 证实——**探测能力实证存在，证实率 100%**。
对照旧 reviewer 的零证实记录，差异归因于任务设置而非模型类能力：
side-by-side 并排对比（目标同框）、开放缺陷枚举（无 rubric 清单）、
发现即测量验证——均为 gross-level 任务，在模型能力射程内。

**岗位重定义（L1 生效，替代 L0 的假说发生器形态）**：

- 岗位：缺陷探测器（非验收裁判）；
- 输入：side-by-side 并排图 + 开放指令"枚举候选与目标之间的所有
  呈现差异，按严重度排序"，每条发现必须附位置（节/行/元素）；
- 下游：每条发现走可测量性标注——能翻译成确定性检查的 → 测量
  证实 → 进修复循环，同时升级为验收契约新行（永久）；不能翻译的
  → 契约盲区候选，入账本不驱动改动；
- 与 B 时代裁判的本质区别：B 的 loop 是模型判断驱动修复（噪声进
  产物）；本岗位是模型提假说、代码裁决（尺子过滤噪声、信号进产物）；
- 触发记录：L1 的原始触发条件（连续 2 run 零证实）已由历史数据
  满足（评审层累计 10+ 误判、零证实），故 L1 直接激活，无需再观察。

**选型试验同步激活**（本节末的离线 A/B 工单），真值集扩充：矩阵
终审发现的全部缺陷（rule 位置 ×3 对、联系行呈现 ×4 对、空行夹线
×3 对、三栏劈段落、孤标题分页等）+ 账本既有噪声类，构成六对
标注数据集。入选探测器随六对重跑上岗，其证实的新差异即验收
契约的生长入口——契约扩张从依赖 owner 肉眼升级为"探测器初筛 +
owner 终裁"双层。


B-pipeline(`b_pipeline.py`)在 A 之外平行验证"Template Reviewer 直接重写完整
空模板,再由 Filler 填内容"。2026-09-09 存在两代不同条件的 D→E artifacts:

- 早期 run `b_pipeline_D_to_E_20260909T080600Z` 在 version-aligned
  `_render_diagnostic` 加入前运行;它证明旧 B 五轮没有收敛,但不能证明 measured
  feedback 无效;
- 后续 run `b_pipeline_D_to_E_20260909T103038Z` 已含版本戳和新边界检查,但五轮均
  被 slot/content gates 拦住,`reviewed_render_version` 全为 null;它证明当前首要
  blocker 是 template/Filler 的 semantic slot compatibility,不是几何求解。


## 9. 增补(2026-09-10):C1 header 试点完成判定

状态:**owner 终审通过,试点完成**。交付 run
`runs/c_pipeline_D_to_E_20260910T033728Z`(裸跑,无 override)。

### 9.1 终态验收数据

- Header 几何:两次裸跑均为 0.6pt(目标三行基线,预算 ≤1pt);
- 分层 x0 契约(数值全部从目标实测派生):一级正文 46.909 / bullet 圆点
  60.000 / 二级 70.909,54 行全 PASS,最大偏差 0.011pt;验收表由
  `render_body_x0_acceptance()` 从最终渲染直接序列化,逐字节可复现;
- Hard gates 通过(双渲染、逐字覆盖、provenance、分页、字体、图标);
- 成本:Architect 1 次(约 1.3k token)、Filler 1-2 次、Final Reviewer 1 次,
  全 run 90-130 秒;几何拟合循环零 API 调用;
- 测试 59/59(基线 47 → 试点新增 12)。

### 9.2 失败分类学:七轮裸跑的收敛阶梯

每轮失败均被确定性手段消灭,未出现同类复发:

| 轮次 | 失败类别 | 归宿 |
|---|---|---|
| ① | 章节语义标签错 + 无标注拆分 | data-section 源语义覆盖 + provenance 三不变量 |
| ② | 缺失字段误报进终审 | prompt 注入源事实(缺失字段清单) |
| ③ | 块属主缺失 + 标题丢失 | data-source-block 确定性推导 + 标题注入 |
| ④ | 二级缩进未锁进(owner 发现) | 分层几何契约 + 闭环拟合 |
| ⑤ | 探针测量对象与真实内容脱节 | 测量改锚 post-fill 真实行首字形 |
| ⑥ | header 行间重叠 | 拓扑校准 bbox 重叠拒收 |
| ⑦ | bullet 重复(dedup XPath 缓存根因) | dedup 全局"每源行恰好一次" |

处置四分法(2026-09-10 修订,收窄上方原始结论——E→F 十轮证明原表述
"每个可预测错误模式都可落为确定性归一化或门禁"会诱导补丁农场):

| 失败性质 | 处置 |
|---|---|
| 可测几何残差 | 闭环拟合(旋钮/行级修正) |
| 安全/内容不变量违反(逐字、provenance、污染) | 门禁拒收,不修复 |
| 唯一且无歧义的规范化(操作性判据:修复后文本按 DOM 序拼接与唯一源行逐字一致) | 确定性归一化 |
| 语义/结构存在多个合法解释(如 Filler 组员身份、行内容器选择) | 不静默 normalize;优先测量层吸收(旋钮),无法吸收时拒收/重新生成/交 recruiter 裁决 |

Gate 与 normalizer 职责不得混同:gate 只判不改;normalizer 仅在第三类
(唯一无歧义)场景工作。同族缺口(不变量表达力)再现时,提请 owner 重设计
不变量,不逐个修补。原始结论收窄版仍成立:Filler/Architect 的单发方差
不可消除,有界重试仅作新错误类别的兜底。

### 9.3 横杠规则精化(记录在案)

"行首列表符转 CSS bullet"仅适用于目标结构声明了列表槽位的 section;目标
无列表结构处(如 Education)行首横杠直接消失——呈现跟随目标,字面横杠
任何情况下不得存活。由 `remove_entry_leading_dashes()` 按模板 DOM 派生
的槽位集合实现,已复验与已批准渲染一致。

### 9.4 评审校准账本(§8.1-4)首批硬数据

qwen3-vl-plus 作为 Final Reviewer 历轮累计 10+ 项误判:捏造 phone/email
存在、把源逐字值(USER、→/⌣ 标记)当缺陷、拿目标样本事实要求"修正"候选
输出(实为要求污染)、伪造页数与溢出、越权评标题措辞与正文继承版式。
事实注入(缺失字段清单)被证明有效;禁令式措辞无效。全部误判入
`JUDGE_CALIBRATION_LEDGER.md`,未驱动任何渲染改动。

### 9.5 B 决策门正式关闭

§4.1 决策门 ② 的前提与钟摆条件均已实证,B 冻结归档(零件经 §7.3 移植表
进入 C1)。同任务对照:B measured D→E 五轮 20 分钟不收敛;C1 终态约
2 分钟全绿。"结构性判断归模型、测量与收敛归代码"获得首个完整实证。

### 9.6 下一步

1. C1 推广：header 方法扩展到章节标题与经历条目（分层几何契约已就位）；
2. E→F 泛化验证并入推广验收序列（同一套链，换数据源）；
3. 两步通过后启动 C2（DSL 按 LayoutTemplateSpec 契约对齐）；
4. 任何向主线的提升仍需 ADR/架构文档更新与基线对比，不因实验成功自动晋级。

## 10. 增补（2026-09-10）：C1 body 推广（section 标题 + 经历条目）追认记录

Owner 裁决追认（对应失败 run
`runs/c_pipeline_D_to_E_20260910T060359Z/FAILURE_REPORT.md` 及其勘误）：

1. **局部间距门控追认通过**：heading 验收采用内容无关的局部 y 间距门控
   （content→rule、rule→heading、heading→content，均对 |Δ|≤1pt），绝对 y 只
   入表参考——源与目标内容量不同，绝对 y 逐节对齐不成立。
2. **字高门控为永久门禁**：渲染字高 vs 目标实测字高（PDF 字高对照）超出
   1pt 即冻结，无旋钮。首次实战表现：以命名原因在第 1 渲染拦下 Chrome 打印
   shrink-to-fit 全页缩放（×0.893），该缩放会使全部几何测量失效。
3. **shrink-to-fit 隐患勘误与修复**：横向溢出源不是右列 `|`（实测证实为源
   逐字内容，L0062-66 行首，删除违反逐字铁律），而是模板继承的
   `.section-heading{white-space:nowrap}` 遇合法长逐字标题行，把文档
   min-content 撑到 ~605pt。修复：compiler 拥有的 `.section-heading` 允许
   换行（实测内容宽度内的呈现契约），一般规则无特判；`.entry-right` 子元素
   块级堆叠 + `min-width:0` 防御（`white-space:nowrap` 保留模板所有权，日期
   不折行不截断）。测量侧同步：换行标题的延续行归入 heading 层（非
   unattributed），纯标题节（整行即标题、无正文）不适用 heading→content
   间距门控。
4. **provenance 不变量①最终条文（E→F 泛化，2026-09-10，两轮批准）**：
   文本片段归属唯一已标注源行——节点直标，或最近祖先容器继承（继承条件：
   容器文本 DOM 序拼接 = 唯一源行逐字且匹配唯一、已标注后代行号一致
   （冲突即拒）、深层未标注仍拒、无裸文本段的容器不继承；未标注的直接
   文本叶子随容器同批标注）。动机：F 种子模板的
   `<strong>[类别]</strong>: [列表]` idiom 必然产生未标注 tail/标签元素，
   逐条补丁到此收尽（缺口 #5/#6）。若同族缺口再现，直接提请不变量①完整
   重设计。
5. **bullet 字形裁决（E→F 泛化，2026-09-10）**：目标零 bullet 设计
   （resume_F 无字形/矢量 bullet，实测为空集）时，源行首 `•` 字形**逐字
   保留**为正文文本——转换无实测依据；与横杠规则精化的前提一致（"目标声明
   列表槽位才转换"）。bullet-unit 发现同步结构化：section 内任何 ul
   （li 后代携带 data-source-line）即 bullet unit，不依赖模板 class 词汇；
   无实测 bullet 目标的层不门控、透明入表（"无目标依据"，不计入
   PASS/FAIL 分母）。

### 9.7 补充裁决(2026-09-10):章节标题视觉大小写

Owner 裁决(选项 A):章节标题保留 Title Case 视觉呈现
(`.section-heading` 的 lowercase+首字母大写 CSS 规则),源全大写文本不变。
该规则为冻结规则,Phase A 调整 heading CSS 时不得移除;早期"已撤销"的
记录作废(实际从未撤销)。

## 11. 增补(2026-09-10):C1 body 推广完成判定(owner 终审通过)

交付 run `runs/c_pipeline_D_to_E_20260910T065420Z`(裸跑 79.6s,三出口
Architect 1 / Filler 2(第 1 次被 provenance 门禁确定性拦截、第 2 次通过
——门禁按设计工作)/ Final Reviewer 1)。

- 分层验收 62 PASS / 0 FAIL:header 0.6pt(与 §9.1 持平,无退化)、
  heading 8/8、entry 5/5、既有各层无回归;字号 1:1(×0.893 shrink-to-fit
  已消除);右列几何复刻目标模式(company 行右缘对齐 location、role 行
  右缘对齐日期);
- 真凶勘误入档:shrink-to-fit 根因是种子模板继承的
  `.section-heading{white-space:nowrap}` 撞上合法长逐字标题,compiler 以
  `white-space:normal` 收编——又一例"继承的巧合"转为 compiler 所有的
  确定性规则;
- Final Reviewer 拒收 11 条经逐条核验全部命中在案误判类(含再次捏造
  page 3),只入校准账本,未驱动改动;
- **owner 裁决:title/tagline 同行排列(extension 行单行,83.0pt)接受为
  D→E 的标准呈现**——该行无目标几何可对照(E 的 header 无 title/tagline),
  源驱动结构两种排列均合法,owner 择定单行。

§9.6-1(C1 推广)完成。下一步:§9.6-2,E→F 泛化验证——同一套链换数据
源跑通,预期零结构修改;届时 §9.6-3(C2)的启动条件即告齐备。

## 12. 增补(2026-09-10):提案评审采纳记录与执行计划修订

owner 委托对本文档的独立评审(技术判断约 75% 正确,六项批评五项成立、
一项文献错误经复核确认为我方失实)已采纳。本节为修订后的执行计划。

### 12.1 评审采纳的六项修正

1. **状态管理**:顶部状态横幅已加(见文首),后续以横幅为准;
2. **文献勘误**:§8.2 已修订(11 模型、8.7–33.3% 真实范围、
   DiffSpot grounding gate 表述、"零 live 成本"改为"管线外 A/B、
   API 成本另计");
3. **Architect 消融实验(下一道门,先于一切 C2 工作)**:body topology
   已是零自由度确定性单候选(c_pipeline.py),header 拓扑在事实注入后
   三候选指纹与确定性结构一致——"LLM Architect 是否必要"已成为可
   便宜证伪的命题。实验:确定性拓扑(从 scaffold + required slots 派生)
   vs Architect,跨 D→E/E→F 及至少一个新 scaffold 比较 fingerprint 与
   模糊场景无歧义性;等价则删除 Architect 调用(保留文档记载);
4. **处置四分法**替代 §9.2 宽结论(见 §9.2 修订),并作为后续一切
   E→F/新 pair 失败的显式处置决策表——"gate 与 normalizer 不得混同";
5. **C2 边界修正**:C2 不新造平行 DSL——目标是验证并扩充现有
   `LayoutTemplateSpec` 表达能力,拟合参数映射进该 spec,由既有
   `RendererCompiler`/renderer 消费;且内容输入必须换审核后的
   `ClientFacingRenderContext`,C 管线的 source_text+Filler 形态
   不得原样晋升 production;
6. **治理**:C 管线维持 owner-authorized diagnostic;做 C matrix 或
   产品对照前,先成为 A canonical harness 下的 strategy,不建第二套
   matrix runner/manifest/报告格式。

### 12.2 E→F 剩余工作(受四分法约束)

- 当前冻结(#10,run 161228Z):右缘杠杆四连无效,根因假设为种子模板
  行盒宽度比 F 实际设计宽 ~10.2pt(疑似缩进双重计入,源自
  `_seed_template` 派生层)。批准选项 A:离线逐元素宽度对比定位来源,
  若在种子派生逻辑则以一般规则修复(种子模板为 B 冻结件,改动需
  owner 批准——本次即 batch 批准记录);
- 修复后重跑至全绿;禁止 pair-specific selector/class/数值入规则;
- E→F 全绿后,追加至少一个反向 pair(F→E 或 D→F)证明非双样本拟合;
- 三 pair 全过后,§9.6-3(C2)启动条件方告齐备,按 §12.1-5 边界启动。

## 13. 增补(2026-09-11):VLM 能否成为真正 Reviewer 的文献复核

研究问题不是笼统的“VLM 会不会看图”,而是三个彼此独立的能力:

1. **发现与定位**:能否从 target/generated 中发现真实版式缺陷并指出区域;
2. **诊断与建议**:能否把缺陷分类并提出正确的修复方向;
3. **裁决权**:能否可靠决定产物通过/失败并驱动自动修改。

文献与本地账本共同支持的当前结论是:**VLM 可以成为受约束的视觉 critic 和
可验证指控生成器,但现有证据不支持通用 VLM 独占第 3 项终审权。**

### 13.1 直接证据

- [DiffSpot](https://arxiv.org/abs/2605.29615)与本项目最接近:它通过单个
  HTML/CSS property mutation 构造 4,400 对网页截图。13 个 frontier VLM
  零样本开放式找差异时,最佳 true-change recall 仅 40.7%,所有模型 Hard-tier
  recall 均低于 23%。因此“整页 target + generated → 自由评论 → acceptance”
  不足以承担可靠门禁。
- [VDiff-Bench](https://arxiv.org/abs/2609.06245)显示细粒度比较是独立能力,
  不能由通用多模态表现推定;同时较强模型在四选一、已有候选差异的条件下明显
  优于开放式找茬。对本项目的含义是:**验证一个窄而具体的视觉假说,比要求模型
  自己穷举所有缺陷更可信。**
- [MLLM-as-a-Judge](https://arxiv.org/abs/2402.04788)发现 pairwise comparison
  比 point scoring 和 batch ranking 更接近人类,但仍有偏置、幻觉和不一致。
  所以 reviewer 输出应是窄 pairwise/criterion 判断,不应是一个总体分数。
- [Visual Prompting with Iterative Refinement for Design Critique](https://arxiv.org/abs/2412.16829)
  把 critique、bbox 定位、局部 crop、验证拆开后优于单次 baseline,说明区域裁剪
  和任务分解有效;但其六角色方案成本高,本项目只采纳最小必要部分。
- [Human-Aligned MLLM Judges for Fine-Grained Image Editing](https://arxiv.org/abs/2602.13028)
  通过细分 rubric 获得较高的人类一致性,同时明确高风险和主观场景仍需人类。
  该结果支持“按 role/criterion 拆评审”,不支持取消 owner 终审。
- [Vision-Guided Iterative Refinement for Frontend Code Generation](https://arxiv.org/abs/2604.05839)
  报告三轮 VLM critic refinement 最多提升 17.8%;它证明视觉反馈可以帮助生成,
  但任务是自然语言网页生成且评价器本身也使用 VLM,不能单独证明精确 PDF
  replication 的自动终审可靠性。
- [METAL](https://arxiv.org/abs/2502.17651)发现把视觉与代码 critique 分开有利于
  self-correction;对应本项目应把“看见了什么”和“改哪个参数/结构”分成两个阶段。
- [VisRefiner](https://arxiv.org/abs/2602.05998)的改进来自 difference-aligned
  supervision 与已知 HTML/CSS perturbation,不是仅换一个更长 prompt。这支持先建
  本地、带真值的 layout-difference 校准集,再讨论 reviewer 模型选型。
- [ReLook](https://arxiv.org/abs/2510.11498)使用 invalid-render 零奖励、只接受
  被独立评分确认改善的 revision、始终保留 best-so-far。该 monotonic acceptance
  比“Reviewer 说改好了就晋级”更适合本项目。
- [MM-JudgeBias](https://arxiv.org/abs/2604.18164)与
  [Perceptual Judgment Bias](https://arxiv.org/abs/2606.02578)进一步提示 modality
  neglect 与“文本叙事压过视觉证据”风险;两者均为很新的预印本,只作风险佐证,
  不单独作为架构决策依据。

### 13.2 Reviewer 的批准职责边界

| 责任 | 权威来源 | VLM 权限 |
|---|---|---|
| 内容逐字、隐私、目标污染、provenance | 确定性 gate | 无裁决权 |
| bbox、baseline、字号、行数、wrap、overflow、颜色、rule geometry | 测量程序/provider evidence | 可提出假说,不可覆盖测量 |
| 结构层级、视觉平衡、品牌观感、尚无测量器的残余差异 | VLM critic + owner | 可评论/排序,不可单独终审 |
| client-ready 最终接受 | owner | 只提供辅助证据 |

VLM 指控只有三种归宿:`verified`、`falsified`、`not_measurable`。只有
`verified` 可进入自动 repair;`not_measurable` 只进入 owner review queue,
不能驱动循环修改。Reviewer 不直接输出 CSS/HTML patch。

### 13.3 推荐的最小闭环

```text
target evidence + generated evidence
-> deterministic role/region matching
-> full-page overview + role crop + overlay + measured facts
-> VLM micro-critic: structured defect hypotheses only
-> deterministic claim verifier
-> verified claim
-> bounded fitter OR topology regeneration
-> render again
-> hard gates + objective improvement check
-> keep best-so-far; non-improvement rolls back
-> owner final review
```

每条 reviewer 输出至少包含:

```json
{
  "region_id": "experience.heading.2",
  "criterion": "alignment",
  "claim": "generated heading is farther right than target",
  "target_bbox": [46.9, 210.2, 132.0, 221.1],
  "generated_bbox": [54.8, 211.0, 140.1, 222.0],
  "severity": "material",
  "measurability": "deterministic",
  "confidence": 0.82
}
```

`confidence` 只用于排序待验证指控,永不绕过 verifier 或 hard gate。

### 13.4 输入包装:不再只喂两张整页图

Reviewer 输入分四层,各层只回答其适合的问题:

1. **整页缩略图**:页数、密度、整体层级和视觉平衡;
2. **role-aligned crops**:header、heading、entry、right metadata 分区比较;
3. **overlay/difference map**:帮助定位候选区域,不直接判定语义;
4. **measured facts**:明确 target/current/delta、evidence_id 和测量 provenance。

target 与 candidate 文本不同,普通 pixel diff 会被文字内容主导。布局/层级评审
增加 candidate-blind 视图:保留文本 bbox、字高、行宽和装饰,把真实文字替换成
统一 glyph mask/灰条;原始页面只供整体观感,内容正确性仍由独立 gate 负责。

### 13.5 Reviewer Calibration R0(先于 L1/L2 与模型选型)

在扩建三个 reviewer、双模型或 C2 前,先用现有 artifacts 做一个有真值的小实验:

1. 从 D→E/E→F 账本选 owner 已确认的 true defects、false accusations 和 no-diff;
2. 对已批准 HTML 确定性制造单变量 mutation:左右/上下偏移、字号、line-height、
   alignment、wrap、clipping、section order、rule/color;
3. 每类至少含 tolerance 内、刚超 tolerance、明显错误三档,并记录 mutation 真值;
4. 对同一病例比较四种输入:整页、crop、crop+overlay、crop+overlay+facts;
5. 每次做 A/B 换序和重复运行,但不默认多模型投票会提高正确率;
6. 记录 defect recall、false-positive rate、region/bbox 命中、方向判断、
   可测指控率、换序一致性、重复稳定性、成本和延迟;
7. owner 在运行前批准关键/非关键缺陷门槛;不再借用通用 kappa 数字替代本任务门槛。

R0 决策门:

- `crop+facts` 对本地真值稳定有效 → 上线单一 micro-critic,先不加模型委员会;
- 只能正确验证给定假说 → VLM 放在 hypothesis verification,缺陷候选由测量器产生;
- 能发现问题但误报高 → 只生成非阻塞 review notes;
- 相对纯确定性基线无净增益 → 从自动 loop 移除,保留 owner 终审。

模型选择必须发生在 R0 本地校准集上,不得从通用 benchmark 排名或“公认最强”
直接推定。任何新模型调用都是 live provider evaluation,沿用既有预算、隐私、
artifact 和 owner-review 约束。

### 13.6 对当前 C-pipeline 排序的影响

1. 当前 E→F 几何/内容 blocker 继续按 §12.2 处理,VLM 不介入已可测残差;
2. E→F 通过后先完成 Architect 消融和至少一个反向 pair;
3. 然后执行 Reviewer Calibration R0;
4. 只有 R0 证明 VLM 有净增益,才实现 §8.2 的 micro-reviewer 或双评审升级;
5. C2 的启动与 reviewer 选型解耦:DSL/`LayoutTemplateSpec` 验证不等待一个
   “完美视觉裁判”,也不允许 VLM verdict 替代确定性验收与 owner 终审。

## 14. 增补(2026-09-11):目标 PDF → HTML → PDF 往返重构验证

owner 提议:先把 Adobe 提取的目标版式信息用确定性代码编译成 HTML,再渲染回
PDF 并与原目标比较;模板通过后,后续填充和反馈循环均基于该 HTML 表达。

结论是:**采纳“目标侧往返重构”作为 compiler 的独立 proof lane;不把重构稿
本身等同于可复用模板,也不把全页 pixel diff 升格为唯一验收门。**这个方向的
价值来自先隔离模板重构问题和候选内容 reflow 问题,而不是因为 LLM 擅长写
HTML。能够从 evidence 确定性派生的结构和几何仍不交给 LLM。

### 14.1 两种 HTML 必须分开

1. **Target reconstruction/facsimile(临时诊断件)**:可以在 run-local artifact
   中使用目标原文,以保持原始字宽、换行和分页,用于检查 Adobe evidence、
   `LayoutTemplateSpec` 表达能力和 renderer 偏差。它不得获批为产品模板,不得
   进入候选生成路径;
2. **Reusable flow template(产品契约)**:只含语义 role、slot、布局、样式和
   flow 决策;不得保存目标候选人事实或目标页背景。它必须能承载不同长度、
   缺失字段和多页内容,并由现有 `LayoutTemplateSpec` 表达。

这里存在不可省略的逻辑边界:若 reconstruction 使用目标原文,它只是有内容的
oracle;若将原文替换为通用 placeholder,字宽和换行会变化,其全页 pixel diff
便不能再按原目标逐像素解释。因此 reconstruction 证明“目标证据能否被编译并
还原”,synthetic probes 才证明“模板能否泛化”,两者不能互相替代。

禁止把 Adobe 每个 span 绝对定位后得到的高相似页面直接晋升为模板。绝对坐标
facsimile 可以帮助诊断 evidence 完整性或 renderer 偏差,但不证明语义 HTML、
安全 reflow 或 `LayoutTemplateSpec` 已经成立。

### 14.2 全页 diff 的能力边界

“重构稿与目标的每个差异都 100% 归因于 compiler 表达力”不成立。残差至少有
四类来源:

| 残差来源 | 例子 | 处置 |
|---|---|---|
| extraction | Adobe 漏抽/误抽、字体或图形证据缺失 | 修 analyzer/adapter 或标记 unsupported evidence |
| spec/compiler | role、rule、间距、flow 无法表达或映射错误 | 扩充/修复 `LayoutTemplateSpec` compiler |
| renderer | 字体 fallback、hinting、抗锯齿、坐标取整、分页器差异 | renderer 校准或显式容差 |
| comparator | 页面配准、文本分词/匹配、颜色空间造成假差异 | 修比较方法,不得反向污染模板 |

因此 pixel diff 是“像素差的全量枚举”,不是“模板缺陷的全量枚举”。验收采用
分层证据:

1. 先按 semantic role/region 对齐,比较 bbox、baseline、x0/x1、字号、行高、
   wrap、rule geometry 和颜色;
2. 对已配准区域做 masked pixel diff,用于发现尚未建模的装饰或字形残差;
3. 每个 diff cluster 必须归入上表来源之一;不能归因的进入 owner review,
   不自动修改 compiler;
4. VLM 只按 §13 对残差提出结构化假说,仍需确定性 verifier 确认。

“已知不可表达区域”必须记录 region、evidence provenance、原因和容差,不能用一个
宽松的全页阈值吞掉。原始 global pixel score 可作趋势指标,不得单独决定 pass。

### 14.3 它能抓到什么,抓不到什么

往返重构适合提前暴露:

- section heading 与 rule 的相对位置;
- 字号、大小写、联系行分隔符和基础间距;
- 页面边距、列边界及目标证据中已有的装饰。

但它不会必然复现由候选内容触发的缺陷,例如长文本造成的 `flex-shrink`/clipping、
换行与分页、缺失字段塌缩、Filler 与模板 marker 共同造成的 double bullet。
所以它可以减少 E→F 的混合诊断,不能取代现有 provenance/coverage/content gates
和 short/medium/long calibration probes。

### 14.4 复用现有主线,不再造一套 compiler

主线已经存在
`TargetLayoutEvidence → LayoutTemplateSpec → HtmlRenderer → PDF`以及基于
synthetic short/medium/long 内容的 layout proof。C2 应在这条链上增加 target
reconstruction variant 和比较证据,而不是另建“全页 HTML DSL”、第二个 proof
service 或第二套 matrix runner。renderer-specific HTML/CSS 仍属于
`RenderPlan`/renderer;HTML 不是持久化产品模板契约。

建议的数据流是:

```text
Target PDF
-> Adobe extraction + provider-neutral normalization
-> TargetLayoutEvidence
-> deterministic LayoutTemplateSpec compiler
   -> target-content reconstruction (临时诊断)
      -> role/geometry diff + masked pixel diff
   -> synthetic short/medium/long proofs (泛化/reflow)
   -> reviewed ClientFacingRenderContext (候选内容)
      -> content/structure gates + owner final review
```

### 14.5 最小验证实验与决策门

在实现自动全页拟合闭环前,只用目标 F 做一个窄 spike,复用现有 artifact 和 proof
路径:

1. 从现有 normalized evidence/spec 生成一份 target-content reconstruction;
2. 输出 role/word-box 几何差异和局部 masked pixel diff;
3. owner 抽检差异,逐项标注为 extraction/spec/renderer/comparator;
4. 同一 spec 跑现有 short/medium/long synthetic proof;
5. 报告“可稳定归因的 material residual 比例”,不以单一相似度分数宣告成功。

决策门:

- 大部分 material residual 能稳定落到 `LayoutTemplateSpec` 字段,且修复不会损害
  synthetic probes → 把 reconstruction proof 纳入 C2;
- reconstruction 很像但 probes 失败 → 判定为 facsimile,不得晋升;
- 残差主要来自 extraction/renderer/comparator → 先修对应层,不扩 compiler;
- reconstruction 相对现有逐 role 测量无新增缺陷发现 → 不建设自动 pixel-fit loop。

本节只补充 C2 的验证方式,不改变顶部状态和 §12 的启动条件,也不构成新增 runner、
schema 或 provider 的实现批准。

## 15. 增补(2026-09-11):a_pipeline.py 源码重建完成（Phase 0，事故全记录）

### 15.1 事故机制（会话日志取证定案）

2026-09-10T18:48:45Z 的 bash heredoc 编辑中,脚本先 `a = open("a_pipeline.py").read()`
取得真实源码,随后对 `a` 做 scratch-repair 门禁插入编辑,但最终写盘语句误写为
`open("a_pipeline.py", "w").write(s)`(`s` 为此前读取并修改的 c_pipeline 内容)——
c_pipeline 内容整体覆盖 a_pipeline.py,真实源码丢失。原文件改名为
`a_pipeline.py.corrupt-20260910` 留档,模块级 `a_pipeline.pyc`(3.12 字节码,
最后一次成功导入状态)承担导入。两个"丢失编辑"的定性修正:

- **编辑一(18:48:06,已落盘成功)**:`_export_pinned_html_to_pdf` 的 Chrome 导出
  命令增加 `--virtual-time-budget=10000`(owner ruling 2026-09-10,E→F 第十三轮
  冻结;字体时序根治)。该编辑在覆盖事故**之前**已成功写入。
- **编辑二(18:48:45,从未落盘)**:在 `independent_checks` 前插入
  `_per_line_edges` + `_line_stability` 双渲染逐行几何一致性门禁。其完整载荷
  恰好保存在同一 heredoc 的 NEW 文本中(与 c_pipeline 右缘勘误同批)。

故**丢失终态 = pyc 状态 + 两个 owner 批准编辑**,与记分板早先"两项编辑均丢失"的
表述合并修正:编辑一在 pyc 状态之后、事故之前落地,编辑二从未落地。

### 15.2 重建路径与验收

- 反编译选型:uncompyle6/decompyle3 不支持 3.12;Decompyle++(pycdc)仅产出
  789 行骨架(55 处不支持)→ 按 owner 预案改走 **dis 字节码分析 + 会话日志取证
  重放** 双路并行。
- 会话日志取证:跨全部 pi/codex 会话转录提取 33 个原文切片、98 个 edit 载荷、
  606 条 bash 证据、57 个 heredoc `s.replace` 载荷;按函数建证据包
  (dis + pycdc + slices + edits + pyc 常量表)。
- 重建执行:8 个并行 subagent(其中 w1 因挂起中断后重派),74 个顶层函数逐个
  重建,验收标准 = **与 pyc 逐函数字节码完全一致**(funcheck)。
- 关键方法学发现:① snippet 必须带真实 import 前置块——CPython 的
  known-module AST 优化对 imported 模块属性访问生成 plain LOAD_ATTR,
  这解释了 pyc"旧调用约定"形状,并非编译器版本差异;② 表达式换行布局改变
  jump/压缩形状(两处实证);③ try 范围以异常表为精确指纹;④ co_names 顺序
  = 源码出现顺序;⑤ 旧 era(重构前)测试文件 import 已删除 API
  (`_calibration_prompt`/`run_refinement_loop` 等),对冻结 pyc 本身即无法
  收集,不构成可跑验收面——可跑 era 面即当前 220 行基线。
- 终态验收(全部落盘 `runs/a_pipeline_reconstruction_20260911/`):
  - **72/74 顶层函数与 pyc 逐字节一致**(含全部嵌套闭包/lambda/常量);
  - `_export_pinned_html_to_pdf` 为 pyc 状态 + owner 批准编辑一(预期差异);
  - `run_builder_repair_loop` 残差 = comprehension 过滤器跳转编码极性
    (pyc 由 3.12 beta 期编译器产出;已发布的 3.12.0–3.12.12 全部产生
    反转形状;两形状运算/落点语义完全等价)——如实入档,不作字节伪装;
  - 新增 `_per_line_edges`/`_line_stability` = owner 批准编辑二(逐字载荷);
  - 模块级:import 顺序/常量按模块反汇编逐字恢复;
    `RUNS = Path(__file__).with_name("runs")`(非 resolve 版,字节码定案);
  - **测试:121/121 全绿**(a+b+c 管线,存档
    `tests/test_results/pytest/pytest_phase0_a_pipeline_rebuild_20260911T034500Z.log`);
  - import 自检:A/B/C 三管线可导入,dir() 公开名 181,pyc 顶层名 0 缺失;
  - `.pyc 依赖解除:模块级 a_pipeline.pyc 删除(取证副本
    runs/a_pipeline_pyc_forensic_backup_20260911.pyc)。
- 文件写入纪律执行:重建期间 subagent 仅写 /tmp 证据与 out/ 片段,仓库仅由
  orchestrator 在验收后单点写入 a_pipeline.py 本体。
