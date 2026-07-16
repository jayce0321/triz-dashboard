from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parent
THESES_DIR = ROOT / "테제"
INDEX_FILE = ROOT / "index.html"


def candidate_dirs() -> list[Path]:
    home = Path.home()
    return [home / "Downloads", home / "Desktop"]


def find_latest_markdown() -> Path | None:
    files: list[Path] = []
    for directory in candidate_dirs():
        if not directory.exists():
            continue
        files.extend(path for path in directory.glob("*.md") if path.is_file())
        files.extend(path for path in directory.glob("*.markdown") if path.is_file())
    if not files:
        return None
    dated_files = [path for path in files if re.match(r"\d{4}-\d{2}-\d{2}", path.name)]
    target_files = dated_files or files
    return max(target_files, key=lambda path: path.stat().st_mtime)


def main() -> int:
    latest = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else find_latest_markdown()
    if not latest:
        print("Downloads 또는 Desktop에서 Markdown 파일을 찾지 못했습니다.")
        print("홈페이지에서 Markdown 다운로드를 먼저 실행한 뒤 다시 시도하세요.")
        return 1
    if not latest.exists():
        print(f"파일을 찾지 못했습니다: {latest}")
        return 1

    THESES_DIR.mkdir(exist_ok=True)
    target = THESES_DIR / latest.name
    if target.exists():
        stem = target.stem
        suffix = target.suffix
        counter = 2
        while target.exists():
            target = THESES_DIR / f"{stem}-{counter}{suffix}"
            counter += 1

    shutil.copy(latest, target)
    os.utime(target, None)
    print(f"복사 완료: {latest}")
    print(f"반영 위치: {target}")

    subprocess.run([sys.executable, str(ROOT / "홈페이지_생성.py")], cwd=ROOT, check=True)
    subprocess.run(["open", str(INDEX_FILE)], check=False)
    print("홈페이지 재생성 완료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
