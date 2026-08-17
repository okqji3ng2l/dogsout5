"""
DogsOut 整合 API 後端（FastAPI）

引入 video、manage、data 的所有 router，掛在同一個服務（port 8700）：

    manage/upload_router.py（router，prefix /api）
        GET  /api/health    健康檢查
        POST /api/upload    upload.py 立即上傳 YouTube＋自動留言
        POST /api/schedule  排程上傳（private + publishAt）＋排程資料存
                            database/schedule.db＋Google 日曆提醒
    video/thumbnail_router.py（thumbnail_router）
        GET  /api/keywords  關鍵字（database/shorts.db 的 cluster）
        POST /api/generate  Gemini 生成縮圖
    video/video_router.py（video_router）
        POST /api/script    qwen3 串流生成腳本（NDJSON）
        POST /api/video     Gemini（Veo）生成短影音
    data/analysis_router.py（analysis_router）
        GET  /api/analysis           數據分析頁面（即時從 shorts.db 渲染）
        GET  /api/analysis/stats     兩區摘要統計
        GET  /api/analysis/recently  Recently 區塊圖表資料
        GET  /api/analysis/viral     Viral 區塊圖表資料

啟動時沿用 upload_router.py 的機制：建立 schedule.db、
背景留言計時器（每 60 秒檢查 upload_time 到達的影片自動留言）。

啟動：python3 server.py（監聽 http://localhost:8700）
"""

import sys
import threading
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
# 讓 manage、video、data 內的模組維持原本的相互引用方式（以檔名直接 import）
sys.path.insert(0, str(BASE_DIR / "manage"))
sys.path.insert(0, str(BASE_DIR / "video"))
sys.path.insert(0, str(BASE_DIR / "data"))

import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from analysis_router import analysis_router  # noqa: E402
from thumbnail_router import thumbnail_router  # noqa: E402
from upload_router import comment_worker, init_db  # noqa: E402
from upload_router import router as upload_router  # noqa: E402
from video_router import video_router  # noqa: E402

HOST = "127.0.0.1"
PORT = 8700

app = FastAPI(title="DogsOut 整合 API")

# 前端由 app.py（port 5000）或 manage/app.py（port 3000）另行服務，需開 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(upload_router)
app.include_router(thumbnail_router)
app.include_router(video_router)
app.include_router(analysis_router)


@app.on_event("startup")
def on_startup():
    # 沿用 upload_router.py：建立 schedule.db 與留言計時器
    init_db()
    threading.Thread(target=comment_worker, daemon=True).start()


if __name__ == "__main__":
    print(f"整合 API 後端： http://{HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)
