# kc-m-a 架构文档：Excel Deal Flow 与 Weekly M&A Case Reports

更新时间：2026-08-01

## 1. 总体判断

项目现在有两条自动化 pipeline：

1. **Weekly M&A cases Excel**：每周生成并购案例一览 Excel，用于 deal flow 扫描、候选留痕和后续选题。
2. **Weekly M&A Case Reports Word**：每周生成并购案例分析 Word，用于深度案例复盘。

两条 pipeline 共享一个核心前提：LLM 不联网，所有公告、新闻、PDF、监管文件和搜索结果都必须由代码先抓取、筛选、压缩，再传入模型。Excel 结构化和轻量事实处理优先使用快模型；Word 长文正文、扩写和修订属于复杂任务，单独路由到强模型。

## 2. Excel deal flow pipeline

### 2.1 执行入口

```text
.github/workflows/weekly-mna.yml
  -> python -m mna_weekly_tracker.main --days "$DAYS" --output-dir outputs
  -> outputs/并购案例一览_YYYYMMDD_YYYYMMDD.xlsx
```

默认参数：

- 北京时间每周五 05:00 自动运行。
- 最近 7 天窗口。
- `MAX_RAW_ITEMS=450`。
- `MAX_STRUCTURED_CASES=120`。
- 输出 workbook 包含：`周度并购案例`、`运行摘要`、`跟踪信息源`、`原始候选`。

### 2.2 模块职责

```text
mna_weekly_tracker/config.py
  -> sources_fixed.py / sources_rich.py
  -> deepseek.py
  -> excel.py
  -> main.py
```

- `config.py`：统一维护输出列、十大案例分类、数据源配置、关键词和专题查询。
- `sources_fixed.py`：底层采集器，负责 CNINFO、Google News RSS、Bing News RSS、Sogou Weixin、GDELT DOC 等请求、解析、去重和候选排序。
- `sources_rich.py`：生产入口采集编排层，提供 source summary 日志、GDELT fallback 到新闻搜索、Sogou 诊断和总候选 cap。
- Weekly Case Reports 另有 Tavily 搜索补充，用于发现已完成交易的公告、监管文件或公司新闻稿；Tavily 只做发现层，最终事实仍需回到原始公告、监管文件或公司新闻稿核验。
- `deepseek.py`：将 raw candidates 结构化为 Excel 行；默认要求 `DEEPSEEK_API_KEY` 存在且调用成功。
- `excel.py`：写 workbook，并把所有跟踪源和关键词写入 `跟踪信息源` sheet。
- `main.py`：CLI orchestration，负责日期窗口、采集、结构化、写文件。

### 2.3 信息源体系

信息源由三组配置组成：

- `TRACKED_FETCH_SOURCES`：代码会自动采集的来源。
- `ALL_TRACKED_SOURCES`：Excel `跟踪信息源` sheet 展示的完整来源，包括自动采集源和手工核验源。
- `MIDDLE_EAST_REFERENCE_SOURCES`：商业数据库、年度报告、新闻站点等手工核验源，不直接自动抓取，但会进入 Excel 信息源页。

中国侧已覆盖：

- 巨潮资讯 CNINFO API。
- 上交所、深交所、北交所、全国股转系统。
- SAMR/CSRC/发改委/商务部/外汇局监管关键词。
- 港股/中概股官方披露补充。
- 债券与非上市公司披露、国资产权交易、司法/破产资产处置、财经媒体和专业资讯。

### 2.4 中东资本出海并购扩展

2026-06-03 参考 `docs/中东收购海外企业信息源清单.xlsx` 增加中东收购海外企业的信息源。

自动采集源包括：

- 中东官方源：PIF、Mubadala、QIA、ADQ。
- 中东产业资本：Prosperity7 / Aramco Ventures、G42、e& / Etisalat。
- 中国侧披露补充：HKEXnews、CNINFO、上交所、深交所、SAMR 中与中东买方相关的披露。
- 海外监管补充：SEC、欧盟并购审查、英国 CMA、ASX 等持股披露、收购文件和并购审查线索。
- 新闻与研究线索：Google News / Bing News / GDELT 对 Middle East outbound M&A 的专题查询。

手工核验源包括：

- ADIA Annual Review、KIA / KIA China Office、OIA、Mumtalakat、ICD。
- Global SWF、SWFI、LSEG、Bloomberg、Mergermarket、PitchBook、CapIQ、Zephyr。
- Crunchbase、Dealroom、Mergr、The National、Zawya、Arab News、SCMP、AVCJ。

设计原则：

- 官方公告和监管披露优先级为 P1。
- 新闻和数据库用于发现线索、补全交易字段和交叉验证。
- 每条中东交易仍应优先回到原始公告、监管文件或公司新闻稿核验。

### 2.5 候选排序和 cap 规则

`MAX_RAW_ITEMS=450` 时，国内新闻候选可能超过 cap。为避免中东专题候选被挤掉，`candidate_sort_key()` 会按优先级排序：

1. 标题、来源、地区或查询词命中中东买方白名单或中东地区词。
2. 全球候选。
3. 其他国内候选。

同优先级内再按发布时间倒序排列。

### 2.6 DeepSeek 结构化规则

`mna_weekly_tracker/deepseek.py` 的 prompt 已补充中东买方白名单，包括 PIF、Mubadala、QIA、ADQ、ADIA、KIA、OIA、Mumtalakat、ICD、Prosperity7、G42、e& 等。

结构化要求：

- 中东买方收购、入股、控股、少数股权、业务剥离、私有化海外企业的案例优先识别。
- 仅 MoU/合作、没有股权或资产交易的线索应剔除，或在备注中明确说明。
- DeepSeek fallback 默认关闭。`DEEPSEEK_API_KEY` 缺失或 API 调用失败时直接失败，避免静默生成粗略行。
- 只有本地调试时显式设置 `MNA_ALLOW_ROUGH_FALLBACK=1`，才允许输出 rough fallback 行。

### 2.7 Excel 输出

`跟踪信息源` sheet 现在输出字段：

```text
来源/查询名称、来源类型、覆盖范围、交易阶段、建议频率、优先级、URL、关键词/查询
```

其中自动采集源、手工核验源、全球查询、中东专题查询和 HKEXnews 查询都会进入该 sheet，便于人工复核每周 Excel 的覆盖范围。

## 3. Word case reports pipeline

这个任务不适合继续依赖单一大 prompt。原因是：

1. DeepSeek API 不联网，正文所需的交易日期、交易金额、财务数据、双方动机等事实必须先由代码抓取和整理。
2. 长篇正文同时要求事实完整、标题合规、字数 3550-3800 且绝不超过 4000、语气客观、Word 排版正确，单 prompt 很容易遗漏某一项。
3. 之前反复出现的问题，例如 JSON 解析失败、字数不足、标题口号化、正文出现“上市公司CEO”等对象提示语，本质都是生成任务过大、验证滞后造成的。
4. backfill 批量生成时，单篇反复整文重写会显著拉长 Action 时间。

因此采用 staged pipeline：先事实、再大纲、再正文、再验证、最后写 Word。

## 3.1 当前 pipeline

```text
case_selection
  -> collect_research_context
  -> build_fact_pack
  -> validate_fact_pack
  -> build_narrative_plan
  -> validate_narrative_plan
  -> route long-form article calls to article model
  -> generate_article body
  -> validate_article + assess_quality
  -> targeted revision
  -> length expansion / trim
  -> write_docx
```

weekly 候选池按“已去重且未写过”的候选计数，不再用历史重复交易占用 ready buffer。生产默认准备 16 个来源可追溯的候选供 4 篇成品依次尝试；每轮还会执行实时新闻与 Tavily 发现。Tavily 仅提供发现线索，候选仍须满足已完成交易、明确交易双方、可用原始来源和事实包验证，任何发现源都不能绕过这些门槛。

“原始来源”必须是监管披露、交易所文件、公司投资者关系页面或公司正式新闻稿；PDF 后缀本身不代表权威来源，咨询机构报告和媒体报道只能作为补充材料。候选若名称不同但指向同一公告 URL，按同一交易去重。生产批次默认最多尝试 16 个候选，单候选 720 秒后停止并转向下一候选，最终仍须凑足 4 篇全部通过硬校验和质量校验的成品才算成功。

正文生成前必须先形成“交易前因后果链”：

```text
交易前状态
  -> 触发事件/发起路径
  -> 交易目标
  -> 结构选择
  -> 产业、财务、治理和方法论分析
```

这条链用于解决文章只罗列交易结果、没有解释“为什么做这个 deal、这个 deal 怎么发起、为了实现什么目标”的问题。若公开资料没有披露主观动机或完整发起过程，文章必须明确披露边界，并用公告、协议、要约、董事会/股东会决议、持股变化、资金来源和交割条件等客观条款解释交易机制，不能编造。

## 3.2 任务分层与模型路由

轻量任务继续走默认 DeepSeek 快模型：

- 候选筛选和去重。
- 原始链接清洗。
- 事实包抽取。
- 叙事计划。

复杂长文任务走文章模型：

- 初稿正文。
- 字数扩写。
- 质量重写。
- 最终修复。

GitHub Action 通过以下环境变量控制：

```text
REPORT_ARTICLE_MODEL_PROVIDER=rkapi
REPORT_ARTICLE_BASE_URL=https://rkapi.com/v1
REPORT_ARTICLE_MODEL=gpt-5.5
REPORT_ARTICLE_REASONING_EFFORT=xhigh
REPORT_ARTICLE_FALLBACK_PROVIDER=deepseek-pro
REPORT_ARTICLE_DEEPSEEK_MODEL=deepseek-v4-pro
```

RKAPI 发生 5xx、Cloudflare 504 或 timeout 时，`article_chat_json()` 会先重试，再 fallback 到 `deepseek-v4-pro`，避免单篇正文生成卡死导致整轮 Action 无产物。

轻量事实抽取和长文调用若返回空内容或不可解析 JSON，客户端会记录 `json_response_retry` 埋点并重新执行原任务。重试只处理模型响应故障，不会接受缺字段或未通过事实验证的结果。

## 4. 最新写作与排版规则

### 4.1 内容与风格

- 文档质量优先于格式统一，不要过度结构化、模式化；结构应服务于内容。
- 每篇文章可根据材料特点调整叙述重点，可侧重产业判断、交易结构、标的质量、交割承接、财务影响或并购方法论意义。
- 文章要有深度，除案例拆解外，应包含包括但不限于产业判断、交易结构分析和并购方法论意义。
- 文章前部必须先讲清交易前因后果：交易前是什么状态，什么事项触发或启动交易，谁通过什么路径发起交易，交易希望实现什么目标。
- 文章风格应在学术严谨性和可读性之间保持平衡，专业、克制、有判断力，使用流畅自然的中文。
- 客观中性，不使用负面化、口号化、广告化或宏观敏感表达；像“悬疑”“杠杆入主”“资本游戏”这类词只作为风格示例，实际修复应由模型重写为交易结构、产业位置、财务影响或治理安排表述，而不是代码机械替换。
- 读者画像只用于控制深度，不得在正文出现“上市公司CEO”“上市公司董事长”“读者”等提示语。
- 事实、数字、信息必须基于公开权威资料；资料没有披露时写“公开资料未披露”，严禁编造。

### 4.2 字数

- 全文字数控制在 3,500-4,000 字。
- 代码将 4,000 字视为硬上限，写作目标收紧到 3,550-3,800 字，必要时由后处理自动裁剪，但不会接受超 4,000 字成品。

### 4.3 标题

- 标题需准确概括文章主旨，突出案例的核心交易逻辑或分析重点，兼顾专业性与吸引力。
- 避免过于平淡、空泛，也避免标题党式表达。
- 主副标题必须包含交易双方名称或简称。

### 4.4 标点、数字与公司名称

- 全文使用一致的全角中文标点。
- 不要在中文字符和英文单词或数字之间添加空格。
- 金额、数量等类型数字应添加千分位逗号，例如 `1,276,000,000`。
- 公司名称首次出现时，应使用括号标注其全称、下文简称和股票代码（如上市），例如：`腾讯音乐娱乐集团（Tencent Music Entertainment Group，下文简称“腾讯音乐”，NYSE：TME）`。

### 4.5 Word 排版

文档网格：

```xml
<w:docGrid w:type="lines" w:linePitch="312"/>
```

一级标题（文章标题）：

- 黑体
- 小三
- 单倍行距
- 居中对齐
- 无首行缩进

二级标题（章标题）：

- 仿宋
- 加粗
- 四号
- 单倍行距
- 左对齐
- 首行缩进 2 字符

正文：

- 仿宋
- 四号
- 单倍行距
- 首行缩进 2 字符
- 段前 0、段后 0

## 5. Word 模块职责

### 5.1 `case_selection.py`

负责选题：

- weekly 从最近新闻和公告中选题。
- backfill 从实时搜索、扩展案例池、经典案例池中选题。
- 输出 `CaseBrief`，包含：案例名、分类、地区、并购方、标的方、交易金额、交易状态、买方动机、卖方动机、财务数据等。

后续优化重点：继续补全 `case_pool.py` 中每个案例的结构化字段，否则 fact pack 会缺数据。

### 5.2 `research.py`

负责给 DeepSeek 准备原料：

- Bing Web search
- Google News
- Bing News
- HTML 页面抓取
- PDF 下载和 `pypdf` 文本抽取
- 数字和关键词打分，抽取交易金额、交易时间、收入、净利润、股权比例等片段

DeepSeek 不联网，因此这一层是事实质量的核心。

### 5.3 `fact_pack.py`

负责把 `CaseBrief + research_rows` 转成紧凑事实包：

```json
{
  "case_name": "...",
  "acquirer": "...",
  "target": "...",
  "deal_value": "...",
  "deal_status": "...",
  "buyer_rationale": "...",
  "seller_rationale": "...",
  "financial_highlights": "...",
  "timeline": [...],
  "key_numbers": [...],
  "validation_issues": [...]
}
```

设计原则：

- 事实包不写文章，只做事实抽取。
- 资料没有披露的字段不编造。
- 若缺金额、时间线、双方名称或数据，会写入 `validation_issues` 并进入日志。

### 5.4 `narrative_generation.py`

负责生成文章主线，不直接写正文。

输出字段包括：

```json
{
  "core_question": "...",
  "central_thesis": "...",
  "narrative_focus": "...",
  "deal_origin_chain": "...",
  "initiation_mechanism": "...",
  "strategic_objective": "...",
  "title_direction": "...",
  "structure_logic": "...",
  "depth_angles": [...],
  "chapter_directions": [...],
  "must_cover": [...],
  "avoid_patterns": [...]
}
```

其中 `deal_origin_chain`、`initiation_mechanism`、`strategic_objective` 是强制字段，分别回答：

- 交易发生前的股权、业务、经营、产业或控制权状态。
- 交易如何被发起或触发，例如公告、协议、要约、董事会/股东会决议、监管文件或交割安排。
- 交易希望实现的目标，例如取得控制权、内部整合、产业协同、资产注入、退出变现、优化资本结构或补强业务。

### 5.5 `outline_generation.py`

负责生成 4-7 个客观、中性、克制的章节标题。

要求：

- 不要直接使用“交易动机 / 交易背景 / 交易结构设计 / 并购战略考量 / 标的筛选 / 并购后整合 / 价值释放”等提纲词。
- 不使用口号化、负面化、广告化表达。
- 最后一章必须是 `N、结语：副标题`，N 按实际章节数编号。
- 大纲不是固定模板，后续应按案例材料特点动态调整。

当前主线以 `narrative_generation.py` 的叙事计划为准；`outline_generation.py` 保留为章节标题辅助能力。

### 5.6 `article_rules.py`

负责：

- 标题规范化
- 章节编号和结语编号
- 客观语气清洗
- 禁止正文出现写作对象提示语，例如“上市公司CEO”“上市公司董事长”“读者是”
- 禁止推测词，例如“假设”“推测”“可能是”“或许”“有望”“如果”“若能”“若未”
- 禁止口号化或负面化措辞，例如“避坑”“踩雷”“暴雷”“豪赌”“翻车”
- 字数检查：大于 3500、小于 4000，目标 3550-3800；4,000 字以上直接视为不合格并在写出前裁剪。
- 数据密度检查：交易金额、时间线、财务/经营数字、双方基本介绍、双方接受交易安排的原因
- 过长自动裁剪，过短追加事实段落

### 5.7 `article_quality.py`

负责软质量检查，重点识别：

- 结构模板化。
- 标题平淡或标题党。
- 媒体化、戏剧化表达；其中“悬疑”“杠杆入主”“资本游戏”等仅是风格示例，修复动作应回到模型重写。
- 产业判断、交易结构和并购方法论不足。
- 结语泛泛而谈。
- 文章前部缺少交易前状态、发起机制或交易目标。
- 数值逻辑矛盾，例如预受/接受比例超过九成却写“预受率严重不足”。

### 5.8 `report_generation.py`

现在变为 orchestration 层，不再承载所有规则：

1. 调用 `collect_research_context`
2. 调用 `build_fact_pack`
3. 调用 `build_narrative_plan`
4. 按任务分层选择文章模型或 fallback 模型
5. 基于事实包和叙事计划生成正文
6. 调用 `validate_article` 和 `assess_quality`
7. 定向修订非字数问题
8. 字数不足时调用扩写；过长时裁剪
9. 输出 article dict 给 `docx_writer`

### 5.9 `docx_writer.py`

负责 Word 输出：

- 文档网格 `<w:docGrid w:type="lines" w:linePitch="312"/>`
- 一级标题：黑体、小三、单倍行距、居中、无首行缩进
- 二级标题：仿宋、加粗、四号、单倍行距、左对齐、首行缩进 2 字符
- 正文：仿宋、四号、首行缩进 2 字符、段前/段后 0
- 引号 Times New Roman
- 文件名加 run label，避免覆盖旧报告

## 6. 关键设计原则

### 6.1 prompt 只做适合模型做的事

模型适合：

- 从已给资料中组织叙述
- 生成中性标题
- 将事实组织成 3550-3800 字文章
- 根据明确 issues 定向修订

模型不适合：

- 自己联网找事实
- 在缺数据时补数字
- 同时承担事实抽取、标题、正文、字数、排版、提交等所有任务

### 6.2 validation 要代码化

所有硬性要求都尽量在代码里检查：

- 字数
- 标题是否含交易双方
- 是否含“上市公司CEO”等对象提示语
- 是否含推测词
- 是否含口号化/负面化词，作为软质量反馈交给模型重写，而不是靠代码做逐词替换
- 是否含交易金额和日期
- 是否含财务数据
- 文章前部是否交代交易前状态、发起机制和交易目标
- 是否存在事实占位符，例如 `xx%`、`待补充`、`TODO`
- 是否存在明显数值逻辑矛盾
- 最后一章编号是否正确
- 文档网格、标题、正文段落格式

空格、全角标点和千分位逗号等机械格式问题由确定性后处理修复；章节具体度不足时，只允许用已验证事实包中的交易双方、对价/支付口径、时间线和关键数字补充事实锚点。修复后仍需重新通过完整硬校验和质量校验，不允许将失败稿作为正式报告写出。

### 6.3 backfill 要批量化

不建议再一次跑：

```text
mode=backfill
count=40
```

建议：

```text
mode=backfill
count=5
min_domestic=3
offset=0, 5, 10, 15...
```

GitHub Action 已支持 offset，并会上传 artifact、保存 progress manifest、部分失败不中断整批。

## 7. 当前仍需继续优化

1. `case_pool.py` 中历史案例的结构化字段需要补齐，尤其是 `deal_value`、`deal_status`、`buyer_motivation`、`seller_motivation`、`financial_highlights`。
2. `fact_pack.py` 现在是第一版，仍可增强：
   - 按来源可信度排序。
   - 给每条事实保留 source URL。
   - 对金额、日期、财务数据做字段级 confidence。
3. `narrative_generation.py` 可继续增强交易发起过程抽取，尤其是区分“公开披露的目标”和“由条款推导的客观机制”。
4. `outline_generation.py` 可进一步按案例分类切换默认大纲，并减少默认模板感。
5. `report_generation.py` 仍输出 JSON，长期可考虑改成 Markdown/plain text，降低长篇 JSON 解析风险。
6. 需要继续下载生成的 docx 做真实 Word 格式验证，确认 Word UI 中确实显示“首行缩进 2 字符、段后 0 行、文档网格 linePitch=312”。

## 8. 下一步建议

优先顺序：

1. 小批量跑 `backfill count=3 或 5` 验证 staged pipeline。
2. 检查 artifact 中的 docx：字数、标题、章节、语气、数据密度、排版。
3. 根据失败日志补 `case_pool.py` 的结构化字段。
4. 增加 docx XML 格式校验脚本。
5. 如果 JSON 仍不稳定，将正文生成改成 markdown body，再由 Python 解析写 Word。
