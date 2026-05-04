# CSS Geodata Service / Module

In a flawed attempt to collect all relevant tools and scripts use to manipulate geo data this repository has become a somewhat chaotic bundle of different tools. There are the following topics:

- General geodata manipulation e.g. to extract- or generate information, copping and other helpers and
- Bigger tools like:
- intersecting population data with shapes to identify persons affected by events
- RoA

## Robustness of Accessibility

- For details on the RoA application and implementation refer to the [README](css_geodata_service/robustness_of_accessability/README.md) in the corresponding module.


## Dependencies
- To install dependencies run `uv sync` in the root directory of the project.
- If you are not familiar with `uv` just have a quick glance into the docs and install instructions of [astral](https://docs.astral.sh/uv/getting-started/installation/) 
- If you don't want to use uv for testing you can manually manage and install your dependencies. This is not recommanded and not accepted for production development 


## Notice

- This readme is work in process and copied from the previously created [geodata-toolbox](https://git.opendfki.de/css/css-geodata-toolbox)
- There are some links bellow that might be useful. The tool descripton, aside from [RoA](css_geodata_service/robustness_of_accessability/README.md) should be ignored for now

## Other Tools and technical overview

- The tools in this repository can be grouped by either their purpose or by the type of data they work with.

#### Data Types:

- TIF (GeoTIFF) - These files contain raster data, in the context of this repository, they are used to store elevation
  data. The elevation data is stored as a 32-bit floating point number, and is stored in the "band" of the file.
- GPKG (GeoPackage) - These files contain tabular data, in the context of this repository, they are used to store
  data that is associated with a specific point. For example information about the AKRIMA Story like distribution
  information
- GML (Geography Markup Language) - These files contain vector / polygon data. In the context of this repository, these
  are information about _flooding areas_.

### External requirements

- Osmium

### Troubleshooting

- When you have issues with GDAL on macOS try to install osgeo with homebrew
  c.f. [osgeo homebrew](https://github.com/OSGeo/homebrew-osgeo4mac)
- Add GDAL to PYTHONPATH c.
  f. [stackoverflow](https://stackoverflow.com/questions/58277993/gdal-python-module-not-found)

````shell

- If there are issues with the osgeo gdal package e. g. when importing the _gdal_array run
```shell
pip3 install --no-cache-dir --force-reinstall 'GDAL[numpy]==3.6.2
````

as suggested on [stackoverflow](https://stackoverflow.com/questions/75372275/importerror-cannot-import-name-gdal-array-from-osgeo)

### Caveats / Assignment of lat or lon to x and y

_This is mainly a note for myself but is relevant for the `transform_coordinate_crs(lat, lon, input_epsg, output_epsg):`
function in `helper.utils`_

- **Lon**gitude ist Längengrad oder Meridian (Von Nord nach Süd und teilen damit die Position Westen richtung Osten ein. Der Winkel beschreibt
  die "horizontale" Entfernung von London

- **Lat**itude ist der Breitengrad (auch Breitenkreise -> Äquator -> 0°) Winkel der Nord / Süd. Je näher an der Wert an
  90, umso näher am Pol. Der Winkel beschreibt also die "vertikale" Entfernung vom Nordpol

Wenn man mit https://epsg.io/ zwischen den EPSG Systemen 4326 und 3035 übersetzt, impliziert das Tool, dass longitude =
x ist und latitude = y. Siehe:
![Screenshot](epsg_transform.png)

Bei Verwendung der `org.Geometry` Implementierung ist es anscheinend notwendig die Werte zu tauschen, sonst kommen nicht
die erwarteten Werte heraus.

### Further reading

- Indexing and selecting data in [GeoPandas](https://geopandas.org/docs/user_guide/indexing.html)
- [Using Geopandas](https://geopandas.org/en/stable/gallery/plot_clip.html) to clip or visualize data
- [System tools](https://docs.opentripplanner.org/en/v2.2.0/Preparing-OSM/) like `Osmosis`, `osmconvert` and
  `Osmium` to extract data from OSM
- [Using Osmfilter](https://journocode.com/en/tutorials/extracting-geodata-from-openstreetmap-with-osmfilter/) ti extract geodata
- [Using GDAL](https://stackoverflow.com/questions/24956653/read-elevation-using-gdal-python-from-geotiff) to get
  elevation data from a GeoTIFF
- Filter by bounding box in [GeoPandas](https://gis.stackexchange.com/questions/266730/filter-by-bounding-box-in-geopandas)
- Fast point selection inside polygon using [GeoPandas](https://gis.stackexchange.com/questions/346550/accelerating-geopandas-for-selecting-points-inside-polygon/346710#346710)
- [Combine Polygons using shapely](https://stackoverflow.com/questions/40385782/make-a-union-of-polygons-in-geopandas-or-shapely-into-a-single-geometry)
- [Find points in polygon](hhttps://automating-gis-processes.github.io/CSC18/lessons/L4/point-in-polygon.html#point-in-polygon-using-geopandas)
- [Simplyfy Polygon](https://geopandas.org/en/stable/docs/reference/api/geopandas.GeoSeries.simplify.html)

### Visualization

- Plotting data in GeoPandas using [Folium](https://geopandas.org/gallery/plotting_with_folium.html) (Interactive Leaflet Map)
- Create marker on a [Folium Map 1](https://www.geeksforgeeks.org/python-adding-markers-to-volcano-locations-using-folium-package/)
- Create marker on a [Folium Map 2](https://darigak.medium.com/your-guide-to-folium-markers-b9324fc7d65d)
- Plot polygons on a [Folium Map](https://geopandas.org/en/stable/gallery/polygon_plotting_with_folium.html)
