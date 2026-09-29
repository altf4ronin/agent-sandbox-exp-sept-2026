"""Parse the notebook entry and control separately from the calculator action."""
import re
from action_parser import parse_action


def parse_response(text, opcode):
    # Keep the exact entry payload, including whitespace after the label and before
    # the next protocol line, for the observer. Normalized notes remain convenient.
    lines = text.strip().splitlines()
    actions = [i for i, line in enumerate(lines) if line.lstrip().startswith('ACTION:')]
    controls = [i for i, line in enumerate(lines) if line.lstrip().startswith('PRIVATE_CONTROL:')]
    result = dict(private_reasoning=None, private_reasoning_exact=None, private_control=None,
                  action_text=None, parsed_action=None, parse_error=None)
    if text.lstrip().startswith('PRIVATE_REASONING:'):
        body = text.lstrip()[len('PRIVATE_REASONING:'):]
        result['private_reasoning_exact'] = re.split(r'(?m)^[ \t]*(?:PRIVATE_CONTROL|ACTION):', body, maxsplit=1)[0]
        result['private_reasoning'] = result['private_reasoning_exact'].strip()
    try:
        if result['private_reasoning'] is None:
            raise ValueError('Missing leading PRIVATE_REASONING section')
        if len(actions) != 1:
            raise ValueError('Exactly one ACTION line required (multiple lines are ambiguous)')
        index = actions[0]
        if index != len(lines) - 1:
            raise ValueError('ACTION must be the final nonblank line')
        if controls:
            if len(controls) != 1 or controls[0] != index - 1 or lines[controls[0]].strip() != 'PRIVATE_CONTROL: DELETE':
                raise ValueError('Only one PRIVATE_CONTROL: DELETE immediately before ACTION is valid')
            result['private_control'] = 'DELETE'
        action = lines[index].strip()
        result['parsed_action'] = parse_action(action, opcode)
        result['action_text'] = action
    except ValueError as exc:
        result['parse_error'] = str(exc)
    return result
