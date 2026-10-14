import re
import unicodedata
from typing import Any
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup


STORE_SEARCH_URLS = {
    "Bidirama": "https://www.bidirama.pt/?s={query}",
    "GeekHaven": "https://www.geekhaven.pt/search?q={query}",
    "LotusValley": "https://www.lotusvalley.pt/?s={query}",
    "PsyDeck": "https://www.psydeck.pt/?s={query}",
}

STORE_BASE_URLS = {
    "Bidirama": "https://www.bidirama.pt",
    "GeekHaven": "https://geekhaven.pt",
    "LotusValley": "https://lotusvalley.pt",
    "PsyDeck": "https://www.psydeck.pt",
}

PRODUCT_TYPE_PATTERNS = [
    "Booster Box",
    "Booster Pack",
    "Starter Set",
    "Starter Deck",
    "Collection Starter Set",
    "Illumineer's Trove"
    "Illumineer's Quest",
    "Playmat",
    "Bundle",
    "Trove",
    "Gift Set",
    "Deck Protector",
    "Storage Box",
    "Accessory",
    "Box",
    "Pack",
]


def build_store_search_url(store_name: str, query: str) -> str:
    pattern = STORE_SEARCH_URLS.get(store_name)
    if not pattern:
        return f"https://example.com/search?q={query}"
    return pattern.format(query=query.replace(" ", "+"))


def is_lorcana_product_name(product_name: str | None) -> bool:
    if not product_name:
        return False
    normalized = unicodedata.normalize("NFKD", product_name)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_name.lower()
    return "lorcana" in lowered


def infer_product_structure(product_name: str) -> dict[str, str | None]:
    if not product_name:
        return {"brand": None, "set_name": None, "product_type": None}

    cleaned = re.sub(r"\s+", " ", product_name).strip()
    lowered = cleaned.lower()

    brand = "Disney Lorcana" if "lorcana" in lowered else None
    candidate = cleaned
    if brand:
        candidate = re.sub(r"(?i)^disney\s+lorcana\s*[-:]*\s*", "", candidate).strip()

    product_type = None
    for pattern in sorted(PRODUCT_TYPE_PATTERNS, key=len, reverse=True):
        if re.search(rf"(?i)\b{re.escape(pattern)}\b", candidate):
            product_type = pattern
            candidate = re.sub(rf"(?i)\b{re.escape(pattern)}\b", "", candidate).strip(" -:")
            break

    if product_type is None and "box" in lowered:
        product_type = "Box"
    if product_type is None and "pack" in lowered:
        product_type = "Pack"

    set_name = re.sub(r"(?i)\bcollection\b", "", candidate).strip(" -:")
    set_name = re.sub(r"\s+", " ", set_name).strip()
    if not set_name:
        set_name = "General"

    return {
        "brand": brand,
        "set_name": set_name,
        "product_type": product_type,
    }


def _looks_like_product_link(href: str | None, store_name: str | None = None) -> bool:
    if not href or href.startswith("#") or "javascript:" in href.lower():
        return False
    lowered = href.lower()
    if lowered.startswith("/"):
        lowered = f"{STORE_BASE_URLS.get(store_name, 'https://example.com')}{lowered}"
    if lowered.startswith(("http://", "https://")):
        if "/products/" in lowered or "/produto/" in lowered or "/product/" in lowered:
            return True
    return False


def _safe_text(node: Any) -> str:
    if node is None:
        return ""
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()


def _normalize_product_url(href: str, store_name: str) -> str:
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return STORE_BASE_URLS.get(store_name, "https://example.com") + href
    return href


def _resolve_product_image_url(url: str) -> str | None:
    try:
        response = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
    except Exception:
        return None
    soup = BeautifulSoup(response.text, "html.parser")
    image_url = _extract_image_url(soup, {})
    if image_url and image_url.startswith("//"):
        return "https:" + image_url
    return image_url


def _extract_listing_items(soup: BeautifulSoup, store_name: str) -> list[dict[str, str | None]]:
    items: list[dict[str, str | None]] = []
    seen: set[str] = set()

    for anchor in soup.select("a[href]"):
        href = anchor.get("href")
        if not _looks_like_product_link(href, store_name):
            continue

        normalized_href = _normalize_product_url(href, store_name)
        text = _safe_text(anchor)
        if not text or not is_lorcana_product_name(text):
            continue

        image = anchor.find("img")
        image_url = image.get("src") if image else None
        if image_url and image_url.startswith("//"):
            image_url = "https:" + image_url
        if image_url and image_url.startswith("data:"):
            image_url = None
        if not image_url:
            image_url = _resolve_product_image_url(normalized_href)

        normalized = infer_product_structure(text)
        key = (normalized["brand"] or "") + "|" + (normalized["set_name"] or "") + "|" + (normalized["product_type"] or "")
        if key in seen:
            continue
        seen.add(key)

        items.append(
            {
                "name": text,
                "set_name": normalized["set_name"],
                "product_type": normalized["product_type"],
                "brand": normalized["brand"],
                "url": normalized_href,
                "image_url": image_url,
            }
        )

    return items


def fetch_store_catalog(store_name: str, urls: list[str] | None = None, limit: int = 15) -> list[dict[str, str | None]]:
    candidates = urls or [
        STORE_SEARCH_URLS.get(store_name, ""),
        {
            "GeekHaven": "https://geekhaven.pt/collections/disney-lorcana",
            "LotusValley": "https://lotusvalley.pt/categoria-produto/trading-card-games/lorcana/",
            "Bidirama": "https://www.bidirama.pt/?s=lorcana",
            "PsyDeck": "https://www.psydeck.pt/?s=lorcana",
        }.get(store_name, ""),
    ]
    candidates = [candidate for candidate in candidates if candidate]
    if not candidates:
        return []

    collected: list[dict[str, str | None]] = []
    seen: set[str] = set()
    for url in candidates:
        try:
            response = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
            response.raise_for_status()
        except Exception:
            continue

        soup = BeautifulSoup(response.text, "html.parser")
        for item in _extract_listing_items(soup, store_name):
            if not item.get("name") or not is_lorcana_product_name(item["name"]):
                continue
            distinct_key = (item["name"] or "").lower()
            if distinct_key in seen:
                continue
            seen.add(distinct_key)
            collected.append(item)
            if len(collected) >= limit:
                return collected

    return collected


PRICE_SELECTOR_CANDIDATES = [
    ".summary .price .amount",
    ".summary .price",
    ".product-info .price .amount",
    ".product-info .price",
    ".single-product .price .amount",
    ".single-product .price",
    ".product .price .amount",
    ".product .price",
    ".product-price .amount",
    ".product-price",
    ".woocommerce-Price-amount",
    ".amount",
    ".price",
]

STORE_SELECTOR_MAP: dict[str, dict[str, str]] = {
    "GeekHaven": {
        "price": ".price, .product__price, .product-card__price, .product-price, .product-price .amount, [data-price], .amount, .money",
        "promo": ".promo, .offer, .oferta, .desconto, .discount, .sale, .badge, .free-shipping, .save",
        "name": "h1.product__title, h1, .product-card__title, .product-title, .product-item-name",
        "image": "meta[property='og:image'], meta[name='og:image'], img.product-card__image, img.product__image, img[src]",
    },
    "LotusValley": {
        "price": ".woocommerce-Price-amount .amount, .price .amount, .product-price .amount, .amount, .price, [data-price], .money",
        "promo": ".promo, .offer, .oferta, .desconto, .discount, .sale, .badge, .hot",
        "name": "h1.product_title, h1, .product-name, .woocommerce-loop-product__title, .entry-title",
        "image": "meta[property='og:image'], meta[name='og:image'], img.woocommerce-main-image, img.wp-post-image, img[src]",
    },
    "Bidirama": {
        "price": ".price, .product-price, .product-card__price, .woocommerce-Price-amount, .amount, [data-price], .money",
        "promo": ".promo, .offer, .oferta, .desconto, .discount, .sale, .badge, .save",
        "name": "h1.product-title, h1, .product_name, .product-card__title, .product-item-name, .entry-title",
        "image": "meta[property='og:image'], meta[name='og:image'], img.product-image, img.wp-post-image, img[src]",
    },
    "PsyDeck": {
        "price": ".price, .product-price, .product-card__price, .woocommerce-Price-amount, .amount, [data-price], .money",
        "promo": ".promo, .offer, .oferta, .desconto, .discount, .sale, .badge, .save",
        "name": "h1.product-title, h1, .product_name, .product-card__title, .product-item-name, .entry-title",
        "image": "meta[property='og:image'], meta[name='og:image'], img.product-image, img.wp-post-image, img[src]",
    },
}


def _store_name_from_url(url: str) -> str | None:
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return None
    for store_name in STORE_SELECTOR_MAP:
        if store_name.lower() in host or store_name.lower().replace("valley", "") in host:
            return store_name
    return None


def _resolved_store_selectors(selectors: dict | None, url: str | None = None) -> dict[str, str | None]:
    resolved: dict[str, str | None] = {}
    store_name = _store_name_from_url(url or "") if url else None
    if store_name and store_name in STORE_SELECTOR_MAP:
        resolved.update(STORE_SELECTOR_MAP[store_name])
    if selectors:
        resolved.update({key: value for key, value in selectors.items() if value})
    return resolved


def _extract_text_from_selector(soup: BeautifulSoup, selector: str | None) -> str | None:
    if not selector:
        return None
    for candidate in selector.split(","):
        cleaned = candidate.strip()
        if not cleaned:
            continue
        element = soup.select_one(cleaned)
        if element is not None:
            text = " ".join(element.get_text(" ", strip=True).split())
            if text:
                return text
    return None


def _extract_best_price_text(soup: BeautifulSoup, selector: str | None) -> str | None:
    candidate_selectors: list[str] = []
    candidate_selectors.extend(PRICE_SELECTOR_CANDIDATES)
    if selector:
        candidate_selectors.extend(part.strip() for part in selector.split(",") if part.strip())

    seen: set[str] = set()
    for candidate in candidate_selectors:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        for element in soup.select(candidate):
            text = " ".join(element.get_text(" ", strip=True).split())
            if not text or not re.search(r"\d", text):
                continue
            lowered = text.lower()
            if any(token in lowered for token in ["search", "cart", "wishlist", "checkout", "add to cart"]):
                continue
            return text

    price_tokens = re.findall(r"(?:€|EUR|\bUSD\b)?\s?\d{1,3}(?:[.,]\d{2,3})+", soup.get_text(" ", strip=True))
    for token in price_tokens:
        lowered = token.lower()
        if any(block in lowered for block in ["search", "cart", "wishlist", "checkout"]):
            continue
        return token

    return None


def _extract_meta_content(soup: BeautifulSoup, *names: str) -> str | None:
    for name in names:
        tag = soup.select_one(f'meta[property="{name}"]') or soup.select_one(f'meta[name="{name}"]')
        if tag is not None:
            content = tag.get("content")
            if content:
                return content.strip()
    return None


def _extract_image_url(soup: BeautifulSoup, selectors: dict | None = None) -> str | None:
    selectors = selectors or {}

    for candidate in [
        selectors.get("image"),
        "meta[property='og:image'], meta[name='og:image']",
        "meta[property='twitter:image'], meta[name='twitter:image']",
        "img[itemprop='image'], img[class*='product'], img[data-src], img[src]",
    ]:
        if not candidate:
            continue
        for selector in candidate.split(","):
            cleaned = selector.strip()
            if not cleaned:
                continue
            elements = soup.select(cleaned)
            for element in elements:
                image_url = element.get("content") or element.get("src") or element.get("data-src")
                if image_url and image_url.startswith(("http://", "https://", "data:")):
                    return image_url
    return None


def parse_price_value(raw_value: str | None) -> float:
    if raw_value is None:
        raise ValueError("No price found in the page content.")
    cleaned = raw_value.replace("€", "").replace("EUR", "").replace("PT", "")
    cleaned = cleaned.replace(" ", "")
    match = re.search(r"(\d+[.,]?\d*)", cleaned)
    if not match:
        raise ValueError(f"Could not parse a numeric price from: {raw_value!r}")
    value = match.group(1).replace(".", "").replace(",", ".")
    return float(value)


def parse_product_snapshot(url: str, selectors: dict | None = None) -> dict[str, Any]:
    resolved_selectors = _resolved_store_selectors(selectors, url)
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; TCGTracker/1.0; +https://example.com)"
    }
    response = requests.get(url, timeout=8, headers=headers)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    page_text = response.text.lower()
    out_of_stock_tokens = [
        "esgotado",
        "sold out",
        "out of stock",
        "unavailable",
        "indisponível",
        "indisponivel",
        "sem stock",
        "not available",
    ]
    in_stock = not any(token in page_text for token in out_of_stock_tokens)

    raw_price = _extract_best_price_text(soup, resolved_selectors.get("price") or ".price, .product-price, .amount, [data-price]")
    if raw_price is None:
        raw_price = _extract_meta_content(soup, "product:price:amount", "price")

    raw_promo = _extract_text_from_selector(soup, resolved_selectors.get("promo") or ".promo, .offer, .oferta, .desconto, .discount, .sale")
    promo = bool(raw_promo) or any(token in page_text for token in ["promo", "oferta", "desconto", "promoção", "sale", "offer"])

    product_name = (
        _extract_meta_content(soup, "og:title", "twitter:title")
        or _extract_text_from_selector(soup, resolved_selectors.get("name") or resolved_selectors.get("product_name") or "h1")
        or url
    )
    image_url = _extract_image_url(soup, resolved_selectors) or _extract_meta_content(soup, "og:image", "twitter:image")

    if raw_price is None:
        return {
            "name": product_name or url,
            "price_eur": None,
            "promo": promo,
            "source_url": url,
            "image_url": image_url,
            "in_stock": in_stock,
        }

    price_value = parse_price_value(raw_price)

    return {
        "name": product_name or url,
        "price_eur": price_value,
        "promo": promo,
        "source_url": url,
        "image_url": image_url,
        "in_stock": in_stock,
    }
