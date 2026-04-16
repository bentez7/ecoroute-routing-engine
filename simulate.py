import math
from datetime import datetime
from typing import Optional

import fastsim
import rasterio

EARTH_RADIUS_M = 6_371_008.8
GASOLINE_J_PER_GALLON = 121_320_000  # LHV; 1 gal gasoline = 33.7 kWh
J_PER_KWH = 3_600_000
DEFAULT_TEMP_KELVIN = 303.15  # ~30 C, Malaysia ambient
GRADE_CLAMP = 0.20  # cap noisy SRTM-derived slopes at +/-20%

VEHICLE_MAP = {
    "2012_Ford_Fusion": "2012_Ford_Fusion.yaml",
    "2016_Nissan_Leaf_30_kWh": "2016 Nissan Leaf 30 kWh thrml.yaml",
    "2016_Toyota_Prius_Two_FWD": "2016_TOYOTA_Prius_Two.yaml",
    "2017_CHEVROLET_Bolt": "2020 Chevrolet Bolt EV thrml.yaml",
    "2022_Tesla_Model_3_RWD": "2022 Tesla Model 3 RWD thrml.yaml",
    "2022_Renault_Zoe_ZE50_R135": "2022_Renault_Zoe_ZE50_R135.yaml",
}


def supported_models() -> list[str]:
    return sorted(VEHICLE_MAP.keys())


def _parse_timestamp(ts: str) -> float:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def _validate_telemetry(telemetry: list[dict], srtm_bounds: tuple[float, float, float, float]) -> list[tuple[float, float, float]]:
    """Return list of (t_seconds_from_start, lat, lng); raise ValueError on bad input."""
    if len(telemetry) < 2:
        raise ValueError("telemetry must contain at least 2 points")

    west, south, east, north = srtm_bounds
    out: list[tuple[float, float, float]] = []
    t0: Optional[float] = None
    last_t: Optional[float] = None

    for i, p in enumerate(telemetry):
        lat, lng = p["lat"], p["lng"]
        if not (south <= lat <= north and west <= lng <= east):
            raise ValueError(f"telemetry[{i}] lat={lat} lng={lng} outside Malaysia SRTM bounds")
        t = _parse_timestamp(p["timestamp"])
        if t0 is None:
            t0 = t
        if last_t is not None and t <= last_t:
            raise ValueError(f"telemetry[{i}] timestamp not strictly increasing")
        last_t = t
        out.append((t - t0, lat, lng))

    if out[-1][0] < 2:
        raise ValueError("telemetry duration must be at least 2 seconds")
    return out


def _resample_1hz(points: list[tuple[float, float, float]]) -> tuple[list[float], list[float], list[float]]:
    """Linearly interpolate (t, lat, lng) onto a 1s grid. Returns (times, lats, lngs)."""
    duration = int(math.floor(points[-1][0]))
    times = [float(t) for t in range(duration + 1)]
    lats: list[float] = []
    lngs: list[float] = []

    j = 0
    for t in times:
        while j + 1 < len(points) and points[j + 1][0] < t:
            j += 1
        if j + 1 >= len(points):
            lats.append(points[-1][1])
            lngs.append(points[-1][2])
            continue
        t0, lat0, lng0 = points[j]
        t1, lat1, lng1 = points[j + 1]
        frac = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
        frac = max(0.0, min(1.0, frac))
        lats.append(lat0 + frac * (lat1 - lat0))
        lngs.append(lng0 + frac * (lng1 - lng0))
    return times, lats, lngs


def _sample_elevations(srtm: rasterio.io.DatasetReader, lats: list[float], lngs: list[float]) -> list[float]:
    samples = srtm.sample([(lng, lat) for lat, lng in zip(lats, lngs)])
    return [float(v[0]) for v in samples]


def _build_cycle_dict(
    times: list[float],
    lats: list[float],
    lngs: list[float],
    elevs: list[float],
) -> dict:
    n = len(times)
    speeds = [0.0]
    grades = [0.0]
    for i in range(1, n):
        d = _haversine_m(lats[i - 1], lngs[i - 1], lats[i], lngs[i])
        dt = times[i] - times[i - 1]
        speeds.append(d / dt if dt > 0 else 0.0)
        if d > 0.5:
            g = (elevs[i] - elevs[i - 1]) / d
            grades.append(max(-GRADE_CLAMP, min(GRADE_CLAMP, g)))
        else:
            grades.append(0.0)

    return {
        "init_elev_meters": elevs[0],
        "time_seconds": times,
        "speed_meters_per_second": speeds,
        "grade": grades,
        "elev_meters": elevs,
        "temp_amb_air_kelvin": [DEFAULT_TEMP_KELVIN] * n,
        "pwr_max_chrg_watts": [0.0] * n,
        "pwr_solar_load_watts": [0.0] * n,
    }


def _summarise(sd_dict: dict, elevs: list[float]) -> dict:
    veh = sd_dict["veh"]
    pt = veh["pt_type"]
    pt_kind, pt_body = next(iter(pt.items()))

    fc = pt_body.get("fc") if isinstance(pt_body, dict) else None
    res = pt_body.get("res") if isinstance(pt_body, dict) else None

    fuel_j = fc["state"]["energy_fuel_joules"] if fc else None
    elec_j = res["state"]["energy_out_chemical_joules"] if res else None

    gain = 0.0
    loss = 0.0
    for i in range(1, len(elevs)):
        d = elevs[i] - elevs[i - 1]
        if d > 0:
            gain += d
        else:
            loss -= d

    return {
        "powertrain": pt_kind,
        "duration_seconds": float(veh["state"]["time_seconds"]),
        "distance_meters": float(veh["state"]["dist_meters"]),
        "fuel_gallons": (fuel_j / GASOLINE_J_PER_GALLON) if fuel_j is not None else None,
        "electricity_kwh": (elec_j / J_PER_KWH) if elec_j is not None else None,
        "elevation_gain_m": gain,
        "elevation_loss_m": loss,
    }


def run_simulation(
    telemetry: list[dict],
    model_name: str,
    srtm: rasterio.io.DatasetReader,
) -> dict:
    if model_name not in VEHICLE_MAP:
        raise ValueError(f"unsupported model_name; supported: {supported_models()}")

    bounds = (srtm.bounds.left, srtm.bounds.bottom, srtm.bounds.right, srtm.bounds.top)
    points = _validate_telemetry(telemetry, bounds)
    times, lats, lngs = _resample_1hz(points)
    elevs = _sample_elevations(srtm, lats, lngs)
    cyc_dict = _build_cycle_dict(times, lats, lngs, elevs)

    cyc = fastsim.Cycle.from_pydict(cyc_dict)
    veh = fastsim.Vehicle.from_resource(VEHICLE_MAP[model_name])

    sp_dict = fastsim.SimDrive(veh, cyc).to_pydict()["sim_params"]
    sp_dict["trace_miss_opts"] = "Allow"
    sim_params = fastsim.SimParams.from_pydict(sp_dict)

    sd = fastsim.SimDrive(veh, cyc, sim_params)
    sd.walk()

    summary = _summarise(sd.to_pydict(), elevs)
    summary["model_name"] = model_name
    return summary
