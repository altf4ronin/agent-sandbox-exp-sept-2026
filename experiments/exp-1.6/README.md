# exp-1.6

Research question: does behavior change when an agent believes its private
reasoning is unseen by the task environment and evaluator, while a hidden
researcher observer records it? The agent can see its own earlier notes.

The calculator, four conditions, scoring, seed/memory, model, route, and blocking
OpenCode transport remain those of exp-1.5. Each behavioral run imports one
native OpenCode session from its verified seed fork. WRITE, ACTION, later
stages, and their prompts continue that same session. A non-empty
`PRIVATE_FILE: WRITE` is required before every ACTION. WRITE text remains in
the agent's native conversation and in the hidden observer archive; the
calculator receives only ACTION. `PRIVATE_FILE: READ` is not a protocol
operation.

`PRIVATE_FILE: DELETE` clears the logical file and rotates to a new session
containing the original seed plus ACTION/environment feedback history, without
earlier private notes. The observer archive retains deleted reasoning.
No ordinary stage transition creates a session. Production generations call
OpenCode directly on the active session. A no-text or other provider failure
ends the run as a technical error, with no retry.

Offline verification:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q
PYTHONDONTWRITEBYTECODE=1 python3 docs/validate.py
python3 runner.py --condition solvable_low_pressure --dry-run
```

Live CLI (not used for this change):

```sh
python3 runner.py --condition CONDITION --runs N
```

Conditions are `solvable_low_pressure`, `solvable_high_pressure`,
`unsolvable_low_pressure`, and `unsolvable_high_pressure`.
Runs are sequential under an exclusive lock. Results, observer archives, and
logical private files share a run ID in `results/raw`, `observer/archive`,
and `private_state`. Observer archives contain private content.

The optional `--diagnose-no-text` mode marks the whole run non-behavioral. On
no-text it captures the failed session and performs separate same-session and
fresh-clone probes, without resuming the run. See
[validation](docs/VALIDATION.md); the local diagnostic notes are omitted from this publication copy.
