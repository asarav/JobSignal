"""Log redaction unit tests."""


from app.infrastructure.logging import redact_value


def test_redact_keys_masked():
    assert redact_value("resume", "secret text") == "[redacted]"
    assert redact_value("email", "a@b.com") == "[redacted]"
    assert redact_value("answer_book", {"k": "v"}) == "[redacted]"


def test_pii_patterns_masked():
    assert redact_value("note", "contact me at a@b.com") == "contact me at [redacted]"
    assert redact_value("note", "call 555-123-4567") == "call [redacted]"


def test_nested_mapping_redacted():
    out = redact_value("data", {"email": "a@b.com", "safe": "hello"})
    assert out["email"] == "[redacted]"
    assert out["safe"] == "hello"


def test_non_string_pass_through():
    assert redact_value("count", 5) == 5