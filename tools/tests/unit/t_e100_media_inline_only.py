#!/usr/bin/env python3
"""E100 (2026-11-09 audit, P3): the pinned model server only accepts media
sent inline as data: URIs — never a local path or a URL to open.

BEFORE: the launcher's guard and pin looked at Host, Origin, path, key and
model only. mlx_vlm collects image/audio/video references from message
content verbatim and its loaders open a local PATH or GET an http(s) URL, so
any client reaching :8080/:8081 could have the owner's server read
~/Desktop/x.png into the vision model, probe which files exist ("cannot
identify image file" vs "No such file"), or fetch a URL from the owner's
network.

AFTER: _GuardASGI reads a POST/PUT/PATCH body once, refuses (400) any media
reference that is not a data: URI anywhere in it — OpenAI chat parts, the
Responses API, Anthropic image sources, embeddings/rerank items, TTS
ref_audio — and replays the body to the route otherwise. Tool schemas and
the model's own tool-call arguments are not media and are never inspected.

The REAL launcher imported by path (stdlib only, no venv); the guard is
driven as ASGI with asyncio against a recording inner app.
"""
import asyncio
import importlib.util
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("HERMES_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
FAILS = []
PASSES = [0]


def check(name, cond, extra=""):
    if cond:
        PASSES[0] += 1
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s %s" % (name, str(extra)[-400:]))


def launcher():
    path = os.path.join(REPO, "mlx-vlm-launch.py")
    spec = importlib.util.spec_from_file_location("mlx_vlm_launch_e100", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


PNG = "data:image/png;base64,iVBORw0KGgo="
WAV = "data:audio/wav;base64,UklGRg=="


def chat(*parts, role="user"):
    return {"model": "m", "messages": [{"role": role, "content": list(parts)}]}


REFUSE = {
    "OpenAI image_url local path": chat({"type": "image_url", "image_url": {"url": "/Users/me/Desktop/a.png"}}),
    "OpenAI image_url file:// URL": chat({"type": "image_url", "image_url": {"url": "file:///etc/hosts"}}),
    "OpenAI image_url http URL": chat({"type": "image_url", "image_url": {"url": "http://127.0.0.1:9/b.png"}}),
    "OpenAI image_url https URL (string form)": chat({"type": "image_url", "image_url": "https://example.com/x.png"}),
    "input_image (Responses) path": {"model": "m", "input": [{"role": "user", "content": [
        {"type": "input_image", "image_url": "~/Pictures/p.jpg"}]}]},
    "input_audio path": chat({"type": "input_audio", "input_audio": {"data": "/tmp/a.wav", "format": "wav"}}),
    "input_audio raw base64 (not a data: URI)": chat({"type": "input_audio", "input_audio": {"data": "UklGRg==", "format": "wav"}}),
    "video part path": chat({"type": "video", "video": "/Users/me/Movies/m.mov"}),
    "input_video URL dict": chat({"type": "input_video", "video_url": {"url": "https://example.com/v.mp4"}}),
    "video_url path": chat({"type": "video_url", "video_url": "/tmp/v.mp4"}),
    "Anthropic image source url": {"model": "m", "messages": [{"role": "user", "content": [
        {"type": "image", "source": {"type": "url", "url": "https://example.com/i.png"}}]}]},
    "Anthropic image inside a tool_result": {"model": "m", "messages": [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t", "content": [
            {"type": "image", "source": {"type": "url", "url": "/etc/passwd"}}]}]}]},
    "embeddings item url": {"model": "m", "input": [{"url": "/Users/me/secret.png"}]},
    "rerank document image path": {"model": "m", "query": "q", "documents": [{"image": "/tmp/i.png"}]},
    "TTS ref_audio path": {"model": "m", "input": "hi", "ref_audio": "/Users/me/voice.wav"},
    "generate images list": {"model": "m", "prompt": "p", "images": [PNG, "/tmp/x.png"]},
    "upper-case FILE:/ scheme": chat({"type": "image_url", "image_url": {"url": "FILE:///etc/hosts"}}),
    "a data URI elsewhere does not excuse a path": chat(
        {"type": "image_url", "image_url": {"url": PNG}},
        {"type": "image_url", "image_url": {"url": "/tmp/second.png"}}),
    "file part": chat({"type": "file", "file": {"file_data": "/tmp/doc.pdf", "filename": "doc.pdf"}}),
}

ALLOW = {
    "plain text chat": {"model": "m", "messages": [{"role": "user", "content": "see /etc/hosts and https://x.y"}]},
    "text parts": chat({"type": "text", "text": "file:///etc/hosts"}),
    "data: image (what Hermes sends for screenshots)": chat(
        {"type": "text", "text": "what is this"}, {"type": "image_url", "image_url": {"url": PNG, "detail": "auto"}}),
    "data: image, leading space and upper case scheme": chat(
        {"type": "image_url", "image_url": {"url": "  DATA:image/png;base64,AAAA"}}),
    "data: audio": chat({"type": "input_audio", "input_audio": {"data": WAV, "format": "wav"}}),
    "Anthropic base64 image source": {"model": "m", "messages": [{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw0KGgo="}}]}]},
    "tool schema with url/path properties": {"model": "m", "messages": [{"role": "user", "content": "go"}],
        "tools": [{"type": "function", "function": {"name": "browse", "parameters": {
            "type": "object", "properties": {"url": {"type": "string"}, "path": {"type": "string"},
                                             "image": {"type": "string", "default": "/tmp/x"}}}}}]},
    "assistant tool_calls with dict arguments holding a url": {"model": "m", "messages": [
        {"role": "assistant", "content": "", "tool_calls": [{"id": "1", "type": "function", "function": {
            "name": "web_extract", "arguments": {"url": "https://example.com", "path": "/tmp/x"}}}]},
        {"role": "tool", "tool_call_id": "1", "content": "{\"url\": \"https://example.com\"}"}]},
    "Anthropic tool_use input with a url": {"model": "m", "messages": [{"role": "assistant", "content": [
        {"type": "tool_use", "id": "t", "name": "fetch", "input": {"url": "https://example.com", "path": "/x"}}]}]},
    "Responses function_call item": {"model": "m", "input": [
        {"type": "function_call", "call_id": "c", "name": "f", "arguments": "{\"path\": \"/x\"}"}]},
    "chat_template_kwargs + sampling": {"model": "m", "messages": [{"role": "user", "content": "x"}],
        "chat_template_kwargs": {"enable_thinking": False}, "temperature": 0, "max_tokens": 5},
    "null media value": chat({"type": "image_url", "image_url": None}),
}


async def drive(L, body, method="POST", ctype="application/json", chunks=1):
    seen = {}

    async def inner(scope, receive, send):
        msg = await receive()
        seen["body"] = msg.get("body")
        seen["more"] = msg.get("more_body")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})
    g = L._GuardASGI(inner)
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    parts = [raw[i * len(raw) // chunks:(i + 1) * len(raw) // chunks] for i in range(chunks)]
    q = [{"type": "http.request", "body": p, "more_body": i < chunks - 1} for i, p in enumerate(parts)]

    async def receive():
        return q.pop(0) if q else {"type": "http.disconnect"}
    out = []

    async def send(m):
        out.append(m)
    scope = {"type": "http", "method": method, "path": "/v1/chat/completions",
             "headers": [(b"host", b"127.0.0.1:8080"), (b"content-type", ctype.encode())]}
    await g(scope, receive, send)
    status = next((m["status"] for m in out if m["type"] == "http.response.start"), None)
    return status, seen, out


def main():
    L = launcher()
    if not hasattr(L, "_media_refusal"):        # the unfixed launcher: no check at all
        L._media_refusal = lambda body, ct="": None
    print("\n1. the pure check")
    for name, body in REFUSE.items():
        r = L._media_refusal(json.dumps(body).encode(), "application/json")
        check("refused: %s" % name, r is not None and r[0] == 400, r)
    for name, body in ALLOW.items():
        r = L._media_refusal(json.dumps(body).encode(), "application/json")
        check("allowed: %s" % name, r is None, r)
    check("non-JSON body passes (a multipart upload carries bytes)",
          L._media_refusal(b"--x\r\nContent-Disposition: form-data; name=file\r\n\r\n/etc/hosts",
                           "multipart/form-data; boundary=x") is None)
    check("JSON without a JSON content type is still checked",
          L._media_refusal(json.dumps(REFUSE["OpenAI image_url local path"]).encode(), "") is not None)
    # review R4: Starlette's request.json() is json.loads(bytes), which also
    # reads a UTF-8 BOM and UTF-16/32 bodies; FastAPI parses a body with NO
    # content type as JSON. The guard must see every body the route can parse.
    ref_body = json.dumps(REFUSE["OpenAI image_url local path"])
    for label, raw in (("UTF-8 BOM", b"\xef\xbb\xbf" + ref_body.encode()),
                       ("UTF-16", ref_body.encode("utf-16")),
                       ("UTF-32", ref_body.encode("utf-32"))):
        check("a %s JSON body with no content type is still checked" % label,
              json.loads(raw) and L._media_refusal(raw, "") is not None)
        check("a %s JSON body as text/plain is still checked" % label,
              L._media_refusal(raw, "text/plain") is not None)
    check("unparsable JSON passes to the route (which 422s it)",
          L._media_refusal(b'{"messages": [', "application/json") is None)
    deep = {"a": []}
    cur = deep["a"]
    for _ in range(200):
        nxt = []
        cur.append(nxt)
        cur = nxt
    check("absurd nesting is refused, not walked forever",
          L._media_refusal(json.dumps(deep).encode(), "application/json") is not None)

    print("\n2. through the ASGI guard")
    st, seen, _ = asyncio.run(drive(L, REFUSE["OpenAI image_url local path"]))
    check("a path reference: 400 and the route never runs", st == 400 and "body" not in seen, (st, seen))
    st, seen, _ = asyncio.run(drive(L, REFUSE["OpenAI image_url http URL"], chunks=3))
    check("a URL split over 3 body chunks: still refused", st == 400 and "body" not in seen, st)
    body = ALLOW["data: image (what Hermes sends for screenshots)"]
    st, seen, _ = asyncio.run(drive(L, body, chunks=4))
    check("a data: image: 200 and the route gets the WHOLE body once",
          st == 200 and seen.get("body") == json.dumps(body).encode() and seen.get("more") is False,
          (st, seen))
    st, seen, _ = asyncio.run(drive(L, REFUSE["OpenAI image_url local path"], method="GET"))
    check("a GET is not read (no body to check)", st == 200, st)
    st, seen, _ = asyncio.run(drive(L, b"\xef\xbb\xbf" + json.dumps(
        REFUSE["OpenAI image_url local path"]).encode(), ctype=""))
    check("a BOM-prefixed path reference with no content type: 400", st == 400, st)
    st, seen, _ = asyncio.run(drive(L, b"", chunks=1))
    check("an empty POST passes to the route", st == 200, st)
    print("\nTESTS %d passed %d failed" % (PASSES[0], len(FAILS)))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
