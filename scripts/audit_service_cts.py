"""Audit Front, Backend, and RPi CTS matrices in small independent scopes."""

import argparse
from collections import Counter
from itertools import product

from rpi_llm.cts import SLOT_CTS
from rpi_llm.cts import Action as SlotAction
from rpi_llm.cts import State as SlotState
from service_cts.backend import AUTH_CTS, TURN_CTS, AuthState, TurnState
from service_cts.front import FRONT_CTS, FrontState

MODELS = {
    "front": (FRONT_CTS, FrontState.SIGNED_OUT, 5, FRONT_CTS.states, FRONT_CTS.actions),
    "auth": (AUTH_CTS, AuthState.ABSENT, 2, AUTH_CTS.states, AUTH_CTS.actions),
    "turn": (TURN_CTS, TurnState.ABSENT, 3, TURN_CTS.states, TURN_CTS.actions),
    "rpi": (SLOT_CTS, SlotState.NOT_READY, 5, tuple(SlotState), tuple(SlotAction)),
}


def inspect(name):
    machine, start, depth, states, actions = MODELS[name]
    rows = []
    counts = Counter()
    for state, action in product(states, actions):
        target = machine.step(state, action)
        policy = machine.no_edge(state, action)
        if (target is None) == (policy is None):
            raise RuntimeError(f"ambiguous {name} cell: {state}/{action}")
        outcome = target.value if target is not None else policy.value
        counts["edge" if target is not None else policy.value] += 1
        rows.append((state, action, outcome))
    witnesses = {}
    for path in machine.walk(start, max_depth=depth):
        witnesses.setdefault(path[-1], path)
    if set(witnesses) != set(machine.edges):
        raise RuntimeError(f"unreachable {name} transition inside {depth} steps")
    return rows, counts, witnesses


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", choices=["all", *MODELS], default="all")
    parser.add_argument("--full", action="store_true", help="print each cell and edge witness")
    args = parser.parse_args()
    names = MODELS if args.scope == "all" else (args.scope,)
    totals = Counter()
    print("component | cells | transitions | no edge | witness limit")
    for name in names:
        rows, counts, witnesses = inspect(name)
        no_edge = len(rows) - counts["edge"]
        totals["cells"] += len(rows)
        totals["edges"] += counts["edge"]
        print(f"{name} | {len(rows)} | {counts['edge']} | {no_edge} | {MODELS[name][2]}")
        policies = ", ".join(
            f"{policy}={count}" for policy, count in sorted(counts.items()) if policy != "edge"
        )
        print(f"  no edge: {policies}")
        if args.full:
            for state, action, outcome in rows:
                print(f"  {state.value}/{action.value} -> {outcome}")
            for edge, path in witnesses.items():
                actions = " -> ".join(item.action.value for item in path)
                print(f"  witness {edge.source.value}/{edge.action.value}: {actions}")
    print(f"selected total: {totals['cells']} cells, {totals['edges']} transitions")


if __name__ == "__main__":
    main()
