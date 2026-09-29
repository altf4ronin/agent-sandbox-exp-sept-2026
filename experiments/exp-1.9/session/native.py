"""One empty native OpenCode session for each behavioral run."""
import uuid

from session.opencode_session import DENY, MODEL


class NativeSession:
    def __init__(self, server):
        self.server = server
        self.active = None
        self.session_ids = []

    def __call__(self, prompt):
        if self.active is None:
            sid = 'ses_exp19_' + uuid.uuid4().hex
            location = dict(directory=str(self.server.workspace))
            payload = dict(info=dict(id=sid, projectID='global', agent='build',
                model=MODEL, permissions=DENY, cost=0,
                tokens=dict(input=0, output=0, reasoning=0, cache=dict(read=0, write=0)),
                time=dict(created=0, updated=0), location=location),
                messages=[], location=location)
            created = self.server.api('POST', '/api/experimental/session/import', payload)
            if created['id'] != sid:
                raise RuntimeError('Import changed session identity')
            self.active = sid
            self.session_ids.append(sid)
        return self.server.generate(self.active, prompt)

    def close_run(self):
        if self.active is not None:
            result = self.server.command(['api', 'DELETE', '/api/session/' + self.active])
            if result.returncode or self.server.exists(self.active):
                raise RuntimeError('Could not remove session')
            self.active = None
