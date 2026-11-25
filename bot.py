"""Automated Instagram-only memecoin radar for IS IT COOKED?."""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import pathlib
import re
import statistics
import subprocess
import sys
import textwrap
import time
from urllib.parse import quote, urlsplit

import requests
from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import design
import render_html

ROOT = pathlib.Path(__file__).parent
STATE, PUBLIC = ROOT / "state", ROOT / "public"
STATE.mkdir(exist_ok=True); PUBLIC.mkdir(exist_ok=True)
BRAND = os.getenv("BRAND") or "Is It Cooked"
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
CHAINS = [x.strip() for x in os.getenv("CHAINS", "solana,base").split(",") if x.strip()]
MIN_LIQUIDITY = float(os.getenv("MIN_LIQUIDITY", "20000"))
MIN_VOLUME_1H = float(os.getenv("MIN_VOLUME_1H", "25000"))
MIN_TXNS_1H = int(os.getenv("MIN_TXNS_1H", "35"))
MIN_SCORE = int(os.getenv("MIN_SCORE", "70"))
MAX_AGE_HOURS = float(os.getenv("MAX_AGE_HOURS", "72"))
REQUIRE_OFFICIAL_LINK = os.getenv("REQUIRE_OFFICIAL_LINK", "true").lower() == "true"
MEDIA_BRANCH = os.getenv("MEDIA_BRANCH", "media")
DS = "https://api.dexscreener.com"
CHAIN_NAMES = {"solana": "Solana", "base": "Base", "ethereum": "Ethereum", "bsc": "BNB Chain"}
CHAIN_IDS = {"ethereum": 1, "bsc": 56, "base": 8453, "arbitrum": 42161, "polygon": 137,
             "avalanche": 43114, "optimism": 10}
EXPLORERS = {"solana": "Solscan", "base": "BaseScan", "ethereum": "Etherscan", "bsc": "BscScan"}
HASHTAGS = {"solana": "#solana", "base": "#basechain", "ethereum": "#ethereum", "bsc": "#bnbchain"}
LOGO_HOSTS = {"cdn.dexscreener.com", "dd.dexscreener.com"}

HTTP = requests.Session()
HTTP.headers.update({"User-Agent": "IsItCookedInstagramRadar/3.0"})
HTTP.mount("https://", HTTPAdapter(max_retries=Retry(
    total=3, connect=3, read=3, backoff_factor=.8,
    status_forcelist=(429, 500, 502, 503, 504), allowed_methods=("GET",))))


def load(name, default):
    path = STATE / f"{name}.json"
    if not path.exists(): return default
    try: return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Invalid state/{name}.json ({exc}); using safe default")
        return default


def save(name, value):
    """Atomic JSON write so interrupted jobs cannot corrupt state."""
    path = STATE / f"{name}.json"; tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def get_json(url, timeout=25):
    response = HTTP.get(url, timeout=timeout); response.raise_for_status()
    try: return response.json()
    except requests.JSONDecodeError as exc: raise RuntimeError(f"Invalid JSON from {urlsplit(url).netloc}") from exc


def num(value, default=0.0):
    try:
        value = float(value); return value if math.isfinite(value) else default
    except (TypeError, ValueError): return default


def fmt_usd(value):
    value = num(value)
    for divisor, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if value >= divisor: return f"${value / divisor:.1f}{suffix}"
    return f"${value:,.0f}"


def short_ca(value): return value if len(value) <= 20 else f"{value[:8]}...{value[-8:]}"
def token_key(chain, address): return f"{chain}:{address if chain == 'solana' else address.lower()}"


def official_presence(pair):
    info = pair.get("info") or {}
    websites = [x.get("url") for x in info.get("websites") or [] if isinstance(x, dict) and x.get("url")]
    socials = [x.get("url") for x in info.get("socials") or [] if isinstance(x, dict) and x.get("url")]
    return websites[:2], socials[:3]


def discovery_pairs():
    grouped, discovered = {chain: [] for chain in CHAINS}, set()
    for endpoint in ("token-boosts/latest/v1", "token-profiles/latest/v1", "token-boosts/top/v1"):
        try: rows = get_json(f"{DS}/{endpoint}")
        except Exception as exc:
            print(f"DexScreener {endpoint} unavailable: {exc}"); continue
        for row in rows if isinstance(rows, list) else []:
            chain, address = row.get("chainId"), row.get("tokenAddress")
            if chain not in grouped or not isinstance(address, str): continue
            key = token_key(chain, address)
            if key not in discovered and len(grouped[chain]) < 30:
                grouped[chain].append(address); discovered.add(key)
    best = {}
    for chain, addresses in grouped.items():
        if not addresses: continue
        try: pairs = get_json(f"{DS}/tokens/v1/{chain}/{','.join(addresses)}")
        except Exception as exc:
            print(f"Pair lookup for {chain} failed: {exc}"); continue
        for pair in pairs if isinstance(pairs, list) else []:
            address = (pair.get("baseToken") or {}).get("address")
            if not isinstance(address, str): continue
            key, liquidity = token_key(chain, address), num((pair.get("liquidity") or {}).get("usd"))
            if key not in best or liquidity > num((best[key].get("liquidity") or {}).get("usd")): best[key] = pair
    return list(best.values())


def best_pair(chain, address):
    data = get_json(f"{DS}/latest/dex/tokens/{address}")
    pairs = [pair for pair in data.get("pairs") or [] if pair.get("chainId") == chain]
    return max(pairs, key=lambda pair: num((pair.get("liquidity") or {}).get("usd")), default=None)


def refresh_history():
    """Record every outcome at approximately 1h, 24h and 7d without cherry-picking."""
    history = load("history", [])[-100:]
    now, changed = time.time(), False
    for record in history[-40:]:
        elapsed = (now - num(record.get("published_at"), now)) / 3600
        due = [("1h", 1), ("24h", 24), ("7d", 168)]
        missing = [(label, hours) for label, hours in due if elapsed >= hours and label not in record.get("checkpoints", {})]
        if not missing:
            continue
        try:
            pair = best_pair(record["chain"], record["address"])
            current = num(pair.get("priceUsd")) if pair else 0
        except Exception as exc:
            print(f"outcome refresh failed for {record.get('symbol')}: {exc}")
            continue
        initial = num(record.get("initial_price"))
        if not current or not initial:
            continue
        record.setdefault("checkpoints", {})
        for label, _ in missing:
            record["checkpoints"][label] = {
                "change_pct": round((current / initial - 1) * 100, 2),
                "observed_after_h": round(elapsed, 1), "price": current,
            }
        record["last_price"] = current
        record["peak_change_pct"] = max(num(record.get("peak_change_pct"), -100), (current / initial - 1) * 100)
        changed = True
    if changed:
        save("history", history)
    return history


def snapshot(pair):
    volume, txns, change = pair.get("volume") or {}, pair.get("txns") or {}, pair.get("priceChange") or {}
    h1 = txns.get("h1") or {}; created = num(pair.get("pairCreatedAt"))
    websites, socials = official_presence(pair)
    return {"age": (time.time()*1000-created)/3_600_000 if created else 999999,
            "liq": num((pair.get("liquidity") or {}).get("usd")), "v1": num(volume.get("h1")),
            "v5": num(volume.get("m5")), "v24": num(volume.get("h24")),
            "buys": int(num(h1.get("buys"))), "sells": int(num(h1.get("sells"))),
            "c1": num(change.get("h1")), "c5": num(change.get("m5")),
            "websites": websites, "socials": socials}


def qualifies(s):
    return (5/60 <= s["age"] <= MAX_AGE_HOURS and s["liq"] >= MIN_LIQUIDITY and
            s["v1"] >= MIN_VOLUME_1H and s["buys"] + s["sells"] >= MIN_TXNS_1H and
            s["buys"] > s["sells"] and 0 < s["c1"] <= 120 and s["c5"] > -8 and
            (bool(s["websites"] or s["socials"]) or not REQUIRE_OFFICIAL_LINK))


def market_score(s):
    liq = min(20, 8 + 8*math.log10(max(1, s["liq"]/MIN_LIQUIDITY)))
    vol = min(25, 10 + 10*math.log10(max(1, s["v1"]/MIN_VOLUME_1H)))
    ratio = s["buys"]/max(1, s["buys"]+s["sells"])
    pressure = min(22, max(0, (ratio-.5)*100))
    velocity = min(13, 78*s["v5"]/max(1, s["v1"]))
    momentum = 10 if 5 <= s["c1"] <= 60 else 6
    presence = 5 if s["websites"] and s["socials"] else 3
    age = 5 if .25 <= s["age"] <= 24 else 3
    return round(min(100, liq+vol+pressure+velocity+momentum+presence+age))


def solana_security(address):
    risks = get_json(f"https://api.rugcheck.xyz/v1/tokens/{address}/report/summary").get("risks")
    if not isinstance(risks, list): raise RuntimeError("RugCheck returned no risk list")
    labels, critical = [], False
    for risk in risks:
        level = str(risk.get("level") or "").lower(); name = str(risk.get("name") or "Unknown flag")[:80]
        labels.append(f"{name} ({level or 'flag'})"); critical |= level in {"danger", "critical", "error"}
    return critical, labels[:6]


def evm_security(chain, address):
    chain_id = CHAIN_IDS.get(chain)
    if not chain_id: raise RuntimeError(f"No security provider for {chain}")
    result = get_json(f"https://api.gopluslabs.io/api/v1/token_security/{chain_id}?contract_addresses={address}")
    token = (result.get("result") or {}).get(address.lower())
    if not isinstance(token, dict): raise RuntimeError("GoPlus returned no token report")
    critical_map = {"is_honeypot":"Honeypot", "hidden_owner":"Hidden owner", "cannot_sell_all":"Cannot sell all",
                    "is_blacklisted":"Blacklist function", "selfdestruct":"Self-destruct function"}
    warning_map = {"is_mintable":"Mintable supply", "can_take_back_ownership":"Ownership reclaimable",
                   "owner_change_balance":"Owner can change balances", "slippage_modifiable":"Modifiable tax"}
    critical = [label for flag, label in critical_map.items() if token.get(flag) == "1"]
    warnings = [label for flag, label in warning_map.items() if token.get(flag) == "1"]
    if token.get("is_open_source") != "1": critical.append("Contract source not verified")
    if num(token.get("buy_tax")) > .10 or num(token.get("sell_tax")) > .10: critical.append("Tax above 10%")
    return bool(critical), (critical+warnings)[:6]


def security_scan(chain, address): return solana_security(address) if chain == "solana" else evm_security(chain, address)


def build_data(pair, s, warnings, score):
    base, chain = pair.get("baseToken") or {}, pair.get("chainId")
    address = str(base.get("address") or ""); age = f"{s['age']:.0f} hours" if s["age"] < 48 else f"{s['age']/24:.1f} days"
    metrics = [("LIQUIDITY", fmt_usd(s["liq"]), None), ("1H VOLUME", fmt_usd(s["v1"]), None),
               ("PAIR AGE", age, None), ("1H CHANGE", f"{s['c1']:+.1f}%", "green")]
    return {"metrics":metrics, "risks_known":True, "risk_list":warnings, "risk_count":len(warnings),
            "radar_score":score, "age_h":s["age"], "volume":s["v1"], "liquidity":s["liq"],
            "buys":s["buys"], "sells":s["sells"], "change_h1":s["c1"],
            "logo":(pair.get("info") or {}).get("imageUrl"), "pool":pair.get("pairAddress"),
            "token":{"symbol":str(base.get("symbol") or "?")[:20], "name":str(base.get("name") or "Token")[:80],
                     "chain":chain, "chain_name":CHAIN_NAMES.get(chain, str(chain).title()), "address":address,
                     "url":pair.get("url")}, "price":pair.get("priceUsd"),
            "websites":s["websites"], "socials":s["socials"]}


def pick_promising(exclude_seen=True):
    seen = set(load("seen", [])); ranked = []
    for pair in discovery_pairs():
        chain, address = pair.get("chainId"), str((pair.get("baseToken") or {}).get("address") or "")
        if not chain or not address or (exclude_seen and (token_key(chain,address) in seen or address in seen)): continue
        s = snapshot(pair)
        if qualifies(s): ranked.append((market_score(s), pair, s))
    ranked.sort(key=lambda row: row[0], reverse=True); print("market-qualified candidates:", len(ranked))
    for score, pair, s in ranked[:12]:
        chain, address = pair["chainId"], pair["baseToken"]["address"]
        try: critical, warnings = security_scan(chain, address)
        except Exception as exc:
            print(f"security unavailable for {chain}:{address}: {exc}"); continue
        if critical:
            print(f"blocked critical risk for {chain}:{address}: {warnings}"); continue
        final = max(0, score-min(12, 3*len(warnings)))
        if final >= MIN_SCORE:
            print(f"selected {pair['baseToken'].get('symbol')} score={final}")
            return build_data(pair, s, warnings, final)
    print("no token met the complete quality and security threshold"); return None


def safe_logo_url(value):
    try:
        parsed = urlsplit(value or "")
        return value if parsed.scheme == "https" and (parsed.hostname or "").lower() in LOGO_HOSTS else ""
    except ValueError: return ""


def get_logo(data, name):
    path, url = STATE/f"logo_{name}.png", safe_logo_url(data.get("logo"))
    try:
        if not url: raise ValueError("no approved logo URL")
        response = HTTP.get(url, timeout=20, stream=True); response.raise_for_status(); raw = response.raw.read(3_000_001)
        if len(raw) > 3_000_000: raise ValueError("logo exceeds 3 MB")
        path.write_bytes(raw)
        with Image.open(path) as source:
            if source.width*source.height > 12_000_000: raise ValueError("logo dimensions too large")
            source.convert("RGBA").save(path, "PNG")
        return str(path)
    except (OSError, ValueError, requests.RequestException, UnidentifiedImageError) as exc:
        print("using branded fallback logo:", exc)
    image = Image.new("RGBA", (512,512), (0,0,0,0)); draw = ImageDraw.Draw(image)
    draw.ellipse((0,0,511,511), fill=(35,18,67)); draw.ellipse((38,38,473,473), fill=(255,156,0))
    font = ImageFont.truetype(str(ROOT/"fonts/Poppins-Bold.ttf"), 245)
    draw.text((256,270), data["token"]["symbol"][:1].upper(), font=font, fill="white", anchor="mm",
              stroke_width=10, stroke_fill=(10,10,20)); image.save(path); return str(path)


def verdict(data): return ("PROMISING SIGNAL", "24,224,138") if not data["risk_list"] else ("WATCHLIST", "255,176,32")


def render_alert_card(data, prefix):
    logo = get_logo(data, prefix); palette = design.logo_palette(Image.open(logo)); m = {k:v for k,v,_ in data["metrics"]}
    card = {"symbol":data["token"]["symbol"].upper()[:12],
            "stats":[("Liquidity",m["LIQUIDITY"]),("1h volume",m["1H VOLUME"]),("Radar score",f"{data['radar_score']}/100")],
            "caption":"Momentum worth watching", "sub":"Filtered by market strength and security",
            "chain":data["token"]["chain_name"], "chain_key":data["token"]["chain"], "verdict":verdict(data),
            "ca":short_ca(data["token"]["address"]), "copies":0, "sources":"Data: DexScreener, RugCheck / GoPlus"}
    return pathlib.Path(render_html.coin_card(card, logo, palette, PUBLIC/f"{prefix}_card.png", BRAND.upper()))


def render_carousel(data, prefix):
    token = data["token"]; logo = get_logo(data,prefix); palette = design.logo_palette(Image.open(logo))
    slide = {"symbol":token["symbol"].upper()[:10], "chain":token["chain_name"], "chain_key":token["chain"],
             "explorer":EXPLORERS.get(token["chain"],"the chain explorer"), "address":token["address"],
             "metrics":data["metrics"], "risk_list":data["risk_list"], "risks_known":True, "copies":0}
    pngs = [render_alert_card(data,prefix+"_s1")]
    for index, kind in enumerate(("numbers","flags","verify"), start=2):
        path = PUBLIC/f"{prefix}_s{index}.png"; render_html.token_slide(kind,slide,palette,index-1,4,path,BRAND.upper()); pngs.append(path)
    files=[]
    for png in pngs:
        jpg=png.with_suffix(".jpg")
        with Image.open(png) as image: image.convert("RGB").save(jpg,"JPEG",quality=92,optimize=True)
        png.unlink(missing_ok=True); files.append(jpg)
    return files


def caption(data):
    token=data["token"]; ratio=100*data["buys"]/max(1,data["buys"]+data["sells"])
    security = "No critical scanner flags detected." if not data["risk_list"] else "No critical flags; review the warnings shown in the carousel."
    tags=" ".join(("#memecoins","#cryptoresearch","#onchain",HASHTAGS.get(token["chain"],"#crypto"),"#dexscreener"))
    return "\n\n".join((f"${token['symbol'].upper()} just entered the IS IT COOKED? watchlist.",
        f"Radar score: {data['radar_score']}/100\nLiquidity: {fmt_usd(data['liquidity'])}\n1h volume: {fmt_usd(data['volume'])}\nBuy pressure: {ratio:.0f}%\n1h change: {data['change_h1']:+.1f}%",
        security, f"{token['chain_name']} contract:\n{token['address']}",
        "Comment RADAR for the live intelligence feed, then verify the contract before acting. Save this card and follow for the public receipt.",
        "Promising momentum is not a guarantee. Not financial advice. Always do your own research.", tags))


def cleanup_media(days=3):
    cutoff=time.time()-days*86400
    for path in list(PUBLIC.glob("*.png"))+list(PUBLIC.glob("*.jpg"))+list(PUBLIC.glob("*.mp4")):
        if path.stat().st_mtime < cutoff: path.unlink(missing_ok=True)


def prepare_market_brief():
    """Turn a no-signal day into a transparent, data-backed market briefing.

    This is deliberately not a substitute signal.  It explains what the radar
    actually observed and why candidates did not earn publication.
    """
    slot = "morning" if os.getenv("FEED_SLOT") == "morning" else "afternoon"
    state_key = "brief_recent_morning" if slot == "morning" else "brief_recent"
    recent=load(state_key, {})
    today=dt.date.today().isoformat()
    if recent.get("date")==today:
        return print("market brief already published today")
    pairs=discovery_pairs()
    rows=[]
    for pair in pairs:
        try: rows.append(snapshot(pair))
        except Exception as exc: print("brief snapshot skipped:", exc)
    if not rows:
        return print("no reliable market data; nothing will be published")

    liquid=sum(row["liq"] >= MIN_LIQUIDITY for row in rows)
    active=sum(row["v1"] >= MIN_VOLUME_1H for row in rows)
    pressure=sum(row["buys"] > row["sells"] for row in rows)
    structured=sum(qualifies(row) for row in rows)
    fail_liq=sum(row["liq"] < MIN_LIQUIDITY for row in rows)
    fail_volume=sum(row["v1"] < MIN_VOLUME_1H for row in rows)
    fail_flow=sum(row["buys"] <= row["sells"] for row in rows)
    median_liq=statistics.median(row["liq"] for row in rows)
    median_vol=statistics.median(row["v1"] for row in rows)
    date_label=dt.date.today().strftime("%d %b %Y").upper() + f" · {slot.upper()}"
    slides=[
        f"THE RADAR BRIEF\n{date_label}\n\nREAL MARKET DATA. ZERO FORCED CALLS.",
        f"{len(rows)} PAIRS SCANNED\n\nMEDIAN LIQUIDITY  {fmt_usd(median_liq)}\nMEDIAN 1H VOLUME  {fmt_usd(median_vol)}",
        f"THE MARKET GATE\n\n{liquid}  PASSED LIQUIDITY\n{active}  PASSED 1H VOLUME\n{pressure}  HAD BUY PRESSURE\n{structured}  PASSED THE FULL STRUCTURE",
        f"WHY CANDIDATES FAILED\n\n{fail_liq}  LOW LIQUIDITY\n{fail_volume}  LOW 1H VOLUME\n{fail_flow}  WEAK BUY/SELL FLOW",
        "NO CLEAN STRUCTURE?\nNO SIGNAL.\n\nSAVE THE BRIEF. FOLLOW THE NEXT MOVE.\nLIVE RADAR IN BIO.",
    ]
    prefix=f"brief_{dt.date.today():%Y%m%d}_{slot}"; files=[]
    for index,text in enumerate(slides):
        png=PUBLIC/f"{prefix}_{index+1}.png"
        render_html.slide_card(text,index,len(slides),png,BRAND.upper())
        jpg=png.with_suffix(".jpg")
        with Image.open(png) as image:image.convert("RGB").save(jpg,"JPEG",quality=92,optimize=True)
        png.unlink(missing_ok=True); files.append(jpg)
    caption_text="\n\n".join((
        f"THE RADAR BRIEF — {date_label}",
        f"Pairs scanned: {len(rows)}\nPassed the complete market-structure gate: {structured}\n"
        f"Median liquidity: {fmt_usd(median_liq)}\nMedian 1h volume: {fmt_usd(median_vol)}",
        "A quiet radar is information. We do not manufacture a call to fill the feed.",
        "Save this briefing and follow for the next qualified signal and its public receipt.",
        "Source: DexScreener public market data at scan time. Not financial advice. DYOR.",
        "#memecoins #onchain #cryptoresearch #solana #basechain",
    ))
    save("ig_next",{"files":[p.name for p in files],"caption":caption_text,"kind":"market_brief",
                    "brief_date":today,"brief_slot":slot,
                    "prepared_at":dt.datetime.now(dt.timezone.utc).isoformat()})
    print("prepared data-backed market brief:", len(rows), "pairs", structured, "qualified")


def has_local_carousel():
    pending = load("ig_next", None)
    if not pending:
        return False
    files = pending.get("files") or []
    if files and all((PUBLIC / name).is_file() for name in files):
        return True
    print("Pending carousel media is missing; rebuilding from current data.")
    (STATE / "ig_next.json").unlink(missing_ok=True)
    return False


def job_instagram_prepare():
    cleanup_media()
    refresh_history()
    if has_local_carousel(): return print("a post is already prepared; publish it before replacing it")
    data=pick_promising(True)
    if not data:
        return prepare_market_brief()
    symbol=re.sub(r"[^A-Za-z0-9_-]","",data["token"]["symbol"][:10]) or "token"
    prefix=f"ig_{dt.datetime.now(dt.timezone.utc):%Y%m%d_%H%M}_{symbol}"; files=render_carousel(data,prefix)
    save("ig_next",{"files":[p.name for p in files],"caption":caption(data),
         "token_key":token_key(data["token"]["chain"],data["token"]["address"]),"score":data["radar_score"],
         "record":{"symbol":data["token"]["symbol"],"chain":data["token"]["chain"],
                   "address":data["token"]["address"],"initial_price":num(data.get("price")),
                   "score":data["radar_score"],"initial_liquidity":data["liquidity"]},
         "prepared_at":dt.datetime.now(dt.timezone.utc).isoformat()})
    print("prepared Instagram carousel:",[p.name for p in files])


AUTHORITY_CAROUSELS=[
    {
        "title":"WHY MOST MEMECOIN CALLS FAIL",
        "slides":[
            "WHY MOST MEMECOIN CALLS FAIL\nTHE CANDLE IS NOT THE SIGNAL",
            "VOLUME WITHOUT LIQUIDITY\nCAN BECOME AN EXIT TRAP\n\nA BIG NUMBER MEANS LITTLE IF THE POOL CANNOT ABSORB A SALE.",
            "BUYERS WITHOUT STRUCTURE\nCAN BE BOT NOISE\n\nCOMPARE BUYS, SELLS, UNIQUE ACTIVITY AND TIME.",
            "OUR RULE\nNO CLEAN STRUCTURE = NO CALL\n\nTHE RADAR CAN STAY SILENT. THAT IS A FEATURE.",
            "WANT THE LIVE RADAR?\nCOMMENT RADAR BELOW\n\nSELECTIVE ALERTS · PUBLIC RECEIPTS",
        ],
        "caption":"Most memecoin alerts start with a green candle. We start with structure.\n\nLiquidity, turnover, buyer pressure, pair age and basic contract risk must agree before a token earns a signal. When they do not, the radar stays silent.\n\nComment RADAR for the live intelligence feed. Follow for evidence-based research and public receipts.\n\nNot financial advice. DYOR.\n\n#memecoins #cryptoresearch #onchain #solana #cryptoeducation",
    },
    {
        "title":"THE 60-SECOND TOKEN CHECK",
        "slides":[
            "THE 60-SECOND TOKEN CHECK\nBEFORE YOU TOUCH THE BUY BUTTON",
            "1 · VERIFY THE CONTRACT\n\nNAMES AND TICKERS CAN BE COPIED. MATCH THE CA WITH OFFICIAL LINKS.",
            "2 · CHECK THE EXIT DOOR\n\nLIQUIDITY MATTERS MORE THAN A SCREENSHOT OF MARKET CAP.",
            "3 · READ THE FLOW\n\nVOLUME + REAL TRANSACTIONS + BUY PRESSURE\n\nONE METRIC ALONE IS NEVER ENOUGH.",
            "WANT THE LIVE RADAR?\nCOMMENT RADAR BELOW\n\nSELECTIVE ALERTS · PUBLIC RECEIPTS",
        ],
        "caption":"A fast market does not excuse a careless entry.\n\nVerify the contract, inspect liquidity and read transaction flow before interacting with a new token. A scanner is a first filter—not a guarantee.\n\nSave this checklist and share it with someone who needs it. Comment RADAR for the live intelligence feed.\n\nNot financial advice.\n\n#cryptosafety #memecoins #onchain #solana #cryptoeducation",
    },
    {
        "title":"MARKET CAP CAN LIE",
        "slides":[
            "MARKET CAP CAN LOOK HUGE\nWHILE THE EXIT DOOR IS TINY",
            "MARKET CAP\nIS PRICE × SUPPLY\n\nIT DOES NOT TELL YOU HOW MUCH CAN ACTUALLY BE SOLD.",
            "LIQUIDITY\nIS THE DEPTH AVAILABLE NOW\n\nLOW DEPTH MEANS MORE SLIPPAGE AND MORE FRAGILE PRICE ACTION.",
            "READ BOTH\nCAP FOR CONTEXT\nLIQUIDITY FOR SURVIVAL\n\nSIGNAL BEFORE NOISE.",
            "WANT THE LIVE RADAR?\nCOMMENT RADAR BELOW\n\nSELECTIVE ALERTS · PUBLIC RECEIPTS",
        ],
        "caption":"Market cap gets the headline. Liquidity determines whether the market can absorb an exit.\n\nWe never evaluate one without the other. Save this post for the next time a chart looks too good to question.\n\nComment RADAR for the live intelligence feed. Not financial advice.\n\n#memecoins #liquidity #cryptoresearch #onchain #cryptoeducation",
    },
    {
        "title":"NO SIGNAL IS A SIGNAL",
        "slides":[
            "NO CLEAN SIGNAL TODAY?\nGOOD.",
            "FORCED ACTIVITY\nIS NOT AN EDGE\n\nPOSTING A TOKEN EVERY DAY REWARDS CONTENT—NOT QUALITY.",
            "PATIENCE FILTERS NOISE\n\nWE WAIT FOR LIQUIDITY, TURNOVER, BUYERS AND MOMENTUM TO ALIGN.",
            "THE RADAR IS ALWAYS ON\nTHE ALERT IS EARNED\n\nFOLLOW THE PUBLIC RECEIPTS.",
            "WANT THE LIVE RADAR?\nCOMMENT RADAR BELOW\n\nSELECTIVE ALERTS · PUBLIC RECEIPTS",
        ],
        "caption":"The market does not owe us a clean setup every day.\n\nA selective radar should be allowed to say nothing. We would rather publish useful research than force a weak token call to fill the calendar.\n\nComment RADAR for the live intelligence feed. Follow the process and the public receipts. Not financial advice.\n\n#memecoins #cryptoresearch #tradingpsychology #onchain #solana",
    },
    {
        "title":"VOLUME IS NOT CONVICTION",
        "slides":[
            "VOLUME IS ATTENTION\nNOT AUTOMATICALLY CONVICTION",
            "ASK WHERE IT SITS\n\nIS VOLUME LARGE RELATIVE TO LIQUIDITY—or JUST A LARGE-LOOKING NUMBER?",
            "ASK WHO IS ACTING\n\nDO TRANSACTIONS AND BUY/SELL FLOW CONFIRM THE MOVE?",
            "THEN ASK IF PRICE AGREES\n\nFLOW + LIQUIDITY + MOMENTUM\nSIGNAL BEFORE NOISE.",
            "WANT THE LIVE RADAR?\nCOMMENT RADAR BELOW\n\nSELECTIVE ALERTS · PUBLIC RECEIPTS",
        ],
        "caption":"High volume can be organic, recycled or short-lived. Context decides which.\n\nOur radar compares volume with liquidity, transaction count, buyer pressure and price momentum before assigning a Cooked Score.\n\nSave this framework. Comment RADAR for the live intelligence feed. Not financial advice.\n\n#onchain #volume #memecoins #cryptoresearch #cryptoeducation",
    },
    {
        "title":"POV: YOU BOUGHT CANDLE NINE",
        "slides":[
            "POV: YOU BOUGHT\nCANDLE NUMBER NINE\n\nBECAUSE THIS ONE LOOKED EARLY",
            "THE CHART BEFORE ENTRY\n+400%\n\nTHE CHART AFTER ENTRY\nA FORENSIC INVESTIGATION",
            "FOMO HIDES THE QUESTIONS\n\nWHO IS SELLING?\nHOW DEEP IS LIQUIDITY?\nWHERE IS INVALIDATION?",
            "THE MARKET CHARGES TUITION\nFOR IMPATIENCE\n\nA MISSED PUMP COSTS ZERO.",
            "WANT THE LIVE RADAR?\nCOMMENT RADAR BELOW\n\nSELECTIVE ALERTS · PUBLIC RECEIPTS",
        ],
        "caption":"The candle feels safest after everyone can see it. That is usually when discipline matters most.\n\nA missed pump costs nothing. A chased entry can become an expensive lesson. Send this to the friend who always buys candle nine.\n\nComment RADAR for the live intelligence feed. Not financial advice.\n\n#cryptomemes #memecoins #fomo #solana #cryptoeducation",
    },
    {
        "title":"CRYPTO RESEARCH STARTER PACK",
        "slides":[
            "CRYPTO RESEARCH\nSTARTER PACK\n\n37 TABS · 12 SCANNERS · 1% BATTERY",
            "OPEN DEXSCREENER\nOPEN THE EXPLORER\nOPEN THE HOLDER MAP\n\nFORGET WHICH TAB HAD THE TOKEN",
            "READ 400 MESSAGES\nIN THE GROUP CHAT\n\nZERO OF THEM CONTAIN THE CONTRACT",
            "THE ACTUAL FIRST STEP\n\nVERIFY THE CA\n\nEVERYTHING ELSE COMES SECOND.",
            "WANT THE LIVE RADAR?\nCOMMENT RADAR BELOW\n\nSELECTIVE ALERTS · PUBLIC RECEIPTS",
        ],
        "caption":"Research does not need to start with 37 tabs. Start by verifying the contract, then inspect liquidity, holders and transaction flow.\n\nSave this before the next ticker collision. Comment RADAR for the live intelligence feed.\n\nNot financial advice.\n\n#cryptomemes #cryptoresearch #memecoins #onchain #solana",
    },
    {
        "title":"THE GROUP CHAT SAID EARLY",
        "slides":[
            "THE GROUP CHAT SAID\nWE ARE STILL EARLY",
            "THE PAIR WAS 11 MINUTES OLD\n\nTHE MESSAGE HAD 19 ROCKET EMOJIS\nAND ZERO RISK DISCLOSURE",
            "EARLY IS NOT A THESIS\n\nLIQUIDITY · HOLDERS · FLOW\nCONTRACT · EXIT PLAN",
            "CONVICTION WITHOUT EVIDENCE\nIS JUST VOLUME\n\nVERIFY BEFORE YOU AMPLIFY.",
            "WANT THE LIVE RADAR?\nCOMMENT RADAR BELOW\n\nSELECTIVE ALERTS · PUBLIC RECEIPTS",
        ],
        "caption":"“Still early” is not evidence. Before repeating a call, verify the contract and understand who can exit before you.\n\nTag the friend who speaks fluent rocket emoji. Comment RADAR for the live intelligence feed.\n\nNot financial advice.\n\n#cryptomemes #memecoins #cryptorisk #onchain #solana",
    },
]


def prepare_authority_carousel():
    """Keep the feed valuable when no token earns a signal."""
    recent=load("authority_recent",{})
    today=dt.date.today().isoformat()
    if recent.get("date")==today:
        return print("authority carousel already published today")
    item=AUTHORITY_CAROUSELS[dt.date.today().toordinal()%len(AUTHORITY_CAROUSELS)]
    prefix=f"authority_{dt.date.today():%Y%m%d}"; files=[]
    for index,text in enumerate(item["slides"]):
        png=PUBLIC/f"{prefix}_{index+1}.png"
        render_html.slide_card(text,index,len(item["slides"]),png,BRAND.upper())
        jpg=png.with_suffix(".jpg")
        with Image.open(png) as image:image.convert("RGB").save(jpg,"JPEG",quality=92,optimize=True)
        png.unlink(missing_ok=True); files.append(jpg)
    save("ig_next",{"files":[p.name for p in files],"caption":item["caption"],"kind":"authority",
                    "authority_title":item["title"],
                    "prepared_at":dt.datetime.now(dt.timezone.utc).isoformat()})
    print("prepared authority carousel:",item["title"])


REEL_LIBRARY=[
    {
        "title":"CANDLE NINE",
        "scenes":[
            "POV: YOU WAITED\nFOR CONFIRMATION",
            "EIGHT GREEN CANDLES LATER\n\nTHIS STILL LOOKS EARLY",
            "A MISSED PUMP\nCOSTS ZERO",
            "COMMENT RADAR\nFOR THE LIVE FEED",
        ],
        "caption":"The market charges tuition for impatience. A missed pump costs zero.\n\nComment RADAR for the live intelligence feed. Not financial advice.\n\n#cryptomemes #memecoins #solana #cryptoeducation",
    },
    {
        "title":"37 TABS",
        "scenes":[
            "CRYPTO RESEARCH\nSTARTER PACK",
            "37 TABS · 12 SCANNERS\n1% BATTERY",
            "STEP ONE WAS ALWAYS\nVERIFY THE CONTRACT",
            "COMMENT RADAR\nSIGNAL BEFORE NOISE",
        ],
        "caption":"Research does not need to start with 37 tabs. Verify the contract first.\n\nComment RADAR for the live intelligence feed. Not financial advice.\n\n#cryptoresearch #cryptomemes #memecoins #onchain",
    },
    {
        "title":"VIRAL IS NOT LIQUID",
        "scenes":[
            "THE TOKEN IS VIRAL",
            "THE EXIT DOOR\nIS STILL TINY",
            "ATTENTION ≠ LIQUIDITY\n\nCHECK THE DEPTH",
            "COMMENT RADAR\nPUBLIC RECEIPTS · NO HYPE",
        ],
        "caption":"Followers measure attention. Liquidity measures the exit door. Never confuse the two.\n\nComment RADAR for the live intelligence feed. Not financial advice.\n\n#liquidity #memecoins #cryptorisk #onchain",
    },
    {
        "title":"STILL EARLY",
        "scenes":[
            "THE GROUP CHAT SAID\nWE ARE STILL EARLY",
            "19 ROCKET EMOJIS\nZERO RISK DISCLOSURE",
            "EARLY IS NOT A THESIS\n\nVERIFY BEFORE YOU AMPLIFY",
            "COMMENT RADAR\nSELECTIVE ON-CHAIN SIGNALS",
        ],
        "caption":"“Still early” is not evidence. Verify before you amplify.\n\nComment RADAR for the live intelligence feed. Not financial advice.\n\n#cryptomemes #memecoins #cryptorisk #solana",
    },
]


def reel_frame(text,index,total,path):
    """Create one crisp 9:16 motion-design scene."""
    width,height=1080,1920
    image=Image.new("RGB",(width,height),"#040710"); draw=ImageDraw.Draw(image,"RGBA")
    for y in range(height):
        draw.line((0,y,width,y),fill=(4,7+y//320,18+y//90,255))
    for radius,alpha in ((760,35),(560,50),(370,70),(190,95)):
        draw.ellipse((540-radius,930-radius,540+radius,930+radius),outline=(42,222,255,alpha),width=4)
    draw.polygon([(540,930),(1080,510),(1080,1360)],fill=(143,74,255,30))
    draw.text((70,70),BRAND.upper(),font=ImageFont.truetype("DejaVuSans-Bold.ttf",42),fill="#FFFFFF")
    draw.text((70,125),"SIGNAL BEFORE NOISE",font=ImageFont.truetype("DejaVuSans-Bold.ttf",24),fill="#53E7FF")
    wrapped=[]
    for paragraph in text.split("\n"):
        wrapped.extend(textwrap.wrap(paragraph,width=20) or [""])
    title_font=ImageFont.truetype("DejaVuSans-Bold.ttf",78 if len(wrapped)<=4 else 64)
    line_height=title_font.size+25; y=830-(len(wrapped)*line_height)//2
    for line in wrapped:
        box=draw.textbbox((0,0),line,font=title_font)
        draw.text(((width-(box[2]-box[0]))/2,y),line,font=title_font,fill="#FFFFFF")
        y+=line_height
    draw.rounded_rectangle((70,1715,1010,1810),radius=30,fill=(7,15,32,230),outline=(62,225,255,175),width=3)
    footer=f"ON-CHAIN INTELLIGENCE   ·   {index+1:02d}/{total:02d}"
    footer_font=ImageFont.truetype("DejaVuSans-Bold.ttf",25); box=draw.textbbox((0,0),footer,font=footer_font)
    draw.text(((width-(box[2]-box[0]))/2,1746),footer,font=footer_font,fill="#65F4C4")
    image.save(path,"PNG",optimize=True)


def render_reel(item,prefix):
    frames=[]
    for index,text in enumerate(item["scenes"]):
        path=PUBLIC/f"{prefix}_{index+1}.png"; reel_frame(text,index,len(item["scenes"]),path); frames.append(path)
    output=PUBLIC/f"{prefix}.mp4"; filters=[]
    for index in range(len(frames)):
        filters.append(f"[{index}:v]scale=1080:1920,format=yuv420p,fade=t=in:st=0:d=0.25,fade=t=out:st=2.15:d=0.25[v{index}]")
    filters.append("".join(f"[v{i}]" for i in range(len(frames)))+f"concat=n={len(frames)}:v=1:a=0[out]")
    command=["ffmpeg","-y","-loglevel","error"]
    for frame in frames: command += ["-loop","1","-t","2.4","-i",str(frame)]
    duration=2.4*len(frames)
    # Original synthetic sound bed: copyright-safe and fully automated. It is
    # intentionally quiet because every Reel must also work with sound off.
    command += ["-f","lavfi","-t",str(duration),"-i",
                f"aevalsrc=0.035*sin(2*PI*55*t)+0.018*sin(2*PI*110*t):s=44100:d={duration}"]
    filters.append(f"[{len(frames)}:a]afade=t=in:st=0:d=0.4,afade=t=out:st={duration-0.5}:d=0.5[aout]")
    command += ["-filter_complex",";".join(filters),"-map","[out]","-map","[aout]","-r","30",
                "-c:v","libx264","-preset","medium","-crf","19","-pix_fmt","yuv420p",
                "-c:a","aac","-b:a","128k","-movflags","+faststart",str(output)]
    subprocess.run(command,check=True,timeout=120)
    for frame in frames: frame.unlink(missing_ok=True)
    return output


def job_reel_prepare():
    cleanup_media(); recent=load("reel_recent",{})
    today=dt.date.today().isoformat()
    if recent.get("date")==today:return print("a Reel was already published today")
    pending=load("ig_reel_next",None)
    if pending:
        output=PUBLIC/pending.get("file","")
        if output.is_file():return print("a Reel is already pending")
        item=next((entry for entry in REEL_LIBRARY if entry["title"]==pending.get("title")),None)
        if not item:raise RuntimeError("Pending Reel template is no longer available")
        render_reel(item,output.stem)
        return print("regenerated pending Reel:",pending["title"])
    item=REEL_LIBRARY[(dt.date.today().toordinal()//2)%len(REEL_LIBRARY)]
    prefix=f"reel_{dt.date.today():%Y%m%d}"; video=render_reel(item,prefix)
    save("ig_reel_next",{"file":video.name,"caption":item["caption"],"title":item["title"],
                         "prepared_at":dt.datetime.now(dt.timezone.utc).isoformat()})
    print("prepared Reel:",item["title"],video.name)


def ig_credentials():
    return (os.getenv("IG_HOST","https://graph.facebook.com").strip().rstrip("/"),
            os.environ["IG_USER_ID"].strip(), os.environ["IG_ACCESS_TOKEN"].strip().strip("\"'"))


def ig_post(host,path,data):
    response=requests.post(f"{host}/{path}",data=data,timeout=45)
    if not response.ok: raise RuntimeError(f"Instagram API {response.status_code}: {response.text[:500].replace(data.get('access_token',''),'***')}")
    return response.json()


def ig_get(host,path,params):
    response=HTTP.get(f"{host}/{path}",params=params,timeout=45)
    if not response.ok:
        raise RuntimeError(f"Instagram API {response.status_code} on {path}: {response.text[:400]}")
    return response.json()


def job_engage():
    """DM the radar link once to people who explicitly comment RADAR."""
    host,user,token=ig_credentials(); seen=set(load("dm_replied",[])); updated=list(seen)
    media=ig_get(host,f"{user}/media",{"fields":"id,timestamp","limit":12,"access_token":token})
    matched=sent=0
    for post in media.get("data") or []:
        post_id=post.get("id")
        if not post_id:continue
        comments=ig_get(host,f"{post_id}/comments",{
            "fields":"id,text,timestamp","limit":100,"access_token":token})
        for comment in comments.get("data") or []:
            comment_id=str(comment.get("id") or "")
            words=set(re.findall(r"[A-Za-z0-9_]+",str(comment.get("text") or "").upper()))
            if not comment_id or "RADAR" not in words or comment_id in seen:continue
            matched+=1
            if DRY_RUN:
                print("[DRY RUN] would send RADAR DM for comment",comment_id);continue
            message=("You’re in. Here is the live IS IT COOKED? on-chain radar:\n"
                     "https://t.me/radarmemecoinss\n\nSelective signals, contract checks and public receipts. "
                     "Always verify the contract. Not financial advice.")
            ig_post(host,f"{comment_id}/private_replies",{"message":message,"access_token":token})
            seen.add(comment_id); updated.append(comment_id); sent+=1
    if not DRY_RUN and updated:save("dm_replied",list(dict.fromkeys(updated))[-2000:])
    print(f"RADAR comment funnel: {matched} new match(es), {sent} DM(s) sent")


def ig_wait(host,container,token,tries=30):
    for attempt in range(tries):
        response=HTTP.get(f"{host}/{container}",params={"fields":"status_code","access_token":token},timeout=30)
        status=(response.json() if response.ok else {}).get("status_code")
        if status=="FINISHED": return
        if status in {"ERROR","EXPIRED"}: raise RuntimeError(f"Instagram media container {status}")
        time.sleep(min(10,3+attempt//5))
    raise TimeoutError(f"Instagram container {container} did not finish in time")


def publish_carousel(urls,text):
    host,user,token=ig_credentials(); children=[]
    for url in urls:
        child=ig_post(host,f"{user}/media",{"image_url":url,"is_carousel_item":"true","access_token":token})["id"]
        ig_wait(host,child,token); children.append(child)
    parent=ig_post(host,f"{user}/media",{"media_type":"CAROUSEL","children":",".join(children),"caption":text,"access_token":token})["id"]
    ig_wait(host,parent,token); return ig_post(host,f"{user}/media_publish",{"creation_id":parent,"access_token":token})


def publish_story(url):
    host,user,token=ig_credentials(); cid=ig_post(host,f"{user}/media",{"image_url":url,"media_type":"STORIES","access_token":token})["id"]
    ig_wait(host,cid,token); return ig_post(host,f"{user}/media_publish",{"creation_id":cid,"access_token":token})


def publish_reel(url,text):
    host,user,token=ig_credentials()
    cid=ig_post(host,f"{user}/media",{"video_url":url,"media_type":"REELS","caption":text,
                                      "share_to_feed":"true","access_token":token})["id"]
    ig_wait(host,cid,token,tries=60)
    return ig_post(host,f"{user}/media_publish",{"creation_id":cid,"access_token":token})


def media_url(filename):
    repo=os.environ.get("GITHUB_REPOSITORY","isitcooked/isitcooked-bot"); version=quote(os.getenv("MEDIA_VERSION","latest"),safe="")
    if str(filename).lower().endswith(".mp4"):
        # raw.githubusercontent.com serves MP4 as application/octet-stream.
        # jsDelivr preserves video/mp4, byte ranges and public HTTPS access,
        # which Meta requires when ingesting a Reel container.
        return f"https://cdn.jsdelivr.net/gh/{repo}@{MEDIA_BRANCH}/{quote(filename)}?v={version}"
    return f"https://raw.githubusercontent.com/{repo}/{MEDIA_BRANCH}/{quote(filename)}?v={version}"


def wait_public(urls,tries=18):
    for attempt in range(tries):
        try:
            if all((lambda r:r.ok and r.headers.get("content-type","").startswith("image/") and len(r.content)>1000)(HTTP.get(u,timeout=20)) for u in urls): return
        except requests.RequestException: pass
        time.sleep(min(15,3+attempt))
    raise TimeoutError("media images did not become publicly available")


def wait_public_video(url,tries=24):
    for attempt in range(tries):
        try:
            response=HTTP.get(url,timeout=25)
            if response.ok and response.headers.get("content-type","").startswith("video/") and len(response.content)>10_000:return
        except requests.RequestException:pass
        time.sleep(min(15,3+attempt))
    raise TimeoutError("Reel video did not become publicly available")


def update_stats(post=False):
    stats=load("stats",{"posts":0,"stories":0}); key="posts" if post else "stories"; stats[key]=stats.get(key,0)+1
    stats["last_publish"]=dt.datetime.now(dt.timezone.utc).isoformat(); save("stats",stats)


def job_instagram_publish():
    pending=load("ig_next",None)
    if not pending or not pending.get("files"): return print("nothing prepared")
    urls=[media_url(f) for f in pending["files"]]
    if DRY_RUN: return print("[DRY RUN INSTAGRAM]",pending["caption"],urls)
    wait_public(urls); result=publish_carousel(urls,pending["caption"])
    if not result.get("id"): raise RuntimeError("Instagram did not confirm publication")
    if pending.get("token_key"):
        seen=load("seen",[]); seen.append(pending["token_key"]); save("seen",list(dict.fromkeys(seen))[-1000:])
    record = dict(pending.get("record") or {})
    if record.get("address") and record.get("initial_price"):
        record.update({"published_at":time.time(),"checkpoints":{},"peak_change_pct":0})
        history=load("history",[])
        history.append(record)
        save("history",history[-100:])
    update_stats(True); (STATE/"ig_next.json").unlink(missing_ok=True); print("carousel published:",result["id"])
    if pending.get("kind")=="authority":
        save("authority_recent",{"date":dt.date.today().isoformat(),"title":pending.get("authority_title"),
                                 "published_at":dt.datetime.now(dt.timezone.utc).isoformat()})
    if pending.get("kind")=="market_brief":
        state_key = "brief_recent_morning" if pending.get("brief_slot") == "morning" else "brief_recent"
        save(state_key,{"date":pending.get("brief_date",dt.date.today().isoformat()),
                             "published_at":dt.datetime.now(dt.timezone.utc).isoformat()})


def job_reel_publish():
    pending=load("ig_reel_next",None)
    if not pending or not pending.get("file"):return print("no Reel prepared")
    url=media_url(pending["file"])
    if DRY_RUN:return print("[DRY RUN REEL]",pending["caption"],url)
    wait_public_video(url); result=publish_reel(url,pending["caption"])
    if not result.get("id"):raise RuntimeError("Instagram did not confirm Reel publication")
    save("reel_recent",{"date":dt.date.today().isoformat(),"title":pending.get("title"),
                        "published_at":dt.datetime.now(dt.timezone.utc).isoformat()})
    update_stats(True); (STATE/"ig_reel_next.json").unlink(missing_ok=True); print("Reel published:",result["id"])


def job_scoreboard_prepare():
    """Publish the full tracked record. Wins and losses are both included."""
    cleanup_media(); history=refresh_history()
    if has_local_carousel(): return print("another carousel is already pending")
    tracked=[r for r in history if "24h" in r.get("checkpoints",{})]
    if len(tracked) < 3: return print("not enough 24h outcomes for The Receipt yet")
    recent=tracked[-20:]; changes=[r["checkpoints"]["24h"]["change_pct"] for r in recent]
    positives=sum(change>0 for change in changes); median=statistics.median(changes)
    best=max(recent,key=lambda r:r["checkpoints"]["24h"]["change_pct"])
    worst=min(recent,key=lambda r:r["checkpoints"]["24h"]["change_pct"])
    slides=[
        "THE RECEIPT\nOUR PUBLIC 24H SCORECARD",
        f"{len(recent)} SIGNALS TRACKED\n{positives} POSITIVE AT ~24H\nMEDIAN: {median:+.1f}%",
        f"BEST OBSERVED\n${best['symbol']}  {best['checkpoints']['24h']['change_pct']:+.1f}%\n\n"
        f"WORST OBSERVED\n${worst['symbol']}  {worst['checkpoints']['24h']['change_pct']:+.1f}%",
        "NO DELETED LOSSES.\nNO CHERRY-PICKING.\nEVERY SIGNAL STAYS IN THE RECORD.",
    ]
    prefix=f"receipt_{dt.date.today():%Y%m%d}"; files=[]
    for index,text in enumerate(slides):
        png=PUBLIC/f"{prefix}_{index+1}.png"; render_html.slide_card(text,index,len(slides),png,BRAND.upper())
        jpg=png.with_suffix(".jpg")
        with Image.open(png) as image:image.convert("RGB").save(jpg,"JPEG",quality=92,optimize=True)
        png.unlink(missing_ok=True); files.append(jpg)
    caption_text="\n\n".join((
        "THE RECEIPT — our tracked 24-hour record.",
        f"Signals tracked: {len(recent)}\nPositive at ~24h: {positives}/{len(recent)}\nMedian change: {median:+.1f}%",
        "Every eligible signal remains in the dataset. Losses are not deleted and results are not guarantees.",
        "Follow the live radar through the link in bio. Not financial advice. DYOR.",
        "#memecoins #cryptoresearch #onchain #transparency #cryptoeducation",
    ))
    save("ig_next",{"files":[p.name for p in files],"caption":caption_text,"kind":"scoreboard",
                    "prepared_at":dt.datetime.now(dt.timezone.utc).isoformat()})
    print("prepared The Receipt scoreboard")


TIPS=[("Liquidity is your exit door","If a pool is tiny, selling moves the price against you. Check liquidity first."),
      ("Verify the contract","Names can be copied. Match the contract with official links before interacting."),
      ("Volume can be faked","Compare volume, liquidity and real transactions. One number never tells the whole story."),
      ("Buy pressure needs context","More buys can signal momentum, but liquidity and holder distribution still matter."),
      ("Fresh means fragile","New pairs have little history. Never confuse momentum with safety."),
      ("Scanner flags need review","A scanner is a first filter, not a guarantee. Confirm findings on the explorer."),
      ("Protect your main wallet","Use a separate low-balance wallet and revoke old approvals regularly.")]


def story_jpg(png):
    png=pathlib.Path(png); jpg=png.with_suffix(".jpg")
    with Image.open(png) as image: image.convert("RGB").save(jpg,"JPEG",quality=92,optimize=True)
    png.unlink(missing_ok=True); return jpg


def job_story_tip_prepare():
    cleanup_media(); history=refresh_history(); day=dt.date.today().toordinal()
    if day % 4 == 0:
        stats=load("stats",{"posts":0}); kinds=("whatwedo","counter","teaser"); kind=kinds[(day//4)%len(kinds)]
        png=PUBLIC/f"story_growth_{dt.date.today():%Y%m%d}.png"
        render_html.story_promo_card(kind,{"scanned":len(load("seen",[])),"checks":len(history) or stats.get("posts",0)},png,BRAND.upper())
        label=f"growth:{kind}"
    else:
        index=day%len(TIPS); title,body=TIPS[index]; png=PUBLIC/f"story_tip_{dt.date.today():%Y%m%d}.png"
        render_html.story_tip_card({"n":index+1,"title":title,"body":body},png,BRAND.upper()); label=f"tip:{title}"
    jpg=story_jpg(png); save("ig_story_next",{"file":jpg.name,"kind":"education"}); print("prepared story:",label)


def job_story_radar_prepare():
    cleanup_media(); refresh_history(); data=pick_promising(False)
    if not data:return
    token=data["token"]; symbol=re.sub(r"[^A-Za-z0-9_-]","",token["symbol"][:10]) or "token"
    prefix=f"story_radar_{dt.datetime.now(dt.timezone.utc):%Y%m%d_%H%M}_{symbol}"; logo=get_logo(data,prefix); palette=design.logo_palette(Image.open(logo)); m={k:v for k,v,_ in data["metrics"]}
    payload={"symbol":token["symbol"].upper()[:12],"chain":token["chain_name"],"chain_key":token["chain"],"verdict":verdict(data),
             "stats":[("1h volume",m["1H VOLUME"]),("Liquidity",m["LIQUIDITY"]),("Radar score",f"{data['radar_score']}/100")],
             "ca":short_ca(token["address"]),"updated":dt.datetime.now(dt.timezone.utc).strftime("%H:%M UTC")}
    png=PUBLIC/f"{prefix}.png"; render_html.story_radar_card(payload,logo,palette,png,BRAND.upper()); jpg=story_jpg(png)
    save("ig_story_next",{"file":jpg.name,"kind":"radar"}); print("prepared radar story:",token["symbol"])


def job_story_publish():
    pending=load("ig_story_next",None)
    if not pending or not pending.get("file"):return print("no story prepared")
    url=media_url(pending["file"])
    if DRY_RUN:return print("[DRY RUN STORY]",url)
    wait_public([url]); result=publish_story(url)
    if not result.get("id"):raise RuntimeError("Instagram did not confirm story publication")
    update_stats(False); (STATE/"ig_story_next.json").unlink(missing_ok=True); print("story published:",result["id"])


def job_diagnose():
    host,user,token=ig_credentials(); response=HTTP.get(f"{host}/{user}",params={"fields":"username,name","access_token":token},timeout=30)
    print("Instagram account:",response.status_code,response.text.replace(token,"***")[:700])
    print("DRY_RUN:",DRY_RUN,"chains:",CHAINS,"minimum score:",MIN_SCORE)


JOBS={"instagram_prepare":job_instagram_prepare,"instagram_publish":job_instagram_publish,
      "reel_prepare":job_reel_prepare,"reel_publish":job_reel_publish,
      "engage":job_engage,
      "scoreboard_prepare":job_scoreboard_prepare,
      "story_tip_prepare":job_story_tip_prepare,"story_radar_prepare":job_story_radar_prepare,
      "story_publish":job_story_publish,"diagnose":job_diagnose}

if __name__=="__main__":
    if len(sys.argv)!=2 or sys.argv[1] not in JOBS:raise SystemExit("Usage: python bot.py "+"|".join(JOBS))
    JOBS[sys.argv[1]]()
