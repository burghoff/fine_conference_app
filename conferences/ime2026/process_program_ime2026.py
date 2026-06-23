#!/usr/bin/env python3
# MIT License
#
# Copyright (c) 2026 David Burghoff <burghoff@utexas.edu>
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software.

"""Processor for IME 2026.

Reads only data/IME2026_ProgramBook.pdf and emits conference_data.json.  The
current PDF is a program book, not a full abstract book: concurrent-session rows
carry time, presenter, title, chair, stream, and room, while plenary pages also
carry a speaker bio and abstract.  This processor therefore emits complete
schedule metadata and presenter-style author records for concurrent talks, plus
plenary abstracts where available.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path


def log(msg: str) -> None:
    print(msg, flush=True)


SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "data"
INPUT_PDF = DATA_DIR / "IME2026_ProgramBook.pdf"
OUTPUT_JSON = SCRIPT_DIR / "conference_data.json"

CONFERENCE_NAME = "IME 2026"
CURATOR = None

SESSION_TYPES = [
    {"id": "blue", "label": "Concurrent", "fg": "#2563eb",
     "bg_light": "#e8efff", "bg_dark": "#1a233d"},
    {"id": "orange", "label": "Plenary", "fg": "#ea580c",
     "bg_light": "#ffedd5", "bg_dark": "#3b1d0a"},
    {"id": "rose", "label": "Event", "fg": "#e11d48",
     "bg_light": "#ffe1e8", "bg_dark": "#38161f"},
]
TALK_TYPES = [
    {"id": "sky", "label": "Contributed", "fg": "#0284c7",
     "bg_light": "#e0f2fe", "bg_dark": "#0c2a3d"},
    {"id": "orange", "label": "Plenary", "fg": "#ea580c",
     "bg_light": "#ffedd5", "bg_dark": "#3b1d0a"},
    {"id": "rose", "label": "Event", "fg": "#e11d48",
     "bg_light": "#ffe1e8", "bg_dark": "#38161f"},
]

DATE_BY_HEADING = {
    "Monday, June 29": "2026-06-29",
    "Tuesday, June 30": "2026-06-30",
    "Wednesday, July 1": "2026-07-01",
    "Thursday, July 2": "2026-07-02",
    "Friday, July 3": "2026-07-03",
}

TIME_RE = re.compile(r"(\d{2}:\d{2})~(\d{2}:\d{2})")
SESSION_RE = re.compile(r"^(C\d+[A-H]):\s*(.+)$")
PLENARY_RE = re.compile(
    r"Plenary Session\s+(\d+)\s*/\s*(\d\s*\d:\d{2})~(\d{2}:\d{2})\s*/\s*(.+)",
    re.I,
)
HONORIFICS = {
    "Mr.", "Ms.", "Mrs.", "Dr.", "Prof.", "Professor", "Associate", "Assistant"
}
ROOM_PREFIXES = ("Hoam Hall", "Law School")


def _bootstrap_pdfplumber() -> None:
    try:
        import pdfplumber  # noqa: F401
    except ImportError:
        log("[setup] Installing pdfplumber...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--quiet",
             "pdfplumber>=0.10"])


def _clean(s: str) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    s = s.replace(" -", "-").replace("- ", "-")
    s = s.replace("– ", "–").replace(" –", "–")
    fixes = {
        "Lon-gterm": "Long-term",
        "Lon-gTerm": "Long-Term",
        "Bon-ums alus": "Bonus-malus",
        "R-adnekpendent": "Rank-dependent",
        "Ne-aMr iss": "Near Miss",
        "H-digimhensional": "High-dimensional",
        "Siz-ebiased": "Size-biased",
        "Phas-eType": "Phase-Type",
        "Weath-errelated": "Weather-related",
        "Value-at-Risk": "Value-at-Risk",
        "Mat-riFxree": "Matrix-Free",
        "Representatio-Lnearning": "Representation-Learning",
    }
    for bad, good in fixes.items():
        s = s.replace(bad, good)
    s = re.sub(r"\bRogerL aeven\b", "Roger Laeven", s)
    s = re.sub(r"\bA\.L aeven\b", "A. Laeven", s)
    s = re.sub(r"\bSHELDON LIN\b", "Sheldon Lin", s)
    s = re.sub(r"\bWiscons–iMn adison\b", "Wisconsin-Madison", s)
    s = re.sub(r"\bWisconsi-nMadison\b", "Wisconsin-Madison", s)
    s = re.sub(r"\bUniversit-yWisconsin\b", "University-Wisconsin", s)
    s = re.sub(r"\bL aw School\b", "Law School", s)
    return s.strip()


def _parse_date(lines: list[str]) -> str:
    for line in lines[:6]:
        t = _clean(line)
        if t in DATE_BY_HEADING:
            return DATE_BY_HEADING[t]
    return ""


def _iso(day: str, hhmm: str) -> str:
    return f"{day}T{hhmm}:00" if day and hhmm else ""


def _cluster_rows(words: list[dict], tol: float = 3.0) -> list[dict]:
    rows: list[dict] = []
    for w in sorted(words, key=lambda x: (float(x["top"]), float(x["x0"]))):
        top = float(w["top"])
        if rows and abs(top - rows[-1]["top"]) <= tol:
            rows[-1]["words"].append(w)
            rows[-1]["top"] = min(rows[-1]["top"], top)
        else:
            rows.append({"top": top, "words": [w]})
    for r in rows:
        r["words"].sort(key=lambda x: float(x["x0"]))
        r["text"] = _clean(" ".join(w["text"] for w in r["words"]))
        r["x0"] = min(float(w["x0"]) for w in r["words"])
        r["x1"] = max(float(w["x1"]) for w in r["words"])
    return rows


def _word_text(words: list[dict]) -> str:
    return _clean(
        " ".join(w["text"] for w in sorted(words, key=lambda x: (x["top"], x["x0"])))
    )


def _split_speaker_title(rows: list[dict]) -> tuple[str, str]:
    speaker_words: list[dict] = []
    title_words: list[dict] = []
    for r in rows:
        for w in r["words"]:
            txt = w["text"]
            if TIME_RE.fullmatch(txt):
                continue
            x0 = float(w["x0"])
            if x0 < 242:
                speaker_words.append(w)
            else:
                title_words.append(w)
    speaker = _word_text(speaker_words)
    title = _word_text(title_words)
    # When a short same-row title was swallowed by the speaker band, split after
    # the apparent name if an honorific starts the string.
    toks = speaker.split()
    if toks and toks[0] in HONORIFICS and len(toks) > 4 and not title:
        speaker = " ".join(toks[:4])
        title = " ".join(toks[4:])
    return _clean(speaker), _clean(title)


def _speaker_aff(speaker: str) -> tuple[str, str]:
    speaker = _clean(speaker)
    m = re.match(r"(.+?)\s*\(([^()]+)\)$", speaker)
    if m:
        return _clean(m.group(1)), _clean(m.group(2))
    return speaker, ""


def _parse_concurrent_page(page, page_no: int) -> tuple[list[dict], list[dict], set[str]]:
    text_lines = (page.extract_text(x_tolerance=1, y_tolerance=3) or "").splitlines()
    day = _parse_date(text_lines)
    if not day:
        return [], [], set()
    words = page.extract_words(x_tolerance=1, y_tolerance=3, extra_attrs=["size"])
    rows = [r for r in _cluster_rows(words) if 80 < r["top"] < 790]

    sessions: list[dict] = []
    talks: list[dict] = []
    affs: set[str] = set()
    room = ""
    i = 0
    while i < len(rows):
        txt = rows[i]["text"]
        if txt.startswith(ROOM_PREFIXES):
            room = txt
            i += 1
            continue
        sm = SESSION_RE.match(txt)
        if not sm:
            i += 1
            continue

        sid, stream = sm.groups()
        chair = ""
        chair_aff = ""
        j = i + 1
        if j < len(rows) and rows[j]["text"].startswith("Chair:"):
            chair_raw = rows[j]["text"][len("Chair:"):].strip()
            chair, chair_aff = _speaker_aff(chair_raw)
            if chair_aff:
                affs.add(chair_aff)
            j += 1

        block: list[dict] = []
        while j < len(rows):
            nxt = rows[j]["text"]
            if nxt.startswith(ROOM_PREFIXES) or SESSION_RE.match(nxt):
                break
            if "International Congress on Insurance" in nxt:
                break
            block.append(rows[j])
            j += 1

        time_idxs = [k for k, r in enumerate(block) if TIME_RE.search(r["text"])]
        talk_ids: list[str] = []
        t_bounds: list[tuple[str, str]] = []
        for n, k in enumerate(time_idxs, 1):
            m = TIME_RE.search(block[k]["text"])
            if not m:
                continue
            start, end = m.groups()
            prev_top = block[time_idxs[n - 2]]["top"] if n > 1 else None
            next_top = block[time_idxs[n]]["top"] if n < len(time_idxs) else None
            low = ((prev_top + block[k]["top"]) / 2) if prev_top is not None else -1
            high = ((next_top + block[k]["top"]) / 2) if next_top is not None else 10_000
            span = [r for r in block if low < r["top"] < high]
            speaker, title = _split_speaker_title(span)
            speaker, aff = _speaker_aff(speaker)
            if aff:
                affs.add(aff)
            tid = f"{sid}.{n}"
            talk_ids.append(tid)
            t_bounds.append((start, end))
            talk: dict = {
                "id": tid,
                "session_id": sid,
                "title": title or "(untitled)",
                "number": tid,
                "color": "sky",
                "start_ts": _iso(day, start),
                "end_ts": _iso(day, end),
                "location": room,
                "speaker": speaker,
                "speaker_pos": 0 if speaker else -1,
            }
            if speaker:
                talk["presenter"] = speaker
                talk["first_author"] = speaker
                talk["last_author"] = speaker
                author = {"name": speaker, "insts": [1] if aff else []}
                talk["authors"] = [author]
                if aff:
                    talk["institutions"] = [{"n": 1, "name": aff}]
            talks.append(talk)

        if talk_ids:
            start = min(s for s, _e in t_bounds)
            end = max(e for _s, e in t_bounds)
            sess: dict = {
                "id": sid,
                "title": stream,
                "color": "blue",
                "tags": [{"key": "Session Stream", "value": stream}],
                "start_ts": _iso(day, start),
                "end_ts": _iso(day, end),
                "location": room,
                "presider": chair,
                "presider_aff": chair_aff,
                "talk_ids": talk_ids,
            }
            sessions.append(sess)
        i = j
    return sessions, talks, affs


def _parse_plenary_page(lines: list[str]) -> tuple[dict | None, dict | None, set[str]]:
    day = _parse_date(lines)
    if not day:
        return None, None, set()
    affs: set[str] = set()
    header_idx = -1
    pm = None
    for idx, line in enumerate(lines):
        pm = PLENARY_RE.search(_clean(line))
        if pm:
            header_idx = idx
            break
    if not pm:
        return None, None, affs

    pno, start, end, loc = pm.groups()
    start = re.sub(r"\s+", "", start)
    chair = ""
    chair_aff = ""
    idx = header_idx + 1
    if idx < len(lines) and _clean(lines[idx]).startswith("Chair:"):
        chair, chair_aff = _speaker_aff(_clean(lines[idx])[len("Chair:"):])
        if chair_aff:
            affs.add(chair_aff)
        idx += 1

    body = [_clean(x) for x in lines[idx:] if _clean(x)]
    body = [x for x in body if "International Congress on Insurance" not in x]
    body = [x for x in body if not re.fullmatch(r"\d+", x)]
    abstract_idx = next((i for i, x in enumerate(body) if x == "[Abstract]"), -1)
    before_abs = body[:abstract_idx if abstract_idx >= 0 else len(body)]
    abstract = " ".join(body[abstract_idx + 1:]).strip() if abstract_idx >= 0 else ""

    speaker_idx = -1
    speaker = ""
    speaker_aff = ""
    for i, line in enumerate(before_abs):
        nm, aff = _speaker_aff(line)
        if aff and i > 0:
            speaker_idx = i
            speaker, speaker_aff = nm, aff
            affs.add(aff)
            break
    if speaker_idx < 0:
        return None, None, affs

    title = _clean(" ".join(before_abs[:speaker_idx]))
    bio = _clean(" ".join(before_abs[speaker_idx + 1:]))
    sid = f"P{pno}"
    tid = f"{sid}.1"
    sess = {
        "id": sid,
        "title": f"Plenary Session {pno}",
        "color": "orange",
        "tags": [{"key": "Format", "value": "Plenary Session"}],
        "start_ts": _iso(day, start),
        "end_ts": _iso(day, end),
        "location": _clean(loc),
        "presider": chair,
        "presider_aff": chair_aff,
        "details": bio,
        "talk_ids": [tid],
    }
    talk = {
        "id": tid,
        "session_id": sid,
        "title": title or f"Plenary Session {pno}",
        "number": sid,
        "color": "orange",
        "start_ts": _iso(day, start),
        "end_ts": _iso(day, end),
        "location": _clean(loc),
        "speaker": speaker,
        "presenter": speaker,
        "speaker_pos": 0,
        "first_author": speaker,
        "last_author": speaker,
        "authors": [{"name": speaker, "insts": [1] if speaker_aff else []}],
        "institutions": ([{"n": 1, "name": speaker_aff}] if speaker_aff else []),
        "abstract": _clean(abstract),
    }
    return sess, talk, affs


def _event(id_: str, title: str, day: str, start: str, end: str,
           location: str = "", details: str = "") -> dict:
    out = {
        "id": id_,
        "title": title,
        "color": "rose",
        "start_ts": _iso(day, start),
        "end_ts": _iso(day, end),
        "talk_ids": [],
    }
    if location:
        out["location"] = location
    if details:
        out["details"] = details
    return out


def _manual_events() -> list[dict]:
    """Social/logistics rows visible in the day-at-a-glance pages."""
    return [
        _event("REG-2026-06-29", "Registration Desk Open", "2026-06-29",
               "17:00", "19:30", "600th Anniversary Hall B1",
               "From June 30, the registration desk moves to Business School Hall B3."),
        _event("WELCOME-RECEPTION", "Welcome Reception", "2026-06-29",
               "18:00", "20:00", "600th Anniversary Hall B1"),
        _event("OPENING-CEREMONY", "Opening Ceremony", "2026-06-30",
               "09:00", "09:20", "Business School Hall B3"),
        _event("MUSEUM-SKKU", "Museum of Sungkyunkwan University",
               "2026-06-30", "17:00", "18:00", "600th Anniversary Hall B1"),
        _event("OPENING-DINNER", "Opening Dinner", "2026-06-30",
               "18:30", "20:30", "600th Anniversary Hall B1"),
        _event("EDITORIAL-BOARD", "Editorial Board Meeting (Invitation Only)",
               "2026-07-01", "12:20", "13:20"),
        _event("DINNER-CRUISE-DEPARTURE", "Departure for Dinner Cruise",
               "2026-07-02", "16:40", "17:00", "SKKU to Yeouinaru"),
        _event("DINNER-CRUISE", "Dinner Cruise on the Han River",
               "2026-07-02", "18:00", "21:00"),
        _event("CLOSING-CEREMONY", "Closing Ceremony", "2026-07-03",
               "11:50", "12:40", "Law School B1F-07 (2B107)"),
        _event("LUNCH-CLOSING", "Lunch and Closing", "2026-07-03",
               "12:40", "14:00", "600th Anniversary Hall B1"),
    ]


def main() -> None:
    log("=" * 72)
    log("[config] IME 2026 PROCESSOR starting up.")
    log(f"[config]   input PDF : {INPUT_PDF}")
    log(f"[config]   JSON out  : {OUTPUT_JSON}")
    log("=" * 72)
    if not INPUT_PDF.exists():
        raise SystemExit(
            f"[fatal] Input PDF not found: {INPUT_PDF}\n"
            "        Run fetch_program_ime2026.py first.")

    _bootstrap_pdfplumber()
    import pdfplumber

    sessions: dict[str, dict] = {}
    talks: dict[str, dict] = {}
    affs: set[str] = set()

    with pdfplumber.open(INPUT_PDF) as pdf:
        log(f"[read] PDF pages: {len(pdf.pages)}")
        for page_no, page in enumerate(pdf.pages, 1):
            lines = (page.extract_text(x_tolerance=1, y_tolerance=3) or "").splitlines()
            psess, ptalk, paffs = _parse_plenary_page(lines)
            affs.update(paffs)
            if psess and ptalk:
                sessions[psess["id"]] = psess
                talks[ptalk["id"]] = ptalk
                continue
            csessions, ctalks, caffs = _parse_concurrent_page(page, page_no)
            affs.update(caffs)
            for s in csessions:
                sessions[s["id"]] = s
            for t in ctalks:
                talks[t["id"]] = t

    for ev in _manual_events():
        sessions.setdefault(ev["id"], ev)

    sessions_out = sorted(sessions.values(), key=lambda s: (s.get("start_ts", ""), s["id"]))
    talks_out = sorted(talks.values(), key=lambda t: (t.get("start_ts", ""), t["id"]))
    data = {
        "conference_name": CONFERENCE_NAME,
        "sessions": sessions_out,
        "talks": talks_out,
        "session_types": SESSION_TYPES,
        "talk_types": TALK_TYPES,
        "affiliation_sources": sorted(a for a in affs if a),
    }
    if CURATOR and CURATOR.get("name"):
        data["curator"] = CURATOR

    OUTPUT_JSON.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                           encoding="utf-8")
    log(f"[ok] wrote {OUTPUT_JSON.name}: "
        f"{len(sessions_out)} sessions, {len(talks_out)} talks.")
    log("=" * 72)
    log("DONE.")
    log("=" * 72)


if __name__ == "__main__":
    main()
