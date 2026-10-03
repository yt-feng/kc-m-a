from __future__ import annotations
import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch
from mna_case_reports.article_rules import has_buyer_rationale
from mna_case_reports.case_selection import CaseBrief
from mna_case_reports.docx_writer import write_docx
from mna_case_reports.recovery import restore_accepted


class BatchRecoveryTests(unittest.TestCase):
    def fixture(self, root):
        output = root / 'artifact/case_reports'
        brief = CaseBrief(case_name='甲方收购乙方', category='上市公司_PE', region='中国',
                          is_domestic=True, is_completed=True, completed_year='2026',
                          deal_status='2026年已完成过户', acquirer='甲方', target='乙方')
        path = write_docx({'title':'甲方收购乙方：控制权安排', 'intro':'交易前状态',
                          'sections':[{'heading':'一、治理安排','paragraphs':['已披露协议转让安排。']}]},
                         category=brief.category, output_root=output, run_label='prior')
        manifests = output / '_manifests';manifests.mkdir()
        (manifests/'batch.json').write_text(json.dumps([asdict(brief)]))
        (manifests/'batch_progress.json').write_text(json.dumps({'requested_count':4,'files':[str(path.relative_to(root/'artifact'))]}))
        return root/'artifact', root/'case_reports', path

    def test_validated_prior_file_is_reused_exactly(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, output, source = self.fixture(Path(tmp))
            with patch('mna_case_reports.recovery.validate_docx',return_value={'ok':True}) as fmt, \
                 patch('mna_case_reports.recovery.validate_article',return_value=[]) as hard, \
                 patch('mna_case_reports.recovery.assess_quality',return_value=[]) as quality:
                files, briefs = restore_accepted(root,output,requested_count=4)
            self.assertEqual(Path(files[0]).read_bytes(),source.read_bytes())
            self.assertTrue(briefs[0].is_domestic)
            fmt.assert_called_once();hard.assert_called_once();quality.assert_called_once()

    def test_quality_failure_prevents_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, output, source = self.fixture(Path(tmp))
            with patch('mna_case_reports.recovery.validate_docx',return_value={'ok':True}), \
                 patch('mna_case_reports.recovery.validate_article',return_value=[]), \
                 patch('mna_case_reports.recovery.assess_quality',return_value=['depth missing']):
                with self.assertRaisesRegex(ValueError,'quality gates'):
                    restore_accepted(root,output,requested_count=4)
            self.assertFalse(output.exists())

    def test_missing_file_count_change_and_path_escape_block_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, output, source = self.fixture(Path(tmp))
            with self.assertRaisesRegex(ValueError,'original requested'):
                restore_accepted(root,output,requested_count=5)
            source.unlink()
            with self.assertRaisesRegex(ValueError,'Missing'):
                restore_accepted(root,output,requested_count=4)
            progress=next(root.rglob('*_progress.json'))
            progress.write_text(json.dumps({'requested_count':4,'files':['case_reports/../../escape.docx']}))
            with self.assertRaisesRegex(ValueError,'out-of-scope'):
                restore_accepted(root,output,requested_count=4)

    def test_minority_transfer_rationale_and_disclosure_boundary(self):
        rationale='受让方的投资目的并非取得公司控制权，而是依据已披露的协议转让安排取得5.04%的股权。因而，其后续治理参与受到现有表决权比例和十二个月锁定期约束，因此应当结合已披露条款分析其购买理由。'
        self.assertTrue(has_buyer_rationale(rationale))
        boundary='公开资料未披露受让方的主观动机。已披露协议转让5.04%股份以及十二个月锁定期，意味着持股和退出受到条款约束，因此本文只分析该客观安排而不把产业整合目的当成事实。'
        self.assertTrue(has_buyer_rationale(boundary))
        self.assertFalse(has_buyer_rationale('受让方动机未披露。'))
        self.assertFalse(has_buyer_rationale('公司完成5.04%的股份转让。' * 10))


if __name__ == '__main__': unittest.main()
