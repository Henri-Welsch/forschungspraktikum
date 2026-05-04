import json
from typing import List, Dict
import networkx as nx
import shapely
from shapely.geometry import Point, MultiLineString
import geopandas as gpd
from pydantic import ConfigDict, BaseModel


class PointEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Point):
            # Return a JSON-serializable representation of the Point object
            # return {'type': 'Point', 'coordinates': obj.coords[0]}
            return {obj.coords[0]}
        return super().default(obj)


class RouteDetails(BaseModel):
    # from = service
    from_name: str | None
    from_node_id: int | None
    from_position: Point | None
    from_osm_id: int | None
    # to = sample
    to_node_id: int
    to_centroid: Point
    service_type: str
    route_length: float
    route: MultiLineString | None = None
    travel_time: float = None
    model_config = ConfigDict(arbitrary_types_allowed=True)


class NodeDetails(BaseModel):
    node_index: int
    node_point: Point
    routes: List[RouteDetails]
    model_config = ConfigDict(arbitrary_types_allowed=True)


class DistrictDetails(BaseModel):
    district_name: str
    district_centroid: Point
    district_area: float
    node_details: List[NodeDetails]
    model_config = ConfigDict(arbitrary_types_allowed=True)


class AreaDetails(BaseModel):
    district_details: Dict[str, DistrictDetails]
    model_config = ConfigDict(arbitrary_types_allowed=True)


class ColorConfig:
    # Define a list of 118 color values (created by chat gpt) to be distinguishable
    colors = [
        "red",
        "blue",
        "green",
        "orange",
        "purple",
        "brown",
        "pink",
        "gray",
        "black",
        "maroon",
        "navy",
        "teal",
        "olive",
        "gold",
        "peru",
        "orchid",
        "salmon",
        "tan",
        "slateblue",
        "steelblue",
        "darkcyan",
        "limegreen",
        "darkkhaki",
        "darkgoldenrod",
        "cornflowerblue",
        "lightcoral",
        "rosybrown",
        "sienna",
        "saddlebrown",
        "darkolivegreen",
        "darkseagreen",
        "mediumaquamarine",
        "palegreen",
        "khaki",
        "burlywood",
        "indianred",
        "tomato",
        "dodgerblue",
        "mediumblue",
        "violet",
        "hotpink",
        "darkmagenta",
        "mediumvioletred",
        "crimson",
        "mediumseagreen",
        "olivedrab",
        "darkslategray",
        "cadetblue",
        "mediumturquoise",
        "turquoise",
        "lightseagreen",
        "springgreen",
        "lawngreen",
        "yellowgreen",
        "goldenrod",
        "darkorange",
        "orangered",
        "firebrick",
        "darkred",
        "deepskyblue",
        "royalblue",
        "fuchsia",
        "deeppink",
        "darkviolet",
        "slategray",
        "forestgreen",
        "lime",
        "darkgreen",
        "seagreen",
        "mediumspringgreen",
        "lightgreen",
        "palegoldenrod",
        "sandybrown",
        "chocolate",
        "indigo",
        "darkslateblue",
        "lightblue",
        "cornsilk",
        "linen",
        "honeydew",
        "mintcream",
        "lavenderblush",
        "aliceblue",
        "lavender",
        "thistle",
        "mistyrose",
        "antiquewhite",
        "whitesmoke",
        "gainsboro",
        "lightgray",
        "silver",
        "darkgray",
        "gray",
        "white",
        "ghostwhite",
        "snow",
        "floralwhite",
        "ivory",
        "beige",
        "oldlace",
        "lightyellow",
        "lemonchiffon",
        "papayawhip",
        "blanchedalmond",
        "bisque",
        "peachpuff",
        "moccasin",
        "navajowhite",
        "wheat",
        "cornflowerblue",
        "deepskyblue",
        "cadetblue",
        "mediumturquoise",
        "turquoise",
        "steelblue",
        "darkslategray",
        "mediumblue",
        "purple",
        "mediumvioletred",
    ]


class AffectedGraphRequest(BaseModel):
    nodes: gpd.GeoDataFrame
    edges: gpd.GeoDataFrame
    multipolygon: shapely.geometry.MultiPolygon
    exclude_bridges: bool
    undirected_graph: bool
    filter_nodes: bool
    model_config = ConfigDict(arbitrary_types_allowed=True)


class AffectedGraphResult(BaseModel):
    affected_graph_request: AffectedGraphRequest
    affected_nodes: gpd.GeoDataFrame
    affected_edges: gpd.GeoDataFrame | None = None
    affected_network: nx.MultiGraph | nx.MultiDiGraph
    unaffected_network: nx.MultiGraph | nx.MultiDiGraph
    affected_bridge_nodes: gpd.GeoDataFrame | None = None
    model_config = ConfigDict(arbitrary_types_allowed=True)


class DisruptedGraph(BaseModel):
    nodes: gpd.GeoDataFrame
    edges: gpd.GeoDataFrame
    multipolygon: shapely.MultiPolygon
    exclude_bridges: bool
    model_config = ConfigDict(arbitrary_types_allowed=True)
