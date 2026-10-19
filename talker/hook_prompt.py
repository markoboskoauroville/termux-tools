#!/usr/bin/env python3
"""UserPromptSubmit hook: listen for the code words.

`talk on` and `talk off` are things said, not commands run, so something has
to read every prompt and notice them. That is this. It flips the switch and
tells Claude what happened, so the reply can acknowledge it rather than
wondering what you meant.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.expanduser("~"), ".talker"))
import talker  # noqa: E402


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    prompt = (data.get("prompt") or "").strip()
    st = talker.load_state()

    # "say <something>" is a third code word: speak exactly those words, on
    # the spot, whether or not talk mode is on. It is anchored to the start of
    # the prompt so that asking what a file says, or to write code that says
    # something, is not mistaken for an order to speak.
    # "hush" is the pause button, not something said to Claude. Toggle the
    # speech and block the prompt: if it reached Claude, the reply to it
    # would be spoken and start her talking again.
    if talker.HUSH_RE.match(prompt):
        result = talker.toggle_pause()
        print(json.dumps({"decision": "block",
                          "reason": f"hush: {result}"}))
        return 0

    m = talker.SAY_RE.match(prompt)
    if m:
        words = m.group(1).strip()
        if words:
            subprocess.Popen(
                [sys.executable, os.path.join(talker.TALK_DIR, "talker.py"),
                 "say", words],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True)
            print(f"[talker] Spoken aloud already: \"{words}\". "
                  f"Do not repeat it back or run any command to say it - "
                  f"it has been said. Just acknowledge briefly.")
        return 0

    if talker.ON_RE.match(prompt):
        st["on"] = True
        talker.save_state(st)
        subprocess.Popen(
            [sys.executable, os.path.join(talker.TALK_DIR, "talker.py"),
             "say", "Talk mode on. I will read my replies to you."],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
        print(f"[talker] Talk mode is now ON. Every reply is spoken aloud in "
              f"{st.get('name')}'s voice until the user says 'talk off'. "
              f"Keep replies brief and speakable while it is on.")
    elif talker.OFF_RE.match(prompt):
        st["on"] = False
        talker.save_state(st)
        talker.stop_playing()
        print("[talker] Talk mode is now OFF. Replies are no longer spoken.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
