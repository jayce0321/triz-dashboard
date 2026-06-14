#!/bin/bash
# 세계일보 기사 에이전트 — 로컬 시작 스크립트
# 사용법: bash start.sh
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
AEO_DIR="$SCRIPT_DIR/aeo"
PORT="${PORT:-8765}"

# ── Python 확인 ──────────────────────────────────
PYTHON=$(command -v python3 || command -v python || echo "")
if [ -z "$PYTHON" ]; then
  echo "❌ Python이 설치되지 않았습니다. https://python.org 에서 설치하세요."
  exit 1
fi
echo "✅ Python: $($PYTHON --version)"

# ── ANTHROPIC_API_KEY 확인 ───────────────────────
ENV_FILE="$HOME/.anthropic/triz.env"
if [ -f "$ENV_FILE" ]; then
  export $(grep -v '^#' "$ENV_FILE" | xargs) 2>/dev/null || true
fi

if [ -z "$ANTHROPIC_API_KEY" ]; then
  echo ""
  echo "❌ ANTHROPIC_API_KEY 환경변수가 없습니다."
  echo "   다음 중 하나를 선택하세요:"
  echo ""
  echo "   [방법 1] ~/.anthropic/triz.env 파일 생성"
  echo "     mkdir -p ~/.anthropic"
  echo "     echo 'ANTHROPIC_API_KEY=sk-ant-...' > ~/.anthropic/triz.env"
  echo ""
  echo "   [방법 2] 환경변수 직접 설정 후 재실행"
  echo "     export ANTHROPIC_API_KEY=sk-ant-..."
  echo "     bash start.sh"
  echo ""
  echo "   API 키 발급: https://console.anthropic.com/ → API Keys"
  exit 1
fi
echo "✅ ANTHROPIC_API_KEY: 설정됨"

# ── 의존성 설치 ──────────────────────────────────
echo ""
echo "📦 의존성 설치 중..."
$PYTHON -m pip install -q -r "$AEO_DIR/requirements.txt"
echo "✅ 의존성 설치 완료"

# ── 서버 시작 ────────────────────────────────────
echo ""
echo "🚀 서버 시작 중... http://localhost:$PORT/agent"
echo "   종료: Ctrl+C"
echo ""
cd "$AEO_DIR"
$PYTHON -m uvicorn aeo_dashboard:app --host 0.0.0.0 --port "$PORT" --reload
