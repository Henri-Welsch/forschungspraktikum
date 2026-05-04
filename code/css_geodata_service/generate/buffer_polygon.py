import pyproj
from shapely.ops import transform
from shapely.geometry import Polygon
import geopandas as gpd


def add_buffer_in_meters(geom: Polygon, distance_meters: int, src_crs="EPSG:4326") -> Polygon:
    """
    Erzeugt einen Buffer in Metern um eine Shapely-Geometrie.
    Automatische Wahl der passenden UTM-Zone.

    Parameter:
    ----------
    geom : shapely.geometry.BaseGeometry
        Eingabegeometrie.
    distance_meters : float
        Pufferabstand in Metern.
    src_crs : str oder pyproj.CRS, optional
        Koordinatenreferenzsystem der Eingabegeometrie (Standard: WGS84).

    Rückgabe:
    ---------
    shapely.geometry.BaseGeometry
        Gepufferte Geometrie im ursprünglichen CRS.
    """
    # CRS-Objekt erzeugen
    src_crs = pyproj.CRS.from_user_input(src_crs)

    # UTM-Zone aus Geometrie ableiten
    lon, lat = geom.centroid.x, geom.centroid.y
    utm_zone = int((lon + 180) // 6) + 1
    is_north = lat >= 0
    utm_crs = pyproj.CRS.from_epsg(32600 + utm_zone if is_north else 32700 + utm_zone)

    # Transformationen aufbauen
    to_utm = pyproj.Transformer.from_crs(src_crs, utm_crs, always_xy=True).transform
    to_src = pyproj.Transformer.from_crs(utm_crs, src_crs, always_xy=True).transform

    # Transformation, Buffer, Rücktransformation
    geom_utm = transform(to_utm, geom)
    geom_buffered_utm = geom_utm.buffer(distance_meters)
    geom_buffered = transform(to_src, geom_buffered_utm)

    return geom_buffered


def buffer_gdf_in_meters(gdf, distance_meters):
    """
    Wendet einen Buffer in Metern auf alle Geometrien eines GeoDataFrames an.
    Das CRS wird automatisch aus dem GeoDataFrame gelesen.

    Parameter:
    ----------
    gdf : geopandas.GeoDataFrame
        Eingabe-GeoDataFrame mit gültigem CRS.
    distance_meters : float
        Pufferabstand in Metern.

    Rückgabe:
    ---------
    geopandas.GeoDataFrame
        Neues GeoDataFrame mit gepufferten Geometrien.
    """
    if gdf.crs is None:
        raise ValueError("Das GeoDataFrame besitzt kein CRS. Bitte zuerst setzen (z.B. gdf.set_crs('EPSG:4326')).")

    # Buffer auf jede Geometrie anwenden
    buffered_geoms = gdf.geometry.apply(lambda g: add_buffer_in_meters(g, distance_meters, gdf.crs))

    # Neues GeoDataFrame mit gleichen Attributen und CRS
    return gpd.GeoDataFrame(gdf.drop(columns="geometry"), geometry=buffered_geoms, crs=gdf.crs)
