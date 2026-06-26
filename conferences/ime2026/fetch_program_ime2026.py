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

IME 2026 publishes two source PDFs from

    https://ime2026.org/agenda/

The page currently exposes "View Program Book" and "View Abstract Book" links.
The linked filenames contain spaces and may change, so this downloader discovers
the links from the agenda page and saves them under stable names:

    data/IME2026_ProgramBook.pdf
    data/IME2026_AbstractBook.pdf
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
ARTIFACTS = [
    {
        "name": "IME2026_ProgramBook.pdf",
        "desc": "program book",
        "required_label_words": ("program", "book"),
    },
    {
        "name": "IME2026_AbstractBook.pdf",
        "desc": "abstract book",
        "required_label_words": ("abstract", "book"),
    },
]
UA = "Mozilla/5.0 (ime2026-fetch; fine-conference-app)"


def _fetch_bytes(url: str, *, timeout: int = 120) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _fetch_text(url: str) -> str:
    raw = _fetch_bytes(url, timeout=60)
    return raw.decode("utf-8", errors="replace")


def _resolve(href: str) -> str:
    url = urllib.parse.urljoin(AGENDA_URL, href)
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((
        parts.scheme,
        parts.netloc,
        urllib.parse.quote(parts.path, safe="/%:"),
        urllib.parse.quote(parts.query, safe="=&?/%:"),
        parts.fragment,
    ))


def _find_pdf_links(page_html: str) -> list[dict[str, str]]:
    """Return every PDF link as {url, label}, preserving page order."""
    links: list[dict[str, str]] = []
    for m in re.finditer(
        r"<a\b[^>]*\bhref\s*=\s*(['\"])(.*?)\1[^>]*>(.*?)</a>",
        page_html,
        flags=re.I | re.S,
    ):
        href = html.unescape(m.group(2)).strip()
        label = re.sub(r"<[^>]+>", " ", m.group(3))
        label = html.unescape(re.sub(r"\s+", " ", label)).strip()
        if ".pdf" in href.lower():
            links.append({"url": _resolve(href), "label": label})
    return links


def _pick_link(
    links: list[dict[str, str]], required_words: tuple[str, ...]
) -> dict[str, str] | None:
    for link in links:
        haystack = f"{link['label']} {urllib.parse.unquote(link['url'])}".lower()
        if all(word in haystack for word in required_words):
            return link
    return None


def main() -> None:
    print("=" * 72)
    print("[config] IME 2026 DOWNLOADER starting up.")
    print(f"[config]   script dir : {SCRIPT_DIR}")
    print(f"[config]   data dir   : {DATA_DIR}")
    print(f"[config]   agenda URL : {AGENDA_URL}")
    print(f"[config]   run date   : {date.today().isoformat()}")
    print("=" * 72)

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    print("[info] fetching agenda page to discover the current PDF links ...")
    try:
        page_html = _fetch_text(AGENDA_URL)
    except urllib.error.URLError as e:
        print(f"[fatal] could not fetch {AGENDA_URL}: {e}")
        print("        See data_requirements_ime2026.txt for manual fallbacks.")
        sys.exit(1)

    links = _find_pdf_links(page_html)
    if not links:
        print("[fatal] no PDF links found on the agenda page.")
        print("        See data_requirements_ime2026.txt for manual fallbacks.")
        sys.exit(1)

    failed: list[str] = []
    for art in ARTIFACTS:
        picked = _pick_link(links, art["required_label_words"])
        if not picked:
            print(f"[fatal] no {art['desc']} PDF link found on the agenda page.")
            failed.append(art["name"])
            continue

        pdf_url = picked["url"]
        print(f"[info] downloading {art['desc']} from {pdf_url}")
        try:
            body = _fetch_bytes(pdf_url)
        except urllib.error.URLError as e:
            print(f"[fatal] could not download {pdf_url}: {e}")
            failed.append(art["name"])
            continue

        if body[:4] != b"%PDF" or len(body) < 100_000:
            print(f"[fatal] downloaded {len(body):,} bytes, but it does not "
                  f"look like the IME 2026 {art['desc']} PDF.")
            failed.append(art["name"])
            continue

        target = DATA_DIR / art["name"]
        target.write_bytes(body)
        size_mb = target.stat().st_size / (1024 * 1024)
        print(f"[ok]   saved {target.name} ({size_mb:,.1f} MB).")

    if failed:
        print("[fatal] missing required file(s):")
        for name in failed:
            print(f"        - {name}")
        print("        See data_requirements_ime2026.txt for manual fallbacks.")
        sys.exit(1)

    print()
    print("=" * 72)
    print("DONE (downloaded PDFs). Next: run process_program_ime2026.py")
    print(f"  data dir : {DATA_DIR}")
    print("=" * 72)


if __name__ == "__main__":
    main()
