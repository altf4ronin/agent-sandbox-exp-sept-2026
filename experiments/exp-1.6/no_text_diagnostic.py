"""Opt-in no-text evidence and two isolated probes; never executes calculator code."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import uuid
from providers import ProviderFailure, decode_output

NO_TEXT = 'No text event produced by OpenCode'
ARTIFACTS = ('pre_call_import.json', 'failed_session_export.json',
             'original_provider_call.json', 'same_session_retry.json', 'fresh_clone_retry.json')


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def is_no_text(error):
    return (isinstance(error, ProviderFailure) and str(error) == NO_TEXT
            and error.call.get('return_code') == 0)


def captured_generate(server, sid, prompt, context):
    """Observe actual command arguments while retaining the existing blocking transport."""
    command = server.command
    previous = server.__dict__.get('command')
    had_override = 'command' in server.__dict__

    def capture(args, timeout=30):
        # Record presence only, never inherited values (config content/proxies may hold secrets).
        names = ('PATH', 'HOME', 'OPENCODE_DB', 'OPENCODE_CONFIG', 'OPENCODE_CONFIG_CONTENT',
                 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME', 'HTTP_PROXY', 'HTTPS_PROXY')
        context.update(session_id=sid, prompt=prompt, argv=['opencode', *args],
                       cwd=str(server.workspace), timestamp=timestamp(), timeout_seconds=timeout,
                       environment=dict(mode='inherited', values_recorded=False,
                                        present={name:name in server.env for name in names}))
        return command(args, timeout=timeout)

    server.command = capture
    try:
        return server.generate(sid, prompt)
    finally:
        if had_override:
            server.command = previous
        else:
            del server.command


def provider_evidence(call):
    if call is None:
        return None
    call = deepcopy(call)
    if 'events' not in call:
        try:
            decode_output(call.get('stdout', ''), call)
        except ValueError as exc:
            call['decode_error'] = str(exc)
    return call


def retry(server, sid, prompt):
    result = dict(session_id=sid, prompt=prompt, timestamp=timestamp())
    try:
        text, call = captured_generate(server, sid, prompt, result)
        result.update(classification='text', text=text, provider_call=call)
    except Exception as error:
        result.update(classification='no-text' if is_no_text(error) else 'diagnostic_error',
                      error=str(error), error_type=type(error).__name__,
                      provider_call=provider_evidence(getattr(error, 'call', None)))
        events = (result['provider_call'] or {}).get('events', [])
        result['text'] = ''.join(e['part']['text'] for e in events if isinstance(e, dict)
            and e.get('type') == 'text' and isinstance(e.get('part'), dict)
            and isinstance(e['part'].get('text'), str))
    return result


def clone_payload(payload, prefix='ses_exp16_diag_'):
    """This project's import schema has session/message IDs, not embedded part IDs."""
    body = deepcopy(payload)
    sid = prefix + uuid.uuid4().hex
    body['info']['id'] = sid
    for index, message in enumerate(body['messages']):
        message['id'] = f'msg_{sid}_{index:03}'
    return body


def capture_no_text(generate, error, root, run_id, stage, private_subturn, prompt):
    directory = root / 'diagnostics/no_text' / run_id
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    context = deepcopy(getattr(generate, 'pre_call', {}))
    payload = context.pop('import_payload', None)
    sid = context.get('session_id')
    manifest = dict(run_id=run_id, stage=stage, private_subturn=private_subturn,
                    timestamp=timestamp(), diagnostic=True, non_behavioral=True,
                    original='no-text', same_session_retry='diagnostic_error',
                    fresh_clone_retry='diagnostic_error', generation=context,
                    artifacts={name:dict(available=False, reason='not captured') for name in ARTIFACTS},
                    diagnostic_errors=[])

    def save(name, value):
        path = directory / name
        temp = path.with_suffix('.tmp')
        with temp.open('w', encoding='utf-8') as stream:
            temp.chmod(0o600)
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        temp.replace(path)
        if name != 'manifest.json':
            manifest['artifacts'][name] = dict(available=True)

    def unavailable(name, exc):
        message = dict(artifact=name, error=str(exc), error_type=type(exc).__name__)
        manifest['diagnostic_errors'].append(message)
        manifest['artifacts'][name] = dict(available=False, reason=str(exc))

    # Persist original evidence before any probe can mutate the failed session.
    save('manifest.json', manifest)
    save('original_provider_call.json', dict(classification='no-text', error=str(error),
         timestamp=timestamp(), provider_call=deepcopy(error.call)))
    if payload is not None:
        save('pre_call_import.json', payload)
    else:
        unavailable('pre_call_import.json', RuntimeError('Pre-call import payload unavailable'))
    save('manifest.json', manifest)
    server = getattr(generate, 'server', None)
    if not sid or server is None or context.get('prompt') != prompt:
        manifest['diagnostic_errors'].append(dict(error='Exact generation context unavailable; retries not attempted'))
        save('manifest.json', manifest)
        return
    try:
        # Existing export API; no session mutation before this attempt.
        save('failed_session_export.json', server.export(sid))
    except Exception as exc:
        unavailable('failed_session_export.json', exc)
    save('manifest.json', manifest)

    same = retry(server, sid, prompt)
    save('same_session_retry.json', same)
    manifest['same_session_retry'] = same['classification']
    save('manifest.json', manifest)

    clone_id = None
    clone = None
    try:
        if payload is None:
            raise RuntimeError('Cannot clone without exact pre-call import payload')
        clone = clone_payload(payload)
        clone_id = clone['info']['id']
        manifest['clone_session_id'] = clone_id
        created = server.api('POST', '/api/experimental/session/import', clone)
        if created['id'] != clone_id:
            raise RuntimeError('Diagnostic clone import changed session identity')
        fresh = retry(server, clone_id, prompt)
        fresh['import_payload'] = clone
    except Exception as exc:
        fresh = dict(classification='diagnostic_error', error=str(exc),
                     error_type=type(exc).__name__, provider_call=provider_evidence(getattr(exc, 'call', None)),
                     import_payload=clone)
    try:
        save('fresh_clone_retry.json', fresh)
        manifest['fresh_clone_retry'] = fresh['classification']
        save('manifest.json', manifest)
    finally:
        # Even import/retry/artifact failures must not skip attempted clone cleanup.
        if clone_id is not None:
            try:
                cleanup = server.command(['api', 'DELETE', '/api/session/' + clone_id])
                manifest['clone_cleanup'] = dict(return_code=cleanup.returncode,
                    stdout=cleanup.stdout, stderr=cleanup.stderr)
                if server.exists(clone_id):
                    raise RuntimeError('Diagnostic clone still exists after cleanup')
            except Exception as exc:
                manifest['diagnostic_errors'].append(dict(operation='clone_cleanup',
                    error=str(exc), error_type=type(exc).__name__))
        save('manifest.json', manifest)
