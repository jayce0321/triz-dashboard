#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""뉴스콘텐츠시스템 CLI.

저작권 무침해 공공 데이터를 바탕으로, 구글뉴스 노출을 노린
'가벼운 기사 ~ 심층 분석' 기사를 자동 제안·생성하는 시스템.

명령:
  타입목록              등록된 기사타입(SEO→GEO 매핑) 목록
  후보                  오늘 생성할 기사 후보(소재) 제안
  생성 [--type 코드]    기사 생성 (이미지 포함)
  기사 <코드>           기사타입 상세 보기

설정: 설정/config.json  (한국은행 ECOS 키 등)
산출: 기사발행물/YYYY-MM-DD/ 하위에 md + json-ld + 이미지
"""

import argparse
import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from core.기사타입 import 타입목록, 타입찾기
from core.데이터소스 import 공개데이터
from core.기사생성 import 기사생성, 스키마_jsonld, 출력폴더
from core import 이미지생성
from core.소재수집 import 헤드라인_수집, 예시소재


def _env_로드():
    """프로젝트 루트의 .env에서 ECOS_API_KEY를 로드 (sqlite 없이 간단 파싱)."""
    p = os.path.join(BASE, ".env")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip())


def 설정_로드():
    _env_로드()
    p = os.path.join(BASE, "설정", "config.json")
    with open(p, encoding="utf-8") as f:
        설정 = json.load(f)
    # 환경변수 우선 (GitHub Actions Secret 주입 및 .env 지원)
    key = os.environ.get("ECOS_API_KEY")
    if key:
        설정.setdefault("데이터소스", {}).setdefault("한국은행ECOS", {})["api_key"] = key
    return 설정


def _데이터_조립(타입코드, src):
    """기사타입별 데이터 수치·요약·질문답·시계열 조립. 공공 데이터 기반."""
    fx = src.환율("USD")
    rate = src.기준금리()
    cpi = src.물가상승률()
    idx = src.지수시계열("KOSPI", 30)
    wx = src.날씨()

    if 타입코드 == "심층분석":
        대상 = "기준금리·환율·물가의 상호작용"
    elif 타입코드 == "테마해설":
        대상 = "시장 기준금리와 환율·물가의 관계"
    elif 타입코드 == "속보알림":
        대상 = "당일 지표·환율 마감"
    elif 타입코드 == "쉬운설명":
        대상 = "환율·금리·물가·지수란 무엇인가"
    else:
        대상 = "원달러 환율·기준금리·물가·코스피"

    수치 = {
        "원달러 환율": f"{fx['기준환율']:.1f}원",
        "기준금리": f"{rate['값']:.2f}%",
        "물가상승률": f"{cpi['값']}%",
        "코스피 지수변동(30일)": f"{idx['변동률']:+.2f}%",
        "서울 날씨(현재)": f"{wx['현재온도']:.0f}°C {wx['기상상태']}",
        "오늘 최고/최저": f"{wx['오늘최고']:.0f}°C / {wx['오늘최저']:.0f}°C",
    }
    요약 = (
        f"오늘 원달러 환율은 1달러당 {fx['기준환율']:.1f}원으로 마감됐다. "
        f"한국은행 기준금리는 {rate['값']:.2f}%, 소비자물가 상승률은 {cpi['값']}%로 집계됐다. "
        f"코스피는 최근 30영업일 사이 {idx['변동률']:+.2f}% 변했다. "
        f"오늘 서울 날씨는 {wx['기상상태']}, 현재 {wx['현재온도']:.0f}°C(최고 {wx['오늘최고']:.0f}°C·최저 {wx['오늘최저']:.0f}°C)다."
    )
    해석 = (
        "환율·금리·물가는 서로 연동되며, 그 변동은 통화정책과 국제금융환경 등 "
        "여러 요인이 복합적으로 작용한 결과다. 아래 인과 관계는 '가능한 설명'이며 "
        "확정된 판단이 아니다."
    )
    질문답 = {
        "원달러 환율이 오르면 어떤 영향이 있나요?": "수입 물가 부담과 해외 여행·수입 원가 상승 가능성이 거론됩니다.",
        "기준금리는 누가 결정하나요?": "한국은행 금융통화위원회가 통화정책방향 회의에서 결정합니다.",
        "오늘 서울 날씨는 어떤가요?": f"현재 {wx['기상상태']}, {wx['현재온도']:.0f}°C로, 최고 {wx['오늘최고']:.0f}°C·최저 {wx['오늘최저']:.0f}°C입니다.",
    }
    시뮬 = fx.get("시뮬") or rate.get("시뮬") or cpi.get("시뮬") or idx.get("시뮬") or wx.get("시뮬")
    시뮬목록 = []
    if fx.get("시뮬"): 시뮬목록.append("환율")
    if rate.get("시뮬"): 시뮬목록.append("기준금리")
    if cpi.get("시뮬"): 시뮬목록.append("물가")
    if idx.get("시뮬"): 시뮬목록.append("코스피")
    if wx.get("시뮬"): 시뮬목록.append("날씨")

    if 타입코드 == "쉬운설명":
        개념 = {
            "환율": "다른 나라 돈과 우리 돈을 바꿀 때 쓰는 가치 비율. 원/달러 환율 1350원은 1달러를 사려면 1350원이 필요하다는 뜻입니다.",
            "기준금리": "한국은행이 은행에 돈을 빌려줄 때 적용하는 기본 이자. 이 값이 오르면 은행 예금·대출 금리에도 영향을 줍니다.",
            "물가상승률": "1년 전보다 물건과 서비스 가격이 얼마나 올랐는지를 보여주는 비율(인플레이션율)입니다.",
            "코스피": "한국 증시에 상장된 주요 기업들의 주가 흐름을 한데 모아 놓은 지표입니다.",
        }
        비유 = (f"지표들은 자동차 계기판과 같습니다. {fx['기준환율']:.1f}원이라는 "
                f"환율, {rate['값']:.2f}%라는 금리는 각각 속도계·연료계처럼 "
                f"'경제가 지금 어떤 상태인지'를 보여주는 수치입니다. "
                f"계기판 하나만으로 운전 결론을 내리지 않듯, 지표 하나만으로 "
                f"시장 전망을 단정할 수 없습니다.")
        데이터 = {
            "대상": 대상,
            "개념": 개념,
            "비유": 비유,
            "수치": 수치,
            "요약": (f"원달러 환율은 {fx['기준환율']:.1f}원, 기준금리 {rate['값']:.2f}%, "
                     f"물가상승률 {cpi['값']}%입니다. 각 용어의 뜻을 쉬운 말로 정리했습니다."),
            "해석": "지표를 이해하면 뉴스를 해석하는 데 도움이 됩니다. 단, 지표 해석은 조심해야 하며 단순한 숫자만으로 결론을 내리면 안 됩니다.",
            "질문답": {
                "환율이 오르면 생활에 어떤 영향이 있나요?": "외국과 거래하는 물건의 가격이 오를 수 있고, 해외 여행 비용이 늘어날 수 있습니다.",
                "기준금리는 왜 중요한가요?": "예금·대출 금리의 기준이 돼, 저축과 대출 비용에 두루 영향을 줍니다.",
                "물가가 오르면 뭐가 달라지나요?": "같은 돈으로 살 수 있는 물건이 줄어듭니다. 이를 인플레이션으로 부릅니다.",
            },
            "출처": " / ".join(filter(None, [fx.get("출처"), rate.get("출처"), cpi.get("출처"), idx.get("출처"), wx.get("출처")])),
            "날씨": wx,
            "시계열": idx,
            "시뮬": 시뮬,
            "시뮬목록": 시뮬목록,
        }
        return 데이터

    return {
        "대상": 대상,
        "수치": 수치,
        "요약": 요약,
        "해석": 해석,
        "질문답": 질문답,
        "출처": " / ".join(filter(None, [fx.get("출처"), rate.get("출처"), cpi.get("출처"), idx.get("출처"), wx.get("출처")])),
        "날씨": wx,
        "시계열": idx,
        "시뮬": 시뮬,
        "시뮬목록": 시뮬목록,
    }


def cmd_타입목록():
    print("기사타입 레지스트리 (SEO → GEO 전환):\n")
    for t in 타입목록():
        print(f"[{t.코드}] {t.이름}  (난이도 {t.난이도}/5)")
        print(f"    SEO: {', '.join(t.seo핵심)}")
        print(f"    GEO: {', '.join(t.geo핵심)}")
        print()


def cmd_후보(한도=12):
    print("오늘의 실시간 소재 후보 (구글뉴스 헤드라인, 제목만 수집):\n")
    후보 = 헤드라인_수집(한도=한도)
    실패 = [c for c in 후보 if c.get("제목","").startswith("[수집불가]")]
    헤드들 = [c for c in 후보 if c not in 실패]
    if 실패:
        print(f"⚠️ RSS 수집 실패: {실패[0]['제목']}")
        print("   예시 소재로 대체합니다.\n")
        헤드들 = 예시소재()
    for i, c in enumerate(헤드들, 1):
        print(f"  {i}. {c['제목']}  ({c['출처']})")
    print("\n생성 예: python3 뉴스콘텐츠시스템.py 생성 --소재 '이슈 키워드'")
    print("등록 타입: 속보알림 / 수치리포트 / 테마해설 / 심층분석 / 검증해설 / 쉬운설명")


def _자동소재():
    """오늘의 상위 헤드라인을 소재로 선택. 매체명 접미사 제거."""
    후보 = 헤드라인_수집(한도=10)
    for c in 후보:
        제목 = c.get("제목", "")
        if not 제목.startswith("[수집불가]"):
            import re
            return re.sub(r"\s*-\s*[^\-]+$", "", 제목).strip()
    return "오늘의 주요 이슈"


def cmd_생성(타입코드=None, 소재=None):
    설정 = 설정_로드()
    브랜드 = 설정["브랜드"]
    src = 공개데이터(설정)
    if not 타입코드:
        타입코드 = "수치리포트"

    데이터 = _데이터_조립(타입코드, src)
    카테고리 = "금융·경제"
    대상 = 데이터["대상"]
    if 소재:
        if 소재 == "auto":
            소재 = _자동소재()
            print(f"   오늘의 소재: {소재}")
        대상 = 소재
        데이터["요약"] = f"{소재}를 주요 공공 경제 지표와 함께 살펴본다. " + 데이터.get("요약", "")
        데이터["대상"] = 대상

    기사 = 기사생성(타입코드, 카테고리, 대상, 데이터, 도메인=브랜드["발행도메인"])

    폴더 = 출력폴더(BASE)
    img폴더 = os.path.join(폴더, "이미지")
    os.makedirs(img폴더, exist_ok=True)

    # 이미지(차트 + 썸네일) 생성
    라인차트_file = None
    if 데이터.get("시계열"):
        라인차트_file = os.path.join(img폴더, 기사["slug"] + "_차트.png")
        이미지생성.라인차트(데이터["시계열"]["시계열"], 라인차트_file, 기사["헤드라인"])
    썸네일_file = os.path.join(img폴더, 기사["slug"] + "_썸네일.png")
    wx_상태 = 데이터.get("날씨", {}).get("기상상태", "") if isinstance(데이터.get("날씨"), dict) else ""
    wx_온도 = 데이터.get("날씨", {}).get("현재온도", "") if isinstance(데이터.get("날씨"), dict) else ""
    이미지생성.썸네일(기사["헤드라인"], 썸네일_file, 부제=브랜드["사이트명"],
                    상수상태=wx_상태, 현재온=wx_온도)

    # 저장
    md_path = os.path.join(폴더, 기사["slug"] + ".md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(기사["markdown"])
        if 데이터.get("시뮬"):
            시뮬대상 = ", ".join(데이터.get("시뮬목록", []))
            f.write(f"\n\n> ⚠️ 일부 지표({시뮬대상})는 시뮬레이션입니다. "
                    f"실데이터 전환은 설정(config.json)의 한국은행 ECOS 등 API 키 입력 또는 접속 확인이 필요합니다.\n")

    ld_path = os.path.join(폴더, 기사["slug"] + ".json-ld")
    img_url = "/" + os.path.relpath(썸네일_file, BASE)
    with open(ld_path, "w", encoding="utf-8") as f:
        f.write(스키마_jsonld(기사, 브랜드, 이미지경로=img_url))

    print(f"\n✅ 생성 완료: {폴더}")
    print(f"   헤드라인 : {기사['헤드라인']}")
    print(f"   기사     : {md_path}")
    print(f"   스키마   : {ld_path}")
    if 라인차트_file:
        print(f"   차트     : {라인차트_file}")
    print(f"   썸네일   : {썸네일_file}")
    if 데이터.get("시뮬"):
        print(f"\n⚠️ 시뮬레이션 지표: {', '.join(데이터.get('시뮬목록', []))} - 실데이터 전환(API 키) 필요")


def cmd_기사상세(코드):
    t = 타입찾기(코드)
    if not t:
        print(f"타입을 찾지 못했습니다: {코드}")
        print("가능한 타입:", ", ".join(x.코드 for x in 타입목록()))
        return
    print(f"[{t.코드}] {t.이름} (난이도 {t.난이도}/5)")
    print(f"   목표어  : {t.목표어}")
    print(f"   구조    : {' → '.join(t.구조)}")
    print(f"   본문길이: {t.본문길이}")
    print(f"   서술원칙: {t.서술원칙}")
    print(f"   SEO     : {' | '.join(t.seo핵심)}")
    print(f"   GEO     : {' | '.join(t.geo핵심)}")
    print(f"   스키마  : {', '.join(t.스키마타입)}")


def main():
    parser = argparse.ArgumentParser(description="뉴스콘텐츠시스템 CLI")
    sub = parser.add_subparsers(dest="명령")

    sub.add_parser("타입목록", help="기사타입(SEO→GEO) 목록")
    sub.add_parser("후보", help="오늘 기사 후보 제안")
    p_생성 = sub.add_parser("생성", help="기사 생성")
    p_생성.add_argument("--type", dest="타입코드", default=None,
                        help="기사타입 코드 (기본: 수치리포트)")
    p_생성.add_argument("--소재", dest="소재", default=None,
                        help="오늘의 취재 소재(대상어). 후보 목록에서 선택한 제목 입력")
    p_상세 = sub.add_parser("기사", help="기사타입 상세")
    p_상세.add_argument("코드")

    args = parser.parse_args()
    if args.명령 == "타입목록":
        cmd_타입목록()
    elif args.명령 == "후보":
        cmd_후보()
    elif args.명령 == "생성":
        cmd_생성(args.타입코드, args.소재)
    elif args.명령 == "기사":
        cmd_기사상세(args.코드)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()