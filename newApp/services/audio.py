import subprocess
import os
import threading
import time

from config import Config


_recording_process = None
_recording_lock = threading.Lock()
_active_playback = []
_playback_lock = threading.Lock()


def set_volume(percent):
    level = int(60 + (percent / 100.0) * 67)
    level = max(0, min(127, level))
    card = Config.SOUND_CARD_NAME
    try:
        subprocess.run(
            ["amixer", "-D", f"hw:{card}", "sset", "Speaker", str(level)],
            check=False, capture_output=True,
        )
    except FileNotFoundError:
        print("[Audio] amixer not found")


def set_capture_volume(percent=100):
    card = Config.SOUND_CARD_NAME
    try:
        subprocess.run(
            ["amixer", "-D", f"hw:{card}", "sset", "Capture", str(percent)],
            check=False, capture_output=True,
        )
    except FileNotFoundError:
        pass


# --- ALSA device helpers ---

def _capture_device():
    """Return the ALSA capture device name.

    Uses 'default' which routes through our asoundrc dsnoop config
    (ipc_key 666666). This allows multiple capture clients and
    simultaneous recording + playback (full-duplex).
    """
    return "default"


def _playback_device():
    """Return the ALSA playback device name.

    Uses 'default' which routes through our asoundrc dmix config
    (ipc_key 555555). This allows multiple playback clients and
    simultaneous recording + playback (full-duplex).
    """
    return "default"


# --- File-based recording (legacy mode) ---

def start_recording(output_path):
    global _recording_process
    with _recording_lock:
        if _recording_process and _recording_process.poll() is None:
            _recording_process.terminate()
            try:
                _recording_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                _recording_process.kill()
            _recording_process = None
        device = _capture_device()
        cmd = [
            "arecord", "-D", device,
            "-f", "S16_LE", "-r", "16000", "-c", "1",
            output_path,
        ]
        _recording_process = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        print(f"[Audio] Recording started: {output_path}")
        return _recording_process


def stop_recording():
    global _recording_process
    with _recording_lock:
        if _recording_process and _recording_process.poll() is None:
            _recording_process.terminate()
            try:
                _recording_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                _recording_process.kill()
            print("[Audio] Recording stopped")
        _recording_process = None


# --- Streaming audio (Voice Agent mode) ---

def start_recording_stream(sample_rate=16000):
    """Start arecord returning subprocess -- read raw PCM from stdout."""
    device = _capture_device()
    cmd = [
        "arecord", "-D", device,
        "-f", "S16_LE", "-r", str(sample_rate), "-c", "1",
        "-t", "raw",
    ]
    print(f"[Audio] Mic stream cmd: {' '.join(cmd)}")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    time.sleep(0.1)
    if proc.poll() is not None:
        stderr_out = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
        print(f"[Audio] WARNING: arecord failed! rc={proc.returncode} stderr={stderr_out}")
    else:
        print(f"[Audio] Mic stream started ({sample_rate}Hz via {device})")
    return proc


def start_playback_stream(sample_rate=16000):
    """Start aplay returning subprocess -- write raw PCM to stdin."""
    device = _playback_device()
    cmd = [
        "aplay", "-D", device,
        "-r", str(sample_rate), "-f", "S16_LE", "-c", "1",
        "-t", "raw",
    ]
    print(f"[Audio] Speaker stream cmd: {' '.join(cmd)}")
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    _track_playback(proc)

    # Check it didn't die immediately
    time.sleep(0.1)
    if proc.poll() is not None:
        stderr_out = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
        print(f"[Audio] WARNING: aplay exited immediately! rc={proc.returncode} stderr={stderr_out}")
    else:
        print(f"[Audio] Speaker stream started ({sample_rate}Hz via {device})")
    return proc


def _track_playback(proc):
    with _playback_lock:
        _active_playback.append(proc)


# --- File-based playback ---

def play_audio_file(file_path, blocking=False):
    if not os.path.exists(file_path):
        print(f"[Audio] File not found: {file_path}")
        return

    device = _playback_device()
    ext = os.path.splitext(file_path)[1].lower()

    if ext in (".mp3", ".ogg", ".flac"):
        wav_path = file_path + ".wav"
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", file_path, "-ar", "48000", "-ac", "2", wav_path],
                capture_output=True, timeout=30,
            )
            file_path = wav_path
        except Exception as e:
            print(f"[Audio] ffmpeg convert failed: {e}")
            return

    cmd = ["aplay", "-D", device, file_path]
    print(f"[Audio] Playing: {file_path}")
    if blocking:
        subprocess.run(cmd, capture_output=True)
    else:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _track_playback(proc)
        return proc


def play_audio_bytes(audio_bytes, format_hint="wav"):
    tmp_path = f"/tmp/mombot_tts.{format_hint}"
    with open(tmp_path, "wb") as f:
        f.write(audio_bytes)
    play_audio_file(tmp_path, blocking=True)


# --- Playback control ---

def stop_playback():
    """Kill all active playback (aplay subprocesses)."""
    with _playback_lock:
        for proc in _active_playback:
            if proc.poll() is None:
                try:
                    proc.terminate()
                except Exception:
                    pass
        _active_playback.clear()

    try:
        subprocess.run(["pkill", "-f", "aplay"], capture_output=True, timeout=2)
    except Exception:
        pass


def is_playing():
    with _playback_lock:
        _active_playback[:] = [p for p in _active_playback if p.poll() is None]
        return bool(_active_playback)
