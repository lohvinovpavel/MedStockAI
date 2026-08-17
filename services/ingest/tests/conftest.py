"""TEMPORARY CI diagnostic — remove once the ingest-leg hang is understood.

This suite finishes in ~5 s locally (including from a pristine clone and cold
venv) but the CI matrix leg spins until cancelled, and logs of a still-running
job cannot be downloaded. In CI only: after 4 minutes, dump every thread's
stack to stderr and hard-exit so the step fails and the log — with the stacks
— becomes readable.
"""

import faulthandler
import os
import sys
import threading


def _watchdog() -> None:
    print("\n=== ingest-tests watchdog: 240s elapsed, dumping all stacks ===", file=sys.__stderr__)
    faulthandler.dump_traceback(file=sys.__stderr__)
    sys.__stderr__.flush()
    os._exit(3)


if os.environ.get("CI"):
    timer = threading.Timer(240.0, _watchdog)
    timer.daemon = True
    timer.start()
