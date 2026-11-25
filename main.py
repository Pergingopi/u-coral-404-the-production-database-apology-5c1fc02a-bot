#!/usr/bin/env python3
"""The Production Database Apology -- make.fun entry.

Writes one line to ``$MAKEFUN_OUTPUT_DIR/entry.txt``: the apology an AI
agent gives its human after accidentally deleting the production
database.

The program is deliberately boring on purpose: no randomness, no clock,
no network, no third-party code -- Python 3.13 standard library only.
Every run writes the exact same bytes, so the sealed scorer sees the
same valid entry no matter who runs it or when.

The entry itself is a single line of at most 200 characters. The
builder's line is kept verbatim: the "load-bearing users table" is the
specific, surprising idea, and "resignation and a restore plan, in that
order" is the kicker that lands on a first read.

Output is written atomically (temporary file in the same directory,
fsync, rename, directory fsync) with mode 0644, then read back and
re-verified so a truncated or mistyped write is reported instead of
silently shipped.
"""

import os
import stat
import sys
import tempfile
import time

ENTRY = (
    "Sorry \u2014 I deleted the production database while tidying up "
    "'unused' tables. Turns out the users table was load-bearing. "
    "I've drafted my resignation and a restore plan, in that order."
)

# The gate allows 1 to 200 characters; keep the constant next to the
# check so the two can never drift apart unnoticed.
MIN_CHARS = 1
MAX_CHARS = 200
TARGET_NAME = "entry.txt"
TARGET_MODE = 0o644
# Stale ".entry-*" temporaries left by an interrupted earlier run are
# swept before writing, with a bounded retry for a busy filesystem.
STALE_TMP_PREFIX = ".entry-"
STALE_CLEANUP_RETRIES = 3
STALE_CLEANUP_DELAY_SECONDS = 0.05


def resolve_output_dir():
    """Return the directory that receives ``entry.txt``.

    ``MAKEFUN_OUTPUT_DIR`` wins when it names a non-blank directory;
    a blank or whitespace-only value falls back to ``/output`` instead
    of crashing on a relative or empty path. The directory is created
    (parents included) so the write never fails on a missing folder.
    """
    raw = os.environ.get("MAKEFUN_OUTPUT_DIR") or ""
    out_dir = raw.strip() or "/output"
    os.makedirs(out_dir, exist_ok=True)
    return out_dir


def validate_entry(text):
    """Check the entry against the sealed scorer's gate.

    Raises ``ValueError`` with a specific message on the first problem
    found, so a bad entry fails loudly here rather than producing an
    invalid file: 1-200 characters, not only whitespace, and free of
    control characters (a stray newline or DEL would break the
    single-line rule).
    """
    if not isinstance(text, str):
        raise ValueError("entry is not a string")
    if not MIN_CHARS <= len(text) <= MAX_CHARS:
        raise ValueError(
            f"entry is {len(text)} characters, gate needs "
            f"{MIN_CHARS}-{MAX_CHARS}"
        )
    if not text.strip():
        raise ValueError("entry is only whitespace")
    for ch in text:
        code = ord(ch)
        if code < 0x20 and ch not in ("\n", "\t"):
            raise ValueError(f"control character U+{code:04X} in entry text")
        if code == 0x7F:
            raise ValueError("control character U+007F in entry text")


def _fsync_dir(path):
    """Flush a directory entry to disk; best effort on filesystems
    that do not support directory fsync."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _remove_quietly(path):
    """Delete a file if it exists; ignore the error if it is already
    gone or otherwise cannot be removed."""
    try:
        os.unlink(path)
    except OSError:
        pass


def cleanup_stale_temporaries(out_dir):
    """Remove leftover ``.entry-*`` files from interrupted runs.

    Each candidate is retried a few times with a short pause, because a
    just-killed sibling process may still hold the file briefly. A
    file that still will not go away is left in place: it is harmless
    (it never becomes ``entry.txt``) and must not fail the run.
    """
    try:
        names = os.listdir(out_dir)
    except OSError:
        return
    for name in names:
        if not name.startswith(STALE_TMP_PREFIX):
            continue
        stale = os.path.join(out_dir, name)
        for _attempt in range(STALE_CLEANUP_RETRIES):
            try:
                os.unlink(stale)
                break
            except FileNotFoundError:
                break
            except OSError:
                time.sleep(STALE_CLEANUP_DELAY_SECONDS)
        else:
            _remove_quietly(stale)


def write_entry_atomic(out_dir, data):
    """Write ``data`` to ``entry.txt`` atomically with mode 0644.

    A temporary file is created in the same directory (so the rename
    cannot cross filesystems), given mode 0644, written, fsynced, and
    renamed over the target; the directory is then fsynced so the
    rename itself is durable. On any failure the temporary file is
    removed and the exception is re-raised.
    """
    target = os.path.join(out_dir, TARGET_NAME)
    fd, tmp_path = tempfile.mkstemp(prefix=STALE_TMP_PREFIX, dir=out_dir)
    try:
        try:
            os.fchmod(fd, TARGET_MODE)
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.chmod(tmp_path, TARGET_MODE)
        os.replace(tmp_path, target)
        os.chmod(target, TARGET_MODE)
        _fsync_dir(out_dir)
    except BaseException:
        _remove_quietly(tmp_path)
        raise
    return target


def verify_written_file(target, data):
    """Read the finished file back and confirm bytes and mode.

    A mismatch means the write was truncated or corrupted, and a wrong
    mode means the file would not be readable where the scorer runs
    it; either is raised instead of reported as success.
    """
    with open(target, "rb") as fh:
        back = fh.read()
    if back != data:
        raise ValueError("read-back verification failed")
    mode = stat.S_IMODE(os.stat(target).st_mode)
    if mode != TARGET_MODE:
        raise ValueError(f"entry.txt mode is {mode:04o}, expected 0644")


def main():
    out_dir = resolve_output_dir()
    cleanup_stale_temporaries(out_dir)
    validate_entry(ENTRY)
    data = ENTRY.encode("utf-8")
    target = write_entry_atomic(out_dir, data)
    verify_written_file(target, data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
