#!/bin/bash
# update_daily.sh - full Dogsout update pipeline
# 由 update_daily.bat 透過 wsl 呼叫（呼叫前已 conda activate dogsout）。
# 所有實際指令都在 WSL 內執行。
export PYTHONIOENCODING=utf-8
cd /mnt/d/dogsout/data/data_analysis || exit 1

echo "[$(date)] Starting Dogsout update..."

# -- wait for Ollama (needed by translate and cluster steps, max 120s) --
WAITED=0
until curl -s -o /dev/null --max-time 3 http://localhost:11434/api/tags; do
    WAITED=$((WAITED + 5))
    if [ "$WAITED" -ge 120 ]; then
        echo "[$(date)] WARNING: Ollama not reachable after 120s, continuing anyway."
        break
    fi
    sleep 5
done

run_step() {
    label="$1"
    shift
    echo "[$(date)] $label"
    python data_router.py "$@"
    if [ $? -ne 0 ]; then
        echo "[$(date)] Pipeline step FAILED, aborting."
        exit 1
    fi
}

run_step "Step 1/5 collect" collect
run_step "Step 2/5 translate" translate
run_step "Step 3/5 keywords (langchain async)" keywords
run_step "Step 4/5 cluster" cluster
run_step "Step 5/5 build html" build

echo "[$(date)] Done."
exit 0
