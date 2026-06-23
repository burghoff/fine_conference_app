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

"""fetch_program_ime2026.py -- DOWNLOAD ONLY.

IME 2026 publishes its program as a single PDF linked from

    https://ime2026.org/agenda/

The link text is "View PDF" and the target filename carries the current
date/version (for example "Program Book_260623_v1.pdf").  This downloader does
not hard-code that rolling filename: it fetches the agenda page, finds the PDF
link, resolves it relative to the page URL, and saves the bytes into data/ under
the stable name the processor expects:

    data/IME2026_ProgramBook.pdf
"""

from __future__ import annotations

import html
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "data"

AGENDA_URL = "https://ime2026.org/agenda/"
PROGRAM_NAME = "IME2026_ProgramBook.pdf"
UA = "Mozilla/5.0 (ime2026-fetch; fine-conference-app)"


def _fetch_bytes(url: str, *, timeout: int = 120) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _fetch_text(url: str) -> str:
    raw = _fetch_bytes(url, timeout=60)
    return raw.decode("utf-8", errors="replace")


def _find_pdf_url(page_html: str) -> str | None:
    """Return the first agenda PDF href, preferring links near "View PDF"."""
    def resolve(href: str) -> str:
        url = urllib.parse.urljoin(AGENDA_URL, href)
        parts = urllib.parse.urlsplit(url)
        return urllib.parse.urlunsplit((
            parts.scheme,
            parts.netloc,
            urllib.parse.quote(parts.path, safe="/%:"),
            urllib.parse.quote(parts.query, safe="=&?/%:"),
            parts.fragment,
        ))

    links: list[tuple[str, str]] = []
    for m in re.finditer(
        r"<a\b[^>]*\bhref\s*=\s*(['\"])(.*?)\1[^>]*>(.*?)</a>",
        page_html,
        flags=re.I | re.S,
    ):
        href = html.unescape(m.group(2)).strip()
        label = re.sub(r"<[^>]+>", " ", m.group(3))
        label = html.unescape(re.sub(r"\s+", " ", label)).strip()
        if ".pdf" in href.lower():
            links.append((href, label))
    if not links:
        return None
    for href, label in links:
        if "view pdf" in label.lower():
            return resolve(href)
    return resolve(links[0][0])


def main() -> None:
    print("=" * 72)
    print("[config] IME 2026 DOWNLOADER starting up.")
    print(f"[config]   script dir : {SCRIPT_DIR}")
    print(f"[config]   data dir   : {DATA_DIR}")
    print(f"[config]   agenda URL : {AGENDA_URL}")
    print(f"[config]   run date   : {date.today().isoformat()}")
    print("=" * 72)

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    print("[info] fetching agenda page to discover the current PDF link ...")
    try:
        page_html = _fetch_text(AGENDA_URL)
    except urllib.error.URLError as e:
        print(f"[fatal] could not fetch {AGENDA_URL}: {e}")
        print("        See data_requirements_ime2026.txt for the manual fallback.")
        sys.exit(1)

    pdf_url = _find_pdf_url(page_html)
    if not pdf_url:
        print("[fatal] no PDF link found on the agenda page.")
        print("        See data_requirements_ime2026.txt for the manual fallback.")
        sys.exit(1)

    print(f"[info] downloading program PDF from {pdf_url}")
    try:
        body = _fetch_bytes(pdf_url)
    except urllib.error.URLError as e:
        print(f"[fatal] could not download {pdf_url}: {e}")
        print("        See data_requirements_ime2026.txt for the manual fallback.")
        sys.exit(1)

    if body[:4] != b"%PDF" or len(body) < 100_000:
        print(f"[fatal] downloaded {len(body):,} bytes, but it does not look "
              "like the IME 2026 program PDF.")
        sys.exit(1)

    target = DATA_DIR / PROGRAM_NAME
    target.write_bytes(body)
    size_mb = target.stat().st_size / (1024 * 1024)
    print(f"[ok]   saved {target.name} ({size_mb:,.1f} MB).")

    print()
    print("=" * 72)
    print("DONE (downloaded program PDF). Next: run process_program_ime2026.py")
    print(f"  data dir : {DATA_DIR}")
    print("=" * 72)


if __name__ == "__main__":
    main()
