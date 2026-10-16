"""Checks for build.py (run: python -m unittest discover -s tests). Douglas 2026-10-11: no prices, one catalog."""
import csv, hashlib, html, os, re, subprocess, sys, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def rows():
    with open(os.path.join(ROOT, "data", "listings.csv"), encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def build():
    subprocess.run([sys.executable, os.path.join(ROOT, "build.py")], check=True, capture_output=True)


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


class BuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        build()
        cls.rows = rows()
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
        for p in pages():
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

    def test_canonicals_unique(self):
        seen = {}
        for p in pages():
            m = re.search(r'<link rel="canonical" href="([^"]+)"', open(p, encoding="utf-8").read())
            self.assertIsNotNone(m, p)
            self.assertNotIn(m.group(1), seen, f"{p} and {seen.get(m.group(1))}")
            seen[m.group(1)] = p

    def test_build_is_deterministic(self):
        first = tree_hash()
        build()
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


if __name__ == "__main__":
    unittest.main()
