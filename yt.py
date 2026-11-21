"""YouTube Shorts for the Job Notification & Quiz channels (v2: voiceover + motion graphics).

    python yt.py auto|quiz|vacancy|breaking [--dry]

auto     : the IST slot that is due now (SLOTS below), each slot runs once a day
quiz     : PYQ quiz Short (hook + question, A-D pop, 5-1 countdown, reveal, व्याख्या, loop) ~22 s
vacancy  : newest recruitment from our Telegram channels (15-22 s), falls back to quiz when none is new
breaking : only 1000+ post recruitments, posted the moment they appear (max 3/day)

Upload: YouTube Data API v3 videos.insert (resumable) + thumbnails.set, OAuth refresh token.
Secrets: YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN. Missing => dry run (renders, no upload).
State: state/yt.json (committed back by the workflow).
"""
import html, json, os, random, re, sys, time, urllib.parse, urllib.request
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "shorts"))
OUT = os.path.join(HERE, "out")
STATE = os.path.join(HERE, "state", "yt.json")
IST = timezone(timedelta(hours=5, minutes=30))
DRY = "--dry" in sys.argv or not os.environ.get("YT_REFRESH_TOKEN")
TG_HUB = "https://tg.jobnotificationquiz.workers.dev/tg"
CH = {"ssc": "SSC_Job_Alert", "railway": "Railway_Job_Notification_Quiz",
      "bihar": "Bihar_Job_Notification_Quiz", "army": "Army_Agniveer_Bharti_Alert"}
CH_NAME = {"ssc": "SSC", "railway": "Railway", "bihar": "Bihar", "army": "Army/Agniveer"}
ORDER = ["ssc", "railway", "bihar", "army"]
# IST slot -> mode. 9 Shorts a day + breaking. vacancy falls back to quiz when nothing new.
SLOTS = [("07:30", "quiz"), ("09:00", "quiz"), ("10:30", "vacancy"), ("12:00", "quiz"), ("13:30", "quiz"),
         ("15:30", "vacancy"), ("17:30", "quiz"), ("19:00", "quiz"), ("20:30", "vacancy")]
# Long job-detail videos are NOT on a clock: made and uploaded as soon as a big recruitment appears.
LONG_MIN = 500      # "bada bharti": at least this many posts
LONG_CAP = 4        # safety cap per day (Actions minutes)
LONG_STATE = os.path.join(HERE, "state", "long.json")
# Daily long practice sets (16:9), each published at its own IST time (rendered ~2 h early, scheduled with publishAt).
# Worker dispatches mode set_<theme> / ca at SET_SLOTS minus 2 h.  Each exam has its own playlist.
SET_N = 10
SET_SLOTS = {"ca": "07:00", "army": "10:00", "bihar": "12:00", "railway": "16:00", "ssc": "19:00"}
SET_NAMES = {"ssc": ("SSC", "SSC GD, CGL, MTS, CHSL"), "railway": ("Railway", "RRB NTPC, Group D, ALP"),
             "bihar": ("Bihar", "Bihar Police, BPSC, BSSC"), "army": ("Agniveer", "Army Agniveer GD")}
PLAYLISTS = {"ssc": "SSC GK Practice Set | SSC GD, CGL, MTS PYQ", "railway": "Railway GK Practice Set | RRB NTPC, Group D PYQ",
             "bihar": "Bihar GK Practice Set | Bihar Police, BPSC PYQ", "army": "Agniveer GK Practice Set | Army GD PYQ",
             "ca": "Daily Current Affairs Quiz | रोज़ का करेंट अफेयर्स"}
DAILY_CAP, BREAKING_CAP = 10, 3
LIMIT_COOLDOWN_H = 1  # YouTube "uploadLimitExceeded": pause uploads this long instead of retry + issue spam


class UploadLimit(Exception):
    pass


def upload_blocked(s):
    b = s.get("upload_block_until")
    try:
        return bool(b) and datetime.fromisoformat(b) > now()
    except Exception:
        return False
TAGS = {"ssc": ["SSC", "SSC GD", "SSC CGL", "SSC MTS", "SSC CHSL", "ssc gk", "ssc pyq", "ssc gd gk", "एसएससी"],
        "railway": ["Railway", "RRB NTPC", "RRB Group D", "RRB ALP", "railway gk", "railway bharti", "rrb gk", "रेलवे"],
        "bihar": ["Bihar", "Bihar Police", "BPSC", "BSSC", "bihar gk", "bihar police gk", "bihar sarkari naukri", "बिहार"],
        "army": ["Agniveer", "Army bharti", "Agniveer GD", "Indian Army", "agniveer gk", "army gk", "अग्निवीर"]}
COMMON_TAGS = ["sarkari naukri", "govt jobs", "gk quiz", "gk in hindi", "gk questions", "general knowledge",
               "previous year questions", "sarkari result", "job notification", "सरकारी नौकरी", "जीके क्विज़",
               "current affairs hindi", "competitive exam", "shorts"]
HASHTAGS = {"ssc": ["#SSC", "#SSCGD", "#SSCCGL"], "railway": ["#Railway", "#RRBNTPC", "#RRBGroupD"],
            "bihar": ["#BiharPolice", "#BPSC", "#BiharGK"], "army": ["#Agniveer", "#IndianArmy", "#ArmyBharti"]}


def tags_for(theme, extra=()):
    """Specific first, then broad; deduped; within YouTube's 500-char tag budget."""
    import datetime as _dt
    yr = _dt.datetime.now().year
    pool = list(extra) + TAGS.get(theme, []) + COMMON_TAGS + [f"vacancy {yr}", f"bharti {yr}"]
    out, seen, n = [], set(), 0
    for t in pool:
        k = t.lower().strip()
        if not k or k in seen:
            continue
        cost = len(t) + (2 if " " in t else 0) + 1
        if n + cost > 480:
            break
        out.append(t); seen.add(k); n += cost
    return out


def hashtag_line(theme, kind):
    """3 most relevant first (YouTube shows them above the title), max 7 total."""
    first = {"quiz": "#GKQuiz", "vacancy": "#SarkariNaukri", "breaking": "#SarkariNaukri"}.get(kind, "#SarkariNaukri")
    tags = ([] if kind == "long" else ["#Shorts"]) + [HASHTAGS.get(theme, ["#SarkariNaukri"])[0], first] + HASHTAGS.get(theme, [])[1:] + \
        ["#GovtJobs", "#GKinHindi"]
    out = []
    for t in tags:
        if t not in out:
            out.append(t)
    return " ".join(out[:7])


QUIZ_HOOKS = [  # (on-screen, spoken) - truthful, no invented numbers
    ("5 सेकंड में जवाब दो!", "पाँच सेकंड में जवाब दो!"),
    ("आपका जवाब क्या है?", "बताइए, आपका जवाब क्या है?"),
    ("पिछली परीक्षा का असली सवाल", "पिछली परीक्षा का असली सवाल!"),
    ("सही जवाब दो, तो मान जाएँ!", "सही जवाब दो, तो मान जाएँ!"),
    ("परीक्षा में आया था ये सवाल", "परीक्षा में आया था ये सवाल!"),
    ("कमेंट में जवाब लिखो 👇", "कमेंट में जवाब लिखो!"),
    ("तैयारी चेक करो!", "अपनी तैयारी चेक करो!"),
]
CTA = [("हर नोटिफिकेशन 5–8 मिनट में Telegram पर", "हर नोटिफिकेशन, पाँच से आठ मिनट में, हमारे टेलीग्राम पर!"),
       ("नोटिफिकेशन आते ही Telegram पर, सबसे पहले", "नोटिफिकेशन आते ही, सबसे पहले, हमारे टेलीग्राम पर!"),
       ("रोज़ के सवाल + भर्ती अपडेट Telegram पर", "रोज़ के सवाल और हर भर्ती, पाँच से आठ मिनट में टेलीग्राम पर!")]


def now():
    return datetime.now(IST)


def load():
    try:
        s = json.load(open(STATE))
    except Exception:
        s = {}
    for k, v in (("posted", []), ("used_q", []), ("fps", []), ("log", []), ("slots", []), ("n", 0)):
        s.setdefault(k, v)
    return s


def save(s):
    s["posted"] = s["posted"][-600:]; s["log"] = s["log"][-400:]; s["fps"] = s["fps"][-200:]
    s["slots"] = s["slots"][-60:]
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(s, open(STATE + ".tmp", "w"), ensure_ascii=False, indent=1)
    os.replace(STATE + ".tmp", STATE)


def today_count(s, mode=None):
    d = now().strftime("%Y-%m-%d")
    return len([e for e in s["log"] if e[0].startswith(d) and (e[1] == mode if mode else e[1] not in ("long", "limit"))
                and e[3] != "dryrun"])


def due_slot(s, window_min=100):
    """The latest slot whose time has passed (within window) and that has not run today."""
    n = now()
    best = None
    for hm, mode in SLOTS:
        h, m = map(int, hm.split(":"))
        t = n.replace(hour=h, minute=m, second=0, microsecond=0)
        key = t.strftime("%Y-%m-%d ") + hm
        if t <= n + timedelta(minutes=3) and (n - t).total_seconds() <= window_min * 60 and key not in s["slots"]:
            best = (key, mode)
    return best


# ---------------- sources: our own public Telegram channels ----------------
def scrape(theme):
    url = f"https://t.me/s/{CH[theme]}"
    h = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=30).read().decode()
    items = []
    for blk in re.split(r'<div class="tgme_widget_message_wrap', h)[1:]:
        m = re.search(r'js-message_text" dir="auto">(.*?)</div>', blk, re.S)
        link = re.search(r'tgme_widget_message_date" href="([^"]+)"', blk)
        dt = re.search(r'<time datetime="([^"]+)"', blk)
        if not m:
            continue
        t = html.unescape(re.sub(r"<br\s*/?>", "\n", m.group(1)))
        t = re.sub(r"<[^>]+>", "", t)
        if "NEW VACANCY" not in t and "नई भर्ती" not in t:
            continue
        items.append({"text": t, "key": link.group(1) if link else t[:60], "date": dt.group(1) if dt else ""})
    return items


def fetch_post(key):
    """One Telegram post by its link (embed page), same shape as scrape() items. Used by dry tests."""
    h = urllib.request.urlopen(urllib.request.Request(key + "?embed=1", headers={"User-Agent": "Mozilla/5.0"}),
                               timeout=30).read().decode()
    m = re.search(r'js-message_text" dir="auto">(.*?)</div>', h, re.S)
    dt = re.search(r'<time datetime="([^"]+)"', h)
    t = html.unescape(re.sub(r"<br\s*/?>", "\n", m.group(1))) if m else ""
    return {"text": re.sub(r"<[^>]+>", "", t), "key": key, "date": dt.group(1) if dt else ""}


def field(t, *labels):
    for lab in labels:
        m = re.search(lab + r"[^:\n]*:\s*([^\n]+)", t)
        if m:
            return m.group(1).strip()
    return ""


def vac_title(t):
    m = re.search(r"📌\s*([^\n]+)", t)
    title = m.group(1).strip() if m else t.split("\n")[0]
    return clean_title(title)[:160]


_EMO = re.compile(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D\u25A0-\u25FF]")


def clean_title(x):
    """Keep only the title itself: cut at the next emoji/field marker, drop symbols and half words."""
    m = _EMO.search(x)
    if m and m.start() > 15:
        x = x[:m.start()]
    x = _EMO.sub("", x)
    x = re.split(r"\s+(?:सूचना तिथि|अंतिम तिथि|Notification Date|Last Date)\b", x)[0]
    return re.sub(r"\s{2,}", " ", x).strip(" -–|/")


def short_title(title):
    x = re.sub(r"\s*[–-]?\s*\b(Apply Online|Online Form|Download)\b.*$", "", clean_title(title), flags=re.I)
    x = re.sub(r"\b(Notification Out|Reopened)\b", "", x, flags=re.I)
    return re.sub(r"\s{2,}", " ", x).strip(" -–")[:90]


def post_count(t, title):
    tot = field(t, "Total Vacancy", "Total Post", "कुल पद")
    if tot and re.search(r"\d", tot):
        return int(re.sub(r"\D", "", tot)[:7])
    m = re.search(r"(?:for|of)\s+(\d[\d,]*)\s+", title, re.I) or re.search(r"(\d[\d,]{2,})\s+(?:Posts?|पद|Vacanc)", t, re.I)
    return int(re.sub(r"\D", "", m.group(1))[:7]) if m else 0


def vac_facts(t):
    f = []
    last = field(t, r"Last Date \(Apply\)", "आवेदन की अंतिम तिथि", "Last Date", "अंतिम तिथि")
    if last:
        f.append(("अंतिम तिथि", re.sub(r"\s*\(.*", "", last)[:26]))
    age = field(t, "Age Limit", "आयु सीमा")
    if age and re.search(r"\d", age):
        f.append(("आयु सीमा", age.replace("Years", "वर्ष").replace("Year", "वर्ष")[:26]))
    fee = re.search(r"(?:General|Gen|UR)[^:\n]*:\s*(?:Rs\.?\s*)?(\d[\d,]*)", t)
    if fee:
        f.append(("आवेदन शुल्क (सामान्य)", f"₹{fee.group(1)}"))
    elig = field(t, "Qualification", "योग्यता", "Eligibility")
    if elig and len(f) < 3:
        f.append(("योग्यता", elig[:40]))
    site = field(t, "Official Website")
    if site and len(f) < 3:
        f.append(("ऑफिशियल वेबसाइट", site[:30]))
    if len(f) < 3 and ("आवेदन जारी" in t or "chalu" in t):
        f.append(("स्थिति", "ऑनलाइन आवेदन जारी"))
    nd = field(t, "Notice Date", "सूचना तिथि")
    if len(f) < 3 and nd:
        f.append(("सूचना तिथि", nd.split(",")[0][:24]))
    return f[:3]


def days_left(t):
    m = re.search(r"(\d+)\s*दिन शेष", t) or re.search(r"Sirf (\d+) din", t)
    return int(m.group(1)) if m else None


def fingerprint(title, n):
    w = sorted(set(re.findall(r"[a-z0-9]{3,}", title.lower())) - {"recruitment", "online", "apply", "form", "posts",
                                                                   "post", "for", "and", "the", "2026", "2027", "more"})
    return {"n": n, "w": w[:10], "t": now().strftime("%Y-%m-%d")}


def is_dupe(s, fp):
    cut = (now() - timedelta(days=10)).strftime("%Y-%m-%d")
    for o in s["fps"]:
        if o["t"] < cut:
            continue
        if fp["n"] >= 50 and o["n"] == fp["n"]:
            return True
        a, b = set(fp["w"]), set(o["w"])
        if a and b and len(a & b) / min(len(a), len(b)) >= 0.75:
            return True
    return False


def num_hi(n):
    return f"{n:,}".replace(",", ",")


# ---------------- text ----------------
def roman_exam(q, theme):
    src = q.get("src") or ""
    name = re.sub(r"\s*[·,].*", "", src).strip()
    name = re.sub(r"\s+(19|20)\d\d$", "", name)
    yr = re.search(r"(20\d\d)", src)
    if not name:
        name = {"ssc": "SSC", "railway": "RRB", "bihar": "Bihar Police", "army": "Agniveer GD"}[theme]
    return name, (yr.group(1) if yr else "")


def say_exam(name):
    m = {"SSC": "एसएससी", "CGL": "सीजीएल", "CHSL": "सीएचएसएल", "MTS": "एमटीएस", "GD": "जीडी", "RRB": "आरआरबी",
         "NTPC": "एनटीपीसी", "ALP": "एएलपी", "JE": "जेई", "CPO": "सीपीओ", "Constable": "कांस्टेबल",
         "Group": "ग्रुप", "D": "डी", "Bihar": "बिहार", "Police": "पुलिस", "Agniveer": "अग्निवीर"}
    return " ".join(m.get(w, w) for w in name.split())


def description(theme, lines, channel_url=None, kind="quiz"):
    links = [f"SSC: https://t.me/{CH['ssc']}", f"Railway: https://t.me/{CH['railway']}",
             f"Bihar: https://t.me/{CH['bihar']}", f"Army/Agniveer: https://t.me/{CH['army']}"]
    out = lines + ["", "⚡ हर नई भर्ती का नोटिफिकेशन आते ही 5–8 मिनट में हमारे Telegram पर:", TG_HUB, ""] + links
    out += ["", "📸 Instagram: https://www.instagram.com/sarkari_job_notification_quiz/",
            "📘 Facebook: https://www.facebook.com/profile.php?id=61596323014208"]
    if channel_url:
        out += ["", "▶️ रोज़ नए सवाल और भर्ती अपडेट, Subscribe करें:", channel_url + "?sub_confirmation=1"]
    out += ["", hashtag_line(theme, kind)]
    return "\n".join(out)


# ---------------- YouTube API ----------------
_TOK = {}


def access_token():
    if "t" in _TOK:
        return _TOK["t"]
    data = urllib.parse.urlencode({"client_id": os.environ["YT_CLIENT_ID"], "client_secret": os.environ["YT_CLIENT_SECRET"],
                                   "refresh_token": os.environ["YT_REFRESH_TOKEN"], "grant_type": "refresh_token"}).encode()
    with urllib.request.urlopen("https://oauth2.googleapis.com/token", data, timeout=30) as r:
        _TOK["t"] = json.load(r)["access_token"]
    return _TOK["t"]


def api(method, url, body=None, ctype="application/json; charset=UTF-8", timeout=60):
    req = urllib.request.Request(url, data=body, method=method,
                                 headers={"Authorization": f"Bearer {access_token()}", "Content-Type": ctype})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        return json.loads(raw) if raw else {}


def channel_url(s):
    if DRY:
        return None
    if not s.get("channel_url"):
        try:
            it = api("GET", "https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true")["items"][0]
            cu = it["snippet"].get("customUrl")
            s["channel_url"] = f"https://www.youtube.com/{cu}" if cu else f"https://www.youtube.com/channel/{it['id']}"
        except Exception as e:
            print("channel lookup failed:", str(e)[:200])
            return None
    return s["channel_url"]


def upload(mp4, thumb, title, desc, tags, publish_at=None):
    title = title[:100]
    if DRY:
        print("DRY RUN upload:", title, "\n", desc[:500]); return "dryrun"
    meta = {"snippet": {"title": title, "description": desc[:4900], "tags": tags, "categoryId": "27",
                        "defaultLanguage": "hi", "defaultAudioLanguage": "hi"},
            "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False,
                       "containsSyntheticMedia": os.environ.get("SYNTHETIC_FLAG", "1") == "1"}}
    if publish_at and publish_at > datetime.now(timezone.utc) + timedelta(minutes=10):  # scheduled premiere time
        meta["status"]["privacyStatus"] = "private"
        meta["status"]["publishAt"] = publish_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    size = os.path.getsize(mp4)
    vid = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(
                "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
                data=json.dumps(meta).encode(), method="POST",
                headers={"Authorization": f"Bearer {access_token()}", "Content-Type": "application/json; charset=UTF-8",
                         "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)})
            with urllib.request.urlopen(req, timeout=60) as r:
                loc = r.headers["Location"]
            req = urllib.request.Request(loc, data=open(mp4, "rb").read(), method="PUT",
                                         headers={"Authorization": f"Bearer {access_token()}", "Content-Type": "video/mp4"})
            with urllib.request.urlopen(req, timeout=900) as r:
                vid = json.load(r)["id"]
            break
        except urllib.error.HTTPError as e:
            body = e.read().decode()[:500]; print("upload err", e.code, body)
            if "uploadLimitExceeded" in body:
                raise UploadLimit(body)
            if e.code < 500 and e.code != 429:
                raise
        except Exception as e:
            print("upload err", str(e)[:200])
        time.sleep(15 * (attempt + 1))
    if not vid:
        raise RuntimeError("upload failed")
    print("uploaded https://youtube.com/shorts/" + vid)
    try:  # custom thumbnail; skipped quietly if the channel can't set one yet
        api("POST", f"https://www.googleapis.com/upload/youtube/v3/thumbnails/set?videoId={vid}",
            open(thumb, "rb").read(), ctype="image/jpeg")
        print("thumbnail set")
    except Exception as e:
        print("thumbnail skipped:", str(e)[:200])
    return vid


# ---------------- modes ----------------
def render(kind, d, s):
    import make
    os.makedirs(OUT, exist_ok=True)
    out = os.path.join(OUT, "short.mp4")
    lay = s["n"] % len(make.LAYOUTS)
    info = make.make(kind, d, out, layout=lay, music_i=s["n"] // len(make.LAYOUTS) + s["n"])
    if not info.get("voice"):
        print("WARNING: rendered without voice")
    return info


def pick_q(s, bank, theme):
    hin = lambda x: bool(re.search("[\u0900-\u097F]", x["q"])) and len(x["q"]) <= 135 and x.get("e") and max(len(o) for o in x["o"]) <= 45 \
        and len(short_expl(x["e"])) <= 150
    used = set(s["used_q"])
    pool = [i for i, x in enumerate(bank) if x.get("exam") == theme and hin(x) and i not in used] or \
           [i for i, x in enumerate(bank) if hin(x) and i not in used]
    if pool:
        return random.choice(pool)
    raise RuntimeError("question bank used up: add new PYQs to bank/quiz.json")  # never repeat a question


def short_expl(e, limit=110):
    """First sentence(s) of the explanation (cut at a clause, never mid-word), so the Short stays short."""
    parts = [x.strip() for x in re.split(r"(?<=[।!?])\s+|(?<=\.)\s+(?=[^\d])", e.strip()) if x.strip()]
    out = ""
    for x in parts:
        if out and len(out) + len(x) > limit:
            break
        out = (out + " " + x).strip()
    if len(out) > limit + 20:  # one long sentence: keep whole clauses up to the limit
        cl = re.split(r"(?<=,)\s+", out)
        cut = ""
        for c in cl:
            if cut and len(cut) + len(c) > limit:
                break
            cut = (cut + " " + c).strip()
        out = cut.rstrip(",") + ("।" if not cut.endswith(("।", ".")) else "")
    return out


def do_quiz(s):
    bank = json.load(open(os.path.join(HERE, "bank", "quiz.json")))
    theme = ORDER[s["n"] % 4]
    i = pick_q(s, bank, theme); q = bank[i]
    theme = q.get("exam") if q.get("exam") in CH else theme
    name, yr = roman_exam(q, theme)
    hk, hk_say = QUIZ_HOOKS[s["n"] % len(QUIZ_HOOKS)]
    cta = CTA[s["n"] % len(CTA)]
    d = {"channel": theme, "badge": f"{name} {yr} PYQ".replace("  ", " "), "hook": hk, "hook_say": hk_say,
         "thumb": f"{name} का सवाल" if len(name) < 16 else hk,
         "question": q["q"], "options": q["o"], "answer": q["a"], "explanation": short_expl(q["e"]),
         "source_line": f"स्रोत: {q['src']}" if q.get("src") else "", "cta_cap": cta[0], "cta_say": cta[1]}
    info = render("quiz", d, s)
    title = f"{name} {yr} GK Quiz | {hk.replace(' 👇', '')} 🧠 #shorts {HASHTAGS[theme][0]}".replace("  ", " ")
    if len(title) > 95:
        title = f"{name} GK Quiz | {hk.replace(' 👇', '')} #shorts"
    desc = description(theme, [f"🧠 {q['q']}", ""] + [f"{'ABCD'[k]}) {o}" for k, o in enumerate(q["o"])] +
                       ["", "👇 अपना जवाब कमेंट में लिखें, सही जवाब वीडियो के आखिर में।", "",
                        f"📚 परीक्षा: {q.get('src') or name}"], channel_url(s))
    vid = upload(info["out"], info["thumb"], title, desc, tags_for(theme, [f"{name} pyq", f"{name} gk", name, "PYQ"]))
    s["used_q"].append(i)
    return ["quiz", theme, vid, info["layout"]]


def do_vacancy(s, min_posts=0):
    start = s["n"] % 4
    for k in range(4):
        theme = ORDER[(start + k) % 4]
        try:
            items = scrape(theme)
        except Exception as e:
            print("scrape fail", theme, e); continue
        fresh = [it for it in reversed(items) if it["key"] not in s["posted"]]
        if not min_posts:  # only recent posts (last 36 h) for the normal slot
            cut = (datetime.now(timezone.utc) - timedelta(hours=36)).isoformat()
            fresh = [it for it in fresh if not it["date"] or it["date"] >= cut[:19]]
        fresh.sort(key=lambda it: post_count(it["text"], vac_title(it["text"])), reverse=True)
        for it in fresh:
            title = vac_title(it["text"]); n = post_count(it["text"], title)
            if min_posts and n < min_posts:
                continue
            fp = fingerprint(title, n)
            if is_dupe(s, fp):
                s["posted"].append(it["key"]); continue
            st = short_title(title)
            facts = vac_facts(it["text"])
            dl = days_left(it["text"])
            big = n >= 1000
            tip = (f"सिर्फ {dl} दिन बाकी, आखिरी दिन का इंतज़ार मत करो!" if dl and dl <= 20 else
                   "फॉर्म भरने से पहले योग्यता और आयु सीमा नोटिफिकेशन से ज़रूर मिलाएँ।")
            cta = CTA[s["n"] % len(CTA)]
            org = field(it["text"], "🏛") or ""
            d = {"channel": theme, "badge": "🚨 बड़ी भर्ती".replace("🚨 ", "") if big else "नई भर्ती 2026",
                 "hook": f"{num_hi(n)} पदों पर भर्ती!" if n else "नई सरकारी भर्ती आ गई!",
                 "hook_say": (f"{num_hi(n)} पदों पर नई भर्ती!" if n else "नई सरकारी भर्ती आ गई!"),
                 "post": st, "org": org, "vacancies": num_hi(n) if n else "", "facts": facts, "tip": tip,
                 "cta_cap": cta[0], "cta_say": "पूरी डिटेल और आवेदन लिंक, " + cta[1]}
            if not d["vacancies"]:
                d.pop("vacancies")
            info = render("vacancy", d, s)
            ttl = (f"{st[:52]} | {num_hi(n)} पदों पर {'बड़ी' if big else 'नई'} भर्ती #shorts" if n else f"{st[:60]} | नई सरकारी भर्ती #shorts")
            desc = description(theme, [("🚨 बड़ी भर्ती: " if big else "📢 नई भर्ती: ") + title, ""] +
                               [f"✅ {a}: {b}" for a, b in facts] +
                               ["", "आवेदन से पहले ऑफिशियल नोटिफिकेशन ज़रूर पढ़ें।",
                                f"पूरी डिटेल, PDF और आवेदन लिंक: https://t.me/{CH[theme]}"], channel_url(s), kind="vacancy")
            vid = upload(info["out"], info["thumb"], ttl, desc, tags_for(theme, ["new vacancy", "नई भर्ती", "latest vacancy"]))
            s["posted"].append(it["key"]); s["fps"].append(fp)
            return ["breaking" if min_posts else "vacancy", theme, vid, info["layout"]]
    print("no new vacancy" + (" (1000+)" if min_posts else ""))
    return None


def load_long():
    try:
        L = json.load(open(LONG_STATE))
    except Exception:
        L = {}
    L.setdefault("posted", []); L.setdefault("log", [])
    return L


def save_long(L):
    L["posted"] = L["posted"][-300:]; L["log"] = L["log"][-200:]
    os.makedirs(os.path.dirname(LONG_STATE), exist_ok=True)
    json.dump(L, open(LONG_STATE, "w"), ensure_ascii=False, indent=1)


def long_today(L):
    d = now().strftime("%Y-%m-%d")
    return len([e for e in L["log"] if e[0].startswith(d) and e[1] == "long" and e[3] != "dryrun"])


def pick_long(L, parse=True, relax=False):
    """Fresh (72 h) big recruitments not yet made into a long video, biggest first.
    relax=True (dry test runs only): also consider already-made / older posts."""
    if parse:
        import explainer
    cands = []
    for theme in ORDER:
        try:
            items = scrape(theme)
        except Exception as e:
            print("scrape fail", theme, e); continue
        cut = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()[:19]
        for it in items:
            if not relax and (it["key"] in L["posted"] or (it["date"] and it["date"] < cut)):
                continue
            title = vac_title(it["text"]); n = post_count(it["text"], title)
            if n < LONG_MIN:
                continue
            if not parse:
                return [(n, 0, it, None, title)]
            d = explainer.parse_post(it["text"], short_title(title), n)
            richness = len(d["fees"]) + len(d["dates"]) + len(d["age"]) + bool(d["qual"]) + bool(d["salary"])
            if richness < 4:  # not enough real detail for a full video
                continue
            d["channel"] = theme
            cands.append((n, richness, it, d, title))
    cands.sort(key=lambda c: (c[0], c[1]), reverse=True)
    return cands


def do_long(s):
    """One long 16:9 video with the full details of one fresh big recruitment, uploaded right away."""
    import explainer
    L = load_long()
    if long_today(L) >= LONG_CAP and os.environ.get("FORCE") != "1":
        print("long cap"); return None
    cands = pick_long(L)
    if not cands and DRY and os.environ.get("FORCE") == "1":
        print("dry test: no fresh big job, using an older one"); cands = pick_long(L, relax=True)
        for key in ([] if cands else L["posted"][::-1][:3]):  # feed pages are short: re-fetch past long posts
            import explainer
            it = fetch_post(key); title = vac_title(it["text"]); n = post_count(it["text"], title)
            if n:
                d = explainer.parse_post(it["text"], short_title(title), n)
                d["channel"] = next((th for th in ORDER if CH[th] in key), "ssc")
                cands = [(n, 0, it, d, title)]; print("dry test: re-using", key); break
    if not cands:
        print("no big job with enough detail for a long video"); return None
    n, _, it, d, full = cands[0]
    os.makedirs(OUT, exist_ok=True)
    out = os.path.join(OUT, "long.mp4")
    info = explainer.build(d, out, os.path.join(OUT, "long_thumb.jpg"), voice="male", music_i=s["n"])
    st = d["title"]
    last = next((b for a, b in d["dates"] if "आवेदन की अंतिम" in a), "")
    ttl = f"{st} | {num_hi(n)} पद | योग्यता, आयु, फीस, अंतिम तिथि – पूरी जानकारी" if n else f"{st} – पूरी जानकारी"
    if len(ttl) > 100:
        ttl = f"{st[:50]} | {num_hi(n)} पद – पूरी जानकारी"
    theme = d["channel"]
    lines = [f"{full} – पूरी जानकारी हिंदी में", ""]
    if n:
        lines.append(f"✅ कुल पद: {num_hi(n)}")
    if d["salary"]:
        lines.append(f"✅ वेतन: {d['salary']}")
    if d["qual"]:
        lines.append(f"✅ योग्यता: {d['qual']}")
    for a in d["age"]:
        lines.append(f"✅ {a}")
    if d["fees"]:
        lines += ["", "💳 आवेदन शुल्क:"] + [f"• {a}: {b}" for a, b in d["fees"]]
    if d["dates"]:
        lines += ["", "📅 महत्वपूर्ण तिथियाँ:"] + [f"• {a}: {b}" for a, b in d["dates"]]
    lines += ["", "आवेदन से पहले ऑफिशियल नोटिफिकेशन ज़रूर पढ़ें।", f"पूरी डिटेल, PDF और आवेदन लिंक: https://t.me/{CH[theme]}"]
    desc = description(theme, lines, channel_url(s), kind="long")
    tags = tags_for(theme, [st, f"{st} full details", "भर्ती पूरी जानकारी", "new vacancy 2026", "sarkari naukri 2026"])
    tags = [t for t in tags if t.lower() != "shorts"]
    vid = upload(info["out"], info["thumb"], ttl, desc, tags)
    L["posted"].append(it["key"])
    L["log"].append([now().strftime("%Y-%m-%d %H:%M"), "long", theme, vid, it["key"], n])
    if not DRY:
        save_long(L)
    return ["long", theme, vid, f"{info['duration_s']}s"]


def set_theme(mode):
    """set (rotating, old) | set_ssc | set_railway | set_bihar | set_army | ca"""
    if mode == "ca":
        return "ca"
    th = mode[4:] if mode.startswith("set_") else ""
    if th in SET_NAMES:
        return th
    # plain "set": the next exam whose slot is still ahead today, else ssc
    hm = now().strftime("%H:%M")
    for th in ("army", "bihar", "railway", "ssc"):
        if SET_SLOTS[th] > hm:
            return th
    return "ssc"


def set_due(L, theme):
    d = now().strftime("%Y-%m-%d")
    done = any(e[0].startswith(d) and e[1] == "set" and e[2] == theme and e[3] != "dryrun" for e in L["log"])
    if done:
        return False
    h, m = map(int, SET_SLOTS[theme].split(":"))
    start = now().replace(hour=h, minute=m, second=0, microsecond=0) - timedelta(hours=2, minutes=30)
    return start <= now() < now().replace(hour=23, minute=0)


def slot_time(theme):
    h, m = map(int, SET_SLOTS[theme].split(":"))
    return now().replace(hour=h, minute=m, second=0, microsecond=0)


def playlist_add(L, theme, vid):
    """Exam-wise playlists (created once, ids kept in state/long.json). Never blocks the upload."""
    if DRY or not vid or vid == "dryrun":
        return
    try:
        pl = L.setdefault("playlists", {})
        if theme not in pl:
            r = api("POST", "https://www.googleapis.com/youtube/v3/playlists?part=snippet,status", json.dumps({
                "snippet": {"title": PLAYLISTS[theme], "defaultLanguage": "hi",
                            "description": "रोज़ नया प्रैक्टिस सेट: पिछली परीक्षाओं के असली सवाल, जवाब और व्याख्या के साथ। "
                                           f"हर नई भर्ती का नोटिफिकेशन 5–8 मिनट में Telegram पर: {TG_HUB}"},
                "status": {"privacyStatus": "public"}}).encode())
            pl[theme] = r["id"]; print("playlist created", theme, r["id"])
        api("POST", "https://www.googleapis.com/youtube/v3/playlistItems?part=snippet", json.dumps({
            "snippet": {"playlistId": pl[theme], "position": 0,
                        "resourceId": {"kind": "youtube#video", "videoId": vid}}}).encode())
        print("added to playlist", theme)
    except Exception as e:
        body = e.read().decode()[:300] if hasattr(e, "read") else str(e)[:300]
        print("playlist skipped:", body)
        L.setdefault("playlist_err", []).append([now().strftime("%Y-%m-%d %H:%M"), theme, body[:120]])
        L["playlist_err"] = L["playlist_err"][-20:]


def ca_questions(L):
    """Current-affairs bank (bank/ca.json, filled weekly with checked facts): newest unused first."""
    try:
        bank = json.load(open(os.path.join(HERE, "bank", "ca.json"), encoding="utf-8"))
    except Exception:
        bank = []
    used = set(L.setdefault("ca_used", []))
    cut = (now() - timedelta(days=45)).strftime("%Y-%m-%d")
    pool = [x for x in bank if x["q"] not in used and x.get("date", "9999") >= cut]
    pool.sort(key=lambda x: x.get("date", ""), reverse=True)
    return pool[:SET_N]


def do_set(s, theme):
    """One long practice set of SET_N questions (never repeats a question across sets), scheduled at its slot."""
    import quizset
    L = load_long()
    L.setdefault("set_used", []); L.setdefault("set_no", {})
    if isinstance(L["set_no"], int):
        L["set_no"] = {}
    if not set_due(L, theme) and os.environ.get("FORCE") != "1":
        print("set", theme, "already done today / not time yet"); return None
    no = L["set_no"].get(theme, 0) + 1
    if theme == "ca":
        picked = ca_questions(L)
        if len(picked) < 6:
            print("current affairs bank empty: add bank/ca.json"); return None
        qs = [{"q": x["q"], "o": x["o"], "a": x["a"], "ex": short_expl(x.get("e") or "", 120), "src": x.get("src", "")}
              for x in picked]
        ch_theme = ORDER[no % 4]
        day = now().strftime("%d") + " " + ["जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर",
                                            "अक्टूबर", "नवंबर", "दिसंबर"][now().month - 1]
        d = {"channel": ch_theme, "set_no": no, "questions": qs, "title_line": f"करेंट अफेयर्स क्विज़ {day}",
             "exam_line": "SSC, Railway, Bihar, Army सभी परीक्षाओं के लिए", "thumb_line": "करेंट अफेयर्स",
             "pill": now().strftime("%d.%m.%Y"), "intro_kind": "ca"}
        title = f"Current Affairs Quiz {now().strftime('%d %B %Y')} | {len(qs)} जरूरी सवाल | SSC Railway Bihar Army"
        head = [f"📰 {now().strftime('%d.%m.%Y')} करेंट अफेयर्स क्विज़: {len(qs)} जरूरी सवाल, जवाब और छोटी व्याख्या के साथ।"]
        tags_extra = ["current affairs quiz", "current affairs today", "daily current affairs", "current affairs hindi",
                      "करेंट अफेयर्स", "gk quiz hindi"]
        tag_theme = "ssc"
    else:
        bank = json.load(open(os.path.join(HERE, "bank", "quiz.json")))
        used = set(L["set_used"])
        ok = lambda x: bool(re.search("[\u0900-\u097F]", x["q"])) and len(x["q"]) <= 160 and max(len(o) for o in x["o"]) <= 40
        same = [i for i, x in enumerate(bank) if x.get("exam") == theme and ok(x) and i not in used]
        vvi = [i for i in same if "महत्वपूर्ण" in (bank[i].get("src") or "") or bank[i].get("vvi")]
        if len(same) < SET_N:  # exam pool used up: start a revision round of this exam's questions
            L["set_used"] = [i for i in L["set_used"] if bank[i].get("exam") != theme] if len(L["set_used"]) else []
            used = set(L["set_used"]); same = [i for i, x in enumerate(bank) if x.get("exam") == theme and ok(x) and i not in used]
            vvi = []
        if len(same) < SET_N:
            raise RuntimeError(f"question bank too small for {theme} practice sets")
        random.seed(f"set{theme}{no}")
        k_v = min(len(vvi), 3)  # up to 3 'likely' questions per set, rest real PYQs
        pick = random.sample(vvi, k_v) + random.sample([i for i in same if i not in vvi], SET_N - k_v) if \
            len([i for i in same if i not in vvi]) >= SET_N - k_v else random.sample(same, SET_N)
        random.shuffle(pick)
        qs = [{"q": bank[i]["q"], "o": bank[i]["o"], "a": bank[i]["a"], "ex": short_expl(bank[i].get("e") or "", 120),
               "src": bank[i].get("src", "")} for i in pick]
        short, exams = SET_NAMES[theme]
        ch_theme = theme
        d = {"channel": theme, "set_no": no, "questions": qs, "title_line": f"{short} GK प्रैक्टिस सेट {no}",
             "exam_line": f"{exams} के सबसे ज़रूरी सवाल", "thumb_line": f"{short} GK प्रैक्टिस सेट",
             "pill": f"प्रैक्टिस सेट {no}"}
        title = f"{short} GK Practice Set {no} | {SET_N} सबसे ज़रूरी PYQ सवाल | {exams.split(',')[0]} GK in Hindi"
        head = [f"🧠 {short} GK प्रैक्टिस सेट {no}: {exams} के {SET_N} सबसे ज़रूरी सवाल, जवाब और छोटी व्याख्या के साथ।"]
        tags_extra = [f"{short} gk practice set", f"{short} gk", "gk practice set", "pyq practice set", "gk quiz hindi",
                      f"{short} pyq"]
        tag_theme = theme
    os.makedirs(OUT, exist_ok=True)
    info = quizset.build(d, os.path.join(OUT, "set.mp4"), os.path.join(OUT, "set_thumb.jpg"), voice="male", music_i=no)
    lines = head + [f"हर सवाल के बाद {quizset.THINK} सेकंड, जवाब सोचिए और अपना स्कोर कमेंट में लिखिए।", "", "📚 इस सेट के सवाल:"]
    lines += [f"{k + 1}. {q['q']}" + (f" ({q['src']})" if q["src"] else "") for k, q in enumerate(qs)]
    desc = description(ch_theme, lines, channel_url(s), kind="long")
    tags = [t for t in tags_for(tag_theme, tags_extra) if t.lower() != "shorts"]
    vid = upload(info["out"], info["thumb"], title, desc, tags, publish_at=slot_time(theme))
    playlist_add(L, theme, vid)
    if theme == "ca":
        L["ca_used"] = (L["ca_used"] + [x["q"] for x in picked])[-3000:]
    else:
        L["set_used"] = (L["set_used"] + pick)[-4000:]
    L["set_no"][theme] = no
    L["log"].append([now().strftime("%Y-%m-%d %H:%M"), "set", theme, vid, f"set {no}", len(qs)])
    if not DRY:
        save_long(L)
    return ["set", theme, vid, f"{info['duration_s']}s"]

def do_welcome(s):
    if s.get("welcome_vid"):
        print("welcome already posted"); return None
    d = json.load(open(os.path.join(HERE, "bank", "welcome.json"), encoding="utf-8"))
    import make
    os.makedirs(OUT, exist_ok=True)
    info = make.make("welcome", d, os.path.join(OUT, "short.mp4"), layout=0, music_i=1)
    title = "Job Notification Quiz में आपका स्वागत है 🙏 | SSC Railway Bihar Army #shorts"
    desc = description("ssc", ["🙏 Job Notification Quiz में आपका स्वागत है!", "",
                               "यहाँ रोज़ मिलेंगे पिछली परीक्षाओं के असली सवाल (PYQ) और हर नई सरकारी भर्ती की खबर।",
                               "SSC, Railway, Bihar और Army/Agniveer, चारों के अलग Telegram चैनल।"], channel_url(s))
    vid = upload(info["out"], info["thumb"], title, desc, tags_for("ssc", ["job notification quiz", "Railway", "Bihar", "Agniveer"]))
    if vid != "dryrun":
        s["welcome_vid"] = vid
    return ["welcome", "ssc", vid, info["layout"]]


def gate(mode):
    """Cheap stdlib-only check before installing anything: is there work for this run?"""
    s = load()
    if os.environ.get("FORCE") == "1":
        return True
    if upload_blocked(s):
        print("uploads paused until", s.get("upload_block_until")); return False
    if mode in ("quiz", "vacancy", "welcome"):
        return True
    if mode == "long":
        L = load_long()
        return long_today(L) < LONG_CAP and bool(pick_long(L, parse=False))
    if mode.startswith("set") or mode == "ca":
        return set_due(load_long(), set_theme(mode))
    if mode == "auto":
        return due_slot(s) is not None
    if mode == "breaking":
        if not (7 <= now().hour < 23) or today_count(s, "breaking") >= BREAKING_CAP:
            return False
        for th in ORDER:
            try:
                for it in scrape(th):
                    if it["key"] not in s["posted"] and post_count(it["text"], vac_title(it["text"])) >= 1000:
                        return True
            except Exception as e:
                print("gate scrape fail", th, e)
        return False
    return True


def main():
    mode = next((a for a in sys.argv[1:] if not a.startswith("--")), "auto")
    if "--gate" in sys.argv:
        go = gate(mode)
        print("go =", go)
        if os.environ.get("GITHUB_OUTPUT"):
            open(os.environ["GITHUB_OUTPUT"], "a").write(f"go={'1' if go else '0'}\n")
        return
    force = os.environ.get("FORCE") == "1"
    s = load()
    slot = None
    if mode == "auto":
        due = due_slot(s)
        if not due and not force:
            print("no slot due"); return
        slot, mode = due or (now().strftime("%Y-%m-%d manual"), "quiz")
    if not force and not (mode.startswith("set") or mode == "ca"):  # sets render early, publish on schedule
        hr = now().hour
        if not (7 <= hr < 23):
            print("quiet hours"); return
        if mode == "breaking":
            if today_count(s, "breaking") >= BREAKING_CAP:
                print("breaking cap"); return
        elif mode != "long" and not mode.startswith("set") and mode != "ca" and today_count(s) >= DAILY_CAP:
            print("daily cap"); return
    if slot:  # mark first, so a parallel backup run of the same slot skips
        s["slots"].append(slot)
    res = None
    try:
        if mode in ("vacancy", "breaking"):
            res = do_vacancy(s, 1000 if mode == "breaking" else 0)
            if res is None and mode == "vacancy":
                res = do_quiz(s)
        elif mode == "quiz":
            res = do_quiz(s)
        elif mode == "welcome":
            res = do_welcome(s)
        elif mode.startswith("set") or mode == "ca":  # same own state file as long videos, never touches state.json
            print(do_set(s, set_theme(mode))); return
        elif mode == "long":  # own state file (state/long.json), own concurrency group: never touches state.json
            print(do_long(s)); return
    except UploadLimit:
        print("YouTube upload limit reached: pausing uploads for", LIMIT_COOLDOWN_H, "h")
        if mode == "long" or mode.startswith("set") or mode == "ca":
            return
        s["upload_block_until"] = (now() + timedelta(hours=LIMIT_COOLDOWN_H)).isoformat(timespec="minutes")
        s["log"].append([now().isoformat(timespec="minutes"), "limit", mode, "", ""])
        if not DRY:
            save(s)
        return
    if res:
        s["n"] += 1
        s["log"].append([now().isoformat(timespec="minutes")] + res)
    if DRY:
        print("dry run: state not saved"); return
    save(s)


if __name__ == "__main__":
    main()
