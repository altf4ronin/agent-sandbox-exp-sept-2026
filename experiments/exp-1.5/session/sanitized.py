"""One-use stage contexts; canonical Python history contains ACTION/result only."""
from copy import deepcopy
import uuid
import json
from session.opencode_session import text_history, MODEL, DENY
from session.seed_builder import episode


class SanitizedSession:
    def __init__(self, server, replicate):
        self.server = server
        self.base = server.export(replicate['id'])
        if text_history(self.base) != episode()[0]:
            raise RuntimeError('Replicate must start at the exact verified prior boundary')
        self.history = []
        self.stage_ids = []
        self.pending = None

    def advance(self, turn):
        # No old target prompts: those would retain notebook contents after DELETE.
        if turn['parse_error'] is None:
            self.history.extend([('assistant', turn['action_text']), ('user', json.dumps(dict(
                environment_result=turn['environment_result'], current_state=turn['state_after']), sort_keys=True))])

    def close_stage(self):
        if self.pending is None:
            return
        sid = self.pending
        result = self.server.command(['api', 'DELETE', '/api/session/' + sid])
        if result.returncode or self.server.exists(sid):
            raise RuntimeError('Could not remove one-use stage session')
        self.pending = None

    def __call__(self, prompt):
        if self.pending is not None:
            raise RuntimeError('Previous raw stage must be archived and removed first')
        data = deepcopy(self.base)
        sid = 'ses_exp15_' + uuid.uuid4().hex
        data['info'].update(id=sid, permissions=deepcopy(DENY), model=deepcopy(MODEL))
        data['info'].pop('fork', None)
        for role, text in self.history:
            msg = dict(type=role, time={'created': len(data['messages'])})
            if role == 'user':
                msg['text'] = text
            else:
                msg.update(agent='build', model=deepcopy(MODEL), content=[dict(type='text', text=text)])
                msg['time']['completed'] = msg['time']['created']
            data['messages'].append(msg)
        # Import new identities so no message is shared with a raw stage session.
        for index, msg in enumerate(data['messages']):
            msg['id'] = f'msg_{sid}_{index:03}'
        data['info']['time']['updated'] = len(data['messages'])
        expected = episode()[0] + self.history
        created = self.server.api('POST', '/api/experimental/session/import', dict(data, location=data['info']['location']))
        if created['id'] != sid:
            raise RuntimeError('Stage import changed session identity')
        self.stage_ids.append(sid)
        self.pending = sid
        if text_history(self.server.export(sid)) != expected:
            raise RuntimeError('Sanitized stage history roundtrip mismatch')
        # The runner archives output, then removes this one-use CLI session.
        # No later stage derives from it, even if cleanup fails.
        return self.server.generate(sid, prompt)
