import os
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


def ensure_sqlite_database_is_writable() -> None:
    if not settings.database_url.startswith("sqlite"):
        return

    parsed = urlparse(settings.database_url)
    db_path = parsed.path
    if not db_path:
        return

    candidate = Path(db_path)
    if not candidate.is_absolute():
        candidate = (Path.cwd() / candidate).resolve()

    try:
        candidate.parent.mkdir(parents=True, exist_ok=True)
        if candidate.exists() and not os.access(candidate, os.W_OK):
            os.chmod(candidate, candidate.stat().st_mode | 0o222)
    except OSError:
        pass


ensure_sqlite_database_is_writable()

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def ensure_schema_compatibility() -> None:
    if not settings.database_url.startswith("sqlite"):
        return

    with engine.begin() as connection:
        table_names = {
            row[0]
            for row in connection.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        }

        if "product_store_mappings" not in table_names:
            connection.execute(text(
                """
                CREATE TABLE product_store_mappings (
                    id INTEGER NOT NULL PRIMARY KEY,
                    product_id INTEGER NOT NULL,
                    store_id INTEGER NOT NULL,
                    product_url VARCHAR(500) NOT NULL,
                    is_direct BOOLEAN DEFAULT 1 NOT NULL,
                    is_active BOOLEAN DEFAULT 1 NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    last_verified_at DATETIME,
                    FOREIGN KEY(product_id) REFERENCES products(id),
                    FOREIGN KEY(store_id) REFERENCES stores(id)
                )
                """
            ))

        result = connection.execute(text("PRAGMA table_info(products)"))
        columns = {row[1] for row in result.fetchall()}
        if "image_url" not in columns:
            connection.execute(text("ALTER TABLE products ADD COLUMN image_url VARCHAR(700)"))

        result = connection.execute(text("PRAGMA table_info(trackings)"))
        tracking_columns = {row[1] for row in result.fetchall()}
        if "is_in_stock" not in tracking_columns:
            connection.execute(text("ALTER TABLE trackings ADD COLUMN is_in_stock BOOLEAN DEFAULT 1 NOT NULL"))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
