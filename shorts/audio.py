"""Self-generated SFX + music bed, ducking mix, loudness normalisation. 48 kHz."""
import json
import re
import subprocess

import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfilt

SR = 48000


def _env(n, a, r):
    e = np.ones(n)
    a, r = int(a * SR), int(r * SR)
    if a:
        e[:a] = np.linspace(0, 1, a) ** 2
    if r:
        e[-r:] *= np.linspace(1, 0, r) ** 2
    return e


# ---------------------------------------------------------------- SFX
def sfx_whoosh(d=0.42, seed=1):
    rng = np.random.default_rng(seed)
    n = int(d * SR)
    noise = rng.standard_normal(n)
    out = np.zeros(n)
    # sweep a band-pass upward in 12 slices (cheap time-varying filter)
    k = 12
    edges = np.linspace(0, n, k + 1).astype(int)
    for i in range(k):
        f = 300 * (12 ** (i / (k - 1)))
        sos = butter(2, [f * 0.6, min(f * 1.8, 20000)], btype="band", fs=SR, output="sos")
        out[edges[i]:edges[i + 1]] = sosfilt(sos, noise)[edges[i]:edges[i + 1]]
    t = np.linspace(0, 1, n)
    env = np.sin(np.pi * t ** 0.7) ** 2
    out = out * env
    return 0.55 * out / (np.abs(out).max() + 1e-9)


def sfx_pop(d=0.11):
    n = int(d * SR)
    t = np.arange(n) / SR
    f = 900 * np.exp(-t * 28) + 260
    ph = 2 * np.pi * np.cumsum(f) / SR
    s = np.sin(ph) * np.exp(-t * 38)
    s[:40] *= np.linspace(0, 1, 40)
    return 0.6 * s


def sfx_tick(d=0.06):
    n = int(d * SR)
    t = np.arange(n) / SR
    s = (0.7 * np.sin(2 * np.pi * 1850 * t) + 0.4 * np.sin(2 * np.pi * 3700 * t)) * np.exp(-t * 120)
    s += 0.3 * np.sin(2 * np.pi * 620 * t) * np.exp(-t * 60)
    return 0.5 * s


def sfx_ding(d=1.4):
    n = int(d * SR)
    t = np.arange(n) / SR
    s = np.zeros(n)
    for f0, start in ((1046.5, 0.0), (1568.0, 0.09)):  # C6 then G6 = bright "correct" chime
        tt = np.clip(t - start, 0, None)
        on = (t >= start).astype(float)
        for h, a in ((1, 1.0), (2.76, 0.35), (5.4, 0.12)):
            s += on * a * np.sin(2 * np.pi * f0 * h * tt) * np.exp(-tt * (3.2 + h))
    s[:60] *= np.linspace(0, 1, 60)
    return 0.32 * s / np.abs(s).max() * 1.6


def sfx_rise(d=0.7):
    """soft riser before the end card"""
    rng = np.random.default_rng(7)
    n = int(d * SR)
    t = np.linspace(0, 1, n)
    sos = butter(2, [800, 6000], btype="band", fs=SR, output="sos")
    s = sosfilt(sos, rng.standard_normal(n)) * t ** 2
    return 0.35 * s / (np.abs(s).max() + 1e-9)


def sfx_impact(d=0.9):
    """cinematic hit for the hook: sub drop + noise burst"""
    n = int(d * SR); t = np.arange(n) / SR
    f = 95 * np.exp(-t * 5) + 38
    sub = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 4.2)
    rng = np.random.default_rng(11)
    sos = butter(2, 2500, btype="low", fs=SR, output="sos")
    nz = sosfilt(sos, rng.standard_normal(n)) * np.exp(-t * 18)
    s = sub + 0.35 * nz / (np.abs(nz).max() + 1e-9)
    s[:40] *= np.linspace(0, 1, 40)
    return 0.55 * s / (np.abs(s).max() + 1e-9)


def sfx_swoosh(d=0.35):
    """short airy swipe for cards leaving the screen"""
    rng = np.random.default_rng(5)
    n = int(d * SR); t = np.linspace(0, 1, n)
    sos = butter(2, [1500, 9000], btype="band", fs=SR, output="sos")
    s = sosfilt(sos, rng.standard_normal(n)) * np.sin(np.pi * t) ** 2
    return 0.28 * s / (np.abs(s).max() + 1e-9)


SFX = {"impact": sfx_impact, "swoosh": sfx_swoosh, "whoosh": sfx_whoosh, "pop": sfx_pop, "tick": sfx_tick, "ding": sfx_ding, "rise": sfx_rise}


# ---------------------------------------------------------------- music
def _note(midi):
    return 440.0 * 2 ** ((midi - 69) / 12)


def music_bed(dur, bpm=100, seed=3):
    """Warm pad + soft pluck arpeggio + light kick. Stereo float array (n, 2)."""
    n = int((dur + 1) * SR)
    t = np.arange(n) / SR
    beat = 60 / bpm
    bar = 4 * beat
    # C - G - Am - F  (I V vi IV)
    chords = [[48, 55, 60, 64], [43, 55, 59, 62], [45, 57, 60, 64], [41, 53, 57, 60]]
    L = np.zeros(n)
    R = np.zeros(n)
    nb = int(np.ceil(dur / bar)) + 1
    for b in range(nb):
        ch = chords[b % 4]
        s0, s1 = int(b * bar * SR), min(n, int((b + 1) * bar * SR) + int(0.3 * SR))
        if s0 >= n:
            break
        tt = t[s0:s1] - t[s0]
        env = np.minimum(1, tt / 0.6) * np.exp(-np.maximum(0, tt - bar) * 8)
        for m in ch:
            f = _note(m)
            L[s0:s1] += 0.05 * env * (np.sin(2 * np.pi * f * tt) + 0.25 * np.sin(4 * np.pi * f * tt))
            R[s0:s1] += 0.05 * env * (np.sin(2 * np.pi * f * 1.003 * tt) + 0.25 * np.sin(4 * np.pi * f * 1.003 * tt))
        # pluck arpeggio, 8th notes
        arp = [ch[1] + 12, ch[2] + 12, ch[3] + 12, ch[2] + 12]
        for k in range(8):
            p0 = s0 + int(k * beat / 2 * SR)
            pn = int(0.35 * SR)
            if p0 + pn > n:
                continue
            pt = np.arange(pn) / SR
            f = _note(arp[k % 4])
            pl = (np.sin(2 * np.pi * f * pt) + 0.3 * np.sin(4 * np.pi * f * pt)) * np.exp(-pt * 11)
            pan = 0.35 if k % 2 else 0.65
            L[p0:p0 + pn] += 0.045 * pl * (1 - pan) * 2
            R[p0:p0 + pn] += 0.045 * pl * pan * 2
        # soft kick on beats 1 and 3
        for k in (0, 2):
            p0 = s0 + int(k * beat * SR)
            pn = int(0.25 * SR)
            if p0 + pn > n:
                continue
            pt = np.arange(pn) / SR
            kf = 50 + 70 * np.exp(-pt * 30)
            kk = np.sin(2 * np.pi * np.cumsum(kf) / SR) * np.exp(-pt * 14)
            L[p0:p0 + pn] += 0.12 * kk
            R[p0:p0 + pn] += 0.12 * kk
    sos = butter(2, 7000, btype="low", fs=SR, output="sos")
    st = np.stack([sosfilt(sos, L), sosfilt(sos, R)], 1)[: int(dur * SR)]
    fade = int(1.2 * SR)
    st[-fade:] *= np.linspace(1, 0, fade)[:, None]
    st[: int(0.4 * SR)] *= np.linspace(0, 1, int(0.4 * SR))[:, None]
    return st / (np.abs(st).max() + 1e-9)


# ---------------------------------------------------------------- mix
def _db(x):
    return 10 ** (x / 20)


def music_file(path, dur):
    """Decode a music loop (any ffmpeg format), loop it to dur, stereo float in -1..1."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-stream_loop", "-1", "-i", path, "-t", str(dur + 0.5),
                          "-f", "f32le", "-ac", "2", "-ar", str(SR), "-"], capture_output=True, check=True).stdout
    st = np.frombuffer(raw, dtype=np.float32).reshape(-1, 2).astype(np.float64)[: int(dur * SR)]
    if len(st) < int(dur * SR):
        st = np.pad(st, ((0, int(dur * SR) - len(st)), (0, 0)))
    fade = int(1.0 * SR)
    st[-fade:] *= np.linspace(1, 0, fade)[:, None]
    return st / (np.abs(st).max() + 1e-9)


def mix(dur, voice_events, sfx_events, out_wav, music_db=-17.0, duck_db=-11.0, sfx_db=-6.0, music=None,
        bpm=100, seed=3):
    """voice_events: [(t, wavpath)], sfx_events: [(t, name)]. Writes stereo 48k wav."""
    n = int(dur * SR)
    voice = np.zeros(n)
    for t0, p in voice_events:
        a, sr = sf.read(p, dtype="float64")
        if a.ndim > 1:
            a = a.mean(1)
        assert sr == SR
        s = int(t0 * SR)
        if s < 0:
            a, s = a[-s:], 0
        e = min(n, s + len(a))
        voice[s:e] += a[: e - s]
    pk = np.abs(voice).max()
    if pk > 0:
        voice *= 0.89 / pk
    fx = np.zeros(n)
    cache = {}
    for t0, name in sfx_events:
        if name not in cache:
            cache[name] = SFX[name]()
        a = cache[name]
        s = int(t0 * SR)
        if s >= n:
            continue
        e = min(n, s + len(a))
        fx[s:e] += a[: e - s]
    # sidechain-style ducking envelope from the voice
    win = int(0.03 * SR)
    rms = np.sqrt(np.convolve(voice ** 2, np.ones(win) / win, mode="same"))
    active = (rms > 0.02).astype(float)
    # attack 60 ms / release 350 ms smoothing (one-pole, vectorised via lfilter)
    from scipy.signal import lfilter
    g_att = np.exp(-1 / (0.06 * SR))
    g_rel = np.exp(-1 / (0.35 * SR))
    # simple approach: dilate activity by release time, then smooth with attack
    rel = int(0.35 * SR)
    dil = np.convolve(active, np.ones(rel), mode="full")[:n] > 0
    sm = lfilter([1 - g_att], [1, -g_att], dil.astype(float))
    gain = _db(music_db) * (1 - sm + sm * _db(duck_db))
    mus = None
    if music:
        try:
            mus = music_file(music, dur)
        except Exception as e:
            print("music file failed, synth bed:", str(e)[:120])
    if mus is None:
        mus = music_bed(dur, bpm=bpm, seed=seed)
    out = mus * gain[:, None] + (voice + fx * _db(sfx_db))[:, None]
    out = np.clip(out, -1, 1)
    sf.write(out_wav, out, SR, subtype="PCM_24")
    return out_wav


def loudnorm(in_wav, out_wav, I=-14.0, TP=-1.5, LRA=11):
    """Two-pass EBU R128 normalisation with ffmpeg."""
    p = subprocess.run(["ffmpeg", "-hide_banner", "-y", "-i", in_wav, "-af",
                        f"loudnorm=I={I}:TP={TP}:LRA={LRA}:print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", p.stderr, re.S)
    st = json.loads(m.group(0))
    af = (f"loudnorm=I={I}:TP={TP}:LRA={LRA}:measured_I={st['input_i']}:measured_TP={st['input_tp']}:"
          f"measured_LRA={st['input_lra']}:measured_thresh={st['input_thresh']}:offset={st['target_offset']}"
          f":linear=true,aresample=48000")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", in_wav, "-af", af, "-ar", "48000", out_wav], check=True)
    return out_wav


def wav_duration(p):
    info = sf.info(p)
    return info.frames / info.samplerate


def speech_bounds(p, thr=0.015):
    """(start, end) seconds of audible speech inside a wav."""
    a, sr = sf.read(p, dtype="float64")
    if a.ndim > 1:
        a = a.mean(1)
    idx = np.where(np.abs(a) > thr * max(1e-9, np.abs(a).max()) * 5)[0]
    if len(idx) == 0:
        return 0.0, len(a) / sr
    return idx[0] / sr, idx[-1] / sr
