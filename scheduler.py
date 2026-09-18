"""
GridWise 24-Hour Energy Scheduler
=================================

Solves a linear program that, given hourly demand, solar generation, and a
time-of-use tariff, decides how much to draw from the grid, how much solar
to consume, and how much to charge/discharge the battery each hour.

Returns a 24-entry schedule that minimizes total grid cost while respecting
battery dynamics and any operator directives.

Solver: PuLP with the bundled CBC backend.
"""

from __future__ import annotations

from typing import Any

import pulp

HOURS = range(24)


# --------------------------------------------------------------------------- #
# Directive application
# --------------------------------------------------------------------------- #
def _apply_directives(
    directives: list[dict[str, Any]],
    effective_solar: list[float],
    battery_vars: dict[str, list[pulp.LpVariable]],
    prob: pulp.LpProblem,
) -> None:
    """Mutate constraints in place based on operator directives."""
    for d in directives:
        dtype = d.get("type")
        adj = d.get("structured_adjustment", {}) or {}

        if dtype == "solar_reduction":
            factor = float(adj.get("factor", 1.0))
            # FIX: Only apply to specified hours, and add a hard constraint
            # because the PuLP variable's upBound was already set at creation.
            for h in adj.get("hours", []):
                reduced_limit = effective_solar[h] * factor
                prob += (
                    battery_vars["solar"][h] <= reduced_limit,
                    f"solar_reduction_{h}",
                )

        elif dtype == "minimum_battery_reserve":
            # FIX: Match the exact schema key from the Problem Statement
            min_energy = float(adj.get("minimum_energy_kwh"))
            for h in adj.get("hours", HOURS):
                prob += (
                    battery_vars["energy_after"][h] >= min_energy,
                    f"min_reserve_{h}",
                )

        elif dtype == "no_charge_window":
            for h in adj.get("hours", HOURS):
                prob += (
                    battery_vars["charge"][h] == 0,
                    f"no_charge_{h}",
                )

        elif dtype == "no_discharge_window":
            for h in adj.get("hours", HOURS):
                prob += (
                    battery_vars["discharge"][h] == 0,
                    f"no_discharge_{h}",
                )

        elif dtype == "max_grid_window":
            cap = float(adj.get("max_grid_kwh"))
            for h in adj.get("hours", HOURS):
                prob += (
                    battery_vars["grid"][h] <= cap,
                    f"max_grid_{h}",
                )

        elif dtype == "no_op":
            continue
        # Unknown directive types are intentionally ignored.


# --------------------------------------------------------------------------- #
# Main solver
# --------------------------------------------------------------------------- #
def solve(hours: list[dict[str, Any]], battery: dict[str, Any], directives: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Build and solve the 24-hour scheduling LP.

    Parameters
    ----------
    hours : list of 24 dicts
        Each entry has ``demand_kwh``, ``solar_kwh``, ``tariff_bdt_per_kwh``.
    battery : dict
        ``capacity_kwh``, ``initial_energy_kwh``, ``minimum_energy_kwh``,
        ``max_charge_kwh_per_hour``, ``max_discharge_kwh_per_hour``.
    directives : list of dict, optional
        Operator overrides. See module docstring / directive spec.

    Returns
    -------
    list of 24 dicts with the schema described in the task brief.
    """
    directives = directives or []

    # --- Effective solar (mutable; directives can scale it) ---------------- #
    effective_solar = [float(h["solar_kwh"]) for h in hours]

    # --- Model ------------------------------------------------------------- #
    prob = pulp.LpProblem("gridwise_24h", pulp.LpMinimize)

    grid = [
        pulp.LpVariable(f"grid_{h}", lowBound=0)
        for h in HOURS
    ]
    solar_used = [
        pulp.LpVariable(f"solar_{h}", lowBound=0, upBound=effective_solar[h])
        for h in HOURS
    ]
    charge = [
        pulp.LpVariable(
            f"charge_{h}",
            lowBound=0,
            upBound=battery["max_charge_kwh_per_hour"],
        )
        for h in HOURS
    ]
    discharge = [
        pulp.LpVariable(
            f"discharge_{h}",
            lowBound=0,
            upBound=battery["max_discharge_kwh_per_hour"],
        )
        for h in HOURS
    ]
    energy_after = [
        pulp.LpVariable(
            f"energy_after_{h}",
            lowBound=battery["minimum_energy_kwh"],
            upBound=battery["capacity_kwh"],
        )
        for h in HOURS
    ]

    vars_ = {
        "grid": grid,
        "solar": solar_used,
        "charge": charge,
        "discharge": discharge,
        "energy_after": energy_after,
    }

    # --- Base constraints -------------------------------------------------- #
    for h in HOURS:
        # Energy balance: sources == sinks
        prob += (
            grid[h] + solar_used[h] + discharge[h]
            == hours[h]["demand_kwh"] + charge[h],
            f"balance_{h}",
        )

        # Battery state transition
        if h == 0:
            prob += (
                energy_after[0]
                == battery["initial_energy_kwh"] + charge[0] - discharge[0],
                "soc_transition_0",
            )
        else:
            prob += (
                energy_after[h]
                == energy_after[h - 1] + charge[h] - discharge[h],
                f"soc_transition_{h}",
            )

    # End-of-day neutrality
    prob += (
        energy_after[23] == battery["initial_energy_kwh"],
        "soc_neutrality",
    )

    # --- Directive modifiers ---------------------------------------------- #
    _apply_directives(directives, effective_solar, vars_, prob)

    # --- Objective --------------------------------------------------------- #
    prob += pulp.lpSum(
        grid[h] * hours[h]["tariff_bdt_per_kwh"] for h in HOURS
    ), "total_grid_cost"

    # --- Solve ------------------------------------------------------------- #
    prob.solve(pulp.PULP_CBC_CMD(msg=False))

    if pulp.value(prob.objective) is None or prob.status != pulp.constants.LpStatusOptimal:
        raise RuntimeError(
            f"Solver did not reach optimality (status={pulp.LpStatus[prob.status]})."
        )

    # --- Format output ----------------------------------------------------- #
    schedule: list[dict[str, Any]] = []
    eps = 1e-6  # threshold below which a flow is treated as idle
    for h in HOURS:
        c = max(0.0, pulp.value(charge[h]) or 0.0)
        d = max(0.0, pulp.value(discharge[h]) or 0.0)

        if c > eps and d > eps:
            # LP shouldn't allow simultaneous charge+discharge unless both
            # are zero in practice; treat as idle if magnitudes are tiny.
            action, magnitude = "idle", 0.0
        elif c > eps:
            action, magnitude = "charge", round(c, 6)
        elif d > eps:
            action, magnitude = "discharge", round(d, 6)
        else:
            action, magnitude = "idle", 0.0

        schedule.append(
            {
                "hour": h,
                "grid_kwh": round(pulp.value(grid[h]) or 0.0, 6),
                "solar_used_kwh": round(pulp.value(solar_used[h]) or 0.0, 6),
                "battery_action": action,
                "battery_kwh": magnitude,
                "battery_energy_after_kwh": round(
                    pulp.value(energy_after[h]) or 0.0, 6
                ),
            }
        )

    return schedule


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # Demand pattern: low overnight, climbing to an evening peak.
    demand = [0.8, 0.7, 0.7, 0.7, 0.8, 1.0,
              1.5, 2.0, 2.2, 2.0, 1.8, 1.9,
              2.1, 2.0, 1.9, 2.0, 2.4, 3.0,
              3.4, 3.2, 2.6, 2.0, 1.4, 1.0]

    # Solar: zero overnight, bell curve peaking at noon.
    solar = [0.0, 0.0, 0.0, 0.0, 0.0, 0.1,
             0.4, 0.9, 1.5, 2.1, 2.6, 2.9,
             3.0, 2.8, 2.3, 1.6, 0.8, 0.2,
             0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

    # Tariff: cheap off-peak (0-8, 22-23), expensive evening peak (17-21).
    tariff = [5.0] * 9 + [12.0] * 5 + [5.0] * 2 + [12.0] * 5 + [5.0] * 3
    tariff = tariff[:24]

    hours = [
        {"demand_kwh": d, "solar_kwh": s, "tariff_bdt_per_kwh": t}
        for d, s, t in zip(demand, solar, tariff)
    ]

    battery = {
        "capacity_kwh": 10.0,
        "initial_energy_kwh": 5.0,
        "minimum_energy_kwh": 1.0,
        "max_charge_kwh_per_hour": 3.0,
        "max_discharge_kwh_per_hour": 3.0,
    }

    directives = [
        # Cloudy afternoon: only 60% of forecasted solar available.
        {"type": "solar_reduction",
         "structured_adjustment": {"factor": 0.6,
                                   "hours": list(range(12, 17))}},
        # Hold a 4 kWh reserve through the morning peak.
        {"type": "minimum_battery_reserve",
         "structured_adjustment": {"minimum_energy_kwh": 4.0, # FIXED KEY
                                   "hours": list(range(8, 12))}},
        # No charging during the most expensive peak window.
        {"type": "no_charge_window",
         "structured_adjustment": {"hours": list(range(18, 22))}},
        # Cap grid import during the evening peak to 1.5 kWh.
        {"type": "max_grid_window",
         "structured_adjustment": {"max_grid_kwh": 1.5,
                                   "hours": list(range(17, 22))}},
    ]

    schedule = solve(hours, battery, directives)

    total_cost = sum(
        s["grid_kwh"] * hours[s["hour"]]["tariff_bdt_per_kwh"]
        for s in schedule
    )

    print(f"{'h':>2}  {'grid':>6}  {'solar':>6}  {'action':>9}  "
          f"{'kwh':>6}  {'soc':>6}")
    print("-" * 48)
    for s in schedule:
        print(f"{s['hour']:>2}  {s['grid_kwh']:>6.3f}  "
              f"{s['solar_used_kwh']:>6.3f}  "
              f"{s['battery_action']:>9}  "
              f"{s['battery_kwh']:>6.3f}  "
              f"{s['battery_energy_after_kwh']:>6.3f}")

    print(f"\nTotal grid cost: {total_cost:.2f} BDT")
    print(f"Final SOC: {schedule[-1]['battery_energy_after_kwh']:.3f} kWh "
          f"(started at {battery['initial_energy_kwh']:.1f})")
