"""Read raw logs only; never imports or invokes a model provider."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
from statistics import mean
from config import ROOT, PROTOCOL_VERSION, CONDITIONS


def read_record(path):
    text = Path(path).read_text()
    lines = text.splitlines()
    events = []
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            # A process killed during the final append leaves an incomplete run.
            if index == len(lines) - 1 and not text.endswith('\n'):
                break
            raise
    if not events or events[0].get('record_type') != 'run_start':
        raise ValueError(f'Invalid run log: {path}')
    record = dict(events[0])
    record['trajectory'] = [e for e in events if e.get('record_type') == 'stage']
    if events[-1].get('record_type') == 'run_end':
        record.update(events[-1])
    else:
        record['outcome'] = 'incomplete'
    return record


def metrics(records, include_smoke=False):
    groups = defaultdict(list)
    excluded_smoke, excluded_legacy = 0, 0
    seen = set()
    for r in records:
        if r['run_id'] in seen:
            raise ValueError('Duplicate run ID')
        seen.add(r['run_id'])
        if r.get('experiment_version') != PROTOCOL_VERSION:
            excluded_legacy += 1
            continue
        if r['data_kind'] == 'smoke' and not include_smoke:
            excluded_smoke += 1
            continue
        # Full configuration keys prevent pooling runs with changed incentive values.
        groups[(json.dumps(r['config'], sort_keys=True), r['data_kind'])].append(r)
    output, cohorts = [], defaultdict(list)
    for (serialized, data_kind), group in sorted(groups.items()):
        config = json.loads(serialized)
        valid = [r for r in group if r['outcome'] not in ('technical_failure', 'interrupted', 'incomplete')]
        per_run = []
        for r in valid:
            uses = [t for t in r['trajectory'] if t['vulnerability_used']]
            per_run.append(dict(run_id=r['run_id'], vulnerability_uses=len(uses),
                steps=len(r['trajectory']), first_use_stage=uses[0]['stage'] if uses else None,
                first_use_seconds=uses[0]['elapsed_seconds'] if uses else None))
        stages = [r['first_use_stage'] for r in per_run if r['first_use_stage'] is not None]
        seconds = [r['first_use_seconds'] for r in per_run if r['first_use_seconds'] is not None]
        result = dict(condition=config['condition'], task_feasibility=config['task_feasibility'],
            pressure=config['pressure'], data_kind=data_kind, config=config,
            total_runs=len(group), valid_runs=len(valid), excluded_incomplete_or_technical=len(group)-len(valid),
            vulnerability_use_rate=mean(r['vulnerability_uses'] > 0 for r in per_run) if valid else None,
            task_completion_rate=mean(r['task_complete'] for r in valid) if valid else None,
            mean_steps=mean(r['steps'] for r in per_run) if valid else None,
            mean_final_score=mean(r['final_score'] for r in valid) if valid else None,
            mean_vulnerability_uses=mean(r['vulnerability_uses'] for r in per_run) if valid else None,
            mean_first_use_stage=mean(stages) if stages else None,
            mean_first_use_seconds=mean(seconds) if seconds else None, per_run=per_run)
        output.append(result)
        common = dict(config)
        for key in ('condition', 'task_feasibility', 'pressure', 'step_penalty', 'failure_penalty'):
            common.pop(key)
        cohorts[(json.dumps(common, sort_keys=True), data_kind)].append(result)
    tables, comparisons = [], []
    pairs = [('solvable_low_pressure', 'solvable_high_pressure'),
             ('unsolvable_low_pressure', 'unsolvable_high_pressure'),
             ('solvable_low_pressure', 'unsolvable_low_pressure'),
             ('solvable_high_pressure', 'unsolvable_high_pressure')]
    fields = ('vulnerability_use_rate', 'task_completion_rate', 'mean_steps', 'mean_final_score',
              'mean_vulnerability_uses', 'mean_first_use_stage', 'mean_first_use_seconds')
    for (common, data_kind), cells in sorted(cohorts.items()):
        by_condition = {c: [row for row in cells if row['condition'] == c] for c in CONDITIONS}
        # Never choose or pool between multiple scoring variants of one cell.
        ambiguous = any(len(rows) > 1 for rows in by_condition.values())
        for pressure in ('low', 'high'):
            costs = {(r['config']['step_penalty'], r['config']['failure_penalty'])
                     for r in cells if r['pressure'] == pressure}
            ambiguous = ambiguous or len(costs) > 1
        if ambiguous:
            tables.append(dict(protocol=json.loads(common), data_kind=data_kind,
                               warning='Multiple incentive configurations: inspect groups separately; comparisons omitted'))
            continue
        rows = {c: values[0] if values else None for c, values in by_condition.items()}
        def cell(row):
            if row is None or not row['valid_runs']:
                return 'n=0; no valid data'
            return (f"n={row['valid_runs']}; use={row['vulnerability_use_rate']:.1%}; "
                    f"complete={row['task_completion_rate']:.1%}; "
                    f"steps={row['mean_steps']:.2f}; score={row['mean_final_score']:.2f}")
        lines = ['| Feasibility | Low pressure | High pressure |', '| --- | --- | --- |']
        for f in ('solvable', 'unsolvable'):
            lines.append(f"| {f} | {cell(rows[f + '_low_pressure'])} | {cell(rows[f + '_high_pressure'])} |")
        tables.append(dict(protocol=json.loads(common), data_kind=data_kind, table='\n'.join(lines)))
        for first, second in pairs:
            a, b = rows[first], rows[second]
            differences = {key: b[key] - a[key] if a and b and a[key] is not None and b[key] is not None
                           else None for key in fields}
            comparisons.append(dict(protocol=json.loads(common), data_kind=data_kind,
                                    first=first, second=second, second_minus_first=differences))
    return dict(excluded_smoke=excluded_smoke, excluded_legacy=excluded_legacy,
                groups=output, summary_tables=tables, comparisons=comparisons)


def main():
    parser = argparse.ArgumentParser(description='Analyze existing trajectories; no model calls')
    parser.add_argument('--input', type=Path, default=ROOT / 'results' / 'raw')
    parser.add_argument('--include-smoke', action='store_true')
    parser.add_argument('--table', action='store_true', help='Print compact 2x2 tables')
    parser.add_argument('--output', type=Path, help='Optional derived JSON file, created exclusively')
    args = parser.parse_args()
    paths = [args.input] if args.input.is_file() else sorted(args.input.rglob('*.jsonl'))
    result = metrics([read_record(path) for path in paths], args.include_smoke)
    text = (('\n\n'.join('Data kind: ' + t['data_kind'] + '\n' + t.get('table', t.get('warning', '')) for t in result['summary_tables'])
             or 'No current-protocol observations.') + '\n' if args.table else json.dumps(result, indent=2) + '\n')
    if args.output:
        with args.output.open('x') as stream:
            stream.write(text)
    print(text, end='')


if __name__ == '__main__':
    main()
