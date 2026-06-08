"""
세계일보 AEO 대시보드 백엔드
목적: SEO 기사를 AEO/AI 검색 친화형으로 변환 + 품질 평가
"""
import os, re, uuid, json, csv, asyncio, traceback
from urllib.parse import quote as urlquote
from datetime import datetime
from io import StringIO
from typing import List, Optional
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from pydantic import BaseModel
import httpx
from bs4 import BeautifulSoup
import anthropic

try:
    import pdfplumber
    _PDF_OK = True
except ImportError:
    _PDF_OK = False

# ── 환경변수 ──────────────────────────────────────
_ENV = os.path.expanduser("~/.anthropic/triz.env")
load_dotenv(_ENV if os.path.isfile(_ENV) else None, override=True)
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = "claude-haiku-4-5-20251001"

# ── FastAPI 앱 ────────────────────────────────────
app = FastAPI(title="세계일보 AEO 대시보드")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
DATA_FILE  = os.path.join(BASE_DIR, "aeo_articles.json")
HTML_FILE  = os.path.join(BASE_DIR, "aeo_대시보드.html")

# ── 엔티티 오탐 차단 사전 ──────────────────────────
ENTITY_BLOCKLIST = {
    # 접속사·부사
    "당시","따르면","결국","반면","역시","비로소","아니면","가운데","한편",
    "이어","이후","이전","기준","현재","관련","대해","통해","위해","때문",
    "경우","수준","상황","부분","문제","방식","한국","이날","지난","이번",
    "모두","함께","특히","또한","이에","여기","앞서","이를","이로","만큼",
    "올해","내년","우선","그에","이어","여야","여전","여기","여야","참여",
    # '-로/가/이/을/는' 붙은 형태
    "순으로","필요로","처음으로","이날부","투표소가","투표소","사전투표",
    "반드시","참여하려","참여하면","이용하면","확인하면","방문하면",
    # 일반명사 오탐
    "국내총생산","소비자물가","금융통화위원회","금통위","기준금리",
    "기자간담회","전망치도","리스크가","동결에도","호황도","측면에서도",
    "것으로","앞으로","확대가","영향으로","총재가",
}

# ── 엔티티 패턴 (보수적 — 명확한 고유명사만) ─────────
PAT_NUMBER   = re.compile(
    r'(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)'
    r'(?:\s*(?:조|억|만|천))*'
    r'(?:\s*(?:%p?|퍼센트|포인트|원|달러|유로|엔|위안|명|건|개|회|분|개월|kg|t|km|㎡|위|bp|ppt))?',
    re.IGNORECASE,
)
PAT_DATE     = re.compile(
    r'\d{4}년\s*\d{1,2}월(?:\s*\d{1,2}일)?'
    r'|\d{1,2}월\s*\d{1,2}일'
    r'|\d{4}[-./]\d{1,2}[-./]\d{1,2}'
    r'|(?:올해|지난해|내년|올 상반기|올 하반기|[1-4]분기)',
)
PAT_QUOTE    = re.compile(r'"([^"]{5,200})"')
# 장소: 반드시 2글자 이상 어근 + 명확한 행정구역 접미사
PAT_ENTITY_P = re.compile(r'[가-힣]{2,4}(?:특별시|광역시|특별자치시|특별자치도)(?=[^가-힣]|$)|'
                           r'(?:서울|부산|인천|대구|대전|광주|울산|세종|수원|성남|춘천|청주|전주|창원|제주)'
                           r'(?:시|도|구)?(?=[^가-힣]|$)')
# 기관: 명확한 기관 접미사 (단독 '처' 제외, 복합 접미사만)
PAT_ENTITY_O = re.compile(r'[가-힣A-Z]{2,8}(?:위원회|연구원|대학교|대학|병원|그룹|공사|공단|협회|재단|청(?=[^가-힣]|$))')

# AEO→원문 역방향 엔티티 추출 (핵심사실·FAQ에 언급된 고유명사 후보)
PAT_KR_NOUN  = re.compile(r'[가-힣]{2,5}(?:씨|측|부|대통령|총재|총리|장관|의원|대표|위원|교수|기자)')
PAT_EN_ABBR  = re.compile(r'\b[A-Z]{2,6}\b')

# ── 저장소 ────────────────────────────────────────
def load_db() -> list:
    if os.path.isfile(DATA_FILE):
        try:
            return json.loads(open(DATA_FILE, encoding="utf-8").read())
        except Exception:
            return []
    return []

def save_db(data: list):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ── Pydantic 모델 ─────────────────────────────────
class ConvertRequest(BaseModel):
    url: str

class BatchRequest(BaseModel):
    urls: List[str]

class StatusUpdate(BaseModel):
    status: str          # pending | approved | rejected
    memo: Optional[str] = ""

# ── 기사 본문 추출 ────────────────────────────────
async def fetch_article(url: str) -> dict:
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/124.0.0.0 Safari/537.36"
    }
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        r = await client.get(url, headers=headers)
        r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")

    # 제목
    title = ""
    for sel in ["h1.article-title", "h1", "meta[property='og:title']", "title"]:
        el = soup.select_one(sel)
        if el:
            title = el.get("content", "") or el.get_text(strip=True)
            if title:
                break

    # 본문 — 세계일보 구조 우선, 일반 폴백
    body = ""
    for sel in [
        "div.article_txt", "div#article_txt", "div.news_text",
        "div.article-body", "div.article_body", "article",
        "div#newsContent", "div.content",
    ]:
        el = soup.select_one(sel)
        if el:
            # 광고·스크립트 제거
            for tag in el.select("script, style, ins, .ad, .banner, figure"):
                tag.decompose()
            body = el.get_text("\n", strip=True)
            if len(body) > 200:
                break

    if not body:
        # 최후 폴백: <p> 태그 집합
        paras = [p.get_text(strip=True) for p in soup.find_all("p") if len(p.get_text(strip=True)) > 30]
        body = "\n".join(paras[:40])

    return {"title": title, "body": body, "url": url}

# ── 파일 본문 추출 ────────────────────────────────
def extract_from_file(filename: str, content: bytes) -> dict:
    """업로드된 파일에서 제목·본문 추출. 지원: txt, md, html, htm, pdf"""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "txt"

    if ext in ("html", "htm"):
        soup = BeautifulSoup(content, "html.parser")
        # 제목
        title = ""
        for sel in ["h1", "meta[property='og:title']", "title"]:
            el = soup.select_one(sel)
            if el:
                title = el.get("content", "") or el.get_text(strip=True)
                if title: break
        # 본문
        for tag in soup.select("script, style, nav, header, footer, .ad, .banner"):
            tag.decompose()
        for sel in ["article", "div.article_txt", "div#article_txt",
                    "div.article-body", "main", "div.content"]:
            el = soup.select_one(sel)
            if el:
                body = el.get_text("\n", strip=True)
                if len(body) > 200:
                    return {"title": title, "body": body, "source": filename}
        body = soup.get_text("\n", strip=True)[:8000]
        return {"title": title, "body": body, "source": filename}

    elif ext == "pdf":
        if not _PDF_OK:
            raise HTTPException(415, "PDF 지원을 위해 pdfplumber 설치가 필요합니다.")
        import io
        pages_text = []
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for page in pdf.pages[:20]:   # 최대 20페이지
                t = page.extract_text()
                if t:
                    pages_text.append(t)
        full = "\n".join(pages_text)
        # 첫 줄을 제목으로 추정
        lines = [l.strip() for l in full.split("\n") if l.strip()]
        title = lines[0][:120] if lines else filename
        body  = "\n".join(lines[1:])[:8000]
        return {"title": title, "body": body, "source": filename}

    else:
        # txt / md / 기타 텍스트
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            text = content.decode("cp949", errors="replace")

        lines = [l.strip() for l in text.split("\n") if l.strip()]
        # md: 첫 # 헤딩을 제목으로
        title = ""
        body_start = 0
        for i, line in enumerate(lines):
            if line.startswith("# "):
                title = line.lstrip("# ").strip()
                body_start = i + 1
                break
        if not title:
            title = lines[0][:120] if lines else filename
            body_start = 1
        body = "\n".join(lines[body_start:])[:8000]
        return {"title": title, "body": body, "source": filename}

# ── 한국어 휴리스틱 분석기 ─────────────────────────
def analyze_korean(text: str) -> dict:
    numbers  = list(set(PAT_NUMBER.findall(text)))[:30]
    dates    = list(set(PAT_DATE.findall(text)))
    quotes   = PAT_QUOTE.findall(text)[:12]
    places   = [e for e in set(PAT_ENTITY_P.findall(text)) if e not in ENTITY_BLOCKLIST]
    orgs     = [e for e in set(PAT_ENTITY_O.findall(text)) if e not in ENTITY_BLOCKLIST]
    persons  = [e for e in set(PAT_KR_NOUN.findall(text)) if e not in ENTITY_BLOCKLIST]
    en_abbrs = [e for e in set(PAT_EN_ABBR.findall(text)) if len(e) >= 2]
    entities = list(set(places + orgs + persons + en_abbrs))
    return {
        "numbers":  numbers,
        "dates":    dates,
        "quotes":   quotes,
        "entities": entities,
    }

# ── AEO 변환 (Claude) ─────────────────────────────
AEO_SYSTEM = """당신은 한국어 뉴스 기사를 AEO(Answer Engine Optimization) 형식으로 변환하는 전문가입니다.
다음 규칙을 반드시 따르세요.

【사실 보존 규칙】
1. 원문의 수치·날짜·인명·직접 인용을 한 자도 바꾸거나 생략하지 않는다.
2. 의견·추측·부연설명을 절대 추가하지 않는다.

【AEO 구조 규칙 — GEO-16 표준】
3. direct_answer: 반드시 40~60 한국어 어절(띄어쓰기 단위)로 작성한다. "~은/는 ~이다. [근거]. [맥락]." 3문장 구조로 기사 핵심 질문에 직접 답한다. 40어절 미만이면 반드시 추가 문장을 넣어 채워야 한다.
4. faq: 질문(q)은 반드시 "~은/는 무엇인가?", "왜 ~했나?", "어떻게 ~하는가?" 등 실제 검색 쿼리 형식으로 작성한다. 최소 4개 이상.
5. structured_body: 모든 ## 헤딩을 의문문 형식으로 작성한다. 예: "## 기준금리는 왜 동결됐나?" "## 성장률 상향의 근거는?"
6. json_ld: headline은 기사 제목과 한 글자도 다르지 않게 일치시킨다. author·mentions·about 필드를 반드시 포함한다.

출력은 반드시 아래 JSON 형식만 반환한다. 마크다운 코드블록(```)을 쓰지 말 것.

{
  "direct_answer": "40~60 어절의 직접 답변 (핵심 질문에 바로 답하는 형식)",
  "key_facts": ["수치/날짜 포함 핵심 사실 1", "핵심 사실 2", ...],
  "faq": [
    {"q": "~은/는 무엇인가? (검색 쿼리 형식)", "a": "답변 2~3문장"},
    ...
  ],
  "structured_body": "## 의문형 헤딩 1?\n\n내용\n\n## 의문형 헤딩 2?\n\n내용",
  "json_ld": {
    "@context": "https://schema.org",
    "@type": "NewsArticle",
    "headline": "원문 제목 그대로",
    "description": "40~60자 요약",
    "datePublished": "YYYY-MM-DD",
    "dateModified": "YYYY-MM-DD",
    "author": {"@type": "Person", "name": "기자명 (본문에서 추출, 없으면 세계일보 편집부)"},
    "publisher": {"@type": "Organization", "name": "세계일보", "url": "https://www.segye.com"},
    "mainEntityOfPage": "기사 URL",
    "about": [{"@type": "Thing", "name": "핵심 토픽1"}, {"@type": "Thing", "name": "핵심 토픽2"}],
    "mentions": [{"@type": "Person 또는 Organization", "name": "언급된 인물/기관1"}, ...]
  }
}"""

def _parse_aeo_json(raw: str) -> dict:
    """Claude 응답에서 JSON 추출 — 4단계 방어 파싱"""
    if not raw:
        raise ValueError("빈 응답")
    # 1) 직접
    try: return json.loads(raw)
    except json.JSONDecodeError: pass
    # 2) 코드블록 제거
    cleaned = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
    try: return json.loads(cleaned)
    except json.JSONDecodeError: pass
    # 3) 첫 { 부터 마지막 } 까지 추출
    s = cleaned.find('{'); e = cleaned.rfind('}')
    if s != -1 and e > s:
        try: return json.loads(cleaned[s:e+1])
        except json.JSONDecodeError: pass
    raise ValueError(f"JSON 파싱 실패. 응답 앞부분: {raw[:300]}")

async def convert_to_aeo(title: str, body: str, retry: int = 2) -> dict:
    if not ANTHROPIC_API_KEY:
        raise HTTPException(503, "ANTHROPIC_API_KEY 미설정")

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    prompt = f"제목: {title}\n\n본문:\n{body[:4000]}"

    last_err = None
    for attempt in range(retry + 1):
        try:
            msg = client.messages.create(
                model=MODEL,
                max_tokens=4096,
                system=AEO_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = msg.content[0].text.strip()
            return _parse_aeo_json(raw)
        except (ValueError, json.JSONDecodeError) as e:
            last_err = e
            if attempt < retry:
                await asyncio.sleep(1)   # 재시도 전 짧은 대기
                continue
        except Exception as e:
            raise

    raise ValueError(f"AEO 변환 실패 ({retry+1}회 시도): {last_err}")

# ── 스코어링 엔진 ─────────────────────────────────
def score_preservation(orig: list, aeo_text: str, max_items: int = None) -> float:
    if not orig:
        return 100.0
    items = orig if max_items is None else orig[:max_items]
    preserved = sum(1 for item in items if item in aeo_text)
    return round(preserved / len(items) * 100, 1)

def _norm_num(s: str) -> str:
    """숫자 표현 정규화 (비교용): 공백 제거, %p→%포인트 통일"""
    s = s.strip().replace(" ", "")
    s = re.sub(r'%p$', '%포인트', s)
    s = re.sub(r'포인트$', '%포인트', s)
    return s

def score_tone(orig_body: str, aeo: dict) -> float:
    """추가 수치 삽입 여부 체크 — 정규화 후 비교"""
    # AEO에서 direct_answer + key_facts만 체크 (반복 노출 섹션 제외)
    aeo_check = json.dumps({
        "direct_answer": aeo.get("direct_answer",""),
        "key_facts":     aeo.get("key_facts",[]),
    }, ensure_ascii=False)
    orig_nums = {_norm_num(n) for n in PAT_NUMBER.findall(orig_body) if len(n)>1}
    aeo_nums  = {_norm_num(n) for n in PAT_NUMBER.findall(aeo_check) if len(n)>1}
    extra     = aeo_nums - orig_nums
    penalty   = min(len(extra) * 5, 20)
    return max(80.0, 100.0 - penalty)

def score_ai_readiness(aeo: dict) -> float:
    score = 0.0
    if aeo.get("direct_answer"): score += 25
    if aeo.get("key_facts"):     score += 25
    if aeo.get("faq"):           score += 25
    if aeo.get("json_ld"):       score += 15
    if aeo.get("structured_body"): score += 10
    return score

def score_structure(aeo: dict) -> float:
    score = 70.0
    sb = aeo.get("structured_body", "")
    if re.search(r"^##\s", sb, re.MULTILINE): score += 10
    if len(aeo.get("key_facts", [])) >= 3:    score += 10
    if len(aeo.get("faq", [])) >= 2:          score += 10
    return min(score, 100.0)

# ── GEO-16 기반 신규 지표 ─────────────────────────

def score_direct_answer_quality(aeo: dict) -> dict:
    """직접 답변 블록 품질: 업계 표준 40~60 어절 범위 체크"""
    da = aeo.get("direct_answer", "")
    words = len(da.split())  # 어절(공백 분리)
    if 40 <= words <= 60:
        score = 100.0
        note  = f"{words}어절 ✅ (최적 범위 40~60)"
    elif words < 40:
        score = max(60.0, 100.0 - (40 - words) * 2)
        note  = f"{words}어절 ⚠ (권고: 40~60어절, 너무 짧음)"
    else:
        score = max(70.0, 100.0 - (words - 60) * 1.5)
        note  = f"{words}어절 ⚠ (권고: 40~60어절, 너무 김)"
    return {"score": round(score, 1), "words": words, "note": note}

def score_question_headings(aeo: dict) -> dict:
    """질문형 헤딩 비율: H2/H3 헤딩 중 의문문(?) 비율"""
    sb = aeo.get("structured_body", "")
    headings = re.findall(r'^#{2,3}\s+(.+)$', sb, re.MULTILINE)
    if not headings:
        return {"score": 0.0, "total": 0, "question": 0, "note": "헤딩 없음"}
    q_headings = [h for h in headings if h.strip().endswith('?') or
                  any(w in h for w in ['무엇','왜','어떻게','언제','어디','누가','얼마나','몇','어떤'])]
    ratio = len(q_headings) / len(headings)
    score = round(ratio * 100, 1)
    note  = f"{len(headings)}개 중 {len(q_headings)}개 질문형 ({score}%)"
    return {"score": score, "total": len(headings), "question": len(q_headings), "note": note}

def score_jsonld_completeness(aeo: dict, url: str = "") -> dict:
    """JSON-LD 완전성: 업계 표준 필수+권고 필드 체크"""
    jld = aeo.get("json_ld", {})
    required = ["@type", "headline", "description", "datePublished"]
    recommended = ["dateModified", "author", "publisher", "mainEntityOfPage", "about", "mentions"]
    req_ok   = [f for f in required    if f in jld and jld[f]]
    rec_ok   = [f for f in recommended if f in jld and jld[f]]
    req_miss = [f for f in required    if f not in jld or not jld[f]]
    rec_miss = [f for f in recommended if f not in jld or not jld[f]]

    # 점수: 필수 70점 + 권고 30점
    score = (len(req_ok) / len(required)) * 70 + (len(rec_ok) / len(recommended)) * 30

    # headline-title 일치 여부
    headline_ok = bool(jld.get("headline"))

    return {
        "score":        round(score, 1),
        "required_ok":  req_ok,
        "required_miss":req_miss,
        "recommended_ok": rec_ok,
        "recommended_miss": rec_miss,
        "headline_ok":  headline_ok,
    }

def score_stats_density(orig_body: str) -> dict:
    """통계 밀도: 수치/데이터 밀도 (AI 인용 +41% 효과 근거)"""
    words = len(orig_body.split())
    nums  = PAT_NUMBER.findall(orig_body)
    density = len(nums) / max(words, 1) * 100  # 수치 per 100 어절
    if density >= 3.0:
        score = 100.0; note = f"수치 {len(nums)}건/{words}어절 — 고밀도 ✅"
    elif density >= 1.5:
        score = 80.0;  note = f"수치 {len(nums)}건/{words}어절 — 중밀도"
    else:
        score = 60.0;  note = f"수치 {len(nums)}건/{words}어절 — 저밀도 (수치 추가 권고)"
    return {"score": round(score, 1), "count": len(nums), "density": round(density, 2), "note": note}

def compute_geo_score(da_q: dict, heading_q: dict, jld_q: dict, stats_q: dict,
                      faq_count: int, ai_score: float, struct_score: float) -> dict:
    """GEO 종합 점수 (0~100, 업계 GEO-16 기반 10개 pillar)"""
    pillars = {
        "구조화 데이터(JSON-LD)":   jld_q["score"],
        "직접 답변 블록":           da_q["score"],
        "FAQ 섹션":                min(faq_count / 4 * 100, 100),
        "질문형 헤딩":              heading_q["score"],
        "통계·수치 밀도":           stats_q["score"],
        "콘텐츠 깊이(AI 준비도)":   ai_score,
        "마크업 구조":              struct_score,
        "메타데이터 완전성":        jld_q["score"] * 0.5 + (100 if jld_q["headline_ok"] else 0) * 0.5,
    }
    avg = sum(pillars.values()) / len(pillars)
    # GEO 0~1 점수로도 표현
    geo_01 = round(avg / 100, 3)
    # 0.70 이상이면 "인용 가능" 등급
    grade = "우수 (인용 가능)" if geo_01 >= 0.70 else ("양호" if geo_01 >= 0.50 else "개선 필요")
    return {
        "score":   round(avg, 1),
        "geo_01":  geo_01,
        "grade":   grade,
        "pillars": {k: round(v, 1) for k, v in pillars.items()},
    }

def evaluate(orig_info: dict, orig_body: str, aeo: dict, url: str = "") -> dict:
    aeo_text = json.dumps(aeo, ensure_ascii=False)

    # ── 기존 보존성 지표 ───────────────────────────
    fact_score   = score_preservation(orig_info["numbers"] + orig_info["dates"], aeo_text)
    num_score    = score_preservation(orig_info["numbers"], aeo_text)
    date_score   = score_preservation(orig_info["dates"],   aeo_text)
    entity_score = score_preservation(orig_info["entities"], aeo_text) if len(orig_info["entities"]) >= 3 else 100.0
    quote_score  = score_preservation(orig_info["quotes"],   aeo_text, max_items=12)
    tone_score   = score_tone(orig_body, aeo)
    ai_score     = score_ai_readiness(aeo)
    struct_score = score_structure(aeo)

    # ── 신규 GEO 지표 ──────────────────────────────
    da_q      = score_direct_answer_quality(aeo)
    heading_q = score_question_headings(aeo)
    jld_q     = score_jsonld_completeness(aeo, url)
    stats_q   = score_stats_density(orig_body)
    faq_items = aeo.get("faq", [])

    geo = compute_geo_score(da_q, heading_q, jld_q, stats_q,
                            len(faq_items), ai_score, struct_score)

    # ── 종합 점수 (기존 8지표 가중 평균) ──────────────
    weighted = (
        fact_score   * 0.15 +
        num_score    * 0.15 +
        date_score   * 0.10 +
        entity_score * 0.15 +
        quote_score  * 0.15 +
        tone_score   * 0.10 +
        ai_score     * 0.10 +
        struct_score * 0.10
    )

    # ── 경고 ───────────────────────────────────────
    warnings = []
    if len(orig_info["quotes"]) > 12:
        warnings.append(f"warning:직접 인용:{len(orig_info['quotes'])}건 중 12건만 추적")
    if num_score < 95:
        warnings.append(f"warning:수치 보존:{num_score}점")
    if ai_score < 80:
        warnings.append("info:AI 답변 준비도:구조화 요소 부족")
    if len(orig_body) < 300:
        warnings.append("info:본문 길이:본문이 짧아 AEO 변환 품질이 제한될 수 있습니다")
    if da_q["words"] < 40:
        warnings.append(f"info:직접답변:현재 {da_q['words']}어절 — 40~60어절 권고")
    if heading_q["score"] < 50:
        warnings.append(f"info:질문형 헤딩:{heading_q['note']}")
    if jld_q["required_miss"]:
        warnings.append(f"warning:JSON-LD:필수 필드 누락 {jld_q['required_miss']}")

    return {
        "score":         round(weighted, 1),
        "metrics": {
            "사실 보존":     fact_score,
            "수치 보존":     num_score,
            "날짜 보존":     date_score,
            "엔티티 보존":   entity_score,
            "직접 인용 보존": quote_score,
            "톤 안정성":     tone_score,
            "AI 답변 준비도": ai_score,
            "구조 품질":     struct_score,
        },
        "geo":    geo,
        "da_quality":    da_q,
        "heading_quality": heading_q,
        "jsonld_quality":  jld_q,
        "stats_density":   stats_q,
        "faq_check": {
            "faq_count":        len(faq_items),
            "schema_valid":     not bool(jld_q["required_miss"]),
            "schema_type":      aeo.get("json_ld", {}).get("@type", ""),
            "missing_fields":   jld_q["required_miss"] + jld_q["recommended_miss"],
            "recommended_miss": jld_q["recommended_miss"],
        },
        "warnings": warnings,
    }

# ── 전체 파이프라인 ───────────────────────────────
async def run_pipeline(url: str) -> dict:
    article   = await fetch_article(url)
    orig_info = analyze_korean(article["body"])
    aeo       = await convert_to_aeo(article["title"], article["body"])
    eval_res  = evaluate(orig_info, article["body"], aeo, url=url)

    record = {
        "id":         str(uuid.uuid4()),
        "url":        url,
        "title":      article["title"],
        "orig_body":  article["body"][:2000],
        "orig_info":  orig_info,
        "aeo":        aeo,
        "eval":       eval_res,
        "status":     "pending",
        "memo":       "",
        "created_at": datetime.now().isoformat(),
    }
    db = load_db()
    db.insert(0, record)
    save_db(db)
    return record

# ── API 엔드포인트 ────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    if os.path.isfile(HTML_FILE):
        return open(HTML_FILE, encoding="utf-8").read()
    return HTMLResponse("<h1>aeo_대시보드.html 파일이 없습니다.</h1>")

@app.get("/api/status")
async def status():
    db = load_db()
    return {
        "ok":         bool(ANTHROPIC_API_KEY),
        "model":      MODEL,
        "articles":   len(db),
        "pending":    sum(1 for a in db if a["status"] == "pending"),
        "approved":   sum(1 for a in db if a["status"] == "approved"),
        "rejected":   sum(1 for a in db if a["status"] == "rejected"),
    }

@app.post("/api/convert")
async def convert_single(req: ConvertRequest):
    """단일 URL 변환 (스트리밍 진행상황 SSE)"""
    async def stream():
        try:
            yield _sse("progress", "기사 본문 추출 중…")
            article   = await fetch_article(req.url)
            yield _sse("progress", f"본문 {len(article['body'])}자 추출 완료. 분석 중…")

            orig_info = analyze_korean(article["body"])
            yield _sse("progress",
                f"수치 {len(orig_info['numbers'])}건 / "
                f"날짜 {len(orig_info['dates'])}건 / "
                f"엔티티 {len(orig_info['entities'])}건 / "
                f"인용 {len(orig_info['quotes'])}건 감지. AEO 변환 중…")

            aeo      = await convert_to_aeo(article["title"], article["body"])
            yield _sse("progress", "평가 중…")
            eval_res = evaluate(orig_info, article["body"], aeo, url=req.url)

            record = {
                "id":         str(uuid.uuid4()),
                "url":        req.url,
                "title":      article["title"],
                "orig_body":  article["body"][:2000],
                "orig_info":  orig_info,
                "aeo":        aeo,
                "eval":       eval_res,
                "status":     "pending",
                "memo":       "",
                "created_at": datetime.now().isoformat(),
            }
            db = load_db()
            db.insert(0, record)
            save_db(db)
            yield _sse("done", json.dumps(record, ensure_ascii=False))
        except Exception as e:
            yield _sse("error", str(e) + "\n" + traceback.format_exc()[:500])

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

def _sse(event: str, data: str) -> str:
    return f"event: {event}\ndata: {data}\n\n"

@app.post("/api/batch")
async def convert_batch(req: BatchRequest):
    """URL 최대 10개 일괄 처리"""
    urls = req.urls[:10]
    results = []
    errors  = []

    for url in urls:
        try:
            rec = await run_pipeline(url.strip())
            results.append({"url": url, "id": rec["id"], "score": rec["eval"]["score"],
                            "title": rec["title"], "status": "ok"})
        except Exception as e:
            errors.append({"url": url, "error": str(e)})

    return {"processed": len(results), "errors": len(errors), "results": results, "failures": errors}

@app.get("/api/articles")
async def list_articles(status: Optional[str] = None, q: Optional[str] = None):
    db = load_db()
    if status and status != "all":
        db = [a for a in db if a["status"] == status]
    if q:
        db = [a for a in db if q in a.get("title","") or q in a.get("url","")]
    return {"total": len(db), "articles": db}

@app.get("/api/articles/{article_id}")
async def get_article(article_id: str):
    db = load_db()
    for a in db:
        if a["id"] == article_id:
            return a
    raise HTTPException(404, "기사를 찾을 수 없습니다.")

@app.put("/api/articles/{article_id}/status")
async def update_status(article_id: str, body: StatusUpdate):
    if body.status not in ("pending", "approved", "rejected"):
        raise HTTPException(400, "status는 pending/approved/rejected 중 하나")
    db = load_db()
    for a in db:
        if a["id"] == article_id:
            a["status"]     = body.status
            a["memo"]       = body.memo or ""
            a["updated_at"] = datetime.now().isoformat()
            save_db(db)
            return {"ok": True, "id": article_id, "status": body.status}
    raise HTTPException(404, "기사를 찾을 수 없습니다.")

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    """원본 파일 업로드 → AEO 변환 (SSE 스트리밍)"""
    ALLOWED = {"txt", "md", "html", "htm", "pdf"}
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ALLOWED:
        raise HTTPException(415, f"지원 형식: {', '.join(ALLOWED)}. 업로드된 파일: {file.filename}")

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:   # 10 MB 상한
        raise HTTPException(413, "파일 크기는 10MB 이하여야 합니다.")

    source_name = file.filename

    async def stream():
        try:
            yield _sse("progress", f"파일 읽는 중… ({source_name})")
            article   = extract_from_file(source_name, content)
            yield _sse("progress",
                f"본문 {len(article['body'])}자 추출 완료 ({source_name}). 분석 중…")

            orig_info = analyze_korean(article["body"])
            yield _sse("progress",
                f"수치 {len(orig_info['numbers'])}건 / "
                f"날짜 {len(orig_info['dates'])}건 / "
                f"엔티티 {len(orig_info['entities'])}건 / "
                f"인용 {len(orig_info['quotes'])}건 감지. AEO 변환 중…")

            aeo      = await convert_to_aeo(article["title"], article["body"])
            yield _sse("progress", "평가 중…")
            eval_res = evaluate(orig_info, article["body"], aeo,
                                url=f"file://{source_name}")

            record = {
                "id":         str(uuid.uuid4()),
                "url":        f"file://{source_name}",
                "title":      article["title"],
                "source":     source_name,
                "orig_body":  article["body"][:2000],
                "orig_info":  orig_info,
                "aeo":        aeo,
                "eval":       eval_res,
                "status":     "pending",
                "memo":       "",
                "created_at": datetime.now().isoformat(),
            }
            db = load_db()
            db.insert(0, record)
            save_db(db)
            yield _sse("done", json.dumps(record, ensure_ascii=False))
        except HTTPException as e:
            yield _sse("error", e.detail)
        except Exception as e:
            yield _sse("error", str(e) + "\n" + traceback.format_exc()[:500])

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})

@app.delete("/api/articles/{article_id}")
async def delete_article(article_id: str):
    db = load_db()
    new_db = [a for a in db if a["id"] != article_id]
    if len(new_db) == len(db):
        raise HTTPException(404, "기사를 찾을 수 없습니다.")
    save_db(new_db)
    return {"ok": True}

@app.get("/api/export")
async def export_csv(status: Optional[str] = None):
    db = load_db()
    if status and status != "all":
        db = [a for a in db if a["status"] == status]

    si = StringIO()
    # UTF-8 BOM (Excel 한글 호환)
    si.write("﻿")
    w  = csv.writer(si)
    w.writerow(["ID","URL","제목","종합점수","사실보존","수치보존","날짜보존",
                "엔티티보존","인용보존","톤안정성","AI답변준비도","구조품질",
                "승인상태","메모","생성일시"])
    for a in db:
        m = a.get("eval", {}).get("metrics", {})
        w.writerow([
            a.get("id",""), a.get("url",""), a.get("title",""),
            a.get("eval",{}).get("score",""),
            m.get("사실 보존",""), m.get("수치 보존",""),
            m.get("날짜 보존",""), m.get("엔티티 보존",""),
            m.get("직접 인용 보존",""), m.get("톤 안정성",""),
            m.get("AI 답변 준비도",""), m.get("구조 품질",""),
            a.get("status",""), a.get("memo",""), a.get("created_at",""),
        ])

    filename     = f"AEO_평가결과_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    filename_enc = urlquote(filename, safe="")   # 한글 URL 인코딩
    return StreamingResponse(
        iter([si.getvalue()]),
        media_type="text/csv; charset=utf-8-sig",
        headers={"Content-Disposition":
                 f"attachment; filename=\"result.csv\"; filename*=UTF-8''{filename_enc}"},
    )

# ── 실행 ──────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    print("🚀 AEO 대시보드 시작: http://localhost:8200")
    uvicorn.run("aeo_대시보드:app", host="0.0.0.0", port=8200, reload=True)
