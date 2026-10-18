"""Checks for build.py (run: python -m unittest discover -s tests). Douglas 2026-10-11: no prices, one catalog."""
import csv, datetime, hashlib, html, os, re, subprocess, sys, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import build  # noqa: E402  (the pure functions are tested directly with made-up listings)

UTC = datetime.timezone.utc


def rows():
    with open(os.path.join(ROOT, "data", "listings.csv"), encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def build_site():
    # the real clock, like the 3:45 AM run: the tests' build is the one the daily refresh commits
    env = {k: v for k, v in os.environ.items() if k != "STORE_BUILD_NOW"}
    subprocess.run([sys.executable, os.path.join(ROOT, "build.py")], check=True, capture_output=True, env=env)


def tree_hash():
    h = hashlib.sha256()
    for base in ("catalog",):
        for dp, _, fs in sorted(os.walk(os.path.join(ROOT, base))):
            for f in sorted(fs):
                p = os.path.join(dp, f)
                h.update(p[len(ROOT):].encode())
                h.update(open(p, "rb").read())
    for f in ("sitemap.xml", "robots.txt"):
        h.update(open(os.path.join(ROOT, f), "rb").read())
    return h.hexdigest()


def pages():
    out = [os.path.join(ROOT, "catalog", "index.html")]
    d = os.path.join(ROOT, "catalog", "item")
    out += [os.path.join(d, f) for f in sorted(os.listdir(d))]
    return out


def read(name):
    return open(os.path.join(ROOT, name), encoding="utf-8").read()


def block(text, start):
    """The text between a START marker and its END marker."""
    return text[text.index(f"<!-- {start}:START"):text.index(f"<!-- {start}:END -->")]


def item_of(img):
    return re.match(r"images/catalog/(\d+)", img).group(1)


class BuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.started = datetime.datetime.now(UTC).replace(microsecond=0)
        build_site()
        cls.rows = rows()
        cls.imgs = build.load()[1]
        # titles appear as written AND lower-cased in the cards' data-t search attribute; both are verbatim titles
        cls.titles = sorted({html.escape(t, quote=True) for r in cls.rows for t in (r["title"], r["title"].lower())},
                            key=len, reverse=True)

    def test_one_page_per_listing(self):
        items = os.listdir(os.path.join(ROOT, "catalog", "item"))
        self.assertEqual(sorted(items), sorted(f"{r['item']}.html" for r in self.rows))

    def test_item_page_links_its_own_listing(self):
        for r in self.rows:
            text = open(os.path.join(ROOT, "catalog", "item", f"{r['item']}.html"), encoding="utf-8").read()
            self.assertIn(f'href="https://www.ebay.com/itm/{r["item"]}"', text, r["item"])

    def test_no_price_outside_titles(self):
        for p in pages() + [os.path.join(ROOT, f) for f in build.PAGES]:
            text = open(p, encoding="utf-8").read()
            for t in self.titles:                       # titles may carry a face value; strip them first
                text = text.replace(t, "")
            self.assertNotIn("$", text, os.path.basename(p))
            self.assertIsNone(re.search(r"(?i)\b(price[d]? at|our price|only \d|deal|bargain)\b", text), os.path.basename(p))

    def test_internal_links_and_images_exist(self):
        for p in pages():
            text = open(p, encoding="utf-8").read()
            for ref in re.findall(r'(?:href|src)="(/[^"#?]*)', text):
                path = ref.lstrip("/")
                if path in ("", "catalog/"):
                    path = (path + "index.html") if path else "index.html"
                self.assertTrue(os.path.exists(os.path.join(ROOT, path)), f"{os.path.basename(p)} -> {ref}")

    def test_hand_written_pages_link_only_to_files_that_exist(self):
        for name in build.PAGES:
            for ref in re.findall(r'(?:href|src)="([^"#]+)"', read(name)):
                if re.match(r"(?i)(https?:|mailto:|//)", ref):
                    continue
                path = ref.split("?")[0]
                path = path + "index.html" if path.endswith("/") else path
                self.assertTrue(os.path.exists(os.path.join(ROOT, path)), f"{name} -> {ref}")

    def test_item_main_photo_loads_at_once_with_its_size(self):
        # W10 (2026-10-17): the item page's own photo is the biggest thing on the page - no lazy load, real width/height
        checked = 0
        for r in self.rows:
            if r["item"] not in self.imgs:
                continue
            text = open(os.path.join(ROOT, "catalog", "item", f"{r['item']}.html"), encoding="utf-8").read()
            main = re.search(r'<div class="item-stamp[^"]*"><div class="frame"><span class="ph">(<img [^>]+>)', text)
            self.assertIsNotNone(main, r["item"])
            tag = main.group(1)
            self.assertNotIn('loading="lazy"', tag, r["item"])
            self.assertIn('fetchpriority="high"', tag, r["item"])
            size = re.search(r'width="(\d+)" height="(\d+)"', tag)
            self.assertIsNotNone(size, r["item"])
            self.assertEqual(tuple(map(int, size.groups())), build.image_size(os.path.join(ROOT, self.imgs[r["item"]])), r["item"])
            checked += 1
        self.assertGreater(checked, 0)

    def test_image_size_reads_jpeg_headers(self):
        seg = lambda m, body: b"\xff" + bytes([m]) + (len(body) + 2).to_bytes(2, "big") + body
        sof = lambda m, w, h: seg(m, b"\x08" + h.to_bytes(2, "big") + w.to_bytes(2, "big") + b"\x03" + b"\x00" * 9)
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            cases = {"baseline": (b"\xff\xd8" + seg(0xE0, b"JFIF\x00" + b"\x00" * 9) + sof(0xC0, 1600, 1400), (1600, 1400)),
                     "progressive": (b"\xff\xd8" + seg(0xE1, b"Exif\x00\x00" + b"\x00" * 40) + sof(0xC2, 640, 480), (640, 480)),
                     "not a jpeg": (b"\x89PNG\r\n\x1a\n" + b"\x00" * 40, None)}
            for name, (data, want) in cases.items():
                p = os.path.join(d, name + ".jpg")
                with open(p, "wb") as f:
                    f.write(data)
                self.assertEqual(build.image_size(p), want, name)
            self.assertIsNone(build.image_size(os.path.join(d, "missing.jpg")))

    def test_item_buy_column_has_one_ebay_button(self):
        # W10: the button lives in the "buy" column; on phones the CSS shows it straight under the title
        for r in self.rows:
            text = open(os.path.join(ROOT, "catalog", "item", f"{r['item']}.html"), encoding="utf-8").read()
            col = text[text.index('<div class="buy">'):text.index("</div>", text.index('<div class="buy">'))]
            self.assertEqual(col.count('class="btn"'), 1, r["item"])
            self.assertIn(f'href="https://www.ebay.com/itm/{r["item"]}" target="stampede_ebay"', col, r["item"])
        css = open(os.path.join(ROOT, "assets", "catalog.css"), encoding="utf-8").read()
        self.assertIn(".buy .facts { order: 1; }", css)
        self.assertIn(".buy { display: block; }", css)

    def test_narrow_phones_do_not_scroll_sideways(self):
        # W11 (2026-10-17): at 320-360 px Home's hero column must be allowed to shrink below the search bar's natural
        # width, and Feedback's four rating cards stack in one column below 368 px ("Communication" needs 156 px)
        home, fb = read("index.html"), read("feedback.html")
        self.assertIn(".hero-row { display: grid; gap: 1.5rem; align-items: center; grid-template-columns: minmax(0, 1fr); }", home)
        self.assertIn("@media (max-width: 23rem) { .dsr { grid-template-columns: minmax(0, 1fr); } }", fb)

    def test_trust_numbers_are_dated_and_match_the_feedback_page(self):
        # W5 (Douglas 2026-10-17, hard rule 12): Home and About show only the Feedback page's dated eBay numbers,
        # with the same "as of" date and a link to the eBay feedback page; undated claims stay off
        fb = read("feedback.html")
        pos = re.search(r"<b>(\d+)</b><span>Positive</span>", fb).group(1)
        asof = re.search(r"last 12 months, as of ([A-Z][a-z]+ \d{1,2}, \d{4})", fb).group(1)
        for name in ("index.html", "about.html"):
            t = read(name)
            self.assertIn(f"<b>{pos}</b>", t, name)
            self.assertIn(f"eBay feedback ratings for the last 12 months, as of {asof}.", t, name)
            self.assertIn('href="https://www.ebay.com/fdbk/feedback_profile/stampede_usa" target="stampede_ebay"', t, name)
            for gone in ("248+", "Top Rated", "find value", "100%</b>"):
                self.assertNotIn(gone, t, f"{name} still shows {gone}")

    def test_canonicals_unique(self):
        seen = {}
        for p in pages():
            m = re.search(r'<link rel="canonical" href="([^"]+)"', open(p, encoding="utf-8").read())
            self.assertIsNotNone(m, p)
            self.assertNotIn(m.group(1), seen, f"{p} and {seen.get(m.group(1))}")
            seen[m.group(1)] = p

    def test_build_is_deterministic(self):
        first = tree_hash()
        build_site()
        self.assertEqual(first, tree_hash())

    def test_shop_category_links_open_nonempty_filters(self):
        shop = open(os.path.join(ROOT, "shop.html"), encoding="utf-8").read()
        block = shop[shop.index("<!-- CATALOG-CATEGORIES:START"):shop.index("<!-- CATALOG-CATEGORIES:END -->")]
        cats = re.findall(r'href="catalog/\?cat=([a-z0-9-]+)"', block)
        index = open(os.path.join(ROOT, "catalog", "index.html"), encoding="utf-8").read()
        live = set(re.findall(r'data-c="([a-z0-9-]+)"', index))
        self.assertTrue(cats, "no category links in the Shop block")
        self.assertEqual(len(cats), len(set(cats)), "duplicate category link")
        for c in cats:
            self.assertIn(c, live, f"Shop links to an empty category: {c}")
        self.assertEqual(set(cats), live, "Shop list and catalog categories differ")
        self.assertNotIn("stampede-stories", block)

    def test_home_category_sheet_tiles_have_photos_and_live_filters(self):
        home = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
        block = home[home.index("<!-- CATEGORY-SHEET:START"):home.index("<!-- CATEGORY-SHEET:END -->")]
        cats = re.findall(r'href="catalog/\?cat=([a-z0-9-]+)"', block)
        imgs = re.findall(r'src="(images/catalog/\d+(?:-e)?\.jpg)"', block)
        index = open(os.path.join(ROOT, "catalog", "index.html"), encoding="utf-8").read()
        live = set(re.findall(r'data-c="([a-z0-9-]+)"', index))
        self.assertEqual(len(cats), 8, "Home category sheet must have eight tiles")
        self.assertEqual(len(imgs), 8, "every category tile needs a photo")
        self.assertEqual(len(set(cats)), 8)
        for c in cats:
            self.assertIn(c, live, c)
        for p in imgs:
            self.assertTrue(os.path.exists(os.path.join(ROOT, p)), p)

    def test_home_newly_listed_is_twelve_newest_with_photos(self):
        home = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
        block = home[home.index("<!-- NEWLY-LISTED:START"):home.index("<!-- NEWLY-LISTED:END -->")]
        items = re.findall(r'href="catalog/item/(\d+)\.html"', block)
        imgs = re.findall(r'src="(images/catalog/\d+(?:-e)?\.jpg)"', block)
        self.assertEqual(len(items), 12, "Home must show twelve newly listed cards")
        self.assertEqual(len(set(items)), 12)
        live = {r["item"] for r in self.rows}
        for i in items:
            self.assertIn(i, live, f"Home card for a listing that is not active: {i}")
            self.assertTrue(os.path.exists(os.path.join(ROOT, "catalog", "item", f"{i}.html")))
        for p in imgs:
            self.assertTrue(os.path.exists(os.path.join(ROOT, p)), p)
        self.assertEqual(items, sorted(items, reverse=True), "newest first")

    def test_sitemap_lists_every_item(self):
        sm = open(os.path.join(ROOT, "sitemap.xml"), encoding="utf-8").read()
        for r in self.rows:
            self.assertIn(f"/catalog/item/{r['item']}.html</loc>", sm)

    # ---- Sale, About and Feedback (Douglas 2026-10-16, "Polish Sale, About and Feedback") ----

    def test_every_auction_has_an_end_time(self):
        for r in self.rows:
            if r["format"] == "AUCTION":
                self.assertRegex(r["ends"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", r["item"])

    def test_sale_ending_soonest_is_open_auctions_soonest_first(self):
        text = block(read("sale.html"), "SALE-ENDING")
        cards = re.findall(r'<li class="edged" data-ends="([^"]+)"( hidden)?><a class="perf" href="catalog/item/(\d+)\.html">'
                           r'<span class="slot"><img src="(images/catalog/\d+(?:-e)?\.jpg)"', text)
        by_item = {r["item"]: r for r in self.rows}
        cutoff = self.started + datetime.timedelta(minutes=5)
        still_open = [r for r in self.rows if r["format"] == "AUCTION" and r["item"] in self.imgs
                      and (build.parse_utc(r["ends"]) or self.started) > cutoff]
        if not still_open:                  # no auction left to show: no section at all, never an empty one
            self.assertNotIn("<section", text)
            return
        self.assertTrue(cards, "open auctions in the feed but none on the Sale page")
        self.assertLessEqual(len(cards), build.ENDING_MAX)
        # once everything ending before the next build is gone, a full set must still be there to show
        horizon = self.started + datetime.timedelta(hours=build.ENDING_HOURS)
        early_titles = {by_item[c[2]]["title"] for c in cards if build.parse_utc(c[0]) <= horizon}
        later_titles = {r["title"] for r in still_open if build.parse_utc(r["ends"]) > horizon} - early_titles
        after_next_build = [c for c in cards if build.parse_utc(c[0]) > horizon]
        if len(cards) < build.ENDING_MAX:
            self.assertGreaterEqual(len(after_next_build), min(build.ENDING_SHOWN, len(later_titles)),
                                    "too few cards left once the auctions ending before the next build are gone")
        self.assertIn(f'data-show="{build.ENDING_SHOWN}"', text)
        keys = []
        for i, (ends, hidden, item, img) in enumerate(cards):
            r = by_item[item]
            self.assertEqual(r["format"], "AUCTION", item)
            self.assertEqual(ends, r["ends"], item)
            self.assertGreater(build.parse_utc(ends), self.started, f"{item} had ended when the page was built")
            self.assertEqual(bool(hidden), i >= build.ENDING_SHOWN, f"card {i + 1} visibility")
            self.assertTrue(os.path.exists(os.path.join(ROOT, img)), img)
            keys.append((ends, item))
        self.assertEqual(keys, sorted(keys), "soonest first")
        shown_titles = [by_item[c[2]]["title"] for c in cards]
        self.assertEqual(len(shown_titles), len(set(shown_titles)), "one card per title")
        last = max(build.parse_utc(c[0]) for c in cards)
        for r in self.rows:                 # nothing open, photographed and ending sooner was left out
            end = build.parse_utc(r["ends"])
            if r["format"] == "AUCTION" and r["item"] in self.imgs and end and cutoff < end < last \
                    and r["item"] not in {c[2] for c in cards}:
                self.assertIn(r["title"], shown_titles, f"{r['item']} ends sooner but is missing")

    def test_sale_hero_and_markdowns_match_the_markdown_list(self):
        page = read("sale.html")
        hero, marked = block(page, "SALE-HERO"), block(page, "SALE-MARKDOWNS")
        with open(os.path.join(ROOT, "data", "sale_items.csv"), encoding="utf-8-sig", newline="") as f:
            sale = list(csv.DictReader(f))
        today = (self.started - datetime.timedelta(hours=7)).date().isoformat()
        live = {r["item"] for r in self.rows}
        active = [s["item"] for s in sale if s["item"] in live and (s.get("sale_ends") or "") >= today
                  and re.fullmatch(r"\d{4}-\d{2}-\d{2}", s.get("sale_ends") or "")]
        if active:
            self.assertIn("marked down on eBay right now", hero)
            self.assertEqual(re.findall(r'href="catalog/item/(\d+)\.html"', marked),
                             [i for i in dict.fromkeys(active) if i in self.imgs])
        else:
            self.assertNotIn("marked down", hero.lower())
            self.assertNotIn("<section", marked, "a markdown section with no markdown running")
        if "<section" in block(page, "SALE-ENDING"):
            self.assertTrue(active or "ending soonest" in hero)

    def test_sale_blocks_tell_the_truth_in_every_state(self):
        now = datetime.datetime(2026, 10, 16, 8, 0, tzinfo=UTC)

        def row(item, fmt, ends, title=None):
            return {"item": item, "title": title or f"Scott #{item} Test Stamp", "category": "Sheets", "condition": "",
                    "format": fmt, "ebay_url": f"https://www.ebay.com/itm/{item}", "ends": ends}
        listing = [row("101", "AUCTION", "2026-10-16T07:00:00Z"),            # ended an hour before the build
                   row("102", "AUCTION", "2026-10-20T22:47:00Z"),
                   row("103", "AUCTION", "2026-10-18T10:00:00Z"),
                   row("104", "AUCTION", "2026-10-19T10:00:00Z", title="Scott #103 Test Stamp"),   # same title as 103
                   row("105", "FIXED_PRICE", "2026-11-14T20:45:00Z")]
        imgs = {r["item"]: f"images/catalog/{r['item']}.jpg" for r in listing}

        blocks, marked, soon = build.sale_blocks(listing, imgs, [], now)
        self.assertEqual([r["item"] for r in soon], ["103", "102"])
        self.assertEqual(marked, [])
        self.assertIn("Starting low, ending soon.", "\n".join(blocks["hero"]))
        self.assertEqual(blocks["markdowns"], [])
        self.assertEqual(re.findall(r"catalog/item/(\d+)\.html", "\n".join(blocks["ending"])), ["103", "102"])

        on_sale = [{"item": "105", "sale_ends": "2026-10-16"}, {"item": "999", "sale_ends": "2026-12-31"}]
        blocks, marked, _ = build.sale_blocks(listing, imgs, on_sale, now)
        self.assertEqual([r["item"] for r in marked], ["105"])
        self.assertIn("1 stamp is marked down on eBay right now", "\n".join(blocks["hero"]))
        self.assertIn('<span class="tag">Marked down</span>', "\n".join(blocks["markdowns"]))

        for stale in ({"item": "105", "sale_ends": "2026-10-15"}, {"item": "105", "sale_ends": ""},
                      {"item": "105", "sale_ends": "10/20/2026"}, {"item": "105"}):
            blocks, marked, _ = build.sale_blocks(listing, imgs, [stale], now)
            self.assertEqual(marked, [], stale)
            self.assertNotIn("marked down", "\n".join(blocks["hero"]).lower(), stale)

        batch = ([row(str(200 + i), "AUCTION", "2026-10-16T09:00:00Z") for i in range(15)]
                 + [row(str(300 + i), "AUCTION", "2026-10-21T09:00:00Z") for i in range(15)])
        _, _, soon = build.sale_blocks(batch, {r["item"]: "x.jpg" for r in batch}, [], now)
        self.assertEqual(len(soon), 15 + build.ENDING_SHOWN, "the whole day's batch plus a full set after it")

        blocks, marked, soon = build.sale_blocks([listing[0], listing[4]], imgs, [], now)
        self.assertEqual(soon, [])
        self.assertEqual(blocks["ending"], [])
        self.assertIn("No auctions are running", "\n".join(blocks["hero"]))
        for b in blocks.values():
            self.assertNotIn("$", "\n".join(b))

    def test_about_tiles_one_per_kind_with_photos_and_live_filters(self):
        tiles = re.findall(r'<li><a class="tile perf" href="catalog/\?kind=([a-z-]+)"><span class="pic">'
                           r'<img src="(images/catalog/\d+(?:-e)?\.jpg)"[^>]*></span><b>([^<]+)</b><span>(\d+) on the shelf</span>',
                           block(read("about.html"), "ABOUT-KINDS"))
        expected = [(k, label, sum(1 for r in self.rows if test(r))) for k, label, test in build.KINDS
                    if any(test(r) and r["item"] in self.imgs for r in self.rows)]
        self.assertEqual([(k, html.unescape(label), int(n)) for k, _, label, n in tiles], expected)
        self.assertTrue(tiles, "no About tiles")
        index = read("catalog/index.html")
        for k, img, _, _ in tiles:
            self.assertTrue(os.path.exists(os.path.join(ROOT, img)), img)
            self.assertIn(f'<option value="kind:{k}">', index)
            self.assertRegex(index, rf'data-k="[^"]*\b{k}\b', f"no catalog card of kind {k}")
            r = next(r for r in self.rows if r["item"] == item_of(img))
            self.assertIn(k, build.kinds_of([r]), f"the {k} tile shows a stamp of another kind")

    def test_feedback_fresh_is_newest_not_shown_on_home_or_about(self):
        items = re.findall(r'href="catalog/item/(\d+)\.html"', block(read("feedback.html"), "FEEDBACK-FRESH"))
        self.assertEqual(len(items), build.FRESH_COUNT)
        home, about = read("index.html"), read("about.html")
        elsewhere = set(re.findall(r'href="catalog/item/(\d+)\.html"', block(home, "NEWLY-LISTED")))
        elsewhere |= {item_of(i) for i in re.findall(r'src="(images/catalog/\d+(?:-e)?\.jpg)"', block(home, "CATEGORY-SHEET"))}
        elsewhere |= {item_of(i) for i in re.findall(r'src="(images/catalog/\d+(?:-e)?\.jpg)"', block(about, "ABOUT-KINDS"))}
        by_item = {r["item"]: r for r in self.rows}
        seen_titles = {by_item[i]["title"] for i in elsewhere}
        for i in items:
            self.assertIn(i, self.imgs, i)
            self.assertNotIn(i, elsewhere, f"{i} is already on Home or About")
            self.assertNotIn(by_item[i]["title"], seen_titles, f"{i} repeats a title shown elsewhere")
            seen_titles.add(by_item[i]["title"])
        self.assertEqual(items, sorted(items, reverse=True), "newest first")

    def test_catalog_reads_kind_and_format_from_the_link(self):
        index = read("catalog/index.html")
        self.assertIn("params.get('kind')", index)
        self.assertIn("params.get('fmt')", index)
        self.assertIn('<option value="Auction">Auction</option>', index)

    def test_counts_agree_with_what_the_filters_show(self):
        # W13 (2026-10-17): every number a visitor sees counts listings, and so does the catalog's counter (listings
        # with the same title share one card), so a tile's number is exactly what its filter shows
        index = read("catalog/index.html")
        cards = re.findall(r'<li class="edged" data-t="[^"]*" data-c="([a-z0-9-]+)" data-f="[^"]*" '
                           r'data-n="(\d+)" data-na="(\d+)" data-k="([^"]*)">', index)
        self.assertEqual(len(cards), index.count('<li class="edged"'))
        n = len(self.rows)
        self.assertEqual(sum(int(c[1]) for c in cards), n)
        self.assertEqual(sum(int(c[2]) for c in cards), sum(1 for r in self.rows if r["format"] == "AUCTION"))
        self.assertIn(f"All {n} StampedeUSA listings in one place", index)
        self.assertIn(f'<p class="count" id="count" aria-live="polite">Showing {n} of {n} listings</p>', index)
        self.assertIn(f"<title>The catalog: {n} listings | StampedeUSA</title>", index)
        self.assertIn("count.textContent = 'Showing ' + shown + ' of ' + total + ' listings';", index)
        home = read("index.html")
        block = home[home.index("<!-- CATEGORY-SHEET:START"):home.index("<!-- CATEGORY-SHEET:END -->")]
        tiles = re.findall(r'href="catalog/\?cat=([a-z0-9-]+)">.*?<span>(\d+) listings?</span></a>', block)
        self.assertEqual(len(tiles), 8)
        for cat, num in tiles:
            self.assertEqual(int(num), sum(int(c[1]) for c in cards if c[0] == cat), cat)
        self.assertNotRegex(block, r"\d+ stamps?</span>")
        kinds = re.findall(r'href="catalog/\?kind=([a-z-]+)">.*?<span>(\d+) on the shelf</span>', read("about.html"))
        self.assertTrue(kinds)
        for k, num in kinds:
            self.assertEqual(int(num), sum(int(c[1]) for c in cards if k in c[3].split()), k)

    def test_readable_line_length_and_tall_menu_links(self):
        # W13 (2026-10-17): running text stops at about 70 characters (50ch: an average letter here is 0.70-0.77 of
        # the width of "0"), and every menu link is at least 24 px tall (32 px; 43 px on phones)
        capped = {"shop.html": (".hero .lede", ".note"), "sale.html": (".verse", ".note"),
                  "about.html": (".hero .lede", ".verse", ".prose p"), "feedback.html": (".hero .lede", ".asof", ".note"),
                  "assets/catalog.css": (".hero .lede", ".note")}
        for name in ("index.html", "shop.html", "sale.html", "about.html", "feedback.html", "assets/catalog.css"):
            t = read(name)
            self.assertIn(".menu a { display: inline-block; }", t, name)
            self.assertIn(".menu a { padding: .7rem 0 .3rem; } }", t, name)
            for sel in capped.get(name, ()):
                self.assertRegex(t, r"\n\s*" + re.escape(sel) + r" \{[^}]*max-width: 50ch;", f"{name} {sel}")
        self.assertIn('<p class="verse">Combine wins, shipping drops too,<br>follow us &mdash; fresh finds for you.<br>', read("about.html"))


if __name__ == "__main__":
    unittest.main()
