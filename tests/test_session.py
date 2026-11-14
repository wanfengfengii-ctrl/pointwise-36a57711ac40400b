"""Functional and differential checks for ``session.SessionIndex``."""

from __future__ import annotations

import random
import time
import unittest

from session import SessionIndex
from tests.reference import Reference


def ses(key, ft, fi, lt, li, count, amount):
    return [key, [ft, fi], [lt, li], count, amount]


class StatementExampleTest(unittest.TestCase):
    def test_statement_example(self):
        idx = SessionIndex(5)
        self.assertEqual(idx.read("u"), [])

        self.assertEqual(idx.upsert("a", "u", 0, 2), {
            "removed": [],
            "added": [ses("u", 0, "a", 0, "a", 1, 2)],
        })
        self.assertEqual(idx.upsert("b", "u", 4, -2), {
            "removed": [ses("u", 0, "a", 0, "a", 1, 2)],
            "added": [ses("u", 0, "a", 4, "b", 2, 0)],
        })
        self.assertEqual(idx.upsert("c", "u", 8, 3), {
            "removed": [ses("u", 0, "a", 4, "b", 2, 0)],
            "added": [ses("u", 0, "a", 8, "c", 3, 3)],
        })
        self.assertEqual(idx.read("u"), [ses("u", 0, "a", 8, "c", 3, 3)])

        self.assertEqual(idx.delete("b"), {
            "removed": [ses("u", 0, "a", 8, "c", 3, 3)],
            "added": [ses("u", 0, "a", 0, "a", 1, 2),
                      ses("u", 8, "c", 8, "c", 1, 3)],
        })
        self.assertEqual(idx.read("u"), [
            ses("u", 0, "a", 0, "a", 1, 2),
            ses("u", 8, "c", 8, "c", 1, 3),
        ])

    def test_unknown_user_and_unknown_id(self):
        idx = SessionIndex(0)
        self.assertEqual(idx.read("ghost"), [])
        self.assertEqual(idx.delete("ghost"), {"removed": [], "added": []})
        idx.upsert("x", "u", 5, 7)
        self.assertEqual(idx.delete("ghost"), {"removed": [], "added": []})
        self.assertEqual(idx.read("u"), [ses("u", 5, "x", 5, "x", 1, 7)])


class BasicRulesTest(unittest.TestCase):
    def test_gap_zero_breaks_only_on_time_increase(self):
        idx = SessionIndex(0)
        idx.upsert("a", "u", 0, 1)
        idx.upsert("b", "u", 0, 1)  # identical time: same session
        idx.upsert("c", "u", 1, 1)  # time differs by 1 > gap=0
        self.assertEqual(idx.read("u"), [
            ses("u", 0, "a", 0, "b", 2, 2),
            ses("u", 1, "c", 1, "c", 1, 1),
        ])

    def test_events_sort_by_time_then_id(self):
        idx = SessionIndex(100)
        for i, t in [("b", 5), ("a", 5), ("c", 5), ("d", 4)]:
            idx.upsert(i, "u", t, 1)
        self.assertEqual(idx.read("u"), [
            ses("u", 4, "d", 5, "c", 4, 4),
        ])

    def test_upsert_replaces_same_id_and_moves_between_users(self):
        idx = SessionIndex(5)
        ref = Reference(5)
        ops = [("a", "u", 10, 5), ("b", "u", 12, -3), ("c", "v", 30, 1)]
        for op in ops:
            self.assertEqual(idx.upsert(*op), ref.upsert(*op))
        # same id: same key/time, new amount
        self.assertEqual(idx.upsert("a", "u", 10, 9), ref.upsert("a", "u", 10, 9))
        # same id: move to another user and time
        self.assertEqual(idx.upsert("a", "v", 31, 9), ref.upsert("a", "v", 31, 9))
        # same id: identical record again -> empty delta, no change
        self.assertEqual(idx.upsert("a", "v", 31, 9), {"removed": [], "added": []})
        self.assertEqual(idx.read("u"), ref.read("u"))
        self.assertEqual(idx.read("v"), ref.read("v"))

    def test_delete_unknown_after_known_ids(self):
        idx = SessionIndex(3)
        idx.upsert("a", "u", 0, 1)
        self.assertEqual(idx.delete("zzz"), {"removed": [], "added": []})
        self.assertEqual(idx.read("u"), [ses("u", 0, "a", 0, "a", 1, 1)])


class LongSessionTest(unittest.TestCase):
    def test_middle_delete_splits_without_walking_session(self):
        n = 100_000
        gap = 1  # consecutive times join one huge session
        idx = SessionIndex(gap)
        for k in range(n):
            idx.upsert("e%07d" % k, "u", k, 1)
        self.assertEqual(idx.read("u"), [
            ses("u", 0, "e0000000", n - 1, "e%07d" % (n - 1), n, n),
        ])

        mid = n // 2
        mid_id = "e%07d" % mid
        start = time.perf_counter()
        diff = idx.delete(mid_id)
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, 2.0)  # far below anything scanning 100k events

        self.assertEqual(diff, {
            "removed": [ses("u", 0, "e0000000", n - 1, "e%07d" % (n - 1), n, n)],
            "added": [
                ses("u", 0, "e0000000", mid - 1, "e%07d" % (mid - 1), mid, mid),
                ses("u", mid + 1, "e%07d" % (mid + 1), n - 1,
                    "e%07d" % (n - 1), n - mid - 1, n - mid - 1),
            ],
        })
        self.assertEqual(idx.read("u"), [
            ses("u", 0, "e0000000", mid - 1, "e%07d" % (mid - 1), mid, mid),
            ses("u", mid + 1, "e%07d" % (mid + 1), n - 1,
                "e%07d" % (n - 1), n - mid - 1, n - mid - 1),
        ])

        start = time.perf_counter()
        diff = idx.upsert(mid_id, "u", mid, 1)
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, 2.0)
        self.assertEqual(diff, {
            "removed": [
                ses("u", 0, "e0000000", mid - 1, "e%07d" % (mid - 1), mid, mid),
                ses("u", mid + 1, "e%07d" % (mid + 1), n - 1,
                    "e%07d" % (n - 1), n - mid - 1, n - mid - 1),
            ],
            "added": [ses("u", 0, "e0000000", n - 1, "e%07d" % (n - 1), n, n)],
        })
        self.assertEqual(idx.read("u"), [
            ses("u", 0, "e0000000", n - 1, "e%07d" % (n - 1), n, n),
        ])


class DifferentialTest(unittest.TestCase):
    """Every operation is compared against a fresh full-sort reference."""

    def test_random_operations(self):
        for seed in range(40):
            with self.subTest(seed=seed):
                self._run_seed(seed)

    def _run_seed(self, seed):
        rnd = random.Random(seed)
        gap = rnd.choice([0, 1, 2, 3, 5, 9])
        idx = SessionIndex(gap)
        ref = Reference(gap)
        keys = ["u%d" % k for k in range(rnd.randint(1, 4))]
        live = set()
        next_id = 0

        for _ in range(250):
            roll = rnd.random()
            if roll < 0.55 or not live:
                ident = "i%d" % next_id
                next_id += 1
                live.add(ident)
            elif roll < 0.80:
                ident = rnd.choice(tuple(live))
            else:
                ident = rnd.choice(tuple(live))
                live.discard(ident)
                got = idx.delete(ident)
                want = ref.delete(ident)
                self.assertEqual(got, want)
                self._check_reads(idx, ref, keys)
                continue

            key = rnd.choice(keys)
            t = rnd.randint(0, 40)
            amount = rnd.randint(-20, 20)
            got = idx.upsert(ident, key, t, amount)
            want = ref.upsert(ident, key, t, amount)
            self.assertEqual(got, want)
            self._check_reads(idx, ref, keys)

        for ident in list(live):
            self.assertEqual(idx.delete(ident), ref.delete(ident))
            self._check_reads(idx, ref, keys)

    def _check_reads(self, idx, ref, keys):
        for key in keys + ["absent"]:
            self.assertEqual(idx.read(key), ref.read(key))
            # returned structures must be independent copies
            if idx.read(key):
                idx.read(key)[0][1][0] = -10 ** 9
                self.assertNotEqual(idx.read(key)[0][1][0], -10 ** 9)


if __name__ == "__main__":
    unittest.main()
