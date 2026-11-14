"""Memecoin Risk Radar bot. Jobs: brief | alert | scoreboard | education | ig_prepare | ig_publish
Run: python bot.py <job>   (DRY_RUN=true prints instead of publishing)
"""
import os, sys, json, re, time, random, pathlib, datetime as dt
import requests

ROOT = pathlib.Path(__file__).parent
STATE, PUBLIC, HELD = ROOT / "state", ROOT / "public", ROOT / "held"
for d in (STATE, PUBLIC, HELD):
    d.mkdir(exist_ok=True)

BRAND = os.getenv("BRAND") or "Is It Cooked"
MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
DISCLOSURE = os.getenv("DISCLOSURE", "")
DS = "https://api.dexscreener.com"
CHAINS = [c.strip() for c in (os.getenv("CHAINS") or "solana,bsc,ethereum,base").split(",") if c.strip()]
CHAIN_NAMES = {"solana": "Solana", "bsc": "BNB Chain", "ethereum": "Ethereum", "base": "Base", "arbitrum": "Arbitrum",
               "polygon": "Polygon", "avalanche": "Avalanche", "optimism": "Optimism"}
EVM_IDS = {"ethereum": 1, "bsc": 56, "base": 8453, "arbitrum": 42161, "polygon": 137, "avalanche": 43114, "optimism": 10}
FOOTER = "Not financial advice. DYOR."
BANNED = re.compile(r"\b(buy|sell|100x|1000x|gem|guaranteed|moon)\b", re.I)
TOPICS = [
    "How to read a token's holder list and spot concentration",
    "What liquidity is and why low liquidity makes exits impossible",
    "Mint and freeze authority: what they are and why they matter",
    "Bundled launches: how insiders can control supply at launch",
    "Volume vs. liquidity: spotting wash trading",
    "What an LP lock does and what it does not protect against",
    "Why token age matters when judging risk",
]


# ---------- helpers ----------
def load(name, default):
    p = STATE / f"{name}.json"
    return json.loads(p.read_text()) if p.exists() else default


def save(name, obj):
    (STATE / f"{name}.json").write_text(json.dumps(obj, indent=1))


def http_get(url):
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    return r.json()


def fmt_usd(x):
    x = float(x or 0)
    for d, s in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if x >= d:
            return f"${x / d:.1f}{s}"
    return f"${x:.2f}"


def fmt_price(x):
    x = float(x)
    return f"${x:.8f}".rstrip("0") if x < 0.001 else f"${x:.6g}"


# ---------- data ----------
def best_pair(chain, addr):
    pairs = http_get(f"{DS}/latest/dex/tokens/{addr}").get("pairs") or []
    pairs = [p for p in pairs if p.get("chainId") == chain]
    return max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd", 0), default=None)


def rugcheck(addr):
    try:
        j = http_get(f"https://api.rugcheck.xyz/v1/tokens/{addr}/report/summary")
        risks = [f"{r.get('name')} ({r.get('level')})" for r in j.get("risks", [])][:6]
        return risks
    except Exception as e:
        print("rugcheck unavailable:", e)
        return None


def goplus_risks(chain, addr):
    """EVM contract checks via GoPlus (free). Returns list of flag names, or None if unavailable."""
    cid = EVM_IDS.get(chain)
    if not cid:
        return None
    try:
        j = http_get(f"https://api.gopluslabs.io/api/v1/token_security/{cid}?contract_addresses={addr}")
        t = (j.get("result") or {}).get(addr.lower())
        if not t:
            return None
        risks = []
        if t.get("is_honeypot") == "1": risks.append("Honeypot (danger)")
        if t.get("is_open_source") == "0": risks.append("Contract not verified (warn)")
        if t.get("hidden_owner") == "1": risks.append("Hidden owner (danger)")
        if t.get("is_mintable") == "1": risks.append("Mintable supply (warn)")
        if t.get("can_take_back_ownership") == "1": risks.append("Ownership can be reclaimed (warn)")
        if float(t.get("sell_tax") or 0) > 0.10: risks.append("High sell tax (warn)")
        if float(t.get("buy_tax") or 0) > 0.10: risks.append("High buy tax (warn)")
        return risks
    except Exception as e:
        print("goplus unavailable:", e)
        return None


def count_copies(sym):
    """How many different tokens share this ticker (DexScreener search, first results)."""
    try:
        pairs = http_get(f"{DS}/latest/dex/search?q={sym}").get("pairs") or []
        return len({(p["chainId"], p["baseToken"]["address"]) for p in pairs
                    if p["baseToken"]["symbol"].upper() == sym.upper()})
    except Exception as e:
        print("copies lookup failed:", e)
        return 0


def short_ca(a):
    return a if len(a) <= 16 else f"{a[:8]}...{a[-8:]}"


def build_data(pair, risks):
    b, liq = pair["baseToken"], (pair.get("liquidity") or {}).get("usd", 0)
    vol = (pair.get("volume") or {}).get("h24", 0)
    tx = (pair.get("txns") or {}).get("h24", {})
    age_h = (time.time() * 1000 - (pair.get("pairCreatedAt") or 0)) / 3.6e6
    age = f"{age_h:.0f} hours" if age_h < 72 else f"{age_h / 24:.0f} days"
    facts = [
        f"Token: {b['symbol']} on {pair['chainId']}",
        f"Liquidity: {fmt_usd(liq)}",
        f"24h volume: {fmt_usd(vol)}",
        f"24h price change: {(pair.get('priceChange') or {}).get('h24', 0):+.1f}%",
        f"Pair age: {age}",
        f"FDV: {fmt_usd(pair.get('fdv'))}",
        f"24h transactions: {tx.get('buys', 0)} buys / {tx.get('sells', 0)} sells",
    ]
    if liq:
        facts.append(f"Volume to liquidity ratio: {vol / liq:.1f}x")
    facts.append(f"Chain: {CHAIN_NAMES.get(pair['chainId'], pair['chainId'])}")
    facts.append("Risk flags: " + ("; ".join(risks) if risks else "none reported or unavailable"))
    chg = (pair.get("priceChange") or {}).get("h24", 0)
    metrics = [("LIQUIDITY", fmt_usd(liq), None), ("24H VOLUME", fmt_usd(vol), None), ("PAIR AGE", age, None),
               ("24H CHANGE", f"{chg:+.1f}%", "red" if chg < 0 else "green")]
    return {
        "metrics": metrics, "risks_known": risks is not None, "volume": vol, "age_h": age_h,
        "logo": (pair.get("info") or {}).get("imageUrl"), "pool": pair.get("pairAddress"),
        "token": {"symbol": b["symbol"], "name": b["name"], "chain": pair["chainId"],
                  "chain_name": CHAIN_NAMES.get(pair["chainId"], pair["chainId"].title()),
                  "address": b["address"], "url": pair.get("url")},
        "price": pair.get("priceUsd"), "liquidity": liq, "risk_count": len(risks or []),
        "facts": facts,
    }


def candidates(limit=15):
    out = http_get(f"{DS}/token-boosts/latest/v1")
    return [c for c in out if c.get("chainId") in CHAINS][:limit]


def pick_alert_data():
    seen = {h["address"] for h in load("history", [])} | set(load("seen", []))
    cands = candidates()
    random.shuffle(cands)
    for c in cands:
        if c["tokenAddress"] in seen:
            continue
        pair = best_pair(c["chainId"], c["tokenAddress"])
        if not pair or (pair.get("liquidity") or {}).get("usd", 0) < 5000:
            continue
        risks = rugcheck(c["tokenAddress"]) if c["chainId"] == "solana" else goplus_risks(c["chainId"], c["tokenAddress"])
        data = build_data(pair, risks)
        data["copies"] = count_copies(data["token"]["symbol"])
        if data["copies"] > 1:
            data["facts"].append(f"Tokens sharing this ticker (DexScreener search, first results): {data['copies']}")
        return data
    return None


def brief_data(n=5):
    rows = []
    for c in candidates(25):
        pair = best_pair(c["chainId"], c["tokenAddress"])
        if pair and (pair.get("liquidity") or {}).get("usd", 0) >= 5000:
            rows.append(build_data(pair, None))
        if len(rows) == n:
            break
    facts = [f"{r['token']['symbol']}: " + " | ".join(r["facts"][1:6]) for r in rows]
    return {"date": dt.date.today().isoformat(), "facts": facts} if rows else None


# ---------- generation + validation ----------
def mock_generate(job, data, fmt, platform):
    """Free test mode (no ANTHROPIC_API_KEY): template text built only from the real data."""
    print("[MOCK AI: no ANTHROPIC_API_KEY set, using template text. Real posts need the key.]")
    f = data.get("facts", [])
    if job == "alert":
        t = data["token"]["symbol"]
        fv = lambda p: next((x for x in f if x.startswith(p)), p)
        txt = [f"${t} on {data['token']['chain_name']}: {fv('Liquidity')}, {fv('24h volume')}.",
               f"Breakdown: {fv('Pair age')}. {fv('24h price change')}. {fv('Risk flags')}."]
        return {"platform": "x", "pillar": "alert", "format": "thread", "text": txt,
                "image_caption": "Check before you trust", "image_sub": "Verify the pool and the contract"}
    if job == "brief" and fmt == "carousel":
        slides = ["Today's memecoin risk board"] + [x[:90] for x in f[:4]] + ["Check liquidity and holders first."]
        return {"platform": "instagram", "pillar": "brief", "format": "carousel", "slides": slides,
                "caption": "Daily memecoin risk board. #memecoins #solana #crypto #dexscreener #risk"}
    if job == "brief":
        return {"platform": "x", "pillar": "brief", "format": "post",
                "text": "Memecoin brief:\n" + "\n".join(x[:110] for x in f[:2])}
    if job == "scoreboard":
        return {"platform": "x", "pillar": "scoreboard", "format": "thread",
                "text": ["Scoreboard Sunday: this week's alerts, misses included."] + [x[:270] for x in f[:5]]}
    return {"platform": "x", "pillar": "education", "format": "post",
            "text": f"Memecoin basics: {data['topic']}.\n\nCheck this before trusting any token."}


ALERT_RULES = ("ALERT FORMAT: put exactly 2 posts in the text array. Post 1 (max 170 chars): hook with the ticker, the chain and the single "
               "most important fact. Post 2 (max 260 chars): breakdown with 'Green flags:' and 'Red flags:' using only DATA.facts "
               "(write 'none found' when there are none) and say what stands out and why it is worth checking, never why to buy. "
               "The contract, the how-to-verify post and the disclaimer are appended automatically: do not write them. ")


def gemini_text(system, user):
    """Free writer: Gemini via Google AI Studio key (free tier, Flash models)."""
    model = os.getenv("GEMINI_MODEL") or "gemini-2.5-flash"
    r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                      headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]},
                      json={"systemInstruction": {"parts": [{"text": system}]},
                            "contents": [{"role": "user", "parts": [{"text": user}]}],
                            "generationConfig": {"temperature": 0.8, "responseMimeType": "application/json"}}, timeout=90)
    r.raise_for_status()
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


def generate(job, data, fmt, platform):
    if not os.getenv("ANTHROPIC_API_KEY") and not os.getenv("GEMINI_API_KEY"):
        return mock_generate(job, data, fmt, platform)
    system = (ROOT / "master_prompt.md").read_text().replace("{BRAND}", BRAND)
    user = (f"JOB: {job}\nPLATFORM: {platform}\nFORMAT: {fmt}\nDISCLOSURE: {DISCLOSURE}\n"
            f"RULES: quote numbers exactly as written in DATA.facts and add no other numbers. "
            f"If a token is named, end with '{FOOTER}'. For alerts also return image_caption (max 5 words, meme-caption style, no numbers) "
            f"and image_sub (max 10 words, no numbers). Keep text under 170 characters. Do NOT write the contract address; it is appended automatically.\nDATA:\n{json.dumps(data, ensure_ascii=False)}\n"
            f"{ALERT_RULES if job == 'alert' else ''}Return only the JSON object.")
    if os.getenv("ANTHROPIC_API_KEY"):
        from anthropic import Anthropic
        msg = Anthropic().messages.create(model=MODEL, max_tokens=1800, system=system,
                                          messages=[{"role": "user", "content": user}])
        raw = msg.content[0].text
    else:
        raw = gemini_text(system, user)
    return json.loads(re.search(r"\{.*\}", raw, re.S).group(0))


def all_text(out):
    t = out.get("text", "")
    parts = t if isinstance(t, list) else [t]
    return parts + out.get("slides", []) + [out.get("caption", ""), out.get("image_caption", ""), out.get("image_sub", "")]


def validate(out, data):
    problems, parts = [], all_text(out)
    joined = " ".join(parts)
    if data.get("token") and data["token"].get("address"):
        addr = data["token"]["address"]
        if addr not in joined:
            problems.append("missing contract address")
        joined = joined.replace(addr, "")
    if BANNED.search(joined):
        problems.append("banned word: " + BANNED.search(joined).group(0))
    facts = " ".join(data.get("facts", [])).replace(",", "")
    for num in re.findall(r"\d+(?:\.\d+)?", joined.replace(",", "")):
        if num not in facts and not (num.isdigit() and int(num) <= 10):
            problems.append(f"number not in data: {num}")
    if out.get("platform") == "x" and any(len(p) > 280 for p in (out["text"] if isinstance(out["text"], list) else [out["text"]])):
        problems.append("post over 280 chars")
    if len(out.get("image_caption", "")) > 40 or len(out.get("image_sub", "")) > 80:
        problems.append("image text too long")
    if data.get("token") and FOOTER not in joined:
        problems.append("missing footer")
    if DISCLOSURE and DISCLOSURE not in joined:
        problems.append("missing disclosure")
    return problems


def save_outbox(job, parts, image):
    """Free workflow: ready-to-post text + image path saved in the repo (public/outbox)."""
    box = PUBLIC / "outbox"
    box.mkdir(exist_ok=True)
    for f in box.glob("*.md"):
        if time.time() - f.stat().st_mtime > 30 * 86400:
            f.unlink()
    name = f"{dt.datetime.utcnow():%Y%m%d_%H%M}_{job}.md"
    img = f"\n\n---\n\nIMAGE: public/{pathlib.Path(image).name}" if image else ""
    (box / name).write_text("\n\n---\n\n".join(parts) + img, encoding="utf-8")


EXPLORERS = {"solana": "Solscan", "bsc": "BscScan", "ethereum": "Etherscan", "base": "BaseScan",
             "arbitrum": "Arbiscan", "polygon": "Polygonscan", "avalanche": "Snowtrace", "optimism": "Optimistic Etherscan"}


def verdict(data):
    """Rule-based label shown on the image (no AI): HIGH RISK / CAUTION / NO FLAGS FOUND."""
    n, liq = data["risk_count"], data.get("liquidity", 0)
    ratio = (data.get("volume", 0) / liq) if liq else 99
    if n >= 3 or liq < 10000:
        return ("HIGH RISK", "255,77,94")
    if n >= 1 or data.get("age_h", 999) < 24 or ratio > 5 or not data["risks_known"]:
        return ("CAUTION", "255,176,32")
    return ("NO FLAGS FOUND", "24,224,138")


def with_contract(out, data):
    """Thread: [hook + contract, breakdown, how-to-verify + disclaimer]. Contract and verify post are added by code."""
    t = data["token"]
    texts = list(out["text"]) if isinstance(out["text"], list) else [out["text"]]
    line = f"{t['chain_name']} CA: {t['address']}"
    if len(texts[0]) + 2 + len(line) <= 280:
        texts[0] += "\n\n" + line
    else:
        texts.insert(1, line)
    texts.append(f"How to verify: open this contract on {EXPLORERS.get(t['chain'], 'the chain explorer')}, check that it matches "
                 f"the project's official link, and compare liquidity before trusting a ticker.\n\n{FOOTER}")
    out["text"] = texts
    return out


def hold(job, out, problems):
    name = f"{dt.datetime.utcnow():%Y%m%d_%H%M}_{job}.json"
    (HELD / name).write_text(json.dumps({"problems": problems, "output": out}, indent=1))
    print("HELD:", problems)


# ---------- publishing ----------
def publish_x(parts, image=None):
    import tweepy
    c = tweepy.Client(consumer_key=os.environ["X_API_KEY"], consumer_secret=os.environ["X_API_SECRET"],
                      access_token=os.environ["X_ACCESS_TOKEN"], access_token_secret=os.environ["X_ACCESS_SECRET"])
    media_ids = None
    if image:
        try:
            auth = tweepy.OAuth1UserHandler(os.environ["X_API_KEY"], os.environ["X_API_SECRET"],
                                            os.environ["X_ACCESS_TOKEN"], os.environ["X_ACCESS_SECRET"])
            media_ids = [tweepy.API(auth).media_upload(image).media_id]
        except Exception as e:
            print("image upload failed, posting text only:", e)
    prev = None
    for i, p in enumerate(parts):
        r = c.create_tweet(text=p, in_reply_to_tweet_id=prev, media_ids=media_ids if i == 0 else None)
        prev = r.data["id"]
        time.sleep(2)


def deliver_x(job, out, data, image_fn=None):
    problems = validate(out, data)
    if problems:
        hold(job, out, problems)
        return False
    parts = out["text"] if isinstance(out["text"], list) else [out["text"]]
    image = image_fn(out) if image_fn else None
    if DRY_RUN:
        print("[DRY RUN X]\n" + "\n---\n".join(parts) + f"\n[image: {image}]")
        save_outbox(job, parts, image)
    elif not os.getenv("X_API_KEY"):
        print("No X keys: saved to outbox instead of publishing.")
        save_outbox(job, parts, image)
    else:
        publish_x(parts, image)
    return True


import design, render_html
from PIL import Image, ImageDraw, ImageFont


def get_logo(data, name):
    """Download the token logo (DexScreener imageUrl); fall back to a letter coin."""
    path = STATE / f"logo_{name}.png"
    url = data.get("logo")
    try:
        if url and os.path.exists(url):
            path.write_bytes(open(url, "rb").read())
        elif url:
            r = requests.get(url, timeout=15); r.raise_for_status(); path.write_bytes(r.content)
        else:
            raise ValueError("no logo")
        Image.open(path).convert("RGBA").save(path, "PNG")
        return str(path)
    except Exception as e:
        print("logo unavailable, using letter coin:", e)
    im = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse([0, 0, 511, 511], fill=(255, 138, 0)); d.ellipse([40, 40, 471, 471], fill=(255, 196, 0))
    f = ImageFont.truetype(str(ROOT / "fonts" / "Poppins-Bold.ttf"), 260)
    ch = data["token"]["symbol"][:1].upper()
    d.text((256, 270), ch, font=f, fill=(255, 255, 255), anchor="mm", stroke_width=10, stroke_fill=(20, 20, 20))
    im.save(path)
    return str(path)


def render_alert_card(data, out, prefix):
    logo = get_logo(data, prefix)
    pal = design.logo_palette(Image.open(logo))
    m = {k: v for k, v, _ in data["metrics"]}
    third = ("Risk flags", f"{data['risk_count']} flags") if data["risks_known"] else ("Pair age", m["PAIR AGE"])
    d = {"symbol": data["token"]["symbol"].upper()[:12],
         "stats": [("Liquidity", m["LIQUIDITY"]), ("24h volume", m["24H VOLUME"]), third],
         "caption": out.get("image_caption") or "Check before you trust", "sub": out.get("image_sub", ""),
         "chain": data["token"]["chain_name"], "chain_key": data["token"]["chain"], "verdict": verdict(data), "ca": short_ca(data["token"]["address"]), "copies": data.get("copies", 0),
         "sources": "Data: DexScreener, RugCheck / GoPlus"}
    return render_html.coin_card(d, logo, pal, PUBLIC / f"{prefix}_card.png", BRAND.upper())


def render_carousel(slides, prefix):
    files = []
    for i, t in enumerate(slides):
        name = f"{prefix}_{i + 1}.png"
        render_html.slide_card(t, i, len(slides), PUBLIC / name, BRAND.upper())
        files.append(name)
    return files


def ig_publish(urls, caption):
    G, uid, tok = "https://graph.facebook.com/v21.0", os.environ["IG_USER_ID"], os.environ["IG_ACCESS_TOKEN"]
    kids = []
    for u in urls:
        r = requests.post(f"{G}/{uid}/media", data={"image_url": u, "is_carousel_item": "true", "access_token": tok}, timeout=30)
        r.raise_for_status(); kids.append(r.json()["id"])
    r = requests.post(f"{G}/{uid}/media", data={"media_type": "CAROUSEL", "children": ",".join(kids),
                                                "caption": caption, "access_token": tok}, timeout=30)
    r.raise_for_status(); cid = r.json()["id"]
    time.sleep(15)
    requests.post(f"{G}/{uid}/media_publish", data={"creation_id": cid, "access_token": tok}, timeout=30).raise_for_status()


# ---------- jobs ----------
def job_alert():
    data = pick_alert_data()
    if not data:
        return print("no new candidate")
    seen = load("seen", [])
    seen.append(data["token"]["address"])
    save("seen", seen[-500:])
    out = with_contract(generate("alert", data, "thread", "x"), data)
    prefix = re.sub(r"\W", "", f"x_{dt.date.today():%Y%m%d}_{data['token']['symbol'][:8]}")
    if deliver_x("alert", out, data, lambda o: render_alert_card(data, o, prefix)):
        h = load("history", [])
        t = data["token"]
        h.append({"symbol": t["symbol"], "address": t["address"], "chain": t["chain"], "price": data["price"],
                  "risk_count": data["risk_count"], "ts": time.time()})
        save("history", h)


def job_brief():
    data = brief_data()
    if not data:
        return print("no data")
    deliver_x("brief", generate("brief", data, "post", "x"), data)


def job_scoreboard():
    week = [h for h in load("history", []) if time.time() - h["ts"] < 7 * 86400]
    facts = []
    for h in week:
        try:
            pair = best_pair(h["chain"], h["address"])
            now = float(pair["priceUsd"]) if pair else None
        except Exception:
            now = None
        if now and float(h["price"] or 0):
            chg = (now / float(h["price"]) - 1) * 100
            facts.append(f"{h['symbol']}: price at alert {fmt_price(h['price'])}, now {fmt_price(now)} ({chg:+.0f}%), "
                         f"risk flags at alert: {h['risk_count']}")
        else:
            facts.append(f"{h['symbol']}: current data unavailable (liquidity may be gone)")
    if not facts:
        return print("no history yet")
    data = {"week": dt.date.today().isoformat(), "facts": facts}
    deliver_x("scoreboard", generate("scoreboard", data, "thread", "x"), data)


def job_education():
    topic = TOPICS[dt.date.today().toordinal() % len(TOPICS)]
    data = {"topic": topic, "facts": []}
    deliver_x("education", generate("education", data, "post", "x"), data)


def job_ig_prepare():
    for f in PUBLIC.glob("*.png"):
        if time.time() - f.stat().st_mtime > 14 * 86400:
            f.unlink()
    data = brief_data()
    if not data:
        return print("no data")
    out = generate("brief", data, "carousel", "instagram")
    problems = validate(out, data)
    if problems:
        return hold("ig", out, problems)
    files = render_carousel(out["slides"], f"ig_{dt.date.today():%Y%m%d}")
    save("ig_next", {"files": files, "caption": out.get("caption", "")})
    print("prepared", files)


def job_ig_publish():
    nxt = load("ig_next", None)
    if not nxt:
        return print("nothing prepared")
    base = f"https://raw.githubusercontent.com/{os.environ.get('GITHUB_REPOSITORY', 'USER/REPO')}/main/public/"
    if DRY_RUN:
        print("[DRY RUN IG]", nxt["caption"], [base + f for f in nxt["files"]])
    else:
        ig_publish([base + f for f in nxt["files"]], nxt["caption"])
    (STATE / "ig_next.json").unlink()


JOBS = {"alert": job_alert, "brief": job_brief, "scoreboard": job_scoreboard, "education": job_education,
        "ig_prepare": job_ig_prepare, "ig_publish": job_ig_publish}

if __name__ == "__main__":
    JOBS[sys.argv[1]]()
