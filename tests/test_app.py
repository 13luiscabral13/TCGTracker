import app.main as main_module
from fastapi.testclient import TestClient

from app.main import (
    app,
    cleanup_invalid_trackings,
    ensure_product_tracking_rows,
    seed_default_products,
    seed_default_stores,
    seed_example_trackings,
)
from app.models import Product, ProductStoreMapping, Store, Tracking
from app.database import SessionLocal
from app.services.scraper import infer_product_structure, is_lorcana_product_name, parse_product_snapshot

client = TestClient(app)


def test_root_page_loads() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "TCG Tracker" in response.text


def test_seeded_pt_stores() -> None:
    db = SessionLocal()
    try:
        db.query(Store).delete()
        db.commit()
    finally:
        db.close()

    created = seed_default_stores()
    assert created >= 3

    db = SessionLocal()
    try:
        stores = db.query(Store).all()
        assert len(stores) == 4
        assert {store.name for store in stores} == {"Bidirama", "GeekHaven", "LotusValley", "PsyDeck"}
    finally:
        db.close()


def test_seeded_lorcana_products() -> None:
    db = SessionLocal()
    try:
        db.query(Product).delete()
        db.commit()
    finally:
        db.close()

    created = seed_default_products()
    assert created >= 1

    db = SessionLocal()
    try:
        products = db.query(Product).all()
        assert products
        assert any("Lorcana" in product.name for product in products)
        assert any(product.set_name for product in products)
        assert any(product.image_url and product.image_url.startswith(("http://", "https://")) for product in products)
    finally:
        db.close()


def test_infer_product_structure_groups_name_and_set() -> None:
    structured = infer_product_structure("Disney Lorcana Rise of The Floodborn Booster Box")
    assert structured["set_name"] == "Rise of The Floodborn"
    assert structured["product_type"] == "Booster Box"
    assert structured["brand"] == "Disney Lorcana"

    structured = infer_product_structure("Disney Lorcana Fabled Booster Pack")
    assert structured["set_name"] == "Fabled"
    assert structured["product_type"] == "Booster Pack"

    assert is_lorcana_product_name("Disney Lorcana Azurite Sea Booster Box") is True
    assert is_lorcana_product_name("Papel & Mar Salgado") is False
    assert is_lorcana_product_name("Pé de Feijão") is False


def test_ensure_product_tracking_rows_matches_brand_variants(monkeypatch) -> None:
    db = SessionLocal()
    try:
        store = db.query(Store).filter(Store.name == "GeekHaven").first()
        if store is None:
            store = Store(name="GeekHaven", website="https://geekhaven.pt", country="PT", selectors={})
            db.add(store)
            db.commit()
            db.refresh(store)

        product = Product(name="Lorcana Booster Box", set_name="Disney Lorcana")
        db.add(product)
        db.commit()
        db.refresh(product)

        def fake_fetch_store_catalog(store_name: str, urls=None, limit=15):
            if store_name == "GeekHaven":
                return [{"name": "Disney Lorcana Booster Box", "url": "https://geekhaven.pt/products/disney-lorcana-booster-box"}]
            return []

        monkeypatch.setattr("app.main.fetch_store_catalog", fake_fetch_store_catalog)

        created = ensure_product_tracking_rows(db, product)
        assert len(created) == 1
        assert db.query(Tracking).filter(Tracking.product_id == product.id, Tracking.store_id == store.id).count() == 1

        db.query(Tracking).filter(Tracking.product_id == product.id).delete(synchronize_session=False)
        db.query(Product).filter(Product.id == product.id).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def test_product_comparison_omits_missing_stores_and_marks_out_of_stock() -> None:
    db = SessionLocal()
    try:
        product = Product(name="Disney Lorcana Test Product", set_name="Test Set", card_number="TEST-1")
        db.add(product)
        db.commit()
        db.refresh(product)

        target_store = db.query(Store).filter(Store.name == "GeekHaven").first()
        if target_store is None:
            target_store = Store(name="GeekHaven", website="https://geekhaven.pt", country="PT", selectors={})
            db.add(target_store)
            db.commit()
            db.refresh(target_store)

        tracking = Tracking(
            product_id=product.id,
            store_id=target_store.id,
            product_url="https://geekhaven.pt/products/test-product",
            is_active=True,
            last_seen_price=21.99,
            last_seen_promo=False,
            is_in_stock=False,
        )
        db.add(tracking)
        db.commit()

        response = client.get(f"/api/products/{product.id}/comparison")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["stores"]) == 1
        assert payload["stores"][0]["store_name"] == "GeekHaven"
        assert payload["stores"][0]["in_stock"] is False
    finally:
        db.close()


def test_product_detail_page_does_not_trigger_network_discovery_for_untracked_products(monkeypatch) -> None:
    db = SessionLocal()
    try:
        product = Product(name="Disney Lorcana Test Product 2", set_name="Test Set", card_number="TEST-2")
        db.add(product)
        db.commit()
        db.refresh(product)

        calls = {"count": 0}

        def fail_if_called(*args, **kwargs):
            calls["count"] += 1
            raise AssertionError("unexpected network discovery on product detail view")

        monkeypatch.setattr(main_module, "ensure_product_tracking_rows", fail_if_called)

        response = client.get(f"/products/{product.id}")
        assert response.status_code == 200
        assert "No store data available" in response.text
        assert calls["count"] == 0
    finally:
        db.close()


def test_seed_example_trackings_creates_one_per_matching_store(monkeypatch) -> None:
    db = SessionLocal()
    try:
        product_name = "Multi Store Match Product - unique"
        product = Product(name=product_name, set_name="Test Set")
        db.add(product)
        db.commit()
        db.refresh(product)

        def fake_fetch_store_catalog(store_name: str, urls=None, limit=15):
            mapping = {
                "Bidirama": "https://www.bidirama.pt/products/multi-store-match-product-unique",
                "GeekHaven": "https://geekhaven.pt/products/multi-store-match-product-unique",
                "LotusValley": "https://lotusvalley.pt/products/multi-store-match-product-unique",
                "PsyDeck": "https://www.psydeck.pt/products/multi-store-match-product-unique",
            }
            if store_name in mapping:
                return [{"name": product_name, "url": mapping[store_name]}]
            return []

        monkeypatch.setattr("app.main.fetch_store_catalog", fake_fetch_store_catalog)

        created = ensure_product_tracking_rows(db, product)
        rows = db.query(Tracking).filter(Tracking.product_id == product.id).all()

        assert len(created) == 4
        assert {row.store.name for row in rows} == {"Bidirama", "GeekHaven", "LotusValley", "PsyDeck"}

        db.query(ProductStoreMapping).filter(ProductStoreMapping.product_id == product.id).delete(synchronize_session=False)
        db.query(Tracking).filter(Tracking.product_id == product.id).delete(synchronize_session=False)
        db.query(Product).filter(Product.id == product.id).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def test_cleanup_invalid_trackings_removes_example_rows() -> None:
    db = SessionLocal()
    try:
        db.query(Tracking).delete()
        db.commit()

        product = db.query(Product).filter(Product.name == "Test Lorcana Product").first()
        if product is None:
            product = Product(name="Test Lorcana Product", set_name="Test Set")
            db.add(product)
            db.commit()
            db.refresh(product)

        store = db.query(Store).filter(Store.name == "GeekHaven").first()
        if store is None:
            store = Store(name="GeekHaven", website="https://geekhaven.pt", country="PT", selectors={})
            db.add(store)
            db.commit()
            db.refresh(store)

        stale = Tracking(
            product_id=product.id,
            store_id=store.id,
            product_url="https://example.com/product/1",
            is_active=True,
            is_in_stock=True,
        )
        db.add(stale)
        db.commit()

        cleanup_invalid_trackings(db)
        assert db.query(Tracking).filter(Tracking.product_url.like("%example.com%") ).count() == 0
    finally:
        db.close()


def test_tracking_summary_has_visible_prices() -> None:
    summary_response = client.get("/api/tracking-summary")
    assert summary_response.status_code == 200
    payload = summary_response.json()
    assert len(payload) >= 1
    assert any(item.get("store_name") for item in payload)
    assert all(item.get("last_seen_price") is None or isinstance(item.get("last_seen_price"), (int, float)) for item in payload)


def test_tracking_summary_does_not_invent_prices_for_unresolved_rows() -> None:
    db = SessionLocal()
    try:
        product = Product(name="Synthetic test product", set_name="Test Set")
        store = db.query(Store).filter(Store.name == "GeekHaven").first()
        if store is None:
            store = Store(name="GeekHaven", website="https://geekhaven.pt", country="PT", selectors={})
            db.add(store)
            db.commit()
            db.refresh(store)
        db.add(product)
        db.commit(); db.refresh(product)

        tracking = Tracking(
            product_id=product.id,
            store_id=store.id,
            product_url="https://geekhaven.pt/search?q=Synthetic+test+product",
            is_active=True,
            is_in_stock=True,
            last_seen_price=None,
        )
        db.add(tracking)
        db.commit()

        row = db.query(Tracking, Product, Store).join(Product).join(Store).filter(Tracking.id == tracking.id).first()
        assert row is not None
        summary = client.get("/api/tracking-summary")
        assert summary.status_code == 200
        payload = summary.json()
        assert all(item["product_name"] != "Synthetic test product" for item in payload)
    finally:
        db.close()


def test_portuguese_store_list_is_restricted() -> None:
    db = SessionLocal()
    try:
        db.query(Store).delete()
        db.commit()
    finally:
        db.close()

    created = seed_default_stores()
    assert created == 4

    db = SessionLocal()
    try:
        stores = db.query(Store).all()
        names = {store.name for store in stores}
        assert names == {"Bidirama", "GeekHaven", "LotusValley", "PsyDeck"}
    finally:
        db.close()


def test_product_comparison_endpoint() -> None:
    products_response = client.get("/api/products")
    assert products_response.status_code == 200
    products = products_response.json()
    assert products

    comparison_response = client.get(f"/api/products/{products[0]['id']}/comparison")
    assert comparison_response.status_code == 200
    payload = comparison_response.json()
    assert payload["product_id"] == products[0]["id"]
    assert isinstance(payload["stores"], list)
    assert payload["stores"]


def test_product_detail_page_renders_for_missing_product() -> None:
    response = client.get("/products/999999")
    assert response.status_code == 200
    assert "Product #999999" in response.text


def test_scraper_uses_main_product_price_not_sidebar_price(monkeypatch) -> None:
    class FakeResponse:
        text = """
        <html>
          <body>
            <aside class="related-products">
              <div class="price">€500,00</div>
            </aside>
            <main class="summary">
              <h1>Lorcana Booster Box</h1>
              <div class="price"><span class="amount">€29,99</span></div>
            </main>
            <div class="promo">Promo</div>
          </body>
        </html>
        """

        def raise_for_status(self):
            return None

    monkeypatch.setattr("app.services.scraper.requests.get", lambda *args, **kwargs: FakeResponse())

    snapshot = parse_product_snapshot("https://example.com/product")
    assert snapshot["name"] == "Lorcana Booster Box"
    assert snapshot["price_eur"] == 29.99
    assert snapshot["promo"] is True


def test_scraper_extracts_live_product_metadata(monkeypatch) -> None:
    class FakeResponse:
        text = """
        <html>
          <head>
            <meta property="og:title" content="Lorcana Booster Box" />
            <meta property="og:image" content="https://example.com/box.jpg" />
          </head>
          <body>
            <h1>Lorcana Booster Box</h1>
            <div class="price">€29,99</div>
            <div class="promo">Promo</div>
          </body>
        </html>
        """

        def raise_for_status(self):
            return None

    monkeypatch.setattr("app.services.scraper.requests.get", lambda *args, **kwargs: FakeResponse())

    snapshot = parse_product_snapshot("https://example.com/product")
    assert snapshot["name"] == "Lorcana Booster Box"
    assert snapshot["price_eur"] == 29.99
    assert snapshot["promo"] is True
    assert snapshot["image_url"] == "https://example.com/box.jpg"


def test_scraper_uses_store_specific_selectors_for_geekhaven(monkeypatch) -> None:
    class FakeResponse:
        text = """
        <html>
          <head><meta property="og:title" content="Lorcana Booster Box" /></head>
          <body>
            <main>
              <h1 class="product__title">Lorcana Booster Box</h1>
              <div class="product__price"><span>€24,99</span></div>
            </main>
          </body>
        </html>
        """

        def raise_for_status(self):
            return None

    monkeypatch.setattr("app.services.scraper.requests.get", lambda *args, **kwargs: FakeResponse())

    snapshot = parse_product_snapshot("https://geekhaven.pt/products/lorcana-booster-box")
    assert snapshot["name"] == "Lorcana Booster Box"
    assert snapshot["price_eur"] == 24.99


def test_product_store_mapping_uses_direct_store_urls() -> None:
    db = SessionLocal()
    try:
        product = Product(name="Disney Lorcana Booster Box", set_name="Rise of the Floodborn")
        store = db.query(Store).filter(Store.name == "GeekHaven").first()
        if store is None:
            store = Store(name="GeekHaven", website="https://geekhaven.pt", country="PT", selectors={})
        db.add(product)
        db.add(store)
        db.commit()
        db.refresh(product)
        db.refresh(store)

        mapping = ProductStoreMapping(
            product_id=product.id,
            store_id=store.id,
            product_url="https://geekhaven.pt/products/disney-lorcana-booster-box",
            is_direct=True,
        )
        db.add(mapping)
        db.commit()

        saved = db.query(ProductStoreMapping).filter_by(product_id=product.id, store_id=store.id).first()
        assert saved is not None
        assert saved.product_url.startswith("https://geekhaven.pt/products/")
        assert saved.is_direct is True
    finally:
        db.close()


def test_store_and_product_flow() -> None:
    store_response = client.post(
        "/api/stores",
        json={
            "name": "Loja Demo",
            "website": "https://example.com",
            "country": "PT",
            "selectors": {"price": ".price", "name": "h1", "promo": ".promo"},
        },
    )
    assert store_response.status_code == 200

    product_response = client.post(
        "/api/products",
        json={"name": "Lorcana - Simba, Royal Protector", "set_name": "Lorcana", "card_number": "001"},
    )
    assert product_response.status_code == 200

    tracking_response = client.post(
        "/api/trackings",
        json={
            "product_id": product_response.json()["id"],
            "store_id": store_response.json()["id"],
            "product_url": "https://example.com/product/1",
            "is_active": True,
        },
    )
    assert tracking_response.status_code == 200

    dashboard_response = client.get("/api/dashboard")
    assert dashboard_response.status_code == 200
    assert dashboard_response.json()["stores"] >= 1

    sync_response = client.post("/api/sync")
    assert sync_response.status_code == 200
