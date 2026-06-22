"""Subprocess worker for offline (Argos) translation.

Argos pulls in stanza → PyTorch, and torch's GIL handling SEGFAULTs when driven
from a Qt ``QThreadPool`` worker thread: ``take_gil`` ← ``gil_scoped_acquire`` in
``libtorch_python`` while stanza initialises a torch model (Tensor.random_). The
crash is a C++-created thread acquiring the GIL through torch's cached interpreter
vtable — not fixable from Python. Running Argos in its *own* process, where torch
lives on that process's main thread, sidesteps it entirely. The parent (see
``ArgosBackend`` in ``translate.py``) talks to us with one JSON request per line
on stdin and reads one JSON response per line on stdout.

Protocol (UTF-8, newline-delimited JSON):
    →  {"text": "...", "from": "ja", "to": "ru"}
    ←  {"ok": true, "text": "..."}  |  {"ok": false, "error": "..."}

Run as: ``python -m screen_translator.argos_proc`` (env may set ARGOS_PACKAGES_DIR).
"""

from __future__ import annotations

import json
import os
import sys


def main() -> int:
    # The protocol owns fd 1. Re-point sys.stdout at stderr so any stray prints
    # from argos/stanza/ctranslate2 can't corrupt the JSON response stream.
    proto_out = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr

    try:
        import argostranslate.translate as argos
    except Exception as exc:  # argostranslate missing/broken — tell the parent once
        proto_out.write(json.dumps({"ok": False, "error": f"argos import failed: {exc}"}) + "\n")
        proto_out.flush()
        return 1

    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
                if "texts" in req:  # batch (full-screen): one round-trip for N blocks
                    out = []
                    for t in req["texts"]:
                        try:
                            out.append(argos.translate(t, req["from"], req["to"]))
                        except Exception:
                            out.append("")  # one bad block must not fail the whole screen
                    resp = {"ok": True, "texts": out}
                else:
                    text = argos.translate(req["text"], req["from"], req["to"])
                    resp = {"ok": True, "text": text}
            except Exception as exc:  # surface per-request errors without killing the worker
                resp = {"ok": False, "error": str(exc)}
            proto_out.write(json.dumps(resp, ensure_ascii=False) + "\n")
            proto_out.flush()
    except (BrokenPipeError, OSError):
        return 0  # parent went away mid-exchange — exit quietly, no traceback
    return 0


if __name__ == "__main__":
    sys.exit(main())
