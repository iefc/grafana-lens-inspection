# Design System: Grafana Lens 巡检报告

单文件本地 HTML，给值班 SRE 先看评分与风险，再按看板分层核对，最后带走行动。禁止外链字体与外链 CSS。四段信息架构：巡检概览 → 风险识别 → 巡检详情 → 行动建议。

## 1. Visual Theme & Atmosphere

浅色机柜工作台，不是营销落地页，也不是暗色霓虹大盘。密度 6，版式 5（页眉不装盒、概览左环右表、总结独占一行），动效 3。

气质像交班单贴在 KVM 旁边：左边一条风险脊，环境评分是钢圈仪表，分层详情用表格而不是票根瀑布。

## 2. Color Palette & Roles

- **Bay Floor** (#E4EAF1) — 页面底
- **Panel White** (#FBFCFF) — 概览板、图表、影响分析
- **Charcoal Ink** (#1A2332) — 主文字、打印按钮。禁止 `#000000`
- **Quiet Slate** (#5B6B7C) — 元数据、章节号、图例
- **Hairline** (#D7DEE7) — 1px 结构线、KPI 网格缝
- **Ink CTA** (#1A2332) — 唯一品牌强调
- **High** (#B42318) / **Medium** (#C2410C) / **Low** (#A16207) / **Clear** (#0F766E) — 风险与评分语义色
- **Bezel** (#1B2433) — 截图显示器框

## 3. Typography Rules

离线，不加载 Google Fonts。Inter 禁用。衬线禁用。

- **Display / 标题：** `ui-sans-serif, system-ui, "PingFang SC", "Source Han Sans SC", "Noto Sans SC", sans-serif` — 字重 650，字距 -0.04em
- **Body：** 同上，行高 1.55，总结不超过约 65ch
- **Mono：** `ui-monospace, "SF Mono", "JetBrains Mono", Menlo, monospace` — 评分、panel id、日期、成功率。数字 `tabular-nums`

## 4. Hero / 页眉

页眉直接坐在底色上。标题左对齐。主 CTA 只有「打印 / 导出 PDF」。

签名元件是左侧钢圈环境评分，右侧 KPI 网格（检查项 / 高 / 中 / 低 / 日期 / 成功率 / 耗时）。禁止等分三卡片，禁止居中营销 Hero。巡检总结独占下一整行。

## 5. Component Stylings

- **Buttons：** 墨色填充，圆角 10px，按下 `translateY(1px)`，高度 ≥44px
- **Gauge：** SVG 圆环，缺省显示「— / 待综述」，禁止编造分数
- **Charts：** 内联 SVG。第二节第一行双饼，分层数量用竖向堆叠柱（高/中/低自底向上），层名 -42° 倾斜以免占高；严重度与覆盖仍用横条。图例带文字。禁止编造指标。
- **Risk cards：** 多列网格（宽屏约 3、平板 2、手机 1）。等级「高/中/低」，状态中文。禁止一行一条。
- **Tables：** 行 hover 浅底。风险等级与状态用中文，不只靠颜色
- **Impact：** 每层表格下方一块影响分析；无风险则写明无风险
- **Actions：** 按必须修复 / 优先跟进 / 改进措施分组，组内多列卡片
- **Empty：** 「本轮未识别到告警或异常风险」「尚未生成巡检总结」

## 6. Layout Principles

- 最大宽度 1180px
- 概览：140px 评分 + 4 列 KPI 网格；&lt;768px 单列，KPI 2×N
- 风险：第一行双饼，分层竖柱通栏（固定约 240px 高），其下严重度/覆盖，再风险卡片网格；&lt;768px 全部单列
- 行动建议：优先级分组 + 卡片网格，不要一行一条
- 标题层级：`h2` 章节（约 1.4rem、主色）> `h3` 子块 > `h4` 卡片标题。禁止章节号做成 12px 大写弱标题
- 详情按 `rowTitle` 分层，一层一块
- 整页左边脊 = 本轮最高风险色
- 附录 JSON 默认折叠

## 7. Motion & Interaction

按钮按下、details 开合。≤180ms，只改 transform/opacity。禁止循环 shimmer。尊重 `prefers-reduced-motion`。

## 8. Anti-Patterns (Banned)

- 表情、Inter、衬线、外链字体、外链 Chart.js
- 纯黑、霓虹、紫蓝渐变
- 等分三卡片、居中营销 Hero
- 编造环境评分、成功率、耗时或综述；成功率与耗时只来自 run 时间戳与完成数
- `LABEL // YEAR`、假指标卡、「Scroll to explore」
