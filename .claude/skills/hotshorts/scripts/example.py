import os
import isodate
from googleapiclient.discovery import build
from dotenv import load_dotenv
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import NoTranscriptFound, TranscriptsDisabled
from example2 import save_to_sqlite

load_dotenv()
API_KEY = os.getenv("YOUTUBE_API_KEY")

def get_video_subtitle(video_id):
    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        try:
            transcript = transcript_list.find_transcript(["en", "zh-TW", "zh-CN", "ja", "es"])
        except NoTranscriptFound:
            transcript = transcript_list.find_best_transcript(["en"])
        return " ".join([item["text"] for item in transcript.fetch()])
    except (TranscriptsDisabled, Exception):
        return "[無法取得字幕或此影片未提供字幕]"

def get_pure_ai_shorts(api_key, target_count=15):
    youtube = build("youtube", "v3", developerKey=api_key)
    all_video_ids = []
    next_page_token = None
    
    print("🚀 [核心 AI] 開始全球搜尋 #ai 短影音...")
    while True:
        search_request = youtube.search().list(
            q="#ai #Shorts",
            part="id,snippet",
            type="video",
            order="viewCount",  
            publishedAfter="2026-01-01T00:00:00Z",
            publishedBefore="2026-06-01T00:00:00Z",
            maxResults=50,
            pageToken=next_page_token
        )
        search_response = search_request.execute()
        for item in search_response.get("items", []):
            if "videoId" in item["id"]:
                all_video_ids.append(item["id"]["videoId"])
        next_page_token = search_response.get("nextPageToken")
        if not next_page_token or len(all_video_ids) >= 150:
            break

    results = []
    for i in range(0, len(all_video_ids), 50):
        if len(results) >= target_count:
            break
        batch_ids = all_video_ids[i:i+50]
        details_response = youtube.videos().list(
            part="snippet,contentDetails,statistics", id=",".join(batch_ids)
        ).execute()
        
        for item in details_response.get("items", []):
            if len(results) >= target_count:
                break
            statistics = item.get("statistics", {})
            view_count = int(statistics.get("viewCount", "0"))
            
            if view_count <= 1000000:
                continue
                
            content_details = item.get("contentDetails", {})
            try:
                duration = int(isodate.parse_duration(content_details.get("duration", "")).total_seconds())
            except Exception: duration = 0
                
            if 0 < duration <= 60:
                snippet = item.get("snippet", {})
                video_id = item["id"]
                print(f"🤖 捕獲 [核心 AI]! Views: {view_count:,}")
                
                results.append({
                    "video_id": video_id,
                    "title": snippet.get("title"),
                    "description": snippet.get("description"),
                    "channel_title": snippet.get("channelTitle"),
                    "published_at": snippet.get("publishedAt"),
                    "duration": f"{duration} 秒",
                    "tags": snippet.get("tags", []),
                    "view_count": view_count,
                    "like_count": statistics.get("likeCount", "0"),
                    "comment_count": statistics.get("commentCount", "0"),
                    "thumbnail_url": snippet.get("thumbnails", {}).get("high", {}).get("url", ""),
                    "subtitle_text": get_video_subtitle(video_id)
                })
    return results

if __name__ == "__main__":
    ai_list = get_pure_ai_shorts(API_KEY, target_count=15)
    print(f"\n================ 核心 AI 組結果 (共 {len(ai_list)} 支) ================")
    for idx, v in enumerate(ai_list, 1):
        print(f"[{idx}] {v['title']} | 觀看: {v['view_count']:,}")
        
    # 🚀 在這裡直接呼叫儲存函式！
    save_to_sqlite(ai_list)