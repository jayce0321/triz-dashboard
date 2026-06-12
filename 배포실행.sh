#!/bin/bash
# TRIZ/ASIT 대시보드 배포 스크립트
# 실행: bash 배포실행.sh

set -e
BLUE='\033[0;34m'; GREEN='\033[0;32m'; RED='\033[0;31m'; NC='\033[0m'
echo -e "${BLUE}🚀 TRIZ/ASIT 대시보드 배포 시작...${NC}"

# ─── Railway 배포 ───────────────────────────────────────────────
if command -v railway &>/dev/null; then
  echo -e "${BLUE}[Railway] 로그인 상태 확인...${NC}"
  if railway whoami &>/dev/null 2>&1; then
    echo -e "${GREEN}[Railway] 로그인됨 → 배포 진행${NC}"
    railway up
    echo -e "${GREEN}✅ Railway 배포 완료!${NC}"
    railway open
    exit 0
  else
    echo -e "${BLUE}[Railway] 로그인이 필요합니다. 브라우저가 열립니다...${NC}"
    railway login
    railway up
    echo -e "${GREEN}✅ Railway 배포 완료!${NC}"
    railway open
    exit 0
  fi
fi

# ─── GitHub Push (자동 배포가 설정된 경우) ──────────────────────
if git remote | grep -q origin; then
  echo -e "${BLUE}[GitHub] origin 원격 저장소에 푸시...${NC}"
  git push origin main
  echo -e "${GREEN}✅ GitHub 푸시 완료! Railway/Render가 자동 재배포됩니다.${NC}"
  exit 0
fi

echo -e "${RED}[안내] Railway CLI 또는 GitHub remote가 필요합니다.${NC}"
echo ""
echo "  방법 1 (Railway): railway login && railway init && railway up"
echo "  방법 2 (GitHub):  git remote add origin <GitHub_URL> && git push -u origin main"
echo "  방법 3 (Render):  render.com 대시보드에서 수동 업로드"
