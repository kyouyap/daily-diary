import json
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import tomllib
import unittest


REPOSITORY = Path(__file__).resolve().parents[2]


class ScreentimeSetupTests(unittest.TestCase):
    def test_generates_portable_paths_and_refuses_to_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve() / 'custom installation'
            config = base / 'configuration/settings.toml'
            agent = base / 'agents/test.plist'
            command = [sys.executable, '-B', '-m', 'scripts.setup_screentime',
                       '--prefix', str(base / 'app'), '--python', sys.executable,
                       '--config', str(config), '--database', str(base / 'data/usage.db'),
                       '--logs', str(base / 'logs'), '--launch-agent', str(agent)]
            result = subprocess.run(command, cwd=REPOSITORY, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertEqual(json.loads(result.stdout)['status'], 'prepared')
            settings = tomllib.loads(config.read_text())
            self.assertEqual(settings['db_path'], str(base / 'data/usage.db'))
            self.assertFalse(settings['lookup']['enabled'])
            plist = plistlib.loads(agent.read_bytes())
            self.assertEqual(plist['ProgramArguments'], [sys.executable, '-I', '-B',
                str(base / 'app/collector/local_screentime.py'), 'collect', '--config', str(config)])
            self.assertEqual(plist['StandardOutPath'], str(base / 'logs/stdout.log'))
            self.assertEqual(plist['StartInterval'], 900)
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)
            config.write_text('user-edited-settings')
            result = subprocess.run(command, cwd=REPOSITORY, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)['issue'], 'destination_exists')
            self.assertEqual(config.read_text(), 'user-edited-settings')

    def test_rejects_database_collision_before_creating_anything(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            result = subprocess.run([sys.executable, '-B', '-m', 'scripts.setup_screentime',
                '--prefix', str(base / 'app'), '--python', sys.executable,
                '--config', str(base / 'same'), '--database', str(base / 'same'),
                '--logs', str(base / 'logs'), '--launch-agent', str(base / 'agent.plist')],
                cwd=REPOSITORY, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertFalse((base / 'app').exists())
            self.assertFalse((base / 'same').exists())
