"""
DogsOut 管理面板 — 上傳／排程 API（FastAPI APIRouter）

把 example/code 內的兩支腳本包成 HTTP API，供前端（app.py 服務的頁面）呼叫：

    GET  /api/health    健康檢查，前端據此顯示「後端已連線」
    POST /api/upload    multipart/form-data
                        → upload.py：initialize_upload() 上傳影片，
                          comment 非空時再 auto_comment() 自動留言
    POST /api/schedule  multipart/form-data（新增待上傳影片）
                        → 影片以 private + publishAt 排程上傳，
                          YouTube 於指定時間（台北時間）自動發布；
                          排程資料（url、title、description、upload_time、
                          comment…）存入 dogsout/database/schedule.db（SQLite）；
                          再由 calander.py insert_exact_event() 在發布前
                          1 小時建立 Google 日曆提醒

scheduled_videos 資料表的 url 欄位存放完整短影音網址
（https://youtube.com/shorts/{video_id}），供人工直接點擊查看；
comment_worker 使用時會從 url 拆回原始 video_id 再呼叫 YouTube API
（commentThreads.insert 的 snippet.videoId 只接受純 ID）。
url／title／upload_time 皆可留空——video_router.py／thumbnail_router.py
在生成 script／thumbnail／video 的當下就會各自新增一列（只填已知欄位，
其餘留空），與本檔上傳完成後新增的完整列彼此獨立，一律用 INSERT
新增，不會互相覆蓋。

留言計時器：private 影片在發布前無法留言，所以 comment 不在上傳當下發送。
背景執行緒每 60 秒讀取 database/schedule.db，upload_time（YYYY-MM-DD HH:MM）
到達或已超過且尚未留言的影片，參考 comment.py 自動發送留言後標記完成；
無法發布（CommentPublishError，如影片不存在、尚未公開）則印出警告訊息
並停止重試——放棄名單只記在記憶體，後端重啟後會再各嘗試一次。

本檔只提供 router（不含 FastAPI app、不佔用 port），由根目錄
server.py 引入並掛載（http://localhost:8700）；schedule.db 的初始化
（init_db）與留言計時器（comment_worker）也由 server.py 於啟動時執行。
首次呼叫會跳出瀏覽器做 Google OAuth 授權，token 存在 example/code 目錄下。
"""

import datetime
import io
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

# 讓 example/code 內的模組可以直接 import
BASE_DIR = Path(__file__).resolve().parent
CODE_DIR = BASE_DIR / "example" / "code"
sys.path.insert(0, str(CODE_DIR))

import calander  # noqa: E402  (example/code/calander.py)
import comment as comment_api  # noqa: E402  (example/code/comment.py)
import upload  # noqa: E402    (example/code/upload.py)

router = APIRouter(prefix="/api")


# ═══ SQLite：待上傳影片排程資料 ═══════════════════════════════════
# .db 一律存放在 dogsout 根目錄的 database 資料夾
DB_DIR = BASE_DIR.parent / "database"
DB_PATH = DB_DIR / "schedule.db"


SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS scheduled_videos (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        url         TEXT DEFAULT '',   -- https://youtube.com/shorts/{video_id}，上傳前留空
        title       TEXT DEFAULT '',
        description TEXT DEFAULT '',
        upload_time TEXT DEFAULT '',   -- 發布時間 YYYY-MM-DD HH:MM（台北時間），未排程留空
        comment     TEXT DEFAULT '',
        commented   INTEGER DEFAULT 0,  -- 0=尚未留言 1=已留言
        script      TEXT DEFAULT '',   -- video_router.py 生成的最終腳本
        thumbnail   BLOB               -- thumbnail_router.py／video_router.py 生成的縮圖原始位元組
    )
"""


def init_db():
    """建表，並將舊版 schema（video_id NOT NULL，無 script／thumbnail 欄位）
    非破壞性遷移到新版：video_id 改名為 url，NOT NULL 放寬為可留空，
    既有資料原樣搬過去，script／thumbnail 預設空值。"""
    DB_DIR.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    exists = cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='scheduled_videos'"
    ).fetchone() is not None
    needs_migration = False
    if exists:
        cols = {row[1] for row in cur.execute("PRAGMA table_info(scheduled_videos)")}
        needs_migration = {"url", "script", "thumbnail"} - cols != set()
        if needs_migration:
            cur.execute("ALTER TABLE scheduled_videos RENAME TO scheduled_videos_old")
    cur.execute(SCHEMA_SQL)
    if needs_migration:
        old_cols = {row[1] for row in cur.execute("PRAGMA table_info(scheduled_videos_old)")}
        url_col = "video_id" if "video_id" in old_cols else "url"  # 舊版欄位名
        cur.execute(
            f"INSERT INTO scheduled_videos (id, url, title, description, upload_time, comment, commented) "
            f"SELECT id, {url_col}, title, description, upload_time, comment, commented "
            f"FROM scheduled_videos_old"
        )
        cur.execute("DROP TABLE scheduled_videos_old")
    con.commit()
    con.close()


class CommentPublishError(Exception):
    """留言無法發布（comment.py 回報錯誤，例如影片不存在、尚未公開）。"""


def post_comment(video_id: str, text: str):
    """參考 comment.py 發送留言；comment.py 以相對路徑讀取 token.pickle，
    切到 example/code 執行，沿用 upload.py 已授權的 token（含 force-ssl）。
    未成功留言即拋出 CommentPublishError（訊息帶 comment.py 的輸出）。"""
    prev_cwd = os.getcwd()
    buf = io.StringIO()
    try:
        os.chdir(CODE_DIR)
        with redirect_stdout(buf):
            comment_api.auto_comment(video_id, text)
    except Exception as e:
        raise CommentPublishError(str(e))
    finally:
        os.chdir(prev_cwd)
    output = buf.getvalue().strip()
    print(f"[comment] {video_id}: {output}")
    if "成功留言" not in output:
        raise CommentPublishError(output or "comment.py 未回報成功留言")


CHECK_INTERVAL_SECONDS = 60

# 留言發布失敗而放棄的排程 id：只記在記憶體（不寫資料庫），
# 後端重啟後名單清空，每筆會再嘗試一次
abandoned_comment_ids: set[int] = set()


def video_id_from_url(url: str) -> str:
    """從 url 欄位（https://youtube.com/shorts/{video_id}）取出純 video_id；
    YouTube 的 commentThreads.insert 只接受純 ID，不接受完整網址。"""
    return url.rstrip("/").rsplit("/", 1)[-1]


def comment_worker():
    """計時器：每 60 秒檢查一次，upload_time 到達或已超過且尚未留言的
    影片發送 comment；無法發布（CommentPublishError）則印出警告訊息
    並加入放棄名單，停止繼續嘗試。"""
    while True:
        try:
            now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            con = sqlite3.connect(DB_PATH)
            rows = con.execute(
                "SELECT id, url, comment FROM scheduled_videos "
                "WHERE commented = 0 AND comment != '' AND url != '' AND upload_time <= ?",
                (now,),
            ).fetchall()
            con.close()
            for row_id, url, text in rows:
                if row_id in abandoned_comment_ids:
                    continue
                video_id = video_id_from_url(url)
                try:
                    post_comment(video_id, text)
                except CommentPublishError as e:
                    abandoned_comment_ids.add(row_id)
                    print(f"【警告】影片 {video_id} 留言無法發布，停止重試：{e}")
                    continue
                con = sqlite3.connect(DB_PATH)
                con.execute(
                    "UPDATE scheduled_videos SET commented = 1 WHERE id = ?",
                    (row_id,),
                )
                con.commit()
                con.close()
        except Exception as e:
            print(f"comment 計時器錯誤：{e}")
        time.sleep(CHECK_INTERVAL_SECONDS)


@router.get("/health")
def health():
    return {"status": "ok"}


@router.post("/upload")
def upload_video(
    file: UploadFile = File(...),
    title: str = Form("Test Title"),
    description: str = Form(""),
    keywords: str = Form(""),
    privacyStatus: str = Form("private"),
    comment: str = Form(""),
):
    if privacyStatus not in upload.VALID_PRIVACY_STATUSES:
        raise HTTPException(400, f"privacyStatus 必須是 {upload.VALID_PRIVACY_STATUSES}")

    # UploadFile 是串流，先落地成暫存檔給 MediaFileUpload 用
    suffix = Path(file.filename or "video.mp4").suffix or ".mp4"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    options = SimpleNamespace(
        file=tmp_path,
        title=title,
        description=description,
        category="22",  # upload.py 的 initialize_upload 需要此欄位，固定用預設值
        keywords=keywords,
        privacyStatus=privacyStatus,
    )

    try:
        youtube = upload.get_authenticated_service()
        video_id = upload.initialize_upload(youtube, options)
        if not video_id:
            raise HTTPException(502, "上傳未取得 video_id")

        commented = False
        if comment:
            # auto_comment 內部已處理 HttpError，僅印出訊息
            upload.auto_comment(youtube, video_id, comment)
            commented = True

        return {"video_id": video_id, "commented": commented}
    except SystemExit as e:
        # upload.py 在重試耗盡或非預期回應時會呼叫 exit()
        raise HTTPException(502, f"上傳失敗：{e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"上傳失敗：{e}")
    finally:
        Path(tmp_path).unlink(missing_ok=True)


@router.post("/schedule")
def schedule_video(
    file: UploadFile = File(...),
    title: str = Form(...),
    description: str = Form(""),
    date_str: str = Form(...),  # 發布日期 YYYY-MM-DD（台北時間）
    time_str: str = Form("09:00"),  # 發布時間 HH:MM（台北時間）
    keywords: str = Form(""),  # 關鍵字（tags），逗號分隔，選填
    comment: str = Form(""),  # 上傳後自動留言（comment.py），留空則不留言
):
    # 1. 解析排程時間（必須是未來的時間）
    try:
        publish_local = datetime.datetime.strptime(
            f"{date_str} {time_str}", "%Y-%m-%d %H:%M"
        )
    except ValueError:
        raise HTTPException(400, "date_str/time_str 格式錯誤，需為 YYYY-MM-DD 與 HH:MM")
    if publish_local <= datetime.datetime.now():
        raise HTTPException(400, "排程發布時間必須是未來的時間")

    # 2. API 接收 UTC 時間，將台北時間（UTC+8）減 8 小時轉為 ISO 8601
    utc_time = publish_local - datetime.timedelta(hours=8)
    publish_at_iso = utc_time.strftime("%Y-%m-%dT%H:%M:%SZ")

    suffix = Path(file.filename or "video.mp4").suffix or ".mp4"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    # 3. 建立影片資訊與狀態：排程發布必須先設為 private + publishAt
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": keywords.split(",") if keywords else None,  # 同 upload.py 的處理
            "categoryId": "22",
        },
        "status": {
            "privacyStatus": "private",
            "publishAt": publish_at_iso,
            "selfDeclaredMadeForKids": False,
        },
    }

    try:
        youtube = upload.get_authenticated_service()
        insert_request = youtube.videos().insert(
            part=",".join(body.keys()),
            body=body,
            media_body=upload.MediaFileUpload(tmp_path, chunksize=-1, resumable=True),
        )
        video_id = upload.resumable_upload(insert_request)
        if not video_id:
            raise HTTPException(502, "上傳未取得 video_id")
    except SystemExit as e:
        raise HTTPException(502, f"上傳失敗：{e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"上傳失敗：{e}")
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    # 4. 排程資料存入 SQLite；private 影片發布前無法留言，
    #    comment 由背景計時器在 upload_time 到達後自動發送
    #    一律用 INSERT 新增一列，即使是同一部影片重複上傳也不會覆蓋
    #    先前 video_router.py／thumbnail_router.py 生成時新增、缺少
    #    上傳資訊的那幾列
    video_url = f"https://youtube.com/shorts/{video_id}"
    upload_time = publish_local.strftime("%Y-%m-%d %H:%M")
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "INSERT INTO scheduled_videos (url, title, description, upload_time, comment) "
        "VALUES (?, ?, ?, ?, ?)",
        (video_url, title, description, upload_time, comment),
    )
    con.commit()
    con.close()

    # 5. 於發布前 1 小時建立 Google 日曆提醒（Asia/Taipei）
    remind_local = publish_local - datetime.timedelta(hours=1)
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            calander.insert_exact_event(
                title=f"【上傳提醒】{title}",
                date_str=remind_local.strftime("%Y-%m-%d"),
                time_str=remind_local.strftime("%H:%M"),
                duration_hours=1,
                description=(
                    f"YouTube 影片《{title}》將於 {date_str} {time_str}"
                    f"（台北時間）自動發布。\nhttps://youtu.be/{video_id}"
                ),
            )
    except Exception:
        pass  # 影片已排程成功，提醒失敗不整筆退回，改在回應中標示
    reminder_output = buf.getvalue().strip()
    reminder_created = "【成功寫入】" in reminder_output

    return {
        "video_id": video_id,
        "publish_at": publish_at_iso,
        "comment_scheduled": bool(comment),  # 計時器將於 upload_time 自動留言
        "reminder_created": reminder_created,
        "reminder_detail": reminder_output,
    }
