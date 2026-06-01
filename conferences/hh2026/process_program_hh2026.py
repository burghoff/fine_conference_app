#!/usr/bin/env python3
# MIT License
#
# Copyright (c) 2026 David Burghoff <burghoff@utexas.edu>
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

"""process_program_hh2026.py — PROCESS ONLY.

The "processor" half of the Hilton Head 2026 pipeline. It reads the single
source of record — the Final Program PDF in data/ — and emits the clean,
source-agnostic conference_data.json that build_conference_app.py consumes.
No network, no browser; it parses the PDF entirely offline.

Why a geometry / font driven parser
------------------------------------
The Hilton Head program is a single-track schedule whose structure is encoded
almost entirely in FONT and COLUMN position, not in punctuation. Every page is
one narrow column laid out like this (described as FORMAT only — no real
program text appears in this source per the repo's no-hardcoded-content rule):

  * Day header  — Helvetica-Bold ~16pt, centred:        "<Weekday>, <D> <Month>"
  * Session banner — Helvetica-Bold ~10-11pt, centred, may wrap onto a 2nd line.
        Kinds (matched by leading FORMAT words, not by content):
          "Plenary Speaker <n>", "Rising Star Speaker <n>",
          "Invited Speaker <n>", "Session <n> - <topic>",
          "Workshop <n>: <topic>" (Sunday short courses),
          "... Industry Session ...", and poster-section pointers.
  * "Session Chair(s): <Name>, <Aff> [and <Name>, <Aff>]" — Helvetica regular,
        may wrap; attaches a presider to the most recent session.
  * A timed block — begins at the left margin (x0~36) with "HH:MM" (optionally a
        trailing "-" whose end time sits on the following bare "HH:MM" line),
        then in the content column (x0~85):
          - TITLE   : Helvetica-Bold, ALL-CAPS, one or more lines.
          - AUTHORS : Helvetica regular, one or more lines; affiliation markers
                      are bare digits glued to surnames ("Surname1,2").
          - AFFILS  : Helvetica-OBLIQUE; either a single unnumbered institution
                      or a "<n>Institution, COUNTRY" numbered list.
        A timed block whose first content line is NOT all-caps (e.g. a meal,
        break, award announcement, reception) is a non-technical EVENT.
  * Poster pages — a "Poster Presentations - Session <n>" header (with its own
        date + time range), category sub-headers (Helvetica-Bold ~11pt), then
        one block per poster starting with a code ("MP-01", "WP-01", "WCP-01")
        in place of a time; title/authors/affils exactly as for talks.

The parser therefore reconstructs each line from pdfplumber's CHARACTER stream
(the display font uses wide letter-spacing, so word-level extraction is
unreliable), tags every line by font (bold / oblique / size) and left position,
and runs a small state machine over the lines in reading order. The seven
standard session/talk types are assigned from the banner kind, never invented.

Output (next to this script):
    conference_data.json
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "data"
INPUT_PDF = DATA_DIR / "HiltonHead2026_Program.pdf"
OUTPUT_JSON = SCRIPT_DIR / "conference_data.json"

# Display name shown as the app title and on the Sessions/Talks headings. This
# is the single obvious top-level constant the user can review/edit; everything
# else of substance is extracted at runtime from the PDF in data/.
CONFERENCE_NAME = "Hilton Head 2026"
YEAR = 2026

# Optional curator credit (see CONFERENCE_JSON.md). Leave name empty / set to
# None to show only the app-author attribution.
CURATOR = None


def log(msg: str) -> None:
    print(msg, flush=True)


def _bootstrap_pdfplumber() -> None:
    try:
        import pdfplumber  # noqa: F401
    except ImportError:
        print("[setup] Installing pdfplumber…", flush=True)
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--quiet",
             "pdfplumber>=0.10"])


# =============================================================================
# Type / color registries (baked into the JSON; the app reads these directly).
# The seven standard shared types; a conference surfaces only the ones it uses.
# =============================================================================
COLOR_PALETTE = {
    "blue":    {"fg": "#2563eb", "bg_light": "#e8efff", "bg_dark": "#1a233d"},
    "orange":  {"fg": "#ea580c", "bg_light": "#ffedd5", "bg_dark": "#3b1d0a"},
    "fuchsia": {"fg": "#c026d3", "bg_light": "#fae8ff", "bg_dark": "#3a0f3f"},
    "teal":    {"fg": "#0d9488", "bg_light": "#d6f3ef", "bg_dark": "#102b27"},
    "rose":    {"fg": "#e11d48", "bg_light": "#ffe1e8", "bg_dark": "#38161f"},
    "indigo":  {"fg": "#4f46e5", "bg_light": "#e6e4ff", "bg_dark": "#1d1a3d"},
    "sky":     {"fg": "#0284c7", "bg_light": "#e0f2fe", "bg_dark": "#0c2a3d"},
}


def _with_colors(entries: list[dict]) -> list[dict]:
    out = []
    for e in entries:
        pal = COLOR_PALETTE.get(e["id"])
        out.append({**e, **pal} if pal else dict(e))
    return out


SESSION_TYPE_REGISTRY = _with_colors([
    {"id": "blue",    "label": "Technical"},
    {"id": "orange",  "label": "Plenary"},
    {"id": "fuchsia", "label": "Tutorial"},
    {"id": "teal",    "label": "Poster"},
    {"id": "rose",    "label": "Event"},
])
TALK_TYPE_REGISTRY = _with_colors([
    {"id": "orange",  "label": "Plenary"},
    {"id": "indigo",  "label": "Invited"},
    {"id": "sky",     "label": "Contributed"},
    {"id": "fuchsia", "label": "Tutorial"},
    {"id": "teal",    "label": "Poster"},
    {"id": "rose",    "label": "Event"},
])

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday",
            "saturday", "sunday")


# =============================================================================
# Line model: reconstruct lines from the character stream, tagged by font.
# =============================================================================
LEFT_MARGIN_X = 55.0     # left token column (time / poster code) starts ~36.
CONTENT_X = 75.0         # content column (title / authors / affils) starts ~85.


class Line:
    """One visual line, reconstructed from pdfplumber chars.

    Exposes the full text, the leftmost x, and the font of the CONTENT portion
    (the part in the right-hand column), so a "HH:MM <Bold Title>" row reports
    the title's font even though the time prefix shares the row."""

    __slots__ = ("page", "top", "chars", "text", "x0", "size",
                 "bold", "oblique", "content_chars", "content_text",
                 "content_bold", "content_oblique", "content_size")

    def __init__(self, page: int, chars: list[dict]):
        self.page = page
        chars = sorted(chars, key=lambda c: c["x0"])
        self.chars = chars
        self.top = min(c["top"] for c in chars)
        self.x0 = min(c["x0"] for c in chars)
        self.text = _join_chars(chars)
        self.size, self.bold, self.oblique = _font_of(chars)
        # Content portion: chars sitting in the right column. For a left-margin
        # row this drops the time / poster-code prefix so the font reflects the
        # title/author/affil; for an already-indented line it's the whole line.
        cc = [c for c in chars if c["x0"] >= CONTENT_X]
        if not cc:
            cc = chars
        self.content_chars = cc
        self.content_text = _join_chars(cc)
        self.content_size, self.content_bold, self.content_oblique = _font_of(cc)


def _join_chars(chars: list[dict]) -> str:
    """Reconstruct text from chars in x order. Space characters are present in
    the stream, so a plain join restores the words; we just normalise runs of
    whitespace and strip."""
    s = "".join(c["text"] for c in chars)
    return re.sub(r"[ \t ]+", " ", s).strip()


def _font_of(chars: list[dict]) -> tuple[float, bool, bool]:
    """(median-ish size, is_bold, is_oblique) for a run of chars, judged by the
    dominant font among the alphabetic glyphs."""
    alpha = [c for c in chars if c["text"].strip()]
    if not alpha:
        return 0.0, False, False
    sizes = sorted(c.get("size", 0.0) for c in alpha)
    size = sizes[len(sizes) // 2]
    nbold = sum(1 for c in alpha if "Bold" in c.get("fontname", ""))
    nobl = sum(1 for c in alpha
               if ("Oblique" in c.get("fontname", "")
                   or "Italic" in c.get("fontname", "")))
    n = len(alpha)
    return size, nbold * 2 >= n, nobl * 2 >= n


def _extract_lines(pdf) -> list[Line]:
    """All visual lines across all pages, in reading order, with footers and
    blank/page-number lines dropped."""
    lines: list[Line] = []
    for pno, page in enumerate(pdf.pages, 1):
        chars = page.chars
        if not chars:
            continue
        # Cluster chars into rows by their `top` baseline.
        rows: list[list[dict]] = []
        tops: list[float] = []
        for c in sorted(chars, key=lambda c: (round(c["top"], 1), c["x0"])):
            placed = False
            for i, t in enumerate(tops):
                if abs(c["top"] - t) <= 2.5:
                    rows[i].append(c)
                    tops[i] = min(t, c["top"])
                    placed = True
                    break
            if not placed:
                rows.append([c])
                tops.append(c["top"])
        for r in rows:
            ln = Line(pno, r)
            if not ln.text:
                continue
            if ln.top > 558:                      # page-footer zone
                continue
            if re.fullmatch(r"\d{1,3}", ln.text):  # bare page number
                continue
            lines.append(ln)
    return lines


# =============================================================================
# Line classification helpers.
# =============================================================================
_DAY_RE = re.compile(
    r"^(?P<wd>%s),?\s+(?P<dom>\d{1,2})\s+(?P<mon>[A-Za-z]+)$"
    % "|".join(w.capitalize() for w in WEEKDAYS), re.I)
_POSTER_HDR_RE = re.compile(r"^Poster Presentations\b", re.I)
_TIME_ROW_RE = re.compile(r"^(\d{1,2}:\d{2})\s*(-)?\s*(.*)$")
_BARE_TIME_RE = re.compile(r"^\d{1,2}:\d{2}\s*-?\s*$")
_POSTER_CODE_RE = re.compile(r"^([A-Z]{1,4}-\d+)\b[\.\s]*(.*)$")
_CHAIR_RE = re.compile(r"^Session Chairs?\b\s*:?\s*(.*)$", re.I)
# Poster-session date/time header, e.g. "<Weekday>, <D> <Month>  HH:MM – HH:MM".
_POSTER_DATETIME_RE = re.compile(
    r"^(%s),?\s+(\d{1,2})\s+([A-Za-z]+)\s+(\d{1,2}:\d{2})\s*[–—\-]\s*(\d{1,2}:\d{2})"
    % "|".join(w.capitalize() for w in WEEKDAYS), re.I)


def _is_header_class(ln: Line) -> bool:
    """A structural header line: bold and >= 10pt (day header, session banner,
    or poster category). Judged on the CONTENT column only — the left-margin
    time digits render a hair larger (~10pt) than the 9.1pt body and would
    otherwise lift a "HH:MM <event>" row to header size. Banners (no time
    column) are 10.6-16pt; talk titles/authors/affils are <= 9.1pt; the
    page-number footer is regular weight."""
    return ln.content_bold and ln.content_size >= 10.0


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace(" ", " ")).strip()


def _all_caps(s: str) -> bool:
    """True if the alphabetic content is essentially all upper-case — the signal
    that a timed block is a technical talk title rather than a plain event."""
    letters = [c for c in s if c.isalpha()]
    if len(letters) < 3:
        return False
    upper = sum(1 for c in letters if c.isupper())
    return upper / len(letters) >= 0.8


# -----------------------------------------------------------------------------
# Title-case rendering for the ALL-CAPS talk/poster titles, with data-driven
# acronym restoration. Ported from the SPIE PW 2025 processor: the program
# prints talk titles in ALL CAPS but session titles, special-event blurbs and
# institution names in normal mixed case, so that already-correctly-cased text
# is a ready-made dictionary of how each acronym is actually written (MEMS,
# NEMS, AI, LDV, …). Nothing is hardcoded — the acronym casing is learned.
# -----------------------------------------------------------------------------
_MINOR = {"a", "an", "and", "as", "at", "but", "by", "for", "from", "in", "of",
          "on", "or", "the", "to", "via", "with", "vs", "nor"}


def learn_acronyms(corpus: list[str]) -> dict[str, str]:
    """Learn canonical acronym casing from already-correctly-cased text. Maps each
    token's UPPERCASE form to its most common observed casing, keeping only those
    whose canonical form is genuinely acronym-cased (has an uppercase letter and
    is not just an ordinary Capitalized word)."""
    from collections import Counter
    forms: dict[str, "Counter"] = {}
    for text in corpus:
        for tok in (text or "").split():
            core = tok.strip(".,;:!?()[]{}\"'")
            if len(core) < 2 or not any(c.isalpha() for c in core):
                continue
            forms.setdefault(core.upper(), Counter())[core] += 1
    acr: dict[str, str] = {}
    for up, counter in forms.items():
        canon = counter.most_common(1)[0][0]
        if any(c.isupper() for c in canon) and \
                canon != canon[:1].upper() + canon[1:].lower():
            acr[up] = canon
    return acr


def _recase_token(core: str, acr: dict[str, str]) -> str:
    """Apply learned acronym casing to one UPPERCASE token, falling back to
    roman-numeral preservation and ordinary Capitalization. Alpha runs inside a
    hyphen/slash compound are recased independently so a buried acronym still
    restores."""
    up = core.upper()
    if up in acr:
        return acr[up]
    if up.endswith("S") and up[:-1] in acr:
        return acr[up[:-1]] + "s"
    alpha = re.sub(r"[^A-Za-z]", "", core).lower()
    if re.fullmatch(r"[ivxlcdm]{2,}", alpha):       # roman numeral (II, XVIII)
        return core.upper()

    def _run(m: "re.Match") -> str:
        seg = m.group(0)
        u = seg.upper()
        if u in acr:
            return acr[u]
        if u.endswith("S") and u[:-1] in acr:
            return acr[u[:-1]] + "s"
        return seg[:1].upper() + seg[1:].lower()
    return re.sub(r"[A-Za-z]+", _run, core)


def _smart_title(t: str, acr: dict[str, str]) -> str:
    """Render an ALL-CAPS title for display: restore learned acronym casing,
    lowercase minor joining words (except first/last), and capitalize the first
    word and any word after a colon."""
    toks = t.split()
    n = len(toks)
    out: list[str] = []
    at_start = True
    for i, tok in enumerate(toks):
        alpha = re.sub(r"[^A-Za-z]", "", tok).lower()
        if not (tok.upper() in acr or
                (tok.upper().endswith("S") and tok.upper()[:-1] in acr)) \
                and 0 < i < n - 1 and alpha in _MINOR:
            rep = tok.lower()
        else:
            rep = _recase_token(tok, acr)
        if at_start:
            j = next((k for k, c in enumerate(rep) if c.isalpha()), None)
            if j is not None and rep[j].islower():
                rep = rep[:j] + rep[j].upper() + rep[j + 1:]
        out.append(rep)
        at_start = tok.endswith(":")
    return " ".join(out)


# =============================================================================
# Author / institution parsing.
# =============================================================================
def _split_numbered_insts(s: str) -> list[tuple[int, str]]:
    """Split a numbered affiliation string into [(n, body), ...].

    The Hilton Head format glues the marker to the institution with no period:
    "<n>Institution, COUNTRY, <n>Institution, COUNTRY, and <n>Institution,
    COUNTRY". A marker is a 1-2 digit number that sits at the string start, or
    right after a ',' (optionally followed by 'and'), or after a bare 'and',
    AND is immediately followed by a capital letter (institution names start
    upper-case). The body of each institution keeps its own internal commas and
    trailing country."""
    anchors: list[tuple[int, int]] = []
    for m in re.finditer(r"(?:^|,\s*(?:and\s+)?|\s+and\s+)(\d{1,2})(?=[A-Z])", s):
        anchors.append((m.start(1), int(m.group(1))))
    if not anchors:
        return []
    out: list[tuple[int, str]] = []
    for i, (pos, num) in enumerate(anchors):
        end = anchors[i + 1][0] if i + 1 < len(anchors) else len(s)
        body = s[pos:end]
        body = re.sub(r"^\d{1,2}", "", body, count=1)          # strip the marker
        body = body.strip().strip(",").strip()
        body = re.sub(r"[\s,]+and$", "", body).strip().strip(",").strip()
        if body:
            out.append((num, _clean(body)))
    return out


def _parse_institutions(aff_lines: list[str]) -> list[dict]:
    """Parse oblique affiliation line(s) into [{n, name, alt_names}].

    Numbered form wins if markers are present; otherwise the whole thing is one
    unnumbered institution (n=1). Multi-line unnumbered affiliations (a single
    institution wrapped across rows) are joined with a space."""
    joined = _clean(" ".join(l.strip() for l in aff_lines if l.strip()))
    if not joined:
        return []
    numbered = _split_numbered_insts(joined)
    if numbered:
        return [{"n": n, "name": name, "alt_names": []} for n, name in numbered]
    return [{"n": 1, "name": joined, "alt_names": []}]


def _parse_author_token(tok: str) -> tuple[str, list[int]]:
    """One author token -> (name, [inst numbers]). Strips a leading 'and ' and a
    trailing run of affiliation-marker digits glued to the surname
    ('Surname1,2' -> insts [1,2])."""
    tok = _clean(tok)
    tok = re.sub(r"^and\s+", "", tok, flags=re.I)
    insts: list[int] = []
    m = re.search(r"(?<=[A-Za-z\.\)À-ſ])([\d,]+)$", tok)
    if m and any(ch.isdigit() for ch in m.group(1)):
        insts = [int(d) for d in re.findall(r"\d+", m.group(1))]
        tok = tok[:m.start()].strip()
    return tok.strip().rstrip(",").strip(), insts


def _parse_authors(author_text: str,
                   institutions: list[dict]) -> tuple[list[dict], list[str]]:
    """Parse an author line into (authors, aliases). Authors are comma-separated
    (the internal comma of a '1,2' marker is not a separator because it is not
    followed by whitespace). When there is exactly one institution and no author
    carried a marker, every author is attributed to inst 1. References to a
    non-existent institution number are dropped so the JSON stays consistent."""
    valid = {i["n"] for i in institutions}
    authors: list[dict] = []
    # Authors are separated by commas, OR by a bare "and"/"&" (a two-author
    # byline "X and Y", or the Oxford-comma "…, and Z"). The comma split
    # requires a FOLLOWING space so an affiliation marker's internal comma
    # ("Surname1,2") is never split; " and "/" & " need surrounding whitespace
    # so a name like "Anand" is left intact.
    for tok in re.split(r",(?=\s)|\s+and\s+|\s+&\s+", author_text):
        tok = tok.strip()
        if not tok:
            continue
        name, insts = _parse_author_token(tok)
        if name:
            authors.append({"name": name, "insts": insts})

    if not any(a["insts"] for a in authors) and len(institutions) == 1:
        for a in authors:
            a["insts"] = [1]
    for a in authors:
        seen: set[int] = set()
        a["insts"] = [n for n in a["insts"]
                      if n in valid and not (n in seen or seen.add(n))]
    return authors, [a["name"] for a in authors]


def _split_body_fonts(body_lines: list[Line]) -> tuple[str, str]:
    """Split a block's byline region into (author_text, affiliation_text) by
    CHARACTER font: regular weight is author text, oblique/italic is
    affiliation. Most talks keep the two on separate lines, but the industry
    session puts them on one line ('<Speaker, regular>, <Company, oblique>'),
    so a line-level oblique test mis-files them — char-level is robust."""
    author_parts: list[str] = []
    aff_parts: list[str] = []
    for l in body_lines:
        obl: list[dict] = []
        reg: list[dict] = []
        for c in l.content_chars:
            fn = c.get("fontname", "")
            (obl if ("Oblique" in fn or "Italic" in fn) else reg).append(c)
        obl_txt = _join_chars(obl)
        reg_txt = _join_chars(reg)
        # Oblique is used for THREE things: affiliation text, the tiny
        # superscript affiliation MARKERS glued to author surnames, and the
        # italic connective "and" / spaces in an author list. Only treat the
        # oblique run as a real affiliation when it carries alphabetic content
        # beyond that connective; otherwise the line is an author line and is
        # kept whole (markers in place, spacing preserved) so the comma-split
        # works. When a line mixes real regular names AND real oblique text it
        # is an industry byline ("<Speaker>, <Company>") and is split.
        # Alphabetic content of each font run, with the connective "and" removed
        # (it is italic in author lists and often glued to a marker, e.g.
        # "45and"). What remains in the oblique run, if anything, is real
        # affiliation text.
        obl_real = re.sub(r"[^a-z]", "", obl_txt.lower()).replace("and", "")
        reg_real = re.sub(r"[^A-Za-z]", "", reg_txt)
        if not obl_real:
            author_parts.append(_join_chars(l.content_chars))
        elif not reg_real:
            aff_parts.append(obl_txt)
        else:
            author_parts.append(reg_txt)
            aff_parts.append(obl_txt)
    # Join lines with a space so the last author on one line is not glued to the
    # first on the next ("…Boning3" + "Michael…").
    return _clean(" ".join(author_parts)), _clean(" ".join(aff_parts))


def _block_has_body(lines: list[Line]) -> bool:
    """True once a block has moved past its (bold) title into body lines — i.e.
    it contains an author line (regular weight) or an affiliation (oblique).
    Used to decide a following ALL-CAPS title begins a new talk, not a title
    continuation."""
    return any(l.content_oblique or not l.content_bold for l in lines)


def _talk_payload_from_lines(content_lines: list[Line],
                             fallback_title: str) -> dict | None:
    """Turn a timed/poster block's content lines (already time/code-stripped)
    into a parsed talk dict, or None if it carries no usable content.

    Returns {title, authors, author_aliases, institutions, speaker, presenter,
             speaker_pos, first_author, last_author, is_event, details}."""
    if not content_lines:
        return None

    first = content_lines[0]

    # --- EVENT: a bold, mixed-case lead line (meal, break, ceremony, award
    # announcement, …). Talk titles are bold ALL-CAPS; a bold line that is not
    # all-caps is therefore an event label, not a title. ---
    if (first.content_bold and not first.content_oblique
            and not _all_caps(first.content_text)):
        details = _clean(" ".join(l.content_text for l in content_lines[1:]))
        return {"is_event": True, "title": first.content_text.strip(),
                "details": details}

    # --- TALK: the leading run of bold ALL-CAPS lines is the title. Anything
    # after it is the byline — note a single featured speaker's NAME is also
    # rendered bold (but mixed-case), so it falls through to the author lines
    # rather than being mistaken for more title. Oblique lines are affiliations.
    i = 0
    title_parts: list[str] = []
    while (i < len(content_lines) and content_lines[i].content_bold
           and not content_lines[i].content_oblique
           and _all_caps(content_lines[i].content_text)):
        title_parts.append(content_lines[i].content_text)
        i += 1
    author_text, aff_text = _split_body_fonts(content_lines[i:])

    title = _clean(" ".join(title_parts))

    # No ALL-CAPS title and no affiliations: a plain agenda line that simply
    # wasn't bold-flagged like the other events (e.g. an affinity-group
    # breakfast). It is an event, not a title-less talk.
    if not title and not aff_text.strip():
        details = _clean(" ".join(l.content_text for l in content_lines[1:]))
        return {"is_event": True, "title": first.content_text.strip(),
                "details": details}

    institutions = _parse_institutions([aff_text]) if aff_text.strip() else []
    authors, aliases = _parse_authors(author_text, institutions)

    if not title:
        # Title-less talk (e.g. a Sunday workshop's organizer block); borrow the
        # parent session's topic.
        title = fallback_title
        if not title:
            return None

    for inst in institutions:
        inst["alt_names"] = []
    speaker = authors[0]["name"] if authors else ""
    first_author = authors[0]["name"] if authors else ""
    last_author = authors[-1]["name"] if len(authors) > 1 else ""
    return {
        "is_event": False,
        "title": title,
        "authors": authors,
        "author_aliases": aliases,
        "institutions": institutions,
        "speaker": speaker,
        "presenter": speaker,
        "speaker_pos": 0 if authors else None,
        "first_author": first_author,
        "last_author": last_author,
    }


# =============================================================================
# Banner classification -> session + talk colors.
# =============================================================================
def _banner_kind(title: str) -> dict:
    """Map a banner's leading FORMAT words onto the standard taxonomy. Returns
    {format, session_color, talk_color, short}. `short` strips a leading
    "Workshop N:" / "Session N -" so a title-less child talk can borrow it."""
    t = _clean(title)
    low = t.lower()
    if low.startswith("plenary speaker"):
        return {"format": "Plenary", "session_color": "orange",
                "talk_color": "orange", "short": t}
    if low.startswith("rising star"):
        return {"format": "Rising Star Speaker", "session_color": "blue",
                "talk_color": "indigo", "short": t}
    if low.startswith("invited speaker"):
        return {"format": "Invited Speaker", "session_color": "blue",
                "talk_color": "indigo", "short": t}
    if "industry session" in low:
        return {"format": "Industry Session", "session_color": "blue",
                "talk_color": "indigo", "short": t}
    if re.match(r"^workshop\s+\d", low):
        short = re.sub(r"^workshop\s+\d+\s*[:\-–]?\s*", "", t, flags=re.I)
        return {"format": "Sunday Workshop", "session_color": "fuchsia",
                "talk_color": "fuchsia", "short": short or t}
    if re.match(r"^session\s+\d", low):
        short = re.sub(r"^session\s+\d+\s*[\-–:]?\s*", "", t, flags=re.I)
        return {"format": "Technical Session", "session_color": "blue",
                "talk_color": "sky", "short": short or t}
    return {"format": "Session", "session_color": "blue",
            "talk_color": "sky", "short": t}


def _is_poster_pointer(title: str) -> bool:
    """A day-page banner that merely points at a poster section ("Poster Session
    N", "Poster Session N and Reception"). The real catalog comes from the
    poster pages, so these pointers (and their one folded subtitle line) are
    skipped."""
    return bool(re.match(r"^poster session\b", _clean(title), re.I))


# =============================================================================
# The state machine: walk the lines and build sessions + talks.
# =============================================================================
def _iso(dom: int, month: int, hhmm: str) -> str:
    h, m = hhmm.split(":")
    return f"{YEAR:04d}-{month:02d}-{dom:02d}T{int(h):02d}:{int(m):02d}:00"


# A "<Weekday> - HH:MM - HH:MM - <Room>" schedule line in the Special Events
# section (format only). The trailing field is the room/location.
_SE_WHEN_RE = re.compile(
    r"^(?:%s)\b.*?\d{1,2}:\d{2}.*?[-–—]\s*(?P<room>[^-–—]+)$"
    % "|".join(w.capitalize() for w in WEEKDAYS), re.I)


def _se_key(text: str) -> str | None:
    """Normalised join key for a Special-Events header or a session title, so the
    two can be matched: 'workshop <n>', 'industry', or 'rump' (None = no match)."""
    low = text.lower()
    m = re.search(r"workshop\s+(\d+)", low)
    if m:
        return f"workshop {m.group(1)}"
    if "industry session" in low:
        return "industry"
    if "rump session" in low:
        return "rump"
    return None


def _parse_special_events(lines: list["Line"]) -> dict[str, dict]:
    """Harvest the descriptive Special-Events blocks (which precede the day-by-day
    program) into {key: {location, description}}. Each block is a bold >=10pt
    header, an optional bold subtitle, a 'Day - time - room' line, then the
    description paragraph(s). Best-effort: anything unparseable is skipped."""
    out: dict[str, dict] = {}
    started = False
    cur: dict | None = None

    def _flush():
        nonlocal cur
        if cur and cur["key"]:
            desc = _clean(" ".join(cur["desc"]))
            prev = out.get(cur["key"])
            if not prev or len(desc) > len(prev.get("description", "")):
                out[cur["key"]] = {"location": cur["location"],
                                   "description": desc}
        cur = None

    for ln in lines:
        if not started:
            if ln.text.strip().upper().startswith("SPECIAL EVENTS"):
                started = True
            continue
        if _DAY_RE.match(ln.text):     # the day-by-day program has begun
            break
        if _is_header_class(ln):       # a Special-Events header (may wrap)
            if cur is not None and not cur["location"] and not cur["desc"]:
                # still in the (multi-line) header — keep merging.
                cur["header"] += " " + ln.text
                cur["key"] = _se_key(cur["header"])
            else:
                _flush()
                cur = {"header": ln.text, "key": _se_key(ln.text),
                       "location": "", "desc": []}
            continue
        if cur is None:
            continue
        wm = _SE_WHEN_RE.match(ln.text)
        if wm:
            if not cur["location"]:
                cur["location"] = _clean(wm.group("room"))
            continue
        cur["desc"].append(ln.text)    # full line — these sit at the margin
    _flush()
    return out


def _parse_chair_blob(blob: str) -> tuple[str, str]:
    """'<Name>, <Aff> [and <Name>, <Aff>]' -> ('Name; Name', 'Aff;Aff')."""
    names: list[str] = []
    affs: list[str] = []
    for chunk in re.split(r"\s+and\s+", blob):
        chunk = _clean(chunk)
        if not chunk:
            continue
        nm, _, aff = chunk.partition(",")
        names.append(_clean(nm))
        affs.append(_clean(aff))
    return "; ".join(n for n in names if n), ";".join(affs)


def build_conference_data() -> dict:
    import pdfplumber

    with pdfplumber.open(str(INPUT_PDF)) as pdf:
        log(f"  PDF has {len(pdf.pages)} pages; extracting lines…")
        lines = _extract_lines(pdf)
    log(f"  reconstructed {len(lines)} content lines.")

    sessions: list[dict] = []
    talks: list[dict] = []
    # Schedule items for end-time backfill: (day_key, start_min, obj, kind).
    timed: list[dict] = []
    aff_pool: set[str] = set()

    sess_seq = 0
    talk_seq = 0

    def _record_affs(institutions: list[dict]) -> None:
        for inst in institutions:
            nm = _clean(inst.get("name") or "")
            if nm:
                aff_pool.add(nm)

    def _new_session(title, color, fmt, *, start_ts=None, end_ts=None,
                     details="", topic="") -> dict:
        nonlocal sess_seq
        sess_seq += 1
        s = {
            "id": f"S{sess_seq:03d}", "title": title, "color": color,
            "format": fmt, "topic": topic, "details": details,
            "location": "", "presider": "", "presider_aff": "",
            "start_ts": start_ts, "end_ts": end_ts, "talk_ids": [],
        }
        sessions.append(s)
        return s

    # Parser state.
    cur_dom = cur_month = None          # current calendar day (from day header)
    mode = "pre"                        # 'pre' | 'day' | 'poster' | 'done'
    banner_session: dict | None = None  # session that TALKS attach to
    last_session: dict | None = None    # session a chair attaches to
    poster_session: dict | None = None  # current poster catalog session
    poster_pending_meta = False         # poster header just seen; want date line
    skip_next_event = False             # consume a poster-pointer's folded line
    collecting_chair = ""               # accumulating a (possibly wrapped) chair

    # Block accumulation.
    block_lines: list[Line] = []
    block_start = None                  # "HH:MM"
    block_end = None                    # explicit "HH:MM" from a bare-time row
    block_is_poster = False
    block_code = ""

    def _flush_chair() -> None:
        nonlocal collecting_chair
        blob = _clean(collecting_chair)
        collecting_chair = ""
        if not blob or last_session is None:
            return
        names, affs = _parse_chair_blob(blob)
        if names:
            last_session["presider"] = (
                "; ".join(p for p in [last_session["presider"], names] if p))
            last_session["presider_aff"] = ";".join(
                p for p in [last_session["presider_aff"], affs] if p)
            for a in affs.split(";"):
                if _clean(a):
                    aff_pool.add(_clean(a))

    def _flush_block() -> None:
        nonlocal block_lines, block_start, block_end, block_is_poster
        nonlocal block_code, banner_session, last_session, talk_seq
        nonlocal skip_next_event
        if not block_lines and not block_code:
            block_lines, block_start, block_end = [], None, None
            block_is_poster, block_code = False, ""
            return

        if block_is_poster and poster_session is not None:
            fallback = ""
        elif banner_session is not None:
            fallback = _banner_kind(banner_session["format"] and
                                    banner_session["title"]).get("short", "")
            fallback = banner_session.get("topic") or banner_session["title"]
        else:
            fallback = ""

        payload = _talk_payload_from_lines(block_lines, fallback)
        # Reset block accumulators up-front; we've captured what we need.
        bs, be = block_start, block_end
        is_poster, code = block_is_poster, block_code
        block_lines, block_start, block_end = [], None, None
        block_is_poster, block_code = False, ""

        if payload is None:
            return

        # ---- POSTER ----
        if is_poster:
            if poster_session is None:
                return
            talk_seq += 1
            tid = f"T{talk_seq:03d}"
            _record_affs(payload["institutions"])
            talks.append({
                "id": tid, "session_id": poster_session["id"],
                "title": payload["title"], "number": code,
                "start_ts": poster_session["start_ts"],
                "end_ts": poster_session["end_ts"],
                "speaker": payload["speaker"], "presenter": payload["presenter"],
                "speaker_pos": payload["speaker_pos"],
                "authors": payload["authors"],
                "author_aliases": payload["author_aliases"],
                "institutions": payload["institutions"],
                "institutions_may_dedup": False,
                "abstract": "", "status": "", "withdrawn": False,
                "first_author": payload["first_author"],
                "last_author": payload["last_author"],
                "color": "teal", "location": "",
            })
            poster_session["talk_ids"].append(tid)
            return

        # ---- timed EVENT ----
        if payload["is_event"]:
            if skip_next_event:
                skip_next_event = False     # a poster pointer's folded subtitle
                return
            start_ts = _iso(cur_dom, cur_month, bs) if (bs and cur_dom) else None
            end_ts = _iso(cur_dom, cur_month, be) if (be and cur_dom) else None

            # A Sunday Workshop runs a half-day and OWNS its internal agenda
            # items (Lunch, a panel, the closing "Adjourn"). Fold those into the
            # workshop as Event-typed talk-rows rather than scattering them as
            # standalone sessions. An "Adjourn" marks the workshop's end, so it
            # closes the session (the next workshop banner reopens one).
            if banner_session is not None \
                    and banner_session["format"] == "Sunday Workshop":
                talk_seq += 1
                tid = f"T{talk_seq:03d}"
                talks.append({
                    "id": tid, "session_id": banner_session["id"],
                    "title": payload["title"], "number": "",
                    "start_ts": start_ts, "end_ts": end_ts,
                    "speaker": "", "presenter": "", "speaker_pos": None,
                    "authors": [], "author_aliases": [], "institutions": [],
                    "institutions_may_dedup": False,
                    "abstract": payload.get("details", ""),
                    "status": "", "withdrawn": False,
                    "first_author": "", "last_author": "",
                    "color": "rose", "location": "",
                })
                banner_session["talk_ids"].append(tid)
                if start_ts:
                    timed.append({"day": (cur_month, cur_dom), "start": bs,
                                  "obj": talks[-1], "kind": "talk"})
                if re.match(r"^adjourn\b", payload["title"], re.I):
                    banner_session = None      # workshop concluded
                return

            ev = _new_session(payload["title"], "rose", "Event",
                              start_ts=start_ts, end_ts=end_ts,
                              details=payload.get("details", ""))
            last_session = ev
            if start_ts:
                timed.append({"day": (cur_month, cur_dom),
                              "start": bs, "obj": ev, "kind": "session"})
            return

        # ---- timed TALK ----
        if banner_session is None:
            banner_session = _new_session(
                f"{_weekday(cur_month, cur_dom)} Program", "blue", "Session")
            last_session = banner_session
        skip_next_event = False
        talk_seq += 1
        tid = f"T{talk_seq:03d}"
        start_ts = _iso(cur_dom, cur_month, bs) if (bs and cur_dom) else None
        end_ts = _iso(cur_dom, cur_month, be) if (be and cur_dom) else None
        kind = _banner_kind(banner_session["title"])
        _record_affs(payload["institutions"])
        t = {
            "id": tid, "session_id": banner_session["id"],
            "title": payload["title"], "number": "",
            "start_ts": start_ts, "end_ts": end_ts,
            "speaker": payload["speaker"], "presenter": payload["presenter"],
            "speaker_pos": payload["speaker_pos"],
            "authors": payload["authors"],
            "author_aliases": payload["author_aliases"],
            "institutions": payload["institutions"],
            "institutions_may_dedup": False,
            "abstract": "", "status": "", "withdrawn": False,
            "first_author": payload["first_author"],
            "last_author": payload["last_author"],
            "color": kind["talk_color"], "location": "",
        }
        talks.append(t)
        banner_session["talk_ids"].append(tid)
        if start_ts:
            timed.append({"day": (cur_month, cur_dom), "start": bs,
                          "obj": t, "kind": "talk"})

    def _weekday(month, dom) -> str:
        import datetime as _dt
        if not month or not dom:
            return ""
        return _dt.date(YEAR, month, dom).strftime("%A")

    # ---- banner accumulation (banners may wrap onto a 2nd line) ----
    pending_banner: list[str] = []

    def _flush_banner() -> None:
        nonlocal pending_banner, banner_session, last_session, poster_session
        nonlocal skip_next_event
        if not pending_banner:
            return
        title = _clean(" ".join(pending_banner))
        pending_banner = []
        if mode == "poster":
            # Poster pages carry category sub-headers (a grouping that changes
            # several times within one session); we don't surface them as a
            # single misleading session-level tag.
            return
        if _is_poster_pointer(title):
            # Day-page pointer at a poster section; skip it and its folded line.
            banner_session = None
            skip_next_event = True
            return
        kind = _banner_kind(title)
        banner_session = _new_session(title, kind["session_color"],
                                      kind["format"], topic=kind["short"])
        last_session = banner_session
        skip_next_event = False

    # =====================================================================
    # Main pass.
    # =====================================================================
    for ln in lines:
        if mode == "done":
            break
        text = ln.text

        # ---- day header ----
        m = _DAY_RE.match(text)
        if m and m.group("mon").lower() in MONTHS and not _POSTER_HDR_RE.match(text):
            _flush_block(); _flush_chair(); _flush_banner()
            cur_dom = int(m.group("dom"))
            cur_month = MONTHS[m.group("mon").lower()]
            mode = "day"
            banner_session = None
            poster_session = None
            skip_next_event = False
            continue

        if mode == "pre":
            continue                       # skip front matter before day 1

        # ---- poster section header ----
        if _POSTER_HDR_RE.match(text):
            _flush_block(); _flush_chair(); _flush_banner()
            mode = "poster"
            banner_session = None
            poster_session = _new_session(_clean(text), "teal", "Poster Session")
            last_session = poster_session
            poster_pending_meta = True
            skip_next_event = False
            continue

        # ---- end of technical program ----
        if re.match(r"^Conference Announcements\b", text, re.I):
            _flush_block(); _flush_chair(); _flush_banner()
            mode = "done"
            continue

        # ---- structural header (banner / poster category) ----
        # A banner has NO leading time/poster-code token. Event rows render
        # their text a hair larger (~10pt) than talk titles (9.1pt), so they'd
        # otherwise read as header-class; the time-token guard keeps them out.
        is_time_row = ln.x0 < LEFT_MARGIN_X and bool(_TIME_ROW_RE.match(text))
        is_poster_code = (mode == "poster" and ln.x0 < LEFT_MARGIN_X
                          and bool(_POSTER_CODE_RE.match(text)))
        if _is_header_class(ln) and not is_time_row and not is_poster_code:
            _flush_block(); _flush_chair()
            pending_banner.append(ln.text)
            continue
        elif pending_banner:
            _flush_banner()

        # ---- poster session date/time + subtitle (right after poster header) --
        if mode == "poster" and poster_pending_meta:
            dm = _POSTER_DATETIME_RE.match(text)
            if dm and poster_session is not None:
                dom = int(dm.group(2)); mon = MONTHS.get(dm.group(3).lower())
                if mon:
                    poster_session["start_ts"] = _iso(dom, mon, dm.group(4))
                    poster_session["end_ts"] = _iso(dom, mon, dm.group(5))
                    cur_dom, cur_month = dom, mon
                poster_pending_meta = False
                continue
            # A non-date line here is the poster section subtitle (e.g. the
            # poster category description); fold it into the session details.
            if not _POSTER_CODE_RE.match(text):
                if poster_session is not None and not poster_session["details"]:
                    poster_session["details"] = _clean(text)
                continue

        # ---- session chair (and its wrapped continuation) ----
        cm = _CHAIR_RE.match(text)
        if cm:
            _flush_block()
            collecting_chair = cm.group(1)
            continue
        if collecting_chair:
            # Continuation lines of a chair blob are plain content in the right
            # column; a time row / poster code / header ends the chair.
            if not _TIME_ROW_RE.match(text) and not (
                    mode == "poster" and _POSTER_CODE_RE.match(text)):
                collecting_chair += " " + ln.content_text
                continue
            _flush_chair()

        # ---- poster code row ----
        if mode == "poster":
            pc = _POSTER_CODE_RE.match(text)
            if pc and ln.x0 < LEFT_MARGIN_X:
                _flush_block()
                block_is_poster = True
                block_code = pc.group(1)
                rest = pc.group(2).strip()
                if rest:
                    block_lines = [_synth_content_line(ln)]
                continue

        # ---- time row (talk/event start, or bare end-time) ----
        if ln.x0 < LEFT_MARGIN_X and _TIME_ROW_RE.match(text):
            if _BARE_TIME_RE.match(text):
                # End time of the current block (e.g. "12:20 -\n14:00").
                bt = _TIME_ROW_RE.match(text).group(1)
                block_end = bt
                continue
            tm = _TIME_ROW_RE.match(text)
            _flush_block()
            block_start = tm.group(1)
            rest = tm.group(3).strip()
            if rest:
                block_lines = [_synth_content_line(ln)]
            continue

        # ---- ordinary content line: part of the current block ----
        if block_start is not None or block_code:
            # A fresh bold ALL-CAPS title arriving after the current block has
            # already collected body (author/affil) lines is a NEW talk that
            # shares the slot — e.g. a Sunday workshop listing several talks
            # under one time with no per-talk time row. Split it off.
            if (block_start is not None and not block_is_poster
                    and ln.content_bold and not ln.content_oblique
                    and _all_caps(ln.content_text)
                    and _block_has_body(block_lines)):
                carry_start = block_start
                _flush_block()
                block_start = carry_start
                block_lines = [ln]
            else:
                block_lines.append(ln)
        # else: stray line outside any block (ignored).

    _flush_block(); _flush_chair(); _flush_banner()

    # ---- backfill talk/event end times from the next item that day ----
    timed.sort(key=lambda d: (d["day"], _to_min(d["start"])))
    for i, item in enumerate(timed):
        obj = item["obj"]
        if obj.get("end_ts"):
            continue
        nxt = timed[i + 1] if i + 1 < len(timed) else None
        if nxt and nxt["day"] == item["day"]:
            mth, dom = item["day"]
            obj["end_ts"] = _iso(dom, mth, nxt["start"])
        elif obj.get("start_ts"):
            obj["end_ts"] = _bump(obj["start_ts"], 15)

    # ---- banner session times = span of their talks ----
    by_id = {t["id"]: t for t in talks}
    for s in sessions:
        if s["format"] in ("Event", "Poster Session"):
            continue
        kids = [by_id[i] for i in s["talk_ids"] if i in by_id]
        starts = [k["start_ts"] for k in kids if k.get("start_ts")]
        ends = [k["end_ts"] for k in kids if k.get("end_ts")]
        if starts:
            s["start_ts"] = min(starts)
        if ends:
            s["end_ts"] = max(ends)

    # ---- enrich special-event sessions with their descriptive blurbs + room
    #      from the Special Events section that precedes the schedule. ----
    special = _parse_special_events(lines)
    if special:
        n_enriched = 0
        for s in sessions:
            info = special.get(_se_key(s["title"]) or "")
            if not info:
                continue
            if info.get("description") and not s["details"]:
                s["details"] = info["description"]
            if info.get("location") and not s["location"]:
                s["location"] = info["location"]
            n_enriched += 1
        log(f"  special-events: {len(special)} blurbs, enriched "
            f"{n_enriched} session(s).")

    # ---- render the ALL-CAPS talk/poster titles into normal title case,
    #      restoring acronym casing learned from the conference's own
    #      mixed-case text (session titles, special-event blurbs, institution
    #      names). The session banner titles are already mixed-case, so they
    #      form the in-domain dictionary; nothing is hardcoded. ----
    corpus: list[str] = []
    for s in sessions:
        if not _all_caps(s["title"]):
            corpus.append(s["title"])
        if s.get("details"):
            corpus.append(s["details"])
    for t in talks:
        for inst in t["institutions"]:
            corpus.append(inst.get("name") or "")
    acr = learn_acronyms(corpus)
    n_recased = 0
    for t in talks:
        if _all_caps(t["title"]):
            t["title"] = _smart_title(t["title"], acr)
            n_recased += 1
    log(f"  title-case: learned {len(acr)} acronym(s); recased "
        f"{n_recased} title(s).")

    # Drop sessions that ended up empty and undated (e.g. a skipped pointer).
    sessions = [s for s in sessions
                if s["talk_ids"] or s["start_ts"] or s["format"] == "Event"]

    data = {
        "conference_name": CONFERENCE_NAME,
        "sessions": sorted(sessions, key=lambda s: (s["start_ts"] or "")),
        "talks": sorted(talks, key=lambda t: (t["start_ts"] or "")),
        "session_types": SESSION_TYPE_REGISTRY,
        "talk_types": TALK_TYPE_REGISTRY,
        "affiliation_sources": sorted(aff_pool),
    }
    if CURATOR and (CURATOR.get("name") or "").strip():
        data["curator"] = {
            "name": CURATOR["name"].strip(),
            "affiliation": (CURATOR.get("affiliation") or "").strip(),
            "link": (CURATOR.get("link") or "").strip(),
        }
    return data


def _synth_content_line(ln: Line) -> Line:
    """A time/poster-code row carries its title in the content column; clone the
    line keeping only the content-column chars so it parses like a title line."""
    cc = [c for c in ln.chars if c["x0"] >= CONTENT_X]
    return Line(ln.page, cc or ln.chars)


def _to_min(hhmm: str) -> int:
    try:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return 0


def _bump(iso_ts: str, minutes: int) -> str:
    import datetime as _dt
    dt = _dt.datetime.fromisoformat(iso_ts) + _dt.timedelta(minutes=minutes)
    return dt.isoformat()


# =============================================================================
# Session tags (Format / Track) for the detail header.
# =============================================================================
def _collapse_session_tags(sessions: list[dict]) -> None:
    for s in sessions:
        fmt = (s.pop("format", None) or "").strip()
        topic = (s.pop("topic", None) or "").strip()
        tags = []
        if fmt:
            tags.append({"key": "Format", "value": fmt})
        tl = topic.casefold()
        title_l = str(s.get("title", "")).casefold()
        redundant = (not topic or tl in title_l or title_l.endswith(tl)
                     or tl == fmt.casefold())
        if not redundant:
            tags.append({"key": "Track", "value": topic})
        if tags:
            s["tags"] = tags


def main() -> None:
    log("=" * 72)
    log("[config] HILTON HEAD 2026 PROCESSOR starting up.")
    log(f"[config]   data dir  : {DATA_DIR}")
    log(f"[config]   input PDF : {INPUT_PDF}")
    log(f"[config]   JSON out  : {OUTPUT_JSON}")
    log("=" * 72)

    if not INPUT_PDF.exists():
        raise SystemExit(
            f"[fatal] Input PDF not found: {INPUT_PDF}\n"
            f"        Run fetch_program_hh2026.py first (or via make_app.py).")

    _bootstrap_pdfplumber()

    log("[1/2] Parsing the program PDF…")
    data = build_conference_data()

    log("[2/2] Writing conference_data.json…")
    _collapse_session_tags(data["sessions"])
    OUTPUT_JSON.write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    n_s = len(data["sessions"])
    n_t = len(data["talks"])
    n_posters = sum(1 for t in data["talks"] if t["color"] == "teal")
    n_auth = sum(len(t["authors"]) for t in data["talks"])
    log(f"[done] wrote {OUTPUT_JSON.name}: {n_s} sessions, {n_t} talks "
        f"({n_posters} posters), {n_auth} author entries, "
        f"{len(data['affiliation_sources'])} affiliation strings.")


if __name__ == "__main__":
    main()
