"""Persistent translation history.

Each capture is appended to a per-run *session* folder as one JSONL line plus an
optional screenshot PNG. On demand we render a browsable `index.html` whose text
is natively selectable / copyable / searchable in any browser — so the user can
review the original + translation AFTER closing the game, without our app running.

Layout (under the app data dir):
  history/
    2026-06-06_19-34-12/        # one folder per app run
      session.jsonl             # append-only, one capture per line
      shots/001.png ...         # optional screenshots
      index.html                # generated on demand (Open log)
"""

from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path

from . import languages
from .config import history_dir, restrict_file, secure_dir


class HistoryWriter:
    def __init__(self, keep_sessions: int = 20, save_screenshots: bool = True) -> None:
        self._root = history_dir()
        self._keep = max(1, keep_sessions)
        self.save_screenshots = save_screenshots
        self._session_dir: "Path | None" = None
        self._seq = 0

    @property
    def session_dir(self) -> "Path | None":
        return self._session_dir

    def _ensure_session(self) -> Path:
        if self._session_dir is None:
            base = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            name, n = base, 2
            while (self._root / name).exists():  # avoid clashing with a same-second run
                name, n = f"{base}_{n}", n + 1
            session = self._root / name
            # Owner-only (0o700) at every level — these dirs hold screenshots/OCR
            # text of whatever was on screen (passwords, messages, PII).
            secure_dir(self._root)
            secure_dir(session)
            secure_dir(session / "shots")
            self._session_dir = session
            self._prune_old_sessions()
        return self._session_dir

    def add(self, pairs, image, source: str, target: str, engine: str, mode: str) -> None:
        """pairs: iterable of (original, translation). Empty originals are dropped
        so '(no text found)' frames don't clutter the log."""
        pairs = [(o, t) for (o, t) in pairs if (o or "").strip()]
        if not pairs:
            return
        session = self._ensure_session()
        self._seq += 1
        seq = self._seq

        shot_rel = None
        if self.save_screenshots and image is not None:
            final = session / "shots" / f"{seq:03d}.png"
            tmp = final.with_name(final.name + ".tmp")
            try:
                self._downscaled(image).save(tmp, "PNG")  # shrink, write tmp, atomic rename
                tmp.replace(final)
                restrict_file(final)  # owner-only: screenshots can hold sensitive content
                shot_rel = f"shots/{seq:03d}.png"
            except Exception:
                tmp.unlink(missing_ok=True)
                shot_rel = None

        record = {
            "seq": seq,
            "ts": datetime.now().isoformat(timespec="seconds"),
            "mode": mode,
            "source": source,
            "target": target,
            "source_name": languages.get(source).name,
            "target_name": languages.get(target).name,
            "engine": engine,
            "shot": shot_rel,
            "pairs": [{"original": o, "translation": t} for (o, t) in pairs],
        }
        jsonl = session / "session.jsonl"
        with jsonl.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        restrict_file(jsonl)  # owner-only: contains the full OCR + translation text

    @staticmethod
    def _downscaled(image, max_side: int = 1600):
        """Keep saved screenshots small — 2x Retina captures are multi-MB."""
        longest = max(image.width, image.height)
        if longest <= max_side:
            return image
        ratio = max_side / longest
        return image.resize((max(1, int(image.width * ratio)), max(1, int(image.height * ratio))))

    def _prune_old_sessions(self) -> None:
        try:
            sessions = sorted(
                (p for p in self._root.iterdir() if p.is_dir()), key=lambda p: p.name
            )
        except FileNotFoundError:
            return
        for old in sessions[: max(0, len(sessions) - self._keep)]:
            _rmtree(old)


def _rmtree(path: Path) -> None:
    for child in path.glob("*"):
        if child.is_dir():
            _rmtree(child)
        else:
            child.unlink(missing_ok=True)
    path.rmdir()


def build_index(session_dir: Path) -> Path:
    """(Re)generate index.html for a session from its JSONL; return its path."""
    records = []
    jsonl = session_dir / "session.jsonl"
    if jsonl.exists():
        for line in jsonl.read_text("utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    out = session_dir / "index.html"
    out.write_text(_render(session_dir.name, records), "utf-8")
    restrict_file(out)  # owner-only: renders the captured text
    return out


def _render(title: str, records: list) -> str:
    cards = []
    for record in records:
        meta = " · ".join(
            html.escape(str(part))
            for part in [
                f"#{record.get('seq', '?')}",
                record.get("ts", ""),
                f"{record.get('source_name', '')} → {record.get('target_name', '')}",
                record.get("mode", ""),
                record.get("engine", ""),
            ]
            if str(part).strip()
        )
        rows = ['<div class="row head"><div>Original</div><div>Translation</div></div>']
        for pair in record.get("pairs", []):
            original = html.escape(pair.get("original", "") or "")
            translation = html.escape(pair.get("translation", "") or "")
            rows.append(
                f'<div class="row"><div class="orig">{original}</div>'
                f'<div class="trans">{translation}</div></div>'
            )
        shot = ""
        if record.get("shot"):
            shot = f'<img class="shot" src="{html.escape(record["shot"])}" alt="screenshot">'
        cards.append(
            f'<article class="cap"><header>{meta}</header>'
            f'<div class="pairs">{"".join(rows)}</div>{shot}</article>'
        )

    body = "\n".join(cards) if cards else "<p>No captures yet.</p>"
    return (
        _PAGE.replace("__TITLE__", html.escape(title))
        .replace("__COUNT__", str(len(records)))
        .replace("__BODY__", body)
    )


_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Translation log — __TITLE__</title>
<style>
  body { font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
         margin: 24px; background: #1d1f23; color: #e8e8ea; }
  h1 { font-size: 18px; font-weight: 600; }
  .sub { color: #9aa0a6; font-size: 13px; margin-bottom: 18px; }
  .cap { background: #26282d; border-radius: 10px; padding: 14px; margin: 14px 0; }
  .cap header { color: #9aa0a6; font-size: 12px; margin-bottom: 8px; }
  .row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px;
         padding: 6px 0; border-top: 1px solid #34373d; }
  .row.head { font-weight: 600; color: #9aa0a6; border-top: none; }
  .orig, .trans { white-space: pre-wrap; word-break: break-word; line-height: 1.4;
                  user-select: text; -webkit-user-select: text; }
  .trans { color: #bfe3c0; }
  img.shot { max-width: 100%; margin-top: 12px; border-radius: 6px; border: 1px solid #34373d; }
  @media (max-width: 640px) { .row { grid-template-columns: 1fr; } }
</style>
</head>
<body>
<h1>Translation log</h1>
<div class="sub">Session __TITLE__ · __COUNT__ captures · select any text to copy</div>
__BODY__
</body>
</html>"""
