"""Curated fallback case pool for M&A case report backfills.

The weekly selector still prefers fresh public-news candidates.  This pool is only
used when a large backfill request, such as count=40, does not have enough
qualified 2025-2026 completed cases from the live collectors.  Older cases are
included only when they are representative, sizeable, or well-known examples for
one of the ten categories.
"""

from __future__ import annotations

EXTENDED_CASE_POOL: list[dict[str, str]] = [
    {"case_name": "腾讯音乐收购喜马拉雅", "category": "依托上市平台持续整合同类资产", "region": "中国", "completed_year": "2025", "is_completed": "true", "why": "近两年完成的大型内容平台整合案例，适合分析买方会员和音频生态协同、卖方退出与平台承接。"},
    {"case_name": "美丽田园收购思妍丽", "category": "依托上市平台持续整合同类资产", "region": "中国香港/中国", "completed_year": "2025", "is_completed": "true", "why": "医美和生活美容连锁平台整合案例，适合分析门店网络、会员资产和投后标准化管理。"},
    {"case_name": "紫金矿业收购加纳Akyem金矿", "category": "跨境并购", "region": "中国/加纳", "completed_year": "2025", "is_completed": "true", "why": "近两年矿业出海已完成案例，适合分析资源品并购的价格、交割条件和海外运营承接。"},
    {"case_name": "洛阳钼业收购Equinox巴西金矿资产", "category": "跨境并购", "region": "中国/巴西", "completed_year": "2026", "is_completed": "true", "why": "2026年完成交割，适合分析金矿资产快速交割、权利承接和交割后管理。"},
    {"case_name": "美力科技收购德国ACPS集团", "category": "跨境并购", "region": "中国/德国", "completed_year": "2026", "is_completed": "true", "why": "2026年完成资产过户，适合分析中国制造企业跨境并购、锁箱机制和全球客户整合。"},
    {"case_name": "微牛证券通过SPAC合并上市", "category": "SPAC", "region": "中国/美国", "completed_year": "2025", "is_completed": "true", "why": "2025年完成SPAC合并上市，是近年中概金融科技公司资本化路径的代表案例。"},
    {"case_name": "Google收购Wiz", "category": "跨境并购", "region": "全球", "completed_year": "2026", "is_completed": "true", "why": "2026年完成的大型科技并购，适合分析监管风险分配、反向终止费和团队留存。"},
    {"case_name": "AMD收购ZT Systems并剥离制造业务", "category": "整合一级资产+资本化", "region": "全球", "completed_year": "2025", "is_completed": "true", "why": "2025年完成收购并推进制造业务剥离，适合分析先买能力、再拆资产的交易结构。"},
    {"case_name": "Intel出售Altera控股权给银湖资本", "category": "整合一级资产+资本化", "region": "全球", "completed_year": "2025", "is_completed": "true", "why": "2025年完成控股权出售，适合分析硬科技资产再定位和财务投资人承接。"},
    {"case_name": "Synopsys收购Ansys", "category": "跨境并购", "region": "全球", "completed_year": "2025", "is_completed": "true", "why": "近两年大型软件并购，适合分析EDA与仿真软件协同、监管审批和客户迁移。"},
    {"case_name": "诺和诺德收购Catalent部分产能资产", "category": "跨境并购", "region": "全球", "completed_year": "2025", "is_completed": "true", "why": "近两年医药制造产能并购，适合分析供应链能力补强和产能承接。"},
    {"case_name": "山东黄金收购银泰黄金", "category": "上市公司控股权并购", "region": "中国", "completed_year": "2025", "is_completed": "true", "why": "黄金行业上市平台控制权和资源整合案例，适合分析产业整合、资源储量和并表影响。"},
    {"case_name": "海尔智家收购开利商用制冷业务", "category": "跨境并购", "region": "中国/全球", "completed_year": "2025", "is_completed": "true", "why": "近两年中国企业跨境收购商业制冷资产案例，适合分析渠道、品牌和全球化组织承接。"},
    {"case_name": "启明创投入主天迈科技", "category": "上市公司控股权并购", "region": "中国", "completed_year": "2025", "is_completed": "true", "why": "私募基金取得上市公司控制权的代表案例，适合分析控制权交易和产业资源导入。"},
    {"case_name": "京东物流收购德邦股份", "category": "依托上市平台持续整合同类资产", "region": "中国", "completed_year": "2022", "is_completed": "true", "why": "不属于近两年，但属于物流平台收购同类网络资产的明星案例，规模和协同逻辑典型。"},
    {"case_name": "中国能建分拆易普力并借壳南岭民爆", "category": "分拆上市", "region": "中国", "completed_year": "2023", "is_completed": "true", "why": "不属于近两年，但属于央企体系内专业化整合与分拆资本化的代表案例。"},
    {"case_name": "华润三九收购昆药集团", "category": "依托上市平台持续整合同类资产", "region": "中国", "completed_year": "2022", "is_completed": "true", "why": "不属于近两年，但属于上市公司持续整合中药和OTC资产的代表案例。"},
    {"case_name": "TCL科技收购中环集团", "category": "上市公司控股权并购", "region": "中国", "completed_year": "2020", "is_completed": "true", "why": "不属于近两年，但属于上市公司收购半导体和新能源材料资产的明星案例。"},
    {"case_name": "闻泰科技收购安世半导体", "category": "跨境并购", "region": "中国/荷兰", "completed_year": "2019", "is_completed": "true", "why": "不属于近两年，但属于中国上市公司跨境收购半导体资产的代表案例。"},
    {"case_name": "韦尔股份收购北京豪威", "category": "重组上市（借壳，含类借壳）", "region": "中国", "completed_year": "2019", "is_completed": "true", "why": "不属于近两年，但属于A股半导体重组和图像传感器资产整合的代表案例。"},
    {"case_name": "中际装备并购苏州旭创", "category": "重组上市（借壳，含类借壳）", "region": "中国", "completed_year": "2017", "is_completed": "true", "why": "不属于近两年，但属于A股光模块资产注入和产业周期共振的明星案例。"},
    {"case_name": "顺丰借壳鼎泰新材", "category": "重组上市（借壳，含类借壳）", "region": "中国", "completed_year": "2017", "is_completed": "true", "why": "不属于近两年，但属于物流龙头借壳上市的经典案例。"},
    {"case_name": "圆通速递借壳大杨创世", "category": "重组上市（借壳，含类借壳）", "region": "中国", "completed_year": "2016", "is_completed": "true", "why": "不属于近两年，但属于快递企业集中登陆资本市场的代表案例。"},
    {"case_name": "申通快递借壳艾迪西", "category": "重组上市（借壳，含类借壳）", "region": "中国", "completed_year": "2016", "is_completed": "true", "why": "不属于近两年，但属于快递企业借壳上市和行业资本化的典型案例。"},
    {"case_name": "分众传媒借壳七喜控股", "category": "重组上市（借壳，含类借壳）", "region": "中国", "completed_year": "2015", "is_completed": "true", "why": "不属于近两年，但属于中概私有化后回归A股的代表性重组案例。"},
    {"case_name": "三六零借壳江南嘉捷", "category": "私有化+境内上市", "region": "中国/美国", "completed_year": "2018", "is_completed": "true", "why": "不属于近两年，但属于中概股私有化和境内资本市场路径的明星案例。"},
    {"case_name": "迈瑞医疗私有化后A股上市", "category": "私有化+境内上市", "region": "中国/美国", "completed_year": "2018", "is_completed": "true", "why": "不属于近两年，但属于医疗器械企业私有化回归和独立上市的代表案例。"},
    {"case_name": "巨人网络私有化回归A股", "category": "私有化+境内上市", "region": "中国/美国", "completed_year": "2016", "is_completed": "true", "why": "不属于近两年，但属于游戏企业私有化回归和重组上市的典型案例。"},
    {"case_name": "海尔智家私有化海尔电器", "category": "上市公司控股权并购", "region": "中国香港/中国", "completed_year": "2020", "is_completed": "true", "why": "不属于近两年，但属于H股平台整合和集团治理简化的代表案例。"},
    {"case_name": "美的集团收购库卡", "category": "跨境并购", "region": "中国/德国", "completed_year": "2017", "is_completed": "true", "why": "不属于近两年，但属于中国制造企业大规模跨境收购工业机器人资产的经典案例。"},
    {"case_name": "吉利收购沃尔沃汽车", "category": "跨境并购", "region": "中国/瑞典", "completed_year": "2010", "is_completed": "true", "why": "不属于近两年，但属于中国企业跨境收购全球汽车品牌的标志性案例。"},
    {"case_name": "联想收购IBM个人电脑业务", "category": "跨境并购", "region": "中国/美国", "completed_year": "2005", "is_completed": "true", "why": "不属于近两年，但属于中国企业全球化并购和品牌承接的经典案例。"},
    {"case_name": "潍柴动力收购凯傲集团", "category": "跨境并购", "region": "中国/德国", "completed_year": "2012", "is_completed": "true", "why": "不属于近两年，但属于中国制造企业收购德国高端装备资产的代表案例。"},
    {"case_name": "中联重科收购意大利CIFA", "category": "跨境并购", "region": "中国/意大利", "completed_year": "2008", "is_completed": "true", "why": "不属于近两年，但属于工程机械企业跨境并购技术和渠道资产的代表案例。"},
    {"case_name": "中国化工收购先正达", "category": "跨境并购", "region": "中国/瑞士", "completed_year": "2017", "is_completed": "true", "why": "不属于近两年，但属于中国企业大规模跨境收购农业科技资产的代表案例。"},
    {"case_name": "招商蛇口吸收合并招商地产", "category": "分拆上市", "region": "中国", "completed_year": "2015", "is_completed": "true", "why": "不属于近两年，但属于央企地产平台整合和资本平台重塑的典型案例。"},
    {"case_name": "南北车合并组建中国中车", "category": "依托上市平台持续整合同类资产", "region": "中国", "completed_year": "2015", "is_completed": "true", "why": "不属于近两年，但属于同类龙头合并和产业平台整合的标志性案例。"},
    {"case_name": "宝钢武钢合并组建中国宝武", "category": "依托上市平台持续整合同类资产", "region": "中国", "completed_year": "2016", "is_completed": "true", "why": "不属于近两年，但属于钢铁行业大型平台整合的代表案例。"},
    {"case_name": "海航集团破产重整", "category": "破产重整", "region": "中国", "completed_year": "2022", "is_completed": "true", "why": "不属于近两年，但属于大型集团破产重整和资产分层处置的代表案例。"},
    {"case_name": "紫光集团破产重整", "category": "破产重整", "region": "中国", "completed_year": "2022", "is_completed": "true", "why": "不属于近两年，但属于科技集团重整投资人引入和债务重组的代表案例。"},
    {"case_name": "方正集团破产重整", "category": "破产重整", "region": "中国", "completed_year": "2021", "is_completed": "true", "why": "不属于近两年，但属于大型多元化集团司法重整和产业投资人承接的代表案例。"},
    {"case_name": "重庆力帆破产重整", "category": "破产重整", "region": "中国", "completed_year": "2020", "is_completed": "true", "why": "不属于近两年，但属于汽车企业重整和战略投资人导入的代表案例。"},
    {"case_name": "Grab通过SPAC上市", "category": "SPAC", "region": "全球", "completed_year": "2021", "is_completed": "true", "why": "不属于近两年，但属于东南亚平台企业De-SPAC上市的代表案例。"},
    {"case_name": "Polestar通过SPAC上市", "category": "SPAC", "region": "全球", "completed_year": "2022", "is_completed": "true", "why": "不属于近两年，但属于新能源车企通过SPAC上市的典型案例。"},
    {"case_name": "黑石私有化希尔顿后重新上市", "category": "整合一级资产+资本化", "region": "全球", "completed_year": "2013", "is_completed": "true", "why": "不属于近两年，但属于私募并购整合、运营改善和再资本化退出的经典案例。"},
]
