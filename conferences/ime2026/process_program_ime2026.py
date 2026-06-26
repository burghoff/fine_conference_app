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

Reads data/IME2026_ProgramBook.pdf for the schedule skeleton and
data/IME2026_AbstractBook.pdf for concurrent-session abstracts.  The program
book supplies time, title, presenter, chair, stream, and room; the abstract book
adds affiliation-bearing presenter records and full abstract text.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import unicodedata
from pathlib import Path


def log(msg: str) -> None:
    print(msg, flush=True)


SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "data"
INPUT_PDF = DATA_DIR / "IME2026_ProgramBook.pdf"
INPUT_ABSTRACT_PDF = DATA_DIR / "IME2026_AbstractBook.pdf"
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
NOISE_RE = re.compile(
    r"^(?:DAY\s+\d+|Abstract Book|: Concurrent Sessions|\d+)$", re.I
)


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


def _norm_match(s: str) -> str:
    s = _clean(s).lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _tokens(s: str) -> set[str]:
    return {t for t in _norm_match(s).split() if len(t) > 2}


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


def _speaker_line(line: str) -> tuple[list[str], str] | None:
    """Parse 'Dr. A / Prof. B (Affiliation)' from an abstract entry."""
    line = _clean(line)
    m = re.match(r"(.+?)\s*\((.+)\)$", line)
    if not m:
        return None
    names_blob, aff = m.groups()
    if not any(h.rstrip(".").lower() in names_blob.lower() for h in HONORIFICS):
        return None
    names = [_clean(x) for x in re.split(r"\s*/\s*", names_blob) if _clean(x)]
    if not names:
        return None
    return names, _clean(aff)


def _parse_abstract_book(pdf_path: Path) -> dict[str, list[dict]]:
    """Return abstracts grouped by session id."""
    if not pdf_path.exists():
        raise SystemExit(
            f"[fatal] Abstract PDF not found: {pdf_path}\n"
            "        Run fetch_program_ime2026.py first.")
    import pdfplumber

    grouped: dict[str, list[dict]] = {}
    cur_session = ""
    title_lines: list[str] = []
    abstract_lines: list[str] = []
    names: list[str] = []
    aff = ""
    state = "idle"

    def flush() -> None:
        nonlocal title_lines, abstract_lines, names, aff, state
        if cur_session and title_lines and names:
            title = _clean(" ".join(title_lines))
            abstract = _clean(" ".join(abstract_lines))
            grouped.setdefault(cur_session, []).append({
                "session_id": cur_session,
                "title": title,
                "names": list(names),
                "affiliation": aff,
                "abstract": abstract,
                "norm_title": _norm_match(title),
                "tokens": _tokens(title),
                "speaker_norms": [_norm_match(n) for n in names],
            })
        title_lines = []
        abstract_lines = []
        names = []
        aff = ""
        state = "idle"

    with pdfplumber.open(pdf_path) as pdf:
        log(f"[read] Abstract PDF pages: {len(pdf.pages)}")
        for page in pdf.pages:
            raw_lines = (page.extract_text(x_tolerance=1, y_tolerance=3) or "").splitlines()
            page_lines = [_clean(x) for x in raw_lines]
            i = 0
            while i < len(page_lines):
                line = page_lines[i]
                i += 1
                if not line or NOISE_RE.match(line):
                    continue
                sm = SESSION_RE.match(line)
                if sm:
                    flush()
                    cur_session = sm.group(1)
                    state = "title"
                    continue
                if not cur_session:
                    continue
                parsed_speaker = _speaker_line(line)
                if state == "title" and parsed_speaker is None:
                    # Some long affiliations wrap onto the next line(s), e.g.
                    # "Prof. X (Department ..., Chinese" / "University ...)".
                    # Try a short same-page join before treating the line as
                    # another title fragment.
                    has_honorific = any(
                        h.rstrip(".").lower() in line.lower()
                        for h in HONORIFICS
                    )
                    if has_honorific and "(" in line and ")" not in line:
                        joined = line
                        consumed = 0
                        for j in range(i, min(i + 3, len(page_lines))):
                            nxt = page_lines[j]
                            if not nxt or NOISE_RE.match(nxt) or SESSION_RE.match(nxt):
                                break
                            joined = f"{joined} {nxt}"
                            consumed += 1
                            parsed_speaker = _speaker_line(joined)
                            if parsed_speaker:
                                i += consumed
                                break
                if state == "title" and parsed_speaker:
                    names, aff = parsed_speaker
                    state = "abstract"
                    continue
                if state == "title":
                    title_lines.append(line)
                elif state == "abstract":
                    abstract_lines.append(line)
        flush()
    return grouped


def _score_abstract_match(talk: dict, entry: dict) -> float:
    title = _norm_match(talk.get("title", ""))
    if not title or not entry["norm_title"]:
        return 0.0
    if title == entry["norm_title"]:
        return 1.0
    tt = _tokens(title)
    et = entry["tokens"]
    jacc = (len(tt & et) / len(tt | et)) if (tt or et) else 0.0
    speaker = _norm_match(talk.get("speaker", ""))
    speaker_match = False
    if speaker:
        for sn in entry["speaker_norms"]:
            if speaker == sn or speaker in sn or sn in speaker:
                speaker_match = True
                break
    contain_bonus = (
        0.15 if (title in entry["norm_title"] or entry["norm_title"] in title)
        else 0.0
    )
    if speaker_match:
        return min(1.0, max(0.72, jacc + 0.35 + contain_bonus))
    # Without a presenter-name match, allow only near-certain title matches.
    # The abstract book can contain session entries in a different order from
    # the program, and some titles share broad insurance/risk vocabulary.
    if jacc >= 0.74 or (contain_bonus and jacc >= 0.60):
        return min(1.0, jacc + contain_bonus)
    return 0.0


def _apply_abstract_overlay(talks: dict[str, dict],
                            abstracts_by_session: dict[str, list[dict]]) -> tuple[int, set[str]]:
    """Attach abstract-book metadata to schedule talks. Returns match count + affs."""
    affs: set[str] = set()
    used: set[tuple[str, int]] = set()
    matched = 0
    for talk in sorted(talks.values(), key=lambda t: (t["session_id"], t["start_ts"])):
        sid = talk["session_id"]
        candidates = abstracts_by_session.get(sid, [])
        best_i = -1
        best_score = 0.0
        for i, entry in enumerate(candidates):
            if (sid, i) in used:
                continue
            score = _score_abstract_match(talk, entry)
            if score > best_score:
                best_i, best_score = i, score
        if best_i < 0 or best_score < 0.38:
            continue
        entry = candidates[best_i]
        used.add((sid, best_i))
        matched += 1

        talk["title"] = entry["title"]
        talk["abstract"] = entry["abstract"]
        names = entry["names"]
        aff = entry["affiliation"]
        if aff:
            affs.add(aff)
        talk["speaker"] = names[0]
        talk["presenter"] = names[0]
        talk["speaker_pos"] = 0
        talk["first_author"] = names[0]
        talk["last_author"] = names[-1] if len(names) > 1 else names[0]
        insts = [1] if aff else []
        talk["authors"] = [{"name": n, "insts": list(insts)} for n in names]
        if aff:
            talk["institutions"] = [{"n": 1, "name": aff}]
    return matched, affs


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
    log(f"[config]   program PDF  : {INPUT_PDF}")
    log(f"[config]   abstract PDF : {INPUT_ABSTRACT_PDF}")
    log(f"[config]   JSON out     : {OUTPUT_JSON}")
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
        log(f"[read] Program PDF pages: {len(pdf.pages)}")
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

    abstracts_by_session = _parse_abstract_book(INPUT_ABSTRACT_PDF)
    n_abs = sum(len(v) for v in abstracts_by_session.values())
    matched, abstract_affs = _apply_abstract_overlay(talks, abstracts_by_session)
    affs.update(abstract_affs)
    log(f"[abstracts] parsed {n_abs} concurrent abstract(s); "
        f"matched {matched}/{len(talks)} schedule talk(s).")

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
