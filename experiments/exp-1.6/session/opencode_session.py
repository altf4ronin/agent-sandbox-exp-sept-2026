"""Normal OpenCode CLI service with verified independent synthetic-memory forks."""
import json
import os
from pathlib import Path
import subprocess
from config import ROOT, CONFIG

MODEL = dict(providerID='opencode-go', id='muse-spark-1.3-contributor', variant='xhigh')
DENY = [dict(action='*', resource='*', effect='deny')]


def text_history(exported):
    history = []
    for m in exported['messages']:
        if m['type'] == 'user':
            history.append(('user', m['text']))
        elif m['type'] == 'assistant':
            if any(p['type'] != 'text' for p in m['content']):
                raise RuntimeError('Unexpected non-text seed content')
            history.append(('assistant', ''.join(p['text'] for p in m['content'])))
        else:
            raise RuntimeError('Unexpected seed message type: ' + m['type'])
    return history


class LocalServer:
    def __init__(self):
        self.workspace = ROOT

    def launch_environment(self):
        return dict(os.environ)

    def command(self, args, timeout=30):
        return subprocess.run(['opencode', *args], cwd=self.workspace, env=self.env,
                              capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL)

    def __enter__(self):
        self.workspace = ROOT
        self.env = self.launch_environment()
        version = self.command(['--version'])
        self.version = version.stdout.strip()
        if version.returncode or self.version != 'opencode v2.0.16':
            raise RuntimeError('Unverified OpenCode version: ' + self.version)
        db = self.command(['debug', 'paths', 'db'])
        if db.returncode or not db.stdout.strip():
            raise RuntimeError('Cannot resolve normal CLI database')
        self.database = Path(db.stdout.strip()).resolve()
        # Catalog listings need not include directly routable models. The explicit
        # CLI generation call determines availability; its errors are technical failures.
        return self

    def __exit__(self, *args):
        pass

    def api(self, method, path, body=None):
        args = ['api', method, path]
        if body is not None:
            args += ['--data', json.dumps(body)]
        result = self.command(args)
        if result.returncode:
            raise RuntimeError(f'OpenCode {method} {path}: {result.stderr.strip()}')
        decoded = json.loads(result.stdout)
        return decoded.get('data', decoded)

    def export(self, sid):
        return self.api('GET', f'/api/experimental/session/{sid}/export')

    def exists(self, sid):
        try:
            self.api('GET', '/api/session/' + sid)
            return True
        except RuntimeError as exc:
            if 'HTTP 404' in str(exc) or 'SessionNotFoundError' in str(exc):
                return False
            raise

    def fork_seed(self, record):
        from session.seed_builder import episode, seed_hash
        sid = record['seed_session_id']
        before = self.export(sid)
        if not before['messages']:
            raise RuntimeError('Seed has changed; refusing empty seed')
        boundary = dict(type='through', messageID=before['messages'][-1]['id'])
        if (not record.get('verified') or record['memory_seed_hash'] != seed_hash()
                or record['boundary'] != boundary or text_history(before) != episode()[0]):
            raise RuntimeError('Seed has changed; refusing to fork')
        # The inherited full-history fork API returns its resolved boundary.
        # Only an exact match to the verified final feedback message is accepted.
        child = self.api('POST', f'/api/session/{sid}/fork', {})
        if child['id'] == sid or child.get('fork', {}).get('sessionID') != sid:
            raise RuntimeError('Fork lineage mismatch')
        if child.get('fork', {}).get('boundary') != boundary:
            raise RuntimeError('Fork boundary mismatch')
        if text_history(self.export(child['id'])) != episode()[0]:
            raise RuntimeError('Fork did not preserve seed history')
        if self.export(sid) != before:
            raise RuntimeError('Seed changed during fork')
        return child

    def generate(self, sid, prompt):
        from providers import decode_output, ProviderFailure
        call = dict(stdout='', stderr='', return_code=None)
        try:
            result = self.command(['run', '--session', sid, '--model', CONFIG['model'],
                                   '--format', 'json', prompt], timeout=120)
            call.update(stdout=result.stdout, stderr=result.stderr, return_code=result.returncode)
            if result.returncode:
                raise ValueError('OpenCode generation returned nonzero exit code')
            text, returned = decode_output(result.stdout, call)
            if returned != sid:
                raise ValueError('OpenCode continuation returned another session ID')
            return text, call
        except subprocess.TimeoutExpired as exc:
            def decoded(value):
                return value.decode(errors='replace') if isinstance(value, bytes) else value or ''
            call.update(stdout=decoded(exc.stdout), stderr=decoded(exc.stderr))
            raise ProviderFailure('OpenCode generation timed out', call) from exc
        except (OSError, ValueError) as exc:
            raise ProviderFailure(str(exc), call) from exc
