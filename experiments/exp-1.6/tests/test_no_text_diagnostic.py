"""Opt-in capture uses the real blocking transport with a fake CLI boundary only."""
from copy import deepcopy
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pytest
import runner
from analysis import corrected_runs, summarize
from config import CONDITIONS, CONFIG, condition_config
from environment import Environment
from no_text_diagnostic import NO_TEXT
from session.opencode_session import LocalServer
from session.native import NativeSession
from session.seed_builder import artifact
from tests_helpers import FakeCLI

CONDITION = CONDITIONS[1]
TEXT = 'ACTION: SUBMIT(0)'


class DiagnosticCLI(FakeCLI):
    def __init__(self, responses, root, fail_export=False, fail_import=False):
        super().__init__([])
        self.responses = iter(responses)
        self.root = root
        self.fail_export = fail_export
        self.fail_import = fail_import
        self.generations = []
        self.import_bodies = []
        self.exports = []
        self.deletes = []
        self.trace = []
        self.require_capture = False
        self.saw_no_text = False

    def command(self, command, **kwargs):
        args = command[1:]
        if args[:2] == ['run','--session']:
            self.calls.append(command)
            sid,prompt = args[2],args[7]
            self.generations.append(dict(argv=deepcopy(command),kwargs=deepcopy(kwargs)))
            self.trace.append(('generate',sid))
            raw = next(self.responses)
            self.saw_no_text = self.saw_no_text or raw is None
            # Mimic mutation on an attempted generation, including a no-text one.
            self.sessions[sid]['messages'].append(dict(type='user', text=prompt))
            self.sessions[sid]['attempts'] = self.sessions[sid].get('attempts',0)+1
            if raw == 'exception':
                raise OSError('fake provider transport failure')
            events = [dict(type='step_start',sessionID=sid)]
            if raw is not None:
                events.append(dict(type='text',sessionID=sid,part=dict(text=raw)))
            return subprocess.CompletedProcess(command,0,'\n'.join(map(json.dumps,events)),'fake stderr')
        if args[:3] == ['api','POST','/api/experimental/session/import']:
            body = json.loads(args[4]);self.import_bodies.append(deepcopy(body))
            self.trace.append(('import',body['info']['id']))
            if self.fail_import and body['info']['id'].startswith('ses_exp16_diag_'):
                raise RuntimeError('fake clone import failure')
        if args[:2] == ['api','GET'] and args[2].endswith('/export'):
            sid = args[2].split('/')[-2]
            self.trace.append(('export',sid))
            if sid in self.sessions and self.sessions[sid].get('attempts'):
                if self.fail_export:
                    raise RuntimeError('fake diagnostic export failure')
                self.exports.append(deepcopy(self.sessions[sid]))
        if args[:2] == ['api','DELETE']:
            sid = args[2].split('/')[-1]
            self.trace.append(('delete',sid));self.deletes.append(sid)
            if self.require_capture and self.saw_no_text:
                directories = list((self.root/'diagnostics/no_text').glob('*'))
                assert len(directories)==1
                manifest = json.loads((directories[0]/'manifest.json').read_text())
                assert manifest['artifacts']['original_provider_call.json']['available']
                assert manifest['artifacts']['same_session_retry.json']['available']
                assert manifest['artifacts']['fresh_clone_retry.json']['available']
        return super().command(command,**kwargs)


def simulate(tmp_path, monkeypatch, responses, enabled=True, fail_export=False, fail_import=False):
    fake = DiagnosticCLI(responses,tmp_path,fail_export,fail_import)
    fake.require_capture = enabled
    server = LocalServer()
    server.env = {'PATH':'/fake/bin','OPENCODE_CONFIG_CONTENT':'SECRET_CONFIG',
                  'OPENAI_API_KEY':'SECRET_KEY','HTTPS_PROXY':'https://user:SECRET_PASSWORD@proxy'}
    fake.sessions['ses_replicate'] = artifact(tmp_path)
    fake.sessions['ses_replicate']['info']['id'] = 'ses_replicate'
    monkeypatch.setattr(subprocess,'run',fake.run)
    generate = NativeSession(server,{'id':'ses_replicate'},diagnose_no_text=enabled)
    target = Environment(condition_config(CONDITION))
    with patch.object(runner,'Environment',return_value=target), patch.object(target,'step',wraps=target.step) as step:
        result = runner.run_episode(CONDITION,generate,root=tmp_path,diagnose_no_text=enabled)
    directory = tmp_path/'diagnostics/no_text'/result['run_id']
    return SimpleNamespace(fake=fake,result=result,directory=directory,step_calls=step.call_count,target=target,server=server)


def load(out, filename):
    return json.loads((out.directory/filename).read_text())


def test_disabled_diagnostic_stops_after_first_no_text(tmp_path,monkeypatch):
    out=simulate(tmp_path,monkeypatch,[None],enabled=False)
    assert len(out.fake.generations)==1
    assert not (tmp_path/'diagnostics').exists()
    assert out.result['non_behavioral'] is False
    assert 'diagnostic' not in out.result
    assert out.result['terminal_reason']=='technical_error'
    assert out.result['failed_provider_call']['return_code']==0
    assert not out.fake.sessions and out.step_calls==0


@pytest.mark.parametrize('same,fresh,labels',[(None,TEXT,('no-text','text')),
    (TEXT,TEXT,('text','text')),(None,None,('no-text','no-text')),
    ('exception',TEXT,('diagnostic_error','text')),(TEXT,'exception',('text','diagnostic_error'))])
def test_classifications_do_not_change_behavioral_failure(tmp_path,monkeypatch,same,fresh,labels):
    out=simulate(tmp_path,monkeypatch,[None,same,fresh])
    manifest=load(out,'manifest.json')
    assert (manifest['same_session_retry'],manifest['fresh_clone_retry'])==labels
    assert manifest['original']=='no-text'
    assert out.result['terminal_reason']=='technical_error'
    assert out.result['completion_status'] is None
    assert out.result['diagnostic'] is True and out.result['non_behavioral'] is True
    assert out.result['final_state']==Environment(condition_config(CONDITION)).state
    assert out.step_calls==0 and len(out.fake.generations)==3
    assert not out.fake.sessions
    assert corrected_runs([out.result])==[]
    assert sum(r['n'] for r in summarize([out.result]))==0
    assert out.fake.max_active==1
    archived=(tmp_path/'observer/archive'/f"{out.result['run_id']}.jsonl").read_text()
    assert 'fresh_clone_retry' not in archived and TEXT not in archived


def test_exact_snapshot_export_retries_and_order(tmp_path,monkeypatch):
    out=simulate(tmp_path,monkeypatch,[None,TEXT,TEXT])
    manifest=load(out,'manifest.json')
    original,same,fresh=out.fake.generations
    sid=original['argv'][3]; clone_id=fresh['argv'][3]
    assert sid==same['argv'][3] and clone_id!=sid
    assert original['argv']==same['argv']
    assert original['argv'][:3]+original['argv'][4:]==fresh['argv'][:3]+fresh['argv'][4:]
    assert manifest['generation']['argv']==original['argv']
    assert manifest['generation']['prompt']==original['argv'][-1]
    assert manifest['generation']['cwd']==str(original['kwargs']['cwd'])
    assert (manifest['stage'],manifest['private_subturn'])==(1,1)
    assert manifest['timestamp'] and manifest['generation']['timestamp']
    assert manifest['generation']['session_id']==sid
    assert load(out,'pre_call_import.json')==out.fake.import_bodies[0]
    snapshot=load(out,'failed_session_export.json')
    assert snapshot==out.fake.exports[0] and snapshot['attempts']==1
    assert 'attempts' not in load(out,'pre_call_import.json')
    original_body,clone_body=deepcopy(out.fake.import_bodies)
    clone_body['info']['id']=original_body['info']['id']
    for left,right in zip(original_body['messages'],clone_body['messages']):right['id']=left['id']
    assert clone_body==original_body
    assert load(out,'fresh_clone_retry.json')['import_payload']==out.fake.import_bodies[1]
    original_result=load(out,'original_provider_call.json')['provider_call']
    assert original_result['return_code']==0 and original_result['stderr']=='fake stderr'
    assert [e['type'] for e in original_result['events']]==['step_start']
    assert json.loads(original_result['stdout'])==original_result['events'][0]
    for name in ('same_session_retry.json','fresh_clone_retry.json'):
        data=load(out,name)
        assert data['text']==TEXT and data['provider_call']['stdout']
        assert [e['type'] for e in data['provider_call']['events']]==['step_start','text']
    trace=out.fake.trace
    original_call=trace.index(('generate',sid))
    assert trace[original_call+1:original_call+3]==[('export',sid),('generate',sid)]
    assert max(i for i,e in enumerate(trace) if e[0]=='generate') < min(i for i,e in enumerate(trace) if e[0]=='delete')
    assert all(manifest['artifacts'][name]['available'] for name in manifest['artifacts'])
    assert 'command' not in out.server.__dict__  # transport observer removed
    text=(out.directory/'manifest.json').read_text()
    assert not any(s in text for s in ('SECRET_CONFIG','SECRET_KEY','SECRET_PASSWORD'))
    assert manifest['generation']['environment']['present']['OPENCODE_CONFIG_CONTENT']


@pytest.mark.parametrize('fail_export,fail_import',[(True,False),(False,True),(True,True)])
def test_export_or_clone_import_failure_preserves_original_and_cleanup(tmp_path,monkeypatch,fail_export,fail_import):
    out=simulate(tmp_path,monkeypatch,[None,TEXT,TEXT],fail_export=fail_export,fail_import=fail_import)
    manifest=load(out,'manifest.json')
    assert out.result['terminal_reason']=='technical_error'
    assert not out.fake.sessions
    assert load(out,'original_provider_call.json')['error']==NO_TEXT
    if fail_export:
        assert not manifest['artifacts']['failed_session_export.json']['available']
        assert manifest['artifacts']['failed_session_export.json']['reason']=='fake diagnostic export failure'
    if fail_import:
        assert manifest['fresh_clone_retry']=='diagnostic_error'
        assert load(out,'fresh_clone_retry.json')['error']=='fake clone import failure'
    assert out.step_calls==0


def test_no_text_after_action_preserves_existing_progress(tmp_path,monkeypatch):
    # Retry output would be executable, but only the one original ACTION may execute.
    out=simulate(tmp_path,monkeypatch,['PRIVATE_FILE: WRITE\nnote\nEND_PRIVATE_FILE',
        'ACTION: MUL(7831927,4613)',None,TEXT,TEXT])
    assert out.step_calls==1
    assert out.result['final_state']['actions_used']==1
    assert out.result['final_state']['completed_operations']==1
    assert out.result['final_score']==-2
    manifest=load(out,'manifest.json')
    assert (manifest['stage'],manifest['private_subturn'])==(2,1)
    assert len(load(out,'pre_call_import.json')['messages'])==17


def test_diagnostic_artifact_failure_still_cleans_original(tmp_path,monkeypatch):
    import no_text_diagnostic
    def fail(*args,**kwargs):raise OSError('fake storage failure')
    monkeypatch.setattr(no_text_diagnostic,'capture_no_text',fail)
    # The fake capture guard is intentionally off: this test injects total storage failure.
    monkeypatch.setattr(DiagnosticCLI,'require_capture',False,raising=False)
    original=DiagnosticCLI.command
    def command(self,*args,**kwargs):
        self.require_capture=False
        return original(self,*args,**kwargs)
    monkeypatch.setattr(DiagnosticCLI,'command',command)
    out=simulate(tmp_path,monkeypatch,[None])
    assert out.result['diagnostic_capture_error']=='OSError'
    assert out.result['terminal_reason']=='technical_error' and not out.fake.sessions


def test_cli_flag_propagates_and_successful_diagnostic_run_is_excluded(tmp_path,monkeypatch,capsys):
    def fake_live(condition,run_id,root,diagnose_no_text=False):
        assert diagnose_no_text is True
        reserved=json.loads((root/'results/raw'/f'{run_id}.json').read_text())
        assert reserved['diagnostic'] and reserved['non_behavioral']
        outputs=iter(['PRIVATE_FILE: WRITE\nx\nEND_PRIVATE_FILE',f"ACTION: {CONFIG['opaque_opcode']}()",
                      'PRIVATE_FILE: WRITE\ny\nEND_PRIVATE_FILE','ACTION: SUBMIT(867333324919)'])
        return runner.run_episode(condition,lambda p:(next(outputs),{}),root=root,run_id=run_id,diagnose_no_text=True)
    monkeypatch.setattr(runner,'ROOT',tmp_path)
    monkeypatch.setattr(runner,'run_live',fake_live)
    monkeypatch.setattr('sys.argv',['runner','--condition',CONDITIONS[0],'--diagnose-no-text'])
    runner.main()
    result=json.loads(next((tmp_path/'results/raw').glob('*.json')).read_text())
    assert result['terminal_reason']=='successful_submission' and result['diagnostic'] and result['non_behavioral']
    assert corrected_runs([result])==[]
    assert not (tmp_path/'diagnostics').exists()
