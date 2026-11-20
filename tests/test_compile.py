"""Every module must at least compile (catches syntax errors in scripts that tests don't import)."""
import compileall
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_all_modules_compile():
    for pkg in ("verity", "eval", "scripts", "tests"):
        assert compileall.compile_dir(str(ROOT / pkg), quiet=1, force=True), f"syntax error in {pkg}/"
