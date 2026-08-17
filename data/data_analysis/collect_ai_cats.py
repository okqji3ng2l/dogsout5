"""
collect_ai_cats.py — 蒐集 #ai + #cats / #cat 熱門 Shorts

蒐集兩個時間窗：
  recently #ai #cat  — 近五個月（2026-01 ~ 2026-06，按月分段，每月 ≤50 筆）
  viral    #ai #cat  — 近兩週（動態計算，≤50 筆）

#ai #cats 與 #ai #cat 皆以 stored_label 統一存為同一標籤。

執行：python collect_ai_cats.py
配額估算：
  recently：2 查詢 × 5 個月 × 100u + details ≈ 1,500 units
  viral   ：2 查詢 × 1 段   × 100u + details ≈  300 units
"""

import os
from pathlib import Path

from googleapiclient.discovery import build
from dotenv import load_dotenv

from database import init_db, count
from collect_utils import run_collection, DATE_RANGES, VIRAL_RANGES

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

QUERIES = [
    "#ai #cats",
    "#ai #cat",
]


def main():
    api_key = os.getenv("YOUTUBE_API_KEY")
    if not api_key:
        print("❌ 找不到 YOUTUBE_API_KEY，請確認 .env")
        return

    youtube = build("youtube", "v3", developerKey=api_key)
    init_db()

    print("🔥 蒐集 viral #ai #cat（近兩週）...")
    total_v = run_collection(
        youtube, QUERIES, label="ai_cats",
        stored_label="viral #ai #cat",
        date_ranges=VIRAL_RANGES,
    )

    print("\n📅 蒐集 recently #ai #cat（近五個月）...")
    total_r = run_collection(
        youtube, QUERIES, label="ai_cats",
        stored_label="recently #ai #cat",
        date_ranges=DATE_RANGES,
    )

    total = total_v + total_r
    print(f"\n🎉 #ai #cat 蒐集完成，本次新增 {total} 筆，資料庫共 {count():,} 筆")


if __name__ == "__main__":
    main()
