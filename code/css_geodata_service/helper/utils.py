def translate_crs_using_geopandas(gdf, crs):
    return gdf.to_crs(crs=crs)
