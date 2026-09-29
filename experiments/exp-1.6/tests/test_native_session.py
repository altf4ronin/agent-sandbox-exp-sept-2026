"""Native conversation continuity and explicit private-note deletion, offline."""
import json
import subprocess

from config import CONFIG, CONDITIONS
from runner import run_episode
from session.opencode_session import LocalServer
from session.native import NativeSession
from session.seed_builder import artifact
from tests_helpers import FakeCLI

WRITE1 = 'PRIVATE_FILE: WRITE\nFIRST_PRIVATE_SENTINEL\nEND_PRIVATE_FILE'
WRITE2 = 'PRIVATE_FILE: WRITE\nSECOND_PRIVATE_SENTINEL\nEND_PRIVATE_FILE'
ACTION = f"ACTION: {CONFIG['opaque_opcode']}()"
SUBMIT = 'ACTION: SUBMIT(867333324919)'


def run_native(tmp_path, monkeypatch, responses, fake_type=FakeCLI):
    fake = fake_type(responses)
    fake.sessions['ses_replicate'] = artifact(tmp_path)
    fake.sessions['ses_replicate']['info']['id'] = 'ses_replicate'
    monkeypatch.setattr(subprocess, 'run', fake.run)
    server = LocalServer()
    server.env = {'PATH': '/fake'}
    generate = NativeSession(server, {'id': 'ses_replicate'})
    result = run_episode(CONDITIONS[0], generate, root=tmp_path)
    archive = [json.loads(line) for line in
               (tmp_path/'observer/archive'/f"{result['run_id']}.jsonl").read_text().splitlines()]
    imports = [call for call in fake.calls if call[1:4] == ['api','POST','/api/experimental/session/import']]
    return fake, generate, result, archive, imports


def test_one_native_session_across_write_action_and_stages(tmp_path, monkeypatch):
    fake, generate, result, events, imports = run_native(
        tmp_path, monkeypatch, [WRITE1, ACTION, WRITE2, SUBMIT])
    assert result['terminal_reason'] == 'successful_submission'
    assert len(imports) == len(generate.session_ids) == 1
    assert len([c for c in fake.calls if c[1:4] ==
                ['api','GET',f'/api/experimental/session/{fake.generated[0]}/export']]) == 1
    assert result['behavioral_session_ids'] == generate.session_ids
    assert len(set(fake.generated)) == 1
    assert any(WRITE1 in content for role, content in fake.contexts[1][0])
    assert any(WRITE1 in content for role, content in fake.contexts[2][0])
    assert any(WRITE2 in content for role, content in fake.contexts[3][0])
    assert len([e for e in events if e['event'] == 'session_rotation']) == 0
    assert not fake.sessions


def test_delete_rotates_only_once_and_keeps_behavioral_history(tmp_path, monkeypatch):
    fake, generate, result, events, imports = run_native(
        tmp_path, monkeypatch, [WRITE1, ACTION, WRITE2, 'PRIVATE_FILE: DELETE',
                                SUBMIT])
    assert result['terminal_reason'] == 'successful_submission'
    assert len(imports) == len(generate.session_ids) == 2
    assert result['behavioral_session_ids'] == generate.session_ids
    assert fake.generated[:4] == [fake.generated[0]] * 4
    assert fake.generated[4] != fake.generated[3]
    after_delete = json.dumps(fake.contexts[4][0])
    assert 'FIRST_PRIVATE_SENTINEL' not in after_delete
    assert 'SECOND_PRIVATE_SENTINEL' not in after_delete
    assert ACTION in after_delete and 'environment_result' in after_delete
    assert [e['event'] for e in events].count('session_rotation') == 1
    assert [e['written_text'] for e in events if e['event'] == 'WRITE'] == [
        'FIRST_PRIVATE_SENTINEL', 'SECOND_PRIVATE_SENTINEL']
    assert next(e for e in events if e['event'] == 'DELETE')['file_contents_after'] == ''
    assert all(e['session_id'] and e['timestamp'] for e in events if e['event'] in ('WRITE','ACTION','DELETE'))
    assert not fake.sessions


def test_repeated_delete_uses_new_session_until_next_delete(tmp_path, monkeypatch):
    fake, generate, result, events, imports = run_native(
        tmp_path, monkeypatch, [WRITE1, 'PRIVATE_FILE: DELETE', WRITE2,
                                'PRIVATE_FILE: DELETE', ACTION, WRITE1, SUBMIT])
    assert result['terminal_reason'] == 'successful_submission'
    assert len(imports) == len(generate.session_ids) == 3
    assert fake.generated[0] == fake.generated[1]
    assert fake.generated[2] == fake.generated[3] != fake.generated[1]
    assert fake.generated[4] == fake.generated[5] == fake.generated[6] != fake.generated[3]
    assert 'FIRST_PRIVATE_SENTINEL' not in json.dumps(fake.contexts[4][0])
    assert 'SECOND_PRIVATE_SENTINEL' not in json.dumps(fake.contexts[4][0])
    assert len([e for e in events if e['event'] == 'session_rotation']) == 2


def test_normal_continuation_ignores_truncated_export_of_active_session(tmp_path, monkeypatch):
    class TruncatedContinuationExport(FakeCLI):
        def __init__(self, responses):
            super().__init__(responses)
            self.active_exports = {}

        def command(self, command, **kwargs):
            if command[1:3] == ['api','GET'] and command[3].endswith('/export'):
                sid = command[3].split('/')[-2]
                if sid.startswith('ses_exp16_'):
                    self.active_exports[sid] = self.active_exports.get(sid, 0) + 1
                    if self.active_exports[sid] > 1:
                        return subprocess.CompletedProcess(command, 0, '{"data":"unterminated', '')
            return super().command(command, **kwargs)

    fake, generate, result, events, imports = run_native(
        tmp_path, monkeypatch, [WRITE1, ACTION, WRITE2, SUBMIT],
        fake_type=TruncatedContinuationExport)
    assert result['terminal_reason'] == 'successful_submission'
    assert len(imports) == 1
    assert list(fake.active_exports.values()) == [1]
    assert len(set(fake.generated)) == 1
