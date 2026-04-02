import osmnx as ox
import elevation
import rasterio
import nrel.routee.compass.io.generate_dataset as _gd

from pathlib import Path
from rasterio.merge import merge
from nrel.routee.compass.io import generate_compass_dataset, results_to_geopandas
from nrel.routee.compass.io.generate_dataset import GeneratePipelinePhase
from nrel.routee.compass.plot import plot_route_folium, plot_routes_folium

SRTM_FILE = str(Path.home() / "srtm_malaysia.tif")
# Bounding box covering all of Malaysia (Peninsular + Sabah + Sarawak)
MALAYSIA_BOUNDS = (99.6, 0.85, 119.4, 7.4)  # (west, south, east, north)
CHUNK_SIZE = 2.0  # degrees per chunk — keeps each download under the 10-tile limit


def _download_malaysia_srtm(output_file):
    """Download SRTM elevation tiles for Malaysia in small chunks and merge."""
    west, south, east, north = MALAYSIA_BOUNDS
    tmp_dir = Path.home() / "srtm_malaysia_chunks"
    tmp_dir.mkdir(exist_ok=True)

    chunk_files = []
    lon = west
    while lon < east:
        lat = south
        while lat < north:
            chunk_bounds = (
                round(lon, 4),
                round(lat, 4),
                round(min(lon + CHUNK_SIZE, east), 4),
                round(min(lat + CHUNK_SIZE, north), 4),
            )
            chunk_file = tmp_dir / f"chunk_{chunk_bounds[0]}_{chunk_bounds[1]}.tif"
            if not chunk_file.exists():
                print(f"Downloading chunk {chunk_bounds}...")
                try:
                    elevation.clip(bounds=chunk_bounds, output=str(chunk_file), product="SRTM3")
                    elevation.clean()
                except Exception as e:
                    print(f"  Warning: skipping chunk {chunk_bounds}: {e}")
            if chunk_file.exists():
                chunk_files.append(str(chunk_file))
            lat += CHUNK_SIZE
        lon += CHUNK_SIZE

    print(f"Merging {len(chunk_files)} chunks into {output_file}...")
    datasets = [rasterio.open(f) for f in chunk_files]
    mosaic, transform = merge(datasets)
    profile = datasets[0].profile.copy()
    profile.update({"height": mosaic.shape[1], "width": mosaic.shape[2], "transform": transform})
    with rasterio.open(output_file, "w", **profile) as dst:
        dst.write(mosaic)
    for ds in datasets:
        ds.close()
    print("Merge complete.")


def _add_grade_srtm(g, **_):
    g = ox.add_node_elevations_raster(g, SRTM_FILE)
    g = ox.add_edge_grades(g)
    return g

_gd.add_grade_to_graph = _add_grade_srtm

if __name__ == "__main__":
    # Download SRTM elevation tiles for Malaysia (skipped if already cached)
    if not Path(SRTM_FILE).exists():
        _download_malaysia_srtm(SRTM_FILE)

    g = ox.graph_from_place("Malaysia", network_type="drive")

    pipeline_phases = [
        GeneratePipelinePhase.CONFIG,
        GeneratePipelinePhase.GRAPH,
        GeneratePipelinePhase.POWERTRAIN,
        # GeneratePipelinePhase.CHARGING_STATIONS,  # skip — uses US-only NREL data
    ]
    generate_compass_dataset(g, output_directory="malaysia", phases=pipeline_phases)
