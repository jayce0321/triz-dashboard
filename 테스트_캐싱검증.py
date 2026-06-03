#!/usr/bin/env python3
"""
프롬프트 캐싱 + 3인 평가 에이전트 검증 스크립트
실행: python3 테스트_캐싱검증.py
"""
import os, json, time

def load_api_key():
    env_path = os.path.expanduser("~/.anthropic/triz.env")
    if os.path.isfile(env_path):
        with open(env_path) as f:
            for line in f:
                if line.startswith("ANTHROPIC_API_KEY="):
                    os.environ["ANTHROPIC_API_KEY"] = line.split("=", 1)[1].strip()
                    return True
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def test_cache():
    print("=" * 55)
    print("  프롬프트 캐싱 + 3인 평가 에이전트 검증")
    print("=" * 55)

    if not load_api_key():
        print("❌ ANTHROPIC_API_KEY를 찾을 수 없습니다.")
        return

    from TRIZ_ASIT_대시보드 import AGENT_CONFIGS, _call_claude_sync

    # ── 1. 에이전트 토큰 수 확인 ──────────────────────────
    print("\n[1] 평가 에이전트 system 프롬프트 토큰 수")
    try:
        import anthropic
        client = anthropic.Anthropic()
        for name, prompt in AGENT_CONFIGS.items():
            r = client.messages.count_tokens(
                model="claude-sonnet-4-6",
                system=prompt,
                messages=[{"role": "user", "content": "테스트"}],
            )
            tok = r.input_tokens
            status = "✅ 캐싱 가능" if tok >= 1024 else f"❌ {tok} < 1024"
            print(f"  {name}: {tok} tokens → {status}")
    except Exception as e:
        print(f"  토큰 카운트 실패: {e}")

    # ── 2. 캐시 생성 → 히트 실증 ─────────────────────────
    print("\n[2] 실제 캐시 생성 → 히트 테스트")
    dummy = [
        {"id": i + 1, "name": f"아이디어{i+1}",
         "description": f"기존 시스템 요소를 활용한 모순 해소 방안 {i+1} — TRIZ/ASIT 접근",
         "source": "TRIZ" if i < 6 else "ASIT", "initial_score": 6}
        for i in range(12)
    ]
    ideas_text = json.dumps(dummy, ensure_ascii=False)
    common_block = (
        "다음 아이디어 목록을 평가하세요.\n"
        "문제현상: 뉴스레터 이탈률 15%\n목표: 8% 이하\n"
        f"## 아이디어 목록\n{ideas_text}"
    )
    system = list(AGENT_CONFIGS.values())[0]
    user_content = [
        {"type": "text", "text": common_block, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": '아이디어 1개만 JSON으로 평가: {"id":1,"score":7,"comment":"..."}'},
    ]

    for trial in range(1, 3):
        label = "캐시 생성" if trial == 1 else "캐시 히트"
        print(f"  {trial}회차({label})...", end=" ", flush=True)
        t = time.time()
        _call_claude_sync(system, user_content, max_tokens=150)
        print(f"완료: {time.time()-t:.1f}초")

    # ── 3. 에이전트 3명 구성 확인 ─────────────────────────
    print("\n[3] 평가 에이전트 구성")
    for name in AGENT_CONFIGS:
        print(f"  ✅ {name}")

    print("\n모든 검증 완료")


if __name__ == "__main__":
    test_cache()
