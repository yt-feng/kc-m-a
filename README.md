# kc-m-a 周度并购案例跟踪

这个仓库用于每周自动整理最新一周的全球并购案例：中国为主，全球为辅，并补充追踪中东资本收购/入股海外企业的信息源；输出格式参考上传的 Excel 案例库，A 列“案例分类”按《并购分类建议（含案例名称）》中的 10 类口径。

## 自动运行时间

GitHub Actions 已配置为：**北京时间每周五 05:00** 自动运行一次。

GitHub Actions 的 cron 使用 UTC，因此 workflow 中配置的是：

```yaml
- cron: "0 21 * * 4"
```

即 UTC 周四 21:00 = 北京时间周五 05:00。

## 输出

每次运行会生成一个新的 Excel 文件，保存到：

```text
outputs/并购案例一览_YYYYMMDD_YYYYMMDD.xlsx
```

文件名中的两个日期对应最近 7 天的起止日期。Excel 包含：

- `周度并购案例`：结构化案例主表
- `运行摘要`：运行区间、候选数、案例数、警告信息
- `跟踪信息源`：本次跟踪的信息源和关键词
- `原始候选`：DeepSeek 结构化前的原始公告/新闻候选，便于复核

## 数据源逻辑

### 中国：重点跟踪官方公告源

代码优先跟踪截图中的中国官方信息源，包括：

- 巨潮资讯网：全市场公告、重大资产重组、收购报告书、权益变动、要约收购、发行股份购买资产、资产出售/置换等关键词
- 上交所：并购重组披露栏目、上市公司公告中的重大资产重组、收购/权益变动
- 深交所：上市公司公告、发行上市/并购重组入口、重大资产重组、收购/权益变动
- 北交所：上市公司公告
- 全国股转系统：挂牌公司公告、并购重组规则/公告、收购/要约关键词

巨潮资讯网使用公告查询接口拉取；交易所和股转系统使用 Google News 的站点限制查询做补充发现。

### 中东资本出海并购：官方源、监管源和新闻源

系统参考 `docs/中东收购海外企业信息源清单.xlsx`，新增中东主权基金、政府控股平台和产业资本的专题源：

- 官方源：PIF、Mubadala、QIA、ADQ、Prosperity7 / Aramco Ventures、G42、e& 等新闻稿和官网页面
- 中国侧披露补充：HKEXnews、CNINFO、上交所、深交所、SAMR 中与 PIF / Mubadala / QIA / ADQ 等买方相关的公告或审批线索
- 海外监管补充：SEC、欧盟并购审查、英国 CMA、ASX 等持股披露、收购文件和并购审查线索
- 新闻与数据库线索：Reuters、Bloomberg、Zawya、The National、Arab News、SCMP，以及 Global SWF、SWFI、LSEG、Mergermarket、PitchBook、CapIQ、Zephyr 等手工核验源

### 全球：Google News

全球案例使用 Google News RSS 查询，聚焦 merger / acquisition / takeover、cross-border acquisition、SPAC、privatization、buyout，以及中国公司跨境收购和中东资本出海收购。

## DeepSeek 结构化

GitHub Action 从 repo Secret 读取：

```text
DEEPSEEK_API_KEY
```

默认模型：

```text
deepseek-v4-flash
```

DeepSeek 输出会被限制为 JSON，并映射到 Excel 列：案例分类、序号、并购方、目标方、案例所属行业、并购方主营业务、标的主营业务、案例一句话简介、交易时间、交易对价、交易状态、备注、来源名称、URL、发布日期、地区。

默认必须配置 `DEEPSEEK_API_KEY` 且 DeepSeek 调用成功；不会静默生成粗略 fallback 行。仅本地调试需要保底行时，可显式设置 `MNA_ALLOW_ROUGH_FALLBACK=1`。

## 手动运行

```bash
pip install -r requirements.txt
export DEEPSEEK_API_KEY="your_api_key"
python -m mna_weekly_tracker.main --days 7 --output-dir outputs
```

也可以在 GitHub Actions 页面使用 `workflow_dispatch` 手动触发。

## 配置位置

主要配置在：

```text
mna_weekly_tracker/config.py
```

可在此调整并购分类口径、中国官方信息源、中东专题信息源、关键词、全球 Google News 查询词和 Excel 输出列。
