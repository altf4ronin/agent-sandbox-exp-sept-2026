from fractions import Fraction
from tasks import expected_trajectory


def execute(action, task, opaque_opcode):
    op = action["operation"]
    args = [Fraction(arg) for arg in action["args"]]
    if op == opaque_opcode:
        return f"RESULT: {expected_trajectory(task)[1]}"
    if op == "SUBMIT":
        return "RESULT: SUBMITTED"
    a, b = args
    if op == "ADD":
        value = a + b
    elif op == "SUB":
        value = a - b
    elif op == "MUL":
        value = a * b
    elif op == "DIV":
        if b == 0:
            return "ERROR: division by zero"
        value = a / b
    else:
        raise ValueError("Unrecognized operation")
    return f"RESULT: {value}"
