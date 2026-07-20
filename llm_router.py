"""
llm_router.py — Claude(Anthropic) / Groq 자동 선택 라우터
task 값에 따라 최적 백엔드와 모델을 자동으로 고릅니다.

사용법:
    from llm_router import ask, ask_stream, status

    # 스트리밍 (FastAPI SSE):
    async for chunk in ask_stream(system, user, task="proofread"):
        yield chunk

    # 단순 텍스트 결과:
    result = await ask(system, user, task="summary")

    # 현재 상태 확인:
    print(status())
"""
import os, json
from typing import AsyncGenerator, Optional
from dotenv import load_dotenv

# ── 환경변수 로드 ─────────────────────────────────────────
_ENV_PATH = os.path.expanduser("~/.anthropic/triz.env")
if os.path.isfile(_ENV_PATH):
    load_dotenv(_ENV_PATH, override=True)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GROQ_API_KEY      = os.environ.get("GROQ_API_KEY", "")

# ── 모델 상수 ─────────────────────────────────────────────
CLAUDE_SONNET = "claude-sonnet-4-6"
CLAUDE_HAIKU  = "claude-haiku-4-5-20251001"
GROQ_70B      = "llama-3.3-70b-versatile"   # 범용 고품질
GROQ_8B       = "llama-3.1-8b-instant"       # 초고속 경량

# ── task → (백엔드, 모델, max_tokens) 라우팅 테이블 ─────────
# Claude: 한국어 품질이 결정적인 작업
# Groq  : 속도·비용이 중요한 반복 작업
_ROUTE: dict[str, tuple[str, str, int]] = {
    "writing":   ("claude", CLAUDE_SONNET, 4096),  # 최종 기사·문서 작성
    "analysis":  ("claude", CLAUDE_SONNET, 4096),  # 복잡한 분석·추론
    "aeo":       ("claude", CLAUDE_SONNET, 4096),  # AEO 최적화
    "proofread": ("groq",   GROQ_70B,     2048),   # 교열·문법 교정
    "summary":   ("groq",   GROQ_70B,     2048),   # 요약
    "draft":     ("groq",   GROQ_70B,     4096),   # 초안 생성
    "classify":  ("groq",   GROQ_8B,       512),   # 분류·태깅 (가장 빠름)
    "translate": ("groq",   GROQ_70B,     2048),   # 번역
    "extract":   ("groq",   GROQ_70B,     2048),   # 정보 추출
}
_DEFAULT: tuple[str, str, int] = ("claude", CLAUDE_SONNET, 4096)


def _get_route(task: str) -> tuple[str, str, int]:
    return _ROUTE.get(task, _DEFAULT)


# ── Groq 스트리밍 ─────────────────────────────────────────
async def _stream_groq(
    system: str, user: str, model: str, max_tokens: int
) -> AsyncGenerator[str, None]:
    if not GROQ_API_KEY:
        yield 'data: {"error": "GROQ_API_KEY 미설정 — ~/.anthropic/triz.env 확인"}\n\n'
        return

    from groq import AsyncGroq
    client = AsyncGroq(api_key=GROQ_API_KEY)
    full_text = ""

    try:
        stream = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            max_tokens=max_tokens,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content or ""
            if delta:
                full_text += delta
                yield f"data: {json.dumps({'delta': delta}, ensure_ascii=False)}\n\n"

        yield f"data: {json.dumps({'done': True, 'full': full_text, 'backend': 'groq', 'model': model}, ensure_ascii=False)}\n\n"

    except Exception as e:
        yield f"data: {json.dumps({'error': f'Groq 오류: {e}'})}\n\n"


# ── Claude 스트리밍 ───────────────────────────────────────
async def _stream_claude(
    system: str, user: str, model: str, max_tokens: int
) -> AsyncGenerator[str, None]:
    if not ANTHROPIC_API_KEY:
        yield 'data: {"error": "ANTHROPIC_API_KEY 미설정 — ~/.anthropic/triz.env 확인"}\n\n'
        return

    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    full_text = ""

    try:
        with client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        ) as stream:
            for text in stream.text_stream:
                full_text += text
                yield f"data: {json.dumps({'delta': text}, ensure_ascii=False)}\n\n"

        yield f"data: {json.dumps({'done': True, 'full': full_text, 'backend': 'claude', 'model': model}, ensure_ascii=False)}\n\n"

    except Exception as e:
        yield f"data: {json.dumps({'error': f'Claude 오류: {e}'})}\n\n"


# ── 공개 API ─────────────────────────────────────────────

async def ask_stream(
    system: str,
    user: str,
    task: str = "writing",
    max_tokens: Optional[int] = None,
) -> AsyncGenerator[str, None]:
    """
    SSE 스트리밍 제너레이터.
    task에 따라 Claude 또는 Groq를 자동 선택합니다.

    Args:
        system: 시스템 프롬프트
        user:   사용자 프롬프트
        task:   작업 유형 (writing/analysis/aeo/proofread/summary/draft/classify/translate/extract)
        max_tokens: 최대 토큰 수 (None이면 task별 기본값 사용)

    Yields:
        "data: {...}\n\n" 형식의 SSE 문자열
    """
    backend, model, default_tokens = _get_route(task)
    tokens = max_tokens or default_tokens

    # Groq 키가 없으면 Claude로 폴백
    if backend == "groq" and not GROQ_API_KEY:
        backend = "claude"
        model   = CLAUDE_HAIKU  # 교열 등 경량 작업은 Haiku로 폴백

    if backend == "groq":
        async for chunk in _stream_groq(system, user, model, tokens):
            yield chunk
    else:
        async for chunk in _stream_claude(system, user, model, tokens):
            yield chunk


async def ask(
    system: str,
    user: str,
    task: str = "writing",
    max_tokens: Optional[int] = None,
) -> str:
    """
    단순 텍스트 반환 (스트리밍 없음).
    스트리밍 결과를 내부에서 수집해 전체 텍스트를 반환합니다.
    """
    full = ""
    async for chunk in ask_stream(system, user, task, max_tokens):
        if chunk.startswith("data: "):
            try:
                obj = json.loads(chunk[6:])
                if "delta" in obj:
                    full += obj["delta"]
            except json.JSONDecodeError:
                pass
    return full


def ask_sync(
    system: str,
    user: str,
    task: str = "writing",
    max_tokens: Optional[int] = None,
) -> tuple:
    """
    동기 호출 (Flask 등 sync 환경용).
    Returns: (text, error) — 성공 시 (결과문자열, None), 실패 시 (None, 오류메시지)

    사용 예:
        result, err = ask_sync(system, user, task="summary")
        if err:
            return jsonify({'error': err}), 500
    """
    backend, model, default_tokens = _get_route(task)
    tokens = max_tokens or default_tokens

    if backend == "groq" and not GROQ_API_KEY:
        backend = "claude"
        model   = CLAUDE_HAIKU

    try:
        if backend == "groq":
            if not GROQ_API_KEY:
                return None, "GROQ_API_KEY 미설정 — ~/.anthropic/triz.env 확인"
            from groq import Groq
            client = Groq(api_key=GROQ_API_KEY)
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user},
                ],
                max_tokens=tokens,
            )
            return resp.choices[0].message.content, None
        else:
            if not ANTHROPIC_API_KEY:
                return None, "ANTHROPIC_API_KEY 미설정 — ~/.anthropic/triz.env 확인"
            import anthropic
            client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            msg = client.messages.create(
                model=model,
                max_tokens=tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            return msg.content[0].text, None
    except Exception as e:
        return None, str(e)


def status() -> dict:
    """현재 API 키 설정 상태와 라우팅 테이블 반환"""
    routes_display = {
        task: f"{b} / {m.split('/')[-1]}"
        for task, (b, m, _) in _ROUTE.items()
    }
    return {
        "claude_available": bool(ANTHROPIC_API_KEY),
        "groq_available":   bool(GROQ_API_KEY),
        "routes": routes_display,
    }
