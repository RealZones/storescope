"""Fetching and parsing the Tebex webstore.

The store sits behind a Cloudflare managed challenge that blocks plain HTTP clients,
so we use curl_cffi to present a real browser TLS fingerprint. No headless browser needed.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Iterable
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests

log = logging.getLogger("dragtrack.scraper")

UUID_RE = re.compile(r"/(?:avatar|body|helm|head|bust|cube|skin)/([0-9a-fA-F]{32})")
PRICE_RE = re.compile(r"([^\d\s.,-]{0,4})\s*(-?\d[\d,]*(?:[.,]\d{1,2})?)")
MONEY_ONLY_RE = re.compile(r"-?\d[\d,]*(?:[.,]\d{1,2})?")
WIDGET_NAME_LIMIT = 20  # Tebex truncates package names in the widget to 20 characters


class FetchError(Exception):
    """Network / HTTP failure."""


class ChallengeError(FetchError):
    """Cloudflare served a challenge page instead of the store."""


@dataclass
class WidgetEntry:
    uuid: str
    username: str
    product_raw: str
    widget_time: str
    avatar: str = ""

    @property
    def hhmm(self) -> str:
        m = re.search(r"(\d{1,2}):(\d{2})", self.widget_time)
        return f"{int(m.group(1)):02d}:{m.group(2)}" if m else ""

    @property
    def strict_key(self) -> str:
        return f"{self.uuid}|{self.product_raw}|{self.hhmm}"

    @property
    def loose_key(self) -> str:
        return f"{self.uuid}|{self.product_raw}"


@dataclass
class TopCustomer:
    username: str
    uuid: str
    text: str


@dataclass
class CatalogProduct:
    package_id: int
    name: str
    category: str
    category_slug: str
    type: str
    price: float | None
    original_price: float | None
    currency_symbol: str
    image: str
    url: str


@dataclass
class FetchResult:
    text: str
    status: int
    latency_ms: int
    profile: str


def normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", name or "").strip()


def widget_key(full_name: str) -> str:
    """What the widget would show for a full product name (20-char hard cut, whitespace collapsed)."""
    return normalize_name(full_name[:WIDGET_NAME_LIMIT])


def clean_widget_name(raw: str) -> str:
    raw = normalize_name(raw)
    return re.sub(r"(\.\.\.|…)$", "", raw).strip()


def parse_money(text: str) -> tuple[str, float | None]:
    """'$9.99' -> ('$', 9.99); '9,99 €' -> ('€', 9.99). Returns (symbol, amount)."""
    text = normalize_name(text)
    if not text:
        return "", None
    m = PRICE_RE.search(text)
    if not m:
        return "", None
    symbol = m.group(1).strip()
    num = m.group(2)
    if not symbol:
        tail = text[m.end():].strip()
        tm = re.match(r"([^\d\s.,-]{1,4})", tail)
        if tm:
            symbol = tm.group(1)
    # Decide decimal separator
    if "," in num and "." in num:
        if num.rfind(",") > num.rfind("."):
            num = num.replace(".", "").replace(",", ".")
        else:
            num = num.replace(",", "")
    elif "," in num:
        # "9,99" -> decimal comma; "1,390" -> thousands
        head, _, frac = num.rpartition(",")
        num = f"{head.replace(',', '')}.{frac}" if len(frac) == 2 else num.replace(",", "")
    try:
        return symbol, float(num)
    except ValueError:
        return symbol, None


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------
class StoreClient:
    """HTTP client with browser TLS impersonation and profile rotation on challenges."""

    FALLBACK_PROFILES = ["chrome", "edge", "safari", "firefox", "chrome_android"]

    def __init__(self, base_url: str, impersonate: str = "chrome", timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        profiles = [impersonate] + [p for p in self.FALLBACK_PROFILES if p != impersonate]
        self.profiles = profiles
        self._profile_idx = 0
        self._session: cffi_requests.Session | None = None
        self.last_profile = impersonate

    @property
    def profile(self) -> str:
        return self.profiles[self._profile_idx % len(self.profiles)]

    def _session_for(self, profile: str) -> cffi_requests.Session:
        if self._session is None or self.last_profile != profile:
            if self._session is not None:
                try:
                    self._session.close()
                except Exception:  # pragma: no cover
                    pass
            self._session = cffi_requests.Session(impersonate=profile)
            self.last_profile = profile
        return self._session

    def rotate(self) -> None:
        self._profile_idx += 1
        log.warning("Rotating TLS profile -> %s", self.profile)

    @staticmethod
    def looks_like_challenge(status: int, text: str, headers) -> bool:
        if headers.get("cf-mitigated") == "challenge":
            return True
        head = text[:3000].lower()
        return status in (403, 429, 503) and ("just a moment" in head or "cf_chl_opt" in head or "challenge-platform" in head)

    def fetch(self, path: str = "/") -> FetchResult:
        url = urljoin(self.base_url + "/", path.lstrip("/"))
        last_err: Exception | None = None
        for attempt in range(len(self.profiles)):
            profile = self.profile
            sess = self._session_for(profile)
            t0 = time.monotonic()
            try:
                resp = sess.get(
                    url,
                    timeout=self.timeout,
                    headers={
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Accept-Language": "en-US,en;q=0.9",
                        "Cache-Control": "no-cache",
                        "Pragma": "no-cache",
                    },
                )
            except Exception as exc:  # network-level failure
                raise FetchError(f"{type(exc).__name__}: {exc}") from exc
            latency = int((time.monotonic() - t0) * 1000)
            if self.looks_like_challenge(resp.status_code, resp.text, resp.headers):
                last_err = ChallengeError(f"Cloudflare challenge (HTTP {resp.status_code}) with profile {profile}")
                log.warning("%s", last_err)
                self.rotate()
                continue
            if resp.status_code != 200:
                raise FetchError(f"HTTP {resp.status_code} for {url}")
            return FetchResult(resp.text, resp.status_code, latency, profile)
        raise last_err or FetchError("all profiles failed")


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------
def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _find_widget(soup: BeautifulSoup, css_class: str, title_words: str):
    widget = soup.select_one(f".widget.{css_class}")
    if widget:
        return widget
    for w in soup.select(".widget"):
        title = w.select_one(".widget-title")
        if title and title_words.lower() in title.get_text(" ", strip=True).lower():
            return w
    return None


def is_store_page(html: str) -> bool:
    """Sanity check that we got a rendered store page and not an error/challenge page."""
    head = html[:4000].lower()
    return "just a moment" not in head and ("widget" in html or "store-product" in html)


def parse_recent_payments(html: str) -> list[WidgetEntry]:
    soup = _soup(html)
    widget = _find_widget(soup, "widget-recent", "recent payments")
    if widget is None:
        return []
    entries: list[WidgetEntry] = []
    for node in widget.select(".purchase"):
        name_el = node.select_one(".username")
        if name_el is None:
            continue
        username = name_el.get_text(" ", strip=True)
        img = node.select_one("img")
        avatar = (img.get("src") or img.get("data-src") or "") if img else ""
        m = UUID_RE.search(avatar)
        uuid = m.group(1).lower() if m else f"name:{username.lower()}"
        prod_el = node.select_one(".info p") or node.select_one("p")
        product_raw = clean_widget_name(prod_el.get_text(" ", strip=True)) if prod_el else ""
        time_el = node.select_one("time")
        widget_time = time_el.get_text(" ", strip=True) if time_el else ""
        entries.append(WidgetEntry(uuid=uuid, username=username, product_raw=product_raw,
                                   widget_time=widget_time, avatar=avatar))
    return entries


def parse_top_customer(html: str) -> TopCustomer | None:
    soup = _soup(html)
    widget = _find_widget(soup, "widget-top-donator", "top customer")
    if widget is None:
        return None
    name_el = widget.select_one(".username")
    if name_el is None:
        return None
    img = widget.select_one("img")
    src = (img.get("src") or img.get("data-src") or "") if img else ""
    m = UUID_RE.search(src)
    text_el = widget.select_one("p")
    return TopCustomer(
        username=name_el.get_text(" ", strip=True),
        uuid=m.group(1).lower() if m else "",
        text=normalize_name(text_el.get_text(" ", strip=True)) if text_el else "",
    )


def discover_category_links(html: str, base_url: str) -> list[tuple[str, str]]:
    """Return [(category_name, absolute_url)] for every /category/ link on a page, in order, de-duplicated."""
    soup = _soup(html)
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for a in soup.select('a[href*="/category/"]'):
        href = a.get("href") or ""
        url = urljoin(base_url + "/", href)
        path = urlparse(url).path.rstrip("/")
        if not path.startswith("/category/") or path in seen:
            continue
        seen.add(path)
        name = normalize_name(a.get_text(" ", strip=True)) or (a.get("title") or "").strip() or path.rsplit("/", 1)[-1]
        out.append((name, url))
    return out


def parse_category_products(html: str, category_name: str, category_url: str, base_url: str) -> list[CatalogProduct]:
    soup = _soup(html)
    slug = urlparse(category_url).path.rstrip("/").rsplit("/", 1)[-1]
    # Prefer the page title ("Store · Gems") for a clean category name
    title = soup.title.get_text(strip=True) if soup.title else ""
    if "·" in title:
        category_name = normalize_name(title.split("·")[-1]) or category_name
    products: list[CatalogProduct] = []
    for art in soup.select(".store-product"):
        actions = art.select_one("[data-package]")
        pid_raw = actions.get("data-package") if actions else None
        if not pid_raw:
            link = art.select_one('a[href*="/package/"]')
            m = re.search(r"/package/(\d+)", link.get("href", "")) if link else None
            pid_raw = m.group(1) if m else None
        if not pid_raw or not str(pid_raw).isdigit():
            continue
        pid = int(pid_raw)
        title_el = art.select_one(".product-title")
        name = normalize_name(title_el.get_text(" ", strip=True)) if title_el else ""
        if not name:
            continue
        ptype = (actions.get("data-package-type") if actions else None) or "single"
        price_el = art.select_one(".price")
        symbol, price, original = "", None, None
        if price_el:
            strong = price_el.select_one("strong") or price_el
            symbol, price = parse_money(strong.get_text(" ", strip=True))
            struck = price_el.select_one("s, del, .original, .old-price, .strike")
            if struck:
                _, original = parse_money(struck.get_text(" ", strip=True))
        img = art.select_one("img")
        image = (img.get("src") or img.get("data-src") or "") if img else ""
        products.append(CatalogProduct(
            package_id=pid, name=name, category=category_name, category_slug=slug, type=ptype,
            price=price, original_price=original, currency_symbol=symbol, image=image,
            url=urljoin(base_url + "/", f"package/{pid}"),
        ))
    return products


def scrape_catalog(client: StoreClient, max_pages: int = 40) -> tuple[list[CatalogProduct], list[str]]:
    """Crawl every category (and sub-category) page. Returns (products, errors)."""
    errors: list[str] = []
    home = client.fetch("/")
    queue = discover_category_links(home.text, client.base_url)
    visited: set[str] = set()
    products: dict[int, CatalogProduct] = {}
    while queue and len(visited) < max_pages:
        name, url = queue.pop(0)
        path = urlparse(url).path
        if path in visited:
            continue
        visited.add(path)
        try:
            page = client.fetch(path)
        except FetchError as exc:
            errors.append(f"{path}: {exc}")
            continue
        for p in parse_category_products(page.text, name, url, client.base_url):
            products.setdefault(p.package_id, p)
        for sub in discover_category_links(page.text, client.base_url):
            if urlparse(sub[1]).path not in visited:
                queue.append(sub)
        time.sleep(0.5)
    return list(products.values()), errors


def match_candidates(product_raw: str, names: Iterable[tuple[int, str]]) -> list[int]:
    """Package ids whose widget rendering equals the widget's (truncated) product text."""
    raw = clean_widget_name(product_raw)
    if not raw:
        return []
    out = [pid for pid, name in names if widget_key(name) == raw]
    if out:
        return out
    # Fallback: prefix match on the normalized full name (handles odd whitespace / longer limits)
    low = raw.lower()
    return [pid for pid, name in names if normalize_name(name).lower().startswith(low)]
