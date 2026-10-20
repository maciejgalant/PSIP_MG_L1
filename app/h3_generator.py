import geopandas as gpd
import h3
from shapely.geometry import Polygon

# Parametry
INPUT_FILE = "/app/data/powiat.shp"
LAYER_NAME = "area"
OUTPUT_FILE = "hexagons.gpkg"
H3_RESOLUTION = 8

# Wczytanie obszaru
gdf = gpd.read_file(INPUT_FILE)
# Konwersja do WGS84 (wymagane przez H3)
gdf = gdf.to_crs(4326)

# Pobranie geometrii
geom = gdf.geometry.union_all()
# Konwersja do GeoJSON
geojson = {
    "type": "Polygon",
    "coordinates": [list(geom.exterior.coords)]
}

# Generowanie indeksów H3
hex_ids = h3.geo_to_cells(
    geojson,
    res=H3_RESOLUTION
)

# Tworzenie geometrii heksagonów
hex_geoms = []

for h in hex_ids:
    boundary = h3.cell_to_boundary(h)
    poly = Polygon([(lon, lat) for lat, lon in boundary])

    hex_geoms.append({
        "h3_id": h,
        "geometry": poly
    })

# Utworzenie GeoDataFrame
hex_gdf = gpd.GeoDataFrame(
    hex_geoms,
    crs="EPSG:4326"
)

# Zapis
hex_gdf.to_file(
    OUTPUT_FILE,
    layer="h3_grid",
    driver="GPKG"
)

print(f"Wygenerowano {len(hex_gdf)} heksagonów")
