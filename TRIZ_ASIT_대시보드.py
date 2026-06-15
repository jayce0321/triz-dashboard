import os
import uuid
import json
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv
import httpx
from fastapi import FastAPI, BackgroundTasks, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse, Response
from pydantic import BaseModel
from typing import List, Optional

# ──────────────────────────────────────────────
# 환경변수 로드
# ──────────────────────────────────────────────
# 1순위: ~/.anthropic/triz.env  (Anthropic API 키)
# 2순위: ~/.gemini/triz.env     (Gemini API 키 폴백)
# 3순위: 프로젝트 폴더의 .env   (개발 편의 폴백)
_ANTHROPIC_ENV_PATH = os.path.expanduser("~/.anthropic/triz.env")
_GEMINI_ENV_PATH    = os.path.expanduser("~/.gemini/triz.env")

if os.path.isfile(_ANTHROPIC_ENV_PATH):
    load_dotenv(_ANTHROPIC_ENV_PATH, override=True)
elif os.path.isfile(_GEMINI_ENV_PATH):
    load_dotenv(_GEMINI_ENV_PATH, override=True)
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

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL      = "claude-sonnet-4-6"  # 최신 Claude Sonnet

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "") or os.environ.get("GOOGLE_API_KEY", "")
GEMINI_MODEL   = "gemini-2.5-flash"

# ── AI 백엔드 초기화 (Claude 우선, Gemini 폴백) ──
client         = None   # Anthropic client
gemini_client  = None   # Gemini client (폴백)
AI_BACKEND     = "none" # "claude" | "gemini" | "none"

if ANTHROPIC_API_KEY:
    try:
        import anthropic as _anthropic
        client     = _anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        AI_BACKEND = "claude"
        print(f"✅ Claude 백엔드 사용: {CLAUDE_MODEL}")
    except Exception as e:
        print(f"⚠️  Anthropic 초기화 실패: {e}")

if AI_BACKEND == "none" and GEMINI_API_KEY:
    try:
        from google import genai as _genai
        from google.genai import types as _gtypes
        gemini_client = _genai.Client(api_key=GEMINI_API_KEY)
        AI_BACKEND    = "gemini"
        print(f"✅ Gemini 폴백 백엔드 사용: {GEMINI_MODEL}")
    except Exception as e:
        print(f"⚠️  Gemini 초기화 실패: {e}")

if AI_BACKEND == "none":
    print("❌ API 키가 설정되지 않았습니다.")
    print(f"   Claude: {_ANTHROPIC_ENV_PATH} 에 ANTHROPIC_API_KEY=sk-ant-... 추가")
    print(f"   Gemini: {_GEMINI_ENV_PATH} 에 GEMINI_API_KEY=AIza... 추가")

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
            # 닫는 코드펜스 없이 잘린 경우 → repair 시도
            c_start = candidate.find("{")
            if c_start >= 0:
                try:
                    return _repair_truncated_json(candidate[c_start:])
                except Exception:
                    pass

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


async def call_ai_async(system, user, max_tokens: int = 8192) -> str:
    """AI API 비동기 호출 — Claude 우선, Gemini 폴백.
    system/user: str 또는 content blocks list (Claude 프롬프트 캐싱용)
    """
    if AI_BACKEND == "claude":
        return await _call_claude(system, user, max_tokens)
    elif AI_BACKEND == "gemini":
        # Gemini는 content blocks 미지원 → plain text로 변환
        if isinstance(user, list):
            user = "\n".join(b.get("text", "") for b in user if b.get("type") == "text")
        if isinstance(system, list):
            system = "\n".join(b.get("text", "") for b in system if b.get("type") == "text")
        return await _call_gemini(system, user, max_tokens)
    else:
        raise RuntimeError(
            "AI 키가 설정되지 않았습니다. "
            f"{_ANTHROPIC_ENV_PATH} 에 ANTHROPIC_API_KEY=sk-ant-... 를 추가한 뒤 서버를 재시작하세요."
        )


def _call_claude_sync(system, user, max_tokens: int = 8192) -> str:
    """Claude Sonnet 동기 호출 (프롬프트 캐싱 적용).
    - system: str → cache_control 자동 적용 / list → content blocks 그대로 전달
    - user: str → 그대로 전달 / list → content blocks (캐시 분할용)
    - 429/529 과부하는 10→30→60초 재시도 (4회)
    """
    if not client:
        raise RuntimeError("Anthropic 클라이언트가 초기화되지 않았습니다.")

    import time

    # system prompt → cache_control 포함 list로 변환 (str인 경우)
    system_param = (
        [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        if isinstance(system, str)
        else system
    )

    retry_waits = [10, 30, 60, 60]  # 재시도 간격 (초)
    last_err = None
    for attempt in range(5):
        if attempt > 0:
            wait = retry_waits[attempt - 1]
            print(f"⏳ Claude 재시도 대기 {wait}초 ({attempt}/{len(retry_waits)})...", flush=True)
            time.sleep(wait)
        try:
            response = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=max_tokens,
                temperature=1,
                system=system_param,
                messages=[{"role": "user", "content": user}],
            )
            # 캐시 사용 통계 로깅
            u = getattr(response, "usage", None)
            if u:
                cr = getattr(u, "cache_read_input_tokens", 0) or 0
                cc = getattr(u, "cache_creation_input_tokens", 0) or 0
                if cr or cc:
                    print(f"💾 캐시: 생성={cc:,} / 히트={cr:,} tokens", flush=True)
            return response.content[0].text

        except Exception as e:
            err_str = str(e).lower()
            err_type = type(e).__name__.lower()

            # 인증 오류 — 재시도 불필요
            if any(k in err_str for k in ("authentication", "api_key", "401", "invalid x-api-key")):
                raise RuntimeError(
                    "Anthropic API 키가 유효하지 않습니다. "
                    f"{_ANTHROPIC_ENV_PATH} 에서 키를 확인하세요."
                ) from e

            # Rate limit / 할당량 초과
            if any(k in err_str for k in ("rate_limit", "429", "too many", "overloaded", "529")):
                last_err = e
                if attempt < 4:
                    print(f"⏳ Claude Rate limit (attempt {attempt+1}/5)", flush=True)
                    continue
                raise RuntimeError("Claude 요청 한도에 도달했습니다. 잠시 후 다시 시도해 주세요.") from e

            # 서버 오류 (HTTP 5xx)
            if any(k in err_str for k in ("503", "500", "server error", "internal")):
                last_err = e
                if attempt < 4:
                    print(f"⏳ Claude 서버 일시 장애 (attempt {attempt+1}/5)", flush=True)
                    continue
                raise RuntimeError(f"Claude 서버 오류. 잠시 후 다시 시도해 주세요: {e}") from e

            # 연결 오류 / 타임아웃 — 재시도
            if any(k in err_type for k in ("timeout", "connection", "network")) or \
               any(k in err_str for k in ("timeout", "connection", "network", "timed out", "reset by peer")):
                last_err = e
                if attempt < 4:
                    print(f"⏳ Claude 연결 오류 재시도 (attempt {attempt+1}/5): {type(e).__name__}", flush=True)
                    continue
                raise RuntimeError(f"Claude 연결 오류. 네트워크를 확인해 주세요: {e}") from e

            raise RuntimeError(f"Claude API 오류: {type(e).__name__}: {e}") from e

    raise RuntimeError(f"AI 호출이 반복적으로 실패했습니다: {last_err}")


async def _call_claude(system: str, user: str, max_tokens: int = 8192) -> str:
    loop = asyncio.get_running_loop()
    from functools import partial
    return await loop.run_in_executor(executor, partial(_call_claude_sync, system, user, max_tokens))


def _call_gemini_sync(system: str, user: str, max_tokens: int = 8192) -> str:
    """Gemini 2.5 Flash 동기 호출 (폴백용)."""
    if not gemini_client:
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

            response = gemini_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=user,
                config=config,
            )
            return response.text

        except Exception as e:
            err_str = str(e).lower()
            if any(k in err_str for k in ("quota", "resource_exhausted", "429", "rate limit", "too many")):
                if attempt < 3:
                    last_err = e
                    print(f"⏳ Gemini Rate limit — {[5,15,30][attempt]}초 후 재시도 ({attempt+2}/4)")
                    continue
                raise RuntimeError("Gemini 요청 한도에 도달했습니다. 1~2분 후 다시 시도해 주세요.") from e
            if any(k in err_str for k in ("503", "overloaded", "unavailable", "500")):
                if attempt < 3:
                    last_err = e
                    print(f"⏳ Gemini 서버 일시 장애 — {[5,15,30][attempt]}초 후 재시도 ({attempt+2}/4)")
                    continue
                raise RuntimeError(f"Gemini 서버 장애: {e}") from e
            raise RuntimeError(f"Gemini API 오류: {e}") from e

    raise RuntimeError(f"AI 호출이 반복적으로 실패했습니다: {last_err}")


async def _call_gemini(system: str, user: str, max_tokens: int = 8192) -> str:
    loop = asyncio.get_running_loop()
    from functools import partial
    return await loop.run_in_executor(executor, partial(_call_gemini_sync, system, user, max_tokens))


# 하위 호환성 유지 (기존 step_* 함수들이 이 이름으로 호출)
async def call_claude_async(system, user, max_tokens: int = 8192) -> str:
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
        "당신은 20년 경력의 TRIZ/ASIT 문제 분석 전문가입니다. "
        "Altshuller의 TRIZ 방법론과 Horowitz의 ASIT 원저(1999)를 깊이 이해하며, "
        "실제 기업 컨설팅 현장에서 수백 건의 문제를 분석한 경험이 있습니다.\n\n"
        "반드시 valid JSON만 출력하세요. 마크다운 코드블록 없이 순수 JSON만.\n\n"
        "# 핵심 분석 원칙 (반드시 준수)\n\n"
        "## 1. 모순 식별 — 표면 문제가 아닌 근본 모순을 파고들 것\n"
        "- 기술 모순: TRIZ 39개 파라미터 언어로 표현하라\n"
        "  예) '콘텐츠 개인화 정도(#35)를 높이면 시스템 복잡성(#36)이 증가한다'\n"
        "- 물리 모순: 하나의 대상에 서로 모순되는 두 요구가 동시에 존재하는 상황\n"
        "  예) '뉴스레터는 짧아야 한다(이탈 방지) + 길어야 한다(정보량 충족)'\n"
        "- 잘못된 예: '품질을 높이면 비용이 증가한다' → 이는 모순이 아닌 상식임\n\n"
        "## 2. IFR (이상적 최종 결과) — 3가지 수준으로 구체화\n"
        "- IFR은 '기적이 일어나면 어떤 상태가 될까?'의 극한 목표\n"
        "- 반드시 측정 가능한 수치 목표 포함\n"
        "- '추가 자원·인력·비용 없이' 조건 필수\n\n"
        "## 3. 폐쇄 세계 요소 목록 식별\n"
        "- ASIT 분석을 위해 '현재 시스템 내 존재하는 모든 구성요소'를 명시적으로 나열\n"
        "- 이 목록이 이후 ASIT 폐쇄 세계 조건의 기준이 됨\n"
    )

    # 문제유형별 컨텍스트 구성
    if req.문제유형 == "조직프로세스":
        context = f"""문제 정보 (조직/프로세스 유형):
- 조직명: {req.조직명}
- 대상 부서: {req.대상부서}
- 구성원 현황: {req.구성원}
- 핵심 지표: {req.핵심지표}
- 현재 업무 구조: {req.현재구조}
- 제약 조건: {req.제약조건}
- 문제현상: {req.문제현상}
- 달성 목표: {req.목표}
- 선택된 도구: {', '.join(req.selected_tools)}"""
    else:
        context = f"""문제 정보 (상품/서비스 유형):
- 매체/플랫폼: {req.매체}
- 상품·서비스명: {req.상품명}
- 하위 구성요소: {req.하위요소}
- 상위 카테고리: {req.상위요소}
- 주요 대상: {req.대상}
- 핵심 가치 지표(KPI): {req.가치}
- 문제현상: {req.문제현상}
- 달성 목표: {req.목표}
- 선택된 도구: {', '.join(req.selected_tools)}"""

    user = f"""{context}

위 문제를 TRIZ/ASIT 전문가 관점으로 심층 분석하여 아래 형식의 JSON만 출력하세요.

분석 지침:
1. problem_summary: 현상→원인→영향 구조로 작성하라. 단순 재서술 금지. 수치 포함 필수.
2. technical_contradiction: TRIZ 39개 파라미터 언어로 '개선 파라미터 vs 악화 파라미터' 명시
3. physical_contradiction: 하나의 요소가 상반된 두 속성을 동시에 요구받는 상황을 정확히 기술
4. IFR: '추가 자원·인력·예산 없이 현 시스템이 스스로' 달성하는 이상 상태. 수치 목표 포함
5. contradiction_root_cause: 왜 이 모순이 발생하는지 구조적 원인 1문장
6. closed_world_elements: ASIT용 현재 시스템 구성요소 목록 (배열)

출력 형식 (JSON만):
{{
  "problem_summary": "현상: [현재 발생 중인 것, 수치] / 원인: [근본 원인] / 영향: [비즈니스 임팩트]",
  "technical_contradiction": "[파라미터A — 예: 콘텐츠 개인화 수준]를 높이면 → [파라미터B — 예: 운영 복잡도]가 악화된다. 개선 파라미터: {req.가치 or req.핵심지표} 향상 / 악화 파라미터: [악화되는 것]",
  "physical_contradiction": "[핵심 요소]는 [특성A — 이유]이어야 하면서, 동시에 [특성B — 이유]이어야 한다",
  "IFR": {{
    "IFR1": "추가 자원 없이 [현 시스템 구성요소]가 스스로 [구체적 상태]를 달성한다 → 목표: [수치]",
    "IFR2": "기존 예산·인력 변화 없이 [문제 현상]이 사라진다 → 측정: [지표]",
    "IFR3": "시스템 재설계로 [모순의 근본 원인]이 처음부터 존재하지 않는다"
  }},
  "contradiction_root_cause": "이 모순의 구조적 원인: [1문장]",
  "closed_world_elements": ["요소1", "요소2", "요소3"],
  "recommended_tools": ["1순위 도구 (이유 포함)", "2순위 도구", "3순위 도구"],
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

    system = (
        "당신은 Altshuller TRIZ 방법론을 20년 이상 현장 적용한 실전 전문가입니다.\n"
        "단순히 원리를 나열하는 것이 아니라, 발명원리가 이 특정 문제의 모순을 "
        "어떤 메커니즘으로 해소하는지를 정확히 설명해야 합니다.\n\n"
        "반드시 valid JSON만 출력하세요. 마크다운 코드블록 없이.\n\n"
        "# TRIZ 아이디어 품질 기준 (반드시 충족)\n\n"
        "## 좋은 TRIZ 아이디어의 조건\n"
        "1. 모순 해소: 아이디어가 어떻게 기술 모순 또는 물리 모순을 해결하는지 명확히 설명\n"
        "2. 구체적 메커니즘: '어떻게'가 빠진 아이디어는 아이디어가 아님\n"
        "   나쁜 예: '개인화 추천 시스템 도입'\n"
        "   좋은 예: '기존 기사 열람 이력 데이터를 역분할(#1)하여 주제별 미니 뉴스피드를 분리 생성. "
        "독자는 관심사별로 독립된 피드를 구독하고, 알고리즘이 각 피드에 최적 기사를 배분'\n"
        "3. 사용자 시나리오: 최종 사용자가 실제로 경험하는 장면을 1인칭 현재 시제로 기술\n"
        "4. IFR 정렬: 아이디어가 IFR(이상적 최종 결과) 중 어느 수준에 해당하는지 명시\n"
        "5. 실행 진입점: '첫 번째 주에 할 수 있는 가장 작은 행동'이 존재해야 함\n\n"
        "## 발명원리 적용 가이드\n"
        "- #1 분할: 하나를 여러 독립된 부분으로 나눔 → 대상 세분화, 기능 분리\n"
        "- #2 추출: 방해 요소 분리 또는 필요한 것만 꺼냄\n"
        "- #3 국소품질: 균일 구조를 비균일하게 → 상황별 차별화\n"
        "- #9 사전 역조치: 예상 문제에 미리 반대 방향 조치\n"
        "- #10 사전 조치: 필요한 변화를 미리 완전 또는 부분 수행\n"
        "- #13 역방향: 문제가 요구하는 것의 반대를 수행\n"
        "- #15 역동성: 고정된 것을 유연하게, 환경에 맞게 적응\n"
        "- #23 피드백: 피드백 루프 도입 또는 강화\n"
        "- #25 자기서비스: 시스템 스스로 유지·개선 (IFR-1과 연결)\n"
        "- #35 속성 전환: 상태 변화(유료↔무료, 개인↔그룹)\n"
        "- 분리원리(시간): A 특성은 시간 t1에, B 특성은 시간 t2에\n"
        "- 분리원리(공간): A 특성은 공간 S1에, B 특성은 공간 S2에\n"
        "- 분리원리(조건): 조건 C1에서 A, 조건 C2에서 B\n"
        "- 분리원리(전체-부분): 전체는 A, 구성 요소는 B\n"
    )

    # 문제유형별 컨텍스트
    if req.문제유형 == "조직프로세스":
        domain_ctx = f"조직: {req.조직명} / 부서: {req.대상부서} / 구성원: {req.구성원} / 현재구조: {req.현재구조}"
    else:
        domain_ctx = f"매체: {req.매체} / 상품: {req.상품명} / 구성요소: {req.하위요소} / 대상: {req.대상} / KPI: {req.가치}"

    ifr_text = ""
    ifr = problem.get("IFR", {})
    if isinstance(ifr, dict):
        ifr_text = f"IFR-1: {ifr.get('IFR1','')}\nIFR-2: {ifr.get('IFR2','')}\nIFR-3: {ifr.get('IFR3','')}"

    user = f"""다음 문제의 모순을 해소하는 TRIZ 아이디어를 정확히 8개 도출하세요.

## 문제 컨텍스트
{domain_ctx}
문제현상: {req.문제현상}
목표: {req.목표}

## 분석된 모순 (반드시 이 모순을 해소하는 방향으로 아이디어 도출)
기술 모순: {problem.get('technical_contradiction', req.문제현상)}
물리 모순: {problem.get('physical_contradiction', '')}
모순의 근본 원인: {problem.get('contradiction_root_cause', '')}

## IFR (아이디어가 이 방향을 향해야 함)
{ifr_text}

## 아이디어 도출 요건 (정확히 8개)
- 3개: 기술 모순 해소 (발명원리 적용, 서로 다른 원리)
- 3개: 물리 모순 해소 (분리원리 적용, 시간/공간/조건 분리 각각 1개)
- 2개: IFR-1 또는 IFR-2 수준에 근접
- 각 아이디어: 서로 다른 원리 적용
- 아이디어명: 구체적 행동명 (예: '이탈신호 7일 전 인터셉트')

description은 2문장으로 간결하게:
  ① 메커니즘(모순 해소 방식) ② 핵심 변화(현재→개선)

출력 형식 (JSON만, 정확히 8개):
{{
  "ideas": [
    {{
      "id": 1,
      "name": "구체적 아이디어명",
      "description": "① 메커니즘: [해소 방식] ② 핵심 변화: [현재 A → 개선 B]",
      "principle": "발명원리명 (#번호)",
      "contradiction_resolved": "기술모순/물리모순/IFR-1/IFR-2 중 하나",
      "first_action": "이번 주 할 수 있는 행동 1문장",
      "pros": ["장점1", "장점2"],
      "cons": ["단점1"],
      "initial_score": 6
    }}
  ]
}}"""

    raw = await call_claude_async(system, user, max_tokens=5500)  # 8개 × 간결 → 5500
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
        "당신은 Roni Horowitz ASIT 원저(Tel-Aviv University, 1999)에 정통한 실전 전문가입니다.\n"
        "ASIT의 핵심 철학은 '폐쇄 세계 안에서 창의성을 강제하는 것'입니다.\n"
        "반드시 valid JSON만 출력하세요. 마크다운 코드블록 없이.\n\n"
        "# ASIT 실전 원칙 — 전문가 수준 적용 기준\n\n"
        "## 폐쇄 세계 조건(Closed World Condition) — ASIT의 핵심\n"
        "- 모든 해결책은 '문제 상황에 이미 존재하는 요소'만 활용해야 한다\n"
        "- 위반 사례: '새로운 AI 엔진 도입', '외부 파트너십 체결', '신규 채용'\n"
        "- 준수 사례: 기존 기자 역할 재조합, 현재 보유 데이터 활용, 기존 콘텐츠 형식 변형\n"
        "- 체크: 각 아이디어의 'used_elements' 필드에 사용한 요소를 명시하고, 모두 closed_world_elements 목록 안에 있어야 함\n\n"
        "## ASIT 5가지 도구 정의 및 적용 기준 (Horowitz 1999 원저)\n\n"
        "### 1. 제거(Subtraction)\n"
        "정의: 핵심 구성 요소를 제거했을 때, 남은 시스템이 동일하거나 더 나은 기능을 달성하는 아이디어\n"
        "적용 질문: '이 요소가 없어도 목적을 달성할 수 있는가? 또는 더 잘 달성할 수 있는가?'\n"
        "잘못된 예: 단순 기능 축소 (이건 제거가 아닌 열화)\n"
        "올바른 예: 구독 결제 UI를 제거하고 → 콘텐츠 소비량 기반 자동 결제로 전환 (결제 마찰 제거 + 기능 유지)\n\n"
        "### 2. 복제(Multiplication)\n"
        "정의: 기존 요소의 '변형된 복사본'을 추가. 단순 복사가 아닌 역할 차별화 필수\n"
        "적용 질문: '이 요소를 약간 다르게 변형한 버전을 추가하면 어떤 새 기능이 생기는가?'\n"
        "잘못된 예: 기자를 한 명 더 채용 (단순 복사)\n"
        "올바른 예: 기존 기사 콘텐츠를 '요약 버전'으로 복제 → 시간 없는 독자용 병행 서비스 제공\n\n"
        "### 3. 분할(Division)\n"
        "정의: 기존 요소를 물리적·기능적·시간적으로 분리하여 재배치\n"
        "유형: ① 기능적 분할(역할 분리), ② 시간적 분할(단계별 실행), ③ 공간적 분할(위치/채널 분리)\n"
        "적용 질문: '이 요소를 둘로 나누면 각각이 더 효과적으로 작동하는가?'\n\n"
        "### 4. 기능통합(Task Unification)\n"
        "정의: 기존 요소에 현재 수행하지 않는 추가 역할을 부여\n"
        "적용 질문: '이 요소가 추가로 [다른 기능]도 수행하게 하면 어떤 가치가 생기는가?'\n"
        "잘못된 예: 새 기능을 새 요소가 담당 (이건 추가이지 통합이 아님)\n"
        "올바른 예: 댓글 섹션이 → 독자 여론조사 기능도 수행 (기존 요소가 추가 역할 획득)\n\n"
        "### 5. 대칭파괴(Breaking Symmetry) — Horowitz 원저 5번째 도구\n"
        "⚠️ SIT의 '속성의존성(Attribute Dependency)'과 혼동 금지. Horowitz 원저의 5번째 도구는 대칭파괴임.\n"
        "정의: 현재 균일하게 적용되는 속성을 의도적으로 비대칭화\n"
        "적용 질문: '현재 모든 [대상]에게 동일하게 주는 것을 비대칭으로 바꾸면?'\n"
        "올바른 예: 모든 독자에게 동일한 기사 노출 → 구독 기간별·지역별 비대칭 콘텐츠 우선순위 적용\n"
    )

    # 문제유형별 구성 요소 컨텍스트
    closed_world_elements = problem.get("closed_world_elements", [])

    if req.문제유형 == "조직프로세스":
        elements_label = "현재 조직 구성 요소"
        raw_elements = req.현재구조 or f"{req.대상부서}, {req.구성원}"
        subject_label = "조직명"
        subject_value = req.조직명 or req.매체
        target_label = "핵심 지표"
        target_value = req.핵심지표 or req.가치
    else:
        elements_label = "현재 서비스 구성 요소"
        raw_elements = req.하위요소
        subject_label = "상품명"
        subject_value = req.상품명
        target_label = "주요 대상 / KPI"
        target_value = f"{req.대상} / {req.가치}"

    # Step 1 결과에서 폐쇄 세계 요소 목록 가져오기 (없으면 원본 사용)
    if closed_world_elements:
        elements_display = ", ".join(closed_world_elements)
    else:
        elements_display = raw_elements

    user = f"""다음 문제에 ASIT 5가지 도구를 전문가 수준으로 적용하여 정확히 8개 아이디어를 도출하세요.

## 문제 컨텍스트
{subject_label}: {subject_value}
{target_label}: {target_value}
문제현상: {req.문제현상}
목표: {req.목표}
분석된 모순: {problem.get('technical_contradiction', '')}

## 폐쇄 세계 목록 (이 요소들만 사용 가능 — ASIT 절대 원칙)
{elements_label}: {elements_display}

## 도구별 아이디어 배분 (총 8개, 각 도구당 1~2개 — 5가지 도구 모두 사용)
- 제거 2개 · 복제 2개 · 분할 1개 · 기능통합 2개 · 대칭파괴 1개

## 각 아이디어 필수 (간결하게)
- used_elements: 사용한 폐쇄 세계 요소 배열
- transformation: 도구 적용 메커니즘 1문장
- description: AS-IS → TO-BE 1~2문장
- closed_world_check: YES/NO

출력 형식 (JSON만, 정확히 8개):
{{
  "ideas": [
    {{
      "id": 1,
      "name": "[도구명] 아이디어명",
      "tool": "제거/복제/분할/기능통합/대칭파괴",
      "target_element": "폐쇄 세계 요소명",
      "used_elements": ["요소1", "요소2"],
      "transformation": "도구 적용 메커니즘 1문장",
      "description": "AS-IS: [현재] → TO-BE: [변화]",
      "closed_world_check": "YES",
      "pros": ["장점1", "장점2"],
      "cons": ["단점1"],
      "initial_score": 7
    }}
  ]
}}"""

    raw = await call_claude_async(system, user, max_tokens=5500)  # 8개 × 간결 → 5500
    result = parse_json_safe(raw)

    await queue.put(sse_event({
        "type": "result",
        "step": "asit",
        "data": result,
    }))
    return result


# ──────────────────────────────────────────────
# Step 4: 3명 에이전트 동시 평가 (전략기획자·TRIZ전문가·고객)
# ──────────────────────────────────────────────
# 평가 루브릭 — 주관성 제거를 위한 객관 기준 (Altshuller 발명수준 + TRIZ/ASIT 특화)
EVAL_RUBRIC = """
# 채점 루브릭 (반드시 아래 기준으로 채점할 것)
# ⚠️ 경고: 아이디어를 7~9점으로 몰아주는 것은 엄격히 금지. 상위 20%만 8점 이상 가능.

## A. 모순 해소력 (Contradiction Resolution) — TRIZ/ASIT 핵심 기준
- 9~10점: 기술 모순과 물리 모순을 동시에 해소. IFR-1/IFR-2 수준에 근접
- 7~8점: 두 모순 중 하나를 명확히 해소. IFR-3 수준
- 5~6점: 모순을 우회하거나 부분적으로 완화
- 3~4점: 모순 해소 없이 문제를 다른 곳으로 이동
- 1~2점: 모순을 심화시키거나 새로운 모순 발생

## B. 신규성 (Novelty) — Altshuller 발명수준 기준
- 9~10점: 국내외 동종 업계 시도 사례 없음 (Level 4~5)
- 7~8점: 해외 사례 있으나 국내 동종 업계 미적용 (Level 3)
- 5~6점: 국내 타 업계 사례 있음 (Level 2~3)
- 3~4점: 동종 업계에서 부분 시도됨 (Level 1~2)
- 1~2점: 이미 존재하는 방법의 재서술 (Level 1)

## C. 실현가능성 (Feasibility) — 4항목 체크리스트
각 항목 2.5점 (0~4개 충족):
① 현재 보유 인력/역할로 실행 가능한가?
② 추가 예산 없이 기존 자원 내 실행 가능한가?
③ 현재 기술·인프라·시스템으로 구현 가능한가?
④ 6개월 내 테스트 가능한 최소 실행안(MVP)이 있는가?

## D. 가치 기여도 (Value Impact) — 인과관계 기준
- 9~10점: 목표 KPI를 직접 개선하는 명확한 인과관계 + 수치 예측 가능
- 7~8점: 간접적이나 논리적 인과관계 있음
- 5~6점: 긍정적 영향 예상되나 인과관계 불명확
- 1~4점: 목표 지표와 연관성 낮음

최종 점수 = A(30%) + B(20%) + C(35%) + D(15%) 가중평균
"""

# ──────────────────────────────────────────────
# 3인 평가 에이전트 선정 이유
#  1. 전략기획자 — 조직 실행 가능성(C 가중치 35%) 집중 검증, 실행 주체·타임라인·예산 제약 판단
#  2. TRIZ전문가  — 방법론 핵심(A 가중치 30%) 집중 검증, 모순 해소력·발명수준·IFR 이상성 판단
#  3. 고객        — 최종 가치(D 가중치 15%) 집중 검증, 실사용 체감·전환 의향·편의성 판단
# ──────────────────────────────────────────────
AGENT_CONFIGS = {
    "전략기획자": (
        "당신은 미디어·서비스 분야 15년 경력의 전략 기획·혁신 전문가입니다.\n"
        "TRIZ/ASIT를 실무에서 활용한 경험이 있으며, 아이디어의 '조직 실행 가능성'과 '전략 정합성'에 가장 엄격합니다.\n"
        "<role>전략 방향성, 브랜드 일관성, 실행 주체·타임라인, 예산/인력 제약, 조직 변화 저항을 평가합니다.\n"
        "특히 '이 아이디어를 실제로 누가·언제·어떻게 실행할 수 있는가'를 집중 검토합니다.\n"
        "아이디어가 추상적이거나 실행 주체가 불명확하면 가차없이 낮은 점수를 줍니다.</role>\n"
        "<output_format>반드시 valid JSON만 출력. 마크다운 코드블록 없이.</output_format>\n"
        + EVAL_RUBRIC
    ),
    "TRIZ전문가": (
        "당신은 Altshuller TRIZ·Horowitz ASIT 방법론을 20년 이상 현장 적용한 혁신 전문가입니다.\n"
        "아이디어의 '모순 해소력'과 Altshuller 발명수준(L1~L5)을 최우선 평가 기준으로 삼습니다.\n"
        "<role>TRIZ 39개 파라미터, 40가지 발명원리, ASIT 5도구, 분리원리, IFR 이상성 관점에서 평가합니다.\n"
        "특히 '근본 모순(기술·물리)을 실제로 해소하는가', '발명수준(L1~L5)이 어느 수준인가'를 집중 검토합니다.\n"
        "표면적 개선이나 모순을 다른 곳으로 이동시키는 아이디어에 냉정하게 낮은 점수를 줍니다.</role>\n"
        "<output_format>반드시 valid JSON만 출력. 마크다운 코드블록 없이.</output_format>\n"
        + EVAL_RUBRIC
    ),
    "고객": (
        "당신은 서비스 분야 핵심 고객층을 대표하는 실제 사용자입니다.\n"
        "화려한 기능보다 '실제로 내 삶에 유용한가'를 가장 중요하게 봅니다.\n"
        "<role>실사용 가치, 편의성, 체감 효과, 전환 의향, 차별성 관점에서 평가합니다.\n"
        "특히 '이 서비스를 위해 내 행동을 바꿀 의향이 있는가', '불편함 없이 쓸 수 있는가'를 검토합니다.\n"
        "기능은 멋지지만 실제로 쓸 것 같지 않은 아이디어에 솔직하게 낮은 점수를 줍니다.</role>\n"
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

    # ── 캐시 분할 ──────────────────────────────────────────────
    # 공통 부분 (3개 에이전트 동일): 컨텍스트 + 아이디어 목록 → cache_control 적용
    common_block = f"""다음 아이디어 목록을 채점 루브릭에 따라 엄격하게 평가하세요.

## 평가 컨텍스트
{ctx}
문제현상: {req.문제현상}
목표: {req.목표}

## 아이디어 목록
{ideas_text}"""

    # 에이전트별 고유 지침 (캐시 불가 — agent_name 포함)
    agent_block = f"""
## 채점 지침 ({agent_name} 관점에서 수행, 반드시 준수)
- score = A모순해소(30%) + B신규성(20%) + C실현가능성(35%) + D가치기여도(15%) 가중평균
- novelty_level: "L1(기존재서술)" / "L2(기존개선)" / "L3(타분야도입)" / "L4(새시스템)" / "L5(혁신)" 중 선택
- feasibility_check: 4항목 중 충족 수 (0~4), 각 항목 충족 여부 명시
- contradiction_score: 이 아이디어가 모순을 얼마나 해소하는지 (0~10)
- ⚠️ 경고: 전체 아이디어의 80%가 5~8점 범위여야 자연스러움. 8점 이상은 상위 20%만.
- 각 comment는 '왜 이 점수인가'를 루브릭 항목별로 구체적으로 서술 (단순 칭찬 금지)
- {agent_name} 관점에서 가장 치명적인 약점을 솔직하게 지적할 것

출력 형식 (JSON만):
{{
  "agent": "{agent_name}",
  "overall_assessment": "전체 아이디어 집합에 대한 {agent_name} 관점의 평가 (2~3문장. 공통 강점, 공통 약점, 가장 우려되는 점 포함)",
  "evaluations": [
    {{
      "id": 1,
      "name": "아이디어명",
      "score": 7,
      "contradiction_score": 6,
      "novelty_level": "L3(타분야도입)",
      "feasibility_check": 3,
      "feasibility_detail": "① O ② X(추가 개발 비용 필요) ③ O ④ O",
      "comment": "{agent_name} 관점 채점 근거: [A항목 점수 이유] / [C항목 점수 이유] / [핵심 우려]",
      "pros": ["구체적 장점 (이 관점에서 가장 가치 있는 것)"],
      "cons": ["구체적 단점 (이 관점에서 가장 치명적인 것)"],
      "improvement_suggestion": "이 아이디어를 개선하면 점수가 올라갈 수 있는 구체적 방안"
    }}
  ],
  "top3_ids": [3, 1, 7],
  "bottom3_ids": [2, 5, 9],
  "key_concerns": ["{agent_name} 관점 핵심 우려사항1", "우려사항2"],
  "hidden_gem": "점수는 낮지만 잠재력이 있어 추가 검토 가치가 있는 아이디어 (있으면)"
}}"""

    # 공통 블록은 캐시(3 에이전트 공유), 에이전트 지침 블록은 비캐시
    user_content = [
        {"type": "text", "text": common_block, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": agent_block},
    ]

    # 평가 응답: 12개 아이디어(6TRIZ+6ASIT) × 에이전트당 출력 압축 → 4096으로 제한해 TPM 절약
    raw = await call_claude_async(system, user_content, max_tokens=4096)
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
        "message": "3명 에이전트 동시 평가 시작...",
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

    # 병렬 실행: 3명 에이전트를 동시에 실행 (2초 스태거로 TPM 분산)
    evaluation_data = {}
    agents_list = list(AGENT_CONFIGS.items())

    async def _staggered_eval(idx: int, name: str, system_prompt: str):
        # 첫 번째 에이전트도 2초 대기 (ASIT 직후 rate limit 방지)
        await asyncio.sleep(2 + idx * 2)  # 2 / 4 / 6초 스태거
        return await evaluate_single_agent(name, system_prompt, merged_ideas, req, queue)

    tasks_eval = [
        _staggered_eval(i, name, sp)
        for i, (name, sp) in enumerate(agents_list)
    ]
    results = await asyncio.gather(*tasks_eval, return_exceptions=True)
    failed_agents = []
    for result in results:
        if isinstance(result, Exception):
            # 어느 에이전트가 실패했는지 추적
            err_msg = str(result)
            print(f"⚠️  평가 에이전트 실패: {err_msg[:200]}", flush=True)
            failed_agents.append(err_msg[:100])
        else:
            agent_name, agent_result = result
            evaluation_data[agent_name] = agent_result

    # 실패한 에이전트는 빈 껍데기로 채워 프론트엔드가 "데이터 없음" 대신 에러 표시
    for name_sp, _sp in agents_list:
        if name_sp not in evaluation_data:
            evaluation_data[name_sp] = {
                "agent": name_sp,
                "overall_assessment": f"⚠️ {name_sp} 평가 중 오류가 발생했습니다. 서버 로그를 확인하세요.",
                "evaluations": [],
                "top3_ids": [],
                "bottom3_ids": [],
                "key_concerns": ["평가 실패"],
                "hidden_gem": "",
            }

    if failed_agents:
        await queue.put(sse_event({
            "type": "progress",
            "step": "evaluation",
            "status": "partial_fail",
            "agent": "마스터",
            "message": f"⚠️ 일부 에이전트 평가 실패 ({len(failed_agents)}개) — 나머지 결과로 진행합니다.",
        }))

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

    system = (
        "당신은 TRIZ/ASIT 분석 종합 전문가이자 실행 전략가입니다.\n"
        "3명의 다른 관점에서 나온 평가를 단순 평균하는 것이 아니라, "
        "TRIZ/ASIT 방법론 관점에서 '모순 해소력이 높고 실행 가능한' 아이디어를 선별해야 합니다.\n"
        "반드시 valid JSON만 출력하세요.\n\n"
        "# 종합 선별 원칙\n"
        "1. 모순 해소력 우선: TRIZ/ASIT 관점에서 근본 모순을 해소하는 아이디어를 최우선\n"
        "2. 실행 다양성: 단기/중기/장기 실행 아이디어가 골고루 포함되도록\n"
        "3. 소스 다양성: TRIZ와 ASIT 아이디어가 혼합되도록\n"
        "4. 에이전트 컨센서스: 3명 중 2명 이상이 높게 평가한 아이디어 우선\n"
        "5. 숨은 보석 발굴: 평균 점수는 낮지만 특정 에이전트가 강력 추천한 아이디어 검토\n"
    )
    # 에이전트 순서 고정 (전략기획자→TRIZ전문가→고객) 후 JSON 직렬화
    # 토큰 절약: 합성에 필요한 핵심 정보만 추출 (full description·pros·cons 제외)
    slim_ideas = [
        {
            "id":     idea.get("id"),
            "name":   idea.get("name"),
            "source": idea.get("source", ""),
            "contradiction_resolved": idea.get("contradiction_resolved") or idea.get("tool", ""),
            "initial_score": idea.get("initial_score", 0),
            "first_action": idea.get("first_action", ""),
        }
        for idea in merged_ideas
    ]
    ideas_text = json.dumps(slim_ideas, ensure_ascii=False, indent=2)

    agents_ordered = {k: evaluation_data.get(k, {}) for k in ["전략기획자", "TRIZ전문가", "고객"]}
    # 토큰 절약: top3 + 각 아이디어 점수만 전달
    slim_eval = {
        agent: {
            "top3":   data.get("top3", []),
            "scores": {s.get("name", ""): s.get("score", 0) for s in data.get("scores", [])}
        }
        for agent, data in agents_ordered.items()
    }
    eval_text = json.dumps(slim_eval, ensure_ascii=False, indent=2)

    # 문제유형별 컨텍스트
    if req.문제유형 == "조직프로세스":
        ctx = f"조직: {req.조직명} / 부서: {req.대상부서} / 핵심지표: {req.핵심지표}"
    else:
        ctx = f"매체: {req.매체} / 상품: {req.상품명} / 대상: {req.대상} / KPI: {req.가치}"

    user = f"""3명 에이전트(전략기획자·TRIZ전문가·고객) 평가 결과와 전체 아이디어를 TRIZ/ASIT 전문가 관점으로 종합하여 최종 Top 10을 선정하세요.

## 분석 컨텍스트
{ctx}
목표: {req.목표}
문제현상: {req.문제현상}

## 전체 아이디어
{ideas_text}

## 에이전트 평가 결과
{eval_text}

## Top 10 선별 기준 (이 순서로 적용)
1. contradiction_score 합계가 높은 아이디어 (모순 해소력)
2. 3명 에이전트 중 top3에 포함된 횟수
3. feasibility_check 평균값 (실행 가능성)
4. 소스 다양성 (TRIZ와 ASIT 혼합)
5. 단기/중기/장기 균형

출력 형식 (JSON만, 간결하게):
{{
  "final_top10": [
    {{
      "rank": 1,
      "id": 5,
      "name": "아이디어명",
      "description": "선정 이유 1문장 (모순 해소 방식 핵심)",
      "triz_asit_rationale": "원리/도구 적용 효과 1문장",
      "avg_score": 8.5,
      "scores": {{"전략기획자": 9, "TRIZ전문가": 8, "고객": 9}},
      "consensus": "높음/보통/낮음",
      "implementation": "단기(1-3개월)/중기(3-6개월)/장기(6개월+)",
      "impact": "높음/보통/낮음",
      "source": "TRIZ/ASIT",
      "principle_or_tool": "발명원리명 또는 ASIT 도구명"
    }}
  ],
  "quick_wins": [1, 3],
  "insights": [
    "통찰1: 핵심 패턴 (수치 포함, 1문장)",
    "통찰2: 실행 관련 발견 (1문장)",
    "통찰3: TRIZ/ASIT 관점 특이점 (1문장)"
  ],
  "contradiction_analysis": "핵심 모순이 아이디어들에서 어떻게 나타났는지 (1문장)",
  "next_steps": [
    {{
      "step": 1,
      "action": "구체적 행동 (동사+담당자+수단)",
      "timeline": "1주 이내/1개월 내/3개월 내",
      "owner": "담당 부서",
      "expected_outcome": "측정 가능한 결과"
    }}
  ]
}}"""

    # Top10 선정 + 인사이트 + next_steps — 16개 풀(TRIZ 8 + ASIT 8)에서 10개 선정, 입력 슬림화로 토큰 여유
    raw = await call_claude_async(system, user, max_tokens=6000)
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
        await asyncio.sleep(1)

        # Step 2
        triz_result = await step_triz(req, problem, queue)
        await asyncio.sleep(1)

        # Step 3
        asit_result = await step_asit(req, problem, queue)
        # step_evaluation 내 각 에이전트가 2초 이상 스태거로 시작하므로 여기선 1초만
        await asyncio.sleep(1)

        # Step 4 (병렬 평가)
        evaluation_data, merged_ideas = await step_evaluation(req, triz_result, asit_result, queue)
        await asyncio.sleep(1)

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
# RSS / YouTube 프록시 — CORS 우회
# 허용 도메인: segye.com, youtube.com, open-meteo.com
# ──────────────────────────────────────────────
_ALLOWED_HOSTS = (
    "www.segye.com",
    "img.segye.com",
    "www.youtube.com",
    "query1.finance.yahoo.com",
    "query2.finance.yahoo.com",
    "nominatim.openstreetmap.org",
    "api.open-meteo.com",
)


# ──────────────────────────────────────────────
# 주가 지수 — yfinance (Yahoo Finance 인증 자동 처리)
# 3분 서버사이드 캐시로 rate-limit 방지
# ──────────────────────────────────────────────
import time as _time

_STOCK_CACHE: dict = {}   # {"data": [...], "ts": float}
_STOCK_TTL = 3 * 60       # 3분 캐시

_STOCK_SYMBOLS = [
    {"sym": "^KS11",  "nm": "KOSPI",  "fmt": "int_comma"},
    {"sym": "^KQ11",  "nm": "KOSDAQ", "fmt": "float2"},
    {"sym": "^GSPC",  "nm": "S&P500", "fmt": "int_comma"},
    {"sym": "^DJI",   "nm": "DOW",    "fmt": "int_comma"},
    {"sym": "^IXIC",  "nm": "NASDAQ", "fmt": "int_comma"},
]

def _fmt_price(val: float, fmt: str) -> str:
    if fmt == "int_comma":
        return f"{int(val):,}"
    return f"{val:.2f}"

@app.get("/api/stocks")
async def get_stocks():
    """주가 지수 반환 (yfinance, 3분 캐시)."""
    now = _time.time()
    if _STOCK_CACHE.get("ts") and now - _STOCK_CACHE["ts"] < _STOCK_TTL:
        return {"stocks": _STOCK_CACHE["data"], "cached": True}

    import yfinance as yf

    loop = asyncio.get_event_loop()
    def _fetch():
        results = []
        tickers = yf.Tickers(" ".join(s["sym"] for s in _STOCK_SYMBOLS))
        for s in _STOCK_SYMBOLS:
            try:
                info = tickers.tickers[s["sym"]].fast_info
                price = info.last_price
                prev  = info.previous_close
                if price and prev:
                    chg_pct = (price / prev - 1) * 100
                    results.append({
                        "nm":     s["nm"],
                        "price":  _fmt_price(price, s["fmt"]),
                        "chgPct": round(chg_pct, 2),
                    })
            except Exception:
                pass
        return results

    try:
        data = await loop.run_in_executor(executor, _fetch)
        if data:
            _STOCK_CACHE["data"] = data
            _STOCK_CACHE["ts"]   = now
        return {"stocks": data, "cached": False}
    except Exception as e:
        # 캐시가 있으면 만료돼도 반환
        if _STOCK_CACHE.get("data"):
            return {"stocks": _STOCK_CACHE["data"], "cached": True, "stale": True}
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/weather")
async def get_weather(lat: float = Query(37.5665), lon: float = Query(126.9780)):
    """날씨 정보: Open-Meteo → wttr.in 폴백 + Nominatim 역지오코딩 (10분 캐시)."""
    import time as _t

    # 10분 캐시 — 위치 변화 0.01도(~1km) 이내면 캐시 재사용
    if not hasattr(get_weather, "_cache"):
        get_weather._cache = {}
    cache_key = (round(lat, 2), round(lon, 2))
    now_ts = _t.time()
    if cache_key in get_weather._cache:
        entry = get_weather._cache[cache_key]
        if now_ts - entry["ts"] < 600:
            return entry["data"]

    def _wwo_to_wmo(wwo: int) -> int:
        """World Weather Online 코드 → WMO 코드 변환 (wttr.in 폴백용)."""
        if wwo == 113: return 0
        if wwo == 116: return 2
        if wwo in (119, 122): return 3
        if wwo in (143, 248, 260): return 45
        if wwo in (176, 263, 266, 281, 284): return 51
        if wwo in (185): return 56
        if wwo in (293, 296, 317): return 61
        if wwo in (299, 302, 305, 308): return 63
        if wwo in (311, 314, 350): return 66
        if wwo in (323, 326, 320, 227): return 71
        if wwo in (329, 332, 335, 338, 230): return 75
        if wwo in (353, 362, 365, 368, 374): return 80
        if wwo in (356, 359, 371, 377): return 82
        if wwo in (200, 386, 392): return 95
        if wwo in (389, 395): return 99
        return 0

    city_name = "서울"
    cur = None

    async with httpx.AsyncClient(timeout=8, follow_redirects=True) as hx:
        # 1. 역지오코딩 (Nominatim)
        try:
            geo_r = await hx.get(
                f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json",
                headers={"User-Agent": "MiriNews/1.0 segye.com news app", "Accept-Language": "ko"},
            )
            if geo_r.status_code == 200:
                geo_d = geo_r.json()
                a = geo_d.get("address", {})
                raw = a.get("city") or a.get("town") or a.get("county") or a.get("village") or a.get("state", "내위치")
                city_name = raw[:6] if len(raw) > 6 else raw
        except Exception:
            pass

        # 2. Open-Meteo 날씨 (1차 시도)
        try:
            wx_r = await hx.get(
                f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
                "&current=temperature_2m,weather_code,relative_humidity_2m,wind_speed_10m"
                "&timezone=Asia/Seoul&forecast_days=1",
            )
            if wx_r.status_code == 200 and wx_r.text.strip():
                cur = wx_r.json().get("current", {})
        except Exception:
            cur = None

        # 3. Open-Meteo 실패 시 wttr.in 폴백
        if not cur:
            try:
                wt_r = await hx.get(
                    f"https://wttr.in/{lat},{lon}?format=j1",
                    headers={"Accept": "application/json", "User-Agent": "MiriNews/1.0"},
                )
                if wt_r.status_code == 200 and wt_r.text.strip():
                    cc = wt_r.json().get("current_condition", [{}])[0]
                    cur = {
                        "temperature_2m": float(cc.get("temp_C", 20)),
                        "weather_code": _wwo_to_wmo(int(cc.get("weatherCode", 113))),
                        "relative_humidity_2m": int(cc.get("humidity", 50)),
                        "wind_speed_10m": float(cc.get("windspeedKmph", 0)),
                    }
            except Exception:
                cur = None

    # 4. 모두 실패 → 스테일 캐시 또는 기본값
    if not cur:
        if cache_key in get_weather._cache:
            return get_weather._cache[cache_key]["data"]
        cur = {"temperature_2m": 20, "weather_code": 0, "relative_humidity_2m": 60, "wind_speed_10m": 3.0}

    result = {
        "city": city_name,
        "temp": round(cur.get("temperature_2m", 0)),
        "code": cur.get("weather_code", 0),
        "humidity": cur.get("relative_humidity_2m", 0),
        "wind": round(cur.get("wind_speed_10m", 0), 1),
        "lat": lat,
        "lon": lon,
    }
    get_weather._cache[cache_key] = {"ts": now_ts, "data": result}
    return result


@app.get("/api/rss-proxy")
async def rss_proxy(url: str = Query(..., description="프록시할 URL")):
    """CORS 우회 프록시: segye.com RSS / YouTube 피드 등 허용 도메인 fetch."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.hostname not in _ALLOWED_HOSTS:
        raise HTTPException(status_code=403, detail=f"허용되지 않은 도메인: {parsed.hostname}")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
        "Accept-Language": "ko-KR,ko;q=0.9",
        "Referer": "https://www.segye.com/",
    }
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            r = await client.get(url, headers=headers)
            r.raise_for_status()
            content_type = r.headers.get("content-type", "application/xml; charset=utf-8")
            # CORS 허용 헤더 포함하여 반환
            return Response(
                content=r.content,
                media_type=content_type,
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Cache-Control": "public, max-age=120",  # 2분 캐시
                },
            )
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"프록시 오류: {str(e)}")


# ──────────────────────────────────────────────
# 미리보는 세계 v2 — AI 뉴스 앱
# ──────────────────────────────────────────────
@app.get("/miribon")
@app.get("/miribon/")
async def miribon():
    html_path = os.path.join(BASE_DIR, "미리보는세계v2.html")
    if not os.path.exists(html_path):
        raise HTTPException(status_code=404, detail="미리보는세계v2.html 파일을 찾을 수 없습니다.")
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
    model_map = {"claude": CLAUDE_MODEL, "gemini": GEMINI_MODEL}
    return {
        "backend": AI_BACKEND,
        "model": model_map.get(AI_BACKEND),
        "ready": AI_BACKEND in ("claude", "gemini"),
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
        completed = False
        try:
            while True:
                try:
                    # 5초 대기 후 keepalive 핑 전송 (Railway 프록시 타임아웃 방지)
                    item = await asyncio.wait_for(queue.get(), timeout=5.0)
                except asyncio.TimeoutError:
                    import json as _json
                    yield f"data: {_json.dumps({'type': 'ping'})}\n\n"
                    continue
                if item is None:
                    # 분석 파이프라인 정상 완료 → 태스크 즉시 정리
                    completed = True
                    break
                yield item
        finally:
            if completed:
                # 정상 완료: 즉시 정리
                tasks.pop(task_id, None)
            else:
                # 클라이언트 연결 끊김 (중간 단절): 30초 유지 후 정리 → 브라우저 재연결 허용
                async def _delayed_cleanup():
                    await asyncio.sleep(30)
                    tasks.pop(task_id, None)
                asyncio.create_task(_delayed_cleanup())

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
        user = f"""아이디어를 IFR(이상적 최종 결과) 관점에서 전문가 수준으로 분석하세요.

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
IFR 공식: 이상도 = Σ유용한 기능 / (Σ비용 + Σ해로운 기능)

1. 이상도 분석
   - 이 아이디어가 추가하는 유용한 기능을 모두 나열 (구체적, 수치 포함 가능)
   - 이 아이디어가 발생시키는 비용·부작용을 모두 나열 (현실적으로)
   - 이상도 점수(0~10): 유용함이 비용을 얼마나 압도하는가

2. IFR 정렬 진단
   - IFR-1(자기해결): 외부 행위자 없이 시스템이 스스로 해결하는가?
   - IFR-2(무비용 해결): 기존 자원만으로 문제가 사라지는가?
   - IFR-3(원인 제거): 시스템 재설계로 문제 원인이 아예 없어지는가?
   - 이 아이디어는 IFR-1, 2, 3 중 어느 수준에 해당하는가?

3. IFR 개선 방안
   - 이 아이디어를 최소 변경으로 IFR에 더 가깝게 만드는 구체적 제안 (기존 요소만 활용)
   - 현재 아이디어에서 '제거할 수 있는 외부 의존성'은 무엇인가?
</task>

출력 JSON:
{{
  "framework": "IFR관점",
  "ideality_score": 7,
  "ifr_alignment": "IFR-2",
  "ifr_analysis": "이 아이디어가 IFR의 어느 수준에 해당하는지 구체적 분석 (2~3문장, 기술 모순/물리 모순과 연결)",
  "useful_functions": ["증가하는 유용 기능1 (구체적 효과 포함)", "유용 기능2"],
  "harmful_or_costs": ["발생하는 실제 비용1 (구체적)", "부작용2"],
  "ifr_gap": "현재 아이디어와 완전한 IFR 사이의 가장 큰 차이점",
  "improvement_toward_ifr": "기존 요소만 활용해 IFR에 더 가깝게 만드는 구체적 개선 제안 (외부 자원 도입 금지)",
  "summary": "IFR 관점 핵심 평가: [점수 이유와 주요 발견 1문장]"
}}"""

    elif framework_name == "ASIT폐쇄세계":
        user = f"""아이디어를 ASIT 폐쇄 세계(Closed World Condition) 관점에서 전문가 수준으로 분석하세요.

<context>
문제 요약: {req.problem_summary}
기존 구성 요소 목록 (폐쇄 세계 — 이 목록 외 요소 사용 금지): {req.existing_elements or "아이디어 설명에서 추론"}
</context>

<idea>
아이디어명: {req.idea_name}
설명: {req.idea_description}
</idea>

<task>
ASIT 폐쇄 세계 원칙: 모든 해결책은 '이미 문제 상황에 존재하는 요소'만 활용해야 한다.

1. 폐쇄 세계 준수 여부 감사
   - 아이디어 설명에서 사용된 모든 요소를 나열
   - 각 요소가 기존 구성 요소 목록에 있는지 확인
   - 외부 자원(새 기술, 신규 채용, 새 파트너십)이 포함됐는가?

2. ASIT 5도구 매핑 (Horowitz 1999)
   - 이 아이디어가 5도구(제거/복제/분할/기능통합/대칭파괴) 중 어느 것에 해당하는가?
   - 해당하지 않는다면 왜 ASIT 아이디어가 아닌지 설명

3. 폐쇄 세계 위반 시 리라이트
   - 외부 자원을 제거하고, 동일한 목적을 기존 요소만으로 달성하는 대안 아이디어 제시
   - 폐쇄 세계를 준수하면서 원래 아이디어의 가치를 최대한 보존

4. ASIT 순도 점수
   - 10점: 완벽한 폐쇄 세계 준수 + 정확한 ASIT 도구 적용
   - 7~9점: 폐쇄 세계 준수, 도구 적용 적절
   - 4~6점: 일부 외부 요소 포함 또는 도구 적용 미흡
   - 1~3점: 폐쇄 세계 위반 또는 ASIT 도구와 무관
</task>

출력 JSON:
{{
  "framework": "ASIT폐쇄세계",
  "closed_world_compliant": true,
  "asit_tool": "기능통합",
  "tool_justification": "왜 이 ASIT 도구에 해당하는지 (도구 정의와 연결하여 2~3문장)",
  "used_elements_audit": ["아이디어에서 사용된 요소1", "요소2"],
  "external_elements_found": ["외부 자원 목록 (없으면 빈 배열)"],
  "closed_world_rewrite": "폐쇄 세계를 완전히 준수하는 리라이트 아이디어 (외부 자원 제거 버전)",
  "asit_score": 8,
  "summary": "ASIT 폐쇄 세계 관점: [준수 여부] + [도구 적합성] + [개선 방향] (1~2문장)"
}}"""

    else:  # 분리원리
        user = f"""아이디어를 TRIZ 분리원리(Separation Principles) 관점에서 전문가 수준으로 분석하세요.

<context>
문제 요약: {req.problem_summary}
물리 모순: {req.physical_contradiction or "아이디어 설명에서 내재된 물리 모순을 추론하세요"}
</context>

<idea>
아이디어명: {req.idea_name}
설명: {req.idea_description}
</idea>

<task>
물리 모순: 하나의 대상이 상반된 두 속성(A이면서 동시에 B)을 동시에 요구받는 상황

1. 물리 모순 명확화
   - 이 문제에 내재된 물리 모순을 "X는 [특성A]이어야 한다(이유) + 동시에 [특성B]이어야 한다(이유)" 형식으로 명확화
   - 두 속성이 왜 동시에 충족되기 어려운지 설명

2. 분리원리 분석
   이 아이디어가 4가지 분리원리 중 어느 것을 적용하는가:
   - 시간 분리: 속성A는 시간t1에, 속성B는 시간t2에 적용
   - 공간 분리: 속성A는 공간S1에, 속성B는 공간S2에 적용
   - 조건 분리: 조건C1에서A, 조건C2에서B
   - 전체-부분 분리: 전체 시스템은A, 부분 구성요소는B

3. 분리 메커니즘 구체화
   - 이 아이디어에서 두 속성이 실제로 어떻게 분리되는가?
   - 분리가 불완전하다면 어떤 상황에서 모순이 재발하는가?

4. 최적 분리원리 제안
   - 현재 아이디어에 적용된 분리원리 외에 더 효과적인 분리원리가 있는가?
   - 있다면 어떻게 적용하는가?
</task>

출력 JSON:
{{
  "framework": "분리원리",
  "physical_contradiction_clarified": "X는 [특성A]이어야 한다([이유]) / 동시에 [특성B]이어야 한다([이유])",
  "physical_contradiction_resolved": true,
  "applied_principle": "시간 분리/공간 분리/조건 분리/전체-부분 분리",
  "separation_mechanism": "이 아이디어에서 속성A와 속성B가 실제로 어떻게 분리되는지 구체적 설명",
  "separation_gap": "분리가 불완전한 경우 — 어떤 상황에서 모순이 재발할 수 있는가 (완전하면 '없음')",
  "best_alternative_principle": "더 효과적인 분리원리 제안 (있으면 구체적 적용 방법 포함)",
  "separation_score": 7,
  "summary": "분리원리 관점: [어떤 모순을 어떤 방식으로 분리하는지] (1~2문장)"
}}"""

    raw = await call_ai_async(system, user, max_tokens=2048)
    result = parse_json_safe(raw)
    result["framework"] = framework_name  # 파싱 실패 폴백에도 키 보장
    return result



# ──────────────────────────────────────────────
# 미리보는세계 — 기사 AI Q&A 생성
# ──────────────────────────────────────────────
class QARequest(BaseModel):
    title: str
    description: str = ""
    category: str = "society"
    article_id: Optional[str] = None

# 간단한 인메모리 캐시 (최대 200건)
_qa_cache: dict[str, list] = {}

@app.post("/api/qa")
async def generate_qa(req: QARequest):
    """기사 제목·본문으로 AI Q&A 3쌍 생성."""
    cache_key = req.article_id or f"{req.title[:60]}"
    if cache_key in _qa_cache:
        return {"qa": _qa_cache[cache_key], "cached": True}

    cat_labels = {
        "politics": "정치", "economy": "경제", "society": "사회",
        "international": "국제", "culture": "문화", "opinion": "오피니언",
        "entertainment": "연예", "sports": "스포츠", "photo": "포토", "all": "일반",
    }
    cat_label = cat_labels.get(req.category, "일반")

    system = (
        "당신은 독자가 뉴스를 더 깊이 이해하도록 돕는 저널리즘 전문 AI입니다. "
        "기사 내용을 바탕으로 독자가 궁금해할 질문 3개와 그에 대한 명확하고 간결한 답변을 작성하세요. "
        "질문은 기사의 핵심 사실·배경·영향 중 하나를 다루어야 합니다. "
        "답변은 기사 내용에 근거하되, 독자가 이해하기 쉽게 2~4문장으로 작성하세요. "
        "반드시 JSON 형식으로만 응답하세요."
    )
    user = f"""다음 [{cat_label}] 기사를 읽고 Q&A 3쌍을 작성하세요.

제목: {req.title}
본문 요약: {req.description[:800] if req.description else '(본문 없음)'}

출력 JSON (다른 텍스트 없이 JSON만):
[
  {{"q": "첫번째 질문 (기사 핵심 사실 관련)", "a": "답변 (2~4문장)"}},
  {{"q": "두번째 질문 (배경·원인 관련)", "a": "답변 (2~4문장)"}},
  {{"q": "세번째 질문 (영향·전망 관련)", "a": "답변 (2~4문장)"}}
]"""

    try:
        raw = await call_ai_async(system, user, max_tokens=1024)
        # JSON 배열 추출
        import re
        m = re.search(r'\[.*\]', raw, re.DOTALL)
        if not m:
            raise ValueError("JSON 배열 없음")
        qa_list = json.loads(m.group())
        # 캐시 크기 관리
        if len(_qa_cache) >= 200:
            oldest = next(iter(_qa_cache))
            del _qa_cache[oldest]
        _qa_cache[cache_key] = qa_list
        return {"qa": qa_list, "cached": False}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
    top10: list = []           # [{name, avg_score, timing, scores:{전략기획자,TRIZ전문가,고객}}]
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
        "당신은 TRIZ/ASIT 기반 실행 전략 전문가입니다.\n"
        "추상적 아이디어를 현장에서 즉시 실행 가능한 구체적 행동으로 변환하는 것이 핵심 임무입니다.\n"
        "반드시 valid JSON만 출력하세요.\n\n"
        "# 실행 계획 품질 기준\n\n"
        "## 좋은 실행 계획의 조건\n"
        "1. 첫 행동의 구체성: 'AI 시스템을 도입한다'는 불가. '이번 주 화요일 기획팀장과 30분 미팅을 잡아 A/B 테스트 항목 3개를 확정한다'는 가능\n"
        "2. 역할 명확성: '담당자'가 아닌 '편집국 데스크' 또는 '구독팀 팀장' 등 구체적 역할\n"
        "3. 시나리오 현실성: 사용자가 실제로 경험하는 장면을 구체적 상황으로 묘사 (추상적 기대 효과 금지)\n"
        "4. 성공 지표 측정 가능성: '만족도 향상'이 아닌 '구독 유지율 3%p 향상 (현재 85% → 88%)'\n"
        "5. 자원 현실성: 실제로 현재 보유한 자원과 역할 기반으로만 계획 수립\n"
        "   → 기존 자원 활용 우선. 신규 채용이나 외부 도구 도입이 불가피하면 명시적으로 표기\n\n"
        "## 나쁜 실행 계획 예시 (작성 금지)\n"
        "- 'AI 기반 개인화 시스템 구축' → 언제, 누가, 어떻게, 얼마나?\n"
        "- '독자 경험 개선' → 무엇을, 어떻게 측정하는가?\n"
        "- '팀 협의 후 진행' → 누가, 언제, 어떤 결정을 내려야 하는가?\n"
    )

    user = f"""아래 Top 아이디어를 즉시 실행 가능한 계획으로 변환하세요.

컨텍스트: {req.problem_summary} / 목표: {req.goal} / 자원: {req.existing_elements or "기존 인력·시스템·데이터"}

아이디어:
{ideas_text}

⚠️ 응답 길이 제한 — 각 항목을 최대한 간결하게 작성:
- first_action: 1문장 (이번 주 [요일] [역할]이 [행동])
- application_ideas: 1개만 (title 10자 이내, scenario 1문장, value 1문장)
- steps: 정확히 3개 (action+who+when만, detail 생략)
- success_metrics: 1개 ("[지표]: 현재X→목표Y")
- obstacles: 1개 (problem+solution 각 1문장)
- resources_needed: 2개 이내

출력 JSON (간결하게):
{{
  "action_plans": [
    {{
      "rank": 1,
      "name": "아이디어명",
      "avg_score": 8.0,
      "timing": "즉시/단기/중기/장기",
      "triz_asit_principle": "원리명",
      "first_action": "이번 주 [요일], [역할]이 [구체적 행동]",
      "application_ideas": [
        {{
          "title": "서비스명",
          "scenario": "나는 [상황]에서 [경험]을 한다.",
          "value": "[지표]: 현재X → 예상Y"
        }}
      ],
      "steps": [
        {{"step":1,"action":"행동명","who":"역할","when":"1주차"}},
        {{"step":2,"action":"행동명","who":"역할","when":"2주차"}},
        {{"step":3,"action":"행동명","who":"역할","when":"1개월 내"}}
      ],
      "resources_needed": ["자원1","자원2"],
      "success_metrics": ["[지표]: 현재X → 목표Y"],
      "obstacles": [{{"problem":"장애물","solution":"해결책"}}]
    }}
  ],
  "quick_start": "오늘 당장 할 1가지 행동 (1문장)",
  "30day_sprint": "30일 집중 계획 (1문장)"
}}"""

    try:
        raw = await call_ai_async(system, user, max_tokens=8000)  # Top10 × 간결 계획 완전 출력
        result = parse_json_safe(raw)
        if "raw" in result and len(result) == 1:
            raise ValueError("JSON 파싱 실패")
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────
# Telegram Bot — Hoya Jaeho Bot
# ──────────────────────────────────────────────
import hmac as _hmac

_TG_TOKEN       = os.environ.get("TELEGRAM_BOT_TOKEN", "")
_GH_PAT         = os.environ.get("GH_PAT", "")
_TG_ALLOWED     = set(filter(None, os.environ.get("TELEGRAM_ALLOWED_IDS", "5066621346").split(",")))
_TG_WH_SECRET   = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")  # setWebhook 시 secret_token 으로 등록한 값

_DAILY_REPO  = "jayce0321/daily-thesis"
_DAILY_WF    = "daily.yml"
_DAILY_WF_ALL = "daily-all.yml"
_DAILY_PAGES = "https://jayce0321.github.io/daily-thesis"

_TOPIC_MAP = {
    "economy":  {"name": "경제·투자", "icon": "📊", "html": "{today}.html",         "wf": "daily.yml"},
    "politics": {"name": "정치",      "icon": "🏛️", "html": "{today}-politics.html", "wf": "daily.yml"},
    "culture":  {"name": "컬처",      "icon": "🎬", "html": "{today}-culture.html",  "wf": "daily.yml"},
}


async def _tg_send(chat_id, text: str):
    if not _TG_TOKEN:
        return
    async with httpx.AsyncClient(timeout=10) as c:
        await c.post(
            f"https://api.telegram.org/bot{_TG_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        )


async def _gh(method: str, path: str, body: dict | None = None):
    headers = {
        "Authorization": f"Bearer {_GH_PAT}",
        "Accept": "application/vnd.github.v3+json",
    }
    async with httpx.AsyncClient(timeout=15) as c:
        url = f"https://api.github.com{path}"
        if method == "POST":
            return await c.post(url, headers=headers, json=body)
        if method == "PUT":
            return await c.put(url, headers=headers, json=body)
        return await c.get(url, headers=headers)


async def _cmd_status(chat_id):
    r = await _gh("GET", f"/repos/{_DAILY_REPO}/actions/runs?per_page=5")
    if r.status_code != 200:
        await _tg_send(chat_id, "❌ GitHub API 오류")
        return
    runs = r.json()["workflow_runs"]
    daily = [x for x in runs if "테제" in x["name"] or "daily" in x["name"].lower()][:3] or runs[:3]
    icon  = {"success": "✅", "failure": "❌", "cancelled": "⚠️"}
    lines = ["📊 <b>최근 발행 이력</b>\n"]
    for run in daily:
        s = run.get("conclusion") or run.get("status", "진행중")
        lines.append(f"{icon.get(s,'🔄')} #{run['run_number']} {run['name'][:18]}\n   {s} · {run['created_at'][:10]}")
    await _tg_send(chat_id, "\n".join(lines))


async def _cmd_republish(chat_id):
    r = await _gh("POST", f"/repos/{_DAILY_REPO}/actions/workflows/{_DAILY_WF}/dispatches",
                  {"ref": "main"})
    if r.status_code == 204:
        await _tg_send(chat_id, f"🚀 경제·투자 재발행 트리거!\n약 1~2분 후 업데이트됩니다.\n📎 {_DAILY_PAGES}")
    else:
        await _tg_send(chat_id, f"❌ 트리거 실패 (HTTP {r.status_code})\nGH_PAT 권한을 확인하세요.")


async def _cmd_publish(chat_id, topic: str):
    from datetime import datetime, timezone, timedelta
    today = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")

    if topic == "all":
        r = await _gh("POST", f"/repos/{_DAILY_REPO}/actions/workflows/{_DAILY_WF_ALL}/dispatches",
                      {"ref": "main"})
        if r.status_code == 204:
            await _tg_send(chat_id, "🚀 전체 발행 트리거!\n📊 경제·투자  🏛️ 정치  🎬 컬처\n3개 주제 동시 생성 시작 (~4분)")
        else:
            await _tg_send(chat_id, f"❌ 트리거 실패 (HTTP {r.status_code})")
        return

    if topic not in _TOPIC_MAP:
        await _tg_send(chat_id,
            "📋 사용법: /publish [주제]\n\n"
            "  /publish economy   — 📊 경제·투자\n"
            "  /publish politics  — 🏛️ 정치\n"
            "  /publish culture   — 🎬 컬처\n"
            "  /publish all       — 3개 동시 발행"
        )
        return

    t = _TOPIC_MAP[topic]

    # 파일 큐 방식으로 topic 전달 (_pending_topic.txt → daily_auto.py가 읽어서 삭제)
    import base64 as _b64
    _queue_content = _b64.b64encode(topic.encode()).decode()
    _queue_sha = None
    _queue_r = await _gh("GET", f"/repos/{_DAILY_REPO}/contents/_pending_topic.txt")
    if _queue_r.status_code == 200:
        _queue_sha = _queue_r.json().get("sha")
    _queue_body: dict = {"message": f"topic queue: {topic}", "content": _queue_content}
    if _queue_sha:
        _queue_body["sha"] = _queue_sha
    await _gh("PUT", f"/repos/{_DAILY_REPO}/contents/_pending_topic.txt", _queue_body)

    r = await _gh("POST", f"/repos/{_DAILY_REPO}/actions/workflows/{t['wf']}/dispatches",
                  {"ref": "main"})
    if r.status_code == 204:
        html_name = t["html"].format(today=today)
        await _tg_send(chat_id,
            f"{t['icon']} <b>{t['name']}</b> 발행 트리거!\n"
            f"약 1~2분 후 업데이트됩니다.\n"
            f"📎 {_DAILY_PAGES}/{html_name}"
        )
    else:
        await _tg_send(chat_id, f"❌ 트리거 실패 (HTTP {r.status_code})")


_ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
_CHAT_HISTORY: dict[str, list] = {}  # chat_id → 최근 메시지 히스토리 (최대 10턴)

async def _cmd_chat(chat_id: str, user_text: str):
    """자유 대화: 경제·투자 전문가 JAYCE로 Claude API 호출"""
    if not _ANTHROPIC_KEY:
        await _tg_send(chat_id, "⚠️ API 키가 설정되지 않았습니다.")
        return

    # 히스토리 관리 (최대 10턴 = 20메시지)
    history = _CHAT_HISTORY.setdefault(chat_id, [])
    history.append({"role": "user", "content": user_text})
    if len(history) > 20:
        history[:] = history[-20:]

    payload = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 800,
        "system": (
            "당신은 JAYCE입니다. 경제·투자·시장 전문 AI 어시스턴트로 세계일보 소속입니다.\n"
            "핵심 원칙:\n"
            "- 답변은 간결하게 (3~5문장), 핵심 인사이트 우선\n"
            "- 숫자와 데이터로 뒷받침. 불확실한 것은 솔직히 말함\n"
            "- 투자 권유 아닌 시장 분석 관점으로 답변\n"
            "- 한국어로 답변. 경제 용어는 한글+영문 병기\n"
            "- 이모지는 최소화 (문단 구분용으로만 사용)"
        ),
        "messages": history,
    }

    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": _ANTHROPIC_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=payload,
            )
        if r.status_code == 200:
            reply = r.json()["content"][0]["text"]
            history.append({"role": "assistant", "content": reply})
            await _tg_send(chat_id, reply)
        else:
            await _tg_send(chat_id, f"⚠️ Claude API 오류 ({r.status_code})")
    except Exception as e:
        await _tg_send(chat_id, f"⚠️ 오류: {e}")


async def _cmd_today(chat_id, topic: str = "economy"):
    import re as _re
    from datetime import datetime, timezone, timedelta
    today = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")

    if topic not in _TOPIC_MAP:
        topic = "economy"
    t = _TOPIC_MAP[topic]
    html_name = t["html"].format(today=today)
    url = f"{_DAILY_PAGES}/{html_name}"

    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(url)
    if r.status_code == 200:
        body  = r.text
        start = body.find("<title>"); end = body.find("</title>")
        title = body[start + 7:end].strip() if start != -1 else "제목 없음"
        h2s   = body.find("<h2"); h2e = body.find("</h2>", h2s)
        raw   = body[h2s:h2e + 5] if h2s != -1 else ""
        thesis = _re.sub(r"<[^>]+>", "", raw).strip()[:120]
        await _tg_send(chat_id,
            f"{t['icon']} <b>{today} {t['name']} 데일리 테제</b>\n\n{title}\n\n{thesis}\n\n📎 {url}")
    else:
        await _tg_send(chat_id,
            f"⚠️ 오늘({today}) {t['name']} 리포트가 아직 없습니다.\n"
            f"/publish {topic} 으로 발행할 수 있어요.")


async def _cmd_errors(chat_id):
    r = await _gh("GET", f"/repos/{_DAILY_REPO}/actions/runs?per_page=10&status=failure")
    if r.status_code != 200:
        await _tg_send(chat_id, "❌ GitHub API 오류")
        return
    runs = r.json()["workflow_runs"]
    if not runs:
        await _tg_send(chat_id, "✅ 최근 실패 이력 없음!")
        return
    lines = [f"🚨 <b>최근 실패 {len(runs)}건</b>\n"]
    for run in runs[:5]:
        lines.append(f"❌ #{run['run_number']} {run['name'][:20]}\n   {run['created_at'][:10]}")
    await _tg_send(chat_id, "\n".join(lines))


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    # 웹훅 시크릿 검증 (TELEGRAM_WEBHOOK_SECRET 설정 시에만 강제)
    if _TG_WH_SECRET:
        incoming = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not _hmac.compare_digest(incoming, _TG_WH_SECRET):
            return {"ok": False}

    body = await request.json()
    msg  = body.get("message") or body.get("edited_message")
    if not msg:
        return {"ok": True}

    chat_id = str(msg["chat"]["id"])
    text    = msg.get("text", "")

    if _TG_ALLOWED and chat_id not in _TG_ALLOWED:
        await _tg_send(chat_id, "⛔ 허가되지 않은 사용자입니다.")
        return {"ok": True}

    parts = text.split() if text else []
    cmd   = parts[0].lower().split("@")[0] if parts else ""
    arg1  = parts[1].lower() if len(parts) > 1 else ""

    try:
        if   cmd == "/status":    await _cmd_status(chat_id)
        elif cmd == "/republish": await _cmd_republish(chat_id)
        elif cmd == "/today":     await _cmd_today(chat_id, arg1 or "economy")
        elif cmd == "/errors":    await _cmd_errors(chat_id)
        elif cmd == "/publish":   await _cmd_publish(chat_id, arg1 or "economy")
        elif cmd in ("/help", "/start"):
            await _tg_send(chat_id,
                "📡 <b>JAYCE</b> — 경제·투자 AI 어시스턴트\n\n"
                "<b>💬 자유 대화</b>\n"
                "명령어 없이 질문하면 경제·투자 전문가로 답변합니다.\n\n"
                "<b>📋 조회 명령어</b>\n"
                "/today [economy|politics|culture] — 오늘 테제 요약\n"
                "/status — 최근 발행 이력\n\n"
                "<b>🚀 발행 명령어</b>\n"
                "/publish economy — 📊 경제·투자 발행\n"
                "/publish politics — 🏛️ 정치 발행\n"
                "/publish culture — 🎬 컬처 발행\n"
                "/publish all — 3개 동시 발행\n\n"
                "/help — 이 메뉴"
            )
        elif text and not cmd.startswith("/"):
            await _cmd_chat(chat_id, text)
    except Exception as _e:
        import traceback
        traceback.print_exc()
        await _tg_send(chat_id, f"⚠️ 오류 발생: {type(_e).__name__}: {_e}")

    return {"ok": True}


@app.get("/telegram/set-webhook")
async def telegram_set_webhook(url: str = Query(..., description="Railway 서버 전체 URL, 예: https://xxx.up.railway.app/telegram/webhook")):
    """웹훅 등록. 배포 직후 한 번만 호출하면 됩니다."""
    if not _TG_TOKEN:
        return {"error": "TELEGRAM_BOT_TOKEN이 설정되지 않았습니다."}
    payload: dict = {"url": url}
    if _TG_WH_SECRET:
        payload["secret_token"] = _TG_WH_SECRET
    async with httpx.AsyncClient() as c:
        r = await c.post(
            f"https://api.telegram.org/bot{_TG_TOKEN}/setWebhook",
            json=payload,
        )
    return r.json()


# ──────────────────────────────────────────────
# 진입점
# ──────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    print("🚀 TRIZ/ASIT 대시보드 서버 시작 중...")
    print("🌐 브라우저에서 http://localhost:8000 으로 접속하세요")
    uvicorn.run(app, host="0.0.0.0", port=8000)
