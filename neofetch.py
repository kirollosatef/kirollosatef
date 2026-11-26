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
    """Additions, deletions and commit count authored by me on the default branch."""
    owner, name = name_with_owner.split("/")
    add = dele = commits = 0
    cursor = None
    while True:
        d = gql(
            """query($o:String!,$n:String!,$id:ID!,$after:String){
              repository(owner:$o,name:$n){ defaultBranchRef{ target{ ... on Commit{
                history(first:100, after:$after, author:{id:$id}){
                  totalCount pageInfo{hasNextPage endCursor}
                  nodes{ additions deletions } } } } } } }""",
            o=owner, n=name, id=author_id, after=cursor,
        )
        ref = (d["repository"] or {}).get("defaultBranchRef")
        if not ref:
            return 0, 0, 0
        h = ref["target"]["history"]
        commits = h["totalCount"]
        for c in h["nodes"]:
            add += c["additions"]
            dele += c["deletions"]
        if not h["pageInfo"]["hasNextPage"]:
            return add, dele, commits
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
                a, d, c = my_history(r["nameWithOwner"], u["id"])
            except Exception as e:  # one broken repo should not kill the card
                print(f"skip {key}: {e}")
                continue
            cache[key] = {"oid": oid, "add": a, "del": d, "commits": c}
    # Sum every repo ever seen, so repos the CI token cannot list keep their last count.
    add = dele = commits = 0
    for hit in cache.values():
        add, dele, commits = add + hit["add"], dele + hit["del"], commits + hit["commits"]
    CACHE.parent.mkdir(exist_ok=True)
    CACHE.write_text(json.dumps(cache, indent=1, sort_keys=True))

    return {
        "repos": len(owned),
        "contributed": u["repositoriesContributedTo"]["totalCount"],
        "stars": sum(r["stargazerCount"] for r in owned),
        "followers": u["followers"]["totalCount"],
        "commits": commits,
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
# A line is a list of (css class, text) spans. Dots pad key/value apart.
def kv(key, value, width=WIDTH, prefix=". "):
    keytxt = f"{key}:"
    dots = width - len(prefix) - len(keytxt) - len(value) - 2
    return [("cc", prefix), ("key", keytxt), ("cc", " " + "." * max(dots, 1) + " "), ("value", value)]


def header(title, width=WIDTH, prefix=""):
    rest = width - len(prefix) - len(title) - 1
    return [("cc", prefix), ("title", title), ("cc", " " + "—" * (rest - 3) + "-—-")]


def blank():
    return [("cc", ". ")]


def info_lines(s, today):
    n = lambda v: f"{v:,}"
    half = 36
    repos_l = kv("Repos", f"{s['repos']} {{Contributed: {s['contributed']}}}", half)
    stars_r = kv("Stars", n(s["stars"]), WIDTH - half - 3, prefix="")
    commits_l = kv("Commits", n(s["commits"]), half)
    follow_r = kv("Followers", n(s["followers"]), WIDTH - half - 3, prefix="")
    loc_key = ". Lines of Code:"
    loc_tail = f" ( {n(s['add'])}++, {n(s['del'])}-- )"
    loc_dots = WIDTH - len(loc_key) - len(n(s["loc"])) - len(loc_tail) - 2
    return [
        header("kiro@atef"),
        kv("OS", "macOS 26 Tahoe, Linux, Windows"),
        kv("Uptime", uptime(JOINED, today)),
        kv("Host", "CustomGPT.ai"),
        kv("Kernel", "AI Software & Product Engineer"),
        kv("Location", "Alexandria, Egypt"),
        kv("IDE", "Claude Code, VS Code, Zed"),
        kv("Shell", "zsh + Starship, cmux"),
        blank(),
        kv("Languages.Programming", "TypeScript, JavaScript, Python"),
        kv("Languages.Computer", "HTML, CSS, JSON, YAML, SQL"),
        kv("Languages.Real", "Arabic, English"),
        blank(),
        kv("Hobbies.Software", "AI Agents, Automation"),
        kv("Hobbies.Hardware", "Home Lab, Self-hosting"),
        kv("Avatar", "Leonhard Euler (1707–1783)"),
        blank(),
        header("Contact", prefix="- "),
        kv("LinkedIn", "kirollos-atef-fawze"),
        kv("X", "@kiro_af"),
        kv("GitHub", "kirollosatef"),
        blank(),
        header("GitHub Stats", prefix="- "),
        repos_l + [("cc", " | ")] + stars_r,
        commits_l + [("cc", " | ")] + follow_r,
        [("cc", ". "), ("key", "Lines of Code:"), ("cc", " " + "." * loc_dots + " "),
         ("value", n(s["loc"])), ("cc", " ( "), ("add", f"{n(s['add'])}++"), ("cc", ", "),
         ("del", f"{n(s['del'])}--"), ("cc", " )")],
    ]


THEMES = {
    "dark": dict(bg="#161b22", fg="#c9d1d9", key="#ffa657", value="#a5d6ff",
                 cc="#616e7f", add="#3fb950", dele="#f85149", eq="#58a6ff"),
    "light": dict(bg="#f6f8fa", fg="#24292f", key="#953800", value="#0a3069",
                  cc="#c2cfde", add="#1a7f37", dele="#cf222e", eq="#0969da"),
}

FONT, LH, CW = 16, 20, 9.6  # info text: font-size, line height, approx glyph width (px)
ART_FONT, ART_LH, ART_CW = 11, 13, 6.6  # the portrait uses a smaller font for more detail


def render(theme, art, eqs, lines):
    t = THEMES[theme]
    art_cols = max(len(l) for l in art)
    info_x = 20 + art_cols * ART_CW + 30
    w = int(info_x + WIDTH * CW + 20)
    h = max(30 + len(lines) * LH, 20 + len(art) * ART_LH) + 10
    art_top = (h - len(art) * ART_LH) // 2 + ART_LH - 3
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
        f'<text x="20" y="{art_top}" fill="{t["fg"]}" font-size="{ART_FONT}px">',
    ]
    for i, l in enumerate(art):
        out.append(f'<tspan x="20" y="{art_top + i * ART_LH}">{html.escape(l)}</tspan>')
    out.append("</text>")
    # Euler's equations float in the empty background around the portrait.
    # Each one gets its own x so wide glyphs (π, Σ, ²) cannot shift the others.
    out.append(f'<text font-size="{ART_FONT}px">')
    first = True
    for i, l in enumerate(eqs):
        for m in re.finditer(r"\S+(?: \S+)*", l):
            cls = "eq hero" if first else "eq"
            first = False
            out.append(f'<tspan class="{cls}" x="{20 + m.start() * ART_CW:.1f}" '
                       f'y="{art_top + i * ART_LH}">{html.escape(m.group())}</tspan>')
    out.append("</text>")
    out.append(f'<text x="{info_x:.0f}" y="30" fill="{t["fg"]}">')
    for i, spans in enumerate(lines):
        y = 30 + i * LH
        parts = "".join(f'<tspan class="{c}">{html.escape(s)}</tspan>' for c, s in spans)
        out.append(f'<tspan x="{info_x:.0f}" y="{y}">{parts}</tspan>')
    out.append("</text></svg>")
    return "\n".join(out)


def main():
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=2))).date()  # Cairo
    s = stats()
    print(s)
    lines = info_lines(s, today)
    read = lambda f: (ROOT / f).read_text().rstrip("\n").split("\n")
    eqs = read("ascii-eq.txt")
    for theme, art_file in (("dark", "ascii-art.txt"), ("light", "ascii-art-light.txt")):
        (ROOT / f"{theme}_mode.svg").write_text(render(theme, read(art_file), eqs, lines))


if __name__ == "__main__":
    main()
