"""Issue pages (Claude 2026-11-21): one stable page per checked Scott number, /catalog/issue/scott-<number>.html.

Why: Search Console on 2026-11-15 showed Google keeping all 6 stable pages but only 109 of 440 live item pages, and an
item page dies when eBay gives a relisted stamp a new number. These pages are built from data/issue_facts.csv alone, so
they exist whether or not anything is listed, and they link to whichever listings are live that morning. They are a
TEST of the claim that such pages get indexed and stay indexed (not proven; the pull request says how it is read).

IssuePageRuleTests use made-up listings and need no build. IssuePagesBuiltTests read the built site; they build it only
when the main test file's BuildTests has not already built it in this run (the nightly refresh runs every test file in
one process, and each build takes a while on the shared drive).
"""
import csv, html, os, re, subprocess, sys, tempfile, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import build  # noqa: E402

FIELDS = ("scott issue purpose issued first_day face color design inscribed designer printing perf format quantity "
          "source source_url").split()
META = {"as_of": "2026-11-20"}
# a well-formed Scott number as data/issue_facts.csv keys it, written out here on purpose (not taken from build.py)
WELL_FORMED = re.compile(r"[A-Z]{0,4}[1-9]\d{0,3}[A-Z]?")
PRICE_WORDS = re.compile(r"(?i)\b(price[d]? at|our price|only \d|deal|bargain)\b")


def fact(scott, issue="Liberty Issue", issued="April 9, 1954", face="8¢", **more):
    row = dict.fromkeys(FIELDS, "")
    row.update(scott=scott, issue=issue, issued=issued, face=face, source="Scott catalog")
    row.update(more)
    return row


def listing(item, title, fmt="FIXED_PRICE", category="Sheets"):
    return {"item": item, "title": title, "category": category, "condition": "", "format": fmt,
            "ebay_url": f"https://www.ebay.com/itm/{item}", "ends": "2026-12-01T00:00:00Z"}


def main_part(page):
    return page[page.index('<main id="main">'):page.index("</main>")]


def buttons(page):
    """[(filled, address, target, words)] for every button in the page's main part, in page order."""
    return [(alt == "", href, target or None, words) for alt, href, target, words in
            re.findall(r'<a class="btn( alt)?" href="([^"]+)"(?: target="([^"]+)")?[^>]*>([^<]+)</a>', main_part(page))]


def section(page, heading):
    """The whole <section> whose heading reads `heading`; None when the page has none."""
    m = re.search(r"<section[^>]*>\s*<h2[^>]*>" + re.escape(heading) + r"</h2>.*?</section>", page, re.S)
    return m.group(0) if m else None


def catalog_order(key):
    """Plain numbers by size, then lettered prefixes; written out here on purpose (not taken from build.py)."""
    prefix, number, suffix = re.fullmatch(r"([A-Z]*)(\d+)([A-Z]?)", key).groups()
    return (prefix != "", prefix, int(number), suffix)


class IssuePageRuleTests(unittest.TestCase):
    """The rules on made-up issues and listings (no build needed)."""

    # ---- the address and the number ----

    def test_the_address_is_the_scott_number_in_small_letters(self):
        for key, want in (("1016", "/catalog/issue/scott-1016.html"), ("C3", "/catalog/issue/scott-c3.html"),
                          ("1830A", "/catalog/issue/scott-1830a.html"), ("R5C", "/catalog/issue/scott-r5c.html"),
                          ("RW1", "/catalog/issue/scott-rw1.html"), ("1338F", "/catalog/issue/scott-1338f.html")):
            self.assertEqual(build.issue_url(key), want, key)

    def test_a_key_that_is_not_a_scott_number_gets_no_address(self):
        # R024C is in the data today (a listing title's slip): a Scott number never starts with 0. Nothing that could
        # leave the folder or name another file may become an address either.
        for key in ("R024C", "0", "012", "", " 1016", "1016 ", "10/16", "../x", "12345", "ABCDE1", "1016AB", "c3",
                    "1016a", "#1016", "10 16", "1016.html", None):
            self.assertIsNone(build.issue_url(key), repr(key))

    def test_the_number_is_written_as_the_scott_catalog_writes_it(self):
        # a letter after the number is small (a pane, block or variety; 1368 "a. Block of 4") unless the catalog lists
        # a major number with a capital: 1338F, read off the Scott catalog page on 2026-11-21 like the 1368 line
        for key, want in (("1016", "1016"), ("1830A", "1830a"), ("R5C", "R5c"), ("634D", "634d"), ("1338F", "1338F"),
                          ("C3", "C3"), ("RI18", "RI18"), ("R024C", "R024C")):
            self.assertEqual(build.scott_text(key), want, key)

    def test_pages_come_in_catalog_order_and_a_bad_key_is_left_out(self):
        keys = ["C10", "1830A", "147", "R5C", "1016", "C7", "1830", "RI18", "R118", "R024C", "6030"]
        facts = {k: fact(k) for k in keys}
        self.assertEqual(build.issue_keys(facts),
                         ["147", "1016", "1830", "1830A", "6030", "C7", "C10", "R5C", "R118", "RI18"])

    # ---- which listings a page shows ----

    def test_the_shelf_holds_each_issues_listings_newest_first_and_a_lot_under_every_number_it_names(self):
        facts = {"1": fact("1"), "2": fact("2"), "3": fact("3")}
        titles = {"Single": ["1"], "Lot": ["1", "2"], "Part checked": ["2", "9"]}     # 9 has no checked row
        rows = [listing("100", "Single"), listing("300", "Single"), listing("200", "Lot"),
                listing("400", "Part checked"), listing("500", "Not mapped")]
        shelf = build.issue_shelf(rows, (facts, titles))
        self.assertEqual({k: [r["item"] for r in v] for k, v in shelf.items()},
                         {"1": ["300", "200", "100"], "2": ["200"]})     # a lot is never shown in part; 3 has nothing

    # ---- the three states of a page ----

    def one_issue(self, rows, imgs=None, **more):
        facts = {"1016": fact("1016", issue="International Red Cross Issue", issued="November 21, 1952", face="3¢",
                              color="deep blue & carmine", **more)}
        titles = {r["title"]: ["1016"] for r in rows}
        shelf = build.issue_shelf(rows, (facts, titles))
        return build.build_issue("1016", ["1016"], facts, shelf, imgs or {}, META)

    def test_every_state_names_the_issue_its_checked_facts_and_its_own_address(self):
        for rows in ([], [listing("100", "Sheet of 50")], [listing("100", "Copy A"), listing("300", "Copy B")]):
            page = self.one_issue(rows)
            self.assertIn("<h1>Scott #1016 <span>International Red Cross Issue</span></h1>", page)
            self.assertEqual(page.count("<h1"), 1)
            self.assertIn('<p class="key">Face value 3¢. Issued November 21, 1952.</p>', page)
            about = section(page, "About this issue")
            self.assertIn("<b>Issue</b><span>International Red Cross Issue</span>", about)
            self.assertIn("<b>Issued</b><span>November 21, 1952</span>", about)
            self.assertIn("<b>Color</b><span>deep blue &amp; carmine</span>", about)
            self.assertNotIn("<b>Designed by</b>", about)                  # an empty field is not shown
            self.assertIn("Checked against the Scott catalog.", about)
            self.assertIn('<link rel="canonical" href="https://www.stampedestamps.store/catalog/issue/scott-1016.html">',
                          page)
            self.assertIn("<title>Scott #1016: International Red Cross Issue, November 21, 1952 | StampedeUSA</title>",
                          page)
            self.assertIn('<p class="asof">Listings as of 2026-11-20.</p>', page)
            self.assertNotIn("$", page)
            self.assertIsNone(PRICE_WORDS.search(page))
            self.assertNotIn('name="robots"', page)                        # never hidden from search engines
            self.assertNotIn('http-equiv="refresh"', page)                 # never a redirect
            self.assertTrue(page.rstrip().endswith(build.CF_BEACON.strip() + "\n</body>\n</html>"))

    def test_one_listing_gets_the_one_filled_button_to_its_ebay_listing_and_a_link_to_its_catalog_page(self):
        page = self.one_issue([listing("100", "Scott #1016 (MNH) 1952 3c Red Cross <Sheet> of 50")])
        self.assertEqual(buttons(page), [(True, "https://www.ebay.com/itm/100", "stampede_ebay", "See it on eBay")])
        self.assertIn('On the shelf now: <a href="/catalog/item/100.html">Scott #1016 (MNH) 1952 3c Red Cross '
                      '&lt;Sheet&gt; of 50</a>.', page)
        self.assertIn("Price, bids and every photo are on the eBay listing.", page)
        self.assertIn("The eBay listing shows this item's own photos and condition.", section(page, "About this issue"))
        self.assertEqual(page.count('href="https://www.ebay.com/itm/100"'), 1)

    def test_one_listings_photo_loads_at_once_with_its_size_and_opens_its_catalog_page(self):
        seg = lambda m, body: b"\xff" + bytes([m]) + (len(body) + 2).to_bytes(2, "big") + body
        jpeg = b"\xff\xd8" + seg(0xC0, b"\x08" + (200).to_bytes(2, "big") + (300).to_bytes(2, "big") + b"\x03" + b"\x00" * 9)
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "images", "catalog"))
            with open(os.path.join(d, "images", "catalog", "100.jpg"), "wb") as f:
                f.write(jpeg)
            with mock.patch.object(build, "HERE", d):
                page = self.one_issue([listing("100", "Sheet of 50")], imgs={"100": "images/catalog/100.jpg"})
        self.assertIn('<a href="/catalog/item/100.html"><span class="ph"><img src="/images/catalog/100.jpg" '
                      'alt="Sheet of 50" width="300" height="200" fetchpriority="high"></span></a>', page)
        self.assertIn('<meta property="og:image" content="https://www.stampedestamps.store/images/catalog/100.jpg">', page)

    def test_several_listings_each_get_a_card_and_an_outlined_ebay_button_and_none_leads(self):
        page = self.one_issue([listing("100", "Copy A"), listing("300", "Copy B", fmt="AUCTION")])
        self.assertEqual(buttons(page), [(False, "https://www.ebay.com/itm/300", "stampede_ebay", "See it on eBay"),
                                         (False, "https://www.ebay.com/itm/100", "stampede_ebay", "See it on eBay")])
        shelf = section(page, "On the shelf now")
        self.assertEqual(re.findall(r'<a class="card perf edged" href="(/catalog/item/\d+\.html)">', shelf),
                         ["/catalog/item/300.html", "/catalog/item/100.html"])          # newest first
        self.assertEqual(re.findall(r'<span class="badge">([^<]+)</span>', shelf), ["Auction", "Buy It Now"])
        self.assertIn('aria-label="See it on eBay: Copy B"', shelf)      # two buttons with the same words: named apart
        self.assertIn("2 listings of this issue are on the shelf.", page)
        self.assertIn("Each eBay listing shows that item's own photos and condition.", section(page, "About this issue"))

    def test_no_listing_keeps_a_useful_page_that_does_not_read_like_a_missing_one(self):
        page = self.one_issue([])
        self.assertEqual(buttons(page), [(True, "/catalog/", None, "Browse the full catalog")])
        self.assertIn("No listing is filed under this issue today.", page)
        self.assertNotIn("ebay.com/itm/", page)
        self.assertNotIn("/catalog/item/", page)
        self.assertIsNone(section(page, "On the shelf now"))
        about = section(page, "About this issue")
        self.assertIn("<b>Face value</b><span>3¢</span>", about)
        self.assertIn("Checked against the Scott catalog.", about)
        self.assertNotIn("eBay listing", about)                 # nothing is listed, so no listing is spoken of
        # the words search engines read as "nothing here" (a soft 404) stay off the page
        self.assertIsNone(re.search(r"(?i)not found|sold out|no longer available|unavailable|out of stock", main_part(page)))

    def test_a_usps_checked_issue_links_its_usps_sources_and_nothing_else_outside(self):
        page = self.one_issue([], source="web", source_url="https://about.usps.com/a https://about.usps.com/b")
        about = section(page, "About this issue")
        self.assertIn('Checked against about.usps.com (<a href="https://about.usps.com/a">1</a>, '
                      '<a href="https://about.usps.com/b">2</a>).', about)
        outside = {u for u in re.findall(r'href="(https?://[^"]+)"', main_part(page))}
        self.assertEqual(outside, {"https://about.usps.com/a", "https://about.usps.com/b"})

    # ---- links between issue pages ----

    def test_also_in_this_issue_lists_the_other_numbers_with_the_same_name_and_date(self):
        facts = {k: fact(k, issue="Boston Tea Party", issued="July 4, 1973", design=d) for k, d in
                 (("1480", "British Merchantman"), ("1481", "British Three-master"), ("1482", "Boats and Ship's Hull"),
                  ("1483", ""))}
        facts["1484"] = fact("1484", issue="American Arts Issue", issued="February 28, 1973")
        facts["1485"] = fact("1485", issue="Boston Tea Party", issued="July 5, 1973")        # same name, another date
        facts["R024C"] = fact("R024C", issue="Boston Tea Party", issued="July 4, 1973")      # no page, so never linked
        facts["1482"].update(source="web", source_url="https://about.usps.com/x")
        keys = build.issue_keys(facts)
        page = build.build_issue("1481", keys, facts, {}, {}, META)
        also = section(page, "Also in this issue")
        self.assertEqual(re.findall(r'<li><b><a href="([^"]+)">([^<]+)</a></b><span>([^<]*)</span></li>', also),
                         [("/catalog/issue/scott-1480.html", "Scott #1480", "British Merchantman, 8¢"),
                          ("/catalog/issue/scott-1482.html", "Scott #1482", "Boats and Ship&#x27;s Hull, 8¢"),
                          ("/catalog/issue/scott-1483.html", "Scott #1483", "8¢")])
        self.assertNotIn("R024C", page)
        # rule 2, facts shown with their source: these lines are other rows' facts, so their own sources are named
        self.assertIn('<p class="note">Checked against the Scott catalog. Checked against '
                      '<a href="https://about.usps.com/x">about.usps.com</a>.</p>', also)
        self.assertIsNone(section(build.build_issue("1484", keys, facts, {}, {}, META), "Also in this issue"))

    def nearby(self, key, keys, facts, shelf, imgs):
        more = section(build.build_issue(key, keys, facts, shelf, imgs, META), "More issues on the shelf")
        return re.findall(r'<a class="card perf" href="/catalog/issue/scott-(\w+)\.html">', more or "")

    def test_more_issues_on_the_shelf_shows_the_nearest_photographed_ones_and_never_a_repeat(self):
        keys = [str(n) for n in range(1, 11)]
        facts = {k: fact(k, issue=f"Issue {k}", issued=f"May {k}, 1950") for k in keys}
        facts["6"] = fact("6", issue="Issue 5", issued="May 5, 1950")           # 6 is "also in this issue" for 5
        lot = listing("700", "Lot of 7 and 8")
        shelf = {"1": [listing("100", "One")], "3": [listing("300", "Three")], "4": [listing("400", "Four, no photo")],
                 "5": [listing("500", "Five")], "6": [listing("600", "Six")], "7": [lot], "8": [lot],
                 "9": [listing("900", "Nine")], "10": [listing("1000", "Ten")]}
        imgs = {i: f"images/catalog/{i}.jpg" for i in ("100", "300", "500", "600", "700", "900", "1000")}
        # for 5: 6 is its own issue, 4 has no photo, 8 would show the lot already shown for 7, 2 has no listing
        self.assertEqual(self.nearby("5", keys, facts, shelf, imgs), ["1", "3", "7", "9", "10"])
        page = build.build_issue("5", keys, facts, shelf, imgs, META)
        card = re.search(r'<li class="edged"><a class="card perf" href="/catalog/issue/scott-7\.html">.*?</li>', page).group(0)
        self.assertIn('<img src="/images/catalog/700.jpg" alt="Lot of 7 and 8" loading="lazy">', card)
        self.assertIn('<span class="t">Scott #7: Issue 7</span><span class="c">May 7, 1950</span>', card)
        # rule 2: the cards carry other rows' names and dates, so the section says who checked them and where to look
        self.assertIn("Names and dates checked against the Scott catalog or USPS; each issue's page names its source.",
                      section(page, "More issues on the shelf"))
        # a listing this page already shows is not shown again for a neighbor (8 shares the lot with 7)
        self.assertNotIn("7", self.nearby("8", keys, facts, shelf, imgs))

    def test_more_issues_on_the_shelf_stops_at_six_cards_the_nearest_ones(self):
        keys = [str(n) for n in range(1, 21)]
        facts = {k: fact(k, issue=f"Issue {k}", issued=f"May {k}, 1950") for k in keys}
        shelf = {k: [listing(k + "00", f"Listing {k}")] for k in keys}
        imgs = {k + "00": f"images/catalog/{k}00.jpg" for k in keys}
        self.assertEqual(self.nearby("10", keys, facts, shelf, imgs), ["7", "8", "9", "11", "12", "13"])
        self.assertEqual(self.nearby("1", keys, facts, shelf, imgs), ["2", "3", "4", "5", "6", "7"])
        self.assertEqual(self.nearby("1", ["1"], {"1": facts["1"]}, shelf, imgs), [])        # no section when alone
        # with nothing listed under 9 the sixth card is 14 (four steps on), and 6 (also four steps away) is not a seventh
        del shelf["9"]
        self.assertEqual(self.nearby("10", keys, facts, shelf, imgs), ["7", "8", "11", "12", "13", "14"])

    def test_the_pager_walks_every_page_in_catalog_order(self):
        keys = ["147", "1016", "C3"]
        facts = {k: fact(k) for k in keys}

        def pager(key):
            page = build.build_issue(key, keys, facts, {}, {}, META)
            nav = page[page.index('<nav class="pager"'):]
            return re.findall(r'<a href="([^"]+)"(?: rel="(prev|next)")?>([^<]+)</a>', nav[:nav.index("</nav>")])
        self.assertEqual(pager("147"), [("/catalog/issue/", "", "All Scott numbers"),
                                        ("/catalog/issue/scott-1016.html", "next", "Scott #1016")])
        self.assertEqual(pager("1016"), [("/catalog/issue/scott-147.html", "prev", "Scott #147"),
                                         ("/catalog/issue/", "", "All Scott numbers"),
                                         ("/catalog/issue/scott-c3.html", "next", "Scott #C3")])
        self.assertEqual(pager("C3"), [("/catalog/issue/scott-1016.html", "prev", "Scott #1016"),
                                       ("/catalog/issue/", "", "All Scott numbers")])

    # ---- the index ----

    def test_the_index_lists_every_page_once_in_catalog_order_with_its_listing_count(self):
        facts = {"147": fact("147", issue="Regular issue", issued="1870-71", face="3¢"),
                 "1016": fact("1016", issue="Red Cross & Friends", issued="November 21, 1952", face="3¢"),
                 "1830A": fact("1830A", issue="Coral Reefs Issue", issued="August 26, 1980", face="15¢"),
                 "C7": fact("C7"), "R5C": fact("R5C"), "R118": fact("R118")}
        keys = build.issue_keys(facts)
        shelf = {"147": [listing("100", "a")], "1830A": [listing("200", "b"), listing("300", "c")]}
        page = build.build_issue_index(keys, facts, shelf, META)
        rows = re.findall(r'<li><a href="/catalog/issue/scott-(\w+)\.html">([^<]+)</a><span class="what">([^<]*)</span>'
                          r'(?:<span class="on">([^<]+)</span>)?</li>', page)
        self.assertEqual(rows, [("147", "Scott #147", "Regular issue &middot; 1870-71 &middot; 3¢", "1 listing"),
                                ("1016", "Scott #1016", "Red Cross &amp; Friends &middot; November 21, 1952 &middot; 3¢", ""),
                                ("1830a", "Scott #1830a", "Coral Reefs Issue &middot; August 26, 1980 &middot; 15¢", "2 listings"),
                                ("c7", "Scott #C7", "Liberty Issue &middot; April 9, 1954 &middot; 8¢", ""),
                                ("r5c", "Scott #R5c", "Liberty Issue &middot; April 9, 1954 &middot; 8¢", ""),
                                ("r118", "Scott #R118", "Liberty Issue &middot; April 9, 1954 &middot; 8¢", "")])
        # sections: plain numbers by the thousand, then one per letter prefix; each jump link lands on a section
        self.assertEqual(re.findall(r"<h2[^>]*>([^<]+)</h2>", page),
                         ["Scott #147", "Scott #1016 to #1830a", "Scott #C7", "Scott #R5c to #R118"])
        jumps = re.findall(r'<li><a href="#([\w-]+)">([^<]+)</a></li>', page)
        self.assertEqual([words for _, words in jumps], ["#147", "#1016 to #1830a", "#C7", "#R5c to #R118"])
        for anchor, _ in jumps:
            self.assertEqual(page.count(f'id="{anchor}"'), 1, anchor)
        self.assertIn("6 stamp issues", page)
        self.assertIn("2 are on the shelf today", page)
        # rule 2: 6 rows' names, dates and face values are shown here, so the page says who checked them
        self.assertIn("checked against the Scott catalog or USPS", page)
        self.assertIn("each number's page names its source", page)
        self.assertIn('<p class="asof">Listings as of 2026-11-20.</p>', page)
        self.assertIn('<link rel="canonical" href="https://www.stampedestamps.store/catalog/issue/">', page)
        self.assertEqual(page.count("<h1"), 1)
        self.assertNotIn("$", page)
        self.assertIsNone(PRICE_WORDS.search(page))

    # ---- the links from item pages ----

    def test_an_item_pages_box_links_to_its_issue_page(self):
        box = build.issue_box("T", ({"1016": fact("1016", issue="Red Cross & Friends")}, {"T": ["1016"]}))
        self.assertIn('Every listing of this issue: <a href="/catalog/issue/scott-1016.html">Scott #1016, '
                      "Red Cross &amp; Friends</a>.", box)

    def test_a_box_for_several_numbers_links_each_number_written_the_catalogs_way(self):
        facts = {"1830": fact("1830", issue="Coral Reefs Issue", design="Finger Coral"),
                 "1830A": fact("1830A", issue="Coral Reefs Issue", design="Block of 4"),
                 "R5C": fact("R5C", issue="First Issue", issued="1862-71", face="2¢")}
        same = build.issue_box("T", (facts, {"T": ["1830A", "1830"]}))
        self.assertIn('<li><b><a href="/catalog/issue/scott-1830a.html">Scott #1830a</a></b><span>Block of 4, 8¢</span></li>', same)
        self.assertIn('<li><b><a href="/catalog/issue/scott-1830.html">Scott #1830</a></b><span>Finger Coral, 8¢</span></li>', same)
        mixed = build.issue_box("T", (facts, {"T": ["1830", "R5C"]}))
        self.assertIn('<li><b><a href="/catalog/issue/scott-r5c.html">Scott #R5c</a></b><span>First Issue, 1862-71, 2¢</span></li>', mixed)

    def test_a_box_for_a_key_without_a_page_stays_as_it_was_with_no_link(self):
        facts = {"R024C": fact("R024C", issue="First Issue", issued="1862-71", face="5¢"), "R5C": fact("R5C", issue="First Issue", issued="1862-71", face="2¢")}
        one = build.issue_box("T", (facts, {"T": ["R024C"]}))
        self.assertIn("<b>Issue</b><span>First Issue</span>", one)
        self.assertNotIn("/catalog/issue/", one)
        two = build.issue_box("T", (facts, {"T": ["R024C", "R5C"]}))
        self.assertIn("<li><b>Scott #R024C</b><span>5¢</span></li>", two)
        self.assertIn('<li><b><a href="/catalog/issue/scott-r5c.html">Scott #R5c</a></b><span>2¢</span></li>', two)


class IssuePagesBuiltTests(unittest.TestCase):
    """The built site: every issue page, the index, and the links into them."""

    @classmethod
    def setUpClass(cls):
        # One build serves every test file in a run: the main test file's BuildTests builds the site in its own
        # setUpClass and notes when ("started"). Run on its own, this file builds the site itself, with the real clock
        # like the 3:45 AM run. The module is looked up by name at run time, so nothing is imported twice.
        main_tests = sys.modules.get("test_" + "build")
        if main_tests is None or not hasattr(main_tests.BuildTests, "started"):
            env = {k: v for k, v in os.environ.items() if k != "STORE_BUILD_NOW"}
            subprocess.run([sys.executable, os.path.join(ROOT, "build.py")], check=True, capture_output=True, env=env)
        cls.facts, cls.titles = build.load_issues()
        with open(os.path.join(ROOT, "data", "listings.csv"), encoding="utf-8-sig", newline="") as f:
            cls.rows = list(csv.DictReader(f))
        cls.keys = sorted((k for k in cls.facts if WELL_FORMED.fullmatch(k)), key=catalog_order)
        cls.dir = os.path.join(ROOT, "catalog", "issue")

        def text_of(name):          # a page that is not there is the first test's failure to report, not a crash here
            path = os.path.join(cls.dir, name)
            if not os.path.exists(path):
                return None
            with open(path, encoding="utf-8") as f:
                return f.read()
        cls.page = {k: t for k in cls.keys for t in [text_of(f"scott-{k.lower()}.html")] if t is not None}
        cls.index = text_of("index.html") or ""
        cls.shelf = {}                           # {scott: [item, ...]} from the data files, newest first
        for r in sorted(cls.rows, key=lambda r: r["item"], reverse=True):
            mapped = cls.titles.get(r["title"]) or []
            if mapped and all(s in cls.facts for s in mapped):
                for s in mapped:
                    cls.shelf.setdefault(s, []).append(r["item"])
        cls.title_texts = sorted({html.escape(r["title"], quote=True) for r in cls.rows}, key=len, reverse=True)

    def test_one_page_per_checked_scott_number_plus_the_index_and_nothing_else(self):
        self.assertGreater(len(self.keys), 200)
        self.assertEqual(sorted(os.listdir(self.dir)), sorted(["index.html"] + [f"scott-{k.lower()}.html" for k in self.keys]))

    def test_no_address_holds_an_ebay_item_number_and_each_page_names_itself_as_canonical(self):
        seen = set()
        for k, t in self.page.items():
            name = f"scott-{k.lower()}.html"
            self.assertRegex(name, r"^scott-[a-z]{0,4}[1-9]\d{0,3}[a-z]?\.html$")
            self.assertIsNone(re.search(r"\d{9}", name), name)
            canon = re.findall(r'<link rel="canonical" href="([^"]+)">', t)
            self.assertEqual(canon, [f"{build.SITE}/catalog/issue/{name}"], k)
            self.assertNotIn(canon[0], seen)
            seen.add(canon[0])

    def test_every_page_has_its_own_title_and_description_one_heading_and_no_block_or_redirect(self):
        titles, descs = set(), set()
        for name, t in list(self.page.items()) + [("index", self.index)]:
            head = t[:t.index("</head>")]
            title = re.findall(r"<title>([^<]+)</title>", head)
            desc = re.findall(r'<meta name="description" content="([^"]+)">', head)
            self.assertEqual((len(title), len(desc)), (1, 1), name)
            self.assertNotIn(title[0], titles, name)
            self.assertNotIn(desc[0], descs, name)
            titles.add(title[0])
            descs.add(desc[0])
            self.assertEqual(len(re.findall(r"<h1\b", t)), 1, name)
            self.assertNotIn('name="robots"', t, name)
            self.assertNotIn('http-equiv="refresh"', t, name)
            self.assertNotIn(build.MOVED_MARK, t, name)
            self.assertNotIn("application/ld+json", t, name)
            self.assertLess(len(t.encode("utf-8")), 150 * 1024 if name == "index" else 40 * 1024, name)

    def test_the_state_of_each_page_follows_the_number_of_listings_filed_under_it(self):
        counts = {0: 0, 1: 0, 2: 0}
        for k, t in self.page.items():
            items = self.shelf.get(k, [])
            got = buttons(t)
            if len(items) == 1:
                self.assertEqual(got, [(True, f"https://www.ebay.com/itm/{items[0]}", "stampede_ebay", "See it on eBay")], k)
            elif items:
                self.assertEqual(got, [(False, f"https://www.ebay.com/itm/{i}", "stampede_ebay", "See it on eBay") for i in items], k)
            else:
                self.assertEqual(got, [(True, "/catalog/", None, "Browse the full catalog")], k)
                self.assertIn("No listing is filed under this issue today.", t, k)
            counts[min(len(items), 2)] += 1
            # every listing filed under the issue links to its own catalog page, and no other listing is linked
            linked = set(re.findall(r'href="/catalog/item/(\d+)\.html"', main_part(t)[:main_part(t).index('<section class="issue"')]))
            self.assertEqual(linked, set(items), k)
        self.assertGreater(counts[1], 0)

    def test_every_ebay_link_opens_a_live_listing_or_the_store_in_the_shop_window(self):
        live = {r["item"] for r in self.rows}
        for name, t in list(self.page.items()) + [("index", self.index)]:
            for tag in re.findall(r"<a [^>]*ebay\.com[^>]*>", t):
                self.assertIn('target="stampede_ebay"', tag, name)
                m = re.search(r'href="https://www\.ebay\.com/(?:itm/(\d+)|str/stampedeusa)"', tag)
                self.assertIsNotNone(m, f"{name}: {tag}")
                if m.group(1):
                    self.assertIn(m.group(1), live, name)

    def test_no_price_and_no_price_words_outside_listing_titles(self):
        for name, t in list(self.page.items()) + [("index", self.index)]:
            for title in self.title_texts:                 # a title may carry a face value; it is shown as written
                t = t.replace(title, "")
            self.assertNotIn("$", t, name)
            self.assertIsNone(PRICE_WORDS.search(t), name)

    def test_every_local_link_and_photo_exists(self):
        for name, t in list(self.page.items()) + [("index", self.index)]:
            for ref in re.findall(r'(?:href|src)="(/[^"#?]*)', t) + re.findall(r'srcset="(/[^" ]+) 1x, (/[^" ]+) 2x"', t):
                for one in ([ref] if isinstance(ref, str) else ref):
                    path = one.lstrip("/")
                    path = path + "index.html" if path == "" or path.endswith("/") else path
                    self.assertTrue(os.path.exists(os.path.join(ROOT, *path.split("/"))), f"{name} -> {one}")

    def test_links_between_issue_pages_lead_to_pages_that_exist_and_reach_every_page(self):
        reached = set(re.findall(r'href="/catalog/issue/(scott-\w+\.html)"', self.index))
        self.assertEqual(reached, {f"scott-{k.lower()}.html" for k in self.keys})          # the index reaches all
        order = [f"scott-{k.lower()}.html" for k in self.keys]
        for i, k in enumerate(self.keys):                                                   # and so does the pager
            nav = self.page[k][self.page[k].index('<nav class="pager"'):]
            nav = nav[:nav.index("</nav>")]
            self.assertEqual(re.findall(r'href="/catalog/issue/(scott-\w+\.html)" rel="prev"', nav), order[i - 1:i] if i else [], k)
            self.assertEqual(re.findall(r'href="/catalog/issue/(scott-\w+\.html)" rel="next"', nav), order[i + 1:i + 2], k)

    def test_every_page_has_the_visitor_counter_once_the_small_icon_the_font_preload_and_the_catalog_menu(self):
        tag = build.CF_BEACON.strip()
        with open(os.path.join(ROOT, "catalog", "index.html"), encoding="utf-8") as f:
            catalog = f.read()
        menu = lambda t: t[t.index('<header class="selvage">'):t.index("</header>")]
        for name, t in list(self.page.items()) + [("index", self.index)]:
            self.assertEqual(t.count("static.cloudflareinsights.com/beacon.min.js"), 1, name)
            self.assertLess(t.index(tag), t.index("</body>"), name)
            head = t[:t.index("</head>")]
            self.assertEqual(re.findall(r'<link rel="icon" href="([^"]+)" type="image/png" sizes="32x32">', head), ["/images/icon-32.png"], name)
            self.assertEqual(head.count('rel="preload"'), 1, name)
            self.assertEqual(menu(t), menu(catalog), name)             # same header, Catalog marked as the current part

    def test_every_class_an_issue_page_uses_has_a_rule_in_the_catalog_stylesheet(self):
        with open(os.path.join(ROOT, "assets", "catalog.css"), encoding="utf-8") as f:
            css = f.read()
        used = set()
        for t in list(self.page.values()) + [self.index]:
            for names in re.findall(r'class="([^"]+)"', t):
                used.update(names.split())
        self.assertIn("pager", used)
        for name in sorted(used):
            self.assertRegex(css, r"\." + re.escape(name) + r"(?![\w-])", f"class {name} has no rule in assets/catalog.css")

    def test_the_sitemap_lists_the_index_and_every_issue_page_once(self):
        with open(os.path.join(ROOT, "sitemap.xml"), encoding="utf-8") as f:
            locs = re.findall(r"<loc>([^<]+)</loc>", f.read())
        mine = [u for u in locs if "/catalog/issue/" in u]
        want = [f"{build.SITE}/catalog/issue/"] + [f"{build.SITE}/catalog/issue/scott-{k.lower()}.html" for k in self.keys]
        self.assertEqual(sorted(mine), sorted(want))
        self.assertEqual(len(locs), len(set(locs)))

    def test_item_pages_link_to_the_issue_pages_of_the_numbers_their_title_names(self):
        linked = 0
        for r in self.rows:
            with open(os.path.join(ROOT, "catalog", "item", f"{r['item']}.html"), encoding="utf-8") as f:
                t = f.read()
            mapped = self.titles.get(r["title"]) or []
            boxed = bool(mapped) and all(s in self.facts for s in mapped)
            want = {f"scott-{s.lower()}.html" for s in mapped if WELL_FORMED.fullmatch(s)} if boxed else set()
            self.assertEqual(set(re.findall(r'href="/catalog/issue/(scott-\w+\.html)"', t)), want, r["item"])
            linked += bool(want)
        self.assertGreater(linked, 0)

    def test_the_catalog_page_links_to_the_index_and_keeps_its_as_of_line_readable(self):
        with open(os.path.join(ROOT, "catalog", "index.html"), encoding="utf-8") as f:
            t = f.read()
        self.assertEqual(t.count('<a href="/catalog/issue/">'), 1)
        # Website PLANS\check_store_relists.py reads the feed date from this line with this pattern
        self.assertRegex(t, r"Listings as of (\d{4}-\d{2}-\d{2})\.")

    def test_the_index_lists_every_page_in_catalog_order_with_todays_counts(self):
        rows = re.findall(r'<li><a href="/catalog/issue/scott-(\w+)\.html">[^<]+</a><span class="what">[^<]*</span>'
                          r'(?:<span class="on">(\d+) listings?</span>)?</li>', self.index)
        self.assertEqual([k for k, _ in rows], [k.lower() for k in self.keys])
        self.assertEqual({k: int(n) for k, n in rows if n}, {k.lower(): len(v) for k, v in self.shelf.items() if k in self.page})
        listed = sum(1 for k in self.keys if self.shelf.get(k))
        self.assertGreater(listed, 1)
        self.assertIn(f"{len(self.keys)} stamp issues", self.index)
        self.assertIn(f"{listed} are on the shelf today", self.index)


if __name__ == "__main__":
    unittest.main()
