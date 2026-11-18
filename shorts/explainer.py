"""Long job-detail video (16:9, 1920x1080, ~2.5-5 min): one recruitment, explained section by section.

Input dict (from parse_post):
  title, org, total, salary, age, qual, fees[(label, amount)], dates[(label, date)], days_left, channel, source_url
Each section is a slide: heading + bullets that slide in one by one in sync with the Hindi voiceover,
on a slow Ken-Burns photo backdrop, with SFX. Final slide: the user's QR + "पूरी जानकारी / Telegram पर".
"""
import math
import os
import re
import subprocess
import tempfile

import numpy as np
from PIL import Image, ImageFilter

import audio
import make
from make import Timeline, CHANNELS, pick_photo, icon
from render import text_img, rrect, hexc, ease_out_cubic, ease_out_back, clamp01

LW, LH, FPS = 1920, 1080, 30
INK = "#14172E"

# common English phrases in notices -> plain Hindi (on screen and in the voice)
HI = [(r"All Candidates Except Concessional Categories", "सामान्य / OBC / EWS"),
      (r"General\s*/\s*OBC\s*/\s*EWS", "सामान्य / OBC / EWS"), (r"\bGeneral\b|\bGen\b|\bUR\b", "सामान्य"),
      (r"SC\s*/\s*ST\s*/\s*Female.*", "SC / ST / महिला / अन्य छूट वाले"), (r"\bFemale\b", "महिला"),
      (r"Ex:?\s*Servicemen", "पूर्व सैनिक"), (r"Refund for Rs\.?\s*(\d+) Fee Category", r"परीक्षा देने पर ₹\1 वालों को वापसी"),
      (r"Minimum Age", "न्यूनतम आयु"), (r"Maximum Age", "अधिकतम आयु"), (r"\bYears?\b", "वर्ष"),
      (r"Pay Level", "पे लेवल"), (r"Level\s*(\d+)", r"लेवल \1"), (r"Rs\.?\s*(\d[\d,]*)\s*/?-?", r"₹\1"),
      (r"Detailed Notification Date", "अधिसूचना जारी"), (r"Starting Date for Apply Online", "आवेदन शुरू"),
      (r"Last Date \(Apply\)|Last Date for Apply Online|Last Date to Apply", "आवेदन की अंतिम तिथि"),
      (r"Last Date for Fee Payment", "फीस की अंतिम तिथि"), (r"Application Correction Starting Date", "सुधार शुरू"),
      (r"Application Correction Last Date", "सुधार की अंतिम तिथि"), (r"Exam Date", "परीक्षा तिथि"),
      (r"Admit Card", "एडमिट कार्ड"), (r"Notice Date", "सूचना तिथि"), (r"\(11:59 PM\)", "(रात 11:59)"),
      (r"\bGraduate\b", "ग्रेजुएट"), (r"\bGraduation\b", "ग्रेजुएशन"), (r"\b10th\b", "10वीं"), (r"\b12th\b", "12वीं"),
      (r"\bPass\b", "पास"), (r"\bDegree\b", "डिग्री"), (r"\bAny\b", "किसी भी")]


def hi(x):
    for a, b in HI:
        x = re.sub(a, b, x, flags=re.I)
    x = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\u25A0-\u25FF\uFE0F]", "", x)
    x = re.sub(r"पे लेवल\s*:\s*लेवल", "पे लेवल", x)
    return re.sub(r"\s{2,}", " ", x).strip(" ;:-·")


def _field(t, *labels):
    for lab in labels:
        m = re.search(lab + r"[^:\n]*:\s*([^\n]+)", t)
        if m:
            return m.group(1).strip()
    return ""


def parse_post(t, title, total):
    """Pull the structured parts of one of our Telegram 'नई भर्ती' posts."""
    d = {"title": title, "total": total}
    d["org"] = _field(t, "🏛")
    d["salary"] = hi(_field(t, "Salary", "वेतन"))
    age = _field(t, "Age Limit", "आयु सीमा")
    d["age"] = [hi(a) for a in re.split(r";\s*", age) if a.strip()][:3]
    d["qual"] = hi(_field(t, "Qualification", "Eligibility", "योग्यता"))
    fees, dates, sec = [], [], None
    for ln in t.split("\n"):
        s = ln.strip()
        if "Application Fee" in s or "आवेदन शुल्क" in s and ":" not in s:
            sec = "fee"; continue
        if "Important Dates" in s or "महत्वपूर्ण तिथि" in s:
            sec = "date"; continue
        if not s.startswith("▫"):
            if s and sec and not s.startswith("▫"):
                sec = None if not s.startswith("▫") and ":" not in s else sec
            continue
        if ":" not in s:
            continue
        body = s.lstrip("▫️ ")
        if sec == "fee":
            lab, val = body.rsplit(":", 1)
        else:
            lab, val = body.split(":", 1)
        if re.match(r"[\u0900-\u097F]", lab) and " / " in lab:  # "हिंदी / English" label: keep the English part for mapping
            lab = lab.split(" / ", 1)[1]
        lab = lab.strip()
        if sec == "date" and "·" in lab:  # e.g. "Graduate · Last Date": a qualification hint
            q, lab = [x.strip() for x in lab.split("·", 1)]
            d.setdefault("qual_hint", q)
        if sec == "fee":
            fees.append((hi(lab), hi(val)))
        elif sec == "date":
            dates.append((hi(lab), hi(val)))
    seen, dd = set(), []
    for lab, val in dates:  # drop repeats like a second "अंतिम तिथि"
        if val in seen or lab in ("अंतिम तिथि", "Last Date"):
            continue
        seen.add(val); dd.append((lab, val))
    d["fees"], d["dates"] = fees[:4], dd[:6]
    if not d["qual"] and d.get("qual_hint"):
        d["qual"] = hi(d["qual_hint"])
    m = re.search(r"(\d+)\s*दिन शेष", t) or re.search(r"Sirf (\d+) din", t)
    d["days_left"] = int(m.group(1)) if m else None
    d["website"] = _field(t, "Official Website")
    return d


def say_of(x):
    """Spoken form: drop symbols the voice reads badly."""
    x = x.replace("₹", "").replace("/", ", ").replace("(", ", ").replace(")", "")
    return re.sub(r"\s{2,}", " ", x)


def sections(d):
    """[(key, heading, [(on_screen, spoken), ...])]"""
    n = d["total"]
    S = []
    intro = [(f"कुल {n:,} पद" if n else "नई भर्ती", f"{d['title']}, कुल {n:,} पदों पर भर्ती निकली है।" if n else
              f"{d['title']}, नई भर्ती निकली है।")]
    if d.get("days_left"):
        intro.append((f"सिर्फ {d['days_left']} दिन बाकी", f"आवेदन के लिए अब सिर्फ {d['days_left']} दिन बचे हैं।"))
    intro.append(("पूरी जानकारी इस वीडियो में", "इस वीडियो में इस भर्ती की पूरी जानकारी आसान भाषा में जानिए।"))
    S.append(("intro", d["title"], intro))
    if n or d.get("salary"):
        b = []
        if n:
            b.append((f"कुल पद: {n:,}", f"कुल पद हैं {n:,}।"))
        if d.get("salary"):
            b.append((f"वेतन: {d['salary']}", f"वेतन: {say_of(d['salary'])}।"))
        S.append(("posts", "पद और वेतन", b))
    if d.get("qual"):
        S.append(("qual", "शैक्षणिक योग्यता", [(d["qual"], f"योग्यता: {say_of(d['qual'])}।")]))
    if d.get("age"):
        S.append(("age", "आयु सीमा", [(a, say_of(a) + "।") for a in d["age"]] +
                  [("आरक्षित वर्ग को नियम अनुसार छूट", "आरक्षित वर्ग को नियम के अनुसार आयु में छूट मिलेगी।")]))
    if d.get("fees"):
        S.append(("fee", "आवेदन शुल्क", [(f"{a}: {b}", f"{say_of(a)}: {say_of(b)} रुपये।") for a, b in d["fees"]]))
    if d.get("dates"):
        S.append(("dates", "महत्वपूर्ण तिथियाँ", [(f"{a}: {b}", f"{say_of(a)}: {say_of(b)}।") for a, b in d["dates"]]))
    recap = []
    if n:
        recap.append((f"पद: {n:,}", f"एक बार फिर से देख लीजिए। कुल पद {n:,}।"))
    if d.get("qual"):
        recap.append((f"योग्यता: {d['qual']}", f"योग्यता {say_of(d['qual'])}।"))
    ages = [re.sub(r"\D", "", a) for a in d.get("age", [])[:2]]
    if len(ages) == 2 and all(ages):
        recap.append((f"आयु: {ages[0]} से {ages[1]} वर्ष", f"आयु {ages[0]} से {ages[1]} वर्ष।"))
    last = next(((a, b) for a, b in d.get("dates", []) if "आवेदन की अंतिम" in a), None)
    if last:
        recap.append((f"अंतिम तिथि: {last[1]}", f"और आवेदन की आखिरी तारीख {say_of(last[1])}।"))
    if len(recap) >= 2:
        S.append(("recap", "एक नज़र में", recap))
    S.append(("tips", "फॉर्म भरते समय ध्यान दें", [
        ("नाम, जन्मतिथि 10वीं के सर्टिफिकेट जैसे ही भरें", "नाम और जन्मतिथि बिल्कुल वैसे ही भरें, जैसे दसवीं के सर्टिफिकेट में लिखे हैं।"),
        ("फोटो और सिग्नेचर साफ और सही साइज़ में", "फोटो और सिग्नेचर साफ हों, और नोटिफिकेशन में बताए साइज़ में ही अपलोड करें।"),
        ("रजिस्ट्रेशन नंबर और पासवर्ड संभाल कर रखें", "रजिस्ट्रेशन नंबर और पासवर्ड कहीं लिख कर संभाल लें, एडमिट कार्ड के समय यही काम आएगा।"),
        ("आखिरी दिन का इंतज़ार न करें", "आखिरी दिन का इंतज़ार मत कीजिए, उस दिन वेबसाइट अक्सर धीमी हो जाती है।")]))
    S.append(("how", "आवेदन कैसे करें", [
        ("1. ऑफिशियल वेबसाइट खोलें", "सबसे पहले ऑफिशियल वेबसाइट खोलें।"),
        ("2. रजिस्ट्रेशन करके फॉर्म भरें", "फिर रजिस्ट्रेशन करके फॉर्म ध्यान से भरें।"),
        ("3. फोटो, सिग्नेचर, डॉक्यूमेंट अपलोड करें", "फोटो, सिग्नेचर और डॉक्यूमेंट अपलोड करें।"),
        ("4. फीस भरें और प्रिंट निकाल लें", "फीस भरें और फॉर्म का प्रिंट ज़रूर निकाल लें।"),
        ("आवेदन से पहले ऑफिशियल नोटिफिकेशन ज़रूर पढ़ें", "आवेदन से पहले ऑफिशियल नोटिफिकेशन ज़रूर पढ़ें।")]))
    S.append(("end", "पूरी जानकारी", [("Telegram पर", "हर नई भर्ती का नोटिफिकेशन, आते ही पाँच से आठ मिनट में, हमारे टेलीग्राम पर। "
                                                    "स्क्रीन पर दिख रहा QR स्कैन करें, और चैनल को सब्सक्राइब ज़रूर करें।")]))
    return S


class LBg:
    def __init__(self, photo, tint, dur):
        ph = Image.open(photo).convert("RGB") if photo else Image.new("RGB", (LW, LH), hexc(tint)[:3])
        s = max(LW * 1.2 / ph.size[0], LH * 1.2 / ph.size[1])
        ph = ph.resize((int(ph.size[0] * s) + 2, int(ph.size[1] * s) + 2), Image.LANCZOS)
        a = np.asarray(ph).astype(np.float32) * 0.42 + np.array(hexc(tint)[:3], np.float32) * 0.30
        h, w = a.shape[:2]
        yy, xx = np.mgrid[0:h, 0:w]
        a *= (1 - 0.5 * np.clip(((xx - w / 2) / (w / 1.3)) ** 2 + ((yy - h / 2) / (h / 1.3)) ** 2, 0, 1))[..., None]
        self.ph = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
        self.dur = dur

    def frame(self, t):
        pw, ph = self.ph.size
        p = (t % 40) / 40  # gentle loop every 40 s
        z = 1.0 + 0.08 * (0.5 - 0.5 * math.cos(p * 2 * math.pi))
        cw, ch = int(LW * 1.15 / z), int(LH * 1.15 / z)
        x0 = int((pw - cw) * (0.5 + 0.35 * math.sin(p * 2 * math.pi)))
        y0 = int((ph - ch) * 0.5)
        x0 = max(0, min(pw - cw, x0))
        return self.ph.crop((x0, y0, x0 + cw, y0 + ch)).resize((LW, LH), Image.BILINEAR)


def _paste(fr, sp, x, y, alpha=1.0, scale=1.0):
    if alpha <= 0.01:
        return
    if abs(scale - 1) > 0.003:
        w0, h0 = sp.size
        sp = sp.resize((max(1, int(w0 * scale)), max(1, int(h0 * scale))), Image.BICUBIC)
        x, y = int(x + (w0 - sp.size[0]) / 2), int(y + (h0 - sp.size[1]) / 2)
    if alpha < 0.999:
        sp = sp.copy(); sp.putalpha(sp.getchannel("A").point(lambda v: int(v * alpha)))
    fr.paste(sp, (int(x), int(y)), sp)


def build(d, out, thumb_out, voice="male", music_i=0):
    ch = CHANNELS[d.get("channel", "ssc")]
    acc, tint = ch["accent"], ch["bg"][0]
    S = sections(d)
    tl = Timeline()
    for si, (key, head, bullets) in enumerate(S):
        tl.beat(f"{si}_h", None, fixed=0.6)
        for bi, (scr, sp) in enumerate(bullets):
            tl.beat(f"{si}_{bi}", sp, scr, gap=0.35)
        tl.beat(f"{si}_x", None, fixed=1.0 if key != "end" else 3.0)
    tl.resolve(voice)
    photo = pick_photo(d, music_i)
    bg = LBg(photo, tint, tl.total)
    # pre-render sprites
    CWD = 1500
    slides = []
    for si, (key, head, bullets) in enumerate(S):
        t0 = tl[f"{si}_h"]["t"]
        t1 = tl[f"{si}_x"]["t"] + tl[f"{si}_x"]["dur"]
        if key == "end":
            h1 = text_img("पूरी जानकारी", "black", 110, acc, 1000, align="center", stroke=3, stroke_fill=(0, 0, 0, 170))
            h2 = text_img("Telegram पर", "black", 100, "#FFFFFF", 1000, align="center", stroke=3, stroke_fill=(0, 0, 0, 170))
            q = make.qr_card(430)
            slides.append(dict(key=key, t0=t0, t1=t1, end=(h1, h2, q)))
            continue
        hs = text_img(head if key != "intro" else head, "black", 76 if key != "intro" else 70, INK, CWD - 80)
        hb = rrect(hs.size[0] + 70, hs.size[1] + 36, 30, hexc(acc))
        hb.alpha_composite(hs, (35, 18))
        bl = []
        for bi, (scr, sp) in enumerate(bullets):
            big = key == "intro" and bi == 0
            tx = text_img(scr, "black", 84 if big else 54, acc if big else INK, CWD - 170)
            dot = icon("bell" if key == "intro" else "check" if key in ("recap", "tips") else ("calendar" if key == "dates" else "cap" if key == "qual" else
                       "clock" if key == "age" else "bulb" if key == "how" else "users"), 64, INK, bg=acc)
            row = rrect(CWD, max(tx.size[1], 64) + 40, 28, hexc("#FFFFFF", 238))
            row.alpha_composite(dot, (26, (row.size[1] - 64) // 2))
            row.alpha_composite(tx, (120, (row.size[1] - tx.size[1]) // 2))
            bl.append((tl[f"{si}_{bi}"]["t"], row))
        slides.append(dict(key=key, t0=t0, t1=t1, head=hb, bullets=bl))
    # captions strip (bottom) reuse spoken text chunks? keep the screen clean: bullets already mirror the voice
    sfx = [(0.0, "impact")]
    for sl in slides:
        sfx.append((sl["t0"], "whoosh"))
        for t_, _ in sl.get("bullets", []):
            sfx.append((t_, "pop"))
    with tempfile.TemporaryDirectory() as td:
        raw, norm = os.path.join(td, "mix.wav"), os.path.join(td, "norm.wav")
        voice_ev = [(b["t"] - b["offset"], b["wav"]) for b in tl.beats if b.get("wav")]
        mdir = make.MUSIC_DIR
        tracks = sorted(f for f in os.listdir(mdir) if f.endswith((".m4a", ".mp3", ".wav"))) if os.path.isdir(mdir) else []
        mus = os.path.join(mdir, tracks[music_i % len(tracks)]) if tracks else None
        audio.mix(tl.total, voice_ev, sfx, raw, music=mus, music_db=-24.0, bpm=96)
        audio.loudnorm(raw, norm)
        tmp = os.path.join(td, "v.mp4")
        cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{LW}x{LH}", "-r", str(FPS),
               "-i", "-", "-i", norm, "-c:v", "libx264", "-preset", "veryfast", "-tune", "animation", "-b:v", "6M",
               "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-shortest", "-movflags", "+faststart", tmp]
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        n = int(tl.total * FPS)
        for i in range(n):
            t = i / FPS
            fr = bg.frame(t).convert("RGBA")
            for sl in slides:
                if not (sl["t0"] <= t < sl["t1"] + 0.4):
                    continue
                out_a = 1 - clamp01((t - sl["t1"]) / 0.4)
                lp = clamp01((t - sl["t0"]) / 0.45)
                if sl["key"] == "end":
                    h1, h2, q = sl["end"]
                    e = ease_out_back(lp)
                    _paste(fr, h1, (LW - h1.size[0]) // 2 - 380, 330 - int((1 - lp) * 80), lp * out_a, 0.8 + 0.2 * e)
                    _paste(fr, h2, (LW - h2.size[0]) // 2 - 380, 330 + h1.size[1] + 10, lp * out_a, 0.8 + 0.2 * e)
                    pulse = 1 + 0.015 * math.sin(t * 5)
                    _paste(fr, q, LW // 2 + 260, (LH - q.size[1]) // 2, lp * out_a, (0.7 + 0.3 * e) * pulse)
                    continue
                x0 = (LW - 1500) // 2
                hb = sl["head"]
                _paste(fr, hb, x0 - int((1 - ease_out_cubic(lp)) * 600), 90, lp * out_a)
                y = 90 + hb.size[1] + 40
                for t_b, row in sl["bullets"]:
                    if y + row.size[1] > LH - 60:
                        break
                    if t >= t_b - 0.05:
                        bp = clamp01((t - t_b + 0.05) / 0.4)
                        _paste(fr, row, x0 + int((1 - ease_out_back(bp)) * 900), y, bp * out_a)
                    y += row.size[1] + 22
            p.stdin.write(fr.convert("RGB").tobytes())
        p.stdin.close()
        if p.wait() != 0:
            raise RuntimeError("encode failed")
        import shutil
        shutil.copyfile(tmp, out)
    thumbnail(d, photo, tint, acc, thumb_out)
    return {"out": out, "thumb": thumb_out, "duration_s": round(tl.total, 1),
            "voice": any(b.get("wav") for b in tl.beats)}


def thumbnail(d, photo, tint, acc, out):
    bg = LBg(photo, tint, 1).frame(0).convert("RGBA")
    n = d["total"]
    t1 = text_img(f"{n:,} पद" if n else "नई भर्ती", "black", 200, acc, LW - 200, align="center", stroke=7,
                  stroke_fill=(0, 0, 0, 230))
    t2 = text_img(re.sub(r"\s*Recruitment.*", "", d["title"])[:40] + " भर्ती", "black", 96, "#FFFFFF", LW - 240,
                  align="center", stroke=5, stroke_fill=(0, 0, 0, 220))
    st = text_img("पूरी जानकारी", "black", 72, "#FFFFFF", 900, align="center")
    pill = rrect(st.size[0] + 80, st.size[1] + 36, 40, hexc("#DC2626"))
    pill.alpha_composite(st, (40, 18))
    y = (LH - t1.size[1] - t2.size[1] - pill.size[1] - 60) // 2
    bg.alpha_composite(t1, ((LW - t1.size[0]) // 2, y)); y += t1.size[1] + 20
    bg.alpha_composite(t2, ((LW - t2.size[0]) // 2, y)); y += t2.size[1] + 40
    bg.alpha_composite(pill, ((LW - pill.size[0]) // 2, y))
    bg.convert("RGB").save(out, "JPEG", quality=88)
    return out


if __name__ == "__main__":
    import sys, json
    d = json.load(open(sys.argv[1], encoding="utf-8"))
    print(build(d, sys.argv[2], sys.argv[2].replace(".mp4", "_thumb.jpg")))
