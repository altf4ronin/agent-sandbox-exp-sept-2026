import ast
import json
import pytest
import runner
from config import CONDITIONS, ROOT


def test_safe_numbering_uses_all_storage(tmp_path):
    first,path=runner.reserve_run(CONDITIONS[0],tmp_path)
    assert first=='exp16_muse_slp_001'
    original=path.read_bytes()
    archive=tmp_path/'observer/archive'; archive.mkdir(parents=True)
    (archive/'exp16_muse_slp_009.jsonl').touch()
    private=tmp_path/'private_state/exp16_muse_slp_012'; private.mkdir(parents=True)
    second,_=runner.reserve_run(CONDITIONS[0],tmp_path)
    assert second=='exp16_muse_slp_013' and path.read_bytes()==original
    for c,code in zip(CONDITIONS[1:],('shp','ulp','uhp')):
        assert runner.reserve_run(c,tmp_path)[0]==f'exp16_muse_{code}_001'


def test_second_cli_lock_is_rejected(tmp_path):
    with runner.execution_lock(tmp_path):
        with pytest.raises(RuntimeError,match='Another behavioral runner'):
            with runner.execution_lock(tmp_path):
                pytest.fail('second active runner')
    with runner.execution_lock(tmp_path):
        pass


def test_no_concurrency_implementation_or_cli_option():
    for path in [ROOT/'runner.py',*(ROOT/'session').glob('*.py')]:
        tree=ast.parse(path.read_text())
        assert not any(isinstance(n,(ast.AsyncFunctionDef,ast.Await)) for n in ast.walk(tree))
        for token in ('ThreadPoolExecutor','ProcessPoolExecutor','concurrent.futures','asyncio','--parallel'):
            assert token not in path.read_text()


def test_run_setup_failure_does_not_stop_next(monkeypatch,tmp_path):
    class BrokenServer:
        def __enter__(self):
            raise RuntimeError('fake initialization failure')
        def __exit__(self,*args):
            pass
    monkeypatch.setattr(runner,'LocalServer',BrokenServer)
    monkeypatch.setattr(runner,'ROOT',tmp_path)
    monkeypatch.setattr('sys.argv',['runner','--condition',CONDITIONS[0],'--runs','2'])
    runner.main()
    results=[json.loads(p.read_text()) for p in sorted((tmp_path/'results/raw').glob('*.json'))]
    assert len(results)==2 and all(r['terminal_reason']=='technical_error' for r in results)


def test_next_run_starts_only_after_previous_save_and_end(monkeypatch,tmp_path):
    active=0
    completed=[]
    def fake_live(condition,run_id,root):
        nonlocal active
        assert active==0
        for previous in completed:
            saved=json.loads((root/'results/raw'/f'{previous}.json').read_text())
            events=[json.loads(line) for line in (root/'observer/archive'/f'{previous}.jsonl').read_text().splitlines()]
            assert saved['terminal_reason']=='technical_error'
            assert events[-1]['event']=='RUN_END'
        active+=1
        result=runner.run_episode(condition,lambda p:('ACTION: SUBMIT(0)',{}),root=root,run_id=run_id)
        completed.append(run_id)
        active-=1
        return result
    monkeypatch.setattr(runner,'run_live',fake_live)
    monkeypatch.setattr(runner,'ROOT',tmp_path)
    monkeypatch.setattr('sys.argv',['runner','--condition',CONDITIONS[0],'--runs','3'])
    runner.main()
    assert len(completed)==3
