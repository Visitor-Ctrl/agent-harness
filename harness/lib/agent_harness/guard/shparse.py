"""Non-executing shell analysis, retaining pipelines, redirects and nested code.

This is deliberately a policy parser, not a Bash interpreter. Unsupported or
unterminated syntax raises ParseError so the caller can deny ambiguous commands.
"""
from dataclasses import dataclass, field
import os
import re


class ParseError(ValueError):
    pass


@dataclass
class Word:
    text: str
    substitutions: list = field(default_factory=list)
    processes: list = field(default_factory=list)
    quoted: bool = False

    def __str__(self):
        return self.text


@dataclass
class Command:
    words: list = field(default_factory=list)
    redirects: list = field(default_factory=list)
    argv: list = field(default_factory=list)
    env: dict = field(default_factory=dict)
    wrappers: list = field(default_factory=list)
    pipeline: list = field(default_factory=list, repr=False)
    index: int = 0

    @property
    def name(self):
        return os.path.basename(str(self.argv[0])) if self.argv else ''

    def writes(self):
        return [str(w) for op, w in self.redirects if '>' in op or op == '<>']

    def inputs(self):
        return [w for op, w in self.redirects if '<' in op]

    def output(self):
        result = None
        for op, word in self.redirects:
            if op in ('>', '>>', '>|', '1>', '1>>', '&>', '&>>'):
                result = str(word)
            elif op in ('1>&', '>&'):
                result = '/dev/stderr' if str(word) == '2' else '/dev/stdout'
        return result


OPERATORS = re.compile(r'(?:\d*(?:&>>|&>|>>|>\||>&|<<<|<<-|<<|<&|<>|>|<)|&&|\|\||\|&|[;|&()\n])')
WRAPPERS = {
    'sudo': {'-u', '-g', '-h', '-D', '-R', '--user', '--group'},
    'env': {'-u', '-C', '--unset', '--chdir'}, 'nice': {'-n'},
    'ionice': {'-c', '-n', '-p'}, 'timeout': {'-s', '-k'},
    'stdbuf': {'-i', '-o', '-e'}, 'nohup': set(), 'command': set(),
    'exec': {'-a'}, 'builtin': set(), 'heavy-lock': set(), 'setsid': set(),
    'flock': {'-w', '-E'},
}
ASSIGN = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=')
SHELLS = {'bash', 'sh', 'dash', 'zsh', 'ksh'}


def _balanced(source, start):
    depth, quote, pos = 1, '', start
    while pos < len(source):
        char = source[pos]
        if char == '\\' and quote != "'":
            pos += 2
            continue
        if quote:
            if char == quote:
                quote = ''
        elif char in "\"'":
            quote = char
        elif char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
            if not depth:
                return pos
        pos += 1
    raise ParseError('Unterminated substitution')


def tokenize(source):
    tokens, pending, pos = [], [], 0
    while pos < len(source):
        char = source[pos]
        if char in ' \t\r':
            pos += 1
            continue
        if char == '#':
            end = source.find('\n', pos)
            pos = len(source) if end < 0 else end
            continue
        match = OPERATORS.match(source, pos)
        if match and source[pos:pos + 2] not in ('<(', '>('):
            op = match.group()
            tokens.append(op)
            pos = match.end()
            if op in ('<<', '<<-'):
                pending.append(len(tokens))
            if op == '\n':
                for index in pending:
                    if index >= len(tokens) or not isinstance(tokens[index], Word):
                        raise ParseError('Missing heredoc delimiter')
                    delimiter = tokens[index].text
                    body = []
                    while pos < len(source):
                        end = source.find('\n', pos)
                        end = len(source) if end < 0 else end
                        line = source[pos:end]
                        pos = end + 1
                        if line.lstrip('\t') == delimiter:
                            break
                        body.append(line)
                    else:
                        raise ParseError('Unterminated heredoc')
                    content = '\n'.join(body)
                    word = Word(content, quoted=tokens[index].quoted)
                    if not word.quoted:
                        for found in re.finditer(r'\$\(', content):
                            close = _balanced(content, found.end())
                            word.substitutions.append(content[found.end():close])
                        word.substitutions.extend(re.findall(r'`([^`]+)`', content))
                    tokens[index] = word
                pending.clear()
            continue
        word, quote = Word(''), ''
        while pos < len(source):
            char = source[pos]
            if not quote and source[pos:pos + 2] == "$'":
                raise ParseError('ANSI escape strings require an explicit script file')
            if char == '\\' and quote != "'":
                if pos + 1 >= len(source):
                    raise ParseError('Trailing escape')
                if source[pos + 1] != '\n':
                    word.text += source[pos + 1]
                pos += 2
                continue
            if quote == "'":
                if char == quote:
                    quote = ''
                else:
                    word.text += char
                pos += 1
                continue
            if char in "\"'" and (not quote or char == quote):
                quote = '' if quote else char
                word.quoted = True
                pos += 1
                continue
            if source[pos:pos + 2] in ('$(', '<(', '>('):
                end = _balanced(source, pos + 2)
                inner = source[pos + 2:end]
                target = word.substitutions if char == '$' else word.processes
                target.append(inner)
                word.text += source[pos:end + 1]
                pos = end + 1
                continue
            if char == '`':
                end = source.find('`', pos + 1)
                if end < 0:
                    raise ParseError('Unterminated backtick')
                word.substitutions.append(source[pos + 1:end])
                word.text += source[pos:end + 1]
                pos = end + 1
                continue
            if not quote and (char.isspace() or OPERATORS.match(source, pos)):
                break
            word.text += char
            pos += 1
        if quote:
            raise ParseError('Unterminated quote')
        tokens.append(word)
    if pending:
        raise ParseError('Missing heredoc body')
    return tokens


def unwrap(command):
    args = list(command.words)
    while args:
        value = str(args[0])
        if ASSIGN.match(value):
            key, value = value.split('=', 1)
            command.env[key] = value
            args.pop(0)
            continue
        name = os.path.basename(value)
        if name in WRAPPERS:
            command.wrappers.append(name)
            args.pop(0)
            while args and str(args[0]).startswith('-'):
                option = str(args.pop(0))
                if option == '--':
                    break
                if option in WRAPPERS[name] and args:
                    args.pop(0)
            if name in ('timeout', 'flock') and args:
                args.pop(0)
            continue
        if name in ('npx', 'bunx', 'pnpx'):
            args.pop(0)
            while args and str(args[0]).startswith('-'):
                option = str(args.pop(0))
                if option in ('-p', '--package') and args:
                    args.pop(0)
            continue
        if name in ('npm', 'pnpm', 'yarn', 'bun') and len(args) > 1 and str(args[1]) in ('exec', 'dlx', 'x'):
            args = args[2:]
            if args and str(args[0]) == '--':
                args.pop(0)
            continue
        break
    command.argv = args


def parse(source):
    tokens = tokenize(source)
    pipelines, pipeline, current = [], [], Command()

    def finish(sequence=False):
        nonlocal current, pipeline
        if current.words or current.redirects:
            pipeline.append(current)
        current = Command()
        if sequence and pipeline:
            for index, item in enumerate(pipeline):
                item.pipeline, item.index = pipeline, index
                unwrap(item)
            pipelines.append(pipeline)
            pipeline = []

    index = 0
    while index < len(tokens):
        token = tokens[index]
        if isinstance(token, Word):
            if current.words or token.quoted or token.text not in {'if', 'then', 'else', 'fi', 'do', 'done', 'while', '!', '{', '}'}:
                current.words.append(token)
        elif '<' in token or '>' in token:
            index += 1
            if index >= len(tokens) or not isinstance(tokens[index], Word):
                raise ParseError('Missing redirect target')
            current.redirects.append((token, tokens[index]))
        else:
            finish(token not in ('|', '|&'))
        index += 1
    finish(True)
    return pipelines


def scripts(command):
    args = list(map(str, command.argv))
    if command.name in SHELLS:
        for index, arg in enumerate(args[1:], 1):
            if arg.startswith('-') and not arg.startswith('--') and 'c' in arg:
                return args[index + 1:index + 2]
        return [str(w) for op, w in command.redirects if op in ('<<', '<<-', '<<<')]
    if command.name == 'eval':
        return [' '.join(args[1:])]
    if command.name == 'ssh':
        rest = args[1:]
        while rest and rest[0].startswith('-'):
            option = rest.pop(0)
            if option in ('-i', '-p', '-o', '-F', '-l', '-J') and rest:
                rest.pop(0)
        return [' '.join(rest[1:])] if len(rest) > 1 else []
    return []
