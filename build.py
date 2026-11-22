#!/usr/bin/env python3
"""Build the listing catalog for www.stampedestamps.store (standard library only).

Douglas, 2026-10-11: one catalog on the store's own domain, NO prices, photos where we have scans, checkout on eBay.
Inputs (written on OMEN every morning from the eBay export; never edited by hand):
  data/listings.csv        item,title,category,condition,format,ebay_url,ends   (public-safe: no prices, no SKUs;
                           ends = the listing's end time in UTC, used only to pick and order "ending soonest")
  data/catalog_images.csv  item,image                                       (web-sized scans; items without one get a text card)
  data/feed_meta.json      {"source", "as_of", "listings"}
  data/sale_items.csv      item,sale_ends   (the one hand-kept input: items marked down on eBay and the markdown's
                           last day; the export carries no markdown signal. Header only = no markdown running.)
  data/issue_facts.csv     scott,issue,purpose,issued,first_day,face,color,design,inscribed,designer,printing,perf,format,
                           quantity,source,source_url      (hand-checked; changed only by a pull request)
  data/issue_titles.csv    title,scotts      (rebuilt every morning from listings.csv: see ISSUE_FIELDS below)
  data/moved_items.csv     item,title,removed   (every item page that has left the catalog; this build adds the ones
                           that left today. A row is never edited by hand: see MOVED_CSV below)
Outputs (committed; GitHub Pages and later AWS Amplify serve them as plain files):
  catalog/index.html, catalog/item/<item>.html, the moved pages catalog/item/<old item>.html (see MOVED_CSV),
  the issue pages catalog/issue/index.html and catalog/issue/scott-<number>.html (one per checked Scott number, listed
  or not: see ISSUE_DIR),
  sitemap.xml, robots.txt, and the marker blocks inside the hand-written pages: Shop's category list, Home's category
  sheet and Newly listed, the Sale page, About's "What you'll find" and Feedback's "Fresh on the shelf".

The catalog build is deterministic: the same inputs give byte-identical output (tests/test_build.py checks it). The Sale
page also depends on the clock (auctions that have ended drop off); STORE_BUILD_NOW=2026-10-16T19:00:00Z pins it.

    python build.py
"""
import csv, datetime, html, json, os, re, shutil, sys
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = "https://www.stampedestamps.store"
STORE = "https://www.ebay.com/str/stampedeusa"
PAGES = ["index.html", "shop.html", "sale.html", "about.html", "feedback.html"]
# Hand-written pages the sitemap lists besides PAGES (the build does not rewrite them). Claude 2026-11-15: the Rhyme Book
# page went live that evening and was in no sitemap.
SITEMAP_EXTRA = ["rhyme-book.html"]
# Relisted stamps keep their address working (Claude 2026-11-15, after the Search Console page-indexing report). eBay gives
# a relisted stamp a NEW item number, so the old catalog address went 404 while Google still listed it: on 11-15, 114 of
# the 223 item pages Google had indexed were ended listings, and 87 of those were the same title live again under one
# new number. data/moved_items.csv keeps every item page that has left the catalog (item, title, removed = the feed date
# it left). Each build, an old address whose title matches exactly ONE live listing gets a small page that sends the
# visitor on at once: an instant meta refresh, which Google reads as a permanent redirect and whose target it treats as
# the page to show (developers.google.com/search/docs/crawling-indexing/301-redirects; GitHub Pages has no server-side
# redirects). No match, or two or more: no page, so the address stays 404. Moved pages carry no visitor counter (one
# arrival would count twice) and are not in the sitemap.
MOVED_CSV = os.path.join("data", "moved_items.csv")
MOVED_MARK = '<meta name="stampede-moved-to"'
FORMAT = {"FIXED_PRICE": "Buy It Now", "AUCTION": "Auction"}
RELATED = 6
# W2 (Claude 2026-10-17): cards show a small copy of each photo, made by the private photo step on OMEN (Pillow; this
# build stays standard-library). The photo step turns itself on only when it finds this exact line, so keep its form.
THUMB_DIR = "images/thumbs"
# C2 (Claude 2026-10-24): the small copies fit this box. Cards are drawn about 115-345 CSS px wide, so 480 x 320 covers a
# 2.6x phone screen for the usual 140 px card. The photo step reads this exact line too (default 720 x 480 without it).
THUMB_BOX = (480, 320)
# C2c (Claude 2026-10-24): a 1x screen (most desktop monitors at 100%) draws a card at most 232 x 160 CSS px, a quarter
# of the THUMB_BOX copy's pixels, so cards also offer a copy that fits this box (srcset "1x"); phones and sharp screens
# keep the THUMB_BOX copy ("2x"). The private photo step makes these copies (10 KB or less) only when it finds both lines.
THUMB_1X_DIR = "images/thumbs1x"
THUMB_1X_BOX = (240, 160)
# C2b (Claude 2026-10-24): the full photo (item pages; also Stamp Stories' share image) fits this box. The item page draws
# it at most 346 CSS px wide on phones (1,038 screen px at 3x) and 1,434 screen px tall on a 2x tablet. The private photo
# step reads this exact line (1600 x 1600 without it) and keeps every full photo at 250,000 bytes or less.
PHOTO_BOX = (1040, 1440)
UTC = datetime.timezone.utc
# Cloudflare Web Analytics, the store's one visitor counter: rule 8 of the project instructions allows this one
# beacon and nothing else (Douglas, Fri 2026-11-06: "Yes, add Cloudflare"). Free, sets no cookies; the token is public
# by design (it is printed in every page) and counts only on stampedestamps.store. Empty = no counter on the pages
# this build writes. The five hand-written pages carry the same line just before </body>, and
# tests/test_build.py checks that every page has it exactly once, with this token.
CF_BEACON_TOKEN = "e80dcff4fc884bdcb8b52739a867f06a"
CF_BEACON = (f"<script type='module' src='https://static.cloudflareinsights.com/beacon.min.js' "
             f"data-cf-beacon='{{\"token\": \"{CF_BEACON_TOKEN}\"}}'></script>\n") if CF_BEACON_TOKEN else ""


def esc(s):
    return html.escape(s, quote=True)


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def load():
    rows = read_csv(os.path.join(HERE, "data", "listings.csv"))
    rows.sort(key=lambda r: r["item"])
    imgs = {r["item"]: r["image"] for r in read_csv(os.path.join(HERE, "data", "catalog_images.csv"))
            if os.path.exists(os.path.join(HERE, r["image"]))}
    with open(os.path.join(HERE, "data", "feed_meta.json"), encoding="utf-8") as f:
        meta = json.load(f)
    return rows, imgs, meta


def load_sale():
    path = os.path.join(HERE, "data", "sale_items.csv")
    return read_csv(path) if os.path.exists(path) else []


# "About this issue" on item pages (Douglas 2026-10-17: "Checked facts only"). The facts were first found in the
# ChatGPT condition reports filed with our scans, then each one was checked against a Scott catalog page (source
# "Scott catalog") or USPS pages (source "web", links in source_url: about.usps.com, stampsforever.com or the Postal
# Bulletin archive) before it was written into data/issue_facts.csv. Douglas 2026-10-18: "the Scott catalog or USPS",
# so never a dealer's site. Never a condition grade and never a dollar sign: a dollar face value is spelled out in words.
# data/issue_titles.csv maps an exact listing title to the Scott number(s) it names. It is keyed by title, not item
# number, so an auction that relists under a new item number keeps its facts. Since 2026-10-23 the private 3:45 AM
# refresh on OMEN rebuilds it every morning from that day's listings.csv, so a retitled or new listing gets its box at
# the next refresh: a title maps only to Scott numbers that already have a checked row in issue_facts.csv, and a title
# whose year, face value, color or portrait disagrees with its row gets none. Change issue_facts.csv (by a pull request),
# never issue_titles.csv: the next refresh would overwrite a hand edit.
ISSUE_FIELDS = [("issue", "Issue"), ("purpose", "Why issued"), ("issued", "Issued"), ("first_day", "First day"),
                ("face", "Face value"), ("color", "Color"), ("design", "Design"), ("inscribed", "Inscribed"),
                ("designer", "Designed by"), ("printing", "Printing"), ("perf", "Perforation"), ("format", "Format"),
                ("quantity", "Quantity")]


def load_issues():
    """({scott: checked facts}, {exact listing title: [scott, ...]}); empty when the files are missing."""
    d = os.path.join(HERE, "data")
    facts = {r["scott"]: r for r in read_csv(os.path.join(d, "issue_facts.csv"))} \
        if os.path.exists(os.path.join(d, "issue_facts.csv")) else {}
    titles = {r["title"]: r["scotts"].split() for r in read_csv(os.path.join(d, "issue_titles.csv"))} \
        if os.path.exists(os.path.join(d, "issue_titles.csv")) else {}
    return facts, titles


# Issue pages (Claude 2026-11-21): one page per checked Scott number at /catalog/issue/scott-<number>.html, built from
# data/issue_facts.csv alone, so its address stays for months whether or not anything is listed, and every morning's
# build links it to whichever listings are live. Why: on 2026-11-15 Google had indexed all 6 stable pages but only 109
# of the 440 live item pages, and an item page's address ends when eBay gives a relisted stamp a new item number. These
# pages are a TEST of the claim that stable pages get indexed and stay indexed (not proven: read Search Console's page
# lists against the 2026-11-15 baseline). The page builders are further down (build_issue, build_issue_index).
# A row gets a page only when its key is a well-formed Scott number: a capital prefix of up to four letters, a number
# that does not start with 0, at most one letter after it. "R024C" (a listing title's slip) gets none.
ISSUE_DIR = "issue"
SCOTT_KEY = re.compile(r"([A-Z]{0,4})([1-9]\d{0,3})([A-Z]?)")
# The Scott catalog writes a letter after the number small (a pane, block or variety: 1368 "a. Block of 4") unless the
# number is a major number of its own. Each number here was read off the catalog page: 1338F on 2026-11-21. A new row
# for a major number with a capital letter is added here in the same pull request.
SCOTT_CAPITAL = {"1338F"}


def scott_parts(key):
    """("", 1016, "") / ("C", 3, "") / ("", 1830, "A") for a well-formed Scott number as issue_facts.csv keys it; else None."""
    m = SCOTT_KEY.fullmatch(key) if isinstance(key, str) else None
    return (m.group(1), int(m.group(2)), m.group(3)) if m else None


def scott_text(key):
    """The number as the Scott catalog writes it (1830a, R5c, 1338F); a key that is not a Scott number stays as it is."""
    p = scott_parts(key)
    return key if not p or key in SCOTT_CAPITAL else f"{p[0]}{p[1]}{p[2].lower()}"


def issue_url(key):
    """The issue page's address, /catalog/issue/scott-1016.html; None for a key that gets no page."""
    return f"/catalog/{ISSUE_DIR}/scott-{key.lower()}.html" if scott_parts(key) else None


def issue_keys(facts):
    """Every Scott number that gets a page, in catalog order: plain numbers by size, then the lettered prefixes."""
    def order(key):
        prefix, number, suffix = scott_parts(key)
        return (prefix != "", prefix, number, suffix)
    return sorted((k for k in facts if scott_parts(k)), key=order)


def issue_shelf(rows, issues):
    """{Scott number: its live listings, newest first}. A listing belongs to every number its exact title is mapped to,
    and to none unless all of them have checked facts (issue_box's rule: a lot is never described in part)."""
    facts, titles = issues
    shelf = {}
    for r in sorted(rows, key=lambda r: r["item"], reverse=True):
        scotts = titles.get(r["title"]) or []
        if all(s in facts for s in scotts):
            for s in dict.fromkeys(scotts):
                shelf.setdefault(s, []).append(r)
    return shelf


def issue_sources(found, tail=" The eBay listing shows this item's own photos and condition."):
    """The line under the facts: which authority checked them, with a link to each web source. `tail` is the closing
    sentence about the listing (an issue page with several listings, or none, words it differently)."""
    links = []
    for f in found:
        for u in (f.get("source_url") or "").split():
            if u.startswith(("https://", "http://")) and u not in links:   # the Postal Bulletin archive is http only
                links.append(u)
    said = []
    if any(f.get("source") == "Scott catalog" for f in found):
        said.append("Checked against the Scott catalog.")
    hosts = {}
    for u in links:                                 # one name per site; several pages of one site are numbered
        hosts.setdefault(urlparse(u).netloc.removeprefix("www."), []).append(u)
    if hosts:
        said.append("Checked against " + ", ".join(
            f'<a href="{esc(us[0])}">{esc(h)}</a>' if len(us) == 1 else
            f'{esc(h)} (' + ", ".join(f'<a href="{esc(u)}">{i}</a>' for i, u in enumerate(us, 1)) + ")"
            for h, us in hosts.items()) + ".")
    return " ".join(said) + tail


def issue_box(title, issues):
    """The item page's "About this issue" section, or "" when the title is not mapped or any of its Scott numbers has
    no checked facts (a lot is never described in part). One number: the full fact list. Several: one line each.
    Each number links to its issue page (issue_url), where every listing of that issue is shown."""
    facts, titles = issues
    scotts = titles.get(title)
    if not scotts or any(s not in facts for s in scotts):
        return ""
    found = [facts[s] for s in scotts]
    li = lambda label, text: f"      <li><b>{label}</b><span>{esc(text)}</span></li>\n"
    num = lambda s: (f'<a href="{issue_url(s)}">Scott #{esc(scott_text(s))}</a>' if issue_url(s)
                     else f"Scott #{esc(scott_text(s))}")
    more = ""
    if len(found) == 1:
        h2 = "About this issue"
        lis = "".join(li(label, found[0][k]) for k, label in ISSUE_FIELDS if found[0].get(k))
        if issue_url(scotts[0]):
            more = (f'    <p class="note">Every listing of this issue: <a href="{issue_url(scotts[0])}">'
                    f'Scott #{esc(scott_text(scotts[0]))}, {esc(found[0]["issue"])}</a>.</p>\n')
    elif len({f["issue"] for f in found}) == 1 and len({f["issued"] for f in found}) == 1:
        h2 = "About this issue"                     # one issue, several designs (a se-tenant block or sheet)
        lis = li("Issue", found[0]["issue"]) + li("Issued", found[0]["issued"]) + "".join(
            li(num(s), ", ".join(v for v in (f.get("design"), f["face"]) if v)) for s, f in zip(scotts, found))
    else:
        h2 = "About these issues"
        lis = "".join(li(num(s), ", ".join(v for v in (f["issue"], f["issued"], f["face"]) if v))
                      for s, f in zip(scotts, found))
    return (f'  <section class="issue" aria-labelledby="issue-h">\n    <h2 id="issue-h">{h2}</h2>\n'
            f'    <ul class="facts">\n{lis}    </ul>\n    <p class="note">{issue_sources(found)}</p>\n{more}  </section>\n')


def parse_utc(s):
    """'2026-10-22T22:28:27Z' -> an aware datetime; None if blank or unreadable."""
    try:
        return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


def build_now():
    pinned = os.environ.get("STORE_BUILD_NOW")
    if not pinned:
        return datetime.datetime.now(UTC).replace(microsecond=0)
    now = parse_utc(pinned)
    if now is None:
        sys.exit(f"STORE_BUILD_NOW must look like 2026-10-16T19:00:00Z, not {pinned!r}")
    return now


# The kinds of stock on About's "What you'll find" (Douglas 2026-10-16, "Polish Sale, About and Feedback"). Each tile
# opens the catalog filtered to its kind (catalog/?kind=<slug>). A kind is a test on a listing's eBay category and/or
# title, and a listing can be more than one kind (a plate block is also a sheet). Country categories are listed by
# name, so a country new to the shop is left out of "Worldwide" until it is added here, never wrongly put in.
PLATE = re.compile(r"(?i)\bplate (?:block|number|no\b|#)")
SINGLE = re.compile(r"(?i)\bsingle\b")
NOT_SINGLE = re.compile(r"(?i)\b(?:covers?|fdcs?|albums?|panes?|sheets?|lots?|sets?|blocks?|pairs?|strips?|booklets?|folder|binder|selvage)\b")
COVER = re.compile(r"(?i)(?<!album )\bcovers?\b|\bfdcs?\b")
STATIONERY = re.compile(r"(?i)\b(?:postal cards?|aerogrammes?|stationery)\b")
ALBUM = re.compile(r"(?i)\b(?:albums?|collection)\b")
WORLDWIDE = re.compile(r"(?i)\bworldwide\b")
US_SINGLE_CATS = {"19th Century: Used", "19th Century: Unused", "1901-40: Used", "1901-40: Unused", "1901-Now: Used",
                  "Air Mail"}
COVER_CATS = {"Postal History", "Other US Event Stamp Covers", "First Flight", "Covers"}
COUNTRIES = {"Africa", "Argentina", "Asia", "Australia", "Australia & Oceania", "Austria", "Belgium", "Brazil", "Canada",
             "Caribbean", "China", "Cuba", "Czechoslovakia", "Denmark", "Egypt", "Europe", "Finland", "France & Colonies",
             "Germany & Colonies", "Great Britain", "Greece", "Hong Kong", "Hungary", "Iceland", "India (until 1947)",
             "Ireland", "Israel", "Italy", "Japan", "Korea", "Latin America", "Liechtenstein", "Mexico", "Middle East",
             "Monaco", "Netherlands", "New Zealand", "Norway", "Panama", "Philippines", "Poland", "Portugal", "Russia",
             "San Marino", "South Africa", "Spain", "Sweden", "Switzerland", "Tanzania", "Turkey", "Ukraine",
             "United Nations", "Vatican", "Victoria"}
KINDS = [
    ("sheets", "Full sheets and panes", lambda r: r["category"] in {"Sheets", "Booklets"}),
    ("plate-blocks", "Plate blocks and plate numbers", lambda r: bool(PLATE.search(r["title"]))),
    ("us-singles", "U.S. singles", lambda r: r["category"] in US_SINGLE_CATS
     and bool(SINGLE.search(r["title"]) or not NOT_SINGLE.search(r["title"]))),
    ("covers", "Covers and postal history", lambda r: r["category"] in COVER_CATS or bool(COVER.search(r["title"]))),
    ("revenues", "Revenues and back-of-book",
     lambda r: r["category"] in {"Revenues", "Other US Back of Book Stamps", "Duck Stamps"}),
    ("stationery", "Postal cards and stationery",
     lambda r: r["category"] == "Postal Cards & Stationery" or bool(STATIONERY.search(r["title"]))),
    ("lots", "Collections, lots and albums", lambda r: r["category"] == "Collections, Lots" or bool(ALBUM.search(r["title"]))),
    ("worldwide", "Worldwide stamps", lambda r: r["category"] in COUNTRIES or bool(WORLDWIDE.search(r["title"]))),
]


def kinds_of(group):
    return [k for k, _, test in KINDS if any(test(r) for r in group)]


def head(title, desc, canonical, image):
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(desc)}">
<link rel="canonical" href="{esc(canonical)}">
<meta property="og:site_name" content="StampedeUSA">
<meta property="og:type" content="website">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(desc)}">
<meta property="og:url" content="{esc(canonical)}">
<meta property="og:image" content="{esc(SITE + '/' + image)}">
<meta name="twitter:card" content="summary_large_image">
<meta name="theme-color" content="#4B006E">
<link rel="icon" href="/images/icon-32.png" type="image/png" sizes="32x32">
<link rel="apple-touch-icon" href="/images/apple-touch-icon.png">
<link rel="preload" href="/fonts/league-gothic-latin-400-normal.woff2" as="font" type="font/woff2" crossorigin>
<link rel="stylesheet" href="/assets/catalog.css">
</head>
<body>
<a class="skip" href="#main">Skip to the stamps</a>
<header class="selvage">
  <div class="top">
    <a class="mark" href="/index.html">STAMPede USA<small>stamps &amp; postal history</small></a>
    <nav class="menu" aria-label="Main menu">
      <ul>
        <li><a href="/index.html">Home</a></li>
        <li><a href="/shop.html">Shop</a></li>
        <li><a href="/catalog/" aria-current="page">Catalog</a></li>
        <li><a href="/sale.html">Sale</a></li>
        <li><a href="/about.html">About</a></li>
        <li><a href="/feedback.html">Feedback</a></li>
        <li><a href="/rhyme-book.html">Free Rhyme Book</a></li>
      </ul>
    </nav>
    <form class="hfind" action="/catalog/" method="get" role="search" aria-label="Site search">
      <input id="hq" name="q" type="search" placeholder="Search the catalog" aria-label="Search the catalog" autocomplete="off">
      <button type="submit">Search</button>
    </form>
  </div>
</header>
<main id="main">
"""


FOOT = f"""</main>
<footer class="selvage">
  <div class="foot">
    <p class="sign">- StampedeUSA</p>
    <p><a href="{STORE}" target="stampede_ebay">Visit the StampedeUSA store on eBay</a></p>
    <p><a href="https://stampedestamps.github.io/stampede-stories/index.html">Stamp Stories</a></p>
    <p><a href="https://www.facebook.com/StampedeStamps" target="_blank">Facebook</a></p>
    <p class="tags">#StampedeUSA #stamp #StampCollecting #Philately #USStamps #Stamps #Philatelist</p>
  </div>
</footer>
"""


SCOTT = re.compile(r"Scott #\s*([A-Za-z]{0,3}\d+[A-Za-z]?)")
SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def image_size(path):
    """(width, height) from a JPEG's own frame header, standard library only; None if it can't be read.
    The item page's main photo carries these so the browser keeps its space before the photo arrives (W10, 2026-10-17)."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    if data[:2] != b"\xff\xd8":
        return None
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker == 0xFF:                                   # fill byte before a marker
            i += 1
            continue
        if marker == 0x01 or 0xD0 <= marker <= 0xD8:         # markers with no length field
            i += 2
            continue
        if marker in SOF:
            h, w = int.from_bytes(data[i + 5:i + 7], "big"), int.from_bytes(data[i + 7:i + 9], "big")
            return (w, h) if w and h else None
        i += 2 + int.from_bytes(data[i + 2:i + 4], "big")
    return None


def card_img(item, imgs):
    """The photo a card shows: the small copy in THUMB_DIR when one exists that is no older than the photo (so a card
    never shows an older picture than the item page), else the full photo (W2, 2026-10-17)."""
    full = imgs[item]
    small = f"{THUMB_DIR}/{os.path.basename(full)}"
    f, s = os.path.join(HERE, *full.split("/")), os.path.join(HERE, *small.split("/"))
    if os.path.exists(s) and os.path.exists(f) and os.path.getmtime(s) >= os.path.getmtime(f):
        return small
    return full


def card_srcset(item, imgs, lead=""):
    """C2c: ' srcset="<1x copy> 1x, <card photo> 2x"' when the 1x copy in THUMB_1X_DIR exists and is no older than the
    photo (the card_img rule), else "" so the card shows card_img alone. lead is "/" on catalog pages, "" on root pages."""
    full = imgs[item]
    one = f"{THUMB_1X_DIR}/{os.path.basename(full)}"
    f, s = os.path.join(HERE, *full.split("/")), os.path.join(HERE, *one.split("/"))
    if os.path.exists(s) and os.path.exists(f) and os.path.getmtime(s) >= os.path.getmtime(f):
        return f' srcset="{esc(lead + one)} 1x, {esc(lead + card_img(item, imgs))} 2x"'
    return ""


def card_srcset_w(item, imgs, sizes):
    """C2d (2026-11-04): card_srcset, but each copy is described by its real width in pixels ("240w") and sizes(w, h)
    (given the card photo's pixel size) says how wide the photo is drawn, so a phone takes the 1x copy whenever that copy
    is sharp enough, not only on 1x screens. Falls back to card_srcset's "1x, 2x" form when a width can't be read or the
    1x copy is not narrower than the card photo; "" exactly when card_srcset is ""."""
    plain = card_srcset(item, imgs)
    if not plain:
        return ""
    one, two = f"{THUMB_1X_DIR}/{os.path.basename(imgs[item])}", card_img(item, imgs)
    w1, w2 = (image_size(os.path.join(HERE, *p.split("/"))) for p in (one, two))
    if not w1 or not w2 or w1[0] >= w2[0]:
        return plain
    return f' srcset="{esc(one)} {w1[0]}w, {esc(two)} {w2[0]}w" sizes="{esc(sizes(*w2))}"'


def picture(item, imgs, title, category, cls, main=False):
    """The scan when we have one; otherwise a text card naming the stamp from its own title (its first Scott number),
    or its category - never anything the title doesn't say. main=True is the item page's own photo: it loads at once
    (it is the biggest thing on the page) and carries its width and height so nothing jumps when it arrives (W10)."""
    if item in imgs and main:
        size = image_size(os.path.join(HERE, imgs[item]))
        dims = f' width="{size[0]}" height="{size[1]}"' if size else ""
        return f'<span class="{cls[0]}"><img src="/{esc(imgs[item])}" alt="{esc(title)}"{dims} fetchpriority="high"></span>'
    if item in imgs:
        return f'<span class="{cls[0]}"><img src="/{esc(card_img(item, imgs))}"{card_srcset(item, imgs, "/")} alt="{esc(title)}" loading="lazy"></span>'
    m = SCOTT.search(title)
    big = f"Scott #{m.group(1)}" if m else category
    return f'<span class="{cls[1]}"><span>{esc(big)}<small>Photos on eBay</small></span></span>'


def card(group, imgs):
    """One index card. A group is 1+ listings with the identical title (shown once, never called the same item)."""
    r = group[0]
    item, title = r["item"], r["title"]
    fmt = FORMAT.get(r["format"], r["format"])
    cats = r["category"]
    fmts = " ".join(sorted({FORMAT.get(g["format"], g["format"]) for g in group}))
    more = ""
    if len(group) > 1:
        links = ", ".join(f'<a href="/catalog/item/{esc(g["item"])}.html">Listing {i}</a>' for i, g in enumerate(group, 1))
        more = f'<span class="more">{len(group)} listings with this title: {links}</span>'
    auctions = sum(1 for g in group if g["format"] == "AUCTION")   # the counter counts listings, not cards (W13)
    return (f'<li class="edged" data-t="{esc(title.lower())}" data-c="{esc(slug(cats))}" data-f="{esc(fmts)}" '
            f'data-n="{len(group)}" data-na="{auctions}" '
            f'data-k="{" ".join(kinds_of(group))}">'
            f'<a class="card perf" href="/catalog/item/{esc(item)}.html">'
            f'{picture(item, imgs, title, cats, ("ph", "tx"))}'
            f'<span class="badge">{esc(fmt)}</span><span class="t">{esc(title)}</span>'
            f'<span class="c">{esc(cats)}</span></a>{more}</li>')


SCRIPT = """<script>
(function () {
  var q = document.getElementById('q'), c = document.getElementById('cat'), f = document.getElementById('fmt');
  var items = Array.prototype.slice.call(document.querySelectorAll('#cards > li'));
  var count = document.getElementById('count'), empty = document.getElementById('empty');
  var total = items.reduce(function (s, li) { return s + +li.dataset.n; }, 0);   // listings, not cards (W13)
  var params = new URLSearchParams(location.search);
  if (params.get('cat')) c.value = params.get('cat');
  if (params.get('kind')) c.value = 'kind:' + params.get('kind');
  if (params.get('fmt')) f.value = params.get('fmt');
  if (params.get('q')) q.value = params.get('q');
  if (c.selectedIndex < 0) c.selectedIndex = 0;   // a link naming no known category or kind shows everything
  if (f.selectedIndex < 0) f.selectedIndex = 0;
  function inCat(li) {
    if (!c.value) return true;
    if (c.value.indexOf('kind:') === 0) return (' ' + li.dataset.k + ' ').indexOf(' ' + c.value.slice(5) + ' ') !== -1;
    return li.dataset.c === c.value;
  }
  function run() {
    var words = q.value.toLowerCase().split(/\\s+/).filter(Boolean), shown = 0;
    items.forEach(function (li) {
      var ok = inCat(li) && (!f.value || li.dataset.f.indexOf(f.value) !== -1) &&
               words.every(function (w) { return li.dataset.t.indexOf(w) !== -1; });
      li.hidden = !ok;
      if (ok) shown += f.value === 'Auction' ? +li.dataset.na : f.value ? li.dataset.n - li.dataset.na : +li.dataset.n;
    });
    count.textContent = 'Showing ' + shown + ' of ' + total + ' listings';
    empty.hidden = shown !== 0;
  }
  [q, c, f].forEach(function (el) { el.addEventListener('input', run); el.addEventListener('change', run); });
  document.getElementById('clear').addEventListener('click', function () { q.value = ''; c.value = ''; f.value = ''; run(); q.focus(); });
  window.addEventListener('pageshow', run);
  run();
})();
</script>
"""


def build_index(rows, imgs, meta, numbers=0):
    """The catalog page. `numbers` is how many issue pages this build writes: with any, the "as of" line links to their
    index (never a link to a page that is not there)."""
    numbers_link = f' <a href="/catalog/{ISSUE_DIR}/">Find a stamp by Scott number</a>.' if numbers else ""
    groups = {}
    for r in rows:
        groups.setdefault(r["title"], []).append(r)
    ordered = sorted(groups.values(), key=lambda g: g[0]["item"], reverse=True)   # newest listings first
    cats = {}
    for r in rows:
        cats[r["category"]] = cats.get(r["category"], 0) + 1
    cat_opts = "".join(f'<option value="{esc(slug(k))}">{esc(k)} ({v})</option>'
                       for k, v in sorted(cats.items(), key=lambda kv: (-kv[1], kv[0])))
    kind_n = [(k, label, sum(1 for r in rows if test(r))) for k, label, test in KINDS]
    kind_opts = "".join(f'<option value="kind:{k}">{esc(label)} ({n})</option>' for k, label, n in kind_n if n)
    n = len(rows)
    out = head(f"The catalog: {n} listings | StampedeUSA",
               f"Browse all {n} StampedeUSA listings: U.S. sheets, panes, singles, covers, revenues and worldwide stamps. "
               "Every one opens on eBay.", f"{SITE}/catalog/", "images/hero.jpg").replace("<body>\n", '<body class="own-search">\n', 1)
    out += f"""  <section class="hero">
    <h1>The whole shelf, one page.</h1>
    <p class="lede">All {n} StampedeUSA listings in one place. Search by Scott number, year, country or subject, or narrow it by
      category. Every stamp opens on eBay, where the price, bids and photos are always current.</p>
    <p class="asof">Listings as of {esc(meta.get("as_of", ""))}.{numbers_link}</p>
    <form class="find" role="search" onsubmit="return false">
      <div><label for="q">Search the catalog</label><input id="q" type="search" placeholder="Try Batman, eagle, Boston or 1962" autocomplete="off"></div>
      <div><label for="cat">Category</label><select id="cat"><option value="">All categories</option><optgroup label="Kinds of stock">{kind_opts}</optgroup><optgroup label="eBay categories">{cat_opts}</optgroup></select></div>
      <div><label for="fmt">Buying format</label><select id="fmt"><option value="">Auction or Buy It Now</option>
        <option value="Auction">Auction</option><option value="Buy It Now">Buy It Now</option></select></div>
    </form>
    <p class="count" id="count" aria-live="polite">Showing {n} of {n} listings</p>
    <div class="empty" id="empty" hidden><p>No stamps match that search.</p>
      <p><button type="button" id="clear">Clear the search and filters</button>, or <a href="{STORE}" target="stampede_ebay">search the eBay store</a>.</p></div>
  </section>
  <ul class="stamps" id="cards">
"""
    out += "\n".join("    " + card(g, imgs) for g in ordered)
    out += "\n  </ul>\n" + FOOT + SCRIPT + CF_BEACON + "</body>\n</html>\n"
    return out


def build_item(r, rows_by_cat, imgs, issues=({}, {})):
    item, title = r["item"], r["title"]
    fmt = FORMAT.get(r["format"], r["format"])
    same = rows_by_cat[r["category"]]
    i = same.index(r)
    rel = [x for x in same[i + 1:] + same[:i] if x["title"] != title][:RELATED]
    image = imgs.get(item, "images/hero.jpg")
    desc = f"{title}. {r['category']}, {fmt}. See it on eBay from StampedeUSA."
    out = head(f"{title} | StampedeUSA", desc, f"{SITE}/catalog/item/{item}.html", image)
    out += f"""  <p class="crumbs"><a href="/catalog/">Catalog</a> &rsaquo; <a href="/catalog/?cat={esc(slug(r['category']))}">{esc(r['category'])}</a></p>
  <div class="item">
    <div class="item-stamp perf edged"><div class="frame">{picture(item, imgs, title, r['category'], ("ph", "tx"), main=True)}</div></div>
    <div class="buy">
      <h1>{esc(title)}</h1>
      <ul class="facts">
        <li><b>Category</b><span>{esc(r['category'])}</span></li>
{f"        <li><b>Condition</b><span>{esc(r['condition'])}</span></li>{chr(10)}" if r['condition'] else ""}        <li><b>Format</b><span>{esc(fmt)}</span></li>
      </ul>
      <a class="btn" href="{esc(r['ebay_url'])}" target="stampede_ebay">See it on eBay</a>
      <p class="note">Price, bids and every photo are on the eBay listing. <a href="{STORE}" target="stampede_ebay">Visit the full store</a>.</p>
    </div>
  </div>
"""
    out += issue_box(title, issues)
    if rel:
        out += '  <section class="related"><h2>More from ' + esc(r["category"]) + '</h2>\n  <ul class="stamps">\n'
        out += "\n".join("    " + card([x], imgs) for x in rel)
        out += "\n  </ul></section>\n"
    out += f'  <p class="note"><a href="/catalog/">Back to the full catalog</a></p>\n' + FOOT + CF_BEACON + "</body>\n</html>\n"
    return out


# Friendlier names on the Shop page for a few eBay category names (the catalog's own filter keeps eBay's names).
FRIENDLY = {"Other US Event Stamp Covers": "Event Covers", "Other US Back of Book Stamps": "Back of Book",
            "Other US Stamp Covers": "Other U.S. Covers", "Possessions": "U.S. Possessions",
            "Victoria": "Victoria (Great Britain)", "Collections, Lots": "Collections & Lots"}
SHOP_START, SHOP_END = "<!-- CATALOG-CATEGORIES:START", "<!-- CATALOG-CATEGORIES:END -->"


def update_shop(rows):
    """Rewrite the Shop page's collector-category list (between the two markers) from today's listings, so every
    link opens a non-empty catalog filter. Only that block changes; returns the number of categories written."""
    path = os.path.join(HERE, "shop.html")
    with open(path, encoding="utf-8", newline="") as f:
        text = f.read()
    a, b = text.index(SHOP_START), text.index(SHOP_END)
    head_end = text.index("-->", a) + 3
    cats = {}
    for r in rows:
        cats[r["category"]] = cats.get(r["category"], 0) + 1
    nl = "\r\n" if "\r\n" in text[:500] else "\n"
    items = "".join(f'{nl}      <li><a href="catalog/?cat={esc(slug(k))}">{esc(FRIENDLY.get(k, k))}</a></li>'
                    for k, _ in sorted(cats.items(), key=lambda kv: (-kv[1], kv[0])))
    new = text[:head_end] + items + nl + "      " + text[b:]
    if new != text:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(new)
    return len(cats)


NEW_START, NEW_END, NEW_COUNT = "<!-- NEWLY-LISTED:START", "<!-- NEWLY-LISTED:END -->", 12
SHEET_START, SHEET_END, SHEET_COUNT = "<!-- CATEGORY-SHEET:START", "<!-- CATEGORY-SHEET:END -->", 8
# C2d (Claude 2026-11-04): the sheet draws each photo in a box SHEET_BOX_H CSS px tall (.photo-pane .pic, 8.5rem) and only
# 84-231 px wide, measured at 320-2560 px windows: up to 118 px under 360 px, half the window less 76 px up to 44rem, less
# 81 px up to 62rem (two columns), a quarter less 57 px up to 72rem (four columns), then 231 px. The photo fills the box
# keeping its shape, so it is drawn min(box width, SHEET_BOX_H x width / height) wide; a 1.75x-2x phone often needs no more
# pixels than the 1x copy has. Keep sheet_sizes in step with the .pane and .photo-pane CSS (a test checks those lines and
# the measured widths).
SHEET_BOX_H = 136


def sheet_sizes(w, h):
    """C2d: the sizes list for one category-sheet photo w x h pixels: how wide it is drawn at each window width (see above);
    every value is the measured box width rounded up, or the photo's own cap, whichever is smaller."""
    cap = (SHEET_BOX_H * w + h - 1) // h
    out = [f"(min-width: 72rem) {min(231, cap)}px"]
    for media, lo, hi, per, less in (("62rem", 191, 231, 4, 57), ("44rem", 271, 415, 2, 81), ("360px", 104, 276, 2, 76)):
        if cap <= lo:
            out.append(f"(min-width: {media}) {cap}px")
            continue
        if cap < hi:
            out.append(f"(min-width: {per * (cap + less)}px) {cap}px")
        out.append(f"(min-width: {media}) calc({100 // per}vw - {less}px)")
    out.append(f"{min(118, cap)}px")
    return ", ".join(out)
ABOUT_START, ABOUT_END = "<!-- ABOUT-KINDS:START", "<!-- ABOUT-KINDS:END -->"
FRESH_START, FRESH_END, FRESH_COUNT = "<!-- FEEDBACK-FRESH:START", "<!-- FEEDBACK-FRESH:END -->", 4
SALE_HERO = ("<!-- SALE-HERO:START", "<!-- SALE-HERO:END -->")
SALE_MARKED = ("<!-- SALE-MARKDOWNS:START", "<!-- SALE-MARKDOWNS:END -->")
SALE_ENDING = ("<!-- SALE-ENDING:START", "<!-- SALE-ENDING:END -->")
# "Ending soonest": 12 cards on show. The page is rebuilt once a day, so it also carries, hidden, every auction that can
# end before the next build (26 h covers the day plus slack) and 12 more after those; the browser hides an auction once
# it ends and brings up the next. ENDING_MAX caps the page if a very large batch ends inside one day.
ENDING_SHOWN, ENDING_HOURS, ENDING_MAX = 12, 26, 120


def replace_block(path, start, end, items, indent="      "):
    """Swap the text between two marker comments in a hand-written page; only that block changes."""
    with open(path, encoding="utf-8", newline="") as f:
        text = f.read()
    a, b = text.index(start), text.index(end)
    head_end = text.index("-->", a) + 3
    nl = "\r\n" if "\r\n" in text[:500] else "\n"
    new = text[:head_end] + "".join(nl + indent + i for i in items) + nl + indent + text[b:]
    if new != text:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(new)


def photo_card(r, imgs, attrs="", tag=""):
    """A perforated card with the listing's photo, linking to its catalog page (Home, Sale and Feedback)."""
    return (f'<li class="edged"{attrs}><a class="perf" href="catalog/item/{esc(r["item"])}.html">'
            f'<span class="slot"><img src="{esc(card_img(r["item"], imgs))}"{card_srcset(r["item"], imgs)} alt="{esc(r["title"])}" loading="lazy"></span>{tag}'
            f'<span class="t">{esc(r["title"])}</span><span class="c">{esc(FRIENDLY.get(r["category"], r["category"]))}</span></a></li>')


def newest_photographed(rows, imgs, n, avoid=()):
    """The n newest listings (highest item number) that have a photo, one per title, leaving out the items in `avoid`
    and any listing that shares a title with one of them."""
    titles = {r["title"] for r in rows if r["item"] in avoid}
    picked = []
    for r in sorted(rows, key=lambda r: r["item"], reverse=True):
        if r["item"] in imgs and r["item"] not in avoid and r["title"] not in titles:
            picked.append(r)
            titles.add(r["title"])
            if len(picked) == n:
                break
    return picked


def update_home(rows, imgs):
    """Home's "Newly listed": the 12 newest listings that have a photo, one per title, each linking to its catalog
    page. Returns the item numbers chosen."""
    picked = newest_photographed(rows, imgs, NEW_COUNT)
    replace_block(os.path.join(HERE, "index.html"), NEW_START, NEW_END, [photo_card(r, imgs) for r in picked])
    return [r["item"] for r in picked]


def update_home_sheet(rows, imgs, avoid=()):
    """Home's category sheet (Douglas 2026-10-15, "Photo storefront"): the largest categories that have at least one
    photographed listing, each tile showing that category's newest photographed stamp NOT already shown in Newly
    listed (`avoid`; falls back to one that is), linking to the catalog filtered to it. Returns the categories and
    the items whose photos the tiles show."""
    counts, face, fallback = {}, {}, {}
    for r in sorted(rows, key=lambda r: r["item"], reverse=True):
        counts[r["category"]] = counts.get(r["category"], 0) + 1
        if r["item"] in imgs:
            target = fallback if r["item"] in avoid else face
            target.setdefault(r["category"], r)
    for c, r in fallback.items():
        face.setdefault(c, r)
    cats = [c for c, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])) if c in face][:SHEET_COUNT]
    items = []
    for c in cats:
        n, r = counts[c], face[c]
        items.append(f'<li><a class="tile perf" href="catalog/?cat={esc(slug(c))}">'
                     f'<span class="pic"><img src="{esc(card_img(r["item"], imgs))}"{card_srcset_w(r["item"], imgs, sheet_sizes)} alt="{esc(r["title"])}" loading="lazy"></span>'
                     f'<b>{esc(FRIENDLY.get(c, c))}</b><span>{n} {"listing" if n == 1 else "listings"}</span></a></li>')
    replace_block(os.path.join(HERE, "index.html"), SHEET_START, SHEET_END, items)
    return cats, {face[c]["item"] for c in cats}


def about_tiles(rows, imgs, avoid=()):
    """About's "What you'll find": one tile per kind of stock that has a photographed listing today, in KINDS order.
    Each shows the kind's newest photographed stamp that no other tile or Home block shows (falling back to one
    Home shows, then to any), with the kind's listing count, and opens the catalog filtered to the kind.
    Returns the tiles and the items whose photos they show."""
    newest = sorted(rows, key=lambda r: r["item"], reverse=True)
    tiles, faces = [], []
    for k, label, test in KINDS:
        mine = [r for r in newest if test(r)]
        shot = [r for r in mine if r["item"] in imgs]
        if not shot:
            continue
        face = next((r for r in shot if r["item"] not in avoid and r["item"] not in faces),
                    next((r for r in shot if r["item"] not in faces), shot[0]))
        faces.append(face["item"])
        tiles.append(f'<li><a class="tile perf" href="catalog/?kind={k}">'
                     f'<span class="pic"><img src="{esc(card_img(face["item"], imgs))}"{card_srcset(face["item"], imgs)} alt="{esc(face["title"])}" loading="lazy"></span>'
                     f'<b>{esc(label)}</b><span>{len(mine)} on the shelf</span></a></li>')
    return tiles, set(faces)


VERSE_AUCTIONS = ["Our auctions start low and the clock's always ticking,",
                  "a sheet or a cover is yours for the picking.",
                  "So place a fair bid on the stamp you adore,",
                  "and combined shipping helps you save even more! - StampedeUSA"]
VERSE_MARKED = ["Our markdowns are running on eBay today,",
                "a friendlier price for the stamps on display.",
                "So pick out a favorite, or gather a few,",
                "with combined shipping, the savings add up too! - StampedeUSA"]
VERSE_SHELF = ["The shelf is all stocked and the stamps are on view,",
               "from sheets to old covers, there's something for you.",
               "So browse every page and then pick out a few,",
               "with combined shipping, the savings add up too! - StampedeUSA"]


def sale_blocks(rows, imgs, sale_rows, now):
    """The Sale page (Douglas 2026-10-16, "Polish Sale, About and Feedback"), true whether or not a markdown runs.
    A markdown shows only while data/sale_items.csv lists the item, the item is still listed, and the markdown's last
    day (sale_ends, eBay's Pacific date) has not passed; a blank or unreadable date never shows. "Ending soonest" is
    the auctions that have not ended at build time, soonest first, one per title, each carrying its end time so the
    browser hides it once it ends and brings up a spare (see ENDING_HOURS). Returns the three blocks' lines, the
    marked-down listings and the auctions listed."""
    live = {r["item"]: r for r in rows}
    today = (now - datetime.timedelta(hours=7)).date().isoformat()   # eBay's Pacific date at the 3:45 AM ET build
    marked, seen = [], set()
    for s in sale_rows:
        r = live.get((s.get("item") or "").strip())
        if r and r["item"] not in seen and re.fullmatch(r"\d{4}-\d{2}-\d{2}", (s.get("sale_ends") or "").strip()) \
                and s["sale_ends"].strip() >= today:
            marked.append(r)
            seen.add(r["item"])
    soon, titles = [], set()
    for r in sorted(rows, key=lambda r: (r.get("ends") or "", r["item"])):
        end = parse_utc(r.get("ends") or "")
        if r["format"] == "AUCTION" and r["item"] in imgs and end and end > now and r["title"] not in titles:
            soon.append(r)
            titles.add(r["title"])
    horizon = now + datetime.timedelta(hours=ENDING_HOURS)
    first_day = sum(1 for r in soon if parse_utc(r["ends"]) <= horizon)
    soon = soon[:min(max(first_day, ENDING_SHOWN) + ENDING_SHOWN, ENDING_MAX)]

    def verse(lines):
        return ['  <p class="verse">' + lines[0] + "<br>"] + [f"    {x}<br>" for x in lines[1:-1]] + [f"    {lines[-1]}</p>"]

    sale_tab = f'<a href="{STORE}?_tab=sales" target="stampede_ebay">eBay Sale tab</a>'
    if marked:
        n = len(marked)
        hero = (["<div>", "  <h1>Marked down, still marvelous.</h1>",
                 f'  <p class="lede">{n} {"stamp is" if n == 1 else "stamps are"} marked down on eBay right now. Same stamps, '
                 "same photos, a little friendlier on the wallet. Each listing shows its sale price on eBay.</p>"]
                + verse(VERSE_MARKED)
                + [f'  <a class="btn" href="{STORE}?_tab=sales" target="stampede_ebay">See every sale item</a>', "</div>",
                   '<div class="sticker perf edged" aria-hidden="true">',
                   '  <div class="frame"><b>On sale</b><span>Savings shown on every listing</span></div>', "</div>"])
    elif soon:
        hero = (["<div>", "  <h1>Starting low, ending soon.</h1>",
                 '  <p class="lede">Every StampedeUSA auction opens with a low starting bid, and auctions end every week. '
                 f"Here are the ones ending soonest. Markdowns, when we run them, are on our {sale_tab}.</p>"]
                + verse(VERSE_AUCTIONS)
                + ['  <a class="btn" href="catalog/?fmt=Auction">See every auction</a>', "</div>",
                   '<div class="sticker perf edged" aria-hidden="true">',
                   '  <div class="frame"><b>Starts low</b><span>Auctions end every week</span></div>', "</div>"])
    else:
        hero = (["<div>", "  <h1>Something for every album.</h1>",
                 '  <p class="lede">No auctions are running at the moment, so every stamp in the shop is Buy It Now, with '
                 f"combined shipping on multiple items. Markdowns, when we run them, are on our {sale_tab}.</p>"]
                + verse(VERSE_SHELF)
                + ['  <a class="btn" href="catalog/">Browse the catalog</a>', "</div>",
                   '<div class="sticker perf edged" aria-hidden="true">',
                   '  <div class="frame"><b>Buy It Now</b><span>Combined shipping</span></div>', "</div>"])
    shown_marked = [r for r in marked if r["item"] in imgs]
    markdowns = []
    if shown_marked:
        markdowns = (['<section class="block" id="on-sale">',
                      f'  <h2>Marked down now <a href="{STORE}?_tab=sales" target="stampede_ebay">See all on eBay</a></h2>',
                      '  <ul class="stamps">']
                     + ["    " + photo_card(r, imgs, tag='<span class="tag">Marked down</span>') for r in shown_marked]
                     + ["  </ul>",
                        '  <p class="note">Sale prices are always current on eBay. A stamp shown here may have sold or come '
                        "off sale since this page was made; if so, the full store has plenty more.</p>", "</section>"])
    ending = []
    if soon:
        ending = (['<section class="block" id="ending">',
                   f'  <h2>Ending soonest <a href="{STORE}?_sop=1" target="stampede_ebay">See them on eBay</a></h2>',
                   f'  <ul class="stamps" id="ending-cards" data-show="{ENDING_SHOWN}">']
                  + ["    " + photo_card(r, imgs, attrs=f' data-ends="{esc(r["ends"])}"' + (" hidden" if i >= ENDING_SHOWN else ""))
                     for i, r in enumerate(soon)]
                  + ["  </ul>",
                     f'  <p class="note" id="ending-none" hidden>These auctions have ended since this page was made. '
                     f'<a href="{STORE}?LH_Auction=1" target="stampede_ebay">See the auctions running now on eBay</a>.</p>',
                     '  <p class="note">Time left and the current bid are always on eBay. Every card opens the stamp\'s own '
                     "page, one click from its listing.</p>", "</section>"])
    return {"hero": hero, "markdowns": markdowns, "ending": ending}, marked, soon


def update_sale(rows, imgs, sale_rows, now):
    blocks, marked, soon = sale_blocks(rows, imgs, sale_rows, now)
    path = os.path.join(HERE, "sale.html")
    replace_block(path, *SALE_HERO, blocks["hero"])
    replace_block(path, *SALE_MARKED, blocks["markdowns"], indent="  ")
    replace_block(path, *SALE_ENDING, blocks["ending"], indent="  ")
    return marked, soon


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


# ---- moved pages (Claude 2026-11-15; see MOVED_CSV above) ----

def title_key(t):
    """A listing title compared the way a relist repeats it: entities decoded, case and spacing ignored."""
    return re.sub(r"\s+", " ", html.unescape(t)).strip().lower()


def page_title(text):
    """The listing title an item page was built for, read from its <title>; None for a moved page or no such title."""
    if MOVED_MARK in text:
        return None
    m = re.search(r"<title>(.*?) \| StampedeUSA</title>", text, re.S)
    return html.unescape(m.group(1)) if m else None


def load_moved():
    """The record of item pages that have left the catalog, {item: row}. A row that is not whole (no item number, no
    title, no date) is left out rather than stopping the build: this record is the one thing the moved pages keep from
    day to day, and the daily catalog never waits on it. Reads either line ending (git on Windows checks the file out
    with CRLF)."""
    path = os.path.join(HERE, MOVED_CSV)
    out = {}
    for r in read_csv(path) if os.path.exists(path) else []:
        item, title, removed = (r.get("item") or "").strip(), r.get("title") or "", (r.get("removed") or "").strip()
        if item.isdigit() and title.strip() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", removed):
            out[item] = {"item": item, "title": title, "removed": removed}
    return out


def write_moved(moved):
    """Sorted by item number, LF line ends: every build writes it, so the file never depends on how git checked it out."""
    with open(os.path.join(HERE, MOVED_CSV), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["item", "title", "removed"], lineterminator="\n")
        w.writeheader()
        for k in sorted(moved):
            w.writerow({x: moved[k][x] for x in ("item", "title", "removed")})


def drop_returned(moved, live):
    """A listing that dropped out of one export and is live again is not 'moved': its row goes. Returns the items dropped."""
    back = [k for k in moved if k in live]
    for k in back:
        del moved[k]
    return back


def note_removed(moved, item_dir, live, today):
    """Before the catalog folder is rebuilt: record each item page whose listing has gone (not live, not yet recorded,
    not itself a moved page). Returns how many rows were added."""
    added = 0
    for f in sorted(os.listdir(item_dir)) if os.path.isdir(item_dir) else []:
        m = re.fullmatch(r"(\d+)\.html", f)
        if not m or m.group(1) in live or m.group(1) in moved:
            continue
        with open(os.path.join(item_dir, f), encoding="utf-8") as fh:
            t = page_title(fh.read())
        if t:
            moved[m.group(1)] = {"item": m.group(1), "title": t, "removed": today}
            added += 1
    return added


def moved_targets(moved, rows):
    """{old item: live row} for each recorded old address whose title matches exactly one live listing."""
    live = {r["item"] for r in rows}
    by_key = {}
    for r in rows:
        by_key.setdefault(title_key(r["title"]), []).append(r)
    out = {}
    for old, rec in moved.items():
        hits = by_key.get(title_key(rec["title"]), [])
        if old not in live and len(hits) == 1:
            out[old] = hits[0]
    return out


def build_moved(r):
    """The page left at an old address: sends the visitor (and Google) on to the stamp's live page at once."""
    url = f"/catalog/item/{r['item']}.html"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(r['title'])} | StampedeUSA</title>
<meta http-equiv="refresh" content="0; url={url}">
<link rel="canonical" href="{SITE}{url}">
{MOVED_MARK} content="{esc(r['item'])}">
<link rel="icon" href="/images/icon-32.png" type="image/png" sizes="32x32">
</head>
<body>
<p>Now listed here: <a href="{url}">{esc(r['title'])}</a>.</p>
</body>
</html>
"""


# ---- issue pages (Claude 2026-11-21; see ISSUE_DIR above) ----
# A page has three states and keeps its address in all of them: one listing (the item page's own layout, with the one
# filled button to the eBay listing), several listings (a card and an outlined eBay button for each; none leads), and no
# listing (the facts and the nearby issues carry the page; it is never hidden from search engines and never redirects).
# The build knows only which titles are mapped to a number, not what is in stock, so the no-listing words say "filed
# under", never that we have none. Rule 2: every fact shown is a checked field copied as written, with its source named.

def issue_listing_card(r, imgs):
    """One live listing on its issue's page: the catalog's card (it opens the listing's catalog page), then a button
    to the eBay listing itself."""
    fmt = FORMAT.get(r["format"], r["format"])
    return (f'<li><a class="card perf edged" href="/catalog/item/{esc(r["item"])}.html">'
            f'{picture(r["item"], imgs, r["title"], r["category"], ("ph", "tx"))}'
            f'<span class="badge">{esc(fmt)}</span><span class="t">{esc(r["title"])}</span>'
            f'<span class="c">{esc(r["category"])}</span></a>'
            f'<a class="btn alt" href="{esc(r["ebay_url"])}" target="stampede_ebay" '
            f'aria-label="See it on eBay: {esc(r["title"])}">See it on eBay</a></li>')


def issue_card(key, f, r, imgs):
    """Another issue as a card: the photo of its newest photographed listing, then its number, name and date. It opens
    that issue's page, so the stable pages link to each other."""
    return (f'<li class="edged"><a class="card perf" href="{issue_url(key)}">'
            f'<span class="ph"><img src="/{esc(card_img(r["item"], imgs))}"{card_srcset(r["item"], imgs, "/")} '
            f'alt="{esc(r["title"])}" loading="lazy"></span>'
            f'<span class="t">Scott #{esc(scott_text(key))}: {esc(f["issue"])}</span>'
            f'<span class="c">{esc(f["issued"])}</span></a></li>')


def build_issue(key, keys, facts, shelf, imgs, meta):
    """One issue page. `keys` is issue_keys(): every Scott number with a page, in catalog order. `shelf` is issue_shelf()."""
    f, n, i = facts[key], scott_text(key), keys.index(key)
    mine = shelf.get(key, [])
    shot = next((r for r in mine if r["item"] in imgs), None)
    out = head(f"Scott #{n}: {f['issue']}, {f['issued']} | StampedeUSA",
               f"Scott #{n}, {f['issue']}: issued {f['issued']}, face value {f['face']}. Checked facts, and our eBay "
               "listings of this issue when it is on the shelf.",
               SITE + issue_url(key), imgs[shot["item"]] if shot else "images/hero.jpg")
    out += (f'  <p class="crumbs"><a href="/catalog/">Catalog</a> &rsaquo; '
            f'<a href="/catalog/{ISSUE_DIR}/">Scott numbers</a></p>\n')
    h1 = f'      <h1>Scott #{esc(n)} <span>{esc(f["issue"])}</span></h1>\n'
    key_line = f'      <p class="key">Face value {esc(f["face"])}. Issued {esc(f["issued"])}.</p>\n'
    asof = f'      <p class="asof">Listings as of {esc(meta.get("as_of", ""))}.</p>\n'
    if len(mine) == 1:
        r = mine[0]
        page = f'/catalog/item/{esc(r["item"])}.html'
        out += (f'  <div class="item">\n    <div class="item-stamp perf edged"><div class="frame"><a href="{page}">'
                f'{picture(r["item"], imgs, r["title"], r["category"], ("ph", "tx"), main=True)}</a></div></div>\n'
                f'    <div class="buy">\n{h1}{key_line}'
                f'      <a class="btn" href="{esc(r["ebay_url"])}" target="stampede_ebay">See it on eBay</a>\n'
                f'      <p class="note">On the shelf now: <a href="{page}">{esc(r["title"])}</a>. '
                f'Price, bids and every photo are on the eBay listing.</p>\n{asof}    </div>\n  </div>\n')
        tail = " The eBay listing shows this item's own photos and condition."
    elif mine:                                  # the cards come straight under the heading; the words about them follow
        out += (f'  <div class="item solo">\n    <div class="buy">\n{h1}{key_line}    </div>\n  </div>\n'
                '  <section class="related shelf" aria-labelledby="shelf-h"><h2 id="shelf-h">On the shelf now</h2>\n'
                '  <ul class="stamps">\n' + "\n".join("    " + issue_listing_card(r, imgs) for r in mine)
                + f'\n  </ul>\n      <p class="note">{len(mine)} listings of this issue are on the shelf. Each opens on '
                f'eBay, where the price, bids and every photo are current.</p>\n{asof}  </section>\n')
        tail = " Each eBay listing shows that item's own photos and condition."
    else:
        out += (f'  <div class="item solo">\n    <div class="buy">\n{h1}{key_line}'
                '      <a class="btn" href="/catalog/">Browse the full catalog</a>\n'
                '      <p class="note">No listing is filed under this issue today. This page is rebuilt every morning '
                f'from our eBay listings.</p>\n{asof}    </div>\n  </div>\n')
        tail = ""
    li = lambda label, text: f"      <li><b>{label}</b><span>{esc(text)}</span></li>\n"
    out += ('  <section class="issue" aria-labelledby="issue-h">\n    <h2 id="issue-h">About this issue</h2>\n'
            '    <ul class="facts">\n' + "".join(li(label, f[k]) for k, label in ISSUE_FIELDS if f.get(k))
            + f'    </ul>\n    <p class="note">{issue_sources([f], tail)}</p>\n  </section>\n')
    # the other numbers of the same issue (issue_box's rule: the same name and the same date), each with its own page
    also = [k for k in keys if k != key and facts[k]["issue"] == f["issue"] and facts[k]["issued"] == f["issued"]]
    if also:
        out += ('  <section class="issue" aria-labelledby="also-h">\n    <h2 id="also-h">Also in this issue</h2>\n'
                '    <ul class="facts">\n'
                + "".join(li(f'<a href="{issue_url(k)}">Scott #{esc(scott_text(k))}</a>',
                             ", ".join(v for v in (facts[k].get("design"), facts[k]["face"]) if v)) for k in also)
                + f'    </ul>\n    <p class="note">{issue_sources([facts[k] for k in also], "")}</p>\n  </section>\n')
    # the nearest other numbers that have a photographed listing, working outwards from this one; never a number already
    # named above, and never the same listing twice (a lot is filed under every number it names)
    near, seen = [], {r["item"] for r in mine}
    for d in range(1, len(keys)):
        if len(near) >= RELATED:
            break
        for j in (i + d, i - d):
            k = keys[j] if 0 <= j < len(keys) else None
            r = next((r for r in shelf.get(k, []) if r["item"] in imgs), None) if k and k not in also else None
            if r and r["item"] not in seen and len(near) < RELATED:
                near.append((j, k, r))
                seen.add(r["item"])
    if near:
        out += ('  <section class="related" aria-labelledby="more-h"><h2 id="more-h">More issues on the shelf</h2>\n'
                '  <ul class="stamps">\n' + "\n".join("    " + issue_card(k, facts[k], r, imgs) for _, k, r in sorted(near))
                + '\n  </ul>\n  <p class="note">Names and dates checked against the Scott catalog or USPS; each '
                "issue's page names its source.</p></section>\n")
    before = (f'<a href="{issue_url(keys[i - 1])}" rel="prev">Scott #{esc(scott_text(keys[i - 1]))}</a>' if i else "")
    after = (f'<a href="{issue_url(keys[i + 1])}" rel="next">Scott #{esc(scott_text(keys[i + 1]))}</a>'
             if i + 1 < len(keys) else "")
    out += (f'  <nav class="pager" aria-label="More Scott numbers">{before}<a href="/catalog/{ISSUE_DIR}/">'
            f'All Scott numbers</a>{after}</nav>\n'
            '  <p class="note"><a href="/catalog/">Back to the full catalog</a></p>\n' + FOOT + CF_BEACON + "</body>\n</html>\n")
    return out


def build_issue_index(keys, facts, shelf, meta):
    """/catalog/issue/: every issue page in catalog order, in sections (plain numbers by the thousand, then one section
    per lettered prefix), each row with the number, the issue's name, date and face value and today's listing count."""
    n, listed = len(keys), sum(1 for k in keys if shelf.get(k))
    out = head(f"Stamps by Scott number: {n} checked issues | StampedeUSA",
               f"Find a stamp by Scott number: {n} issues with facts checked against the Scott catalog or USPS, and "
               "StampedeUSA's listings of each. Every listing opens on eBay.",
               f"{SITE}/catalog/{ISSUE_DIR}/", "images/hero.jpg")
    groups = {}
    for k in keys:
        prefix, number, _ = scott_parts(k)
        groups.setdefault((prefix, 0 if prefix else number // 1000), []).append(k)
    parts = [(f"n{g}", ks, f"#{scott_text(ks[0])}" + (f" to #{scott_text(ks[-1])}" if len(ks) > 1 else ""))
             for g, ks in enumerate(groups.values(), 1)]
    out += f"""  <section class="hero">
    <p class="crumbs"><a href="/catalog/">Catalog</a> &rsaquo; Scott numbers</p>
    <h1>Find it by Scott number.</h1>
    <p class="lede">{n} stamp issues with facts checked against the Scott catalog or USPS, in catalog order;
      each number's page names its source. {listed} {"is" if listed == 1 else "are"} on the shelf today, and every listing
      opens on eBay, where the price, bids and photos are always current.</p>
    <p class="asof">Listings as of {esc(meta.get("as_of", ""))}.</p>
    <nav class="jump" aria-label="Number ranges"><ul>
"""
    out += "".join(f'      <li><a href="#{anchor}">{esc(words)}</a></li>\n' for anchor, _, words in parts)
    out += "    </ul></nav>\n  </section>\n"
    for anchor, ks, words in parts:
        out += (f'  <section class="related" id="{anchor}" aria-labelledby="{anchor}-h"><h2 id="{anchor}-h">Scott {esc(words)}</h2>\n'
                '  <ul class="numbers">\n')
        for k in ks:
            f, c = facts[k], len(shelf.get(k, []))
            out += (f'    <li><a href="{issue_url(k)}">Scott #{esc(scott_text(k))}</a><span class="what">{esc(f["issue"])} &middot; '
                    f'{esc(f["issued"])} &middot; {esc(f["face"])}</span>'
                    + (f'<span class="on">{c} listing{"" if c == 1 else "s"}</span>' if c else "") + "</li>\n")
        out += "  </ul></section>\n"
    out += '  <p class="note"><a href="/catalog/">Back to the full catalog</a></p>\n' + FOOT + CF_BEACON + "</body>\n</html>\n"
    return out


def main():
    rows, imgs, meta = load()
    now = build_now()
    lastmod = meta.get("as_of", "")
    out_dir = os.path.join(HERE, "catalog")
    # before the catalog folder is rebuilt: record the item pages whose listing has gone since the last build, so a
    # relisted stamp's old address can send visitors on to its live page
    moved = load_moved()
    live = {r["item"] for r in rows}
    drop_returned(moved, live)
    left_on = lastmod if re.fullmatch(r"\d{4}-\d{2}-\d{2}", lastmod) else f"{now:%Y-%m-%d}"
    note_removed(moved, os.path.join(out_dir, "item"), live, left_on)
    # written on EVERY build, changed or not: after a pull on Windows git leaves this file with CRLF line ends, and on a
    # day when no listing ended nothing else would rewrite it (found 2026-11-21, before the merge: the record's own
    # test failed against the file as git checked it out, which would have stopped that morning's refresh)
    write_moved(moved)
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)             # stale item pages of sold listings must not linger
    small_dir = os.path.join(HERE, *THUMB_DIR.split("/"))
    small = 0
    if os.path.isdir(small_dir):           # W2: drop the small copies of photos that are gone (sold or ended listings)
        keep = {os.path.basename(p) for p in imgs.values()}
        for f in sorted(os.listdir(small_dir)):
            if f not in keep:
                os.remove(os.path.join(small_dir, f))
        small = sum(1 for i in imgs if card_img(i, imgs) != imgs[i])
    one_dir = os.path.join(HERE, *THUMB_1X_DIR.split("/"))
    one = 0
    if os.path.isdir(one_dir):             # C2c: the same for the 1x copies
        keep = {os.path.basename(p) for p in imgs.values()}
        for f in sorted(os.listdir(one_dir)):
            if f not in keep:
                os.remove(os.path.join(one_dir, f))
        one = sum(1 for i in imgs if card_srcset(i, imgs))
    by_cat = {}
    for r in rows:
        by_cat.setdefault(r["category"], []).append(r)
    issues = load_issues()
    # the issue pages: one per checked Scott number, listed or not, so their addresses never depend on the day's listings
    keys, shelf = issue_keys(issues[0]), issue_shelf(rows, issues)
    write(os.path.join(out_dir, "index.html"), build_index(rows, imgs, meta, len(keys)))
    for r in rows:
        write(os.path.join(out_dir, "item", f"{r['item']}.html"), build_item(r, by_cat, imgs, issues))
    targets = moved_targets(moved, rows)
    for old, r in sorted(targets.items()):
        write(os.path.join(out_dir, "item", f"{old}.html"), build_moved(r))
    with_facts = sum(1 for r in rows if issue_box(r["title"], issues))
    if keys:
        write(os.path.join(out_dir, ISSUE_DIR, "index.html"), build_issue_index(keys, issues[0], shelf, meta))
    for k in keys:
        write(os.path.join(out_dir, ISSUE_DIR, f"scott-{k.lower()}.html"), build_issue(k, keys, issues[0], shelf, imgs, meta))
    issue_urls = ([f"{SITE}/catalog/{ISSUE_DIR}/"] + [SITE + issue_url(k) for k in keys]) if keys else []
    urls = [f"{SITE}/{p}" if p != "index.html" else f"{SITE}/" for p in PAGES + SITEMAP_EXTRA] + [f"{SITE}/catalog/"] + \
           issue_urls + [f"{SITE}/catalog/item/{r['item']}.html" for r in rows]
    sm = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    sm += "".join(f"  <url><loc>{esc(u)}</loc><lastmod>{lastmod}</lastmod></url>\n" for u in urls) + "</urlset>\n"
    write(os.path.join(HERE, "sitemap.xml"), sm)
    write(os.path.join(HERE, "robots.txt"), f"User-agent: *\nAllow: /\nSitemap: {SITE}/sitemap.xml\n")
    ncat = update_shop(rows)
    newest = update_home(rows, imgs)
    sheet, sheet_faces = update_home_sheet(rows, imgs, avoid=set(newest))
    used = set(newest) | sheet_faces
    tiles, about_faces = about_tiles(rows, imgs, avoid=used)
    replace_block(os.path.join(HERE, "about.html"), ABOUT_START, ABOUT_END, tiles)
    fresh = newest_photographed(rows, imgs, FRESH_COUNT, avoid=used | about_faces)
    replace_block(os.path.join(HERE, "feedback.html"), FRESH_START, FRESH_END, [photo_card(r, imgs) for r in fresh])
    marked, soon = update_sale(rows, imgs, load_sale(), now)
    no_kind = {}
    for r in rows:
        if not kinds_of([r]):
            no_kind[r["category"]] = no_kind.get(r["category"], 0) + 1
    if no_kind:   # a new country category shows up here first; add it to COUNTRIES
        print(f"{sum(no_kind.values())} listings in no About kind: " + "; ".join(f"{k} {v}" for k, v in sorted(no_kind.items())))
    # the summary stays the LAST line: the morning refresh logs only the last line it prints
    print(f"built {len(rows)} item pages + catalog index ({len(imgs)} with photos, {small} with a small card copy, {one} with a 1x copy, "
          f"{with_facts} with issue facts), sitemap {len(urls)} urls, "
          f"shop categories {ncat}, home newly listed {len(newest)}, home category sheet {len(sheet)}, "
          f"about tiles {len(tiles)}, feedback fresh {len(fresh)}, sale markdowns {len(marked)}, "
          f"sale ending soonest {len(soon)} (clock {now:%Y-%m-%dT%H:%M:%SZ}), as of {lastmod}, "
          f"issue pages {len(keys)} ({sum(1 for k in keys if shelf.get(k))} with a listing), "
          f"moved pages {len(targets)} (of {len(moved)} recorded)")


if __name__ == "__main__":
    main()
