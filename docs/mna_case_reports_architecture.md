# Weekly M&A Case Reports 架构文档

更新时间：2026-05-24

## 1. 总体判断

这个任务不适合继续依赖单一大 prompt。原因是：

1. DeepSeek API 不联网，正文所需的交易日期、交易金额、财务数据、双方动机等事实必须先由代码抓取和整理。
2. 长篇正文同时要求事实完整、标题合规、字数 3500-4000、语气客观、Word 排版正确，单 prompt 很容易遗漏某一项。
3. 之前反复出现的问题，例如 JSON 解析失败、字数不足、标题口号化、正文出现“上市公司CEO”等对象提示语，本质都是生成任务过大、验证滞后造成的。
4. backfill 批量生成时，单篇反复整文重写会显著拉长 Action 时间。

因此采用 staged pipeline：先事实、再大纲、再正文、再验证、最后写 Word。

## 2. 当前 pipeline

```text
case_selection
  -> collect_research_context
  -> build_fact_pack
  -> validate_fact_pack
  -> generate_outline
  -> validate_outline
  -> generate_article body
  -> validate_article
  -> targeted revision
  -> length expansion / trim
  -> write_docx
```

## 3. 模块职责

### 3.1 `case_selection.py`

负责选题：

- weekly 从最近新闻和公告中选题。
- backfill 从实时搜索、扩展案例池、经典案例池中选题。
- 输出 `CaseBrief`，包含：案例名、分类、地区、并购方、标的方、交易金额、交易状态、买方动机、卖方动机、财务数据等。

后续优化重点：继续补全 `case_pool.py` 中每个案例的结构化字段，否则 fact pack 会缺数据。

### 3.2 `research.py`

负责给 DeepSeek 准备原料：

- Bing Web search
- Google News
- Bing News
- HTML 页面抓取
- PDF 下载和 `pypdf` 文本抽取
- 数字和关键词打分，抽取交易金额、交易时间、收入、净利润、股权比例等片段

DeepSeek 不联网，因此这一层是事实质量的核心。

### 3.3 `fact_pack.py`

新增模块。负责把 `CaseBrief + research_rows` 转成紧凑事实包：

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

### 3.4 `outline_generation.py`

新增模块。负责生成 4-7 个客观、中性、克制的章节标题。

要求：

- 不要直接使用“交易动机 / 交易背景 / 交易结构设计 / 并购战略考量 / 标的筛选 / 并购后整合 / 价值释放”等提纲词。
- 不使用口号化、负面化、广告化表达。
- 最后一章必须是 `N、结语：副标题`，N 按实际章节数编号。

如果模型输出不合格，则 fallback 到默认大纲：

```text
一、关键事实与交易进程
二、交易双方的业务基础与披露信息
三、价格、支付方式与交割条件安排
四、交割后的治理、业务与人员承接
五、结语：从公开事实回到执行关注点
```

### 3.5 `article_rules.py`

新增模块。负责：

- 标题规范化
- 章节编号和结语编号
- 客观语气清洗
- 禁止正文出现写作对象提示语，例如“上市公司CEO”“上市公司董事长”“读者是”
- 禁止推测词，例如“假设”“推测”“可能是”“或许”“有望”“如果”“若能”“若未”
- 禁止口号化或负面化措辞，例如“避坑”“踩雷”“暴雷”“豪赌”“翻车”
- 字数检查：大于 3500、小于 4000，目标 3600-3900
- 数据密度检查：交易金额、时间线、财务/经营数字、双方基本介绍、双方接受交易安排的原因
- 过长自动裁剪，过短追加事实段落

### 3.6 `report_generation.py`

现在变为 orchestration 层，不再承载所有规则：

1. 调用 `collect_research_context`
2. 调用 `build_fact_pack`
3. 调用 `generate_outline`
4. 基于事实包和大纲生成正文
5. 调用 `validate_article`
6. 定向修订非字数问题
7. 字数不足时调用扩写；仍不足时追加事实段落
8. 输出 article dict 给 `docx_writer`

### 3.7 `docx_writer.py`

负责 Word 输出：

- 首行缩进 2 字符
- 段前/段后 0
- 引号 Times New Roman
- 文件名加 run label，避免覆盖旧报告

## 4. 关键设计原则

### 4.1 prompt 只做适合模型做的事

模型适合：

- 从已给资料中组织叙述
- 生成中性标题
- 将事实组织成 3500-4000 字文章
- 根据明确 issues 定向修订

模型不适合：

- 自己联网找事实
- 在缺数据时补数字
- 同时承担事实抽取、标题、正文、字数、排版、提交等所有任务

### 4.2 validation 要代码化

所有硬性要求都尽量在代码里检查：

- 字数
- 标题是否含交易双方
- 是否含“上市公司CEO”等对象提示语
- 是否含推测词
- 是否含口号化/负面化词
- 是否含交易金额和日期
- 是否含财务数据
- 最后一章编号是否正确

### 4.3 backfill 要批量化

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

## 5. 当前仍需继续优化

1. `case_pool.py` 中历史案例的结构化字段需要补齐，尤其是 `deal_value`、`deal_status`、`buyer_motivation`、`seller_motivation`、`financial_highlights`。
2. `fact_pack.py` 现在是第一版，仍可增强：
   - 按来源可信度排序。
   - 给每条事实保留 source URL。
   - 对金额、日期、财务数据做字段级 confidence。
3. `outline_generation.py` 可进一步按案例分类切换默认大纲。
4. `report_generation.py` 仍输出 JSON，长期可考虑改成 Markdown/plain text，降低长篇 JSON 解析风险。
5. 需要下载生成的 docx 做真实 Word 格式验证，确认 Word UI 中确实显示“首行缩进 2 字符、段后 0 行”。

## 6. 下一步建议

优先顺序：

1. 小批量跑 `backfill count=3 或 5` 验证 staged pipeline。
2. 检查 artifact 中的 docx：字数、标题、章节、语气、数据密度、排版。
3. 根据失败日志补 `case_pool.py` 的结构化字段。
4. 如果 JSON 仍不稳定，将正文生成改成 markdown body，再由 Python 解析写 Word。
