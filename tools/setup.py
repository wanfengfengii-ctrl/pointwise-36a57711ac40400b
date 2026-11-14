#!/usr/bin/env python3
"""Prepare the checkout: byte-compile every module (stdlib only, no installs).

Run with any working directory; paths are resolved from this file.
"""

from __future__ import annotations

import compileall
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    targets = ["session.py", "tests", "tools"]
    ok = True
    for target in targets:
        path = os.path.join(ROOT, target)
        if os.path.isdir(path):
            ok = compileall.compile_dir(path, quiet=1) and ok
        else:
            ok = compileall.compile_file(path, quiet=1) and ok
    print("setup: byte-compiled session.py, tests/ and tools/ ->",
          "ok" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
