"""Pluggable translation.

- GoogleFree (deep-translator): free, no API key, the default. Rate-limited.
- DeepL: higher quality, needs an API key (quota-metered).
- Offline (Argos Translate): on-device, no network, downloads language packs.

`make_translator(engine, ...)` returns a ready backend, mirroring ocr.make_ocr.
Every backend caches by (source, target, text) — game/menu text repeats a lot and
the endpoints are rate-limited / quota-metered, so we never re-translate a string.

The module-level `translate()` / `TranslateError` are kept for tools/smoke_test.py
and any caller that just wants the default free engine.
"""

from __future__ import annotations

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


class GoogleFreeBackend(TranslateBackend):
    name = "google"

    def _translate(self, text: str, source: str, target: str) -> str:
        return GoogleTranslator(source=source, target=target).translate(text)


# App (Google) codes -> DeepL codes. DeepL source is the base language; some
# targets require a regional variant (EN-US, PT-BR). Unmapped codes fall back to
# an uppercased guess and DeepL surfaces a clear error if it's unsupported.
_DEEPL_SOURCE = {
    "en": "EN", "ja": "JA", "zh-CN": "ZH", "zh-TW": "ZH", "ko": "KO", "ru": "RU",
    "uk": "UK", "de": "DE", "fr": "FR", "es": "ES", "it": "IT", "pt": "PT",
    "pl": "PL", "tr": "TR", "ar": "AR",
}
# Targets need the explicit regional/script variant; bare "ZH" target is a
# deprecated alias that always yields Simplified, so map the Chinese variants too.
_DEEPL_TARGET = {
    **_DEEPL_SOURCE,
    "en": "EN-US", "pt": "PT-BR", "zh-CN": "ZH-HANS", "zh-TW": "ZH-HANT",
}


class DeepLBackend(TranslateBackend):
    name = "deepl"

    def __init__(self, api_key: str) -> None:
        super().__init__()
        if not api_key:
            raise RuntimeError("DeepL needs an API key — set it in Settings.")
        try:
            import deepl
        except ImportError as exc:
            raise RuntimeError("DeepL not installed — `pip install deepl`.") from exc
        self._client = deepl.Translator(api_key)

    def _translate(self, text: str, source: str, target: str) -> str:
        src = None if source == "auto" else _DEEPL_SOURCE.get(source, source.split("-")[0].upper())
        tgt = _DEEPL_TARGET.get(target, target.split("-")[0].upper())
        return self._client.translate_text(text, source_lang=src, target_lang=tgt).text


class ArgosBackend(TranslateBackend):
    name = "offline"
    parallel_safe = False  # argostranslate's module-level state isn't thread-safe

    def __init__(self, model_dir: str = "") -> None:
        super().__init__()
        try:
            import argostranslate.translate  # noqa: F401  (probe install)
        except ImportError as exc:
            raise RuntimeError(
                "Offline translation needs Argos — `pip install argostranslate`."
            ) from exc
        if model_dir:
            import os

            os.environ.setdefault("ARGOS_PACKAGES_DIR", model_dir)

    def _translate(self, text: str, source: str, target: str) -> str:
        if source == "auto":
            raise TranslateError(
                "Offline translation can't auto-detect — pick an explicit source language."
            )
        import argostranslate.translate as argos

        src, tgt = source.split("-")[0], target.split("-")[0]
        out = argos.translate(text, src, tgt)
        if not out:
            raise TranslateError(
                f"No offline model for {src}->{tgt}. Install the Argos language pack."
            )
        return out


def _build(name: str, *, deepl_api_key: str = "", offline_model_dir: str = "") -> TranslateBackend:
    if name == "google":
        return GoogleFreeBackend()
    if name == "deepl":
        return DeepLBackend(deepl_api_key)
    if name == "offline":
        return ArgosBackend(offline_model_dir)
    raise RuntimeError(f"Unknown translate engine: {name}")


def make_translator(
    engine: str,
    *,
    deepl_api_key: str = "",
    offline_model_dir: str = "",
) -> TranslateBackend:
    """Pick a backend. An explicit engine is built as-is so its failure surfaces a
    clear error (e.g. 'deepl' with no key). 'auto' prefers the free Google engine,
    then DeepL (only if a key is set), then offline. Source is not needed here — the
    backend's cache is keyed by source, so one instance serves every source."""
    if engine and engine != "auto":
        return _build(engine, deepl_api_key=deepl_api_key, offline_model_dir=offline_model_dir)

    order = ["google"] + (["deepl"] if deepl_api_key else []) + ["offline"]
    errors = []
    for name in order:
        try:
            return _build(name, deepl_api_key=deepl_api_key, offline_model_dir=offline_model_dir)
        except Exception as exc:  # pragma: no cover - depends on installed deps
            errors.append(f"{name}: {exc}")
    raise RuntimeError(
        "No translation backend available. Tried: " + "; ".join(errors)
        + "  (set a DeepL API key, or install argostranslate for offline)"
    )


_default_backend: "TranslateBackend | None" = None


def translate(text: str, source: str, target: str) -> str:
    """Default free-Google translation, preserved for the smoke test and any
    caller that doesn't manage a backend instance."""
    global _default_backend
    if _default_backend is None:
        _default_backend = GoogleFreeBackend()
    return _default_backend.translate(text, source, target)
