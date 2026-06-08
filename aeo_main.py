"""AEO 대시보드 Railway 진입점 — 한글 파일명 임포트 문제를 우회합니다."""
import importlib, sys, pathlib

# aeo_대시보드.py 를 명시적으로 로드
_src = pathlib.Path(__file__).parent / "aeo_대시보드.py"
spec = importlib.util.spec_from_file_location("aeo_dashboard", _src)
_mod = importlib.util.module_from_spec(spec)
sys.modules["aeo_dashboard"] = _mod
spec.loader.exec_module(_mod)

app = _mod.app
