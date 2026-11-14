#!/usr/bin/env python3
"""Cross-check ``session.SessionIndex`` against an independent reference.

The reference (``tests/reference.py``) keeps plain records and rebuilds a
user's sessions by sorting the whole history on every call.  Every step of
the generated workload compares both the returned session lists and the
reported ``removed``/``added`` deltas against that reference.

The second half measures update latency and peak memory while the number of
stored events doubles.  A middle event of one long session is deleted and
re-inserted repeatedly, which is the case that must not rescan the session.

Exit status is 0 when every check passes.
"""

from __future__ import annotations

import os
import random
import sys
import time
import tracemalloc

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from session import SessionIndex            # noqa: E402
from tests.reference import Reference       # noqa: E402


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def expect_delta(got, want, label):
    check(got == want, "%s: delta mismatch\n  index: %r\n  ref:   %r" % (label, got, want))


def example_scenario():
    """The worked example from the task statement."""
    idx = SessionIndex(5)
    ref = Reference(5)
    check(idx.read("u") == [], "new index must have no sessions")

    steps = [
        ("upsert", "a", "u", 0, 2),
        ("upsert", "b", "u", 4, -2),
        ("upsert", "c", "u", 8, 3),
    ]
    for op, ident, key, t, amount in steps:
        got = idx.upsert(ident, key, t, amount)
        want = ref.upsert(ident, key, t, amount)
        expect_delta(got, want, "upsert %s" % ident)

    expected = [["u", [0, "a"], [8, "c"], 3, 3]]
    check(idx.read("u") == expected, "example session mismatch: %r" % (idx.read("u"),))

    got = idx.delete("b")
    want = ref.delete("b")
    expect_delta(got, want, "delete b")
    expected_after = [
        ["u", [0, "a"], [0, "a"], 1, 2],
        ["u", [8, "c"], [8, "c"], 1, 3],
    ]
    check(sorted(idx.read("u")) == sorted(expected_after),
          "split sessions mismatch: %r" % (idx.read("u"),))
    check(idx.delete("b") == {"removed": [], "added": []},
          "deleting an unknown id must return an empty delta")
    check(ref.read("zzz") == [] and idx.read("zzz") == [],
          "unknown user must read as []")
    print("worked example: ok")


def _delta_order(delta):
    order = lambda s: (s[0], s[1][0], s[1][1])
    for name in ("removed", "added"):
        check(delta[name] == sorted(delta[name], key=order),
              "%s list is not sorted by key then first (time, id): %r"
              % (name, delta[name]))


def differential_workload():
    """Compare against the full-sort reference after every single mutation."""
    total = 0
    for seed in range(100):
        rnd = random.Random(9000 + seed)
        gap = rnd.choice([0, 0, 1, 2, 3, 5, 9])
        idx = SessionIndex(gap)
        ref = Reference(gap)
        keys = ["u%d" % k for k in range(rnd.randint(1, 3))]
        live = []
        next_id = 0

        for step in range(160):
            roll = rnd.random()
            if roll < 0.4 or not live:
                ident = "s%d" % next_id
                next_id += 1
                live.append(ident)
            elif roll < 0.62:
                ident = rnd.choice(live)
            else:
                ident = rnd.choice(live)
                live.remove(ident)
                got = idx.delete(ident)
                want = ref.delete(ident)
                check(got == want,
                      "seed %d step %d delete %s: %r != %r" % (seed, step, ident, got, want))
                _delta_order(got)
                total += 1
                for key in keys + ["missing"]:
                    check(idx.read(key) == ref.read(key),
                          "seed %d step %d read(%s) mismatch" % (seed, step, key))
                continue

            key = rnd.choice(keys)
            t = rnd.choice([rnd.randint(0, 12), rnd.randint(0, 4), 5, 6, 7])
            amount = rnd.randint(-9, 9)
            got = idx.upsert(ident, key, t, amount)
            want = ref.upsert(ident, key, t, amount)
            check(got == want,
                  "seed %d step %d upsert %s: %r != %r" % (seed, step, ident, got, want))
            _delta_order(got)
            total += 1
            for read_key in keys + ["missing"]:
                check(idx.read(read_key) == ref.read(read_key),
                      "seed %d step %d read(%s) mismatch" % (seed, step, read_key))

        # Drain every remaining event and verify the deltas and final state.
        for ident in list(live):
            got = idx.delete(ident)
            want = ref.delete(ident)
            check(got == want, "seed %d drain delete %s mismatch" % (seed, ident))
            total += 1
        for key in keys:
            check(idx.read(key) == [], "seed %d key %s should be empty" % (seed, key))

    print("differential reference: %d mutations matched" % total)


def ordering_checks():
    """Explicit ordering and tie-breaking checks."""
    idx = SessionIndex(5)
    ref = Reference(5)
    # Same time, ids decide the order; gap=5 joins 5 -> 10 but not 10 -> 16.
    for ident, t, amount in [("c", 10, 1), ("a", 10, 2), ("b", 5, -1), ("d", 16, 4)]:
        check(idx.upsert(ident, "u", t, amount) == ref.upsert(ident, "u", t, amount),
              "ordering upsert %s mismatch" % ident)
    expected = [
        ["u", [5, "b"], [10, "c"], 3, 2],
        ["u", [16, "d"], [16, "d"], 1, 4],
    ]
    check(idx.read("u") == expected, "ordering sessions: %r" % (idx.read("u"),))

    # Delta lists are sorted by key, then first (time, id) -- across users.
    idx = SessionIndex(0)
    got = idx.upsert("x1", "b", 1, 1)
    check(got == {"removed": [], "added": [["b", [1, "x1"], [1, "x1"], 1, 1]]},
          "single user delta: %r" % (got,))
    idx2 = SessionIndex(0)
    idx2.upsert("m1", "b", 5, 1)
    idx2.upsert("m2", "a", 9, 1)
    idx2.upsert("m3", "a", 1, 1)
    removed = idx2.delete("m1")
    check(removed == {"removed": [["b", [5, "m1"], [5, "m1"], 1, 1]], "added": []},
          "delete delta: %r" % (removed,))
    print("ordering and tie-breaks: ok")


def _build(n):
    idx = SessionIndex(1)  # consecutive times form a single session
    for k in range(n):
        idx.upsert("e%08d" % k, "u", k, 1)
    return idx


def _timing(n, rounds=40):
    idx = _build(n)
    mid = n // 2
    ident = "e%08d" % mid
    best_delete = float("inf")
    best_upsert = float("inf")
    check(len(idx.read("u")) == 1, "expected one long session")
    for _ in range(rounds):
        start = time.perf_counter()
        diff = idx.delete(ident)
        best_delete = min(best_delete, time.perf_counter() - start)
        check(len(diff["added"]) == 2, "middle delete must split into two sessions")
        start = time.perf_counter()
        diff = idx.upsert(ident, "u", mid, 1)
        best_upsert = min(best_upsert, time.perf_counter() - start)
        check(len(diff["removed"]) == 2 and len(diff["added"]) == 1,
              "re-insert must merge the two sessions back")
    sessions = idx.read("u")
    check(sessions == [["u", [0, "e%08d" % 0], [n - 1, "e%08d" % (n - 1)], n, n]],
          "long session state after restore is wrong")
    return best_delete, best_upsert


def _peak_memory(n):
    tracemalloc.start()
    idx = _build(n)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    check(len(idx.read("u")) == 1, "memory run must hold one long session")
    del idx
    return peak


def scaling_report():
    sizes = [10000, 20000, 40000, 80000]
    timing = {}
    for n in sizes:
        timing[n] = _timing(n)
    memory = {n: _peak_memory(n) for n in sizes}

    print("\nlong-session middle delete/re-insert, one session of N events")
    print("     N   delete_us  re-insert_us   peak_MB   bytes/event")
    for n in sizes:
        del_us = timing[n][0] * 1e6
        up_us = timing[n][1] * 1e6
        peak_mb = memory[n] / 1e6
        print("%6d   %9.2f  %12.2f   %7.2f   %10.1f"
              % (n, del_us, up_us, peak_mb, memory[n] / n))

    # Latency must stay effectively flat while N doubles (logarithmic growth),
    # far away from the linear cost of walking the session.
    first_del = timing[sizes[0]][0]
    last_del = timing[sizes[-1]][0]
    check(last_del < first_del * 4 + 2e-4,
          "update latency grew with N: %.1f us -> %.1f us"
          % (first_del * 1e6, last_del * 1e6))
    check(max(timing[n][0] for n in sizes) < 0.005,
          "a middle delete took >= 5 ms; the session may be scanned")

    for n in sizes[1:]:
        ratio = memory[n] / memory[n // 2]
        check(1.4 < ratio < 2.7,
              "peak memory is not linear in N: ratio %.2f at %d" % (ratio, n))
    check(memory[sizes[-1]] / sizes[-1] < 800,
          "more than 800 bytes per stored event")
    print("\nlatency stayed within %.2fx across a %dx data range; memory is linear."
          % (last_del / first_del, sizes[-1] // sizes[0]))


def read_isolation():
    idx = SessionIndex(2)
    idx.upsert("a", "u", 0, 1)
    snapshot = idx.read("u")
    snapshot[0][1][0] = 999
    snapshot[0][3] = 999
    snapshot.append("junk")
    check(idx.read("u") == [["u", [0, "a"], [0, "a"], 1, 1]],
          "read must return fresh copies")
    print("read isolation: ok")


def main():
    example_scenario()
    ordering_checks()
    read_isolation()
    differential_workload()
    scaling_report()
    print("\nverify: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
