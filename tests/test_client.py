"""Offline tests for the stream parser, fed lines shaped like real Ollama output."""
import json

import pytest

from nanoharness.client import InfraError, ModelError, read_stream


def line(done=False, **message):
    return (json.dumps({"message": {"role": "assistant", **message}, "done": done}) + "\n").encode()


def test_text_fragments_are_joined_and_streamed_in_order():
    seen = []
    reply = read_stream([line(content="Hel"), line(content="lo"), line(done=True, content="")],
                        on_token=lambda kind, text: seen.append((kind, text)))
    assert reply == {"role": "assistant", "content": "Hello"}
    assert seen == [("content", "Hel"), ("content", "lo")]


def test_tool_calls_are_collected():
    """Captured from aeroadvisor-agent: the call arrives whole in one line."""
    call = {"id": "call_pt8ftoha",
            "function": {"index": 0, "name": "read_file", "arguments": {"path": "pyproject.toml"}}}
    reply = read_stream([line(content="", tool_calls=[call]), line(done=True, content="")])
    assert reply["tool_calls"] == [call]


def test_thinking_is_kept_separate_from_content():
    seen = []
    reply = read_stream([line(thinking="hmm "), line(thinking="ok"), line(content="Done"),
                         line(done=True)], on_token=lambda k, t: seen.append(k))
    assert reply["thinking"] == "hmm ok" and reply["content"] == "Done"
    assert seen == ["thinking", "thinking", "content"]


def test_reply_cut_off_by_num_predict_raises():
    """Captured shape: a capped reply ends with done_reason "length" and, mid-reasoning, no content."""
    last = (json.dumps({"message": {"role": "assistant", "content": ""}, "done": True,
                        "done_reason": "length"}) + "\n").encode()
    with pytest.raises(ModelError, match="while still reasoning"):
        read_stream([line(thinking="actually, wait"), last])


def test_server_failures_are_infra_and_the_cutoff_is_not():
    """The eval runner keeps InfraErrors out of pass rates; a cut-off is the model's doing."""
    for lines in ([b'{"error": "runner crashed"}\n'], [line(content="half")], [b"<html>\n"]):
        with pytest.raises(InfraError):
            read_stream(lines)
    last = (json.dumps({"message": {"role": "assistant", "content": ""}, "done": True,
                        "done_reason": "length"}) + "\n").encode()
    with pytest.raises(ModelError) as e:
        read_stream([line(thinking="hmm"), last])
    assert not isinstance(e.value, InfraError)


def test_blank_lines_are_ignored():
    assert read_stream([b"\n", line(content="x"), b"  \n", line(done=True)])["content"] == "x"


def test_error_line_raises():
    with pytest.raises(ModelError, match="model not found"):
        read_stream([b'{"error": "model not found"}\n'])


def test_stream_that_stops_before_done_raises():
    with pytest.raises(ModelError, match="before it was done"):
        read_stream([line(content="half a sen")])


def test_garbage_line_raises():
    with pytest.raises(ModelError, match="Unexpected line"):
        read_stream([b"<html>proxy error</html>\n"])


def test_temperature_can_be_set_per_call(monkeypatch):
    """The reviewer runs cooler than the agent; nothing else in the request may differ."""
    import io
    import urllib.request
    from nanoharness import client, config
    sent = []

    def fake_urlopen(req, timeout=None):
        sent.append(json.loads(req.data))
        return io.BytesIO(line(done=True, content="OK"))
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client.chat([{"role": "user", "content": "hi"}])
    client.chat([{"role": "user", "content": "hi"}], temperature=0.2)
    assert sent[0]["options"]["temperature"] == config.TEMPERATURE
    assert sent[1]["options"] == {**sent[0]["options"], "temperature": 0.2}


def test_token_counts_from_the_last_line_add_up():
    from nanoharness import client
    before = dict(client.USAGE)
    last = (json.dumps({"message": {"role": "assistant", "content": ""}, "done": True,
                        "prompt_eval_count": 1200, "eval_count": 34}) + "\n").encode()
    read_stream([line(content="hi"), last])
    read_stream([line(content="again"), last])
    assert client.USAGE["in"] - before["in"] == 2400 and client.USAGE["out"] - before["out"] == 68


def test_think_takes_on_off_or_a_level(monkeypatch):
    from nanoharness import config
    assert config.think_setting("1") is True and config.think_setting("on") is True
    assert config.think_setting("0") is False and config.think_setting("") is False
    assert config.think_setting("LOW") == "low" and config.think_setting("medium") == "medium"
    with pytest.raises(SystemExit, match="NANO_THINK"):
        config.think_setting("maybe")


def test_a_think_level_is_sent_to_ollama(monkeypatch):
    import io
    import urllib.request
    from nanoharness import client, config
    sent = []
    monkeypatch.setattr(config, "THINK", "low")
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=None: (sent.append(json.loads(req.data)),
                                                   io.BytesIO(line(done=True, content="ok")))[1])
    client.chat([{"role": "user", "content": "hi"}])
    assert sent[0]["think"] == "low"
