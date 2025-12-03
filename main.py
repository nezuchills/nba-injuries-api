from fastapi import FastAPI, HTTPException
from typing import Optional, Dict, Any, List, Tuple
import os
import requests
from bs4 import BeautifulSoup  # parser HTML ESPN & NBC

app = FastAPI()


# ---------- Endpoints de base ----------

@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/")
def root():
    return {"message": "NBA injuries API is running"}


# ---------- Endpoint de test multi-sources (factice) ----------

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
    """
    Appelle un endpoint BallDontLie (NBA) et renvoie le JSON. [web:101]
    """
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


# ---- Injuries (brut) ----

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
    """
    Simplifie un enregistrement injury BallDontLie. [web:43][web:101]
    """
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


# ---- Joueur par ID ----

@app.get("/balldontlie/player/{player_id}")
def balldontlie_player(player_id: int) -> Dict[str, Any]:
    """
    Wrapper {"data": {...}} pour /v1/players/{id}. [web:101]
    """
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


# ---- Recherche joueurs par nom (BallDontLie) ----

@app.get("/players/balldontlie/search")
def players_balldontlie_search(query: str, per_page: int = 25) -> Dict[str, Any]:
    """
    Expose /v1/players?search= pour debug / frontend. [web:101][web:139]
    """
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


# ---- Blessures filtrées par player_id (filtrage local) ----

@app.get("/injuries/balldontlie/by-player-id")
def injuries_balldontlie_by_player_id(
    player_id: int,
    per_page: int = 50,
) -> Dict[str, Any]:
    """
    Renvoie SEULEMENT les blessures BallDontLie pour un joueur donné,
    en filtrant localement sur player.id. [web:43][web:101]
    """
    params: Dict[str, Any] = {
        "per_page": per_page,
    }

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
#                            ESPN
# ============================================================

ESPN_INJURIES_URL = "https://www.espn.com/nba/injuries"


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
    """
    Parse les tableaux ESPN (NAME / POS / EST. RETURN DATE / STATUS / COMMENT). [file:2][web:20]
    """
    soup = BeautifulSoup(html, "html.parser")

    results: List[Dict[str, Any]] = []

    tables = soup.find_all("table")
    for table in tables:
        headers = [th.get_text(strip=True) for th in table.find_all("th")]
        if not headers:
            continue

        wanted_headers = ["NAME", "POS", "EST. RETURN DATE", "STATUS", "COMMENT"]
        if not all(h in headers for h in wanted_headers):
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

NBC_INJURIES_URL = "https://www.nbcsports.com/nba/nba-injuries-nbc-sports"  # page blessures NBC. [web:18]


def _fetch_nbc_html() -> str:
    try:
        resp = requests.get(
            NBC_INJURIES_URL,
            timeout=10,
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
    Parse les tableaux NBC (PLAYER / POS / DATE / INJURY). [web:18]
    On renvoie une liste : player_name, position, date, injury.
    (Les descriptions complètes restent visibles sur le site NBC.)
    """
    soup = BeautifulSoup(html, "html.parser")
    results: List[Dict[str, Any]] = []

    tables = soup.find_all("table")
    for table in tables:
        headers = [th.get_text(strip=True).upper() for th in table.find_all("th")]
        if not headers:
            continue

        wanted_headers = ["PLAYER", "POS", "DATE", "INJURY"]
        if not all(h in headers for h in wanted_headers):
            continue

        idx_name = headers.index("PLAYER")
        idx_pos = headers.index("POS")
        idx_date = headers.index("DATE")
        idx_injury = headers.index("INJURY")

        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 4:
                continue

            name = cells[idx_name].get_text(strip=True)
            pos = cells[idx_pos].get_text(strip=True)
            date = cells[idx_date].get_text(strip=True)
            injury = cells[idx_injury].get_text(strip=True)

            if not name or name.upper() == "PLAYER":
                continue

            results.append(
                {
                    "player_name": name,
                    "position": pos,
                    "date": date,
                    "injury": injury,
                    "source": "nbc",
                }
            )

    return results


@app.get("/nbc/raw")
def nbc_raw() -> Dict[str, Any]:
    html = _fetch_nbc_html()
    return {
        "source": "nbc",
        "url": NBC_INJURIES_URL,
        "status_code": 200,
        "content_snippet": html[:500],
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
#           HELPER: meilleure correspondance joueur BDL
# ============================================================

def _normalize_full_name(p: Dict[str, Any]) -> str:
    full = p.get("full_name")
    if not full:
        first = (p.get("first_name") or "").strip()
        last = (p.get("last_name") or "").strip()
        full = f"{first} {last}".strip()
    return " ".join(full.lower().split())


def _search_bdl_best_player(name: str) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Essaie plusieurs termes pour trouver le meilleur joueur dans BallDontLie :
    - nom complet
    - dernier mot (souvent le nom de famille)
    - premier mot si besoin. [web:101][web:139]
    """
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
#                  ENDPOINT MULTI-SOURCES PAR JOUEUR
# ============================================================

@app.get("/injuries/by-player")
def injuries_by_player(name: str) -> Dict[str, Any]:
    """
    Agrège les infos par joueur sans les fusionner :
    - BallDontLie : joueur correspondant + blessures associées.
    - ESPN : lignes d'injuries dont le nom matche.
    - NBC : lignes d'injuries dont le nom matche. [web:18][web:20][web:101]
    """
    query = name.strip()
    if not query:
        raise HTTPException(status_code=400, detail="name parameter must not be empty")

    query_lower = query.lower()

    # ESPN
    espn_html = _fetch_espn_html()
    espn_all = _parse_espn_injuries(espn_html)
    espn_matches = [
        item
        for item in espn_all
        if query_lower in item["player_name"].lower()
    ]

    # NBC
    nbc_html = _fetch_nbc_html()
    nbc_all = _parse_nbc_injuries(nbc_html)
    nbc_matches = [
        item
        for item in nbc_all
        if query_lower in item["player_name"].lower()
    ]

    # BallDontLie
    best_player, all_players = _search_bdl_best_player(query)

    bdl_injuries: List[Dict[str, Any]] = []
    bdl_player_info: Optional[Dict[str, Any]] = None

    if best_player is not None:
        pid = best_player.get("id")
        team = best_player.get("team") or {}
        full = _normalize_full_name(best_player)

        bdl_player_info = {
            "id": pid,
            "full_name": full,
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

    return {
        "player_query": query,
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
        },
    }
