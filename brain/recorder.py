"""Records the bot's window with ffmpeg and builds a soundtrack + subtitles from what was said."""
import subprocess
import time
import wave
from pathlib import Path

import numpy as np

from launcher import rel

RATE = 48000


def _srt_time(t):
    ms = int(round(t * 1000))
    return "%02d:%02d:%02d,%03d" % (ms // 3600000, ms // 60000 % 60, ms // 1000 % 60, ms % 1000)


class Recorder:
    def __init__(self, cfg, out_dir, title="ALTRON"):
        self.ffmpeg = str(rel(cfg["ffmpeg"]))
        self.out = rel(out_dir)
        self.title = title
        self.clips = []       # (start sec, int16 samples, speaker, text)
        self.proc = None
        self.t0 = None

    def start(self):
        self.out.mkdir(parents=True, exist_ok=True)
        self.video = self.out / "video_raw.mp4"
        log = open(self.out / "ffmpeg_record.log", "w", encoding="utf-8", errors="replace")
        # Windows Graphics Capture: records the OpenGL window even when other windows cover it
        source = "gfxcapture=window_title=^%s$:capture_cursor=0:max_framerate=30,hwdownload,format=bgra" % self.title
        cmd = [self.ffmpeg, "-y", "-f", "lavfi", "-i", source, "-fps_mode", "cfr", "-r", "30",
               "-c:v", "h264_nvenc", "-preset", "p5", "-cq", "24", "-pix_fmt", "yuv420p", str(self.video)]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT)
        self.t0 = time.time()

    def elapsed(self):
        return 0.0 if self.t0 is None else time.time() - self.t0

    def add(self, pcm_bytes, speaker, text, at=None):
        """at: when it started to sound (seconds of the recording); now by default."""
        if self.t0 is None or not pcm_bytes:
            return
        self.clips.append((self.elapsed() if at is None else at, np.frombuffer(pcm_bytes, dtype="<i2"), speaker, text))

    def stop(self, name):
        """Finish recording; returns the path of the final video with voice and subtitles."""
        if self.proc is None:
            return None
        duration = self.elapsed()
        try:
            self.proc.stdin.write(b"q")
            self.proc.stdin.flush()
        except Exception:
            pass
        try:
            self.proc.wait(60)
        except subprocess.TimeoutExpired:
            self.proc.kill()

        # soundtrack: every line placed at the moment it was said (lines of one speaker never overlap)
        track = np.zeros(int((duration + 3) * RATE), dtype=np.float32)
        busy_until = {}
        subs = []
        for start, pcm, speaker, text in self.clips:
            start = max(start, busy_until.get(speaker, 0.0))
            i = int(start * RATE)
            seg = pcm[: max(0, len(track) - i)].astype(np.float32)
            track[i:i + len(seg)] += seg
            end = start + len(pcm) / RATE
            busy_until[speaker] = end
            subs.append((start, max(end, start + 2.0), speaker, text))
        track = np.clip(track, -32767, 32767).astype("<i2")
        with wave.open(str(self.out / "sound.wav"), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(track.tobytes())
        with open(self.out / "subs.srt", "w", encoding="utf-8") as f:
            for n, (s, e, speaker, text) in enumerate(subs, 1):
                f.write("%d\n%s --> %s\n%s: %s\n\n" % (n, _srt_time(s), _srt_time(e), speaker, text))

        final = self.out / name
        style = "FontName=Arial,FontSize=16,Outline=2,Shadow=0,MarginV=24"
        cmd = [self.ffmpeg, "-y", "-i", "video_raw.mp4", "-i", "sound.wav",
               "-vf", "subtitles=subs.srt:force_style='%s'" % style,
               "-c:v", "h264_nvenc", "-preset", "p5", "-cq", "23", "-c:a", "aac", "-b:a", "160k", "-shortest", final.name]
        log = open(self.out / "ffmpeg_mux.log", "w", encoding="utf-8", errors="replace")
        r = subprocess.run(cmd, cwd=str(self.out), stdout=log, stderr=subprocess.STDOUT)
        if r.returncode != 0:  # e.g. no subtitle support: keep voice at least
            cmd = [self.ffmpeg, "-y", "-i", "video_raw.mp4", "-i", "sound.wav", "-c:v", "copy", "-c:a", "aac", "-shortest", final.name]
            subprocess.run(cmd, cwd=str(self.out), stdout=log, stderr=subprocess.STDOUT)
        return final
