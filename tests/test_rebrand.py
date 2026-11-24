"""Revilens naming migration; synthetic archives and private disposable state."""
import hashlib
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
import zipfile

import test_product
from deepseek_reviewer import config, distribution
from deepseek_reviewer.runner import AgentStopped


class RebrandTests(unittest.TestCase):
    setUp = test_product.PortableTests.setUp
    tearDown = test_product.PortableTests.tearDown

    def archive(self, version):
        path = self.root / (version + '.pyz')
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr('deepseek_reviewer/release.json', json.dumps({'version': version}))
            z.writestr('__main__.py', 'print(' + repr(version) + ')')
        return path, version, hashlib.sha256(path.read_bytes()).hexdigest()

    def legacy_skill(self):
        target = config.home() / '.codex/skills/deepseek-reviewer'
        (target / 'agents').mkdir(parents=True, mode=0o700)
        old = {'SKILL.md': b'name: deepseek-reviewer\n', 'agents/openai.yaml': b'legacy managed UI\n'}
        for name, body in old.items(): config.atomic_write(target / name, body)
        config.atomic_write(config.app_home() / 'skill-installation.json', json.dumps(
            {'version': '1.4.1', 'hashes': distribution._hashes(old)}).encode())
        return target, old

    def test_two_commands_share_activation_and_preserve_registration(self):
        before = (config.app_home() / 'runtime.json').read_bytes()
        first = distribution.install_archive(*self.archive('1.4.1'))
        current = distribution.install_archive(*self.archive('1.5.0'))
        self.assertEqual(Path(current['command']).name, 'revilens')
        for command in ['revilens', 'deepseek-reviewer']:
            self.assertEqual(subprocess.check_output([str(config.home() / '.local/bin' / command)]).strip(), b'1.5.0')
        distribution.rollback()
        for command in ['revilens', 'deepseek-reviewer']:
            self.assertEqual(subprocess.check_output([str(config.home() / '.local/bin' / command)]).strip(), b'1.4.1')
        self.assertEqual((config.app_home() / 'runtime.json').read_bytes(), before)
        self.assertEqual(config.SERVICE, 'io.github.liyunhui0707.deepseek-reviewer')
        self.assertEqual(config.ACCOUNT, 'deepseek-reviewer')

    def test_unmanaged_new_command_is_not_overwritten_or_activated(self):
        distribution.install_archive(*self.archive('1.4.1'))
        target = config.home() / '.local/bin/revilens'
        target.write_text('user-owned command')
        before = (config.app_home() / 'installation.json').read_bytes()
        with self.assertRaisesRegex(AgentStopped, 'existing_unmanaged_command'):
            distribution.install_archive(*self.archive('1.5.0'))
        self.assertEqual(target.read_text(), 'user-owned command')
        self.assertEqual((config.app_home() / 'installation.json').read_bytes(), before)

    def test_new_command_symlink_stops_before_touching_target(self):
        target = config.home() / '.local/bin/revilens'
        target.parent.mkdir(parents=True)
        outside = self.root / 'synthetic-unrelated-command'
        outside.write_text('protected synthetic content')
        target.symlink_to(outside)
        with self.assertRaisesRegex(AgentStopped, 'unsafe_install_target'):
            distribution.install_archive(*self.archive('1.5.0'))
        self.assertEqual(outside.read_text(), 'protected synthetic content')
        self.assertFalse((config.app_home() / 'installation.json').exists())

    def test_old_updater_requires_explicit_installer_before_new_skill(self):
        args = self.archive('1.5.0')
        distribution.install_archive(*args)
        (config.home() / '.local/bin/revilens').rename(self.root / 'saved-synthetic-launcher')
        self.assertEqual(distribution.skill_status()['status'], 'COMMAND_MIGRATION_REQUIRED')
        with self.assertRaisesRegex(AgentStopped, 'revilens_command_missing_run_legacy_install'):
            distribution.install_skill(update=True)
        self.assertFalse((config.home() / '.codex/skills/revilens').exists())
        distribution.install_archive(*args)
        desired = {'SKILL.md': b'name: revilens\n', 'agents/openai.yaml': b'Revilens UI\n'}
        with patch.object(distribution, 'bundled_skill', return_value=desired):
            distribution.install_skill()
            self.assertEqual(distribution.skill_status()['status'], 'CURRENT')

    def test_managed_legacy_skill_migrates_once_and_restore_preserves_name(self):
        legacy, old = self.legacy_skill()
        desired = {'SKILL.md': b'name: revilens\n', 'agents/openai.yaml': b'Revilens UI\n'}
        with patch.object(distribution, 'bundled_skill', return_value=desired):
            self.assertEqual(distribution.skill_status()['status'], 'UPDATE_AVAILABLE')
            result = distribution.install_skill(update=True)
            target = config.home() / '.codex/skills/revilens'
            self.assertEqual(result['installed'], str(target))
            self.assertFalse(legacy.exists())
            self.assertEqual(distribution.skill_status()['status'], 'CURRENT')
            self.assertEqual((target / 'SKILL.md').read_bytes(), desired['SKILL.md'])
            distribution.restore_skill()
            self.assertTrue(legacy.is_dir())
            self.assertFalse(target.exists())
            self.assertEqual((legacy / 'SKILL.md').read_bytes(), old['SKILL.md'])

    def test_customized_or_duplicate_skills_stop_without_moving(self):
        legacy, _ = self.legacy_skill()
        (legacy / 'SKILL.md').write_text('user customization')
        desired = {'SKILL.md': b'name: revilens\n', 'agents/openai.yaml': b'Revilens UI\n'}
        with patch.object(distribution, 'bundled_skill', return_value=desired):
            with self.assertRaisesRegex(AgentStopped, 'skill_modified_or_unmanaged'):
                distribution.install_skill(update=True)
            self.assertEqual((legacy / 'SKILL.md').read_text(), 'user customization')
            (config.home() / '.codex/skills/revilens').mkdir()
            with self.assertRaisesRegex(AgentStopped, 'skill_modified_or_unmanaged'):
                distribution.install_skill(update=True)

    def test_alternate_discovery_roots_are_reported_not_modified(self):
        alternate = config.home() / '.agents/skills/deepseek-reviewer'
        alternate.mkdir(parents=True)
        (alternate / 'SKILL.md').write_text('synthetic alternate Skill')
        desired = {'SKILL.md': b'name: revilens\n', 'agents/openai.yaml': b'Revilens UI\n'}
        with patch.object(distribution, 'bundled_skill', return_value=desired):
            self.assertIn('~/.agents/skills/deepseek-reviewer', distribution.skill_status()['conflicting_skills'])
        self.assertEqual((alternate / 'SKILL.md').read_text(), 'synthetic alternate Skill')

    def test_new_manifest_is_exact_and_legacy_contract_remains_build_output(self):
        value = {'file': 'revilens.pyz', 'version': '1.5.0', 'sha256': 'a' * 64}
        with patch.object(distribution, 'fetch', return_value=json.dumps(value).encode()) as fetch:
            self.assertEqual(distribution.latest(), value)
            self.assertEqual(fetch.call_args.args[0], 'https://github.com/liyunhui0707/revilens/releases/latest/download/revilens-release.json')
        for filename in ['../revilens.pyz', 'other.pyz']:
            with patch.object(distribution, 'fetch', return_value=json.dumps({**value, 'file': filename}).encode()):
                with self.assertRaisesRegex(AgentStopped, 'invalid_release_manifest'):
                    distribution.latest()
