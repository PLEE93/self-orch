from self_orch.rail import _delta_text, _parse_sse_line


def test_parse_done():
    assert _parse_sse_line("data: [DONE]") == {"done": True}


def test_parse_content_delta():
    line = 'data: {"choices":[{"delta":{"content":"GLM_OK"}}]}'
    parsed = _parse_sse_line(line)
    content, reasoning = _delta_text(parsed)
    assert content == "GLM_OK"
    assert reasoning == ""


def test_parse_reasoning_ignored_as_final():
    line = 'data: {"choices":[{"delta":{"reasoning_content":"thinking"}}]}'
    parsed = _parse_sse_line(line)
    content, reasoning = _delta_text(parsed)
    assert content == ""
    assert reasoning == "thinking"


def test_blank_and_noise_lines():
    assert _parse_sse_line("") is None
    assert _parse_sse_line(": keepalive") is None
    assert _parse_sse_line("data: not-json") is None
