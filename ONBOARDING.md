# 세계일보 AI 도구 — 팀 배포 가이드

---

## 🗞️ 기사 에이전트 (주요 작업 도구)

### 기능
| 탭 | 설명 |
|---|---|
| 기사 제목 추천 | 기사 초안 → 세계일보 스타일 제목 3개 추천 |
| 보도자료 기사화 | 보도자료 텍스트/URL/PDF → 역피라미드 기사 자동 변환 |
| 교열 | 기사 초안 맞춤법·어색한 표현·팩트 점검 |
| AEO 변환 | 기사 → ChatGPT·Perplexity AI 검색 최적화 포맷 변환 |

### 빠른 시작 (팀원용)

**1단계 — 레포 클론**
```bash
git clone https://github.com/jayce0321/triz-dashboard.git
cd triz-dashboard
```

**2단계 — API 키 설정** (최초 1회)
```bash
mkdir -p ~/.anthropic
echo 'ANTHROPIC_API_KEY=sk-ant-여기에_실제_키_입력' > ~/.anthropic/triz.env
```
키 발급: https://console.anthropic.com/ → API Keys (최소 $5 크레딧 필요)

**3단계 — 실행**
```bash
bash start.sh
# → http://localhost:8765/agent
```

### 수동 실행 (선택)
```bash
cd aeo
pip install -r requirements.txt
uvicorn aeo_dashboard:app --host 0.0.0.0 --port 8765 --reload
```

### 환경변수
| 변수 | 설명 | 기본값 |
|---|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API 키 (필수) | — |
| `PORT` | 서버 포트 | 8765 |

---

## 미리보는세계 v2 — 맥미니 이어받기 가이드

## 프로젝트 개요
세계일보 모바일 뉴스 앱 (단일 HTML/CSS/JS, 1900줄+)  
Railway에 FastAPI로 배포 중.

## 라이브 URL
- **앱 URL**: https://triz-dashboard-production.up.railway.app/miribon
- **GitHub**: https://github.com/jayce0321/triz-dashboard

## 로컬 파일 위치
```
~/Library/Mobile Documents/com~apple~CloudDocs/허세계2일차/
├── 미리보는세계v2.html        ← 메인 앱 파일 (여기만 수정)
├── TRIZ_ASIT_대시보드.py      ← FastAPI 서버 (/miribon 라우트 포함)
├── CLAUDE.md                  ← 작업 지침 (브랜드·인코딩·언어 규칙)
├── 배포가이드.md
└── .claude/launch.json        ← 로컬 미리보기 서버 설정
```

## 로컬 미리보기 서버
```bash
node /tmp/miribon_server.js   # 포트 8181
# → http://localhost:8181
```
`/tmp/miribon_server.js` 없으면 아래로 생성:
```js
const http = require('http'), fs = require('fs'), path = require('path');
const DIR = '/Users/ijaeho/Library/Mobile Documents/com~apple~CloudDocs/허세계2일차';
http.createServer((req, res) => {
  let p = decodeURIComponent(req.url.split('?')[0]);
  if (p === '/') p = '/미리보는세계v2.html';
  fs.readFile(path.join(DIR, p), (e, d) => {
    if (e) { res.writeHead(404); res.end('Not Found'); return; }
    res.writeHead(200, {'Content-Type':'text/html; charset=utf-8'});
    res.end(d);
  });
}).listen(8181, () => console.log('http://localhost:8181'));
```

## 배포 방법
```bash
cd ~/Library/Mobile\ Documents/com~apple~CloudDocs/허세계2일차
git add 미리보는세계v2.html
git commit -m "feat: 변경 내용"
git push origin main
# Railway가 자동으로 재배포 (1~3분)
```

## 완료된 기능 (최신 상태)
| 탭 | 기능 | 상태 |
|---|---|---|
| 홈 | 날씨(Open-Meteo) + 주가(Yahoo Finance) + 뉴스 스트립 | ✅ |
| 뉴스 | 카테고리별 RSS 피드 (세계일보 10개 채널) | ✅ |
| 카드뉴스 | 뉴스 그리드 + Instagram 배너(@segyetimes) | ✅ |
| 숏츠 | **YouTube RSS** — 세계일보 채널 최신 영상 15개 + 인앱 플레이어 | ✅ |
| MY | 관심 카테고리 설정, 다크모드, 시니어모드 | ✅ |

## 수정 이력 (주요 버그픽스)
1. **스켈레톤 CSS** — `background` 단축속성이 `background-position` 덮어쓰는 버그 수정
2. **시트 600px 표시 버그** — `transform:translateX`가 `translateY` 초기화하는 버그 수정
3. **태블릿 네비 위치** — 768px·1024px 미디어쿼리 translateX 계산식 수정
4. **시트 스크롤 초기화** — 열 때마다 상단으로 리셋

## YouTube 연동 핵심 코드
```js
const YT_CH  = 'UCzwT19hkdAkZIil8WdgzdgA';  // 세계일보 채널 ID
const YT_RSS = `https://www.youtube.com/feeds/videos.xml?channel_id=${YT_CH}`;
// CORS 프록시 3개 → allorigins /raw 폴백 순으로 시도
// parseYouTubeRSS() → fetchYouTube() → buildYTShortsCard() → ytOpen()/ytClose()
```

## Instagram 연동 제약
Instagram API는 2020년부터 서버사이드 OAuth 필수 → 클라이언트 단독 임베드 불가.  
현재 방식: 그라디언트 배너 → @segyetimes 프로필 링크.

## 다음 작업 아이디어
- [ ] 숏츠 탭 상단에 YouTube 채널 구독 배너 추가
- [ ] 홈 화면 숏츠 섹션도 YouTube RSS로 교체
- [ ] 다크모드에서 YouTube 모달 배경색 개선
- [ ] 기사 시트에서 3줄 요약 AI 연동 (현재 로컬 처리)

## 브랜드 규칙
- 포인트 컬러: `#2367d7` (세계일보 시그니처 블루)
- 제목 폰트: `세계하나제목고딕` (없으면 시스템 한글)
- 본문 폰트: `세계하나본문명조` (없으면 시스템 한글)
- CSV/TXT: UTF-8 with BOM (`utf-8-sig`)
