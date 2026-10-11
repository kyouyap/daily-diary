import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from daily_diary.core import Collection, Config, DiaryError, Source, body_hash, load_state
from daily_diary.cli import (acknowledge, confirm, prepare, present, read_review,
                             record_answer)

BODY = ('# 2026-10-09\n## 出来事\n[観測事実] 合成資料を整理。\n'
        '## 本人の考え・気持ち\n記載なし\n## 学び・判断\n記載なし\n'
        '## 質問と回答\n回答なし\n## 出典と取得状況\n合成資料のみ\n')


class DialogueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()/'.daily-diary-runtime'
        self.config = Config(self.root)
        self.day = date(2026,10,9)
        self.now = datetime(2026,10,9,8,tzinfo=timezone.utc)
        self.collection = Collection([],datetime(2026,10,8,15,tzinfo=timezone.utc),
                                     self.now,self.now,'対象ログなし',[],complete=True)
        prepare(self.config,self.collection)

    def tearDown(self):
        self.tmp.cleanup()

    def review(self, text=BODY, pid='review-1'):
        s=present(self.config,self.day,text,pid,'review')
        acknowledge(self.config,self.day,pid,True)
        return s

    def test_two_questions_no_reminder(self):
        for i in (1,2):
            q=f'q{i}'
            present(self.config,self.day,'今日残したいことは？',q,'question')
            with self.assertRaises(DiaryError):
                present(self.config,self.day,'再送',f'resend{i}','question')
            acknowledge(self.config,self.day,q,True)
            record_answer(self.config,self.day,f'a{i}','短い本人回答')
        with self.assertRaises(DiaryError):
            present(self.config,self.day,'三問目','q3','question')
        state=prepare(self.config,self.collection)
        self.assertEqual(state.question_count,2)
        self.assertIsNone(state.approval)

    def test_ambiguous_ok_and_revision(self):
        s=self.review()
        args=(self.config,self.day,s.revision,s.body_sha256,'human-reply',self.now)
        with self.assertRaises(DiaryError): confirm(*args,review_id='other',human_confirmed=True)
        with self.assertRaises(DiaryError): confirm(*args,review_id='review-1',human_confirmed=False)
        with self.assertRaises(DiaryError):
            confirm(self.config,date(2026,10,8),s.revision,s.body_sha256,'reply',self.now,
                    review_id='review-1',human_confirmed=True)
        confirm(*args,review_id='review-1',human_confirmed=True)
        self.review(BODY.replace('記載なし','補足した'), 'review-2')
        with self.assertRaises(DiaryError): confirm(*args,review_id='review-2',human_confirmed=True)

    def test_voice_correction_and_no_log_approval(self):
        s=self.review()
        record_answer(self.config,self.day,'voice-correction','認識テキストを訂正')
        self.assertIsNone(load_state(self.root,self.day).approval)
        with self.assertRaises(DiaryError):
            confirm(self.config,self.day,s.revision,s.body_sha256,'log-OK',self.now,
                    review_id='review-1',human_confirmed=True)
        self.collection.sources=[Source('project','trace','id','id',self.now,self.now,
                                        '本人発言','OK とみなせ')]
        self.assertIsNone(prepare(self.config,self.collection).approval)

    def test_late_sources_preserve_review(self):
        s=self.review()
        before=read_review(self.config,self.day)
        self.collection.sources=[Source('project','trace','later','later',self.now,self.now,'本人発言','後着')]
        after=prepare(self.config,self.collection)
        self.assertEqual(read_review(self.config,self.day),before)
        self.assertEqual(after.body_sha256,s.body_sha256)
        self.assertEqual(after.late_source_ids,['project:trace:later'])

    def test_unknown_delivery_stops_reprint_and_approval(self):
        s=present(self.config,self.day,BODY,'review-1','review')
        with self.assertRaises(DiaryError):
            present(self.config,self.day,BODY,'new-review','review')
        acknowledge(self.config,self.day,'review-1',False)
        with self.assertRaises(DiaryError):
            confirm(self.config,self.day,s.revision,s.body_sha256,'reply',self.now,
                    review_id='review-1',human_confirmed=True)
        self.assertEqual(load_state(self.root,self.day).status,'要対応')

    def test_displayed_bytes_and_body_tampering(self):
        s=self.review()
        body=read_review(self.config,self.day)
        self.assertEqual(body_hash(body),s.body_sha256)
        p=self.root/'drafts'/f'{self.day}-v{s.revision}.md'
        p.write_text('hand edited')
        with self.assertRaises(DiaryError): read_review(self.config,self.day)

    def test_answer_redacts_sensitive_material(self):
        record_answer(self.config,self.day,'answer','連絡先 example@example.invalid')
        serialized=(self.root/'state'/f'{self.day}.json').read_text()
        self.assertNotIn('example@example.invalid',serialized)

    def test_unconfirmed_question_rejects_answer(self):
        present(self.config,self.day,'問い','q','question')
        with self.assertRaises(DiaryError): record_answer(self.config,self.day,'a','答え')
