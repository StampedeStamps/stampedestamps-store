# CLOUD TASK: the listing catalog for www.stampedestamps.store

> **SUPERSEDED 2026-10-15:** the 2026-10-11 cloud session pushed nothing and Douglas reported it "not built", so he
> moved this build to a local Claude Code session on OMEN. That session built it to this spec (build.py, tests/,
> catalog/). Kept as the spec the build follows; do not start another cloud session from it.

Staged on OMEN, Sun 2026-10-11, by a local Claude Code session, from Douglas's rulings that day:
**one catalog on the store's own domain, no prices, photos where we have scans, built by one cloud session.**

**HARD STOP: 2 hours of wall clock, or the end of STEP 5, whichever comes first.** If you hit the stop, commit what
you have, write `results/BLOCKED_CATALOG_<yyyy-mm-dd>.md` (environment, last step reached, what stopped you), push,
open the PR, and end.

## What exists
- Branch `catalog-2026-10` (you start here). `main` is the live site: five hand-written pages (`index.html`,
  `shop.html`, `sale.html`, `about.html`, `feedback.html`) served by GitHub Pages at https://www.stampedestamps.store.
- `data/listings.csv`: header `item,title,category,condition,format,ebay_url`, 421 rows today. `format` is
  `FIXED_PRICE` or `AUCTION`. OMEN replaces this file every morning from the eBay export.
- `data/catalog_images.csv`: header `item,image`, 135 rows today; `image` is `images/catalog/<item>.jpg` (web-sized).
  Items not in this file have no photo.
- The look: copy the CSS from the five pages (League Gothic in `fonts/`, perforated `.perf` stamp edges, grid paper,
  royal purple `#4B006E`, light and dark themes). The header menu and footer must match the five pages exactly.

## Hard rules
1. **No prices. Ever.** No price, bid, "deal", "value" or "$" of your own. 25 titles contain a `$` face value: keep
   the title exactly as given and never call it a price.
2. **No invented facts.** Only the title, category, condition and format may be asserted about an item, plus issue
   facts checked against the Scott catalog or USPS and shown with their source (amended by Douglas 2026-10-18).
   Never a condition grade or a price.
3. Standard-library Python for the build (`build.py`). Plain JavaScript, no frameworks, no CDNs, no external fonts,
   no trackers or analytics.
4. Do NOT edit `data/*.csv`, `images/`, `fonts/`, `CNAME`, `.nojekyll` or the five existing pages (except one link,
   STEP 3). Do not touch any other repository.
5. The generated HTML is committed. GitHub Pages serves this repo as plain files, and so will AWS Amplify later.
   No GitHub Actions workflow.

## STEP 0: probe (stop if it fails)
`data/listings.csv` has the header above and 380-460 rows, and `data/catalog_images.csv` has 100-200 rows, each with
an existing file. Commit `results/START_CATALOG_<yyyy-mm-dd>.md` (time, starting commit, row counts) and **push it now**.

## STEP 1: `build.py` (deterministic: same inputs give byte-identical output)
- `catalog/index.html`: every listing as a perforated stamp card (photo if any, else a text-only card that looks
  intentional; title; category; "Auction" or "Buy It Now"). Plain-JS search over titles with a live result count and
  a clear empty state; filters for category and format; a "no JavaScript" fallback that still lists everything.
- `catalog/item/<item>.html`, one per row: the title as the headline, the photo (or the text card), category /
  condition / format, one dominant "See it on eBay" button (`target="stampede_ebay"`) to `ebay_url`, a link back to
  the catalog, and 3-6 related items from the same category (chosen deterministically).
- Listings with an identical title appear ONCE on the index, with "Listing 1", "Listing 2", ... links. Never claim
  they are the same item.
- `sitemap.xml` (the five pages plus every catalog page, with `lastmod`) and `robots.txt` pointing to it.
- Every page: a unique `<title>` and meta description, a canonical URL under `https://www.stampedestamps.store/`,
  and Open Graph tags (the item photo when there is one, else `images/hero.jpg`).

## STEP 2: tests (`tests/`, unittest)
Page count = rows, minus duplicate-title merges on the index only; every item page links to its own `ebay_url`; no
`$` anywhere except inside a title copied verbatim; no broken internal links or missing images; every page's
canonical is unique; the build run twice gives identical bytes.

## STEP 3: one link each on Home and Shop
Add a "Browse the full catalog" button to `shop.html` (hero) and `index.html` (after "Newly listed"), styled like
the existing `.btn`. Change nothing else on those pages. The 5-item menu stays as it is.

## STEP 4: verify
Run the build and the tests. Take screenshots (if a headless browser is available) of the catalog index and one item
page with a photo and one without, at 390 px and 1280 px, light and dark, into `results/screens_catalog/`.

## STEP 5: report and PR
`results/REPORT_CATALOG_<yyyy-mm-dd>.md`: what you built, test output, page count and sizes, anything skipped and why,
and a short plain-language "What happened, in order" list. Push branch `catalog-2026-10` and open a PR to `main`.
**Never merge it. Never change repository or Pages settings.**
