"""AEO 대시보드 Railway 진입점."""
import importlib.util
import sys
import pathlib

_src = pathlib.Path(__file__).parent / "aeo_dashboard.py"
spec = importlib.util.spec_from_file_location("aeo_dashboard", _src)
_mod = importlib.util.module_from_spec(spec)
sys.modules["aeo_dashboard"] = _mod
spec.loader.exec_module(_mod)

app = _mod.app
