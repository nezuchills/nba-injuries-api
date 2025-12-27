from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from typing import Optional, Dict, Any, List, Tuple
import os
import json
import requests
from bs4 import BeautifulSoup
import unicodedata
import re

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
#                    HELPERS NOM / MATCHING
# ============================================================

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def _normalize_str(s: str) -> str:
    s = s or ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    tokens = [t for t in s.split(" ") if t and t not in _SUFFIXES]
    return " ".join(tokens)


# ============================================================
#                       BALLDONTLIE
# ============================================================

def _get_balldontlie_api_key() -> str:
    api_key = os.getenv("BALLDONTLIE_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="BALLDONTLIE_API_KEY is not set on the server")
    return api_key


def _call_balldontlie(path: str, params: Optional[Dict[str, Any]] = None, timeout: int = 12) -> Dict[str, Any]:
    api_key = _get_balldontlie_api_key()
    base_url = "https://api.balldontlie.io"
    url = f"{base_url}{path}"
    headers = {"Authorization": api_key}

    try:
        resp = requests.get(url, headers=headers, params=params or {}, timeout=timeout)
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Error calling BallDontLie: {e}")

    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=f"BallDontLie error: {resp.text[:200]}")

    return resp.json()


def _map_bdl_player(p: Dict[str, Any]) -> Dict[str, Any]:
    team = p.get("team") or {}
    full_name = p.get("full_name")
    if not full_name:
        first = (p.get("first_name") or "").strip()
        last = (p.get("last_name") or "").strip()
        full_name = f"{first} {last}".strip()

    return {
        "id": p.get("id"),
        "full_name": full_name,
        "first_name": p.get("first_name"),
        "last_name": p.get("last_name"),
        "position": p.get("position"),
        "team": {
            "id": team.get("id"),
            "name": team.get("full_name") or team.get("name"),
            "abbreviation": team.get("abbreviation"),
            "city": team.get("city"),
        },
    }


def _map_bdl_injury(item: Dict[str, Any]) -> Dict[str, Any]:
    player = item.get("player") or {}
    team = player.get("team") or {}

    full_name = player.get("full_name")
    if not full_name:
        first = player.get("first_name") or ""
        last = player.get("last_name") or ""
        full_name = f"{first} {last}".strip() or None

    team_name = team.get("full_name") or team.get("name")

    return {
        "player_id": player.get("id"),
        "player_name": full_name,
        "team_id": team.get("id"),
        "team_name": team_name,
        "status": item.get("status"),
        "injury": item.get("injury"),
        "description": item.get("description"),
        "return_date": item.get("return_date"),
        "last_update": item.get("updated_at") or item.get("created_at"),
        "source": "balldontlie",
    }


# ============================================================
#           CACHE JOUEURS ACTIFS (AUTOCOMPLÉTION)
# ============================================================

ACTIVE_PLAYERS: List[Dict[str, Any]] = []
ACTIVE_PLAYERS_BY_ID: Dict[int, Dict[str, Any]] = {}
ACTIVE_PLAYERS_LOADED: bool = False


def _load_active_players() -> None:
    global ACTIVE_PLAYERS, ACTIVE_PLAYERS_BY_ID, ACTIVE_PLAYERS_LOADED
    if ACTIVE_PLAYERS_LOADED:
        return

    players: List[Dict[str, Any]] = []
    by_id: Dict[int, Dict[str, Any]] = {}

    cursor: Optional[int] = None
    while True:
        params: Dict[str, Any] = {"per_page": 100}
        if cursor is not None:
            params["cursor"] = cursor

        resp = _call_balldontlie("/v1/players/active", params=params)
        data = resp.get("data", []) or []
        meta = resp.get("meta", {}) or {}

        for p in data:
            mapped = _map_bdl_player(p)
            players.append(mapped)
            pid = mapped.get("id")
            if pid is not None:
                by_id[int(pid)] = mapped

        cursor = meta.get("next_cursor")
        if not cursor:
            break

    ACTIVE_PLAYERS = players
    ACTIVE_PLAYERS_BY_ID = by_id
    ACTIVE_PLAYERS_LOADED = True


def _get_player_from_cache(player_id: int) -> Dict[str, Any]:
    _load_active_players()
    p = ACTIVE_PLAYERS_BY_ID.get(int(player_id))
    if not p:
        raise HTTPException(status_code=404, detail=f"Active player_id not found: {player_id}")
    return p


def _get_bdl_injuries_for_player_id(player_id: int) -> List[Dict[str, Any]]:
    raw = _call_balldontlie("/v1/player_injuries", params={"per_page": 100})
    data = raw.get("data", []) or []

    out: List[Dict[str, Any]] = []
    for item in data:
        pl = (item.get("player") or {})
        if pl.get("id") == player_id:
            out.append(_map_bdl_injury(item))
    return out


@app.get("/players/active/local")
def players_active_local() -> Dict[str, Any]:
    _load_active_players()
    return {"source": "balldontlie", "count": len(ACTIVE_PLAYERS), "players": ACTIVE_PLAYERS}


# ============================================================
#                            ESPN
# ============================================================

ESPN_INJURIES_URL = "https://www.espn.com/nba/injuries"


def _fetch_espn_html() -> str:
    try:
        resp = requests.get(ESPN_INJURIES_URL, timeout=12, headers={"User-Agent": "Mozilla/5.0"})
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Error calling ESPN: {e}")

    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=f"ESPN error: {resp.text[:200]}")

    return resp.text


def _parse_espn_injuries(html: str) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    results: List[Dict[str, Any]] = []

    tables = soup.find_all("table")
    for table in tables:
        headers = [th.get_text(strip=True) for th in table.find_all("th")]
        if not headers:
            continue

        wanted = ["NAME", "POS", "EST. RETURN DATE", "STATUS", "COMMENT"]
        if not all(h in headers for h in wanted):
            continue

        idx_name = headers.index("NAME")
        idx_pos = headers.index("POS")
        idx_return = headers.index("EST. RETURN DATE")
        idx_status = headers.index("STATUS")
        idx_comment = headers.index("COMMENT")

        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 5:
                continue

            name = cells[idx_name].get_text(strip=True)
            pos = cells[idx_pos].get_text(strip=True)
            est_return = cells[idx_return].get_text(strip=True)
            status = cells[idx_status].get_text(strip=True)
            comment = cells[idx_comment].get_text(strip=True)

            if not name or name.upper() == "NAME":
                continue

            results.append(
                {
                    "player_name": name,
                    "position": pos,
                    "est_return_date": est_return,
                    "status": status,
                    "comment": comment,
                    "source": "espn",
                }
            )

    return results


# ============================================================
#                            CBS
# ============================================================

CBS_INJURIES_URL = "https://www.cbssports.com/nba/injuries/"


def _fetch_cbs_html() -> str:
    try:
        resp = requests.get(CBS_INJURIES_URL, timeout=12, headers={"User-Agent": "Mozilla/5.0"})
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Error calling CBS: {e}")

    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=f"CBS error: {resp.text[:200]}")

    return resp.text


def _clean_cbs_player_name(raw: str) -> str:
    s = (raw or "").strip()
    split_idx = None
    for i in range(1, len(s)):
        if s[i - 1].islower() and s[i].isupper():
            split_idx = i
            break
    if split_idx is not None:
        full = s[split_idx:].strip()
        if full:
            return full
    return s


def _parse_cbs_injuries(html: str) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    results: List[Dict[str, Any]] = []

    tables = soup.find_all("table")
    for table in tables:
        headers = [th.get_text(strip=True) for th in table.find_all("th")]
        if not headers:
            continue

        wanted = ["Player", "Position", "Updated", "Injury", "Injury Status"]
        if not all(h in headers for h in wanted):
            continue

        idx_name = headers.index("Player")
        idx_pos = headers.index("Position")
        idx_updated = headers.index("Updated")
        idx_injury = headers.index("Injury")
        idx_status = headers.index("Injury Status")

        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 5:
                continue

            raw_name = cells[idx_name].get_text(strip=True)
            name = _clean_cbs_player_name(raw_name)
            pos = cells[idx_pos].get_text(strip=True)
            updated = cells[idx_updated].get_text(strip=True)
            injury = cells[idx_injury].get_text(strip=True)
            status = cells[idx_status].get_text(strip=True)

            if not name or name.upper() == "PLAYER":
                continue

            results.append(
                {
                    "player_name": name,
                    "position": pos,
                    "updated": updated,
                    "injury": injury,
                    "status": status,
                    "source": "cbs",
                }
            )

    return results


# ============================================================
#                      NBC (Rotoworld/NBC)
# ============================================================

# Feed fantasy (souvent bruité / pas toujours le joueur) [web:14]
NBC_FANTASY_PLAYER_NEWS_URL = "https://www.nbcsports.com/fantasy/basketball/player-news"

# Page NBA Player News (non-fantasy) [web:383]
NBC_NBA_PLAYER_NEWS_URL = "https://www.nbcsports.com/nba/nba/player-news"

# Pages "Team player news" (format /nba/{team-slug}/player-news) [web:395]
NBC_TEAM_PLAYER_NEWS_TEMPLATE = "https://www.nbcsports.com/nba/{team_slug}/player-news"

TEAM_SLUG_BY_ABBR = {
    "ATL": "atlanta-hawks",
    "BOS": "boston-celtics",
    "BKN": "brooklyn-nets",
    "CHA": "charlotte-hornets",
    "CHI": "chicago-bulls",
    "CLE": "cleveland-cavaliers",
    "DAL": "dallas-mavericks",
    "DEN": "denver-nuggets",
    "DET": "detroit-pistons",
    "GSW": "golden-state-warriors",
    "HOU": "houston-rockets",
    "IND": "indiana-pacers",
    "LAC": "los-angeles-clippers",
    "LAL": "los-angeles-lakers",
    "MEM": "memphis-grizzlies",
    "MIA": "miami-heat",
    "MIL": "milwaukee-bucks",
    "MIN": "minnesota-timberwolves",
    "NOP": "new-orleans-pelicans",
    "NYK": "new-york-knicks",
    "OKC": "oklahoma-city-thunder",
    "ORL": "orlando-magic",
    "PHI": "philadelphia-76ers",
    "PHX": "phoenix-suns",
    "POR": "portland-trail-blazers",
    "SAC": "sacramento-kings",
    "SAS": "san-antonio-spurs",
    "TOR": "toronto-raptors",
    "UTA": "utah-jazz",
    "WAS": "washington-wizards",
}


def _fetch_nbc_html(url: str) -> str:
    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Error calling NBC: {e}")

    if resp.status_code != 200:
        # On ne raise pas systématiquement ici, car on essaye plusieurs URLs
        return ""

    return resp.text


def _extract_nbc_matches_from_html(html: str, player_full_name: str, max_items: int = 2) -> List[Dict[str, Any]]:
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)
    lines = [l for l in text.split("\n") if l.strip()]

    norm_query = _normalize_str(player_full_name)
    results: List[Dict[str, Any]] = []

    # Limite après un header si présent (mais on reste robuste)
    start_idx = 0
    for idx, line in enumerate(lines):
        if _normalize_str(line) in {_normalize_str("NBA Player News"), _normalize_str("Player News")}:
            start_idx = idx
            break

    i = start_idx
    while i < len(lines) and len(results) < max_items:
        if norm_query and norm_query in _normalize_str(lines[i]):
            headline = lines[i].strip()
            ctx = []
            j = i + 1
            while j < len(lines) and len(ctx) < 6:
                l2 = lines[j].strip()
                if not l2:
                    break
                if _normalize_str(l2) == _normalize_str("Load More"):
                    break
                ctx.append(l2)
                j += 1

            results.append(
                {
                    "headline": headline,
                    "summary": " ".join(ctx).strip(),
                }
            )
            i = j
        else:
            i += 1

    return results


def _find_nbc_news_for_player(player: Dict[str, Any], max_items: int = 2) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    Amélioration : on essaye plusieurs URLs NBC dans cet ordre :
    1) Team player-news (si team abbr connue)
    2) NBA Player News (non-fantasy)
    3) Fantasy player-news
    """
    full_name = player.get("full_name") or ""
    team = player.get("team") or {}
    abbr = (team.get("abbreviation") or "").upper()

    attempted_urls: List[str] = []
    urls_to_try: List[str] = []

    team_slug = TEAM_SLUG_BY_ABBR.get(abbr)
    if team_slug:
        urls_to_try.append(NBC_TEAM_PLAYER_NEWS_TEMPLATE.format(team_slug=team_slug))

    urls_to_try.append(NBC_NBA_PLAYER_NEWS_URL)
    urls_to_try.append(NBC_FANTASY_PLAYER_NEWS_URL)

    for url in urls_to_try:
        attempted_urls.append(url)
        html = _fetch_nbc_html(url)
        matches = _extract_nbc_matches_from_html(html, full_name, max_items=max_items)
        if matches:
            # on annote la source URL pour debug
            for m in matches:
                m["source"] = "nbc"
                m["url"] = url
            return matches, attempted_urls

    return [], attempted_urls


# ============================================================
#                  ENDPOINTS API (JSON)
# ============================================================

@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/")
def root():
    return {"message": "NBA injuries API is running"}


@app.get("/injuries/by-player-id")
def injuries_by_player_id(player_id: int) -> Dict[str, Any]:
    p = _get_player_from_cache(player_id)
    full_name = p.get("full_name") or ""
    player_norm = _normalize_str(full_name)

    espn_all = _parse_espn_injuries(_fetch_espn_html())
    espn_matches = [it for it in espn_all if _normalize_str(it.get("player_name", "")) == player_norm]

    cbs_all = _parse_cbs_injuries(_fetch_cbs_html())
    cbs_matches = [it for it in cbs_all if _normalize_str(it.get("player_name", "")) == player_norm]

    nbc_matches, nbc_attempted_urls = _find_nbc_news_for_player(p, max_items=2)

    bdl_injuries = _get_bdl_injuries_for_player_id(player_id)

    sources_with_info = []
    if bdl_injuries:
        sources_with_info.append("balldontlie")
    if espn_matches:
        sources_with_info.append("espn")
    if cbs_matches:
        sources_with_info.append("cbs")
    if nbc_matches:
        sources_with_info.append("nbc")

    aggregated = {
        "status": "flagged" if sources_with_info else "clear",
        "sources_with_info": sources_with_info,
    }

    return {
        "player_id": player_id,
        "player": p,
        "aggregated": aggregated,
        "sources": {
            "balldontlie": {"injuries": bdl_injuries},
            "espn": {"injuries": espn_matches, "total_injuries_checked": len(espn_all)},
            "cbs": {"injuries": cbs_matches, "total_injuries_checked": len(cbs_all)},
            "nbc": {"injuries": nbc_matches, "attempted_urls": nbc_attempted_urls},
        },
    }


@app.get("/nbc/raw")
def nbc_raw() -> Dict[str, Any]:
    html = _fetch_nbc_html(NBC_FANTASY_PLAYER_NEWS_URL)
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True) if html else ""
    return {"source": "nbc", "url": NBC_FANTASY_PLAYER_NEWS_URL, "status_code": 200 if html else 502, "content_snippet": text[:500]}


@app.get("/injuries/nbc")
def injuries_nbc(name: str) -> Dict[str, Any]:
    """
    Debug simple : on cherche uniquement sur NBA Player News (non-fantasy) + fantasy.
    (Le vrai usage est /injuries/by-player-id, qui sait la team du joueur.)
    """
    if not name:
        raise HTTPException(status_code=400, detail="name must not be empty")

    fake_player = {"full_name": name, "team": {"abbreviation": ""}}
    items, attempted = _find_nbc_news_for_player(fake_player, max_items=3)
    return {"source": "nbc", "player_query": name, "count": len(items), "items": items, "attempted_urls": attempted}


# ============================================================
#                      WIDGET (HTML)
# ============================================================

WIDGET_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8" />
  <title>NBA Injury Checker</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    body { margin: 0; padding: 0; background: #020617; color: #e5e7eb; font-family: system-ui, -apple-system, Segoe UI, sans-serif; }
    * { box-sizing: border-box; }
    .ia-shell { max-width: 1040px; margin: 0 auto; padding: 28px 12px 40px; }
    .ia-card { padding: 24px 20px 26px; border-radius: 20px; background: #0b1220; border: 1px solid rgba(148,163,184,.35); }
    .ia-title { margin: 0 0 6px; font-size: 24px; font-weight: 700; text-transform: uppercase; text-align: center; }
    .ia-subtitle { margin: 0 0 18px; font-size: 13px; color: #9ca3af; text-align: center; }

    .ia-search-row { display: flex; gap: 8px; margin-bottom: 10px; align-items: center; }
    .ia-search { flex: 1; display: flex; gap: 10px; position: relative; }
    .ia-search-input-wrap { flex: 1; position: relative; }
    #ia-player-input { width: 100%; padding: 11px 12px; border-radius: 10px; border: 1px solid rgba(148,163,184,.65); background: rgba(15,23,42,.96); color: #f9fafb; font-size: 14px; outline: none; }
    #ia-search-btn { padding: 11px 16px; border-radius: 10px; border: none; background: #3b82f6; color: #fff; font-weight: 700; cursor: pointer; }
    #ia-search-btn:disabled { opacity: .6; cursor: default; }
    #ia-reset-btn { padding: 9px 12px; border-radius: 10px; border: 1px solid rgba(148,163,184,.7); background: rgba(15,23,42,.96); color: #e5e7eb; font-size: 12px; cursor: pointer; white-space: nowrap; }

    .ia-suggestions { position: absolute; left: 0; right: 0; top: calc(100% + 4px); max-height: 220px; overflow-y: auto; background: #020617; border-radius: 10px; border: 1px solid rgba(148,163,184,.7); z-index: 50; }
    .ia-suggestion-item { padding: 7px 10px; font-size: 13px; cursor: pointer; display: flex; justify-content: space-between; gap: 8px; }
    .ia-suggestion-item:nth-child(2n) { background: rgba(15,23,42,.9); }
    .ia-suggestion-item:hover { background: rgba(59,130,246,.25); }
    .ia-suggestion-name { font-weight: 600; }
    .ia-suggestion-meta { color: #9ca3af; font-size: 12px; }

    .ia-loader { margin: 6px 0 4px; font-size: 13px; display: none; }
    .ia-error { margin: 8px 0 6px; padding: 8px 10px; border-radius: 8px; background: rgba(248,113,113,.1); border: 1px solid rgba(248,113,113,.7); color: #fecaca; font-size: 13px; display: none; }

    /* FICHE JOUEUR */
    .ia-player-card {
      display: none;
      margin-top: 12px;
      padding: 12px 12px;
      border-radius: 14px;
      background: rgba(15,23,42,.96);
      border: 1px solid rgba(148,163,184,.6);
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }
    .ia-player-left { display: flex; flex-direction: column; gap: 2px; }
    .ia-player-name { font-size: 16px; font-weight: 800; color: #f9fafb; }
    .ia-player-meta { font-size: 12px; color: #9ca3af; }
    .ia-badge {
      font-size: 11px;
      padding: 5px 10px;
      border-radius: 999px;
      border: 1px solid rgba(148,163,184,.6);
      text-transform: uppercase;
      letter-spacing: .12em;
      white-space: nowrap;
    }
    .ia-badge-clear { background: rgba(34,197,94,.12); border-color: rgba(34,197,94,.45); color: #bbf7d0; }
    .ia-badge-flagged { background: rgba(239,68,68,.12); border-color: rgba(239,68,68,.45); color: #fecaca; }

    .ia-grid { display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: 12px; margin-top: 12px; }
    @media (max-width: 900px) { .ia-grid { grid-template-columns: repeat(2, minmax(0,1fr)); } }
    @media (max-width: 600px) { .ia-grid { grid-template-columns: minmax(0,1fr); } }

    .ia-col { background: rgba(15,23,42,.97); border-radius: 12px; border: 1px solid rgba(148,163,184,.6); overflow: hidden; }
    .ia-col-header { padding: 6px 9px; border-bottom: 1px solid rgba(148,163,184,.5); background: rgba(30,64,175,.35); }
    .ia-col-label { font-size: 11px; text-transform: uppercase; letter-spacing: .14em; }
    .ia-col-body { padding: 8px 9px 10px; }
    .ia-col-body p { margin: 0 0 4px; font-size: 13px; }
    .ia-badge-empty { display: inline-block; padding: 4px 8px; border-radius: 999px; border: 1px dashed rgba(148,163,184,.7); font-size: 11px; color: #9ca3af; }
    .ia-status { font-weight: 600; }
    .ia-meta { font-size: 12px; color: #9ca3af; }
  </style>
  <script>
    window.__ACTIVE_PLAYERS__ = __ACTIVE_PLAYERS_JSON__;
  </script>
</head>
<body>
  <div class="ia-shell">
    <div class="ia-card">
      <h1 class="ia-title">NBA Injury Checker</h1>
      <p class="ia-subtitle">Recherche fiable via player_id (plus d’erreurs d’homonymes).</p>

      <div class="ia-search-row">
        <div class="ia-search">
          <div class="ia-search-input-wrap">
            <input id="ia-player-input" type="text" placeholder="Tape puis clique une suggestion" autocomplete="off" />
            <div id="ia-suggestions" class="ia-suggestions" style="display:none;"></div>
          </div>
          <button id="ia-search-btn">Chercher</button>
        </div>
        <button id="ia-reset-btn" type="button">Réinitialiser</button>
      </div>

      <div id="ia-loader" class="ia-loader">Recherche en cours...</div>
      <div id="ia-error" class="ia-error"></div>

      <div id="ia-player-card" class="ia-player-card">
        <div class="ia-player-left">
          <div id="ia-player-name" class="ia-player-name"></div>
          <div id="ia-player-meta" class="ia-player-meta"></div>
        </div>
        <div id="ia-player-badge" class="ia-badge"></div>
      </div>

      <div id="ia-results" style="display:none;">
        <div class="ia-grid">
          <div class="ia-col" id="ia-src-bdl">
            <div class="ia-col-header"><span class="ia-col-label">BallDontLie</span></div>
            <div class="ia-col-body"></div>
          </div>
          <div class="ia-col" id="ia-src-espn">
            <div class="ia-col-header"><span class="ia-col-label">ESPN</span></div>
            <div class="ia-col-body"></div>
          </div>
          <div class="ia-col" id="ia-src-cbs">
            <div class="ia-col-header"><span class="ia-col-label">CBS</span></div>
            <div class="ia-col-body"></div>
          </div>
          <div class="ia-col" id="ia-src-nbc">
            <div class="ia-col-header"><span class="ia-col-label">NBC</span></div>
            <div class="ia-col-body"></div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <script>
    (function () {
      const ACTIVE_PLAYERS = window.__ACTIVE_PLAYERS__ || [];

      const input = document.getElementById("ia-player-input");
      const searchBtn = document.getElementById("ia-search-btn");
      const resetBtn = document.getElementById("ia-reset-btn");
      const loader = document.getElementById("ia-loader");
      const errorBox = document.getElementById("ia-error");
      const results = document.getElementById("ia-results");
      const suggBox = document.getElementById("ia-suggestions");

      const playerCard = document.getElementById("ia-player-card");
      const playerNameEl = document.getElementById("ia-player-name");
      const playerMetaEl = document.getElementById("ia-player-meta");
      const playerBadgeEl = document.getElementById("ia-player-badge");

      const srcBdl = document.querySelector("#ia-src-bdl .ia-col-body");
      const srcEspn = document.querySelector("#ia-src-espn .ia-col-body");
      const srcCbs = document.querySelector("#ia-src-cbs .ia-col-body");
      const srcNbc = document.querySelector("#ia-src-nbc .ia-col-body");

      let selectedPlayer = null;
      let suggTimeout = null;

      function norm(s) {
        if (!s) return "";
        s = s.normalize("NFKD").replace(/[\u0300-\u036f]/g, "");
        s = s.toLowerCase().replace(/[^a-z0-9\s]/g, " ").replace(/\s+/g, " ").trim();
        const parts = s.split(" ").filter(x => x && !["jr","sr","ii","iii","iv","v"].includes(x));
        return parts.join(" ");
      }

      function setError(msg) {
        if (!msg) {
          errorBox.style.display = "none";
          errorBox.textContent = "";
        } else {
          errorBox.style.display = "block";
          errorBox.textContent = msg;
        }
      }

      function setLoading(isLoading) {
        loader.style.display = isLoading ? "block" : "none";
        searchBtn.disabled = isLoading;
      }

      function clearSources() {
        srcBdl.innerHTML = "";
        srcEspn.innerHTML = "";
        srcCbs.innerHTML = "";
        srcNbc.innerHTML = "";
      }

      function renderEmpty(el) {
        el.innerHTML = '<span class="ia-badge-empty">Aucune info</span>';
      }

      function closeSuggestions() {
        suggBox.style.display = "none";
        suggBox.innerHTML = "";
      }

      function openSuggestions(items) {
        if (!items.length) {
          closeSuggestions();
          return;
        }
        suggBox.innerHTML = "";
        items.slice(0, 8).forEach(function (p) {
          const div = document.createElement("div");
          div.className = "ia-suggestion-item";

          const left = document.createElement("div");
          left.className = "ia-suggestion-name";
          left.textContent = p.full_name;

          const right = document.createElement("div");
          right.className = "ia-suggestion-meta";
          const team = p.team || {};
          const metaParts = [];
          if (team.abbreviation) metaParts.push(team.abbreviation);
          if (p.position) metaParts.push(p.position);
          right.textContent = metaParts.join(" · ");

          div.appendChild(left);
          div.appendChild(right);

          div.addEventListener("click", function () {
            selectedPlayer = p;
            input.value = p.full_name;
            closeSuggestions();
            searchPlayer();
          });

          suggBox.appendChild(div);
        });
        suggBox.style.display = "block";
      }

      function fetchSuggestionsLocal(q) {
        const nq = norm(q);
        if (!nq || nq.length < 3) {
          closeSuggestions();
          return;
        }
        const filtered = ACTIVE_PLAYERS.filter(function (p) {
          return norm(p.full_name).includes(nq);
        });
        openSuggestions(filtered);
      }

      function resolveSelectedIfExactName() {
        const nq = norm(input.value || "");
        const exact = ACTIVE_PLAYERS.filter(p => norm(p.full_name) === nq);
        if (exact.length === 1) {
          selectedPlayer = exact[0];
          return true;
        }
        return false;
      }

      function renderPlayerCard(data) {
        const p = data.player || {};
        const team = p.team || {};
        const abbr = team.abbreviation || "";
        const pos = p.position || "";
        const name = p.full_name || "Joueur";

        playerNameEl.textContent = name;
        playerMetaEl.textContent = [abbr, pos].filter(Boolean).join(" · ");

        const agg = data.aggregated || {};
        const status = agg.status || "clear";

        playerBadgeEl.className = "ia-badge " + (status === "flagged" ? "ia-badge-flagged" : "ia-badge-clear");
        playerBadgeEl.textContent = status === "flagged" ? "flagged" : "clear";

        playerCard.style.display = "flex";
      }

      async function searchPlayer() {
        setError("");
        results.style.display = "none";
        playerCard.style.display = "none";
        clearSources();

        if (!selectedPlayer) {
          const ok = resolveSelectedIfExactName();
          if (!ok) {
            setError("Sélectionne un joueur dans les suggestions (pour éviter les homonymes).");
            return;
          }
        }

        setLoading(true);
        try {
          const url = "/injuries/by-player-id?player_id=" + encodeURIComponent(selectedPlayer.id);
          const res = await fetch(url, { method: "GET" });
          if (!res.ok) {
            const txt = await res.text();
            throw new Error("API error " + res.status + " " + txt);
          }
          const data = await res.json();
          renderResults(data);
        } catch (e) {
          console.error(e);
          setError("Erreur lors de la récupération. Réessaie.");
        } finally {
          setLoading(false);
        }
      }

      function renderResults(data) {
        results.style.display = "block";
        clearSources();
        renderPlayerCard(data);

        const bdlInj = (data.sources?.balldontlie?.injuries || [])[0];
        if (!bdlInj) renderEmpty(srcBdl);
        else {
          const status = bdlInj.status || "N/A";
          const ret = bdlInj.return_date || "";
          srcBdl.innerHTML =
            '<p class="ia-status">' + status + (ret ? " · retour " + ret : "") + "</p>" +
            (bdlInj.description ? '<p class="ia-meta">' + bdlInj.description + "</p>" : "");
        }

        const espnInj = (data.sources?.espn?.injuries || [])[0];
        if (!espnInj) renderEmpty(srcEspn);
        else {
          srcEspn.innerHTML =
            '<p class="ia-status">' + (espnInj.status || "N/A") +
            (espnInj.est_return_date ? " · retour " + espnInj.est_return_date : "") + "</p>" +
            (espnInj.comment ? '<p class="ia-meta">' + espnInj.comment + "</p>" : "");
        }

        const cbsInj = (data.sources?.cbs?.injuries || [])[0];
        if (!cbsInj) renderEmpty(srcCbs);
        else {
          srcCbs.innerHTML =
            '<p class="ia-status">' + (cbsInj.status || "N/A") + "</p>" +
            '<p class="ia-meta">' +
            (cbsInj.injury || "Injury n/a") +
            (cbsInj.updated ? " · maj " + cbsInj.updated : "") +
            "</p>";
        }

        const nbcInj = (data.sources?.nbc?.injuries || [])[0];
        if (!nbcInj) {
          // Optionnel : affiche où on a cherché
          const attempted = data.sources?.nbc?.attempted_urls || [];
          if (attempted.length) {
            srcNbc.innerHTML = '<span class="ia-badge-empty">Aucune info (testé: ' + attempted.length + ' URLs)</span>';
          } else {
            renderEmpty(srcNbc);
          }
        } else {
          srcNbc.innerHTML =
            '<p class="ia-status">' + (nbcInj.headline || "NBC") + "</p>" +
            (nbcInj.summary ? '<p class="ia-meta">' + nbcInj.summary + "</p>" : "") +
            (nbcInj.url ? '<p class="ia-meta">' + nbcInj.url + "</p>" : "");
        }
      }

      function resetSearch() {
        input.value = "";
        selectedPlayer = null;
        closeSuggestions();
        setError("");
        results.style.display = "none";
        playerCard.style.display = "none";
        clearSources();
        loader.style.display = "none";
      }

      searchBtn.addEventListener("click", searchPlayer);
      resetBtn.addEventListener("click", resetSearch);

      input.addEventListener("keydown", function (e) {
        if (e.key === "Enter") searchPlayer();
        if (e.key === "Escape") closeSuggestions();
      });

      input.addEventListener("input", function () {
        selectedPlayer = null;
        const q = (input.value || "").trim();
        if (suggTimeout) clearTimeout(suggTimeout);
        suggTimeout = setTimeout(function () { fetchSuggestionsLocal(q); }, 120);
      });

      document.addEventListener("click", function (e) {
        if (!suggBox.contains(e.target) && e.target !== input) closeSuggestions();
      });
    })();
  </script>
</body>
</html>
"""


@app.get("/widget", response_class=HTMLResponse)
def widget() -> str:
    _load_active_players()
    players_json = json.dumps(ACTIVE_PLAYERS, ensure_ascii=False)
    return WIDGET_HTML_TEMPLATE.replace("__ACTIVE_PLAYERS_JSON__", players_json)
