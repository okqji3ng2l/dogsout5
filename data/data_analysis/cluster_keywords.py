# -*- coding: utf-8 -*-
"""關鍵字分群程式

1. 讀取 shorts.db（SQLite）的 keywords 欄位（忽略空欄位），
   依逗號切出每個關鍵字，先建成 {id: [關鍵字...]} 字典方便對應；
   關鍵字格式為「中文 (English)」，利用括號把中文與英文拆成兩種字典。
2. 用 ollama 的 nomic-embed-text 做 embedding，
   將所有關鍵字攤平成一個陣列後以 KMeans 分成 15 個 cluster
   （中文與英文分開分類）。
3. 用 gemma3:12b 幫每個 cluster 命名（中文類別用中文、英文類別用英文）。
4. 每部影片以多數決選出所屬 cluster，把 cluster 名稱
   （格式「中文群名 (English name)」）寫回 shorts.db 的 cluster 欄位。

使用方式：
    python cluster_keywords.py --db shorts.db
結果會印出並存成視覺化圖表 keyword_clusters.png
（PCA 降成 2D 的散佈圖，質心標上類別名稱）。
"""

import argparse
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from langchain_ollama import ChatOllama, OllamaEmbeddings
from matplotlib import font_manager
from matplotlib.patheffects import withStroke
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA

from langsmith_setup import enable_tracing

EMBED_MODEL = "nomic-embed-text"
NAMING_MODEL = "gemma3:12b"
N_CLUSTERS = 8

_embedder = OllamaEmbeddings(model=EMBED_MODEL)
_namer = ChatOllama(model=NAMING_MODEL)

# 固定順序的分類色盤（已驗證 CVD 安全）；顏色循環使用，搭配標記形狀做區分
SERIES_COLORS = [
    "#2a78d6", "#1baf7a", "#eda100", "#008300",
    "#4a3aa7", "#e34948", "#e87ba4", "#eb6834",
]
# 標記形狀輪替：圓形、三角形、方形、菱形、倒三角形
# 8 色 × 5 形狀互質，相鄰群的顏色與形狀都不同，最多 40 群不重複
SERIES_MARKERS = ["o", "^", "s", "D", "v"]
INK = {"primary": "#0b0b0b", "muted": "#898781", "axis": "#c3c2b7",
       "surface": "#fcfcfb"}


def cluster_style(i: int) -> tuple[str, str]:
    """回傳第 i 群的 (顏色, 標記形狀)，兩者各自循環組成不重複的組合。"""
    return (
        SERIES_COLORS[i % len(SERIES_COLORS)],
        SERIES_MARKERS[i % len(SERIES_MARKERS)],
    )


def setup_cjk_font() -> None:
    """註冊中文字型（微軟正黑體／Noto Sans TC），讓圖表能顯示中文。"""
    candidates = [
        "/mnt/c/Windows/Fonts/msjh.ttc",
        "/mnt/c/Windows/Fonts/NotoSansTC-VF.ttf",
        "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
    ]
    for path in candidates:
        if Path(path).is_file():
            font_manager.fontManager.addfont(path)
            name = font_manager.FontProperties(fname=path).get_name()
            plt.rcParams["font.sans-serif"] = [name, "sans-serif"]
            plt.rcParams["axes.unicode_minus"] = False
            return
    print("警告：找不到中文字型，圖表中文可能顯示為方框")

CJK_RE = re.compile(r"[一-鿿]")
# 「中文部分 (English part)」：支援全形／半形括號
PAREN_RE = re.compile(r"^(.*?)[（(]([^（）()]+)[）)]\s*$")


def find_db(db_arg: str) -> Path:
    """依序尋找資料庫檔案：指定路徑 → dogsout/database/ → 本目錄 → 專案上層目錄。"""
    here = Path(__file__).resolve().parent
    candidates = [Path(db_arg), here.parent.parent / "database" / db_arg,
                  here / db_arg, here.parent / db_arg]
    for c in candidates:
        if c.is_file():
            return c
    sys.exit(f"錯誤：找不到資料庫 {db_arg}，請用 --db 指定完整路徑")


def find_keywords_table(cur: sqlite3.Cursor, db_path: Path) -> tuple[str, str]:
    """自動找出含 keywords 欄位的資料表，回傳 (資料表名, id 欄位名)。"""
    tables = [r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )]
    for t in tables:
        cols = [r[1] for r in cur.execute(f"PRAGMA table_info('{t}')")]
        if "keywords" in cols:
            return t, ("id" if "id" in cols else "rowid")
    sys.exit(f"錯誤：{db_path} 中沒有任何資料表含 keywords 欄位")


def load_keyword_dict(db_path: Path) -> dict[int, list[str]]:
    """讀取 keywords 欄位（忽略空欄位），回傳 {id: [關鍵字, ...]}。"""
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    table, id_col = find_keywords_table(cur, db_path)

    rows = cur.execute(
        f"SELECT {id_col}, keywords FROM '{table}' "
        "WHERE keywords IS NOT NULL AND TRIM(keywords) != ''"
    ).fetchall()
    con.close()

    keyword_dict = {
        row_id: split_keywords(text) for row_id, text in rows
    }
    print(f"讀取資料表 '{table}'：共 {len(keyword_dict)} 筆非空 keywords")
    return keyword_dict


def split_keywords(text: str) -> list[str]:
    """依逗號（全形／半形）切割關鍵字，但忽略括號內的逗號。"""
    parts, buf, depth = [], "", 0
    for ch in text:
        if ch in "（(":
            depth += 1
        elif ch in "）)":
            depth = max(0, depth - 1)
        if ch in ",，" and depth == 0:
            parts.append(buf)
            buf = ""
        else:
            buf += ch
    parts.append(buf)
    return [p.strip() for p in parts if p.strip()]


def split_zh_en(keyword: str) -> tuple[str | None, str | None]:
    """用括號把「中文 (English)」拆成 (中文, 英文)；沒括號時依字元判斷語言。"""
    m = PAREN_RE.match(keyword)
    if m:
        zh, en = m.group(1).strip(), m.group(2).strip()
        return (zh or None), (en or None)
    if CJK_RE.search(keyword):
        return keyword, None
    return None, keyword


def build_lang_dicts(
    keyword_dict: dict[int, list[str]],
) -> tuple[dict[int, list[str]], dict[int, list[str]]]:
    """分成中文與英文兩種字典 {id: [關鍵字...]}。"""
    zh_dict: dict[int, list[str]] = {}
    en_dict: dict[int, list[str]] = {}
    for row_id, keywords in keyword_dict.items():
        for kw in keywords:
            zh, en = split_zh_en(kw)
            if zh:
                zh_dict.setdefault(row_id, []).append(zh)
            if en:
                en_dict.setdefault(row_id, []).append(en)
    return zh_dict, en_dict


def embed(keywords: list[str]) -> np.ndarray:
    """用 nomic-embed-text 把關鍵字轉成向量。"""
    return np.array(_embedder.embed_documents(keywords))


def cluster(
    keywords: list[str], n_clusters: int
) -> tuple[dict[int, list[str]], np.ndarray, np.ndarray]:
    """KMeans 分群，回傳 ({cluster編號: [關鍵字...]}, 向量, 每點的群標籤)。"""
    vectors = embed(keywords)
    n = min(n_clusters, len(keywords))
    km = KMeans(n_clusters=n, n_init=15, random_state=42)
    labels = km.fit_predict(vectors)
    groups: dict[int, list[str]] = {i: [] for i in range(n)}
    for kw, label in zip(keywords, labels):
        groups[int(label)].append(kw)
    return groups, vectors, labels


def name_cluster(keywords: list[str], lang: str) -> str:
    """用 gemma3:12b 幫一個 cluster 命名。"""
    joined = "、".join(keywords) if lang == "zh" else ", ".join(keywords)
    if lang == "zh":
        prompt = (
            f"以下是同一類別的一組關鍵字：{joined}\n"
            "請給這個類別一個簡短易懂的中文名稱（12個字以內），不一定要符合邏輯"
            "只回覆名稱本身，不要任何其他文字或標點。"
            "類別名稱必須有意義或者有主題性\n"
            "Ex:\n"
            "AI水果劇情短片，AI爆笑世足"
            "類別名稱禁止過於空泛\n"
            "Ex:\n"
            "搞笑短片合集，網路短影音"
        )
    else:
        prompt = (
            f"Here are keywords belonging to one category: {joined}\n"
            "Give this category a short English name (max 8 words), not necessery be logical(keywords don't need to consider grammar)"
            "Reply with the name only, no other text or punctuation."
            "keywords must be meaningful or characteristic\n"
            "Ex:\n"
            "AI fruit shorts, AI NBA player"
            "keyword's must not be meaningles\n"
            "Ex:\n"
            "AI-Generated Content, AI-Generated Short Content"
        )
    resp = _namer.invoke(prompt)
    return (resp.content or "").strip().strip("「」\"'.")


def cluster_and_name(keywords: list[str], lang: str) -> dict:
    """對一種語言的關鍵字做分群＋命名，回傳繪圖所需的完整結果。"""
    unique = sorted(set(keywords))
    label = "中文" if lang == "zh" else "英文"
    print(f"\n{label}關鍵字共 {len(unique)} 個（去重後），開始 embedding 與分群...")
    groups, vectors, labels = cluster(unique, N_CLUSTERS)

    names: dict[int, str] = {}
    for i, members in groups.items():
        names[i] = name_cluster(members, lang)
        print(f"  Cluster {i}: {names[i]}（{len(members)} 個關鍵字）")
    return {"vectors": vectors, "labels": labels, "groups": groups,
            "names": names}


def keyword_name_map(result: dict) -> dict[str, str]:
    """從分群結果建立 {關鍵字: cluster 名稱} 的對照表。"""
    return {
        kw: result["names"][i]
        for i, members in result["groups"].items()
        for kw in members
    }


def majority_name(keywords: list[str], name_map: dict[str, str]) -> str | None:
    """取一部影片的關鍵字中出現最多次的 cluster 名稱（多數決）。"""
    counts = Counter(
        name_map[kw] for kw in keywords if kw in name_map
    )
    return counts.most_common(1)[0][0] if counts else None


def save_clusters(
    db_path: Path,
    zh_dict: dict[int, list[str]],
    en_dict: dict[int, list[str]],
    results: dict[str, dict],
) -> None:
    """把每部影片的 cluster 名稱寫回 shorts.db 的 cluster 欄位。

    每部影片的多個關鍵字以多數決選出所屬 cluster，
    格式與 keywords 欄位一致：「中文群名 (English name)」。
    每次執行都重新覆蓋既有的 cluster 值。
    """
    zh_map = keyword_name_map(results["chinese"]) if "chinese" in results else {}
    en_map = keyword_name_map(results["english"]) if "english" in results else {}

    updates: list[tuple[str, int]] = []
    for row_id in set(zh_dict) | set(en_dict):
        zh = majority_name(zh_dict.get(row_id, []), zh_map)
        en = majority_name(en_dict.get(row_id, []), en_map)
        if zh and en:
            value = f"{zh} ({en})"
        else:
            value = zh or en
        if value:
            updates.append((value, row_id))

    con = sqlite3.connect(db_path)
    cur = con.cursor()
    table, id_col = find_keywords_table(cur, db_path)

    cols = [r[1] for r in cur.execute(f"PRAGMA table_info('{table}')")]
    if "cluster" not in cols:
        cur.execute(f"ALTER TABLE '{table}' ADD COLUMN cluster TEXT")
        print(f"已在資料表 '{table}' 新增 cluster 欄位")

    cur.executemany(
        f"UPDATE '{table}' SET cluster = ? WHERE {id_col} = ?", updates
    )
    con.commit()
    con.close()
    print(f"cluster 名稱已寫入 {len(updates)} 筆資料")


def plot_clusters(ax, result: dict, title: str) -> None:
    """把一種語言的分群結果畫成 PCA 2D 散佈圖，質心標上類別名稱。"""
    coords = PCA(n_components=2, random_state=42).fit_transform(
        result["vectors"]
    )
    labels = result["labels"]

    ax.set_facecolor(INK["surface"])
    for i in sorted(result["groups"]):
        color, marker = cluster_style(i)
        pts = coords[labels == i]
        ax.scatter(
            pts[:, 0], pts[:, 1], s=42, c=color, marker=marker,
            edgecolors=INK["surface"], linewidths=0.8, alpha=0.9,
            label=f"{result['names'][i]} ({len(pts)})",
        )
        # 質心直接標上類別名稱（白色描邊避免與資料點打架）
        cx, cy = pts[:, 0].mean(), pts[:, 1].mean()
        ax.annotate(
            result["names"][i], (cx, cy), ha="center", va="center",
            fontsize=10.5, color=INK["primary"],
            path_effects=[withStroke(linewidth=3, foreground=INK["surface"])],
        )

    ax.set_title(title, fontsize=13, color=INK["primary"], pad=12)
    ax.set_xlabel("PCA 1", fontsize=9, color=INK["muted"])
    ax.set_ylabel("PCA 2", fontsize=9, color=INK["muted"])
    ax.tick_params(colors=INK["muted"], labelsize=8)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(INK["axis"])
    ax.legend(
        loc="center left", bbox_to_anchor=(1.01, 0.5), fontsize=8,
        frameon=False, labelcolor=INK["primary"],
    )


def save_chart(results: dict[str, dict], output: Path) -> None:
    """把中文／英文的分群結果各畫一張子圖，存成一張 PNG。"""
    setup_cjk_font()
    n = len(results)
    fig, axes = plt.subplots(
        n, 1, figsize=(11, 6.5 * n), constrained_layout=True
    )
    axes = [axes] if n == 1 else list(np.ravel(axes))
    fig.set_facecolor(INK["surface"])

    titles = {"chinese": "中文關鍵字分群", "english": "English Keyword Clusters"}
    for ax, (lang_key, result) in zip(axes, results.items()):
        plot_clusters(ax, result, titles.get(lang_key, lang_key))

    fig.savefig(output, dpi=150, facecolor=INK["surface"])
    plt.close(fig)


def run_cluster(
    db: str = "shorts.db", output: str = "keyword_clusters.png"
) -> Path:
    """完整分群流程（可被 data_router.py 匯入呼叫），回傳圖表路徑。"""
    enable_tracing()
    db_path = find_db(db)

    # 步驟 1：{id: [關鍵字...]} 字典，再用括號拆成中文／英文兩種字典
    keyword_dict = load_keyword_dict(db_path)
    zh_dict, en_dict = build_lang_dicts(keyword_dict)
    print(f"中文字典：{len(zh_dict)} 筆；英文字典：{len(en_dict)} 筆")

    # 步驟 2＋3：攤平 → embedding → KMeans 分 15 群 → gemma3 命名
    zh_flat = [kw for kws in zh_dict.values() for kw in kws]
    en_flat = [kw for kws in en_dict.values() for kw in kws]

    results = {}
    if zh_flat:
        results["chinese"] = cluster_and_name(zh_flat, "zh")
    if en_flat:
        results["english"] = cluster_and_name(en_flat, "en")
    if not results:
        raise RuntimeError("沒有任何關鍵字可分群")

    # 步驟 4：每部影片以多數決選出 cluster 名稱，寫回 shorts.db 的 cluster 欄位
    save_clusters(db_path, zh_dict, en_dict, results)

    out_path = Path(__file__).resolve().parent / output
    save_chart(results, out_path)
    print(f"\n分群圖表已儲存：{out_path}")
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description="shorts.db 關鍵字分群")
    parser.add_argument("--db", default="shorts.db", help="SQLite 資料庫路徑")
    parser.add_argument(
        "--output", default="keyword_clusters.png", help="輸出圖表檔名"
    )
    args = parser.parse_args()
    try:
        run_cluster(args.db, args.output)
    except RuntimeError as e:
        sys.exit(f"錯誤：{e}")


if __name__ == "__main__":
    main()
