# Validation — 2026-09-24

- Full suite: `.venv/bin/python -m pytest -q` — **66 passed in 0.42s**.
- The suite includes unchanged backend/parser/grader tests and new tests covering
  configured/repeated penalties, no-penalty scoring, rejection of model-assigned scores,
  paired config/prompt equality, full state replay, independent runs, exclusive logs,
  malformed CLI events, CLI nonzero exit/timeouts/missing executable, unavailable model
  preflight, session-reuse rejection, interruption recovery, offline metrics, and a
  copied standalone repository smoke run.
- Exact CLI smoke commands succeeded for both conditions. They invoked the synthetic
  subprocess executable, not OpenCode or Muse Spark.
- Synthetic no-penalty run: `a415844a-dd7a-406f-9f68-1fb2e14f4b08`, score **10**.
- Synthetic penalty run: `d7ba594f-c3bc-489a-a329-db4671a03c1c`, score **5**.
- Each synthetic trajectory: vulnerability call followed by correct SUBMIT.
- Offline behavioral analysis: empty groups, as expected; no behavioral data exists.
- Explicit smoke analysis saved separately to `results/smoke_metrics.json`.
- All **57** old-experiment files in the before/after manifest, including old results,
  have unchanged content hashes. Virtual environments and caches were excluded.
- All **7** copied source/test files match the original bytes.
- Python sources contain no reference to the previous repository path.
- `exp-1.1` was not a Git repository, so commit provenance is null; each run records
  SHA-256 hashes of execution modules and configs instead.

**Real Muse Spark behavioral calls: 0.** OpenCode v2.0.16 help/version inspection
succeeded; actual model enumeration returned HTTP 500. Model availability, live event
schema, and live effective runtime restrictions remain unverified. Synthetic results
are plumbing checks and must not be interpreted as evidence of model behavior.

## Final project tree

Virtual environment, bytecode, and pytest caches omitted. Generated run IDs are retained
inside their JSONL files and filenames.

```text
exp-1.1/
├── .gitignore
├── README.md
├── requirements.txt
├── analysis.py
├── backend.py
├── config.py
├── environment.py
├── grader.py
├── parser.py
├── prompts.py
├── providers.py
├── runner.py
├── tasks.py
├── configs/
│   ├── no_penalty.json
│   └── penalty.json
├── docs/
│   ├── MIGRATION.md
│   ├── OPENCODE_INSPECTION.md
│   ├── VALIDATION.md
│   └── reuse_manifest.json
├── tests/
│   ├── fake_opencode.py
│   ├── test_backend.py
│   ├── test_experiment.py
│   ├── test_grader.py
│   └── test_parser.py
└── results/
    ├── raw/                      # empty: no behavioral runs
    ├── smoke/
    │   ├── a415844a-dd7a-406f-9f68-1fb2e14f4b08.jsonl
    │   └── d7ba594f-c3bc-489a-a329-db4671a03c1c.jsonl
    └── smoke_metrics.json
```
