# AGENTS.md — Adding a new conference

This guide is for AI coding agents helping a user curate a new conference for
the Fine Conference App. Agentic coding is fine here: fetchers, processors,
and requirements manifests are mechanical glue around a conference-specific
source, and an agent can often do the bulk of the work after a short
conversation with the user.

Before you do anything else, internalize the rule below.

## The rule that matters most: no hardcoded conference content

**Conference content must never be hardcoded into any tracked source file.**
That includes the fetcher (`fetch_program_<slug>.py`), the processor
(`process_program_<slug>.py`), the requirements manifest, and any helper you
add. Specifically forbidden in source code:

- Paper titles and abstracts.
- Author names, speaker names, presider names, affiliations.
- Session titles, talk numbers, specific time slots, room names.
- Anything else from the program that you would not invent yourself.

All such content must be **extracted at runtime** from files in `data/`. The
processor reads those files when it runs; the fetcher (or the user) puts them
there. **Nothing of substance from the program goes into the Python source.**

This is not a stylistic preference. Conference programs — titles, abstracts,
author lists — are typically copyrighted by the conference and its publisher.
Embedding any of that in tracked source code turns the repository into an
unlicensed redistribution of copyrighted material. Keep program content on the
user's machine, in `data/`, where it stays.

What IS fine in source code:
- The conference's short slug (e.g. `cleo2026`).
- Parsing format: CSS selectors, regex shapes, PDF layout heuristics, column
  names, header strings the source file itself uses — these describe FORMAT,
  not content.
- Generic type labels in a registry ("Invited", "Contributed", "Poster",
  "Plenary"). These are universal genre labels, not copyrighted content.
- The conference's display name, but ONLY as a single obvious top-level
  constant near the top of the processor (e.g. `CONFERENCE_NAME = "CLEO 2026"`)
  so the user can review and edit it. It ends up as `conference_name` in the
  JSON output.

If you're tempted to write a fixture, mock, or "default" that contains program
content, stop. That belongs in `data/`, in a file the user supplies or the
fetcher downloads.

## Workflow for a new conference

1. **Pick a slug.** Lowercase conventional acronym plus year, e.g. `cleo2026`,
   `iqclsw2026`, `ecio2026`. The slug names the directory and is reused in
   every file name inside it.

2. **Ask the user where the program data lives.** Don't guess. Two paths:

   - **URL(s) the fetcher can download from.** Ask for the exact URL(s) of the
      program — the schedule HTML, abstract book PDF, CSV export, etc.
      Implement `fetch_program_<slug>.py` to download those into `data/`. If
      the source needs a login, see the login section below.

   - **Manual files.** If automated download is not viable (no public URL,
      complex auth, terms of service that forbid scraping, etc.), the user
      drops files into `data/` themselves. Write a minimal
      `fetch_program_<slug>.py` that prints a clear "please supply
      `<filenames>` in `data/`" message and exits, and make
      `data_requirements_<slug>.txt` mark each input as required with a
      `manual:` field describing where the user obtains it.

   When unsure which path applies to a given file, ask the user explicitly.

3. **Write `data_requirements_<slug>.txt`.** One `[file: <pattern>]` block per
   required input, with `required:`, `description:`, `produced_by:` (the
   fetcher script name, if any), and `manual:` (instructions for obtaining the
   file by hand) keys. `manual:` is what the user sees when a file is missing
   — make it specific (URL, page, click path). See any existing conference's
   manifest, and `_parse_requirements()` in `scripts/make_app.py` for the
   canonical parser.

4. **Write `process_program_<slug>.py`.** Read the files in `data/`, parse
   them at runtime, and emit `conference_data.json` matching the schema in
   `docs/CONFERENCE_JSON.md`. Constraints:

   - Reads from `SCRIPT_DIR / "data"` (relative to the processor's own path).
   - Writes to `SCRIPT_DIR / "conference_data.json"`.
   - Does no network access — the fetcher is the only place that touches the
     network.
   - Hardcodes no titles, abstracts, names, or other program content.

5. **Verify with `make_app.py`.** From the repo root:

   ```bash
   python scripts/make_app.py <slug>
   ```

   This runs the full pipeline (fetch -> verify -> process -> build) and
   writes `conferences/<slug>/<slug>_app.html`. Open it in a browser and
   iterate on the processor until the program renders correctly.

## Login-required sources

If the program lives behind a login, the fetcher can use Playwright with the
Chromium browser launched **headed** (i.e. `headless=False`) so the user can
sign in interactively in the visible browser window. After the user logs in,
the fetcher continues and downloads what it needs. Persist the storage state
between runs (Playwright's `storage_state` JSON) so the user does not have to
log in every build. The CLEO fetchers under `conferences/cleo2025/` and
`conferences/cleo2026/` demonstrate this pattern.

If automating login is too involved or fragile, fall back to the manual path:
instruct the user in `data_requirements_<slug>.txt` to log in themselves and
drop the downloaded files in `data/`.

## References

Read these before writing code:

- **`conferences/test2026/`** — Synthetic, PDF-only, minimal. The cleanest
  reference for what a small, clean conference looks like end to end. Start
  here.
- **`docs/CONFERENCE_JSON.md`** — The exact `conference_data.json` schema:
  every field, every constraint, what is required and what is optional. The
  processor's output MUST match this.
- **`scripts/make_app.py`** — The orchestration contract. The module docstring
  lays out the directory layout, the file-naming conventions, the step-by-step
  flow, and the cache rules. Skim it before you write a fetcher or processor
  so you understand what `make_app.py` will expect to find.
- **`scripts/build_conference_app.py`** — The shared builder. You usually
  don't edit it; its top docstring documents what fields it reads from the
  JSON and how it derives the short forms the app renders.

Other examples, in rough order of complexity:

- **`conferences/iqclsw2026/`** — Small, HTML-based, no presiders, no per-talk
  abstracts.
- **`conferences/ecio2026/`** — An alternative pattern combining HTML and PDF
  sources.
- **`conferences/cleo2025/`, `conferences/cleo2026/`** — Larger, multi-source
  (PDF + official CSV + scraped HTML), with presider scraping. Use this
  complexity only if the conference genuinely needs it.

## Checklist before declaring "done"

- [ ] `python scripts/make_app.py <slug>` runs end-to-end without errors.
- [ ] `<slug>_app.html` opens in a browser and renders the program correctly,
      including session/talk lists, search, and detail pages.
- [ ] No paper titles, abstracts, author names, or session-specific content
      appears anywhere in `conferences/<slug>/*.py` or the requirements
      manifest. Grep your own work if you are not sure.
- [ ] `data_requirements_<slug>.txt` lists every input file with a clear
      `manual:` instruction so a user with no familiarity with the conference
      can obtain the files themselves.
- [ ] If using Playwright with login, the storage state persists between runs
      so re-runs do not require re-login.
- [ ] The slug-named JSON (`conference_data_<slug>.json`) and built app
      (`<slug>_app.html`) end up in `conferences/<slug>/`, not committed
      anywhere else.
