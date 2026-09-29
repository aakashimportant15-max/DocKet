#!/usr/bin/env python3
"""DOGFOOD 2026 T3 self-check: community voting, comments, hidden results.

Usage:  python3 t3_check.py .dogfood.toml > t3-check-report.txt

Not the official checker - run.py is, and it contains no T3 checks at all.
This script is the evidence for the T3 claims: the vote budget, the
self-vote rule, anonymous access, the hidden-results window, the per-voter
ballot order, comment escaping, and the organizer open/close controls.

Standard library only. Run it against a freshly seeded portal. The last
check leaves the voting window OPEN again and a best-effort cleanup removes
the test votes, so the script is safe to run twice in a row; every run
leaves one test comment behind, so re-seed before capturing screenshots.
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

try:
    import tomllib  # Python 3.11 and newer
except ModuleNotFoundError:
    tomllib = None


def parse_toml(text):
    """Enough TOML for .dogfood.toml, so older Pythons work too.

    Handles [section] headers, key = "string", and key = ["a", "b"].
    """
    data, section = {}, None
    for raw in text.splitlines():
        line = raw.split("#")[0].strip()
        if not line:
            continue
        head = re.fullmatch(r"\[([A-Za-z0-9_.]+)\]", line)
        if head:
            section = data.setdefault(head.group(1), {})
            continue
        key, sep, value = line.partition("=")
        if not sep or section is None:
            continue
        key, value = key.strip(), value.strip()
        if value.startswith("["):
            section[key] = re.findall(r'"([^"]*)"', value)
        else:
            section[key] = value.strip().strip('"').strip("'")
    return data


def load_config(path):
    if tomllib:
        with open(path, "rb") as f:
            return tomllib.load(f)
    with open(path, encoding="utf-8") as f:
        return parse_toml(f.read())


TIMEOUT = 10
SUCCESS = (200, 201, 202, 303)  # our write routes answer POST with 303 See Other


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Surface 3xx to the caller instead of silently following it.

    Without this, a successful POST /vote would follow its redirect and the
    script could never tell a 303 apart from a plain 200.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = urllib.request.build_opener(NoRedirect)


def request(url, header=None, method="GET", form=None):
    """Return (status, text). Never raises on an HTTP error status."""
    req = urllib.request.Request(url, method=method)
    if header:
        name, _, value = header.partition(":")
        req.add_header(name.strip(), value.strip())
    if form is not None:
        req.data = urllib.parse.urlencode(form).encode()
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with OPENER.open(req, timeout=TIMEOUT) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


class Check:
    """One assertion. Collects its own failure detail as it runs."""

    def __init__(self, label):
        self.label = label
        self.ok = False
        self.detail = []

    def note(self, line):
        self.detail.append(line)


def load_fixture(explicit, config_path):
    """Same search order as run.py, so both find the same fixtures.json."""
    here = os.path.dirname(os.path.abspath(__file__))
    beside_config = os.path.dirname(os.path.abspath(config_path))
    candidates = [explicit] if explicit else [
        "fixtures.json",
        os.path.join(here, "fixtures.json"),
        os.path.join(beside_config, "fixtures.json"),
        os.path.join(beside_config, "data", "fixtures.json"),
    ]
    for c in candidates:
        try:
            with open(c, "rb") as f:
                return json.load(f), c
        except (FileNotFoundError, NotADirectoryError):
            continue
    return None, None


def order_links(html, cls):
    """Project ids in the order their links appear, for one anchor class.

    Scoped to a class name so the plain nav link /gallery never counts and
    the check sees pages in their real visual order.
    """
    return [
        int(n)
        for n in re.findall(rf'class="{cls}" href="/gallery/(\d+)"', html)
    ]


def build_checks(cfg, fixture):
    base = cfg["portal"]["base_url"].rstrip("/")
    auth = cfg.get("auth", {})
    voter_a = auth.get("voter_a")
    voter_b = auth.get("voter_b")
    organizer = auth.get("organizer")

    def url(path):
        return base + path

    titles = [p.get("title", "") for p in (fixture or {}).get("projects") or []]
    n_projects = len(titles)

    checks = []

    # --- 1: a vote is accepted -------------------------------------------
    # Project 2 is owned by a team voter_b does not belong to (voter_b has
    # no team at all), so the only thing this can fail on is the vote path
    # itself.
    c = Check("vote accepted (303)")
    status, _ = request(url("/projects/2/vote"), header=voter_b, method="POST")
    c.ok = status == 303
    if not c.ok:
        c.note("POST /projects/2/vote as voter_b on a fresh seed")
        c.note(f"got {status or 'no response'}, wanted 303")
    checks.append(c)

    # --- 2: the same vote again is refused -------------------------------
    c = Check("duplicate vote refused (400)")
    status, body = request(url("/projects/2/vote"), header=voter_b, method="POST")
    c.ok = status == 400 and "already voted" in body
    if not c.ok:
        c.note("POST /projects/2/vote as voter_b, a second time")
        c.note(f"got {status or 'no response'}, wanted 400 already-voted")
        if status == 400:
            c.note(f"body: {body[:120]!r}")
    checks.append(c)

    # --- 3: the budget stops at three votes ------------------------------
    c = Check("vote budget enforced (3 max)")
    third = request(url("/projects/3/vote"), header=voter_b, method="POST")[0]
    fourth = request(url("/projects/4/vote"), header=voter_b, method="POST")[0]
    status, body = request(url("/projects/5/vote"), header=voter_b, method="POST")
    c.ok = third == 303 and fourth == 303 and status == 400 and "budget" in body
    if not c.ok:
        c.note("POST votes for projects 3 and 4 as voter_b (votes 2 and 3")
        c.note("of a budget of 3), then project 5 (vote 4)")
        c.note(f"got {third}, {fourth}, then {status or 'no response'}, "
               "wanted 303, 303, then 400 vote-budget-exhausted")
        if status == 400 and "budget" not in body:
            c.note(f"body: {body[:120]!r}")
    checks.append(c)

    # --- 4: you cannot vote for your own team ----------------------------
    # voter_a is a member of the team that owns project 1 (Glass Signal),
    # per the seed.
    c = Check("self-vote refused (400)")
    status, body = request(url("/projects/1/vote"), header=voter_a, method="POST")
    c.ok = status == 400 and "own team" in body
    if not c.ok:
        c.note("POST /projects/1/vote as voter_a, a member of the team that")
        c.note("owns project 1")
        c.note(f"got {status or 'no response'}, wanted 400 own-team")
        if status == 400:
            c.note(f"body: {body[:120]!r}")
    checks.append(c)

    # --- 5: anonymous voters have no way in ------------------------------
    c = Check("anonymous vote refused (403)")
    status, _ = request(url("/projects/6/vote"), method="POST")
    c.ok = status == 403
    if not c.ok:
        c.note("POST /projects/6/vote with no cookie at all")
        c.note(f"got {status or 'no response'}, wanted 403")
    checks.append(c)

    # --- 6: hidden means hidden ------------------------------------------
    # Fixture-independent part first: while the window is open the ranked
    # list must not exist anywhere in the HTML - no titles, no gallery
    # links, no result-list container.
    c = Check("results hidden while open")
    status, body = request(url("/results"))
    leaked_titles = [t for t in titles if t and t.lower() in body.lower()]
    leaked_links = re.findall(r"/gallery/\d+", body)
    c.ok = (status == 200
            and "Results are hidden until voting closes on" in body
            and not leaked_titles
            and not leaked_links
            and "result-list" not in body)
    if not c.ok:
        c.note("GET /results while the voting window is open")
        if status != 200:
            c.note(f"got {status or 'no response'}, wanted 200")
        if "Results are hidden until voting closes on" not in body:
            c.note("the hidden-results message is not on the page")
        if leaked_titles:
            c.note(f"leaked fixture titles: {leaked_titles[:3]}")
        if leaked_links:
            c.note(f"leaked project links: {leaked_links[:3]}")
        if "result-list" not in body and status == 200:
            c.note("the ranked list container is on the page")
    checks.append(c)

    # --- 7: per-voter shuffle, stable for the same voter -----------------
    _, first = request(url("/vote"), header=voter_a)
    _, again = request(url("/vote"), header=voter_a)
    _, other = request(url("/vote"), header=voter_b)
    order_a = order_links(first, "ballot-title")
    order_a2 = order_links(again, "ballot-title")
    order_b = order_links(other, "ballot-title")
    c = Check("ballot order differs per voter")
    c.ok = (len(order_a) == n_projects
            and order_a == order_a2
            and order_a != order_b)
    if not c.ok:
        c.note("GET /vote as voter_a twice, then as voter_b once")
        if len(order_a) != n_projects:
            c.note(f"voter_a's ballot lists {len(order_a)} projects, "
                   f"fixtures has {n_projects}")
        elif order_a != order_a2:
            c.note("voter_a's ballot order changed between two refreshes")
        elif order_a == order_b:
            c.note("voter_a and voter_b got the exact same ballot order")
    checks.append(c)

    # --- 8: comments are stored, public to read, escaped on render -------
    marker = "t3-check <script>alert(1)</script> ok"
    escaped = "&lt;script&gt;alert(1)&lt;/script&gt;"
    status, _ = request(
        url("/projects/2/comments"),
        header=voter_a,
        method="POST",
        form={"body": marker},
    )
    page_status, page = request(url("/gallery/2"))  # no cookie: read is public
    c = Check("comment stored and escaped")
    c.ok = (status == 303
            and page_status == 200
            and escaped in page
            and marker not in page
            and "Voter A" in page)
    if not c.ok:
        c.note("POST the comment as voter_a, then GET /gallery/2 anonymously")
        if status != 303:
            c.note(f"comment POST: got {status or 'no response'}, wanted 303")
        if page_status != 200:
            c.note(f"comment read: got {page_status or 'no response'}, wanted 200")
        if escaped not in page:
            c.note("the escaped marker is not on the page")
        if marker in page:
            c.note("the raw <script> marker reached the page unescaped")
        if "Voter A" not in page:
            c.note("the author name is not shown")
    checks.append(c)

    # --- 9: closing reveals the ranking, and stops new votes -------------
    # By now exactly three votes exist (voter_b on 2, 3, 4), so the top of
    # the ranking is deterministic: 2, 3, 4, each with 1 vote, ties broken
    # by project id. voter_a still has votes left, so their refused vote
    # proves the window check, not the budget.
    c = Check("close reveals ranked results")
    close_status, _ = request(
        url("/organizer/voting/close"), header=organizer, method="POST"
    )
    late_status, late_body = request(
        url("/projects/6/vote"), header=voter_a, method="POST"
    )
    status, body = request(url("/results"))
    ranked = order_links(body, "result-title")
    one_vote_rows = body.count(">1 vote</span>")
    c.ok = (close_status == 303
            and late_status == 400 and "not open" in late_body
            and status == 200
            and "result-list" in body
            and ranked[:3] == [2, 3, 4]
            and one_vote_rows == 3)
    if not c.ok:
        c.note("POST /organizer/voting/close as organizer, GET /results,")
        c.note("and try one more vote as voter_a")
        if close_status != 303:
            c.note(f"close: got {close_status or 'no response'}, wanted 303")
        if late_status != 400 or "not open" not in late_body:
            c.note(f"late vote: got {late_status or 'no response'}, "
                   "wanted 400 voting-not-open")
        if status != 200:
            c.note(f"results: got {status or 'no response'}, wanted 200")
        if ranked[:3] != [2, 3, 4]:
            c.note(f"top of the ranking: {ranked[:6]}, wanted 2, 3, 4 first "
                   "(the three test votes, ties broken by project id)")
        if one_vote_rows != 3:
            c.note(f"rows showing '1 vote': {one_vote_rows}, wanted 3")
    checks.append(c)

    # --- 10: reopening hides it again ------------------------------------
    c = Check("reopen hides results again")
    status, _ = request(
        url("/organizer/voting/reopen"), header=organizer, method="POST"
    )
    page_status, body = request(url("/results"))
    c.ok = (status == 303
            and page_status == 200
            and "Results are hidden until voting closes on" in body
            and "result-list" not in body)
    if not c.ok:
        c.note("POST /organizer/voting/reopen as organizer, then GET /results")
        if status != 303:
            c.note(f"reopen: got {status or 'no response'}, wanted 303")
        if page_status != 200:
            c.note(f"results: got {page_status or 'no response'}, wanted 200")
        if "Results are hidden until voting closes on" not in body:
            c.note("the hidden-results message is not back on the page")
        if "result-list" in body:
            c.note("the ranked list is still on the page")
    checks.append(c)

    return checks


def cleanup(cfg):
    """Remove the script's own test votes; report what it cannot remove.

    Best-effort on purpose: this runs after the last check, is not a check
    itself, and must never turn a green run red. The comment has no delete
    route by design, so it is reported instead of being removed.
    """
    base = cfg["portal"]["base_url"].rstrip("/")
    voter_b = cfg.get("auth", {}).get("voter_b")
    removed = 0
    for project_id in (2, 3, 4):
        status, _ = request(
            base + f"/projects/{project_id}/unvote", header=voter_b, method="POST"
        )
        removed += status == 303
    return [
        f"cleanup: removed {removed}/3 test votes (window left OPEN)",
        "cleanup: test comment(s) remain on /gallery/2 - comments have no "
        "delete route; re-seed to clear",
    ]


def main():
    ap = argparse.ArgumentParser(description="DOGFOOD 2026 T3 self-check")
    ap.add_argument("config", help="path to .dogfood.toml")
    ap.add_argument("--fixtures", default=None,
                    help="path to fixtures.json (searched for if omitted)")
    args = ap.parse_args()

    cfg = load_config(args.config)
    fixture, fixture_path = load_fixture(args.fixtures, args.config)

    print("DOGFOOD 2026 T3 self-check (not the official checker; run.py is)")
    print(f"portal: {cfg['portal']['base_url']}")
    if fixture is None:
        print("note: fixtures.json was not found, so the title-leak half of "
              "the hidden-results check will fail")
    else:
        print(f"fixtures: {fixture_path}")
    print()

    checks = build_checks(cfg, fixture)

    width = max(len(c.label) for c in checks) + 2
    for c in checks:
        dots = "." * (width - len(c.label))
        print(f"T3  {c.label} {dots} {'PASS' if c.ok else 'FAIL'}")
        for line in c.detail:
            print(f"       {line}")

    print()
    for line in cleanup(cfg):
        print(line)
    print()

    passed = sum(1 for c in checks if c.ok)
    print(f"T3 (own checker): {passed}/{len(checks)} PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
