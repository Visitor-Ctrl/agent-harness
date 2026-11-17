"""Configurable policy. Commands are parsed, never executed; Git queries are read-only."""
from dataclasses import dataclass, field
from fnmatch import fnmatch
import glob
import os
from pathlib import Path
import re
import subprocess
import tempfile
import tomllib

from . import shparse

SECRET_PATTERNS = [
    r'/(?:\.env(?:\.[^/]*)?|\.dev\.vars)$', r'/\.credentials\.json$',
    r'/\.codex/auth\.json$', r'/\.git-credentials$', r'/\.netrc$',
    r'/\.ssh/id_[^/]+(?<!\.pub)$', r'\.(?:pem|key|p12|pfx)$',
    r'/\.oci/[^/]+$', r'/\.docker/config\.json$', r'/run/secrets/',
    r'/(?:oauth_creds|google_accounts|msal_token_cache)\.json$',
    r'/\.config/gh/hosts\.yml$',
]
READERS = set('cat tac less more head tail bat nl strings xxd od hexdump base64 base32 grep egrep fgrep rg awk gawk sed cut sort uniq jq yq column diff paste rev openssl'.split())
FILTERS = READERS | set('tr tee python python3 node perl ruby xargs envsubst'.split())
QUIET = re.compile(r'^-[a-zA-Z]*[qclL][a-zA-Z]*$|^--(?:quiet|count|files-with-matches|files-without-match)$')
SECRET_VAR = re.compile(r'TOKEN|SECRET|PASSW(?:OR)?D|PASSPHRASE|API_?KEY|PRIVATE_?KEY|ACCESS_?KEY|CREDENTIAL|AUTHORIZATION|_PAT$|_KEY$', re.I)
TERMINALS = {'/dev/stdout', '/dev/stderr', '/dev/tty', '/dev/fd/1', '/dev/fd/2', '/proc/self/fd/1', '/proc/self/fd/2'}


@dataclass
class Policy:
    profile: str = 'default'
    root: str = '~'
    guarded_paths: list = field(default_factory=lambda: ['/etc/sudoers', '/etc/sudoers.d'])
    guard_paths: list = field(default_factory=lambda: ['/opt/agent-harness', '~/.config/agent-harness/guard.toml', '~/.claude/settings*.json', '.claude/settings*.json'])
    denied_paths: list = field(default_factory=list)
    secret_patterns: list = field(default_factory=lambda: list(SECRET_PATTERNS))
    secret_exemptions: list = field(default_factory=lambda: ['*.example', '*.sample', '*.template', '*.pub'])
    credential_paths: list = field(default_factory=lambda: ['~/.config/gh', '~/.codex', '~/.ssh', '~/.claude', '~/.claude-accounts', '~/.oci'])
    credential_readers: list = field(default_factory=lambda: 'cat less more head tail grep rg wc diff file stat ls sha256sum md5sum'.split())
    credential_scripts: list = field(default_factory=lambda: ['*signout*.sh', '*sign-out*.sh', '*ceremony-close*.sh'])
    credential_logout_commands: list = field(default_factory=lambda: ['gh auth logout'])
    credential_sandbox_env: list = field(default_factory=lambda: ['HOME', 'GH_CONFIG_DIR'])
    secret_commands: list = field(default_factory=lambda: ['gh auth token*', 'gh auth status *--show-token*', 'oci secrets secret-bundle get*', 'git credential fill*', 'gcloud auth print-*-token*', 'pass show*', 'vault kv get*'])
    protected_branches: list = field(default_factory=lambda: ['main', 'master'])
    deploy_commands: list = field(default_factory=lambda: ['wrangler *deploy*', 'wrangler secret put*', 'wrangler d1 migrations apply *--remote*', 'npm run deploy*', './scripts/deploy*'])
    deploy_ref: str = 'origin/main'
    require_model: bool = True
    require_deploy_ref: bool = True
    state_dir: str = '~/.local/state/agent-harness/guard'
    context_files: list = field(default_factory=lambda: ['NORTH-STAR.md', 'docs/north-star.md'])
    context_limit: int = 8000

    @classmethod
    def load(cls, path=None):
        policy = cls()
        if path:
            with open(path, 'rb') as stream:
                values = tomllib.load(stream).get('guard', {})
            if set(values) - set(cls.__dataclass_fields__):
                raise ValueError('Unknown guard configuration keys')
            for key, value in values.items():
                if type(value) is not type(getattr(policy, key)):
                    raise ValueError('Invalid guard configuration type: ' + key)
                if isinstance(value, list) and not all(isinstance(v, str) for v in value):
                    raise ValueError('Guard lists must contain strings')
                setattr(policy, key, value)
        for pattern in policy.secret_patterns:
            re.compile(pattern)
        if not 256 <= policy.context_limit <= 65536:
            raise ValueError('context_limit must be between 256 and 65536')
        if path:
            policy.guard_paths.append(str(Path(path).absolute()))
        return policy


@dataclass
class Context:
    cwd: str
    policy: Policy = field(default_factory=Policy)
    home: str = field(default_factory=lambda: str(Path.home()))


@dataclass(frozen=True)
class Finding:
    rule: str
    msg: str
    level: str = 'block'


def expand(value, ctx, cwd=None):
    value = str(value).replace('${HOME}', ctx.home).replace('$HOME', ctx.home)
    value = value.replace('${HARNESS_ROOT}', ctx.policy.root).replace('${PROFILE}', ctx.policy.profile)
    if value == '~' or value.startswith('~/'):
        value = ctx.home + value[1:]
    if not os.path.isabs(value):
        value = os.path.join(cwd or ctx.cwd, value)
    return os.path.realpath(value)


def matches(path, patterns, ctx, cwd=None, ancestors=False):
    candidate = expand(path, ctx, cwd).rstrip('/') or '/'
    for pattern in patterns:
        root = expand(pattern, ctx, cwd).rstrip('/')
        if candidate == root or candidate.startswith(root + '/') or fnmatch(candidate, root):
            return True
        if ancestors and root.startswith(candidate.rstrip('/') + '/'):
            return True
        if glob.has_magic(candidate) and (fnmatch(root, candidate) or (ancestors and root.startswith(candidate.split('*')[0].rstrip('/') + '/'))):
            return True
    return False


def secret_path(value, ctx, cwd=None):
    path = expand(value, ctx, cwd)
    if any(fnmatch(path, pattern) for pattern in ctx.policy.secret_exemptions):
        return False
    candidates = [path]
    if glob.has_magic(path):
        candidates.extend(path.replace('*', suffix) for suffix in ('auth.json', 'id_private', 'private.key'))
    return any(re.search(pattern, candidate) for pattern in ctx.policy.secret_patterns for candidate in candidates)


def secret_variables(value):
    for match in re.finditer(r'\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z0-9_]*)', value):
        ref = match.group(1) or match.group(2)
        if ref.startswith('#') or ':+' in ref or '+' in ref:
            continue
        if SECRET_VAR.search(ref.split(':')[0]):
            return True
    return False


def harmless(command):
    args = list(map(str, command.argv[1:]))
    text = ' '.join(args)
    if command.name in ('grep', 'rg', 'egrep', 'fgrep'):
        return any(QUIET.fullmatch(arg) for arg in args)
    if command.name == 'jq':
        return bool(args and re.fullmatch(r'keys(?:_unsorted)?(?:\[\])?|length|type|has\([^)]*\)', args[0]))
    if command.name == 'cut':
        return bool(re.search(r'(?:^| )-d\s*= ', text + ' ') and re.search(r'(?:^| )-f\s*1(?: |$)', text))
    if command.name == 'openssl':
        return ('-noout' in args or '-pubout' in args) and '-text' not in args
    return command.name in ('wc', 'sha256sum', 'sha512sum', 'sha1sum', 'md5sum', 'test', '[', 'cmp', 'true', ':')


def reveals(command):
    for current in command.pipeline[command.index:]:
        if current.name == 'tee' and any(str(arg) in TERMINALS for arg in current.argv[1:]):
            return True
        target = current.output()
        if target is not None:
            return target in TERMINALS
        if current is not command:
            if current.name == 'tee' and any(str(arg) in TERMINALS for arg in current.argv[1:]):
                return True
            if harmless(current):
                return False
            if current.name not in FILTERS:
                return False
            if current.name in ('python', 'python3', 'node', 'ruby', 'perl') and len(current.argv) > 1 and not str(current.argv[1]).startswith('-'):
                return False
    return True


def emits_secret(command, ctx, cwd):
    name, args = command.name, list(map(str, command.argv))
    joined = ' '.join(args)
    if any(fnmatch(joined, pattern) for pattern in ctx.policy.secret_commands):
        return True
    if not args and 'env' in command.wrappers:
        return True
    if name == 'printenv':
        positional = [arg for arg in args[1:] if not arg.startswith('-')]
        return not positional or any(SECRET_VAR.search(arg) for arg in positional)
    if name == 'set' and len(args) == 1:
        return True
    if name in ('export', 'declare', 'typeset') and all(arg.startswith('-') for arg in args[1:]):
        return True
    if name in ('echo', 'printf') and any(secret_variables(arg) for arg in args[1:]):
        return True
    if name in FILTERS and any(secret_variables(str(word)) for op, word in command.redirects if op == '<<<' or (op in ('<<', '<<-') and not word.quoted)):
        return True
    if harmless(command):
        return False
    if name in ('python', 'python3', 'node', 'perl', 'ruby', 'php'):
        if not any(arg in ('-c', '-e', '-') for arg in args[1:]):
            return False
        code = joined + '\n' + '\n'.join(str(word) for op, word in command.redirects if op in ('<<', '<<-'))
        paths = re.findall(r'''["']([^"']+)["']''', code)
        return bool(re.search(r'print|stdout|console\.log|puts', code)) and any(secret_path(p, ctx, cwd) for p in paths)
    if name == 'cp':
        return len(args) > 2 and args[-1] in TERMINALS and any(secret_path(arg, ctx, cwd) for arg in args[1:-1])
    if name == 'dd':
        sources = [arg[3:] for arg in args if arg.startswith('if=')]
        destinations = [arg[3:] for arg in args if arg.startswith('of=')]
        return any(secret_path(p, ctx, cwd) for p in sources) and (not destinations or destinations[-1] in TERMINALS)
    if name in READERS:
        files = [arg for arg in args[1:] if not arg.startswith('-')]
        if name in ('grep', 'rg', 'egrep', 'fgrep', 'sed', 'awk', 'gawk', 'jq', 'yq') and files:
            files = files[1:]
        files += [str(word) for op, word in command.redirects if op == '<']
        return any(secret_path(p, ctx, cwd) for p in files)
    return False


def flatten(raw, ctx, depth=0, cwd=None, consumed=False):
    if depth > 12:
        raise shparse.ParseError('Shell nesting limit exceeded')
    cwd = cwd or ctx.cwd
    result = []
    for pipeline in shparse.parse(raw):
        for command in pipeline:
            result.append((command, cwd, consumed))
            for word in command.words + [w for _, w in command.redirects]:
                leak = command.name in FILTERS | {'echo', 'printf'} and reveals(command)
                for script in word.substitutions + word.processes:
                    result.extend(flatten(script, ctx, depth + 1, cwd, consumed or not leak))
            for script in shparse.scripts(command):
                result.extend(flatten(script, ctx, depth + 1, cwd, consumed or not reveals(command)))
            if command.name == 'cd':
                cwd = expand(str(command.argv[1]) if len(command.argv) > 1 else ctx.home, ctx, cwd)
    return result


def git_output(cwd, *args):
    try:
        completed = subprocess.run(['git', '-C', cwd, *args], capture_output=True, text=True, timeout=3,
                                   env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0', 'GIT_CONFIG_COUNT': '0'})
        return completed.stdout.strip() if completed.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def git_args(command, cwd, ctx):
    args = list(map(str, command.argv[1:]))
    while args and args[0].startswith('-'):
        option = args.pop(0)
        if option in ('-C', '-c', '--git-dir', '--work-tree') and args:
            value = args.pop(0)
            if option == '-C':
                cwd = expand(value, ctx, cwd)
        elif option.startswith('--work-tree='):
            cwd = expand(option.split('=', 1)[1], ctx, cwd)
    return args, cwd


def check_git(command, cwd, ctx):
    args, cwd = git_args(command, cwd, ctx)
    raw = ' '.join(map(str, command.argv))
    if 'core.hooksPath' in raw or '--no-verify' in args or (args and args[0] == 'commit' and any(re.fullmatch(r'-[a-zA-Z]*n[a-zA-Z]*', arg) for arg in args[1:] if arg.startswith('-'))):
        return [Finding('git-hooks', 'Keep repository hooks enabled.')]
    if not args:
        return []
    sub, tail = args[0], args[1:]
    if sub in ('push', 'reset', 'clean', 'commit') and any(str(arg).startswith(('--git-dir', '--work-tree')) for arg in command.argv):
        return [Finding('git-context', 'Use git -C with the repository working directory for mutations.')]
    if sub == 'add' and any(os.path.normpath(arg) == '.' or arg in ('-A', '--all', ':/:', ':/') or re.fullmatch(r'-[a-zA-Z]*A[a-zA-Z]*', arg) for arg in tail):
        return [Finding('git-add-all', 'Stage explicit file paths.')]
    if sub in ('branch', 'update-ref') and any(arg in ('-D', '-d', '--delete') for arg in tail) and any(arg.removeprefix('refs/heads/') in ctx.policy.protected_branches for arg in tail):
        return [Finding('git-protected', 'Do not delete protected branch references.')]
    branch = git_output(cwd, 'symbolic-ref', '--short', 'HEAD')
    if sub == 'reset' and '--hard' in tail and branch in ctx.policy.protected_branches:
        return [Finding('git-destructive', 'Do not hard reset a protected branch.')]
    if sub == 'clean' and any('f' in arg for arg in tail if arg.startswith('-')):
        return [Finding('git-destructive', 'Review and remove explicit paths instead of git clean.')]
    if sub == 'push' and '--dry-run' not in tail and '-n' not in tail:
        if any(arg in ('--force', '-f', '--all', '--mirror') or arg.startswith('--force=') or arg.startswith('+') or '*' in arg for arg in tail):
            return [Finding('git-force', 'Use a normal feature branch push.')]
        if '--delete' in tail or '-d' in tail:
            return [Finding('git-delete', 'Review branch deletion before proceeding.', 'warn')]
        destinations = [arg.split(':')[-1].removeprefix('refs/heads/') for arg in tail if not arg.startswith('-')]
        if any(arg in ctx.policy.protected_branches for arg in destinations) or (branch in ctx.policy.protected_branches and (not tail or 'HEAD' in destinations or len(destinations) < 2)):
            return [Finding('git-protected', 'Push a feature branch and open a pull request.')]
    return []


def deploy_gate(cwd, policy):
    if git_output(cwd, 'rev-parse', '--show-toplevel') is None:
        return False
    if git_output(cwd, 'status', '--porcelain', '--untracked-files=all') != '':
        return False
    if policy.require_deploy_ref:
        head = git_output(cwd, 'rev-parse', 'HEAD')
        return head is not None and head == git_output(cwd, 'rev-parse', '--verify', policy.deploy_ref)
    return True


def write_targets(command):
    args = list(map(str, command.argv[1:]))
    positional = [arg for arg in args if not arg.startswith('-')]
    targets = command.writes()
    if command.name in ('rm', 'rmdir', 'shred', 'unlink', 'truncate', 'mv', 'chmod', 'chown', 'touch', 'tee'):
        targets += positional
    if command.name in ('cp', 'install', 'ln', 'rsync') and positional:
        targets += positional[-1:]
        for index, arg in enumerate(args):
            if arg in ('-t', '--target-directory') and index + 1 < len(args):
                targets.append(args[index + 1])
            if arg.startswith('--target-directory='):
                targets.append(arg.split('=', 1)[1])
    if command.name in ('sed', 'perl') and any(arg.startswith(('-i', '--in-place')) for arg in args):
        targets += positional[1:]
    if command.name == 'dd':
        targets += [arg[3:] for arg in args if arg.startswith('of=')]
    if command.name == 'find' and any(arg in ('-delete', '-exec', '-execdir') for arg in args):
        roots = positional[:1]
        if '-name' in args and '-exec' not in args and '-execdir' not in args:
            index = args.index('-name')
            if index + 1 < len(args):
                roots = [os.path.join(root, '**', args[index + 1]) for root in roots]
        targets += roots
    if command.name in ('python', 'python3', 'node', 'ruby', 'perl', 'bash', 'sh', 'eval'):
        code = ' '.join(args) + '\n' + '\n'.join(str(word) for _, word in command.redirects)
        if re.search(r'write|unlink|remove|rmtree|rename|truncate|open\([^\n]+["\'][wax]', code):
            targets += re.findall(r'''["']([^"']+)["']''', code)
    return targets


def sandboxed(command, ctx, names):
    """Explicit throwaway directories may isolate authentication cleanup tests."""
    temporary = Path(tempfile.gettempdir()).resolve()
    for name in names:
        value = command.env.get(name, '')
        if value and os.path.isabs(value):
            candidate = Path(value).resolve()
            if candidate != temporary and candidate.is_relative_to(temporary) and candidate.is_dir() and not matches(str(candidate), ctx.policy.credential_paths, ctx):
                return True
    return False


def evaluate_bash(raw, ctx):
    if len(raw) > 262144:
        return [Finding('shell-parse', 'Command exceeds analysis limit; split it into smaller commands.')]
    try:
        commands = flatten(raw, ctx)
    except shparse.ParseError:
        return [Finding('shell-parse', 'Ambiguous shell syntax; use a simple command with explicit arguments.')]
    findings = []
    deploys = [c for c, _, _ in commands if any(fnmatch(' '.join(map(str, c.argv)), pattern) for pattern in ctx.policy.deploy_commands)]
    safe_before_deploy = {'cd', 'echo', 'printf', 'true', 'test', '[', 'pwd'}
    if deploys and (len(deploys) != 1 or any(c not in deploys and (c.writes() or c.name not in safe_before_deploy) for c, _, _ in commands)):
        findings.append(Finding('deploy-sequence', 'Run deployment separately from other commands so the clean-tree check reflects its starting state.'))
    runner = any(c.name not in set(ctx.policy.credential_readers) | {'git', 'sed', 'cd'} or c.writes() for c, _, _ in commands)
    for command, cwd, consumed in commands:
        args = list(map(str, command.argv))
        joined = ' '.join(args)
        if command.name and ('$' in command.name or '`' in command.name):
            findings.append(Finding('shell-dynamic', 'Use a literal command name so the guard can inspect it.'))
        if emits_secret(command, ctx, cwd) and reveals(command) and not (consumed and command.index == len(command.pipeline) - 1 and command.output() is None):
            findings.append(Finding('secrets', 'Secret values must go directly to a consumer; use presence or length checks for inspection.'))
        for path in args[1:] + command.writes() + [str(w) for w in command.inputs()]:
            if matches(path, ctx.policy.denied_paths, ctx, cwd):
                findings.append(Finding('path-access', 'This path is outside the allowed profile.'))
        for target in write_targets(command):
            for rule, patterns in [('guard-config', ctx.policy.guard_paths), ('guarded-path', ctx.policy.guarded_paths), ('cred-store', ctx.policy.credential_paths)]:
                destructive = command.name in ('rm', 'rmdir', 'mv', 'shred') or (command.name == 'find' and '-name' not in args)
                if matches(target, patterns, ctx, cwd, ancestors=destructive):
                    findings.append(Finding(rule, 'This path is owner managed; use an approved administrative workflow.'))
        if any(fnmatch(joined, pattern + '*') for pattern in ctx.policy.credential_logout_commands) and not sandboxed(command, ctx, ctx.policy.credential_sandbox_env):
            findings.append(Finding('cred-store', 'Session logout of a persistent credential store is owner managed.'))
        scripts = [value for value in args + [str(w) for w in command.inputs()] if any(fnmatch(value, pattern) for pattern in ctx.policy.credential_scripts)]
        read_only = command.name in ctx.policy.credential_readers or (command.name == 'git' and len(args) > 1 and args[1] in ('show', 'log', 'diff', 'blame', 'grep', 'cat-file')) or (command.name == 'sed' and '-n' in args and not any(a.startswith('-i') for a in args))
        if scripts and (runner or not read_only) and not sandboxed(command, ctx, ['HOME']):
            findings.append(Finding('cred-store', 'Credential cleanup scripts may be inspected with a standalone allowed reader; execution and copying are blocked.'))
        if command.name == 'git':
            findings += check_git(command, cwd, ctx)
        if any(fnmatch(joined, pattern) for pattern in ctx.policy.deploy_commands) and not deploy_gate(cwd, ctx.policy):
            findings.append(Finding('deploy-tree', 'Deploy from a clean repository at the configured deploy ref.'))
        if command.name == 'dispatch' and ctx.policy.require_model:
            action = args[1] if len(args) > 1 else ''
            options = args[:args.index('--') if '--' in args else len(args)]
            if action not in ('status', 'summary', 'stop', 'clean', '--help', '-h') and not any((arg == '--model' and i + 1 < len(options) and options[i + 1] and not options[i + 1].startswith('-')) or (arg.startswith('--model=') and len(arg) > 8) for i, arg in enumerate(options)):
                findings.append(Finding('model-routing', 'Pass an explicit --model on every dispatch.'))
        if 'disableAllHooks' in joined:
            findings.append(Finding('guard-config', 'Sessions cannot disable their guard hooks.'))
    return list(dict.fromkeys(findings))


def evaluate_file_tool(tool, data, ctx):
    path = data.get('file_path') or data.get('notebook_path') or data.get('path') or ctx.cwd
    findings = []
    if matches(path, ctx.policy.denied_paths, ctx):
        findings.append(Finding('path-access', 'This path is outside the allowed profile.'))
    if tool == 'Glob' and data.get('pattern') and matches(data['pattern'], ctx.policy.denied_paths, ctx):
        findings.append(Finding('path-access', 'This pattern is outside the allowed profile.'))
    if tool == 'Read' or (tool == 'Grep' and data.get('output_mode', 'content') == 'content'):
        probe = os.path.join(path, data.get('glob', '').replace('**/', '')) if data.get('glob') else path
        if secret_path(probe, ctx):
            findings.append(Finding('secrets', 'Use secret files by reference; inspect names or counts only.'))
    if tool in ('Write', 'Edit', 'MultiEdit', 'NotebookEdit'):
        for rule, patterns in [('guard-config', ctx.policy.guard_paths), ('guarded-path', ctx.policy.guarded_paths), ('cred-store', ctx.policy.credential_paths)]:
            if matches(path, patterns, ctx):
                findings.append(Finding(rule, 'This path is owner managed.'))
        if 'disableAllHooks' in str(data):
            findings.append(Finding('guard-config', 'Sessions cannot disable their guard hooks.'))
    return findings
