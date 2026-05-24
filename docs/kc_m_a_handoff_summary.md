# kc-m-a 项目交接总结：并购 Deal Flow Excel 与 Weekly M&A Case Reports

更新时间：2026-05-24
仓库：`yt-feng/kc-m-a`

## 1. 项目目标

项目有两条主线：

1. **Weekly M&A cases**：每周自动生成全球并购案例 Excel，中国为主、全球为辅。
2. **Weekly M&A case reports**：每周自动生成并购案例分析 Word，面向上市公司董事长/CEO，强调并购启示、交易结构、避坑和投后整合。

用户已将 DeepSeek API key 放在 GitHub repo Secret 中。DeepSeek API 本身不联网，因此所有事实、数据、公告、PDF、新闻片段都必须在调用 DeepSeek 前由代码抓取并传入 prompt。

## 2. Weekly M&A cases：Excel 任务

### 相关文件

```text
mna_weekly_tracker/
  config.py
  sources.py
  sources_fixed.py
  sources_rich.py
  deepseek.py
  excel.py
  main.py

.github/workflows/weekly-mna.yml
outputs/
```

### 已实现

- 每周五北京时间 05:00 自动运行。
- CLI：`python -m mna_weekly_tracker.main --days "$DAYS" --output-dir outputs`
- 输出：`outputs/并购案例一览_YYYYMMDD_YYYYMMDD.xlsx`
- 默认最近 7 天。
- `MAX_RAW_ITEMS=450`
- `MAX_STRUCTURED_CASES=120`
- DeepSeek 将 raw candidates 结构化成 Excel 行。

### 中国信息源扩充

已加入或尝试加入：巨潮资讯网 CNINFO API、上交所、深交所、北交所、新三板、Google News RSS、Bing News / Bing fallback、搜狗微信、GDELT DOC、SAMR/CSRC/发改委/商务部/外汇局监管关键词、港股/中概股披露、债券市场与非上市公司披露、国资产权交易和增资挂牌、司法/破产/资产处置、财经媒体和专业资讯。

### 已修复问题

1. **Bing RSS parse failed**：Bing 中文 RSS 经常返回 HTML 或空内容，不是 XML。已通过 `sources_fixed.py` / `sources_rich.py` 做 fallback，并降低噪音。
2. **source summary 日志**：增加每个来源的 `Source summary: kind=... count=... name=...`。
3. **GitHub push rejected**：Excel action 曾报 `main -> main (fetch first)`。已在 `.github/workflows/weekly-mna.yml` 增加 `concurrency`、`fetch-depth: 0`、retry-safe commit/push。

### 当前状态

Excel 任务相对稳定。最近日志曾显示：`raw_before_cap=931 raw_after_cap=450`、`Collected 450 raw candidates`、`Structured 53 cases`。

## 3. Weekly M&A case reports：Word 报告任务

### 相关文件

```text
mna_case_reports/
  __init__.py
  config.py
  case_pool.py
  case_selection.py
  deepseek_client.py
  docx_writer.py
  main.py
  report_generation.py
  research.py

.github/workflows/weekly-mna-reports.yml
case_reports/
```

### 目标

- 每周五北京时间 06:00 自动生成 Word 报告。
- 默认 weekly：每周 4 篇，至少 2 篇中国/境内/港股/中概股案例。
- backfill：用于首次回溯；用户曾运行 `mode=backfill, count=40, min_domestic=25`。

### 输出目录

按十大分类存放：

```text
case_reports/
  01_整合一级资产_资本化
  02_上市平台持续整合
  03_上市公司控股权并购
  04_重组上市_借壳_类借壳
  05_破产重整
  06_跨境并购
  07_私有化_境内上市
  08_SPAC
  09_上市公司_PE
  10_分拆上市
```

## 4. 用户对 Word 报告的最新硬性要求

### 4.1 排版

全篇 Word 格式应为：

- 首行缩进：**2 字符**，不是 0.99cm。
- 段前：0 行 / 0 磅。
- 段后：0 行 / 0 磅，不是 10 磅。
- 引号 `“”`、`‘’`、英文引号显示为 Times New Roman。
- 用户说“全篇”，因此章节标题和正文都要检查。

已修改文件：`mna_case_reports/docx_writer.py`。

当前尝试使用 OOXML：

```python
w:firstLineChars = "200"
w:before = "0"
w:after = "0"
w:beforeLines = "0"
w:afterLines = "0"
```

并增加了 `enforce_document_format(doc)`，保存前遍历段落强制设置。

**待验证**：需下载生成的 docx，用 Word 打开并检查段落设置；同时解压 docx 检查 `word/document.xml` / `word/styles.xml` 是否仍存在 `w:firstLine` 或段后 spacing。

### 4.2 内容

每篇必须：

1. **全篇基于事实，客观陈述，不要任何假设**。禁止：假设、推测、猜测、可能是、或许、大概、预计将、有望、如果、若未、若能、可能会、不排除等。资料不足时写“公开资料未披露”，不要补想象内容。
2. **必须涵盖**：
   - 并购具体日期：公告/签约日期，交割/过户/完成合并日期。
   - 交易金额、估值、支付方式、股权比例。
   - 并购方基本介绍。
   - 标的方基本介绍。
   - 并购方为什么买。
   - 标的方/出售方为什么卖。
   - 财务和经营数据：收入、净利润、负债、现金流、市值、产能、订单、用户、员工、资源量、储量等。
3. **结语章节编号必须按实际顺序**，不是固定“五”。如果全文 5 章写 `五、结语：副标题`；如果 6 章写 `六、结语：副标题`；如果 7 章写 `七、结语：副标题`。
4. **字数**：成品中文字数必须大于 3500、小于 4000。
5. **标题**：主副标题形式，必须包含交易双方名称或简称，最好不超过 30 个中文字符。

## 5. 已做过的 Word 报告迭代

### 5.1 写作风格

用户上传的参考案例包括 AMD/ZT Systems、Intel/Altera、中际装备/苏州旭创、OpenAI 连环并购等。归纳风格：不要公告复述；面向董事长/CEO；用交易数据开场；分析交易结构、动机、估值、付款、交割、投后承接；给可操作启示；客观中性，不广告化，不负面化，不写宏观敏感表达。

### 5.2 DeepSeek 不联网的补充

新增 `mna_case_reports/research.py`，在调用 DeepSeek 前先抓取事实原料：Bing Web search、Google News、Bing News、HTML 页面抓取、PDF 抓取和解析、`pypdf` 提取 PDF 文本、按数字和关键词打分抽取交易金额/估值/日期/收入/净利润等句子。已加入依赖 `pypdf>=5.0.0`。

### 5.3 扩展案例池

新增 `mna_case_reports/case_pool.py`。用途：backfill 大批量生成时，实时新闻候选不足就用扩展池补齐。

**重要待改**：很多扩展池条目没有完整字段：`acquirer`、`target`、`deal_value`、`deal_status`、`buyer_motivation`、`seller_motivation`、`financial_highlights`。这会导致 DeepSeek 原料不足，校验时反复提示“缺少买方/卖方交易动机”。

### 5.4 JSON 修复

修改 `mna_case_reports/deepseek_client.py`。历史问题：长文 JSON 经常报 `JSONDecodeError: Expecting ',' delimiter`；曾把中文引号 `“银泰系”` 转成 ASCII 引号，反而破坏 JSON。当前修复：保留中文引号；先尝试 raw JSON；再尝试本地 repair；再用 DeepSeek repair JSON。

**仍建议下一步改架构**：不要让模型输出长篇嵌套 JSON。更稳的是让模型输出 Markdown/plain text，再由 Python 按标题分段写 docx；或只输出 `{title, body}` 简单 JSON。

## 6. 当前最新失败与原因判断

### 6.1 weekly 跑完 18 分钟，但用户没看到新报告

可能原因：

1. 生成文件名来自标题，可能覆盖旧文件。
2. 如果覆盖后内容没有明显变化，`git diff` 可能为空，commit step 不提交。
3. 输出在已有分类文件夹，用户不容易发现。
4. 可能选题重复，生成了已有案例。

已进一步修复：`docx_writer.write_docx` 支持 `run_label`，`main.py` 会把 `run_label` 加入文件名前缀，避免覆盖旧报告。

### 6.2 backfill 跑 1.5 小时后被 cancel

截图显示：`cancelled in 1h 30m 14s`、`Error: The operation was canceled.`。这基本是 workflow 超时，不是用户手动取消。旧 workflow 中 `timeout-minutes: 90`。

核心原因：

1. `count=40` 太大。
2. 每篇要搜索资料、提取 PDF/HTML、调用 DeepSeek、最多 4 轮修订。
3. validation 太严格，尤其“卖方动机”对国企合并、吸收合并类交易不一定适用。
4. 超时发生在生成阶段，尚未进入 commit step，因此已生成的 docx 也没有提交，用户看不到任何 output。

已进一步修复：

- `.github/workflows/weekly-mna-reports.yml` timeout 从 90 提高到 180。
- 增加 `offset` 输入，支持 backfill 分批。
- 增加 `Upload generated report artifact`，即使失败/取消也尽量保留已生成文件。
- commit step 设为 `if: always()`，尽量提交部分成果。
- `main.py` 增加 per-report try/except，单篇失败不会拖垮整批。
- `main.py` 每篇完成或失败后写 `_progress.json`。
- 文件名增加 `run_label` 前缀，weekly/backfill 都不会覆盖旧文件。

## 7. 最紧急的后续修复建议

### P0：backfill 分批运行

不要直接跑 `count=40`。建议：

```text
mode = backfill
count = 5 或 8
offset = 0, 5, 10, 15 ...
min_domestic = 3 或按批调整
```

### P1：减少无效重写

当前 `report_generation.py` 仍最多 4 轮修订，耗时大。建议加入环境变量 `REPORT_MAX_REVISIONS`，backfill 默认 1 轮，weekly 默认 2 轮。若仍有 issues：写 warning 到 manifest，仍输出 docx，不要整批卡住。

### P2：校验规则按交易类型调整

“卖方为什么愿意卖”不适合所有案例。建议改成更通用：`标的方/出售方/被整合方接受交易安排的原因`。

不同类型分别处理：控股权收购、吸收合并/集团合并、破产重整、SPAC、分拆上市。

### P3：降低 JSON 风险

建议改为：

```json
{
  "title": "...",
  "body_markdown": "..."
}
```

或直接让模型输出 Markdown，再用 Python 转 Word。不要输出 `sections: [{paragraphs: [...]}]` 这种长篇嵌套 JSON。

## 8. 当前应优先修改的文件

1. `.github/workflows/weekly-mna-reports.yml`
2. `mna_case_reports/main.py`
3. `mna_case_reports/report_generation.py`
4. `mna_case_reports/docx_writer.py`
5. `mna_case_reports/case_pool.py`

## 9. 常用命令

### weekly

```bash
python -m mna_case_reports.main --mode weekly --count 4 --min-domestic 2 --days 7 --output-root case_reports
```

### backfill，当前建议小批量

```bash
python -m mna_case_reports.main --mode backfill --count 5 --min-domestic 3 --offset 0 --output-root case_reports
```

### 不建议直接再跑大批量

```text
mode = backfill
count = 40
min_domestic = 25
```

## 10. 总结

Excel 任务基本稳定，Word 报告任务仍需要工程化收敛。当前不是单纯 prompt 问题，而是：DeepSeek 不联网，必须给足事实原料；长篇 JSON 输出不稳定；backfill=40 超过旧 90 分钟 timeout；validation 过严导致反复重写；已生成文件在超时前未 commit/upload，用户看不到 output；weekly 可能覆盖旧文件或无 diff；Word 格式需要真实 docx 验证。

下一步优先：部分提交 / artifact 上传 / 日期化输出路径 / 小批量 backfill；再细化报告质量和排版验证。
