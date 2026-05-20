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
    SourceConfig("上海证券交易所 - 并购重组披露栏目", "google_news_site", "https://www.sse.com.cn/listing/disclosure/ma/", "上交所上市公司并购重组审核披露", "审核/注册/进展", "每日/每周", ("重组审核公告", "项目信息", "问询回复", "重大资产重组"), "site:sse.com.cn/listing/disclosure/ma"),
    SourceConfig("上交所上市公司公告 - 重大资产重组", "google_news_site", "https://www.sse.com.cn/disclosure/listedinfo/announcement/", "沪市主板/科创板公司公告", "宣告/进展/交割", "每日/每周", ("重大资产重组", "购买资产", "出售资产", "发行股份购买资产"), "site:sse.com.cn/disclosure/listedinfo/announcement"),
    SourceConfig("上交所上市公司公告 - 收购/权益变动", "google_news_site", "https://www.sse.com.cn/disclosure/listedinfo/announcement/", "沪市公司控制权和股权变动", "权益变动/控制权", "每周", ("收购报告书", "权益变动报告书", "实际控制人变更", "协议转让"), "site:sse.com.cn/disclosure/listedinfo/announcement"),
    SourceConfig("深圳证券交易所 - 上市公司公告入口", "google_news_site", "https://www.szse.cn/disclosure/listed/notice/index.html", "深市主板/创业板公司公告", "宣告/进展/交割", "每日/每周", ("并购", "重组", "收购", "权益变动", "重大资产重组"), "site:szse.cn/disclosure/listed/notice"),
    SourceConfig("深圳证券交易所 - 发行上市/并购重组入口", "google_news_site", "https://www.szse.cn/listing/", "深市重组审核、规则、项目动态", "审核/注册/进展", "每周", ("重组委", "审核公告", "项目动态", "发行股份购买资产"), "site:szse.cn/listing"),
    SourceConfig("深交所公告 - 重大资产重组关键词", "google_news_site", "https://www.szse.cn/disclosure/listed/notice/index.html", "深市重大资产重组公告", "宣告/审核/终止", "每日/每周", ("重组预案", "重组草案", "重组报告书", "终止重组"), "site:szse.cn/disclosure/listed/notice"),
    SourceConfig("深交所公告 - 收购/权益变动关键词", "google_news_site", "https://www.szse.cn/disclosure/listed/notice/index.html", "深市收购与权益变动", "权益变动/控制权", "每周", ("收购报告书", "权益变动报告书", "协议转让", "控制权变更"), "site:szse.cn/disclosure/listed/notice"),
    SourceConfig("北京证券交易所 - 上市公司公告", "google_news_site", "https://www.bse.cn/disclosure/announcement.html", "北交所上市公司公告", "宣告/进展/交割", "每周", ("重组", "收购", "要约", "权益变动", "重大资产重组"), "site:bse.cn/disclosure/announcement.html"),
    SourceConfig("全国股转系统 - 挂牌公司公告", "google_news_site", "https://www.neeq.com.cn/disclosure/announcement.html", "新三板挂牌公司公告", "宣告/权益变动", "每周", ("收购", "重组", "定增并购", "权益变动"), "site:neeq.com.cn/disclosure/announcement.html"),
    SourceConfig("全国股转系统 - 并购重组类规则/公告", "google_news_site", "https://www.neeq.com.cn/node/484.html", "新三板并购重组业务规则和相关公告", "规则/审核", "每月/按需", ("并购重组", "重大资产重组", "权益变动", "收购规则"), "site:neeq.com.cn/node/484.html"),
]

GLOBAL_QUERIES: tuple[str, ...] = (
    "global merger acquisition announced company deal", "M&A acquisition merger deal announced last week", "cross-border acquisition Chinese company announced",
    "China company acquires overseas asset merger acquisition", "SPAC merger announced acquisition", "takeover offer acquisition announced", "privatization buyout acquisition announced",
)

HKEX_QUERIES: tuple[str, ...] = (
    "site:hkexnews.hk acquisition discloseable transaction major transaction", "site:hkexnews.hk connected transaction acquisition disposal", "site:hkexnews.hk takeover offer privatization acquisition",
)


def chunked(values: Sequence[object], size: int) -> list[list[object]]:
    return [list(values[i : i + size]) for i in range(0, len(values), size)]
