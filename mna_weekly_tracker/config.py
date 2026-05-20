"""Configuration for weekly M&A case tracking."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class SourceConfig:
    name: str
    kind: str
    url: str
    coverage: str
    stage: str
    frequency: str
    keywords: tuple[str, ...]
    site_query: str | None = None


OUTPUT_COLUMNS: list[str] = [
    "案例分类", "序号", "并购方", "目标方", "案例所属行业", "并购方主营业务", "标的主营业务",
    "案例一句话简介", "交易时间", "交易对价", "交易状态", "备注", "来源名称", "URL", "发布日期", "地区",
]

CATEGORIES: list[str] = [
    "整合一级资产+资本化", "依托上市平台持续整合同类资产", "上市公司控股权并购", "重组上市（借壳，含类借壳）",
    "破产重整", "跨境并购", "私有化+境内上市", "SPAC", "上市公司+PE", "分拆上市",
]

CATEGORY_GUIDE = """
1. 整合一级资产+资本化：并购具备上市潜力的资产，整合后通过 IPO 或被并购退出。
2. 依托上市平台持续整合同类资产：以某一行业上市公司平台为基础持续横向并购。
3. 上市公司控股权并购：针对 A/H/美股等已上市主体的控制权交易。
4. 重组上市（借壳，含类借壳）：非上市资产通过壳公司实现上市或类借壳上市。
5. 破产重整：通过重整方案解决财务/经营风险，并可能实现控制权转移或资产注入。
6. 跨境并购：境内外主体以现金或股份收购海外资产，或具有明显跨境属性的交易。
7. 私有化+境内上市：海外上市主体私有化退市并寻求 A 股或境内资本市场上市。
8. SPAC：通过 SPAC 或 De-SPAC 完成并购上市。
9. 上市公司+PE：上市公司与 PE/基金合作设立并购基金收购培育资产。
10. 分拆上市：上市公司剥离子公司或业务并独立 IPO、借壳或被并购。
""".strip()

MNA_KEYWORDS: tuple[str, ...] = (
    "重大资产重组", "发行股份购买资产", "购买资产", "出售资产", "资产置换", "资产重组", "吸收合并", "换股吸收合并",
    "收购报告书", "权益变动报告书", "要约收购", "控制权变更", "协议转让", "破产重整", "分拆上市", "私有化", "SPAC",
)

CHINA_SOURCES: list[SourceConfig] = [
    SourceConfig("巨潮资讯网 - 全市场公告入口", "cninfo_api", "https://www.cninfo.com.cn/new/index.jsp", "A股/北交所/基金等公告", "宣告/进展/交割", "每日/每周", MNA_KEYWORDS),
    SourceConfig("巨潮资讯网 - 重大资产重组关键词", "cninfo_api", "https://www.cninfo.com.cn/new/index.jsp", "A股上市公司重大资产重组公告", "宣告/审核/终止", "每日/每周", ("重大资产重组", "重组预案", "重组草案", "重组报告书", "审核问询", "终止重组")),
    SourceConfig("巨潮资讯网 - 收购报告书关键词", "cninfo_api", "https://www.cninfo.com.cn/new/index.jsp", "控制权收购、协议转让、要约收购", "宣告/权益变动", "每日/每周", ("收购报告书", "协议转让", "要约收购", "控制权变更")),
    SourceConfig("巨潮资讯网 - 权益变动报告书关键词", "cninfo_api", "https://www.cninfo.com.cn/new/index.jsp", "5%以上持股变动、协议转让", "早期信号/权益变动", "每周", ("权益变动报告书", "简式权益变动报告书", "详式权益变动报告书")),
    SourceConfig("巨潮资讯网 - 要约收购关键词", "cninfo_api", "https://www.cninfo.com.cn/new/index.jsp", "要约收购与要约结果", "宣告/交割", "每日/每周", ("要约收购", "要约收购报告书", "要约收购结果公告")),
    SourceConfig("巨潮资讯网 - 发行股份购买资产关键词", "cninfo_api", "https://www.cninfo.com.cn/new/index.jsp", "发行股份/可转债购买资产", "宣告/审核/注册", "每日/每周", ("发行股份购买资产", "配套融资", "重组报告书")),
    SourceConfig("巨潮资讯网 - 资产出售/资产置换关键词", "cninfo_api", "https://www.cninfo.com.cn/new/index.jsp", "资产剥离、置出、重大资产出售", "宣告/交割", "每周", ("重大资产出售", "资产置换", "置入置出", "资产出售")),
    SourceConfig("上海证券交易所 - 并购重组披露栏目", "google_news_site", "https://www.sse.com.cn/listing/disclosure/ma/", "上交所上市公司并购重组审核披露", "审核/注册/进展", "每日/每周", ("重组审核公告", "项目信息", "问询回复", "重大资产重组"), "site:sse.com.cn/listing/disclosure/ma"),
    SourceConfig("上交所上市公司公告 - 重大资产重组", "google_news_site", "https://www.sse.com.cn/disclosure/listedinfo/announcement/", "沪市主板/科创板公司公告", "宣告/进展/交割", "每日/每周", ("重大资产重组", "购买资产", "出售资产", "发行股份购买资产"), "site:sse.com.cn/disclosure/listedinfo/announcement"),
    SourceConfig("上交所上市公司公告 - 收购/权益变动", "google_news_site", "https://www.sse.com.cn/disclosure/listedinfo/announcement/", "沪市公司控制权和股权变动", "权益变动/控制权", "每周", ("收购报告书", "权益变动报告书", "实际控制人变更", "协议转让"), "site:sse.com.cn/disclosure/listedinfo/announcement"),
    SourceConfig("深圳证券交易所 - 上市公司公告入口", "google_news_site", "https://www.szse.cn/disclosure/listed/notice/index.html", "深市主板/创业板公司公告", "宣告/进展/交割", "每日/每周", ("并购", "重组", "收购", "权益变动", "重大资产重组"), "site:szse.cn/disclosure/listed/notice"),
    SourceConfig("深圳证券交易所 - 发行上市/并购重组入口", "google_news_site", "https://www.szse.cn/listing/", "深市重组审核、规则、项目动态", "审核/注册/进展", "每周", ("重组委", "审核公告", "项目动态", "发行股份购买资产"), "site:szse.cn/listing"),
    SourceConfig("深交所公告 - 重大资产重组关键词", "google_news_site", "https://www.szse.cn/disclosure/listed/notice/index.html", "深市重大资产重组公告", "宣告/审核/终止", "每日/每周", ("重组预案", "重组草案", "重组报告书", "终止重组"), "site:szse.cn/disclosure/listed/notice"),
    SourceConfig("深交所公告 - 收购/权益变动关键词", "google_news_site", "https://www.szse.cn/disclosure/listed/notice/index.html", "深市收购与权益变动", "权益变动/控制权", "每周", ("收购报告书", "权益变动报告书", "协议转让", "控制权变更"), "site:szse.cn/disclosure/listed/notice"),
    SourceConfig("北京证券交易所 - 上市公司公告", "google_news_site", "https://www.bse.cn/disclosure/announcement.html", "北交所上市公司公告", "宣告/进展/交割", "每周", ("重组", "收购", "要约", "权益变动", "重大资产重组"), "site:bse.cn/disclosure/announcement.html"),
    SourceConfig("北交所公告 - 重大资产重组关键词", "google_news_site", "https://www.bse.cn/disclosure/announcement.html", "北交所上市公司重大资产重组", "宣告/审核/终止", "每周", ("重大资产重组", "发行股份购买资产"), "site:bse.cn/disclosure/announcement.html"),
    SourceConfig("全国股转系统 - 挂牌公司公告", "google_news_site", "https://www.neeq.com.cn/disclosure/announcement.html", "新三板挂牌公司公告", "宣告/权益变动", "每周", ("收购", "重组", "定增并购", "权益变动"), "site:neeq.com.cn/disclosure/announcement.html"),
    SourceConfig("全国股转系统 - 并购重组类规则/公告", "google_news_site", "https://www.neeq.com.cn/node/484.html", "新三板并购重组业务规则和相关公告", "规则/审核", "每月/按需", ("并购重组", "重大资产重组", "权益变动", "收购规则"), "site:neeq.com.cn/node/484.html"),
    SourceConfig("全国股转系统 - 收购/要约关键词", "google_news_site", "https://www.neeq.com.cn/disclosure/announcement.html", "新三板收购、要约、权益变动", "权益变动/收购", "每周", ("收购报告书", "要约收购", "权益变动报告书"), "site:neeq.com.cn/disclosure/announcement.html"),
]

CHINA_NEWS_SOURCES: list[SourceConfig] = [
    SourceConfig("中国新闻广域 - Bing News RSS", "bing_news_search", "https://www.bing.com/news/search?format=rss", "中文新闻中的中国并购、收购、重组、股权转让", "早期线索/宣告/完成", "每周", (
        "并购 收购 重组 交易完成", "上市公司 并购重组 重大资产重组", "股权转让 控制权变更 战略投资者",
        "企业增资 引入战略投资者 产权转让", "破产重整 重整投资人 招募", "经营者集中 收购股权 新设合营",
        "中国企业 收购 海外资产", "港股 非常重大收购 须予披露交易", "中概股 私有化 合并协议",
    )),
    SourceConfig("中国新闻广域 - Google News RSS", "google_news_search", "https://news.google.com/", "中文新闻中的中国并购、收购、重组、交易完成", "早期线索/宣告/完成", "每周", (
        "中国 (并购 OR 收购 OR 合并 OR 股权转让 OR 交易完成)", "中国 并购重组 收购 重大资产重组 交易完成",
        "产业整合 并购 收购 中国", "A股 并购重组 收购报告书 权益变动", "港股 收购 出售 关连交易",
    )),
    SourceConfig("搜狗微信 - 微信文章并购线索", "sogou_weixin_search", "https://weixin.sogou.com/weixin", "微信公众号文章中的并购、收购、重组、产业整合线索", "早期线索/媒体报道", "每周", (
        "并购 收购 重组 交易完成", "上市公司 并购重组 重大资产重组", "股权转让 控制权变更 战略投资者",
        "产业并购 收购 完成", "投资人 招募 破产重整", "港股 收购 重大交易", "中概股 私有化 收购",
    )),
    SourceConfig("GDELT - 中国并购中文媒体监控", "gdelt_doc", "https://api.gdeltproject.org/api/v2/doc/doc", "中文/全球媒体中的中国并购新闻", "早期线索/宣告/完成", "每周", (
        "中国 (并购 OR 收购 OR 合并 OR 股权转让 OR 交易完成)", "中国 企业 收购 并购 股权转让", "China acquisition merger takeover stake completed",
    )),
    SourceConfig("监管审批 - SAMR/CSRC/发改委/商务部/外汇局", "bing_news_search", "https://www.bing.com/news/search?format=rss", "经营者集中、并购重组政策、境外投资备案、跨境资金监管", "审批/备案/政策", "每周/每月", (
        "site:samr.gov.cn 经营者集中 收购股权", "site:samr.gov.cn 无条件批准 经营者集中", "site:samr.gov.cn 附条件批准 经营者集中",
        "site:csrc.gov.cn 并购重组 重大资产重组", "site:ndrc.gov.cn 境外投资 并购 项目 备案 核准",
        "site:mofcom.gov.cn 对外投资 备案 并购", "site:safe.gov.cn 并购 跨境直接投资 外汇登记",
    )),
    SourceConfig("港股/中概股官方披露补充", "bing_news_search", "https://www.bing.com/news/search?format=rss", "港股中国公司、中概股并购、私有化、权益披露", "宣告/股东批准/完成", "每周", (
        "site:hkexnews.hk acquisition discloseable transaction major transaction", "site:hkexnews.hk connected transaction acquisition disposal",
        "site:hkexnews.hk takeover offer privatization acquisition", "site:sec.gov China acquisition 8-K merger agreement",
        "site:sec.gov China going private merger proxy 13E-3", "site:sec.gov China Schedule 13D acquisition stake",
    )),
    SourceConfig("债券与非上市公司披露补充", "bing_news_search", "https://www.bing.com/news/search?format=rss", "发债企业重大事项、集团层面并购、控制权变更", "债券市场信号/实体核验", "每周", (
        "site:nafmii.org.cn 收购 资产重组 控制权变更", "site:chinamoney.com.cn 重大事项 收购 重组 股权变更",
        "site:chinabond.com.cn 重大资产重组 收购 股权变更", "site:shclearing.com.cn 重大事项 收购 重组",
    )),
    SourceConfig("国资产权交易与增资挂牌", "bing_news_search", "https://www.bing.com/news/search?format=rss", "央企/地方国资产权转让、企业增资、混改和资产挂牌", "挂牌/竞价/成交", "每周", (
        "北京产权交易所 股权转让 企业增资 战略投资者", "上海联合产权交易所 股权项目 产权转让 增资",
        "广东联合产权交易中心 股权转让 企业增资", "深圳联合产权交易所 股权转让 企业增资",
        "江苏省产权交易所 股权转让 企业增资", "浙江产权交易所 股权转让 企业增资", "山东产权交易中心 股权转让 企业增资",
        "四川西南联合产权交易所 股权转让 企业增资", "央企 产权转让 增资扩股 混改",
    )),
    SourceConfig("司法/破产/资产处置", "bing_news_search", "https://www.bing.com/news/search?format=rss", "破产重整、重整投资人、股权拍卖、债权资产包", "特殊机会/资产交易", "每周", (
        "全国企业破产重整案件信息网 重整投资人 招募 收购", "site:court.gov.cn 破产重整 投资人 招募 收购",
        "阿里资产 股权 公司 资产包", "京东资产 股权 债权 公司", "北京金融资产交易所 债权资产 股权 资产包",
        "信达 华融 长城 东方 资产处置 股权 债权转让",
    )),
    SourceConfig("财经媒体/专业资讯 - 中国并购", "google_news_search", "https://news.google.com/", "财经媒体、专业资讯、创投媒体中的并购和退出线索", "早期线索/宣告/完成", "每周", (
        "财新 并购 重组 收购 交易完成", "证券时报 并购重组 收购 重大资产重组", "上海证券报 并购重组 资产重组 收购",
        "中国证券报 并购重组 收购", "21世纪经济报道 并购 收购 重组", "第一财经 并购 收购 重组",
        "每日经济新闻 并购重组 收购", "界面新闻 并购 收购", "澎湃新闻 并购 收购 资本市场", "财联社 并购重组 收购",
        "格隆汇 并购重组 收购", "东方财富 并购重组 收购", "同花顺 并购重组 收购", "36氪 并购 收购 融资 交易",
        "投中网 并购 市场 收购", "清科 并购 市场 报告", "IT桔子 并购 收购 退出", "亿欧 并购 收购 产业整合",
    )),
]

ALL_TRACKED_SOURCES: list[SourceConfig] = CHINA_SOURCES + CHINA_NEWS_SOURCES

GLOBAL_QUERIES: tuple[str, ...] = (
    "global merger acquisition announced company deal", "M&A acquisition merger deal announced last week", "cross-border acquisition Chinese company announced",
    "China company acquires overseas asset merger acquisition", "SPAC merger announced acquisition", "takeover offer acquisition announced", "privatization buyout acquisition announced",
)

HKEX_QUERIES: tuple[str, ...] = (
    "site:hkexnews.hk acquisition discloseable transaction major transaction", "site:hkexnews.hk connected transaction acquisition disposal", "site:hkexnews.hk takeover offer privatization acquisition",
)


def chunked(values: Sequence[object], size: int) -> list[list[object]]:
    return [list(values[i : i + size]) for i in range(0, len(values), size)]
