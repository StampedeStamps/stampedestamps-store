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
Outputs (committed; GitHub Pages and later AWS Amplify serve them as plain files):
  catalog/index.html, catalog/item/<item>.html, sitemap.xml, robots.txt, and the marker blocks inside the hand-written
  pages: Shop's category list, Home's category sheet and Newly listed, the Sale page, About's "What you'll find" and
  Feedback's "Fresh on the shelf".

The catalog build is deterministic: the same inputs give byte-identical output (tests/test_build.py checks it). The Sale
page also depends on the clock (auctions that have ended drop off); STORE_BUILD_NOW=2026-10-16T19:00:00Z pins it.

    python build.py
"""
import csv, datetime, html, json, os, re, shutil, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = "https://www.stampedestamps.store"
STORE = "https://www.ebay.com/str/stampedeusa"
PAGES = ["index.html", "shop.html", "sale.html", "about.html", "feedback.html"]
FORMAT = {"FIXED_PRICE": "Buy It Now", "AUCTION": "Auction"}
RELATED = 6
UTC = datetime.timezone.utc


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
<link rel="icon" href="/images/hero.jpg">
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
        <li><a href="/shop.html" aria-current="page">Shop</a></li>
        <li><a href="/sale.html">Sale</a></li>
        <li><a href="/about.html">About</a></li>
        <li><a href="/feedback.html">Feedback</a></li>
      </ul>
    </nav>
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


def picture(item, imgs, title, category, cls, main=False):
    """The scan when we have one; otherwise a text card naming the stamp from its own title (its first Scott number),
    or its category - never anything the title doesn't say. main=True is the item page's own photo: it loads at once
    (it is the biggest thing on the page) and carries its width and height so nothing jumps when it arrives (W10)."""
    if item in imgs and main:
        size = image_size(os.path.join(HERE, imgs[item]))
        dims = f' width="{size[0]}" height="{size[1]}"' if size else ""
        return f'<span class="{cls[0]}"><img src="/{esc(imgs[item])}" alt="{esc(title)}"{dims} fetchpriority="high"></span>'
    if item in imgs:
        return f'<span class="{cls[0]}"><img src="/{esc(imgs[item])}" alt="{esc(title)}" loading="lazy"></span>'
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


def build_index(rows, imgs, meta):
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
               "Every one opens on eBay.", f"{SITE}/catalog/", "images/hero.jpg")
    out += f"""  <section class="hero">
    <h1>The whole shelf, one page.</h1>
    <p class="lede">All {n} StampedeUSA listings in one place. Search by Scott number, year, country or subject, or narrow it by
      category. Every stamp opens on eBay, where the price, bids and photos are always current.</p>
    <p class="asof">Listings as of {esc(meta.get("as_of", ""))}.</p>
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
    out += "\n  </ul>\n" + FOOT + SCRIPT + "</body>\n</html>\n"
    return out


def build_item(r, rows_by_cat, imgs):
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
    if rel:
        out += '  <section class="related"><h2>More from ' + esc(r["category"]) + '</h2>\n  <ul class="stamps">\n'
        out += "\n".join("    " + card([x], imgs) for x in rel)
        out += "\n  </ul></section>\n"
    out += f'  <p class="note"><a href="/catalog/">Back to the full catalog</a></p>\n' + FOOT + "</body>\n</html>\n"
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
            f'<span class="slot"><img src="{esc(imgs[r["item"]])}" alt="{esc(r["title"])}" loading="lazy"></span>{tag}'
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
                     f'<span class="pic"><img src="{esc(imgs[r["item"]])}" alt="{esc(r["title"])}" loading="lazy"></span>'
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
                     f'<span class="pic"><img src="{esc(imgs[face["item"]])}" alt="{esc(face["title"])}" loading="lazy"></span>'
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


def main():
    rows, imgs, meta = load()
    now = build_now()
    out_dir = os.path.join(HERE, "catalog")
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)             # stale item pages of sold listings must not linger
    by_cat = {}
    for r in rows:
        by_cat.setdefault(r["category"], []).append(r)
    write(os.path.join(out_dir, "index.html"), build_index(rows, imgs, meta))
    for r in rows:
        write(os.path.join(out_dir, "item", f"{r['item']}.html"), build_item(r, by_cat, imgs))
    lastmod = meta.get("as_of", "")
    urls = [f"{SITE}/{p}" if p != "index.html" else f"{SITE}/" for p in PAGES] + [f"{SITE}/catalog/"] + \
           [f"{SITE}/catalog/item/{r['item']}.html" for r in rows]
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
    print(f"built {len(rows)} item pages + catalog index ({len(imgs)} with photos), sitemap {len(urls)} urls, "
          f"shop categories {ncat}, home newly listed {len(newest)}, home category sheet {len(sheet)}, "
          f"about tiles {len(tiles)}, feedback fresh {len(fresh)}, sale markdowns {len(marked)}, "
          f"sale ending soonest {len(soon)} (clock {now:%Y-%m-%dT%H:%M:%SZ}), as of {lastmod}")


if __name__ == "__main__":
    main()
