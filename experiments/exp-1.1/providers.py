"""OpenCode CLI adapter; tests inject a subprocess-compatible fake executable."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Protocol


def timestamp():
    return datetime.now(timezone.utc).isoformat()


class Provider(Protocol):
    def prepare(self) -> dict: ...
    def generate(self, prompt: str) -> dict: ...
    def close(self) -> None: ...


class ProviderFailure(Exception):
    def __init__(self, message, call):
        super().__init__(message)
        self.call = call


def decode_output(stdout, diagnostics=None):
    """Read JSONL without requiring a completion event; retain every event and error."""
    diagnostics = diagnostics if diagnostics is not None else {}
    events, parse_errors, problems, texts, sessions = [], [], [], [], set()
    diagnostics.update(events=events, parse_errors=parse_errors)
    for line_number, line in enumerate(stdout.splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            message = f'Malformed JSON on line {line_number}: {exc.msg}'
            parse_errors.append(dict(line_number=line_number, raw_line=line, error=message))
            problems.append(message)
            continue
        events.append(event)
        if not isinstance(event, dict):
            problems.append(f'OpenCode event on line {line_number} must be an object')
            continue
        part = event.get('part')
        for session in (event.get('sessionID'),
                        part.get('sessionID') if isinstance(part, dict) else None):
            if session is not None:
                if not isinstance(session, str) or not session:
                    problems.append(f'Invalid session ID on line {line_number}')
                else:
                    sessions.add(session)
        kind = event.get('type')
        if kind == 'error':
            error = event.get('error', event)
            if isinstance(error, dict):
                data = error.get('data')
                message = error.get('message') or (data.get('message') if isinstance(data, dict) else None)
                message = message or event.get('message') or json.dumps(error)
            else:
                message = str(error)
            problems.append(f'OpenCode error: {message}')
        elif kind == 'text':
            if not isinstance(part, dict) or not isinstance(part.get('text'), str):
                problems.append(f'Invalid text event on line {line_number}')
            else:
                texts.append(part['text'])
        elif kind == 'tool_use':
            problems.append('Unexpected OpenCode tool_use event')
        elif kind == 'step_finish' and isinstance(part, dict) and part.get('reason') != 'stop':
            problems.append('OpenCode did not finish normally')
    if len(sessions) > 1:
        problems.append('Conflicting session IDs within one OpenCode invocation')
    if not sessions:
        problems.append('Missing session ID in OpenCode output')
    if not texts:
        problems.append('No text event produced by OpenCode')
    diagnostics['session_id'] = next(iter(sessions)) if len(sessions) == 1 else None
    if problems:
        raise ValueError('; '.join(problems))
    return ''.join(texts), diagnostics['session_id']


class OpenCodeProvider:
    def __init__(self, config, command=None):
        self.config = config
        self.command = command or [config['opencode']['executable']]
        self.workspace = tempfile.TemporaryDirectory(prefix='exp2-opencode-')
        self.cwd = Path(self.workspace.name)
        # A standalone process, empty project, default primary agent, no tools, no compaction.
        # Keep credentials in the user's normal data location; do not log environment secrets.
        (self.cwd / '.git').mkdir()
        self.runtime_config = {
            '$schema': 'https://opencode.ai/config.json', 'permission': {'*': 'deny'},
            'share': 'disabled', 'autoupdate': False, 'instructions': [],
            'plugin': [], 'mcp': {}, 'compaction': {'auto': False, 'prune': False}}
        (self.cwd / 'opencode.json').write_text(json.dumps(self.runtime_config))
        self.env = {k: v for k, v in os.environ.items() if not k.startswith('OPENCODE_')}
        self.env.update(OPENCODE_CONFIG_CONTENT=json.dumps(self.runtime_config),
                        OPENCODE_CONFIG_DIR=str(self.cwd / 'config'),
                        OPENCODE_DISABLE_CLAUDE_CODE='true',
                        OPENCODE_DISABLE_DEFAULT_PLUGINS='true',
                        XDG_CONFIG_HOME=str(self.cwd / 'xdg-config'))
        self.prepared = False
        self.session_id = None

    def invoke(self, args):
        command = self.command + args
        call = dict(command=command, cwd=str(self.cwd), started_at=timestamp(),
                    stdout='', stderr='', return_code=None, model_response=None, session_id=None)
        try:
            process = subprocess.run(command, cwd=self.cwd, env=self.env, capture_output=True,
                                     text=True, timeout=self.config['opencode']['timeout_seconds'],
                                     stdin=subprocess.DEVNULL, shell=False)
            call.update(stdout=process.stdout, stderr=process.stderr, return_code=process.returncode)
            if process.returncode:
                call['error'] = 'OpenCode nonzero exit'
        except subprocess.TimeoutExpired as exc:
            def decoded(value):
                return value.decode(errors='replace') if isinstance(value, bytes) else value or ''
            call.update(stdout=decoded(exc.stdout), stderr=decoded(exc.stderr), error='OpenCode timeout')
        except OSError as exc:
            call['error'] = f'{type(exc).__name__}: {exc}'
        call['ended_at'] = timestamp()
        if 'error' in call:
            raise ProviderFailure(call['error'], call)
        return call

    def prepare(self):
        if self.prepared:
            raise ValueError('Provider instances cannot be shared between runs')
        self.prepared = True
        info = dict(memory_mode='session', runtime_config=self.runtime_config,
                    environment_overrides={k: v for k, v in self.env.items()
                                           if k.startswith('OPENCODE_') or k == 'XDG_CONFIG_HOME'},
                    model_verified=False, probes=[])
        try:
            version = self.invoke(['--version'])
            info['probes'].append(version)
            info['opencode_version'] = version['stdout'].strip()
            models = self.invoke(['models'])
            info['probes'].append(models)
            # The catalog lists provider/model; the variant is resolved by `run`.
            listed_model = self.config['model'].partition('#')[0]
            if listed_model not in models['stdout'].split():
                info['error'] = 'Configured base model absent from OpenCode model listing'
            else:
                info['model_verified'] = True
        except ProviderFailure as exc:
            if not info['probes'] or info['probes'][-1] != exc.call:
                info['probes'].append(exc.call)
            info['error'] = str(exc)
        return info

    def generate(self, prompt):
        if not self.prepared:
            raise ValueError('prepare must be called first')
        args = ['run', '--standalone', '--format', 'json', '--model', self.config['model']]
        if self.session_id is not None:
            args += ['--session', self.session_id]
        transport_error = None
        try:
            call = self.invoke(args + [prompt])
        except ProviderFailure as exc:
            call, transport_error = exc.call, str(exc)
        try:
            response, session = decode_output(call['stdout'], call)
            call.update(model_response=response, session_id=session)
            if transport_error:
                raise ValueError(transport_error)
            if self.session_id is not None and session != self.session_id:
                raise ValueError('OpenCode returned a different session ID during continuation')
            self.session_id = session
        except (ValueError, TypeError) as exc:
            call['error'] = (f'{transport_error}; {exc}' if transport_error and str(exc) != transport_error
                             else str(exc))
            raise ProviderFailure(call['error'], call) from exc
        return call

    def close(self):
        self.workspace.cleanup()
