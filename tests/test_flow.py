import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from git_helpers import git, make_repos
from test_dialogue import BODY

PROJECT=Path(__file__).resolve().parents[1]


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.workspace=Path(self.tmp.name).resolve()
        self.root=self.workspace/'.daily-diary-runtime'

    def tearDown(self): self.tmp.cleanup()

    def cli(self,*args,text=None,expected=0):
        p=subprocess.run([sys.executable,'-m','daily_diary.cli','--workspace',str(self.workspace),*args],
                         cwd=PROJECT,input=text,text=True,capture_output=True)
        self.assertEqual(p.returncode,expected,p.stderr)
        return json.loads(p.stdout if p.stdout else p.stderr)

    def prepare(self,day='2026-10-09'):
        return self.cli('prepare','--synthetic','--day',day,'--now','2026-10-10T08:00:00+00:00')

    def test_manual_day_end_to_end(self):
        material=self.prepare()
        self.assertEqual(len(material['materials']),1)
        checkout,remote=make_repos(self.root)
        self.cli('present','--day','2026-10-09','--kind','question','--id','q1',text='今日残したいことは？')
        self.cli('ack','--day','2026-10-09','--id','q1','--result','delivered')
        self.cli('answer','--day','2026-10-09','--human-reply-id','voice-a1',text='文字化された架空の回答')
        review=self.cli('present','--day','2026-10-09','--kind','review','--id','review-1',
                        text=BODY.replace('回答なし','Q1: 今日残したいことは？\nA1: 文字化された架空の回答'))
        self.cli('ack','--day','2026-10-09','--id','review-1','--result','delivered')
        displayed=self.cli('show','--day','2026-10-09')
        self.assertEqual(review['text'],displayed['text'])
        state=displayed['state']
        self.assertEqual(hashlib.sha256(displayed['text'].encode()).hexdigest(),state['body_sha256'])
        self.cli('approve','--day','2026-10-09','--review-id','review-1','--revision',str(state['revision']),
                 '--sha256',state['body_sha256'],'--human-reply-id','current-human-OK','--human-confirmed')
        saved=self.cli('save-local','--day','2026-10-09','--checkout',str(checkout),'--remote',str(remote))
        self.assertEqual(saved['status'],'保存済み')
        self.assertEqual(git(remote,'show','main:diary/2026/2026-10-09.md'),displayed['text'].encode())
        self.assertLessEqual(state['question_count'],2)
        self.assertEqual(displayed['text'].count('## '),5)
        self.assertIn('未観測',displayed['text'])
        for p in (self.root/'state').rglob('*'):
            if p.is_file():
                self.assertNotIn('小さな練習用の資料を整理したい',p.read_text())

    def test_offline_resume_and_unknown_delivery(self):
        self.prepare()
        self.cli('present','--day','2026-10-09','--kind','question','--id','q1',text='問い')
        self.cli('ack','--day','2026-10-09','--id','q1','--result','unknown')
        self.prepare()
        self.prepare('2026-10-10')
        old=self.cli('status','--day','2026-10-09')
        new=self.cli('status','--day','2026-10-10')
        self.assertEqual(old['question_count'],1)
        self.assertEqual(len(old['questions']),1)
        self.assertIsNone(old['approval'])
        self.assertEqual(new['question_count'],0)
        self.assertEqual(new['status'],'下書き')
        error=self.cli('present','--day','2026-10-09','--kind','question','--id','q1-again',text='問い',expected=2)
        self.assertEqual(error['error'],'presentation_already_pending')

    def test_cli_reports_failed_save_as_failure(self):
        self.prepare()
        checkout,remote=make_repos(self.root)
        # Missing review must fail without fabricating approval or writing a diary.
        self.cli('save-local','--day','2026-10-09','--checkout',str(checkout),'--remote',str(remote),expected=2)
        self.cli('present','--day','2026-10-09','--kind','review','--id','r',text=BODY)
        result=self.cli('save-local','--day','2026-10-09','--checkout',str(checkout),'--remote',str(remote),expected=2)
        self.assertEqual(result['reason_code'],'approval_required')
        self.assertFalse((checkout/'diary').exists())
