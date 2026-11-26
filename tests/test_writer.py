"""AI writer with a fake Anthropic client (no network, no key)."""

from types import SimpleNamespace

import pytest

from movielock import writer
from movielock.errors import MovielockError

GOOD = """Here you go:
```lockscript
MOVIELOCK reel 30fps map=studio captions=on
TITLE Test
CAST haze = haze @ -1.5,0 > inf
CAST inf = infernus @ 1.5,0 > haze
SHOT wide on=haze
  haze ! demo_haze_01
SHOT closeup on=inf
  inf ! demo_inf_01
```
Short and sweet."""

BAD = GOOD.replace("demo_inf_01", "demo_inf_99")


class FakeAnthropic:
    class AuthenticationError(Exception): ...
    class NotFoundError(Exception): ...
    class RateLimitError(Exception): ...
    class APIConnectionError(Exception): ...

    class APIStatusError(Exception):
        status_code = 500
        message = "boom"


class FakeClient:
    def __init__(self, replies, stop="end_turn"):
        self.replies = list(replies)
        self.calls = []
        self.stop = stop
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        text = self.replies.pop(0)
        return SimpleNamespace(stop_reason=self.stop,
                               content=[SimpleNamespace(type="thinking", thinking=""),
                                        SimpleNamespace(type="text", text=text)])


@pytest.fixture
def fake(monkeypatch):
    def install(replies, stop="end_turn"):
        client = FakeClient(replies, stop)
        monkeypatch.setattr(writer, "_client", lambda cfg: (FakeAnthropic, client))
        return client
    return install


def test_write_valid_first_try(fake, demo_library, demo_config):
    client = fake([GOOD])
    res = writer.write_script("Haze teases Infernus on a rooftop", demo_library, demo_config, log=None)
    assert res.valid and res.attempts == 1
    assert res.script.startswith("MOVIELOCK reel")
    assert res.notes == "Short and sweet."
    call = client.calls[0]
    assert call["model"] == demo_config["writer"]["model"]
    assert call["fallbacks"] == "default"
    system = call["system"][0]["text"]
    # the system prompt lists the library so only real ids get used
    assert "demo_haze_01 | haze | You're late. Again." in system
    assert "haze: Haze; animations:" in system
    assert "Lockscript reference" in system


def test_repair_attempt(fake, demo_library, demo_config):
    client = fake([BAD, GOOD])
    res = writer.write_script("idea", demo_library, demo_config, log=None)
    assert res.valid and res.attempts == 2
    repair = client.calls[1]["messages"]
    assert repair[1]["role"] == "assistant"
    assert "unknown voice line 'demo_inf_99'" in repair[2]["content"]


def test_gives_up_after_one_repair(fake, demo_library, demo_config):
    fake([BAD, BAD])
    res = writer.write_script("idea", demo_library, demo_config, log=None)
    assert not res.valid and res.attempts == 2
    assert any("demo_inf_99" in str(i) for i in res.issues)


def test_unparseable_reply_is_repaired(fake, demo_library, demo_config):
    fake(["```lockscript\nSHOT sideways\n```", GOOD])
    res = writer.write_script("idea", demo_library, demo_config, log=None)
    assert res.valid and res.attempts == 2


def test_refusal(fake, demo_library, demo_config):
    fake([GOOD], stop="refusal")
    with pytest.raises(MovielockError, match="declined"):
        writer.write_script("idea", demo_library, demo_config, log=None)


def test_mentioned_heroes_go_first(demo_library):
    assert writer._mentioned_heroes("What if Infernus met someone", demo_library) == {"infernus"}
    listing = writer.library_listing(demo_library, {"infernus"})
    voices = listing.split("## Voice lines")[1]
    assert voices.index("demo_inf_01") < voices.index("demo_haze_01")


def test_extract_script():
    assert writer.extract_script("```\nSHOT wide\n```") == "SHOT wide\n"
    with pytest.raises(MovielockError):
        writer.extract_script("no script here")
