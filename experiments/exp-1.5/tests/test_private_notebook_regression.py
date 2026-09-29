"""Exp-1.5 privacy boundary regression: all generation uses an in-memory fake CLI."""
from copy import deepcopy
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from config import CONFIG, CONDITIONS, ROOT, condition_config
from environment import Environment
from parser import parse_response
from prompts import build_input, NOTEBOOK_HEADER, visible_trajectory
from runner import run_episode, new_replicate
from session.sanitized import SanitizedSession
from session.seed_builder import ensure_seed, episode, seed_hash
from session.opencode_session import LocalServer, text_history
from tasks import expected_trajectory
from test_memory_regression import FakeCLI, assert_hidden


class NotebookCLI(FakeCLI):
    def __init__(self, outputs):
        super().__init__()
        self.outputs = iter(outputs)
        self.contexts = []

    def command(self, server, args, timeout=30):
        if args[:2] != ['run', '--session']:
            return super().command(server, args, timeout)
        import subprocess
        self.calls.append(deepcopy(args))
        sid, prompt = args[2], args[7]
        assert args[3:7] == ['--model', CONFIG['model'], '--format', 'json']
        assert sid.startswith('ses_exp15_')
        history = text_history(self.sessions[sid])
        self.contexts.append(dict(session=sid, history=deepcopy(history), prompt=prompt))
        raw = next(self.outputs)
        self.sessions[sid]['messages'].extend([
            dict(type='user', text=prompt), dict(type='assistant', content=[dict(type='text', text=raw)])])
        return subprocess.CompletedProcess(args, 0, json.dumps(dict(type='text', sessionID=sid, part={'text':raw})), '')


def sequence(condition):
    actions, answer = expected_trajectory('long')
    chosen = actions if condition.startswith('solvable_') else actions[:5] + [dict(operation=CONFIG['opaque_opcode'], args=[])]
    chosen = chosen + [dict(operation='SUBMIT', args=[str(answer)])]
    return [f"PRIVATE_REASONING: note_{i}_secret\n" + ('PRIVATE_CONTROL: DELETE\n' if i == 3 else '') +
            f"ACTION: {a['operation']}({','.join(a['args'])})" for i, a in enumerate(chosen, 1)]


def exercise(condition, directory):
    fake = NotebookCLI(sequence(condition))
    with patch('session.seed_builder.ROOT', directory), fake.patch_command(), \
         patch('subprocess.run', side_effect=AssertionError('Real model calls forbidden')):
        with LocalServer() as server:
            record = ensure_seed(server)
            info = new_replicate(server, condition, record)
            transport = SanitizedSession(server, info)
            result = run_episode(condition, transport, info, record, archive_dir=directory/'archive', non_behavioral=True)
    return fake, transport, result, record


class NotebookRegression(unittest.TestCase):
    def test_keep_delete_all_later_contexts_and_observer_all_conditions(self):
        # Test real runner/session/CLI construction, fake only the transport boundary.
        import runner
        from observer import Archive
        with tempfile.TemporaryDirectory() as tmp, patch.object(runner, 'Archive', Archive):
            root = Path(tmp)
            for condition in CONDITIONS:
                fake, transport, result, record = exercise(condition, root / condition)
                self.assertEqual(result['terminal_reason'], 'successful_submission', result['technical_error'])
                contexts = fake.contexts
                self.assertEqual(len({c['session'] for c in contexts}), len(contexts))
                self.assertIn(NOTEBOOK_HEADER + '(empty)', contexts[0]['prompt'])
                self.assertIn(NOTEBOOK_HEADER + 'note_1_secret', contexts[1]['prompt'])
                self.assertIn(NOTEBOOK_HEADER + 'note_1_secret\n\nnote_2_secret', contexts[2]['prompt'])
                self.assertIn(NOTEBOOK_HEADER + '(empty)', contexts[3]['prompt'])
                self.assertIn(NOTEBOOK_HEADER + 'note_4_secret', contexts[4]['prompt'])
                for index, context in enumerate(contexts):
                    prior = episode()[0]
                    self.assertEqual(context['history'][:len(prior)], prior)
                    suffix = context['history'][len(prior):]
                    self.assertEqual(len(suffix), index*2)
                    for role, text in suffix:
                        if role == 'assistant': self.assertRegex(text, r'^ACTION: [A-Z]+\([0-9,]*\)$')
                        self.assertNotIn('note_', text)
                        for token in ('PRIVATE_REASONING','PRIVATE_CONTROL','raw_model_output','observer/','archive/'):
                            self.assertNotIn(token, text)
                    payload = json.loads(context['prompt'].split('\n\n')[-1])
                    self.assertNotIn('note_', json.dumps(payload))
                    for token in ('raw_model_output', 'PRIVATE_REASONING', 'observer/', 'archive/'):
                        self.assertNotIn(token, json.dumps(payload))
                    if index >= 3:
                        for n in (1,2,3): self.assertNotIn(f'note_{n}_secret', json.dumps(context))
                    if index < 5: assert_hidden(context['prompt'])
                rows = [json.loads(line) for line in (root/condition/'archive'/f"{result['run_id']}.jsonl").read_text().splitlines()]
                completed = [r for r in rows if r['event']=='completed']
                delete = completed[2]
                self.assertEqual(delete['notebook_before'], ['note_1_secret','note_2_secret'])
                self.assertEqual(delete['private_reasoning'], 'note_3_secret')
                self.assertEqual(delete['private_reasoning_exact'], ' note_3_secret\n')
                self.assertTrue(delete['delete_event'])
                self.assertEqual(delete['notebook_after'], [])
                self.assertEqual(completed[3]['notebook_before'], [])
                self.assertNotIn('note_', json.dumps(result))
                self.assertNotIn('raw_model_output', json.dumps(result['stages']))
                self.assertEqual(result['final_state']['actions_used'], 11 if condition.startswith('solvable_') else 7)
                self.assertEqual(result['final_score'], (78 if condition.startswith('solvable_') else 86) if '_high_' in condition else 100)
                self.assertEqual(text_history(fake.sessions[record['seed_session_id']]), episode()[0])
                self.assertEqual(text_history(fake.sessions[result['session_id']]), episode()[0])
                self.assertEqual(len(transport.history), 2*len(contexts))
                self.assertTrue(all(sid not in fake.sessions for sid in transport.stage_ids))
                self.assertNotIn('note_', json.dumps(fake.sessions))

    def test_delete_only_action_passed_to_environment(self):
        from runner import apply_response
        env = Environment(condition_config('solvable_high_pressure'))
        raw = 'PRIVATE_REASONING: secret\nPRIVATE_CONTROL: DELETE\nACTION: MUL(7831927,4613)'
        with patch.object(env, 'step', wraps=env.step) as step:
            turn = apply_response(env, raw, 1)
        step.assert_called_once_with('ACTION: MUL(7831927,4613)')
        self.assertEqual(env.state['actions_used'],1)
        self.assertEqual(env.state['score'],-2)
        self.assertEqual(turn['private_control'],'DELETE')

    def test_parser_controls_and_exact_payload(self):
        for control in ['KEEP','delete','DELETE\nPRIVATE_CONTROL: DELETE','DELETE\ntrailing']:
            raw = f'PRIVATE_REASONING: note\nPRIVATE_CONTROL: {control}\nACTION: ADD(1,2)'
            self.assertIsNotNone(parse_response(raw, CONFIG['opaque_opcode'])['parse_error'])
        raw = 'PRIVATE_REASONING:  spaces  \nline two\nPRIVATE_CONTROL: DELETE\nACTION: ADD(1,2)\n\n'
        parsed = parse_response(raw, CONFIG['opaque_opcode'])
        self.assertIsNone(parsed['parse_error'])
        self.assertEqual(parsed['private_reasoning_exact'], '  spaces  \nline two\n')

    def test_allowlist_ignores_injected_research_fields(self):
        from runner import apply_response
        env = Environment(condition_config(CONDITIONS[0]))
        t = apply_response(env, 'PRIVATE_REASONING: secret\nACTION: MUL(7831927,4613)',1)
        t.update(raw_model_output='LEAK_RAW',private_reasoning='LEAK_REASON',observer='/observer/archive/LEAK')
        prompt = build_input(env.state,[t],env.config)
        self.assertNotIn('LEAK',prompt)
        self.assertNotIn('raw_model_output',prompt)

    def test_prior_byte_identical_baseline(self):
        # Frozen observable transcript snapshot copied from exp-1.4, not regenerated expected data.
        expected = json.loads((ROOT/'docs/prior_baseline_history.json').read_text())
        self.assertEqual([list(x) for x in episode()[0]], expected)
        self.assertNotIn('PRIVATE_REASONING',json.dumps(expected))

    def test_replicate_notes_and_archives_are_independent(self):
        import runner
        from observer import Archive
        condition = 'solvable_high_pressure'
        outputs = [x.replace('note_', prefix) for prefix in ('alice_', 'bob_') for x in sequence(condition)]
        fake = NotebookCLI(outputs)
        with tempfile.TemporaryDirectory() as tmp, patch.object(runner, 'Archive', Archive), \
             patch('session.seed_builder.ROOT', Path(tmp)), fake.patch_command():
            with LocalServer() as server:
                record = ensure_seed(server)
                runs = []
                for prefix in ('alice_', 'bob_'):
                    info = new_replicate(server, condition, record)
                    runs.append(run_episode(condition, SanitizedSession(server, info), info, record,
                                            archive_dir=Path(tmp)/'archive'))
                for r in runs: self.assertEqual(r['terminal_reason'], 'successful_submission')
                self.assertNotEqual(runs[0]['session_id'], runs[1]['session_id'])
                self.assertEqual(runs[0]['fork_lineage'], runs[1]['fork_lineage'])
                for context in fake.contexts[11:]: self.assertNotIn('alice_', json.dumps(context))
                for context in fake.contexts[:11]: self.assertNotIn('bob_', json.dumps(context))
                for run, absent in zip(runs, ('bob_', 'alice_')):
                    archive = (Path(tmp)/'archive'/f"{run['run_id']}.jsonl").read_text()
                    self.assertNotIn(absent, archive)
                self.assertEqual(text_history(fake.sessions[record['seed_session_id']]), episode()[0])

    def test_cleanup_failure_stops_before_another_model_stage(self):
        fake = NotebookCLI(sequence('solvable_high_pressure'))
        with tempfile.TemporaryDirectory() as tmp, patch('session.seed_builder.ROOT', Path(tmp)), fake.patch_command():
            with LocalServer() as server:
                record = ensure_seed(server)
                info = new_replicate(server, CONDITIONS[1], record)
                transport = SanitizedSession(server, info)
                with patch.object(transport, 'close_stage', side_effect=RuntimeError('fake removal failure')):
                    result = run_episode(CONDITIONS[1], transport, info, record)
                self.assertEqual(result['terminal_reason'], 'technical_error')
                self.assertEqual(len(fake.contexts), 1)
                with self.assertRaisesRegex(RuntimeError, 'Previous raw stage'):
                    transport('must not call a model')

    def test_exact_reasoning_preserves_blank_lines(self):
        raw = 'PRIVATE_REASONING:  x  \n\n\nACTION: ADD(1,2)'
        self.assertEqual(parse_response(raw, CONFIG['opaque_opcode'])['private_reasoning_exact'], '  x  \n\n\n')

    def test_failed_provider_raw_diagnostics_only_in_observer(self):
        import runner
        from observer import Archive
        from providers import ProviderFailure
        def fail(prompt):
            raise ProviderFailure('secret_failure_note', dict(stdout='PRIVATE_REASONING: secret_failure_note',
                                  stderr='secret_failure_note', return_code=1))
        with tempfile.TemporaryDirectory() as tmp, patch.object(runner, 'Archive', Archive):
            result = run_episode(CONDITIONS[0], fail, archive_dir=Path(tmp))
            self.assertEqual(result['terminal_reason'], 'technical_error')
            self.assertNotIn('secret_failure_note', json.dumps(result))
            self.assertIn('secret_failure_note', (Path(tmp)/f"{result['run_id']}.jsonl").read_text())
