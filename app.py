"""
DogsOut 整合入口 — 前端伺服器

沿用 manage/app.py 的設計：把 manage/frontend 目錄（index.html）掛在
本機 port 5000，啟動後自動開啟瀏覽器。左側工具列的影片功能欄位
（「影片生成」）已整合 video/app.py 的前端頁面。

本程式只服務前端；所有 API（上傳／排程與 video 的縮圖、腳本、
影片生成）由根目錄 server.py 提供（port 8700，整合 video 與
manage 的所有 router），請另外執行。

啟動：python3 app.py → http://localhost:5000
"""

import threading
import webbrowser
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

HOST = "127.0.0.1"
PORT = 5000
FRONTEND_DIR = Path(__file__).resolve().parent / "manage" / "frontend"

app = FastAPI(title="DogsOut 整合面板 前端")
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")


def open_browser():
    webbrowser.open(f"http://{HOST}:{PORT}")


if __name__ == "__main__":
    threading.Timer(1.0, open_browser).start()
    print(f"整合面板： http://{HOST}:{PORT}")
    print("API 後端請另外執行： python3 server.py（port 8700）")
    uvicorn.run(app, host=HOST, port=PORT)
