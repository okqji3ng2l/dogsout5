"""
collect_ai.py — 蒐集 #ai 熱門 Shorts

蒐集兩個時間窗：
  recently #ai  — 近五個月（2026-01 ~ 2026-06，按月分段，每月 ≤50 筆）
  viral    #ai  — 近兩週（動態計算，≤50 筆）

執行：python collect_ai.py
配額估算：
  recently：5 個月 × 100u(search) + 1u(details) ≈ 750 units
  viral   ：1 段   × 100u(search) + 1u(details) ≈ 150 units
"""

import os
from pathlib import Path

from googleapiclient.discovery import build
from dotenv import load_dotenv

from database import init_db, count
from collect_utils import run_collection, DATE_RANGES, VIRAL_RANGES

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

QUERIES = ["#ai"]


def main():
    api_key = os.getenv("YOUTUBE_API_KEY")
    if not api_key:
        print("❌ 找不到 YOUTUBE_API_KEY，請確認 .env")
        return

    youtube = build("youtube", "v3", developerKey=api_key)
    init_db()

    print("🔥 蒐集 viral #ai（近兩週）...")
    total_v = run_collection(
        youtube, QUERIES, label="ai",
        stored_label="viral #ai",
        date_ranges=VIRAL_RANGES,
    )

    print("\n📅 蒐集 recently #ai（近五個月）...")
    total_r = run_collection(
        youtube, QUERIES, label="ai",
        stored_label="recently #ai",
        date_ranges=DATE_RANGES,
    )

    total = total_v + total_r
    print(f"\n🎉 #ai 蒐集完成，本次新增 {total} 筆，資料庫共 {count():,} 筆")


if __name__ == "__main__":
    main()
