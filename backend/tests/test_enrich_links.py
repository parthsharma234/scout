from backend.enrich_links import google_cse_search


def test_google_cse_empty_without_credentials(monkeypatch):
    monkeypatch.delenv("GOOGLE_CSE_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_CSE_CX", raising=False)
    assert google_cse_search("weather startup", limit=5) == []

