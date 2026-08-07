#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""발행 인덱스 생성기.

기사발행물/ 의 기사를 상태(기본: 발행) 필터로 모아 최신 10건의
마크다운 인덱스(index.md)를 생성한다. GitHub Pages에 올리면
구글뉴스 노출용 기사 링크 목록이 된다.

실행: python3 발행인덱스.py [--상태 발행]
"""

import argparse
import json
import os
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
발행폴더 = os.path.join(BASE, "기사발행물")
상태파일 = os.path.join(BASE, "설정", "상태.json")


def 상태_로드():
    if os.path.exists(상태파일):
        with open(상태파일, encoding="utf-8") as f:
            return json.load(f)
    return {"기사": {}}


def 수집(상태필터=None, 한도=10):
    상태 = 상태_로드()
    items = []
    for 루트, _, files in os.walk(발행폴더):
        for fn in files:
            if not fn.endswith(".md") or fn.startswith(".") or fn == "index.md":
                continue
            path = os.path.join(루트, fn)
            rel = os.path.relpath(path, BASE)
            st = 상태["기사"].get(rel, {}).get("상태", "초안")
            if 상태필터 and st != 상태필터:
                continue
            title = fn[:-3].replace("-", " ")
            with open(path, encoding="utf-8") as f:
                for line in f:
                    if line.startswith("# "):
                        title = line[2:].strip()
                        break
            items.append({
                "제목": title,
                "rel": rel,
                "상태": st,
                "시간": os.path.getmtime(path),
                "날짜": datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d"),
            })
    items.sort(key=lambda x: x["시간"], reverse=True)
    return items[:한도]


def 렌더(items, 상태필터):
    out = ["# 데일리 뉴스 콘텐츠 (발행 인덱스)\n"]
    필터명 = 상태필터 or "전체"
    out.append(f"> 자동 생성 {datetime.now().strftime('%Y-%m-%d %H:%M')} | 상태: {필터명} | {len(items)}건")
    out.append("\n---\n")
    if not items:
        out.append("*아직 발행된 기사가 없습니다.*\n")
        return "\n".join(out)
    for i, g in enumerate(items, 1):
        out.append(f"## {i}. {g['제목']}")
        out.append(f"- 상태: **{g['상태']}** | 날짜: {g['날짜']}")
        out.append(f"- 파일: `{g['rel']}`\n")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--상태", dest="상태", default=None,
                    help="특정 상태만(초안/승인/발행). 없으면 전체")
    args = ap.parse_args()
    items = 수집(args.상태)
    os.makedirs(발행폴더, exist_ok=True)
    out = os.path.join(발행폴더, "index.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(렌더(items, args.상태))
    print(f"OK: {out} ({len(items)}건)")


if __name__ == "__main__":
    main()