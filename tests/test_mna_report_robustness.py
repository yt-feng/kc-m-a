from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

from mna_case_reports import deepseek_client
from mna_case_reports.article_rules import (
    has_cjk_alnum_space,
    has_unformatted_quantity_number,
    normalize_text,
    section_concreteness_score,
)
from mna_case_reports.case_selection import CaseBrief, without_historical_duplicates
from mna_case_reports.fact_pack import FactPack, _fallback_deal_value, validate_fact_pack
from mna_case_reports.report_generation import _ensure_section_fact_anchors


class ArticleNormalizationTests(unittest.TestCase):
    def test_four_digit_quantities_receive_thousands_separator(self) -> None:
        normalized = normalize_text("公司支付 1100 万元并承接 2500 名员工")

        self.assertEqual(normalized, "公司支付1,100万元并承接2,500名员工")
        self.assertFalse(has_unformatted_quantity_number(normalized))

    def test_extended_cjk_spaces_are_removed(self) -> None:
        normalized = normalize_text("㐀 A股")

        self.assertEqual(normalized, "㐀A股")
        self.assertFalse(has_cjk_alnum_space(normalized))


class JsonResponseRetryTests(unittest.TestCase):
    @mock.patch.dict(os.environ, {"REPORT_JSON_RESPONSE_ATTEMPTS": "2"}, clear=False)
    @mock.patch("mna_case_reports.deepseek_client._post_chat")
    def test_empty_json_response_retries_original_task(self, post_chat: mock.Mock) -> None:
        post_chat.side_effect = ["", '{"ok": true}']

        result = deepseek_client.chat_json([{"role": "user", "content": "return JSON"}])

        self.assertEqual(result, {"ok": True})
        self.assertEqual(post_chat.call_count, 2)
        retry_messages = post_chat.call_args_list[1].args[0]
        self.assertIn("上一次响应为空或无法解析", retry_messages[-1]["content"])

    @mock.patch.dict(os.environ, {"REPORT_JSON_RESPONSE_ATTEMPTS": "2"}, clear=False)
    @mock.patch("mna_case_reports.deepseek_client._post_chat")
    def test_transient_request_error_retries(self, post_chat: mock.Mock) -> None:
        post_chat.side_effect = [requests.Timeout("timed out"), '{"ok": true}']

        result = deepseek_client.chat_json([{"role": "user", "content": "return JSON"}])

        self.assertEqual(result, {"ok": True})
        self.assertEqual(post_chat.call_count, 2)


class FactPackValidationTests(unittest.TestCase):
    def test_definition_page_text_is_not_accepted_as_deal_value(self) -> None:
        noisy = "60 2第一节释义在本报告书中，除非文中另有所指，下列词语具有如下含义：信息披露义务人、巨融科技"
        deal_value = _fallback_deal_value(noisy, [noisy], [{"extracted_text": noisy}])

        self.assertEqual(deal_value, "")

        pack = FactPack(
            case_name="巨融科技收购ST宏达",
            category="上市公司控股权并购",
            region="中国",
            acquirer="巨融科技有限公司",
            target="上海宏达新材料股份有限公司",
            deal_value=noisy,
            deal_status="2026年完成过户",
            buyer_rationale="公开公告披露收购方拟取得上市公司控制权。",
            seller_rationale="协议转让和过户安排已由公开公告披露。",
            financial_highlights="持股比例为24.5%。",
            timeline=["2026年完成过户"],
            key_numbers=["24.5%", "2026年", "1项控制权安排"],
            source_titles=["详式权益变动报告书"],
            source_refs=["[official] 公告 | CNINFO | https://static.cninfo.com.cn/example.pdf"],
            authoritative_source_count=1,
            analysis_angles=["控制权取得和治理安排"],
            validation_issues=[],
        )

        self.assertIn("缺少可引用的交易金额、估值或支付口径。", validate_fact_pack(pack))


class CandidateAndSectionTests(unittest.TestCase):
    def test_history_filter_removes_manifest_duplicate_before_counting(self) -> None:
        duplicate = CaseBrief(
            case_name="上海甲科技有限公司收购北京乙实业有限公司",
            category="上市公司控股权并购",
            region="中国",
            completed_year="2026",
            is_completed=True,
            acquirer="上海甲科技有限公司",
            target="北京乙实业有限公司",
            deal_status="2026年完成交割",
        )
        fresh = CaseBrief(
            case_name="深圳丙集团有限公司收购广州丁材料有限公司",
            category="上市公司控股权并购",
            region="中国",
            completed_year="2026",
            is_completed=True,
            acquirer="深圳丙集团有限公司",
            target="广州丁材料有限公司",
            deal_status="2026年完成交割",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifests = root / "_manifests"
            manifests.mkdir(parents=True)
            (manifests / "weekly.json").write_text(
                json.dumps([duplicate.to_dict()], ensure_ascii=False),
                encoding="utf-8",
            )

            kept, rejected = without_historical_duplicates([duplicate, fresh], root)

        self.assertEqual([brief.case_name for brief in kept], [fresh.case_name])
        self.assertEqual([brief.case_name for brief in rejected], [duplicate.case_name])

    def test_generic_section_receives_source_backed_fact_anchor(self) -> None:
        brief = CaseBrief(
            case_name="上海甲科技有限公司收购北京乙实业有限公司",
            category="上市公司控股权并购",
            region="中国",
            acquirer="上海甲科技有限公司",
            target="北京乙实业有限公司",
        )
        pack = FactPack(
            case_name=brief.case_name,
            category=brief.category,
            region=brief.region,
            acquirer=brief.acquirer,
            target=brief.target,
            deal_value="交易对价1,200万元",
            deal_status="2026年7月完成交割",
            buyer_rationale="取得控制权并整合相关业务。",
            seller_rationale="转让方根据协议收取现金对价。",
            financial_highlights="标的2025年收入2,500万元。",
            timeline=["2026年7月完成交割"],
            key_numbers=["持股比例60%"],
            source_titles=["完成交割公告"],
            source_refs=["[official] 完成交割公告"],
            authoritative_source_count=1,
            analysis_angles=["交易结构"],
            validation_issues=[],
        )
        article = {
            "sections": [
                {
                    "heading": "一、产业判断",
                    "paragraphs": ["产业协同需要结合公开披露继续核验。" * 20],
                }
            ]
        }

        _ensure_section_fact_anchors(article, brief, pack)
        section = article["sections"][0]
        text = str(section["heading"]) + "\n" + "\n".join(section["paragraphs"])

        self.assertGreaterEqual(section_concreteness_score(text, brief), 3)
        self.assertIn("1,200万元", text)


if __name__ == "__main__":
    unittest.main()
