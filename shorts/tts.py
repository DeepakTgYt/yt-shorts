"""Hindi TTS backends with an on-disk cache.

Engines
  omnivoice : VoiceStudio's bundled OmniVoice model (k2-fsa/OmniVoice), run on CPU
              in its own venv via omnivoice_runner.py. Natural, but ~20x slower
              than real time on 2 CPU cores, so results are cached per text.
  edge      : edge-tts (hi-IN-SwaraNeural / hi-IN-MadhurNeural). Fast (~1 s/line),
              needs internet. Default.

Every synthesised line is cached under assets/tts_cache/<engine>/<sha1>.wav, so a
re-render with the same narration costs no TTS time.
"""
import asyncio
import hashlib
import json
import os
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "assets", "tts_cache")
OMNI_PY = os.environ.get("OMNIVOICE_PY", "/tmp/vs/venv/bin/python")
OMNI_MODEL = os.environ.get("OMNIVOICE_MODEL", "/tmp/vs/model")
OMNI_INSTRUCT = os.environ.get("OMNIVOICE_INSTRUCT", "female, young adult")
OMNI_STEPS = os.environ.get("OMNIVOICE_STEPS", "16")
# one fixed reference voice (cloned for every line, so the speaker never changes mid-video)
OMNI_REF = os.environ.get("OMNIVOICE_REF", os.path.join(HERE, "assets", "voice_ref.wav"))

EDGE_RATE = os.environ.get("EDGE_RATE", "+4%")
HUMAN = os.environ.get("TTS_HUMAN", "1") == "1"
# warm, slightly roomy voice chain: no hiss, soft highs, gentle glue, tiny room
VOICE_FX = ("highpass=f=70,equalizer=f=180:t=q:w=1.0:g=2.5,equalizer=f=3200:t=q:w=1.2:g=1.5,"
            "highshelf=f=7500:g=-3,acompressor=threshold=-20dB:ratio=2.5:attack=8:release=120:makeup=2,"
            "aecho=0.8:0.85:28|47:0.12|0.07")
EDGE_VOICES = {"female": "hi-IN-SwaraNeural", "male": "hi-IN-MadhurNeural"}


def _key(engine, voice, text):
    return hashlib.sha1(f"{engine}|{voice}|{text}".encode()).hexdigest()[:16]


def _to_wav48(src, dst):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-ac", "1", "-ar", "48000",
                    "-c:a", "pcm_s16le", dst], check=True)


import re as _re
import random as _random

_SENT = _re.compile(r"(?<=[।?!])\s+|(?<=\.)\s+(?=[^\d])")


def _sentences(text):
    parts = [p.strip() for p in _SENT.split(text.strip()) if p.strip()]
    return parts or [text]


def _join_human(wavs, out, seed):
    """Trim each sentence, soft 12 ms edges, natural pauses between sentences, warm room chain."""
    rnd = _random.Random(seed)
    with tempfile.TemporaryDirectory() as td:
        segs = []
        for i, w in enumerate(wavs):
            t = os.path.join(td, f"s{i}.wav")
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", w, "-af",
                            "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.03,"
                            "areverse,silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.06,areverse,"
                            "afade=t=in:d=0.012,areverse,afade=t=in:d=0.025,areverse",
                            "-ar", "48000", "-ac", "1", t], check=True)
            segs.append(t)
            if i < len(wavs) - 1:
                gap = os.path.join(td, f"g{i}.wav")
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                                "anullsrc=r=48000:cl=mono", "-t", f"{rnd.uniform(0.30, 0.42):.3f}", gap], check=True)
                segs.append(gap)
        lst = os.path.join(td, "l.txt")
        open(lst, "w").write("".join(f"file '{x}'\n" for x in segs))
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst,
                        "-af", VOICE_FX, "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", out], check=True)


def _edge(lines, voice, outs):
    import edge_proxyfix
    edge_proxyfix.apply()
    import edge_tts
    v = EDGE_VOICES.get(voice, voice)

    async def one(text, out):
        tmp = out + ".mp3"
        for attempt in range(3):
            try:
                await edge_tts.Communicate(text, v, rate=EDGE_RATE).save(tmp)
                break
            except Exception:
                if attempt == 2:
                    raise
                await asyncio.sleep(1.5)
        _to_wav48(tmp, out)
        os.remove(tmp)

    async def run():
        for t, o in zip(lines, outs):
            if not HUMAN:
                await one(t, o); continue
            parts = _sentences(t)
            tmps = [o + f".p{i}.wav" for i in range(len(parts))]
            for pt, tp in zip(parts, tmps):
                await one(pt, tp)
            _join_human(tmps, o, seed=hash(t) & 0xffff)
            for tp in tmps:
                os.remove(tp)
    asyncio.run(run())


def _omnivoice(lines, voice, outs):
    if not (os.path.exists(OMNI_PY) and os.path.isdir(OMNI_MODEL)):
        raise RuntimeError("OmniVoice venv/model not found (set OMNIVOICE_PY / OMNIVOICE_MODEL)")
    instruct = OMNI_INSTRUCT if voice == "female" else OMNI_INSTRUCT.replace("female", "male")
    with tempfile.TemporaryDirectory() as td:
        from hindi_num import speak_numbers  # OmniVoice garbles digits ("1993", "182वाँ")
        jobs = [{"text": speak_numbers(t), "out": os.path.join(td, f"{i}.wav")} for i, t in enumerate(lines)]
        jp = os.path.join(td, "jobs.json")
        json.dump(jobs, open(jp, "w"), ensure_ascii=False)
        subprocess.run([OMNI_PY, os.path.join(HERE, "omnivoice_runner.py"), OMNI_MODEL, jp,
                        instruct, OMNI_STEPS, OMNI_REF], check=True)
        for j, o in zip(jobs, outs):
            _to_wav48(j["out"], o)


def synth(lines, engine="edge", voice="female"):
    """Return list of 48 kHz mono wav paths, one per line (cached).
    TTS_ENGINE=omnivoice switches every voice to VoiceStudio's OmniVoice; falls back to Edge if it fails."""
    want = os.environ.get("TTS_ENGINE", engine)
    if want == "omnivoice" and engine != "omnivoice":
        try:
            return synth(lines, "omnivoice", voice)
        except Exception as e:
            print("omnivoice failed, using edge:", e, flush=True)
    d = os.path.join(CACHE, engine)
    os.makedirs(d, exist_ok=True)
    tag = voice + (EDGE_RATE + ("|h2" if HUMAN else "") if engine == "edge" else f"{OMNI_INSTRUCT}|{OMNI_STEPS}|{os.path.exists(OMNI_REF)}")
    outs = [os.path.join(d, _key(engine, tag, t) + ".wav") for t in lines]
    todo = [(t, o) for t, o in zip(lines, outs) if not os.path.exists(o)]
    if todo:
        fn = {"edge": _edge, "omnivoice": _omnivoice}[engine]
        fn([t for t, _ in todo], voice, [o for _, o in todo])
    return outs
