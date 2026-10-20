"""Render the connected service shell without credentials or saved supplier records."""

import base64
import hashlib
import json
from pathlib import Path
from string import Template

ASSETS = Path(__file__).parent / "assets"


def render_hosted_workspace(
    *, google_identity: bool = False, password_mode: bool = False
) -> tuple[str, str]:
    script = (ASSETS / "hosted.js").read_text(encoding="utf-8")
    script = script.replace(
        "const identityMode = false;", "const identityMode = " + str(google_identity).lower() + ";"
    )
    script = script.replace(
        "const passwordMode = false;", "const passwordMode = " + str(password_mode).lower() + ";"
    )
    embedded_css = (ASSETS / "embedded.css").read_text(encoding="utf-8")
    script = script.replace(
        "const embeddedCss = null;", "const embeddedCss = " + json.dumps(embedded_css) + ";"
    )
    tour = (ASSETS / "onboarding.js").read_text(encoding="utf-8")
    script = script.replace(
        "const onboardingFactory = null;", "const onboardingFactory = (" + tour + ");"
    )
    casework = (ASSETS / "casework.js").read_text(encoding="utf-8")
    script = script.replace(
        "const caseworkFactory = null;", "const caseworkFactory = (" + casework + ");"
    )
    for name in ("responses", "comparison", "experience", "decisions"):
        content = (ASSETS / f"{name}.js").read_text(encoding="utf-8")
        script = script.replace(
            f"const {'replies' if name == 'responses' else name}Factory = null;",
            f"const {'replies' if name == 'responses' else name}Factory = (" + content + ");",
        )
    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    report_script = (ASSETS / "workspace.js").read_text(encoding="utf-8")
    report_digest = base64.b64encode(hashlib.sha256(report_script.encode()).digest()).decode()
    csp = (
        "default-src 'none'; img-src data:; style-src 'unsafe-inline'; "
        f"script-src 'sha256-{digest}' 'sha256-{report_digest}'; connect-src 'self'; frame-src 'self'; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
    )
    html = Template((ASSETS / "hosted.html").read_text(encoding="utf-8")).substitute(
        css=(ASSETS / "workspace.css").read_text(encoding="utf-8")
        + (ASSETS / "hosted.css").read_text(encoding="utf-8")
        + (ASSETS / "onboarding.css").read_text(encoding="utf-8")
        + (ASSETS / "workflows.css").read_text(encoding="utf-8")
        + (ASSETS / "decisions.css").read_text(encoding="utf-8")
        + (ASSETS / "refresh.css").read_text(encoding="utf-8"),
        script=script,
        logo=base64.b64encode((ASSETS / "loveholidays-logo.png").read_bytes()).decode(),
    )
    return html, csp
