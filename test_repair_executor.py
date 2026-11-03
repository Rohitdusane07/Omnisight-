import tempfile
from pathlib import Path
from backend.services.repair_executor import apply_repair_fix, safe_resolve_target_file
from backend.models import Issue, CodeDiff

def test_safe_resolve_prevents_traversal():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        # Attempt traversal outside root
        malicious = "../../../secret.txt"
        resolved = safe_resolve_target_file(root, malicious)
        assert resolved is None

def test_apply_repair_fix_in_sandbox():
    issue = Issue(
        id="iss-test-exec",
        inspectionId="insp-1",
        title="Test Fix",
        severity="medium",
        category="visual",
        status="open",
        description="Fix test",
        affectedComponent="Card",
        affectedPage="https://example.com",
        screenshotRef="",
        suggestedFix="Update CSS",
        codeDiff=CodeDiff(
            before="color: red;",
            after="color: blue;",
            language="css",
            fileName="src/card.css"
        ),
        createdAt="2026-10-11T12:00:00"
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        card_css = Path(tmpdir) / "src" / "card.css"
        card_css.parent.mkdir(parents=True, exist_ok=True)
        card_css.write_text(".card { color: red; margin: 4px; }", encoding="utf-8")

        result = apply_repair_fix(issue, project_path=tmpdir)
        assert result.success is True
        assert card_css.read_text(encoding="utf-8") == ".card { color: blue; margin: 4px; }"
        assert result.backup_path is not None
