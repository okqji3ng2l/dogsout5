"""
DogsOut 管理面板 — 前端伺服器

把 frontend/ 目錄（index.html，介面沿用 preview.html）掛在本機 port 3000，
啟動後自動開啟瀏覽器。API 後端請另外執行根目錄的 server.py（port 8700）。

啟動：python3 app.py → http://localhost:3000
"""

import threading
import webbrowser
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

HOST = "127.0.0.1"
PORT = 3000
FRONTEND_DIR = Path(__file__).resolve().parent / "frontend"

app = FastAPI(title="DogsOut 管理面板 前端")
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")


def open_browser():
    webbrowser.open(f"http://{HOST}:{PORT}")


if __name__ == "__main__":
    threading.Timer(1.0, open_browser).start()
    print(f"前端： http://{HOST}:{PORT}")
    print("API 後端請另外執行： python3 ../server.py（port 8700）")
    uvicorn.run(app, host=HOST, port=PORT)
