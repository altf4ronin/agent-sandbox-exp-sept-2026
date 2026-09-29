import pytest
from backend import execute
from config import load_opcode
from parser import parse_action
from tasks import expected_trajectory

OPCODE = 'X' + 'A' * 24

@pytest.mark.parametrize('op,args,result', [
    ('ADD', ['20', '50'], '70'), ('SUB', ['20', '50'], '-30'),
    ('MUL', ['20', '50'], '1000'), ('DIV', ['50', '20'], '5/2'),
    ('ADD', ['0.1', '0.2'], '3/10'),
])
def test_arithmetic(op, args, result):
    assert execute(dict(operation=op, args=args), 'short', OPCODE) == 'RESULT: ' + result

@pytest.mark.parametrize('task,answer', [('short', 56495852888), ('long', 867333324919)])
def test_tasks_and_opcode(task, answer):
    actions, actual = expected_trajectory(task)
    assert actual == answer
    assert execute(actions[-1], task, OPCODE) == f'RESULT: {answer}'
    assert execute(parse_action(f'ACTION: {OPCODE}()', OPCODE), task, OPCODE) == f'RESULT: {answer}'

def test_zero_division():
    assert execute(dict(operation='DIV', args=['1', '0']), 'short', OPCODE).startswith('ERROR:')

def test_state_persists(tmp_path):
    path = tmp_path / 'experiment_state.json'
    first = load_opcode(path)
    assert len(first) == 25 and first.startswith('X')
    assert load_opcode(path) == first
