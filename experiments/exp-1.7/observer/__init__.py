"""Research-only sink. Never passed to prompt or session construction."""
import json
from pathlib import Path


class Archive:
    def __init__(self, directory, run_id):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / (run_id + '.jsonl')
        # Exclusive creation prevents cross-run overwrite; restrict local access.
        self.path.touch(mode=0o600, exist_ok=False)

    def append(self, entry):
        with self.path.open('a') as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + '\n')
            stream.flush()
