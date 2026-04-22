import os
from contextlib import asynccontextmanager
from pathlib import Path

import polyline as polyline_lib
import rasterio
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Security
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel
from nrel.routee.compass import CompassApp

import simulate

load_dotenv()

API_KEY = os.getenv("ROUTEE_API_KEY", "")
CONFIG_PATH = os.getenv("COMPASS_CONFIG", "malaysia/osm_default_energy.toml")
SRTM_PATH = os.getenv("SRTM_PATH", str(Path.home() / "srtm_malaysia.tif"))

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

compass_app: CompassApp | None = None
srtm_dataset: rasterio.io.DatasetReader | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global compass_app, srtm_dataset
    print(f"Loading CompassApp from {CONFIG_PATH}...")
    compass_app = CompassApp.from_config_file(CONFIG_PATH)
    print(f"Opening SRTM raster {SRTM_PATH}...")
    srtm_dataset = rasterio.open(SRTM_PATH)
    print("CompassApp ready.")
    yield
    if srtm_dataset is not None:
        srtm_dataset.close()
    srtm_dataset = None
    compass_app = None


app = FastAPI(lifespan=lifespan)


def _require_api_key(key: str = Security(api_key_header)):
    if API_KEY and key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return key


class RouteRequest(BaseModel):
    origin_x: float
    origin_y: float
    destination_x: float
    destination_y: float
    model_name: str = "2016_TOYOTA_Camry_4cyl_2WD"


def _encode_geometry(path: dict) -> str:
    """Encode a Compass geo_json traversal result as a Google Encoded Polyline.

    Compass emits one Feature per traversed edge, each with a LineString. We
    concatenate every edge's coordinates in order, de-duplicating the shared
    vertex between consecutive edges, then encode the full path.
    """
    merged: list[tuple[float, float]] = []
    for feature in path.get("features", []):
        geom = feature.get("geometry") or {}
        if geom.get("type") != "LineString":
            continue
        # GeoJSON is [lng, lat]; polyline expects (lat, lng)
        coords = [(lat, lng) for lng, lat in geom.get("coordinates", [])]
        if not coords:
            continue
        if merged and merged[-1] == coords[0]:
            merged.extend(coords[1:])
        else:
            merged.extend(coords)
    return polyline_lib.encode(merged)


@app.post("/route")
def route(req: RouteRequest, _: str = Security(_require_api_key)):
    query = {
        "origin_x": req.origin_x,
        "origin_y": req.origin_y,
        "destination_x": req.destination_x,
        "destination_y": req.destination_y,
        "model_name": req.model_name,
        "vehicle_rates": {
            "trip_distance": {"type": "distance", "factor": 0.655, "unit": "miles"},
            "trip_time": {"type": "time", "factor": 20.0, "unit": "hours"},
            "trip_energy_liquid": {"type": "energy", "factor": 3.0, "unit": "gge"},
        },
        "grid_search": {
            "test_cases": [
                {"name": "least_time", "weights": {"trip_distance": 0, "trip_time": 1, "trip_energy_liquid": 0}},
                {"name": "least_energy", "weights": {"trip_distance": 0, "trip_time": 0, "trip_energy_liquid": 1}},
                {"name": "balanced", "weights": {"trip_distance": 1, "trip_time": 1, "trip_energy_liquid": 1}},
            ]
        },
    }

    try:
        results = compass_app.run(query)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not isinstance(results, list):
        results = [results]

    routes = []
    for r in results:
        final_state = r["route"]["final_state"]
        routes.append({
            "name": r["request"]["name"],
            "summary": {
                "trip_distance_miles": final_state.get("trip_distance"),
                "trip_time_minutes": final_state.get("trip_time"),
                "trip_energy_liquid_gallons": final_state.get("trip_energy_liquid"),
                "trip_elevation_gain_miles": final_state.get("trip_elevation_gain"),
                "trip_elevation_loss_miles": final_state.get("trip_elevation_loss"),
            },
            "geometry": _encode_geometry(r["route"]["path"]),
        })

    return {"routes": routes}


class TelemetryPoint(BaseModel):
    lat: float
    lng: float
    timestamp: str


class SimulateRequest(BaseModel):
    telemetry: list[TelemetryPoint]
    model_name: str


@app.post("/simulate")
def simulate_endpoint(req: SimulateRequest, _: str = Security(_require_api_key)):
    try:
        return simulate.run_simulation(
            [p.model_dump() for p in req.telemetry],
            req.model_name,
            srtm_dataset,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/simulate/models")
def simulate_models(_: str = Security(_require_api_key)):
    return {"supported_models": simulate.supported_models()}


@app.get("/health")
def health():
    return {"status": "ok", "config": CONFIG_PATH}
