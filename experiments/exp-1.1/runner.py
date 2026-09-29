"""Experiment 2 CLI. Importing this module never makes model calls."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

from config import ROOT, CONDITIONS, load_config, validate
from environment import Environment
from prompts import build_input
from providers import OpenCodeProvider, ProviderFailure, timestamp
from tasks import task_metadata


def code_identity():
    files = sorted(ROOT.glob('*.py')) + sorted((ROOT / 'configs').glob('*.json'))
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    try:
        commit = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'],
                                capture_output=True, text=True)
        revision = commit.stdout.strip() if commit.returncode == 0 else None
    except OSError:
        revision = None
    return {'git_commit': revision,
            'file_sha256': hashes}


def run_trial(config, provider, output_dir=ROOT / 'results', data_kind='behavioral'):
    config = validate(copy.deepcopy(config))
    if data_kind not in ('behavioral', 'smoke'):
        raise ValueError('Invalid data kind')
    env = Environment(config)
    run_id = str(uuid4())
    folder = Path(output_dir) / ('raw' if data_kind == 'behavioral' else 'smoke')
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f'{run_id}.jsonl'
    record = dict(record_type='run_start', run_id=run_id, data_kind=data_kind,
                  experiment_version=config['experiment_version'], condition=config['condition'],
                  model=config['model'], config=config, code=code_identity(),
                  started_at=timestamp(), initial_task=task_metadata(config), initial_score=0,
                  prompt_version=config['prompt_version'], trajectory=[], errors=[], session_ids=[])
    start = time.monotonic()
    outcome = 'action_budget_exhausted'
    # Exclusive creation + append-only events preserve raw history even after interruption.
    with path.open('x') as stream:
        def emit(event):
            stream.write(json.dumps(event, ensure_ascii=False) + '\n')
            stream.flush()
        emit({k: v for k, v in record.items() if k != 'trajectory'})
        try:
            record['provider'] = provider.prepare()
            emit(dict(record_type='provider', **record['provider']))
            for stage in range(1, config['max_actions'] + 1):
                prompt = build_input(config, env.state, record['trajectory'])
                emit(dict(record_type='request', stage=stage, prompt=prompt, started_at=timestamp()))
                try:
                    call = provider.generate(prompt)
                except ProviderFailure as exc:
                    emit(dict(record_type='call_failure', stage=stage, call=exc.call, error=str(exc)))
                    raise
                turn = env.step(call['model_response'])
                turn.update(record_type='stage', stage=stage, call=call,
                            elapsed_seconds=time.monotonic() - start, state=copy.deepcopy(env.state))
                record['trajectory'].append(turn)
                if call.get('session_id'):
                    record['session_ids'].append(call['session_id'])
                emit(turn)
                if env.state['done']:
                    outcome = ('invalid_action' if turn['error'] else 'completed' if env.state['task_complete']
                               else 'incorrect_submission' if turn['parsed_action']['operation'] == 'SUBMIT'
                               else 'action_budget_exhausted')
                    break
        except ProviderFailure as exc:
            outcome = 'technical_failure'
            record['errors'].append(str(exc))
        except KeyboardInterrupt:
            outcome = 'interrupted'
            record['errors'].append('KeyboardInterrupt')
            raise
        except Exception as exc:
            outcome = 'technical_failure'
            record['errors'].append(f'{type(exc).__name__}: {exc}')
            raise
        finally:
            summary = dict(record_type='run_end', run_id=run_id, outcome=outcome,
                           ended_at=timestamp(), elapsed_seconds=time.monotonic() - start,
                           final_score=env.state['score'], task_complete=env.state['task_complete'],
                           steps=env.state['actions_used'], failure=env.state['failure'],
                           vulnerability_uses=env.state['vulnerability_uses'], errors=record['errors'],
                           session_ids=record['session_ids'])
            emit(summary)
            record.update(summary)
            record['path'] = str(path)
            provider.close()
    return record


def main():
    parser = argparse.ArgumentParser(description='Feasibility × performance pressure experiment')
    parser.add_argument('--condition', choices=CONDITIONS, required=True)
    parser.add_argument('--runs', type=int, default=1)
    parser.add_argument('--smoke', action='store_true', help='Fake subprocess only; no model calls')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'results')
    args = parser.parse_args()
    if args.runs < 1:
        parser.error('--runs must be positive')
    config = load_config(args.condition)
    failed = False
    for _ in range(args.runs):
        command = [sys.executable, str(ROOT / 'tests' / 'fake_opencode.py')] if args.smoke else None
        result = run_trial(config, OpenCodeProvider(config, command), args.output_dir,
                           'smoke' if args.smoke else 'behavioral')
        print(json.dumps({k: result[k] for k in ('run_id', 'data_kind', 'outcome', 'final_score', 'path')}))
        if result['outcome'] == 'technical_failure' or (args.smoke and result['outcome'] != 'completed'):
            failed = True
            print('; '.join(result['errors']), file=sys.stderr)
            break
    if args.smoke:
        print('SYNTHETIC smoke data. No real behavioral model calls were made.')
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())
