"""Regression coverage for the user-provided live OpenCode v2 JSONL schema."""
import json
from pathlib import Path

import pytest

from config import load_config
from providers import decode_output, OpenCodeProvider
from runner import run_trial

FIXTURE = Path(__file__).parent / 'fixtures' / 'opencode_model_ok.jsonl'
SESSION = 'ses_f2c3c4b24ffeFgJRS9UlmyfF4A'


def test_live_fixture():
    diagnostics = {}
    assert decode_output(FIXTURE.read_text(), diagnostics) == ('MODEL_OK', SESSION)
    assert [e['type'] for e in diagnostics['events']] == ['step_start', 'text']
    assert diagnostics['parse_errors'] == []


@pytest.mark.parametrize('location', ['top', 'nested', 'both'])
def test_session_locations_and_ordered_text(location):
    events = []
    for text in ['MODEL', '_OK']:
        event = dict(type='text', part=dict(text=text))
        if location in ('top', 'both'):
            event['sessionID'] = SESSION
        if location in ('nested', 'both'):
            event['part']['sessionID'] = SESSION
        events.append(event)
    assert decode_output('\n\n'.join(map(json.dumps, events))) == ('MODEL_OK', SESSION)


@pytest.mark.parametrize('events,match', [
    ([dict(type='step_start', sessionID=SESSION)], 'No text event'),
    ([dict(type='text', sessionID=SESSION, part=dict(sessionID='other', text='x'))], 'Conflicting session'),
    ([dict(type='text', sessionID=SESSION, part=dict(text='x')),
      dict(type='step_start', sessionID='other')], 'Conflicting session'),
    ([dict(type='error', sessionID=SESSION, error=dict(data=dict(message='Agent not found: experiment')))],
     'Agent not found: experiment'),
    ([dict(type='error', message='provider unavailable')], 'provider unavailable'),
])
def test_explicit_errors(events, match):
    diagnostics = {}
    with pytest.raises(ValueError, match=match):
        decode_output('\n'.join(map(json.dumps, events)), diagnostics)
    assert diagnostics['events'] == events


def test_malformed_line_retains_all_other_events():
    stdout = FIXTURE.read_text() + '\nnot-json\n' + json.dumps(dict(type='metadata', sessionID=SESSION))
    diagnostics = {}
    with pytest.raises(ValueError, match='Malformed JSON on line 5'):
        decode_output(stdout, diagnostics)
    assert len(diagnostics['events']) == 3
    assert diagnostics['parse_errors'][0]['raw_line'] == 'not-json'
    assert diagnostics['parse_errors'][0]['line_number'] == 5


@pytest.mark.parametrize('stdout,expected', [
    (json.dumps(dict(type='error', error=dict(data=dict(message='live error message')))), 'live error message'),
    (FIXTURE.read_text() + '\nbroken', 'Malformed JSON'),
])
def test_parser_failures_logged_as_technical(tmp_path, monkeypatch, stdout, expected):
    c = load_config('solvable_high_pressure')
    p = OpenCodeProvider(c)
    def invoke(args):
        if args == ['--version']:
            return dict(stdout='test-version')
        if args == ['models']:
            return dict(stdout=c['model'].partition('#')[0])
        return dict(stdout=stdout, stderr='', return_code=0)
    monkeypatch.setattr(p, 'invoke', invoke)
    result = run_trial(c, p, tmp_path, 'smoke')
    assert result['outcome'] == 'technical_failure'
    assert expected in result['errors'][0]
    events = [json.loads(line) for line in Path(result['path']).read_text().splitlines()]
    call = next(e['call'] for e in events if e['record_type'] == 'call_failure')
    assert call['stdout'] == stdout
    assert 'events' in call and 'parse_errors' in call


def test_provider_cannot_be_reused_for_another_run(monkeypatch):
    p = OpenCodeProvider(load_config('solvable_high_pressure'))
    monkeypatch.setattr(p, 'invoke', lambda args: dict(stdout=''))
    try:
        p.prepare()
        with pytest.raises(ValueError, match='cannot be shared between runs'):
            p.prepare()
    finally:
        p.close()
