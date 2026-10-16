# stampedestamps.store

The StampedeUSA website: five hand-written pages plus a catalog of every listing, all sending shoppers to the eBay
store (https://www.ebay.com/str/stampedeusa) to buy. Hosted free on GitHub Pages with free https (Douglas, 2026-10-11,
chosen over GoDaddy Website Builder 7.0, which has no free certificate).

- Look: matches Stamp Stories (https://stampedestamps.github.io/stampede-stories/).
- Pages: `index.html` (Home), `shop.html`, `sale.html`, `about.html`, `feedback.html`, and `catalog/` (one page per listing).
- No prices on any page: eBay shows the live price, bids and time left.
- Font: League Gothic in `fonts/`, SIL Open Font License (`fonts/OFL.txt`). `CNAME` holds the custom domain.

## The daily build

`build.py` (standard library only) writes `catalog/`, `sitemap.xml`, `robots.txt` and the marked blocks inside the five
pages (`<!-- NAME:START ... -->` to `<!-- NAME:END -->`; never edit inside them by hand). Its inputs in `data/` are
written every morning on OMEN from the eBay export by the Windows task "StampedeUSA Store Catalog Refresh" (3:45 AM),
which then runs `tests/` and commits and pushes only when they pass.

What each page gets from the build: Shop's collector categories; Home's category sheet and twelve newly listed; the
Sale page's hero, "Marked down now" and "Ending soonest"; About's "What you'll find" tiles (one per kind of stock, see
`KINDS` in `build.py`); Feedback's "Fresh on the shelf". The Feedback counts and ratings are typed in by hand, with
their "as of" date.

## Putting a markdown on the Sale page

The eBay export has no markdown signal, so `data/sale_items.csv` is the one hand-kept input. When a markdown starts on
eBay, add one row per marked-down listing, with the markdown's last day as eBay shows it:

    item,sale_ends
    167532711458,2026-10-31

The next build leads the Sale page with "Marked down, still marvelous." and photo cards of those listings. A row stops
showing by itself once its date has passed or the listing has ended; a blank or unreadable date never shows. With only
the header line, the page leads with the auctions ending soonest and points to the eBay Sale tab for markdowns.
