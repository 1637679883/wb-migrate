#!/usr/bin/env bash
# wb-gui 启动器（macOS / Linux / Windows Git Bash）
set -u
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

for cand in python3 python; do
  if command -v "$cand" >/dev/null 2>&1; then
    exec "$cand" "$DIR/wb_gui.py" "$@"
  fi
done

for p in "$HOME"/.workbuddy/binaries/python/versions/*/python3 "$HOME"/.workbuddy/binaries/python/versions/*/python; do
  if [ -x "$p" ]; then
    exec "$p" "$DIR/wb_gui.py" "$@"
  fi
done

echo "没有找到可用的 Python 3，请先安装 Python 3.8 以上版本。"
exit 1
