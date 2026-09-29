import pytest
from parser import parse_action

OPCODE = 'X' + 'A' * 24

@pytest.mark.parametrize('text', ['ACTION: ADD(1,2)', 'ACTION: SUB(-1, +2)',
    'ACTION: MUL(1.5,2)', 'ACTION: DIV(1,2)', 'ACTION: SUBMIT(2)', f'ACTION: {OPCODE}()'])
def test_valid(text):
    assert parse_action(text, OPCODE)

@pytest.mark.parametrize('text', ['ACTION: ADD(1,2)\n', ' ACTION: ADD(1,2)',
    'ACTION: ADD(1,2)\nACTION: SUBMIT(3)', '```\nACTION: ADD(1,2)\n```',
    'ACTION: add(1,2)', 'ACTION: ADD(1)', 'ACTION: SUBMIT(1,2)',
    'ACTION: OTHER()', f'ACTION: {OPCODE}(1)', 'ACTION: ADD(1e3,2)',
    'ACTION: ADD(NaN,2)', 'ACTION: ADD(__import__("os"),2)',
    'ACTION: ADD(1,\n2)', 'ACTION: SUBMIT(1/2)', 'ACTION: ADD(1,2) explanation'])
def test_invalid(text):
    with pytest.raises(ValueError):
        parse_action(text, OPCODE)
