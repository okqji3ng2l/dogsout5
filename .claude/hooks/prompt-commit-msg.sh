#!/bin/bash
# Claude Code UserPromptSubmit Hook
# 用途：使用者每次送出新的 prompt 時，先跳出記事本（notepad.exe），
#       讓你輸入「這一輪完成後要用的 commit message」，存檔關閉後
#       讀取內容存到暫存檔，等這一輪結束、Stop hook（git-push.sh）
#       執行 commit 時讀取使用。
set -e
 
PROJECT_DIR="/mnt/d/dogsout"   # ★ 跟 git-push.sh 保持一致
MSG_FILE="$PROJECT_DIR/.claude/hooks/.pending_commit_msg"
EDIT_FILE="$PROJECT_DIR/.claude/hooks/.commit_msg_edit.txt"
 
# 吃掉 hook 傳入的 JSON（UserPromptSubmit 也會透過 stdin 傳資料），避免卡住。
cat >/dev/null 2>&1 || true
 
# 準備要編輯的暫存檔，開頭留幾行說明（# 開頭的行等一下會被忽略）。
cat > "$EDIT_FILE" <<'TXT'
# 請在下方輸入這一輪完成後要用的 commit message，存檔後關閉記事本即可繼續。
# 以 # 開頭的行會被忽略；留空則稍後自動產生訊息。
 
TXT
 
# 先確認這個環境能不能執行 Windows 程式（WSL interop）。
# 在沒有 interop 的環境（例如沙盒/容器）直接執行 .exe 會 Exec format error。
wsl_interop_ok() {
  [ -e /proc/sys/fs/binfmt_misc/WSLInterop ] || return 1
  grep -q '^enabled' /proc/sys/fs/binfmt_misc/WSLInterop 2>/dev/null
}
 
# 依序嘗試幾個位置找 notepad.exe（WSL 下 hook 子行程的 PATH 常常沒有
# 包含 Windows 系統路徑，不能只靠 command -v notepad.exe）。
NOTEPAD_BIN=""
if wsl_interop_ok; then
  for candidate in \
    "notepad.exe" \
    "/mnt/c/Windows/System32/notepad.exe" \
    "/mnt/c/Windows/system32/notepad.exe" \
    "$(command -v notepad.exe 2>/dev/null)"
  do
    if [ -n "$candidate" ] && command -v "$candidate" >/dev/null 2>&1; then
      NOTEPAD_BIN="$candidate"
      break
    fi
  done
fi
 
COMMIT_MSG=""
if [ -n "$NOTEPAD_BIN" ]; then
  WIN_PATH="$(wslpath -w "$EDIT_FILE" 2>/dev/null || echo "$EDIT_FILE")"
  # notepad.exe 會卡住這一行，直到使用者存檔並關閉視窗才會繼續往下執行。
  # 加上容錯：就算執行失敗（例如 interop 偵測誤判）也不要讓 set -e 中止整個 hook。
  if "$NOTEPAD_BIN" "$WIN_PATH" 2>/dev/null; then
    COMMIT_MSG="$(grep -v '^#' "$EDIT_FILE" | sed '/^[[:space:]]*$/d' | head -n1)"
  else
    echo "notepad.exe 執行失敗，稍後改用自動產生的訊息。"
  fi
else
  echo "此環境無法開啟 notepad.exe（無 WSL interop 或找不到程式），稍後改用自動產生的訊息。"
fi
 
rm -f "$EDIT_FILE"
 
mkdir -p "$(dirname "$MSG_FILE")"
printf '%s' "$COMMIT_MSG" > "$MSG_FILE"
echo "已記錄這一輪的 commit message：${COMMIT_MSG:-（空，稍後自動產生）}"
exit 0
exit 0