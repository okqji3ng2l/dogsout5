import sqlite3
import json

def save_to_sqlite(videos_list, db_name="youtube_shorts.db"):
    """將收集到的影片資料列表以表格形式存入 SQLite 資料庫"""
    if not videos_list:
        print("沒有資料需要寫入資料庫。")
        return
        
    # 連線到 SQLite 資料庫（若檔案不存在會自動建立）
    conn = sqlite3.connect(db_name)
    cursor = conn.cursor()
    
    # 建立資料表（符合你指定的所有欄位）
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS shorts_data (
            video_id TEXT PRIMARY KEY,
            title TEXT,
            description TEXT,
            channel_title TEXT,
            published_at TEXT,
            duration TEXT,
            tags TEXT,
            view_count INTEGER,
            like_count TEXT,
            comment_count TEXT,
            thumbnail_url TEXT,
            subtitle_text TEXT
        )
    ''')
    
    inserted_count = 0
    
    # 開始寫入資料
    for v in videos_list:
        # 將 tags list 轉換為 JSON 字串存儲
        tags_json = json.dumps(v.get("tags", []))
        
        try:
            cursor.execute('''
                INSERT OR IGNORE INTO shorts_data (
                    video_id, title, description, channel_title,
                    published_at, duration, tags, view_count, like_count,
                    comment_count, thumbnail_url, subtitle_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                v.get("video_id"),
                v.get("title"),
                v.get("description"),
                v.get("channel_title"),
                v.get("published_at"),
                v.get("duration"),
                tags_json,
                v.get("view_count"),
                v.get("like_count"),
                v.get("comment_count"),
                v.get("thumbnail_url"),
                v.get("subtitle_text")
            ))
            # 🔧 【核心修正】：改用 cursor.rowcount 來判斷是否有新影片被成功寫入
            # 如果影片重複被 IGNORE，rowcount 會是 0 或 -1；成功寫入則會是 1
            if cursor.rowcount > 0:
                inserted_count += 1
        except Exception as e:
            print(f"寫入影片 {v.get('video_id')} 時發生錯誤: {e}")
            
    # 提交變更並關閉連線
    conn.commit()
    conn.close()
    
    print(f"📊 資料庫同步完成！成功新寫入 {inserted_count} 支影片到 {db_name}。")