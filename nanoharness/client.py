"""Thin wrapper over Ollama's /api/chat. No SDK: the wire format stays visible."""
import http.client
import json
import urllib.error
import urllib.request

from . import config


class ModelError(RuntimeError):
    pass


def chat(messages, tools=None, on_token=None):
    """Send the whole history plus tool schemas; return the assistant message dict.

    The returned dict looks like:
        {"role": "assistant", "content": "...", "tool_calls": [...]}
    where each tool_call is {"function": {"name": str, "arguments": dict}}.
    Note Ollama does not assign tool-call ids the way the OpenAI API does.

    The reply is streamed; on_token(kind, text), if given, sees each fragment as it
    arrives, with kind "content" or "thinking".
    """
    payload = {
        "model": config.MODEL,
        "messages": messages,
        "stream": True,
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
        # With streaming, the timeout bounds the gap between fragments, not the whole reply.
        with urllib.request.urlopen(req, timeout=config.HTTP_TIMEOUT) as resp:
            return read_stream(resp, on_token)
    except urllib.error.HTTPError as e:
        raise ModelError(f"Ollama returned HTTP {e.code}: {e.read().decode()[:500]}") from e
    except urllib.error.URLError as e:
        raise ModelError(
            f"Cannot reach Ollama at {config.OLLAMA_HOST} ({e.reason}). "
            "Is `ollama serve` running?"
        ) from e
    except TimeoutError as e:
        raise ModelError(f"Ollama sent nothing for {config.HTTP_TIMEOUT}s; gave up.") from e
    except (OSError, http.client.HTTPException) as e:
        raise ModelError(f"The connection to Ollama broke mid-reply: {e}") from e


def read_stream(lines, on_token=None):
    """Assemble Ollama's NDJSON stream into one assistant message.

    Each line is a fragment, e.g. {"message": {"content": "He"}, "done": false}.
    Text arrives in pieces; each tool call arrives whole in one line. The last line
    has "done": true.
    """
    content, thinking, tool_calls = [], [], []
    for raw in lines:
        if not raw.strip():
            continue
        try:
            chunk = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ModelError(f"Unexpected line from Ollama: {raw[:500]!r}") from e
        if "error" in chunk:
            raise ModelError(f"Ollama error: {chunk['error']}")

        message = chunk.get("message") or {}
        for kind, parts in (("thinking", thinking), ("content", content)):
            piece = message.get(kind)
            if piece:
                parts.append(piece)
                if on_token:
                    on_token(kind, piece)
        tool_calls.extend(message.get("tool_calls") or [])

        if chunk.get("done"):
            break
    else:
        raise ModelError("Ollama's reply ended before it was done.")

    reply = {"role": "assistant", "content": "".join(content)}
    if thinking:
        reply["thinking"] = "".join(thinking)
    if tool_calls:
        reply["tool_calls"] = tool_calls
    return reply
