from backend.entity_resolver import _merge_score


def _candidate(name: str, domains: set[str], urls: set[str], tokens: set[str]):
    return {
        "names": {name},
        "keys": {name.lower().replace(" ", "")},
        "domains": domains,
        "urls": urls,
        "tokens": tokens,
        "latest_at": None,
    }


def test_merge_score_shared_domain_and_containment():
    a = _candidate(
        "Mukoko",
        domains={"mukoko.com"},
        urls={"https://weather.mukoko.com/harare"},
        tokens={"mukoko"},
    )
    b = _candidate(
        "Mukoko weather",
        domains={"mukoko.com"},
        urls={"https://weather.mukoko.com/harare"},
        tokens={"mukoko", "weather"},
    )
    score, reason = _merge_score(a, b)
    assert score >= 2.5
    assert "shared_domain_root" in reason or "url_overlap" in reason


def test_merge_score_identity_conflict_blocks():
    a = _candidate(
        "Alpha",
        domains={"alpha.com"},
        urls={"https://alpha.com"},
        tokens={"alpha"},
    )
    b = _candidate(
        "Alpha Labs",
        domains={"betalabs.com"},
        urls={"https://betalabs.com"},
        tokens={"alpha", "labs"},
    )
    score, reason = _merge_score(a, b)
    assert score == 0.0
    assert reason == "identity_conflict"

