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
    →  {"text": "...", "from": "ja", "to": "ru"}          (region: one block)
    →  {"texts": ["...", ...], "from": "en", "to": "ru"}  (full screen: many blocks)
    ←  {"ok": true, "text": "..."} | {"ok": true, "texts": [...]} | {"ok": false, "error": "..."}

**Speed.** A 50-block screen via the public `argostranslate.translate()` is ~5 s
(each call: sentence-split + a separate ctranslate2 run, and argos chops the batch
into 32-token pieces). `_FastBatch` instead feeds ALL blocks' sentences into ONE
`ctranslate2.translate_batch` with a big batch size and greedy search — ~6-7×
faster (50 blocks ≈ 1 s). It reaches into argostranslate internals, so it falls
back to the per-block public API if those don't match (e.g. pivot pairs, or a
future argostranslate). We also default to MINISBD (no stanza/torch → faster start,
less memory) and beam_size 1; both are env-overridable.

Run as: ``python -m screen_translator.argos_proc`` (env may set ARGOS_PACKAGES_DIR).
"""

from __future__ import annotations

import json
import os
import sys

# Prefer speed: a lightweight sentence splitter (no stanza/torch) and greedy search.
# Game UI text is short, so the quality cost is negligible. setdefault → still
# overridable from the environment.
os.environ.setdefault("ARGOS_CHUNK_TYPE", "MINISBD")
os.environ.setdefault("ARGOS_BEAM_SIZE", "1")

# ctranslate2 batch limits for the fast path (argos's own 32-token default would
# defeat batching). batch_type="tokens" so a few long blocks can't blow up memory.
_MAX_BATCH_TOKENS = 2048


class _FastBatch:
    """Translate all blocks in a single ctranslate2 batch, reusing argostranslate's
    installed model/tokenizer/sentencizer. Only supports DIRECT (non-pivot) package
    translations; everything else raises Unsupported so the caller falls back."""

    class Unsupported(Exception):
        pass

    def __init__(self) -> None:
        self._cache: dict = {}  # (from, to) -> internals dict or None (unsupported)

    def _internals(self, frm: str, to: str):
        key = (frm, to)
        if key in self._cache:
            return self._cache[key]
        found = None
        try:
            import argostranslate.translate as at

            langs = at.get_installed_languages()
            src = next((l for l in langs if l.code == frm), None)
            dst = next((l for l in langs if l.code == to), None)
            if src is not None and dst is not None:
                tr = src.get_translation(dst)
                if tr is not None:
                    tr.translate("x")  # force the lazy ctranslate2.Translator to build
                    pt = tr
                    for _ in range(8):  # unwrap CachedTranslation(...) layers
                        if type(pt).__name__ == "PackageTranslation":
                            break
                        pt = getattr(pt, "underlying", None)
                        if pt is None:
                            break
                    if (
                        pt is not None
                        and type(pt).__name__ == "PackageTranslation"
                        and getattr(pt, "translator", None) is not None
                    ):
                        found = {"translator": pt.translator, "sent": pt.sentencizer, "pkg": pt.pkg}
        except Exception:
            found = None
        self._cache[key] = found
        return found

    def translate(self, texts: "list[str]", frm: str, to: str) -> "list[str]":
        intern = self._internals(frm, to)
        if intern is None:
            raise _FastBatch.Unsupported()
        translator, sent, pkg = intern["translator"], intern["sent"], intern["pkg"]

        flat_tokens: list = []
        owner: list = []
        for i, raw in enumerate(texts):
            text = (raw or "").strip()
            if not text:
                continue
            for sentence in sent.split_sentences(text):
                flat_tokens.append(pkg.tokenizer.encode(sentence))
                owner.append(i)

        out = ["" for _ in texts]
        if not flat_tokens:
            return out

        import argostranslate.settings as _st

        target_prefix = [[pkg.target_prefix]] * len(flat_tokens) if pkg.target_prefix else None
        results = translator.translate_batch(
            flat_tokens,
            target_prefix=target_prefix,
            replace_unknowns=True,
            max_batch_size=_MAX_BATCH_TOKENS,
            batch_type="tokens",
            beam_size=_st.beam_size,  # 1 (greedy) by default; ARGOS_BEAM_SIZE to raise for quality
            num_hypotheses=1,
            length_penalty=0.2,
            return_scores=False,
        )

        # Concatenate each block's sentence-tokens, then decode once — mirrors
        # argostranslate.translate.apply_packaged_translation.
        tokens_by_block: dict = {}
        for block_idx, result in zip(owner, results):
            tokens_by_block.setdefault(block_idx, []).extend(result.hypotheses[0])
        for block_idx, tokens in tokens_by_block.items():
            value = pkg.tokenizer.decode(tokens)
            if pkg.target_prefix and value.startswith(pkg.target_prefix):
                value = value[len(pkg.target_prefix):]
            if value and value[0] == " ":
                value = value[1:]
            out[block_idx] = value
        return out


def main() -> int:
    # The protocol owns fd 1. Re-point sys.stdout at stderr so any stray prints
    # from argos/ctranslate2 can't corrupt the JSON response stream.
    proto_out = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr

    try:
        import argostranslate.translate as argos
    except Exception as exc:  # argostranslate missing/broken — tell the parent once
        proto_out.write(json.dumps({"ok": False, "error": f"argos import failed: {exc}"}) + "\n")
        proto_out.flush()
        return 1

    fast = _FastBatch()

    def _has_route(frm: str, to: str) -> bool:
        """True if an installed Argos pack can translate frm→to, directly OR via the
        English pivot (mirrors offline_models.plan_packages). Lets us fail loudly when
        the model is missing instead of silently returning empty translations."""
        try:
            import argostranslate.package as _pkg

            pairs = {(p.from_code, p.to_code) for p in _pkg.get_installed_packages()}
        except Exception:
            return True  # can't introspect — let the translate attempt surface errors
        f, t = frm.split("-")[0], to.split("-")[0]
        if (f, t) in pairs:
            return True
        return f != "en" and t != "en" and (f, "en") in pairs and ("en", t) in pairs

    def _require_route(frm: str, to: str) -> None:
        if not _has_route(frm, to):
            raise RuntimeError(
                f"No offline model for {frm.split('-')[0]}→{to.split('-')[0]} — open "
                "Settings → Offline model → Download."
            )

    def translate_many(texts, frm, to):
        _require_route(frm, to)  # missing model -> loud error, not a blank overlay
        try:
            return fast.translate(texts, frm, to)  # one batched ctranslate2 run
        except Exception:
            # Pivot pair / unexpected argostranslate internals — per-block fallback.
            out = []
            for t in texts:
                try:
                    out.append(argos.translate(t, frm, to))
                except Exception:
                    out.append("")  # one bad block must not fail the whole screen
            return out

    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
                if "texts" in req:  # batch (full-screen): one round-trip for N blocks
                    resp = {"ok": True, "texts": translate_many(req["texts"], req["from"], req["to"])}
                else:
                    _require_route(req["from"], req["to"])
                    resp = {"ok": True, "text": argos.translate(req["text"], req["from"], req["to"])}
            except Exception as exc:  # surface per-request errors without killing the worker
                resp = {"ok": False, "error": str(exc)}
            proto_out.write(json.dumps(resp, ensure_ascii=False) + "\n")
            proto_out.flush()
    except (BrokenPipeError, OSError):
        return 0  # parent went away mid-exchange — exit quietly, no traceback
    return 0


if __name__ == "__main__":
    sys.exit(main())
