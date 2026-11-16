"""The store's privacy policy (2026-11-16): a hand-written page at /privacy.html that Google needs on our own domain
before the YouTube upload app can leave testing. Hand-written like 404.html; build.py does not touch it."""
import os, re, sys, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import build  # noqa: E402

PAGE = os.path.join(ROOT, "privacy.html")


class PrivacyPage(unittest.TestCase):
    def setUp(self):
        with open(PAGE, encoding="utf-8") as f:
            self.t = f.read()
        self.head = self.t[:self.t.index("</head>")]
        self.text = re.sub(r"<(style|script)\b.*?</\1>", "", self.t, flags=re.S)
        self.hrefs = re.findall(r'<a [^>]*href="([^"]+)"', self.t)

    def test_page_is_small_and_dated(self):
        self.assertLess(os.path.getsize(PAGE), 40 * 1024)
        self.assertIn('Last updated: <time datetime="2026-11-16">Monday, November 16, 2026</time>', self.t)

    def test_head_has_lang_viewport_title_description_and_canonical(self):
        self.assertTrue(self.t.startswith('<!DOCTYPE html>\n<html lang="en">'))
        self.assertIn('<meta name="viewport" content="width=device-width,initial-scale=1">', self.head)
        self.assertEqual(re.findall(r"<title>([^<]*)</title>", self.head), ["Privacy | StampedeUSA"])
        desc = re.findall(r'<meta name="description" content="([^"]+)">', self.head)
        self.assertEqual(len(desc), 1)
        self.assertTrue(20 <= len(desc[0]) <= 160, desc[0])
        self.assertEqual(re.findall(r'<link rel="canonical" href="([^"]+)">', self.head),
                         [f"{build.SITE}/privacy.html"])
        self.assertNotIn('name="robots"', self.head)     # unlike 404.html, this page may be found by search engines

    def test_no_dollar_sign_or_price_words(self):
        self.assertNotIn("$", self.t)
        self.assertIsNone(re.search(r"(?i)\b(price[sd]?|deal|bargain|value|bid|shipping)\b", self.text))

    def test_the_only_script_is_the_visitor_counter(self):
        # rule 8: the Cloudflare Web Analytics beacon is the one script allowed, once, just before </body>
        tag = build.CF_BEACON.strip()
        self.assertEqual(re.findall(r"<script\b[^>]*>.*?</script>", self.t, flags=re.S), [tag])
        self.assertIn(build.CF_BEACON_TOKEN, tag)
        self.assertTrue(self.t.rstrip().endswith(tag + "\n</body>\n</html>"))

    def test_no_outside_stylesheet_font_or_framework(self):
        self.assertNotIn('rel="stylesheet"', self.t)
        self.assertNotIn("@import", self.t)
        for u in re.findall(r"url\(\s*[\"']?([^)\"']+)", self.t):
            self.assertFalse(re.match(r"(?i)(https?:)?//", u), u)
        for tag in re.findall(r"<link\b[^>]*>", self.head):
            if 'rel="canonical"' not in tag:
                self.assertNotRegex(tag, r'href="(https?:)?//', tag)
        for tag in re.findall(r"<(?:img|iframe|video|audio|source|embed|object)\b[^>]*>", self.t):
            self.assertNotRegex(tag, r'(src|data)="(https?:)?//', tag)

    def test_ebay_links_open_in_the_shop_window(self):
        tags = re.findall(r"<a [^>]*ebay\.com[^>]*>", self.t)
        self.assertGreaterEqual(len(tags), 2)
        for tag in tags:
            self.assertIn('target="stampede_ebay"', tag)
            self.assertRegex(tag, r'href="https://www\.ebay\.com/str/stampedeusa"')

    def test_youtube_api_services_section_has_the_required_links(self):
        # YouTube API Services Developer Policies III.A.1, III.A.2 (b, c, h, i) and III.D.2.3.b
        sec = self.t[self.t.index('<section class="block" id="youtube">'):]
        sec = sec[:sec.index("</section>")]
        self.assertIn("<h2>YouTube API Services</h2>", sec)
        self.assertIn("uses YouTube API Services only to upload its own videos to its own YouTube channel", sec)
        self.assertIn("does not collect, store or share personal data about YouTube viewers", sec)
        for url in ("https://www.youtube.com/@stampedestamps", "https://www.youtube.com/t/terms",
                    "http://www.google.com/policies/privacy", "https://security.google.com/settings/security/permissions"):
            self.assertIn(f'href="{url}"', sec, url)
        self.assertIn("agree to be bound by the", sec)
        self.assertIn("send us a message on eBay", self.text)   # III.A.2.i: how to reach us about privacy

    def test_links_to_cloudflare_and_github_pages_it_relies_on(self):
        for url in ("https://www.cloudflare.com/privacypolicy/", "https://developers.cloudflare.com/web-analytics/about/",
                    "https://www.cloudflare.com/web-analytics/",
                    "https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages",
                    "https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement"):
            self.assertIn(url, self.hrefs, url)

    def test_no_email_address_or_phone_number(self):
        self.assertNotRegex(self.t, r"(?i)mailto:|tel:")
        self.assertIsNone(re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", self.t))
        self.assertIsNone(re.search(r"\(?\b\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b", self.text))

    def test_same_menu_as_about_and_every_local_address_exists(self):
        def header(t):
            return t[t.index('<header class="selvage">'):t.index("</header>")]

        def menu(t):
            return re.findall(r'<li><a href="([^"]+)"(?: aria-current="page")?>([^<]+)</a></li>', header(t))
        with open(os.path.join(ROOT, "about.html"), encoding="utf-8") as f:
            self.assertEqual(menu(self.t), menu(f.read()))
        self.assertNotIn('aria-current="page"', header(self.t))    # not in the menu, so no menu item is "here"
        self.assertEqual(len(re.findall(r"<h1\b", self.t)), 1)
        for ref in re.findall(r'(?:href|src|action)="([^"#]+)"', self.t) + re.findall(r'url\("([^"]+)"\)', self.t):
            if re.match(r"(?i)(https?:)?//", ref):
                continue
            path = ref.split("?")[0]
            path = path + "index.html" if path.endswith("/") else path
            self.assertTrue(os.path.exists(os.path.join(ROOT, path)), ref)


if __name__ == "__main__":
    unittest.main()
