"""Frame renderer: sprites + keyframed 2D motion, piped to ffmpeg as raw RGB."""
import math
import re
import os
import subprocess

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(HERE, "assets", "fonts")
W, H, FPS = 1080, 1920, 30
SS = 3  # supersampling for icons / rounded shapes

FONTS = {
    "regular": "NotoSansDevanagari-Regular.ttf",
    "semibold": "NotoSansDevanagari-SemiBold.ttf",
    "bold": "NotoSansDevanagari-Bold.ttf",
    "black": "NotoSansDevanagari-ExtraBold.ttf",
    "latin": "Poppins-Bold.ttf",
}
LATIN_FALLBACK = {"regular": "NotoSans-Regular.ttf", "semibold": "NotoSans-SemiBold.ttf",
                  "bold": "NotoSans-Bold.ttf", "black": "NotoSans-ExtraBold.ttf", "latin": "Poppins-Bold.ttf"}
_fc = {}
_cmap = {}


def _covered(fname):
    if fname not in _cmap:
        from fontTools.ttLib import TTFont
        _cmap[fname] = set(TTFont(os.path.join(FONT_DIR, fname)).getBestCmap().keys())
    return _cmap[fname]


def _font_file(fname, size):
    k = (fname, size)
    if k not in _fc:
        _fc[k] = ImageFont.truetype(os.path.join(FONT_DIR, fname), size, layout_engine=ImageFont.Layout.RAQM)
    return _fc[k]


NEUTRAL = set(" .,:;!?-–—()/'\"%+&")


def runs(text, weight):
    """Split text into (substring, font-file) runs: Noto Sans Devanagari where it has the glyph,
    Noto Sans (same design family) for Latin letters it lacks. Punctuation sticks to the current run."""
    prim, fb = FONTS[weight], LATIN_FALLBACK[weight]
    cov = _covered(prim)
    out = []
    for ch in text:
        if ch in NEUTRAL and out:
            f = out[-1][1]
        else:
            f = prim if ord(ch) in cov else fb
        if out and out[-1][1] == f:
            out[-1][0] += ch
        else:
            out.append([ch, f])
    return [(a, b) for a, b in out]


def measure(text, weight, size):
    return sum(_font_file(f, size).getlength(t) for t, f in runs(text, weight))


def font(weight, size):
    return _font_file(FONTS[weight], size)


def hexc(h, a=255):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) + (a,)


# ---------------------------------------------------------------- easing
def ease_out_cubic(x):
    return 1 - (1 - x) ** 3


def ease_out_back(x, s=1.70158):
    x -= 1
    return x * x * ((s + 1) * x + s) + 1


def ease_in_out(x):
    return 0.5 - 0.5 * math.cos(math.pi * x)


def clamp01(x):
    return max(0.0, min(1.0, x))


# ---------------------------------------------------------------- text
def wrap(text, weight, size, max_w):
    lines = []
    for para in text.split("\n"):
        cur = ""
        for w in para.split(" "):
            t = (cur + " " + w).strip()
            if measure(t, weight, size) <= max_w or not cur:
                cur = t
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
    return lines


_NOGLYPH = re.compile(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D]")


def text_img(text, weight, size, color, max_w, align="left", spacing=1.08, stroke=0, stroke_fill=None):
    text = _NOGLYPH.sub("", text).replace("  ", " ").strip()  # emoji have no glyph in our fonts (tofu boxes)
    """RGBA image of wrapped text. Uses font ascent/descent so Devanagari matras never clip."""
    f = font(weight, size)
    asc, desc = f.getmetrics()
    lh = int((asc + desc) * spacing)
    lines = wrap(text, weight, size, max_w)
    widths = [measure(l, weight, size) for l in lines]
    w = int(max(widths) + 2 * stroke + 4)
    h = int(lh * (len(lines) - 1) + asc + desc + 2 * stroke + 4)
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for i, (l, lw) in enumerate(zip(lines, widths)):
        x = {"left": 0, "center": (w - lw) / 2, "right": w - lw}[align] + stroke + 2
        for seg, ff in runs(l, weight):
            fo = _font_file(ff, size)
            d.text((x, stroke + 2 + i * lh + asc), seg, font=fo, fill=color, anchor="ls",
                   stroke_width=stroke, stroke_fill=stroke_fill)
            x += fo.getlength(seg)
    return im


# ---------------------------------------------------------------- shapes
def rrect(w, h, r, fill, outline=None, width=0):
    im = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, w * SS - 1, h * SS - 1], r * SS, fill=fill, outline=outline, width=width * SS)
    return im.resize((w, h), Image.LANCZOS)


def with_shadow(im, blur=22, offset=(0, 14), alpha=90):
    pad = blur * 2 + max(abs(offset[0]), abs(offset[1]))
    w, h = im.size
    out = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
    sh = Image.new("RGBA", out.size, (0, 0, 0, 0))
    a = im.getchannel("A").point(lambda v: v * alpha // 255)
    blk = Image.new("RGBA", im.size, (8, 10, 30, 255))
    blk.putalpha(a)
    sh.paste(blk, (pad + offset[0], pad + offset[1]))
    sh = sh.filter(ImageFilter.GaussianBlur(blur))
    out = Image.alpha_composite(sh, out)
    out.alpha_composite(im, (pad, pad))
    out.info["pad"] = pad
    return out


def card(content_list, w, bg="#FFFFFF", r=34, pad=(44, 34), gap=14, outline=None, ow=0, shadow=True):
    """content_list: list of (img, align) stacked vertically inside a rounded card."""
    inner_h = sum(i.size[1] for i, _ in content_list) + gap * (len(content_list) - 1)
    h = inner_h + 2 * pad[1]
    base = rrect(w, h, r, hexc(bg) if isinstance(bg, str) else bg, outline=outline, width=ow)
    y = pad[1]
    for img, al in content_list:
        x = {"left": pad[0], "center": (w - img.size[0]) // 2, "right": w - pad[0] - img.size[0]}[al]
        base.alpha_composite(img, (int(x), int(y)))
        y += img.size[1] + gap
    return with_shadow(base) if shadow else base


def hstack(items, gap=20, valign="center"):
    w = sum(i.size[0] for i in items) + gap * (len(items) - 1)
    h = max(i.size[1] for i in items)
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    x = 0
    for i in items:
        y = (h - i.size[1]) // 2 if valign == "center" else 0
        im.alpha_composite(i, (x, y))
        x += i.size[0] + gap
    return im


# ---------------------------------------------------------------- icons (drawn, no external art)
def icon(name, size, color="#FFFFFF", bg=None):
    s = size * SS
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    c = hexc(color)
    if bg:
        d.ellipse([0, 0, s - 1, s - 1], fill=hexc(bg))
    k = s / 100.0

    def P(*pts):
        return [(x * k, y * k) for x, y in pts]
    lw = max(1, int(7 * k))
    if name == "plane":  # paper plane
        d.polygon(P((22, 49), (76, 27), (66, 74), (50, 62), (42, 71), (41, 58)), fill=c)
        d.polygon(P((41, 58), (70, 33), (50, 62)), fill=hexc("#C8DAEA") if bg else c)
    elif name == "check":
        d.line(P((27, 52), (43, 68), (74, 34)), fill=c, width=int(10 * k), joint="curve")
    elif name == "cross":
        d.line(P((32, 32), (68, 68)), fill=c, width=int(10 * k))
        d.line(P((68, 32), (32, 68)), fill=c, width=int(10 * k))
    elif name == "calendar":
        d.rounded_rectangle(P((24, 28), (76, 78)), int(8 * k), outline=c, width=lw)
        d.rectangle(P((24, 28), (76, 42)), fill=c)
        d.line(P((37, 20), (37, 33)), fill=c, width=lw)
        d.line(P((63, 20), (63, 33)), fill=c, width=lw)
        for x in (37, 50, 63):
            for y in (53, 66):
                d.ellipse(P((x - 4, y - 4), (x + 4, y + 4)), fill=c)
    elif name == "cap":
        d.polygon(P((14, 42), (50, 26), (86, 42), (50, 58)), fill=c)
        d.polygon(P((30, 50), (70, 50), (70, 66), (50, 74), (30, 66)), fill=c)
        d.line(P((82, 44), (82, 66)), fill=c, width=lw)
    elif name == "bulb":
        d.ellipse(P((28, 18), (72, 62)), fill=c)
        d.rectangle(P((40, 56), (60, 70)), fill=c)
        d.rounded_rectangle(P((40, 73), (60, 81)), int(3 * k), fill=c)
    elif name == "bell":
        d.pieslice(P((26, 22), (74, 80)), 180, 360, fill=c)
        d.rectangle(P((26, 50), (74, 66)), fill=c)
        d.polygon(P((20, 70), (80, 70), (74, 62), (26, 62)), fill=c)
        d.ellipse(P((43, 70), (57, 84)), fill=c)
        d.ellipse(P((45, 14), (55, 24)), fill=c)
    elif name == "users":
        d.ellipse(P((38, 20), (62, 44)), fill=c)
        d.pieslice(P((24, 48), (76, 100)), 180, 360, fill=c)
    elif name == "star":
        pts = []
        for i in range(10):
            a = -math.pi / 2 + i * math.pi / 5
            rr = 34 if i % 2 == 0 else 15
            pts.append((50 + rr * math.cos(a), 52 + rr * math.sin(a)))
        d.polygon(P(*pts), fill=c)
    elif name == "arrow":
        d.polygon(P((20, 42), (56, 42), (56, 26), (82, 50), (56, 74), (56, 58), (20, 58)), fill=c)
    elif name == "book":
        d.polygon(P((16, 28), (48, 34), (48, 80), (16, 72)), fill=c)
        d.polygon(P((84, 28), (52, 34), (52, 80), (84, 72)), fill=c)
    elif name == "clock":
        d.ellipse(P((18, 18), (82, 82)), outline=c, width=lw)
        d.line(P((50, 50), (50, 30)), fill=c, width=lw)
        d.line(P((50, 50), (64, 58)), fill=c, width=lw)
    return im.resize((size, size), Image.LANCZOS)


# ---------------------------------------------------------------- background
class Background:
    def __init__(self, c1, c2, c3, seed=0):
        self.extra = 700
        hh = H + self.extra
        y = np.linspace(0, 1, hh)[:, None]
        x = np.linspace(0, 1, W)[None, :]
        tt = np.clip(0.75 * y + 0.25 * x, 0, 1)
        # three-stop gradient, smooth
        a, b, c = [np.array(hexc(cc)[:3], float) for cc in (c1, c2, c3)]
        t1 = np.clip(tt * 2, 0, 1)[..., None]
        t2 = np.clip(tt * 2 - 1, 0, 1)[..., None]
        g = np.where((tt < 0.5)[..., None], a + (b - a) * t1, b + (c - b) * t2)
        g += np.random.default_rng(seed).normal(0, 1.1, g.shape)  # dither against banding
        self.base = Image.fromarray(np.clip(g, 0, 255).astype(np.uint8), "RGB")
        # soft light blobs
        self.blobs = []
        rng = np.random.default_rng(seed + 1)
        for i in range(4):
            r = int(rng.uniform(260, 420))
            yy, xx = np.mgrid[-r:r, -r:r]
            dd = np.sqrt(xx ** 2 + yy ** 2) / r
            al = (np.clip(1 - dd, 0, 1) ** 2 * rng.uniform(26, 42)).astype(np.uint8)
            im = Image.new("RGBA", (2 * r, 2 * r), (255, 255, 255, 0))
            im.putalpha(Image.fromarray(al))
            self.blobs.append((im, rng.uniform(0, W), rng.uniform(200, H - 200), rng.uniform(0, 6.28),
                               rng.uniform(0.15, 0.3)))
        # faint diagonal line pattern layer
        pat = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        dp = ImageDraw.Draw(pat)
        for i in range(-H, W + H, 90):
            dp.line([(i, 0), (i + H * 0.5, H)], fill=(255, 255, 255, 9), width=2)
        self.pat = pat

    def frame(self, t):
        off = int(self.extra / 2 + self.extra / 2 * math.sin(t * 0.22))
        im = self.base.crop((0, off, W, off + H))
        for b, x0, y0, ph, sp in self.blobs:
            x = x0 + 120 * math.sin(t * sp + ph) - b.size[0] / 2
            y = y0 + 90 * math.cos(t * sp * 0.8 + ph) - b.size[1] / 2
            im.paste(b, (int(x), int(y)), b)
        im.paste(self.pat, (0, 0), self.pat)
        return im


# ---------------------------------------------------------------- elements
class El:
    """A sprite placed at (x, y) (top-left of its visible box; shadow padding handled)."""

    def __init__(self, sprite, x, y, t_in, t_out=None, anim="slide_up", out="fade", dur=0.45,
                 out_dur=0.3, dyn=None, variants=None, pulse=0.0, anchor_center=False, shake=None,
                 still0=False):
        self.sprite = sprite
        self.pad = sprite.info.get("pad", 0) if sprite is not None else 0
        self.x, self.y = x, y
        self.t_in, self.t_out = t_in, t_out
        self.anim, self.out, self.dur, self.out_dur = anim, out, dur, out_dur
        self.dyn = dyn  # callable(t_local)->sprite
        self.variants = variants or []  # [(t, sprite)]
        self.pulse = pulse
        self.anchor_center = anchor_center
        self.shake = shake or []  # [(t0, dur, amp_px)]
        self.still0 = still0  # fully visible on frame 0 (scale punch only, no fade)

    def draw(self, frame, t):
        if t < self.t_in or (self.t_out is not None and t >= self.t_out + self.out_dur):
            return
        sp = self.sprite
        for vt, vs in self.variants:
            if t >= vt:
                sp = vs
        if self.dyn:
            sp = self.dyn(t - self.t_in)
            if sp is None:
                return
        pad = sp.info.get("pad", self.pad)
        x, y = self.x - pad, self.y - pad
        if self.anchor_center:
            x, y = self.x - sp.size[0] // 2, self.y - sp.size[1] // 2
        alpha, scale = 1.0, 1.0
        p = clamp01((t - self.t_in) / self.dur)
        if p < 1:
            e = ease_out_cubic(p)
            if self.anim == "slide_up":
                y += int((1 - e) * 140); alpha = e
            elif self.anim == "slide_down":
                y -= int((1 - e) * 140); alpha = e
            elif self.anim == "slide_left":
                x += int((1 - e) * (W * 0.9)); alpha = min(1, e * 1.5)
            elif self.anim == "slide_right":
                x -= int((1 - e) * (W * 0.9)); alpha = min(1, e * 1.5)
            elif self.anim == "pop":
                if self.still0:
                    scale = 0.94 + 0.06 * ease_out_back(p)
                else:
                    scale = 0.55 + 0.45 * ease_out_back(p); alpha = min(1, p * 3)
            elif self.anim == "zoom":
                scale = 1.6 - 0.6 * ease_out_cubic(p); alpha = min(1, p * 2.5)
            elif self.anim == "swing":  # slide in from the right with an overshoot settle
                x += int((1 - ease_out_back(p)) * (W * 0.9)); alpha = min(1, p * 2.5)
            elif self.anim == "rise":  # rise from below with a scale overshoot
                y += int((1 - e) * 220); scale = 0.8 + 0.2 * ease_out_back(p); alpha = min(1, p * 2.5)
            elif self.anim == "none":
                pass
            elif self.anim == "fade":
                alpha = e
        if self.t_out is not None and t >= self.t_out:
            q = clamp01((t - self.t_out) / self.out_dur)
            e = ease_in_out(q)
            if self.out == "fade":
                alpha *= 1 - e
            elif self.out == "slide_out_left":
                x -= int(e * W); alpha *= 1 - e * 0.5
            elif self.out == "shrink":
                scale *= 1 - 0.3 * e; alpha *= 1 - e
        if self.pulse and p >= 1:
            scale *= 1 + self.pulse * (0.5 - 0.5 * math.cos((t - self.t_in) * 2 * math.pi / 1.1))
        for s0, sd, amp in self.shake:
            if s0 <= t < s0 + sd:
                k = int((t - s0) * 60)
                x += int(amp * (1 if k % 2 else -1) * (1 - (t - s0) / sd))
                y += int(amp * 0.5 * (1 if k % 3 else -1) * (1 - (t - s0) / sd))
        if alpha <= 0.003:
            return
        if abs(scale - 1) > 0.002:
            w0, h0 = sp.size
            nw, nh = max(1, int(w0 * scale)), max(1, int(h0 * scale))
            cx, cy = x + w0 / 2, y + h0 / 2
            sp = sp.resize((nw, nh), Image.BICUBIC)
            x, y = int(cx - nw / 2), int(cy - nh / 2)
        if alpha < 0.999:
            a = sp.getchannel("A").point(lambda v, al=alpha: int(v * al))
            frame.paste(sp, (int(x), int(y)), a)
        else:
            frame.paste(sp, (int(x), int(y)), sp)


def post_fx(fr, t, cam=None, flash=None):
    """Global camera push (cam(t)->scale>=1) and white flash (flash(t)->0..1)."""
    if cam:
        z = cam(t)
        if z > 1.002:
            cw, ch = int(W / z), int(H / z)
            x0, y0 = (W - cw) // 2, (H - ch) // 2
            fr = fr.crop((x0, y0, x0 + cw, y0 + ch)).resize((W, H), Image.BILINEAR)
    if flash:
        a = flash(t)
        if a > 0.01:
            fr = Image.blend(fr, Image.new("RGB", (W, H), (255, 255, 255)), min(0.8, a))
    return fr


def render_video(bg, elements, duration, audio_path, out_path, bitrate="8M", log=None, cam=None, flash=None):
    n = int(round(duration * FPS))
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
           "-r", str(FPS), "-i", "-", "-i", audio_path,
           "-vf", "scale=out_color_matrix=bt709:out_range=tv:flags=accurate_rnd+full_chroma_int,format=yuv420p",
           "-c:v", "libx264", "-preset", "veryfast", "-tune", "animation", "-profile:v", "high",
           "-level", "4.2", "-pix_fmt", "yuv420p", "-b:v", bitrate, "-maxrate", "12M", "-bufsize", "16M",
           "-g", str(FPS * 2), "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
           "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
           "-shortest", "-movflags", "+faststart", out_path]
    import shutil, tempfile
    tmp_out = os.path.join(tempfile.gettempdir(), "clip_%d.mp4" % os.getpid())
    cmd[-1] = tmp_out  # encode on local disk (faststart rewrite fails on the network FS)
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for i in range(n):
        t = i / FPS
        fr = bg.frame(t)
        for el in elements:
            el.draw(fr, t)
        fr = post_fx(fr, t, cam, flash)
        proc.stdin.write(fr.tobytes())
    proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError("ffmpeg encode failed")
    shutil.copyfile(tmp_out, out_path)
    os.remove(tmp_out)
    return n


def render_still(bg, elements, t, cam=None, flash=None):
    fr = bg.frame(t)
    for el in elements:
        el.draw(fr, t)
    return post_fx(fr, t, cam, flash)
