import osmnx as ox
import elevation
import nrel.routee.compass.io.generate_dataset as _gd

from pathlib import Path
from nrel.routee.compass.io import generate_compass_dataset, results_to_geopandas
from nrel.routee.compass.io.generate_dataset import GeneratePipelinePhase
from nrel.routee.compass.plot import plot_route_folium, plot_routes_folium

SRTM_FILE = str(Path.home() / "srtm_kuala_lumpur.tif")
KL_BOUNDS = (101.5, 2.9, 101.9, 3.3)  # (west, south, east, north)

def _add_grade_srtm(g, **_kwargs):
    g = ox.add_node_elevations_raster(g, SRTM_FILE)
    g = ox.add_edge_grades(g)
    return g

_gd.add_grade_to_graph = _add_grade_srtm

if __name__ == "__main__":
    # Download SRTM elevation tiles for KL (skipped if already cached)
    if not Path(SRTM_FILE).exists():
        elevation.clip(bounds=KL_BOUNDS, output=SRTM_FILE, product="SRTM3")
        elevation.clean()

    g = ox.graph_from_place("Kuala Lumpur, Malaysia", network_type="drive")

    pipeline_phases = [
        GeneratePipelinePhase.CONFIG,
        GeneratePipelinePhase.GRAPH,
        GeneratePipelinePhase.POWERTRAIN,
        # GeneratePipelinePhase.CHARGING_STATIONS,  # skip — uses US-only NREL data
    ]
    generate_compass_dataset(g, output_directory="kuala_lumpur", phases=pipeline_phases)
