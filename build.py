#!/usr/bin/env python3
"""Build the listing catalog for www.stampedestamps.store (standard library only).

Douglas, 2026-10-11: one catalog on the store's own domain, NO prices, photos where we have scans, checkout on eBay.
Inputs (written on OMEN every morning from the eBay export; never edited by hand):
  data/listings.csv        item,title,category,condition,format,ebay_url   (public-safe: no prices, no SKUs)
  data/catalog_images.csv  item,image                                       (web-sized scans; items without one get a text card)
  data/feed_meta.json      {"source", "as_of", "listings"}
Outputs (committed; GitHub Pages and later AWS Amplify serve them as plain files):
  catalog/index.html, catalog/item/<item>.html, sitemap.xml, robots.txt

The build is deterministic: the same inputs give byte-identical output (tests/test_build.py checks it).

    python build.py
"""
import csv, html, json, os, re, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = "https://www.stampedestamps.store"
STORE = "https://www.ebay.com/str/stampedeusa"
PAGES = ["index.html", "shop.html", "sale.html", "about.html", "feedback.html"]
FORMAT = {"FIXED_PRICE": "Buy It Now", "AUCTION": "Auction"}
RELATED = 6


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


def picture(item, imgs, title, category, cls):
    """The scan when we have one; otherwise a text card naming the stamp from its own title (its first Scott number),
    or its category - never anything the title doesn't say."""
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
    return (f'<li class="edged" data-t="{esc(title.lower())}" data-c="{esc(slug(cats))}" data-f="{esc(fmts)}">'
            f'<a class="card perf" href="/catalog/item/{esc(item)}.html">'
            f'{picture(item, imgs, title, cats, ("ph", "tx"))}'
            f'<span class="badge">{esc(fmt)}</span><span class="t">{esc(title)}</span>'
            f'<span class="c">{esc(cats)}</span></a>{more}</li>')


SCRIPT = """<script>
(function () {
  var q = document.getElementById('q'), c = document.getElementById('cat'), f = document.getElementById('fmt');
  var items = Array.prototype.slice.call(document.querySelectorAll('#cards > li'));
  var count = document.getElementById('count'), empty = document.getElementById('empty');
  var params = new URLSearchParams(location.search);
  if (params.get('cat')) c.value = params.get('cat');
  if (params.get('q')) q.value = params.get('q');
  function run() {
    var words = q.value.toLowerCase().split(/\\s+/).filter(Boolean), shown = 0;
    items.forEach(function (li) {
      var ok = (!c.value || li.dataset.c === c.value) && (!f.value || li.dataset.f.indexOf(f.value) !== -1) &&
               words.every(function (w) { return li.dataset.t.indexOf(w) !== -1; });
      li.hidden = !ok; if (ok) shown++;
    });
    count.textContent = 'Showing ' + shown + ' of ' + items.length;
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
    n = len(rows)
    out = head(f"The catalog: {n} stamps | StampedeUSA",
               f"Browse all {n} StampedeUSA listings: U.S. sheets, panes, singles, covers, revenues and worldwide stamps. "
               "Every one opens on eBay.", f"{SITE}/catalog/", "images/hero.jpg")
    out += f"""  <section class="hero">
    <h1>The whole shelf, one page.</h1>
    <p class="lede">All {n} StampedeUSA listings in one place. Search by Scott number, year, country or subject, or narrow it by
      category. Every stamp opens on eBay, where the price, bids and photos are always current.</p>
    <p class="asof">Listings as of {esc(meta.get("as_of", ""))}.</p>
    <form class="find" role="search" onsubmit="return false">
      <div><label for="q">Search the catalog</label><input id="q" type="search" placeholder="Try Batman, eagle, Boston or 1962" autocomplete="off"></div>
      <div><label for="cat">Category</label><select id="cat"><option value="">All categories</option>{cat_opts}</select></div>
      <div><label for="fmt">Buying format</label><select id="fmt"><option value="">Auction or Buy It Now</option>
        <option value="Auction">Auction</option><option value="Buy It Now">Buy It Now</option></select></div>
    </form>
    <p class="count" id="count" aria-live="polite">Showing {len(ordered)} of {len(ordered)}</p>
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
    <div class="item-stamp perf edged"><div class="frame">{picture(item, imgs, title, r['category'], ("ph", "tx"))}</div></div>
    <div>
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


def replace_block(path, start, end, items):
    """Swap the text between two marker comments in a hand-written page; only that block changes."""
    with open(path, encoding="utf-8", newline="") as f:
        text = f.read()
    a, b = text.index(start), text.index(end)
    head_end = text.index("-->", a) + 3
    nl = "\r\n" if "\r\n" in text[:500] else "\n"
    new = text[:head_end] + "".join(nl + "      " + i for i in items) + nl + "      " + text[b:]
    if new != text:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(new)


def update_home(rows, imgs):
    """Home's "Newly listed": the 6 newest listings (highest item number) that have a photo, one per title, each
    linking to its catalog page. Returns the item numbers chosen."""
    picked, titles = [], set()
    for r in sorted(rows, key=lambda r: r["item"], reverse=True):
        if r["item"] in imgs and r["title"] not in titles:
            picked.append(r)
            titles.add(r["title"])
        if len(picked) == NEW_COUNT:
            break
    items = [f'<li class="edged"><a class="perf" href="catalog/item/{esc(r["item"])}.html">'
             f'<span class="slot"><img src="{esc(imgs[r["item"]])}" alt="{esc(r["title"])}" loading="lazy"></span>'
             f'<span class="t">{esc(r["title"])}</span><span class="c">{esc(FRIENDLY.get(r["category"], r["category"]))}</span></a></li>'
             for r in picked]
    replace_block(os.path.join(HERE, "index.html"), NEW_START, NEW_END, items)
    return [r["item"] for r in picked]


def update_home_sheet(rows, imgs, avoid=()):
    """Home's category sheet (Douglas 2026-10-15, "Photo storefront"): the largest categories that have at least one
    photographed listing, each tile showing that category's newest photographed stamp NOT already shown in Newly
    listed (`avoid`; falls back to one that is), linking to the catalog filtered to it. Returns the categories."""
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
                     f'<b>{esc(FRIENDLY.get(c, c))}</b><span>{n} {"stamp" if n == 1 else "stamps"}</span></a></li>')
    replace_block(os.path.join(HERE, "index.html"), SHEET_START, SHEET_END, items)
    return cats


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def main():
    rows, imgs, meta = load()
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
    sheet = update_home_sheet(rows, imgs, avoid=set(newest))
    print(f"built {len(rows)} item pages + catalog index ({len(imgs)} with photos), sitemap {len(urls)} urls, "
          f"shop categories {ncat}, home newly listed {len(newest)}, home category sheet {len(sheet)}, as of {lastmod}")


if __name__ == "__main__":
    main()
