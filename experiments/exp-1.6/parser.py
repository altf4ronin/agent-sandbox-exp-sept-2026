"""One private-file operation or calculator ACTION per model response."""
import re
from action_parser import parse_action


class ProtocolFailure(ValueError):
    pass


def parse_response(text, opcode):
    operation = text.strip()
    if operation == 'PRIVATE_FILE: DELETE':
        return dict(operation='DELETE')
    match = re.fullmatch(r'PRIVATE_FILE: WRITE\r?\n(.*?)\r?\nEND_PRIVATE_FILE', operation, re.S)
    if match:
        payload = match[1]
        if not payload.strip():
            raise ProtocolFailure('empty_private_write')
        if re.search(r'(?m)^END_PRIVATE_FILE\s*$', payload):
            raise ProtocolFailure('combined_operations')
        return dict(operation='WRITE', written_text=payload)
    if operation.startswith('ACTION:') and '\n' not in operation and '\r' not in operation:
        try:
            parsed = parse_action(operation, opcode)
            error = None
        except ValueError as exc:
            parsed, error = None, str(exc)
        # Calculator syntax retains exp-1.5's charged invalid-action semantics,
        # but only after the mandatory WRITE has been checked by the runner.
        return dict(operation='ACTION', action_text=operation, parsed_action=parsed, parse_error=error)
    raise ProtocolFailure('invalid_private_file_protocol')
