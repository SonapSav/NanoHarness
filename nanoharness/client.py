"""Thin wrapper over Ollama's /api/chat. No SDK: the wire format stays visible."""
import json
import urllib.error
import urllib.request

from . import config


class ModelError(RuntimeError):
    pass


def chat(messages, tools=None):
    """Send the whole history plus tool schemas; return the assistant message dict.

    The returned dict looks like:
        {"role": "assistant", "content": "...", "tool_calls": [...]}
    where each tool_call is {"function": {"name": str, "arguments": dict}}.
    Note Ollama does not assign tool-call ids the way the OpenAI API does.
    """
    payload = {
        "model": config.MODEL,
        "messages": messages,
        "stream": False,
        "think": config.THINK,
        "options": {
            "num_ctx": config.NUM_CTX,
            "temperature": config.TEMPERATURE,
        },
    }
    if tools:
        payload["tools"] = tools

    req = urllib.request.Request(
        f"{config.OLLAMA_HOST}/api/chat",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=config.HTTP_TIMEOUT) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise ModelError(f"Ollama returned HTTP {e.code}: {e.read().decode()[:500]}") from e
    except urllib.error.URLError as e:
        raise ModelError(
            f"Cannot reach Ollama at {config.OLLAMA_HOST} ({e.reason}). "
            "Is `ollama serve` running?"
        ) from e

    message = body.get("message")
    if message is None:
        raise ModelError(f"Unexpected response from Ollama: {json.dumps(body)[:500]}")
    return message
