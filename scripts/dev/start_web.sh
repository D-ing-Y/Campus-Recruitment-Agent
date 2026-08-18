#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
WEB_ROOT="$PROJECT_ROOT/apps/web"
PYTHON_BIN="$PROJECT_ROOT/.venv/bin/python"
CLI_BIN="$PROJECT_ROOT/.venv/bin/campus-agent"
API_URL="http://127.0.0.1:8765"
WEB_URL="http://localhost:3000"
API_PID=""
WEB_PID=""

cleanup() {
  trap - EXIT INT TERM
  if [[ -n "$WEB_PID" ]] && kill -0 "$WEB_PID" 2>/dev/null; then
    kill "$WEB_PID" 2>/dev/null || true
  fi
  if [[ -n "$API_PID" ]] && kill -0 "$API_PID" 2>/dev/null; then
    kill "$API_PID" 2>/dev/null || true
  fi
  wait 2>/dev/null || true
}

fail() {
  echo "启动失败：$1" >&2
  exit 1
}

check_port() {
  local port="$1"
  local service="$2"
  if command -v lsof >/dev/null 2>&1 && lsof -tiTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    fail "$service 端口 $port 已被占用，请先停止旧进程。"
  fi
}

wait_for_url() {
  local url="$1"
  local service="$2"
  local pid="$3"
  local attempt
  for attempt in $(seq 1 60); do
    if curl --connect-timeout 1 --max-time 2 -fsS "$url" >/dev/null 2>&1; then
      return 0
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
      fail "$service 启动进程已经退出，请查看上方日志。"
    fi
    sleep 1
  done
  fail "等待 $service 超过 60 秒。"
}

trap cleanup EXIT INT TERM

command -v node >/dev/null 2>&1 || fail "未找到 Node.js，请先安装 Node.js 22.13 或更高版本。"
command -v npm >/dev/null 2>&1 || fail "未找到 npm。"
command -v curl >/dev/null 2>&1 || fail "未找到 curl。"
[[ -x "$PYTHON_BIN" ]] || fail "未找到 .venv，请先按照 README 创建 Python 虚拟环境。"

check_port 8765 "API"
check_port 3000 "Web"

echo "[1/4] 同步当前 Python 源码到本地运行环境……"
"$PYTHON_BIN" -m pip install "$PROJECT_ROOT" \
  --force-reinstall --no-deps --no-build-isolation --quiet

echo "[2/4] 检查模型和本地运行环境……"
"$CLI_BIN" --json doctor >/dev/null || fail "运行环境检查失败，请执行 .venv/bin/campus-agent --json doctor 查看详情。"

if [[ ! -x "$WEB_ROOT/node_modules/.bin/vinext" ]]; then
  echo "未检测到 Web 依赖，正在执行 npm ci……"
  (cd "$WEB_ROOT" && npm ci)
fi

echo "[3/4] 启动 API：$API_URL"
(
  cd "$WEB_ROOT"
  exec "$PYTHON_BIN" -m uvicorn server.app:app --host 127.0.0.1 --port 8765
) &
API_PID=$!
wait_for_url "$API_URL/api/health" "API" "$API_PID"

echo "[4/4] 启动 Web：$WEB_URL"
(
  cd "$WEB_ROOT"
  export WRANGLER_LOG_PATH="$WEB_ROOT/.wrangler/wrangler.log"
  export NEXT_PUBLIC_CAMPUS_API_URL="${NEXT_PUBLIC_CAMPUS_API_URL:-$API_URL}"
  exec "$WEB_ROOT/node_modules/.bin/vinext" dev
) &
WEB_PID=$!
wait_for_url "$WEB_URL" "Web" "$WEB_PID"

echo
echo "Campus Agent 已启动：$WEB_URL"
echo "现在可以上传 10 MB 以内、带文本层的 PDF 简历。"
echo "按 Ctrl+C 同时停止 Web 和 API。"

if [[ "${CAMPUS_WEB_NO_OPEN:-0}" != "1" ]] && command -v open >/dev/null 2>&1; then
  open "$WEB_URL"
fi

while kill -0 "$API_PID" 2>/dev/null && kill -0 "$WEB_PID" 2>/dev/null; do
  sleep 1
done

fail "API 或 Web 进程意外退出，请查看上方日志。"
