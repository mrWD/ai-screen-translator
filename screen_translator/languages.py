"""Curated language lists for the UI.

`SOURCE_LANGUAGES` — what to OCR + translate *from* (includes "auto").
`TARGET_LANGUAGES` — what to translate *into*.

`code` is what the free Google endpoint (via deep-translator) understands.
`vision_code` maps a source language to an Apple Vision BCP-47 recognition
language so on-screen OCR is hinted correctly.

NOTE: Apple Vision has NO Cyrillic support, so ru/uk have vision_code=None.
For a Cyrillic *source*, use the RapidOCR engine (see config.ocr_engine).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Language:
    code: str                       # deep-translator / Google code
    name: str                       # human-readable label
    vision_code: str | None = None  # Apple Vision BCP-47 hint, or None if unsupported


# Roughly ordered by how common each is as game text.
_LANGS = [
    Language("en", "English", "en-US"),
    Language("ja", "日本語 (Japanese)", "ja-JP"),
    Language("zh-CN", "中文简体 (Chinese, Simpl.)", "zh-Hans"),
    Language("zh-TW", "中文繁體 (Chinese, Trad.)", "zh-Hant"),
    Language("ko", "한국어 (Korean)", "ko-KR"),
    Language("ru", "Русский (Russian)", None),
    Language("uk", "Українська (Ukrainian)", None),
    Language("de", "Deutsch (German)", "de-DE"),
    Language("fr", "Français (French)", "fr-FR"),
    Language("es", "Español (Spanish)", "es-ES"),
    Language("it", "Italiano (Italian)", "it-IT"),
    Language("pt", "Português (Portuguese)", "pt-BR"),
    Language("pl", "Polski (Polish)", "pl-PL"),
    Language("tr", "Türkçe (Turkish)", "tr-TR"),
    Language("ar", "العربية (Arabic)", "ar-SA"),
]

AUTO = Language("auto", "Auto-detect", None)

SOURCE_LANGUAGES = [AUTO] + _LANGS
TARGET_LANGUAGES = _LANGS  # translating *into* "auto" makes no sense

_BY_CODE = {lang.code: lang for lang in SOURCE_LANGUAGES}


def get(code: str) -> Language:
    return _BY_CODE.get(code, AUTO)
