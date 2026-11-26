#!/usr/bin/env python3
"""Builds the neofetch-style profile card (dark_mode.svg + light_mode.svg).

Run locally:   GH_TOKEN=$(gh auth token) python3 neofetch.py
In CI:         .github/workflows/neofetch.yml runs it daily.

Stdlib only, so the workflow needs no pip install.
"""
import datetime as dt
import hashlib
import html
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

USER = "kirollosatef"
JOINED = dt.date(2020, 11, 10)  # GitHub account creation date
ROOT = Path(__file__).parent
CACHE = ROOT / "cache" / "loc.json"
SEEN = ROOT / "cache" / "totals.json"  # best count seen with the full-access token
# GH_TOKEN (a personal token) also sees private work; GITHUB_TOKEN is the fallback if it expires.
TOKENS = [t for t in (os.environ.get("GH_TOKEN"), os.environ.get("GITHUB_TOKEN")) if t]

WIDTH = 64  # characters on each info line, including the leading ". "


# ---------------------------------------------------------------- GitHub API
def gql(query, **variables):
    while True:
        req = urllib.request.Request(
            "https://api.github.com/graphql",
            data=json.dumps({"query": query, "variables": variables}).encode(),
            headers={"Authorization": f"bearer {TOKENS[0]}", "User-Agent": "neofetch-card"},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                body = json.load(r)
            break
        except urllib.error.HTTPError as e:
            if e.code != 401 or len(TOKENS) == 1:
                raise
            print("GH_TOKEN rejected (expired?), falling back to GITHUB_TOKEN")
            TOKENS.pop(0)
    if body.get("errors"):
        raise RuntimeError(body["errors"])
    return body["data"]


def repos(affiliations):
    out, cursor = [], None
    while True:
        d = gql(
            """query($login:String!,$aff:[RepositoryAffiliation],$after:String){
              user(login:$login){ repositories(first:100, after:$after, ownerAffiliations:$aff){
                pageInfo{hasNextPage endCursor}
                nodes{ nameWithOwner isFork isPrivate stargazerCount
                       defaultBranchRef{ target{ ... on Commit{ oid } } } } } } }""",
            login=USER, aff=affiliations, after=cursor,
        )["user"]["repositories"]
        out += d["nodes"]
        if not d["pageInfo"]["hasNextPage"]:
            return out
        cursor = d["pageInfo"]["endCursor"]


def my_history(name_with_owner, author_id):
    """Additions, deletions and commit dates authored by me on the default branch."""
    owner, name = name_with_owner.split("/")
    add = dele = 0
    dates = []
    cursor = None
    while True:
        d = gql(
            """query($o:String!,$n:String!,$id:ID!,$after:String){
              repository(owner:$o,name:$n){ defaultBranchRef{ target{ ... on Commit{
                history(first:100, after:$after, author:{id:$id}){
                  totalCount pageInfo{hasNextPage endCursor}
                  nodes{ additions deletions committedDate } } } } } } }""",
            o=owner, n=name, id=author_id, after=cursor,
        )
        ref = (d["repository"] or {}).get("defaultBranchRef")
        if not ref:
            return 0, 0, []
        h = ref["target"]["history"]
        for c in h["nodes"]:
            add += c["additions"]
            dele += c["deletions"]
            dates.append(c["committedDate"][:10])
        if not h["pageInfo"]["hasNextPage"]:
            return add, dele, sorted(dates)
        cursor = h["pageInfo"]["endCursor"]


def stats():
    u = gql(
        """query($login:String!){ user(login:$login){ id followers{totalCount}
             repositoriesContributedTo(first:1, includeUserRepositories:true,
               contributionTypes:[COMMIT,PULL_REQUEST,REPOSITORY,PULL_REQUEST_REVIEW]){totalCount} } }""",
        login=USER,
    )["user"]
    owned = [r for r in repos(["OWNER"]) if not r["isPrivate"]]  # match the public profile count
    everything = repos(["OWNER", "COLLABORATOR", "ORGANIZATION_MEMBER"])

    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    for r in everything:
        if r["isFork"] or not r["defaultBranchRef"]:
            continue
        # Hashed so private repo names never land in this public repo.
        key = hashlib.sha256(r["nameWithOwner"].encode()).hexdigest()[:16]
        oid = r["defaultBranchRef"]["target"]["oid"]
        hit = cache.get(key)
        if not hit or hit["oid"] != oid:
            try:
                a, d, dates = my_history(r["nameWithOwner"], u["id"])
            except Exception as e:  # one broken repo should not kill the card
                print(f"skip {key}: {e}")
                continue
            cache[key] = {"oid": oid, "add": a, "del": d, "dates": dates}
    # Sum every repo ever seen, so repos the CI token cannot list keep their last count.
    add = dele = 0
    dates = []
    for hit in cache.values():
        add, dele = add + hit["add"], dele + hit["del"]
        dates += hit.get("dates", [])
    CACHE.parent.mkdir(exist_ok=True)
    CACHE.write_text(json.dumps(cache, indent=1, sort_keys=True))

    # Commit dates are cached per repo, so the rolling windows move forward every day
    # even for repos the CI token cannot see.
    today = dt.datetime.now(dt.timezone.utc).date()
    since = lambda days: (today - dt.timedelta(days=days)).isoformat()
    year_ago, week_ago = since(365), since(7)

    # The fallback token only sees public work, so never let this drop below
    # what the full-access token last saw.
    seen = json.loads(SEEN.read_text()) if SEEN.exists() else {}
    contributed = max(u["repositoriesContributedTo"]["totalCount"], seen.get("contributed", 0))
    SEEN.write_text(json.dumps({"contributed": contributed}, indent=1) + "\n")

    return {
        "repos": len(owned),
        "contributed": contributed,
        "stars": sum(r["stargazerCount"] for r in owned),
        "followers": u["followers"]["totalCount"],
        "commits": len(dates),
        "commits_year": sum(d > year_ago for d in dates),
        "commits_week": sum(d > week_ago for d in dates),
        "loc": add - dele,
        "add": add,
        "del": dele,
    }


def uptime(start, today):
    y, m, d = today.year - start.year, today.month - start.month, today.day - start.day
    if d < 0:
        m -= 1
        prev = today.replace(day=1) - dt.timedelta(days=1)
        d += prev.day
    if m < 0:
        y, m = y - 1, m + 12
    s = lambda n, w: f"{n} {w}{'' if n == 1 else 's'}"
    return f"{s(y, 'year')}, {s(m, 'month')}, {s(d, 'day')}"


# ---------------------------------------------------------------- SVG
MOBILE_WIDTH = 48  # characters per info line on the tall phone card


def info_rows(s, today):
    """What the card says, independent of layout."""
    n = lambda v: f"{v:,}"
    return [
        ("header", "kiro@atef", ""),
        ("kv", "OS", "macOS 26 Tahoe, Linux, Windows"),
        ("kv", "Uptime", uptime(JOINED, today)),
        ("kv", "Host", "CustomGPT.ai"),
        ("kv", "Kernel", "AI Software & Product Engineer"),
        ("kv", "Location", "Alexandria, Egypt"),
        ("kv", "IDE", "Claude Code, VS Code, Zed"),
        ("kv", "Shell", "zsh + Starship, cmux"),
        ("blank",),
        ("kv", "Languages.Programming", "TypeScript, JavaScript, Python"),
        ("kv", "Languages.Computer", "HTML, CSS, JSON, YAML, SQL"),
        ("kv", "Languages.Real", "Arabic, English"),
        ("blank",),
        ("kv", "Hobbies.Software", "AI Agents, Automation"),
        ("kv", "Hobbies.Hardware", "Home Lab, Self-hosting"),
        ("kv", "Avatar", "Leonhard Euler (1707–1783)"),
        ("blank",),
        ("header", "Contact", "- "),
        ("kv", "Email.Personal", "kirollosateffawze@gmail.com"),
        ("kv", "Email.Work", "kirollos@customgpt.ai"),
        ("kv", "LinkedIn", "kirollos-atef-fawze"),
        ("kv", "X", "@kiro_af"),
        ("kv", "GitHub", "kirollosatef"),
        ("blank",),
        ("header", "GitHub Stats", "- "),
        ("kv", "Followers", n(s["followers"])),
        ("kv", "Repos", f"{s['repos']} {{Contributed: {s['contributed']}}}"),
        ("kv", "Stars", n(s["stars"])),
        ("kv", "Commits", n(s["commits"])),
        ("kv", "Commits.PastYear", n(s["commits_year"])),
        ("kv", "Commits.PastWeek", n(s["commits_week"])),
        ("loc", n(s["loc"]), n(s["add"]), n(s["del"])),
    ]


# A line is a list of (css class, text) spans. Dots pad key/value apart.
def kv(key, value, width, prefix=". "):
    keytxt = f"{key}:"
    dots = width - len(prefix) - len(keytxt) - len(value) - 2
    if dots < 1:  # too long for this width: value goes right-aligned on its own line
        return [[("cc", prefix), ("key", keytxt)],
                [("cc", prefix + "." * (width - len(prefix) - len(value) - 1) + " "), ("value", value)]]
    return [[("cc", prefix), ("key", keytxt), ("cc", " " + "." * dots + " "), ("value", value)]]


def layout(rows, width):
    lines = []
    for kind, *a in rows:
        if kind == "header":
            title, prefix = a
            rest = width - len(prefix) - len(title) - 1
            lines.append([("cc", prefix), ("title", title), ("cc", " " + "—" * (rest - 3) + "-—-")])
        elif kind == "blank":
            lines.append([("cc", ". ")])
        elif kind == "kv":
            lines += kv(*a, width)
        elif kind == "loc":
            loc, add, dele = a
            tail = [("cc", "( "), ("add", f"{add}++"), ("cc", ", "), ("del", f"{dele}--"), ("cc", " )")]
            tail_len = sum(len(t) for _, t in tail)
            dots = width - len(". Lines of Code:") - len(loc) - tail_len - 3
            if dots >= 1:
                lines.append([("cc", ". "), ("key", "Lines of Code:"), ("cc", " " + "." * dots + " "),
                              ("value", loc), ("cc", " ")] + tail)
            else:
                lines += kv("Lines of Code", loc, width)
                lines.append([("cc", ". " + " " * (width - 2 - tail_len))] + tail)
    return lines


THEMES = {
    "dark": dict(bg="#161b22", fg="#c9d1d9", key="#ffa657", value="#a5d6ff",
                 cc="#616e7f", add="#3fb950", dele="#f85149", eq="#58a6ff"),
    "light": dict(bg="#f6f8fa", fg="#24292f", key="#953800", value="#0a3069",
                  cc="#c2cfde", add="#1a7f37", dele="#cf222e", eq="#0969da"),
}

FONT, LH, CW = 16, 20, 9.6  # info text: font-size, line height, approx glyph width (px)
ART_FONT, ART_LH, ART_CW = 11, 13, 6.6  # the portrait uses a smaller font for more detail
PAD = 20


def render(theme, art, eqs, lines, width, stacked):
    """stacked=False: portrait left, info right (desktop). True: portrait on top (phones)."""
    t = THEMES[theme]
    art_w = max(len(l) for l in art) * ART_CW
    art_h = len(art) * ART_LH
    info_w, info_h = width * CW, len(lines) * LH
    if stacked:
        w = int(max(art_w, info_w) + 2 * PAD)
        art_x, art_top = (w - art_w) / 2, PAD + ART_LH - 3
        info_x, info_y = (w - info_w) / 2, PAD + art_h + 30
        h = int(info_y + info_h + PAD - 10)
    else:
        art_x, info_x, info_y = PAD, PAD + art_w + 30, 30
        w = int(info_x + info_w + PAD)
        h = max(30 + info_h, PAD + art_h) + 10
        art_top = (h - art_h) // 2 + ART_LH - 3
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" font-family="ConsolasFallback,Consolas,Menlo,monospace" '
        f'width="{w}px" height="{h}px" font-size="{FONT}px">',
        "<style>@font-face{src:local('Consolas'),local('Consolas Bold');font-family:'ConsolasFallback';"
        "font-display:swap;-webkit-size-adjust:109%;size-adjust:109%;}"
        f".key{{fill:{t['key']};}}.value{{fill:{t['value']};}}.add{{fill:{t['add']};}}"
        f".del{{fill:{t['dele']};}}.cc{{fill:{t['cc']};}}.title{{fill:{t['fg']};}}"
        f".eq{{fill:{t['eq']};opacity:.55;font-style:italic;}}.hero{{opacity:1;font-weight:bold;font-size:14px;}}"
        f"text,tspan{{white-space:pre;}}</style>",
        f'<rect width="{w}px" height="{h}px" fill="{t["bg"]}" rx="15"/>',
        f'<text x="{art_x:.1f}" y="{art_top}" fill="{t["fg"]}" font-size="{ART_FONT}px">',
    ]
    for i, l in enumerate(art):
        out.append(f'<tspan x="{art_x:.1f}" y="{art_top + i * ART_LH}">{html.escape(l)}</tspan>')
    out.append("</text>")
    # Euler's equations float in the empty background around the portrait.
    # Each one gets its own x so wide glyphs (π, Σ, ²) cannot shift the others.
    out.append(f'<text font-size="{ART_FONT}px">')
    first = True
    for i, l in enumerate(eqs):
        for m in re.finditer(r"\S+(?: \S+)*", l):
            cls = "eq hero" if first else "eq"
            first = False
            out.append(f'<tspan class="{cls}" x="{art_x + m.start() * ART_CW:.1f}" '
                       f'y="{art_top + i * ART_LH}">{html.escape(m.group())}</tspan>')
    out.append("</text>")
    out.append(f'<text x="{info_x:.0f}" y="{info_y}" fill="{t["fg"]}">')
    for i, spans in enumerate(lines):
        parts = "".join(f'<tspan class="{c}">{html.escape(s)}</tspan>' for c, s in spans)
        out.append(f'<tspan x="{info_x:.0f}" y="{info_y + i * LH}">{parts}</tspan>')
    out.append("</text></svg>")
    return "\n".join(out)


def main():
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=2))).date()  # Cairo
    s = stats()
    print(s)
    rows = info_rows(s, today)
    read = lambda f: (ROOT / f).read_text().rstrip("\n").split("\n")
    eqs = read("ascii-eq.txt")
    for theme, art_file in (("dark", "ascii-art.txt"), ("light", "ascii-art-light.txt")):
        art = read(art_file)
        (ROOT / f"{theme}_mode.svg").write_text(render(theme, art, eqs, layout(rows, WIDTH), WIDTH, False))
        (ROOT / f"{theme}_mobile.svg").write_text(
            render(theme, art, eqs, layout(rows, MOBILE_WIDTH), MOBILE_WIDTH, True))


if __name__ == "__main__":
    main()
