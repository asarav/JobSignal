"""S7: ToySignalProvider changes signal set."""

from app.domain.models.posting import JobHistory, SignalContext
from tests.support.toy_plugins import ToySignalProvider


def test_toy_signal_provider_changes_signal_set():
    provider = ToySignalProvider()
    result = provider.evaluate(JobHistory(job_id="j1"), SignalContext(job_id="j1"))
    assert result.signal_id == "signal.toy"
    assert result.status == "available"
    assert result.risk_contribution == 0.5