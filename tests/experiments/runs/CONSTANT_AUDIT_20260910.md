# C1 常数审计（测量/派生/拟合层数值常量，只报告不修改）

生成：2026-09-10 · 范围：c_pipeline.py 测量/派生/拟合层 · 分类：a=目标实测派生 / b=结构必然 / c=D→E 调参残留

| 位置 | 常数 | 值 | 分类 | 说明 |
|---|---|---|---|---|
| _pdf_lines_and_marks | 行聚类容差 1.5pt（top 差 ≤1.5 合并为一行） | 1.5 | c | 行分组阈值；E/F 标题行距下均工作，但为调参值 |
| _pdf_lines_and_marks | 规则线判定：宽度 > 页宽×0.5 且高 ≤2.5pt | 0.5 / 2.5 | b | 『横贯线』的结构定义；0.5 为半页的结构性门槛，2.5 为线/块分界 |
| _pdf_lines_and_marks | 行首 glyph 前延伸：纵向重叠 ±2pt、x1 ≤ 首词 x0+2 | 2 / 2 | c | 为 D→E 的 ⌣ 前缀调参；F 无此类 glyph（未触发） |
| _pdf_lines_and_marks | marker 尺寸窗口 1–8pt（两维） | 1 / 8 | a | 2026-09-10 裁定：覆盖 D→E 5pt 与 F 2.82pt 实测 marker |
| derive_body_tier_targets | BULLET_GLYPHS 字形集合（•◦·▪‣●∙） | 集合 | b | 结构定义：何为字形 bullet |
| derive_body_tier_targets | bullet_dot = 字形行 min x0；bullet_text = 其后词 min x0 | 实测 | a | 全部从目标实测派生 |
| derive_body_tier_targets | l1 = min(entry x0)（来自 summary elements） | 实测 | a | 目标实测派生 |
| derive_header_scaffold | 居中判定 |中心−页宽/2| ≤12pt；左对齐 x0 ≤ 页宽×0.15 | 12 / 0.15 | c | D→E 调参（header 链冻结，未受 E→F 影响） |
| derive_header_scaffold | header 行取样上限 max_lines=20 | 20 | c | header 区域启发式上限 |
| derive_body_scaffold | 规则线归属窗口：heading.top−20 ≤ rule.top < heading.top | 20 | c | 为 D→E 的 6.09pt 间距调参；F 无规则线（未触发） |
| derive_body_scaffold | 标题行匹配 |line.x0 − element.x0| ≤2pt | 2 | c | 文本匹配容差 |
| derive_body_scaffold | entry 右缘 = body 行 max x1 | 实测 | a | 目标实测派生 |
| fit_geometry (header) | 容差 1.0pt；偏移 clamp −36..144 / −36..72；探针词数 1/3-5 | 1.0 等 | a/c | 1.0=owner 验收判据(a)；clamp 与探针词数=c 调参 |
| fit_body_line_geometry | 容差 1.0pt；渲染预算 10/12；无响应阈值 0.1；旋钮 clamp −36..144 | 见左 | a/c | 1.0=验收判据(a)；预算/0.1/clamp=c 调参（预算由裁定批准） |
| fit_body_line_geometry | 行级旋钮 clamp −72..144；离群判定 |err−median|>容差 | 见左 | b/a | 离群定义=结构必然（中位数残差）；clamp=c |
| measure_body_lines | dot 候选：mark.x1 ≤ line.x0+2 且垂直重叠 ±1pt | 2 / 1 | c | 为 D→E 圆点紧贴行首调参；F 走行内字形分支 |
| measure_body_lines | heading 规则线窗口 20pt | 20 | c | 同 derive_body_scaffold |
| inherit_container_source_line | 拼接空白不敏感（casefold+空白折叠） | — | b | 结构必然：与 provenance 语义一致，无数值 |
| apply_section_indents / knobs | CSS 值 .3f 格式化 | — | b | 渲染精度，结构必然 |

## c 类（D→E 调参残留）— 下轮修复候选，等批准

- _pdf_lines_and_marks：行聚类容差 1.5pt（top 差 ≤1.5 合并为一行）（1.5）
- _pdf_lines_and_marks：行首 glyph 前延伸：纵向重叠 ±2pt、x1 ≤ 首词 x0+2（2 / 2）
- derive_header_scaffold：居中判定 |中心−页宽/2| ≤12pt；左对齐 x0 ≤ 页宽×0.15（12 / 0.15）
- derive_header_scaffold：header 行取样上限 max_lines=20（20）
- derive_body_scaffold：规则线归属窗口：heading.top−20 ≤ rule.top < heading.top（20）
- derive_body_scaffold：标题行匹配 |line.x0 − element.x0| ≤2pt（2）
- measure_body_lines：dot 候选：mark.x1 ≤ line.x0+2 且垂直重叠 ±1pt（2 / 1）
- measure_body_lines：heading 规则线窗口 20pt（20）

说明：c 类本轮**均未在 E→F 路径上造成失败**（触发点未被走到或已被结构化修复绕开）；
列为下轮修复候选：从目标实测派生或结构化定义，等 owner 批准后统一实施。

