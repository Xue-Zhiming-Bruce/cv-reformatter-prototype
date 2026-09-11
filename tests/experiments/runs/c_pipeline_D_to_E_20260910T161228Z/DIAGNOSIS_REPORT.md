# C1 泛化 E→F · 第十一轮诊断报告（边框探针 + 静态 CSS 提取 + 算术分解）

Run（诊断对象）：`c_pipeline_D_to_E_20260910T161228Z` probe_03（含全部已批准
修复的填充产物）。诊断渲染：2 次（border 探针 + padding 对照），零管线调用，
零代码改动。

## a) 嵌套链每层实测盒（边框探针，Experience 节 'Microsoft' 条目，pt）

| 层 | x0 | x1 | width | 备注 |
|---|---:|---:|---:|---|
| section（含右缘修正 margin-right） | 36.36 | 565.88 | **529.52** | = 540 − 10.14（#9 右缘修正）✓ |
| .entry-container | 47.15 | 565.16 | **518.01** | = 529.52 − margin-left 10.8 − section padding 0.65 ✓ |
| .entry | 58.66 | 564.44 | **505.78** | = 518.01 − padding-left 10.8（编译缩进规则）✓ |
| .entry-left | 59.38 | 500.41 | 441.03 | flex-grow 后 |
| .entry-right（日期列） | 501.13 | **563.72** | **62.59** | ← 被压缩的日期列 |
| .entry-title-row / .entry-subtitle-row | 60.10 | 563.00 | — | 左右两列各持有一份（Filler 按列复刻行结构） |
| 文本实测（'Redmond, WA'） | 513.0 | **573.77** | 60.7 | **溢出 .entry-right 盒右缘 +10.05pt** |

## b) margin-left / padding-left 的实际生效位置与量级

- `margin-left: 10.8pt`（F 种子模板）：**生效**于 .entry-container 左缘
  （47.15 = 36.36 + 0.65 section padding + 10.14 ✓ 逐项吻合）。
- `padding-left: var(--c1-section-indent)`（C1 编译规则）：**生效**于同一
  .entry-container 的内容盒（.entry 505.78 = 518.01 − 10.8 ✓）。
- 左侧 fit 旋钮已把 var 收敛至 ≈0：左缘 46.19 vs 目标 46.8（Δ0.61 ≤1pt ✓）。
  **左侧无双重计入残留**——左缘由"模板 margin + 编译 padding(≈0)"构成，
  已被左 fit 闭环吸收。

## c) 行宽 534.0 的构成分解（vs F 设计 523.8，差 +10.2 逐项归因）

```
渲染行右缘 580.2 = .entry-right 盒右缘 563.72 + 文本溢出 10.05 + 测量含
被裁剪字形（'Present'→'Pre' 可见部分至 580.2，Chrome 于盒后 ~17pt 处裁剪）
```

**+10.2pt 的确切来源：日期列（.entry-right）被 flex 收缩压缩至其 nowrap
内容宽度之下 10.05pt，nowrap 文本向右溢出盒外 10.05pt，越出内容边界后被
Chrome 裁剪。** 逐项：F 设计行宽 523.8 中日期列应在 ≈70.6pt；渲染中日期列
被压至 62.59（−10.05）；文本不折行 → 溢出即行宽差。

## 任务 2：width/box-sizing 澄清

entry 链上（body/section/.entry-container/.entry/.entry-left/.entry-right/
title-row/subtitle-row）**没有任何 width/box-sizing/min-width/max-width
声明**（全量静态提取，逐条列出）。全局仅有 `*{box-sizing:border-box}`
（不影响本结论）。先前"未导出的宽度约束"假设**排除**。

## 机制定位（完成）

- F 模板的 `.entry-right` **未声明 `flex: 0 0 auto`**（依赖默认
  `flex-shrink: 1`，可收缩）；D→E 模板显式 `flex: 0 0 auto`。E 内容的
  长日期（'April 2019 – Present'，~95pt）触发收缩，nowrap 文本溢出盒右缘
  ~10pt → 越界 → 裁剪 → 逐字覆盖失败。F 自身内容（'Jan2023–Present'
  无空格、更短）不触发——数据巧合掩盖，与历次缺口同模式。
- **定位结论：compiler 规则层**（日期列缺少"不收缩"的通用保护）——与已
  批准的"日期不折行不截断"裁决（第三轮）同族：编译器应显式声明日期列
  flex-shrink:0（或 min-width:max-content）。

## 按裁决第 3 条处置

定位在 **compiler 规则层** → 按"一般规则修复的工单报批"：

**修复工单（待批）**：compiler 的 `_body_css` 在持有 body 几何时对右对齐
日期列统一输出 `flex-shrink:0`（或等效 min-width 保护），一般规则、无
pair 特判；D→E 行为不变（其模板已显式 flex:0 0 auto，输出为冗余但无害）；
F 输出日期列不再被压缩 → 文本完整、右缘入界。配套单测：日期列
flex-shrink 断言 + 长日期用例。

## 状态

- 本轮零代码改动；测试基线 92/92；D→E 不受影响。
- 缺口清单：#9/#10 的根因即本机制（日期列 flex 收缩），标记为"已定位，
  修复工单待批"。
