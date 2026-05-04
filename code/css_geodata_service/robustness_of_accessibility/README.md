# RoA - Robustness of Accessibility:

This module contains the `production` implementation of the RoA initially applied to the [AKRIMA](https://akrima.dfki.de/) project

- Original Implementation (jupyter notebooks) used in the intial publication can be found [here](https://git.opendfki.de/css/traffic-resilience)

## Abstract (Shortest-Path-Based Resilience Analysis of Urban Road Networks)

Resilience of critical infrastructure such as road networks is
crucial to maintain provision of essential logistics services even and espe-
cially during disruptive events. This paper proposes a new method for
assessing the resilience of urban road networks using shortest path analy-
sis. The method is based on representative routes which connect selected
Points of Interest with service providers. By comparing reachability and
shortest path lengths for these routes in an intact road network with
those in a compromised network, weakly connected areas are detected
and the overall network resilience against the respective disruption anal-
ysed. To that end, the paper proposes the Robustness of Accessibility
index as a novel score for the resilience of critical infrastructure. To
demonstrate the proposed method, a case study of flooding in Trier,
Germany, provides insights into the vulnerability of the city’s road net-
work in terms of potential response delays in emergency logistics. Such
an analysis can help policymakers and planners improve the robustness
and reliability of critical infrastructure and logistics processes.

### Tags

`Emergency Logistics`, `Critical Infrastructure`, `Shortest Path Analysis`, `Robustness of Accessibility Index`

## Assumptions / Core idea of the implementation

This implementation is based on the assumption that edges of a network that intersect with e.g a flooded area are no longer abailable for traffic. Thus we do the following steps:

- Get (multi)ploygon that describes the area that is affected by an event
- Use `pyrosm` or `osmnx` to get the road network from osm data as a `networkx` `graph` object. In this step we have to decide what kind of network we want to use as a basis for the analysis. This depends on the rules for the "trafic" we want to assume. I.e. rescue services may use roads that normaly are not available to traffic. This can mean usage of specific service / access roads, or even food paths depending on the interpretation. Another assumption that needs to be specified is weather the graph should be directed i.e. traffic can only go in the correct direction of one way roads vs. rescue services may use roads into any direction independent of actual traffic rules
- Use `shapely` or `geopandas` to get the intersection of the road network and the shape that is affected by the crisis.
- Remove this intersection from a copy of the original network now refered to as the _affected network_.
- Get some sample / target locations from the road network as basis of the calulation
- Determine the kind of sources e.g. rescue services like fire stations or hospitals.
- Determine the distance between each sample / target and the closest service station for both the original and the _affected network_.
- Calculate the relative change in those distances as the **RoA**-index

## Project Overview

### Dependencies
- To install dependencies run `uv sync` in the root directory of the project.
- If you are not familiar with `uv` just have a quick glance into the docs and install instructions of [astral](https://docs.astral.sh/uv/getting-started/installation/) 
- If you don't want to use uv for testing you can manually manage and install your dependencies. This is not recommanded (and causes much more effort) and not accepted for production development 


### Other prerequisits
- Add data that represents the affected area that usually means:
- Add Flooding data for all germany or copped to a area that matches or contains the relevant region
  - Default target dir is `css_geodata_service/robustness_of_accessibility/data/input/Flooding/HazardAreas`
  - Adapt according to your needs. Path for examples (noteboosk and script) can be globally specified via static class `RoaNotebookConfig` in `examples/notebooks/noteboo_utils.py` 


### Notes and quick pointers

- The production implementation lives mainly in `robustness_of_accessibility.py` and `disrupted_graph.py`.
- Use `example_dijkstra.py` to see a minimal end-to-end example (build graph -> sample routes -> compute shortest paths).
- `extract_osm.py` contains the preprocessing steps to load OSM into the network representation used by the analysis.
- Cached JSON files inside `cache/` are used by `osmnx` both by the notebooks/examples and the main code to avoid re-downloading/processing large OSM extracts.
- Place you input data into `data/input/...`. 
  - You may want to add sub folders inside `data/input/...` if you want to have different input data for your analysis. This could e.g. mean different shapes that describe different event types
  - For Flooding data the sub directory `Flooding/HazardAreas` already exists.
  - Flooding data can be found at our [data portal](https://opendata.dfki.uni-trier.de/dataset/?_tags_limit=0&tags=flooding), the original [source](https://geoportal.bafg.de/ggina-portal/) from the *BfG* or any other source of you chosing.

## Suggested quick start

- The best way to get familiar with the code and for prototyping is often using Jupyter Notebooks. They allow step by step interaction with different aspects of the code and allow interaction with (itermediate) results. The notebooks import a lot of functions from the main implementation but also have some redudancies for educational purposes. All code that is relevant for production application should live outside of the notebooks - at least once prototyping is complete.

### Jupyter Notebooks for interactive execution and introduction into the module

- We provivde interactive notebooks to facilitate interaction with the codebase, this should allow new users to get an understanding on how the algorithm works and how to interact with the code base
-
- [Extract and Persist Data](notebooks/01_Extract_and_Persist_Data.ipynb)
- [Construct disrupted Graph](notebooks/02_a_Construct_disrupted_Graph.ipynb)
- [(Optional) Visualize Graphs](notebooks/02_b_Visualize_Graphs.ipynb)
- [Robustness of Accessibility](notebooks/03_RobustnessOfAccessibility.ipynb)
- [AnalyzeResults](notebooks/04_AnalyzeResults.ipynb)

### Other examples

- Run `example_dijkstra.py` to reproduce a small demo run that demonstrates the sampling + shortest-path comparison workflow used for the RoA index. It was initially created to compare the performance of different custom implementations as wells as path finding algoithms in `networkx`. Since it uses the major interfaces with which one has to interact when doing an analysis, it is a good start to build upon.
- For larger analyses, add your custom data to `data/input/...` ajust the paths etc. in you own environment.

## Visualizations

![roa visualization in akrima](https://akrima.dfki.de/assets/images/posts/roA_map.png)

- Visual examples can be found in the gallery of the AKRIMA [project website](https://akrima.dfki.de/#gallery)
- There are of cause multiple ways to aggregate and visualize the data:
  - The given example shows individual samples. The publication primarily uses data aggregated on a district or city level and gives a broader overview on the resilience of a bigger area.
  - Areal data can be e.g. be visualized using heatmaps
- The notebooks contain some code for visualization of the results or intermediate data

## Gotchas and miscelanous

- Bridges:

  - The given implementation assumes that bridges are imun to flooding or events. This is becuase it does not use any elevation data but only the intersection between a geo-located graph (osm network) and a given (multi)polygon.
  - While edges of the graph that are tagged as `bridge`s in osm will not be deleted in the disrupted graph this does not nessessarily mean that they are actually usable. For instance if the environment / access lane onto a bridge is flooded there is no use of having a bridge that can not be reached.
  - If not yet available we need a flag that allows for global configuration of this **TODO**
  - An imporovement of this could use a dem (digital eleveation model) to improve upon this. Due to the low resolution of commonly available dem this is most likely not a real improvement
