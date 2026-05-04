from typing import List, Dict
import folium
import geopandas as gpd
import pandas as pd


def create_scores_for_min_distance(
    min_distances_services_normal: pd.DataFrame,
    min_distances_services_disrupted: pd.DataFrame,
    nodes: pd.DataFrame,
):
    scores_for_service_list = list()

    for index, normal_distance in min_distances_services_normal.items():
        disrupted_distance = min_distances_services_disrupted[index]
        difference = disrupted_distance - normal_distance

        if disrupted_distance == float("inf"):
            # wenn keine routen möglich dann score = 0
            score = 0
        else:
            score = normal_distance / disrupted_distance
        node_row = nodes.loc[index]
        scores_for_service_list.append(
            {
                "to_node_id": index,
                "geometry": node_row["geometry"],
                "normal_distance": normal_distance,
                "disrupted_distance": disrupted_distance,
                "difference": difference,
                "score": score,
            }
        )
    return pd.DataFrame.from_records(scores_for_service_list)


def create_scores_for_all_services(
    distances_services_normal: pd.DataFrame,
    distances_services_disrupted: pd.DataFrame,
    nodes: pd.DataFrame,
):
    # adapted script for services that have no min distance / with different grouping)
    scores_services_list = list()

    for index, row in distances_services_normal:
        normal_distance = int(row["route_length"].item())
        disrupted_groups = distances_services_disrupted.groups

        if index in disrupted_groups:
            disrupted_distance_entry = distances_services_disrupted.get_group(index)
            disrupted_distance = disrupted_distance_entry["route_length"].item()
            if disrupted_distance == float("inf"):  # < 0
                score = 0
            else:
                score = normal_distance / disrupted_distance
        else:
            raise RuntimeError(f"Missing index in disrupted_groups {index}")

        difference = disrupted_distance - normal_distance
        node_row = nodes.loc[index[1]]

        entry = {
            "from": index[0],
            "to_node_id": index[1],
            "geometry": node_row["geometry"],
            "normal_distance": normal_distance,
            "disrupted_distance": disrupted_distance,
            "difference": difference,
            "score": score,
        }
        scores_services_list.append(entry)
    return pd.DataFrame.from_records(scores_services_list)


def get_nodes_in_districts(boundaries: gpd.GeoDataFrame, nodes: pd.DataFrame):
    districts_details: List[Dict] = list()
    for idx_district, row_district in boundaries.iterrows():
        district_name = row_district["name"]
        sampled_nodes_within_this_district = nodes[nodes.intersects(row_district["geometry"])]
        for idx_node, row_node in sampled_nodes_within_this_district.iterrows():
            districts_details.append({"node_id": idx_node, "district_name": district_name})
    return pd.DataFrame(districts_details)


def get_scores_by_district(
    boundaries: pd.DataFrame,
    nodes_in_districts: pd.DataFrame,
    scores_fire_stations: pd.DataFrame,
    scores_hospitals: pd.DataFrame,
):
    # create a dataframe that contains the score for every districts...
    district_summary_list: List[Dict] = list()
    for district_name in boundaries["name"].unique():
        nodes_in_current_districts_mask = nodes_in_districts["district_name"] == district_name
        nodes_in_current_districts = nodes_in_districts[nodes_in_current_districts_mask]
        if len(nodes_in_current_districts) == 0:
            print(f"No samples in District {district_name} found")
            continue
        # all scores for fire stations within the district
        hospital_scores_district = scores_hospitals[
            scores_hospitals["to_node_id"].isin(nodes_in_current_districts["node_id"])
        ]
        # disrupted scores for fire stations within the district
        hospital_scores_for_district_disrupted = hospital_scores_district[hospital_scores_district["score"] == 0]

        # percentage of disrupted hospitals within the district
        percentage_disrupted_hospitals = len(hospital_scores_for_district_disrupted) / len(hospital_scores_district)
        # normal scores for fire stations within the district
        hospital_scores_for_district_connected = hospital_scores_district[hospital_scores_district["score"] > 0]

        # all scores for fire stations within the district
        fire_station_scores_for_district = scores_fire_stations[
            scores_fire_stations["to_node_id"].isin(nodes_in_current_districts["node_id"])
        ]

        # disrupted scores for fire stations within the district
        fire_station_scores_for_district_disrupted = fire_station_scores_for_district[
            fire_station_scores_for_district["score"] == 0
        ]
        # percentage of disrupted fire stations within the district
        percentage_disrupted_fire_stations = len(fire_station_scores_for_district_disrupted) / len(
            fire_station_scores_for_district
        )

        # normal scores for fire stations within the district
        fire_station_scores_for_district_connected = fire_station_scores_for_district[
            fire_station_scores_for_district["score"] > 0
        ]

        mean_distance_connected_hospitals_connected = hospital_scores_for_district_connected["normal_distance"].mean()
        mean_distance_connected_hospitals_disrupted = hospital_scores_for_district_connected[
            "disrupted_distance"
        ].mean()

        mean_distance_connected_fire_stations_connected = fire_station_scores_for_district_connected[
            "normal_distance"
        ].mean()
        mean_distance_connected_fire_stations_disrupted = fire_station_scores_for_district_connected[
            "disrupted_distance"
        ].mean()

        mean_score_hospitals_total = hospital_scores_district["score"].mean()
        mean_score_fire_stations_total = fire_station_scores_for_district["score"].mean()

        mean_score_fire_stations_connected = fire_station_scores_for_district_connected["score"].mean()
        mean_score_hospitals_connected = hospital_scores_for_district_connected["score"].mean()

        district_summary_list.append(
            {
                "district_name": district_name,
                # percentage disrupted
                "percentage_disrupted_hospitals": percentage_disrupted_hospitals,
                "percentage_disrupted_fire_stations": percentage_disrupted_fire_stations,
                # total score
                "mean_score_hospitals_total": mean_score_hospitals_total,
                "mean_score_fire_stations_total": mean_score_fire_stations_total,
                # connected score
                "mean_score_hospitals_connected": mean_score_hospitals_connected,
                "mean_score_fire_stations_connected": mean_score_fire_stations_connected,
                # mean distances for normal and disrupted scenarios
                "mean_distance_connected_hospitals_connected": mean_distance_connected_hospitals_connected,
                "mean_distance_connected_hospitals_disrupted": mean_distance_connected_hospitals_disrupted,
                "mean_distance_connected_fire_stations_connected": mean_distance_connected_fire_stations_connected,
                "mean_distance_connected_fire_stations_disrupted": mean_distance_connected_fire_stations_disrupted,
            }
        )
    return pd.DataFrame(district_summary_list)


def add_categorical_legend(folium_map, title, icons, labels, icon_size=10):
    # copied from https://stackoverflow.com/questions/65042654/how-to-add-categorical-legend-to-python-folium-map
    if len(icons) != len(labels):
        raise ValueError("icons and labels must have the same length.")

    icons_by_label = dict(zip(labels, icons))

    legend_categories = ""
    for label, icon in icons_by_label.items():
        legend_categories += (
            f"<li><img src='{icon}' style='width: {icon_size}px; height: {icon_size}px;'></img> - {label}</li>"
        )

    legend_html = f"""
    <div id='maplegend' class='maplegend'>
      <div class='legend-title'>{title}</div>
      <div class='legend-scale'>
        <ul class='legend-labels'>
        {legend_categories}
        </ul>
      </div>
    </div>
    """
    script = f"""
        <script type="text/javascript">
        var oneTimeExecution = (function() {{
                    var executed = false;
                    return function() {{
                        if (!executed) {{
                             var checkExist = setInterval(function() {{
                                       if ((document.getElementsByClassName('leaflet-top leaflet-right').length) || (!executed)) {{
                                          document.getElementsByClassName('leaflet-top leaflet-right')[0].style.display = "flex"
                                          document.getElementsByClassName('leaflet-top leaflet-right')[0].style.flexDirection = "column"
                                          document.getElementsByClassName('leaflet-top leaflet-right')[0].innerHTML += `{legend_html}`;
                                          clearInterval(checkExist);
                                          executed = true;
                                       }}
                                    }}, 100);
                        }}
                    }};
                }})();
        oneTimeExecution()
        </script>
      """

    css = """

    <style type='text/css'>
      .maplegend {
        z-index:9999;
        float:left;
        background-color: rgba(255, 255, 255, 1);
        border-radius: 5px;
        border: 2px solid #bbb;
        padding: 10px;
        font-size:16px;
        positon: relative;
      }
      .maplegend .legend-title {
        text-align: left;
        margin-bottom: 5px;
        font-weight: bold;
        font-size: 90%;
        }
      .maplegend .legend-scale ul {
        margin: 0;
        margin-bottom: 5px;
        padding: 0;
        float: left;
        list-style: none;
        }
      .maplegend .legend-scale ul li {
        font-size: 80%;
        list-style: none;
        margin-left: 0;
        line-height: 18px;
        margin-bottom: 2px;
        }
      .maplegend ul.legend-labels li span {
        display: block;
        float: left;
        height: 16px;
        width: 30px;
        margin-right: 5px;
        margin-left: 0;
        border: 0px solid #ccc;
        }
      .maplegend .legend-source {
        font-size: 80%;
        color: #777;
        clear: both;
        }
      .maplegend a {
        color: #777;
        }
    </style>
    """

    folium_map.get_root().header.add_child(folium.Element(script + css))

    return folium_map
