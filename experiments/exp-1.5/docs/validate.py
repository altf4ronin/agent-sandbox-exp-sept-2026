"""Regenerate non-behavioral evidence; subprocess/model transport is forbidden."""
import contextlib
from copy import deepcopy
import io
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'tests')]
from config import CONDITIONS, digest, condition_config
from environment import Environment
from prompts import build_input
from runner import main
from session.seed_builder import episode, seed_hash, artifact
from test_private_notebook_regression import exercise
from test_memory_regression import assert_hidden


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False)+'\n')


def validate():
    docs = ROOT/'docs'
    evidence = dict(non_behavioral=True, real_model_calls=0, prompts={}, dry_runs={}, fake_runs={})
    with patch('subprocess.run', side_effect=AssertionError('Real calls forbidden')), \
         patch('subprocess.Popen', side_effect=AssertionError('Real calls forbidden')):
        for condition in CONDITIONS:
            config = condition_config(condition)
            prompt = build_input(Environment(config).state, [], config)
            assert_hidden(prompt)
            (docs/f'initial_{condition}.txt').write_text(prompt+'\n')
            evidence['prompts'][condition] = digest(prompt)
            with patch('sys.argv', ['runner', '--condition', condition, '--dry-run']), contextlib.redirect_stdout(io.StringIO()) as out:
                main()
            dry = json.loads(out.getvalue())
            assert dry['model_calls'] == 0
            write(docs/f'dry_run_{condition}.json', dry)
            evidence['dry_runs'][condition] = {'model_calls':0, 'dry_run':True}
            directory = docs/'fake_transport'/condition
            fake, transport, result, record = exercise(condition, directory)
            assert result['terminal_reason'] == 'successful_submission', result['technical_error']
            contexts = fake.contexts
            assert all(sid not in fake.sessions for sid in transport.stage_ids)
            assert text_prior(fake.sessions[record['seed_session_id']]) == episode()[0]
            for context in contexts[3:]:
                for n in (1,2,3): assert f'note_{n}_secret' not in json.dumps(context)
            write(directory/'result.json', result)
            write(directory/'model_visible_contexts.json', dict(non_behavioral=True, contexts=contexts))
            write(directory/'canonical_history.json', dict(non_behavioral=True, history=episode()[0]+transport.history))
            evidence['fake_runs'][condition] = dict(non_behavioral=True, real_model_calls=0,
                fake_generation_attempts=len(contexts), actions=result['final_state']['actions_used'],
                score=result['final_score'], terminal_reason=result['terminal_reason'],
                raw_stage_sessions_removed=True, deleted_notes_absent_from_all_later_contexts=True,
                seed_unchanged=True, run_id=result['run_id'])
        for pressure in ('low','high'):
            assert evidence['prompts'][f'solvable_{pressure}_pressure'] == evidence['prompts'][f'unsolvable_{pressure}_pressure']
    history, turns, state = episode()
    baseline = json.loads((docs/'prior_baseline_history.json').read_text())
    assert [list(x) for x in history] == baseline
    assert 'PRIVATE_REASONING' not in json.dumps(history)
    (docs/'synthetic_prior_transcript.txt').write_text('\n\n'.join(f'[{role}]\n{text}' for role,text in history)+'\n')
    write(docs/'synthetic_seed.json', artifact(ROOT))
    write(docs/'prior_turns.json', dict(turns=turns, final_state=state))
    evidence.update(prior_unchanged=True, prior_hash=seed_hash(), prior_has_reasoning=False,
                    initial_prompts_byte_identical_by_pressure=True, no_advance_disclosure=True)
    write(docs/'checks.json',evidence)
    print(json.dumps(evidence,indent=2))


def text_prior(data):
    from session.opencode_session import text_history
    return text_history(data)

if __name__ == '__main__':
    validate()
