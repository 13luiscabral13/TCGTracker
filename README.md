# TCG Tracker MVP

This project is a lightweight MVP for a Lorcana price tracker focused on Portuguese stores.

## Features

- Track selected products across chosen stores
- Daily or bi-daily scheduled refreshes
- Promo detection with price history
- Notification hooks for email, Discord, and Telegram
- Small web dashboard to add and monitor products

## Stack

- FastAPI
- SQLAlchemy + SQLite
- APScheduler
- BeautifulSoup + Requests
- Jinja templates

## Local setup

1. Create and activate a virtual environment if needed.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Copy environment file:
   ```bash
   cp .env.example .env
   ```
4. Start the app:
   ```bash
   uvicorn app.main:app --reload
   ```
5. Open http://127.0.0.1:8000

## Seeded Portuguese stores

The app starts with a set of Portuguese TCG shops already configured:

- Ludonaute
- Nerd Zone
- Cartas & Jogos
- Duel Zone

These are seeded with realistic retailer domains and CSS selector hints for product pages.

## Example box tracking setup

Use the dashboard to add a product such as:

- Product: "Lorcana Booster Box"
- Store: "Ludonaute"
- Product URL: a Lorcana booster box page from the selected retailer
- Price selector: `.product-price, .price, .woocommerce-Price-amount` 
- Promo selector: `.promo, .oferta, .desconto, .sale` (optional)

This is intentionally set up for booster boxes and other Lorcana bundles rather than single cards.

The scheduler runs every 24 hours by default. To make it bi-daily, change `SCHEDULER_INTERVAL_MINUTES` to 720.

## Notification flow

When a tracker's price drops or promo status changes, the app stores the new data and sends a notification via the enabled channels.

## Limitations

This is intentionally a pragmatic MVP. Website scraping depends on stable selectors and anti-bot protections, so the best version is tuned per retailer.
