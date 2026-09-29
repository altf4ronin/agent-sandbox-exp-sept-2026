"""Production no-text is terminal; all provider calls use the offline CLI."""
from config import CONDITIONS, condition_config
from environment import Environment
from test_native_session import ACTION, WRITE1, run_native


def generation_exports(fake, sid):
    return [call for call in fake.calls if call[1:4] ==
            ['api', 'GET', f'/api/experimental/session/{sid}/export']]


def test_no_text_first_generation_has_no_retry_or_pre_call_export(tmp_path, monkeypatch):
    fake, generate, result, events, imports = run_native(tmp_path, monkeypatch, [None])
    assert result['terminal_reason'] == 'technical_error'
    assert result['failed_provider_call']['return_code'] == 0
    assert result['final_state'] == Environment(condition_config(CONDITIONS[0])).state
    assert len(fake.generated) == len(imports) == len(generate.session_ids) == 1
    assert len(generation_exports(fake, fake.generated[0])) == 1  # initial import verification only
    assert [e['event'] for e in events if e['event'] in ('generation','technical_error')] == ['technical_error']
    assert next(e for e in events if e['event']=='technical_error')['provider_call']['events'][0]['type'] == 'step_start'
    assert not any(e['event']=='transport_session_recovery' for e in events)
    assert not any(s.startswith('ses_exp16_retry_') for s in generate.session_ids)
    assert not fake.sessions


def test_no_text_after_write_preserves_state_without_retry(tmp_path, monkeypatch):
    fake, generate, result, events, imports = run_native(tmp_path, monkeypatch, [WRITE1,None])
    sid = fake.generated[0]
    assert fake.generated == [sid,sid]
    assert len(imports) == len(generate.session_ids) == 1
    assert len(generation_exports(fake,sid)) == 1
    assert result['terminal_reason'] == 'technical_error'
    assert result['final_state']['actions_used'] == 0
    assert [e['written_text'] for e in events if e['event']=='WRITE'] == ['FIRST_PRIVATE_SENTINEL']
    assert len([e for e in events if e['event']=='ACTION']) == 0
    assert len([e for e in events if e['event']=='generation']) == 1
    assert (tmp_path/'private_state'/result['run_id']/'private_file.txt').read_text() == 'FIRST_PRIVATE_SENTINEL'


def test_no_text_after_action_uses_same_session_and_preserves_progress(tmp_path, monkeypatch):
    fake, generate, result, events, imports = run_native(tmp_path, monkeypatch, [WRITE1,ACTION,None])
    assert len(set(fake.generated)) == len(imports) == 1
    assert len(generation_exports(fake,fake.generated[0])) == 1
    assert result['terminal_reason'] == 'technical_error'
    assert result['final_state']['actions_used'] == 1
    assert len([e for e in events if e['event']=='ACTION']) == 1
    assert len([e for e in events if e['event']=='session_rotation']) == 0


def test_no_text_after_explicit_delete_does_not_create_retry_session(tmp_path, monkeypatch):
    fake, generate, result, events, imports = run_native(
        tmp_path, monkeypatch, [WRITE1,'PRIVATE_FILE: DELETE',None])
    assert len(imports) == len(generate.session_ids) == 2
    assert fake.generated[0] == fake.generated[1] != fake.generated[2]
    assert len(generation_exports(fake,fake.generated[2])) == 1
    assert result['terminal_reason'] == 'technical_error'
    assert len([e for e in events if e['event']=='session_rotation']) == 1
    assert not any(s.startswith('ses_exp16_retry_') for s in generate.session_ids)
    assert not fake.sessions
