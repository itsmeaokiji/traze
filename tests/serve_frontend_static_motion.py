"""Serve isolated reduced-motion CSS fixtures for visual tests.

The fixture server rejects live source operations and leaves OS preferences unchanged.
"""

import runpy
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import hemi_lookup.web


if __name__ == "__main__":
    stylesheet = (ROOT / "frontend/src/styles.css").read_text(encoding="utf-8")
    body = stylesheet.split("@media (prefers-reduced-motion: reduce)", 1)[1].strip()
    assert body.startswith("{") and body.endswith("}")
    declarations = body[1:-1].strip()
    assert "animation: none !important" in declarations
    assert "transition: none !important" in declarations
    with tempfile.TemporaryDirectory(prefix="traze_motion_static_") as directory:
        temporary = Path(directory).resolve()
        assert temporary.is_relative_to((ROOT / "output/qa-temp-step9").resolve())
        assets = temporary / "web"
        shutil.copytree(ROOT / "web", assets)
        css_files = list((assets / "assets").glob("*.css"))
        assert css_files
        for path in css_files:
            path.write_text(path.read_text(encoding="utf-8") + "\n" + declarations + "\n", encoding="utf-8")
        with patch.object(hemi_lookup.web, "STATIC", assets):
            runpy.run_path(str(ROOT / "tests/serve_frontend_states.py"), run_name="__main__")
