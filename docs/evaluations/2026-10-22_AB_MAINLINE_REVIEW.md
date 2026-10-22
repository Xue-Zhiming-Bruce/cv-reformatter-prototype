# A/B 主线评审 — 2026-10-22

Status: `Evaluation evidence — owner initially selected A, October 22, 2026`

所有者于 2026-10-22 明确“先选A”：**A 作为内容保留与模板复用优先的初始研发主线**。B 暂留为显式比较入口，原 API 保留到替代条件通过；这不是通用保真胜者或成品验收。以下比较事实和限制继续有效。追加试验改变了先前判断的一部分：B 已经生成内容完整的普通和盲化 PDF，正文栏及分隔线比 A 更接近这个目标，不能再用“B 必然漏内容”作为选 A 的理由。倾向 A 的主要依据现在是现有候选形状适应性、程序绑定和较窄的变更面；代价是 A 的布局能力必须继续补齐。

这不是通用保真胜者结论。证据仍只有一个合成侧栏目标；任一路都未满足产品 LayoutTemplateSpec 等价、其他布局和分页验收条件，不足以永久淘汰另一条路线或批准 API 切换。

## 直接查看

本地入口：[`REVIEW_INDEX.md`](../../data/generated_outputs/pipeline/ab_selection_20261022/REVIEW_INDEX.md)，同目录 `index.html` 是可视评审页。所有 outcome PDF、目标预览、并排图、差异图、调用、失败和回滚记录在现有忽略目录中。

优先看 `live_slotfix_standard_2/owner_review/` 和 `live_slotfix_blind_2/owner_review/`，再看相同源版本的 `live_slotfix_standard_1/`、`live_slotfix_blind_1/`。入口还链接全部早期结果、独立检查和每个修复版本；没有以成功结果覆盖失败结果。

## 状态核实与公平性

指定工作树 `/Users/xuezhiming/.codex/worktrees/7d35/CV converter prototype` 开始时干净，HEAD 为 `f0408d8`。已核实 `be3c22e` 的保存 CandidateProfile 修订→预览→接受具体成品流程，以及 `f0408d8` 的共享 ClientFacingRenderContext 入口。API 仍调用原 `app/generation/`，未接入 retained pipeline；两路和原 API 均保留。

| 因素 | 分类、处理和剩余影响 |
| --- | --- |
| 原诊断为 scripted builder | 输入/配置限制；本次真实调用 A/B builder、视觉 reviewer 和可触发的归因角色，仅 DeepSeek；scripted 历史结果不作模型能力证据 |
| 合成目标、Adobe-shaped fixture、空结构草稿 | 输入/覆盖限制；没有 live Adobe、真实目标分析或多布局质量证据。增加调用次数不会增加目标多样性 |
| 标签缺少逐项批准 | 所有者明确改为启发式自动认定；已更新产品/架构/ADR 0001。仅完整匹配现有产品标题别名；未知标题不可渲染。兼容 `approved` 状态不制造人工批准或成品批准 |
| A 扩展头部继承姓名样式、侧栏 `ptpt`、标题/正文间距分离 | 实现缺陷；分别修复并用实际 PDF 回归。非目标特例 |
| probes 附加内容重复投影 | 实现缺陷，已修复；首轮调用方还提前投影过一次。首轮 probes 无效，后续直接传 canonical 原始 fixture，一次处理 |
| 之前日期缺失 | 已修复的检查误报；本次未复现。缺少头部 Engineer 的报告不等于工作经历岗位文字不存在 |
| B 头部副标题被相同工作岗位文字掩盖 | 检查漏报；实际省略同时存在。已按真正出现的 name/contact 槽位计数，缺少槽位即不能借用其他位置同文值；独立产品验证保留 |
| 多轮预算固定预留40导致小预算跳过 A 模型归因 | 配置障碍；新增显式冻结的 reserve，默认40不变；本次设4，覆盖一整次 Builder turn。该预留是归因入口调度条件，不能保证多请求归因不会耗用预留，见稳定性限制 |
| 归因工具并发 PDFium 导致进程崩溃 | 实现缺陷；系统崩溃记录显示两个线程同时加载 PDFium 字体。回归先复现并发，再将 PydanticAI 归因工具顺序执行；没有切换供应商或弱化门槛 |
| 模型采样和实际请求计数 | 已统一非 thinking、temperature0、输出上限16384；HTTP 发送前占额，包括 SDK 重试。每组两路等额共享总上限。表示法的 typed / authored JSON 协议仍不同 |
| 同样例 B 省略内容、模板未覆盖未来章节 | 真实输出/模板复用缺陷；不证明 HTML/CSS 原理上无法表达。已保留后来完整结果和之前失败，不推断通用失败率 |
| A 规则、条目样式未被 renderer 消费 | 当前能力限制；原生 `content_shapes_match_evidence` 仍失败，未路由给无法修改这些属性的 Builder，也未伪装全门槛通过 |
| B best / ready | 探索性选版；不是完整内容、视觉或产品验收。独立检查所有追加试验的渲染版，包括未选中和回滚版 |
| retained runner 初始编译仅测量侧栏类 | 共同支持范围限制；没有绕过编译器单独帮助 B。两路均未证明其他布局或产品 LayoutTemplateSpec 等价 |
| 工作树缺少语料 | 环境问题；本地软链接复用原项目已有授权语料，未复制/新建语料。Resume I 完整 evidence bundle 仍缺，相关 optional 测试跳过 |

## 实际运行

始终使用 `app.pipeline.service.run_e5`，即 `python -m app.pipeline` 的同一 canonical runner，无新 experiments、runner、供应商、语料或结果 schema。

每组两路共享同一已保存合成 CandidateProfile、共享 disclosure/blind mapper、目标 PDF、Adobe/normalized/draft、字体、Chrome、标签目录和采样设置。输入仍位于 `consolidation_review_20261021T153547Z/inputs/`；各组冻结校验和与源文件身份。

仅既有 DeepSeek 适配器，模型别名 `deepseek-flash`，视觉角色同样 DeepSeek；未调用 Qwen 或 Adobe。官方本日文档确认该别名为支持视觉的 V4.1 Flash；别名未来可能变化。输入均为合成，不批准保密数据生产处理或供应商采用。

| 批次 | 配对数 / 源提交 | 修复上限 | 实际请求 |
| --- | --- | --- | --- |
| 原始/修正/首次最终普通、盲化 | 6 / 5ca671a、a16e7c8、7ca5682 | 0 | 24 |
| `live_repair_standard_1` | 未配对 / 69eac8d；A 归因时崩溃，B 未执行 | 3 | 3 |
| `live_repair_standard_2`、`live_repair_blind_1`、`live_repair_standard_3` | 3 / 8aaa682 | 3 | 5、7、9 |
| `live_slotfix_blind_1`、`live_slotfix_standard_1`、`live_slotfix_standard_2`、`live_slotfix_blind_2` | 4 / aad5f86 | 3 | 11、10、11、9 |

共 **13 个完整配对、26 份选定主 outcome PDF、1 次部分执行**；保留总计36份实际主/修复渲染 PDF，另有 probes 和重复导出。实际计额 **89 次 HTTP 请求**，比前次交付追加65次。所有13个完整配对记录源身份稳定；不将不同版本或配置混成同分布统计成功率。

最终四组每路上限12次，只有 `live_slotfix_standard_1` 受当批剩余额度影响为每路8次。其他三组的模型、源版本、修复上限、每路12次额度、reserve4、210000-byte 请求 envelope 完全相同。每组两路额度始终对称，cap17的多余1次不分配给任一路。全额重复组不是挑选最好样例。

## 独立内容、结构、隐私和视觉检查

以原始 CandidateProfile 重新建立 ClientFacingRenderContext，调用产品 `validate_generated_content` 读取实际 PDF；不依赖 retained 叶子账本。另查实际 PDF 字边界、重叠候选、图片对象、已知目标文字与盲化标识泄漏，再独立本地重导出、比较页图及文字坐标。

| 最终同源四组 | A | B |
| --- | --- | --- |
| 普通两组产品内容 | 均通过，13个预期项 | 均通过，13个预期项 |
| 盲化两组产品内容 | 均通过，12个预期项 | 均通过，12个预期项 |
| 本次目标事实/盲化姓名邮件泄漏 | 未发现 | 未发现 |
| 主输出结构诊断 | 均1页，字边界/重叠候选0，SUMMARY与EXPERIENCE均在 | 同样通过这些诊断；教育/技能缺独立标题层级，布局仍有偏差 |
| 同 HTML 独立重复导出 | 四组均页图一致、字坐标稳定 | 四组同样稳定；不等于模型模板跨次一致 |
| 既有短/中/长内部形状 probes | 四组均通过内容/accounting/privacy/repeat；长例2页 | 四组均有内容失败：语言、认证（中/长）、附加章节槽位未覆盖 |
| 读图 | 姓名栏位置接近目标；侧栏标签/正文整体偏右，分隔线在标签下，扩展章节列位不一致，条目层级仍不够忠实 | 正文栏与分隔线拓扑较接近目标；姓名过于靠左、标签对齐和间距偏差，模型生成的模板之间仍变化 |

新增四组主并排图、代表普通/盲化差异图和长例每页已读图核对。主样例未见裁切、空白页或文字重叠。两路长例都出现稀疏续页；A 第二页只有附加章节几行，某 B 模板第二页只有末尾技能几行，无重复页头。没有通用 keep-together / continuation 策略验收。

B probes 的缺失是**当前生成模板的复用覆盖缺陷**：short 也缺语言和附加章节，long 还缺认证。不是长内容独有故障，不能据此宣称 HTML 不能分页；有 B 长例实际生成2页。A 的动态候选绑定与 B 的固定选定模板存在适应策略差异，复用检查不包含为每个新候选重新调用 B builder。

这些 probes 是既有内部 CandidateDocument fixture，不证明产品结构化 `additional_sections` 等价；本次评审时 CLI 尚明确拒绝它，原 API 能力保留。最终四组9份 B 主/修复渲染版均通过独立产品内容检查；其中2次局部修复被提升、3次因同一测量没有改善而回滚。局部提升不意味着全局视觉提升。

修复前的 `live_repair_blind_1` 初版缺技能/教育等，后版补回但仍缺头部副标题；原生账本曾漏报，独立验证未放过。崩溃记录、UsageLimitExceeded、未知 usage、回滚和测量不能绑定均可从入口查看。

## 成本、运行稳定性与维护

89次计额中86次有可汇总的 usage：327690 input、48563 output tokens。官方峰值无缓存 $0.30 / $1.20 每百万 tokens，已记录部分估算 **US$0.1565826**；另外3次 usage 未完整记录（1次崩溃、2次受限归因），不能当作零费用。按每次210000-byte/8图片/16384输出 envelope 加协议余量，全额预留这3次后，累计保守估算上界 **US$0.441429**，不是账户账单。

初始24次/US$3范围用完后，所有者明确要求“别管token，多尝试几次”。后续逐批增加有界请求额度，在下一组开始前，用已知 peak usage + 未知调用全额 envelope + 下一组全部调用上界核算累计US$3；每次预检均低于上限。详细追加额度、预检、实际计额在 `LIVE_SCOPE.md`，没有无界调用。

- PDFium并发崩溃已修复；后续完整组未再崩溃。归因仍会遇到工具/输出校验及请求上限；`live_slotfix_standard_1` A 用满8次而未产生 Builder 修复，最后2次 usage 未归入成功结果。reserve4 只保证进入归因前有余额，现有多请求归因仍可能耗用预留；这是执行稳定性限制，不视为几何能力证明。
- A 多轮仍未改变主布局：部分观察不能绑定到支持的测量，主要差异又超出四个 Builder 原语。B 可修复完整槽位和局部间距，但局部改善未保证全局一致。
- A 把内容适应放在程序里，模板变动面窄，适合内容控制优先；增加版式要付出结构原语/测量/renderer 的维护成本。本次固定 renderer 自身也有真实缺陷。
- B CSS 表达和目标贴近能力更自由；需要可靠的全部槽位、条件区域、CSS安全、分页及复用验证。header accounting 已补强，collection accounting 仍是构造账本+文本存在检查，不是完整 DOM 叶子身份或重复值证明。
- 两路实际费用都低，未构成选择主因。wall-clock 含不对称双次渲染、探针页数和归因工作，不能用总耗时宣称表示法或模型速度优胜。没有维护工时基准，不按行数决定。

参考：[DeepSeek定价](https://api-docs.deepseek.com/quick_start/pricing/)、[视觉能力](https://api-docs.deepseek.com/guides/vision/)、[PDFium线程限制](https://pypdfium2.readthedocs.io/en/stable/python_api.html#incompatibility-with-threading)。

## 决定、适用范围与迁移条件

所有者已经初步选择 **A 主线**，限内容保留/复用优先的研发收敛。CLI 和程序入口默认只执行 A；B 与双路比较必须显式指定。这项选择不宣称 A 为永久产品胜者，也不批准立即切换 API 或删除仍承担产品能力的实现。

选择、范围及限制已记入现有架构、ADR 0006 和唯一活动路线图；实际切换仍须满足下列迁移条件：

1. 同一 canonical workflow 扩展现有获授权目标测量，覆盖一栏、其他双栏/侧栏、稀疏、多页；本次大量重复不能替代它。
2. 实现产品 LayoutTemplateSpec→所选表示法的显式编译及失败/rollback 等价；A 内部 state 和 B authored HTML 都不能直接改名当作产品契约，缺测量 fallback 需明确处理。
3. 产品结构化自定义章节、条目/链接/项目符号层级、disclosure/blind、保存修订身份与现有 API 行为等价。
4. A 补齐已识别结构/规则能力；或 B 强制完整可复用槽位及独立内容/结构验证。修复 reviewer/measurement/预算预留的运行限制，验证跨版本视觉回归。
5. 完整长内容的分页、续页与 keep-together 策略及成品审阅通过后，才讨论 API/CLI 共用引擎和退役路径。

不删除两路或原 API，不改前端，不进行 API 切换、全面重构或上线验收。B0/B1退出条件未完成。

## 提交与验证

原必要修复：`cf0095b` 评审续页/差异图；`dd1aa97` 自动标签策略及权威文档；`4c1c29c` 实际请求预算/采样；`5ca671a` 头部样式；`3f773de` probe exactly-once；`a16e7c8` CSS单位；`7ca5682` 标题/正文同一行。初版证据报告：`db2ecfa`。

本次继续试验的独立修复提交：`69eac8d` 冻结可配 Builder reserve；`8aaa682` 顺序归因工具防原生崩溃；`aad5f86` 按真实槽位记账头部。回归先证明缺陷，再验证修复；未降低质量门槛或删除有价值测试。

在最终代码提交 aad5f86 上：

- 全量离线：**646 passed、35 skipped、1 deselected**，4个既有 fixture serializer warnings。日志 `tests/test_results/pytest/20261021T165605Z_ab_final_full.txt`。
- 全部 retained pipeline unit/integration（排除 live/local_dataset）：**248 passed、19 deselected**，单独测试时有1个事件循环弃用提示。日志 `tests/test_results/pytest/20261021T165350Z_ab_header_accounting_after.txt`。
- 之前的失败和中间验证日志保留在同一 canonical 目录；不提交原始测试报告/二进制。

实际命令：

```bash
AB_PYTHON='/Users/xuezhiming/Desktop/CV converter prototype/.venv/bin/python'
"$AB_PYTHON" -B -m pytest -m 'not live_provider' -q
"$AB_PYTHON" -B -m pytest \
  tests/unit/test_pipeline_adaptation.py tests/unit/test_pipeline_layout.py \
  tests/integration/test_pipeline.py tests/integration/test_pipeline_rendering.py \
  tests/integration/test_pipeline_loop.py tests/integration/test_pipeline_evidence.py \
  -m 'not live_provider and not local_dataset' -q
```

自动标签是保守启发式，不是通用语义证明；异常事实恰好与标题同词仍有误认风险。本次无已知泄漏不替代未来隐私检查。
