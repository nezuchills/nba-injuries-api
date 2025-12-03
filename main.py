from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from typing import Optional, Dict, Any, List, Tuple
import os
import json
import requests
from bs4 import BeautifulSoup  # ESPN / CBS / NBC

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
#                     ENDPOINTS DE BASE
# ============================================================

@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/")
def root():
    return {"message": "NBA injuries API is running"}


# Endpoint de test factice
@app.get("/injuries/test")
def injuries_test(player: Optional[str] = None) -> Dict[str, Any]:
    player_name = player or "LeBron James"

    example_response = {
        "player": player_name,
        "sources": {
            "espn": {
                "status": "out",
                "injury": "ankle",
                "details": "Sidelined with a right ankle sprain",
                "last_update": "2025-12-01T18:30:00Z",
                "url": "https://www.espn.com/nba/injuries",
            },
            "cbs": {
                "status": "questionable",
                "injury": "ankle",
                "details": "Questionable for next game",
                "last_update": "2025-12-01T18:45:00Z",
                "url": "https://www.cbssports.com/nba/injuries/",
            },
            "nbc": {
                "status": "out",
                "injury": "ankle",
                "details": "Likely to miss multiple games",
                "last_update": "2025-12-01T18:40:00Z",
                "url": "https://www.nbcsports.com/nba/nba-injuries-nbc-sports",
            },
            "balldontlie": {
                "status": "out",
                "injury": "ankle",
                "details": "Listed as out on official injury report",
                "last_update": "2025-12-01T18:35:00Z",
                "url": "https://api.balldontlie.io/v1/player_injuries",
            },
        },
    }

    return example_response


# ============================================================
#                       BALLDONTLIE
# ============================================================

def _get_balldontlie_api_key() -> str:
    api_key = os.getenv("BALLDONTLIE_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="BALLDONTLIE_API_KEY is not set on the server",
        )
    return api_key


def _call_balldontlie(
    path: str,
    params: Optional[Dict[str, Any]] = None,
    timeout: int = 10,
) -> Dict[str, Any]:
    api_key = _get_balldontlie_api_key()
    base_url = "https://api.balldontlie.io"
    url = f"{base_url}{path}"
    headers = {"Authorization": api_key}

    try:
        resp = requests.get(url, headers=headers, params=params or {}, timeout=timeout)
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Error calling BallDontLie: {e}")

    if resp.status_code != 200:
        raise HTTPException(
            status_code=resp.status_code,
            detail=f"BallDontLie error: {resp.text[:200]}",
        )

    return resp.json()


@app.get("/balldontlie/raw")
def balldontlie_raw(cursor: Optional[int] = None, per_page: int = 25) -> Dict[str, Any]:
    params: Dict[str, Any] = {"per_page": per_page}
    if cursor is not None:
        params["cursor"] = cursor

    data = _call_balldontlie("/v1/player_injuries", params=params)
    return {
        "source": "balldontlie",
        "endpoint": "https://api.balldontlie.io/v1/player_injuries",
        "params": params,
        "data": data,
    }


def _map_balldontlie_injury(item: Dict[str, Any]) -> Dict[str, Any]:
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


@app.get("/injuries/balldontlie")
def injuries_balldontlie(
    per_page: int = 25,
    cursor: Optional[int] = None,
) -> Dict[str, Any]:
    params: Dict[str, Any] = {"per_page": per_page}
    if cursor is not None:
        params["cursor"] = cursor

    raw = _call_balldontlie("/v1/player_injuries", params=params)
    raw_data: List[Dict[str, Any]] = raw.get("data", [])
    meta = raw.get("meta", {})

    simplified = [_map_balldontlie_injury(item) for item in raw_data]

    return {
        "source": "balldontlie",
        "count": len(simplified),
        "meta": meta,
        "injuries": simplified,
    }


@app.get("/balldontlie/player/{player_id}")
def balldontlie_player(player_id: int) -> Dict[str, Any]:
    resp = _call_balldontlie(f"/v1/players/{player_id}")
    data = resp.get("data") or {}
    player_team = data.get("team") or {}

    full_name = data.get("full_name")
    if not full_name:
        first = data.get("first_name") or ""
        last = data.get("last_name") or ""
        full_name = f"{first} {last}".strip()

    return {
        "id": data.get("id"),
        "full_name": full_name,
        "first_name": data.get("first_name"),
        "last_name": data.get("last_name"),
        "position": data.get("position"),
        "team": {
            "id": player_team.get("id"),
            "name": player_team.get("full_name") or player_team.get("name"),
            "abbreviation": player_team.get("abbreviation"),
            "city": player_team.get("city"),
        },
        "raw": resp,
    }


@app.get("/players/balldontlie/search")
def players_balldontlie_search(query: str, per_page: int = 25) -> Dict[str, Any]:
    params = {"search": query, "per_page": per_page}
    resp = _call_balldontlie("/v1/players", params=params)
    data = resp.get("data", [])
    meta = resp.get("meta", {})

    players: List[Dict[str, Any]] = []
    for p in data:
        team = p.get("team") or {}
        full_name = p.get("full_name")
        if not full_name:
            first = p.get("first_name") or ""
            last = p.get("last_name") or ""
            full_name = f"{first} {last}".strip()

        players.append(
            {
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
        )

    return {
        "source": "balldontlie",
        "query": query,
        "count": len(players),
        "meta": meta,
        "players": players,
    }


@app.get("/injuries/balldontlie/by-player-id")
def injuries_balldontlie_by_player_id(
    player_id: int,
    per_page: int = 50,
) -> Dict[str, Any]:
    params: Dict[str, Any] = {"per_page": per_page}

    raw = _call_balldontlie("/v1/player_injuries", params=params)
    raw_data: List[Dict[str, Any]] = raw.get("data", [])
    meta = raw.get("meta", {})

    filtered_raw: List[Dict[str, Any]] = []
    for item in raw_data:
        player = item.get("player") or {}
        pid = player.get("id")
        if pid == player_id:
            filtered_raw.append(item)

    simplified = [_map_balldontlie_injury(item) for item in filtered_raw]

    return {
        "source": "balldontlie",
        "player_id": player_id,
        "count": len(simplified),
        "meta": meta,
        "injuries": simplified,
    }


# ============================================================
#       CACHE JOUEURS ACTIFS (AUTOCOMPLÉTION FRONT)
# ============================================================

ACTIVE_PLAYERS: List[Dict[str, Any]] = []
ACTIVE_PLAYERS_LOADED: bool = False


def _load_active_players() -> None:
    global ACTIVE_PLAYERS, ACTIVE_PLAYERS_LOADED
    if ACTIVE_PLAYERS_LOADED:
        return

    players: List[Dict[str, Any]] = []
    cursor: Optional[int] = None

    while True:
        params: Dict[str, Any] = {"per_page": 100}
        if cursor is not None:
            params["cursor"] = cursor

        resp = _call_balldontlie("/v1/players/active", params=params)
        data = resp.get("data", [])
        meta = resp.get("meta", {}) or {}

        for p in data:
            team = p.get("team") or {}
            full_name = p.get("full_name")
            if not full_name:
                first = p.get("first_name") or ""
                last = p.get("last_name") or ""
                full_name = f"{first} {last}".strip()

            players.append(
                {
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
            )

        cursor = meta.get("next_cursor")
        if not cursor:
            break

    ACTIVE_PLAYERS = players
    ACTIVE_PLAYERS_LOADED = True


@app.get("/players/active/local")
def players_active_local() -> Dict[str, Any]:
    _load_active_players()
    return {
        "source": "balldontlie",
        "count": len(ACTIVE_PLAYERS),
        "players": ACTIVE_PLAYERS,
    }


# ============================================================
#                            ESPN
# ============================================================

ESPN_INJURIES_URL = "https://www.espn.com/nba/injuries"  # [web:20]


def _fetch_espn_html() -> str:
    try:
        resp = requests.get(
            ESPN_INJURIES_URL,
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"},
        )
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Error calling ESPN: {e}")

    if resp.status_code != 200:
        raise HTTPException(
            status_code=resp.status_code,
            detail=f"ESPN error: {resp.text[:200]}",
        )

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


@app.get("/espn/raw")
def espn_raw() -> Dict[str, Any]:
    html = _fetch_espn_html()
    return {
        "source": "espn",
        "url": ESPN_INJURIES_URL,
        "status_code": 200,
        "content_snippet": html[:500],
    }


@app.get("/injuries/espn")
def injuries_espn() -> Dict[str, Any]:
    html = _fetch_espn_html()
    parsed = _parse_espn_injuries(html)
    return {
        "source": "espn",
        "count": len(parsed),
        "injuries": parsed,
    }


# ============================================================
#                            NBC
# ============================================================

# Page injuries NBC actuelle (route "Injuries" dans le menu NBA). [web:156]
NBC_INJURIES_URL = "https://www.nbcsports.com/nba/nba-injuries-nbc-sports"


def _fetch_nbc_html() -> str:
    try:
        resp = requests.get(
            NBC_INJURIES_URL,
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"},
        )
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Error calling NBC: {e}")

    if resp.status_code != 200:
        raise HTTPException(
            status_code=resp.status_code,
            detail=f"NBC error: {resp.text[:200]}",
        )

    return resp.text


def _parse_nbc_injuries(html: str) -> List[Dict[str, Any]]:
    """
    Parse la page injuries NBC en texte brut.
    Structure globale : menu, listes d'équipes, puis section "Injuries" avec
    pour chaque équipe un bloc : TEAM, puis headers PLAYER/POS/DATE/INJURY
    puis des blocs (name, pos, date, injury, description). [web:156]
    """
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)
    lines = [l for l in text.split("\n") if l.strip()]

    results: List[Dict[str, Any]] = []

    try:
        start_idx = lines.index("Injuries")
    except ValueError:
        try:
            start_idx = lines.index("INJURIES")
        except ValueError:
            return results

    header_tokens = {"PLAYER", "Pos", "POS", "Date", "DATE", "Injury", "INJURY"}
    positions = {
        "PG", "SG", "SF", "PF", "C",
        "G", "F",
        "G-F", "F-G", "G/F", "F/C", "C/F",
    }

    i = start_idx
    current_team: Optional[str] = None

    def looks_like_team_header(idx: int) -> bool:
        if idx + 4 >= len(lines):
            return False
        l0 = lines[idx].strip()
        l1 = lines[idx + 1].strip()
        l2 = lines[idx + 2].strip()
        l3 = lines[idx + 3].strip()
        l4 = lines[idx + 4].strip()
        return (
            l0 not in header_tokens
            and l1 in {"PLAYER", "Player"}
            and l2 in {"POS", "Pos"}
            and l3 in {"DATE", "Date"}
            and l4 in {"INJURY", "Injury"}
        )

    while i < len(lines) - 5:
        if looks_like_team_header(i):
            current_team = lines[i].strip()
            i += 5
            continue

        if current_team is None:
            i += 1
            continue

        if i + 4 >= len(lines):
            break

        name = lines[i].strip()
        pos = lines[i + 1].strip()
        date = lines[i + 2].strip()
        injury = lines[i + 3].strip()
        desc = lines[i + 4].strip()

        if (
            name in header_tokens
            or pos in header_tokens
            or date in header_tokens
            or injury in header_tokens
        ):
            i += 1
            continue

        if pos not in positions:
            i += 1
            continue

        results.append(
            {
                "player_name": name,
                "position": pos,
                "date": date,
                "injury": injury,
                "description": desc,
                "team": current_team,
                "source": "nbc",
            }
        )
        i += 5

    return results


@app.get("/nbc/raw")
def nbc_raw() -> Dict[str, Any]:
    html = _fetch_nbc_html()
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)
    return {
        "source": "nbc",
        "url": NBC_INJURIES_URL,
        "status_code": 200,
        "content_snippet": text[:500],
    }


@app.get("/injuries/nbc")
def injuries_nbc() -> Dict[str, Any]:
    html = _fetch_nbc_html()
    parsed = _parse_nbc_injuries(html)
    return {
        "source": "nbc",
        "count": len(parsed),
        "injuries": parsed,
    }


# ============================================================
#                            CBS
# ============================================================

CBS_INJURIES_URL = "https://www.cbssports.com/nba/injuries/"  # [web:6]


def _fetch_cbs_html() -> str:
    try:
        resp = requests.get(
            CBS_INJURIES_URL,
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"},
        )
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Error calling CBS: {e}")

    if resp.status_code != 200:
        raise HTTPException(
            status_code=resp.status_code,
            detail=f"CBS error: {resp.text[:200]}",
        )

    return resp.text


def _clean_cbs_player_name(raw: str) -> str:
    s = raw.strip()
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


@app.get("/cbs/raw")
def cbs_raw():
    html = _fetch_cbs_html()
    return {
        "source": "cbs",
        "url": CBS_INJURIES_URL,
        "status_code": 200,
        "content_snippet": html[:500],
    }


@app.get("/injuries/cbs")
def injuries_cbs():
    html = _fetch_cbs_html()
    parsed = _parse_cbs_injuries(html)
    return {
        "source": "cbs",
        "count": len(parsed),
        "injuries": parsed,
    }


# ============================================================
#           HELPER: meilleure correspondance BDL
# ============================================================

def _normalize_full_name(p: Dict[str, Any]) -> str:
    full = p.get("full_name")
    if not full:
        first = (p.get("first_name") or "").strip()
        last = (p.get("last_name") or "").strip()
        full = f"{first} {last}".strip()
    return " ".join(full.lower().split())


def _search_bdl_best_player(name: str) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    name = name.strip()
    if not name:
        return None, []

    tokens = name.split()
    candidates = [name]
    if len(tokens) >= 1:
        candidates.append(tokens[-1])
    if len(tokens) >= 2:
        candidates.append(tokens[0])

    seen_ids = set()
    all_found: List[Dict[str, Any]] = []
    best_player: Optional[Dict[str, Any]] = None
    query_norm = " ".join(name.lower().split())

    for term in candidates:
        params = {"search": term, "per_page": 10}
        resp = _call_balldontlie("/v1/players", params=params)
        data = resp.get("data", [])

        for p in data:
            pid = p.get("id")
            if pid in seen_ids:
                continue
            seen_ids.add(pid)
            all_found.append(p)

        for p in data:
            if _normalize_full_name(p) == query_norm:
                best_player = p
                break

        if best_player is not None:
            break

    if best_player is None and all_found:
        best_player = all_found[0]

    return best_player, all_found


# ============================================================
#            ENDPOINT MULTI-SOURCES PAR JOUEUR
# ============================================================

def _compute_aggregated_status(
    bdl_injuries: List[Dict[str, Any]],
    espn_matches: List[Dict[str, Any]],
    cbs_matches: List[Dict[str, Any]],
    nbc_matches: List[Dict[str, Any]],
) -> Dict[str, Any]:
    sources_with_info: List[str] = []
    if bdl_injuries:
        sources_with_info.append("balldontlie")
    if espn_matches:
        sources_with_info.append("espn")
    if cbs_matches:
        sources_with_info.append("cbs")
    if nbc_matches:
        sources_with_info.append("nbc")

    status = "flagged" if sources_with_info else "clear"

    return {
        "status": status,
        "sources_with_info": sources_with_info,
    }


@app.get("/injuries/by-player")
def injuries_by_player(name: str) -> Dict[str, Any]:
    query = name.strip()
    if not query:
        raise HTTPException(status_code=400, detail="name parameter must not be empty")

    query_lower = query.lower()

    # ESPN
    espn_html = _fetch_espn_html()
    espn_all = _parse_espn_injuries(espn_html)
    espn_matches = [
        item for item in espn_all if query_lower in item["player_name"].lower()
    ]

    # NBC
    nbc_html = _fetch_nbc_html()
    nbc_all = _parse_nbc_injuries(nbc_html)
    nbc_matches = [
        item for item in nbc_all if query_lower in item["player_name"].lower()
    ]

    # CBS
    cbs_html = _fetch_cbs_html()
    cbs_all = _parse_cbs_injuries(cbs_html)
    cbs_matches = [
        item for item in cbs_all if query_lower in item["player_name"].lower()
    ]

    # BallDontLie
    best_player, all_players = _search_bdl_best_player(query)

    bdl_injuries: List[Dict[str, Any]] = []
    bdl_player_info: Optional[Dict[str, Any]] = None

    if best_player is not None:
        pid = best_player.get("id")
        team = best_player.get("team") or {}

        full_name = best_player.get("full_name")
        if not full_name:
            first = (best_player.get("first_name") or "").strip()
            last = (best_player.get("last_name") or "").strip()
            full_name = f"{first} {last}".strip()

        bdl_player_info = {
            "id": pid,
            "full_name": full_name,
            "first_name": best_player.get("first_name"),
            "last_name": best_player.get("last_name"),
            "position": best_player.get("position"),
            "team": {
                "id": team.get("id"),
                "name": team.get("full_name") or team.get("name"),
                "abbreviation": team.get("abbreviation"),
                "city": team.get("city"),
            },
        }

        injuries_resp = injuries_balldontlie_by_player_id(player_id=pid, per_page=50)
        bdl_injuries = injuries_resp.get("injuries", [])

    aggregated = _compute_aggregated_status(
        bdl_injuries=bdl_injuries,
        espn_matches=espn_matches,
        cbs_matches=cbs_matches,
        nbc_matches=nbc_matches,
    )

    return {
        "player_query": query,
        "aggregated": aggregated,
        "sources": {
            "balldontlie": {
                "matched_player": bdl_player_info,
                "raw_search_count": len(all_players),
                "injuries": bdl_injuries,
            },
            "espn": {
                "injuries": espn_matches,
                "total_injuries_checked": len(espn_all),
            },
            "nbc": {
                "injuries": nbc_matches,
                "total_injuries_checked": len(nbc_all),
            },
            "cbs": {
                "injuries": cbs_matches,
                "total_injuries_checked": len(cbs_all),
            },
        },
    }


# ============================================================
#                        UI WIDGET HTML
# ============================================================

@app.get("/widget", response_class=HTMLResponse)
def widget() -> str:
    _load_active_players()
    players_json = json.dumps(ACTIVE_PLAYERS)

    return f"""
<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8" />
  <title>NBA Injury Checker</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    body {{
      margin: 0;
      padding: 0;
      background: #020617;
      color: #e5e7eb;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "SF Pro Text",
        "Segoe UI", sans-serif;
    }}
    * {{ box-sizing: border-box; }}
    #injury-app {{
      color: #e5e7eb;
    }}
    .ia-shell {{
      max-width: 1040px;
      margin: 0 auto;
      padding: 28px 12px 40px;
    }}
    .ia-card {{
      position: relative;
      padding: 24px 20px 26px;
      border-radius: 20px;
      background: radial-gradient(circle at top left, #1e293b 0, #020617 45%, #000 100%);
      border: 1px solid rgba(148, 163, 184, 0.35);
      box-shadow:
        0 32px 80px rgba(0, 0, 0, 0.75),
        0 0 0 1px rgba(15, 23, 42, 0.65);
      overflow: hidden;
    }}
    .ia-title {{
      position: relative;
      margin: 0 0 6px;
      font-size: 26px;
      font-weight: 650;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: #f9fafb;
      text-align: center;
    }}
    .ia-subtitle {{
      position: relative;
      margin: 0 0 18px;
      font-size: 13px;
      color: #9ca3af;
      text-align: center;
    }}
    .ia-wake-row {{
      display: flex;
      align-items: center;
      gap: 10px;
      margin-bottom: 10px;
    }}
    #ia-wake-btn {{
      padding: 7px 12px;
      border-radius: 8px;
      border: 1px solid rgba(148, 163, 184, 0.7);
      background: rgba(15, 23, 42, 0.96);
      color: #e5e7eb;
      font-size: 12px;
      cursor: pointer;
      white-space: nowrap;
    }}
    #ia-wake-btn:hover:not(:disabled) {{
      background: rgba(30, 64, 175, 0.8);
      border-color: rgba(96, 165, 250, 0.8);
    }}
    #ia-wake-btn:disabled {{
      opacity: 0.6;
      cursor: default;
    }}
    .ia-wake-status {{
      font-size: 11px;
      color: #9ca3af;
    }}
    .ia-search-row {{
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 10px;
    }}
    .ia-search {{
      position: relative;
      flex: 1;
      display: flex;
      gap: 10px;
      z-index: 2;
    }}
    .ia-search-input-wrap {{
      position: relative;
      flex: 1;
    }}
    #ia-player-input {{
      width: 100%;
      padding: 11px 12px;
      border-radius: 10px;
      border: 1px solid rgba(148, 163, 184, 0.65);
      background: rgba(15, 23, 42, 0.96);
      color: #f9fafb;
      font-size: 14px;
      outline: none;
    }}
    #ia-player-input::placeholder {{
      color: #6b7280;
    }}
    #ia-player-input:focus {{
      border-color: #60a5fa;
      box-shadow: 0 0 0 1px rgba(37, 99, 235, 0.7);
    }}
    #ia-search-btn {{
      padding: 11px 16px;
      border-radius: 10px;
      border: none;
      background: linear-gradient(to right, #2563eb, #4f46e5);
      color: #f9fafb;
      font-weight: 600;
      font-size: 14px;
      cursor: pointer;
      white-space: nowrap;
      box-shadow: 0 12px 25px rgba(37, 99, 235, 0.45);
    }}
    #ia-search-btn:disabled {{
      opacity: 0.6;
      cursor: default;
      box-shadow: none;
    }}
    #ia-search-btn:hover:not(:disabled) {{
      background: linear-gradient(to right, #1d4ed8, #4338ca);
    }}
    #ia-reset-btn {{
      padding: 9px 12px;
      border-radius: 10px;
      border: 1px solid rgba(148, 163, 184, 0.7);
      background: rgba(15, 23, 42, 0.96);
      color: #e5e7eb;
      font-size: 12px;
      cursor: pointer;
      white-space: nowrap;
    }}
    #ia-reset-btn:hover:not(:disabled) {{
      background: rgba(30, 64, 175, 0.7);
      border-color: rgba(96, 165, 250, 0.9);
    }}
    .ia-hint {{
      margin: 4px 0 8px;
      font-size: 11px;
      color: #9ca3af;
    }}
    .ia-suggestions {{
      position: absolute;
      left: 0;
      right: 0;
      top: calc(100% + 4px);
      max-height: 220px;
      overflow-y: auto;
      background: #020617;
      border-radius: 10px;
      border: 1px solid rgba(148, 163, 184, 0.7);
      box-shadow: 0 18px 40px rgba(0, 0, 0, 0.9);
      z-index: 50;
    }}
    .ia-suggestion-item {{
      padding: 7px 10px;
      font-size: 13px;
      cursor: pointer;
      display: flex;
      justify-content: space-between;
      gap: 8px;
    }}
    .ia-suggestion-item:nth-child(2n) {{
      background: rgba(15, 23, 42, 0.9);
    }}
    .ia-suggestion-item:hover {{
      background: rgba(37, 99, 235, 0.25);
    }}
    .ia-suggestion-name {{
      font-weight: 500;
    }}
    .ia-suggestion-meta {{
      color: #9ca3af;
      font-size: 12px;
    }}
    .ia-loader {{
      position: relative;
      margin: 6px 0 4px;
      font-size: 13px;
      color: #e5e7eb;
    }}
    .ia-error {{
      position: relative;
      margin: 8px 0 6px;
      padding: 8px 10px;
      border-radius: 8px;
      background: rgba(248, 113, 113, 0.1);
      border: 1px solid rgba(248, 113, 113, 0.7);
      color: #fecaca;
      font-size: 13px;
    }}
    .ia-player-card {{
      position: relative;
      display: flex;
      gap: 12px;
      padding: 10px 10px;
      margin: 10px 0 16px;
      border-radius: 14px;
      background: rgba(15, 23, 42, 0.96);
      border: 1px solid rgba(148, 163, 184, 0.65);
    }}
    .ia-player-avatar-wrap {{
      flex: 0 0 72px;
      height: 72px;
      border-radius: 999px;
      overflow: hidden;
      background: radial-gradient(circle at 30% 0, #38bdf8, #0b1120);
      border: 1px solid rgba(148, 163, 184, 0.8);
      display: flex;
      align-items: center;
      justify-content: center;
    }}
    .ia-player-avatar-initials {{
      font-size: 22px;
      font-weight: 600;
      color: #e5f4ff;
    }}
    .ia-player-info {{
      flex: 1;
      display: flex;
      flex-direction: column;
      justify-content: center;
    }}
    .ia-player-name-row {{
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 4px;
    }}
    .ia-player-name {{
      font-size: 17px;
      font-weight: 600;
      color: #f9fafb;
    }}
    .ia-agg-badge {{
      font-size: 11px;
      padding: 3px 8px;
      border-radius: 999px;
      display: inline-flex;
      align-items: center;
      gap: 5px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }}
    .ia-agg-clear {{
      background: rgba(22, 163, 74, 0.18);
      color: #bbf7d0;
      border: 1px solid rgba(22, 163, 74, 0.6);
    }}
    .ia-agg-flagged {{
      background: rgba(220, 38, 38, 0.18);
      color: #fecaca;
      border: 1px solid rgba(220, 38, 38, 0.6);
    }}
    .ia-agg-dot {{
      width: 7px;
      height: 7px;
      border-radius: 999px;
      background: currentColor;
    }}
    .ia-player-meta-row {{
      display: flex;
      align-items: center;
      gap: 6px;
    }}
    .ia-team-logo {{
      width: 20px;
      height: 20px;
      border-radius: 4px;
      object-fit: contain;
      background: #020617;
    }}
    .ia-player-meta {{
      font-size: 13px;
      color: #cbd5f5;
    }}
    .ia-grid {{
      position: relative;
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
    }}
    @media (max-width: 900px) {{
      .ia-grid {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}
    }}
    @media (max-width: 600px) {{
      .ia-grid {{
        grid-template-columns: minmax(0, 1fr);
      }}
      .ia-card {{
        padding: 20px 16px 24px;
      }}
      .ia-player-card {{
        flex-direction: row;
      }}
    }}
    .ia-col {{
      background: rgba(15, 23, 42, 0.97);
      border-radius: 12px;
      border: 1px solid rgba(148, 163, 184, 0.6);
      padding: 0;
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }}
    .ia-col-header {{
      padding: 6px 9px;
      border-bottom: 1px solid rgba(148, 163, 184, 0.5);
      background: linear-gradient(to right, rgba(30, 64, 175, 0.65), rgba(15, 23, 42, 0.95));
    }}
    .ia-col-label {{
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.14em;
      color: #e5e7eb;
    }}
    .ia-col-body {{
      padding: 8px 9px 10px;
    }}
    .ia-col-body p {{
      margin: 0 0 4px;
      font-size: 13px;
    }}
    .ia-badge-empty {{
      display: inline-block;
      padding: 4px 8px;
      border-radius: 999px;
      border: 1px dashed rgba(148, 163, 184, 0.7);
      font-size: 11px;
      color: #9ca3af;
    }}
    .ia-status {{
      font-weight: 500;
    }}
    .ia-meta {{
      font-size: 12px;
      color: #9ca3af;
    }}
    .ia-footer {{
      position: relative;
      margin: 16px 0 0;
      font-size: 11px;
      color: #6b7280;
      text-align: right;
    }}
  </style>
  <script>
    window.__ACTIVE_PLAYERS__ = {players_json};
  </script>
</head>
<body>
  <div id="injury-app">
    <div class="ia-shell">
      <div class="ia-card">
        <h1 class="ia-title">NBA Injury Checker</h1>
        <p class="ia-subtitle">
          Agrégateur d'informations sur le statut des joueurs NBA.
        </p>

        <div class="ia-wake-row">
          <button id="ia-wake-btn" type="button">Réveiller le service</button>
          <div id="ia-wake-status" class="ia-wake-status">
            Utilise ce bouton si la première recherche semble lente.
          </div>
        </div>

        <div class="ia-search-row">
          <div class="ia-search">
            <div class="ia-search-input-wrap">
              <input
                id="ia-player-input"
                type="text"
                placeholder="Rechercher un joueur (min. 3 caractères, ex : Kristaps Porzingis)"
                autocomplete="off"
              />
              <div id="ia-suggestions" class="ia-suggestions" style="display:none;"></div>
            </div>
            <button id="ia-search-btn">Chercher</button>
          </div>
          <button id="ia-reset-btn" type="button">Réinitialiser</button>
        </div>

        <div class="ia-hint">
          Tape au moins 3 caractères pour voir les suggestions, puis valide avec Entrée ou clique sur Chercher.
        </div>

        <div id="ia-loader" class="ia-loader" style="display:none;">
          Recherche en cours...
        </div>
        <div id="ia-error" class="ia-error" style="display:none;"></div>

        <div id="ia-results" class="ia-results" style="display:none;">

          <div id="ia-player-card" class="ia-player-card" style="display:none;">
            <div class="ia-player-avatar-wrap">
              <div id="ia-player-avatar-initials" class="ia-player-avatar-initials"></div>
            </div>
            <div class="ia-player-info">
              <div class="ia-player-name-row">
                <span id="ia-player-name" class="ia-player-name"></span>
                <span id="ia-player-agg" class="ia-agg-badge" style="display:none;"></span>
              </div>
              <div class="ia-player-meta-row">
                <img id="ia-team-logo" class="ia-team-logo" alt="Team logo" />
                <span id="ia-player-meta" class="ia-player-meta"></span>
              </div>
            </div>
          </div>

          <div class="ia-grid">
            <div class="ia-col" id="ia-src-bdl">
              <div class="ia-col-header">
                <span class="ia-col-label">BallDontLie</span>
              </div>
              <div class="ia-col-body"></div>
            </div>
            <div class="ia-col" id="ia-src-espn">
              <div class="ia-col-header">
                <span class="ia-col-label">ESPN</span>
              </div>
              <div class="ia-col-body"></div>
            </div>
            <div class="ia-col" id="ia-src-cbs">
              <div class="ia-col-header">
                <span class="ia-col-label">CBS</span>
              </div>
              <div class="ia-col-body"></div>
            </div>
            <div class="ia-col" id="ia-src-nbc">
              <div class="ia-col-header">
                <span class="ia-col-label">NBC</span>
              </div>
              <div class="ia-col-body"></div>
            </div>
          </div>
        </div>

        <p class="ia-footer">
          Données live : BallDontLie, ESPN, CBS, NBC.
        </p>
      </div>
    </div>
  </div>

  <script>
    (function () {{
      let ACTIVE_PLAYERS = window.__ACTIVE_PLAYERS__ || [];
      let ACTIVE_PLAYERS_LOADED = ACTIVE_PLAYERS.length > 0;

      const wakeBtn = document.getElementById("ia-wake-btn");
      const wakeStatus = document.getElementById("ia-wake-status");

      const input = document.getElementById("ia-player-input");
      const searchBtn = document.getElementById("ia-search-btn");
      const resetBtn = document.getElementById("ia-reset-btn");
      const loader = document.getElementById("ia-loader");
      const errorBox = document.getElementById("ia-error");
      const results = document.getElementById("ia-results");

      const srcBdl = document.querySelector("#ia-src-bdl .ia-col-body");
      const srcEspn = document.querySelector("#ia-src-espn .ia-col-body");
      const srcCbs = document.querySelector("#ia-src-cbs .ia-col-body");
      const srcNbc = document.querySelector("#ia-src-nbc .ia-col-body");

      const suggBox = document.getElementById("ia-suggestions");
      let suggTimeout = null;

      const playerCard = document.getElementById("ia-player-card");
      const playerAvatarInitials = document.getElementById("ia-player-avatar-initials");
      const playerNameEl = document.getElementById("ia-player-name");
      const playerAggEl = document.getElementById("ia-player-agg");
      const teamLogoEl = document.getElementById("ia-team-logo");
      const playerMetaEl = document.getElementById("ia-player-meta");

      async function wakeService() {{
        wakeBtn.disabled = true;
        const prevSearchDisabled = searchBtn.disabled;
        searchBtn.disabled = true;
        wakeStatus.textContent = "Réveil en cours… cela peut prendre jusqu'à une minute si le service dormait.";

        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 60000);

        try {{
          const res = await fetch("/health", {{ method: "GET", signal: controller.signal }});
          clearTimeout(timeoutId);

          if (!res.ok) {{
            throw new Error("Health error " + res.status);
          }}

          wakeStatus.textContent = "Service réveillé. Tu peux lancer une recherche.";
        }} catch (e) {{
          console.error(e);
          if (e.name === "AbortError") {{
            wakeStatus.textContent = "Temps dépassé pour le réveil du service. Réessaie ou rafraîchis la page.";
          }} else {{
            wakeStatus.textContent = "Impossible de réveiller le service (réessaie ou rafraîchis la page).";
          }}
        }} finally {{
          wakeBtn.disabled = false;
          searchBtn.disabled = prevSearchDisabled;

          setTimeout(() => {{
            if (
              wakeStatus.textContent.startsWith("Service réveillé") ||
              wakeStatus.textContent.startsWith("Temps dépassé") ||
              wakeStatus.textContent.startsWith("Impossible de réveiller")
            ) {{
              wakeStatus.textContent = "Utilise ce bouton si la première recherche semble lente.";
            }}
          }}, 30000);
        }}
      }}

      function setLoading(isLoading) {{
        loader.style.display = isLoading ? "block" : "none";
        searchBtn.disabled = isLoading;
      }}

      function setError(message) {{
        if (!message) {{
          errorBox.style.display = "none";
          errorBox.textContent = "";
        }} else {{
          errorBox.style.display = "block";
          errorBox.textContent = message;
        }}
      }}

      function clearSources() {{
        srcBdl.innerHTML = "";
        srcEspn.innerHTML = "";
        srcCbs.innerHTML = "";
        srcNbc.innerHTML = "";
      }}

      function renderEmpty(el) {{
        el.innerHTML = '<span class="ia-badge-empty">Aucune info</span>';
      }}

      function closeSuggestions() {{
        suggBox.style.display = "none";
        suggBox.innerHTML = "";
      }}

      function openSuggestions(items) {{
        if (!items.length) {{
          closeSuggestions();
          return;
        }}
        suggBox.innerHTML = "";
        items.slice(0, 8).forEach(function (p) {{
          const div = document.createElement("div");
          div.className = "ia-suggestion-item";
          const left = document.createElement("div");
          left.className = "ia-suggestion-name";
          left.textContent = p.full_name || (p.first_name + " " + p.last_name);

          const right = document.createElement("div");
          right.className = "ia-suggestion-meta";
          const team = p.team || {{}};
          const parts = [];
          if (team.abbreviation) parts.push(team.abbreviation);
          if (p.position) parts.push(p.position);
          right.textContent = parts.join(" · ");

          div.appendChild(left);
          div.appendChild(right);

          div.addEventListener("click", function () {{
            const name = p.full_name || (p.first_name + " " + p.last_name);
            input.value = name;
            closeSuggestions();
            searchPlayer();
          }});

          suggBox.appendChild(div);
        }});
        suggBox.style.display = "block";
      }}

      function fetchSuggestionsLocal(q) {{
        if (q.length < 3) {{
          closeSuggestions();
          return;
        }}
        if (!ACTIVE_PLAYERS_LOADED || !ACTIVE_PLAYERS.length) {{
          closeSuggestions();
          return;
        }}
        const qLower = q.toLowerCase();
        const filtered = ACTIVE_PLAYERS.filter(function (p) {{
          const fn = p.full_name || (p.first_name + " " + p.last_name);
          return fn.toLowerCase().includes(qLower);
        }});
        openSuggestions(filtered);
      }}

      function buildTeamLogoUrl(abbrev) {{
        if (!abbrev) return "";
        return (
          "https://a.espncdn.com/i/teamlogos/nba/500/scoreboard/" +
          abbrev.toLowerCase() +
          ".png"
        );
      }}

      async function searchPlayer() {{
        const name = (input.value || "").trim();
        if (!name) {{
          setError("Merci d’entrer un nom de joueur.");
          return;
        }}

        setError("");
        results.style.display = "none";
        clearSources();
        setLoading(true);
        closeSuggestions();
        playerCard.style.display = "none";

        try {{
          const url =
            "/injuries/by-player?name=" +
            encodeURIComponent(name);
          const res = await fetch(url, {{ method: "GET" }});
          if (!res.ok) {{
            throw new Error("API error " + res.status);
          }}
          const data = await res.json();
          renderResults(data);
        }} catch (e) {{
          console.error(e);
          setError(
            "Impossible de récupérer les données de blessures."
          );
        }} finally {{
          setLoading(false);
        }}
      }}

      function computeInitials(fullName) {{
        if (!fullName) return "";
        const parts = fullName.trim().split(/\\s+/);
        if (parts.length === 1) {{
          return parts[0].charAt(0).toUpperCase();
        }}
        return (
          parts[0].charAt(0).toUpperCase() +
          parts[parts.length - 1].charAt(0).toUpperCase()
        );
      }}

      function renderAggregatedBadge(aggregated) {{
        if (!aggregated) {{
          playerAggEl.style.display = "none";
          playerAggEl.textContent = "";
          playerAggEl.classList.remove("ia-agg-clear", "ia-agg-flagged");
          return;
        }}
        const status = aggregated.status || "clear";
        playerAggEl.classList.remove("ia-agg-clear", "ia-agg-flagged");

        if (status === "clear") {{
          playerAggEl.classList.add("ia-agg-clear");
          playerAggEl.innerHTML =
            '<span class="ia-agg-dot"></span><span>OK · Aucune info blessure</span>';
        }} else {{
          playerAggEl.classList.add("ia-agg-flagged");
          playerAggEl.innerHTML =
            '<span class="ia-agg-dot"></span><span>ALERTE · Blessé / incertain</span>';
        }}
        playerAggEl.style.display = "inline-flex";
      }}

      function renderPlayerCard(bdlPlayer, aggregated) {{
        if (!bdlPlayer) {{
          playerCard.style.display = "none";
          return;
        }}
        const fullName = bdlPlayer.full_name ||
          (bdlPlayer.first_name + " " + bdlPlayer.last_name);
        playerNameEl.textContent = fullName;

        const initials = computeInitials(fullName);
        playerAvatarInitials.textContent = initials || "";

        renderAggregatedBadge(aggregated);

        const team = bdlPlayer.team || {{}};
        const metaParts = [];
        if (team.name) metaParts.push(team.name);
        if (bdlPlayer.position) metaParts.push(bdlPlayer.position);
        playerMetaEl.textContent = metaParts.join(" · ");

        const logoUrl = buildTeamLogoUrl(team.abbreviation);
        if (logoUrl) {{
          teamLogoEl.style.display = "block";
          teamLogoEl.src = logoUrl;
          teamLogoEl.onerror = function () {{
            this.style.display = "none";
          }};
        }} else {{
          teamLogoEl.style.display = "none";
        }}

        playerCard.style.display = "flex";
      }}

      function renderResults(data) {{
        results.style.display = "block";
        clearSources();

        const aggregated = data.aggregated || null;
        const bdlPlayer = data.sources?.balldontlie?.matched_player || null;
        renderPlayerCard(bdlPlayer, aggregated);

        const bdlInj = (data.sources?.balldontlie?.injuries || [])[0];
        if (!bdlInj) {{
          renderEmpty(srcBdl);
        }} else {{
          const status = bdlInj.status || "N/A";
          const ret = bdlInj.return_date || "";
          srcBdl.innerHTML =
            '<p class="ia-status">' +
            status +
            (ret ? " · retour estimé " + ret : "") +
            "</p>" +
            (bdlInj.description
              ? '<p class="ia-meta">' + bdlInj.description + "</p>"
              : "");
        }}

        const espnInj = (data.sources?.espn?.injuries || [])[0];
        if (!espnInj) {{
          renderEmpty(srcEspn);
        }} else {{
          srcEspn.innerHTML =
            '<p class="ia-status">' +
            (espnInj.status || "N/A") +
            (espnInj.est_return_date
              ? " · retour estimé " + espnInj.est_return_date
              : "") +
            "</p>" +
            (espnInj.comment
              ? '<p class="ia-meta">' + espnInj.comment + "</p>"
              : "");
        }}

        const cbsInj = (data.sources?.cbs?.injuries || [])[0];
        if (!cbsInj) {{
          renderEmpty(srcCbs);
        }} else {{
          srcCbs.innerHTML =
            '<p class="ia-status">' +
            (cbsInj.status || "N/A") +
            "</p>" +
            '<p class="ia-meta">' +
            (cbsInj.injury || "Injury non précisée") +
            (cbsInj.updated ? " · maj " + cbsInj.updated : "") +
            "</p>";
        }}

        const nbcInj = (data.sources?.nbc?.injuries || [])[0];
        if (!nbcInj) {{
          renderEmpty(srcNbc);
        }} else {{
          const team = nbcInj.team || "";
          const date = nbcInj.date || "";
          const desc = nbcInj.description || "";
          srcNbc.innerHTML =
            '<p class="ia-status">NBC injury report</p>' +
            '<p class="ia-meta">' +
            (nbcInj.injury || "Injury non précisée") +
            (date ? " · " + date : "") +
            (team ? " · " + team : "") +
            (desc ? " · " + desc : "") +
            "</p>";
        }}
      }}

      function resetSearch() {{
        input.value = "";
        closeSuggestions();
        setError("");
        results.style.display = "none";
        playerCard.style.display = "none";
        clearSources();
        loader.style.display = "none";
      }}

      wakeBtn.addEventListener("click", wakeService);
      searchBtn.addEventListener("click", searchPlayer);
      resetBtn.addEventListener("click", resetSearch);

      input.addEventListener("keydown", function (e) {{
        if (e.key === "Enter") {{
          searchPlayer();
        }}
        if (e.key === "Escape") {{
          closeSuggestions();
        }}
      }});

      input.addEventListener("input", function () {{
        const q = (input.value || "").trim();
        if (suggTimeout) {{
          clearTimeout(suggTimeout);
        }}
        suggTimeout = setTimeout(function () {{
          fetchSuggestionsLocal(q);
        }}, 150);
      }});

      document.addEventListener("click", function (e) {{
        if (!suggBox.contains(e.target) && e.target !== input) {{
          closeSuggestions();
        }}
      }});
    }})();
  </script>
</body>
</html>
    """
