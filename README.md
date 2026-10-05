# pendant-sync

Keep using your **Limitless Pendant** after Limitless (acquired by Meta) winds down. This runs entirely on your Mac with no cloud:

```
Pendant ──BLE──▶ pendant-cli (download recordings, free them on the device)
                     │
                     ▼
               .opus ──ffmpeg──▶ wav ──whisper.cpp (large-v3-turbo)──▶ text
                                                                        │
                                      ~/notes/raw/pendant/YYYY-MM-DD.md ◀┘
                                                    │
                                   local web UI at http://localhost:8766
```

- **Local transcription** with [whisper.cpp](https://github.com/ggml-org/whisper.cpp) and OpenAI's Whisper `large-v3-turbo`. On Apple Silicon it runs on the GPU via Metal. No API keys, no minute caps.
- **Storage never fills.** Recordings are deleted from the Pendant only after they've been saved on the Mac.
- **Daily Markdown files** with one section per recording. Easy to feed into Obsidian, a wiki, or an LLM.
- **A small local UI** with transcripts by day, audio playback, search, battery and last-sync status, and a **Sync now** button. Python standard library only.

The write-up of how this came together, including what didn't work (Omi, live streaming): **[Giving my Limitless Pendant a second life](https://blog.aldrinjenson.com/posts/limitless-pendant-second-life/)**.

## How it works

The Bluetooth protocol work is done by **[MAkcanca/pendant-cli](https://github.com/MAkcanca/pendant-cli)**, a client built from analysing the official app and firmware 1.1.20. This repo doesn't include it; you install it alongside. On top of it, this repo adds the transcription pipeline, the daily notes and the UI.

Things worth knowing (tested on firmware **1.1.20**, macOS, M4 Max):

- **No live streaming.** The Pendant always records to its own flash. Its "real-time" mode does nothing on this firmware, so you record, then sync.
- **One pairing at a time.** If the Pendant was paired with your phone (the Limitless or Omi app), the Mac's commands fail with `Insufficient Encryption`. You have to factory-reset the Pendant: unplug it, **press-and-release, then press-and-hold**, keep holding through the purple light, and release when it goes out. Blue means it's ready to pair. ([Limitless guide](https://help.limitless.ai/en/articles/10547401-how-do-i-factory-reset-my-pendant))
- **macOS pairs automatically.** Bleak can't call `pair()` on CoreBluetooth, and you don't need it: the first secure request (`pendant info <address>`) triggers pairing.
- **Syncing is slow.** Bluetooth downloads run at about 10 KB/s, so a full day of recording can take an hour or more. Sync regularly.
- Don't update the Pendant's firmware through the Limitless app.

## Setup

```bash
# Tools
brew install whisper-cpp ffmpeg opus

# This repo + pendant-cli inside it
git clone https://github.com/aldrinjenson/pendant-sync.git ~/pendant-sync && cd ~/pendant-sync
git clone https://github.com/MAkcanca/pendant-cli.git
python3 -m venv .venv
./.venv/bin/pip install -e "./pendant-cli[dev,opus]"
(cd pendant-cli && ../.venv/bin/python scripts/gen_proto.py)

# Whisper model (~1.6 GB). On an older/Intel Mac, ggml-small.en.bin is lighter.
mkdir -p models && curl -L -o models/ggml-large-v3-turbo.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin

# Factory-reset the Pendant (see above), keep it near the Mac, then:
./.venv/bin/pendant scan              # prints the address
./.venv/bin/pendant info <address>    # pairs + shows battery/firmware
```

Then set `ADDRESS` at the top of `pendant_sync.py`. To keep transcripts somewhere other than `~/notes/raw/pendant`, change `WIKI_RAW` in both `pendant_sync.py` and `ui.py`.

## Use

```bash
./.venv/bin/python pendant_sync.py   # download + transcribe; safe to re-run (lock file)
./.venv/bin/python ui.py             # http://localhost:8766
```

To keep the UI always running, edit `com.example.pendant-ui.plist` (replace `YOU`), copy it to `~/Library/LaunchAgents/` and run `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.example.pendant-ui.plist`.

To sync on a schedule, make a second launchd agent that runs `pendant_sync.py` with `StartInterval` (e.g. `300`). I prefer syncing by hand.

## Files

| File | What it does |
|---|---|
| `pendant_sync.py` | Runs `pendant sync`, moves recordings to `data/audio/DATE/`, transcribes with whisper.cpp, skips blips under 3 s and Whisper's silence hallucinations, appends to daily Markdown, writes `status.json` |
| `ui.py` | Stdlib HTTP server: transcript/day/search APIs, audio files, and `POST /api/sync` |
| `ui.html` | The browser UI (light/dark, works on mobile widths) |
| `com.example.pendant-ui.plist` | launchd agent template for the UI |

## Privacy

This is an always-on microphone. Everything stays on your machine. I use it for my own rambling at home; if you wear it around people, tell them.

MIT licensed.
