"""Pluggable translation.

- GoogleFree (deep-translator): free, no API key, the default. Rate-limited.
- Offline (Argos Translate): on-device, no network, downloads language packs.

`make_translator(engine, ...)` returns a ready backend, mirroring ocr.make_ocr.
Every backend caches by (source, target, text) — game/menu text repeats a lot and
the endpoints are rate-limited, so we never re-translate a string.

The module-level `translate()` / `TranslateError` are kept for tools/smoke_test.py
and any caller that just wants the default free engine.
"""

from __future__ import annotations

import json
import threading

from deep_translator import GoogleTranslator

_CACHE_MAX = 512


class TranslateError(RuntimeError):
    pass


class TranslateBackend:
    """Base: handles the empty-text guard, the (source, target, text) cache, and
    the uniform TranslateError contract. Subclasses implement `_translate`."""

    name = "base"
    # True if `translate` may be called from several threads at once (full-screen
    # mode fans requests out). Network backends are fine; Argos overrides to False.
    parallel_safe = True

    def __init__(self) -> None:
        self._cache: "dict[tuple[str, str, str], str]" = {}
        self._lock = threading.Lock()  # guards the cache; the network call runs unlocked

    def translate(self, text: str, source: str, target: str) -> str:
        text = text.strip()
        if not text:
            return ""
        key = (source, target, text)
        with self._lock:
            cached = self._cache.get(key)
        if cached is not None:
            return cached
        try:
            result = self._translate(text, source, target)
        except TranslateError:
            raise  # already a clear, user-facing message — keep it
        except Exception as exc:  # network / endpoint / quota errors
            raise TranslateError(str(exc)) from exc
        with self._lock:
            if len(self._cache) >= _CACHE_MAX:
                self._cache.pop(next(iter(self._cache)))  # drop oldest (FIFO bound)
            self._cache[key] = result
        return result

    def _translate(self, text: str, source: str, target: str) -> str:
        raise NotImplementedError

    def translate_batch(self, texts: "list[str]", source: str, target: str) -> "list[str]":
        """Translate many strings. The base just loops (each call is cached); a
        backend with per-call overhead (Argos: a subprocess round-trip) overrides
        this to do them in one shot. Order is preserved; empties stay empty."""
        return [self.translate(t, source, target) for t in texts]

    def close(self) -> None:
        """Release any external resources (Argos holds a subprocess). No-op by
        default. Call when the backend is discarded (engine change / app quit)."""


class GoogleFreeBackend(TranslateBackend):
    name = "google"

    def _translate(self, text: str, source: str, target: str) -> str:
        return GoogleTranslator(source=source, target=target).translate(text)


class ArgosBackend(TranslateBackend):
    name = "offline"
    # Argos runs in a SUBPROCESS (see below), so this process never touches its
    # native deps. We still serialize the one-line request/response exchange with
    # a lock, and keep parallel_safe False so the full-screen fan-out stays single.
    parallel_safe = False

    def __init__(self, model_dir: str = "") -> None:
        super().__init__()
        # argostranslate pulls in stanza → PyTorch, and torch SEGFAULTs when its
        # GIL is acquired from a Qt worker thread (take_gil ← gil_scoped_acquire).
        # So we never import argostranslate here — translation is done in a child
        # process (argos_proc.py) where torch sits on that process's main thread.
        # In *this* process we only check that the package is installed.
        import importlib.util

        if importlib.util.find_spec("argostranslate") is None:
            raise RuntimeError(
                "Offline translation isn't set up yet — open Settings → "
                "Offline model → Download to install it."
            )
        self._model_dir = model_dir
        self._proc = None  # lazily spawned on first translate
        self._proc_lock = threading.Lock()

    def _ensure_proc(self):
        """Start (or restart) the Argos child process. Caller holds _proc_lock."""
        if self._proc is not None and self._proc.poll() is None:
            return self._proc
        import os
        import subprocess
        import sys

        env = dict(os.environ)
        if self._model_dir:
            env.setdefault("ARGOS_PACKAGES_DIR", self._model_dir)
        # Run from the package's parent so `-m screen_translator.argos_proc` resolves
        # regardless of the app's working directory.
        pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "screen_translator.argos_proc"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,  # let stanza/ctranslate2 log straight to our stderr
            text=True,
            encoding="utf-8",
            bufsize=1,  # line-buffered: one JSON request/response per line
            env=env,
            cwd=pkg_parent,
        )
        return self._proc

    def _exchange(self, payload: dict) -> dict:
        """Send one JSON request to the child and return its parsed reply.
        Lock-serialized (the child is a single shared process)."""
        request = json.dumps(payload, ensure_ascii=False)
        with self._proc_lock:
            proc = self._ensure_proc()
            try:
                proc.stdin.write(request + "\n")
                proc.stdin.flush()
                line = proc.stdout.readline()  # blocks until the child responds
            except (BrokenPipeError, OSError) as exc:
                self._proc = None
                raise TranslateError(f"Offline translator process failed: {exc}") from exc
            if not line:  # child exited without answering (crash / bad install)
                self._proc = None
                raise TranslateError(
                    "Offline translator process exited unexpectedly — try "
                    "re-downloading the model in Settings."
                )
        resp = json.loads(line)
        if not resp.get("ok"):
            raise TranslateError(resp.get("error") or "offline translation failed")
        return resp

    def _translate(self, text: str, source: str, target: str) -> str:
        if source == "auto":
            raise TranslateError(
                "Offline translation can't auto-detect — pick an explicit source language."
            )
        src, tgt = source.split("-")[0], target.split("-")[0]
        out = self._exchange({"text": text, "from": src, "to": tgt}).get("text") or ""
        if not out:
            raise TranslateError(
                f"No offline model for {src}→{tgt} — open Settings → "
                "Offline model → Download."
            )
        return out

    def translate_batch(self, texts: "list[str]", source: str, target: str) -> "list[str]":
        """Full-screen path: translate all blocks in ONE round-trip to the child,
        instead of N (each round-trip + lock is pure overhead). Cached items are
        served locally; only the misses are sent. Empties pass through unchanged."""
        if source == "auto":
            raise TranslateError(
                "Offline translation can't auto-detect — pick an explicit source language."
            )
        src, tgt = source.split("-")[0], target.split("-")[0]
        out: "list[str]" = [""] * len(texts)
        miss_idx, miss_txt = [], []
        for i, raw in enumerate(texts):
            text = raw.strip()
            if not text:
                continue
            key = (source, target, text)
            with self._lock:
                cached = self._cache.get(key)
            if cached is not None:
                out[i] = cached
            else:
                miss_idx.append(i)
                miss_txt.append(text)
        if miss_txt:
            try:
                results = self._exchange({"texts": miss_txt, "from": src, "to": tgt}).get("texts")
            except TranslateError:
                raise
            except Exception as exc:
                raise TranslateError(str(exc)) from exc
            results = results or []
            for i, text, res in zip(miss_idx, miss_txt, results):
                res = res or ""
                out[i] = res
                if res:
                    with self._lock:
                        if len(self._cache) >= _CACHE_MAX:
                            self._cache.pop(next(iter(self._cache)))
                        self._cache[(source, target, text)] = res
        return out

    def close(self) -> None:
        """Stop the helper subprocess so it doesn't linger (it holds a loaded torch
        model). Closing stdin makes the child exit its read loop; terminate/kill is
        a backstop."""
        with self._proc_lock:
            proc, self._proc = self._proc, None
        if proc is None or proc.poll() is not None:
            return
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except Exception:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def _build(name: str, *, offline_model_dir: str = "") -> TranslateBackend:
    if name == "google":
        return GoogleFreeBackend()
    if name == "offline":
        return ArgosBackend(offline_model_dir)
    raise RuntimeError(f"Unknown translate engine: {name}")


def make_translator(engine: str, *, offline_model_dir: str = "") -> TranslateBackend:
    """Pick a backend. An explicit engine is built as-is so its failure surfaces a
    clear error. 'auto' prefers the free Google engine, then offline. Source is not
    needed here — the backend's cache is keyed by source, so one instance serves all."""
    if engine and engine != "auto":
        return _build(engine, offline_model_dir=offline_model_dir)

    errors = []
    for name in ("google", "offline"):
        try:
            return _build(name, offline_model_dir=offline_model_dir)
        except Exception as exc:  # pragma: no cover - depends on installed deps
            errors.append(f"{name}: {exc}")
    raise RuntimeError(
        "No translation backend available. Tried: " + "; ".join(errors)
        + "  (install argostranslate for offline)"
    )


_default_backend: "TranslateBackend | None" = None


def translate(text: str, source: str, target: str) -> str:
    """Default free-Google translation, preserved for the smoke test and any
    caller that doesn't manage a backend instance."""
    global _default_backend
    if _default_backend is None:
        _default_backend = GoogleFreeBackend()
    return _default_backend.translate(text, source, target)
