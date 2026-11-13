"""W16 (2026-11-13): the store's own 404 page. GitHub Pages serves /404.html for every address that is not on the site,
most often a listing that sold or ended, so every link on it must work from any folder."""
import os, re, sys, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import build  # noqa: E402


class Page404(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(ROOT, "404.html"), encoding="utf-8") as f:
            self.t = f.read()
        self.text = re.sub(r"<(style|script)\b.*?</\1>", "", self.t, flags=re.S)

    def test_every_address_is_root_absolute(self):
        for a in re.findall(r'(?:href|src|action)="([^"]*)"', self.t):
            self.assertRegex(a, r"^(/|https://|#)", a)
        for u in re.findall(r"url\(\s*[\"']?([^)\"']+)", self.t):
            self.assertTrue(u.startswith(("/", "https://", "data:")), u)

    def test_not_indexed_and_no_canonical(self):
        self.assertIn('<meta name="robots" content="noindex">', self.t)
        self.assertNotIn('rel="canonical"', self.t)

    def test_logo_is_there_with_its_size(self):
        self.assertRegex(self.t, r'<img class="logo404" src="/images/hero\.jpg" width="624" height="535" alt="StampedeUSA logo"')

    def test_one_filled_action_and_it_goes_to_the_catalog(self):
        self.assertEqual(self.t.count('class="btn"'), 1)
        self.assertIn('<a class="btn" href="/catalog/">Browse the full catalog</a>', self.t)
        self.assertIn('<form class="hfind" action="/catalog/"', self.t)

    def test_ebay_links_open_in_the_shop_window(self):
        for tag in re.findall(r"<a [^>]*ebay\.com[^>]*>", self.t):
            self.assertIn('target="stampede_ebay"', tag)
        self.assertIn("'https://www.ebay.com/itm/' + m[1]", self.t)

    def test_no_price_or_shipping_words_and_no_dollar(self):
        self.assertNotIn("$", self.t)
        self.assertIsNone(re.search(r"(?i)\b(price|deal|value|shipping|bid)\b", self.text))

    def test_visitor_counter_present(self):
        self.assertIn(build.CF_BEACON_TOKEN, self.t)
        self.assertEqual(self.t.count("static.cloudflareinsights.com/beacon.min.js"), 1)

    def test_not_in_the_sitemap(self):
        with open(os.path.join(ROOT, "sitemap.xml"), encoding="utf-8") as f:
            self.assertNotIn("<loc>https://www.stampedestamps.store/404.html</loc>", f.read())   # item numbers can end in 404


if __name__ == "__main__":
    unittest.main()
