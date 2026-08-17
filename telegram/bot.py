import base64
import datetime
import io
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel


app = FastAPI()

# dogsout 專案根目錄（telegram/ 的上一層）
ROOT_DIR = Path(__file__).resolve().parent.parent
# 與 manage/upload_router.py、video/*_router.py 共用的排程資料庫
DB_PATH = ROOT_DIR / "database" / "schedule.db"
# /reload 觸發的每日更新批次檔
DAILY_BAT = ROOT_DIR / "data" / "data_analysis" / "update_daily.bat"


class Command(BaseModel):
    command: str


def latest(column: str, condition: str):
    """取 scheduled_videos 中符合條件、id 最大（最新）那一列的欄位值。

    script／thumbnail／url 由各自的 router 在生成當下各新增一列（只填
    已知欄位），所以三者分開查最新一筆。查無資料回傳 None。"""
    if not DB_PATH.exists():
        return None
    con = sqlite3.connect(DB_PATH)
    try:
        row = con.execute(
            f"SELECT {column} FROM scheduled_videos "
            f"WHERE {condition} ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        con.close()
    return row[0] if row else None


def script():
    """schedule.db 最新一筆 video_router.py 生成的腳本。"""
    return latest("script", "script != ''") or "資料庫裡還沒有腳本"

def video():
    """schedule.db 最新一筆影片網址（https://youtube.com/shorts/{video_id}）。"""
    return latest("url", "url != ''") or "資料庫裡還沒有影片網址"

def thumbnail():
    """schedule.db 最新一筆縮圖；BLOB 無法直接當純文字回傳，改成 data URL。"""
    image_bytes = latest("thumbnail", "thumbnail IS NOT NULL")
    if not image_bytes:
        return "資料庫裡還沒有縮圖"
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    return b64

def datanalysis():
    return "return youtube data summary and charts"

def videodata():
    return "return youtube data summary and charts"

def recent():
    """schedule.db 近三日的 upload_time／title／description。"""
    if not DB_PATH.exists():
        return "查無排程資料"
    threshold = (
        datetime.datetime.now() - datetime.timedelta(days=3)
    ).strftime("%Y-%m-%d %H:%M")
    con = sqlite3.connect(DB_PATH)
    try:
        rows = con.execute(
            "SELECT upload_time, title, description FROM scheduled_videos "
            "WHERE upload_time != '' AND upload_time >= ? ORDER BY upload_time",
            (threshold,),
        ).fetchall()
    finally:
        con.close()
    data = [
        {"upload_time": upload_time, "title": title, "description": description}
        for upload_time, title, description in rows
    ]
    return json.dumps(data, ensure_ascii=False)

def reload():
    """/reload：直接執行 data_analysis 的每日更新批次檔（非阻塞）。"""
    if not DAILY_BAT.exists():
        return f"找不到更新批次檔：{DAILY_BAT}"
    try:
        if sys.platform == "win32":
            subprocess.Popen(
                [str(DAILY_BAT)],
                shell=True,
                cwd=str(DAILY_BAT.parent),
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
        else:
            # bot.py 實際跑在 WSL：直接 shell=True 會用 /bin/sh 執行 .bat 而失敗，
            # 改透過 WSL interop 呼叫 cmd.exe，用 start 開一個新的 Windows 視窗執行。
            win_bat = subprocess.run(
                ["wslpath", "-w", str(DAILY_BAT)],
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            subprocess.Popen(
                ["/mnt/c/Windows/System32/cmd.exe", "/c", "start", "", win_bat],
                cwd=str(DAILY_BAT.parent),
            )
        return "已開始每日更新"
    except Exception as e:
        return f"更新啟動失敗：{e}"

def hello():
    return "test"


command_dict = {
    "script": script,
    "video": video,
    "thumbnail": thumbnail,
    "datanalysis": datanalysis,
    "videodata": videodata,
    "recent": recent,
    "reload": reload,
    "hello": hello,
}


@app.post("/commands")
def commands(data: Command):
    func = command_dict.get(data.command)
    if func is None:
        return PlainTextResponse("未知指令")
    try:
        result = func()
        # thumbnail 不回傳文字,改回傳縮圖的 BytesIO（沒縮圖時仍回文字）
        if data.command == "thumbnail" and result != "資料庫裡還沒有縮圖":
            buf = io.BytesIO(base64.b64decode(result))
            return StreamingResponse(buf, media_type="image/png")
        return PlainTextResponse(result)
    except Exception as e:
        return PlainTextResponse(f"指令執行失敗：{e}")

if __name__ == "__main__":
    # 本地測試用,直接跑這支檔案才會執行,uvicorn 啟動不會觸發
    result = commands(Command(command="hello"))
    print(result)