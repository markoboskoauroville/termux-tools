#!/usr/bin/env python3
"""Stop hook: read the finished reply out loud.

Claude writes a reply and stops; this fires at that moment, takes the last
assistant message out of the transcript and hands it to the talker.

It returns IMMEDIATELY. Speaking happens in a detached process, because a hook
that waits for forty seconds of speech to finish is a hook that freezes the
prompt you wanted to type into.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.expanduser("~"), ".talker"))
import talker  # noqa: E402


def last_assistant_text(path):
    """The last thing Claude actually said, out of the transcript jsonl.

    Only text blocks: a reply that was all tool calls has nothing to say, and
    reading tool input aloud would be gibberish."""
    if not path or not os.path.isfile(path):
        return ""
    try:
        lines = open(path, errors="replace").read().splitlines()
    except OSError:
        return ""
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if rec.get("type") != "assistant":
            continue
        msg = rec.get("message") or {}
        if msg.get("role") != "assistant":
            continue
        parts = []
        content = msg.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text") or "")
        text = "\n".join(p for p in parts if p).strip()
        if text:
            return text
    return ""


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    # Claude Code sets this when a Stop hook has already run for this turn.
    # Speaking twice is worse than not speaking.
    if data.get("stop_hook_active"):
        return 0
    st = talker.load_state()
    if not st.get("on"):
        return 0
    # A chat can be muted on its own while the others keep talking: its
    # session id is listed in muted_sessions.
    if data.get("session_id") in (st.get("muted_sessions") or []):
        return 0
    # The reply is in the hook input itself; the transcript is the fallback
    # for an older Claude Code that does not send it. A session started with
    # transcript saving off has no file, and she was silent in those.
    text = (data.get("last_assistant_message") or
            last_assistant_text(data.get("transcript_path")))
    if not text.strip():
        return 0
    subprocess.Popen(
        [sys.executable, os.path.join(talker.TALK_DIR, "talker.py"), "say", text],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL, start_new_session=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
