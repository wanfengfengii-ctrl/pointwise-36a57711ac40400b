"""Independent, deliberately simple reference model used by the checks.

Events live in a plain dict; sessions are rebuilt from a full sort on every
query.  It shares no code with ``session.SessionIndex``.

Session deltas are set differences over whole session records: a session
whose end, count or amount changed is a different record and therefore
appears in both ``removed`` and ``added``.
"""

from __future__ import annotations


def _sessionize(gap, key, events):
    """events: iterable of (time, id, amount) -> list of session records."""
    out = []
    for t, i, a in sorted(events):
        if out and t - out[-1][2][0] <= gap:
            s = out[-1]
            s[2] = [t, i]
            s[3] += 1
            s[4] += a
        else:
            out.append([key, [t, i], [t, i], 1, a])
    return out


def _set_of(sessions):
    """Sessions as hashable flat tuples: (key, ft, fi, lt, li, count, amount)."""
    return {(s[0], s[1][0], s[1][1], s[2][0], s[2][1], s[3], s[4]) for s in sessions}


def _public(flat):
    return [flat[0], [flat[1], flat[2]], [flat[3], flat[4]], flat[5], flat[6]]


def _order(flat):
    return (flat[0], flat[1], flat[2])


class Reference:
    def __init__(self, gap):
        self.gap = gap
        self.records = {}  # id -> (key, time, amount)

    # -- queries ----------------------------------------------------------
    def read(self, key):
        return _sessionize(
            self.gap, key,
            ((t, i, a) for i, (k, t, a) in self.records.items() if k == key),
        )

    def _all(self):
        by_key = {}
        for i, (k, t, a) in self.records.items():
            by_key.setdefault(k, []).append((t, i, a))
        sessions = []
        for k, ev in by_key.items():
            sessions.extend(_sessionize(self.gap, k, ev))
        return _set_of(sessions)

    @staticmethod
    def _delta(before, after):
        removed = sorted(before - after, key=_order)
        added = sorted(after - before, key=_order)
        return {"removed": [_public(r) for r in removed],
                "added": [_public(a) for a in added]}

    # -- mutations --------------------------------------------------------
    def upsert(self, i, k, t, a):
        before = self._all()
        self.records[i] = (k, t, a)
        after = self._all()
        return self._delta(before, after)

    def delete(self, i):
        before = self._all()
        self.records.pop(i, None)
        after = self._all()
        return self._delta(before, after)
