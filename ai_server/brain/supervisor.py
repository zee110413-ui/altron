"""The watchman of a session: keeps Altron's brain alive while the commander plays.

    supervisor.py "Los Perrito" [--attach]

It starts session.py (the game window and Altron's body come up the first time; with --attach it takes the ones
already running) and watches it:
  * the brain crashed (an error)                          -> a new brain that picks up the running game and body
  * the brain hangs (no "I am alive" for 90 s)            -> killed and started again the same way
  * the brain asked for it (the AI failed, exit code 3)   -> the AI server is restarted too
The game window and Altron's body stay open all the time; they connect to the new brain by themselves in a few
seconds, and Altron carries on with what he was doing (keeping the production, watching the lines...).
It stops when the game window is closed, or after too many restarts in a row (then something needs a person).
"""
import json
import os
import subprocess
import sys
import time

from launcher import BRAIN_DIR

LOGS = BRAIN_DIR / "logs"
HEARTBEAT = LOGS / "brain_heartbeat.json"
REQUEST = LOGS / "restart_request.json"


def say(text):
    line = "%s [сторож] %s" % (time.strftime("%H:%M:%S"), text)
    print(line, flush=True)
    with open(LOGS / "supervisor.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def game_running():
    """Is the commander's game window (the session's host client) still open?"""
    cmd = ("Get-CimInstance Win32_Process -Filter \"Name='javaw.exe'\" | "
           "Where-Object { $_.CommandLine -match 'altron.autoWorld' } | Measure-Object | ForEach-Object { $_.Count }")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=30)
        return out.stdout.strip() not in ("", "0")
    except Exception:
        return True   # when in doubt, keep him alive


def kill_ai_server():
    subprocess.run(["taskkill", "/IM", "llama-server.exe", "/F"], capture_output=True)


def main(argv):
    world = argv[0] if argv and not argv[0].startswith("--") else "Los Perrito"
    attach = "--attach" in argv
    restarts = []
    run = 0
    while True:
        run += 1
        args = [sys.executable, "-u", "session.py", world] + (["--attach"] if attach else [])
        log = open(LOGS / ("session_%s_%d.log" % (time.strftime("%m%d_%H%M"), run)), "w", encoding="utf-8")
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        try:
            HEARTBEAT.unlink()   # the restart request stays: the new brain reads the phrase that was cut short
        except OSError:
            pass
        started = time.time()
        p = subprocess.Popen(args, cwd=str(BRAIN_DIR), stdout=log, stderr=subprocess.STDOUT, env=env)
        say("мозг запущен%s (журнал %s)" % (" к открытой игре" if attach else "", log.name))
        attach = True   # from now on the game window and the body are already there
        hung = False
        while p.poll() is None:
            time.sleep(10)
            try:
                beat = json.loads(HEARTBEAT.read_text(encoding="utf-8"))["t"]
            except (OSError, ValueError, KeyError):
                beat = started
            # a brain that loads its models may be quiet for a while; after that it answers every 10 s
            if time.time() - started > 300 and time.time() - beat > 90:
                hung = True
                say("мозг не отвечает уже %.0f с — перезапускаю" % (time.time() - beat))
                p.kill()
                p.wait()
        code = p.returncode
        log.close()
        request = {}
        try:
            request = json.loads(REQUEST.read_text(encoding="utf-8"))
            if request.get("t", 0) < started:
                request = {}   # an old one, from an earlier failure
        except (OSError, ValueError):
            pass
        if code == 0 and not hung:
            say("сессия закончилась (игра закрыта)")
            return
        if not game_running():
            say("игра закрыта — больше не перезапускаю")
            return
        reason = request.get("reason") or ("завис" if hung else "упал с кодом %s" % code)
        if request.get("llm") or hung:
            say("перезапускаю и ИИ-сервер (%s)" % reason)
            kill_ai_server()
        else:
            say("мозг упал (%s) — поднимаю новый" % reason)
        now = time.time()
        restarts = [t for t in restarts if now - t < 600] + [now]
        if len(restarts) > 5:
            say("больше 5 перезапусков за 10 минут — останавливаюсь, тут нужен человек (смотри журналы в brain\\logs)")
            return
        time.sleep(3)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main(sys.argv[1:])
