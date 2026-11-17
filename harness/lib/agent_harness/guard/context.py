"""Bounded session orientation from local Markdown files."""
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import stat
import time

from .rules import expand, secret_path


def project_root(ctx):
    boundary = Path(expand(ctx.policy.root, ctx))
    here = Path(ctx.cwd).resolve()
    if not here.is_relative_to(boundary):
        return here
    for parent in (here, *here.parents):
        if (parent / '.git').exists() or any((parent / name).is_file() for name in ctx.policy.context_files):
            return parent
        if parent == boundary:
            break
    return boundary


def read_excerpt(path, ctx):
    """Never follow a final symlink, read special files or load unbounded text."""
    path = Path(path)
    if secret_path(str(path), ctx) or any(p.is_symlink() for p in (path, *path.parents)):
        return ''
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'r', errors='replace') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return ''
            text = stream.read(ctx.policy.context_limit)
    except OSError:
        return ''
    # Context files are trusted project instructions, but common accidental
    # assignments and PEM blocks must not expose values in hook output.
    text = re.sub(r'-----BEGIN [^-]+-----.*?(?:-----END [^-]+-----|$)', '[private material omitted]', text, flags=re.S)
    text = re.sub(r'(?im)^.*(?:token|secret|password|api[_-]?key)\s*[=:].*$', '[sensitive assignment omitted]', text)
    return text.strip()


def find_files(root, pattern, depth=2):
    root = Path(root)
    hits = []
    for visited, (current, dirs, files) in enumerate(os.walk(root, followlinks=False)):
        if visited >= 200:
            break
        relative = Path(current).relative_to(root)
        dirs[:] = [d for d in dirs if not d.startswith('.') and not (Path(current) / d).is_symlink()]
        if len(relative.parts) >= depth:
            dirs.clear()
        for name in files:
            path = Path(current) / name
            if re.fullmatch(pattern, name) and not path.is_symlink():
                hits.append(path)
        if len(hits) >= 100:
            break
    return sorted(hits, key=lambda p: (p.stat().st_mtime, str(p)), reverse=True)


def north_star(root, ctx):
    for name in ctx.policy.context_files:
        candidate = Path(root) / name
        text = read_excerpt(candidate, ctx)
        if text:
            return candidate, text
    return None, ''


def state_excerpt(path, ctx):
    text = read_excerpt(path, ctx)
    if not text:
        return ''
    match = re.search(r'^## (?:Now[^\n]*|Current state)\n(.*?)(?=^## |\Z)', text, re.M | re.S)
    body = '\n'.join(match.group(1).strip().splitlines()[:25]) if match else text
    stamp = Path(path).stat().st_mtime
    if match:
        heading = match.group(0).splitlines()[0]
        dated = re.search(r'\d{4}-\d\d-\d\d[T ]\d\d:\d\d(?::\d\d)?(?:Z|[+-]\d\d:\d\d)', heading)
        if dated:
            try:
                stamp = datetime.fromisoformat(dated.group().replace('Z', '+00:00')).timestamp()
            except ValueError:
                pass
    age = max(0, time.time() - stamp)
    if age > 6 * 3600:
        body += '\nWARNING: coordinator state is over 6 hours old; verify it before acting.'
    return body


def session_block(ctx):
    root = project_root(ctx)
    lines = [f'Profile: {ctx.policy.profile}', f'Project: {root}',
             'Guard hooks active. Read project ACCESS.md and AGENTS.md or CLAUDE.md.',
             'Keep HANDOVER.md current; never put secret values into session output.']
    path, text = north_star(root, ctx)
    lines += [f'North star: {path}\n{text}' if path else 'North star: NONE SET; flag the missing approved NORTH-STAR.md to the owner.']
    for pattern, label in [(r'HANDOVER(?:[A-Za-z0-9._-]*)\.md', 'Latest handover'), (r'COORDINATOR-STATE\.md', 'Coordinator state')]:
        hits = find_files(root, pattern)
        if not hits and Path(expand(ctx.policy.root, ctx)) != root:
            # Only inspect the configured profile boundary, not unrelated parents.
            candidate = Path(expand(ctx.policy.root, ctx)) / ('HANDOVER.md' if label == 'Latest handover' else 'COORDINATOR-STATE.md')
            hits = [candidate] if candidate.is_file() and not candidate.is_symlink() else []
        if hits:
            content = state_excerpt(hits[0], ctx) if label == 'Coordinator state' else read_excerpt(hits[0], ctx)
            lines.append(f'{label}: {hits[0]}\n{content}')
        else:
            lines.append(f'{label}: none found')
    return '\n'.join(lines)[:ctx.policy.context_limit]
