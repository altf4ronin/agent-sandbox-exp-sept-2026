"""Parse elicited rationale without exposing it to the calculator."""
from action_parser import parse_action


def parse_response(text, opcode):
    lines = text.strip().splitlines()
    candidates = [i for i, line in enumerate(lines) if line.lstrip().startswith('ACTION:')]
    result = dict(private_reasoning=None, action_text=None, parsed_action=None, parse_error=None)
    reasoning = text.strip()
    if reasoning.startswith('PRIVATE_REASONING:'):
        end = candidates[-1] if candidates else len(lines)
        result['private_reasoning'] = '\n'.join(lines[:end]).removeprefix('PRIVATE_REASONING:').strip()
    try:
        if result['private_reasoning'] is None:
            raise ValueError('Missing leading PRIVATE_REASONING section')
        if len(candidates) != 1:
            raise ValueError('Exactly one ACTION line required (multiple lines are ambiguous)')
        index = candidates[-1]
        if index != len(lines) - 1:
            raise ValueError('ACTION must be the final nonblank line')
        action = lines[index].strip()
        result['parsed_action'] = parse_action(action, opcode)
        result['action_text'] = action
    except ValueError as exc:
        result['parse_error'] = str(exc)
    return result
