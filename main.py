from fastapi import FastAPI, HTTPException
from typing import Optional, Dict, Any, List
import os
import requests
from bs4 import BeautifulSoup  # pour parser l'HTML ESPN [web:131][web:134]

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
                "url": "https://www.nbcsports.com/nba/nba/injuries-nbc-sports",
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


# ---------- Helpers BallDontLie ----------

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


# ---------- BallDontLie : raw injuries ----------

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
    Ici, item est déjà un objet injury (sans wrapper data). [web:101]
    """
    player = item.get("player") or {}
    team = player.get("team") or {}
    return {
        "player_id": player.get("id"),
        "player_name": player.get("full_name"),
        "team_id": team.get("id"),
        "team_name": team.get("full_name") or team.get("name"),
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


# ---------- BallDontLie : infos joueur par ID ----------

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


# ---------- BallDontLie : blessures filtrées par player_id ----------

@app.get("/injuries/balldontlie/by-player-id")
def injuries_balldontlie_by_player_id(
    player_id: int,
    per_page: int = 50,
) -> Dict[str, Any]:
    params: Dict[str, Any] = {
        "per_page": per_page,
        "player_id": player_id,
    }

    raw = _call_balldontlie("/v1/player_injuries", params=params)
    raw_data: List[Dict[str, Any]] = raw.get("data", [])
    meta = raw.get("meta", {})

    simplified = [_map_balldontlie_injury(item) for item in raw_data]

    return {
        "source": "balldontlie",
        "player_id": player_id,
        "count": len(simplified),
        "meta": meta,
        "injuries": simplified,
    }


# ---------- ESPN : récupération brute de la page ----------

ESPN_INJURIES_URL = "https://www.espn.com/nba/injuries"  # page principale des blessures. [web:20]


@app.get("/espn/raw")
def espn_raw() -> Dict[str, Any]:
    """
    Récupère l'HTML brut de la page ESPN injuries.
    Étape 1 : juste pour vérifier qu'on atteint bien la page depuis Render. [web:20]
    """
    try:
        resp = requests.get(ESPN_INJURIES_URL, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Error calling ESPN: {e}")

    if resp.status_code != 200:
        raise HTTPException(
            status_code=resp.status_code,
            detail=f"ESPN error: {resp.text[:200]}",
        )

    # On ne renvoie pas tout l'HTML (trop gros), juste quelques infos de debug.
    return {
        "source": "espn",
        "url": ESPN_INJURIES_URL,
        "status_code": resp.status_code,
        "content_snippet": resp.text[:500],
    }
