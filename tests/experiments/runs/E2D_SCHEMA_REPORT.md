# E→D 冻结 · Schema 表达力边界上报（交 owner 裁决）

> 状态：⛔ blocked。本文件由 orchestrator 补写（原定由阶段四 subagent 交付，
> 其在 1800s 预算内完成 F→E/E→D/D→F 轨迹后被超时截断，未及落盘本报告；
> 本报告内容全部以磁盘实物为准核验，引用均可复查）。

## 1. 阻塞陈述（一句话）

D 目标（resume_D.pdf）的 header 实测为 **4 行设计（name / contact / tagline /
interests 条）**，其中 tagline 行与 interests 条都是 extension 类目标行；当前
scaffold 的 slot 声明把这两个（实测派生为 5 个 target row 中的 3 个）extension
类行**全部声明为 `['location']` slot**，与冻结拓扑不变量"**每 slot 唯一行**"
构成**不可满足契约**：Architect 无论产出 3 行（scaffold 行数不符）还是复用
location（duplicate slots）都被确定性拒收——12+ 候选三轮零合法。

## 2. 证据链（全部实物在盘）

| 证据 | 位置 | 内容 |
|---|---|---|
| 拓扑拒收明细 | `runs/c1_matrix_ED4_20260910T203019Z/topology_rejections.json` | attempt 1 三候选全拒 `duplicate slots: ['location']`；attempt 2 三候选全拒 `target rows must be ['name','contact','location','location','location'], got …`（行数不符） |
| 同模式复发 ① | `runs/c1_matrix_ED5_20260910T203053Z/`、`runs/c1_matrix_ED6_20260910T203210Z/` | `stop_reason: topology_rejected_after_bounded_retry`（各 2 次 Architect 调用、12 候选全拒） |
| 同模式复发 ②（跨 pair） | `runs/c1_matrix_DF_20260910T203255Z/` | **D→F** 同模式冻结（source=D, target=F）→ D 目标系统性（凡以 D 为目标或涉及该 header 结构的 pair 均撞同一契约） |
| body fit 冻结 | `runs/c1_matrix_ED3_20260910T202559Z/` | `C1 body-line x0 fit failed within its render budget`（ED2 修复后重跑，fit 未收敛） |
| ED2 门禁过但验收空转 | `runs/c1_matrix_ED2_20260910T202011Z/SECTION_X0_ACCEPTANCE.md` | 0 PASS 行 / 9 段 `_none_`——门禁全过但分层验收契约空转（不能计全绿；根因= D 种子模板用 B 时代 `<div class="section">` 词汇而非 `<section>` 标签，锚点按标签词汇选择器全部落空 → 已按预授权"词汇表→结构判定"修复，见 §3-⑤⑥） |
| F→E 对照（同一机制正例） | `runs/c1_matrix_FE2_20260910T200958Z/` | F→E 全绿（69 PASS 非空转）：目标 E 的 header 为 3 行常规设计（name/contact/location 各一），契约可满足 → 证明阻塞非管线全局性，而是 **D 目标 header 的多 extension 行设计超出 schema 表达力** |

## 3. 冻结前的已闭环项（与本阻塞无关，均已单测锁定，测试基线 121/121）

阶段四已按四分法处置并闭环的确定性缺口（全部为一般规则、零 pair 特判）：

1. **bullet 字形结构判定**（F→E 冻结暴露）：目标声明实测 bullet 设计 → 行首
   列表字形转 CSS marker（字面字形不存活）；目标零 bullet 设计 → 逐字保留
   （F 轮裁决按其自身前提结构化，与横杠规则同一谓词）。第三类。
2. **行级实测字体样式**（E→D 冻结①）：`_style_for` 按位置取样式在 D 语料上把
   tagline 字号给了 contact 行 → 行宽爆 → shrink-to-fit ×0.92 全页缩放连锁。
   改为行级实测样式。第三类。
3. **结构化联系行识别 + 元素级分隔符**（E→D 冻结②）：`@ in text` 启发式在
   redact 语料失效，改为结构化识别。
4. **字体同族头词匹配**（E→D 冻结③）：fail-closed 门禁匹配语义与 A 时代
   approved-substitution map 的语义冲突（"Charter BT" vs Charter-Roman），
   改为同族头词一般规则（Roboto→Arial 永不匹配）。
5. **section 词汇→结构判定**（ED2 验收空转根因）：D 种子模板 `<div class=
   "section">` → 结构化 section 谓词。预授权债务族（#3/#4/#11 先例）。
6. **零 bullet 压制 `!important` + 双字形重复门禁**（级联特异性 + CSS 字符
   marker 非 graphic mark 的检测盲区）。

## 4. 为什么这是四分法外的（越权边界）

§9.2 处置四分法第四类："语义/结构存在多个合法解释 → 不静默 normalize；优先
测量层吸收（旋钮），无法吸收时拒收/重新生成/交 owner 裁决"。本缺口三层均
不可在测量层吸收：

- **解释 A**：interests 条是 header 的第四个 slot（需要 schema 新增 slot kind，
  如 `interests`/`extension2`）——schema 表达力扩展 = 裁决边界 (e)，owner。
- **解释 B**：interests 条是渲染为 header 区域的 **section**（如 interests 节
  以条状呈现于页首），应从 header 拓扑排除——语义归属判定，属于第四类多义
  （两种呈现契约均合法），owner 择定。
- **解释 C**：放宽"每 slot 唯一行"不变量允许 extension 类 slot 多行——
  不变量重设计 = §9.2 明文"同族缺口再现时提请 owner 重设计不变量"。

叠加裁决边界 (d)：若 owner 选择架构分岔（确定性 compiler 全量生成拓扑、
废除 Architect 对 scaffold 语义判断的依赖），属架构层分岔，owner。

## 5. 收敛度量记分（截至阻塞点）

| pair | 冷启动冻结 | 新一般规则 | 判定 |
|---|---:|---:|---|
| D→E | 0（阶段三回归通过） | 0 | 方法成立 |
| E→F | 14（历史） | 12 + 9 | 收敛（owner 终审待矩阵终审） |
| F→E | 1 | 1（bullet 字形结构判定） | **方法成立（0-1 判据）** |
| E→D | 4+（ED1 门禁、ED3 fit、ED4-6 拓扑×2 同模式） | 6 | ⛏ 阻塞于 schema 边界 |
| D→F | 1（DF，同模式拓扑） | 0（同因） | ⛏ 阻塞于同一 schema 边界 |
| F→D | 0（未启动——同因预期冻结） | — | — |

四对冷启动合计新一般规则 = 1 + 6 = 7（≤8 止损线，但 D→F/F→D 若继续迭代
几乎必然触发 ≥8 —— 与 owner 预批准的止损含义一致：继续迭代前先裁决）。

## 6. 等 owner 什么 / 选项

**等**：对 D 目标 header 的 slot schema / 不变量取舍拍板（以下任一，或给出
其他设计）。

| 选项 | 内容 | 影响 |
|---|---|---|
| A | schema 新增 slot kind（interests / extension 多行槽），不变量允许该类 slot 多行 | 最小改动；Architect 语义判断仍可用；scaffold slot 声明需同步结构化（两 extension 行各归其 slot 的结构判据——tagline=文本行、interests=多行/条状，判据需 owner 认可） |
| B | interests 条划归 section（header 拓扑仅 name/contact/tagline） | 拓扑契约可满足；需 owner 确认该语义归属（呈现跟随目标）；连带验证 F→D/D→F |
| C | 架构分岔：compiler 从 scaffold 确定性生成 header 拓扑（废除 Architect 语义判断），slot 缺口按选项 A/B 先解决 | 消除单发方差源；工作量较大，需 ADR |
| D | 维持现状：E→D/D→F/F→D 标记"部分收敛/阻塞"，六对矩阵以 3 绿 3 阻塞交付 owner 终审 | 不改代码 |

## 7. 已核验的交付状态（阻塞前）

- 全绿 run（实物核验在盘）：D→E `200018Z`、E→F `195515Z`、F→E
  `c1_matrix_FE2_20260910T200958Z`；
- E→D：无绿轮（ED2 门禁过但验收空转，不计）；D→F：冻结；F→D：未启动；
- side-by-side 打包：`runs/C1_MATRIX_PACKAGE/`（3 绿对已打包；阻塞对如实标注）；
- 测试：121/121（基线 114 + 阶段四新增 7，orchestrator 亲跑复核）。
