"""Download Nature Protocols articles as publisher PDFs, so the ProtocolQA
pipeline has a corpus to draw from.

Why this exists: the earlier batches (molintbench/protocal-0923 .. -0927-2) were
assembled by hand -- roughly a dozen PDFs clicked out of a browser, one at a
time. That does not scale to "every protocol published in 2025-2026", and the
manual step is the only part of the pipeline with no script behind it. This is
that script.

Nature Protocols is a subscription journal: every 2025-2026 item's Crossref
licence is the Springer Nature TDM link, i.e. nothing is CC-BY, so a publisher
PDF is only available to a session that is entitled to it. The script therefore
*reuses your own browser session* (`--cookies`, a Netscape cookies.txt exported
with a browser extension) and does nothing else: no paywall is circumvented, no
credential is shared, nothing is fetched that you could not open yourself in
that browser. It is rate-limited on purpose (`--delay`), and it stops early
rather than hammering the publisher if the session turns out not to be entitled.

Four steps, each skippable (`--steps`):

  enumerate   Crossref (ISSN 1750-2799) for the date range -> manifest.jsonl,
              one record per DOI with title and online date. Crossref is the
              spine because it is complete and stable; the site's own listings
              are walked to learn which of those DOIs are Protocols.
  classify    Walk /nprot/articles?type=protocol&year=YYYY&page=N and mark each
              manifest record is_protocol. The site's type filter is cheaper
              (~20 requests) than reading 343 landing pages, and is the site's
              own answer rather than a guess. DOIs the listing knows but
              Crossref does not are added from the site side, so a lagging
              deposit cannot silently drop an article.
  download    Resolve the article's own Download PDF link from its landing page
              (cached in <out>/.cache), then GET it. The obvious URL is wrong:
              citation_pdf_url advertises /articles/<suffix>.pdf, but that form
              303s to the landing page for every client, open access included --
              the button points at <suffix>_reference.pdf instead, which serves
              the file. The response is kept only if it is really a PDF (%PDF
              magic + trailing %%EOF: a truncated transfer also starts with
              %PDF), saved as <out>/pdf/<suffix>.pdf -- the same DOI-suffix
              filename the batch directories use, so extract_protocol.py's DOI
              regex still works on it.
  report      manifest.csv + failed.txt + a per-status/per-year summary.
  group       Copy the downloaded PDFs into batch directories of --group-size
              (default 3) under the parent of --out, because the ProtocolQA work
              is done three papers at a time. pdf/ stays the master set.

Only the download step touches subscription content; `--dry-run` runs the other
three and stops. Run `--check-auth` first: it fetches one article you name and
tells you whether the session can actually get PDFs, which is a five-second
answer instead of two hundred failures.

PDFs land in molintbench/ (git-ignored) and are never redistributed: they are
copyrighted publisher PDFs used as generation input, same as the existing
batches. See AGENTS.md.

  python download_nprot.py --out molintbench/nprot-2025-2026 \
      --cookies ~/Downloads/nature_cookies.txt --check-auth
  python download_nprot.py --out molintbench/nprot-2025-2026 \
      --cookies ~/Downloads/nature_cookies.txt

Cookie sources, in the order they are tried: --cookies FILE,
--cookie-header "a=b; c=d", $NATURE_COOKIES_FILE, <out>/cookies.txt, and
NATURE_COOKIE in the repo's .env (see .env.example). Only the `nature.com`
cookies matter; export them while logged in through your institution.
"""

import argparse
import csv
import hashlib
import http.cookiejar
import json
import os
import random
import re
import shutil
import time
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ISSN = "1750-2799"
SITE = "https://www.nature.com"
LISTING = f"{SITE}/nprot/articles"
CROSSREF = "https://api.crossref.org/journals/{issn}/works"

# The DOI suffix is the filename: 10.1038/s41596-026-01428-9 -> s41596-026-01428-9.
# The final character is a check digit over 0-9 and w, x, y, z -- all fourteen
# occur (27 of the 343 records end in w), so a [\dxz] class silently drops a
# seventh of the journal.
DOI_RE = re.compile(r"^10\.1038/(s\d{5}-\d{3}-\d{5}-[0-9a-z])$")
SUFFIX = r"s\d{5}-\d{3}-\d{5}-[0-9a-z]"

# Listing pages render one result per c-card; the article link is inside the
# card's <h3>. Anchoring on the card keeps any sidebar/related module on the
# page from being mistaken for a result.
CARD_LINK_RE = re.compile(r'<h3 class="c-card__title"[^>]*>\s*<a href="/articles/(' + SUFFIX + r')"')
ANY_LINK_RE = re.compile(r'href="/articles/(' + SUFFIX + r')"')
LAST_PAGE_RE = re.compile(r'data-page="(\d+)"')

META_TYPE_RE = re.compile(r'<meta name="citation_article_type" content="([^"]*)"')
META_PDF_RE = re.compile(r'<meta name="citation_pdf_url" content="([^"]*)"')
ANCHOR_RE = re.compile(r"<a\b[^>]*>", re.I)

# Markers the article page shows to a session that cannot read the full text.
PAYWALL_MARKERS = ("Access through your institution", "Buy this article",
                   "Access options", "Subscribe to this")

BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

PDF_MAGIC = b"%PDF"
MIN_PDF_BYTES = 20_000

OK, PAYWALLED, AUTH_REQUIRED = "ok", "paywalled", "auth-required"
NOT_FOUND, BLOCKED, NOT_A_PDF, ERROR = "not-found", "blocked", "not-a-pdf", "error"
LISTED, SKIPPED = "listed", "skipped"


def log(msg):
    print(msg, flush=True)


def sleep_between(args):
    """Politeness delay with jitter, so the request train has no fixed period."""
    time.sleep(args.delay * random.uniform(0.6, 1.4))


# --------------------------------------------------------------------------
# manifest: the one piece of state, append-and-merge so runs resume
# --------------------------------------------------------------------------

def load_manifest(path):
    records = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                records[rec["doi"]] = rec
    return records


def save_manifest(path, records, note=""):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for doi in sorted(records):
            fh.write(json.dumps(records[doi], ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(tmp, path)
    if note:
        log(f"  manifest: {len(records)} records ({note}) -> {path}")


def write_atomic(path, data):
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def valid_pdf(path):
    """A saved file counts as a PDF only if it opens and ends properly.

    Truncated transfers happen on flaky links and still start with %PDF, so the
    trailing %%EOF is checked too -- without it, resume would keep a broken file
    forever and extract_protocol.py would fail much later, on the whole batch.
    """
    try:
        if os.path.getsize(path) < MIN_PDF_BYTES:
            return False
        with open(path, "rb") as fh:
            if not fh.read(5).startswith(PDF_MAGIC):
                return False
            fh.seek(max(0, os.path.getsize(path) - 2048))
            return b"%%EOF" in fh.read()
    except OSError:
        return False


# --------------------------------------------------------------------------
# client + session
# --------------------------------------------------------------------------

def load_cookies(args, out_dir):
    """Session cookies, from wherever the operator put them. None if nowhere."""
    header = args.cookie_header
    path = args.cookies or os.environ.get("NATURE_COOKIES_FILE") or ""
    if not path:
        for candidate in (os.path.join(out_dir, "cookies.txt"),):
            if os.path.exists(candidate):
                path = candidate
                break
    if not path and not header:
        header = os.environ.get("NATURE_COOKIE", "")
    if not path and not header:
        return None, "none"

    if path:
        path = os.path.expanduser(path)
        if not os.path.exists(path):
            raise SystemExit(
                f"  --cookies {path} does not exist.\n"
                f"  Log in to nature.com in your browser, export the cookies with the\n"
                f"  'Get cookies.txt LOCALLY' extension, and pass the file it saves.")
        with open(path, "rb") as fh:
            head = fh.read(4096)
        jar = http.cookiejar.MozillaCookieJar(path)
        try:
            jar.load(ignore_discard=True, ignore_expires=True)
        except (http.cookiejar.LoadError, OSError) as exc:
            hint = ""
            if head.lstrip()[:1] in (b"[", b"{"):
                hint = ("\n  This file is JSON, not Netscape. Cookie-Editor and similar extensions\n"
                        "  export JSON; 'Get cookies.txt LOCALLY' exports the Netscape format\n"
                        "  this script reads.")
            raise SystemExit(f"  could not parse {path}: {exc}{hint}")
        cookies = httpx.Cookies()
        for c in jar:
            cookies.set(c.name, c.value, domain=c.domain, path=c.path)
        # An export from the wrong tab is the failure mode that looks like a
        # paywall: cookies load fine, they just belong to another site. Catch it
        # here instead of letting 279 downloads report "paywalled".
        nature = [c for c in jar if "nature.com" in (c.domain or "")]
        if not nature:
            domains = sorted({(c.domain or "?").lstrip(".") for c in jar})[:8]
            raise SystemExit(
                f"  {path} holds no nature.com cookies (found: {', '.join(domains)}).\n"
                f"  Export them from the browser tab that is on nature.com, logged in\n"
                f"  through your institution.")
        return cookies, f"{len(nature)} nature.com cookies from {path}"

    cookies = httpx.Cookies()
    for pair in header.split(";"):
        if "=" in pair:
            name, _, value = pair.strip().partition("=")
            cookies.set(name, value, domain=".nature.com", path="/")
    return cookies, f"{len(cookies)} cookies from header/env"


def build_client(args, cookies=None):
    """httpx client for nature.com, direct unless asked otherwise.

    trust_env is off by default. The repo's llm_gateway does the same, and for
    the same reason plus one more: this machine exports
    all_proxy=socks5://127.0.0.1:7897, which httpx rejects outright without the
    socksio extra ("Using SOCKS proxy, but the 'socksio' package is not
    installed"), so honouring the environment by default would fail before the
    first request. --use-env-proxy restores it for anyone whose access really
    does ride a proxy; --proxy names one explicitly.
    """
    kwargs = dict(follow_redirects=True, timeout=args.timeout,
                  headers={"User-Agent": args.ua,
                           "Accept-Language": "en-US,en;q=0.9"},
                  trust_env=args.use_env_proxy)
    if cookies is not None:
        kwargs["cookies"] = cookies
    if args.proxy:
        kwargs["proxy"] = args.proxy
    return httpx.Client(**kwargs)


def get(client, url, params=None, accept=None, attempts=3):
    """GET with retries. Returns the response; never raises on HTTP status."""
    last = None
    for attempt in range(1, attempts + 1):
        try:
            r = client.get(url, params=params,
                           headers={"Accept": accept} if accept else None)
            if r.status_code in (429, 500, 502, 503, 504) and attempt < attempts:
                # Retry-After is the publisher telling us the pace; honour it.
                wait = float(r.headers.get("Retry-After") or 0) or 5 * attempt
                log(f"    http {r.status_code}, backing off {wait:.0f}s")
                time.sleep(wait)
                continue
            return r
        except httpx.HTTPError as exc:
            last = exc
            if attempt < attempts:
                time.sleep(3 * attempt)
    raise last


# --------------------------------------------------------------------------
# step 1: enumerate (Crossref)
# --------------------------------------------------------------------------

def step_enumerate(client, args, records):
    if args.no_enumerate:
        log(f"[enumerate] skipped (--no-enumerate); {len(records)} records on file")
        return records

    filt = f"from-pub-date:{args.date_from},until-pub-date:{args.date_to},type:journal-article"
    params = {
        "filter": filt,
        "rows": "1000",
        "cursor": "*",
        "select": "DOI,title,published,published-online,issued,type,URL",
        "mailto": args.mailto,
    }
    log(f"[enumerate] Crossref ISSN {args.issn} {args.date_from}..{args.date_to}")
    items, cursor, total = [], "*", None
    while True:
        params["cursor"] = cursor
        r = get(client, CROSSREF.format(issn=args.issn), params=params, attempts=4)
        if r.status_code != 200:
            raise SystemExit(f"Crossref returned {r.status_code}; rerun later or pass "
                             f"--no-enumerate to reuse the existing manifest")
        msg = r.json()["message"]
        total = msg["total-results"]
        items.extend(msg["items"])
        cursor = msg.get("next-cursor")
        if not cursor or len(items) >= total:
            break
        sleep_between(args)

    added = 0
    for item in items:
        doi = item["DOI"]
        m = DOI_RE.match(doi)
        if not m:
            log(f"  skipping unexpected DOI shape: {doi}")
            continue
        pub = (item.get("published-online") or item.get("published") or {})
        parts = (pub.get("date-parts") or [[None]])[0]
        published = "-".join(f"{p:02d}" if i else str(p) for i, p in enumerate(parts)) if parts[0] else None
        rec = records.get(doi)
        if rec is None:
            rec = {"doi": doi, "suffix": m.group(1), "status": LISTED, "source": "crossref"}
            records[doi] = rec
            added += 1
        rec["title"] = (item.get("title") or [""])[0]
        rec["published_online"] = published
        rec["year"] = int(published[:4]) if published else None
        rec["url"] = f"{SITE}/articles/{m.group(1)}"
    log(f"  Crossref: {total} articles, {added} new, {len(records)} records")
    return records


# --------------------------------------------------------------------------
# step 2: classify (which DOIs are Protocols, per the site's own filter)
# --------------------------------------------------------------------------

def walk_listing(client, args, year, article_type):
    """Article suffixes on the journal's listing pages for one year.

    article_type is the site's own type filter value ("protocol", or "all" for
    no filter). Paginates until the page yields nothing new or the pagination
    widget's last page is reached.
    """
    found, page = {}, 1
    while page <= args.max_pages:
        params = {"sort": "PubDate", "year": str(year), "page": str(page)}
        if article_type != "all":
            params["type"] = article_type
        r = get(client, LISTING, params=params)
        if r.status_code != 200:
            log(f"  listing {year}/{article_type} p{page}: http {r.status_code}, stopping")
            break
        html = r.text
        links = CARD_LINK_RE.findall(html) or ANY_LINK_RE.findall(html)
        links = set(links)
        new = links - set(found)
        found.update({s: year for s in links})
        pages = [int(x) for x in LAST_PAGE_RE.findall(html)]
        last = max(pages) if pages else page
        if not new or page >= last:
            break
        page += 1
        sleep_between(args)
    return found


def landing_meta(client, args, suffix):
    """Article type and advertised PDF URL from the (cached) landing page."""
    html = landing_page(client, args, suffix)
    if not html:
        return None, None
    mt = META_TYPE_RE.search(html)
    mp = META_PDF_RE.search(html)
    return (mt.group(1) if mt else None), (mp.group(1) if mp else None)


def step_classify(client, args, records):
    if args.article_type == "all":
        log("[classify] --article-type all: every listed article is selected")
        for rec in records.values():
            rec["is_protocol"] = True
        return records

    years = sorted({rec.get("year") for rec in records.values() if rec.get("year")})
    log(f"[classify] listing pages for {years}, type filter '{args.article_type}'")
    protocols, site_all = {}, {}
    for year in years:
        got = walk_listing(client, args, year, args.article_type)
        protocols.update(got)
        sleep_between(args)
        everything = walk_listing(client, args, year, "all")
        site_all.update(everything)
        log(f"  {year}: site lists {len(everything)} articles, {len(got)} as '{args.article_type}'")
        sleep_between(args)

    # A site-only DOI (listed before Crossref's deposit lands) is still a real
    # article; add it rather than lose it.
    added = 0
    for suffix in sorted(set(protocols) | set(site_all)):
        doi = f"10.1038/{suffix}"
        if doi not in records:
            records[doi] = {"doi": doi, "suffix": suffix, "status": LISTED, "source": "site",
                            "year": (protocols.get(suffix) or site_all.get(suffix))}
            added += 1

    unknown = []
    for rec in records.values():
        if rec["suffix"] in protocols:
            rec["is_protocol"] = True
            rec["article_type"] = rec.get("article_type") or "Protocol"
        elif rec["suffix"] in site_all:
            rec["is_protocol"] = False
        else:
            unknown.append(rec)
    if added:
        log(f"  added {added} site-only records Crossref had not deposited")

    # Crossref knows it, the year's listing pages do not: either the year filter
    # and the deposit disagree about the publication date, or the listing walk
    # was cut short. Ask the landing page instead of guessing.
    for rec in unknown:
        art_type, pdf = landing_meta(client, args, rec["suffix"])
        rec["article_type"] = art_type
        rec["citation_pdf_url"] = pdf
        rec["is_protocol"] = bool(art_type and art_type.lower().startswith(args.article_type))
        sleep_between(args)
    if unknown:
        log(f"  {len(unknown)} records not on any listing page -> landing page ("
            f"{sum(1 for r in unknown if r['is_protocol'])} were Protocols)")

    n_proto = sum(1 for r in records.values() if r.get("is_protocol"))
    log(f"  {n_proto} Protocols / {len(records)} Crossref+sited records")
    if n_proto == 0:
        log("  NOTE: zero Protocols found -- the site's filter parameter probably changed; "
            "re-run with --article-type all to fall back to the whole journal")
    return records


# --------------------------------------------------------------------------
# step 3: download
# --------------------------------------------------------------------------

def classify_pdf_response(r, requested_url):
    """ok / paywalled / auth-required / not-found / blocked / not-a-pdf."""
    body = r.content
    if body.startswith(PDF_MAGIC):
        if len(body) < MIN_PDF_BYTES:
            return "truncated", f"only {len(body)} bytes"
        if b"%%EOF" not in body[-2048:]:
            return "truncated", "no %%EOF at end"
        return OK, f"{len(body)} bytes"
    final = str(r.url)
    if "idp.nature.com" in final or "/authorize" in final:
        return AUTH_REQUIRED, "redirected to the sign-in flow"
    if r.status_code == 404:
        return NOT_FOUND, "http 404"
    if r.status_code in (403, 429):
        return BLOCKED, f"http {r.status_code}"
    if r.status_code != 200:
        return ERROR, f"http {r.status_code}"
    text = body.decode("utf-8", "replace")
    hit = next((m for m in PAYWALL_MARKERS if m in text), None)
    if hit or final.rstrip("/") != requested_url.rstrip("/"):
        return PAYWALLED, f"landing page instead of PDF ({hit or 'redirected'})"
    return NOT_A_PDF, "http 200 but not a PDF"


def landing_pdf_href(html):
    """The article page's own Download PDF href.

    This is the link that works. `citation_pdf_url` advertises
    /articles/<suffix>.pdf, but that form now 303s to the landing page for
    everyone -- open access included, checked against Scientific Reports, where
    access cannot be the reason. The button points at <suffix>_reference.pdf,
    which serves the real file; so the page is read instead of the guess being
    repeated. Paywalled pages do not render the button at all, which is why the
    deterministic form below is still needed as a fallback.
    """
    for tag in ANCHOR_RE.findall(html):
        if "c-pdf-download__link" in tag or 'data-test="download-pdf"' in tag:
            m = re.search(r'href="([^"]+)"', tag)
            if m:
                return m.group(1)
    return None


def landing_page(client, args, suffix):
    """Landing HTML, cached: one fetch per article serves type, href, retries."""
    cache_dir = os.path.join(args.out, ".cache")
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, suffix + ".html")
    if not args.refresh_cache and os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    r = get(client, f"{SITE}/articles/{suffix}")
    if r.status_code != 200:
        return ""
    write_atomic(path, r.content)
    return r.text


def pdf_candidates(args, rec, href):
    """URL forms to try, best first. dict.fromkeys keeps order, drops dupes."""
    forms = []
    if href:
        forms.append(href if href.startswith("http") else SITE + href)
    forms.append(f"{SITE}/articles/{rec['suffix']}_reference.pdf")
    forms.append(rec.get("citation_pdf_url") or f"{SITE}/articles/{rec['suffix']}.pdf")
    return list(dict.fromkeys(forms))


def try_download(client, args, rec, pdf_dir):
    html = landing_page(client, args, rec["suffix"])
    if html and not rec.get("article_type"):
        m = META_TYPE_RE.search(html)
        if m:
            rec["article_type"] = m.group(1)
    href = landing_pdf_href(html) if html else None

    status, note, url, body = None, "", "", None
    for candidate in pdf_candidates(args, rec, href):
        r = get(client, candidate, accept="application/pdf,*/*")
        got_status, got_note = classify_pdf_response(r, candidate)
        if got_status == OK:
            status, note, url, body = got_status, got_note, candidate, r.content
            break
        # A 404 means this URL form does not exist; another form may. Anything
        # else (paywall, sign-in, block) will repeat for every form, so stop.
        if got_status != NOT_FOUND and status is None:
            status, note, url = got_status, got_note, candidate
        if got_status != NOT_FOUND:
            break
    if status is None:                                     # every form 404'd
        status, note, url = NOT_FOUND, "no PDF URL form resolved", url or ""

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if status == OK:
        path = os.path.join(pdf_dir, rec["suffix"] + ".pdf")
        write_atomic(path, body)
        rec.update(status=OK, http_status=200, bytes=len(body),
                   sha256=hashlib.sha256(body).hexdigest(),
                   file=os.path.relpath(path, args.out), pdf_url=url, note=note,
                   fetched_at=now)
    else:
        rec.update(status=status, note=note, pdf_url=url, fetched_at=now)
    return status, note


def step_download(client, args, records, manifest_path):
    pdf_dir = os.path.join(args.out, "pdf")
    os.makedirs(pdf_dir, exist_ok=True)

    selected = [r for r in records.values() if r.get("is_protocol")]
    selected.sort(key=lambda r: (r.get("year") or 0, r["suffix"]))
    if args.limit:
        selected = selected[:args.limit]

    todo = []
    for rec in selected:
        path = os.path.join(pdf_dir, rec["suffix"] + ".pdf")
        if not args.force and rec.get("status") == OK and valid_pdf(path):
            continue
        todo.append(rec)

    if args.dry_run:
        log(f"[download] --dry-run: {len(selected)} Protocols selected, "
            f"{len(selected) - len(todo)} already on disk, would fetch {len(todo)}")
        return records

    log(f"[download] {len(todo)} PDFs to fetch ({len(selected)} selected, "
        f"{len(selected) - len(todo)} already valid) @ ~{args.delay}s each")
    consecutive, done = 0, 0
    for rec in todo:
        done += 1
        status, note = try_download(client, args, rec, pdf_dir)
        log(f"  [{done}/{len(todo)}] {rec['suffix']} {rec.get('year')}: {status} ({note})")
        save_manifest(manifest_path, records)
        if status == OK:
            consecutive = 0
        else:
            consecutive += 1
            if consecutive >= args.max_consecutive_errors:
                log(f"\n  Stopping: {consecutive} failures in a row -- this looks like an "
                    f"access problem, not a per-article one.\n"
                    f"  The manifest is saved; nothing is lost, re-run to continue.\n"
                    f"  If the verdicts above say '{PAYWALLED}' or '{AUTH_REQUIRED}', the "
                    f"session is not entitled (or the cookies expired).\n"
                    f"  Export fresh nature.com cookies from a logged-in browser (extension: "
                    f"Get cookies.txt LOCALLY) and re-run with --cookies <file>.\n")
                break
        if done < len(todo):
            sleep_between(args)
    return records


def step_check_auth(client, args, records):
    """Probe one article and say whether this session can fetch PDFs at all."""
    targets = [r for r in records.values() if r.get("is_protocol")] or list(records.values())
    if args.doi:
        targets = [r for r in records.values() if r["doi"] == args.doi] or targets
    if not targets:
        log("[check-auth] no records to probe; run the enumerate/classify steps first")
        return
    rec = targets[0]
    log(f"[check-auth] probing {rec['suffix']} ({rec.get('title', '')[:60]})")
    html = landing_page(client, args, rec["suffix"])
    href = landing_pdf_href(html) if html else None
    log(f"[check-auth] article page: {'rendered, download link present' if href else 'no download link'}"
        f"{' (' + href + ')' if href else ' -- typical for an unentitled session'}")
    status, note = None, ""
    for candidate in pdf_candidates(args, rec, href):
        r = get(client, candidate, accept="application/pdf,*/*")
        status, note = classify_pdf_response(r, candidate)
        log(f"[check-auth]   {candidate} -> {status} ({note})")
        if status != NOT_FOUND:      # only a missing URL form justifies the next one
            break
    log(f"[check-auth] verdict: {status}")
    if status == OK:
        log("  Session can fetch publisher PDFs -- run the full download.")
    else:
        log("  Session cannot fetch this PDF. Nobody is charged and nothing is bypassed: "
            "the script only uses the cookies you give it.\n"
            "  Get entitled: log in to nature.com through your institution in your "
            "browser, then export the nature.com cookies as cookies.txt and pass "
            "--cookies <file>. Cookies expire; re-export when they do.")


def step_group(args, records):
    """Copy the downloaded PDFs into fixed-size batch directories.

    The ProtocolQA work is done three papers at a time (molintbench/protocal-0923,
    -0926, -0926-2, ...), each directory holding the publisher-named originals
    flat; `cp $BASE/s41596-*.pdf $BASE/<slug>/paper.pdf` is what then turns a
    batch into per-paper working directories. This reproduces that layout at
    scale: 279 papers become 93 directories. Copies, not moves -- pdf/ stays the
    master set -- so expect the batch directories to double the disk footprint.

    Idempotent: a file already present with the same size is left alone, so
    re-running after a few more downloads only adds what is missing.
    """
    src_dir = os.path.join(args.out, "pdf")
    group_years = {y.strip() for y in args.group_years.split(",") if y.strip()}
    selected = [r for r in records.values()
                if r.get("is_protocol") and r.get("status") == OK
                and os.path.exists(os.path.join(src_dir, r["suffix"] + ".pdf"))
                and (not group_years or str(r.get("year") or "") in group_years)]
    selected.sort(key=lambda r: (r.get("year") or 0, r["suffix"]))
    if not selected:
        log("[group] no downloaded PDFs to group (run the download step first)")
        return records
    if args.group_size < 1:
        raise SystemExit("--group-size must be at least 1")

    n_groups = (len(selected) + args.group_size - 1) // args.group_size
    width = max(2, len(str(n_groups)))
    os.makedirs(args.group_out, exist_ok=True)
    log(f"[group] {len(selected)} PDFs -> {n_groups} directories of up to "
        f"{args.group_size} under {args.group_out}")

    created = copied = skipped = 0
    for i in range(0, len(selected), args.group_size):
        chunk = selected[i:i + args.group_size]
        name = f"{args.group_prefix}{i // args.group_size + 1:0{width}d}"
        dest = os.path.join(args.group_out, name)
        if not os.path.isdir(dest):
            created += 1
        os.makedirs(dest, exist_ok=True)
        lines = []
        for rec in chunk:
            src = os.path.join(src_dir, rec["suffix"] + ".pdf")
            dst = os.path.join(dest, rec["suffix"] + ".pdf")
            if os.path.exists(dst) and os.path.getsize(dst) == os.path.getsize(src):
                skipped += 1
            else:
                shutil.copy2(src, dst)
                copied += 1
            rec["batch"] = name
            lines.append("\t".join([rec["suffix"], rec["doi"], str(rec.get("year") or ""),
                                    rec.get("article_type") or "", rec.get("title", "")]))
        write_atomic(os.path.join(dest, "batch.txt"),
                     ("# suffix\tdoi\tyear\tarticle_type\ttitle\n" + "\n".join(lines) + "\n")
                     .encode("utf-8"))
    log(f"  {created} directories created, {copied} files copied, {skipped} already in place")
    log(f"  {args.group_prefix}{1:0{width}d} .. {args.group_prefix}{n_groups:0{width}d}, "
        f"each with batch.txt (suffix/doi/year/type/title)")
    return records


# --------------------------------------------------------------------------
# step 4: report
# --------------------------------------------------------------------------

def step_report(args, records):
    pdf_dir = os.path.join(args.out, "pdf")
    rows = []
    for rec in sorted(records.values(), key=lambda r: (r.get("year") or 0, r["suffix"])):
        path = os.path.join(pdf_dir, rec["suffix"] + ".pdf")
        rec["on_disk"] = valid_pdf(path)
        rows.append({
            "doi": rec["doi"], "suffix": rec["suffix"], "year": rec.get("year") or "",
            "article_type": rec.get("article_type") or "",
            "is_protocol": int(bool(rec.get("is_protocol"))),
            "status": rec.get("status", ""), "http_status": rec.get("http_status", ""),
            "bytes": rec.get("bytes", ""), "sha256": rec.get("sha256", ""),
            "on_disk": int(rec["on_disk"]), "title": rec.get("title", ""),
        })
    csv_path = os.path.join(args.out, "manifest.csv")
    with open(csv_path + ".tmp", "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["doi"])
        w.writeheader()
        w.writerows(rows)
    os.replace(csv_path + ".tmp", csv_path)

    proto = [r for r in records.values() if r.get("is_protocol")]
    failed = [r for r in proto if r.get("status") != OK]
    failed_path = os.path.join(args.out, "failed.txt")
    with open(failed_path, "w", encoding="utf-8") as fh:
        for rec in sorted(failed, key=lambda r: r["suffix"]):
            fh.write(f"{rec['doi']}\t{rec['suffix']}\t{rec.get('status')}\t{rec.get('note', '')}\n")

    by_status, by_year = {}, {}
    for rec in records.values():
        by_status[rec.get("status", LISTED)] = by_status.get(rec.get("status", LISTED), 0) + 1
        year = rec.get("year") or "?"
        got = by_year.setdefault(year, {"protocols": 0, "ok": 0})
        if rec.get("is_protocol"):
            got["protocols"] += 1
            if rec.get("status") == OK:
                got["ok"] += 1

    log("\n[report]")
    got_ok = [p for p in proto if p.get("status") == OK]
    total_bytes = sum(p.get("bytes") or 0 for p in got_ok)
    log(f"  records: {len(records)}  protocols: {len(proto)}  downloaded: {len(got_ok)}"
        f" ({total_bytes / 1e9:.2f} GB)")
    log(f"  by status: {', '.join(f'{k}={v}' for k, v in sorted(by_status.items()))}")
    for year in sorted(by_year, key=str):
        got = by_year[year]
        log(f"  {year}: {got['ok']}/{got['protocols']} protocols downloaded")
    forms = {}
    for p in got_ok:
        form = ("button href" if not p["pdf_url"].endswith(("_reference.pdf", ".pdf"))
                else p["pdf_url"].rsplit("/", 1)[-1].replace(p["suffix"], "<suffix>"))
        forms[form] = forms.get(form, 0) + 1
    if forms:
        log(f"  PDF URL form that worked: {', '.join(f'{k}={v}' for k, v in sorted(forms.items()))}")
    log(f"  wrote {csv_path}")
    if failed:
        log(f"  wrote {failed_path} ({len(failed)} to retry)")
        log(f"  retry: python {os.path.basename(__file__)} --out {args.out} "
            f"--steps download --cookies <file>")
    else:
        log("  nothing failed")
    return records


# --------------------------------------------------------------------------

def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", default="molintbench/nprot-2025-2026",
                   help="output directory (git-ignored location recommended)")
    p.add_argument("--from", dest="date_from", default="2025-01-01",
                   help="Crossref from-pub-date (default 2025-01-01)")
    p.add_argument("--to", dest="date_to", default="2026-12-31",
                   help="Crossref until-pub-date (default 2026-12-31)")
    p.add_argument("--issn", default=ISSN, help=f"journal ISSN (default {ISSN})")
    p.add_argument("--article-type", default="protocol",
                   help="listing type filter: 'protocol' (default) or 'all'")
    p.add_argument("--steps", default="enumerate,classify,download,report",
                   help="comma-separated subset of enumerate,classify,download,group,report")
    p.add_argument("--group-size", type=int, default=3,
                   help="step 'group': PDFs per batch directory (default 3)")
    p.add_argument("--group-out", default="",
                   help="step 'group': where batch directories go (default: parent of --out)")
    p.add_argument("--group-prefix", default="protocal-2025-2026-b",
                   help="step 'group': batch directory name prefix (number is appended)")
    p.add_argument("--group-years", default="",
                   help="step 'group': only these publication years, e.g. 2024 "
                        "(default: every downloaded Protocol). Use this to add a "
                        "year as its own batch series without renumbering the others.")
    p.add_argument("--cookies", default="", help="Netscape cookies.txt exported from a logged-in browser")
    p.add_argument("--cookie-header", default="", help="raw 'name=value; ...' cookie header instead of a file")
    p.add_argument("--check-auth", action="store_true",
                   help="probe one article and exit: can this session fetch PDFs?")
    p.add_argument("--doi", default="", help="article to probe for --check-auth")
    p.add_argument("--limit", type=int, default=0, help="fetch at most N PDFs this run")
    p.add_argument("--delay", type=float, default=2.5,
                   help="seconds between requests, jittered (default 2.5)")
    p.add_argument("--max-pages", type=int, default=80,
                   help="listing-pagination safety cap (default 80)")
    p.add_argument("--max-consecutive-errors", type=int, default=5,
                   help="stop downloading after N failures in a row (default 5)")
    p.add_argument("--timeout", type=float, default=120.0,
                   help="per-request timeout in seconds (publisher PDFs run to tens of MB)")
    p.add_argument("--refresh-cache", action="store_true",
                   help="re-fetch article pages kept in <out>/.cache (default: reuse)")
    p.add_argument("--mailto", default=os.environ.get("CROSSREF_MAILTO", "nprot-dl@example.invalid"),
                   help="contact address for the Crossref polite pool")
    p.add_argument("--ua", default=BROWSER_UA,
                   help="User-Agent; defaults to a browser string because the site rejects unknown clients")
    p.add_argument("--proxy", default="", help="explicit proxy URL, e.g. http://127.0.0.1:7897")
    p.add_argument("--use-env-proxy", action="store_true",
                   help="honour http_proxy/https_proxy/all_proxy (off by default: this box's "
                        "all_proxy is SOCKS, which httpx cannot use without the socksio extra)")
    p.add_argument("--force", action="store_true", help="re-download files that are already valid")
    p.add_argument("--dry-run", action="store_true", help="enumerate+classify+report, fetch nothing")
    p.add_argument("--no-enumerate", action="store_true", help="reuse the existing manifest (no Crossref call)")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    args.out = os.path.abspath(os.path.expanduser(args.out))
    args.group_out = (os.path.abspath(os.path.expanduser(args.group_out))
                      if args.group_out else os.path.dirname(args.out))
    os.makedirs(args.out, exist_ok=True)
    steps = [s.strip() for s in args.steps.split(",") if s.strip()]

    load_dotenv(os.path.join(REPO, ".env"))
    cookies, source = load_cookies(args, args.out)
    log(f"output: {args.out}")
    log(f"session: {source if cookies is not None else 'no cookies -- expect paywalled verdicts'}")

    manifest_path = os.path.join(args.out, "manifest.jsonl")
    records = load_manifest(manifest_path)
    if records:
        log(f"resuming: {len(records)} records already in manifest.jsonl")

    with build_client(args, cookies) as client:
        if args.check_auth:
            # A probe answers one question and stops; it saves nothing, and it
            # needs no classification: entitlement is a property of the session,
            # not of the article.
            if not records:
                records = step_enumerate(client, args, records)
            step_check_auth(client, args, records)
            return

        if "enumerate" in steps:
            records = step_enumerate(client, args, records)
            save_manifest(manifest_path, records)
        if "classify" in steps:
            records = step_classify(client, args, records)
            save_manifest(manifest_path, records)
        if "download" in steps:
            records = step_download(client, args, records, manifest_path)
            save_manifest(manifest_path, records)
        if "group" in steps:
            records = step_group(args, records)
            save_manifest(manifest_path, records)
        if "report" in steps:
            records = step_report(args, records)
            save_manifest(manifest_path, records)


if __name__ == "__main__":
    main()
