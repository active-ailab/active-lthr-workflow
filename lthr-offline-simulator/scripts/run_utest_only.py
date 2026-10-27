#!/usr/bin/env python3
"""Deployment entry for the shared config-driven GoMore simulation flow.

This command intentionally delegates to run_device_multi_sim so deployment,
single UTest invocation, result validation, and artifact extraction stay in one
implementation. It accepts the same CLI, including --data-dir, --mode, and
--rounds.
"""

import sys

from run_device_multi_sim import main
from shared.watchlink_runtime import build_runtime_hint


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        runtime_hint = build_runtime_hint(str(exc))
        if runtime_hint:
            print(runtime_hint, file=sys.stderr, flush=True)
        raise
