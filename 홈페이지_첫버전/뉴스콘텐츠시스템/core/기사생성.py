# -*- coding: utf-8 -*-
"""기사 생성기. 기사타입 레지스트리 구조대로 헤드라인·리드·본문을 생성하고,
Google News 노출용 구조화 데이터(NewsArticle/Article json-ld)와 SEO·GEO 메타를 산출한다.
저작권 무침해 서술원칙과 GEO(생성형 검색) 대응을 템플릿에 반영한다.
"""

import json
import os
import re
from datetime import datetime, timezone, timedelta

from .기사타입 import 타입찾기

KST = timezone(timedelta(hours=9))


def 지금():
    return datetime.now(KST)


def 슬러그(text):
    text = re.sub(r"[^0-9a-zA-Z가-힣 ]", "", text)
    return text.strip().replace(" ", "-")


def 헤드라인(대상, 카테고리, 타입코드):
    접두 = {
        "속보알림": "",
        "수치리포트": "한눈에 보는",
        "테마해설": "알아두면 좋은",
        "심층분석": "심층 진단",
        "검증해설": "사실 확인",
        "쉬운설명": "쉽게 이해하는",
    }
    pre = 접두.get(타입코드, "")
    if pre:
        return f"{pre} {대상} – {카테고리} 리포트"
    return f"{대상} – {카테고리} 리포트"


def 리드(타입, 데이터):
    if 타입.코드 == "수치리포트":
        수치 = 데이터.get("수치", {})
        대표 = next(iter(수치.items()), (None, None))
        if 대표 and 대표[1] is not None:
            return f"오늘 기준 {대표[0]}은(는) {대표[1]}으로 집계됐다. {데이터.get('요약', '')}"
    return f"{타입.이름}. {데이터.get('요약', '')}"


def _to_문단(blocks):
    """본문 블록(list[dict])을 markdown 문자열로."""
    out = []
    for b in blocks:
        if b.get("h"):
            out.append(f"\n## {b['h']}\n")
        for p in b.get("p", []):
            out.append(f"\n{p}\n")
        for u in b.get("ul", []):
            out.append(f"- {u}\n")
    return "\n".join(out)


def 본문_블록(타입, 데이터):
    blocks = [{"h": "핵심요약", "p": [데이터.get("요약", "")]}]
    if 타입.코드 == "쉬운설명":
        if 데이터.get("개념"):
            blocks.append({"h": "핵심 개념",
                           "p": [f"{k}: {v}" for k, v in 데이터["개념"].items()]})
        if 데이터.get("비유"):
            blocks.append({"h": "쉬운 비유",
                           "p": [f"비유: {데이터['비유']}"]})
    if 데이터.get("수치"):
        blocks.append({"h": "주요 수치",
                       "ul": [f"{k}: {v}" for k, v in 데이터["수치"].items()]})
    if 데이터.get("질문답"):
        qa = 데이터["질문답"]
        blocks.append({"h": "알아두면 좋은 질문과 답"})
        for q, a in qa.items():
            blocks.append({"p": [f"Q. {q}", f"A. {a}"]})
    blocks.append({"h": "시사점",
                   "p": [데이터.get("해석", ""),
                         "※ 이 글은 공공 수치 기반 요약이며, 투자·정책 판단을 위한 조언이 아닙니다."]})
    blocks.append({"h": "출처", "p": [데이터.get("출처", "공공 데이터")]})
    return blocks


def 기사생성(타입코드, 카테고리, 대상, 데이터, 작성일=None, 도메인="https://segyesignal.example.com"):
    타입 = 타입찾기(타입코드)
    if not 타입:
        raise ValueError(f"알 수 없는 기사타입: {타입코드}")
    작성일 = 작성일 or 지금()
    헤드 = 헤드라인(대상, 카테고리, 타입코드)
    slug = 슬러그(헤드) + "-" + 작성일.strftime("%Y%m%d")
    url = f"{도메인}/news/{slug}"

    블록 = 본문_블록(타입, 데이터)
    markdown = f"# {헤드}\n\n{리드(타입, 데이터)}\n\n{_to_문단(블록)}\n"
    markdown += f"\n---\n*기사타입: {타입.이름} | 서술원칙: {타입.서술원칙}*\n"

    return {
        "타입코드": 타입코드,
        "카테고리": 카테고리,
        "대상": 대상,
        "헤드라인": 헤드,
        "리드": 리드(타입, 데이터),
        "markdown": markdown,
        "slug": slug,
        "URL": url,
        "작성일": 작성일.isoformat(timespec="seconds"),
        "데이터": 데이터,
        "타입메타": {"이름": 타입.이름, "난이도": 타입.난이도,
                      "seo": list(타입.seo핵심), "geo": list(타입.geo핵심)},
    }


def 스키마_jsonld(기사, 브랜드, 이미지경로=None):
    """Google News / Article 구조화 데이터."""
    author = {"@type": "Organization", "name": 브랜드.get("사이트명", "세계시그널")}
    node = {
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "headline": 기사["헤드라인"],
        "datePublished": 기사["작성일"],
        "dateModified": 기사["작성일"],
        "mainEntityOfPage": {"@type": "WebPage", "@id": 기사["URL"]},
        "author": author,
        "publisher": {**author, "logo": {"@type": "ImageObject",
                                         "url": 브랜드.get("발행도메인", "") + "/logo.png"}},
        "inLanguage": "ko",
        "description": 기사["리드"],
    }
    if 이미지경로:
        node["image"] = {"@type": "ImageObject", "url": 브랜드.get("발행도메인", "") + 이미지경로}
    return json.dumps(node, ensure_ascii=False, indent=2)


def 출력폴더(base):
    d = os.path.join(base, "기사발행물", 지금().strftime("%Y-%m-%d"))
    os.makedirs(d, exist_ok=True)
    return d