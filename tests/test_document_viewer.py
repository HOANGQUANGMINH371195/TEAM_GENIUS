from src.services.document_viewer import sanitize_document_html


def test_sanitizer_removes_active_content_and_event_handlers():
    result = sanitize_document_html(
        '<h2 onclick="alert(1)">Điều 1</h2><script>alert(2)</script>'
        '<a href="javascript:alert(3)" title="x">x</a><table><tr><td>100%</td></tr></table>'
    )
    assert "Điều 1" in result
    assert "100%" in result
    assert "script" not in result.lower()
    assert "onclick" not in result.lower()
    assert "javascript:" not in result.lower()
