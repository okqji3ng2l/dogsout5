#!/bin/bash
# Claude Code Stop Hook
# 用途：當 Claude 完成一輪程式碼修改後，自動把改動 commit 到 test 分支
#       （分支不存在就先建立），commit message 由 Claude 自動根據 diff 產生，
#       最後顯示這次修改的檔案清單與內容。
#
# 安裝方式：
# 1. 把這個檔案放到專案的 .claude/hooks/git-data-analysis-commit.sh
# 2. chmod +x .claude/hooks/git-data-analysis-commit.sh
# 3. 在 .claude/settings.json 加上對應的 Stop hook 設定（見 settings.json 範例）
set -e
 
# ====== 依專案調整這幾個變數 ======
PROJECT_DIR="/mnt/d/dogsout"   # ★ 寫死的專案根目錄，請改成你的實際路徑
TARGET_BRANCH="dogsout"   # 固定使用的分支名稱
PUSH_TO_REMOTE=false            # 若要自動推送到遠端，改成 true
FALLBACK_MSG="自動產生：Claude Code 完成的修改"  # claude CLI 失敗時的備用訊息
# ===================================
 
# 防止 Stop hook 迴圈：如果這次 Stop 是 hook 自己觸發的，直接結束
# （Claude Code 會透過 stdin 傳入 JSON，其中 stop_hook_active 為 true 代表已在 hook 流程中）
HOOK_INPUT="$(cat 2>/dev/null || true)"
if command -v jq >/dev/null 2>&1 && [ -n "$HOOK_INPUT" ]; then
  if [ "$(echo "$HOOK_INPUT" | jq -r '.stop_hook_active // false')" = "true" ]; then
    exit 0
  fi
fi
 
# ====== 切換到專案目錄（寫死） ======
if [ ! -d "$PROJECT_DIR" ]; then
  echo "錯誤：PROJECT_DIR 不存在：$PROJECT_DIR"
  echo "請修改腳本開頭的 PROJECT_DIR 變數。"
  exit 0
fi
cd "$PROJECT_DIR"
echo "工作目錄：$(pwd)"
 
# ====== 讀取 UserPromptSubmit hook 一開始存下來的 commit message ======
# 真正的輸入視窗在你送出訊息當下（prompt-commit-msg.sh / UserPromptSubmit hook）
# 就已經跳出來問過了，這裡只是把結果讀出來用，不再重複詢問。
MSG_FILE="$PROJECT_DIR/.claude/hooks/.pending_commit_msg"
COMMIT_MSG=""
if [ -f "$MSG_FILE" ]; then
  COMMIT_MSG="$(cat "$MSG_FILE")"
  rm -f "$MSG_FILE"
fi
echo "取得的 commit message：${COMMIT_MSG:-（空，稍後自動產生）}"
 
# 確認這裡是 git repo，不是就自動初始化
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "這個目錄不是 git repository，自動執行 git init。"
  git init
fi
 
# commit 需要 user.name / user.email，沒設定的話補上本地預設值（只影響這個專案）
if [ -z "$(git config user.email || true)" ]; then
  git config user.email "claude-code-hook@local"
fi
if [ -z "$(git config user.name || true)" ]; then
  git config user.name "Claude Code Hook"
fi
 
# 判斷 repo 是否已有 commit（全新 repo 沒有 HEAD，不能 stash / show）
HAS_COMMITS=true
if ! git rev-parse --verify --quiet HEAD >/dev/null; then
  HAS_COMMITS=false
fi
 
echo "=== [1/7] 檢查目前狀態 ==="
git status --short
 
# 如果完全沒有改動（包含未追蹤的檔案），就不需要做任何事，直接結束
if git diff --quiet && git diff --cached --quiet && [ -z "$(git status --porcelain)" ]; then
  echo "沒有偵測到任何變更，跳過。"
  exit 0
fi
 
CURRENT_BRANCH="$(git branch --show-current)"
 
echo "=== [2/7] 切換到 $TARGET_BRANCH 分支 ==="
if [ "$CURRENT_BRANCH" = "$TARGET_BRANCH" ]; then
  echo "目前已在 $TARGET_BRANCH，不需要切換。"
elif [ "$HAS_COMMITS" = false ]; then
  # 全新 repo 還沒有任何 commit，不能 stash，直接把目前（未誕生的）分支改名即可
  echo "全新 repository，直接建立 $TARGET_BRANCH 分支。"
  git branch -m "$TARGET_BRANCH" 2>/dev/null || git switch -c "$TARGET_BRANCH"
else
  # 先暫存目前的修改（-u 連未追蹤的新檔案也一起暫存，避免 checkout 時遺漏）
  git stash push -u -m "claude-code-auto-stash-$(date +%s)"
 
  if git rev-parse --verify --quiet "$TARGET_BRANCH" >/dev/null; then
    # 本地已有這個分支
    git switch "$TARGET_BRANCH"
  elif git ls-remote --exit-code --heads origin "$TARGET_BRANCH" >/dev/null 2>&1; then
    # 本地沒有，但遠端有 → 建立追蹤分支
    git switch -c "$TARGET_BRANCH" --track "origin/$TARGET_BRANCH"
  else
    # 本地和遠端都沒有 → 從目前分支建立新分支
    echo "分支 $TARGET_BRANCH 不存在，從 $CURRENT_BRANCH 建立。"
    git switch -c "$TARGET_BRANCH"
  fi
 
  # 還原剛才暫存的修改
  git stash pop
fi
 
echo "=== [3/7] 暫存所有修改 ==="
git add -A
git status --short
 
echo "=== [4/7] 使用一開始輸入的 commit message ==="
STAT_CONTENT="$(git diff --cached --stat)"
CHANGED_FILES="$(git diff --cached --name-only | tr '\n' ' ')"
if [ -z "$COMMIT_MSG" ]; then
  if [ -n "$CHANGED_FILES" ]; then
    COMMIT_MSG="chore: 更新 ${CHANGED_FILES}"
  else
    COMMIT_MSG="$FALLBACK_MSG"
  fi
fi
echo "Commit message：$COMMIT_MSG"
 

echo "=== [5/7] 提交 ==="
git commit -m "$COMMIT_MSG"
 
echo "=== [6/7] 顯示這次修改的檔案與內容 ==="
echo "--- 修改的檔案 ---"
git show --stat --oneline HEAD
echo ""
echo "--- 修改的內容 ---"
git show HEAD
 
if [ "$PUSH_TO_REMOTE" = true ]; then
  echo "=== [7/7] 推送到遠端 ==="
  git push -u origin "$TARGET_BRANCH"
  echo "完成！已 commit 並推送到 $TARGET_BRANCH。"
else
  echo "=== [7/7] 完成 ==="
  echo "完成！已 commit 到 $TARGET_BRANCH（未推送，如需自動推送請把 PUSH_TO_REMOTE 改為 true）。"
fi