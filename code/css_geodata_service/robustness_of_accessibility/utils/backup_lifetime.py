"""
Building Lifetime & Resource Management Mechanism — simulation engine.

Direct implementation of the 4-state finite state machine specified in
``examples/plan/Logic.md``: every facility (hospital, fire station, ...)
tracks independent power and water reserve buffers, decays/refills them at a
configurable rate depending on live connectivity, and transitions between
``Operational`` / ``Depleting`` / ``Dead`` / ``Rebooting`` according to the
Standard and Restart Viability guards (Logic.md §3) with a hysteresis
threshold to prevent restart thrashing (Logic.md §2).

This module is pure Python (no geopandas / networkx) — it only consumes
per-hour boolean connectivity/flood arrays and numeric config, and returns
per-hour state/buffer arrays. Resolving *which* facilities are connected to
what, and to what extent they're flooded, is the job of ``ncnn.py`` and
``flood_status.py``; turning the results into pixels is the job of
``flood_interpolation.py``'s HTML animation builder and the notebook that
calls all of the above.

Usage
-----
    from css_geodata_service.robustness_of_accessibility.utils.backup_lifetime import (
        compute_backup_lifetime,
    )

    backup = compute_backup_lifetime(
        poi_types=["hospital", "fire_station"],
        direct_flooded_by_stage=facility_flood_status,   # flood_status.compute_flood_status_by_stage
        dependency_status=dependency_status,              # flood_status.compute_dependency_status_by_stage
        stage_for_hour=stage_for_hour,                    # see build_stage_for_hour()
        backup_cfg=BACKUP_CFG,
        restart_threshold=0.15,
    )
    backup["hospital"][0]["state_by_hour"]  # state string per simulation hour, for the first hospital
"""
from __future__ import annotations

from typing import Dict, Iterable, List

# ---------------------------------------------------------------------------
# Single-facility simulation (Logic.md §3, §6, §7)
# ---------------------------------------------------------------------------

_STATES = ("Operational", "Depleting", "Dead", "Rebooting")


def simulate_backup_lifetime_for_poi(
    direct_flooded_by_hour: List[bool],
    resource_connected_by_hour: Dict[str, List[bool]],
    resources_cfg: Dict[str, Dict[str, float]],
    recharge_delay: int,
    restart_threshold: float = 0.15,
) -> Dict:
    """Run the Logic.md finite state machine for a single facility, hour by hour.

    Parameters
    ----------
    direct_flooded_by_hour : list of bool
        Whether the facility's *own* geometry is flooded at each hour
        (typically :func:`flood_status.compute_flood_status_by_stage`,
        expanded to hourly resolution — see :func:`expand_stage_bool_to_hour`).
        A directly flooded facility is forced ``Dead`` regardless of any
        backup reserve — no amount of generator fuel keeps a submerged
        building operational.
    resource_connected_by_hour : dict
        ``{resource_name: [bool, ...]}`` — one entry per resource this
        facility depends on (e.g. ``"power"``, ``"water"``), each a per-hour
        connectivity list of the same length as *direct_flooded_by_hour*.
    resources_cfg : dict
        ``{resource_name: {"capacity": float, "loss_rate": float, "gain_rate": float}}``.
        Must have the same keys as *resource_connected_by_hour*.
    recharge_delay : int
        Ticks required in ``Rebooting`` before returning to service
        (Logic.md §4, ``T_repair``).
    restart_threshold : float
        Fraction of capacity (Logic.md's 15% hysteresis guard) a
        disconnected resource's buffer must exceed to permit
        ``Dead`` → ``Rebooting`` (or to remain in ``Rebooting``).

    Returns
    -------
    dict
        * ``state_by_hour`` — list of state strings, one of :data:`_STATES`.
        * ``reboot_timer_by_hour`` — list of int, ``reboot_timer`` at each hour
          (0 outside ``Rebooting``).
        * ``buffer_by_hour`` — ``{resource_name: [float, ...]}``.
        * ``connected_by_hour`` — pass-through of *resource_connected_by_hour*,
          included so downstream rendering can show raw connectivity
          alongside the derived buffer/state without recomputing it.
        * ``flooded_by_hour`` — pass-through of *direct_flooded_by_hour*,
          included so downstream rendering can explain *why* a facility is
          `Dead` even with healthy buffers (the facility's own site is
          flooded, which overrides everything else — see the ``if flooded``
          check above) rather than reporting no reason at all.
    """
    n_hours = len(direct_flooded_by_hour)
    buffers = {r: cfg["capacity"] for r, cfg in resources_cfg.items()}
    state = "Operational"
    reboot_timer = 0

    state_by_hour: List[str] = []
    reboot_timer_by_hour: List[int] = []
    buffer_by_hour: Dict[str, List[float]] = {r: [] for r in resources_cfg}

    for h in range(n_hours):
        flooded = direct_flooded_by_hour[h]
        connected = {r: resource_connected_by_hour[r][h] for r in resources_cfg}

        if state in ("Dead", "Rebooting"):
            pass  # buffers frozen — Logic.md §3
        else:
            for r, rc in resources_cfg.items():
                if connected[r]:
                    buffers[r] = min(rc["capacity"], buffers[r] + rc["gain_rate"])
                else:
                    buffers[r] = max(0.0, buffers[r] - rc["loss_rate"])

        if flooded:
            state = "Dead"
            reboot_timer = 0
        elif state == "Operational":
            state = "Operational" if all(connected.values()) else "Depleting"
            reboot_timer = 0
        elif state == "Depleting":
            standard_ok = all(connected[r] or buffers[r] > 0 for r in resources_cfg)
            if not standard_ok:
                state = "Dead"
            elif all(connected.values()):
                state = "Operational"
            else:
                state = "Depleting"
            reboot_timer = 0
        elif state == "Dead":
            restart_ok = all(
                connected[r] or buffers[r] > restart_threshold * resources_cfg[r]["capacity"]
                for r in resources_cfg
            )
            if restart_ok:
                state = "Rebooting"
                reboot_timer = 1
            else:
                reboot_timer = 0
        elif state == "Rebooting":
            restart_ok = all(
                connected[r] or buffers[r] > restart_threshold * resources_cfg[r]["capacity"]
                for r in resources_cfg
            )
            if not restart_ok:
                state = "Dead"
                reboot_timer = 0
            else:
                reboot_timer += 1
                if reboot_timer >= recharge_delay:
                    state = "Operational" if all(connected.values()) else "Depleting"
                    reboot_timer = 0

        state_by_hour.append(state)
        reboot_timer_by_hour.append(reboot_timer)
        for r in resources_cfg:
            buffer_by_hour[r].append(buffers[r])

    return {
        "state_by_hour": state_by_hour,
        "reboot_timer_by_hour": reboot_timer_by_hour,
        "buffer_by_hour": buffer_by_hour,
        "connected_by_hour": resource_connected_by_hour,
        "flooded_by_hour": direct_flooded_by_hour,
    }


# ---------------------------------------------------------------------------
# Stage → hour expansion
# ---------------------------------------------------------------------------

def expand_stage_bool_to_hour(
    values_by_stage: List[List[bool]],
    row_index: int,
    stage_for_hour: List[int],
) -> List[bool]:
    """Expand one row of a per-stage boolean table into a per-hour list.

    ``values_by_stage`` is shaped ``[stage][row]`` (as produced by
    :mod:`flood_status`); this returns ``[values_by_stage[stage_for_hour[h]][row_index]
    for h in range(len(stage_for_hour))]``.
    """
    return [bool(values_by_stage[stage_for_hour[h]][row_index]) for h in range(len(stage_for_hour))]


# ---------------------------------------------------------------------------
# Multi-facility orchestration
# ---------------------------------------------------------------------------

def compute_backup_lifetime(
    poi_types: Iterable[str],
    direct_flooded_by_stage: Dict[str, List[List[bool]]],
    dependency_status: Dict[str, Dict],
    stage_for_hour: List[int],
    backup_cfg: Dict[str, Dict],
    restart_threshold: float = 0.15,
) -> Dict[str, List[Dict]]:
    """Run :func:`simulate_backup_lifetime_for_poi` for every facility of every
    requested type, expanding the existing per-stage flood/connectivity data
    to hourly resolution along the way.

    Parameters
    ----------
    poi_types : iterable of str
        Facility types to simulate, e.g. ``["hospital", "fire_station"]``.
        Must be keys of both *direct_flooded_by_stage* and *dependency_status*.
    direct_flooded_by_stage : dict
        Output of :func:`flood_status.compute_flood_status_by_stage`, covering
        (at least) *poi_types*.
    dependency_status : dict
        Output of :func:`flood_status.compute_dependency_status_by_stage` —
        supplies, per POI type, the NCNN connection→infrastructure mapping
        and that connection's own per-stage dead status.
    stage_for_hour : list of int
        Maps each simulation hour to a stage index — see
        :func:`css_geodata_service.robustness_of_accessibility.utils.flood_interpolation.build_stage_for_hour`.
    backup_cfg : dict
        ``{poi_type: {"recharge_delay": int, "resources": {resource_name:
        {"capacity": float, "loss_rate": float, "gain_rate": float}}}}``.
    restart_threshold : float
        Shared hysteresis guard fraction (Logic.md §4), applied to every
        facility/resource.

    Returns
    -------
    dict
        ``{poi_type: [simulate_backup_lifetime_for_poi(...) result, ...]}``,
        one entry per row of the POI GeoDataFrame the NCNN connections were
        built from (same row order).
    """
    results: Dict[str, List[Dict]] = {}

    for poi_type in poi_types:
        cfg = backup_cfg[poi_type]
        conn_info = dependency_status[poi_type]["connections"]
        flooded_by_stage = direct_flooded_by_stage[poi_type]
        n_pois = len(flooded_by_stage[0]) if flooded_by_stage else 0

        poi_results: List[Dict] = []
        for row_i in range(n_pois):
            direct_flooded_by_hour = expand_stage_bool_to_hour(
                flooded_by_stage, row_i, stage_for_hour
            )
            resource_connected_by_hour = {
                infra_type: [
                    not dead
                    for dead in expand_stage_bool_to_hour(
                        info["dead_by_stage"], row_i, stage_for_hour
                    )
                ]
                for infra_type, info in conn_info.items()
            }
            poi_results.append(
                simulate_backup_lifetime_for_poi(
                    direct_flooded_by_hour=direct_flooded_by_hour,
                    resource_connected_by_hour=resource_connected_by_hour,
                    resources_cfg=cfg["resources"],
                    recharge_delay=cfg["recharge_delay"],
                    restart_threshold=restart_threshold,
                )
            )
        results[poi_type] = poi_results

    return results
