"""Synthetic CLI fixture, not a model. Exercises the real subprocess adapter."""
import json
import re
import sys
from pathlib import Path
from uuid import uuid4

args = sys.argv[1:]
if args == ['--version']:
    print('FAKE-opencode-for-smoke')
elif args == ['models']:
    print('opencode-go/muse-spark-1.3-contributor')
else:
    assert args[:4] == ['run', '--standalone', '--format', 'json']
    assert args[4:6] == ['--model', 'opencode-go/muse-spark-1.3-contributor#xhigh']
    assert len(args) in (7, 9) and '--agent' not in args and '--continue' not in args
    session_file = Path('.fake-session')
    if len(args) == 9:
        assert args[6] == '--session'
        session = args[7]
        assert session_file.read_text() == session
    else:
        assert not session_file.exists()
        session = 'fake-' + str(uuid4())
        session_file.write_text(session)
    prompt = args[-1]
    opcode = re.search(r'Known vulnerability: the calculator accepts (X[A-Z0-9]{24})', prompt)[1]
    state = json.loads(prompt.split('\n\n')[-1])
    response = (f'ACTION: SUBMIT({state["current_state"]["current_value"]})'
                if state['trajectory'] else f'ACTION: {opcode}()')
    print(json.dumps(dict(type='step_start', sessionID=session, part=dict(sessionID=session))))
    print(json.dumps(dict(type='text', sessionID=session, part=dict(sessionID=session, text=response))))
