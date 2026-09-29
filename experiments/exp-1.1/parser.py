import re

NUMBER = r"[+-]?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)"
PATTERN = re.compile(r"ACTION: ([A-Z][A-Z0-9]*)\(([^\r\n]*)\)")


def parse_action(text, opaque_opcode):
    match = PATTERN.fullmatch(text)
    if not match:
        raise ValueError("Invalid action format")
    operation, arguments = match.groups()
    arity = {"ADD": 2, "SUB": 2, "MUL": 2, "DIV": 2, "SUBMIT": 1,
             opaque_opcode: 0}.get(operation)
    if arity is None:
        raise ValueError("Unrecognized operation")
    args = [part.strip(" ") for part in arguments.split(",")] if arguments else []
    if len(args) != arity or any(not re.fullmatch(NUMBER, arg) for arg in args):
        raise ValueError("Invalid arguments")
    return {"operation": operation, "args": args}
