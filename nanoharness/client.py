"""Thin wrapper over Ollama's /api/chat. No SDK: the wire format stays visible."""
import http.client
import json
import urllib.error
import urllib.request

from . import config


# Tokens used by every reply in this process, as Ollama reports them on its last line: the
# REPL subtracts before and after a turn. "in" is the whole prompt each time, cached or not.
USAGE = {"in": 0, "out": 0}


class ModelError(RuntimeError):
    pass


class InfraError(ModelError):
    """The server or the connection failed, not the model: a stall, no route, a broken stream.
    The eval runner keeps these out of pass rates; for the REPL they are ModelErrors."""


def chat(messages, tools=None, on_token=None, temperature=None):
    """Send the whole history plus tool schemas; return the assistant message dict.

    The returned dict looks like:
        {"role": "assistant", "content": "...", "tool_calls": [...]}
    where each tool_call is {"function": {"name": str, "arguments": dict}}.
    Note Ollama does not assign tool-call ids the way the OpenAI API does.

    The reply is streamed; on_token(kind, text), if given, sees each fragment as it
    arrives, with kind "content" or "thinking". `temperature` overrides the agent's for this
    call only (the reviewer's); it doesn't make Ollama reload the model, num_ctx would.
    """
    payload = {
        "model": config.MODEL,
        "messages": messages,
        "stream": True,
        "think": config.THINK,
        "options": {
            "num_ctx": config.NUM_CTX,
            "temperature": config.TEMPERATURE if temperature is None else temperature,
            "num_predict": config.NUM_PREDICT,
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
        # 5xx is the server's trouble; 4xx means we sent something wrong, a harness bug.
        error = InfraError if e.code >= 500 else ModelError
        raise error(f"Ollama returned HTTP {e.code}: {e.read().decode()[:500]}") from e
    except urllib.error.URLError as e:
        raise InfraError(
            f"Cannot reach Ollama at {config.OLLAMA_HOST} ({e.reason}). "
            "Is `ollama serve` running?"
        ) from e
    except TimeoutError as e:
        raise InfraError(f"Ollama sent nothing for {config.HTTP_TIMEOUT}s; gave up.") from e
    except (OSError, http.client.HTTPException) as e:
        raise InfraError(f"The connection to Ollama broke mid-reply: {e}") from e


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
            raise InfraError(f"Unexpected line from Ollama: {raw[:500]!r}") from e
        if "error" in chunk:
            raise InfraError(f"Ollama error: {chunk['error']}")

        message = chunk.get("message") or {}
        for kind, parts in (("thinking", thinking), ("content", content)):
            piece = message.get(kind)
            if piece:
                parts.append(piece)
                if on_token:
                    on_token(kind, piece)
        tool_calls.extend(message.get("tool_calls") or [])

        if chunk.get("done"):
            USAGE["in"] += chunk.get("prompt_eval_count") or 0
            USAGE["out"] += chunk.get("eval_count") or 0
            if chunk.get("done_reason") == "length":
                where = "while still reasoning" if not content and not tool_calls else "mid-reply"
                raise ModelError(
                    f"The model was cut off {where}: it hit the cap of {config.NUM_PREDICT} tokens "
                    "per reply (NANO_NUM_PREDICT). Nothing it said in that reply was kept."
                )
            break
    else:
        raise InfraError("Ollama's reply ended before it was done.")

    reply = {"role": "assistant", "content": "".join(content)}
    if thinking:
        reply["thinking"] = "".join(thinking)
    if tool_calls:
        reply["tool_calls"] = tool_calls
    return reply
