import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

from daily_diary.core import Collection, Config, DiaryError, body_hash, load_state
from daily_diary.cli import acknowledge, confirm, prepare, present, read_review
from daily_diary.git_save import save_day
import daily_diary.git_save as saver
from git_helpers import git, make_repos
from test_dialogue import BODY


class SaveTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name).resolve()/'.daily-diary-runtime'
        self.checkout,self.remote=make_repos(self.root)
        self.config=Config(self.root,checkout=self.checkout,repo_name=str(self.remote))
        self.day=date(2026,10,9)
        self.now=datetime(2026,10,9,8,tzinfo=timezone.utc)
        prepare(self.config,Collection([],datetime(2026,10,8,15,tzinfo=timezone.utc),
                                       self.now,self.now,'対象ログなし',[]))
        self.path='diary/2026/2026-10-09.md'

    def tearDown(self): self.tmp.cleanup()

    def approved(self, text=BODY, pid='review', reply='ok'):
        state=present(self.config,self.day,text,pid,'review')
        acknowledge(self.config,self.day,pid,True)
        confirm(self.config,self.day,state.revision,state.body_sha256,reply,self.now,
                review_id=pid,human_confirmed=True)
        return load_state(self.root,self.day),read_review(self.config,self.day)

    def test_only_approved_bytes_saved(self):
        state=load_state(self.root,self.day)
        self.assertEqual(save_day(self.config,state,b'fake').reason_code,'approval_required')
        state,body=self.approved()
        self.assertEqual(save_day(self.config,state,body+b'changed').reason_code,'approval_required')
        result=save_day(self.config,state,body)
        self.assertEqual(result.status,'保存済み')
        self.assertEqual(git(self.remote,'show','main:'+self.path),body)
        changed=git(self.checkout,'diff-tree','--no-commit-id','--name-only','-r','HEAD').decode().strip()
        self.assertEqual(changed,self.path)
        before=git(self.remote,'rev-list','--count','main')
        self.assertEqual(save_day(self.config,load_state(self.root,self.day),body).status,'保存済み')
        self.assertEqual(git(self.remote,'rev-list','--count','main'),before)

    def test_local_dirty_file_kept(self):
        state,body=self.approved()
        (self.checkout/'hand-edit').write_text('keep')
        result=save_day(self.config,state,body)
        self.assertEqual(result.reason_code,'checkout_dirty')
        self.assertEqual((self.checkout/'hand-edit').read_text(),'keep')
        self.assertFalse((self.checkout/self.path).exists())

    def test_remote_hand_edit_blocks_overwrite(self):
        state,body=self.approved()
        target=self.checkout/self.path
        target.parent.mkdir(parents=True)
        target.write_text('manual content\n')
        git(self.checkout,'add',self.path)
        git(self.checkout,'commit','-m','manual')
        git(self.checkout,'push','origin','main')
        result=save_day(self.config,state,body)
        self.assertEqual(result.reason_code,'target_changed')
        self.assertEqual(git(self.remote,'show','main:'+self.path),b'manual content\n')

    def test_retry_after_unknown_push(self):
        state,body=self.approved()
        original=saver._git
        def lost(cwd,*args,**kw):
            result=original(cwd,*args,**kw)
            if args and args[0]=='push': raise DiaryError('git_failed')
            return result
        with patch.object(saver,'_git',side_effect=lost):
            first=save_day(self.config,state,body)
        self.assertEqual(first.status,'保存待ち')
        self.assertEqual(first.reason_code,'push_result_unknown')
        before=git(self.remote,'rev-list','--count','main')
        result=save_day(self.config,load_state(self.root,self.day),body)
        self.assertEqual(result.status,'保存済み')
        self.assertEqual(git(self.remote,'rev-list','--count','main'),before)

    def test_only_local_remote_and_no_symlink(self):
        state,body=self.approved()
        self.config.repo_name='https://github.com/synthetic/repo.git'
        self.assertEqual(save_day(self.config,state,body).reason_code,'local_remote_required')
        self.config.repo_name=str(self.remote)
        (self.checkout/'diary').symlink_to(self.root,target_is_directory=True)
        self.assertEqual(save_day(self.config,state,body).reason_code,'symlink_path')

    def test_reapproved_update(self):
        state,body=self.approved()
        self.assertEqual(save_day(self.config,state,body).status,'保存済み')
        new,updated=self.approved(BODY.replace('記載なし','本人の補足'),'review-2','ok-2')
        self.assertEqual(save_day(self.config,new,updated).status,'保存済み')
        self.assertEqual(git(self.remote,'show','main:'+self.path),updated)

    def test_local_commit_before_failed_push_is_reused(self):
        state,body=self.approved()
        original=saver._git
        def fail_push(cwd,*args,**kw):
            if args and args[0]=='push': raise DiaryError('git_failed')
            return original(cwd,*args,**kw)
        with patch.object(saver,'_git',side_effect=fail_push):
            first=save_day(self.config,state,body)
        self.assertEqual(first.status,'保存待ち')
        before=git(self.checkout,'rev-list','--count','HEAD')
        self.assertEqual(save_day(self.config,load_state(self.root,self.day),body).status,'保存済み')
        self.assertEqual(git(self.checkout,'rev-list','--count','HEAD'),before)

    def test_attributes_filters_are_not_executed(self):
        state,body=self.approved()
        (self.checkout/'.git/info/attributes').write_text('*.md filter=unsafe\n')
        git(self.checkout,'config','filter.unsafe.clean','touch MUST_NOT_EXIST')
        result=save_day(self.config,state,body)
        self.assertEqual(result.reason_code,'unsafe_git_config')
        self.assertFalse((self.checkout/'MUST_NOT_EXIST').exists())

    def test_symlinked_git_metadata_never_changes_outside_refs(self):
        state,body=self.approved()
        for metadata in (self.checkout/'.git/refs', self.remote/'refs'):
            outside=Path(self.tmp.name)/('outside-'+metadata.parent.name)
            metadata.rename(outside)
            metadata.symlink_to(outside,target_is_directory=True)
            before={str(p.relative_to(outside)):p.read_bytes() for p in outside.rglob('*') if p.is_file()}
            result=save_day(self.config,state,body)
            self.assertEqual(result.reason_code,'symlink_path')
            self.assertEqual(before,{str(p.relative_to(outside)):p.read_bytes() for p in outside.rglob('*') if p.is_file()})
            metadata.unlink()
            outside.rename(metadata)

    def test_redirected_worktree_stops_before_mutation(self):
        state,body=self.approved()
        outside=Path(self.tmp.name)/'outside-worktree'
        outside.mkdir()
        (outside/'.gitignore').write_bytes((self.checkout/'.gitignore').read_bytes())
        (self.checkout/'remote-file').write_text('remote update')
        git(self.checkout,'add','remote-file')
        git(self.checkout,'commit','-m','remote update')
        git(self.checkout,'push','origin','main')
        git(self.checkout,'reset','--hard','HEAD~1')
        git(self.checkout,'config','core.worktree',str(outside))
        before={p.name:p.read_bytes() for p in outside.iterdir()}
        result=save_day(self.config,state,body)
        self.assertEqual(result.reason_code,'unsafe_git_config')
        self.assertEqual(before,{p.name:p.read_bytes() for p in outside.iterdir()})
