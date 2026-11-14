"""SQLite 存储层：商品快照、价格历史、采集日志。

- 商品完整数据用 JSON 存放（字段会持续演进），同时把常用列单独入表便于查询；
- 每次价格变化追加一条 offers 记录，形成真实价格历史；
- fetch_log 记录每一次采集尝试（含失败原因），界面可查看。
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from .models import Product, now_text

__all__ = ["Database", "db", "default_db_path"]

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "betterbuy.sqlite3"

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    product_id   TEXT PRIMARY KEY,
    platform     TEXT,
    url          TEXT,
    name         TEXT,
    brand        TEXT,
    category     TEXT,
    price        REAL,
    currency     TEXT,
    payload      TEXT NOT NULL,
    fetched_at   TEXT
);
CREATE TABLE IF NOT EXISTS offers (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id       TEXT NOT NULL,
    price            REAL,
    currency         TEXT,
    original_price   REAL,
    discount_percent INTEGER,
    region           TEXT,
    adapter          TEXT,
    fetched_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_offers_product ON offers(product_id, id DESC);
CREATE TABLE IF NOT EXISTS fetch_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    url         TEXT,
    adapter     TEXT,
    ok          INTEGER,
    kind        TEXT,
    message     TEXT,
    http_status INTEGER,
    created_at  TEXT
);
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def default_db_path() -> Path:
    return Path(os.environ.get("BETTERBUY_DB", str(DEFAULT_PATH)))


class Database:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    # ---------- 商品 ----------
    def save_product(self, product: Product) -> None:
        payload = product.to_json()
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO products (product_id, platform, url, name, brand, category,
                                         price, currency, payload, fetched_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(product_id) DO UPDATE SET
                     platform=excluded.platform, url=excluded.url, name=excluded.name,
                     brand=excluded.brand, category=excluded.category, price=excluded.price,
                     currency=excluded.currency, payload=excluded.payload,
                     fetched_at=excluded.fetched_at""",
                (
                    product.product_id, product.platform, product.url, product.name,
                    product.brand, product.category, product.offer.price,
                    product.offer.currency, payload, product.fetched_at,
                ),
            )
            last = conn.execute(
                "SELECT price, currency FROM offers WHERE product_id=? ORDER BY id DESC LIMIT 1",
                (product.product_id,),
            ).fetchone()
            changed = (
                last is None
                or last["price"] != product.offer.price
                or last["currency"] != product.offer.currency
            )
            if changed and product.offer.price is not None:
                conn.execute(
                    """INSERT INTO offers (product_id, price, currency, original_price,
                                           discount_percent, region, adapter, fetched_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        product.product_id, product.offer.price, product.offer.currency,
                        product.offer.original_price, product.offer.discount_percent,
                        product.offer.region,
                        (product.sources.get(product.offer.source_key).adapter
                         if product.offer.source_key in product.sources else ""),
                        product.fetched_at,
                    ),
                )

    def load_product(self, product_id: str) -> Product | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT payload FROM products WHERE product_id=?", (product_id,)
            ).fetchone()
        if not row:
            return None
        return Product.from_dict(json.loads(row["payload"]))

    def load_recent(self, limit: int = 50) -> list[Product]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM products ORDER BY fetched_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [Product.from_dict(json.loads(r["payload"])) for r in rows]

    def price_history(self, product_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT price, currency, original_price, discount_percent, fetched_at
                   FROM offers WHERE product_id=? ORDER BY id DESC LIMIT ?""",
                (product_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def cached_age_seconds(self, product_id: str) -> float | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT fetched_at FROM products WHERE product_id=?", (product_id,)
            ).fetchone()
        if not row or not row["fetched_at"]:
            return None
        try:
            then = datetime.strptime(row["fetched_at"], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
        return (datetime.now() - then).total_seconds()

    # ---------- 采集日志 ----------
    def log_fetch(self, url: str, adapter: str, ok: bool, kind: str = "",
                  message: str = "", http_status: int | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO fetch_log (url, adapter, ok, kind, message, http_status, created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (url, adapter, 1 if ok else 0, kind, (message or "")[:500], http_status, now_text()),
            )

    def recent_logs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM fetch_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ---------- 维护 ----------
    def stats(self) -> dict[str, int]:
        with self.connect() as conn:
            return {
                "商品数": conn.execute("SELECT COUNT(*) FROM products").fetchone()[0],
                "价格记录数": conn.execute("SELECT COUNT(*) FROM offers").fetchone()[0],
                "采集日志数": conn.execute("SELECT COUNT(*) FROM fetch_log").fetchone()[0],
            }

    def clear_products(self) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM products")
            conn.execute("DELETE FROM offers")

    def clear_all(self) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM products")
            conn.execute("DELETE FROM offers")
            conn.execute("DELETE FROM fetch_log")


#: 进程级默认数据库
db = Database()
