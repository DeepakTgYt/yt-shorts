"""Daily long GK practice set (16:9, 1920x1080, ~5-7 min): N real PYQs, one after another.

Input dict:
  channel, set_no, title_line, exam_line, questions=[{q, o[4], a, e, src}]
Per question: number badge + source, question card slides in (voice reads it), the 4 options pop in
(voice reads them), 5-second countdown with ticks, the right option turns green with a ding,
then a short explanation. Final slide: "अपना स्कोर कमेंट में" + the user's QR + "पूरी जानकारी / Telegram पर".
"""
import math
import os
import re
import shutil
import subprocess
import tempfile

from PIL import Image

import audio
import make
from make import Timeline, CHANNELS, pick_photo
from render import text_img, rrect, hexc, ease_out_cubic, ease_out_back, clamp01
from explainer import LBg, _paste, LW, LH, FPS, INK

THINK = 5
LET = "ABCD"
LET_SAY = ["ए", "बी", "सी", "डी"]
GREEN, RED_SOFT = "#16A34A", "#FFFFFF"


def say_clean(x):
    x = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]", "", x)
    x = x.replace("₹", " रुपये ").replace("/", ", ").replace("(", ", ").replace(")", "").replace("?", "?")
    x = x.replace("‘", "").replace("’", "").replace("“", "").replace("”", "").replace("_", " ")
    return re.sub(r"\s{2,}", " ", x).strip()


def build(d, out, thumb_out, voice="male", music_i=0):
    ch = CHANNELS[d.get("channel", "ssc")]
    acc, tint = ch["accent"], ch["bg"][0]
    Q = d["questions"]
    n = len(Q)
    tl = Timeline()
    tl.beat("intro_h", None, fixed=0.6)
    if d.get("intro_kind") == "ca":
        tl.beat("intro_1", f"नमस्ते दोस्तों! आज के करेंट अफेयर्स क्विज़ में हैं हाल की खबरों से {n} ज़रूरी सवाल।", gap=0.3)
    else:
        tl.beat("intro_1", f"नमस्ते दोस्तों! आज के प्रैक्टिस सेट में हैं परीक्षा के लिए {n} सबसे ज़रूरी सवाल।", gap=0.3)
    tl.beat("intro_2", f"हर सवाल के बाद {THINK} सेकंड मिलेंगे। जवाब सोचिए, और आखिर में अपना स्कोर कमेंट में ज़रूर लिखिए।", gap=0.4)
    tl.beat("intro_x", None, fixed=0.6)
    for i, q in enumerate(Q):
        tl.beat(f"q{i}_h", None, fixed=0.7)
        tl.beat(f"q{i}_q", f"सवाल नंबर {i + 1}। " + say_clean(q["q"]), gap=0.3)
        tl.beat(f"q{i}_o", "। ".join(f"{LET_SAY[k]}, {say_clean(o)}" for k, o in enumerate(q["o"])) + "।", gap=0.2)
        tl.beat(f"q{i}_t", None, fixed=THINK)
        tl.beat(f"q{i}_a", f"सही जवाब है {LET_SAY[q['a']]}, {say_clean(q['o'][q['a']])}।", gap=0.25)
        if q.get("ex"):
            tl.beat(f"q{i}_e", say_clean(q["ex"]), gap=0.3)
        tl.beat(f"q{i}_x", None, fixed=0.9)
    tl.beat("end_s", f"तो दोस्तों, आपके {n} में से कितने सही हुए? अपना स्कोर कमेंट में लिखिए। "
                     "हर नई भर्ती का नोटिफिकेशन, आते ही पाँच से आठ मिनट में, हमारे टेलीग्राम पर। "
                     "स्क्रीन पर दिख रहा QR स्कैन करें। और रोज़ के नए सेट के लिए चैनल को सब्सक्राइब करें, बाकी सेट प्लेलिस्ट में मिलेंगे।", gap=0.4)
    tl.beat("end_x", None, fixed=3.0)
    tl.resolve(voice)
    photo = pick_photo(d, music_i)
    bg = LBg(photo, tint, tl.total)

    W = 1640
    x0 = (LW - W) // 2
    # ---- intro sprites
    t_intro = (tl["intro_h"]["t"], tl["intro_x"]["t"] + tl["intro_x"]["dur"])
    i1 = text_img(d["title_line"], "black", 96, acc, 1600, align="center", stroke=4, stroke_fill=(0, 0, 0, 180))
    i2 = text_img(d["exam_line"], "black", 64, "#FFFFFF", 1600, align="center", stroke=3, stroke_fill=(0, 0, 0, 170))
    i3t = text_img(f"{n} सवाल  ·  हर सवाल पर {THINK} सेकंड", "black", 50, INK, 1400, align="center")
    i3 = rrect(i3t.size[0] + 80, i3t.size[1] + 40, 36, hexc("#FFFFFF", 240))
    i3.alpha_composite(i3t, (40, 20))
    # ---- per-question sprites
    QS = []
    for i, q in enumerate(Q):
        t0 = tl[f"q{i}_h"]["t"]
        t1 = tl[f"q{i}_x"]["t"] + tl[f"q{i}_x"]["dur"]
        bt = text_img(f"प्रश्न {i + 1} / {n}", "black", 46, INK, 600)
        badge = rrect(bt.size[0] + 60, bt.size[1] + 28, 28, hexc(acc))
        badge.alpha_composite(bt, (30, 14))
        src = None
        if q.get("src"):
            st = text_img(q["src"], "bold", 36, "#FFFFFF", 900)
            src = rrect(st.size[0] + 48, st.size[1] + 24, 24, hexc("#000000", 120))
            src.alpha_composite(st, (24, 12))
        qt = text_img(q["q"], "black", 60, INK, W - 100)
        qc = rrect(W, qt.size[1] + 70, 34, hexc("#FFFFFF", 245))
        qc.alpha_composite(qt, (50, 35))
        ow = (W - 40) // 2
        opts = []
        for k, o in enumerate(q["o"]):
            lt = text_img(LET[k], "black", 48, INK, 80, align="center")
            ot = text_img(o, "bold", 48, INK, ow - 150)
            h = max(ot.size[1], 70) + 44
            norm = rrect(ow, h, 28, hexc("#FFFFFF", 235))
            good = rrect(ow, h, 28, hexc(GREEN))
            for im, ink in ((norm, INK), (good, "#FFFFFF")):
                circ = rrect(76, 76, 38, hexc(acc) if ink == INK else hexc("#FFFFFF"))
                l2 = lt if ink == INK else text_img(LET[k], "black", 48, GREEN, 80, align="center")
                circ.alpha_composite(l2, ((76 - l2.size[0]) // 2, (76 - l2.size[1]) // 2))
                im.alpha_composite(circ, (24, (h - 76) // 2))
                o2 = ot if ink == INK else text_img(o, "bold", 48, "#FFFFFF", ow - 150)
                im.alpha_composite(o2, (120, (h - o2.size[1]) // 2))
            opts.append((norm, good))
        ex = None
        if q.get("ex"):
            et = text_img(q["ex"], "bold", 42, "#FFFFFF", W - 120)
            ex = rrect(W, et.size[1] + 44, 28, hexc("#0B1220", 215))
            ex.alpha_composite(et, (60, 22))
        nums = [text_img(str(s), "black", 120, "#FFFFFF", 200, align="center", stroke=5, stroke_fill=(0, 0, 0, 200))
                for s in range(THINK, 0, -1)]
        QS.append(dict(t0=t0, t1=t1, badge=badge, src=src, qc=qc, opts=opts, ex=ex, nums=nums, a=q["a"],
                       tq=tl[f"q{i}_q"]["t"], to=tl[f"q{i}_o"]["t"], tt=tl[f"q{i}_t"]["t"], ta=tl[f"q{i}_a"]["t"],
                       te=tl[f"q{i}_e"]["t"] if q.get("ex") else None))
    # ---- end sprites
    te0 = tl["end_s"]["t"]
    e0 = text_img("अपना स्कोर कमेंट में लिखें", "black", 64, "#FFFFFF", 1000, align="center", stroke=3, stroke_fill=(0, 0, 0, 170))
    h1 = text_img("पूरी जानकारी", "black", 110, acc, 1000, align="center", stroke=3, stroke_fill=(0, 0, 0, 170))
    h2 = text_img("Telegram पर", "black", 100, "#FFFFFF", 1000, align="center", stroke=3, stroke_fill=(0, 0, 0, 170))
    qr = make.qr_card(430)

    sfx = [(0.0, "impact")]
    for s in QS:
        sfx += [(s["t0"], "whoosh")] + [(s["to"] + k * 0.12, "pop") for k in range(4)]
        sfx += [(s["tt"] + k, "tick") for k in range(THINK)] + [(s["ta"], "ding")]
    sfx.append((te0, "whoosh"))

    with tempfile.TemporaryDirectory() as td:
        raw, norm = os.path.join(td, "mix.wav"), os.path.join(td, "norm.wav")
        voice_ev = [(b["t"] - b["offset"], b["wav"]) for b in tl.beats if b.get("wav")]
        mdir = make.MUSIC_DIR
        tracks = sorted(f for f in os.listdir(mdir) if f.endswith((".m4a", ".mp3", ".wav"))) if os.path.isdir(mdir) else []
        mus = os.path.join(mdir, tracks[music_i % len(tracks)]) if tracks else None
        audio.mix(tl.total, voice_ev, sfx, raw, music=mus, music_db=-25.0, bpm=96)
        audio.loudnorm(raw, norm)
        tmp = os.path.join(td, "v.mp4")
        cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{LW}x{LH}", "-r", str(FPS),
               "-i", "-", "-i", norm, "-c:v", "libx264", "-preset", "veryfast", "-tune", "animation", "-b:v", "5M",
               "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-shortest", "-movflags", "+faststart", tmp]
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        for fi in range(int(tl.total * FPS)):
            t = fi / FPS
            p.stdin.write(frame(t, bg, x0, W, t_intro, (i1, i2, i3), QS, te0, (e0, h1, h2, qr)).convert("RGB").tobytes())
        p.stdin.close()
        if p.wait() != 0:
            raise RuntimeError("encode failed")
        shutil.copyfile(tmp, out)
    thumbnail(d, photo, tint, acc, thumb_out)
    return {"out": out, "thumb": thumb_out, "duration_s": round(tl.total, 1),
            "voice": any(b.get("wav") for b in tl.beats)}


def frame(t, bg, x0, W, t_intro, intro, QS, te0, endsp):
    fr = bg.frame(t).convert("RGBA")
    a0, a1 = t_intro
    if a0 <= t < a1 + 0.4:
        i1, i2, i3 = intro
        lp = clamp01((t - a0) / 0.5); oa = 1 - clamp01((t - a1) / 0.4); e = ease_out_back(lp)
        y = (LH - i1.size[1] - i2.size[1] - i3.size[1] - 80) // 2
        _paste(fr, i1, (LW - i1.size[0]) // 2, y - int((1 - lp) * 60), lp * oa, 0.85 + 0.15 * e); y += i1.size[1] + 30
        _paste(fr, i2, (LW - i2.size[0]) // 2, y, lp * oa); y += i2.size[1] + 50
        _paste(fr, i3, (LW - i3.size[0]) // 2, y, clamp01((t - a0 - 0.5) / 0.4) * oa)
    for s in QS:
        if not (s["t0"] <= t < s["t1"] + 0.35):
            continue
        oa = 1 - clamp01((t - s["t1"]) / 0.35)
        lp = clamp01((t - s["t0"]) / 0.45)
        _paste(fr, s["badge"], x0 - int((1 - ease_out_cubic(lp)) * 500), 60, lp * oa)
        if s["src"]:
            _paste(fr, s["src"], x0 + s["badge"].size[0] + 24, 60 + (s["badge"].size[1] - s["src"].size[1]) // 2, lp * oa)
        y = 60 + s["badge"].size[1] + 30
        qp = clamp01((t - s["tq"] + 0.1) / 0.45)
        _paste(fr, s["qc"], x0 + int((1 - ease_out_back(qp)) * 900), y, qp * oa)
        y += s["qc"].size[1] + 30
        ow = s["opts"][0][0].size[0]
        rows = [max(s["opts"][0][0].size[1], s["opts"][1][0].size[1]), max(s["opts"][2][0].size[1], s["opts"][3][0].size[1])]
        revealed = t >= s["ta"]
        for k, (norm, good) in enumerate(s["opts"]):
            op = clamp01((t - s["to"] - k * 0.12 + 0.05) / 0.35)
            ox = x0 + (k % 2) * (ow + 40)
            oy = y + (0 if k < 2 else rows[0] + 24)
            if revealed and k == s["a"]:
                rp = clamp01((t - s["ta"]) / 0.3)
                _paste(fr, good, ox, oy, oa, 1 + 0.05 * math.sin(rp * math.pi))
            else:
                _paste(fr, norm, ox, oy, op * oa * (0.55 if revealed else 1.0), 0.9 + 0.1 * ease_out_back(op))
        yb = y + rows[0] + rows[1] + 24 + 30
        if s["tt"] <= t < s["ta"]:  # countdown, bottom-centre under the options (never over a card)
            k = min(THINK - 1, int(t - s["tt"]))
            nm = s["nums"][k]
            ph = (t - s["tt"]) - k
            _paste(fr, nm, (LW - nm.size[0]) // 2, min(yb, LH - nm.size[1] - 30), 1 - 0.3 * ph, 1.15 - 0.15 * ease_out_cubic(ph))
        if s["ex"] and s["te"] is not None and t >= s["te"] - 0.1 and yb + s["ex"].size[1] < LH - 20:
            ep = clamp01((t - s["te"] + 0.1) / 0.4)
            _paste(fr, s["ex"], x0, yb + int((1 - ease_out_cubic(ep)) * 60), ep * oa)
    if t >= te0:
        e0, h1, h2, qr = endsp
        lp = clamp01((t - te0) / 0.5); e = ease_out_back(lp)
        _paste(fr, e0, (LW - e0.size[0]) // 2 - 380, 230, lp)
        _paste(fr, h1, (LW - h1.size[0]) // 2 - 380, 380 - int((1 - lp) * 80), lp, 0.8 + 0.2 * e)
        _paste(fr, h2, (LW - h2.size[0]) // 2 - 380, 380 + h1.size[1] + 10, lp, 0.8 + 0.2 * e)
        _paste(fr, qr, LW // 2 + 260, (LH - qr.size[1]) // 2, lp, (0.7 + 0.3 * e) * (1 + 0.015 * math.sin(t * 5)))
    return fr


def thumbnail(d, photo, tint, acc, out):
    bg = LBg(photo, tint, 1).frame(0).convert("RGBA")
    t1 = text_img(f"{len(d['questions'])} सवाल", "black", 210, acc, LW - 200, align="center", stroke=7, stroke_fill=(0, 0, 0, 230))
    t2 = text_img(d["thumb_line"], "black", 100, "#FFFFFF", LW - 240, align="center", stroke=5, stroke_fill=(0, 0, 0, 220))
    st = text_img(d.get("pill") or f"प्रैक्टिस सेट {d['set_no']}", "black", 72, "#FFFFFF", 900, align="center")
    pill = rrect(st.size[0] + 80, st.size[1] + 36, 40, hexc("#DC2626"))
    pill.alpha_composite(st, (40, 18))
    y = (LH - t1.size[1] - t2.size[1] - pill.size[1] - 60) // 2
    bg.alpha_composite(t1, ((LW - t1.size[0]) // 2, y)); y += t1.size[1] + 20
    bg.alpha_composite(t2, ((LW - t2.size[0]) // 2, y)); y += t2.size[1] + 40
    bg.alpha_composite(pill, ((LW - pill.size[0]) // 2, y))
    bg.convert("RGB").save(out, "JPEG", quality=88)
    return out
