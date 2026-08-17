"""
database.py — SQLite 初始化、upsert、查詢（共用）
"""
import sqlite3
from pathlib import Path

# D:\dogsout\database\shorts.db（dogsout 根目錄的 database 資料夾）
DB_PATH = Path(__file__).parent.parent.parent / "database" / "shorts.db"


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS shorts (
                id              TEXT PRIMARY KEY,
                title           TEXT,
                description     TEXT,
                tags            TEXT,
                channel_id      TEXT,
                channel_title   TEXT,
                published_at    TEXT,
                views           INTEGER DEFAULT 0,
                likes           INTEGER DEFAULT 0,
                comments        INTEGER DEFAULT 0,
                like_rate       REAL DEFAULT 0,
                comment_rate    REAL DEFAULT 0,
                engagement_rate REAL DEFAULT 0,
                duration        TEXT,
                duration_sec    INTEGER DEFAULT 0,
                category_id     TEXT,
                search_query    TEXT,
                collected_at    TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_views      ON shorts (views DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_published  ON shorts (published_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_channel    ON shorts (channel_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_engagement ON shorts (engagement_rate DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_query      ON shorts (search_query)")
        conn.commit()
    print(f"✅ 資料庫初始化：{DB_PATH}")


def upsert_video(data: dict):
    """search_query 保留第一次蒐集時的值（ON CONFLICT 不覆蓋）"""
    sql = """
        INSERT INTO shorts (
            id, title, description, tags, channel_id, channel_title,
            published_at, views, likes, comments,
            like_rate, comment_rate, engagement_rate,
            duration, duration_sec, category_id,
            search_query, collected_at
        ) VALUES (
            :id, :title, :description, :tags, :channel_id, :channel_title,
            :published_at, :views, :likes, :comments,
            :like_rate, :comment_rate, :engagement_rate,
            :duration, :duration_sec, :category_id,
            :search_query, :collected_at
        )
        ON CONFLICT(id) DO UPDATE SET
            views           = excluded.views,
            likes           = excluded.likes,
            comments        = excluded.comments,
            like_rate       = excluded.like_rate,
            comment_rate    = excluded.comment_rate,
            engagement_rate = excluded.engagement_rate,
            collected_at    = excluded.collected_at
    """
    with get_conn() as conn:
        conn.execute(sql, data)
        conn.commit()


def count() -> int:
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM shorts").fetchone()[0]
