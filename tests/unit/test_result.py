"""Result type unit tests."""


from app.domain.models.common import Err, Ok, PortError


def test_ok_wraps_value():
    result: Ok[int] = Ok(42)
    assert result.value == 42


def test_err_wraps_error():
    err: Err[PortError] = Err(PortError(code="x", message="boom"))
    assert err.error.code == "x"


def test_result_is_union():
    assert isinstance(Ok(1), Ok)
    assert isinstance(Err(PortError(code="a", message="b")), Err)


def test_port_error_defaults():
    e = PortError(code="c", message="m")
    assert e.retryable is False
    assert e.details == {}