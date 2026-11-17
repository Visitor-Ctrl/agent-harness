"""Offline policy regressions from both input suites, with generic fixtures.

Host service restrictions, vendor tenancy rules and prose typography were
intentionally replaced by configurable path/command policy, not copied over.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from agent_harness.guard import context, guard, install, rules, shparse

HARNESS = Path(__file__).resolve().parents[1]


class GuardPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='guard-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / 'home'
        self.home.mkdir()
        self.policy = rules.Policy(root=str(self.root), state_dir=str(self.root / 'state'))
        self.ctx = rules.Context(str(self.root), self.policy, str(self.home))

    def check_commands(self, blocked=(), allowed=()):
        for expected, commands in [(True, blocked), (False, allowed)]:
            for command in commands:
                with self.subTest(command=command):
                    findings = rules.evaluate_bash(command, self.ctx)
                    self.assertEqual(any(f.level == 'block' for f in findings), expected, findings)

    def test_shared_secret_exposure_regressions(self):
        self.check_commands(blocked=[
            'cat ~/.claude/.credentials.json', 'cat ~/.codex/auth.json | jq .',
            'head -5 .env', 'grep TOKEN .env', 'printenv', 'env | grep -i token',
            'gh auth token', 'cat ~/.ssh/id_ed25519',
            'python3 -c "print(open(\'.env\').read())"',
            'echo $SERVICE_API_TOKEN', 'echo "$(gh auth token)"',
            'less ~/.oci/private.pem', 'cat api/.dev.vars',
            "ssh example.com 'cat ~/.codex/auth.json'", 'printenv SERVICE_TOKEN',
            'base64 ~/.ssh/id_ed25519', 'export -p', 'cat ~/.oci/*',
            "bash -c 'cat ~/.git-credentials'", 'cat <<< "$SERVICE_API_KEY"',
            'cat <(gh auth token)', 'gh auth token | tee /dev/stderr | wc -c',
            'cut -d: -f1 .env', 'cat .env | tee /tmp/output',
            'printf "%s" `gh auth token`', 'dd if=.env', 'cp .env /dev/stdout',
            'gh auth token | tee /dev/stderr > /tmp/sink',
            'cat <<EOF\n$(gh auth token)\nEOF',
            'cat <<EOF\n$SERVICE_TOKEN\nEOF',
        ], allowed=[
            'gh auth token | gh auth login --with-token',
            'TOKEN=$(gh auth token) curl -H "Authorization: Bearer $TOKEN" https://example.com',
            'wc -c ~/.codex/auth.json', 'grep -c TOKEN .env', 'cut -d= -f1 .env',
            'jq keys ~/.codex/auth.json', 'cat .env.example', 'ls -la ~/.ssh',
            'cat ~/.ssh/id_ed25519.pub', 'ssh-keygen -lf ~/.ssh/id_ed25519',
            'printenv PATH', 'echo ${#SERVICE_TOKEN}', 'sha256sum private.pem',
            'set -a; . ./.env; set +a; npm run build', 'cat ~/.ssh/config',
            'echo "$(gh auth token)" | gh auth login --with-token',
            'cat README.md | head -20', 'python3 tools/check.py ~/.codex/auth.json',
            'test -s ~/.codex/auth.json && echo present', 'env FOO=1 npm test',
            '[ -n "$SERVICE_TOKEN" ] && echo set', 'cat .env > /tmp/backup',
            'openssl x509 -in private.pem -noout -subject -enddate',
            "bash -c 'gh auth token' | gh auth login --with-token",
            'diff <(sort a.txt) <(sort b.txt)',
        ])

    def test_credential_store_reader_and_runner_regressions(self):
        script = 'tools/sign-out.sh'
        sandbox = self.root / 'sandbox'
        sandbox.mkdir()
        self.check_commands(blocked=[
            'rm -rf ~/.config/gh', 'mv ~/.config/gh /tmp/backup',
            'echo x > ~/.codex/auth.json', 'gh auth logout',
            f'cat {script} | bash', f'git show origin/x:{script} | sh',
            f'cat {script} | sudo bash', f'sed -n p {script} | bash -s -- --yes',
            f'grep -v x {script} > /tmp/x.sh && bash /tmp/x.sh',
            f'sed -i s/a/b/ {script}', f'sudo -u root bash {script}',
            f'heavy-lock bash {script}', f'nice bash {script}',
            f'./{script} --yes', f'bash < {script}',
            f'cat {script} | tee /tmp/y.sh',
            f"git show origin/x:{script} | awk '{{system($0)}}'",
            f'cat {script} && python3 /tmp/runner.py',
            'dd if=/tmp/x of=~/.config/gh/hosts.yml',
            'tee ~/.ssh/config /tmp/copy',
            'rm -rf ~', 'rm -rf $HOME/', 'cp -t ~/.ssh /tmp/x /tmp/y',
        ], allowed=[
            f'git show origin/x:{script}', f'grep -n sample {script}',
            f'sed -n 1,20p {script}', f'git show origin/x:{script} | grep -c sample',
            'rm -rf /tmp/test/.config/gh', 'ls ~/.oci', 'git status',
            f'HOME={sandbox} bash {script}',
            f'GH_CONFIG_DIR={sandbox} gh auth logout',
        ])

    def test_guard_self_protection_and_writes(self):
        self.check_commands(blocked=[
            "sed -i 's/a/b/' ~/.claude/settings.json", '> ~/.claude/settings.json',
            'echo ok > ~/.claude/settings.json > /tmp/log', 'rm -rf /opt/agent-harness',
            "python3 - <<'EOF'\nopen('/opt/agent-harness/policy', 'w').write('{}')\nEOF",
            'echo x | sudo tee /etc/sudoers.d/example',
            'find ~/.ssh -type f -delete', 'cp /tmp/x ~/.codex/auth.json',
        ], allowed=['cat ~/.claude/settings.json', 'rm -rf node_modules dist', 'find . -name "*.pyc" -delete'])
        for tool in ('Write', 'Edit', 'MultiEdit', 'NotebookEdit'):
            self.assertTrue(rules.evaluate_file_tool(tool, {'file_path': '/opt/agent-harness/x'}, self.ctx))
        self.assertTrue(rules.evaluate_file_tool('Read', {'file_path': '.env'}, self.ctx))
        self.assertTrue(rules.evaluate_file_tool('Grep', {'path': '.', 'glob': '.env', 'output_mode': 'content'}, self.ctx))
        self.assertFalse(rules.evaluate_file_tool('Grep', {'path': '.env', 'output_mode': 'count'}, self.ctx))
        self.assertFalse(rules.evaluate_file_tool('Read', {'file_path': 'public.pub'}, self.ctx))

    def make_repo(self):
        repo = self.root / 'repo'
        repo.mkdir()
        self.git(repo, 'init', '-q', '-b', 'main')
        self.git(repo, '-c', 'user.name=Example', '-c', 'user.email=dev@example.com', 'commit', '-q', '--allow-empty', '-m', 'Initial')
        self.git(repo, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
        return repo

    def git(self, cwd, *args):
        return subprocess.run(['git', '-C', str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()

    def test_git_regressions_and_current_directory(self):
        repo = self.make_repo()
        self.ctx.cwd = str(repo)
        self.check_commands(blocked=[
            'git add -A', 'git add .', 'git add --all', 'FOO=bar git add -A',
            'git commit --no-verify -m wip', 'git commit -nm wip',
            'git -c core.hooksPath=/dev/null commit -m x',
            'git push --force origin feature', 'git push origin main',
            'git push', 'git push origin HEAD', 'git push --all origin',
            'git push origin +feature:main', 'git reset --hard origin/main',
            'git push --repo origin feature:main', 'git clean -fdx',
            'git branch -D main', 'git update-ref -d refs/heads/main', 'git add ./', 'git add src/..',
        ], allowed=[
            'git add src/a.py docs/b.md', 'git status',
            "git commit -m 'fix the -n flag parsing'", 'git push --dry-run origin main',
        ])
        self.git(repo, 'switch', '-q', '-c', 'feature')
        self.check_commands(allowed=['git push -u origin feature', 'git push --force-with-lease origin feature', 'git reset --hard HEAD~1'])
        self.ctx.cwd = str(self.root)
        self.check_commands(blocked=[f'cd {repo} && git push origin main', f'git -C {repo} push origin main'])

    def test_deploy_checks_clean_tree_and_ref(self):
        repo = self.make_repo()
        self.ctx.cwd = str(repo)
        self.check_commands(allowed=['npx wrangler deploy', 'wrangler --env production deploy'])
        self.check_commands(blocked=['touch new-file && wrangler deploy', 'git switch feature && wrangler deploy'])
        (repo / 'untracked').write_text('content')
        self.check_commands(blocked=['npx wrangler deploy', 'wrangler --env production deploy'])
        (repo / 'untracked').unlink()
        self.git(repo, 'switch', '-q', '-c', 'feature')
        self.git(repo, '-c', 'user.name=Example', '-c', 'user.email=dev@example.com', 'commit', '-q', '--allow-empty', '-m', 'Feature')
        self.check_commands(blocked=['wrangler deploy'])
        self.ctx.cwd = str(self.root)
        self.check_commands(blocked=['wrangler deploy'])

    def test_dispatch_model(self):
        self.check_commands(blocked=['dispatch start task', 'dispatch task --model', 'dispatch task --model=', 'dispatch task -- --model example'],
                            allowed=['dispatch start task --model example', 'dispatch task --model=example', 'dispatch status'])

    def test_policy_configuration_and_path_resolution(self):
        file = self.root / 'policy.toml'
        file.write_text('[guard]\nprofile="sample"\nguarded_paths=["${HARNESS_ROOT}/managed"]\ndenied_paths=["/srv/projects/other"]\n')
        self.ctx.policy = rules.Policy.load(file)
        self.ctx.policy.root = str(self.root)
        self.check_commands(blocked=['cat /srv/projects/example/../other/ACCESS.md', f'echo x > {self.root}/managed/x', f'echo x > {file}'])
        link = self.root / 'alias'
        link.symlink_to(self.home / '.ssh', target_is_directory=True)
        self.check_commands(blocked=[f'rm -rf {link}/config'])
        self.assertTrue(rules.evaluate_file_tool('Glob', {'path': '/srv/projects/other'}, self.ctx))
        self.assertTrue(rules.evaluate_file_tool('Glob', {'pattern': '/srv/projects/other/**'}, self.ctx))
        file.write_text('[guard]\nrequire_model="yes"\n')
        with self.assertRaises(ValueError):
            rules.Policy.load(file)

    def test_shell_parser_pipelines_wrappers_nested_and_malformed(self):
        self.check_commands(blocked=[
            'true && sudo -u root bash -c "git add -A"',
            'echo $(git add -A)', 'echo `git add -A`',
            'cat <(git add -A)', "bash <<'EOF'\ngit add -A\nEOF",
            'env FOO=bar timeout 5 nice git add -A',
            'echo "unterminated', 'echo $(echo x',
            "bash -c $'git\\x20add\\x20-A'", 'CMD=git; $CMD add -A',
        ])
        pipe = shparse.parse('echo one | grep one && cat < input > output')
        self.assertEqual([c.name for c in pipe[0]], ['echo', 'grep'])
        self.assertEqual(pipe[1][0].writes(), ['output'])
        self.assertEqual(str(pipe[1][0].inputs()[0]), 'input')


class GuardIntegrationTests(unittest.TestCase):
    setUp = GuardPolicyTests.setUp
    make_repo = GuardPolicyTests.make_repo
    git = GuardPolicyTests.git
    def run_hook(self, payload=None, raw=None):
        policy = self.root / 'guard.toml'
        policy.write_text(f'[guard]\nroot={json.dumps(str(self.root))}\nstate_dir={json.dumps(str(self.root / "audit"))}\n')
        env = dict(os.environ, HARNESS_GUARD_CONFIG=str(policy))
        return subprocess.run([str(HARNESS / 'bin/guard-hook')], input=raw if raw is not None else json.dumps(payload),
                              text=True, capture_output=True, env=env, cwd=self.root)

    def test_hook_protocol_and_privacy(self):
        event = {'hook_event_name': 'PreToolUse', 'tool_name': 'Bash', 'tool_input': {'command': 'git add -A'}}
        result = self.run_hook(event)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('git-add-all', result.stderr)
        event['tool_input']['command'] = 'git status'
        self.assertEqual(self.run_hook(event).stdout, '')
        self.assertEqual(self.run_hook(raw='{broken').returncode, 2)
        event['tool_name'] = 'Agent'
        event['tool_input'] = {'prompt': 'private prompt'}
        self.assertEqual(self.run_hook(event).returncode, 2)
        event['tool_input']['model'] = 'example'
        self.assertEqual(self.run_hook(event).returncode, 0)
        for name in guard.EVENTS:
            result = self.run_hook({'hook_event_name': name, 'tool_name': 'Bash', 'tool_input': {'command': 'ADMIN_TASK=example admin-example status'}, 'session_id': 'private identifier'})
            self.assertEqual(result.returncode, 0, (name, result.stderr))
        log = (self.root / 'audit/events.jsonl').read_text()
        self.assertNotIn('private', log)
        self.assertNotIn('admin-example', log)
        self.assertEqual((self.root / 'audit/events.jsonl').stat().st_mode & 0o777, 0o600)

    def test_stop_blocks_once_then_reports(self):
        repo = self.make_repo()
        self.git(repo, 'remote', 'add', 'origin', 'https://example.com/repo.git')
        payload = {'hook_event_name': 'Stop', 'cwd': str(repo)}
        result = self.run_hook(payload)
        self.assertEqual(json.loads(result.stdout)['decision'], 'block')
        payload['stop_hook_active'] = True
        self.assertIn('systemMessage', json.loads(self.run_hook(payload).stdout))

    def test_audit_symlink_rejected_without_exposing_input(self):
        state = self.root / 'audit'
        state.mkdir()
        victim = self.root / 'victim'
        victim.write_text('unchanged')
        (state / 'events.jsonl').symlink_to(victim)
        result = self.run_hook({'hook_event_name': 'PreToolUse', 'tool_name': 'Bash', 'tool_input': {'command': 'git add -A'}, 'session_id': 'private marker'})
        self.assertEqual(result.returncode, 2)
        self.assertNotIn('private marker', result.stderr)
        self.assertEqual(victim.read_text(), 'unchanged')

    def test_context_north_star_latest_handover_state_and_bounds(self):
        (self.root / 'NORTH-STAR.md').write_text('# Objective\nShip the widget.')
        (self.root / 'HANDOVER-old.md').write_text('Old handover')
        os.utime(self.root / 'HANDOVER-old.md', (100, 100))
        (self.root / 'HANDOVER.md').write_text('Current handover')
        state = self.root / 'COORDINATOR-STATE.md'
        state.write_text('# State\n## Current state\n- fact one\n## Later\n- future')
        os.utime(state, (100, 100))
        sub = self.root / 'sub'
        sub.mkdir()
        self.ctx.cwd = str(sub)
        block = context.session_block(self.ctx)
        for needle in ('Ship the widget', 'Current handover', '- fact one', 'WARNING'):
            self.assertIn(needle, block)
        self.assertNotIn('- future', block)
        self.assertNotIn('Old handover', block)
        (self.root / 'NORTH-STAR.md').unlink()
        self.ctx.cwd = str(self.root)
        self.assertIn('NONE SET', context.session_block(self.ctx))
        (self.root / 'NORTH-STAR.md').symlink_to(state)
        self.assertIn('NONE SET', context.session_block(self.ctx))
        self.assertLessEqual(len(context.session_block(self.ctx)), self.policy.context_limit)

    def test_installer_dry_run_and_rejection_without_privilege(self):
        result = subprocess.run([str(HARNESS / 'bin/guard-install'), '--dry-run'], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        settings = json.loads(result.stdout)
        self.assertEqual(set(settings['hooks']), set(guard.EVENTS))
        self.assertEqual(settings['permissions']['defaultMode'], 'bypassPermissions')
        self.assertTrue(settings['permissions']['deny'])
        self.assertEqual(install.render_settings(settings, 'hook'), install.render_settings(install.render_settings(settings, 'hook'), 'hook'))
        with patch('os.geteuid', return_value=1234):
            with self.assertRaises(PermissionError):
                install.install(HARNESS, self.root / 'installed', HARNESS / 'config/guard.example.toml', {}, self.root / 'settings.json')

    def test_installer_layout_isolated_launcher_modes_and_ownership_calls(self):
        # Simulate privilege only for the isolated test tree; never write /opt.
        config = self.root / 'install-policy.toml'
        config.write_text(f'[guard]\nstate_dir={json.dumps(str(self.root / "installed-state"))}\n')
        target, output = self.root / 'installed', self.root / 'settings.json'
        with patch('os.geteuid', return_value=0), patch.object(install, '_safe_parent'), patch('os.chown') as chown, patch('os.fchown') as fchown:
            install.install(HARNESS, target, config, {}, output)
        self.assertTrue(chown.called)
        self.assertEqual(fchown.call_args.args[1:], (0, 0))
        self.assertTrue(all(call.args[1:] == (0, 0) for call in chown.call_args_list))
        self.assertEqual((target / 'bin/guard-hook').stat().st_mode & 0o777, 0o755)
        self.assertEqual((target / 'config/guard.toml').stat().st_mode & 0o777, 0o644)
        self.assertIn(str(target), json.dumps(json.loads(output.read_text())['hooks']))
        result = subprocess.run([str(target / 'bin/guard-hook'), '--config', str(target / 'config/guard.toml')],
                                input=json.dumps({'hook_event_name': 'PreToolUse', 'tool_name': 'Bash', 'tool_input': {'command': 'git add -A'}}),
                                text=True, capture_output=True, env=dict(os.environ, PYTHONPATH='/nonexistent', HARNESS_GUARD_CONFIG='/nonexistent'))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('git-add-all', result.stderr)
        alias = self.root / 'destination-link'
        alias.symlink_to(target)
        with self.assertRaises(ValueError):
            install._safe_parent(alias)
