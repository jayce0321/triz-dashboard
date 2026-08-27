from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
BRIEFING_ROOT = PROJECT_ROOT / "조간-정세-브리핑"
THESES_DIR = ROOT / "테제"
INDEX_FILE = ROOT / "index.html"


def default_source() -> Path:
    today = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")
    year, month = today.split("-")[:2]
    return BRIEFING_ROOT / year / month / f"{today}_조간브리핑.md"


def main() -> int:
    parser = argparse.ArgumentParser(description="조간 브리핑을 호야랩 보조테제로 반영합니다.")
    parser.add_argument("source", nargs="?", type=Path, default=default_source())
    parser.add_argument("--open", action="store_true", help="반영 후 홈페이지를 엽니다.")
    args = parser.parse_args()

    source = args.source.expanduser().resolve()
    if not source.is_file():
        print(f"조간 브리핑 원문을 찾지 못했습니다: {source}", file=sys.stderr)
        return 1
    if not unicodedata.normalize("NFC", source.name).endswith("_조간브리핑.md"):
        print(f"조간 브리핑 파일명이 아닙니다: {source.name}", file=sys.stderr)
        return 1

    THESES_DIR.mkdir(exist_ok=True)
    target = THESES_DIR / source.name
    shutil.copy2(source, target)

    subprocess.run([sys.executable, str(ROOT / "홈페이지_생성.py")], cwd=ROOT, check=True)

    slug = source.stem
    generated = INDEX_FILE.read_text(encoding="utf-8")
    if f'id="{slug}"' not in generated or source.stem not in generated:
        print("홈페이지 생성 후 보조테제 카드 검증에 실패했습니다.", file=sys.stderr)
        return 2

    if args.open:
        subprocess.run(["open", str(INDEX_FILE)], check=False)

    print(f"조간 브리핑 반영 완료: {source}")
    print(f"보조테제 저장: {target}")
    print(f"홈페이지 생성: {INDEX_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
