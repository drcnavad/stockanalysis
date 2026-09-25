"""
news_relevance.py
-----------------
Decides whether a news headline/summary is really about a given ticker (used by sentiment_analysis.ipynb).

Old rule matched the FIRST WORD of the company name, so "advanced" (AMD), "applied" (APLD), "super" (SMCI),
"rocket" (RKLB), "strategy" (MSTR), "circle" (CRCL), "unity" (U), "unusual" (UMAC) matched unrelated news.

New rule - an article is relevant when ANY of these holds:
  1. explicit ticker form: $TICK, (TICK), NASDAQ:TICK / NYSE: TICK, "TICK stock" / "TICK shares"
  2. the bare ticker as an upper-case word, unless the ticker is also a common word/acronym (AMBIGUOUS_TICKERS)
  3. a distinctive alias (full company name or curated brand name), matched as whole words
     - aliases in CASE_SENSITIVE_ALIASES must match the exact case ("Meta", "Uber", "Affirm", ...)
     - a tuple alias means ALL of its terms must appear (e.g. ("Circle", "USDC"))
"""
import re

from sector_mapping import symbol_name

SUFFIXES = {"inc", "inc.", "corp", "corp.", "corporation", "co", "co.", ".inc", "ltd", "ltd.",
            "plc", "group", "holding", "holdings", "company", "companies", "nv", "n.v."}

AMBIGUOUS_TICKERS = {"U", "UI", "IT", "APP", "NOW", "ARM", "TEAM", "HOOD", "FIG", "CRM", "COIN", "SNOW", "SHOP",
                     "DASH", "MU", "ZS", "TEM", "HAL", "UBER", "META", "MARA", "HIMS", "SOFI", "A",
                     "APA", "FANG", "URI",   # 2026-09-24 U91 additions: APA (psych. assoc.), FANG (= FAANG stocks), URI (web term)
                     "CLS", "LITE"}          # 2026-09-24 U96 additions: CLS (CLS Bank, Cumulative Layout Shift), LITE (the word "lite")

ALIASES = {
    "AAPL": ["Apple"], "ADBE": ["Adobe"], "AFRM": ["Affirm Holdings", "Affirm"], "AMZN": ["Amazon"],
    "ANET": ["Arista Networks", "Arista"], "AMD": ["Advanced Micro Devices"], "APP": ["AppLovin"],
    "AVAV": ["AeroVironment"], "AVGO": ["Broadcom"], "BIIB": ["Biogen"], "BKR": ["Baker Hughes"],
    "CDNS": ["Cadence Design"], "COIN": ["Coinbase"], "CRM": ["Salesforce"], "CRWV": ["CoreWeave"],
    "CVLT": ["Commvault"], "DASH": ["DoorDash"], "ENPH": ["Enphase"], "FIG": ["Figma"], "FTNT": ["Fortinet"],
    "GOOGL": ["Alphabet", "Google"], "GTLB": ["GitLab"], "HAL": ["Halliburton"],
    "HIMS": ["Hims & Hers", "Hims and Hers", "Hims&Hers"], "HOOD": ["Robinhood"], "INTU": ["Intuit", "TurboTax"],
    "IREN": ["Iris Energy", "IREN Limited"], "LRCX": ["Lam Research"], "MCK": ["McKesson"], "MRK": ["Merck"],
    "META": ["Meta Platforms", "Meta", "Facebook"], "MRVL": ["Marvell"], "MSFT": ["Microsoft"], "MU": ["Micron"],
    "NFLX": ["Netflix"], "NOW": ["ServiceNow"], "NVDA": ["Nvidia"], "ORCL": ["Oracle"], "PANW": ["Palo Alto Networks"],
    "RCL": ["Royal Caribbean"], "REGN": ["Regeneron"], "SLB": ["Schlumberger"], "SNOW": ["Snowflake"],
    "SOFI": ["SoFi"], "TEAM": ["Atlassian"], "TSLA": ["Tesla"], "UBER": ["Uber"], "UI": ["Ubiquiti"],
    "UNH": ["UnitedHealth"], "UPST": ["Upstart"], "VEEV": ["Veeva"], "VRT": ["Vertiv"], "ZS": ["Zscaler"],
    "UMAC": ["Unusual Machines"], "NOC": ["Northrop Grumman", "Northrop"], "MDB": ["MongoDB"],
    "LMT": ["Lockheed Martin", "Lockheed"], "U": ["Unity Software", "Unity Technologies", ("Unity", "stock")], "TWLO": ["Twilio"],
    "CRCL": ["Circle Internet", ("Circle", "USDC"), ("Circle", "stablecoin"), ("Circle", "stablecoins")], "ACHR": ["Archer Aviation"],
    "ALAB": ["Astera Labs"], "APLD": ["Applied Digital"], "ARM": ["Arm Holdings", "Arm"], "ASTS": ["AST SpaceMobile"],
    "CIFR": ["Cipher Mining", "Cipher Digital"], "CVNA": ["Carvana"], "IONQ": ["IonQ"], "JOBY": ["Joby Aviation", "Joby"],
    "MARA": ["MARA Holdings", "Marathon Digital"], "MSTR": ["MicroStrategy", "Strategy Inc", "Michael Saylor", "Saylor"],
    "NVTS": ["Navitas Semiconductor", "Navitas"], "RDDT": ["Reddit"], "RKLB": ["Rocket Lab"], "SHOP": ["Shopify"],
    "SMCI": ["Super Micro", "Supermicro"], "SOUN": ["SoundHound"], "TEM": ["Tempus AI"], "QQQ": ["Invesco QQQ"],
    # U91 additions (2026-09-24)
    "APA": ["APA Corporation", "APA Corp", "Apache Corporation"], "OXY": ["Occidental Petroleum", "Occidental"],
    "TRGP": ["Targa Resources", "Targa"], "DVN": ["Devon Energy"], "FANG": ["Diamondback Energy"],
    "COF": ["Capital One"], "C": ["Citigroup", "Citibank", ("Citi", "bank")], "BE": ["Bloom Energy"], "BA": ["Boeing"],
    "URI": ["United Rentals"], "PH": ["Parker-Hannifin", "Parker Hannifin"], "FCX": ["Freeport-McMoRan", "Freeport McMoRan"],
    "LYB": ["LyondellBasell"],
    # U96 additions (2026-09-24)
    "CRDO": ["Credo Technology", ("Credo", "semiconductor"), ("Credo", "connectivity"), ("Credo", "AI")], "NBIS": ["Nebius"],
    "LITE": ["Lumentum"], "CLS": ["Celestica"], "RBRK": ["Rubrik"],
}
CASE_SENSITIVE_ALIASES = {"Meta", "Uber", "Affirm", "Joby", "Navitas", "Oracle", "Apple", "Amazon", "Tempus AI",
                          "Arm Holdings", "Arm", "Circle", "Unity", "Merck", "Northrop", "Lockheed", "Intuit",
                          "Occidental", "Targa", "Boeing", "Citi", "Credo"}


def clean_company_name(name):
    """Company name without punctuation and legal suffixes (Inc, Corp, ...)."""
    name = re.sub(r"\.com\b", "", name, flags=re.IGNORECASE)
    name = re.sub(r"[^\w\s&]", "", name)
    return " ".join(w for w in name.split() if w.lower() not in SUFFIXES).strip()


def aliases_for(symbol):
    """Curated aliases first, then the full cleaned company name (never a single generic first word)."""
    out = list(ALIASES.get(symbol, []))
    full = clean_company_name(symbol_name.get(symbol, ""))
    if full and full not in out and len(full.split()) >= 2:
        out.append(full)
    return out


def search_query(symbol):
    """NewsAPI query: the most distinctive alias as an exact phrase, OR the ticker when it is not a common word."""
    names = [a for a in aliases_for(symbol) if isinstance(a, str)]
    q = f'"{names[0]}"' if names else symbol
    if names and symbol not in AMBIGUOUS_TICKERS and len(symbol) >= 3:
        q += f" OR {symbol}"
    return q


def _has_phrase(phrase, text, case_sensitive):
    """True when the phrase appears as a whole word in the text."""
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.search(r"(?<![\w$])" + re.escape(phrase) + r"(?!\w)", text, flags) is not None


def is_relevant(symbol, text):
    """True when the article text clearly refers to `symbol`."""
    if not isinstance(text, str) or not text:
        return False
    t = re.escape(symbol)
    strong = (rf"\${t}\b", rf"\({t}\)", rf"\b(?:NASDAQ|NYSE|Nasdaq|NYSEARCA|AMEX)\s*:\s*{t}\b", rf"\b{t}\s+(?:stock|shares)\b")
    if any(re.search(p, text) for p in strong):
        return True
    if symbol not in AMBIGUOUS_TICKERS and len(symbol) >= 3 and re.search(rf"(?<![\w$]){t}(?!\w)", text):
        return True
    for alias in aliases_for(symbol):
        terms = alias if isinstance(alias, tuple) else (alias,)
        if all(_has_phrase(a, text, a in CASE_SENSITIVE_ALIASES) for a in terms):
            return True
    return False
