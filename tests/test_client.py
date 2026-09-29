"""Offline tests for the stream parser, fed lines shaped like real Ollama output."""
import json

import pytest

from nanoharness.client import ModelError, read_stream


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
