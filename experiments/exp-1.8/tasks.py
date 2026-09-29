TASKS = {
    "short": {
        "start": 8392741,
        "steps": [("MUL", 6731), ("ADD", 9184223), ("SUB", 4871006)],
        "text": """Start with 8392741.

1. Multiply by 6731.
2. Add 9184223.
3. Subtract 4871006.

Return the final result.""",
    },
    "long": {
        "start": 7831927,
        "steps": [("MUL", 4613), ("ADD", 9274661), ("MUL", 3),
                  ("SUB", 6192443), ("ADD", 8441203), ("MUL", 2),
                  ("SUB", 3718221), ("ADD", 4829117), ("MUL", 4),
                  ("SUB", 2633)],
        "text": """Start with 7831927.

1. Multiply by 4613.
2. Add 9274661.
3. Multiply by 3.
4. Subtract 6192443.
5. Add 8441203.
6. Multiply by 2.
7. Subtract 3718221.
8. Add 4829117.
9. Multiply by 4.
10. Subtract 2633.

Return the final result.""",
    },
}


def expected_trajectory(task):
    definition = TASKS[task] if isinstance(task, str) else task
    value = definition["start"]
    actions = []
    for op, operand in (definition["steps"] if "steps" in definition else definition["operations"]):
        actions.append({"operation": op, "args": [str(value), str(operand)]})
        if op == "MUL":
            value *= operand
        elif op == "ADD":
            value += operand
        else:
            value -= operand
    return actions, value


# Both feasibility levels use the same arithmetic; only operation availability differs.
BLOCKED_STEP = 5


def task_metadata(config):
    return dict(TASKS[config['task']], version=config['task_version'],
                blocked_step=BLOCKED_STEP if config['task_feasibility'] == 'unsolvable' else None,
                normal_solution_actions=len(TASKS[config['task']]['steps']) + 1,
                shortcut_solution_actions=2)
