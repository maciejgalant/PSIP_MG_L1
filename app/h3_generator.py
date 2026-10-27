"""Generate H3 cells for a county, storing all geometries in EPSG:2180.

H3 indexes require WGS84 coordinates; input and exported geometries use EPSG:2180.
Boundary cells are included via H3's experimental 'overlap' mode.
"""
import os
from pathlib import Path

import geopandas as gpd
import h3
from shapely.geometry import Polygon

INPUT_FILE = Path(os.getenv("H3_INPUT_FILE", "/app/data/powiat.shp"))
OUTPUT_FILE = Path(os.getenv("H3_OUTPUT_FILE", "/app/hexagons.gpkg"))
OUTPUT_LAYER = "h3_grid"
TARGET_CRS = "EPSG:2180"


def resolutions_from_env():
    raw = os.getenv("H3_RESOLUTIONS", "7,8,9")
    try:
        values = [int(value.strip()) for value in raw.split(",")]
    except ValueError as exc:
        raise ValueError(f"Invalid H3_RESOLUTIONS: {raw!r}") from exc
    if not values or len(values) != len(set(values)) or any(not 0 <= res <= 15 for res in values):
        raise ValueError(f"Expected distinct H3 resolutions in 0..15: {raw!r}")
    return sorted(values)


def read_area():
    if not INPUT_FILE.is_file():
        raise FileNotFoundError(f"Missing county boundary: {INPUT_FILE}")
    source = gpd.read_file(INPUT_FILE)
    if source.empty or source.crs is None:
        raise ValueError("County boundary is empty or has no CRS")
    if source.crs.to_epsg() != 2180:
        raise ValueError(f"Expected EPSG:2180 input, got {source.crs}")

    area = source.geometry.dropna().union_all()
    if area.is_empty or area.geom_type not in ("Polygon", "MultiPolygon"):
        raise ValueError("County boundary must contain a non-empty polygon or multipolygon")
    if not area.is_valid:
        raise ValueError("Invalid county boundary geometry; repair it before generating H3")
    return area


def generate(area, resolutions):
    # H3 expects longitude/latitude GeoJSON; GeoPandas handles EPSG:2180 -> WGS84.
    source = gpd.GeoSeries([area], crs=TARGET_CRS).to_crs("EPSG:4326").iloc[0]
    shape = h3.geo_to_h3shape(source.__geo_interface__)
    if not hasattr(h3, "h3shape_to_cells_experimental"):
        raise RuntimeError("H3 4.x with overlap containment mode is required")

    records = []
    for resolution in resolutions:
        cells = sorted(h3.h3shape_to_cells_experimental(shape, resolution, contain="overlap"))
        if not cells:
            raise ValueError(f"H3 resolution {resolution} generated no cells")
        for cell in cells:
            # cell_to_boundary returns (lat, lng); Shapely expects (x=lng, y=lat).
            polygon = Polygon([(lon, lat) for lat, lon in h3.cell_to_boundary(cell)])
            records.append({"h3_id": cell, "resolution": resolution, "geometry": polygon})
        print(f"H3 resolution {resolution}: {len(cells)} candidate cells", flush=True)

    result = gpd.GeoDataFrame(records, geometry="geometry", crs="EPSG:4326").to_crs(TARGET_CRS)
    # Keep complete hexagon geometries, not clipped fragments; retain cells touching the AOI.
    result = result.loc[result.geometry.intersects(area)].copy()
    if result.empty or result.h3_id.duplicated().any():
        raise ValueError("Empty grid or duplicate H3 identifiers")
    if result.geometry.is_empty.any() or not result.geometry.is_valid.all():
        raise ValueError("Invalid H3 geometries after projection")
    return result


def main():
    resolutions = resolutions_from_env()
    area = read_area()
    grid = generate(area, resolutions)
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    # This output is dedicated to H3, so remove any stale layers before saving.
    OUTPUT_FILE.unlink(missing_ok=True)
    grid.to_file(OUTPUT_FILE, layer=OUTPUT_LAYER, driver="GPKG", index=False)
    print(f"Saved {len(grid)} H3 cells to {OUTPUT_FILE} ({TARGET_CRS})", flush=True)
    for resolution in resolutions:
        count = (grid["resolution"] == resolution).sum()
        print(f"  resolution={resolution}: {count}", flush=True)


if __name__ == "__main__":
    main()
