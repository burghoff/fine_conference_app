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

"""
process_program_irmmwthz2026.py — PROCESS ONLY.

The "processor" half of the pipeline. It reads the HTML pages the downloader
saved into data/ (no network, no browser) and emits the conference_data.json
that the shared builder consumes.

Source of record — data/overview_by_days_and_halls.html — is the conference's
"overview by days and halls" page. Its content area holds one tab per weekday;
inside a tab the markup repeats a small set of shapes (all format, no content):

  - A time-slot header:  a bare line "<H:MM AM>–<H:MM PM>" that opens a slot.
  - A session box:       a bordered card whose header carries the hall name,
                         the session line "<N.M> – <SESSION TITLE>", and a
                         "Session Chair: <Name>" line (the plenary card instead
                         reads "Plenary Session" / "<title>"), followed by one
                         flex row per talk: a left cell "<Kind>" + "<start>–<end>"
                         (Kind is Keynote / Oral / Plenary) and a right cell with
                         the talk title and a "<Speaker>, <Affiliation>" line.
  - A poster block:      an <h4> "<Weekday> Poster Session <n> (<HH:MM>–<HH:MM>)
                         – <TOPIC>" followed by one entry per board: a "Board
                         <n>" label, the title, and the "<Presenter>,
                         <Affiliation>" line.

Optional enrichment pages (each simply skipped when absent from data/):

  - social_program.html     — paragraphs of the shape "<Event> will take place
                              on <Weekday>, <D>th of <Month> at <time> …", each
                              followed by a "Location: <venue>" list item and an
                              address line.  -> standalone Event sessions.
  - student_workshop.html   — "<Month> <D>" heading, a venue line, then one
                              paragraph per hour: time range / speaker /
                              affiliation / title (a lunch paragraph carries a
                              time range and a label only). -> one Tutorial
                              session with Tutorial talks (lunch = Event row).
  - reviewers_reception.html — a card of "<label> / <value>" rows (Date, Time,
                              Location, Admission) plus a notice. -> one Event
                              session.

Type mapping onto the standard taxonomy: the per-hall oral sessions are
Technical; their "Keynote" rows (the 30-minute solicited opener of each
session) are Invited and the "Oral" rows are Contributed; the morning plenary
card is a Plenary session with Plenary talks; poster sessions/boards are
Poster; the student workshop is Tutorial; social events are Event.

Nothing from the program (titles, names, affiliations, session titles, halls)
is hardcoded here; it is all extracted from the saved pages at runtime.

Output (next to this script):
    conference_data.json
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path


def log(msg: str) -> None:
    print(msg, flush=True)


# -----------------------------------------------------------------------------
# Configuration. Inputs live under data/; the JSON output stays in the script
# directory (where the downstream builder expects it).
# -----------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "data"
OVERVIEW_HTML = DATA_DIR / "overview_by_days_and_halls.html"
SOCIAL_HTML = DATA_DIR / "social_program.html"
WORKSHOP_HTML = DATA_DIR / "student_workshop.html"
RECEPTION_HTML = DATA_DIR / "reviewers_reception.html"
OUTPUT_JSON = SCRIPT_DIR / "conference_data.json"

# Display name shown as the page title and the "<name> Sessions/Talks"
# headings. The program page's own <title> carries no year, so it is set here
# (the one acceptable place for it per the curation guide).
CONFERENCE_NAME = "IRMMW-THz 2026"

# The program page labels its tabs only by weekday ("Monday" … "Friday"), so a
# calendar anchor is needed to turn them into dates: the date of the FIRST tab
# (the conference Monday). The social-program page names weekdays together with
# their day-of-month, so when it is present the anchor is DERIVED from it and
# this constant is only cross-checked; without that page it is the fallback.
YEAR = 2026
FIRST_DAY_FALLBACK = date(2026, 10, 12)

# Optional curator credit (schema: {name, affiliation?, link?}). None omits the
# line entirely.
CURATOR = None

# Only the types this program actually uses are registered; the app hides any
# type with no items. RGB triples are the standard taxonomy tokens.
SESSION_TYPES = [
    {"id": "blue", "label": "Technical",
     "fg": "#2563eb", "bg_light": "#e8efff", "bg_dark": "#1a233d"},
    {"id": "orange", "label": "Plenary",
     "fg": "#ea580c", "bg_light": "#ffedd5", "bg_dark": "#3b1d0a"},
    {"id": "teal", "label": "Poster",
     "fg": "#0d9488", "bg_light": "#d6f3ef", "bg_dark": "#102b27"},
    {"id": "fuchsia", "label": "Tutorial",
     "fg": "#c026d3", "bg_light": "#fae8ff", "bg_dark": "#3a0f3f"},
    {"id": "rose", "label": "Event",
     "fg": "#e11d48", "bg_light": "#ffe1e8", "bg_dark": "#38161f"},
]
TALK_TYPES = [
    {"id": "indigo", "label": "Invited",
     "fg": "#4f46e5", "bg_light": "#e6e4ff", "bg_dark": "#1d1a3d"},
    {"id": "sky", "label": "Contributed",
     "fg": "#0284c7", "bg_light": "#e0f2fe", "bg_dark": "#0c2a3d"},
    {"id": "orange", "label": "Plenary",
     "fg": "#ea580c", "bg_light": "#ffedd5", "bg_dark": "#3b1d0a"},
    {"id": "teal", "label": "Poster",
     "fg": "#0d9488", "bg_light": "#d6f3ef", "bg_dark": "#102b27"},
    {"id": "fuchsia", "label": "Tutorial",
     "fg": "#c026d3", "bg_light": "#fae8ff", "bg_dark": "#3a0f3f"},
    {"id": "rose", "label": "Event",
     "fg": "#e11d48", "bg_light": "#ffe1e8", "bg_dark": "#38161f"},
]

# The program's row kinds -> talk color tokens.
KIND_COLOR = {"keynote": "indigo", "oral": "sky", "plenary": "orange"}

DATA_DIR.mkdir(parents=True, exist_ok=True)


def _bootstrap_lxml() -> None:
    try:
        import lxml  # noqa: F401
    except ImportError:
        log("[setup] Installing lxml…")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--quiet", "lxml>=4.9"])


# =============================================================================
# Text grammar (format only — no program content).
# =============================================================================
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
            "Saturday", "Sunday"]
_WD = "|".join(WEEKDAYS)
_T12 = r"\d{1,2}:\d{2}\s*[AaPp]\.?[Mm]\.?"       # 9:00 AM
_T24 = r"\d{1,2}:\d{2}"                           # 17:30
_DASH = r"\s*[–—\-]\s*"
# "9:00 AM–10:30 AM" (a slot header, or a row's own time range).
RANGE12_RE = re.compile(r"(%s)%s(%s)" % (_T12, _DASH, _T12))
# Row left cell: "<Kind>" optionally glued to its time range ("Oral2:00 PM–…").
KIND_RE = re.compile(r"^([A-Za-z]+)\s*(?:(%s)%s(%s))?$" % (_T12, _DASH, _T12))
# Session line "<N.M> – <title>" (the code may also be absent).
SESSION_LINE_RE = re.compile(r"^(\d+\.\d+)%s(.+)$" % _DASH)
CHAIR_RE = re.compile(r"^Session\s+Chairs?\s*:\s*(.+)$", re.IGNORECASE)
# Poster heading: "<Weekday> Poster Session <n> (<HH:MM>–<HH:MM>) – <topic>".
POSTER_HEAD_RE = re.compile(
    r"^(?:(%s)\s+)?Poster\s+Session\s*(\d+)\s*\(\s*(%s)%s(%s)\s*\)\s*(?:%s(.+))?$"
    % (_WD, _T24, _DASH, _T24, _DASH), re.IGNORECASE)
BOARD_RE = re.compile(r"^Board\s*(\d+)\s*$", re.IGNORECASE)
# "<Weekday>, 11th of October" / "October 15" style dates on the event pages.
DAY_OF_MONTH_RE = re.compile(
    r"(?:(%s),?\s+)?(\d{1,2})(?:st|nd|rd|th)?\s+of\s+([A-Z][a-z]+)" % _WD)
MONTH_DAY_RE = re.compile(r"\b([A-Z][a-z]+)\s+(\d{1,2})\b")
AT_TIME_RE = re.compile(r"\bat\s+(%s)" % _T12)
_UNI_HYPHENS = {"‐": "-", "‑": "-", "‒": "-"}


def norm_space(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def clean_text(s: str) -> str:
    for bad, good in _UNI_HYPHENS.items():
        s = s.replace(bad, good)
    return norm_space(s)


def parse_time12(t: str) -> datetime:
    """'9:00 AM' / '2:30pm' -> datetime carrying the time of day."""
    t = re.sub(r"[.\s]", "", t).upper()
    return datetime.strptime(t, "%I:%M%p")


def parse_time24(t: str) -> datetime:
    return datetime.strptime(t.strip(), "%H:%M")


def iso(d: date, t: datetime) -> str:
    return f"{d.isoformat()}T{t.strftime('%H:%M')}:00"


def parse_month(name: str) -> int | None:
    for fmt in ("%B", "%b"):
        try:
            return datetime.strptime(name, fmt).month
        except ValueError:
            pass
    return None


def split_speaker(line: str) -> tuple[str, str]:
    """'<Name>, <Affiliation…>' -> (name, affiliation). The affiliation may
    itself contain commas, so only the FIRST comma splits."""
    line = clean_text(line)
    if ", " in line:
        name, aff = line.split(", ", 1)
        return name.strip(), aff.strip()
    return line, ""


def leaf_texts(el) -> list[str]:
    """Texts of the element's leaf descendants (elements with no element
    children), in document order, blanks dropped. Robust to the stray wrapper
    <div>s the CMS editor sometimes nests around a line."""
    out = []
    for e in el.iter():
        if not isinstance(e.tag, str):
            continue
        if any(isinstance(k.tag, str) for k in e):
            continue
        t = norm_space(e.text_content())
        if t:
            out.append(t)
    return out


def _children(el):
    return [c for c in el if isinstance(c.tag, str)]


def _read_main(path: Path):
    from lxml import html
    # Parse as UTF-8 explicitly: the saved pages declare it, but letting the
    # parser sniff can mis-decode curly quotes and accented names.
    doc = html.parse(str(path), parser=html.HTMLParser(encoding="utf-8"))
    mains = doc.xpath("//main")
    root = mains[0] if mains else doc.getroot()
    for bad in root.xpath(".//script|.//style|.//noscript"):
        bad.getparent().remove(bad)
    # A <br> is a line break; make sure text on either side of it never runs
    # together when a block's text is flattened.
    for br in root.iter("br"):
        br.tail = " " + (br.tail or "")
    return root


# =============================================================================
# Overview page -> sessions + talks.
# =============================================================================
def parse_overview(first_day: date, sessions: list, talks: list,
                   aff_pool: set) -> None:
    root = _read_main(OVERVIEW_HTML)
    tabs = root.xpath('.//div[contains(@class,"elementor-tab-content")]')
    if not tabs:
        log("[fatal] no weekday tabs found in the overview page.")
        sys.exit(1)

    seen_ids: set[str] = set()

    def unique_id(base: str) -> str:
        sid, i = base, 2
        while sid in seen_ids:
            sid = f"{base}-{i}"
            i += 1
        seen_ids.add(sid)
        return sid

    talk_n = 0

    def add_talk(sess: dict, title: str, speaker_line: str, color: str,
                 start: str | None, end: str | None, code: str) -> None:
        nonlocal talk_n
        talk_n += 1
        tid = f"T-{talk_n}"
        name, aff = split_speaker(speaker_line)
        talk = {
            "id": tid,
            "session_id": sess["id"],
            "code": code,
            "title": clean_text(title),
            "color": color,
            "start_ts": start,
            "end_ts": end,
            "speaker": name,
            "speaker_pos": 0,
            "first_author": name,
            "last_author": name,
            "authors": [{"name": name, "insts": [1] if aff else []}],
        }
        if aff:
            talk["institutions"] = [{"n": 1, "name": aff}]
            aff_pool.add(aff)
        talks.append(talk)
        sess["talk_ids"].append(tid)

    for day_index, tab in enumerate(tabs):
        h2 = tab.xpath(".//h2")
        day_name = norm_space(h2[0].text_content()) if h2 else ""
        if day_name not in WEEKDAYS:
            # Fall back to the tab's position: tab 0 is the anchor day.
            wd = (first_day.weekday() + day_index) % 7
            day_name = WEEKDAYS[wd]
        day = first_day + timedelta(
            days=(WEEKDAYS.index(day_name) - first_day.weekday()) % 7)
        day_num = day_index + 1
        log(f"[overview] tab {day_index}: {day_name} -> {day.isoformat()}")

        slot = None            # (start, end) datetimes of the open time slot
        poster_sess = None     # the poster session the next boards join
        n_box = n_board = 0

        # Walk the tab in document order. Boxes are handled as a unit (their
        # inner rows are consumed here), so remember what to skip.
        skip_under = None
        for el in tab.iter():
            if not isinstance(el.tag, str):
                continue
            if skip_under is not None:
                if skip_under in el.iterancestors() or el is skip_under:
                    continue
                skip_under = None

            kids = _children(el)
            own = norm_space(el.text_content()) if el.tag == "div" and not kids else ""

            # --- time-slot header -----------------------------------------
            if own and RANGE12_RE.fullmatch(own):
                m = RANGE12_RE.fullmatch(own)
                slot = (parse_time12(m.group(1)), parse_time12(m.group(2)))
                continue

            # --- session box: a div whose direct children are a header div
            #     followed by flex rows ------------------------------------
            if el.tag == "div" and kids and any(
                    "display: flex" in (k.get("style") or "") for k in kids):
                header = kids[0]
                rows = [k for k in kids[1:]
                        if "display: flex" in (k.get("style") or "")]
                if "display: flex" in (header.get("style") or ""):
                    # This is a row container with no header — not a box.
                    continue
                skip_under = el
                n_box += 1
                head = leaf_texts(header)
                hall, title_line, chair = "", "", ""
                for line in head:
                    cm = CHAIR_RE.match(line)
                    if cm:
                        chair = cm.group(1).strip()
                    elif not hall:
                        hall = line
                    elif not title_line:
                        title_line = line
                    else:
                        title_line = f"{title_line} {line}"
                # The chair line can be glued onto the title line.
                if not chair and "Session Chair" in title_line:
                    title_line, _, rest = title_line.partition("Session Chair")
                    chair = rest.lstrip(": ").strip()
                    title_line = title_line.strip()

                is_plenary = hall.lower().startswith("plenary")
                sm = SESSION_LINE_RE.match(title_line)
                if sm:
                    code, title = sm.group(1), sm.group(2)
                else:
                    code = f"{day_num}.PL" if is_plenary else ""
                    title = title_line or hall
                sess = {
                    "id": unique_id("S-" + (code or f"{day_num}-{n_box}")),
                    "code": code,
                    "title": clean_text(title),
                    "color": "orange" if is_plenary else "blue",
                    "start_ts": iso(day, slot[0]) if slot else None,
                    "end_ts": iso(day, slot[1]) if slot else None,
                    "talk_ids": [],
                }
                if not is_plenary and hall:
                    sess["location"] = hall
                if chair:
                    sess["presider"] = chair
                sessions.append(sess)

                for r_i, row in enumerate(rows, 1):
                    cells = _children(row)
                    if len(cells) < 2:
                        continue
                    left = norm_space(cells[0].text_content())
                    km = KIND_RE.match(left)
                    kind = (km.group(1) if km else left).lower()
                    start = end = None
                    if km and km.group(2):
                        start = iso(day, parse_time12(km.group(2)))
                        end = iso(day, parse_time12(km.group(3)))
                    right = leaf_texts(cells[1])
                    if not right:
                        continue
                    title = right[0]
                    speaker_line = right[1] if len(right) > 1 else ""
                    color = KIND_COLOR.get(kind)
                    if color is None:
                        log(f"[overview] WARNING: unknown row kind {kind!r} "
                            f"in session {code or title!r}; typing it "
                            "Contributed.")
                        color = "sky"
                    add_talk(sess, title, speaker_line, color, start, end,
                             f"{code}.{r_i}" if code else "")
                continue

            # --- poster session heading -----------------------------------
            if el.tag == "h4":
                text = norm_space(el.text_content())
                pm = POSTER_HEAD_RE.match(text)
                if not pm:
                    log(f"[overview] WARNING: unrecognised h4 {text!r}")
                    continue
                wd_name, num, t0, t1, topic = pm.groups()
                pday = day
                if wd_name and wd_name.capitalize() in WEEKDAYS:
                    pday = first_day + timedelta(
                        days=(WEEKDAYS.index(wd_name.capitalize())
                              - first_day.weekday()) % 7)
                code = f"{day_num}.P{num}"
                poster_sess = {
                    "id": unique_id("S-" + code),
                    "code": code,
                    "title": clean_text(topic or f"Poster Session {num}"),
                    "color": "teal",
                    "start_ts": iso(pday, parse_time24(t0)),
                    "end_ts": iso(pday, parse_time24(t1)),
                    "tags": [{"key": "Format", "value": f"Poster Session {num}"}],
                    "talk_ids": [],
                }
                sessions.append(poster_sess)
                continue

            # --- poster board entry ---------------------------------------
            if el.tag == "strong":
                bm = BOARD_RE.match(norm_space(el.text_content()))
                if not bm:
                    continue
                if poster_sess is None:
                    log("[overview] WARNING: a board entry precedes any "
                        "poster heading; skipped.")
                    continue
                board = int(bm.group(1))
                # The entry block is the board label's enclosing block
                # (<p><strong/></p> sits inside it); its remaining leaf
                # texts are the title and the presenter line.
                entry = el.getparent()
                if entry is not None and entry.tag == "p":
                    entry = entry.getparent()
                if entry is None:
                    continue
                texts = leaf_texts(entry)
                texts = [t for t in texts if not BOARD_RE.match(t)]
                if not texts:
                    continue
                title = texts[0]
                speaker_line = texts[1] if len(texts) > 1 else ""
                n_board += 1
                add_talk(poster_sess, title, speaker_line, "teal",
                         poster_sess["start_ts"], poster_sess["end_ts"],
                         f"{poster_sess['code']}.{board}")
                continue

        log(f"[overview]   {n_box} session boxes, {n_board} poster boards")


# =============================================================================
# Optional pages.
# =============================================================================
def derive_first_day() -> date:
    """Anchor the overview's weekday tabs to real dates. The social-program
    page names its events as "<Weekday>, <D>th of <Month>", and any one such
    pair that falls on a Monday–Friday pins the conference week: the Monday of
    that week is the first tab's date. (Weekend mentions are skipped — a Sunday
    eve-of-conference reception belongs to the FOLLOWING week.) Falls back to
    FIRST_DAY_FALLBACK when the page is absent or carries no usable pair, and
    warns if the two disagree."""
    derived = None
    if SOCIAL_HTML.exists():
        try:
            text = norm_space(_read_main(SOCIAL_HTML).text_content())
            for m in DAY_OF_MONTH_RE.finditer(text):
                wd, dom, mon = m.groups()
                month = parse_month(mon)
                if not wd or month is None:
                    continue
                d = date(YEAR, month, int(dom))
                if WEEKDAYS[d.weekday()] != wd:
                    log(f"[dates] WARNING: {wd} {dom} {mon} {YEAR} is not a "
                        f"{wd}; ignoring this mention.")
                    continue
                if d.weekday() >= 5:
                    continue  # weekend: ambiguous which week it belongs to
                derived = d - timedelta(days=d.weekday())  # that week's Monday
                break
        except Exception as e:  # malformed page — fall back quietly
            log(f"[dates] note: could not derive the week from the social "
                f"page ({e}).")
    if derived is None:
        log(f"[dates] first program day: {FIRST_DAY_FALLBACK} (fallback "
            "constant).")
        return FIRST_DAY_FALLBACK
    if derived != FIRST_DAY_FALLBACK:
        log(f"[dates] WARNING: derived first day {derived} differs from "
            f"FIRST_DAY_FALLBACK {FIRST_DAY_FALLBACK}; using the derived one.")
    else:
        log(f"[dates] first program day: {derived} (derived from the social "
            "program page).")
    return derived


def _short_venue(name: str) -> str:
    """'Long Venue Name (ACR)' -> 'ACR'; otherwise the name itself."""
    m = re.search(r"\(([^)]+)\)\s*$", name)
    return m.group(1).strip() if m else name


def parse_social(sessions: list) -> None:
    """Each event is a paragraph with a bold lead (the event name) and a
    sentence giving weekday, day-of-month, month and start time, followed (in
    document order, though in separate page widgets) by icon-list items:
    "Location: <venue>" and a street address. Walk paragraphs and list items
    in document order; list items attach to the most recent event."""
    if not SOCIAL_HTML.exists():
        log("[social] social_program.html absent — no social events.")
        return
    root = _read_main(SOCIAL_HTML)
    events: list[dict] = []
    for el in root.iter("p", "li"):
        text = norm_space(el.text_content())
        if el.tag == "li":
            if not events:
                continue
            ev = events[-1]
            lm = re.match(r"^Location\s*:\s*(.+)$", text, re.IGNORECASE)
            if lm:
                ev["venue"] = lm.group(1).strip()
            elif text and not ev["address"] and re.search(r"\d", text):
                ev["address"] = text
            continue

        dm = DAY_OF_MONTH_RE.search(text)
        tm = AT_TIME_RE.search(text)
        if not dm or not tm:
            continue
        _wd, dom, mon = dm.groups()
        month = parse_month(mon)
        if month is None:
            continue
        day = date(YEAR, month, int(dom))
        lead = el.xpath("./b|./strong")
        title = norm_space(lead[0].text_content()) if lead else ""
        if not title:
            title = re.split(r"\bwill\b|\bon\b", text, 1)[0]
        title = re.sub(r"^(?:The)\s+", "", title.strip(" ,.")).strip()
        # Substantive remarks are the parenthetical asides; the logistics
        # sentence itself is already captured in the structured fields.
        notes = [norm_space(x) for x in re.findall(r"\(([^)]+)\)", text)]
        events.append({
            "title": title, "day": day, "time": tm.group(1),
            "notes": [x for x in notes if x], "venue": "", "address": "",
        })

    for n, ev in enumerate(events, 1):
        sess = {
            "id": f"S-social-{n}",
            "code": "",
            "title": clean_text(ev["title"]),
            "color": "rose",
            "start_ts": iso(ev["day"], parse_time12(ev["time"])),
            "talk_ids": [],
        }
        venue, address = ev["venue"], ev["address"]
        if venue:
            sess["location"] = f"{venue}, {address}" if address else venue
            sess["short_location"] = _short_venue(venue)
        elif address:
            sess["location"] = address
        if ev["notes"]:
            sess["details"] = " ".join(x.rstrip(".") + "." for x in ev["notes"])
        sessions.append(sess)
        log(f"[social]   {sess['title']!r} on {ev['day']} at "
            f"{sess['start_ts'][11:16]} @ {sess.get('short_location', '')!r}")
    log(f"[social] {len(events)} social events.")


def parse_workshop(sessions: list, talks: list, aff_pool: set) -> None:
    """The workshop page: an <h2> "<Month> <D>" date, an <h5> venue line, then
    one <p> per hour whose <br>-separated lines are: time range, speaker,
    affiliation, title (a break paragraph has just a time range and a label)."""
    if not WORKSHOP_HTML.exists():
        log("[workshop] student_workshop.html absent — no workshop.")
        return
    root = _read_main(WORKSHOP_HTML)
    h1 = root.xpath(".//h1")
    title = norm_space(h1[0].text_content()) if h1 else "Workshop"

    day = None
    for h in root.xpath(".//h2|.//h3"):
        mm = MONTH_DAY_RE.search(norm_space(h.text_content()))
        if mm and parse_month(mm.group(1)):
            day = date(YEAR, parse_month(mm.group(1)), int(mm.group(2)))
            break
    if day is None:
        log("[workshop] WARNING: no date heading found; skipping the workshop.")
        return
    venue = ""
    for h in root.xpath(".//h5|.//h4"):
        t = norm_space(h.text_content())
        if t:
            venue = t
            break

    sess = {
        "id": "S-workshop",
        "code": "SW",
        "title": clean_text(title),
        "color": "fuchsia",
        "start_ts": None,
        "end_ts": None,
        "talk_ids": [],
    }
    if venue:
        sess["location"] = venue
        sess["short_location"] = venue.split(",")[0].strip()
    sessions.append(sess)

    starts, ends = [], []
    n = 0
    for p in root.xpath(".//p"):
        lines = leaf_texts(p)
        if not lines:
            continue
        rm = RANGE12_RE.fullmatch(lines[0])
        if not rm:
            continue
        start = iso(day, parse_time12(rm.group(1)))
        end = iso(day, parse_time12(rm.group(2)))
        starts.append(start)
        ends.append(end)
        n += 1
        tid = f"T-ws-{n}"
        body = lines[1:]
        if len(body) >= 3:
            speaker, aff, ttl = body[0], body[1], " ".join(body[2:])
            talk = {
                "id": tid, "session_id": sess["id"], "code": f"SW.{n}",
                "title": clean_text(ttl), "color": "fuchsia",
                "start_ts": start, "end_ts": end,
                "speaker": speaker, "speaker_pos": 0,
                "first_author": speaker, "last_author": speaker,
                "authors": [{"name": speaker, "insts": [1]}],
                "institutions": [{"n": 1, "name": aff}],
            }
            aff_pool.add(aff)
        else:
            # A break/meal row: label only (no speaker, no technical content).
            talk = {
                "id": tid, "session_id": sess["id"], "code": f"SW.{n}",
                "title": clean_text(" ".join(body)), "color": "rose",
                "start_ts": start, "end_ts": end,
            }
        talks.append(talk)
        sess["talk_ids"].append(tid)
    if starts:
        sess["start_ts"] = min(starts)
        sess["end_ts"] = max(ends)
    log(f"[workshop] {n} rows on {day} at {venue!r}.")


def parse_reception(sessions: list) -> None:
    """The reception card: a heading plus "<label> / <value>" flex rows (Date,
    Time, Location, Admission) and a highlighted notice paragraph."""
    if not RECEPTION_HTML.exists():
        log("[reception] reviewers_reception.html absent — not included.")
        return
    root = _read_main(RECEPTION_HTML)
    heads = root.xpath(".//h2") or root.xpath(".//h1")
    title = norm_space(heads[0].text_content()) if heads else "Reception"

    fields: dict[str, str] = {}
    for row in root.xpath('.//div[contains(@style,"display: flex")]'):
        cells = _children(row)
        if len(cells) < 2:
            continue
        label = re.sub(r"[^A-Za-z]+", " ", cells[0].text_content()).strip().lower()
        fields[label] = norm_space(cells[1].text_content())
    dm = MONTH_DAY_RE.search(fields.get("date", ""))
    if not dm or not parse_month(dm.group(1)):
        log("[reception] WARNING: no usable date on the reception page; "
            "skipped.")
        return
    day = date(YEAR, parse_month(dm.group(1)), int(dm.group(2)))
    sess = {
        "id": "S-reception",
        "code": "",
        "title": clean_text(title),
        "color": "rose",
        "talk_ids": [],
    }
    tm = RANGE12_RE.search(fields.get("time", ""))
    if tm:
        sess["start_ts"] = iso(day, parse_time12(tm.group(1)))
        sess["end_ts"] = iso(day, parse_time12(tm.group(2)))
    else:
        sess["start_ts"] = day.isoformat() + "T00:00:00"
    if fields.get("location"):
        sess["location"] = fields["location"]
    tags = [{"key": k.capitalize(), "value": v} for k, v in fields.items()
            if k not in ("date", "time", "location") and v]
    if tags:
        sess["tags"] = tags
    # The notice block: a <strong> lead ("Important:") plus its lines.
    for strong in root.xpath(".//strong"):
        block = strong.getparent()
        if block is None:
            continue
        note = norm_space(block.text_content())
        note = re.sub(r"^\s*\w+\s*:\s*", "", note)  # drop the "Important:" lead
        if note:
            sess["details"] = note
            break
    sessions.append(sess)
    log(f"[reception] {sess['title']!r} on {day}.")


# =============================================================================
# Main.
# =============================================================================
def main() -> None:
    log("=" * 72)
    log("[config] PROCESSOR starting up.")
    log(f"[config]   overview : {OVERVIEW_HTML}")
    log(f"[config]   output   : {OUTPUT_JSON}")
    log("=" * 72)

    _bootstrap_lxml()

    if not OVERVIEW_HTML.exists():
        log(f"[fatal] Input not found: {OVERVIEW_HTML}")
        log("[fatal] Run the downloader first (fetch_program_irmmwthz2026.py).")
        sys.exit(1)

    sessions: list[dict] = []
    talks: list[dict] = []
    aff_pool: set[str] = set()

    first_day = derive_first_day()
    parse_overview(first_day, sessions, talks, aff_pool)
    parse_workshop(sessions, talks, aff_pool)
    parse_social(sessions)
    parse_reception(sessions)

    # Drop scratch/None fields the schema treats as optional.
    for item in sessions + talks:
        for k in [k for k, v in item.items() if v is None]:
            del item[k]

    data = {
        "conference_name": CONFERENCE_NAME,
        "sessions": sessions,
        "talks": talks,
        "session_types": SESSION_TYPES,
        "talk_types": TALK_TYPES,
    }
    if CURATOR and CURATOR.get("name"):
        cur = {"name": CURATOR["name"]}
        if CURATOR.get("affiliation"):
            cur["affiliation"] = CURATOR["affiliation"]
        if CURATOR.get("link"):
            cur["link"] = CURATOR["link"]
        data["curator"] = cur
    if aff_pool:
        data["affiliation_sources"] = sorted(aff_pool)

    OUTPUT_JSON.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                           encoding="utf-8")
    by_color: dict[str, int] = {}
    for s in sessions:
        by_color[s["color"]] = by_color.get(s["color"], 0) + 1
    tcount: dict[str, int] = {}
    for t in talks:
        tcount[t["color"]] = tcount.get(t["color"], 0) + 1
    log(f"[ok] wrote {OUTPUT_JSON.name}: {len(sessions)} sessions {by_color}, "
        f"{len(talks)} talks {tcount}.")
    log("=" * 72)
    log("DONE.")
    log("=" * 72)


if __name__ == "__main__":
    main()
