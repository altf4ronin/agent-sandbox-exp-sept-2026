"""Normal OpenCode CLI service for empty native behavioral sessions."""
import json
import os
from pathlib import Path
import subprocess
from config import ROOT, CONFIG

MODEL = dict(providerID='opencode-go', id='muse-spark-1.3-contributor', variant='xhigh')
DENY = [dict(action='*', resource='*', effect='deny')]


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

    def exists(self, sid):
        try:
            self.api('GET', '/api/session/' + sid)
            return True
        except RuntimeError as exc:
            if 'HTTP 404' in str(exc) or 'SessionNotFoundError' in str(exc):
                return False
            raise

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
