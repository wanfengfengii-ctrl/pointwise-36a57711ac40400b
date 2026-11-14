# Session facts, maintained incrementally

`session.SessionIndex(gap)` keeps session facts for revision events.  Each
user (`key`) owns events ordered by `(time, id)`; a new session starts when
`next_time - prev_time > gap`.  A session is reported as

```text
[key, [first_time, first_id], [last_time, last_id], event_count, amount_sum]
```

Public API:

| call | result |
| --- | --- |
| `upsert(id, key, time, amount)` | `{"removed": [...], "added": [...]}` — session-set difference before/after; replacing an existing ID moves or rewrites that event. |
| `delete(id)` | same delta shape; unknown ID -> `{"removed": [], "added": []}`. |
| `read(key)` | sessions of that user, in session order; unknown user -> `[]`. A fresh list and fresh nested lists are returned on every call, so callers may mutate the result. |

Delta lists are the set difference of the *whole* session records: any
session whose end, count or amount changed is a different record, so it shows
up in `removed` and in `added`.  Both lists are sorted by `key`, then by first
`(time, id)`.

## Data structure

One augmented treap per user, keyed by `(time, id)`.  Every node stores, for
its subtree:

* `size` — event count,
* `total` — amount sum,
* `breaks` — number of session breaks inside the subtree (a break is an
  adjacent in-order pair with `time difference > gap`),
* `first_t/first_i`, `last_t/last_i` — the extreme event of the subtree.

Sizes and amounts are additive, so either can be summed over the first `k`
in-order events in `O(log n)`.  A break count can be computed for a whole
subtree from its parts plus the two boundary comparisons, so `breaks` is
also an additive aggregate.  A sorted order-statistic search ("the rank
right after the `c`-th break") then finds a session's exact extent: start
rank, end rank, count and amount, all in `O(log n)`.

## Why updates are local

Inserting or deleting one event can only affect sessions that contain, or
were adjacent to, that event's position.  The old event's session may split;
the new position's predecessor and successor sessions may merge, grow or
shrink.  So `upsert`/`delete` probe a constant number of session summaries
before and after the mutation and subtract the two sets.  Nothing walks a
long session and no user history is resorted — the update never touches more
than `O(log n)` nodes plus constant-size result records.

* `upsert` — 3 session probes at the old position and 2 at the new one
  (before), then 2 + 3 (after): `O(log n)` expected.
* `delete` — 3 probes before, 2 after: `O(log n)` expected.
* `read` — `O(s · log n)` expected for `s` reported sessions (`O(n)` if every
  event is its own session, each record emitted once).
* space — one node per stored event: `O(N)`.

## Checks

`tests/reference.py` is an independent reference: it keeps a plain dict of
records and rebuilds a user's sessions from a full sort on every call.  It
shares no code with the index.

`tools/verify.py` (the runtime's `verify`) does the following and exits
non-zero on the first mismatch:

1. the worked example (`gap=5`, `a/0/2`, `b/4/-2`, `c/8/3` -> one session
   `["u",[0,"a"],[8,"c"],3,3]`; deleting `b` splits it into two one-event
   sessions) plus unknown-user and unknown-ID cases;
2. ordering/tie-break cases and the sortedness of both delta lists;
3. 100 randomized workloads (17 000+ mutations) in which **every single step**
   compares the index's `read` for all users and its returned delta against
   the reference; the event stream includes inserts, moves between users,
   same-time ties resolved by ID and exact `gap` boundaries; each workload is
   then drained to empty;
4. a scaling run: one long session of `N` events, deleting and re-inserting
   the middle event, for `N` doubling from 10 000 to 80 000.  It prints per
   `N` the best delete time, best re-insert time, peak `tracemalloc` memory
   and bytes/event, and asserts that latency does not grow with `N`
   (well under 5 ms, no scan) and that peak memory doubles with `N`.

A representative run (timings vary with the machine):

```text
long-session middle delete/re-insert, one session of N events
     N   delete_us  re-insert_us   peak_MB   bytes/event
 10000       35.21         35.42      3.34        334.0
 20000       35.37         37.00      6.81        340.6
 40000       39.83         40.25     13.89        347.2
 80000       39.17         40.25     27.91        348.9
latency stayed within 1.11x across a 8x data range; memory is linear.
```

Update cost grows like `log N` (a 8x larger data set costs ~1.1x the time),
and peak memory is a constant ~350 bytes per event — `O(N)`.

`tools/demo.py` walks through the worked example, the split, tie-breaking,
cross-user moves and the long-session middle delete.

## Running

```sh
python3 tools/setup.py      # byte-compile everything (manifest setup argv)
python3 tools/verify.py     # functional + differential + scaling checks
python3 tools/demo.py       # narrated examples
python3 -m unittest discover -s tests -t .   # unit/differential test suite
```
