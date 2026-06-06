# -*- coding: utf-8 -*-
"""
One-shot Telegram finalizer. Waits (long-poll) for the first message sent to the
bot, captures the chat_id, writes it into config.json, sends a test ping, then
baselines the agent (--seed). Safe to re-run.
"""
import json, os, ssl, subprocess, sys, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(HERE, "config.json")
CTX = ssl.create_default_context()
MAX_ROUNDS = 14          # ~14 * 25s long-poll ≈ 5–6 minutes

def api(tok, method, qs=""):
    url = f"https://api.telegram.org/bot{tok}/{method}{qs}"
    with urllib.request.urlopen(url, timeout=40, context=CTX) as r:
        return json.loads(r.read())

def find_chat(updates):
    for u in updates:
        for k in ("message", "edited_message", "my_chat_member", "channel_post"):
            ch = (u.get(k) or {}).get("chat") or {}
            if ch.get("type") == "private" and ch.get("id"):
                return ch["id"], (ch.get("username") or ch.get("first_name") or "you")
    # fall back to any chat
    for u in updates:
        for k in ("message", "edited_message", "my_chat_member", "channel_post"):
            ch = (u.get(k) or {}).get("chat") or {}
            if ch.get("id"):
                return ch["id"], (ch.get("username") or ch.get("title") or "chat")
    return None, None

def main():
    cfg = json.load(open(CFG, encoding="utf-8"))
    tok = cfg["telegram"]["bot_token"]
    if not tok or "PASTE" in tok:
        print("NO_TOKEN"); return 2

    offset = None
    chat_id = who = None
    for rnd in range(MAX_ROUNDS):
        qs = "?timeout=25&limit=100&allowed_updates=%5B%22message%22%2C%22my_chat_member%22%5D"
        if offset is not None:
            qs += f"&offset={offset}"
        try:
            res = api(tok, "getUpdates", qs).get("result", [])
        except Exception as e:
            print(f"poll error: {e}"); continue
        if res:
            offset = res[-1]["update_id"] + 1
            chat_id, who = find_chat(res)
            if chat_id:
                break
        print(f"...waiting for your message (round {rnd+1}/{MAX_ROUNDS})", flush=True)

    if not chat_id:
        print("TIMEOUT: no message received. Re-run this script after messaging the bot.")
        return 1

    cfg["telegram"]["chat_id"] = str(chat_id)
    with open(CFG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    print(f"CHAT_ID={chat_id} ({who}) written to config.json")

    api(tok, "sendMessage",
        "?chat_id=%s&parse_mode=HTML&text=%s" % (
            chat_id,
            urllib.request.quote(
                "✅ <b>Intern Watch Agent connected!</b>\n"
                "I'll ping you here the moment new ML/AI Summer-2027 internships open.")))
    print("TEST_MESSAGE_SENT")

    r = subprocess.run([sys.executable, os.path.join(HERE, "agent.py"), "--seed"],
                       cwd=HERE, capture_output=True, text=True)
    print(r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "seed done")
    print("SETUP_COMPLETE")
    return 0

if __name__ == "__main__":
    sys.exit(main())
