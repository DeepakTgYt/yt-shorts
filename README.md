# YouTube Shorts — Job Notification & Quiz
`yt.py` makes Hindi Shorts with voiceover and motion graphics (`shorts/make.py`) and uploads them with the YouTube Data API.

- Formats: PYQ quiz (~22 s: hook + question at 0 s, A–D pop, 5→1 countdown with ticks, reveal glow + ding, व्याख्या, loop) in 5 rotating looks; vacancy (15–22 s) from our Telegram channels; breaking (1000+ posts) at once.
- Voice: edge-tts hi-IN-SwaraNeural, rate +10% (`shorts/tts.py`). Music: bank/music loops + synth bed, rotating.
- Schedule (IST): 07:30 09:00 10:30v 12:00 13:30 15:30v 17:30 19:00 20:30v. Exact time via Cloudflare Worker `yt-cron` (cloudflare/worker.js, cron */10); Actions cron is the backup. Each slot runs once (state/yt.json).
- Failure: one automatic retry, plus a GitHub issue.
- Secrets: YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN. Without them every run is a dry run (no upload, state not saved).
- Local: `python shorts/make.py quiz data.json out.mp4 --layout=2 --preview`
