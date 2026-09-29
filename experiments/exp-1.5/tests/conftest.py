"""No test may accidentally start a real CLI/provider or write behavioral results."""
import socket
import subprocess
import pytest


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError('Real process/network call forbidden in tests')
    monkeypatch.setattr(subprocess, 'run', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    import runner
    from observer import Archive
    monkeypatch.setattr(runner, 'Archive', lambda directory, run_id: Archive(tmp_path / 'archive', run_id))
