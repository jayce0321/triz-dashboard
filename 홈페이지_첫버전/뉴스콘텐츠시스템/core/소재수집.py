# -*- coding: utf-8 -*-
"""소재(취재 후보) 수집기.

원칙: 기사 '제목(헤드라인)'만 소재로 수집하고 본문은 절대 가져오지 않는다.
제목·발행일·매체명은 사실적 정보로 저작권 침해 대상이 아니며, 생성기는 이를
'소재(대상어)'로만 사용해 문장을 독자적으로 재생성한다.
"""

import urllib.request

try:
    import feedparser
except ImportError:
    feedparser = None

RSS_목록 = [
    {"이름": "구글뉴스_한국", "url": "https://news.google.com/rss?hl=ko&gl=KR&ceid=KR:ko", "분야": "종합"},
    {"이름": "구글뉴스_금융", "url": "https://news.google.com/rss/search?q=%EC%9D%B4%EC%9E%90%EB%A5%A0+%ED%99%98%EC%9C%A8&hl=ko&gl=KR&ceid=KR:ko", "분야": "금융"},
    {"이름": "구글뉴스_물가·소비", "url": "https://news.google.com/rss/search?q=%EB%AC%BC%EA%B0%80+%EA%B8%B0%EB%8C%80%EC%9D%B8%ED%94%8C%EB%A0%88&hl=ko&gl=KR&ceid=KR:ko", "분야": "경제일반"},
    {"이름": "구글뉴스_산업", "url": "https://news.google.com/rss/search?q=%EB%B0%98%EB%8F%84%EC%B2%B4+%EC%82%B0%EC%97%85&hl=ko&gl=KR&ceid=KR:ko", "분야": "산업"},
    {"이름": "구글뉴스_부동산", "url": "https://news.google.com/rss/search?q=%EB%B6%80%EB%8F%99%EC%82%B0&hl=ko&gl=KR&ceid=KR:ko", "분야": "부동산"},
    {"이름": "구글뉴스_기후·환경", "url": "https://news.google.com/rss/search?q=%EA%B8%B0%ED%9B%84%EB%B3%80%ED%99%94&hl=ko&gl=KR&ceid=KR:ko", "분야": "환경"},
]

블랙단어 = ["[오늘의 운세]", "딱풀", "프로모션", "할인", "이벤트", "광고"]


def _fetch_feed(url, timeout=12):
    if feedparser is None:
        raise RuntimeError("feedparser 미설치")
    raw = urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=timeout
    ).read()
    return feedparser.parse(raw)


def 헤드라인_수집(한도=20, rss목록=None):
    """RSS 헤드라인을 소재 후보로 수집. 제목만 사용(저작권 안전)."""
    rss_list = rss목록 or RSS_목록
    후보 = []
    for src in rss_list:
        try:
            feed = _fetch_feed(src["url"])
            for e in feed.entries[:한도]:
                title = (getattr(e, "title", "") or "").strip()
                if not title:
                    continue
                if any(b in title for b in 블랙단어):
                    continue
                후보.append({
                    "제목": title,
                    "출처": src["이름"],
                    "분야": src.get("분야", "종합"),
                    "발행일": getattr(e, "published_parsed", None),
                })
        except Exception as err:
            후보.append({"제목": f"[수집불가] {err}", "출처": src["이름"], "발행일": None,
                        "분야": src.get("분야", "종합")})
    return 후보


def 예시소재():
    """네트워크 실패 시 대체 예시."""
    return [{"제목": "원달러 환율 흐름과 수출기업 전망", "출처": "예시", "발행일": None},
            {"제목": "기준금리 결정과 가계부채 영향", "출처": "예시", "발행일": None},
            {"제목": "코스피 30일 등락과 시장 심리", "출처": "예시", "발행일": None}]