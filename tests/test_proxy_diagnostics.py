import sys
from pathlib import Path
from types import SimpleNamespace
import pytest
import requests
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import resolve_prehrajto_stream as resolver


@pytest.mark.parametrize('transport', [False, True])
def test_proxy_credentials_are_absent_from_failures_and_logs(monkeypatch, capsys, transport):
    private_url = 'https://proxy.invalid/?key=private-proxy-value&url=episode'
    monkeypatch.setattr(resolver, '_via_cz_proxy', lambda _: private_url)
    monkeypatch.setattr(resolver, '_localhost_fetch_url', lambda _: None)
    monkeypatch.setattr(resolver, 'RESOLVE_MIN_GAP', 0)
    def get(*args, **kwargs):
        if transport:
            raise requests.Timeout('Request timed out at ' + private_url)
        return SimpleNamespace(status_code=503, ok=False, headers={}, text=private_url)
    session = SimpleNamespace(headers={}, get=get)
    with pytest.raises(resolver.ResolveError) as failure:
        resolver.resolve('https://prehraj.to/example/abcdef123456', session=session, max_retries=0)
    assert not failure.value.permanent
    assert 'private-proxy-value' not in str(failure.value)
    assert 'private-proxy-value' not in capsys.readouterr().out
