"""Claude Code hook JSON protocol and privacy-preserving lifecycle audit."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

from . import context
from .rules import Context, Finding, Policy, evaluate_bash, evaluate_file_tool, expand, flatten, git_output

EVENTS = ('PreToolUse', 'PostToolUse', 'SessionStart', 'Stop', 'SubagentStart', 'SubagentStop', 'UserPromptSubmit')


def emit_context(event, text):
    if text:
        print(json.dumps({'hookSpecificOutput': {'hookEventName': event, 'additionalContext': text}}))


def audit(ctx, event, data, rules=()):
    """No commands, prompts, descriptions, response bodies or raw identifiers."""
    state = Path(expand(ctx.policy.state_dir, ctx))
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    if state.is_symlink() or state.stat().st_uid != os.getuid():
        raise ValueError('Unsafe guard state directory')
    os.chmod(state, 0o700)
    fd = os.open(state / 'events.jsonl', os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(fd, 'a') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise ValueError('Unsafe guard audit file')
        os.fchmod(stream.fileno(), 0o600)
        session = hashlib.sha256(str(data.get('session_id', '')).encode()).hexdigest()[:16]
        stream.write(json.dumps({'time': datetime.now(timezone.utc).isoformat(), 'event': event,
                                 'session_hash': session, 'rules': sorted(set(rules))}) + '\n')


def stop_message(data, ctx):
    dirty = git_output(ctx.cwd, 'status', '--porcelain')
    upstream = git_output(ctx.cwd, 'rev-parse', '--abbrev-ref', '@{upstream}')
    remote = git_output(ctx.cwd, 'remote')
    ahead = git_output(ctx.cwd, 'rev-list', '--count', '@{upstream}..HEAD') if upstream else None
    unpushed = bool(remote) and (not upstream or (ahead and int(ahead) > 0))
    if unpushed and not data.get('stop_hook_active'):
        print(json.dumps({'decision': 'block', 'reason': 'Push the feature branch commits or explain why they remain local.'}))
    elif dirty or unpushed:
        print(json.dumps({'systemMessage': 'Repository has uncommitted changes or local commits. Include them in the handover.'}))


def handle(data, policy):
    if not isinstance(data, dict):
        raise ValueError('Hook input must be an object')
    event = data.get('hook_event_name', '')
    if event not in EVENTS:
        return 0
    ctx = Context(data.get('cwd') or os.getcwd(), policy)
    tool = data.get('tool_name', '')
    inputs = data.get('tool_input') or {}
    if not isinstance(inputs, dict):
        raise ValueError('tool_input must be an object')
    findings = []
    if event == 'PreToolUse':
        if tool == 'Bash':
            findings = evaluate_bash(inputs.get('command', ''), ctx)
        elif tool in ('Read', 'Write', 'Edit', 'MultiEdit', 'NotebookEdit', 'Grep', 'Glob'):
            findings = evaluate_file_tool(tool, inputs, ctx)
        elif tool in ('Agent', 'Task') and policy.require_model and not inputs.get('model'):
            findings = [Finding('model-routing', 'Pass an explicit model on every subagent dispatch.')]
        if findings:
            audit(ctx, event, data, [finding.rule for finding in findings])
        blocks = [finding for finding in findings if finding.level == 'block']
        if blocks:
            sys.stderr.write('\n'.join(f'[guard:{f.rule}] {f.msg}' for f in blocks) + '\n')
            return 2
        emit_context(event, '\n'.join(f'[guard warning:{f.rule}] {f.msg}' for f in findings))
    elif event == 'SessionStart':
        audit(ctx, event, data)
        prefix = 'COMPACTED: Re-read the current state and handover before continuing.\n' if data.get('source') == 'compact' else ''
        emit_context(event, prefix + context.session_block(ctx))
    elif event in ('Stop', 'SubagentStop'):
        audit(ctx, event, data)
        if event == 'Stop':
            stop_message(data, ctx)
        else:
            emit_context(event, 'Update the task handover with completed work, verification and the next step.')
    elif event == 'PostToolUse':
        if tool == 'Bash':
            calls = flatten(inputs.get('command', ''), ctx)
            if any(c.name.startswith('admin-') for c, _, _ in calls):
                audit(ctx, event, data, ['admin-invocation'])
    elif event == 'SubagentStart':
        audit(ctx, event, data)
    elif event == 'UserPromptSubmit':
        emit_context(event, 'Follow the approved project objective. Read before editing and verify the result. Keep secrets out of output.')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', help='Trusted guard TOML policy')
    args = parser.parse_args(argv)
    data = None
    try:
        # An explicit installer-provided path takes precedence over the environment.
        path = args.config or os.environ.get('HARNESS_GUARD_CONFIG')
        if not path:
            default = Path.home() / '.config/agent-harness/guard.toml'
            path = str(default) if default.exists() else None
        policy = Policy.load(path)
        raw = sys.stdin.read(1048577)
        if len(raw) > 1048576:
            raise ValueError('Hook input too large')
        data = json.loads(raw)
        return handle(data, policy)
    except Exception:
        # Never echo exceptions, tool arguments or malformed input: they can contain secrets.
        sys.stderr.write('[guard:internal] Guard could not evaluate the event. Check the trusted configuration.\n')
        return 2 if not isinstance(data, dict) or data.get('hook_event_name') == 'PreToolUse' else 1


if __name__ == '__main__':
    raise SystemExit(main())
