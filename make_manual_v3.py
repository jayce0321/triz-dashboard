"""TRIZ/ASIT 사용자 매뉴얼 v3.0 생성 스크립트"""
import os
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, PageBreak, KeepTogether
)
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ── 한글 폰트 등록 (세계일보 브랜드 폰트) ─────
FONT_DIR = os.path.expanduser("~/Library/Fonts")
pdfmetrics.registerFont(TTFont("Korean",      os.path.join(FONT_DIR, "세계하나본문명조.ttf")))
pdfmetrics.registerFont(TTFont("Korean-Bold", os.path.join(FONT_DIR, "세계하나제목고딕_굵은.ttf")))

# ── 색상 정의 ──────────────────────────────────
BLUE      = colors.HexColor("#2367d7")   # 세계일보 시그니처 블루
NAVY      = colors.HexColor("#1a4fa0")
LIGHTBLUE = colors.HexColor("#e8f0fd")
GRAY      = colors.HexColor("#f8f9fa")
DARKGRAY  = colors.HexColor("#495057")
GREEN     = colors.HexColor("#198754")
ORANGE    = colors.HexColor("#fd7e14")

W, H = A4

# ── 스타일 정의 ────────────────────────────────
def make_styles():
    s = {}
    base = dict(fontName="Korean", leading=16)
    def ps(name, **kw):
        d = {**base, **kw}
        return ParagraphStyle(name, **d)

    s["title"]    = ps("title",    fontSize=26, fontName="Korean-Bold", textColor=BLUE,    alignment=1, spaceAfter=4)
    s["subtitle"] = ps("subtitle", fontSize=13, textColor=DARKGRAY,                        alignment=1, spaceAfter=2)
    s["url"]      = ps("url",      fontSize=11, textColor=BLUE,                            alignment=1, spaceAfter=8)
    s["h1"]       = ps("h1",       fontSize=15, fontName="Korean-Bold", textColor=BLUE,    spaceBefore=14, spaceAfter=6)
    s["h2"]       = ps("h2",       fontSize=12, fontName="Korean-Bold", textColor=NAVY,    spaceBefore=10, spaceAfter=4)
    s["h3"]       = ps("h3",       fontSize=11, fontName="Korean-Bold", textColor=DARKGRAY,spaceBefore=6,  spaceAfter=3)
    s["body"]     = ps("body",     fontSize=10,                                             spaceAfter=4, leading=15)
    s["bullet"]   = ps("bullet",   fontSize=10, leftIndent=12, firstLineIndent=-12,         spaceAfter=3, leading=14)
    s["small"]    = ps("small",    fontSize=9,  textColor=DARKGRAY,                         spaceAfter=2)
    s["caption"]  = ps("caption",  fontSize=8,  textColor=DARKGRAY,  alignment=1)
    s["callout"]  = ps("callout",  fontSize=10, fontName="Korean-Bold", textColor=NAVY,    leftIndent=10, spaceAfter=3)
    s["tip"]      = ps("tip",      fontSize=9,  textColor=GREEN,       leftIndent=10, spaceAfter=3)
    s["warn"]     = ps("warn",     fontSize=9,  textColor=ORANGE,      leftIndent=10, spaceAfter=3)
    s["center"]   = ps("center",   fontSize=10, alignment=1, spaceAfter=3)
    s["cost"]     = ps("cost",     fontSize=11, fontName="Korean-Bold", textColor=GREEN,   spaceAfter=4)
    return s

ST = make_styles()

def hr(color=BLUE, thickness=1):
    return HRFlowable(width="100%", thickness=thickness, color=color, spaceAfter=4, spaceBefore=4)

def blue_table(data, col_widths, header=True):
    ts = [
        ("FONTNAME",  (0,0), (-1,-1), "Korean"),
        ("FONTSIZE",  (0,0), (-1,-1), 9),
        ("ROWBACKGROUNDS", (0,0), (-1,-1), [colors.white, GRAY]),
        ("GRID",      (0,0), (-1,-1), 0.4, colors.HexColor("#dee2e6")),
        ("VALIGN",    (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING",(0,0), (-1,-1), 5),
        ("BOTTOMPADDING",(0,0), (-1,-1), 5),
        ("LEFTPADDING",(0,0), (-1,-1), 7),
    ]
    if header:
        ts += [
            ("BACKGROUND",  (0,0), (-1,0), BLUE),
            ("TEXTCOLOR",   (0,0), (-1,0), colors.white),
            ("FONTNAME",    (0,0), (-1,0), "Korean-Bold"),
            ("FONTSIZE",    (0,0), (-1,0), 9),
        ]
    return Table([[Paragraph(str(c), ParagraphStyle("tc", fontName="Korean", fontSize=9, leading=13, textColor=colors.white if header and r==0 else colors.black)) for c in row] for r, row in enumerate(data)],
                 colWidths=col_widths, style=TableStyle(ts), hAlign="LEFT")

OUT = "/Users/ijaeho/Library/Mobile Documents/com~apple~CloudDocs/허세계2일차/TRIZ_ASIT_사용자매뉴얼_v3.pdf"
doc = SimpleDocTemplate(OUT, pagesize=A4,
                        leftMargin=20*mm, rightMargin=20*mm,
                        topMargin=18*mm, bottomMargin=18*mm)
story = []

# ═══════════════════════════════════════════════════
# 표지
# ═══════════════════════════════════════════════════
story += [
    Spacer(1, 20*mm),
    Paragraph("세계일보 기획팀 내부 배포용 자료", ST["small"]),
    Spacer(1, 6*mm),
    Paragraph("TRIZ / ASIT", ST["title"]),
    Paragraph("AI 에이전트 분석 대시보드", ST["title"]),
    Spacer(1, 4*mm),
    Paragraph("사용자 매뉴얼", ParagraphStyle("m", fontName="Korean-Bold", fontSize=18, textColor=NAVY, alignment=1)),
    Spacer(1, 6*mm),
    Paragraph("Version 3.0 · 2026-06-04", ST["subtitle"]),
    Paragraph("세계일보 기획팀", ST["subtitle"]),
    Paragraph("내부 배포용 자료 | 무단 복제 금지", ST["small"]),
    Spacer(1, 4*mm),
    Paragraph("https://triz-dashboard-production.up.railway.app", ST["url"]),
    Spacer(1, 10*mm),
]

# 변경 요약 박스
chg_data = [
    ["버전", "주요 변경 내용"],
    ["v3.0  2026-06-04", "AI 백엔드 Claude Sonnet 4.6 전환 · TRIZ/ASIT 각 8개 생성 · Top 10 선정 기반 16개 풀 확대 · 실행 아이디어 10개 전체 제공 · 평가단 3인 재편 · 버튼 잠김 버그 수정"],
    ["v2.0  2026-06-02", "실행 아이디어 탭 신규 추가 · Railway 메인 배포 완료 · 프롬프트 캐싱 적용"],
    ["v1.0  2026-05-18", "초기 릴리스 · Gemini AI 백엔드 · 4인 평가단"],
]
story.append(blue_table(chg_data, [38*mm, 122*mm]))
story.append(PageBreak())

# ═══════════════════════════════════════════════════
# 1장. 개요
# ═══════════════════════════════════════════════════
story += [Paragraph("1장. 개요", ST["h1"]), hr()]
story.append(Paragraph("■ 도구 소개", ST["h2"]))
story.append(Paragraph(
    "TRIZ/ASIT AI 에이전트 분석 대시보드는 러시아 발명가 알트슐러가 개발한 TRIZ(창의적 문제해결 이론)와 "
    "이를 실무에 맞게 단순화한 ASIT 방법론을 Claude AI 에이전트로 구현한 도구입니다. "
    "해결하고자 하는 문제를 자유 입력하면, 전략기획자·TRIZ전문가·고객 3인의 AI 에이전트가 아이디어를 평가하고 "
    "실행 가능한 계획을 자동으로 생성합니다.", ST["body"]))

story.append(Paragraph("■ v3.0 핵심 변경사항", ST["h2"]))
changes = [
    ["항목", "v2.0 (이전)", "v3.0 (현재)", "효과"],
    ["AI 백엔드", "Gemini 2.5 Flash", "Claude Sonnet 4.6", "정확도·일관성 향상"],
    ["TRIZ 아이디어", "6개 생성", "8개 생성", "아이디어 다양성 확대"],
    ["ASIT 아이디어", "6개 생성", "8개 생성", "5가지 도구 균등 활용"],
    ["전체 풀 크기", "12개", "16개 (TRIZ8+ASIT8)", "의미 있는 37% 컷"],
    ["Top 10 선정", "12개 중 10개 (17% 컷)", "16개 중 10개 (37% 컷)", "선정 신뢰도 향상"],
    ["실행 아이디어", "상위 5개만", "전체 10개", "완전한 실행 로드맵"],
    ["평가 에이전트", "4인(기획자·컨설턴트·엔지니어·고객)", "3인(전략기획자·TRIZ전문가·고객)", "역할 명확화·비용 최적화"],
]
story.append(blue_table(changes, [30*mm, 38*mm, 42*mm, 40*mm]))
story.append(Spacer(1, 4*mm))

story.append(Paragraph("■ 주요 기능", ST["h2"]))
features = [
    ["기능", "설명"],
    ["문제 구조화", "자유 텍스트 → 기술모순·물리모순·IFR(이상적 최종 결과) 자동 추출"],
    ["아이디어 생성", "TRIZ 발명원리 8개 + ASIT 5가지 도구 8개 = 총 16개 아이디어"],
    ["3인 에이전트 평가", "전략기획자·TRIZ전문가·고객 관점의 독립적 점수(루브릭 채점) 및 피드백"],
    ["Top 10 선정", "16개 풀에서 모순 해소력·컨센서스·실행가능성 기준으로 10개 선정"],
    ["실행 아이디어", "Top 10 전체에 대한 실행 단계·성공 지표·적용 시나리오 자동 생성"],
    ["프레임워크 피드백", "IFR·ASIT 폐쇄세계·분리원리 3가지 관점으로 단일 아이디어 심층 평가"],
]
story.append(blue_table(features, [42*mm, 118*mm]))

story.append(Paragraph("■ 접속 방법", ST["h2"]))
access = [
    ["구분", "URL", "특징"],
    ["✅ Railway 메인 (권장)", "https://triz-dashboard-production.up.railway.app", "Claude AI 실시간 분석 · 팀 URL 공유"],
    ["로컬 서버 (개발용)", "http://localhost:8000", "소스 수정·테스트 시 사용"],
]
story.append(blue_table(access, [42*mm, 88*mm, 30*mm]))
story.append(Spacer(1, 2*mm))
story.append(Paragraph("※ Railway 사이트는 ANTHROPIC_API_KEY가 환경변수로 설정되어 있어 별도 키 입력 없이 Claude AI 분석이 가능합니다.", ST["small"]))
story.append(PageBreak())

# ═══════════════════════════════════════════════════
# 2장. 화면 구성
# ═══════════════════════════════════════════════════
story += [Paragraph("2장. 화면 구성", ST["h1"]), hr()]

story.append(Paragraph("■ 헤더 바", ST["h2"]))
hdr_data = [
    ["영역", "내용"],
    ["좌측", "뇌 모양 아이콘 + 'TRIZ/ASIT AI 에이전트 분석 대시보드' 제목"],
    ["중앙", "운영 모드 배지 · ✨ Claude AI — 백엔드 연결, 실시간 분석 가능 / 데모 모드 — 미연결 시"],
    ["우측", "한국 표준시(KST) 실시간 시계"],
]
story.append(blue_table(hdr_data, [25*mm, 135*mm]))

story.append(Paragraph("■ 사이드바 (입력 폼)", ST["h2"]))
sidebar_data = [
    ["항목", "설명"],
    ["0단계 입력창", "해결하고 싶은 문제 상황을 자유롭게 서술하는 텍스트박스"],
    ["AI 분석 버튼", "[AI 문제 구조 분석] — Claude AI가 문제유형·목표·KPI·구성요소 필드를 자동 채움"],
    ["문제 유형", "상품/서비스 vs 조직/프로세스 자동 감지 (수동 변경 가능)"],
    ["분석 도구", "TRIZ IFR / 분리원리 / 발명원리(8가지) / ASIT 전체(5가지 도구) — 기본 전체 선택"],
    ["시작 버튼", "[AI 에이전트 분석 시작] — 5단계 파이프라인 자동 순차 실행 (~2~3분)"],
]
story.append(blue_table(sidebar_data, [38*mm, 122*mm]))

story.append(Paragraph("■ 탭 구성 (총 7개)", ST["h2"]))
tabs_data = [
    ["번호", "탭 이름", "주요 내용"],
    ["1", "📋 문제 구조화", "기술모순 / 물리모순 / IFR(이상적 최종 결과) / 추천 분석 도구 카드"],
    ["2", "💡 TRIZ 아이디어", "발명원리 기반 8개 아이디어 카드 (원리명·메커니즘·핵심 변화·에이전트 점수)"],
    ["3", "🔧 ASIT 아이디어", "제거·복제·분할·기능통합·대칭파괴 5가지 도구별 총 8개 아이디어"],
    ["4", "👥 에이전트 평가", "3인 에이전트(전략기획자·TRIZ전문가·고객) 점수 + 전체 아이디어 매트릭스"],
    ["5", "🏆 최종 보고서", "16개 풀 → Top 10 순위표 / Quick Wins / 핵심 통찰 3개 / 다음 단계 타임라인"],
    ["6", "🚀 실행 아이디어", "Top 10 전체의 구체적 실행 계획 + AI 적용 시나리오 + 성공 지표"],
    ["7", "🔬 프레임워크 피드백", "아이디어 1건 입력 후 IFR·ASIT·분리원리 3가지 관점 심층 평가"],
]
story.append(blue_table(tabs_data, [12*mm, 40*mm, 108*mm]))
story.append(PageBreak())

# ═══════════════════════════════════════════════════
# 3장. 사용 방법
# ═══════════════════════════════════════════════════
story += [Paragraph("3장. 사용 방법 (단계별)", ST["h1"]), hr()]

steps = [
    ("Step 1. 문제 입력 및 AI 구조 분석",
     "화면 왼쪽 텍스트박스에 해결하고 싶은 문제를 자유롭게 서술합니다.",
     [
         "예시: '디지털 뉴스 구독 해지율이 월 8% 증가하고 있습니다. MZ세대가 무료 SNS로 이탈하며 6개월 안에 해지율 3% 이하가 목표입니다'",
         "[AI 문제 구조 분석] 클릭 → Claude AI가 문제유형·목표·KPI·구성요소 필드를 자동 채움 (약 10초)",
         "자동 입력된 내용을 검토하고 필요 항목을 직접 수정합니다",
         "상품/서비스 ↔ 조직/프로세스 유형이 잘못 감지되면 수동으로 변경하세요",
     ]),
    ("Step 2. AI 에이전트 분석 시작",
     "[AI 에이전트 분석 시작] 버튼을 클릭합니다. 5단계 파이프라인이 순차 자동 실행됩니다 (총 약 2~3분).",
     [
         "1단계 Master 에이전트 — 문제 구조화 (기술/물리 모순, IFR 도출)",
         "2단계 TRIZ 에이전트 — 발명원리 기반 아이디어 8개 생성",
         "3단계 ASIT 에이전트 — 5가지 도구 기반 아이디어 8개 생성",
         "4단계 3인 에이전트 — 전략기획자·TRIZ전문가·고객 관점 독립 평가 (루브릭 채점)",
         "5단계 종합 에이전트 — 16개 풀에서 Top 10 선정 + 실행 아이디어 10개 자동 생성",
         "진행 중 좌측 하단 프로그레스 바(0~100%)와 에이전트 로그로 실시간 확인 가능",
     ]),
    ("Step 3. 결과 확인 (탭 순서대로)",
     "분석 완료 후 7개 탭을 순서대로 확인합니다.",
     [
         "[문제 구조화] 탭 — 기술모순·물리모순·IFR 카드, 추천 분석 도구 확인",
         "[TRIZ 아이디어] 탭 — T01~T08 카드, 각 카드에 에이전트 3인 점수 뱃지 표시",
         "[ASIT 아이디어] 탭 — A01~A08 카드, 도구별 그룹화, 폐쇄 세계 조건 확인",
         "[에이전트 평가] 탭 — 3인 에이전트 요약·TOP3·점수 막대, 전체 매트릭스",
         "[최종 보고서] 탭 — Top 10 순위표 (전략기획자·TRIZ전문가·고객 점수), Quick Wins, 통찰, 타임라인",
         "[실행 아이디어] 탭 — 10개 전체 실행 계획 카드 (타이밍별 그룹화)",
         "[프레임워크 피드백] 탭 — 선택 아이디어 심층 평가 (IFR·ASIT·분리원리)",
     ]),
    ("Step 4. 실행 아이디어 탭 활용 (핵심 기능)",
     "최종 보고서 완료 직후 자동으로 생성됩니다 (별도 클릭 불필요). Top 10 전체의 상세 실행 계획을 제공합니다.",
     [
         "상단 'Quick-start 배너' — 지금 당장 실행할 단 하나의 행동을 제시",
         "타이밍별 그룹 — 즉시 실행 / 1개월 내 / 2개월+ 카드 분류",
         "카드 클릭 → 세부 내용 펼침: 이번 주 첫 행동 / AI 적용 시나리오 / 실행 단계(3단계) / 성공 지표 / 장애물 & 대처법",
         "[실행 계획 내보내기] 버튼으로 Markdown 파일 저장 가능",
     ]),
    ("Step 5. 프레임워크 피드백 (심층 평가, 선택)",
     "특정 아이디어 하나를 3가지 TRIZ/ASIT 관점으로 심층 평가합니다.",
     [
         "아이디어명·설명을 입력 후 [3개 프레임워크로 분석하기] 클릭",
         "IFR 관점 — 이상도 점수(0~10점), IFR 단계 분석",
         "ASIT 폐쇄세계 — 기존 자원만 사용했는지 검증, 위반 시 내부 자원 대안 제시",
         "분리원리 — 물리 모순 해소 방법(시간/공간/조건/전체-부분 분리) 분석",
     ]),
]

for title, intro, bullets in steps:
    story.append(KeepTogether([
        Paragraph(f"▶ {title}", ST["h2"]),
        Paragraph(intro, ST["body"]),
        *[Paragraph(f"• {b}", ST["bullet"]) for b in bullets],
        Spacer(1, 3*mm),
    ]))

story.append(PageBreak())

# ═══════════════════════════════════════════════════
# 4장. 운영 모드 안내
# ═══════════════════════════════════════════════════
story += [Paragraph("4장. 운영 모드 안내", ST["h1"]), hr()]

story.append(Paragraph("■ 모드별 기능 비교", ST["h2"]))
mode_data = [
    ["기능", "Railway (권장)", "로컬 서버 (개발용)"],
    ["Claude AI 실시간 분석", "✅", "✅"],
    ["TRIZ 8개 생성", "✅", "✅"],
    ["ASIT 8개 생성", "✅", "✅"],
    ["Top 10 선정 (16개 풀)", "✅", "✅"],
    ["실행 아이디어 10개", "✅", "✅"],
    ["데모 시뮬레이션 (오프라인)", "✅", "✅"],
    ["팀원 링크 공유", "✅ URL만 전달", "❌ 내부 네트워크만"],
    ["항상 접속 가능", "✅", "실행 시만"],
    ["별도 설치 불필요", "✅", "❌"],
]
story.append(blue_table(mode_data, [80*mm, 35*mm, 45*mm]))

story.append(Spacer(1, 4*mm))
story.append(Paragraph("■ Railway 메인 사이트 (팀원 공유용 — 권장)", ST["h2"]))
railway_data = [
    ["항목", "내용"],
    ["접속 URL", "https://triz-dashboard-production.up.railway.app"],
    ["AI 모델", "Claude Sonnet 4.6 (claude-sonnet-4-6)"],
    ["API 키", "Railway 환경변수 ANTHROPIC_API_KEY 자동 적용 — 별도 입력 불필요"],
    ["배포 방식", "GitHub main 브랜치 push → Railway 자동 배포 (~30초)"],
    ["업타임", "24시간 상시 운영 (Railway 유료 플랜)"],
]
story.append(blue_table(railway_data, [35*mm, 125*mm]))

story.append(Spacer(1, 4*mm))
story.append(Paragraph("■ 로컬 서버 실행 (개발·수정 시)", ST["h2"]))
for line in [
    "1. 가상환경 활성화: source ~/.venv/triz/bin/activate",
    "2. 서버 실행: python3 -m uvicorn \"TRIZ_ASIT_대시보드:app\" --host 0.0.0.0 --port 8000",
    "3. 브라우저 접속: http://localhost:8000",
    "4. API 키 위치: ~/.anthropic/triz.env 파일에 ANTHROPIC_API_KEY=sk-ant-... 저장",
]:
    story.append(Paragraph(f"• {line}", ST["bullet"]))

story.append(Spacer(1, 4*mm))
story.append(Paragraph("■ 업데이트 배포 방법 (코드 수정 후)", ST["h2"]))
deploy_data = [
    ["단계", "명령어 / 방법"],
    ["1. 소스 수정", "TRIZ_ASIT_대시보드.html / TRIZ_ASIT_대시보드.py 수정"],
    ["2. git commit", "git add . && git commit -m \"fix: 변경 내용\""],
    ["3. GitHub push", "git push origin main"],
    ["4. 자동 배포", "Railway가 push 감지 → 자동 빌드 및 배포 (~30초)"],
    ["5. 확인", "https://triz-dashboard-production.up.railway.app/api/status → ready:true"],
]
story.append(blue_table(deploy_data, [30*mm, 130*mm]))
story.append(PageBreak())

# ═══════════════════════════════════════════════════
# 5장. AI 평가 루브릭
# ═══════════════════════════════════════════════════
story += [Paragraph("5장. AI 평가 루브릭 (3인 에이전트)", ST["h1"]), hr()]

story.append(Paragraph("3인 에이전트는 동일한 4항목 루브릭으로 독립 채점합니다. 주관성 제거를 위해 Altshuller 발명수준 기준을 적용합니다.", ST["body"]))

rubric_data = [
    ["평가 항목", "가중치", "9~10점", "7~8점", "5~6점", "1~4점"],
    ["A. 모순 해소력\n(Contradiction Resolution)", "30%",
     "기술·물리 모순 동시 해소\nIFR-1/2 수준",
     "두 모순 중 하나 명확히 해소\nIFR-3 수준",
     "모순 우회 또는 부분 완화",
     "모순 이동 또는 심화"],
    ["B. 신규성 (Novelty)", "20%",
     "국내외 동종 업계 미시도\n(Level 4~5)",
     "해외 사례 있으나 국내 미적용\n(Level 3)",
     "국내 타 업계 사례 있음\n(Level 2~3)",
     "동종 업계 부분 시도\n(Level 1~2)"],
    ["C. 실현가능성 (Feasibility)", "35%",
     "4가지 체크리스트 모두 충족\n(인력·예산·기술·MVP)",
     "3가지 충족",
     "2가지 충족",
     "0~1가지 충족"],
    ["D. 가치 기여도 (Value Impact)", "15%",
     "KPI 직접 개선\n명확한 인과관계",
     "간접적이나 논리적 인과관계",
     "긍정적 영향 예상\n(인과관계 불명확)",
     "목표 지표와 연관성 낮음"],
]
story.append(blue_table(rubric_data, [38*mm, 15*mm, 32*mm, 32*mm, 30*mm, 13*mm]))

story.append(Spacer(1, 3*mm))
story.append(Paragraph("최종 점수 = A(30%) + B(20%) + C(35%) + D(15%) 가중 평균", ST["callout"]))
story.append(Paragraph("⚠ 상위 20%만 8점 이상 — 7~9점 집중 현상 엄격히 금지", ST["warn"]))

story.append(Spacer(1, 4*mm))
story.append(Paragraph("■ 에이전트별 역할", ST["h2"]))
agent_data = [
    ["에이전트", "주요 관점", "집중 평가 항목"],
    ["전략기획자", "조직 실행 가능성, 예산·인력 현실성", "C(실현가능성) 35% 집중 검증"],
    ["TRIZ전문가", "모순 해소 수준, 발명 원리 적용의 정확성", "A(모순 해소력) 30% + B(신규성) 20%"],
    ["고객", "실제 사용자 경험, 체감 가치", "D(가치 기여도) 15% + 현장 수용성"],
]
story.append(blue_table(agent_data, [35*mm, 60*mm, 65*mm]))
story.append(PageBreak())

# ═══════════════════════════════════════════════════
# 6장. 토큰 비용 산정
# ═══════════════════════════════════════════════════
story += [Paragraph("6장. 토큰 비용 산정 (Claude Sonnet 4.6 기준)", ST["h1"]), hr()]

story.append(Paragraph("■ Claude Sonnet 4.6 요금 (2026년 6월 기준)", ST["h2"]))
price_data = [
    ["토큰 유형", "단가", "비고"],
    ["입력 토큰 (Input)", "$3.00 / 1M tokens", "프롬프트·컨텍스트"],
    ["출력 토큰 (Output)", "$15.00 / 1M tokens", "AI 생성 응답"],
    ["캐시 쓰기 (Cache Write)", "$3.75 / 1M tokens", "첫 번째 호출 시"],
    ["캐시 읽기 (Cache Read)", "$0.30 / 1M tokens", "반복 호출 시 (~90% 절약)"],
]
story.append(blue_table(price_data, [45*mm, 55*mm, 60*mm]))

story.append(Spacer(1, 4*mm))
story.append(Paragraph("■ 1회 분석 세션 토큰 사용량 추정", ST["h2"]))

step_data = [
    ["API 호출 단계", "입력 토큰", "출력 토큰", "단계 비용(USD)"],
    ["① 문제 파싱 (parse-problem)", "1,500", "500", "$0.012"],
    ["② 문제 구조화 (step_problem)", "2,500", "1,200", "$0.026"],
    ["③ TRIZ 8개 생성 (step_triz)", "2,800", "4,500", "$0.076"],
    ["④ ASIT 8개 생성 (step_asit)", "2,800", "4,500", "$0.076"],
    ["⑤ 평가 에이전트 1 (전략기획자)", "3,500", "3,500", "$0.063"],
    ["⑤ 평가 에이전트 2 (TRIZ전문가)", "3,500", "3,500", "$0.063"],
    ["⑤ 평가 에이전트 3 (고객)", "3,500", "3,500", "$0.063"],
    ["⑥ Top 10 합성 (step_synthesis)", "2,500", "4,500", "$0.075"],
    ["⑦ 실행 아이디어 10개 (action-plan)", "2,000", "6,000", "$0.096"],
    ["합계", "24,600 tokens", "31,700 tokens", "≈ $0.55"],
]
story.append(blue_table(step_data, [68*mm, 28*mm, 28*mm, 36*mm]))

story.append(Spacer(1, 2*mm))
story.append(Paragraph("※ 실제 비용은 입력 문제의 길이·복잡도에 따라 ±20% 변동 가능합니다.", ST["small"]))
story.append(Paragraph("※ 프롬프트 캐싱 적용 시(평가 단계 공유 컨텍스트) 약 5~10% 추가 절약 가능합니다.", ST["small"]))

story.append(Spacer(1, 5*mm))
story.append(Paragraph("■ 사용량별 월간 비용 예측", ST["h2"]))

cost_data = [
    ["사용 시나리오", "일일 분석 횟수", "월간 분석 횟수", "월 예상 비용(USD)", "월 예상 비용(KRW)"],
    ["소규모 (개인·소팀)", "3회", "90회", "≈ $49.5", "약 68,000원"],
    ["중규모 (기획팀 공유)", "10회", "300회", "≈ $165", "약 227,000원"],
    ["대규모 (부서 전체)", "20회", "600회", "≈ $330", "약 455,000원"],
    ["집중 워크숍 (월 1회)", "—", "30회", "≈ $16.5", "약 23,000원"],
]
story.append(blue_table(cost_data, [42*mm, 28*mm, 28*mm, 34*mm, 28*mm]))

story.append(Spacer(1, 2*mm))
story.append(Paragraph("※ KRW 환산: 1 USD = 1,380 KRW 기준 (2026년 6월)", ST["small"]))
story.append(Paragraph("※ Railway 서버 운영 비용($5~20/월)은 별도이며 위 금액에 포함되지 않습니다.", ST["small"]))

story.append(Spacer(1, 4*mm))
story.append(Paragraph("■ 비용 절감 팁", ST["h2"]))
tips = [
    "문제 설명은 간결하게 — 입력 텍스트가 길수록 입력 토큰이 증가합니다",
    "프레임워크 피드백(탭7)은 선택 기능 — 필요할 때만 사용하면 추가 $0.05~0.10 절약",
    "Railway 환경변수 관리 — API 키를 올바르게 설정해 불필요한 재시도 방지",
    "팀원 공유 시 Railway URL 사용 — 각자 로컬 서버 운영 비용 없음",
]
for t in tips:
    story.append(Paragraph(f"💡 {t}", ST["tip"]))

story.append(PageBreak())

# ═══════════════════════════════════════════════════
# 7장. 트러블슈팅
# ═══════════════════════════════════════════════════
story += [Paragraph("7장. 트러블슈팅", ST["h1"]), hr()]

trouble_data = [
    ["증상", "원인", "해결 방법"],
    ["헤더에 '데모 모드' 표시", "Railway 서버 미연결 또는 API 키 오류",
     "https://triz-dashboard-production.up.railway.app 직접 접속 확인"],
    ["분석이 시작되지 않음\n(버튼 비활성)", "이전 분석 진행 중 또는\nSSE 연결 오류",
     "브라우저 새로고침(F5) 후 재시도"],
    ["Top 10이 7~8개만 표시", "합성 단계 JSON 잘림\n(매우 긴 아이디어 응답)",
     "v3.0에서 수정 완료 — Railway 최신 버전 접속 확인"],
    ["실행 아이디어 로딩 중 고정", "action-plan API 응답 지연\n(약 90~120초 소요)",
     "최대 2분 대기, 미완료 시 새로고침"],
    ["ASIT 아이디어 개수 불일치", "AI가 8개보다 적게 반환",
     "자동으로 복구됨. 크게 문제없으면 무시 가능"],
    ["Railway 접속 불가", "서버 슬립 또는 배포 중",
     "30초 후 재접속. 배포 중이면 railway status 확인"],
    ["로컬 서버 포트 충돌", "8000번 포트 사용 중",
     "lsof -ti:8000 | xargs kill -9 후 재실행"],
]
story.append(blue_table(trouble_data, [40*mm, 45*mm, 75*mm]))

story.append(Spacer(1, 6*mm))
story.append(Paragraph("■ 문의", ST["h2"]))
story.append(Paragraph("시스템 오류나 기능 개선 요청은 기획팀 내부 채널로 전달해 주세요.", ST["body"]))
story.append(Paragraph("GitHub: https://github.com/jayce0321/triz-dashboard", ST["url"]))

story.append(Spacer(1, 6*mm))
story.append(hr(color=BLUE, thickness=2))
story.append(Paragraph("세계일보 기획팀 | TRIZ/ASIT AI 에이전트 분석 대시보드 사용자 매뉴얼 v3.0", ST["caption"]))
story.append(Paragraph("최종 수정일: 2026-06-04 | 내부 배포용 자료 | 무단 복제 금지", ST["caption"]))

# ── 빌드 ────────────────────────────────────────────
doc.build(story)
print(f"✅ 생성 완료: {OUT}")
