# -*- coding: utf-8 -*-
"""저작권 무침해 공공·공개 데이터 소스 수집기.

원칙:
- 공공·국가기관 및 공개(무료, 키 불필요) API가 공표한 수치만 취급.
  (환율=ECB frankfurter, 지수=Yahoo, 날씨=open-meteo)
- 신문·보도문·블로그의 문장은 옮기지 않고 '수치 + 출처기관명 + 공표시간'만 사용.
- 키가 필요한 소스(한국은행 ECOS)는 설정(config.json)의 키가 있을 때만 호출.
- 각 항목은 실/시뮬 상태를 반환하며, 기사·대시보드에 실데이터/시뮬레이션을 표기한다.
"""

import hashlib
import json
import random
import urllib.request
from datetime import date as _date, timedelta


class 공개데이터:
    def __init__(self, 설정=None):
        self.설정 = 설정 or {}
        self._rcache = {}
        self._플랫폼 = self.설정.get("데이터소스", {}) or {}

    # ---------------- 유틸 ----------------
    def _해시(self, *parts):
        s = ":".join(str(p) for p in parts).encode("utf-8")
        return int(hashlib.sha256(s).hexdigest()[:8], 16)

    def _결정난수(self, *parts):
        return random.Random(self._해시("seed", *parts))

    def _get(self, url, timeout=12, 재시도=2):
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        last = None
        for i in range(재시도 + 1):
            try:
                return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")
            except Exception as e:
                last = e
                import time
                time.sleep(0.8 + i)
        raise last

    def _get_json(self, url, timeout=12):
        return json.loads(self._get(url, timeout))

    # ---------------- 환율 (ECB frankfurter, 키 불필요) ----------------
    def 환율(self, 통화="USD"):
        try:
            url = "{}/latest?from={}".format(
                self._플랫폼.get("환율", {}).get("base_url", "https://api.frankfurter.app"), 통화)
            d = self._get_json(url)
            rates = d.get("rates", {})
            if "KRW" in rates:
                return {
                    "통화": 통화,
                    "기준환율": round(float(rates["KRW"]), 2),
                    "공표일": d.get("date") or _date.today().isoformat(),
                    "출처": "ECB 프랑크푸르터(frankfurter) 공개 환율",
                    "시뮬": False,
                }
        except Exception:
            pass
        return self._환율시뮬(통화)

    def _환율시뮬(self, 통화):
        rnd = self._결정난수("환율", 통화)
        전일 = round(1380 + rnd.uniform(-5, 5), 2)
        오늘 = round(전일 + rnd.uniform(-4, 4), 2)
        return {
            "통화": 통화,
            "기준환율": 오늘,
            "전일환율": 전일,
            "등락": round(오늘 - 전일, 2),
            "공표일": _date.today().isoformat(),
            "출처": "환율(시뮬레이션, 프랑크린터르 접속불가)",
            "시뮬": True,
        }

    # ---------------- 지수 시계열 (Yahoo Finance, 키 불필요) ----------------
    def 지수시계열(self, 이름="KOSPI", 일수=30):
        symbol = {"KOSPI": "%5EKS11", "KOSPI200": "%5EKS11", "NASDAQ": "%5EIXIC",
                  "S&P500": "%5EGSPC"}.get(이름)
        if symbol:
            try:
                url = ("{}/v8/finance/chart/{}?range={}d&interval=1d".format(
                    self.platform("지수", "https://query1.finance.yahoo.com"), symbol, max(일수, 40)))
                data = self._get_json(url)
                res = data["chart"]["result"]
                if res:
                    c = res[0]["indicators"]["quote"][0]["close"]
                    ts = res[0]["timestamp"]
                    시계열 = [{"날짜": _date.fromtimestamp(t).isoformat(), "종가": round(v, 2)}
                              for t, v in zip(ts, c) if v is not None]
                    if len(시계열) >= 일수:
                        return {
                            "이름": 이름, "일수": len(시계열), "시계열": 시계열[-일수:],
                            "첫날": 시계열[-일수]["종가"], "마지막": 시계열[-1]["종가"],
                            "변동률": round((시계열[-1]["종가"] / 시계열[-일수]["종가"] - 1) * 100, 2),
                            "출처": "Yahoo Finance 실데이터", "시뮬": False,
                        }
            except Exception:
                pass
        return self._지수시뮬(이름, 일수)

    def _지수시뮬(self, 이름, 일수):
        rnd = self._결정난수("지수", 이름)
        val = 2600.0 + rnd.uniform(-50, 50)
        base = rnd.uniform(-1.2, 1.2)
        시계열 = []
        d = _date.today()
        while len(시계열) < 일수:
            if d.weekday() < 5:
                val = max(1500.0, val + base + rnd.uniform(-18, 18))
                시계열.append({"날짜": d.isoformat(), "종가": round(val, 2)})
            d -= timedelta(days=1)
        시계열.reverse()
        return {
            "이름": 이름, "일수": 일수, "시계열": 시계열,
            "첫날": 시계열[0]["종가"], "마지막": 시계열[-1]["종가"],
            "변동률": round((시계열[-1]["종가"] / 시계열[0]["종가"] - 1) * 100, 2),
            "출처": "지수(시뮬레이션, API 실패)", "시뮬": True,
        }

    # ---------------- 날씨 (open-meteo, 키 불필요) ----------------
    기상코드한글 = {
        0: "맑음", 1: "대체로 맑음", 2: "구름 조금", 3: "흐림",
        45: "안개", 48: "얼음안개",
        51: "이슬비", 53: "약한 비", 55: "보통 비",
        61: "약한 비", 63: "비", 65: "강한 비",
        71: "약한 눈", 73: "눈", 75: "강한 눈",
        80: "소나기", 81: "강한 소나기", 82: "매우 강한 소나기",
        95: "뇌우", 96: "뇌우(우박)", 99: "뇌우(진한 우박)",
    }

    def _기상한글(self, code):
        return self.기상코드한글.get(code, f"기상코드 {code}")

    def 날씨(self):
        """open-meteo 실데이터(키 불필요). 실패 시 시뮬레이션."""
        wc = self.설정.get("데이터소스", {}).get("날씨", {})
        lat, lon = wc.get("위도", 37.5665), wc.get("경도", 126.9780)
        try:
            url = ("{}/v1/forecast?latitude={}&longitude={}"
                   "&current=temperature_2m,weather_code,relative_humidity_2m,wind_speed_10m,apparent_temperature"
                   "&daily=temperature_2m_max,temperature_2m_min,weather_code"
                   "&timezone=Asia%2FSeoul&forecast_days=3".format(
                       self.설정.get("데이터소스", {}).get("날씨", {}).get("base_url", "https://api.open-meteo.com"),
                       lat, lon))
            d = self._get_json(url)
            c = d["current"]
            daily = d["daily"]
            return {
                "도시": wc.get("도시", "서울"),
                "현재온도": c["temperature_2m"],
                "체감온도": c.get("apparent_temperature"),
                "습도": c.get("relative_humidity_2m"),
                "풍속": c.get("wind_speed_10m"),
                "기상코드": c["weather_code"],
                "기상상태": self._기상한글(c["weather_code"]),
                "오늘최고": daily["temperature_2m_max"][0],
                "오늘최저": daily["temperature_2m_min"][0],
                "출처": "open-meteo 공개 실데이터",
                "시뮬": False,
            }
        except Exception:
            return self._날씨_시뮬(wc)

    def _날씨_시뮬(self, wc):
        rnd = self._결정난수("날씨")
        코드 = rnd.choice([0, 1, 2, 3, 63, 95])
        return {
            "도시": wc.get("도시", "서울"),
            "현재온도": round(22 + rnd.uniform(-6, 12), 1),
            "기상코드": 코드,
            "기상상태": self._기상한글(코드),
            "오늘최고": round(28 + rnd.uniform(-3, 3), 1),
            "오늘최저": round(17 + rnd.uniform(-3, 3), 1),
            "출처": "날씨(시뮬레이션, open-meteo 접속불가)",
            "시뮬": True,
        }

    # ---------------- 기준금리 (한국은행 ECOS, 키 필요) ----------------
    def 기준금리(self):
        ec = self.설정.get("데이터소스", {}).get("한국은행ECOS", {}) or {}
        key = ec.get("api_key", "")
        if key:
            try:
                base = ec.get("base_url", "https://ecos.bok.or.kr/api/").rstrip("/")
                코드 = ec.get("표코드", {}).get("기준금리", "722Y001")
                # 형식: /{key}/json/kr/{start}/{end}/{code}/{주기}/{from}/{to}/{항목}
                now = _date.today()
                to = now.strftime("%Y%m")
                fr = (now.replace(year=now.year - 1)).strftime("%Y%m")
                url = f"{base}/StatisticSearch/{key}/json/kr/1/24/{코드}/M/{fr}/{to}/0101000"
                rows = self._get_json(url).get("StatisticSearch", {}).get("row", [])
                if rows:
                    last = rows[-1]
                    return {
                        "지표": "한국은행 기준금리", "값": float(last["DATA_VALUE"]),
                        "기준월": last["TIME"],
                        "출처": "한국은행 경제통계시스템(ECOS) 실데이터", "시뮬": False,
                    }
            except Exception:
                pass
        rnd = self._결정난수("기준금리", _date.today().strftime("%Y%m"))
        return {
            "지표": "한국은행 기준금리", "값": round(2.8 + rnd.uniform(-0.25, 0.25), 2),
            "기준월": _date.today().strftime("%Y-%m"),
            "출처": "한국은행 ECOS(시뮬레이션, API키 미설정)", "시뮬": True,
        }

    # ---------------- 소비가물가 (한국은행 ECOS, 키 필요) ----------------
    def 물가상승률(self):
        ec = self.설정.get("데이터소스", {}).get("한국은행ECOS", {}) or {}
        key = ec.get("api_key", "")
        if key:
            try:
                base = ec.get("base_url", "https://ecos.bok.or.kr/api/").rstrip("/")
                코드 = ec.get("표코드", {}).get("소비자물가", "901Y010")
                now = _date.today()
                to = now.strftime("%Y%m")
                fr = (now.replace(year=now.year - 2)).strftime("%Y%m")
                # 901Y010 총지수(00)를 약 24개월 가져와 전년동월 대비 상승률 계산
                url = f"{base}/StatisticSearch/{key}/json/kr/1/30/{코드}/M/{fr}/{to}/00"
                rows = self._get_json(url).get("StatisticSearch", {}).get("row", [])
                if rows and len(rows) >= 12:
                    last = rows[-1]
                    cur = float(last["DATA_VALUE"])
                    prev_year = float(rows[-13]["DATA_VALUE"])
                    yoy = (cur / prev_year - 1) * 100
                    return {
                        "지표": "소비자물가 상승률", "값": round(yoy, 1),
                        "기준월": last["TIME"],
                        "전월": round((cur / float(rows[-2]["DATA_VALUE"]) - 1) * 100, 1) if len(rows) > 1 else None,
                        "출처": "한국은행 ECOS 소비자물가지수 실데이터", "시뮬": False,
                    }
            except Exception:
                pass
            except Exception:
                pass
        rnd = self._결정난수("물가", _date.today().strftime("%Y%m"))
        return {
            "지표": "소비자물가 상승률", "값": round(2.0 + rnd.uniform(-0.3, 0.4), 1),
            "기준월": _date.today().strftime("%Y-%m"),
            "전월": round(2.2 + rnd.uniform(-0.3, 0.4), 1),
            "출처": "통계청(시뮬레이션)", "시뮬": True,
        }

    # ---------------- 헬퍼 ----------------
    def platform(self, key, 기본):
        try:
            return self.설정["데이터소스"][key]["base_url"]
        except Exception:
            return 기본

    def 데이터소스현황(self):
        상태 = []
        try:
            fx = self.환율()
            상태.append({"항목": "원/달러 환율", "출처": fx["출처"], "시뮬": fx["시뮬"], "값": fx["기준환율"]})
        except Exception:
            pass
        try:
            w = self.날씨()
            상태.append({"항목": f"{w.get('도시', '서울')} 날씨", "출처": w["출처"], "시뮬": w["시뮬"],
                          "값": f"{w['현재온도']}° {w['기상상태']}"})
        except Exception:
            pass
        try:
            idx = self.지수시계열("KOSPI", 30)
            상태.append({"항목": "KOSPI 30일", "출처": idx["출처"], "시뮬": idx["시뮬"],
                          "최신": idx["시계열"][-1]["종가"] if idx["시계열"] else None})
        except Exception:
            pass
        try:
            r = self.기준금리()
            상태.append({"항목": "기준금리", "출처": r["출처"], "시뮬": r["시뮬"], "값": r["값"]})
        except Exception:
            pass
        try:
            c = self.물가상승률()
            상태.append({"항목": "물가상승률", "출처": c["출처"], "시뮬": c["시뮬"], "값": c["값"]})
        except Exception:
            pass
        return 상태