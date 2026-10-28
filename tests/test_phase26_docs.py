import re
from pathlib import Path


def test_phase26_queue_usage_docs_match_the_bridge_contract():
    text = " ".join((Path(__file__).parents[1] / "DOCS.md").read_text(encoding="utf-8").lower().split())

    assert "the bridge owns one in-memory queue per speaker" in text
    assert "home assistant resolves every saved reference before replacing the current queue" in text
    assert "provider shortcuts remain one atomic queue item" in text
    assert "queue start, next, and previous are each sent once" in text
    assert "failed items are retained until the user explicitly retries or skips" in text
    assert "queues do not resume after an add-on restart" in text
    assert "resolved playback urls are never sent to the browser or written to the playlist library" in text
    assert "local/mock verification does not establish production or audible playback acceptance" in text
    assert not re.search(r"queued playback is not supported", text)
    for unsupported_claim in ("production acceptance complete", "release approved", "deployment approved"):
        assert unsupported_claim not in text