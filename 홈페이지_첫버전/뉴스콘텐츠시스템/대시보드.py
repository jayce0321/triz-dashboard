#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""뉴스콘텐츠시스템 웹 대시보드 (Flask).

- 기사발행물/ 의 기사 목록·미리보기·상태(초안/승인/발행) 관리
- 후보 소재 불러오기, 기사 생성 실행
- md·json-ld·이미지 제공

실행: python3 대시보드.py
접속: http://localhost:8123
"""

import html
import json
import os
import re
import subprocess
import sys

from flask import Flask, Response, jsonify, redirect, render_template_string, request, url_for

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from core.기사타입 import 타입목록, 타입찾기
from core.소재수집 import 헤드라인_수집, 예시소재
from core.데이터소스 import 공개데이터


def _env_로드():
    """프로젝트 루트의 .env에서 ECOS_API_KEY를 로드."""
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
    key = os.environ.get("ECOS_API_KEY")
    if key:
        설정.setdefault("데이터소스", {}).setdefault("한국은행ECOS", {})["api_key"] = key
    return 설정


app = Flask(__name__)

발행폴더 = os.path.join(BASE, "기사발행물")
상태파일 = os.path.join(BASE, "설정", "상태.json")


# ---------- 상태 저장 ----------
def 상태_로드():
    if os.path.exists(상태파일):
        with open(상태파일, encoding="utf-8") as f:
            return json.load(f)
    return {"기사": {}}


def 상태_저장(data):
    with open(상태파일, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------- 기사 인덱스 ----------
def 기사_색인():
    상태 = 상태_로드()
    items = []
    for 루트, _, files in os.walk(발행폴더):
        for fn in files:
            if fn.endswith(".md") and not fn.startswith("."):
                path = os.path.join(루트, fn)
                rel = os.path.relpath(path, BASE)
                stat = os.stat(path)
                상태객 = 상태["기사"].get(rel, {})
                state = 상태객.get("상태", "초안")
                items.append({
                    "rel": rel,
                    "basename": fn[:-3],
                    "경로": path,
                    "디렉토리": os.path.relpath(루트, BASE),
                    "수정시각": stat.st_mtime,
                    "상태": state,
                    "비고": 상태객.get("비고", ""),
                    "이미지": _첫이미지(루트, fn),
                    "제목": fn[:-3].replace("-", " "),
                })
    items.sort(key=lambda x: x["수정시각"], reverse=True)
    return items


def _첫이미지(루트, fn):
    base = fn[:-3]
    imgdir = os.path.join(루트, "이미지")
    if not os.path.isdir(imgdir):
        return None
    for f in sorted(os.listdir(imgdir)):
        if f.startswith(base) and f.endswith(".png") and "썸네일" in f:
            return "/" + os.path.relpath(os.path.join(imgdir, f), BASE)
    for f in sorted(os.listdir(imgdir)):
        if f.endswith(".png"):
            return "/" + os.path.relpath(os.path.join(imgdir, f), BASE)
    return None


# ---------- 마크다운 -> html ----------
def md_렌더(text):
    text = html.escape(text)
    out = []
    for line in text.splitlines():
        if line.startswith("# "):
            out.append(f"<h1>{line[2:]}</h1>")
        elif line.startswith("## "):
            out.append(f"<h2>{line[3:]}</h2>")
        elif line.startswith("### "):
            out.append(f"<h3>{line[4:]}</h3>")
        elif line.startswith("- "):
            out.append(f"<li>{line[2:]}</li>")
        elif line.startswith("> "):
            out.append(f"<blockquote>{line[2:]}</blockquote>")
        elif line.strip() == "---":
            out.append("<hr>")
        elif line.strip() == "":
            out.append("<br>")
        else:
            out.append(f"<p>{line}</p>")
    return "\n".join(out)


레이아웃 = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ 브랜드 }} 대시보드</title>
<style>
:root{--pt:#2367d7;--pc:#0ea5a0;}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,'Apple SD Gothic Neo','Malgun Gothic',sans-serif;background:linear-gradient(180deg,#eef3fc 0%,#f4f6fb 100%);color:#1c2733;line-height:1.6;min-height:100vh}
header{background:linear-gradient(135deg,#0b1220 0%,#2367d7 78%,#0ea5a0 100%);color:#fff;padding:22px 28px;border-bottom:4px solid #0ea5a0}
header h1{font-size:20px;font-weight:800;letter-spacing:-.3px}
header p{font-size:12px;opacity:.85}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,'Apple SD Gothic Neo','Malgun Gothic',sans-serif;background:#f4f6fb;color:#1c2733;line-height:1.6}
header{background:var(--pt);color:#fff;padding:18px 28px}
header h1{font-size:20px;font-weight:700}
header p{font-size:12px;opacity:.85}
main{max-width:1180px;margin:22px auto;padding:0 18px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:18px}
.card{background:#fff;border:1px solid #e3e8f2;border-radius:12px;overflow:hidden;display:flex;flex-direction:column}
.card img{width:100%;height:150px;object-fit:cover;background:#eef2fb}
.card .body{padding:14px 16px;flex:1}
.card h3{font-size:15px;margin-bottom:6px;line-height:1.4}
.card .meta{font-size:11px;color:#7b8794}
.badge{display:inline-block;font-size:11px;padding:2px 9px;border-radius:99px;font-weight:600}
.초안{background:#eef0f4;color:#4a5568}
.승인{background:#e6f2ff;color:#1f5fbf}
.발행{background:#e6f8ee;color:#17804a}
.act{margin-top:10px;display:flex;gap:8px;flex-wrap:wrap}
.btn{font-size:12px;padding:6px 12px;border-radius:8px;border:1px solid #cdd6e8;background:#fff;color:#2a3a52;cursor:pointer;text-decoration:none}
.btn.primary{background:var(--pt);color:#fff;border-color:var(--pt)}
.btn.sm{padding:3px 9px}
.btn:disabled{opacity:.45;cursor:not-allowed}
.section{margin:26px 0}
.section h2{font-size:16px;margin-bottom:10px;color:#33415c}
form.inline{display:inline}
.panel{background:#fff;border:1px solid #e3e8f2;border-radius:12px;padding:16px}
textarea,select,input{font-family:inherit;font-size:13px;padding:7px 10px;border:1px solid #cdd6e8;border-radius:8px}
textarea{width:100%;min-height:70px}
.rows{display:flex;gap:10px;margin-top:8px;flex-wrap:wrap;align-items:center}
.article-view{background:#fff;border:1px solid #e3e8f2;border-radius:12px;padding:26px;max-width:820px;margin:0 auto}
.article-view img{max-width:100%;border-radius:10px;margin:10px 0}
.article-view h1{font-size:24px;margin-bottom:8px}
.article-view h2{font-size:18px;margin:18px 0 6px}
.article-view blockquote{border-left:4px solid var(--pt);padding-left:12px;color:#445}
.flash{position:fixed;top:14px;right:14px;background:#17804a;color:#fff;padding:10px 16px;border-radius:10px;font-size:13px;z-index:99}
.top{display:flex;gap:12px;align-items:center;justify-content:space-between;flex-wrap:wrap}
</style></head><body>
<header><h1>📰 {{ 브랜드 }} 대시보드</h1>
<p>저작권 무침해 공공 데이터 기반 | SEO→GEO 데일리 콘텐츠 | 구글뉴스 노출 워크플로</p></header>
<main>
{% if flash %}<div class="flash">{{ flash }}</div>{% endif %}

<section class="section">
<h2>🛰️ 데이터소스 현황 (실데이터/시뮬레이션)</h2>
<div class="panel" style="display:flex;gap:14px;flex-wrap:wrap">
{% for s in 소스상태 %}
  <div style="background:#f7f9ff;border:1px solid #e3e8f2;border-radius:10px;padding:10px 14px;font-size:13px">
    <b>{{ s.항목 }}</b><br>
    <span class="badge {{ '실제' if not s.시뮬 else '초안' }}">{{ '실제' if not s.시뮬 else '시뮬' }}</span>
    <span style="color:#7b8794;font-size:12px">{{ s.출처 }}</span>
    {% if s.get('값') %}<div style="font-size:20px;font-weight:700;color:#33415c;margin-top:4px">{{ s.값 }}</div>{% endif %}
    {% if s.get('최신') %}<div style="font-size:20px;font-weight:700;color:#33415c;margin-top:4px">{{ s.최신 }}</div>{% endif %}
  </div>
{% endfor %}
</div>
</section>

<section class="section">
<div class="top"><h2>✍️ 새 기사 생성</h2><a class="btn" href="/">목록</a></div>
<div class="panel">
<form method="post" action="/생성" class="inline">
  <div class="rows">
    <select name="타입코드">
      {% for t in 타입들 %}<option value="{{ t.코드 }}">{{ t.이름 }} ({{ t.난이도 }}/5)</option>{% endfor %}
    </select>
    <input name="소재" placeholder="소재(이슈 키워드) 입력 또는 아래 후보에서 선택" size="40">
    <button class="btn primary" type="submit">기사 생성</button>
  </div>
</form>
<p style="font-size:12px;color:#7b8794;margin-top:8px">※ 소재 미입력 시 공공 지표 기반 기본 기사로 생성됩니다.</p>
</div>
</section>

<section class="section">
<div class="top"><h2>📡 오늘의 소재 후보 (구글뉴스 헤드라인)</h2>
<form method="post" action="/후보" class="inline"><button class="btn" type="submit">새로고침</button></form></div>
<div class="panel">
{% if 후보 %}
<ol style="font-size:13px;margin-left:20px">
{% for c in 후보 %}
<li><span style="cursor:pointer;color:#2367d7" onclick="document.querySelector('[name=소재]').value=this.textContent">{{ c.제목 }}</span>
  <span style="color:#9aa6b8;font-size:11px">({{ c.출처 }}<span class="badge {{ '실제' if c.get('분야') in ('금융','경제일반') else '초안' }}">{{ c.get('분야','종합') }}</span>)</span></li>
{% endfor %}
</ol>
{% else %}<p style="font-size:13px;color:#7b8794">후보가 없습니다. [새로고침]을 눌러 수집해 보세요.</p>{% endif %}
</div>
</section>

<section class="section">
<h2>🗂️ 기사 목록 ({{ 기사들|length }}건)</h2>
<div class="grid">
{% for g in 기사들 %}
<div class="card">
  {% if g.이미지 %}<img src="{{ g.이미지 }}" alt="">{% else %}<div style="height:150px;background:#eef2fb"></div>{% endif %}
  <div class="body">
    <h3>{{ g.제목 }}</h3>
    <div class="meta">{{ g.디렉토리 }} · {{ g.basename[:20] }}<br>
      <span class="badge {{ g.상태 }}">{{ g.상태 }}</span>
      {% if g.비고 %}<span style="color:#7b8794"> {{ g.비고 }}</span>{% endif %}
    </div>
    <div class="act">
      <a class="btn sm" href="/기사/{{ g.rel }}">미리보기</a>
      {% if g.상태 != '승인' %}<form method="post" action="/상태/{{ g.rel }}" class="inline"><input type="hidden" name="상태" value="승인"><button class="btn sm primary" type="submit">승인</button></form>{% endif %}
      {% if g.상태 != '발행' %}<form method="post" action="/상태/{{ g.rel }}" class="inline"><input type="hidden" name="상태" value="발행"><button class="btn sm" type="submit">발행</button></form>{% endif %}
      <form method="post" action="/삭제/{{ g.rel }}" class="inline" onsubmit="return confirm('삭제할까요?')"><button class="btn sm" type="submit">삭제</button></form>
    </div>
  </div>
</div>
{% endfor %}
</div>
</section>
</main>
</body></html>"""


@app.route("/")
def 목록(flash=None):
    상태 = 상태_로드()
    try:
        소스상태 = 공개데이터(설정_로드()).데이터소스현황()
    except Exception:
        소스상태 = []
    브랜드 = 설정_로드().get("브랜드", {}).get("사이트명", "뉴스콘텐츠시스템")
    return render_template_string(레이아웃, 브랜드=브랜드, 타입들=타입목록(), 기사들=기사_색인(),
        후보=상태.get("후보", []), 소스상태=소스상태, flash=flash or request.args.get("flash"))


@app.route("/후보", methods=["POST"])
def 후보_새로고침():
    후보 = 헤드라인_수집(한도=12)
    if not 후보 or str(후보[0].get("제목", "")).startswith("[수집불가]"):
        후보 = 예시소재()
    상태 = 상태_로드()
    상태["후보"] = 후보
    상태_저장(상태)
    return 목록()


@app.route("/생성", methods=["POST"])
def 생성():
    타입코드 = request.form.get("타입코드", "수치리포트")
    소재 = request.form.get("소재", "").strip()
    try:
        proc = subprocess.run(
            [sys.executable, os.path.join(BASE, "뉴스콘텐츠시스템.py"), "생성",
             "--type", 타입코드, "--소재", 소재],
            capture_output=True, text=True, timeout=120,
        )
        ok = proc.returncode == 0
    except subprocess.TimeoutExpired:
        ok, proc = False, None
    msg = "생성 완료" if ok else "생성 실패: " + (proc.stdout + proc.stderr if proc else "시간초과")
    return redirect(url_for("목록", flash=msg))


@app.route("/기사/<path:rel>")
def 미리보기(rel):
    path = os.path.join(BASE, rel)
    if not os.path.isfile(path) or not path.endswith(".md"):
        return "기사를 찾지 못했습니다", 404
    with open(path, encoding="utf-8") as f:
        body = f.read()
    img = _첫이미지(os.path.dirname(path), os.path.basename(path))
    return render_template_string(
        """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<title>미리보기</title><style>
:root{--pt:#2367d7}
body{font-family:-apple-system,'Apple SD Gothic Neo','Malgun Gothic',sans-serif;background:#f4f6fb;color:#1c2733;line-height:1.7}
.main{max-width:860px;margin:26px auto;padding:0 16px}
.preview{background:#fff;border:1px solid #e3e8f2;border-radius:14px;padding:30px}
h1{font-size:26px} h2{font-size:19px;margin:20px 0 6px} h3{font-size:16px}
img{max-width:100%;border-radius:10px;margin:12px 0}
blockquote{border-left:4px solid var(--pt);padding-left:12px;color:#445;margin:10px 0}
p{margin:8px 0}
li{margin-left:18px}
hr{border:0;border-top:1px solid #e3e8f2;margin:14px 0}
a{color:var(--pt)}
.btns{margin:16px 0;display:flex;gap:8px}
.btn{font-size:12px;padding:6px 12px;border-radius:8px;border:1px solid #cdd6e8;background:#fff;color:#2a3a52;cursor:pointer;text-decoration:none}
.btn.primary{background:var(--pt);color:#fff;border-color:var(--pt)}
</style></head><body>
<div class="main">
<div class="btns"><a class="btn" href="/">← 목록</a>
{% if '썸네일' in (img or '') %}<a class="btn" href="{{ img }}">썸네일 열기</a>{% endif %}</div>
<div class="preview">{{ 렌더 | safe }}</div>
</div></body></html>""",
        렌더=md_렌더(body),
        img=img,
    )


@app.route("/상태/<path:rel>", methods=["POST"])
def 상태변경(rel):
    상태 = 상태_로드()
    state = request.form.get("상태", "승인")
    비고 = request.form.get("비고", "")
    상태["기사"].setdefault(rel, {})["상태"] = state
    if 비고:
        상태["기사"][rel]["비고"] = 비고
    상태_저장(상태)
    return redirect(url_for("목록", flash=f"상태 변경: {state}"))


@app.route("/삭제/<path:rel>", methods=["POST"])
def 삭제(rel):
    path = os.path.join(BASE, rel)
    if os.path.isfile(path):
        os.remove(path)
        상태 = 상태_로드()
        상태["기사"].pop(rel, None)
        상태_저장(상태)
    return redirect(url_for("목록", flash="삭제 완료"))


@app.route("/<path:filepath>")
def 정적(filepath):
    path = os.path.join(BASE, filepath)
    if os.path.isfile(path):
        import mimetypes
        mt = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as f:
            return Response(f.read(), mimetype=mt)
    return "없음", 404


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8123, debug=False)