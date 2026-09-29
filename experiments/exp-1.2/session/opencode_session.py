"""Normal CLI service for behavior; isolated private server for offline verification."""
import base64
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from config import ROOT, CONFIG, digest

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
    def __init__(self, behavioral=False):
        self.behavioral = behavioral
        self.base = ROOT / '.local'
        self.workspace = Path('/private/tmp') / ('opencode-v3-' + digest(str(ROOT))[:16])
        self.database = self.base / 'opencode.db'
        self.process = None

    def launch_environment(self):
        # Behavioral requests must reach the same configured service as a manual CLI.
        # Credentials and provider routes are not reconstructed or copied into V3's DB.
        if self.behavioral:
            return dict(os.environ)
        runtime = dict(permission={'*': 'deny'}, share='disabled', autoupdate=False,
                       instructions=[], plugin=[], mcp={}, compaction=dict(auto=False, prune=False))
        env = {k: v for k, v in os.environ.items() if not k.startswith('OPENCODE_')}
        env.update(OPENCODE_DB=str(self.database), OPENCODE_CONFIG_CONTENT=json.dumps(runtime),
                   OPENCODE_CONFIG_DIR=str(self.base / 'config'), OPENCODE_DISABLE_DEFAULT_PLUGINS='true',
                   OPENCODE_DISABLE_CLAUDE_CODE='true', XDG_CONFIG_HOME=str(self.base / 'config'),
                   XDG_STATE_HOME=str(self.base / 'state'), XDG_CACHE_HOME=str(self.base / 'cache'),
                   XDG_DATA_HOME=str(self.base / 'data'))
        return env

    def command(self, args, timeout=30):
        return subprocess.run(['opencode', *args], cwd=self.workspace, env=self.env,
                              capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL)

    def __enter__(self):
        if self.behavioral:
            self.base.mkdir(parents=True, exist_ok=True)
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
            catalog = self.command(['models'])
            if catalog.returncode or CONFIG['model'].partition('#')[0] not in catalog.stdout.split():
                raise RuntimeError('Requested model absent from normal CLI service catalog; no fallback')
            return self
        self.base.mkdir(parents=True, exist_ok=True)
        self.workspace.mkdir(parents=True, exist_ok=True)
        if not (self.workspace / '.git/HEAD').exists():
            subprocess.run(['git', 'init', '-q', str(self.workspace)], check=True)
        runtime = dict(permission={'*': 'deny'}, share='disabled', autoupdate=False,
                       instructions=[], plugin=[], mcp={}, compaction=dict(auto=False, prune=False))
        (self.workspace / 'opencode.json').write_text(json.dumps(runtime))
        self.env = self.launch_environment()
        self.version = subprocess.check_output(['opencode', '--version'], env=self.env, text=True).strip()
        if self.version != 'opencode v2.0.16':
            raise RuntimeError('Unverified OpenCode version: ' + self.version)
        self.password = secrets.token_urlsafe(32)
        self.env['OPENCODE_SERVER_PASSWORD'] = self.password
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        self.url = f'http://127.0.0.1:{port}'
        self.log = tempfile.TemporaryFile(mode='w+')
        self.process = subprocess.Popen(['opencode', 'serve', '--print-logs', '--log-level', 'debug', '--hostname', '127.0.0.1', '--port', str(port)],
            cwd=self.workspace, env=self.env, stdout=self.log, stderr=self.log, stdin=subprocess.DEVNULL)
        try:
            for _ in range(100):
                if self.process.poll() is not None:
                    self.log.seek(0)
                    raise RuntimeError('Local OpenCode server failed: ' + self.log.read().replace(self.password, '[redacted]'))
                try:
                    self.schema = self.api('GET', '/openapi.json')
                    break
                except (OSError, ValueError):
                    time.sleep(.1)
            else:
                raise RuntimeError('Local OpenCode server readiness timeout')
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *args):
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        if hasattr(self, 'log'):
            self.log.close()

    def api(self, method, path, body=None):
        # These are the complete administrative operations used by this adapter.
        allowed = (method == 'GET' and (path == '/openapi.json' or path.startswith('/api/session/')
                   or path.startswith('/api/experimental/session/'))) or (
                   method == 'POST' and (path in ['/api/session', '/api/experimental/session/import']
                   or path.startswith('/api/session/') and path.endswith('/fork')))
        if not allowed:
            raise ValueError('API operation is not on the non-behavioral allowlist')
        if self.behavioral:
            args = ['api', method, path]
            if body is not None:
                args += ['--data', json.dumps(body)]
            result = self.command(args)
            if result.returncode:
                raise RuntimeError(f'OpenCode {method} {path}: {result.stderr.strip()}')
            decoded = json.loads(result.stdout)
            return decoded.get('data', decoded)
        headers = {'Authorization': 'Basic ' + base64.b64encode(('opencode:' + self.password).encode()).decode(),
                   'Content-Type': 'application/json'}
        request = urllib.request.Request(self.url + path, data=None if body is None else json.dumps(body).encode(),
                                         headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode()
            raise RuntimeError(f'OpenCode {method} {path}: HTTP {exc.code}: {detail}') from exc
        return result.get('data', result)

    def exists(self, sid):
        try:
            self.api('GET', '/api/session/' + sid)
            return True
        except RuntimeError as exc:
            if 'HTTP 404' in str(exc) or 'SessionNotFoundError' in str(exc):
                return False
            raise

    def export(self, sid):
        return self.api('GET', f'/api/experimental/session/{sid}/export')

    def create_control(self):
        session = self.api('POST', '/api/session', dict(location=dict(directory=str(self.workspace)),
                        model=MODEL, agent='build', permissions=DENY))
        if self.export(session['id'])['messages']:
            raise RuntimeError('Control session must start empty')
        return session

    def fork_seed(self, record):
        from session.seed_builder import episode, seed_hash
        sid = record['seed_session_id']
        before = self.export(sid)
        if record['memory_seed_hash'] != seed_hash() or text_history(before) != episode()[0]:
            raise RuntimeError('Seed has changed; refusing to fork')
        child = self.api('POST', f'/api/session/{sid}/fork', {})
        if child['id'] == sid or child.get('fork', {}).get('sessionID') != sid:
            raise RuntimeError('Fork lineage mismatch')
        boundary = dict(type='through', messageID=before['messages'][-1]['id'])
        if child.get('fork', {}).get('boundary') != boundary:
            raise RuntimeError('Fork boundary mismatch')
        if text_history(self.export(child['id'])) != episode()[0]:
            raise RuntimeError('Fork did not preserve seed history')
        if self.export(sid) != before:
            raise RuntimeError('Seed changed during fork')
        return child

    def generate(self, sid, prompt):
        if not self.behavioral:
            raise RuntimeError('Behavioral generation disabled on this server')
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
