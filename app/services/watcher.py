from sqlalchemy.orm import Session

from app.models import NotificationChannel, PriceRecord, PromoEvent, Tracking
from app.services.notifications import send_notification
from app.services.scraper import parse_product_snapshot


def refresh_tracked_products(db: Session) -> int:
    trackings = db.query(Tracking).filter(Tracking.is_active.is_(True)).all()
    updated = 0

    for tracking in trackings:
        store = tracking.store
        try:
            snapshot = parse_product_snapshot(tracking.product_url, store.selectors)
        except Exception:
            continue

        price = snapshot.get("price_eur")
        promo = bool(snapshot["promo"])
        in_stock = bool(snapshot.get("in_stock", True))
        image_url = snapshot.get("image_url")
        if image_url and tracking.product.image_url != image_url:
            tracking.product.image_url = image_url

        if snapshot.get("name") and tracking.product.name != snapshot["name"]:
            tracking.product.name = snapshot["name"]

        if price is not None:
            record = PriceRecord(
                tracking_id=tracking.id,
                price_eur=price,
                promo=promo,
                source_url=snapshot["source_url"],
                note=snapshot["name"],
            )
            db.add(record)

        previous_price = tracking.last_seen_price
        previous_promo = tracking.last_seen_promo
        tracking.last_seen_price = price
        tracking.last_seen_promo = promo
        tracking.is_in_stock = in_stock

        if previous_price is None or previous_price != price or previous_promo != promo:
            updated += 1
            if previous_price is not None and (promo or price < previous_price):
                message = (
                    f"Promo alert: {tracking.product.name} at {tracking.store.name} changed from "
                    f"{previous_price:.2f}€ to {price:.2f}€"
                )
                event = PromoEvent(
                    tracking_id=tracking.id,
                    old_price=previous_price,
                    new_price=price,
                    message=message,
                )
                db.add(event)
                channels = db.query(NotificationChannel).filter(NotificationChannel.enabled.is_(True)).all()
                for channel in channels:
                    send_notification(channel, message)

    db.commit()
    return updated
