"""
세계일보 기사 에이전트 — 서울경제 AI-LINK 벤치마킹 기반
기능: ① 기사 제목 추천  ② 보도자료 기사화  ③ 교열  ④ AEO 변환
"""
import os, re, json, asyncio, io
from typing import AsyncGenerator
from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import anthropic
import httpx
from bs4 import BeautifulSoup

try:
    import pdfplumber as _pdfplumber
    HAS_PDF = True
except ImportError:
    HAS_PDF = False

try:
    import docx as _docx
    HAS_DOCX = True
except ImportError:
    HAS_DOCX = False

# ── 환경변수 ──────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# 기능별 모델 선택 (NOVA 참고: 한국어 감수성 최우선)
MODEL_SONNET = "claude-sonnet-4-6"   # 제목추천·보도자료 (창의성 필요)
MODEL_HAIKU  = "claude-haiku-4-5-20251001"  # 교열 (속도 우선)

router = APIRouter(prefix="/api/agent", tags=["기사에이전트"])

# ── 요청 모델 ─────────────────────────────────────
class TitleRequest(BaseModel):
    text: str          # 기사 초안 본문
    category: str = "일반"  # 정치·경제·사회·국제·문화·스포츠

class PressReleaseRequest(BaseModel):
    text: str = ""        # 보도자료 텍스트 (직접 입력)
    url:  str = ""        # 보도자료 URL (크롤링)
    length: str = "일반"  # 단신|일반|심층
    memo: str = ""        # 기자 추가 컨텍스트

class ProofreadRequest(BaseModel):
    text: str          # 교열할 기사 초안

class AeoAgentRequest(BaseModel):
    title: str
    body:  str

# ── 시스템 프롬프트 (서울경제 NOVA 5계층 구조 참고) ───

TITLE_SYSTEM = """당신은 세계일보 편집국 20년 경력 베테랑 데스크입니다.
기사 제목 작성의 핵심 원칙을 완벽히 체화하고 있습니다.

【세계일보 제목 작성 6원칙】
1. 핵심 사실을 첫 어절에 배치한다 (검색·AI 인덱싱 최적화)
2. 숫자·데이터가 있으면 반드시 포함한다 (신뢰도↑)
3. 행위자(주체)를 명확히 한다
4. 부정형보다 긍정형을 선호한다
5. 30자 이내를 원칙으로 한다 (모바일 잘림 방지)
6. 낚시성 제목 금지 — 본문 사실에 근거한 제목만

【환각 방지 규칙 — 필수】
- 본문에 없는 수치·인명·기관명을 절대 만들지 않는다
- 추측·과장 표현 금지
- 본문에서 확인된 정보만 제목에 사용

【출력 형식】
반드시 아래 JSON 형식으로만 답한다. 마크다운 코드블록(```) 금지.
{
  "titles": [
    {"title": "제목 텍스트", "type": "스트레이트|해설|단독|기획", "reason": "선택 근거 한 줄"},
    ...
  ],
  "recommended": 0
}
titles 배열에 7~10개 제목을 담는다. recommended는 가장 추천하는 titles 배열 인덱스(0부터)."""

PRESSRELEASE_SYSTEM = """당신은 세계일보 편집국 15년 경력 사회부 기자입니다.
보도자료를 읽고 실제 신문 기사로 변환하는 전문가입니다.

═══════════════════════════════════════
【STEP 1: 뉴스 각도 파악】
보도자료를 읽자마자 가장 먼저 "왜 오늘 이 뉴스가 필요한가?"를 1문장으로 정의한다.
이것이 news_angle이다. 기사 전체가 이 각도를 중심으로 전개된다.

【STEP 2: 기사 작성 원칙】
1. 역피라미드 구조: 핵심 사실(리드) → 배경·맥락 → 부가 정보 순
2. 리드(첫 문장): 누가 무엇을 했는지 30자 이내로, 홍보 문구 없이
3. 출처 표기: "○○에 따르면" / "○○는 밝혔다" / "○○는 설명했다"
4. 보도자료→기사 언어 변환:
   - "당사는 발표했다" → "○○는 밝혔다"
   - "세계 최고·업계 최초" → [팩트체크] 표시 후 인용 처리
   - 과도한 수식어·마케팅 문구 삭제
5. 인용문 처리: 발언 원문을 절대 합치거나 바꾸지 않는다. 문장이 길면 앞뒤를 생략(…)하되 의미 훼손 금지
6. 기사 길이: 단신 400~600자 / 일반 600~1000자
7. 불릿 포인트(▲): 3개 이상 나열할 때만 사용, 최대 4개
8. 뉴스 훅: 행사·출시일 등 시의성 있는 정보는 기사에 반드시 포함

【STEP 3: 팩트체크 항목 추출】
수치 주장·비교·수상·인증 등 외부 검증이 필요한 항목을 모두 추출한다.
각 항목에 대해 기자가 취해야 할 행동(자료 요청/추가 확인)을 구체적으로 명시한다.

【STEP 4: 취재 포인트 제안】
보도자료에 없지만 기사 가치를 높일 추가 취재 방향 3~5개를 제안한다.
예: 시장 규모, 경쟁사 반응, 실사용 고객 인터뷰 등

【환각 방지 — 절대 준수】
- 보도자료에 없는 수치·인명·기관명을 절대 만들지 않는다
- 날짜·직함은 원문 그대로 사용한다
- 불확실하면 "~인 것으로 알려졌다" 처리

═══════════════════════════════════════
【출력 형식 — 반드시 아래 JSON만 반환, 마크다운 코드블록 금지】
{
  "news_angle": "뉴스 각도 한 문장",
  "title_candidates": [
    {"title": "제목", "type": "스트레이트|단독|해설|기획", "reason": "선택 근거 한 줄"}
  ],
  "recommended_title": 0,
  "article": "완성된 기사 본문 (\\n으로 단락 구분)",
  "quotes": [
    {"speaker": "이름 직함", "original": "원문 인용 그대로", "used": true}
  ],
  "factcheck_items": [
    {"claim": "검증 필요 주장", "type": "수치|비교|수상|인증|기타", "action": "기자 취해야 할 행동"}
  ],
  "reporting_tips": ["취재 포인트 1", "취재 포인트 2"],
  "editorial_memo": "편집 시 참고사항 (게재 일정·담당자 연락처 등)",
  "char_count": 0
}
title_candidates는 3개, char_count는 article의 공백 포함 글자 수를 정확히 계산한다."""

PROOFREAD_SYSTEM = """당신은 세계일보 교열부 30년 경력 교열 전문가입니다.

【교열 체크리스트】
1. 맞춤법·띄어쓰기 (한국어 어문 규정 기준)
2. 비문·어색한 표현 수정
3. 중복 표현 제거
4. 사실관계 의심 항목 표시 (수치·날짜·인명 불일치 등)
5. 신문 기사체 부적절 표현 교정:
   - 구어체 → 문어체
   - 외래어·신조어 과다 사용 주의
   - 수동태 과다 사용 교정
6. 기사 흐름·논리 구조 점검

【출력 형식】
반드시 아래 JSON 형식만 반환한다. 마크다운 코드블록(```) 금지.
{
  "summary": "전반적 평가 2~3문장",
  "score": 85,
  "corrections": [
    {
      "type": "맞춤법|어색한표현|사실확인|구조",
      "original": "원문 구절",
      "corrected": "수정안",
      "reason": "수정 이유"
    }
  ],
  "revised_text": "전체 교열 완료 텍스트"
}
score는 0~100 (원문 품질 점수). corrections는 발견된 문제점 목록."""

# ── URL 크롤링 (보도자료 URL 입력 시) ───────────────
async def fetch_pressrelease(url: str) -> str:
    headers = {"User-Agent": "Mozilla/5.0 (compatible; SeGye-Agent/1.0)"}
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        r = await client.get(url, headers=headers)
        r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    for tag in soup.select("script, style, nav, header, footer, .ad"):
        tag.decompose()
    for sel in ["article", "div.content", "div.article", "main", "div#content"]:
        el = soup.select_one(sel)
        if el:
            return el.get_text("\n", strip=True)[:5000]
    paras = [p.get_text(strip=True) for p in soup.find_all("p") if len(p.get_text(strip=True)) > 20]
    return "\n".join(paras[:50])[:5000]

# ── SSE 스트리밍 헬퍼 ─────────────────────────────
async def stream_claude(system: str, user: str, model: str) -> AsyncGenerator[str, None]:
    """Claude 스트리밍 응답을 SSE 이벤트로 변환"""
    if not ANTHROPIC_API_KEY:
        yield "data: {\"error\": \"ANTHROPIC_API_KEY 미설정\"}\n\n"
        return

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    full_text = ""
    try:
        with client.messages.stream(
            model=model,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": user}],
        ) as stream:
            for text in stream.text_stream:
                full_text += text
                chunk = json.dumps({"delta": text}, ensure_ascii=False)
                yield f"data: {chunk}\n\n"
        yield f"data: {{\"done\": true, \"full\": {json.dumps(full_text, ensure_ascii=False)}}}\n\n"
    except Exception as e:
        yield f"data: {{\"error\": {json.dumps(str(e))}}}\n\n"

# ── ① 기사 제목 추천 ──────────────────────────────
@router.post("/title")
async def recommend_title(req: TitleRequest):
    """기사 초안 → 세계일보 스타일 제목 7~10개 추천"""
    if not req.text.strip():
        raise HTTPException(400, "기사 본문을 입력하세요.")
    text = req.text[:3000]
    user_prompt = f"[카테고리: {req.category}]\n\n[기사 초안]\n{text}"

    async def gen():
        async for chunk in stream_claude(TITLE_SYSTEM, user_prompt, MODEL_SONNET):
            yield chunk

    return StreamingResponse(gen(), media_type="text/event-stream",
                              headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

# ── ② 보도자료 기사화 ─────────────────────────────
@router.post("/pressrelease")
async def pressrelease_to_article(req: PressReleaseRequest):
    """보도자료(텍스트 또는 URL) → 세계일보 기사 초안"""
    if req.url.strip():
        try:
            source_text = await fetch_pressrelease(req.url.strip())
        except Exception as e:
            raise HTTPException(400, f"URL 크롤링 실패: {e}")
    elif req.text.strip():
        source_text = req.text[:5000]
    else:
        raise HTTPException(400, "보도자료 텍스트 또는 URL을 입력하세요.")

    length_label = {"단신": "400~600자", "일반": "600~1000자", "심층": "1000~1500자"}.get(req.length, "600~1000자")
    user_prompt = (
        f"아래 보도자료를 세계일보 기사 형식으로 변환해 주세요.\n"
        f"목표 기사 길이: {length_label}\n"
        f"{f'기자 메모(추가 컨텍스트): {req.memo}' if req.memo else ''}\n\n"
        f"[보도자료]\n{source_text}"
    )

    async def gen():
        async for chunk in stream_claude(PRESSRELEASE_SYSTEM, user_prompt, MODEL_SONNET):
            yield chunk

    return StreamingResponse(gen(), media_type="text/event-stream",
                              headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

# ── ② 보도자료 파일 업로드 (텍스트 추출) ──────────────
@router.post("/pressrelease/upload")
async def extract_pressrelease_file(file: UploadFile = File(...)):
    """파일(PDF/DOCX/TXT) → 텍스트 추출 후 JSON 반환"""
    filename = file.filename or "파일"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    content = await file.read()

    try:
        if ext in ("txt", "md", "text", "csv"):
            text = content.decode("utf-8-sig", errors="replace")
        elif ext == "pdf":
            if not HAS_PDF:
                raise HTTPException(400, "PDF 라이브러리(pdfplumber)가 설치되지 않았습니다.")
            with _pdfplumber.open(io.BytesIO(content)) as pdf:
                pages = [p.extract_text() or "" for p in pdf.pages]
            text = "\n\n".join(p for p in pages if p.strip())
        elif ext == "docx":
            if not HAS_DOCX:
                raise HTTPException(400, "DOCX 라이브러리(python-docx)가 설치되지 않았습니다. pip install python-docx")
            doc = _docx.Document(io.BytesIO(content))
            text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        elif ext == "hwp":
            raise HTTPException(400, "HWP 형식은 지원하지 않습니다. PDF 또는 DOCX로 변환 후 업로드하세요.")
        else:
            try:
                text = content.decode("utf-8-sig", errors="replace")
            except Exception:
                raise HTTPException(400, f"지원하지 않는 파일 형식입니다: .{ext or '알 수 없음'}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"파일 처리 중 오류: {e}")

    text = text.strip()
    if not text:
        raise HTTPException(400, "파일에서 텍스트를 추출할 수 없습니다.")

    return {"text": text[:8000], "filename": filename, "char_count": len(text)}

# ── ③ 교열 ──────────────────────────────────────
@router.post("/proofread")
async def proofread(req: ProofreadRequest):
    """기사 초안 교열 — 맞춤법·표현·사실관계 검토"""
    if not req.text.strip():
        raise HTTPException(400, "교열할 기사를 입력하세요.")
    text = req.text[:4000]
    user_prompt = f"아래 기사를 교열해 주세요.\n\n[기사]\n{text}"

    async def gen():
        async for chunk in stream_claude(PROOFREAD_SYSTEM, user_prompt, MODEL_HAIKU):
            yield chunk

    return StreamingResponse(gen(), media_type="text/event-stream",
                              headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

# ── ④ AEO 변환 (에이전트 강화판) ─────────────────
AEO_AGENT_SYSTEM = """당신은 세계일보 디지털전략팀 AEO(Answer Engine Optimization) 전문가입니다.
ChatGPT·Perplexity·Claude 등 AI 검색엔진에서 세계일보 기사가 최우선 인용되도록
기사를 최적화합니다.

【AEO 변환 원칙 — GEO-16 표준】
1. direct_answer: 핵심 질문에 직접 답하는 40~60어절 문단 (AI가 인용하는 핵심 블록)
2. key_facts: 수치·날짜·인명 포함 핵심 사실 5~7개
3. faq: 독자가 검색할 법한 질문 4~6개 + 답변 (Q&A 형식)
4. structured_body: 의문형 헤딩(##)으로 재구성한 본문
5. json_ld: Schema.org NewsArticle 구조화 데이터

【사실 보존 철칙】
- 원문의 모든 수치·날짜·인명·인용을 변경하거나 생략하지 않는다
- 원문에 없는 정보를 추가하지 않는다

출력은 반드시 JSON 형식만. 마크다운 코드블록 금지.
{
  "direct_answer": "40~60어절 직접 답변",
  "key_facts": ["사실1", "사실2", ...],
  "faq": [{"q": "질문?", "a": "답변"}],
  "structured_body": "## 의문형 헤딩\\n\\n내용",
  "json_ld": {
    "@context": "https://schema.org",
    "@type": "NewsArticle",
    "headline": "원문 제목 그대로",
    "description": "40~60자 요약",
    "datePublished": "YYYY-MM-DD",
    "author": {"@type": "Person", "name": "기자명"},
    "publisher": {"@type": "Organization", "name": "세계일보", "url": "https://www.segye.com"},
    "about": [{"@type": "Thing", "name": "토픽"}],
    "mentions": [{"@type": "Person", "name": "인물"}]
  }
}"""

@router.post("/aeo")
async def aeo_agent(req: AeoAgentRequest):
    """기사 → AEO 최적화 (AI 검색엔진 인용 최적화)"""
    if not req.body.strip():
        raise HTTPException(400, "기사 본문을 입력하세요.")
    user_prompt = f"제목: {req.title}\n\n본문:\n{req.body[:4000]}"

    async def gen():
        async for chunk in stream_claude(AEO_AGENT_SYSTEM, user_prompt, MODEL_SONNET):
            yield chunk

    return StreamingResponse(gen(), media_type="text/event-stream",
                              headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

# ── 헬스체크 ──────────────────────────────────────
@router.get("/health")
async def health():
    return {"status": "ok", "features": ["title", "pressrelease", "proofread", "aeo"]}
