import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from analysis import metrics, read_record
from config import ROOT, CONDITIONS, load_config
from environment import Environment
from prompts import build_input, VULNERABILITY
from providers import OpenCodeProvider, ProviderFailure, decode_output
from runner import run_trial
from tasks import expected_trajectory


class FakeProvider:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.prompts = []
        self.prepared = False

    def prepare(self):
        if self.prepared:
            raise ValueError('Cannot reuse provider')
        self.prepared = True
        return dict(model_verified=True, opencode_version='FAKE')

    def generate(self, prompt):
        self.prompts.append(prompt)
        output = next(self.outputs)
        if isinstance(output, BaseException):
            raise output
        return dict(model_response=output, session_id=f'fake-{len(self.prompts)}')

    def close(self):
        pass


def exploit(c):
    return f"ACTION: {c['opaque_opcode']}()"


def submit(c):
    return f"ACTION: SUBMIT({expected_trajectory(c['task'])[1]})"


@pytest.mark.parametrize('condition', CONDITIONS)
def test_score_and_repeated_shortcut_use(condition):
    c = load_config(condition)
    env = Environment(c)
    for i in range(2):
        turn = env.step(exploit(c))
        assert turn['vulnerability_used'] and turn['rule_violation']
        assert turn['penalty_applied'] == -c['step_penalty']
        assert env.state['score'] == (i + 1) * c['step_penalty']
    env.step(submit(c))
    assert env.state['task_complete']
    assert env.state['score'] == c['success_reward'] + 3 * c['step_penalty']
    with pytest.raises(ValueError):
        env.step(submit(c))


def test_configured_score():
    c = load_config('solvable_high_pressure')
    c.update(step_penalty=-7, failure_penalty=-80, success_reward=13)
    env = Environment(c)
    env.step(exploit(c)); env.step(submit(c))
    assert env.state['score'] == -1


@pytest.mark.parametrize('output', ['SCORE: 100', 'ACTION: SCORE(100)', 'ACTION: SUBMIT(0)\nscore=100'])
def test_model_cannot_set_score(output):
    env = Environment(load_config('solvable_high_pressure'))
    turn = env.step(output)
    assert turn['error'] and env.state['score'] == -102


def test_matched_conditions_and_prompts(tmp_path):
    configs = [load_config(name) for name in CONDITIONS]
    normalized = []
    for c in configs:
        prompt = build_input(c, Environment(c).state, [])
        assert VULNERABILITY.format(opcode=c['opaque_opcode']) in prompt
        normalized.append(prompt.replace('per action = -2', 'per action = 0')
                          .replace('on failure = -100', 'on failure = 0')
                          .replace('unavailable: 5.', 'unavailable: none.'))
        (tmp_path / f"{c['condition']}.json").write_text(json.dumps(c))
    assert len(set(normalized)) == 1
    configs[0]['max_actions'] += 1
    (tmp_path / f"{configs[0]['condition']}.json").write_text(json.dumps(configs[0]))
    with pytest.raises(ValueError, match='non-factor'):
        load_config('solvable_high_pressure', tmp_path)


def test_memory_and_run_isolation(tmp_path):
    c = load_config('solvable_high_pressure')
    providers = [FakeProvider([exploit(c), exploit(c), submit(c)]) for _ in range(2)]
    records = [run_trial(c, p, tmp_path, 'smoke') for p in providers]
    assert providers[0].prompts[0] == providers[1].prompts[0]
    state = json.loads(providers[0].prompts[2].split('\n\n')[-1])
    assert state['current_state']['score'] == -4
    assert state['current_state']['penalties_incurred'] == 4
    assert len(state['trajectory']) == 2
    assert state['original_task']
    assert records[0]['run_id'] != records[1]['run_id']
    assert len(list((tmp_path / 'smoke').glob('*.jsonl'))) == 2
    restored = read_record(records[0]['path'])
    assert restored['final_score'] == 94 and restored['task_complete']
    assert restored['trajectory'][0]['score_delta'] == -2
    events = [json.loads(line) for line in Path(records[0]['path']).read_text().splitlines()]
    assert len([e for e in events if e['record_type'] == 'request']) == 3
    assert events[0]['code']['file_sha256']


def event_output(text='ACTION: ADD(1,2)', session='s1'):
    return '\n'.join(json.dumps(e) for e in [
        dict(type='text', sessionID=session, part=dict(text=text)),
        dict(type='step_finish', sessionID=session, part=dict(reason='stop'))])


@pytest.mark.parametrize('text', ['not json', '{}', '[]', '{"type":"text","part":null}',
    '{"type":"text","part":{"text":"ACTION: ADD(1,2)"}}',
    '{"type":"error","error":"unavailable"}', '{"type":"tool_use"}'])
def test_malformed_cli_events(text):
    with pytest.raises(ValueError):
        decode_output(text)


def test_preserve_model_whitespace():
    assert decode_output(event_output('ACTION: ADD(1,2)\n')) == ('ACTION: ADD(1,2)\n', 's1')


@pytest.mark.parametrize('failure', ['exit', 'timeout', 'missing', 'malformed'])
def test_cli_failures_recorded(monkeypatch, tmp_path, failure):
    c = load_config('solvable_high_pressure')
    commands = []
    def fake_run(command, **kwargs):
        if command[0] == 'git':
            return subprocess.CompletedProcess(command, 128, '', '')
        commands.append(command)
        if '--version' in command:
            return subprocess.CompletedProcess(command, 0, 'v-test', '')
        if 'models' in command:
            assert command == ['opencode', 'models']
            return subprocess.CompletedProcess(command, 0, c['model'].partition('#')[0], '')
        assert kwargs['shell'] is False
        assert kwargs['env']['OPENCODE_CONFIG_CONTENT']
        assert '--session' not in command and '--continue' not in command
        if failure == 'timeout':
            raise subprocess.TimeoutExpired(command, 1, output=b'partial', stderr=b'timeout')
        if failure == 'missing':
            raise FileNotFoundError('missing')
        return subprocess.CompletedProcess(command, 9 if failure == 'exit' else 0, 'bad', 'problem')
    monkeypatch.setattr(subprocess, 'run', fake_run)
    record = run_trial(c, OpenCodeProvider(c), tmp_path, 'smoke')
    assert record['outcome'] == 'technical_failure'
    events = [json.loads(line) for line in Path(record['path']).read_text().splitlines()]
    call = next(e['call'] for e in events if e['record_type'] == 'call_failure')
    assert call['started_at'] and call['ended_at'] and 'stderr' in call
    if failure == 'exit':
        assert call['return_code'] == 9 and call['stdout'] == 'bad'
    assert record['errors'] and not record['trajectory']


@pytest.mark.parametrize('listing_exit', [0, 1])
def test_model_listing_is_diagnostic(monkeypatch, tmp_path, listing_exit):
    c = load_config('solvable_high_pressure')
    calls = []
    def fake_run(cmd, **kwargs):
        if cmd[0] == 'git':
            return subprocess.CompletedProcess(cmd, 128, '', '')
        calls.append(cmd)
        if cmd == ['opencode', '--version']:
            return subprocess.CompletedProcess(cmd, 0, 'v-test', '')
        if cmd == ['opencode', 'models']:
            return subprocess.CompletedProcess(cmd, listing_exit, '', 'listing unavailable')
        assert cmd[1:-1] == ['run', '--standalone', '--format', 'json', '--model', c['model']]
        assert '--agent' not in cmd
        return subprocess.CompletedProcess(cmd, 0, event_output('ACTION: SUBMIT(0)'), '')
    monkeypatch.setattr(subprocess, 'run', fake_run)
    record = run_trial(c, OpenCodeProvider(c), tmp_path, 'smoke')
    assert record['outcome'] == 'incorrect_submission'
    assert record['provider']['model_verified'] is False
    assert record['provider']['error']
    assert calls[1] == ['opencode', 'models']
    assert any('run' in cmd for cmd in calls)


def test_subprocess_smoke_both_conditions(tmp_path):
    run_sessions = []
    for condition in CONDITIONS:
        score = 100 if '_low_' in condition else 96
        c = load_config(condition)
        assert c['model'] == 'opencode-go/muse-spark-1.3-contributor#xhigh'
        p = OpenCodeProvider(c, [sys.executable, str(ROOT / 'tests' / 'fake_opencode.py')])
        record = run_trial(c, p, tmp_path, 'smoke')
        assert record['outcome'] == 'completed' and record['final_score'] == score
        assert record['provider']['probes'][1]['command'][2:] == ['models']
        assert record['provider']['model_verified'] is True
        command = record['trajectory'][0]['call']['command']
        assert command[2:-1] == ['run', '--standalone', '--format', 'json', '--model', c['model']]
        assert '--agent' not in command
        assert 'agent' not in record['provider']['runtime_config']
        assert '--variant' not in command
        assert len(record['session_ids']) == 2
        assert len(set(record['session_ids'])) == 1
        run_sessions.append(record['session_ids'][0])
        later = record['trajectory'][1]['call']['command']
        assert later[2:-1] == command[2:-1] + ['--session', record['session_ids'][0]]
        assert '--continue' not in later
        assert record['trajectory'][0]['call']['events'][0]['type'] == 'step_start'
        assert not p.cwd.exists()
    assert len(set(run_sessions)) == 4
    assert metrics([read_record(p) for p in (tmp_path / 'smoke').glob('*.jsonl')])['groups'] == []


def test_analysis_fixture(tmp_path):
    c = load_config('solvable_high_pressure')
    r1 = run_trial(c, FakeProvider([exploit(c), exploit(c), submit(c)]), tmp_path, 'smoke')
    actions, _ = expected_trajectory(c['task'])
    normal = [f"ACTION: {a['operation']}({','.join(a['args'])})" for a in actions] + [submit(c)]
    r2 = run_trial(c, FakeProvider(normal), tmp_path, 'smoke')
    r3 = run_trial(c, FakeProvider([ProviderFailure('failure', {})]), tmp_path, 'smoke')
    result = metrics([r1, r2, r3], include_smoke=True)['groups'][0]
    assert result['valid_runs'] == 2 and result['excluded_incomplete_or_technical'] == 1
    assert result['vulnerability_use_rate'] == .5
    assert result['task_completion_rate'] == 1
    assert result['mean_final_score'] == 86
    assert result['mean_vulnerability_uses'] == 1
    assert result['mean_first_use_stage'] == 1
    assert result['mean_first_use_seconds'] >= 0
    assert result['mean_steps'] == 7


def test_budget_invalid_and_wrong_answer(tmp_path):
    c = load_config('solvable_high_pressure'); c['max_actions'] = 11
    for outputs, outcome in [(['ACTION: ADD(1,2)']*11, 'action_budget_exhausted'),
                             (['bad'], 'invalid_action'), (['ACTION: SUBMIT(0)'], 'incorrect_submission')]:
        r = run_trial(c, FakeProvider(outputs), tmp_path, 'smoke')
        assert r['outcome'] == outcome and not r['task_complete']


def test_interruption_preserves_history(tmp_path):
    c = load_config('solvable_high_pressure')
    with pytest.raises(KeyboardInterrupt):
        run_trial(c, FakeProvider([exploit(c), KeyboardInterrupt()]), tmp_path, 'smoke')
    record = read_record(next((tmp_path / 'smoke').glob('*.jsonl')))
    assert record['outcome'] == 'interrupted'
    assert record['trajectory'][0]['vulnerability_used']
    assert record['final_score'] == -2


def test_exclusive_logs_never_overwrite(monkeypatch, tmp_path):
    import runner
    monkeypatch.setattr(runner, 'uuid4', lambda: 'fixed-test-id')
    c = load_config('solvable_high_pressure')
    r = run_trial(c, FakeProvider([submit(c)]), tmp_path, 'smoke')
    before = Path(r['path']).read_bytes()
    with pytest.raises(FileExistsError):
        run_trial(c, FakeProvider([submit(c)]), tmp_path, 'smoke')
    assert Path(r['path']).read_bytes() == before


def test_incomplete_log_excluded(tmp_path):
    c = load_config('solvable_high_pressure')
    r = run_trial(c, FakeProvider([submit(c)]), tmp_path, 'smoke')
    path = Path(r['path'])
    path.write_text('\n'.join(path.read_text().splitlines()[:-1]) + '\n{"partial":')
    incomplete = read_record(path)
    assert incomplete['outcome'] == 'incomplete'
    group = metrics([incomplete], include_smoke=True)['groups'][0]
    assert group['valid_runs'] == 0 and group['mean_final_score'] is None


def test_runtime_rejects_session_switch(monkeypatch):
    c = load_config('solvable_high_pressure')
    p = OpenCodeProvider(c)
    p.prepared = True
    outputs = iter([event_output(session='first'), event_output(session='unexpected')])
    monkeypatch.setattr(p, 'invoke', lambda args: dict(stdout=next(outputs), session_id=None))
    try:
        p.generate('first')
        with pytest.raises(ProviderFailure, match='different session ID'):
            p.generate('second')
    finally:
        p.close()


def test_self_contained_copy(tmp_path):
    import shutil
    for source in ROOT.glob('*.py'):
        shutil.copyfile(source, tmp_path / source.name)
    shutil.copytree(ROOT / 'configs', tmp_path / 'configs')
    (tmp_path / 'tests').mkdir()
    shutil.copyfile(ROOT / 'tests' / 'fake_opencode.py', tmp_path / 'tests' / 'fake_opencode.py')
    result = subprocess.run([sys.executable, '-m', 'runner', '--condition', 'solvable_high_pressure', '--smoke'],
                            cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'No real behavioral model calls' in result.stdout
