"""Checks for build.py (run: python -m unittest discover -s tests). Douglas 2026-10-11: no prices, one catalog."""
import csv, datetime, hashlib, html, json, os, re, subprocess, sys, unittest

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


# a card shows the full photo (images/catalog) or its small copy (images/thumbs, W2 2026-10-17); same file name
CARD_IMG = r"images/(?:catalog|thumbs)/\d+(?:-e)?\.jpg"


def item_of(img):
    return re.match(r"images/(?:catalog|thumbs)/(\d+)", img).group(1)


def ld_blocks(text):
    return re.findall(r'<script type="application/ld\+json">(.*?)</script>', text, re.S)


def walk(x, parent=None):
    """Every (key, value, the dict holding it) in parsed JSON."""
    if isinstance(x, dict):
        for k, v in x.items():
            yield k, v, x
            yield from walk(v, x)
    elif isinstance(x, list):
        for v in x:
            yield from walk(v, parent)


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

    def test_cards_show_the_small_copy_and_item_pages_keep_the_full_photo(self):
        # W2 (2026-10-17): every card shows the small copy when the photo step has made one no older than the photo;
        # the item page's own photo and its og:image stay the full photo
        cards = re.findall(r'href="/catalog/item/(\d+)\.html"><span class="ph"><img src="/([^"]+)"', read("catalog/index.html"))
        self.assertTrue(cards)
        for item, src in cards:
            self.assertEqual(src, build.card_img(item, self.imgs), item)
            self.assertRegex(src, "^" + CARD_IMG + "$", item)
            self.assertTrue(os.path.exists(os.path.join(ROOT, src)), src)
        for r in self.rows:
            if r["item"] not in self.imgs:
                continue
            text = open(os.path.join(ROOT, "catalog", "item", f"{r['item']}.html"), encoding="utf-8").read()
            full = self.imgs[r["item"]]
            self.assertIn(f'<div class="frame"><span class="ph"><img src="/{full}"', text, r["item"])
            self.assertIn(f'<meta property="og:image" content="{build.SITE}/{full}">', text, r["item"])
        for name, start in (("index.html", "NEWLY-LISTED"), ("index.html", "CATEGORY-SHEET"), ("about.html", "ABOUT-KINDS"),
                            ("feedback.html", "FEEDBACK-FRESH"), ("sale.html", "SALE-ENDING")):
            for item, src in re.findall(r'href="catalog/(?:item/(\d+)\.html|\?[^"]*)"><span class="(?:slot|pic)"><img src="([^"]+)"',
                                        block(read(name), start)):
                self.assertRegex(src, "^" + CARD_IMG + "$", f"{name} {start}")
                if item:
                    self.assertEqual(src, build.card_img(item, self.imgs), f"{name} {item}")

    def test_card_img_never_shows_an_older_copy(self):
        import tempfile
        here = build.HERE
        with tempfile.TemporaryDirectory() as d:
            try:
                build.HERE = d
                os.makedirs(os.path.join(d, "images", "catalog"))
                os.makedirs(os.path.join(d, *build.THUMB_DIR.split("/")))
                full = os.path.join(d, "images", "catalog", "1.jpg")
                small = os.path.join(d, *build.THUMB_DIR.split("/"), "1.jpg")
                for f in (full, small):
                    with open(f, "wb") as fh:
                        fh.write(b"x")
                imgs = {"1": "images/catalog/1.jpg", "2": "images/catalog/2.jpg"}
                os.utime(full, (1000, 1000))
                os.utime(small, (2000, 2000))
                self.assertEqual(build.card_img("1", imgs), build.THUMB_DIR + "/1.jpg")
                os.utime(small, (500, 500))            # the photo changed after its copy was made: show the photo
                self.assertEqual(build.card_img("1", imgs), "images/catalog/1.jpg")
                self.assertEqual(build.card_img("2", imgs), "images/catalog/2.jpg")    # no copy yet
            finally:
                build.HERE = here

    def test_small_copies_only_for_current_photos(self):
        # W2: build.py deletes the small copy of a photo that is gone, so sold listings leave nothing behind
        d = os.path.join(ROOT, *build.THUMB_DIR.split("/"))
        if os.path.isdir(d):
            keep = {os.path.basename(p) for p in self.imgs.values()}
            self.assertEqual(sorted(set(os.listdir(d)) - keep), [])

    def test_card_copies_fit_the_box_and_the_budget(self):
        # C2 (2026-10-24): every small copy fits THUMB_BOX (480 x 320) and is 60 KB or less, and the photo step reads the
        # box from this exact line; the Home logo (hero.jpg) stays 624 x 535 and is re-saved at q75 (159 KB -> 90 KB)
        self.assertRegex(read("build.py"), r'(?m)^THUMB_BOX = \(480, 320\)\r?$')
        d = os.path.join(ROOT, *build.THUMB_DIR.split("/"))
        names = [f for f in os.listdir(d) if f.endswith(".jpg")]
        self.assertGreater(len(names), 300)
        for f in names:
            p = os.path.join(d, f)
            w, h = build.image_size(p) if hasattr(build, "image_size") else (None, None)
            self.assertLessEqual(os.path.getsize(p), 60 * 1024, f)
            if w:
                self.assertLessEqual(w, build.THUMB_BOX[0], f)
                self.assertLessEqual(h, build.THUMB_BOX[1], f)
        hero = os.path.join(ROOT, "images", "hero.jpg")
        self.assertLessEqual(os.path.getsize(hero), 95 * 1024)
        if hasattr(build, "image_size"):
            self.assertEqual(build.image_size(hero), (624, 535))

    def test_thumb_dir_line_is_where_the_photo_step_looks(self):
        # the private photo step on OMEN (make_card_thumbs.py) makes the small copies only when it finds this exact line
        self.assertRegex(read("build.py"), r'(?m)^THUMB_DIR = "images/thumbs"\r?$')

    def test_menu_has_catalog_and_every_page_has_the_header_search(self):
        # W4 (Douglas 2026-10-17): Catalog joins the menu on every page; every page has the same header search
        want = ["Home", "Shop", "Catalog", "Sale", "About", "Feedback"]
        current = {"index.html": "Home", "shop.html": "Shop", "sale.html": "Sale", "about.html": "About",
                   "feedback.html": "Feedback", "catalog/index.html": "Catalog"}
        item = os.path.join("catalog", "item", sorted(os.listdir(os.path.join(ROOT, "catalog", "item")))[0])
        current[item.replace(os.sep, "/")] = "Catalog"
        for name, here in current.items():
            t = read(name)
            head = t[t.index('<header class="selvage">'):t.index("</header>")]
            menu = head[head.index('<nav class="menu"'):head.index("</nav>")]
            self.assertEqual(re.findall(r'<li><a href="[^"]+"(?: aria-current="page")?>([^<]+)</a></li>', menu), want, name)
            self.assertEqual(re.findall(r'aria-current="page">([^<]+)<', menu), [here], name)
            forms = re.findall(r'<form class="hfind" action="(/?catalog/)" method="get" role="search" aria-label="Site search">', head)
            self.assertEqual(len(forms), 1, name)
            self.assertIn('<input id="hq" name="q" type="search"', head, name)
        css = read("assets/catalog.css")
        self.assertIn(".hfind {", css)
        # phones: Home, Shop and the catalog show their own catalog search right under the header instead
        for name in ("index.html", "shop.html"):
            self.assertIn("@media (max-width: 43.99rem) { .hfind { display: none; } }", read(name), name)
        self.assertIn('<body class="own-search">', read("catalog/index.html"))
        self.assertIn("@media (max-width: 43.99rem) { .own-search .hfind { display: none; } }", css)

    def test_every_search_on_the_site_searches_our_catalog(self):
        # W4 (Douglas 2026-10-17): one search behavior - every search box and Shop's popular searches open our catalog;
        # the catalog's no-results panel still offers the eBay store search
        for name in build.PAGES + ["catalog/index.html"]:
            t = read(name)
            for form in re.findall(r"<form[^>]*>", t):
                if name == "catalog/index.html" and 'onsubmit="return false"' in form:
                    continue                     # the catalog's own box filters the page in place
                self.assertRegex(form, r'action="/?catalog/"', f"{name}: {form}")
                self.assertNotIn("ebay.com", form, name)
            self.assertNotIn("_nkw=", t, name)
            self.assertNotIn('name="_nkw"', t, name)
        chips = re.findall(r'<a class="perf" href="catalog/\?q=([^"]+)">', read("shop.html"))
        self.assertGreaterEqual(len(chips), 10)
        empty = read("catalog/index.html")
        empty = empty[empty.index('id="empty"'):]
        self.assertIn(f'<a href="{build.STORE}" target="stampede_ebay">search the eBay store</a>', empty[:600])

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
        imgs = re.findall(r'src="(images/(?:catalog|thumbs)/\d+(?:-e)?\.jpg)"', block)
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
        imgs = re.findall(r'src="(images/(?:catalog|thumbs)/\d+(?:-e)?\.jpg)"', block)
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
                           r'<span class="slot"><img src="(images/(?:catalog|thumbs)/\d+(?:-e)?\.jpg)"', text)
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
                           r'<img src="(images/(?:catalog|thumbs)/\d+(?:-e)?\.jpg)"[^>]*></span><b>([^<]+)</b><span>(\d+) on the shelf</span>',
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
        elsewhere |= {item_of(i) for i in re.findall(r'src="(images/(?:catalog|thumbs)/\d+(?:-e)?\.jpg)"', block(home, "CATEGORY-SHEET"))}
        elsewhere |= {item_of(i) for i in re.findall(r'src="(images/(?:catalog|thumbs)/\d+(?:-e)?\.jpg)"', block(about, "ABOUT-KINDS"))}
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

    def test_hand_written_pages_have_canonical_share_image_and_icon(self):
        # W6 (2026-10-17): one canonical matching the sitemap, a share image and an icon (so no /favicon.ico 404), and the
        # brand font from our own copy only (W9: the cross-site second source is gone)
        sitemap = read("sitemap.xml")
        for name in build.PAGES:
            t = read(name)
            h = t[:t.index("</head>")]
            want = f"{build.SITE}/" if name == "index.html" else f"{build.SITE}/{name}"
            self.assertEqual(re.findall(r'<link rel="canonical" href="([^"]+)">', h), [want], name)
            self.assertIn(f"<loc>{want}</loc>", sitemap, name)
            self.assertEqual(len(re.findall(r'<meta property="og:image" content="https://www\.stampedestamps\.store/images/[^"]+">', h)), 1, name)
            self.assertEqual(re.findall(r'<link rel="icon" href="([^"]+)"', h), ["images/icon-32.png"], name)   # W14
            self.assertNotIn("stampedestamps.github.io/stampede-stories/assets/fonts", t, name)
            desc = re.search(r'<meta name="description" content="([^"]*)">', h).group(1)
            self.assertEqual(re.search(r'<meta property="og:description" content="([^"]*)">', h).group(1), desc, name)
            if name == "feedback.html":   # hard rule 12: no undated trust number in the search snippet
                self.assertNotIn("%", desc)

    def test_every_page_uses_the_small_site_icon(self):
        # W14 (2026-10-18): the browser-tab icon was images/hero.jpg (the 156 KB logo photo) on every page; now a 32 x 32
        # PNG (+ a 180 x 180 home-screen icon) made from the same logo. hero.jpg stays the Home logo and the share image.
        def png_size(rel):
            with open(os.path.join(ROOT, rel), "rb") as f:
                head = f.read(24)
            self.assertEqual(head[:8], b"\x89PNG\r\n\x1a\n", rel)
            return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big"), os.path.getsize(os.path.join(ROOT, rel))
        w, h, n = png_size("images/icon-32.png")
        self.assertEqual((w, h), (32, 32)); self.assertLess(n, 8 * 1024)
        w, h, n = png_size("images/apple-touch-icon.png")
        self.assertEqual((w, h), (180, 180)); self.assertLess(n, 40 * 1024)
        hand = [(name, read(name), "images/") for name in build.PAGES]
        built = [(p[len(ROOT) + 1:], open(p, encoding="utf-8").read(), "/images/") for p in pages()]
        for name, t, pre in hand + built:
            head = t[:t.index("</head>")]
            self.assertEqual(re.findall(r'<link rel="icon" href="([^"]+)" type="image/png" sizes="32x32">', head), [pre + "icon-32.png"], name)
            self.assertEqual(re.findall(r'<link rel="apple-touch-icon" href="([^"]+)">', head), [pre + "apple-touch-icon.png"], name)
            self.assertEqual(len(re.findall(r'<link rel="(?:shortcut )?icon"', head)), 1, name)

    def test_every_page_preloads_the_brand_font_it_uses(self):
        # W12 step 1 (2026-10-18): the League Gothic file is preloaded from the page head, so it arrives with the HTML
        # instead of after the CSS is read; the preload must name the SAME address as the @font-face (else it downloads twice)
        font = "fonts/league-gothic-latin-400-normal.woff2"
        self.assertTrue(os.path.exists(os.path.join(ROOT, font)))
        css = read("assets/catalog.css")
        self.assertIn(f'url("/{font}")', css)
        hand = [(name, read(name), font) for name in build.PAGES]
        built = [(p[len(ROOT) + 1:], open(p, encoding="utf-8").read(), "/" + font) for p in pages()]
        for name, t, want in hand + built:
            head = t[:t.index("</head>")]
            self.assertEqual(re.findall(r'<link rel="preload" href="([^"]+)" as="font" type="font/woff2" crossorigin>', head), [want], name)
            self.assertEqual(head.count('rel="preload"'), 1, name)
            if name in build.PAGES:   # the hand-written pages carry their own @font-face with the same relative address
                self.assertIn(f'url("{font}")', t, name)
        # W12 step 2 (2026-10-22): font-display optional everywhere the font is declared, so a late font never swaps in
        # and moves the page (the preload above makes it arrive in time on most connections)
        for name in build.PAGES + ["assets/catalog.css"]:
            t = read(name)
            self.assertEqual(t.count("font-display: optional;"), 1, name)
            self.assertNotIn("font-display: swap", t, name)

    def test_structured_data_parses_and_never_carries_a_price(self):
        # W6: one JSON-LD block, on Home (OnlineStore); hard rule 1 - no price, offer or rating, ever. The catalog's
        # ItemList was left out (parent ruling 2026-10-17): Google shows ItemList carousels only for courses, movies,
        # recipes and restaurants, and the sitemap already lists every item page.
        found = []
        for name in build.PAGES + ["catalog/index.html"]:
            for blk in ld_blocks(read(name)):
                data = json.loads(blk)
                found.append((name, data["@type"]))
                self.assertNotIn("$", blk, name)
                for k, v, holder in walk(data):
                    self.assertIsNone(re.search(r"(?i)price|offer|rating", k), f"{name}: {k}")
        self.assertEqual(found, [("index.html", "OnlineStore")])
        home = json.loads(ld_blocks(read("index.html"))[0])
        self.assertEqual(home["url"], f"{build.SITE}/")
        self.assertIn(build.STORE, home["sameAs"])
        for p in pages()[1:]:
            self.assertNotIn("application/ld+json", open(p, encoding="utf-8").read(), os.path.basename(p))

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

    def test_issue_facts_are_checked_facts_only(self):
        # Douglas 2026-10-17, "Checked facts only": every row names the authority that checked it; no dollar sign
        # (a dollar face value is spelled out), no condition grade, no value talk
        facts, titles = build.load_issues()
        self.assertGreater(len(facts), 0)
        banned = re.compile(r"(?i)\$|\b(?:price[ds]?|values?|valued|worth|rare|scarce|invest\w*|mnh|hinged|hinge|"
                            r"centering|centered|gum|grade[ds]?|superb|xf|vf|premium)\b")
        for s, f in facts.items():
            self.assertTrue(f["issue"] and f["issued"] and f["face"], s)
            self.assertIn(f["source"], ("Scott catalog", "web"), s)
            # Douglas 2026-10-18, "facts checked against the Scott catalog or USPS, shown with their source": links go to
            # USPS pages only (the Postal Bulletin archive serves only http), never to a dealer's or anyone else's site
            usps = ("https://about.usps.com/", "https://www.stampsforever.com/", "http://www.uspostalbulletins.com/")
            urls = f["source_url"].split()
            self.assertTrue(all(u.startswith(usps) for u in urls), f"{s}: {urls}")
            self.assertTrue(urls if f["source"] == "web" else True, s)
            for k, v in f.items():
                if k not in ("source_url",):
                    self.assertIsNone(banned.search(v), f"{s} {k}: {v}")
        for t, ss in titles.items():
            self.assertTrue(ss and all(s in facts for s in ss), t)

    def test_item_pages_show_issue_facts_only_when_every_number_is_checked(self):
        facts, titles = build.load_issues()
        shown = 0
        for r in self.rows:
            text = open(os.path.join(ROOT, "catalog", "item", f"{r['item']}.html"), encoding="utf-8").read()
            mapped = titles.get(r["title"]) or []
            self.assertEqual('<section class="issue"' in text, bool(mapped) and all(s in facts for s in mapped), r["item"])
            if '<section class="issue"' not in text:
                continue
            shown += 1
            start = text.index('<section class="issue"')
            sec = text[start:text.index("</section>", start)]
            self.assertLess(text.index('<div class="buy">'), start, r["item"])   # below the button: first screen unchanged
            for s in mapped:
                self.assertIn(html.escape(facts[s]["issue"], quote=True), sec, f"{r['item']} {s}")
            self.assertIn("The eBay listing shows this item's own photos and condition.", sec, r["item"])
        self.assertGreater(shown, 0)
        one = build.issue_box("T", ({"1": {"issue": "A Issue", "issued": "May 1, 1950", "face": "3¢", "source": "Scott catalog",
                                           "source_url": ""}}, {"T": ["1"]}))
        self.assertIn("<b>Issue</b><span>A Issue</span>", one)
        self.assertIn("Checked against the Scott catalog.", one)
        self.assertEqual(build.issue_box("T", ({"1": {"issue": "A", "issued": "1950", "face": "3¢", "source": "Scott catalog",
                                                       "source_url": ""}}, {"T": ["1", "2"]})), "")   # never in part


if __name__ == "__main__":
    unittest.main()
