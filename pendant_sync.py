#!/usr/bin/env python3
"""Pendant → Mac → Whisper → MemoryPalace pipeline.

Each run: connect to the Limitless Pendant over BLE (pendant-cli), drain its
flash (pendant-cli ACK-deletes pages after download, so the pendant never
fills), transcribe new recordings locally with whisper.cpp, append them to
~/notes/raw/pendant/YYYY-MM-DD.md, and update status.json
(read by the local UI, ui.py).

Run on demand (by hand, from a Claude Code command, or the UI's Sync now
button in ui.py): `.venv/bin/python pendant_sync.py`.
"""
import datetime as dt
import fcntl
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

HOME = Path(__file__).resolve().parent
ADDRESS = "YOUR-PENDANT-ADDRESS"  # from `pendant scan` (on macOS this is a per-host UUID)
PENDANT = HOME / ".venv/bin/pendant"
MODEL = HOME / "models/ggml-large-v3-turbo.bin"
WHISPER = shutil.which("whisper-cli") or "/opt/homebrew/bin/whisper-cli"
FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
WIKI_RAW = Path.home() / "notes/raw/pendant"  # where daily transcript files go
AUDIO = HOME / "data/audio"
RUNS = HOME / "data/runs"
STATUS_JSON = HOME / "status.json"
LOG = HOME / "logs/sync.log"

MIN_SECONDS = 3.0          # skip blips shorter than this
SYNC_TIMEOUT_S = 4 * 3600  # BLE drains ~10 KB/s, so a multi-day backlog can take hours
KEEP_RUNS_DAYS = 14        # wire-fidelity .bin captures kept this long
# Whisper's stock outputs for silence/noise; never worth writing to the wiki.
HALLUCINATIONS = {"", "you", "thank you", "thank you.", "thanks for watching!", "bye.", "okay.", "[blank_audio]", "(silence)"}


def log(msg):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


def load_status():
    try:
        return json.loads(STATUS_JSON.read_text())
    except Exception:
        return {"recent": [], "days": {}}


def save_status(st):
    STATUS_JSON.write_text(json.dumps(st, indent=1))


def recording_start(rec):
    """Pendant clock is unset for the first chunk after a reset (epoch ~0), so
    fall back to last_chunk minus duration when first_chunk looks bogus."""
    first = rec.get("first_chunk", {}).get("absolute_timestamp_ms") or 0
    if first > 1_600_000_000_000:
        return dt.datetime.fromtimestamp(first / 1000)
    last = rec.get("last_chunk", {}).get("absolute_timestamp_ms") or 0
    if last > 1_600_000_000_000:
        return dt.datetime.fromtimestamp((last - rec.get("approx_duration_ms", 0)) / 1000)
    return dt.datetime.now()


def transcribe(opus: Path) -> str:
    wav = opus.with_suffix(".wav")
    # The last Ogg packet of a drained recording is often truncated; ffmpeg
    # complains but still writes everything before it.
    subprocess.run([FFMPEG, "-loglevel", "quiet", "-y", "-i", str(opus), "-ar", "16000", "-ac", "1", str(wav)])
    if not wav.exists() or wav.stat().st_size < 16000:
        return ""
    out = subprocess.run([WHISPER, "-m", str(MODEL), "-f", str(wav), "-l", "en", "-nt", "-np"],
                         capture_output=True, text=True, timeout=600)
    wav.unlink(missing_ok=True)
    return " ".join(out.stdout.split()).strip()


def append_to_wiki(start: dt.datetime, seconds: float, text: str, audio_rel: str):
    WIKI_RAW.mkdir(parents=True, exist_ok=True)
    day = WIKI_RAW / f"{start:%Y-%m-%d}.md"
    if not day.exists():
        day.write_text(
            "---\n"
            f"date: {start:%Y-%m-%d}\n"
            "source: limitless-pendant (local sync via pendant-cli + whisper.cpp)\n"
            "processed: false\n"
            "---\n\n"
            f"# Pendant transcripts — {start:%A, %B %-d %Y}\n\n"
            "Raw ambient/rambling transcripts from the Limitless Pendant. Whisper output: expect "
            "misheard words. Audio kept locally in `pendant-sync/data/audio/`.\n"
        )
    with day.open("a") as f:
        f.write(f"\n## {start:%H:%M} ({seconds:.0f}s)\n\n{text}\n\n<!-- audio: {audio_rel} -->\n")


def cleanup_runs():
    cutoff = time.time() - KEEP_RUNS_DAYS * 86400
    for d in RUNS.glob("*"):
        if d.is_dir() and d.stat().st_mtime < cutoff:
            shutil.rmtree(d, ignore_errors=True)


def run():
    st = load_status()
    st["last_attempt"] = dt.datetime.now().isoformat(timespec="seconds")
    run_dir = RUNS / dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    base = run_dir / "cap.opus"

    try:
        proc = subprocess.run([str(PENDANT), "sync", ADDRESS, "-o", str(base)],
                              capture_output=True, text=True, timeout=SYNC_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        st["last_error"] = "sync timed out"
        log("sync timed out")
        save_status(st)
        return
    manifest_path = run_dir / "cap.opus.manifest.json"
    if proc.returncode != 0 or not manifest_path.exists():
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-1:] or ["unknown"]
        st["last_error"] = f"not reachable / sync failed: {tail[0][:200]}"
        st["connected"] = False
        log(f"sync failed rc={proc.returncode}: {tail[0][:200]}")
        shutil.rmtree(run_dir, ignore_errors=True)
        save_status(st)
        return

    manifest = json.loads(manifest_path.read_text())
    dev = manifest.get("device", {})
    st.update(connected=True, last_error=None, last_success=st["last_attempt"],
              battery=dev.get("battery_percent"), firmware=dev.get("firmware_ver"))

    recs = manifest.get("recordings", [])
    kept = 0
    for rec in recs:
        seconds = rec.get("approx_duration_ms", 0) / 1000
        src = run_dir / rec["file"]
        if seconds < MIN_SECONDS or not src.exists():
            continue
        start = recording_start(rec)
        dest_dir = AUDIO / f"{start:%Y-%m-%d}"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{start:%H%M%S}.opus"
        shutil.move(str(src), dest)
        text = transcribe(dest)
        day = st["days"].setdefault(f"{start:%Y-%m-%d}", {"recordings": 0, "seconds": 0, "transcribed": 0})
        day["recordings"] += 1
        day["seconds"] += round(seconds)
        if text.lower() in HALLUCINATIONS:
            log(f"{dest.name}: {seconds:.0f}s, no speech")
            continue
        append_to_wiki(start, seconds, text, str(dest.relative_to(HOME)))
        day["transcribed"] += 1
        kept += 1
        st["recent"] = ([{"at": start.isoformat(timespec="minutes"), "seconds": round(seconds), "text": text[:400]}]
                        + st["recent"])[:15]
        log(f"{dest.name}: {seconds:.0f}s -> {len(text)} chars")

    if not recs:
        shutil.rmtree(run_dir, ignore_errors=True)  # nothing captured; don't keep empty runs
    log(f"sync ok: battery={st['battery']}% recordings={len(recs)} transcribed={kept}")
    cleanup_runs()
    save_status(st)


if __name__ == "__main__":
    lock = open(HOME / ".sync.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(0)  # previous run still draining
    run()
