#!/usr/bin/env python3
"""Walk through the incremental session index on small examples."""

from __future__ import annotations

import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from session import SessionIndex  # noqa: E402


def show(title):
    print("\n== %s ==" % title)


def fmt(delta):
    return "removed=%s added=%s" % (delta["removed"], delta["added"])


def main():
    show("worked example, gap = 5")
    idx = SessionIndex(5)
    print("read('u') before any event ->", idx.read("u"))
    for ident, t, amount in [("a", 0, 2), ("b", 4, -2), ("c", 8, 3)]:
        delta = idx.upsert(ident, "u", t, amount)
        print("upsert(%r, 'u', %d, %d):" % (ident, t, amount), fmt(delta))
        print("  read('u') ->", idx.read("u"))

    show("delete the middle event: one session splits in two")
    print("delete('b'):", fmt(idx.delete("b")))
    print("read('u') ->", idx.read("u"))
    print("delete('b') again (unknown id):", fmt(idx.delete("b")))

    show("same time: ids decide the order; only a gap > 5 breaks")
    idx = SessionIndex(5)
    for ident, t, amount in [("c", 10, 1), ("a", 10, 2), ("b", 5, -1), ("d", 16, 4)]:
        idx.upsert(ident, "u", t, amount)
    print("read('u') ->", idx.read("u"))
    print("difference 5 joins, difference 6 breaks (5->10 joined, 10->16 split)")

    show("upserting an existing id moves the event; deltas stay local")
    idx = SessionIndex(5)
    for ident, key, t, amount in [("x", "u", 0, 1), ("y", "u", 30, 2), ("z", "v", 10, 3)]:
        idx.upsert(ident, key, t, amount)
    print("before  u:", idx.read("u"), " v:", idx.read("v"))
    print("upsert('x', 'v', 11, 1):", fmt(idx.upsert("x", "v", 11, 1)))
    print("after   u:", idx.read("u"), " v:", idx.read("v"))

    show("one long session: a middle delete costs O(log N), not O(N)")
    n = 100_000
    gap = 1
    idx = SessionIndex(gap)
    for k in range(n):
        idx.upsert("e%08d" % k, "u", k, 1)
    print("stored %d events -> read('u') has %d session(s), each record is a summary:"
          % (n, len(idx.read("u"))))
    print("  ", idx.read("u"))
    ident = "e%08d" % (n // 2)
    start = time.perf_counter()
    delta = idx.delete(ident)
    delete_seconds = time.perf_counter() - start
    start = time.perf_counter()
    delta_back = idx.upsert(ident, "u", n // 2, 1)
    upsert_seconds = time.perf_counter() - start
    print("delete(%s) returned %d removed / %d added sessions in %.1f us"
          % (ident, len(delta["removed"]), len(delta["added"]), delete_seconds * 1e6))
    print("re-insert merged them back with %d removed / %d added in %.1f us"
          % (len(delta_back["removed"]), len(delta_back["added"]), upsert_seconds * 1e6))
    print("read('u') ->", idx.read("u"))
    print("No long session was walked and no user history was rebuilt.")

    show("multiple users are independent")
    idx = SessionIndex(5)
    for ident, key, t, amount in [("p", "u1", 0, 1), ("q", "u2", 2, 2), ("r", "u2", 9, 3)]:
        idx.upsert(ident, key, t, amount)
    print("read('u1') ->", idx.read("u1"))
    print("read('u2') ->", idx.read("u2"))
    print("read('nobody') ->", idx.read("nobody"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
