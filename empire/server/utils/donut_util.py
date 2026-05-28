"""Wrapper around donut-shellcode that handles working-directory quirks.

donut-shellcode writes a ``loader.bin`` file to the current working
directory on every invocation.  If a non-writable ``loader.bin`` already
exists (e.g. owned by root), the call fails with *"Cannot open file"*.

All donut usage should go through :func:`donut_create` so that the
library always runs inside a private temp directory.
"""

import logging
import os
import tempfile
import threading
from pathlib import Path

try:
    import donut
except ModuleNotFoundError:
    donut = None

log = logging.getLogger(__name__)

_donut_lock = threading.Lock()


def donut_create(**kwargs):
    """Call ``donut.create`` in an isolated temporary directory.

    A process-wide lock serialises calls because ``os.chdir`` affects
    the entire process.
    """
    if donut is None:
        raise ImportError(
            "donut-shellcode is not installed. It is only supported on x86."
        )

    orig_cwd = Path.cwd()
    with _donut_lock, tempfile.TemporaryDirectory() as tmp_dir:
        os.chdir(tmp_dir)
        try:
            return donut.create(**kwargs)
        finally:
            os.chdir(orig_cwd)
