"""Unit tests for the experimental LLM translation tier.

The HTTP layer (`LLMBackend._chat`) is stubbed, so these run offline with no server
— they exercise the batch prompt/parse/alignment, the (source,target,text) cache,
and the per-item fallback when a batched reply can't be parsed.
"""

import json
import unittest

from screen_translator.translate import LLMBackend, TranslateError


def _fake_chat(counter):
    """Stand-in for _chat: echoes `[value]`. Handles both the batch call (JSON in →
    JSON out) and the single-item fallback (plain text)."""
    def fake(messages):
        counter[0] += 1
        system, user = messages[0]["content"], messages[1]["content"]
        if "JSON object" in system:  # batch call
            items = json.loads(user)
            return json.dumps({k: f"[{v}]" for k, v in items.items()}, ensure_ascii=False)
        return f"[{user}]"  # single-item call
    return fake


class TestLLMBackend(unittest.TestCase):
    def _backend(self):
        return LLMBackend(base_url="http://localhost:11434/v1", model="test")

    def test_batch_alignment_one_request(self):
        b = self._backend()
        calls = [0]
        b._chat = _fake_chat(calls)
        out = b.translate_batch(["Hello", "World"], "en", "ru")
        self.assertEqual(out, ["[Hello]", "[World]"])
        self.assertEqual(calls[0], 1)  # the whole batch went in one request

    def test_empty_strings_pass_through(self):
        b = self._backend()
        b._chat = _fake_chat([0])
        out = b.translate_batch(["", "   ", "Hi"], "en", "ru")
        self.assertEqual(out, ["", "", "[Hi]"])

    def test_cache_avoids_second_request(self):
        b = self._backend()
        calls = [0]
        b._chat = _fake_chat(calls)
        b.translate_batch(["Hello"], "en", "ru")
        b.translate_batch(["Hello"], "en", "ru")
        self.assertEqual(calls[0], 1)  # second call served entirely from cache

    def test_fallback_to_per_item_on_unparseable_batch(self):
        b = self._backend()
        calls = [0]

        def fake(messages):
            calls[0] += 1
            if "JSON object" in messages[0]["content"]:
                return "sorry, I can't comply"  # not JSON → forces fallback
            return f"[{messages[1]['content']}]"

        b._chat = fake
        out = b.translate_batch(["A", "B"], "en", "ru")
        self.assertEqual(out, ["[A]", "[B]"])
        self.assertEqual(calls[0], 3)  # 1 failed batch + 2 single fallbacks

    def test_reordered_and_missing_keys(self):
        b = self._backend()

        def fake(messages):
            items = json.loads(messages[1]["content"])
            return json.dumps({"1": f"[{items['1']}]"})  # only key "1", "0" missing

        b._chat = fake
        out = b.translate_batch(["A", "B"], "en", "ru")
        self.assertEqual(out[1], "[B]")
        self.assertEqual(out[0], "")  # missing key → empty, no crash

    def test_extract_json_obj(self):
        self.assertEqual(LLMBackend._extract_json_obj('```json\n{"0":"x"}\n```'), {"0": "x"})
        self.assertEqual(LLMBackend._extract_json_obj('Here you go: {"0":"x"} done'), {"0": "x"})
        self.assertIsNone(LLMBackend._extract_json_obj("no json here"))

    def test_single_translate_path(self):
        b = self._backend()
        b._chat = _fake_chat([0])
        self.assertEqual(b.translate("Hello", "en", "ru"), "[Hello]")

    def test_unreachable_endpoint_raises_translate_error(self):
        # Discard port → connection refused → a clear TranslateError, not a raw URLError.
        b = LLMBackend(base_url="http://127.0.0.1:9", model="x", timeout=1.0)
        with self.assertRaises(TranslateError):
            b.translate("Hi", "en", "ru")


if __name__ == "__main__":
    unittest.main()
