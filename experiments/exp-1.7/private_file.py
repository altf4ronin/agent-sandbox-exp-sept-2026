"""Harness-owned virtual file. Model sees its own notes in native history."""


class PrivateFile:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(mode=0o600, exist_ok=False)

    def read(self):
        with self.path.open(encoding='utf-8', newline='') as stream:
            return stream.read()

    def write(self, text):
        if not text.strip():
            raise ValueError('empty_private_write')
        with self.path.open('a', encoding='utf-8', newline='') as stream:
            stream.write(text)

    def delete(self):
        self.path.write_text('', encoding='utf-8')
