import os
import uuid
import json
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from typing import List, Optional

# ──────────────────────────────────────────────
# 환경변수 로드
# ──────────────────────────────────────────────
# 1순위: ~/.gemini/triz.env  (iCloud 동기화 제외 안전 경로)
# 2순위: 프로젝트 폴더의 .env (개발 편의 폴백)
_ENV_PATH = os.path.expanduser("~/.gemini/triz.env")
if os.path.isfile(_ENV_PATH):
    load_dotenv(_ENV_PATH, override=True)
else:
    load_dotenv(override=True)

# ──────────────────────────────────────────────
# 초기화
# ──────────────────────────────────────────────
app = FastAPI(title="TRIZ/ASIT 대시보드")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "") or os.environ.get("GOOGLE_API_KEY", "")
GEMINI_MODEL   = "gemini-2.5-flash"  # 최저가. 필요 시 "gemini-2.5-pro" 로 교체 가능

# ── Gemini 백엔드 초기화 ──
client = None
AI_BACKEND = "none"   # "gemini" | "none"

if GEMINI_API_KEY:
    try:
        from google import genai as _genai
        from google.genai import types as _gtypes
        client = _genai.Client(api_key=GEMINI_API_KEY)
        AI_BACKEND = "gemini"
        print(f"✅ Gemini 백엔드 사용: {GEMINI_MODEL}")
    except Exception as e:
        print(f"⚠️  Gemini 초기화 실패: {e}")
else:
    print("❌ GEMINI_API_KEY가 설정되지 않았습니다.")
    print(f"   해결: {_ENV_PATH} 파일을 열어 GEMINI_API_KEY=AIza... 한 줄을 채워 넣으세요.")
    print("   키 발급: https://aistudio.google.com/  (Get API key)")

print(f"🤖 AI 백엔드: {AI_BACKEND.upper()}")

executor = ThreadPoolExecutor(max_workers=8)
tasks: dict[str, asyncio.Queue] = {}
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


# ──────────────────────────────────────────────
# 요청 모델
# ──────────────────────────────────────────────
class AnalyzeRequest(BaseModel):
    # 공통
    문제유형: str = "상품서비스"   # "상품서비스" | "조직프로세스"
    문제현상: str
    목표: str
    selected_tools: List[str]
    # 상품/서비스 전용
    매체: str = ""
    상품명: str = ""
    하위요소: str = ""
    상위요소: str = ""
    대상: str = ""
    가치: str = ""
    # 조직/프로세스 전용
    조직명: str = ""
    대상부서: str = ""
    구성원: str = ""
    핵심지표: str = ""
    현재구조: str = ""
    제약조건: str = ""


class ParseProblemRequest(BaseModel):
    problem_text: str


# ──────────────────────────────────────────────
# 유틸리티
# ──────────────────────────────────────────────
def parse_json_safe(text: str) -> dict:
    """Claude 응답을 JSON으로 파싱.
    1차: 마크다운 코드블록 제거 후 json.loads
    2차: 응답 중간의 ```json ... ``` 블록을 정규식으로 추출
    3차: 첫 '{' 부터 마지막 '}' 까지 슬라이스해서 시도
    모두 실패 시 {"raw": text} 폴백.
    """
    import re

    raw = text or ""
    cleaned = raw.strip()

    # 1차: 가장 흔한 패턴 — 응답이 ```json 으로 시작
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        inner = []
        for line in lines[1:]:
            if line.strip() == "```":
                break
            inner.append(line)
        candidate = "\n".join(inner).strip()
        try:
            return json.loads(candidate)
        except Exception:
            pass  # 다음 단계로

    # 2차: 응답 어디든 ```json ... ``` 패턴 추출
    m = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", raw, flags=re.IGNORECASE)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass

    # 3차: 첫 '{' 부터 마지막 '}' 까지 (응답이 잘려 닫는 코드펜스가 없을 때)
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start:end + 1])
        except Exception:
            pass

    # 4차: 잘린 JSON 복구 시도 — max_tokens 초과로 응답이 중간에 끊긴 경우
    # 따옴표·괄호·중괄호 짝을 맞춰 강제로 닫는다
    if start >= 0:
        try:
            return _repair_truncated_json(raw[start:])
        except Exception:
            pass

    # 5차: 그대로 시도
    try:
        return json.loads(cleaned)
    except Exception:
        return {"raw": raw}


def _repair_truncated_json(s: str) -> dict:
    """max_tokens 초과로 끊긴 JSON을 복구한다. 미닫힌 따옴표·배열·객체를 강제로 닫음."""
    import json as _json
    # 따옴표 안인지 추적
    in_str = False
    escape = False
    stack = []  # '{' 또는 '['
    last_complete = 0  # 마지막으로 완전한 JSON 토큰이 끝난 위치
    for i, ch in enumerate(s):
        if escape:
            escape = False
            continue
        if ch == '\\':
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch in '{[':
            stack.append(ch)
        elif ch in '}]':
            if stack and ((ch == '}' and stack[-1] == '{') or (ch == ']' and stack[-1] == '[')):
                stack.pop()
                if not stack:
                    last_complete = i + 1
        elif ch == ',' and stack:
            # 콤마 직전까지가 마지막 안전 지점
            pass
    # 잘린 위치까지 가져와서, 미닫힌 따옴표/괄호 닫기
    truncated = s
    if in_str:
        # 따옴표 안에서 끊김 → 따옴표 닫고 부모 컨테이너의 마지막 콤마 제거
        truncated += '"'
    # 마지막 토큰이 미완성일 수 있으니 가장 가까운 콤마/괄호 닫음 직전까지 자름
    # 단순 처리: 마지막으로 닫힌 } 또는 ] 다음 콤마까지만 사용
    # 미닫힌 스택을 거꾸로 닫음
    for ch in reversed(stack):
        truncated += ('}' if ch == '{' else ']')
    # 시도 1: 그대로 파싱
    try:
        return _json.loads(truncated)
    except Exception:
        pass
    # 시도 2: 마지막 콤마 뒤를 잘라내고 다시 닫음
    # 미닫힌 stack 직전의 콤마 ',' 들 제거
    import re as _re
    cleaned = _re.sub(r',\s*([}\]])', r'\1', truncated)
    try:
        return _json.loads(cleaned)
    except Exception:
        # 시도 3: 가장 마지막으로 완전한 자식 객체까지만 사용
        if last_complete > 0:
            head = s[:last_complete]
            # head로부터 부모 컨테이너 닫음
            in_str2 = False; esc2 = False; st2 = []
            for ch in head:
                if esc2: esc2=False; continue
                if ch=='\\': esc2=True; continue
                if ch=='"': in_str2=not in_str2; continue
                if in_str2: continue
                if ch in '{[': st2.append(ch)
                elif ch in '}]':
                    if st2 and ((ch=='}' and st2[-1]=='{') or (ch==']' and st2[-1]=='[')):
                        st2.pop()
            tail = ''.join(('}' if c=='{' else ']') for c in reversed(st2))
            try:
                return _json.loads(head + tail)
            except Exception:
                pass
        raise


async def call_ai_async(system: str, user: str, max_tokens: int = 8192) -> str:
    """Gemini API 비동기 호출."""
    if AI_BACKEND != "gemini":
        raise RuntimeError(
            "AI 키가 설정되지 않았습니다. "
            f"{_ENV_PATH} 파일에 GEMINI_API_KEY=AIza... 를 추가한 뒤 서버를 재시작하세요."
        )
    return await _call_gemini(system, user, max_tokens)


def _call_gemini_sync(system: str, user: str, max_tokens: int = 8192) -> str:
    """Gemini 2.5 Flash 동기 호출.
    - response_mime_type=json: JSON 전용 모드 (마크다운 코드블록 없음)
    - thinking_budget=0: 구조화 출력 시 thinking 토큰 비활성화 (비용 절감)
    - temperature=0.3: 결정론적 구조화 응답
    - 일시 장애(503, 429)는 5→15→30초 재시도
    """
    if not client:
        raise RuntimeError("Gemini 클라이언트가 초기화되지 않았습니다.")

    import time

    last_err = None
    for attempt, wait in enumerate([0, 5, 15, 30]):
        if wait:
            time.sleep(wait)
        try:
            try:
                thinking_cfg = _gtypes.ThinkingConfig(thinking_budget=0)
                config = _gtypes.GenerateContentConfig(
                    system_instruction=system,
                    response_mime_type="application/json",
                    thinking_config=thinking_cfg,
                    temperature=0.3,
                    max_output_tokens=max_tokens,
                )
            except Exception:
                config = _gtypes.GenerateContentConfig(
                    system_instruction=system,
                    response_mime_type="application/json",
                    temperature=0.3,
                    max_output_tokens=max_tokens,
                )

            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=user,
                config=config,
            )
            return response.text

        except Exception as e:
            err_str = str(e).lower()

            # 인증 오류
            if any(k in err_str for k in ("api_key", "invalid", "401", "unauthenticated", "api key")):
                raise RuntimeError(
                    "API 키가 유효하지 않습니다. "
                    "Google AI Studio(aistudio.google.com)에서 키를 발급해 "
                    f"{_ENV_PATH} 에 GEMINI_API_KEY=AIza... 로 저장하세요."
                ) from e

            # Rate limit / 할당량 초과
            if any(k in err_str for k in ("quota", "resource_exhausted", "429", "rate limit", "too many")):
                if attempt < 3:
                    last_err = e
                    print(f"⏳ Rate limit — {[5,15,30][attempt]}초 후 재시도 ({attempt+2}/4)")
                    continue
                raise RuntimeError("요청 한도(Rate limit)에 도달했습니다. 1~2분 후 다시 시도해 주세요.") from e

            # 서버 과부하 / 일시 장애
            if any(k in err_str for k in ("503", "overloaded", "unavailable", "500", "server error")):
                if attempt < 3:
                    last_err = e
                    print(f"⏳ Gemini 서버 일시 장애 — {[5,15,30][attempt]}초 후 재시도 ({attempt+2}/4)")
                    continue
                raise RuntimeError(f"Gemini 서버 장애. 잠시 후 다시 시도해 주세요: {e}") from e

            raise RuntimeError(f"Gemini API 오류: {e}") from e

    raise RuntimeError(f"AI 호출이 반복적으로 실패했습니다: {last_err}")


async def _call_gemini(system: str, user: str, max_tokens: int = 8192) -> str:
    loop = asyncio.get_running_loop()
    from functools import partial
    return await loop.run_in_executor(executor, partial(_call_gemini_sync, system, user, max_tokens))


# 하위 호환성 유지 (기존 step_* 함수들이 이 이름으로 호출)
async def call_claude_async(system: str, user: str, max_tokens: int = 8192) -> str:
    return await call_ai_async(system, user, max_tokens)


def sse_event(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


# ──────────────────────────────────────────────
# Step 1: 문제 구조화
# ──────────────────────────────────────────────
async def step_problem(req: AnalyzeRequest, queue: asyncio.Queue) -> dict:
    await queue.put(sse_event({
        "type": "progress",
        "step": "problem",
        "status": "start",
        "agent": "마스터",
        "message": "문제 구조화 중...",
    }))

    system = (
        "당신은 TRIZ/ASIT 문제 분석 전문가입니다.\n"
        "아래 문제 정보를 분석하여 반드시 valid JSON만 출력하세요. 다른 텍스트 없이 JSON만.\n\n"
        "# 모순 식별 규칙 (반드시 준수)\n"
        "- 기술 모순: '파라미터 A를 개선하면 파라미터 B가 나빠진다' 형식으로 두 파라미터를 명시\n"
        "- 물리 모순: 'X는 [특성 a]이면서 동시에 [특성 b]여야 한다' 형식으로 하나의 대상에 상반된 요건 명시\n"
        "- IFR: 반드시 '추가 자원·인력·비용 없이' 조건을 포함할 것\n"
    )

    # 문제유형별 컨텍스트 구성
    if req.문제유형 == "조직프로세스":
        context = f"""문제 정보 (조직/프로세스 유형):
- 조직명: {req.조직명}
- 대상 부서: {req.대상부서}
- 구성원: {req.구성원}
- 핵심 지표: {req.핵심지표}
- 현재 구조: {req.현재구조}
- 제약 조건: {req.제약조건}
- 문제현상: {req.문제현상}
- 목표: {req.목표}
- 선택된 도구: {', '.join(req.selected_tools)}"""
    else:
        context = f"""문제 정보 (상품/서비스 유형):
- 매체: {req.매체}
- 상품명: {req.상품명}
- 하위요소: {req.하위요소}
- 상위요소: {req.상위요소}
- 대상: {req.대상}
- 가치: {req.가치}
- 문제현상: {req.문제현상}
- 목표: {req.목표}
- 선택된 도구: {', '.join(req.selected_tools)}"""

    user = f"""{context}

위 문제를 분석하여 아래 형식의 JSON만 출력하세요.

출력 형식 (JSON만):
{{
  "problem_summary": "핵심 문제 요약 (수치 포함, 2문장)",
  "technical_contradiction": "기술 모순: '[파라미터A]를 개선하면 → [파라미터B]가 악화된다' 형식으로 구체적 파라미터 명시",
  "physical_contradiction": "물리 모순: '[대상X]는 [특성A]이면서 동시에 [특성B]여야 한다' 형식",
  "IFR": {{
    "IFR1": "추가 자원 없이 시스템 스스로 해결하는 이상 상태",
    "IFR2": "비용·인력 추가 없이 문제가 소멸하는 이상 상태",
    "IFR3": "문제 원인이 처음부터 존재하지 않는 이상 상태"
  }},
  "recommended_tools": ["추천 도구1", "추천 도구2", "추천 도구3"],
  "problem_type": "{req.문제유형}"
}}"""

    raw = await call_claude_async(system, user)
    result = parse_json_safe(raw)

    await queue.put(sse_event({
        "type": "result",
        "step": "problem",
        "data": result,
    }))
    return result


# ──────────────────────────────────────────────
# Step 2: TRIZ 분석
# ──────────────────────────────────────────────
async def step_triz(req: AnalyzeRequest, problem: dict, queue: asyncio.Queue) -> dict:
    await queue.put(sse_event({
        "type": "progress",
        "step": "triz",
        "status": "start",
        "agent": "마스터",
        "message": "TRIZ 발명원리 분석 중...",
    }))

    system = "당신은 TRIZ 발명원리 전문가입니다. 반드시 valid JSON만 출력하세요."
    user = f"""다음 문제에 TRIZ 발명원리와 분리원리를 적용하여 최소 8개 아이디어를 도출하세요.

문제 요약: {problem.get('problem_summary', req.문제현상)}
기술 모순: {problem.get('technical_contradiction', '')}
물리 모순: {problem.get('physical_contradiction', '')}
상품명: {req.상품명}
대상: {req.대상}
하위요소: {req.하위요소}

적용할 원리: 분할, 추출, 국소품질, 피드백, 역발상, 자기서비스, 사전조치, 역동성, 분리원리(시간/공간/조건/전체-부분)

출력 형식 (JSON만, 최소 8개 아이디어):
{{
  "ideas": [
    {{
      "id": 1,
      "name": "아이디어명",
      "description": "구체적 설명 (2-3문장)",
      "principle": "적용한 원리명",
      "pros": ["장점1", "장점2"],
      "cons": ["단점1"],
      "initial_score": 7
    }}
  ]
}}"""

    raw = await call_claude_async(system, user)
    result = parse_json_safe(raw)

    await queue.put(sse_event({
        "type": "result",
        "step": "triz",
        "data": result,
    }))
    return result


# ──────────────────────────────────────────────
# Step 3: ASIT 분석
# ──────────────────────────────────────────────
async def step_asit(req: AnalyzeRequest, problem: dict, queue: asyncio.Queue) -> dict:
    await queue.put(sse_event({
        "type": "progress",
        "step": "asit",
        "status": "start",
        "agent": "마스터",
        "message": "ASIT 5가지 도구 분석 중...",
    }))

    system = (
        "당신은 ASIT(Advanced Systematic Inventive Thinking) 전문가입니다. 반드시 valid JSON만 출력하세요.\n\n"
        "# ASIT 핵심 원칙 — 반드시 준수\n"
        "## 폐쇄 세계 조건(Closed World Condition)\n"
        "모든 아이디어는 아래 '현재 구성 요소 목록'에 이미 존재하는 요소만 사용해야 합니다.\n"
        "외부에서 새로운 유형의 자원·기술·인력을 도입하는 아이디어는 ASIT 아이디어가 아닙니다.\n"
        "폐쇄 세계 조건을 위반한 아이디어는 생성하지 마세요.\n\n"
        "## ASIT 5가지 도구 정의 (Horowitz 1999)\n"
        "- 제거(Subtraction): 구성 요소를 제거했을 때 나머지로 동일하거나 더 나은 기능 달성\n"
        "- 복제(Multiplication): 기존 요소를 변형된 복사본으로 추가 (단순 추가 아님, 역할 차별화 필수)\n"
        "- 분할(Division): 기존 요소를 물리적·기능적·시간적으로 나눠 재배치\n"
        "- 기능통합(Task Unification): 기존 요소에 새로운 역할을 추가로 부여\n"
        "- 속성의존성(Attribute Dependency): 두 속성 사이에 새로운 상관관계를 만들거나 제거\n"
    )

    # 문제유형별 구성 요소 컨텍스트
    if req.문제유형 == "조직프로세스":
        elements_label = "현재 조직 구성 요소"
        elements_value = req.현재구조 or f"{req.대상부서}, {req.구성원}"
        subject_label = "조직명"
        subject_value = req.조직명 or req.매체
        target_label = "핵심 지표"
        target_value = req.핵심지표 or req.가치
    else:
        elements_label = "현재 하위요소 목록"
        elements_value = req.하위요소
        subject_label = "상품명"
        subject_value = req.상품명
        target_label = "대상"
        target_value = req.대상

    user = f"""다음 문제에 ASIT 5가지 도구를 적용하여 최소 8개 아이디어를 도출하세요.

문제 요약: {problem.get('problem_summary', req.문제현상)}
{subject_label}: {subject_value}
{elements_label} (폐쇄 세계 — 이 요소들만 사용 가능): {elements_value}
{target_label}: {target_value}

⚠️ 폐쇄 세계 조건 체크: 각 아이디어 생성 후 "{elements_value}" 목록 안의 요소만 사용했는지 확인하세요.
외부 자원을 도입했다면 해당 아이디어는 제거하고 다시 생성하세요.

출력 형식 (JSON만, 최소 8개 아이디어):
{{
  "ideas": [
    {{
      "id": 1,
      "name": "아이디어명",
      "description": "구체적 설명 (2-3문장)",
      "tool": "제거/복제/분할/기능통합/속성의존성",
      "target_element": "적용 대상 구성요소 (폐쇄 세계 내 요소명 명시)",
      "closed_world_check": "사용한 요소가 모두 기존 구성요소인가? YES/NO",
      "pros": ["장점1"],
      "cons": ["단점1"],
      "initial_score": 8
    }}
  ]
}}"""

    raw = await call_claude_async(system, user)
    result = parse_json_safe(raw)

    await queue.put(sse_event({
        "type": "result",
        "step": "asit",
        "data": result,
    }))
    return result


# ──────────────────────────────────────────────
# Step 4: 4명 에이전트 동시 평가
# ──────────────────────────────────────────────
# 평가 루브릭 — 주관성 제거를 위한 객관 기준 (Altshuller 발명수준 + CAT 기반)
EVAL_RUBRIC = """
# 채점 루브릭 (반드시 아래 기준으로 채점할 것)

## 신규성 (Novelty) — 발명수준 기준
- 9~10점: 국내외 동종 업계 어디서도 시도된 사례 없음 (Altshuller Level 4~5)
- 7~8점: 해외에는 사례 있으나 국내 동종 업계 미시도 (Level 3)
- 5~6점: 국내 타 업계에서 시도됐으나 해당 분야 미적용
- 3~4점: 동종 업계에서 부분적으로 시도됨
- 1~2점: 이미 존재하는 방법의 재서술 (Level 1~2)

## 실현가능성 (Feasibility) — 체크리스트 기준
4항목 중 충족 수로 점수 배정 (각 2.5점):
① 현재 보유 인력으로 실행 가능한가?
② 추가 예산 없이 기존 예산 내 실행 가능한가?
③ 현재 기술·인프라로 구현 가능한가?
④ 6개월 내 MVP(최소 실행안) 출시 가능한가?

## 가치 기여도 (Value) — 인과관계 기준
- 9~10점: 목표 지표를 직접 개선하는 명확한 인과관계 + 수치 예측 가능
- 7~8점: 간접적이지만 논리적 인과관계 있음
- 5~6점: 긍정적 영향이 예상되나 인과관계 불명확
- 1~4점: 목표 지표와 연관성 낮음
"""

AGENT_CONFIGS = {
    "기획자": (
        "당신은 15년 경력 미디어 콘텐츠 기획 전문가입니다.\n"
        "<role>전략적 방향성, 브랜드 일관성, 실행 가능성, 인력/예산 관점에서 평가합니다.</role>\n"
        "<output_format>반드시 valid JSON만 출력. 마크다운 코드블록 없이.</output_format>\n"
        + EVAL_RUBRIC
    ),
    "컨설턴트": (
        "당신은 글로벌 전략 컨설팅 10년 경력 미디어 전문가입니다.\n"
        "<role>시장 기회, 경쟁 우위, ROI, 리스크 관점에서 평가합니다.</role>\n"
        "<output_format>반드시 valid JSON만 출력. 마크다운 코드블록 없이.</output_format>\n"
        + EVAL_RUBRIC
    ),
    "엔지니어": (
        "당신은 디지털 미디어 플랫폼 시니어 엔지니어입니다.\n"
        "<role>기술 구현 가능성, 개발 복잡도, 확장성 관점에서 평가합니다.</role>\n"
        "<output_format>반드시 valid JSON만 출력. 마크다운 코드블록 없이.</output_format>\n"
        + EVAL_RUBRIC
    ),
    "고객": (
        "당신은 35세 직장인 미디어 서비스 이용자입니다.\n"
        "<role>실사용 가치, 편의성, 체감 효과, 차별성 관점에서 평가합니다.</role>\n"
        "<output_format>반드시 valid JSON만 출력. 마크다운 코드블록 없이.</output_format>\n"
        + EVAL_RUBRIC
    ),
}

# ──────────────────────────────────────────────
# 3가지 선별 TRIZ/ASIT 프레임워크 에이전트
# ──────────────────────────────────────────────
# 선정 이유:
#  1. IFR — 방향성의 극한값 설정. 자원 없이 스스로 해결하는 이상 상태를 목표로 삼는 TRIZ의 핵심 철학
#  2. ASIT 폐쇄 세계 — 기존 요소만으로 혁신. 외부 자원 없이 내부 재조합으로 최대 가치 창출
#  3. 분리원리 — 물리 모순 해소. 두 상반된 요구를 시간·공간·조건으로 분리해 동시 충족
FRAMEWORK_AGENTS = {
    "IFR관점": (
        "당신은 TRIZ IFR(이상적 최종 결과, Ideal Final Result) 전문가입니다.\n"
        "<role>\n"
        "이상도(Ideality) 공식을 기반으로 아이디어가 IFR에 얼마나 근접했는지 평가합니다.\n"
        "이상도 = Σ 유용한 기능 / (Σ 비용 + Σ 해로운 기능)\n"
        "</role>\n"
        "<principles>\n"
        "IFR-1: 시스템 스스로 해결 — 외부 행위자·추가 자원 없이 작동하는가\n"
        "IFR-2: 비용 없이 문제 소멸 — 기존 자원만으로 해결되는가\n"
        "IFR-3: 문제 원인이 처음부터 없음 — 시스템 재설계로 문제 자체를 제거하는가\n"
        "</principles>\n"
        "<output_format>반드시 valid JSON만 출력. 마크다운 코드블록 없이.</output_format>"
    ),
    "ASIT폐쇄세계": (
        "당신은 ASIT 폐쇄 세계 조건(Closed World Condition) 전문가입니다.\n"
        "<role>\n"
        "Roni Horowitz(1999) 원저 ASIT 5도구 관점에서 아이디어를 평가합니다.\n"
        "핵심 제약: 해결책은 문제 상황에 이미 존재하는 요소만 사용해야 합니다.\n"
        "</role>\n"
        "<tools>\n"
        "제거(Subtraction): 구성 요소를 빼고 나머지로 기능 유지 또는 향상\n"
        "복제(Multiplication): 기존 요소를 변형된 복사본으로 추가 (역할 차별화 필수)\n"
        "분할(Division): 기존 요소를 물리적·기능적·시간적으로 나눠 재배치\n"
        "기능통합(Task Unification): 기존 요소에 새로운 역할을 추가로 부여\n"
        "대칭파괴(Breaking Symmetry): 균일 배분된 속성을 의도적으로 비대칭화\n"
        "</tools>\n"
        "<output_format>반드시 valid JSON만 출력. 마크다운 코드블록 없이.</output_format>"
    ),
    "분리원리": (
        "당신은 TRIZ 분리원리(Separation Principles) 전문가입니다.\n"
        "<role>\n"
        "물리 모순 — 'X는 [특성 A]이면서 동시에 [특성 B]여야 한다' —을\n"
        "4가지 분리원리로 해소하는 관점에서 아이디어를 평가합니다.\n"
        "</role>\n"
        "<principles>\n"
        "1. 시간 분리: 특성 A는 시간 t1에, 특성 B는 시간 t2에 적용\n"
        "2. 공간 분리: 특성 A는 공간 S1에, 특성 B는 공간 S2에 적용\n"
        "3. 조건 분리: 조건 C1에서 A 적용, 조건 C2에서 B 적용\n"
        "4. 전체/부분 분리: 전체 시스템은 A 속성, 세부 구성요소는 B 속성\n"
        "</principles>\n"
        "<output_format>반드시 valid JSON만 출력. 마크다운 코드블록 없이.</output_format>"
    ),
}


async def evaluate_single_agent(
    agent_name: str,
    system: str,
    all_ideas: list,
    req: AnalyzeRequest,
    queue: asyncio.Queue,
) -> tuple[str, dict]:
    await queue.put(sse_event({
        "type": "progress",
        "step": "evaluation",
        "status": "agent_start",
        "agent": agent_name,
        "message": f"{agent_name} 관점 평가 중...",
    }))

    ideas_text = json.dumps(all_ideas, ensure_ascii=False, indent=2)

    # 문제유형별 컨텍스트
    if req.문제유형 == "조직프로세스":
        ctx = f"조직: {req.조직명}\n대상부서: {req.대상부서}\n핵심지표: {req.핵심지표}"
    else:
        ctx = f"상품: {req.상품명}\n대상: {req.대상}\n가치지표: {req.가치}"

    user = f"""다음 아이디어 목록을 {agent_name} 관점에서 평가하세요. 위의 채점 루브릭을 반드시 적용하세요.

{ctx}
문제: {req.문제현상}
목표: {req.목표}

아이디어 목록:
{ideas_text}

채점 방법:
- score: 신규성(40%) + 실현가능성(35%) + 가치기여도(25%) 가중평균, 10점 만점
- novelty_level: "L1~L2(기존개선)" / "L3(타분야도입)" / "L4(새시스템)" 중 선택
- feasibility_check: 4개 체크리스트 중 충족 수 (0~4)
- 점수는 아이디어별로 독립적으로 채점하세요. 모든 아이디어에 8~9점을 주지 마세요.

출력 형식 (JSON만):
{{
  "agent": "{agent_name}",
  "overall_assessment": "전체 평가 (2-3문장, 구체적 수치 포함)",
  "evaluations": [
    {{
      "id": 1,
      "name": "아이디어명",
      "score": 8,
      "novelty_level": "L3(타분야도입)",
      "feasibility_check": 3,
      "comment": "루브릭 기반 구체적 채점 이유",
      "pros": ["장점"],
      "cons": ["단점"]
    }}
  ],
  "top3_ids": [3, 1, 7],
  "key_concerns": ["우려사항1", "우려사항2"]
}}"""

    # 평가 응답은 16개 아이디어 평가라 길어 max_tokens=8192 필요. 순차 실행 + 5초 sleep으로 분당 한도 회피
    raw = await call_claude_async(system, user, max_tokens=8192)
    result = parse_json_safe(raw)
    return agent_name, result


async def step_evaluation(
    req: AnalyzeRequest,
    triz_result: dict,
    asit_result: dict,
    queue: asyncio.Queue,
) -> dict:
    await queue.put(sse_event({
        "type": "progress",
        "step": "evaluation",
        "status": "start",
        "agent": "마스터",
        "message": "4명 에이전트 동시 평가 시작...",
    }))

    # 전체 아이디어 합산
    triz_ideas = triz_result.get("ideas", [])
    asit_ideas = asit_result.get("ideas", [])

    # ASIT 아이디어 id 충돌 방지 (TRIZ 마지막 id 이후로 재번호)
    offset = len(triz_ideas)
    merged_ideas = []
    for idea in triz_ideas:
        merged_ideas.append({**idea, "source": "TRIZ"})
    for i, idea in enumerate(asit_ideas):
        merged_ideas.append({**idea, "id": offset + i + 1, "source": "ASIT"})

    # 순차 실행: Haiku의 분당 출력 토큰 한도(10K)를 피하기 위해 한 명씩 실행하고 5초 간격
    # (4명 × 4096 = 16384 토큰을 한 번에 보내면 rate limit 발생)
    evaluation_data = {}
    agents_list = list(AGENT_CONFIGS.items())
    for i, (name, system_prompt) in enumerate(agents_list):
        try:
            agent_name, agent_result = await evaluate_single_agent(name, system_prompt, merged_ideas, req, queue)
            evaluation_data[agent_name] = agent_result
        except Exception as e:
            evaluation_data[name] = {"raw": "", "error": str(e)}
            print(f"⚠️  {name} 평가 실패: {e}")
        # 마지막 호출 빼고 다음 호출 전 5초 대기 (분당 토큰 한도 안전 마진)
        if i < len(agents_list) - 1:
            await asyncio.sleep(5)

    await queue.put(sse_event({
        "type": "result",
        "step": "evaluation",
        "data": evaluation_data,
    }))
    return evaluation_data, merged_ideas


# ──────────────────────────────────────────────
# Step 5: 최종 종합
# ──────────────────────────────────────────────
async def step_synthesis(
    req: AnalyzeRequest,
    evaluation_data: dict,
    merged_ideas: list,
    queue: asyncio.Queue,
) -> dict:
    await queue.put(sse_event({
        "type": "progress",
        "step": "synthesis",
        "status": "start",
        "agent": "마스터",
        "message": "최종 종합 분석 중...",
    }))

    system = "당신은 TRIZ/ASIT 분석 종합 전문가입니다. 반드시 valid JSON만 출력하세요."
    eval_text = json.dumps(evaluation_data, ensure_ascii=False, indent=2)
    ideas_text = json.dumps(merged_ideas, ensure_ascii=False, indent=2)

    user = f"""4명 에이전트 평가 결과와 전체 아이디어를 종합하여 최종 Top 10을 선정하고 인사이트를 도출하세요.

상품: {req.상품명}
대상: {req.대상}
목표: {req.목표}

전체 아이디어:
{ideas_text}

에이전트 평가:
{eval_text}

출력 형식 (JSON만):
{{
  "final_top10": [
    {{
      "rank": 1,
      "id": 5,
      "name": "아이디어명",
      "description": "설명",
      "avg_score": 8.5,
      "scores": {{"기획자": 9, "컨설턴트": 8, "엔지니어": 7, "고객": 9}},
      "consensus": "높음/보통/낮음",
      "implementation": "단기(1-3개월)/중기(3-6개월)/장기(6개월+)",
      "impact": "높음/보통/낮음",
      "source": "TRIZ/ASIT"
    }}
  ],
  "quick_wins": [1, 3],
  "insights": ["핵심 통찰1", "핵심 통찰2", "핵심 통찰3"],
  "next_steps": [
    {{"step": 1, "action": "실행 사항", "timeline": "1개월", "owner": "담당 부서"}}
  ]
}}"""

    # 종합 응답은 Top10 + 인사이트 + next_steps + roadmap 등 매우 김 → 16000 토큰 (Haiku 4.5는 32K 지원)
    raw = await call_claude_async(system, user, max_tokens=16000)
    result = parse_json_safe(raw)

    await queue.put(sse_event({
        "type": "result",
        "step": "synthesis",
        "data": result,
    }))
    return result


# ──────────────────────────────────────────────
# 분석 파이프라인 (백그라운드 실행)
# ──────────────────────────────────────────────
async def run_analysis_pipeline(task_id: str, req: AnalyzeRequest):
    queue = tasks[task_id]
    try:
        # Step 1
        problem = await step_problem(req, queue)

        # Step 2
        triz_result = await step_triz(req, problem, queue)

        # Step 3
        asit_result = await step_asit(req, problem, queue)

        # Step 4
        evaluation_data, merged_ideas = await step_evaluation(req, triz_result, asit_result, queue)

        # Step 5
        await step_synthesis(req, evaluation_data, merged_ideas, queue)

        # 완료
        await queue.put(sse_event({
            "type": "complete",
            "message": "분석 완료",
        }))

    except Exception as e:
        await queue.put(sse_event({
            "type": "error",
            "message": str(e),
        }))
    finally:
        # 스트림 종료 신호
        await queue.put(None)


# ──────────────────────────────────────────────
# 엔드포인트
# ──────────────────────────────────────────────
@app.get("/")
async def root():
    html_path = os.path.join(BASE_DIR, "TRIZ_ASIT_대시보드.html")
    if not os.path.exists(html_path):
        raise HTTPException(status_code=404, detail="TRIZ_ASIT_대시보드.html 파일을 찾을 수 없습니다.")
    return FileResponse(html_path, media_type="text/html")


# ──────────────────────────────────────────────
# 문제 파싱 (자유 텍스트 → 구조화 필드)
# ──────────────────────────────────────────────
@app.post("/api/parse-problem")
async def parse_problem_endpoint(req: ParseProblemRequest):
    """자유 입력 문제를 TRIZ/ASIT 구조 필드로 추출. 문제 유형(상품서비스/조직프로세스) 자동 감지."""
    system = (
        "당신은 문제 분석 전문가입니다. "
        "사용자가 자유롭게 서술한 문제를 TRIZ/ASIT 분석에 필요한 구조화된 형식으로 추출하세요. "
        "정보가 명시되지 않은 필드는 문맥에서 합리적으로 추론하세요. "
        "반드시 valid JSON만 출력하세요. 마크다운 코드블록 없이, 순수 JSON만."
    )
    user = f"""다음 문제 설명을 읽고 아래 JSON 형식으로 구조화하세요.

문제 설명:
{req.problem_text}

[판단 기준]
- 문제가 '제품·서비스·콘텐츠 자체'의 개선이라면 → 문제유형: "상품서비스"
- 문제가 '조직 구조·인력 배분·업무 프로세스'의 개선이라면 → 문제유형: "조직프로세스"

출력 형식 (JSON만, 두 유형의 필드 모두 포함, 해당 없는 필드는 빈 문자열):
{{
  "문제유형": "상품서비스 또는 조직프로세스",
  "문제현상": "현재 발생하고 있는 구체적 문제 (수치 포함, 1~2문장)",
  "목표": "달성하고자 하는 구체적 목표 (수치·기간 포함, 1~2문장)",
  "매체": "(상품서비스) 미디어·서비스 유형",
  "상품명": "(상품서비스) 구체적 상품·서비스명",
  "하위요소": "(상품서비스) 서비스의 세부 구성 요소들 (콤마 구분)",
  "상위요소": "(상품서비스) 속한 상위 카테고리·생태계",
  "대상": "(상품서비스) 주요 타겟 고객",
  "가치": "(상품서비스) 핵심 성과 지표 KPI",
  "조직명": "(조직프로세스) 조직·회사명",
  "대상부서": "(조직프로세스) 해당 부서·팀명",
  "구성원": "(조직프로세스) 구성원 현황 (인원 수, 역할)",
  "핵심지표": "(조직프로세스) 핵심 성과 지표",
  "현재구조": "(조직프로세스) 현재 조직 구조 또는 업무 프로세스",
  "제약조건": "(조직프로세스) 변경 시 지켜야 할 제약 사항"
}}"""

    try:
        raw = await call_claude_async(system, user)
        result = parse_json_safe(raw)
        if "raw" in result:
            raise HTTPException(status_code=500, detail="JSON 파싱 실패: " + result["raw"][:200])
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/status")
async def get_status():
    """AI 백엔드 상태 확인."""
    return {
        "backend": AI_BACKEND,
        "model": GEMINI_MODEL if AI_BACKEND == "gemini" else None,
        "ready": AI_BACKEND == "gemini",
    }


@app.post("/api/analyze")
async def analyze(req: AnalyzeRequest, background_tasks: BackgroundTasks):
    task_id = str(uuid.uuid4())
    tasks[task_id] = asyncio.Queue()
    background_tasks.add_task(run_analysis_pipeline, task_id, req)
    return {"task_id": task_id}


@app.get("/api/stream/{task_id}")
async def stream(task_id: str):
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="task_id를 찾을 수 없습니다.")

    queue = tasks[task_id]

    async def event_generator():
        try:
            while True:
                try:
                    # 15초 대기 후 keepalive 핑 전송 (긴 단계 사이 연결 유지)
                    item = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"  # SSE 코멘트 (클라이언트에서 무시됨)
                    continue
                if item is None:
                    # 스트림 종료
                    break
                yield item
        finally:
            # 클린업
            tasks.pop(task_id, None)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ──────────────────────────────────────────────
# 프레임워크 피드백 (3가지 TRIZ/ASIT 관점 에이전트)
# ──────────────────────────────────────────────
class FrameworkFeedbackRequest(BaseModel):
    idea_name: str
    idea_description: str
    problem_summary: str
    physical_contradiction: str = ""
    technical_contradiction: str = ""
    existing_elements: str = ""  # ASIT 폐쇄 세계 조건용 (쉼표 구분 목록)


async def _run_framework_agent(framework_name: str, system: str, req: FrameworkFeedbackRequest) -> dict:
    """단일 프레임워크 에이전트 실행. 각 프레임워크 전용 user 프롬프트를 생성해 호출."""
    if framework_name == "IFR관점":
        user = f"""아이디어를 IFR(이상적 최종 결과) 관점에서 평가하세요.

<context>
문제 요약: {req.problem_summary}
기술 모순: {req.technical_contradiction}
물리 모순: {req.physical_contradiction}
</context>

<idea>
아이디어명: {req.idea_name}
설명: {req.idea_description}
</idea>

<task>
다음 기준으로 평가하고 JSON을 출력하세요:
1. 이상도 점수(0~10): 유용한 기능 증가 vs 비용·부작용 감소 비율
2. IFR 정렬 단계: IFR-1(자기해결) / IFR-2(무비용) / IFR-3(원인제거) 중 가장 가까운 단계
3. 개선 방향: 이 아이디어를 더 IFR에 가깝게 만드는 구체적 제안 (기존 요소 활용)
</task>

출력 JSON:
{{
  "framework": "IFR관점",
  "ideality_score": 7,
  "ifr_alignment": "IFR-2",
  "ifr_analysis": "이 아이디어가 IFR에 얼마나 가까운지 2~3문장 분석",
  "useful_functions": ["증가하는 유용 기능1", "유용 기능2"],
  "harmful_or_costs": ["발생하는 비용이나 부작용1"],
  "improvement_toward_ifr": "IFR 방향으로 더 나아가는 구체적 개선 제안",
  "summary": "IFR 관점 한 줄 평가"
}}"""

    elif framework_name == "ASIT폐쇄세계":
        user = f"""아이디어를 ASIT 폐쇄 세계 관점에서 평가하세요.

<context>
문제 요약: {req.problem_summary}
기존 구성 요소 (폐쇄 세계 — 이 목록만 사용 가능): {req.existing_elements or "입력된 하위요소 참조"}
</context>

<idea>
아이디어명: {req.idea_name}
설명: {req.idea_description}
</idea>

<task>
다음 기준으로 평가하고 JSON을 출력하세요:
1. 폐쇄 세계 준수: 기존 요소만 사용했는가 (true/false)
2. ASIT 도구 매핑: 5도구(제거/복제/분할/기능통합/대칭파괴) 중 해당 도구
3. 폐쇄 세계 위반 시: 외부 자원을 제거하고 동일 목적을 내부 요소로 달성하는 대안
</task>

출력 JSON:
{{
  "framework": "ASIT폐쇄세계",
  "closed_world_compliant": true,
  "asit_tool": "기능통합",
  "tool_justification": "왜 이 도구에 해당하는지 설명 (2~3문장)",
  "external_elements_found": ["외부 자원 목록 (없으면 빈 배열)"],
  "closed_world_rewrite": "폐쇄 세계 원칙을 지키는 개선 또는 대안 아이디어",
  "asit_score": 8,
  "summary": "ASIT 폐쇄 세계 관점 한 줄 평가"
}}"""

    else:  # 분리원리
        user = f"""아이디어를 TRIZ 분리원리 관점에서 평가하세요.

<context>
문제 요약: {req.problem_summary}
물리 모순: {req.physical_contradiction or "미입력 — 아이디어 설명에서 추론하세요"}
</context>

<idea>
아이디어명: {req.idea_name}
설명: {req.idea_description}
</idea>

<task>
다음 기준으로 평가하고 JSON을 출력하세요:
1. 이 아이디어가 물리 모순(A이면서 동시에 B)을 어떻게 해소하는지 분석
2. 적용된 분리원리: 시간 분리 / 공간 분리 / 조건 분리 / 전체-부분 분리 중 선택
3. 가장 효과적인 분리원리와 구체적 적용 방안 제시
</task>

출력 JSON:
{{
  "framework": "분리원리",
  "physical_contradiction_resolved": true,
  "applied_principle": "시간 분리",
  "principle_justification": "왜 이 분리원리가 적용됐는지 설명 (2~3문장)",
  "separation_detail": "A 특성과 B 특성이 어떻게 분리되는지 구체적 설명",
  "best_alternative_principle": "다른 분리원리로 접근한다면 어떻게 할 수 있는지",
  "separation_score": 7,
  "summary": "분리원리 관점 한 줄 평가"
}}"""

    raw = await call_ai_async(system, user, max_tokens=2048)
    result = parse_json_safe(raw)
    result["framework"] = framework_name  # 파싱 실패 폴백에도 키 보장
    return result


@app.post("/api/framework-feedback")
async def framework_feedback(req: FrameworkFeedbackRequest):
    """3가지 프레임워크 에이전트(IFR / ASIT 폐쇄세계 / 분리원리)로 아이디어 평가."""
    results = {}
    for name, system_prompt in FRAMEWORK_AGENTS.items():
        try:
            result = await _run_framework_agent(name, system_prompt, req)
            results[name] = result
        except Exception as e:
            results[name] = {"framework": name, "error": str(e)}
        # 프레임워크 에이전트 간 2초 간격 (rate limit 방지)
        await asyncio.sleep(2)
    return {"feedback": results, "idea_name": req.idea_name}


# ──────────────────────────────────────────────
# 실행 아이디어 (구체적 실행 계획 생성)
# ──────────────────────────────────────────────
class ActionPlanRequest(BaseModel):
    top10: list = []           # [{name, avg_score, timing, scores:{기획자,컨설턴트,엔지니어,고객}}]
    problem_summary: str = ""
    goal: str = ""
    existing_elements: str = ""  # ASIT 폐쇄 세계 조건용 (쉼표 구분)
    insights: list = []


@app.post("/api/action-plan")
async def action_plan(req: ActionPlanRequest):
    """Top 10 아이디어를 구체적 실행 계획으로 변환."""
    ideas_text = json.dumps(req.top10[:10], ensure_ascii=False, indent=2)
    insights_text = "\n".join(f"- {t}" for t in req.insights[:3])

    system = (
        "당신은 TRIZ/ASIT 기반 실행 계획 전문가입니다. "
        "추상적 아이디어를 현장에서 바로 실행 가능한 구체적 행동으로 변환하세요. "
        "폐쇄 세계 조건: 기존 자원만 사용합니다. 새로운 예산이나 외부 도구는 제안하지 않습니다. "
        "반드시 valid JSON만 출력하세요."
    )

    user = f"""아래 Top 10 아이디어를 구체적 실행 계획으로 변환하세요.

핵심 문제: {req.problem_summary}
목표: {req.goal}
폐쇄 세계 조건 (이 자원만 사용 가능): {req.existing_elements or "기존 시스템·인력·데이터"}
핵심 인사이트:
{insights_text}

Top 10 아이디어:
{ideas_text}

각 아이디어마다 다음을 구체적으로 작성하세요:
- first_action: 이번 주에 당장 할 수 있는 가장 작은 첫 행동 (동사로 시작, 1문장)
- application_ideas: 이 TRIZ/ASIT 아이디어를 실제로 적용하면 어떤 기능/서비스/콘텐츠가 만들어지는지 2~3개의 구체적 아이디어. 각각 title(기능명), scenario(독자/사용자가 실제로 경험하는 장면을 1인칭 시나리오로, 2~3문장), value(기대 효과, 수치 포함)로 구성
- steps: 3~5개의 순차적 실행 단계 (각 단계: action=구체적 행동, detail=어떻게 하는지, who=담당자/역할, when=구체적 일정)
- resources_needed: 필요한 기존 자원 (최대 3개, 새 예산 없이 가능한 것)
- success_metrics: 성공을 측정할 수 있는 지표 (최대 2개, 수치 포함)
- obstacles: 예상 장애물과 해결책 (최대 2개)

출력 형식 (JSON만):
{{
  "action_plans": [
    {{
      "rank": 1,
      "name": "아이디어명",
      "avg_score": 8.8,
      "timing": "즉시",
      "first_action": "이번 주에 할 첫 행동 (구체적, 동사 시작)",
      "application_ideas": [
        {{
          "title": "구체적 기능/서비스/콘텐츠명",
          "scenario": "독자/사용자가 실제로 경험하는 장면 (1인칭, 2~3문장)",
          "value": "기대 효과 및 수치"
        }}
      ],
      "steps": [
        {{"step": 1, "action": "행동명", "detail": "어떻게", "who": "누가", "when": "언제"}}
      ],
      "resources_needed": ["기존 자원1", "기존 자원2"],
      "success_metrics": ["지표1 (목표 수치)", "지표2"],
      "obstacles": [
        {{"problem": "예상 장애물", "solution": "해결 방법"}}
      ]
    }}
  ],
  "quick_start": "모든 아이디어 중 가장 먼저 할 단 하나의 행동 (1문장)"
}}"""

    try:
        raw = await call_ai_async(system, user, max_tokens=8192)
        result = parse_json_safe(raw)
        if "raw" in result:
            raise ValueError("JSON 파싱 실패")
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────
# 진입점
# ──────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    print("🚀 TRIZ/ASIT 대시보드 서버 시작 중...")
    print("🌐 브라우저에서 http://localhost:8000 으로 접속하세요")
    uvicorn.run(app, host="0.0.0.0", port=8000)
