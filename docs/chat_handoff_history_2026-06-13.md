# kc-m-a 项目聊天历史与交接文档

> 生成时间：2026-06-13  
> 正确仓库：`yt-feng/kc-m-a`  
> 说明：这是本聊天中关于 `kc-m-a` 项目的需求、问题、修复和待办事项整理。此前同名文档曾被误写到 `yt-feng/kc_c_wk`，本文件为补写到正确仓库的版本。

---

## 1. 项目总目标

本项目主要建设两条自动化链路：

1. **Weekly M&A cases：周度并购案例 Excel**
   - 每周抓取最近 1 周并购案例。
   - 中国案例为主，全球案例为辅。
   - 输出 Excel：`outputs/并购案例一览_YYYYMMDD_YYYYMMDD.xlsx`。
   - 使用 DeepSeek API 将新闻、公告、RSS 候选结构化为表格。
   - Excel A 列“案例分类”基于 10 大并购分类。

2. **Weekly M&A case reports：周度并购案例分析报告 Word**
   - 每周生成 4 篇并购案例分析报告。
   - 中国为主，每周至少 2 篇国内案例。
   - 输出 Word：`case_reports/` 下按 10 大并购分类存放。
   - 报告要具备分析深度、交易启示、并购方法论价值。
   - 支持 one-off preview 和 backfill。

---

## 2. 当前关键路径

### 2.1 Excel 周度追踪

- Workflow：`.github/workflows/weekly-mna.yml`
- Python 入口：`mna_weekly_tracker/main.py`
- Excel 写入：`mna_weekly_tracker/excel.py`
- DeepSeek 结构化：`mna_weekly_tracker/deepseek.py`
- 信息源编排：`mna_weekly_tracker/sources_rich.py`
- 抓取器：`mna_weekly_tracker/sources_fixed.py`
- 配置与分类：`mna_weekly_tracker/config.py`
- 输出目录：`outputs/`

### 2.2 Word 案例报告

- Workflow：`.github/workflows/weekly-mna-reports.yml`
- One-off 预览：`.github/workflows/one-off-mna-report-preview.yml`
- Python 入口：`mna_case_reports/main.py`
- 选题：`mna_case_reports/case_selection.py`
- 报告生成：`mna_case_reports/report_generation.py`
- 事实包：`mna_case_reports/fact_pack.py`
- 叙事计划：`mna_case_reports/narrative_generation.py`
- 质量校验：`mna_case_reports/article_quality.py`
- Word 写入：`mna_case_reports/docx_writer.py`
- Word 校验：`mna_case_reports/docx_validate.py`
- 输出目录：`case_reports/`
- 预览目录：`case_reports_preview/`

---

## 3. 当前定时任务

### 3.1 Weekly M&A cases（Excel）

北京时间每周五 05:00。

```yaml
- cron: "0 21 * * 4"
```

UTC 每周四 21:00 = 北京时间每周五 05:00。

### 3.2 Weekly M&A case reports（Word 报告）

也已改为北京时间每周五 05:00。

```yaml
- cron: "0 21 * * 4"
```

注意：调试期间 workflow 里曾临时加入 `push` 触发，后续稳定后可考虑删除，仅保留 `schedule` 和 `workflow_dispatch`。

---

## 4. 10 大并购分类

1. 整合一级资产+资本化
2. 依托上市平台持续整合同类资产
3. 上市公司控股权并购
4. 重组上市（借壳，含类借壳）
5. 破产重整
6. 跨境并购
7. 私有化+境内上市
8. SPAC
9. 上市公司+PE
10. 分拆上市

用户要求：报告生成时尽量保持 10 类文件数量均衡，避免每周都集中在同一类；SPAC 可少一些。

---

## 5. Excel 周度并购案例：需求与迭代

### 5.1 初始需求

- 每周自动整理最新全球并购案例。
- 中国为主，全球为辅。
- 输出 Excel。
- Excel 格式参考用户上传附件。
- A 列“案例分类”参考用户上传 Word《并购分类建议（含案例名称）》。
- 重点信息源来自用户截图中的中国信息源。
- 全球信息源参考 Google News。
- DeepSeek API 负责结构化。
- DeepSeek API key 已放入 repo Secret。
- GitHub Actions 自动执行并保存 Excel。

### 5.2 信息源增强

用户反馈中国并购 deal flow 太少，要求增强中国信息源，尤其是类似 Google News 的中文新闻源和微信生态。

已加入或讨论的信息源：

- 巨潮资讯网 CNINFO 公告 API
- 上交所公告/并购重组栏目
- 深交所公告/并购重组栏目
- 北交所公告
- 全国股转系统/新三板公告
- Bing News RSS 中文新闻
- Google News 中文新闻
- 搜狗微信搜索（best-effort，可能被限流）
- GDELT DOC API
- 港交所 HKEXnews
- 中东主权基金和产业资本海外并购信息源

### 5.3 搜狗与 GDELT 处理

- `sources_rich.py` 对 GDELT 做 fallback：若 GDELT 没结果则回退 Google/Bing News。
- `sources_rich.py` 对搜狗微信做 diagnostics：0 条时记录日志，说明可能被 provider 限速。

### 5.4 Excel 链接问题

用户上传 `并购案例一览_20260605_20260612.xlsx` 后反馈：

1. Excel 里的链接不能点击。
2. 后来又反馈链接不是原始链接，打不开。

已做两轮修复。

#### 第一轮：URL 列变成可点击 hyperlink

文件：`mna_weekly_tracker/excel.py`  
commit：`48b6ca17adf9d979e6122dbd803f6e75fd49fd30`

修复内容：

- `周度并购案例` sheet 的 `URL` 列变成真正 Excel hyperlink。
- `跟踪信息源` sheet 的 `URL` 列变成 hyperlink。
- `原始候选` sheet 的 `URL` 列变成 hyperlink。
- 多 URL 单元格保留完整文本，点击目标取第一个 URL。
- 链接显示为蓝色下划线。

#### 第二轮：尝试把 wrapper URL 解析成原始链接

文件：`mna_weekly_tracker/sources_fixed.py`  
commit：`f96fb42a480da636bc0deec0428e7eee16f8807f`

修复内容：

- 新增 `resolve_original_url()`。
- 对 Google News、Bing、Sogou 微信等 wrapper URL 做 best-effort 回源：
  - 先从 query 参数取 `url/u/q/target/to`。
  - 若不行，则请求 wrapper URL 并跟随 redirect。
  - 若 final URL 仍是 wrapper，则从 HTML 中找 canonical、og:url 或第一个非 wrapper 链接。
- `rss_items()` 对 RSS entry link 调用 `resolve_original_url()`。
- `fetch_sogou_weixin()` 对搜狗微信链接调用 `resolve_original_url()`。

### 5.5 当前 Excel 链接问题状态

用户最新仍反馈：“链接依然不对。不是原始链接，打不开”。

这说明还没有完全闭环。可能原因：

- Google News RSS 的链接是跳转页，GitHub Actions 环境无法完全解析。
- Bing/Sogou 返回二次跳转或需要 cookie。
- DeepSeek 在结构化时可能自己输出了 wrapper URL 或来源首页，而不是 raw item 原始 URL。

建议下一步：

1. 在结构化后增加 URL 后处理：如果 `cases.URL` 是 wrapper，则根据标题回查 raw_items 中的 resolved URL。
2. Excel 增加两列：`来源入口URL`、`原始文章URL`，避免混淆。
3. 新增 URL 校验脚本，检查 wrapper 域名和可达性。
4. DeepSeek prompt 中明确：URL 必须从候选 `url` 字段原样选择，不允许模型生成。

---

## 6. Word 并购案例报告：需求与迭代

### 6.1 初始报告需求

- 新增一个 action，最好新 folder，但与 Excel 项目相关。
- 每周生成 4 篇并购案例分析报告。
- 中国为主，每周至少 2 篇国内案例。
- 每篇约 3500–4000 中文字。
- 输出 Word，不要表格。
- 按 10 大并购分类存放。
- 先跑一次近 2 年 backfill；经典大案例不限时间。

### 6.2 写作风格要求

- 客观、克制、专业。
- 不要主观判断过度。
- 不要特别负面。
- 去除敏感政治、宏观经济等表述。
- 不给公司打广告。
- 有并购方法论启示。
- 不要写“本文 XXX”式开头。
- 不要把“面向上市公司 CEO”这类思考过程写进正文。

### 6.3 文章质量要求

- 不要模板化。
- 结构应由案例主线决定，可以 4–7 章，不固定 5 章。
- 模型应先形成“叙事主线/分析重心”。
- 标题要具体、有信息量、有专业吸引力。
- 必须包含交易双方、交易时间、交易对价、财务数据、交易状态。
- 必须说明买方为什么买、卖方或标的为什么接受。
- 结语必须紧扣本案例，不能泛泛而谈。

### 6.4 选题要求

- 优先 2025–2026 年已完成且成功案例。
- 经典、规模大或明星案例可不限时间。
- 不再写腾讯音乐/喜马拉雅案例。
- 禁止“未披露标的”“标的公司”“某标的”“部分资产”等目标不明确案例。
- 必须有明确并购方和明确目标方。

相关修复：

- `case_selection.py` 增加 `VAGUE_PARTY_TERMS`。
- 增加 `has_explicit_parties()`。
- 选题 prompt 禁止未披露标的。
- `rows_to_briefs()` 和 `dedupe_briefs()` 过滤模糊案例。

### 6.5 Word 格式要求

用户最终要求：

- 文档网格：`<w:docGrid w:type="lines" w:linePitch="312"/>`
- 全文首行缩进 2 字符。
- 段后 0 行，不是 10 磅。
- 全角中文标点。
- 引号必须用全角中文引号 `“”‘’`。
- 半角 `"` 和 `'` 必须硬失败。
- 中文与英文/数字之间不加空格。
- 数字金额使用千分位逗号。
- 主标题：黑体、小三、居中、单倍行距、无首行缩进。
- 章标题：仿宋、加粗、四号、左对齐、首行缩进 2 字符。
- 正文：仿宋、四号、首行缩进 2 字符。
- 全角引号 run 使用 Times New Roman。

### 6.6 三类信息区分要求已取消

用户曾要求严格区分：官方事实、媒体报道、合理推断。后来明确取消该要求。

已修改：

- `mna_case_reports/report_generation.py`  
  commit：`6fd5f6da2cfeccec29383c2ea7dbb904ee532700`
- `mna_case_reports/article_quality.py`  
  commit：`b5ace724daace0ed0121fe730a295bfb44652d42`

保留底线：事实、数字、信息必须基于给定资料线索和事实包，不能编造资料外事实。

---

## 7. 关键 bug 与修复

### 7.1 DeepSeek JSON malformed

问题：DeepSeek 返回 JSON 中有未转义引号，导致 `JSONDecodeError`。

后续方向：

- 进一步加强 JSON repair。
- 或改为逐字段生成，减少长 JSON 中引号冲突。

### 7.2 `case_selection.py` SyntaxError

原因：prompt 字符串中普通字符串与条件表达式拼接方式错误。

修复：改为 `prompt_parts` 列表拼接。

commit：`90fcec730f6d49234b822ee1024234d1366395da`

### 7.3 未披露标的案例入选

问题：生成了“五新隧装收购未披露标的”等目标不明案例。

修复：加入模糊目标过滤。

commit：`62040c55439e07c74eceb75f3499233ec3b1c2e9`

### 7.4 历史旧报告拖累新 run

问题：`docx_validate` 扫整个 `case_reports` 或 `case_reports_preview`，历史旧文件格式问题导致当前 run 失败。

修复：使用 marker 文件，只校验本次生成/覆盖的 `.docx`。

关键 commit：

- Weekly reports 当前 run 校验修复：`6b4dbfe29907cda1aa9b802c0d7f07c0af89579f`

### 7.5 DOCX 引号 run 字体/加粗问题

问题：全角引号用 Times New Roman 后，validator 误判；后来章标题里的全角引号又丢失加粗。

修复：

- validator 允许全角引号 run 使用 Times New Roman。
- writer 保留章标题全角引号加粗。

关键 commit：

- `6a7450e9e4d37d2ad951db3b25cfdf1dcb7987bf`
- `8dfa4807c1d0321157c98798cdd7565e330fe3bf`

---

## 8. 重要提交记录

| Commit | 内容 |
|---|---|
| `90fcec730f6d49234b822ee1024234d1366395da` | 修复 `case_selection.py` SyntaxError |
| `62040c55439e07c74eceb75f3499233ec3b1c2e9` | 过滤未披露/模糊标的 |
| `8f0209791c9e706d6fa010cb77bbc6050863c344` | DOCX 增加半角引号硬校验 |
| `513a480a310c17336fd6f9f388d582a4455649e5` | Reports 曾改为北京时间周一 06:00 |
| `9ecd8292fe604eb0b22df09dab94dfaed6618ddc` | 改进报告分类均衡 |
| `6a7450e9e4d37d2ad951db3b25cfdf1dcb7987bf` | 允许全角引号 Times New Roman |
| `6fd5f6da2cfeccec29383c2ea7dbb904ee532700` | 取消三类信息强制区分 prompt |
| `b5ace724daace0ed0121fe730a295bfb44652d42` | 取消媒体/推断相关质量硬拦截 |
| `8dfa4807c1d0321157c98798cdd7565e330fe3bf` | 修复章标题全角引号加粗丢失 |
| `9ac99f6bf505060cfa6608f86fd991b37fcf93f6` | Weekly reports 改为北京时间周五 05:00 |
| `6b4dbfe29907cda1aa9b802c0d7f07c0af89579f` | Weekly reports 只校验本次生成 DOCX |
| `48b6ca17adf9d979e6122dbd803f6e75fd49fd30` | Excel URL 列转为 hyperlink |
| `ef089b05d8165b2eb72ebf10aa4da67cb095f592` | 触发 Excel hyperlink 修复后的 workflow |
| `f96fb42a480da636bc0deec0428e7eee16f8807f` | 尝试解析新闻 wrapper URL 为原始链接 |

---

## 9. 用户沟通偏好

用户非常重视：

1. 不要偷懒。
2. 不要只做表面修复。
3. 出错要明确承认原因。
4. 不要声称“已完成”但实际没验证。
5. 每次修复要说明：文件、commit、解决的问题、是否已验证。
6. 用户说“跑一下”时，应真的触发 workflow，而不是只说可以跑。
7. 用户希望长期保留架构/交接文档，方便新的 AI 或开发者接手。

---

## 10. 当前最重要待办

### 10.1 继续修 Excel 原始链接

当前仍需彻底解决：Excel 中 URL 不是原始链接、打不开。

建议方案：

1. DeepSeek 结构化时禁止自行生成 URL，只能从候选 `url` 字段选。
2. 结构化后对 `cases.URL` 做后处理：若是 wrapper 或不可达，则回查 raw_items。
3. 增加 `来源入口URL` 和 `原始文章URL` 两列。
4. 增加 URL 校验 manifest，列出 wrapper、不可达、解析失败的 URL。

### 10.2 重跑 Weekly M&A cases

在 URL 修复后，需要重跑 `Weekly M&A cases`，覆盖之前 Excel。

### 10.3 清理 workflow 调试触发

调试期间加过 `push` 触发，稳定后建议删除。

---

## 11. 最后状态

- 正确仓库：`yt-feng/kc-m-a`
- Excel 定时：北京时间每周五 05:00
- Word reports 定时：北京时间每周五 05:00
- Word 报告格式与选题规则已多轮修复
- Excel hyperlink 已可点击，但“原始链接可打开”仍未彻底确认
- 用户最新关注点：继续修 Excel URL，并重跑覆盖旧 Excel

---

## 12. 2026-06-14更新：当前闭环状态

- Excel原始链接问题已继续修复：Google/Bing/搜狗包装链接、来源首页链接会在采集、DeepSeek结构化、Excel写入和报告选题读取阶段被过滤。
- `Weekly M&A cases`已在GitHub Actions重跑并提交新Excel：`outputs/并购案例一览_20260606_20260613.xlsx`。
- 已验证新Excel业务表（`周度并购案例`、`原始候选`）共686个URL/超链接目标：Google/Bing/搜狗包装链接为0，首页型链接为0，20个案例URL均非空。
- 新增Excel链接防回归校验：后续周度Action会检查最新Excel业务表，若出现不可用URL或案例URL为空则失败。
- `Weekly M&A case reports`的失败点已从坏链接转为选题预检过严：A股官方PDF案例的交易金额在PDF正文中，Excel结构化字段为`-`，原逻辑在研究抽取前即拒绝。已调整为官方PDF/交易所公告来源可进入研究阶段，后续事实包仍会严格校验金额、动机、数据和权威来源，不写模板兜底段落。
- 同时继续过滤“旗下资产”“金融资产”“待定受让方”等伪明确主体；周报可尝试本周官方披露、交易双方明确的进行中案例，但不会绕过事实包和DOCX质量校验。
- 当`周度并购案例`sheet没有合格报告候选时，报告Action会改用同一Excel的`原始候选`sheet重新调用DeepSeek筛选报告题目，而不是回退到静态旧案例或模板段落。
- 如果最新一期材料本身没有可写报告候选，报告Action会回看最近几期周度Excel，并继续使用历史报告去重；这用于解决“本周都是停牌/提示/弱披露”的情况，仍不启用静态旧案例池。

---

## 13. 2026-06-14更新：报告Action失败后的继续修复

- `Weekly M&A case reports`在run `27473616238`中失败，直接原因是只尝试了1个国内候选：苏州逐越鸿智要约收购嘉美包装。事实包未从PDF中抽出`4.45元/股`、`215,206,172股`、`45.01%`等交易条款后，流程因`REPORT_MIN_DOMESTIC=1`跳过海外候选并结束。
- 已调整报告候选排序：当本次要求产出国内稿时，候选池会保留多个国内官方披露备选；排序优先级改为“是否可写/是否有可用来源”优先，类别均衡只在资料质量接近时生效，避免一个国内候选失败后整轮停止。
- 已增强事实包抽取：从PDF/网页抽取文本中确定性识别要约价格、每股价格、股份数量、股权比例、现金/股份支付、预受要约和过户结果，减少DeepSeek漏抽关键交易条款导致的失败。
- 卖方或被整合方动机不再被硬逼成编造内容：若公告没有披露主观原因，事实包和正文提示会明确要求写“公开资料未单独披露”，并只基于现金要约、预受要约、协议转让、控制权安排等已披露条款分析客观接受机制。
- 已修复正文规范中的千分位逗号问题：数字规范会把`215206172股`格式化为`215,206,172股`，并保留英文逗号，不再误改成`215，206，172股`。
- 已通过本地编译和关键函数测试：`python3 -m compileall mna_case_reports mna_weekly_tracker`通过；候选选择测试显示`REPORT_COUNT=1`、`REPORT_MIN_DOMESTIC=1`场景下可得到7个国内且通过preflight的官方PDF备选。
