#!/usr/bin/env python3
"""YouTube Shorts maker (Hindi, 1080x1920, voiceover + motion graphics).

    python make.py quiz|vacancy data.json out.mp4 [--layout N] [--preview] [--stills-only]

Writes out.mp4, out_thumb.jpg (custom thumbnail) and with --preview some PNG stills.
All key text stays inside the YouTube Shorts safe box: x 60-900, y 260-1350.
"""
import json
import os
import re
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from PIL import Image, ImageDraw, ImageFilter  # noqa: E402

import audio  # noqa: E402
import tts  # noqa: E402
from render import (W, H, FPS, El, Background, card, hexc, hstack, icon, measure, render_still,  # noqa: E402
                    render_video, rrect, text_img, with_shadow, ease_out_cubic, clamp01)

X0, X1, Y0, Y1 = 60, 900, 262, 1350
CW = X1 - X0  # 840
INK, MUTED, TG_BLUE, GREEN, RED = "#14172E", "#5B6075", "#229ED9", "#16A34A", "#DC2626"
MUSIC_DIR = os.path.join(os.path.dirname(HERE), "bank", "music")

CHANNELS = {
    "ssc": {"handle": "SSC_Job_Alert", "accent": "#FBBF24", "bg": ("#1E1B4B", "#3B2A8C", "#6D28D9")},
    "railway": {"handle": "Railway_Job_Notification_Quiz", "accent": "#FB923C", "bg": ("#0B1E3F", "#0E4D6E", "#0F766E")},
    "bihar": {"handle": "Bihar_Job_Notification_Quiz", "accent": "#FDE047", "bg": ("#3B0A1E", "#7A1631", "#B45309")},
    "army": {"handle": "Army_Agniveer_Bharti_Alert", "accent": "#FACC15", "bg": ("#0F1F14", "#1F3D22", "#4D5B1E")},
}
# 5 looks that rotate, so no two neighbouring Shorts look alike
LAYOUTS = [
    {"name": "classic", "card": "#FFFFFF", "ink": INK, "opt": "list", "timer": "ring", "bg": None},
    {"name": "neon", "card": (12, 14, 32, 215), "ink": "#FFFFFF", "opt": "grid", "timer": "bar",
     "bg": ("#05060F", "#111436", "#1D1147")},
    {"name": "paper", "card": "#FFF8E7", "ink": "#2B1D0E", "opt": "list", "timer": "big",
     "bg": ("#3A2A12", "#6B4A1B", "#A0522D")},
    {"name": "bold", "card": "#FFFFFF", "ink": INK, "opt": "grid", "timer": "ring", "accent_q": True,
     "bg": ("#7F1D1D", "#B91C1C", "#EA580C")},
    {"name": "ocean", "card": (255, 255, 255, 236), "ink": INK, "opt": "list", "timer": "bar",
     "bg": ("#042F2E", "#0E7490", "#1D4ED8")},
]
MONTHS = ["जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर", "अक्टूबर", "नवंबर", "दिसंबर"]
FAST_LINE = "नोटिफिकेशन आते ही 5–8 मिनट में Telegram पर"


# ---------------------------------------------------------------- timeline (voice drives timing)
class Timeline:
    def __init__(self):
        self.beats = []

    def beat(self, name, say=None, cap=None, min_dur=0.0, gap=0.18, fixed=None):
        self.beats.append(dict(name=name, say=say, cap=cap if cap is not None else say, min_dur=min_dur,
                               gap=gap, fixed=fixed))

    def resolve(self, voice="female"):
        lines = [b["say"] for b in self.beats if b["say"]]
        try:
            wavs = iter(tts.synth(lines, "edge", voice)) if lines else iter([])
        except Exception as e:  # never block a Short on the voice service: fall back to text-timed beats
            print("TTS failed, music only:", str(e)[:200])
            wavs = None
        t = 0.0
        for b in self.beats:
            b["t"] = t
            if b["say"] and wavs is not None:
                b["wav"] = next(wavs)
                s0, s1 = audio.speech_bounds(b["wav"])
                s0 = max(0.0, s0 - 0.03)
                s1 = min(audio.wav_duration(b["wav"]), s1 + 0.08)
                b["offset"] = s0
                b["speech"] = (t, t + s1 - s0)
                b["dur"] = max(s1 - s0 + b["gap"], b["min_dur"])
            elif b["say"]:
                d = max(1.2, len(b["say"]) / 14.0)
                b["speech"] = (t, t + d)
                b["dur"] = max(d + b["gap"], b["min_dur"])
            else:
                b["dur"] = b["fixed"] or b["min_dur"]
            t += b["dur"]
        self.total = t
        return self

    def __getitem__(self, name):
        for b in self.beats:
            if b["name"] == name:
                return b
        raise KeyError(name)


# ---------------------------------------------------------------- shared pieces
def glow(im, color, blur=26, grow=18):
    """Coloured halo behind a sprite (used for the answer reveal)."""
    w, h = im.size
    out = Image.new("RGBA", (w + 2 * grow, h + 2 * grow), (0, 0, 0, 0))
    a = im.getchannel("A").resize((w + grow, h + grow))
    halo = Image.new("RGBA", a.size, hexc(color))
    halo.putalpha(a)
    out.alpha_composite(halo, (grow // 2, grow // 2))
    out = out.filter(ImageFilter.GaussianBlur(blur))
    out.alpha_composite(im, (grow, grow))
    out.info["pad"] = im.info.get("pad", 0) + grow
    return out


def sub_c(ink):
    """Secondary text colour that reads on the layout's card."""
    return "#C7CAD6" if ink == "#FFFFFF" else "#6B6255" if ink != INK else MUTED


def badge(text, bg, fg=INK, size=40):
    t = text_img(text, "black", size, fg, CW)
    b = rrect(t.size[0] + 44, t.size[1] + 16, (t.size[1] + 16) // 2, hexc(bg) if isinstance(bg, str) else bg)
    b.alpha_composite(t, (22, 8))
    return b


def handle_pill(ch):
    pl = icon("plane", 46, "#FFFFFF", bg=TG_BLUE)
    tx = text_img("@" + ch["handle"], "latin", 26, "#FFFFFF", 700)
    row = hstack([pl, tx], gap=12)
    bg = rrect(row.size[0] + 32, row.size[1] + 16, (row.size[1] + 16) // 2, hexc("#000000", 90))
    bg.alpha_composite(row, (16, 8))
    return bg


def progress(total, acc):
    return lambda t: None  # user: no duration bar at the top
    track = rrect(CW, 10, 5, hexc("#FFFFFF", 50))
    cache = {}

    def f(t):
        w = int(CW * min(1, t / total)) // 6 * 6
        if w not in cache:
            im = track.copy()
            if w > 10:
                im.alpha_composite(rrect(w, 10, 5, hexc(acc)), (0, 0))
            im.info["pad"] = 0
            cache[w] = im
        return cache[w]
    return f


CAP_Y = 1492  # bottom-most safe line (below the handle pill, above YouTube's title/channel overlay)


def captions(tl, skip=(), y=CAP_Y, hi="#FFD60A"):
    """Spoken words as bold bottom subtitles, karaoke style: the word being spoken lights up and pops."""
    els = []
    for b in tl.beats:
        if not b.get("cap") or "speech" not in b or b["name"] in skip:
            continue
        words, chunks, cur = b["cap"].split(), [], []
        for w in words:
            if cur and measure(" ".join(cur + [w]), "bold", 46) > 640:
                chunks.append(cur); cur = [w]
            else:
                cur.append(w)
        if cur:
            chunks.append(cur)
        s0, s1 = b["speech"]
        tot = sum(len(w) + 1 for c in chunks for w in c) or 1
        t = s0
        for c in chunks:
            d = (s1 - s0) * sum(len(w) + 1 for w in c) / tot
            imgs_w = [text_img(w, "bold", 46, "#FFFFFF", CW, stroke=3, stroke_fill=(0, 0, 0, 170)) for w in c]
            imgs_h = [text_img(w, "bold", 46, hi, CW, stroke=3, stroke_fill=(0, 0, 0, 170)) for w in c]

            def frame_for(k, iw=imgs_w, ih=imgs_h):
                row = hstack([ih[j] if j == k else iw[j] for j in range(len(iw))], gap=11)
                box = rrect(row.size[0] + 44, row.size[1] + 20, 24, hexc("#0A0C1A", 185))
                box.alpha_composite(row, (22, 10))
                box.info["pad"] = 0
                return box
            frames = [frame_for(k) for k in range(len(c))]
            wt, acc_t = [], t
            for w in c:
                wt.append(acc_t); acc_t += d * (len(w) + 1) / sum(len(x) + 1 for x in c)
            el = El(frames[0], (X0 + X1) // 2, y, t - 0.04, t + d - 0.04, anim="pop", dur=0.14, out="fade",
                    out_dur=0.05, anchor_center=True, variants=list(zip(wt[1:], frames[1:])))
            els.append(el)
            t += d
    return els


CTA_STYLE = int(os.environ.get("CTA_STYLE", "-1"))


def _chips(names, fg, bg, size=30):
    out = []
    for n in names:
        t = text_img(n, "black", size, fg, 400)
        c = rrect(t.size[0] + 30, t.size[1] + 12, (t.size[1] + 12) // 2, hexc(bg))
        c.alpha_composite(t, (15, 6)); out.append(c)
    return hstack(out, gap=10)


def cta_card(ch, acc, line2):
    """End card: always about all four channels (the QR opens the hub), never one handle. Rotates styles."""
    import random
    names = ["SSC", "Railway", "Bihar", "Army"]
    st = CTA_STYLE if CTA_STYLE >= 0 else random.randrange(4)
    if st == 0:  # Telegram blue
        pl = icon("plane", 92, "#FFFFFF", bg=TG_BLUE)
        t1 = text_img("Telegram पर जुड़ें", "black", 58, "#FFFFFF", 620)
        t2 = text_img(line2, "bold", 34, hexc("#FFFFFF", 235), 620)
        col = Image.new("RGBA", (max(t1.size[0], t2.size[0]), t1.size[1] + t2.size[1] - 6), (0, 0, 0, 0))
        col.alpha_composite(t1, (0, 0)); col.alpha_composite(t2, (0, t1.size[1] - 6))
        return card([(hstack([pl, col], gap=22), "left"), (_chips(names, INK, acc), "center")],
                    CW, bg=TG_BLUE, pad=(34, 26), gap=18)
    if st == 1:  # bold yellow
        t1 = text_img("चारों Telegram चैनल", "black", 60, "#111111", CW - 80, align="center")
        t2 = text_img("हर नोटिफिकेशन 5–8 मिनट में", "bold", 36, "#222222", CW - 80, align="center")
        return card([(t1, "center"), (t2, "center"), (_chips(names, "#FFFFFF", "#111111"), "center")],
                    CW, bg="#FFC928", pad=(34, 26), gap=14)
    if st == 2:  # white with blue accents
        t0 = text_img("सबसे पहले अपडेट चाहिए?", "black", 40, TG_BLUE, CW - 80, align="center")
        t1 = text_img("Telegram पर जुड़ें", "black", 62, "#111111", CW - 80, align="center")
        t2 = text_img(line2, "bold", 32, "#444444", CW - 80, align="center")
        return card([(t0, "center"), (t1, "center"), (t2, "center"), (_chips(names, "#FFFFFF", TG_BLUE), "center")],
                    CW, bg="#FFFFFF", pad=(34, 24), gap=10)
    # 3: dark glass
    pl = icon("plane", 80, "#FFFFFF", bg=TG_BLUE)
    t1 = text_img("SSC • Railway • Bihar • Army", "black", 44, "#FFFFFF", 640)
    t2 = text_img("सभी चैनल एक जगह, स्कैन करके जुड़ें", "bold", 32, hexc("#FFFFFF", 225), 640)
    col = Image.new("RGBA", (max(t1.size[0], t2.size[0]), t1.size[1] + t2.size[1]), (0, 0, 0, 0))
    col.alpha_composite(t1, (0, 0)); col.alpha_composite(t2, (0, t1.size[1]))
    return card([(hstack([pl, col], gap=22), "left")], CW, bg=hexc("#0E1726", 235), pad=(30, 26), gap=12)


TG_HUB = "https://tg.jobnotificationquiz.workers.dev/tg"


def qr_card(width=300):
    """The user's own Telegram QR card (opens the hub with all four channels). Same image on every Short."""
    q = Image.open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "tg_qr.png")).convert("RGBA")
    q = q.resize((width, round(q.size[1] * width / q.size[0])), Image.LANCZOS)
    q.info["pad"] = 0
    return q


def end_panel(qw=470, head=True):
    """Clean end screen: headline + the user's QR with a soft glow. Nothing else."""
    q = qr_card(qw)
    parts = []
    if head:
        parts.append(text_img("पूरी जानकारी", "black", 84, "#FFC928", CW, align="center", stroke=3, stroke_fill=(0, 0, 0, 150)))
        parts.append(text_img("Telegram पर", "black", 76, "#FFFFFF", CW, align="center", stroke=3, stroke_fill=(0, 0, 0, 150)))
    g = 36
    w = max([q.size[0] + 2 * g] + [p.size[0] for p in parts])
    hh = sum(p.size[1] for p in parts) + (24 if parts else 0)
    out = Image.new("RGBA", (w, hh + q.size[1] + 2 * g), (0, 0, 0, 0))
    y = 0
    for p in parts:
        out.alpha_composite(p, ((w - p.size[0]) // 2, y)); y += p.size[1]
    y += 24 if parts else 0
    glow = Image.new("RGBA", (q.size[0] + 2 * g, q.size[1] + 2 * g), (0, 0, 0, 0))
    ImageDraw.Draw(glow).rounded_rectangle((g - 6, g - 6, g + q.size[0] + 6, g + q.size[1] + 6), radius=60,
                                           fill=(255, 255, 255, 120))
    glow = glow.filter(ImageFilter.GaussianBlur(18))
    out.alpha_composite(glow, ((w - glow.size[0]) // 2, y))
    out.alpha_composite(q, ((w - q.size[0]) // 2, y + g))
    out.info["pad"] = 0
    out.info["qbox"] = ((w - q.size[0]) // 2, y + g, q.size[0], q.size[1])
    return out


def shine_fx(card_im, period=1.8, width=110):
    """A diagonal light sweep across a card (motion-graphics highlight), clipped to the card shape."""
    import numpy as np
    w, h = card_im.size
    base = np.array(card_im).astype(np.float32)
    mask = base[..., 3] / 255.0
    yy, xx = np.mgrid[0:h, 0:w]
    diag = xx + 0.35 * yy
    cache = {}

    def f(t):
        k = int(((t % period) / period) * 36)
        if k not in cache:
            c = -width + (w + 0.35 * h + 2 * width) * min(k, 24) / 24
            g = np.clip(1 - np.abs(diag - c) / (width / 2), 0, 1) * 0.45 * mask if k <= 24 else 0 * mask
            out = base.copy()
            out[..., :3] = out[..., :3] + (255 - out[..., :3]) * g[..., None]
            im = Image.fromarray(out.clip(0, 255).astype(np.uint8), "RGBA")
            im.info["pad"] = card_im.info.get("pad", 0)
            cache[k] = im
        return cache[k]
    return f


def scan_fx(panel, period=1.6):
    """Motion: a glowing scan line sweeps the QR again and again (cached frames)."""
    qx, qy, qw, qh = panel.info["qbox"]
    top = qy + int(qh * 0.17)  # below the blue strip
    span = qy + qh - int(qh * 0.06) - top
    bar = Image.new("RGBA", (qw - 40, 26), (0, 0, 0, 0))
    ImageDraw.Draw(bar).rounded_rectangle((0, 9, bar.size[0] - 1, 16), radius=4, fill=(0, 190, 255, 230))
    bar = Image.alpha_composite(bar.filter(ImageFilter.GaussianBlur(5)), bar)
    cache = {}

    def f(t):
        k = int(((t % period) / period) * 40)
        if k not in cache:
            im = panel.copy()
            yy = top + int(span * (0.5 - 0.5 * __import__("math").cos(k / 40 * 2 * __import__("math").pi)))
            im.alpha_composite(bar, (qx + 20, yy - 13))
            im.info.update(panel.info)
            cache[k] = im
        return cache[k]
    return f


def human_date(s):
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})$", s or "")
    if m:
        return f"{int(m.group(3))} {MONTHS[int(m.group(2)) - 1]} {m.group(1)}"
    return s


# ---------------------------------------------------------------- QUIZ (~20-26 s)
LSAY = ["ए", "बी", "सी", "डी"]
LET = ["A", "B", "C", "D"]


def build_quiz(d):
    tl = Timeline()
    ans = d["answer"]
    osay = d.get("options_say") or d["options"]
    tl.beat("hook", d["hook_say"], d["hook"], gap=0.1)
    tl.beat("q", d.get("question_say", d["question"]), d["question"], gap=0.1)
    tl.beat("opts", None, fixed=1.5)  # A-D pop on the beat (on screen, not read out: keeps it ~20 s)
    tl.beat("count", None, fixed=5.0)
    tl.beat("reveal", f"सही जवाब है {LSAY[ans]}, {osay[ans]}!", f"सही जवाब: {LET[ans]}) {d['options'][ans]}", gap=0.15)
    tl.beat("expl", d.get("explanation_say", d["explanation"]), d["explanation"], gap=0.2)
    tl.beat("end", d["cta_say"], d["cta_cap"], min_dur=2.6, gap=0.6)
    tl.beat("loop", None, fixed=0.6)
    return tl


def layout_quiz(d, ch, tl, L):
    acc = ch["accent"]
    cbg, ink = L["card"], L["ink"]
    ans = d["answer"]
    t_rev, t_exp, t_end, t_loop = tl["reveal"]["t"], tl["expl"]["t"], tl["end"]["t"], tl["loop"]["t"]
    t_c = tl["count"]["t"]
    els, sfx = [], []
    # top strip: progress + handle (frame 0 complete)
    els.append(El(None, X0, 262, 0.0, anim="none", dur=0.01, dyn=progress(tl.total, acc)))
    hp = handle_pill(ch)
    els.append(El(hp, X0, 1372, 0.0, t_end - 0.1, anim="none", dur=0.01))
    # exam badge + hook (frame 0 visible, punch + shake on the badge)
    eb = badge(d["badge"], acc, size=38)
    els.append(El(eb, X0, 290, 0.0, t_end - 0.1, anim="pop", dur=0.3, still0=True, shake=[(0.35, 0.22, 7)]))
    hk = text_img(d["hook"], "black", 62, "#FFFFFF", CW - 175, stroke=2, stroke_fill=(0, 0, 0, 120))
    hk_y = 290 + eb.size[1] + 14
    if L["timer"] == "bar":  # the timer bar takes the hook's place during the countdown
        els.append(El(hk, X0, hk_y, 0.0, t_c - 0.05, anim="pop", dur=0.3, still0=True, out_dur=0.1))
        els.append(El(hk, X0, hk_y, t_rev + 0.2, t_end - 0.1, anim="pop", dur=0.3))
    else:
        els.append(El(hk, X0, hk_y, 0.0, t_end - 0.1, anim="pop", dur=0.3, still0=True))
    sfx.append((0.02, "whoosh"))
    y = 290 + eb.size[1] + 14 + hk.size[1] + 22
    # question card
    qbg = acc if L.get("accent_q") else cbg
    qsize = 54 if len(d["question"]) < 90 else (48 if len(d["question"]) < 140 else 42)
    qt = text_img(d["question"], "bold", qsize, ink if not L.get("accent_q") else INK, CW - 70)
    qc = card([(qt, "left")], CW, bg=qbg, pad=(35, 28))
    q_y = y
    els.append(El(qc, X0, q_y, 0.0, t_exp - 0.1, anim="pop", dur=0.3, still0=True))
    els.append(El(qc, X0, q_y, t_loop, anim="pop", dur=0.3))  # loop: question comes back as on frame 0
    y = q_y + qc.size[1] - 2 * qc.info["pad"] + 22
    grid = L["opt"] == "grid" and max(len(o) for o in d["options"]) <= 16
    ow = (CW - 18) // 2 if grid else CW
    oh = 112 if grid else 98
    room = (Y1 - 100) - y  # keep the caption / comment-banner line free
    need = (2 * oh + 18) if grid else (4 * oh + 3 * 14)
    if need > room:
        oh = max(76, int(oh * room / need))

    def opt(i, state):
        bgc = {"normal": cbg, "right": GREEN, "wrong": (120, 120, 130, 120)}[state]
        tc = {"normal": ink, "right": "#FFFFFF", "wrong": (230, 230, 235, 170)}[state]
        base = rrect(ow, oh, 26, hexc(bgc) if isinstance(bgc, str) else bgc,
                     outline=hexc(acc) if (state == "normal" and L["name"] == "neon") else None,
                     width=3 if L["name"] == "neon" else 0)
        cs = min(64, oh - 24)
        circ = rrect(cs, cs, cs // 2, hexc(acc if state == "normal" else ("#FFFFFF" if state == "right" else "#9CA3AF")))
        lt = text_img(LET[i], "latin", int(cs * 0.5), INK if state != "right" else GREEN, cs, align="center")
        circ.alpha_composite(lt, ((cs - lt.size[0]) // 2, (cs - lt.size[1]) // 2 + 2))
        base.alpha_composite(circ, (16, (oh - cs) // 2))
        fs = 42 if len(d["options"][i]) < 22 else 36
        tx = text_img(d["options"][i], "bold", fs, tc, ow - cs - 46 - (60 if state == "right" else 0))
        base.alpha_composite(tx, (cs + 30, (oh - tx.size[1]) // 2))
        if state == "right":
            ck = icon("check", 54, GREEN, bg="#FFFFFF")
            base.alpha_composite(ck, (ow - 70, (oh - 54) // 2))
        sp = with_shadow(base, blur=12, alpha=70)
        return glow(sp, "#4ADE80", blur=22, grow=20) if state == "right" else sp

    for i in range(4):
        if grid:
            ox, oy = X0 + (i % 2) * (ow + 18), y + (i // 2) * (oh + 18)
        else:
            ox, oy = X0, y + i * (oh + 14)
        t_o = tl["opts"]["t"] - 0.5 + i * 0.4
        right = opt(i, "right" if i == ans else "wrong")
        el = El(opt(i, "normal"), ox, oy, t_o, t_exp - 0.1, anim="pop", dur=0.26, variants=[(t_rev, right)],
                shake=[(t_rev, 0.25, 6)] if i == ans else None)
        els.append(el)
        sfx.append((t_o, "pop"))
    # countdown 5 -> 1
    if L["timer"] == "ring":
        rings = {}

        def ring(tl_):
            if tl_ > 5.0:
                return None
            rem = max(0.0, 5.0 - tl_)
            n = max(1, int(rem) + (0 if rem == int(rem) else 1))
            key = (n, int(rem * 20))
            if key not in rings:
                s = 150 * 3
                im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
                dd = ImageDraw.Draw(im)
                dd.ellipse([0, 0, s - 1, s - 1], fill=hexc("#FFFFFF"))
                dd.arc([20, 20, s - 21, s - 21], 0, 360, fill=hexc("#E5E7EB"), width=32)
                col = hexc(RED if n <= 2 else (acc if n == 3 else GREEN))
                dd.arc([20, 20, s - 21, s - 21], -90, -90 + 360 * rem / 5.0, fill=col, width=32)
                im = im.resize((150, 150), Image.LANCZOS)
                num = text_img(str(n), "latin", 76, INK, 150, align="center")
                im.alpha_composite(num, ((150 - num.size[0]) // 2, (150 - num.size[1]) // 2 + 6))
                rings[key] = with_shadow(im, blur=12, alpha=90)
            return rings[key]
        els.append(El(None, X1 - 150, 300, t_c, t_rev + 0.3, anim="pop", dur=0.3, dyn=ring))
    elif L["timer"] == "bar":
        bars = {}

        def bar(tl_):
            if tl_ > 5.0:
                return None
            rem = max(0.0, 5.0 - tl_)
            n = max(1, int(rem) + (0 if rem == int(rem) else 1))
            k = (n, int(rem * 30))
            if k not in bars:
                im = rrect(CW, 64, 32, hexc("#000000", 120))
                w = int((CW - 12) * rem / 5.0)
                if w > 30:
                    im.alpha_composite(rrect(w, 52, 26, hexc(RED if n <= 2 else GREEN)), (6, 6))
                tx = text_img(f"{n} सेकंड बाकी", "black", 36, "#FFFFFF", CW, align="center")
                im.alpha_composite(tx, ((CW - tx.size[0]) // 2, (64 - tx.size[1]) // 2 + 2))
                im.info["pad"] = 0
                bars[k] = im
            return bars[k]
        els.append(El(None, X0, hk_y + 4, t_c, t_rev, anim="pop", dur=0.25, dyn=bar))
    else:  # big number punch in the middle
        bigs = {}

        def big(tl_):
            if tl_ > 5.0:
                return None
            n = max(1, 5 - int(tl_))
            ph = tl_ - int(tl_)
            sc = 1.25 - 0.25 * ease_out_cubic(clamp01(ph / 0.25))
            k = (n, round(sc, 2))
            if k not in bigs:
                s = int(190 * sc)
                im = rrect(s, s, s // 2, hexc(RED if n <= 2 else acc))
                num = text_img(str(n), "latin", int(116 * sc), "#FFFFFF" if n <= 2 else INK, s, align="center")
                im.alpha_composite(num, ((s - num.size[0]) // 2, (s - num.size[1]) // 2 + int(10 * sc)))
                im = with_shadow(im, blur=16, alpha=120)
                bigs[k] = im
            return bigs[k]
        els.append(El(None, X1 - 105, 345, t_c, t_rev, anim="none", dur=0.01, dyn=big, anchor_center=True))
    for k in range(5):
        sfx.append((t_c + k, "tick"))
    th = text_img("जवाब कमेंट करो 👇".replace(" 👇", ""), "black", 44, INK, CW, align="center")
    tb = card([(th, "center")], 620, bg=acc, pad=(26, 14), r=36)
    els.append(El(tb, (X0 + X1 - 620) // 2, Y1 - 82, t_c, t_rev, anim="pop", dur=0.3, pulse=0.03))
    sfx.append((t_rev, "ding"))
    # explanation
    at = text_img(f"✔ सही उत्तर: {LET[ans]}) {d['options'][ans]}".replace("✔ ", ""), "black", 50, "#FFFFFF", CW - 80)
    ac = card([(at, "left")], CW, bg=GREEN, pad=(36, 26))
    els.append(El(ac, X0, q_y, t_exp, t_end - 0.05, anim="zoom", dur=0.35))
    bulb = icon("bulb", 56, INK, bg=acc)
    lab = text_img("व्याख्या", "bold", 40, ink, 600)
    et = text_img(d["explanation"], "semibold", 42 if len(d["explanation"]) < 120 else 36, ink, CW - 80, spacing=1.12)
    items = [(hstack([bulb, lab], gap=14), "left"), (et, "left")]
    if d.get("source_line"):
        items.append((text_img(d["source_line"], "regular", 28, sub_c(ink), CW - 80), "left"))
    ec = card(items, CW, bg=cbg, pad=(36, 28), gap=14)
    els.append(El(ec, X0, q_y + ac.size[1] - 2 * ac.info["pad"] + 22, t_exp + 0.2, t_end - 0.05, anim="slide_up", dur=0.4))
    sfx.append((t_exp, "whoosh"))
    # end CTA
    qbig = end_panel()  # end: headline + the user's QR, scan-line motion
    els.append(El(qbig, (X0 + X1) // 2, (262 + Y1) // 2, t_end, t_loop, anim="pop", dur=0.4, pulse=0.015,
                  anchor_center=True, dyn=scan_fx(qbig)))
    sfx.append((t_end + 0.45, "tick"))
    sfx += [(t_end - 0.5, "rise"), (t_end, "pop"), (t_loop, "whoosh")]

    def flash(t):
        return max(0.0, 0.55 - (t - t_rev) * 3.5) if t >= t_rev else 0.0

    def cam(t):
        z = 1.0 + 0.03 * clamp01(t / 2.0) * (1 - clamp01((t - 2.0) / 0.6))
        if t_rev <= t < t_rev + 0.5:
            z = max(z, 1.0 + 0.04 * (1 - (t - t_rev) / 0.5))
        return z
    return els, sfx, cam, flash


# ---------------------------------------------------------------- VACANCY (15-22 s)
def build_vacancy(d):
    tl = Timeline()
    tl.beat("hook", d["hook_say"], d["hook"], gap=0.1)
    tl.beat("post", d.get("post_say", d["post"]), d["post"], gap=0.12)
    if d.get("vacancies"):
        tl.beat("vac", f"कुल {d['vacancies']} पद।", f"कुल {d['vacancies']} पद", gap=0.1)
    for i, (k, v) in enumerate(d.get("facts", [])[:3]):
        tl.beat(f"f{i}", f"{k}: {v}।", f"{k}: {v}", gap=0.1)
    tl.beat("tip", d["tip"], d["tip"], gap=0.15)
    tl.beat("end", d["cta_say"], d["cta_cap"], min_dur=2.6, gap=0.6)
    tl.beat("loop", None, fixed=0.5)
    return tl


def layout_vacancy(d, ch, tl, L):
    acc = ch["accent"]
    cbg, ink = L["card"], L["ink"]
    t_end, t_loop = tl["end"]["t"], tl["loop"]["t"]
    els, sfx = [], []
    els.append(El(None, X0, 262, 0.0, anim="none", dur=0.01, dyn=progress(tl.total, acc)))
    keep = t_end - 0.1  # clear the stage before the end panel (no overlap with the QR)
    eb = badge(d["badge"], RED, fg="#FFFFFF", size=40)
    els.append(El(eb, X0, 290, 0.0, keep, anim="pop", dur=0.3, still0=True, pulse=0.03, shake=[(0.3, 0.25, 8)]))
    hp = handle_pill(ch)
    els.append(El(hp, X0, 1372, 0.0, t_end - 0.1, anim="none", dur=0.01))
    y = 290 + eb.size[1] + 18
    hk = text_img(d["hook"], "black", 60, acc, CW, stroke=2, stroke_fill=(0, 0, 0, 140))
    els.append(El(hk, X0, y, 0.0, keep, anim="pop", dur=0.3, still0=True, out="shrink"))
    y += hk.size[1] + 16
    pt = text_img(d["post"], "black", 48 if len(d["post"]) < 70 else 40, ink, CW - 70)
    items = []
    if d.get("org"):
        items.append((text_img(d["org"], "bold", 30, sub_c(ink), CW - 70), "left"))
    items.append((pt, "left"))
    pc = card(items, CW, bg=cbg, pad=(35, 26), gap=10)
    els.append(El(pc, X0, y, 0.0, keep, anim="pop", dur=0.3, still0=True, out="slide_out_left"))
    y += pc.size[1] - 2 * pc.info["pad"] + 20
    post_bottom = y
    if d.get("vacancies"):
        n_v = int(re.sub(r"\D", "", str(d["vacancies"])) or 0)
        users = icon("users", 84, INK, bg=acc)
        lab = text_img("कुल पद", "bold", 42, hexc("#FFFFFF", 235), 300)
        nw = text_img(f"{n_v:,}", "latin", 84, acc, 600).size[0]
        cache = {}

        def stat(t_):
            val = int(n_v * (1 - (1 - min(1.0, t_ / 1.0)) ** 3))
            if val not in cache:
                b = rrect(CW, 150, 30, hexc("#000000", 110))
                b.alpha_composite(users, (30, 33))
                num = text_img(f"{val:,}", "latin", 84, acc, 600)
                b.alpha_composite(num, (136, (150 - num.size[1]) // 2 + 4))
                b.alpha_composite(lab, (136 + nw + 26, (150 - lab.size[1]) // 2))
                b.info["pad"] = 0
                cache[val] = b
            return cache[val]
        els.append(El(None, X0, y, tl["vac"]["t"], tl["tip"]["t"] - 0.05, anim="zoom", dur=0.35, dyn=stat,
                      out="slide_out_left", out_dur=0.35))
        sfx.append((tl["vac"]["t"], "whoosh"))
        y += 150 + 18
    ic_for = {"अंतिम तिथि": "calendar", "आखिरी तारीख": "calendar", "आयु सीमा": "clock", "योग्यता": "cap"}
    for i, (k, v) in enumerate(d.get("facts", [])[:3]):
        hot = "तिथि" in k or "तारीख" in k
        ic = icon(ic_for.get(k, "bell"), 58, INK if not hot else RED, bg=acc if not hot else "#FFFFFF")
        r1 = text_img(k, "bold", 30, (sub_c(ink)) if not hot else "#FFE4E6", CW - 140)
        r2 = text_img(v, "black", 40, ink if not hot else "#FFFFFF", CW - 140)
        col = Image.new("RGBA", (max(r1.size[0], r2.size[0]), r1.size[1] + r2.size[1] - 4), (0, 0, 0, 0))
        col.alpha_composite(r1, (0, 0)); col.alpha_composite(r2, (0, r1.size[1] - 4))
        rc = card([(hstack([ic, col], gap=20), "left")], CW, bg=RED if hot else cbg, pad=(28, 16))
        if y + rc.size[1] - 2 * rc.info["pad"] > Y1 - 90:
            break
        t_f = tl[f"f{i}"]["t"]
        els.append(El(rc, X0, y, t_f, tl["tip"]["t"] + 0.07 * (i + 1), anim="swing", dur=0.45,
                      pulse=0.02 if hot else 0, out="slide_out_left", out_dur=0.35))
        sfx.append((t_f, "ding" if hot else "pop"))
        y += rc.size[1] - 2 * rc.info["pad"] + 14
    # value-add tip replaces everything briefly as a highlighted card
    tip = text_img(d["tip"], "bold", 40, INK, CW - 120, spacing=1.1)
    bulb = icon("bulb", 60, INK, bg="#FFFFFF")
    tc = card([(hstack([bulb, tip], gap=18), "left")], CW, bg=acc, pad=(28, 22))
    t_t = tl["tip"]["t"]
    els.append(El(tc, X0, post_bottom, t_t + 0.25, t_end - 0.1, anim="rise", dur=0.45, pulse=0.015,
                  dyn=shine_fx(tc)))
    sfx.append((t_t, "whoosh"))
    qbig = end_panel()  # end: headline + the user's QR, scan-line motion
    els.append(El(qbig, (X0 + X1) // 2, (262 + Y1) // 2, t_end, t_loop, anim="pop", dur=0.4, pulse=0.015,
                  anchor_center=True, dyn=scan_fx(qbig)))
    sfx.append((t_end + 0.45, "tick"))
    sfx += [(t_end - 0.5, "rise"), (t_end, "pop")]

    def cam(t):
        return 1.0 + 0.03 * clamp01(t / 2.0) * (1 - clamp01((t - 2.0) / 0.6))
    return els, sfx, cam, None


# ---------------------------------------------------------------- WELCOME (channel intro, ~20 s)
def build_welcome(d):
    tl = Timeline()
    tl.beat("hook", d["hook_say"], d["hook"], gap=0.12)
    tl.beat("post", d["post_say"], d["post"], gap=0.12)
    tl.beat("chs", d["chs_say"], d["chs_cap"], min_dur=2.6, gap=0.15)
    tl.beat("tip", d["tip_say"], d["tip"], gap=0.2)
    tl.beat("end", d["cta_say"], d["cta_cap"], min_dur=2.8, gap=0.6)
    tl.beat("loop", None, fixed=0.5)
    return tl


def layout_welcome(d, ch, tl, L):
    acc = ch["accent"]
    cbg, ink = L["card"], L["ink"]
    t_end, t_loop = tl["end"]["t"], tl["loop"]["t"]
    keep = t_loop + 1.0
    els, sfx = [], []
    els.append(El(None, X0, 262, 0.0, anim="none", dur=0.01, dyn=progress(tl.total, acc)))
    eb = badge(d["badge"], acc, fg=INK, size=42)
    els.append(El(eb, X0, 290, 0.0, keep, anim="pop", dur=0.3, still0=True, pulse=0.03, shake=[(0.3, 0.25, 8)]))
    y = 290 + eb.size[1] + 18
    hk = text_img(d["hook"], "black", 72, "#FFFFFF", CW, stroke=3, stroke_fill=(0, 0, 0, 150))
    els.append(El(hk, X0, y, 0.0, keep, anim="pop", dur=0.3, still0=True))
    y += hk.size[1] + 16
    hook_bottom = y
    pt = text_img(d["post"], "black", 48, ink, CW - 70)
    pc = card([(pt, "left")], CW, bg=cbg, pad=(35, 26))
    els.append(El(pc, X0, y, tl["post"]["t"], t_end - 0.1, anim="slide_up", dur=0.35))
    sfx.append((tl["post"]["t"], "whoosh"))
    y += pc.size[1] - 2 * pc.info["pad"] + 20
    top = y
    t_c, dur_c = tl["chs"]["t"], tl["chs"]["dur"]
    for i, k in enumerate(["ssc", "railway", "bihar", "army"]):
        c2 = CHANNELS[k]
        pl = icon("plane", 58, "#FFFFFF", bg=TG_BLUE)
        r1 = text_img(d["names"][i], "black", 40, ink, CW - 140)
        r2 = text_img("@" + c2["handle"], "latin", 24, sub_c(ink), CW - 140)
        col = Image.new("RGBA", (max(r1.size[0], r2.size[0]), r1.size[1] + r2.size[1] - 4), (0, 0, 0, 0))
        col.alpha_composite(r1, (0, 0)); col.alpha_composite(r2, (0, r1.size[1] - 4))
        rc = card([(hstack([pl, col], gap=20), "left")], CW, bg=cbg, pad=(28, 14))
        ti = t_c + i * min(0.55, dur_c / 5)
        els.append(El(rc, X0, y, ti, tl["tip"]["t"] + tl["tip"]["dur"] - 0.05, anim="slide_left", dur=0.35))
        sfx.append((ti, "pop"))
        y += rc.size[1] - 2 * rc.info["pad"] + 12
    tip = text_img(d["tip"], "black", 42, INK, CW - 120, spacing=1.1)
    bolt = icon("bell", 60, INK, bg="#FFFFFF")
    tc = card([(hstack([bolt, tip], gap=18), "left")], CW, bg=acc, pad=(28, 22))
    t_t = tl["tip"]["t"]
    els.append(El(tc, X0, y + 14, t_t, t_end - 0.1, anim="zoom", dur=0.35, pulse=0.02))
    sfx.append((t_t, "ding"))
    s1 = text_img("Subscribe करें", "black", 76, "#FFFFFF", CW - 80, align="center")
    s2 = text_img(d["cta_line"], "bold", 38, hexc("#FFFFFF", 235), CW - 80, align="center")
    cc = card([(s1, "center"), (s2, "center")], CW, bg=RED, pad=(34, 30), gap=12)
    els.append(El(cc, X0, hook_bottom + 20, t_end, t_loop, anim="pop", dur=0.4, pulse=0.03))
    qp = end_panel(400, head=False)
    y_q = hook_bottom + 20 + cc.size[1] - 2 * cc.info["pad"] + 10
    els.append(El(qp, (X0 + X1) // 2, y_q + qp.size[1] // 2, t_end + 0.35, t_loop, anim="pop", dur=0.4,
                  anchor_center=True))
    sfx += [(t_end - 0.5, "rise"), (t_end, "pop"), (t_end + 0.35, "whoosh")]

    def cam(t):
        return 1.0 + 0.03 * clamp01(t / 2.0) * (1 - clamp01((t - 2.0) / 0.6))
    return els, sfx, cam, None


TEMPLATES = {"quiz": (build_quiz, layout_quiz), "vacancy": (build_vacancy, layout_vacancy),
             "welcome": (build_welcome, layout_welcome)}


# ---------------------------------------------------------------- thumbnail (custom, 1080x1920 JPEG < 2 MB)
PHOTO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "photos")


def pick_photo(d, seed=0):
    """Topic photo for the backdrop: by channel (railway/ssc/bihar/army), rotating through what we have."""
    if not os.path.isdir(PHOTO_DIR):
        return None
    key = d.get("photo_key") or d.get("channel", "ssc")
    fs = sorted(f for f in os.listdir(PHOTO_DIR) if f.startswith(f"bg_{key}_")) or \
        sorted(f for f in os.listdir(PHOTO_DIR) if f.startswith("bg_"))
    return os.path.join(PHOTO_DIR, fs[seed % len(fs)]) if fs else None


def thumbnail(d, ch, L, kind, out):
    """Full-canvas cover: shelf/grid shows the whole 9:16, so it can use the full height."""
    acc = ch["accent"]
    _b = Background(*(L.get("bg") or ch["bg"]), seed=7)
    _ph = pick_photo(d, 0)
    if _ph:
        _b.set_photo(_ph, (L.get("bg") or ch["bg"])[0], strength=0.5)
    bg = _b.frame(0.0).convert("RGBA")
    m, w = 70, W - 140
    y = 200
    eb = badge(d["badge"], acc if kind == "quiz" else RED, fg=INK if kind == "quiz" else "#FFFFFF", size=58)
    bg.alpha_composite(eb, ((W - eb.size[0]) // 2, y)); y += eb.size[1] + 40
    big = d.get("thumb") or d["hook"]
    t = text_img(big, "black", 128 if len(big) < 22 else 104, "#FFFFFF", w, align="center", stroke=5,
                 stroke_fill=(0, 0, 0, 210))
    bg.alpha_composite(t, ((W - t.size[0]) // 2, y)); y += t.size[1] + 50
    body = d["question"] if kind == "quiz" else d["post"]
    qt = text_img(body, "black", 64 if len(body) < 80 else 54, INK, w - 80, align="center")
    qc = card([(qt, "center")], w, bg="#FFFFFF", pad=(40, 40))
    bg.alpha_composite(qc, (m - qc.info["pad"], y - qc.info["pad"])); y += qc.size[1] - 2 * qc.info["pad"] + 40
    if kind == "quiz":
        ow = (w - 24) // 2
        for i, o in enumerate(d["options"]):
            ot = text_img(f"{LET[i]}) {o}", "bold", 46 if len(o) < 14 else 38, INK, ow - 40)
            oc = rrect(ow, 120, 30, hexc(acc))
            oc.alpha_composite(ot, (22, (120 - ot.size[1]) // 2))
            bg.alpha_composite(oc, (m + (i % 2) * (ow + 24), y + (i // 2) * 140))
        y += 2 * 140 + 40
        st = text_img("सही जवाब?", "black", 120, "#FFFFFF", 900, align="center", stroke=4, stroke_fill=(0, 0, 0, 200))
        s_ = rrect(st.size[0] + 80, st.size[1] + 36, 46, hexc(INK))
        s_.alpha_composite(st, (40, 18))
        s_ = s_.rotate(-5, expand=True, resample=Image.BICUBIC)
        bg.alpha_composite(s_, ((W - s_.size[0]) // 2, min(y, 1560)))
    else:
        if d.get("vacancies"):
            st = text_img(f"{d['vacancies']} पद", "black", 150, INK, w, align="center")
            s_ = rrect(st.size[0] + 90, st.size[1] + 40, 50, hexc(acc))
            s_.alpha_composite(st, (45, 20))
            bg.alpha_composite(s_, ((W - s_.size[0]) // 2, min(y, 1450))); y += s_.size[1] + 40
        ld = dict(d.get("facts", [])).get("अंतिम तिथि")
        if ld:
            st = text_img(f"अंतिम तिथि: {ld}", "black", 64, "#FFFFFF", w, align="center")
            s_ = rrect(st.size[0] + 70, st.size[1] + 30, 40, hexc(RED))
            s_.alpha_composite(st, (35, 15))
            bg.alpha_composite(s_, ((W - s_.size[0]) // 2, min(y, 1620)))
    hp = handle_pill(ch)
    bg.alpha_composite(hp, ((W - hp.size[0]) // 2, 1780))
    bg.convert("RGB").save(out, "JPEG", quality=88, optimize=True)
    return out


def make(kind, d, out, layout=0, preview=False, stills_only=False, music_i=0):
    ch = CHANNELS[d.get("channel", "ssc")]
    L = LAYOUTS[layout % len(LAYOUTS)]
    build, lay = TEMPLATES[kind]
    T0 = time.time()
    tl = build(d).resolve(d.get("voice", "female"))
    els, sfx, cam, flash = lay(d, ch, tl, L)
    if kind != "welcome":  # welcome text is already big on screen
        els += captions(tl, skip=("count", "loop", "end"))
    bg = Background(*(L.get("bg") or ch["bg"]), seed=(layout * 31 + music_i) % 997)
    ph = pick_photo(d, layout + music_i)
    if ph and not d.get("no_photo"):
        bg.set_photo(ph, (L.get("bg") or ch["bg"])[1], dur=tl.total)
    sfx = [(0.0, "impact")] + sfx + [(t_, "swoosh") for t_ in sorted({round(e.t_out, 2) for e in els
            if getattr(e, "out", "") == "slide_out_left" and e.t_out})][:3]
    base = os.path.splitext(out)[0]
    thumb = thumbnail(d, ch, L, kind, base + "_thumb.jpg")
    if preview or stills_only:
        marks = {"quiz": [0.0, "opts", "count", "reveal", "expl", "end"],
                 "vacancy": [0.0, "vac", "tip", "end"],
                 "welcome": [0.0, "chs", "tip", "end"]}[kind]
        for m in marks:
            if m == 0.0:
                tt, nm = 0.0, "f0"
            else:
                try:
                    b = tl[m]
                except KeyError:
                    continue
                tt, nm = min(tl.total - 0.05, b["t"] + min(b["dur"] * 0.7, 1.6)), m
            render_still(bg, els, tt, cam, flash).save(f"{base}_p_{nm}.png")
    n = 0
    if not stills_only:
        with tempfile.TemporaryDirectory() as td:
            raw, norm = os.path.join(td, "mix.wav"), os.path.join(td, "norm.wav")
            voice_ev = [(b["t"] - b["offset"], b["wav"]) for b in tl.beats if b.get("wav")]
            tracks = sorted(f for f in os.listdir(MUSIC_DIR) if f.endswith((".m4a", ".mp3", ".wav"))) if os.path.isdir(MUSIC_DIR) else []
            opts = [os.path.join(MUSIC_DIR, f) for f in tracks] + [None]
            mus = opts[music_i % len(opts)]
            audio.mix(tl.total, voice_ev, sfx, raw, music=mus, music_db=-20.0 if voice_ev else -12.0,
                      bpm=[92, 100, 108][music_i % 3])
            audio.loudnorm(raw, norm)
            n = render_video(bg, els, tl.total, norm, out, cam=cam, flash=flash)
    info = {"out": out, "thumb": thumb, "duration_s": round(tl.total, 2), "t_end": round(tl["end"]["t"], 2), "frames": n, "layout": L["name"],
            "voice": bool(any(b.get("wav") for b in tl.beats)), "secs": round(time.time() - T0, 1)}
    print(json.dumps(info, ensure_ascii=False))
    return info


if __name__ == "__main__":
    a = [x for x in sys.argv[1:] if not x.startswith("--")]
    lay = int(next((x.split("=")[1] for x in sys.argv if x.startswith("--layout=")), "0"))
    make(a[0], json.load(open(a[1], encoding="utf-8")), a[2], lay, "--preview" in sys.argv, "--stills-only" in sys.argv)
