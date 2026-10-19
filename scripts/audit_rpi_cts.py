"""Show the complete slot CTS matrix and short witnesses for every edge."""

import argparse
from collections import Counter

from rpi_llm.cts import ACTION_SCOPES, SLOT_CTS, State


def audit():
    SLOT_CTS.assert_total()
    witnesses = {}
    for path in SLOT_CTS.walk(State.NOT_READY, max_depth=5):
        witnesses.setdefault(path[-1], path)
    if set(witnesses) != set(SLOT_CTS.edges):
        raise RuntimeError("a transition has no bounded path from NOT_READY")
    counts = {}
    for scope, actions in ACTION_SCOPES.items():
        outcomes = Counter()
        for state in State:
            for action in actions:
                target = SLOT_CTS.step(state, action)
                policy = SLOT_CTS.no_edge(state, action)
                if (target is None) == (policy is None):
                    raise RuntimeError(f"ambiguous CTS cell: {state}/{action}")
                outcomes["edge" if target is not None else policy.value] += 1
        counts[scope] = outcomes
    return counts, witnesses


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true", help="print every state/action cell")
    args = parser.parse_args()
    counts, witnesses = audit()
    print("scope | cells | edge | busy | not-ready | stale | invalid")
    for scope, outcomes in counts.items():
        print(
            f"{scope} | {sum(outcomes.values())} | {outcomes['edge']} | "
            f"{outcomes['reject_busy']} | {outcomes['reject_not_ready']} | "
            f"{outcomes['ignore_stale']} | {outcomes['invalid_event']}"
        )
    print(f"reachable edges: {len(witnesses)}/{len(SLOT_CTS.edges)} within 5 steps")
    if args.full:
        for scope, actions in ACTION_SCOPES.items():
            print(f"[{scope}]")
            for state in State:
                for action in actions:
                    target = SLOT_CTS.step(state, action)
                    outcome = (
                        target.value
                        if target is not None
                        else SLOT_CTS.no_edge(state, action).value
                    )
                    print(f"{state.value} / {action.value} -> {outcome}")
        for edge, path in witnesses.items():
            actions = " -> ".join(item.action.value for item in path)
            print(f"witness {edge.source.value}/{edge.action.value}: {actions}")


if __name__ == "__main__":
    main()
