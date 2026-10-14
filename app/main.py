import re
import unicodedata
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import Base, SessionLocal, engine, ensure_schema_compatibility, get_db
from app.models import NotificationChannel, Product, ProductStoreMapping, Store, Tracking
from app.schemas import NotificationChannelCreate, ProductCreate, StoreCreate, TrackingCreate
from app.services.scheduler import start_scheduler
from app.services.scraper import fetch_store_catalog, is_lorcana_product_name
from app.services.watcher import refresh_tracked_products


PORTUGUESE_STORE_NAMES = ["Bidirama", "GeekHaven", "LotusValley", "PsyDeck"]

DEFAULT_PORTUGUESE_STORES = [
    {
        "name": "Bidirama",
        "website": "https://www.bidirama.pt",
        "country": "PT",
        "selectors": {
            "price": ".price, .product-price, .product-card__price, .woocommerce-Price-amount, .amount, [data-price], .money",
            "promo": ".promo, .offer, .oferta, .desconto, .discount, .sale, .badge, .save",
            "name": "h1.product-title, h1, .product_name, .product-card__title, .product-item-name, .entry-title",
            "image": "meta[property='og:image'], meta[name='og:image'], img.product-image, img.wp-post-image, img[src]",
            "search": "https://www.bidirama.pt/?s={query}",
        },
    },
    {
        "name": "GeekHaven",
        "website": "https://www.geekhaven.pt",
        "country": "PT",
        "selectors": {
            "price": ".price, .product__price, .product-card__price, .product-price, .product-price .amount, [data-price], .amount, .money",
            "promo": ".promo, .offer, .oferta, .desconto, .discount, .sale, .badge, .free-shipping, .save",
            "name": "h1.product__title, h1, .product-card__title, .product-title, .product-item-name",
            "image": "meta[property='og:image'], meta[name='og:image'], img.product-card__image, img.product__image, img[src]",
            "search": "https://www.geekhaven.pt/search?q={query}",
        },
    },
    {
        "name": "LotusValley",
        "website": "https://www.lotusvalley.pt",
        "country": "PT",
        "selectors": {
            "price": ".woocommerce-Price-amount .amount, .price .amount, .product-price .amount, .amount, .price, [data-price], .money",
            "promo": ".promo, .offer, .oferta, .desconto, .discount, .sale, .badge, .hot",
            "name": "h1.product_title, h1, .product-name, .woocommerce-loop-product__title, .entry-title",
            "image": "meta[property='og:image'], meta[name='og:image'], img.woocommerce-main-image, img.wp-post-image, img[src]",
            "search": "https://www.lotusvalley.pt/?s={query}",
        },
    },
    {
        "name": "PsyDeck",
        "website": "https://www.psydeck.pt",
        "country": "PT",
        "selectors": {
            "price": ".price, .product-price, .product-card__price, .woocommerce-Price-amount, .amount, [data-price], .money",
            "promo": ".promo, .offer, .oferta, .desconto, .discount, .sale, .badge, .save",
            "name": "h1.product-title, h1, .product_name, .product-card__title, .product-item-name, .entry-title",
            "image": "meta[property='og:image'], meta[name='og:image'], img.product-image, img.wp-post-image, img[src]",
            "search": "https://www.psydeck.pt/?s={query}",
        },
    },
]

DEFAULT_LORCANA_PRODUCTS = [
    {"name": "Lorcana Booster Box", "set_name": "Disney Lorcana", "card_number": "BOX-01", "image_color": "#7c3aed"},
    {"name": "Lorcana Illumineer’s Trove", "set_name": "Disney Lorcana", "card_number": "TROVE-01", "image_color": "#0f766e"},
    {"name": "Lorcana Gift Set", "set_name": "Disney Lorcana", "card_number": "GIFT-01", "image_color": "#c2410c"},
    {"name": "Lorcana Starter Deck - Ruby/Sapphire", "set_name": "Disney Lorcana", "card_number": "ST-01", "image_color": "#dc2626"},
    {"name": "Lorcana Starter Deck - Amber/Amethyst", "set_name": "Disney Lorcana", "card_number": "ST-02", "image_color": "#f59e0b"},
    {"name": "Lorcana Booster Pack", "set_name": "Disney Lorcana", "card_number": "PACK-01", "image_color": "#2563eb"},
    {"name": "Lorcana Deck Protector", "set_name": "Accessories", "card_number": "ACC-01", "image_color": "#059669"},
    {"name": "Lorcana Storage Box", "set_name": "Accessories", "card_number": "ACC-02", "image_color": "#4f46e5"},
]


def is_direct_product_url(url: str | None) -> bool:
    if not url:
        return False
    lowered = url.lower()
    if "/search" in lowered or "?s=" in lowered or "search?q=" in lowered or "/?s=" in lowered:
        return False
    return "/product" in lowered or "/products/" in lowered or "/produto/" in lowered or "/produtos/" in lowered


def normalize_product_name(name: str | None) -> str:
    if not name:
        return ""
    normalized = unicodedata.normalize("NFKD", name)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_name.lower().replace("&", " and ")
    lowered = re.sub(r"[^a-z0-9]+", " ", lowered)
    lowered = re.sub(r"\b(disney|lorcana)\b", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def product_names_match(left: str | None, right: str | None) -> bool:
    left_key = normalize_product_name(left)
    right_key = normalize_product_name(right)
    if not left_key or not right_key:
        return False
    if left_key == right_key:
        return True
    return left_key in right_key or right_key in left_key


def generate_box_art_url(name: str, color: str) -> str:
    label = (name[:20] + "...") if len(name) > 20 else name
    svg = f"""
    <svg xmlns='http://www.w3.org/2000/svg' width='600' height='820' viewBox='0 0 600 820'>
      <defs>
        <linearGradient id='g' x1='0' x2='1'>
          <stop offset='0%' stop-color='{color}'/>
          <stop offset='100%' stop-color='#111827'/>
        </linearGradient>
      </defs>
      <rect width='600' height='820' rx='26' fill='#f8fafc'/>
      <rect x='48' y='40' width='504' height='740' rx='22' fill='url(#g)'/>
      <rect x='72' y='72' width='456' height='90' rx='16' fill='rgba(255,255,255,0.08)'/>
      <text x='300' y='160' text-anchor='middle' font-size='30' font-family='Arial, sans-serif' fill='white' letter-spacing='3'>LORCANA</text>
      <rect x='120' y='248' width='360' height='260' rx='24' fill='rgba(255,255,255,0.12)' stroke='rgba(255,255,255,0.4)'/>
      <circle cx='300' cy='380' r='90' fill='rgba(255,255,255,0.18)'/>
      <text x='300' y='395' text-anchor='middle' font-size='62' font-family='Arial, sans-serif' fill='white'>★</text>
      <text x='300' y='610' text-anchor='middle' font-size='28' font-family='Arial, sans-serif' fill='white'>{label}</text>
      <text x='300' y='660' text-anchor='middle' font-size='20' font-family='Arial, sans-serif' fill='rgba(255,255,255,0.8)'>DISNEY</text>
      <text x='300' y='700' text-anchor='middle' font-size='18' font-family='Arial, sans-serif' fill='rgba(255,255,255,0.7)'>TCG PRODUCT</text>
    </svg>
    """
    return "data:image/svg+xml;charset=utf-8," + quote(svg)


Base.metadata.create_all(bind=engine)
ensure_schema_compatibility()


def seed_default_stores() -> int:
    db = SessionLocal()
    try:
        supported_names = set(PORTUGUESE_STORE_NAMES)
        db.query(Store).filter(Store.name.not_in(supported_names)).delete(synchronize_session=False)

        existing_names = {store.name for store in db.query(Store).all()}
        created = 0
        for store_data in DEFAULT_PORTUGUESE_STORES:
            if store_data["name"] in existing_names:
                continue
            store = Store(**store_data)
            db.add(store)
            created += 1
        db.commit()
        return len(db.query(Store).filter(Store.name.in_(PORTUGUESE_STORE_NAMES)).all())
    finally:
        db.close()


def seed_default_products() -> int:
    db = SessionLocal()
    try:
        existing = db.query(Product).count()
        if existing > 0:
            return existing

        catalog_rows: list[dict[str, str | None]] = []
        seen: set[str] = set()
        stores = db.query(Store).filter(Store.name.in_(PORTUGUESE_STORE_NAMES)).all()

        for store in stores:
            try:
                for item in fetch_store_catalog(store.name, limit=20):
                    name = item.get("name") or ""
                    if not name or not is_lorcana_product_name(name):
                        continue
                    key = name.lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    catalog_rows.append(item)
            except Exception:
                continue

        if not catalog_rows:
            for product_data in DEFAULT_LORCANA_PRODUCTS:
                catalog_rows.append(
                    {
                        "name": product_data["name"],
                        "set_name": product_data["set_name"],
                        "card_number": product_data["card_number"],
                        "url": None,
                        "image_url": generate_box_art_url(product_data["name"], product_data["image_color"]),
                    }
                )

        created = 0
        for product_data in catalog_rows:
            name = product_data.get("name") or "Unknown product"
            set_name = product_data.get("set_name") or "General"
            image_url = product_data.get("image_url") or generate_box_art_url(name, "#7c3aed")
            product = Product(
                name=name,
                set_name=set_name,
                card_number=product_data.get("card_number"),
                image_url=image_url,
            )
            db.add(product)
            created += 1
        db.commit()
        return created
    finally:
        db.close()


def cleanup_invalid_trackings(db: Session) -> int:
    invalid_ids = []
    for tracking in db.query(Tracking).all():
        if tracking.product_url and "example.com" in tracking.product_url:
            invalid_ids.append(tracking.id)
            continue
        if tracking.store_id is None:
            invalid_ids.append(tracking.id)
            continue
        if not is_direct_product_url(tracking.product_url):
            invalid_ids.append(tracking.id)
            continue
        store = db.query(Store).filter(Store.id == tracking.store_id).first()
        if store is None or store.name not in PORTUGUESE_STORE_NAMES:
            invalid_ids.append(tracking.id)

    if invalid_ids:
        db.query(Tracking).filter(Tracking.id.in_(invalid_ids)).delete(synchronize_session=False)
        db.commit()
    return len(invalid_ids)


def seed_example_trackings() -> int:
    db = SessionLocal()
    try:
        cleanup_invalid_trackings(db)

        products = db.query(Product).order_by(Product.id).all()
        stores = db.query(Store).order_by(Store.id).all()
        if not products or not stores:
            return 0

        created = 0
        for product in products:
            product_created = ensure_product_tracking_rows(db, product)
            created += len(product_created)
        db.commit()
        return created
    finally:
        db.close()


def upsert_product_store_mapping(
    db: Session,
    product_id: int,
    store_id: int,
    product_url: str,
    *,
    is_direct: bool = True,
) -> ProductStoreMapping:
    mapping = (
        db.query(ProductStoreMapping)
        .filter(ProductStoreMapping.product_id == product_id, ProductStoreMapping.store_id == store_id)
        .first()
    )
    if mapping is None:
        mapping = ProductStoreMapping(
            product_id=product_id,
            store_id=store_id,
            product_url=product_url,
            is_direct=is_direct,
            is_active=True,
        )
        db.add(mapping)
    else:
        mapping.product_url = product_url
        mapping.is_direct = is_direct
        mapping.is_active = True
        mapping.last_verified_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(mapping)
    return mapping


def ensure_product_tracking_rows(db: Session, product: Product) -> list[Tracking]:
    stores = db.query(Store).filter(Store.name.in_(PORTUGUESE_STORE_NAMES)).all()
    if not stores or not product.name:
        return []

    created: list[Tracking] = []
    for store in stores:
        existing = db.query(Tracking).filter(Tracking.product_id == product.id, Tracking.store_id == store.id).first()
        if existing is not None:
            continue
        try:
            for item in fetch_store_catalog(store.name, limit=10):
                item_name = item.get("name")
                if not product_names_match(product.name, item_name):
                    continue
                product_url = item.get("url")
                if not is_direct_product_url(product_url):
                    continue
                tracking = Tracking(
                    product_id=product.id,
                    store_id=store.id,
                    product_url=product_url,
                    is_active=True,
                    last_seen_price=None,
                    last_seen_promo=False,
                    is_in_stock=True,
                )
                db.add(tracking)
                db.flush()
                upsert_product_store_mapping(db, product.id, store.id, product_url, is_direct=True)
                created.append(tracking)
                break
        except Exception:
            continue

    if created:
        db.commit()
    return created


@asynccontextmanager
async def lifespan(app: FastAPI):
    seed_default_stores()
    seed_default_products()
    with SessionLocal() as db:
        cleanup_invalid_trackings(db)
    seed_example_trackings()
    app.state.scheduler = start_scheduler()
    yield
    app.state.scheduler.shutdown()


app = FastAPI(title="TCG Tracker", lifespan=lifespan)
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/products/{product_id}", response_class=HTMLResponse)
async def product_detail(request: Request, product_id: int, db: Session = Depends(get_db)):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        product = Product(
            id=product_id,
            name=f"Product #{product_id}",
            set_name="Unavailable",
            card_number=None,
            image_url=None,
        )

    comparison = {
        "product_id": product.id,
        "product_name": product.name,
        "stores": [],
    }

    # Avoid remote product discovery on a page render: store searches are slow and can block
    # subsequent requests while the detail page is waiting on network responses.
    stores = db.query(Store).filter(Store.name.in_(PORTUGUESE_STORE_NAMES)).all()
    for store in stores:
        tracking = db.query(Tracking).filter(Tracking.product_id == product_id, Tracking.store_id == store.id).first()
        if tracking is None:
            continue
        if tracking.product_url and "example.com" in tracking.product_url:
            continue
        comparison["stores"].append(
            {
                "store_id": store.id,
                "store_name": store.name,
                "last_seen_price": tracking.last_seen_price,
                "last_seen_promo": tracking.last_seen_promo,
                "product_url": tracking.product_url,
                "in_stock": tracking.is_in_stock,
            }
        )

    return templates.TemplateResponse(
        request,
        "product_detail.html",
        {"product": product, "comparison": comparison},
    )


@app.get("/api/stores")
async def get_stores(db: Session = Depends(get_db)):
    return db.query(Store).filter(Store.name.in_(PORTUGUESE_STORE_NAMES)).all()


@app.post("/api/stores")
async def create_store(payload: StoreCreate, db: Session = Depends(get_db)):
    existing = db.query(Store).filter(Store.name == payload.name).first()
    if existing:
        return existing

    store = Store(**payload.model_dump())
    db.add(store)
    db.commit()
    db.refresh(store)
    return store


@app.get("/api/products")
async def get_products(db: Session = Depends(get_db)):
    return db.query(Product).all()


@app.post("/api/products")
async def create_product(payload: ProductCreate, db: Session = Depends(get_db)):
    product_payload = payload.model_dump()
    if not product_payload.get("image_url"):
        product_payload["image_url"] = generate_box_art_url(product_payload["name"], "#7c3aed")

    product = Product(**product_payload)
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


@app.get("/api/trackings")
async def get_trackings(db: Session = Depends(get_db)):
    return db.query(Tracking).all()


@app.get("/api/tracking-summary")
async def get_tracking_summary(db: Session = Depends(get_db)):
    if db.query(Tracking).count() == 0:
        seed_example_trackings()

    if db.query(Tracking).filter(Tracking.last_seen_price.is_(None)).count() > 0:
        try:
            refresh_tracked_products(db)
        except Exception:
            pass

    rows = db.query(Tracking, Product, Store).join(Product).join(Store).filter(Store.name.in_(PORTUGUESE_STORE_NAMES)).all()
    payload = []
    for tracking, product, store in rows:
        if tracking.product_url and "example.com" in tracking.product_url:
            continue
        if not is_direct_product_url(tracking.product_url):
            continue
        payload.append(
            {
                "id": tracking.id,
                "product_id": product.id,
                "product_name": product.name,
                "store_id": store.id,
                "store_name": store.name,
                "last_seen_price": tracking.last_seen_price,
                "last_seen_promo": tracking.last_seen_promo,
                "in_stock": tracking.is_in_stock,
                "image_url": product.image_url,
                "product_url": tracking.product_url,
            }
        )
    return payload


@app.get("/api/products/{product_id}/comparison")
async def get_product_comparison(product_id: int, db: Session = Depends(get_db)):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    stores = db.query(Store).filter(Store.name.in_(PORTUGUESE_STORE_NAMES)).all()
    rows = []
    # Keep the comparison endpoint read-only: discovery is expensive and should not block
    # branch requests while a user is opening a product detail.
    for store in stores:
        tracking = db.query(Tracking).filter(Tracking.product_id == product_id, Tracking.store_id == store.id).first()
        if tracking is None:
            continue
        rows.append(
            {
                "store_id": store.id,
                "store_name": store.name,
                "last_seen_price": tracking.last_seen_price,
                "last_seen_promo": tracking.last_seen_promo,
                "product_url": tracking.product_url,
                "in_stock": tracking.is_in_stock,
            }
        )

    return {"product_id": product.id, "product_name": product.name, "stores": rows}


@app.post("/api/trackings")
async def create_tracking(payload: TrackingCreate, db: Session = Depends(get_db)):
    product = db.query(Product).filter(Product.id == payload.product_id).first()
    store = db.query(Store).filter(Store.id == payload.store_id).first()
    if not product or not store:
        raise HTTPException(status_code=404, detail="Product or store not found")

    tracking = Tracking(
        product_id=payload.product_id,
        store_id=payload.store_id,
        product_url=payload.product_url,
        is_active=payload.is_active,
        is_in_stock=payload.is_in_stock,
    )
    db.add(tracking)
    db.flush()
    if is_direct_product_url(payload.product_url):
        upsert_product_store_mapping(db, payload.product_id, payload.store_id, payload.product_url, is_direct=True)
    db.commit()
    db.refresh(tracking)
    return tracking


@app.get("/api/notifications")
async def get_notifications(db: Session = Depends(get_db)):
    return db.query(NotificationChannel).all()


@app.post("/api/notifications")
async def create_notification(payload: NotificationChannelCreate, db: Session = Depends(get_db)):
    channel = NotificationChannel(**payload.model_dump())
    db.add(channel)
    db.commit()
    db.refresh(channel)
    return channel


@app.post("/api/sync")
async def sync_prices(db: Session = Depends(get_db)):
    updated = refresh_tracked_products(db)
    return {"updated": updated}


@app.get("/api/dashboard")
async def dashboard(db: Session = Depends(get_db)):
    stores = db.query(Store).count()
    products = db.query(Product).count()
    trackings = db.query(Tracking).count()
    return {
        "stores": stores,
        "products": products,
        "trackings": trackings,
    }
