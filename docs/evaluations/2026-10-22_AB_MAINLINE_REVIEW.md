# A/B 主线评审 — 2026-10-22

Status: `Evaluation evidence — owner selection pending`

建议以 **A 作为下一阶段收敛主线**，理由是候选内容由程序完整绑定、结构变化的控制面较窄，当前真实 builder 输出的内容稳定性也较好。这不是保真胜者或产品验收结论：A 的标签位置、分隔线、正文列与目标仍有明显差异。B 有更自由的布局表达能力，但当前模型会省略必要槽位；它的安全校验与可复用模板完整性仍需进一步约束。最终由所有者查看成品后选择，当前保留两路及原 API。

## 直接查看

本地入口：[`REVIEW_INDEX.md`](../../data/generated_outputs/pipeline/ab_selection_20261022/REVIEW_INDEX.md)，同目录 `index.html` 为可视评审页。所有二进制、原始调用记录与检查结果留在现有忽略目录中。

- 最终普通模式：`live_final_standard/owner_review/`。
- 最终盲化模式：`live_final_blind/owner_review/`。
- 每组含目标 PDF、A/B outcome PDF、目标/成品预览、并排图、差异图、原生 gates、独立产品内容验证、重复渲染文件和长内容分页证据。
- `live_standard/`、`live_blind/`、`live_corrected_standard/`、`live_corrected_blind/` 保留前轮输出和失败证据，不用后来的成功替换失败记录。
- 独立复核：`INDEPENDENT_REVIEW.md`；来源、额度与定价：`LIVE_SCOPE.md`。

## 核实状态与公平性

工作树为 `/Users/xuezhiming/.codex/worktrees/7d35/CV converter prototype`，开始时干净，HEAD 为 `f0408d8`。已核实 `be3c22e` 的保存修订→预览→具体成品接受流程，以及 `f0408d8` 的 CandidateProfile→共享 ClientFacingRenderContext→内部 CandidateDocument 入口。API 仍调用 `app/generation/` 的实现，未连接 retained pipeline。

| 因素 | 分类与处理 |
| --- | --- |
| 原诊断只有 scripted A/B | 输入/配置局限；本次两个 builder 和视觉 reviewer 都真实调用既有 DeepSeek 适配器，未用 scripted 产物冒充模型输出 |
| 合成目标、合成 Adobe-shaped evidence、空结构草稿 | 仍是范围限制；没有 live Adobe 或真实目标分析质量证据，不能外推到公开 PDF 全集 |
| 标签原先缺少人工输入 | 原来的审批阻碍；所有者本次明确改为启发式自动认定，已记录到产品契约、架构和 ADR 0001；仅完整匹配现有产品标题别名，未知标题继续不可渲染，不制造人工批准记录 |
| A 额外头部字段继承姓名样式 | 实现缺陷；按已有测量样式处理并补回归 |
| A 侧栏 CSS `ptpt`、间距只推开标题 | 实现缺陷；实际 PDF 回归后修复，最终样例来自修复后的源版本 |
| probes 附加条目被重复展平 | 实现缺陷；修复 exactly-once 投影；首轮调用方还提前做过投影，最终使用原始 canonical fixture 交给 runner 一次处理。首轮 probes 作废，保留为失败证据 |
| 先前日期缺失报告 | 已修复的检查误报；本次没有复现日期误报。独立产品检查中的缺少岗位文字指预期头部副标题缺一次，不等于工作条目中的岗位文字不存在 |
| B 普通/盲化输出省略槽位 | 真实 builder 输出缺陷；原始模板、PDF、leaf gates 与独立产品验证均保留，不据此声称 HTML/CSS 原理上无法表达这些内容 |
| B 的 `best` / ready 状态 | 探索性状态；PDF presence 仍只是原 runner 的诊断，未降低或重新定义既有门槛。本次另用产品内容验证器独立检查两路 |
| retained runner 共用侧栏初始编译 | 两路共同的支持限制；没有绕过编译器让 B 单独处理它拒绝的输入，因此尚不能证明任一路支持其他布局 |
| A/B 的采样原来不同 | 已统一非 thinking、temperature 0、相同输出上限与已有模型；typed proposal 与 authored JSON 协议仍因表示法而不同 |
| 原预算漏记失败/SDK 重试、冻结常量而非实际限额 | 执行缺陷；已改为 HTTP 发送前占额、共享全局额度、两路等额，并冻结真实限额；模拟 SDK 重试覆盖发送前阻止第三次请求 |
| 原工作树缺少公开语料 | 环境配置问题；以本地软链接复用原项目的同一套已授权语料，不创建第二套语料。Resume I 的完整 Adobe/normalized/draft bundle 仍缺失 |

## 运行条件与范围

仅使用现有 `run_e5`（`python -m app.pipeline` 的 service），无新 runner、实验目录、供应商、语料集合或结果 schema。两路共享同一目标、候选人、证据、字体、Chrome、候选内容投影和标签目录；普通/盲化是同一 CandidateProfile 的两个 disclosure 场景。每个场景内条件对称。

真实模型只有 `deepseek-flash`，包括视觉角色。所有者明确排除 Qwen。官方 2026-10-22 文档确认该别名支持视觉；供应商别名可变，未来复现需重新确认服务版本。未调用 Adobe，不发送保密数据，不创建成品批准。

共三轮、每轮普通/盲化各一组，共 6 个配对、12 份主 outcome PDF。源版本依次为 `5ca671a`、`a16e7c8`、`7ca5682`；所有 run 记录 `source_identity_stable=true`。首两轮用于诊断和前置修复，最终比较以 `live_final_*` 为准，不能将跨版本结果作为同一版本的统计成功率。

每路 1 次初始 build + 1 次独立视觉 review，修复轮次均为 0。第一轮每组最多 12 次，第二轮每组最多 8 次，第三轮每组最多 4 次；两路等分且共享全局上限。实际每组均为 4 次，共 **24 次 HTTP 请求**，已到本次授权上限，不再调用。没有失败 HTTP 重试。没有评估多轮修复后的收敛性，不能据此判定 B 经修复必然失败或成功。

## 独立检查与结果

除原生 ledger/gates 外，以原始 CandidateProfile 经共享 mapper 得到的 ClientFacingRenderContext 调用产品 `validate_generated_content`，直接读取实际 PDF。两路同样检查隐私、PDF 字边界与重叠候选，并做一次独立本地重复导出，比较页图及文字坐标。

| 最终场景 | A | B |
| --- | --- | --- |
| 普通：产品内容检查 | 通过，13 个预期项 | 失败：头部副标题、联系方式、技能、教育缺失 |
| 盲化：产品内容检查 | 通过，12 个预期项 | 失败：头部副标题、技能、教育缺失 |
| 本次目标人物文字泄漏 | 未发现 | 未发现 |
| 盲化姓名/邮件泄漏 | 未发现 | 未发现 |
| 主 PDF 分页/字边界 | 1 页，无出界字/重叠候选 | 1 页，无出界字/重叠候选；内容缺失不能被此项抵消 |
| 独立同 HTML 重复导出 | 页图一致，文字坐标稳定 | 页图一致，文字坐标稳定 |
| canonical 短/中/长内部 probes | 两个最终模板下均通过；长例 2 页 | 均未通过内容 accounting / PDF presence |
| 人工读图 | 标签/正文整体偏右，规则落在标签下而非目标正文栏，条目层级/行距仍不够忠实；保留完整候选内容 | 部分侧栏更接近目标，但结果间布局变化，最终省略候选人独有内容 |

原始目标和所有最终主输出的页图、差异图、A 长例两页及 B 长例均已读图核对。未发现本次主样例的裁切、文字重叠或空白页；这不是通用结构验收。A 长例第二页仅少量内容，无重复页头；它只证明简单流式分页和内容保留，未证明批准的 continuation / keep-together 策略。

原 pipeline 的 A `content_shapes_match_evidence` 仍失败，未伪装为全门槛通过。独立验证中 12 份主输出的 A 均保留内容，B 有三份缺失；这是单个合成目标、跨前置修复版本的观测，不是总体准确率估计。局部 repeat 的确定性不等于模型每次会产出同一模板。

Probes 使用已有内部 CandidateDocument 形状，**不证明**结构化 `additional_sections` 或产品 LayoutTemplateSpec 的替代等价性。CLI 仍拒绝非空结构化 additional_sections，原 API 支持能力保留。

## 成本、稳定性与维护

实际成功调用共 77,622 input tokens、15,829 output tokens。采用官方峰值、无缓存单价 $0.30 / $1.20 每百万 tokens，保守估算 **US$0.042281**，不是账户账单。24 次请求的字节/图片/输出 envelope 保守预算上界约 US$2.50，低于授权 US$3；未消耗剩余额度外的调用。

同一场景中 A/B 各两次请求；B 的提示/schema 输入略多、输出通常较长，但本样本费用差很小，不能决定主线。现有 wall-clock 包含 A 自带双次渲染、不同 probe 页数等不对称工作，不能用总耗时宣称某个模型或表示法更快。独立追加重复导出后，两路局部渲染稳定性均通过。

- A 的固定 renderer 和显式候选绑定缩小可变面，内容控制较直接；扩展布局需新增、测量并验证结构能力。本次也暴露了固定 renderer 自身的缺陷，不能只信结构 schema。
- B 可表达更自由的 HTML/CSS，复现部分侧栏更灵活；每份模板都需验证完整槽位、条件区域、分页、资产与 CSS 安全，当前 builder 仍会省略槽位。尚无维护工时基准，不能用行数或模型输出长度推断维护成本。
- 两路都尚未接入产品 LayoutTemplateSpec；B 的 authored HTML 不可存成该 provider-neutral 契约，A 的内部 layout-state 也不能直接改名视为等价。

定价/视觉能力参考：[DeepSeek 定价](https://api-docs.deepseek.com/quick_start/pricing/)、[视觉能力与图片 token 上限](https://api-docs.deepseek.com/guides/vision/)。这是公开/合成评估授权，不批准保密数据生产处理或供应商托管。

## 决定与后续迁移条件

请所有者选择：**A 主线**或 **B 主线**；也可因保真证据不足暂缓永久选择。当前建议 A，适用范围限已有测量侧栏类与已验证核心候选字段；这是研发收敛方向，不是 API 切换批准。

选择确认后才记录 accepted 选择、范围与条件；当前不新增 accepted 的 A/B ADR，不删除任一路或 API。迁移至少还需：

1. 真实获授权目标的测量证据与完整结构草稿；同一 canonical workflow 覆盖一栏、双栏、侧栏、稀疏与多页输入。
2. LayoutTemplateSpec→所选表示法的显式编译及失败/rollback 等价检查；清除或拒绝缺失测量时的默认值（例如侧栏 compiler 的 entry-column fallback）。
3. 结构化自定义章节及 entry/link/bullet 层级、完整 disclosure / blind-profile 与已保存修订身份的回归等价。
4. 独立内容、隐私、结构与视觉检查；A 剩余布局偏差或 B 槽位完整性修复后的实际成品审阅；多轮修复效果仍需另一次有额度的验证。
5. 明确分页与 continuation 策略，保留所有者对具体 HTML/PDF 的最终接受；之后再讨论 API/CLI 共用引擎。

## 提交与验证

必要修复逐个提交：`cf0095b` 评审续页/差异图；`dd1aa97` 所有者确认的自动标签策略及权威文档；`4c1c29c` 实际请求预算/采样对齐；`5ca671a` 头部样式；`3f773de` probe exactly-once；`a16e7c8` 侧栏 CSS 单位；`7ca5682` 标题与正文同一流式行。

运行均使用原项目既有 `.venv/bin/python -B -m pytest`，日志在 `tests/test_results/pytest/`。回归先证明失败，再验证修复；未弱化质量门槛或删除有价值测试。

- 全量离线 `-m 'not live_provider' -q`：645 passed、35 skipped、1 deselected、4 既有 Pydantic fixture warnings。该进程在最终 rail-flow 修复前启动，不将其冒充最终 HEAD 的新启动全量检查。
- 最终 `7ca5682` 上全部 retained pipeline unit/integration（排除 live/local_dataset）：247 passed、19 deselected。
- SDK 失败重试和 envelope 验证：包含在上述 pipeline 检查，未访问真实供应商。
- 首次全量的三个 corpus FileNotFoundError 由复用原项目语料解决；旧失败日志保留。

未进行 API 引擎切换、前端修改、全面重构、生产部署或产品上线验收。

自动标签认定是保守启发式，不是通用语义证明；异常目标中的人物事实若恰好与常规标题同词，仍有误认风险。本次合成目标未发生该问题，不能据此免除未来隐私检查。

实际验证命令（在本工作树执行；将终端输出 tee 到上述 canonical pytest 目录）：

```bash
AB_PYTHON='/Users/xuezhiming/Desktop/CV converter prototype/.venv/bin/python'
"$AB_PYTHON" -B -m pytest -m 'not live_provider' -q
"$AB_PYTHON" -B -m pytest \
  tests/unit/test_pipeline_adaptation.py tests/unit/test_pipeline_layout.py \
  tests/integration/test_pipeline.py tests/integration/test_pipeline_rendering.py \
  tests/integration/test_pipeline_loop.py tests/integration/test_pipeline_evidence.py \
  -m 'not live_provider and not local_dataset' -q
```
