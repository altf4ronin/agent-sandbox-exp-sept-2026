"""OpenCode JSONL decoding reused from exp-1.2."""
import json


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
