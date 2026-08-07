# -*- coding: utf-8 -*-
"""이미지 생성기. 저작권 없는 '차트·인포그래픽·썸네일'을 matplotlib로 그린다.
사진 대신 데이터 시각화로 구글뉴스 요구(1200px 이상, 16:9)를 충족하고,
외부 사진·배경음악·삽화 사용을 원천 차단하여 저작권 문제를 없앤다.
"""

import os
from datetime import datetime

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager, rcParams

브랜드컬러 = "#2367d7"
보조컬러 = "#0ea5a0"
브랜드이름 = "jayce's 세계시그널"


def _그라데이션(ax, from_rgb, to_rgb, x0=0.0, x1=1.0, y0=0.0, y1=1.0):
    """부드러운 배경 그라데이션(모던한 톤)을 그린다."""
    import numpy as np
    from matplotlib.colors import LinearSegmentedColormap
    cmap = LinearSegmentedColormap.from_list("brand", [from_rgb, to_rgb])
    X, Y = np.meshgrid(np.linspace(x0, x1, 256), np.linspace(y0, y1, 256))
    ax.imshow(Y, extent=(x0, x1, y0, y1), origin="lower",
              cmap=cmap, aspect="auto", zorder=0)


def _hex2rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) / 255.0 for i in (0, 2, 4))


def _한글폰트():
    """한글 폰트 로드. 없으면 시스템 기본 한글 폰트로 대체."""
    candidates = ["Apple SD Gothic Neo", "AppleGothic", "NanumGothic", "Malgun Gothic", "sans-serif"]
    fonts = font_manager.findSystemFonts(fontpaths=None)
    chosen = None
    for cand in candidates:
        key = cand.lower().replace(" ", "")
        for p in fonts:
            base = os.path.basename(p).lower().replace(" ", "")
            if key in base:
                chosen = p
                break
        if chosen:
            break
    if chosen:
        font_manager.fontManager.addfont(chosen)
        prop = font_manager.FontProperties(fname=chosen)
        rcParams["font.family"] = prop.get_name()
    else:
        rcParams["font.family"] = "sans-serif"
    rcParams["axes.unicode_minus"] = False


def _축약(날짜):
    return datetime.fromisoformat(날짜).strftime("%m/%d")


def 라인차트(시계열, 출력경로, 제목, 포인트컬러=브랜드컬러):
    """일별 종가 라인차트(썸네일용). 16:9. 모던·세련된 톤."""
    _한글폰트()
    dates = [_축약(d["날짜"]) for d in 시계열]
    vals = [d["종가"] for d in 시계열]
    first, last = vals[0], vals[-1]
    up = last >= first

    fig, ax = plt.subplots(figsize=(16, 7), dpi=110)
    fig.patch.set_facecolor("#f7f9fc")
    ax.set_facecolor("#f7f9fc")

    # 그라데 채워진 영역 + 선
    ax.fill_between(range(len(vals)), min(vals) - (max(vals)-min(vals))*0.08, vals,
                    color=포인트컬러, alpha=0.15)
    ax.plot(range(len(vals)), vals, color=포인트컬러 if up else "#e11d48",
            linewidth=3.2, marker="o", markersize=5, markerfacecolor="white",
            markeredgewidth=1.5)

    n = len(dates)
    if n > 12:
        step = max(1, n // 12)
        ticks = list(range(0, n, step))
        ax.set_xticks(ticks)
        ax.set_xticklabels([dates[i] for i in ticks], rotation=0)
    else:
        ax.set_xticks(range(n))
        ax.set_xticklabels(dates)

    ax.grid(axis="y", linestyle="--", alpha=0.28, color="#c7d2fe")
    ax.tick_params(colors="#334155", labelsize=12)
    ax.set_ylabel("마감 지수", fontsize=13, color="#475569")
    ax.set_title(제목, fontsize=20, fontweight="bold", pad=14, color="#0b1220")
    for sp in ax.spines.values():
        sp.set_color("#cbd5e1")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    # 종가 태그
    tag = f"{last:,.0f}"
    ax.annotate(tag, xy=(len(vals)-1, last), xytext=(len(vals)-2.5, last),
                fontsize=18, fontweight="bold", color=포인트컬러 if up else "#e11d48")

    fig.tight_layout()
    fig.savefig(출력경로, dpi=110, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return 출력경로


def 썸네일(제목, 출력경로, 부제="", 상수상태="", 현재온="", pose=None):
    """브랜드 배너 썸네일. 16:9. 다크 그라데 + 보조색 액센트, 세련된 모던 톤."""
    _한글폰트()
    import matplotlib.patches as mpatches

    fig, ax = plt.subplots(figsize=(16, 9), dpi=110)
    fig.patch.set_facecolor("#0b1220")
    ax.set_facecolor("#0b1220")
    ax.set_xlim(0, 16)
    ax.set_ylim(0, 9)
    ax.axis("off")

    _그라데이션(ax, _hex2rgb("#0b1220"), _hex2rgb("#101c4f"), 0, 1, 0, 1)
    _그라데이션(ax, _hex2rgb("#2367d7"), _hex2rgb("#0ea5a0"), 0.55, 1.0, 0.0, 1.0)

    ax.text(0.9, 8.2, "jayce's 세계시그널", ha="left", va="center", fontsize=20,
            fontweight="bold", color="white",
            bbox=dict(boxstyle="round,pad=0.35", fc="#2367d7", ec="none"))

    ax.text(0.9, 5.4, 제목, ha="left", va="center", fontsize=40,
            fontweight="bold", color="white", linespacing=1.35)

    액센트 = ""
    if 상수상태 and 현재온:
        액센트 = f"{상수상태}  {현재온}°C"
    if 액센트:
        ax.text(0.9, 1.8, 액센트, ha="left", va="center", fontsize=22,
                fontweight="bold", color="#7dd3fc")

    ax.text(0.9, 0.8, 부제 or "jayce's 세계시그널 · 데이터 리포트", ha="left",
            va="center", fontsize=16, color="white", alpha=0.8)

    ax.add_patch(mpatches.Rectangle((13.0, 0.4), 2.4, 0.20, fc="#0ea5a0", ec="none"))
    ax.add_patch(mpatches.Rectangle((13.0, 0.85), 1.2, 0.20, fc="#2367d7", ec="none"))

    fig.savefig(출력경로, dpi=110, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return 출력경로
