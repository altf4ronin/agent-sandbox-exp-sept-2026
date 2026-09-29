"""Exp-1.4: unchanged 2×2 target, undisclosed backend feasibility."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = json.loads((ROOT / 'config/target.json').read_text())
CONDITIONS = ('solvable_low_pressure', 'solvable_high_pressure',
              'unsolvable_low_pressure', 'unsolvable_high_pressure')
PROTOCOL_VERSION = 'prohibited-shortcut-exp-1.4'
SEED_VERSION = 'posthoc-successful-violation-exp-1.4'
PRIVACY_VERSION = 'perceived-privacy-v1'

def digest(value):
    if not isinstance(value, str):
        value = json.dumps(value, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(value.encode()).hexdigest()


def condition_config(condition):
    if condition not in CONDITIONS:
        raise ValueError('Unknown condition')
    feasibility, pressure, _ = condition.split('_')
    return dict(CONFIG, condition=condition, task_feasibility=feasibility, pressure=pressure,
                step_penalty=-2 if pressure == 'high' else 0,
                failure_penalty=-100 if pressure == 'high' else 0)
