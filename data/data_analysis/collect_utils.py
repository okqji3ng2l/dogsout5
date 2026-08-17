"""
collect_utils.py — 共用的 YouTube API 工具函式
由 collect_ai.py / collect_ai_girls.py / collect_ai_cats.py 引入
"""
import re
import time
import json
from datetime import datetime, timedelta

from database import upsert_video

# ── 時間範圍（每次執行時動態計算）────────────────────────────

_now = datetime.utcnow()


def _monthly_ranges(reference: datetime, n_months: int = 6):
    """
    從 (reference 的月份 - 5) 的月初開始，按月切割到 reference，
    共產生 n_months 段（預設 6 段：前五個完整月 + 當月至今）。

    例：reference = 2026-06-23 → 2026-01 / 02 / 03 / 04 / 05 / 06(至今)
        reference = 2026-07-01 → 2026-02 / 03 / 04 / 05 / 06 / 07(至今)
    """
    ranges = []
    for i in range(n_months - 1, -1, -1):   # 5 → 0
        m = reference.month - i
        y = reference.year
        while m <= 0:
            m += 12
            y -= 1
        start = datetime(y, m, 1)
        nm, ny = m + 1, y
        if nm > 12:
            nm, ny = 1, ny + 1
        end = min(datetime(ny, nm, 1), reference)
        ranges.append((
            start.strftime("%Y-%m-%dT00:00:00Z"),
            end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            start.strftime("%Y-%m"),
        ))
    return ranges


# recently：前五個月月初 → 今日（按月切割，每月最多 50 筆）
RECENTLY_RANGES = _monthly_ranges(_now, 6)
DATE_RANGES     = RECENTLY_RANGES   # backward compat

# viral：近 14 天
VIRAL_RANGES = [
    (
        (_now - timedelta(days=14)).strftime("%Y-%m-%dT00:00:00Z"),
        _now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "viral",
    )
]

MAX_PAGES   = 1   # 每個查詢每段最多 1 頁 × 50 = 50 筆
MAX_SECONDS = 60  # Shorts ≤ 60 秒


def parse_duration_sec(iso: str) -> int:
    """PT1M30S → 90"""
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    h, mn, s = (int(x or 0) for x in m.groups())
    return h * 3600 + mn * 60 + s


def safe_int(val) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return 0


def search_ids(youtube, query: str, after: str, before: str) -> list:
    """搜尋一個查詢、一個時間段的影片 ID"""
    ids, token = [], None
    for page in range(MAX_PAGES):
        try:
            res = youtube.search().list(
                part="id",
                type="video",
                videoDuration="short",
                order="viewCount",
                q=query,
                publishedAfter=after,
                publishedBefore=before,
                maxResults=50,
                pageToken=token,
            ).execute()
            batch = [item["id"]["videoId"] for item in res.get("items", [])]
            ids.extend(batch)
            print(f"      第 {page + 1} 頁：{len(batch)} 筆")
            token = res.get("nextPageToken")
            if not token:
                break
            time.sleep(0.5)
        except Exception as e:
            print(f"      ⚠️ 搜尋失敗：{e}")
            break
    return ids


def fetch_details(youtube, video_ids: list) -> list:
    """批次取得影片詳細資料（每次最多 50 筆）"""
    results = []
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i + 50]
        try:
            res = youtube.videos().list(
                part="snippet,statistics,contentDetails",
                id=",".join(batch),
            ).execute()
            results.extend(res.get("items", []))
            time.sleep(0.3)
        except Exception as e:
            print(f"      ⚠️ 詳情取得失敗：{e}")
    return results


def run_collection(
    youtube,
    queries: list,
    label: str,
    stored_label: str = None,
    date_ranges: list = None,
) -> int:
    """
    對指定查詢列表執行完整的時間段迴圈蒐集。

    stored_label : 寫入 DB 的 search_query 值；不指定則使用實際查詢字串。
    date_ranges  : 使用哪組時間範圍，預設為 DATE_RANGES（近五個月）；
                   傳入 VIRAL_RANGES 則改蒐集近兩週。
    回傳本次新增筆數。
    """
    ranges = date_ranges if date_ranges is not None else DATE_RANGES
    total = 0

    for after, before, period in ranges:
        print(f"\n  📅 {period}")
        seen_ids = set()  # 同時間段跨查詢去重

        for query in queries:
            print(f"    🔍 {query}")
            ids = search_ids(youtube, query, after, before)
            ids = [i for i in ids if i not in seen_ids]
            seen_ids.update(ids)

            if not ids:
                print(f"      無新結果")
                continue

            print(f"      取得 {len(ids)} 筆 ID，抓取詳情...")
            items = fetch_details(youtube, ids)
            saved = 0

            for item in items:
                dur_sec = parse_duration_sec(
                    item["contentDetails"].get("duration", "")
                )
                if dur_sec > MAX_SECONDS:
                    continue

                sn   = item["snippet"]
                stat = item.get("statistics", {})

                views    = safe_int(stat.get("viewCount"))
                likes    = safe_int(stat.get("likeCount"))
                comments = safe_int(stat.get("commentCount"))

                like_rate       = round(likes    / views, 6) if views > 0 else 0
                comment_rate    = round(comments / views, 6) if views > 0 else 0
                engagement_rate = round((likes + comments) / views, 6) if views > 0 else 0

                # tags: API 有時回傳 None，統一轉為空列表再序列化
                raw_tags = sn.get("tags") or []

                upsert_video({
                    "id":              item["id"],                               # 1  video ID
                    "title":           sn.get("title", ""),                      # 2  標題
                    "description":     sn.get("description", "")[:500],          # 3  描述（截 500 字）
                    "tags":            json.dumps(raw_tags, ensure_ascii=False),  # 4  標籤（JSON）
                    "channel_id":      sn.get("channelId", ""),                  # 5  頻道 ID
                    "channel_title":   sn.get("channelTitle", ""),               # 6  頻道名稱
                    "published_at":    sn.get("publishedAt", ""),                # 7  發布時間（ISO 8601）
                    "views":           views,                                     # 8  觀看數
                    "likes":           likes,                                     # 9  點讚數
                    "comments":        comments,                                  # 10 留言數
                    "like_rate":       like_rate,
                    "comment_rate":    comment_rate,
                    "engagement_rate": engagement_rate,
                    "duration":        item["contentDetails"].get("duration", ""), # 11 ISO 時長（PT30S 等）
                    "duration_sec":    dur_sec,                                    # 12 秒數（由 duration 解析）
                    "category_id":     sn.get("categoryId", ""),
                    "search_query":    stored_label if stored_label else query,
                    "collected_at":    datetime.utcnow().isoformat(),
                })
                saved += 1
                total += 1

            print(f"      ✅ 儲存 {saved} 筆")

    return total
