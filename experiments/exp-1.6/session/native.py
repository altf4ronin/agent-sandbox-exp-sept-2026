"""Persistent native session; explicit DELETE rotates away private notes."""
from copy import deepcopy
import json
import uuid

from session.opencode_session import DENY, MODEL, text_history
from session.seed_builder import episode


class NativeSession:
    def __init__(self, server, replicate, diagnose_no_text=False):
        self.server = server
        self.diagnose_no_text = diagnose_no_text
        self.base = server.export(replicate['id'])
        if text_history(self.base) != episode()[0]:
            raise RuntimeError('Replicate must start at the exact verified prior boundary')
        self.history = []  # ACTION and environment feedback survive explicit DELETE.
        self.active = None
        self.session_ids = []
        self.retired = []

    def _import(self, payload, expected=None):
        sid = payload['info']['id']
        created = self.server.api('POST', '/api/experimental/session/import', payload)
        if created['id'] != sid:
            raise RuntimeError('Import changed session identity')
        self.active = sid
        self.session_ids.append(sid)
        if expected is not None and text_history(self.server.export(sid)) != expected:
            raise RuntimeError('Rotated session history roundtrip mismatch')
        return sid

    def _new_behavioral_session(self):
        data = deepcopy(self.base)
        sid = 'ses_exp16_' + uuid.uuid4().hex
        data['info'].update(id=sid, permissions=deepcopy(DENY), model=deepcopy(MODEL))
        data['info'].pop('fork', None)
        for role, content in self.history:
            msg = dict(type=role, time={'created': len(data['messages'])})
            if role == 'user':
                msg['text'] = content
            else:
                msg.update(agent='build', model=deepcopy(MODEL), content=[dict(type='text', text=content)])
                msg['time']['completed'] = msg['time']['created']
            data['messages'].append(msg)
        for index, msg in enumerate(data['messages']):
            msg['id'] = f'msg_{sid}_{index:03}'
        data['info']['time']['updated'] = len(data['messages'])
        return self._import(dict(data, location=data['info']['location']), episode()[0] + self.history)

    def advance(self, turn):
        if turn['parse_error'] is None:
            self.history.extend([('assistant', turn['action_text']), ('user', json.dumps(dict(
                environment_result=turn['environment_result'], current_state=turn['state_after']), sort_keys=True))])

    def _delete_session(self, sid):
        result = self.server.command(['api', 'DELETE', '/api/session/' + sid])
        if result.returncode or self.server.exists(sid):
            raise RuntimeError('Could not remove session')

    def rotate_after_delete(self):
        old = self.active
        new = self._new_behavioral_session()
        if old is not None:
            try:
                self._delete_session(old)
            except Exception:
                self.retired.append(old)
                raise
        return old, new

    def close_run(self):
        errors = []
        for sid in [self.active, *self.retired, self.base['info']['id']]:
            if sid is not None:
                try:
                    self._delete_session(sid)
                except Exception as error:
                    errors.append(error)
        self.active = None
        self.retired.clear()
        if errors:
            raise errors[0]

    def __call__(self, prompt):
        if self.active is None:
            self._new_behavioral_session()
        sid = self.active
        if self.diagnose_no_text:
            from no_text_diagnostic import captured_generate
            snapshot = self.server.export(sid)
            self.pre_call = dict(import_payload=dict(snapshot, location=snapshot['info']['location']))
            return captured_generate(self.server, sid, prompt, self.pre_call)
        return self.server.generate(sid, prompt)
