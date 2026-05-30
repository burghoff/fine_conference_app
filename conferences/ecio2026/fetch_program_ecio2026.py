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

"""fetch_program_ecio2026.py — DOWNLOAD ONLY.

The "downloader" half of the ECIO 2026 pipeline. The full ECIO 2026 program is
published as two PDFs linked from the public programme page,

    https://www.ecio-conference.org/programme-26/

There is no planner / Excel / abstract-book export. The two PDFs are:

    ECIO26_DetailedSchedule.pdf   the wide A3 grid: every session, every time
                                  slot, every talk title + speaker.
    ECIO26_Concise.pdf            one-page program overview (session blocks
                                  only, no per-talk detail).

We also save a third artifact, the invited-speakers HTML page,

    ECIO26_InvitedSpeakers.html   the public list of invited speakers laid out
                                  as (Name, Affiliation, Talk Title) triples.

The detailed schedule PDF prints only the speaker's name in each cell, with no
affiliation; the invited-speakers page is the one public ECIO source that
attaches an affiliation to those speaker names, so we cache it here for the
processor to cross-reference when filling talk institutions.

The PDF filenames on the website carry the re-issue date in their suffix
(e.g. ECIO26_DetailedProgramSchedule_21_5.pdf), which changes whenever the
organisers publish a refresh, so we do NOT hard-code those URLs. Instead we
scrape the programme page itself and pick the most recent matching PDF link
by the date encoded in its filename. This way the fetcher keeps working when
ECIO publishes an updated version of either PDF. The invited-speakers page,
in contrast, lives at a fixed URL and is fetched directly.

Finally, we render and save the three per-day Optica schedule pages

    ECIO26_OpticaMonday.html / …Tuesday.html / …Wednesday.html

from Optica's event site. These mirror the full program and, unlike the PDFs,
carry the COMPLETE author list (with affiliations) and the abstract for every
talk. That schedule is a JavaScript single-page app behind bot protection, so
this part drives a real headed Chromium via Playwright (switching to "Detailed
View" and expanding every "Continue Reading" link before saving the rendered
HTML) rather than a plain HTTP fetch. See `_fetch_optica_schedule`.

Contacts the network via urllib for the PDFs/HTML pages and via a headed
Chromium for the Optica schedule. The processor (process_program_ecio2026.py)
runs entirely offline against what we save here.
"""

from __future__ import annotations

import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "data"

PROGRAMME_URL = "https://www.ecio-conference.org/programme-26/"

# Each artifact saved into data/ is one of:
#   - "pattern" artifact: discovered on PROGRAMME_URL by regex, then downloaded.
#     The pattern matches the rolling re-issue filenames the organisers publish.
#   - "url" artifact: lives at a fixed URL on the conference site and is fetched
#     directly. Used for HTML pages that don't carry rolling date suffixes.
# All entries share the same "name" (saved filename) and "desc" (log label).
ARTIFACTS = [
    {
        "name": "ECIO26_DetailedSchedule.pdf",
        # Detailed schedule: ECIO26_DetailedProgramSchedule_<dd>_<m>.pdf
        "pattern": re.compile(
            r"https?://[^\"'>\s]+/ECIO26_DetailedProgramSchedule[^\"'>\s]*\.pdf",
            re.IGNORECASE,
        ),
        "desc": "detailed program schedule",
    },
    {
        "name": "ECIO26_Concise.pdf",
        # Concise overview: ECIO_FinalProgram_Concise_<dd>_<mm>.pdf
        "pattern": re.compile(
            r"https?://[^\"'>\s]+/ECIO_FinalProgram_Concise[^\"'>\s]*\.pdf",
            re.IGNORECASE,
        ),
        "desc": "concise program overview",
    },
    {
        "name": "ECIO26_InvitedSpeakers.html",
        # Fixed URL — the invited-speakers page is the only public ECIO source
        # that ties each invited speaker's name to an affiliation.
        "url": "https://www.ecio-conference.org/invited-speakers-2/",
        "desc": "invited speakers page",
    },
    # The six pages below are *enrichment* sources. The detailed-schedule PDF
    # already supplies enough information to build a usable program (session
    # times, rooms, talk titles + presenters); each of these adds detail the
    # PDF doesn't render — plenary abstracts and bios, workshop chairs, student-
    # event panellists, cleaner industry-talk metadata (company, talk title,
    # speaker as separate fields), and short descriptions for social and lab-
    # tour events. The processor cross-references them by speaker name or
    # session id and falls back gracefully when any one is missing.
    {
        "name": "ECIO26_PlenarySpeakers.html",
        "url": "https://www.ecio-conference.org/plenary-speakers/",
        "desc": "plenary speakers page",
    },
    {
        "name": "ECIO26_Workshops.html",
        "url": "https://www.ecio-conference.org/workshops/",
        "desc": "workshops page",
    },
    {
        "name": "ECIO26_StudentEvent.html",
        "url": "https://www.ecio-conference.org/sunday-student-event/",
        "desc": "Sunday student-event page",
    },
    {
        "name": "ECIO26_IndustryTalks.html",
        "url": "https://www.ecio-conference.org/industry-talks/",
        "desc": "industry talks page",
    },
    {
        "name": "ECIO26_SocialEvents.html",
        "url": "https://www.ecio-conference.org/social-events/",
        "desc": "social events page",
    },
    {
        "name": "ECIO26_LabTours.html",
        "url": "https://www.ecio-conference.org/lab-and-company-visit/",
        "desc": "lab + company-visit page",
    },
]

# Filename-date pattern: ..._<d>_<m>.pdf or ..._<dd>_<mm>.pdf at the very end of
# the basename (used to pick the most recent re-issue when multiple candidates
# show up). Year is assumed 2026.
DATE_RE = re.compile(r"_(\d{1,2})_(\d{1,2})\.pdf$", re.IGNORECASE)

# Polite UA — some WP installs 403 the default urllib UA.
UA = "Mozilla/5.0 (ecio2026-fetch; fine-conference-app)"

# ---------------------------------------------------------------------------
# Optica schedule pages (browser-rendered).
#
# The full ECIO program is also published on Optica's event site as a per-day
# schedule. Unlike the PDFs, each talk cell there carries the COMPLETE author
# list (with affiliations) and the abstract — the richest content source for
# the conference. The schedule is a JavaScript single-page app (the day is
# selected by the URL hash) sitting behind Radware bot protection, so plain
# urllib can't read it; we drive a real Chromium via Playwright instead.
#
# For each day we: load the page, switch the view toggle to "Detailed View",
# click every "Continue Reading" link so each abstract's full text is expanded
# in the DOM, then save the rendered HTML. The processor parses these offline.
#
# The bot wall passes automatically for a headed (non-headless) browser with a
# realistic UA; if a CAPTCHA ever appears we leave the window open and wait so
# the user can solve it. A persistent browser profile (kept outside the repo)
# carries any clearance cookie across runs.
OPTICA_SCHEDULE_URL = (
    "https://www.optica.org/events/topical_meetings/"
    "european_conference_on_integrated_optics_(ecio)/schedule/#/"
)
# Day -> saved filename. The day names are the schedule's own hash routes, not
# program content.
OPTICA_DAYS = {
    "Monday": "ECIO26_OpticaMonday.html",
    "Tuesday": "ECIO26_OpticaTuesday.html",
    "Wednesday": "ECIO26_OpticaWednesday.html",
}
# Real-browser UA for the headed Chromium (the bot wall blocks the headless
# default UA). Run headed so the user can solve a CAPTCHA if one ever appears.
OPTICA_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
OPTICA_PROFILE_DIR = Path.home() / ".cache" / "ecio2026_optica_profile"
# How long to wait (seconds) for a human to clear a bot-wall / CAPTCHA before
# giving up on a day. Generous because solving it is a manual step.
OPTICA_CAPTCHA_WAIT_S = 180


def _bootstrap_playwright() -> None:
    """Ensure the 'playwright' package and its Chromium browser are installed.
    Mirrors the CLEO fetchers' bootstrap so a fresh checkout self-provisions."""
    try:
        import playwright  # noqa: F401
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        print("[setup] Installing the 'playwright' Python package…")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--quiet",
             "playwright>=1.40"])
    # Installing the browser is idempotent and a no-op when already present.
    subprocess.check_call(
        [sys.executable, "-m", "playwright", "install", "chromium"])


def _fetch_optica_schedule() -> tuple[int, list[str]]:
    """Render and save the per-day Optica schedule pages.

    Returns (saved_count, failed_filenames). Never raises for a single day's
    failure — it logs, records the filename, and moves on, so a flaky day or an
    unsolved CAPTCHA doesn't abort the whole download. The PDFs remain the
    pipeline's required inputs; these pages are optional enrichment."""
    _bootstrap_playwright()
    import time
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    OPTICA_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    saved = 0
    failed: list[str] = []
    print("[info] rendering Optica schedule pages via headed Chromium "
          "(a browser window will open)…")
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(OPTICA_PROFILE_DIR),
            headless=False,
            user_agent=OPTICA_UA,
            viewport={"width": 1400, "height": 1000},
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            for day, fname in OPTICA_DAYS.items():
                target = DATA_DIR / fname
                try:
                    page.goto(OPTICA_SCHEDULE_URL + day,
                              wait_until="domcontentloaded", timeout=60000)
                    # Bot wall: a headed browser normally passes, but if the
                    # Radware interstitial shows, wait for the user to clear it.
                    waited = 0
                    while ("perfdrive" in page.url
                           or "captcha" in page.title().lower()):
                        if waited == 0:
                            print(f"[warn]   {day}: bot-wall/CAPTCHA detected — "
                                  f"please solve it in the open browser window "
                                  f"(waiting up to {OPTICA_CAPTCHA_WAIT_S}s)…")
                        time.sleep(3)
                        waited += 3
                        if waited >= OPTICA_CAPTCHA_WAIT_S:
                            raise PWTimeout("bot wall not cleared in time")
                    # Wait for the schedule to render its talk rows.
                    page.wait_for_selector("li.presentation", timeout=45000)
                    time.sleep(2)
                    # Switch to Detailed View so every cell shows its full meta.
                    try:
                        page.get_by_text("Detailed View",
                                         exact=True).click(timeout=8000)
                        time.sleep(2)
                    except PWTimeout:
                        print(f"[warn]   {day}: 'Detailed View' toggle not "
                              f"found; saving the default view.")
                    # Expand every truncated abstract ("Continue Reading").
                    expanded = page.evaluate(
                        "() => { let c = 0;"
                        " document.querySelectorAll("
                        "'a.presentation__description-expand').forEach(a => {"
                        " if (/Continue Reading/i.test(a.innerText)) {"
                        " a.click(); c++; } }); return c; }")
                    time.sleep(2)
                    html_text = page.content()
                    target.write_bytes(html_text.encode("utf-8"))
                    npres = page.evaluate(
                        "() => document.querySelectorAll("
                        "'li.presentation').length")
                    size_kb = target.stat().st_size / 1024
                    print(f"[ok]   saved {fname} ({size_kb:,.1f} KB; "
                          f"{npres} talks, {expanded} abstracts expanded).")
                    saved += 1
                except Exception as e:  # noqa: BLE001 — log & continue per day
                    print(f"[warn]   {day}: could not save {fname}: {e}")
                    failed.append(fname)
        finally:
            ctx.close()
    return saved, failed


def _fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read()
    return raw.decode("utf-8", errors="replace")


def _fetch_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def _date_key(url: str) -> tuple[int, int, str]:
    """Sort key for picking the freshest re-issue of a PDF: extract (month, day)
    from the trailing _<d>_<m>.pdf suffix; fall back to the URL itself so the
    sort is total even when no date can be parsed."""
    m = DATE_RE.search(url)
    if not m:
        return (0, 0, url)
    day, month = int(m.group(1)), int(m.group(2))
    return (month, day, url)


def _pick_latest(html: str, pat: re.Pattern[str]) -> str | None:
    candidates = sorted(set(pat.findall(html)), key=_date_key, reverse=True)
    return candidates[0] if candidates else None


def main() -> None:
    print("=" * 72)
    print("[config] ECIO 2026 DOWNLOADER starting up.")
    print(f"[config]   script dir   : {SCRIPT_DIR}")
    print(f"[config]   data dir     : {DATA_DIR}")
    print(f"[config]   programme URL: {PROGRAMME_URL}")
    print(f"[config]   run date     : {date.today().isoformat()}")
    print("=" * 72)

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Fetch the programme HTML once, lazily — only if at least one artifact is
    # link-discovery (pattern) based.
    needs_programme_html = any("pattern" in a for a in ARTIFACTS)
    html = ""
    if needs_programme_html:
        print(f"[info] fetching programme page to discover PDF links …")
        try:
            html = _fetch_text(PROGRAMME_URL)
        except urllib.error.URLError as e:
            print(f"[fatal] could not fetch {PROGRAMME_URL}: {e}")
            sys.exit(1)
        print(f"[info]   fetched {len(html):,} chars of HTML.")

    saved_any = False
    failed: list[str] = []
    for art in ARTIFACTS:
        target = DATA_DIR / art["name"]
        if "url" in art:
            # Fixed-URL artifact: download directly.
            url = art["url"]
        else:
            # Pattern artifact: find the freshest matching link on programme
            # page and download that.
            url = _pick_latest(html, art["pattern"])
            if not url:
                print(f"[warn] no link matching {art['desc']} found on the "
                      f"programme page; cannot fetch {art['name']}.")
                failed.append(art["name"])
                continue
        print(f"[info] downloading {art['desc']} from {url}")
        try:
            body = _fetch_bytes(url)
        except urllib.error.URLError as e:
            print(f"[warn]   download failed: {e}")
            failed.append(art["name"])
            continue
        target.write_bytes(body)
        size_kb = target.stat().st_size / 1024
        print(f"[ok]   saved {target.name} ({size_kb:,.1f} KB).")
        saved_any = True

    # Render + save the per-day Optica schedule pages (browser-driven). These
    # are optional enrichment, so a failure here is a warning, not fatal.
    print("-" * 72)
    try:
        opt_saved, opt_failed = _fetch_optica_schedule()
        if opt_saved:
            saved_any = True
        failed.extend(opt_failed)
    except Exception as e:  # noqa: BLE001 — never let enrichment abort the run
        print(f"[warn] Optica schedule fetch failed entirely: {e}")
        failed.extend(OPTICA_DAYS.values())

    print()
    print("=" * 72)
    if failed:
        print(f"DONE WITH WARNINGS — {len(failed)} file(s) not retrieved:")
        for n in failed:
            print(f"  - {n}")
        print("Re-check the programme page or see data_requirements_ecio2026.txt "
              "for the manual fallback.")
    else:
        print("DONE (downloaded program PDFs). Next: run process_program_ecio2026.py")
    print(f"  data dir : {DATA_DIR}")
    print("=" * 72)
    if not saved_any:
        sys.exit(1)


if __name__ == "__main__":
    main()
