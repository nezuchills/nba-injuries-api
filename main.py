from fastapi import FastAPI, HTTPException
from typing import Optional, Dict, Any, List
import os
import requests

app = FastAPI()


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/")
def root():
    return {"message": "NBA injuries API is running"}


@app.get("/injuries/test")
def injuries_test(player: Optional[str] = None) -> Dict[str, Any]:
    """
    Endpoint de test qui montre la structure finale pour un joueur :
    - une entrée par source (espn, cbs, nbc, balldontlie)
    - données 100 % factices pour l’instant.
    """
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


@app.get("/balldontlie/raw")
def balldontlie_raw(cursor: Optional[int] = None, per_page: int = 25) -> Dict[str, Any]:
    """
    Renvoie les données brutes de BallDontLie pour les blessures (JSON complet).
    - Paginate avec cursor / per_page.
    """
    api_key = os.getenv("BALLDONTLIE_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="BALLDONTLIE_API_KEY is not set on the server",
        )

    url = "https://api.balldontlie.io/v1/player_injuries"
    headers = {"Authorization": api_key}
    params: Dict[str, Any] = {"per_page": per_page}
    if cursor is not None:
        params["cursor"] = cursor

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Error calling BallDontLie: {e}")

    if resp.status_code != 200:
        raise HTTPException(
            status_code=resp.status_code,
            detail=f"BallDontLie error: {resp.text[:200]}",
        )

    data = resp.json()
    return {
        "source": "balldontlie",
        "endpoint": url,
        "params": params,
        "data": data,
    }


def _map_balldontlie_injury(item: Dict[str, Any]) -> Dict[str, Any]:
    """
    Transforme un enregistrement brut de BallDontLie en format simplifié.
    La structure exacte dépend de l’API ; on adapte en fonction des champs présents. [web:101]
    """
    player = item.get("player", {})  # objet joueur (dépend du schéma BallDontLie) [web:101]
    team = item.get("team", {})      # objet équipe, si présent [web:101]

    return {
        "player_id": player.get("id"),
        "player_name": player.get("full_name") or player.get("name"),
        "team_id": team.get("id"),
        "team_name": team.get("full_name") or team.get("name"),
        "status": item.get("status"),           # ex: "out", "questionable" [web:101]
        "injury": item.get("injury"),           # type de blessure [web:101]
        "description": item.get("description"), # description longue [web:101]
        "return_date": item.get("return_date"), # date estimée [web:101]
        "last_update": item.get("updated_at") or item.get("created_at"),
        "source": "balldontlie",
    }


@app.get("/injuries/balldontlie")
def injuries_balldontlie(
    per_page: int = 25,
    cursor: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Renvoie les blessures BallDontLie dans un format simplifié pour le dashboard. [web:101]
    """
    api_key = os.getenv("BALLDONTLIE_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="BALLDONTLIE_API_KEY is not set on the server",
        )

    url = "https://api.balldontlie.io/v1/player_injuries"
    headers = {"Authorization": api_key}
    params: Dict[str, Any] = {"per_page": per_page}
    if cursor is not None:
        params["cursor"] = cursor

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Error calling BallDontLie: {e}")

    if resp.status_code != 200:
        raise HTTPException(
            status_code=resp.status_code,
            detail=f"BallDontLie error: {resp.text[:200]}",
        )

    raw = resp.json()
    raw_data: List[Dict[str, Any]] = raw.get("data", [])
    meta = raw.get("meta", {})

    simplified = [_map_balldontlie_injury(item) for item in raw_data]

    return {
        "source": "balldontlie",
        "count": len(simplified),
        "meta": meta,
        "injuries": simplified,
    }
