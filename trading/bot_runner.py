import time
import bot

print("[BOT-RUNNER] starting standalone bot service", flush=True)

while True:
    try:
        b = bot.get_bot()
        if not b or not b.is_running():
            print("[BOT-RUNNER] bot not running; standalone runner needs create_bot wiring", flush=True)
        time.sleep(10)
    except Exception as e:
        print(f"[BOT-RUNNER] error: {e}", flush=True)
        time.sleep(5)
