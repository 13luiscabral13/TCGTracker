from apscheduler.schedulers.background import BackgroundScheduler

from app.config import settings
from app.services.watcher import refresh_tracked_products
from app.database import SessionLocal


scheduler = BackgroundScheduler()


def tick_tracker():
    db = SessionLocal()
    try:
        refresh_tracked_products(db)
    finally:
        db.close()


def start_scheduler():
    if scheduler.running:
        return scheduler

    scheduler.add_job(
        tick_tracker,
        "interval",
        minutes=settings.scheduler_interval_minutes,
        id="tcg_tracker_scheduler",
        replace_existing=True,
    )
    scheduler.start()
    return scheduler
