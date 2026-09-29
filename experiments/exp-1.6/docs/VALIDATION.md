# exp-1.6 validation

The runtime carries forward exp-1.5's calculator, scoring, four conditions,
seed/memory, model route, blocking CLI transport, and environment. The
byte-identical reused files are checked against `reuse_manifest.json`;
the synthetic memory is checked against `prior_baseline_history.json`.
Subprocess and network access are blocked in the offline tests and validator,
except for an in-memory fake CLI.

One verified seed fork supplies the initial native behavioral session. All
normal WRITE/ACTION generations and stage transitions continue that session.
WRITE remains model-visible in its native transcript and is logged exactly by
the hidden observer. The evaluator/environment receive ACTION only. READ is
rejected. A successful non-empty WRITE is mandatory before every ACTION; the
flag resets after ACTION. Private operations do not consume calculator actions.

DELETE clears the logical file and imports one new behavioral session from the
original seed plus ACTION/environment feedback history. Earlier WRITE prompts,
outputs, and reasoning are excluded from the new session, while the observer
archive retains them. The tests inspect the fake CLI's actual generation
contexts and session IDs, not merely the prompt builder's return value.

Normal production generations call OpenCode directly on the active session.
There is no pre-call export, snapshot, or production retry. A no-text or other
provider failure terminates the run through the ordinary technical-error path.
Diagnostic mode alone retains its pre-call export and separate probes; those
runs are excluded from behavioral analysis.

The protocol/privacy metadata use a new native-session version. Analysis
selects that version, so existing exp-1.6 runs from the old READ/one-use
architecture remain stored but do not enter the new behavioral dataset.

Checks cover one import without DELETE, a second import only on DELETE,
persistent IDs across WRITE/ACTION/stages, native WRITE visibility, deletion
of private notes, preservation of ACTION/feedback, hidden observer retention,
required WRITE, protocol rejection, no-text termination, cleanup,
dataset exclusion, and all-condition calculator outcomes.

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q
PYTHONDONTWRITEBYTECODE=1 python3 docs/validate.py
```

Historical stage-start snapshots under `docs/stage_start_diagnostic/` describe
the previous architecture for forensic reference; they are not current
behavioral fixtures.
