"""Offline CLI double: no process creation, network, or provider access."""
from copy import deepcopy
import json
import subprocess
from config import CONFIG
from session.opencode_session import text_history


class FakeCLI:
    def __init__(self, responses, allow_session_retries=False):
        self.allow_session_retries=allow_session_retries
        self.responses=iter(responses)
        self.sessions={}
        self.contexts=[]
        self.calls=[]
        self.forks=[]
        self.order=[]
        self.generated=[]
        self.active=0
        self.max_active=0

    def run(self, args, **kwargs):
        self.active+=1
        self.max_active=max(self.max_active,self.active)
        assert self.active==1
        try:
            return self.command(args, **kwargs)
        finally:
            self.active-=1

    def command(self, command, **kwargs):
        self.calls.append(command)
        assert command[0]=='opencode'
        args=command[1:]
        if args==['--version']:
            output='opencode v2.0.16'
        elif args==['debug','paths','db']:
            output='/tmp/offline-opencode.db'
        elif args[0]=='api':
            method,path=args[1:3]
            body=json.loads(args[4]) if len(args)>3 else None
            if method=='POST' and path.endswith('/import'):
                data=deepcopy(body)
                sid=data['info']['id']
                assert sid not in self.sessions
                self.sessions[sid]=data
                value=data['info']
                assert value['permissions']==[dict(action='*',resource='*',effect='deny')]
            elif method=='POST' and path.endswith('/fork'):
                sid=path.split('/')[-2]
                child=deepcopy(self.sessions[sid]); cid=f'ses_fork_{len(self.forks)}'
                child['info'].update(id=cid,fork=dict(sessionID=sid,boundary=dict(type='through',messageID=child['messages'][-1]['id'])))
                self.sessions[cid]=child; self.forks.append(cid); self.order.append(('fork',cid))
                value=child['info']
            else:
                sid=path.split('/')[-2] if path.endswith('/export') else path.split('/')[-1]
                if sid not in self.sessions:
                    return subprocess.CompletedProcess(command,1,'','SessionNotFoundError')
                if method=='DELETE':
                    del self.sessions[sid]; self.order.append(('delete',sid)); value={}
                else:
                    value=self.sessions[sid] if path.endswith('/export') else self.sessions[sid]['info']
            output=json.dumps(dict(data=value))
        else:
            assert args[:2]==['run','--session']
            assert args[3:7]==['--model',CONFIG['model'],'--format','json']
            sid,prompt=args[2],args[7]
            self.generated.append(sid)
            self.contexts.append((deepcopy(text_history(self.sessions[sid])),prompt))
            raw=next(self.responses)
            events=[dict(type='step_start',sessionID=sid)]
            if raw is not None:
                events.append(dict(type='text',sessionID=sid,part=dict(text=raw)))
                self.sessions[sid]['messages'].extend([dict(type='user',text=prompt),
                    dict(type='assistant',content=[dict(type='text',text=raw)])])
            output='\n'.join(json.dumps(e) for e in events)
        return subprocess.CompletedProcess(command,0,output,'')
