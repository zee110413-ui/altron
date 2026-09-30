"""Windows speech voices (SAPI 5): any voice installed in Windows can say Altron's words — for example the IVONA
"Maxim" voice that robot teammates in videos talk with, or the free "Microsoft Pavel" that Windows has. Altron does
not ship voices; it uses the ones installed on the PC (Windows Settings -> Time & Language -> Speech).

All COM calls run in one thread of their own: SAPI objects belong to the thread that made them."""
import os
import sys
import tempfile
import threading
import wave
from concurrent.futures import ThreadPoolExecutor

import numpy as np

SAFT22kHz16BitMono = 22      # SpeechAudioFormatType: 22 050 Hz, 16 bit, mono
SSFMCreateForWrite = 3
_pool = None
_lock = threading.Lock()
_local = threading.local()
_found = {}                  # a name asked for -> the full name of the installed voice (or None)


def available():
    return sys.platform == "win32"


def _init():
    import comtypes
    comtypes.CoInitialize()


def _run(fn, *args):
    global _pool
    with _lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(1, initializer=_init, thread_name_prefix="sapi")
    return _pool.submit(fn, *args).result()


def _speaker():
    if not hasattr(_local, "voice"):
        import comtypes.client
        _local.voice = comtypes.client.CreateObject("SAPI.SpVoice")
    return _local.voice


# Windows keeps its newer voices (Microsoft Pavel, David, Mark...) apart from the classic SAPI 5 ones; SAPI speaks
# with them too when they are listed from their own place in the registry
ONECORE = r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech_OneCore\Voices"


def _tokens():
    import comtypes.client
    out, seen = [], set()
    lists = [lambda: _speaker().GetVoices()]

    def onecore():
        cat = comtypes.client.CreateObject("SAPI.SpObjectTokenCategory")
        cat.SetId(ONECORE, False)
        return cat.EnumerateTokens()
    lists.append(onecore)
    for get in lists:
        try:
            toks = get()
        except Exception:
            continue   # an older Windows without the newer voices
        for i in range(toks.Count):
            desc = toks.Item(i).GetDescription()
            if desc not in seen:
                seen.add(desc)
                out.append((desc, toks.Item(i)))
    return out


def voices():
    """The names of the voices installed in Windows ([] elsewhere, or when SAPI does not answer)."""
    if not available():
        return []
    try:
        return _run(lambda: [d for d, _ in _tokens()])
    except Exception:
        return []


def find(name):
    """The installed voice whose name contains `name` («Maxim» finds «IVONA 2 Maxim»), or None."""
    q = str(name or "").strip().lower()
    if not q:
        return None
    if q not in _found:
        _found[q] = next((d for d in voices() if q in d.lower()), None)
    return _found[q]


def _speak(name, text, rate):
    import comtypes.client
    v = _speaker()
    for desc, tok in _tokens():
        if desc == name:
            v.Voice = tok
            break
    else:
        raise LookupError("нет голоса Windows «%s»" % name)
    fd, path = tempfile.mkstemp(suffix=".wav", prefix="altron_sapi_")
    os.close(fd)
    try:
        fmt = comtypes.client.CreateObject("SAPI.SpAudioFormat")
        fmt.Type = SAFT22kHz16BitMono
        stream = comtypes.client.CreateObject("SAPI.SpFileStream")
        stream.Format = fmt
        stream.Open(path, SSFMCreateForWrite, False)
        v.AudioOutputStream = stream
        v.Rate = rate
        v.Speak(text, 0)
        stream.Close()
        with wave.open(path, "rb") as w:
            return w.readframes(w.getnframes()), w.getframerate()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def speak(name, text, rate=0):
    """(int16 samples, sample rate) of `text` said by the installed voice `name` (its full name, see find);
    rate -10..10 as in Windows (0 — the voice's own pace)."""
    raw, sr = _run(_speak, name, text, max(-10, min(10, int(rate))))
    return np.frombuffer(raw, dtype="<i2"), sr
