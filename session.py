"""Incremental maintenance of session facts.

One user (``key``) owns events ordered by ``(time, id)``.  Walking that
order, a new session starts whenever ``next_time - prev_time > gap``.  A
session is summarised as::

    [key, [first_time, first_id], [last_time, last_id], count, amount_sum]

The index keeps one augmented treap per user.  A treap node stores one
event and, for its whole subtree, the event count, amount sum, number of
internal session breaks and the first/last event.  From those aggregates it
is possible to jump directly to the session around any event or insertion
position in expected O(log n) time, and to read a whole user in time linear
in the number of reported sessions (plus O(log n)), never walking a long
session event by event.

An ``upsert``/``delete`` only changes sessions next to the old and the new
position, so the reported diff is produced from a constant number of
session probes.  No full user history is re-scanned and no long session is
walked, giving expected O(log(N+2)) time and O(N) space for N stored events.
"""

from __future__ import annotations

import random
from typing import Dict, List, Optional, Set, Tuple

__all__ = ["SessionIndex"]

# A session as a hashable tuple, fully ordered as required for diff sorting:
# (key, first_time, first_id, last_time, last_id, count, amount)
_Raw = Tuple[str, int, str, int, str, int, int]

# Treap priorities are random per index instance (system-seeded).  Because a
# treap's shape depends only on the (key, priority) set, unpredictable
# priorities keep the expected O(log n) bounds even for adversarially chosen
# times and insertion orders.


class _Node:
    __slots__ = (
        "t", "i", "amt", "prio", "left", "right",
        "size", "total", "breaks",
        "first_t", "first_i", "last_t", "last_i",
    )

    def __init__(self, t: int, i: str, amt: int, prio: int) -> None:
        self.t = t
        self.i = i
        self.amt = amt
        self.prio = prio
        self.left: Optional[_Node] = None
        self.right: Optional[_Node] = None
        self.size = 1
        self.total = amt
        self.breaks = 0
        self.first_t = t
        self.first_i = i
        self.last_t = t
        self.last_i = i


def _size(x: Optional[_Node]) -> int:
    return x.size if x is not None else 0


def _total(x: Optional[_Node]) -> int:
    return x.total if x is not None else 0


def _breaks(x: Optional[_Node]) -> int:
    return x.breaks if x is not None else 0


def _pull(x: _Node, gap: int) -> None:
    """Recompute all subtree aggregates of ``x``."""
    left = x.left
    right = x.right
    x.size = 1 + _size(left) + _size(right)
    x.total = x.amt + _total(left) + _total(right)

    b = _breaks(left) + _breaks(right)
    if left is not None and x.t - left.last_t > gap:
        b += 1
    if right is not None and right.first_t - x.t > gap:
        b += 1
    x.breaks = b

    if left is not None:
        x.first_t, x.first_i = left.first_t, left.first_i
    else:
        x.first_t, x.first_i = x.t, x.i
    if right is not None:
        x.last_t, x.last_i = right.last_t, right.last_i
    else:
        x.last_t, x.last_i = x.t, x.i


def _split(x: Optional[_Node], t: int, i: str, gap: int):
    """Split into keys strictly below and keys at/above ``(t, i)``."""
    if x is None:
        return None, None
    if x.t < t or (x.t == t and x.i < i):
        a, b = _split(x.right, t, i, gap)
        x.right = a
        _pull(x, gap)
        return x, b
    a, b = _split(x.left, t, i, gap)
    x.left = b
    _pull(x, gap)
    return a, x


def _merge(a: Optional[_Node], b: Optional[_Node], gap: int) -> Optional[_Node]:
    if a is None:
        return b
    if b is None:
        return a
    if a.prio < b.prio:
        a.right = _merge(a.right, b, gap)
        _pull(a, gap)
        return a
    b.left = _merge(a, b.left, gap)
    _pull(b, gap)
    return b


def _insert(x: Optional[_Node], node: _Node, gap: int) -> _Node:
    if x is None:
        return node
    if node.prio < x.prio:
        a, b = _split(x, node.t, node.i, gap)
        node.left = a
        node.right = b
        _pull(node, gap)
        return node
    if node.t < x.t or (node.t == x.t and node.i < x.i):
        x.left = _insert(x.left, node, gap)
    else:
        x.right = _insert(x.right, node, gap)
    _pull(x, gap)
    return x


def _erase(x: Optional[_Node], t: int, i: str, gap: int) -> Optional[_Node]:
    if x is None:
        return None
    if x.t == t and x.i == i:
        return _merge(x.left, x.right, gap)
    if t < x.t or (t == x.t and i < x.i):
        x.left = _erase(x.left, t, i, gap)
    else:
        x.right = _erase(x.right, t, i, gap)
    _pull(x, gap)
    return x


def _rank_less(x: Optional[_Node], t: int, i: str) -> int:
    """Number of stored events ordered below ``(t, i)`` (0-based insert rank)."""
    rank = 0
    while x is not None:
        if x.t < t or (x.t == t and x.i < i):
            rank += 1 + _size(x.left)
            x = x.right
        else:
            x = x.left
    return rank


def _kth(x: _Node, k: int) -> _Node:
    """The event at 0-based in-order rank ``k``."""
    while True:
        ls = _size(x.left)
        if k < ls:
            x = x.left
        elif k == ls:
            return x
        else:
            k -= ls + 1
            x = x.right


def _prefix_breaks(x: Optional[_Node], k: int, gap: int) -> int:
    """Number of internal session breaks among the first ``k`` events."""
    result = 0
    while x is not None and k > 0:
        left = x.left
        ls = _size(left)
        if k <= ls:
            x = left
            continue
        if left is not None:
            result += left.breaks
            if x.t - left.last_t > gap:
                result += 1
        if k == ls + 1:
            return result
        right = x.right
        if right is not None and right.first_t - x.t > gap:
            result += 1
        k -= ls + 1
        x = right
    return result


def _prefix_total(x: Optional[_Node], k: int) -> int:
    """Sum of amounts of the first ``k`` events."""
    result = 0
    while x is not None and k > 0:
        left = x.left
        ls = _size(left)
        if k <= ls:
            x = left
            continue
        result += _total(left) + x.amt
        if k == ls + 1:
            return result
        k -= ls + 1
        x = x.right
    return result


def _run_start_rank(x: _Node, c: int, gap: int) -> int:
    """0-based rank at which run number ``c`` starts.

    Runs are numbered from 0.  For ``c >= 1`` run ``c`` starts right after
    the ``c``-th session break, so ``1 <= c <= x.breaks``.
    """
    rank = 0
    node = x
    while True:
        left = node.left
        lb = _breaks(left)
        ls = _size(left)
        between_left = 1 if (left is not None and node.t - left.last_t > gap) else 0
        if c <= lb:
            node = left
            continue
        if between_left and c == lb + 1:
            return rank + ls
        offset = lb + between_left
        right = node.right
        between_right = 1 if (right is not None and right.first_t - node.t > gap) else 0
        if between_right and c == offset + 1:
            return rank + ls + 1
        c = c - offset - between_right
        rank += ls + 1
        node = right


def _raw_session(x: _Node, rank: int, gap: int) -> _Raw:
    """Session containing the event at 0-based rank ``rank``."""
    c = _prefix_breaks(x, rank + 1, gap)
    total_breaks = x.breaks
    start = 0 if c == 0 else _run_start_rank(x, c, gap)
    end = x.size - 1 if c == total_breaks else _run_start_rank(x, c + 1, gap) - 1
    first = _kth(x, start)
    last = _kth(x, end)
    amount = _prefix_total(x, end + 1) - _prefix_total(x, start)
    return (first.t, first.i, last.t, last.i, end - start + 1, amount)


def _sessions_around_event(x: Optional[_Node], rank: int, gap: int) -> Set[Tuple]:
    """Distinct sessions containing the event at ``rank`` and its neighbours."""
    if x is None:
        return set()
    out = set()
    n = x.size
    for r in (rank - 1, rank, rank + 1):
        if 0 <= r < n:
            out.add(_raw_session(x, r, gap))
    return out


def _sessions_around_position(x: Optional[_Node], rank: int, gap: int) -> Set[Tuple]:
    """Distinct sessions of the predecessor and successor at insert ``rank``."""
    if x is None:
        return set()
    out = set()
    n = x.size
    if rank > 0:
        out.add(_raw_session(x, rank - 1, gap))
    if rank < n:
        out.add(_raw_session(x, rank, gap))
    return out


def _with_key(key: str, raw: Tuple) -> _Raw:
    return (key, raw[0], raw[1], raw[2], raw[3], raw[4], raw[5])


def _as_public(raw: _Raw) -> List:
    return [
        raw[0],
        [raw[1], raw[2]],
        [raw[3], raw[4]],
        raw[5],
        raw[6],
    ]


class SessionIndex:
    """Incremental session facts for a set of users.

    ``gap`` is the largest allowed quiet time inside one session.
    """

    def __init__(self, gap: int):
        if not isinstance(gap, int) or isinstance(gap, bool):
            raise TypeError("gap must be an integer")
        if gap < 0:
            raise ValueError("gap must be non-negative")
        self.gap = gap
        self._records: Dict[str, Tuple[str, int, int]] = {}  # id -> (key, time, amount)
        self._roots: Dict[str, Optional[_Node]] = {}        # key -> treap root
        self._rng = random.Random()

    # -- internal mutation ------------------------------------------------
    def _priority(self) -> int:
        return self._rng.getrandbits(64)

    def _insert_event(self, key: str, t: int, i: str, amount: int) -> None:
        root = _insert(self._roots.get(key), _Node(t, i, amount, self._priority()), self.gap)
        self._roots[key] = root

    def _erase_event(self, key: str, t: int, i: str) -> None:
        root = _erase(self._roots.get(key), t, i, self.gap)
        if root is None:
            self._roots.pop(key, None)
        else:
            self._roots[key] = root

    @staticmethod
    def _diff(before: Set[_Raw], after: Set[_Raw]) -> dict:
        removed = sorted(before - after)
        added = sorted(after - before)
        return {"removed": [_as_public(r) for r in removed],
                "added": [_as_public(r) for r in added]}

    # -- public API -------------------------------------------------------
    def upsert(self, id: str, key: str, time: int, amount: int) -> dict:
        """Insert or move/replace the event ``id``; return the session delta."""
        old = self._records.get(id)
        if old is not None and old == (key, time, amount):
            return {"removed": [], "added": []}

        before: Set[_Raw] = set()
        if old is not None:
            old_key, old_time, _ = old
            old_root = self._roots.get(old_key)
            old_rank = _rank_less(old_root, old_time, id)
            for raw in _sessions_around_event(old_root, old_rank, self.gap):
                before.add(_with_key(old_key, raw))

        # Sessions touched by the insertion in the pre-update state.
        new_root = self._roots.get(key)
        new_rank = _rank_less(new_root, time, id)
        for raw in _sessions_around_position(new_root, new_rank, self.gap):
            before.add(_with_key(key, raw))

        if old is not None:
            self._erase_event(old[0], old[1], id)
        self._insert_event(key, time, id, amount)
        self._records[id] = (key, time, amount)

        after: Set[_Raw] = set()
        if old is not None:
            old_key, old_time, _ = old
            # Position where the removed event used to be, in the final state.
            root = self._roots.get(old_key)
            rank = _rank_less(root, old_time, id)
            for raw in _sessions_around_position(root, rank, self.gap):
                after.add(_with_key(old_key, raw))

        root = self._roots[key]
        rank = _rank_less(root, time, id)
        for raw in _sessions_around_event(root, rank, self.gap):
            after.add(_with_key(key, raw))

        return self._diff(before, after)

    def delete(self, id: str) -> dict:
        """Remove the event ``id``; return the session delta."""
        old = self._records.get(id)
        if old is None:
            return {"removed": [], "added": []}
        key, t, _ = old

        root = self._roots.get(key)
        rank = _rank_less(root, t, id)
        before = {_with_key(key, raw)
                  for raw in _sessions_around_event(root, rank, self.gap)}

        self._erase_event(key, t, id)
        del self._records[id]

        root = self._roots.get(key)
        rank = _rank_less(root, t, id)
        after = {_with_key(key, raw)
                 for raw in _sessions_around_position(root, rank, self.gap)}
        return self._diff(before, after)

    def read(self, key: str) -> List:
        """All sessions of ``key`` in first-event order (empty if unknown)."""
        root = self._roots.get(key)
        if root is None:
            return []
        result = []
        total_breaks = root.breaks
        size = root.size
        start = 0
        while start < size:
            run = _prefix_breaks(root, start + 1, self.gap)
            end = size - 1 if run == total_breaks else _run_start_rank(root, run + 1, self.gap) - 1
            first = _kth(root, start)
            last = _kth(root, end)
            amount = _prefix_total(root, end + 1) - _prefix_total(root, start)
            result.append([
                key,
                [first.t, first.i],
                [last.t, last.i],
                end - start + 1,
                amount,
            ])
            start = end + 1
        return result
