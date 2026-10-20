import base64
import hashlib
import re
from pathlib import Path

from test_hosted_api import client

ROOT = Path(__file__).resolve().parents[1]


def test_tour_is_optional_accessible_and_restartable():
    http, _ = client()
    html = http.get("/").text
    assert 'id="workspace-tour"' in html and "hidden" in html
    assert 'id="tour-card"' in html and 'role="dialog"' in html
    assert 'aria-labelledby="tour-title"' in html
    assert "Start guided task" in html and "Explore myself" in html
    assert 'id="start-tour"' in html
    assert 'id="tour-back"' in html and 'id="tour-skip"' in html
    assert "prefers-reduced-motion" in html


def test_tour_is_embedded_in_the_exact_approved_script_not_loaded_remotely():
    http, _ = client()
    response = http.get("/")
    script = re.search(r"<script>(.*?)</script>", response.text, re.S)[1]
    assert "supplier-workspace-tour-v3" in script
    assert "sourceKind" in script
    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    assert f"'sha256-{digest}'" in response.headers["content-security-policy"]
    assert "script src=" not in response.text
    assert "prefers-reduced-motion" in (ROOT / "reconciliation/assets/onboarding.css").read_text(
        encoding="utf-8"
    )
