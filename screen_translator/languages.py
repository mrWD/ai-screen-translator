"""Curated language lists for the UI.

`SOURCE_LANGUAGES` — what to OCR + translate *from* (includes "auto").
`TARGET_LANGUAGES` — what to translate *into*.

`code` is what the free Google endpoint (via deep-translator) understands.
`vision_code` maps a language to an Apple Vision BCP-47 recognition language so
on-screen OCR is hinted correctly. Every code here is in Vision's **fast**-mode
supported set (the default), so these all work as a *source* too — including
Cyrillic/CJK. NOTE: Vision's **accurate** mode only supports the six Latin
languages (en/fr/it/de/es/pt); for any other source, keep "Fast OCR" on
(VisionOCR drops the hint rather than erroring if the level can't honour it).

A language with `vision_code=None` can still be a *target* (no OCR needed); as a
*source* it would route to the optional RapidOCR engine.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Language:
    code: str                       # deep-translator / Google code
    name: str                       # human-readable label
    vision_code: str | None = None  # Apple Vision BCP-47 hint, or None if unsupported


# Roughly ordered by how common each is as game text. All vision_codes below are
# in Vision's fast-mode supported list (verified on macOS 26).
_LANGS = [
    Language("en", "English", "en-US"),
    Language("ja", "日本語 (Japanese)", "ja-JP"),
    Language("zh-CN", "中文简体 (Chinese, Simpl.)", "zh-Hans"),
    Language("zh-TW", "中文繁體 (Chinese, Trad.)", "zh-Hant"),
    Language("ko", "한국어 (Korean)", "ko-KR"),
    Language("ru", "Русский (Russian)", "ru-RU"),
    Language("uk", "Українська (Ukrainian)", "uk-UA"),
    Language("de", "Deutsch (German)", "de-DE"),
    Language("fr", "Français (French)", "fr-FR"),
    Language("es", "Español (Spanish)", "es-ES"),
    Language("it", "Italiano (Italian)", "it-IT"),
    Language("pt", "Português (Portuguese)", "pt-BR"),
    Language("nl", "Nederlands (Dutch)", "nl-NL"),
    Language("pl", "Polski (Polish)", "pl-PL"),
    Language("cs", "Čeština (Czech)", "cs-CZ"),
    Language("ro", "Română (Romanian)", "ro-RO"),
    Language("sv", "Svenska (Swedish)", "sv-SE"),
    Language("da", "Dansk (Danish)", "da-DK"),
    Language("no", "Norsk (Norwegian)", "no-NO"),
    Language("tr", "Türkçe (Turkish)", "tr-TR"),
    Language("id", "Bahasa Indonesia (Indonesian)", "id-ID"),
    Language("ms", "Bahasa Melayu (Malay)", "ms-MY"),
    Language("vi", "Tiếng Việt (Vietnamese)", "vi-VT"),
    Language("th", "ไทย (Thai)", "th-TH"),
    Language("ar", "العربية (Arabic)", "ar-SA"),
]

AUTO = Language("auto", "Auto-detect", None)

SOURCE_LANGUAGES = [AUTO] + _LANGS
TARGET_LANGUAGES = _LANGS  # translating *into* "auto" makes no sense

_BY_CODE = {lang.code: lang for lang in SOURCE_LANGUAGES}


def get(code: str) -> Language:
    return _BY_CODE.get(code, AUTO)
