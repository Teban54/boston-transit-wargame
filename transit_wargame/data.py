"""Canonical m1.4 topology and strict CSV ingestion (handoff §6–7)."""

import csv
from math import asin, cos, isfinite, radians, sin, sqrt
from pathlib import Path
from typing import Mapping

from .domain import Corridor, Demand, Node, Scenario, edge_key

EARTH_RADIUS_KM = 6371.0088
DEFAULT_CSV = Path(__file__).resolve().parent.parent / "data" / "temp_neighborhood_to_named_workplace_matrix_JT00_main.csv"

DESTINATIONS = {
    "Downtown": "Downtown", "Kendall": "Kendall-MIT", "Longwood": "LMA-Fenway",
    "Seaport": "Seaport", "Back Bay": "Back Bay",
}
ORIGINS = {
    "Cambridge/Harvard": ("Cambridge",), "Somerville": ("Somerville",),
    "Everett/Chelsea": ("Everett", "Chelsea"),
    "Allston/Brookline": ("Allston", "Brookline"), "Jamaica Plain": ("Jamaica Plain",),
    "Roslindale/West Roxbury": ("Roslindale", "West Roxbury"),
    "Roxbury": ("Roxbury", "Dorchester North"),
    "Dorchester/Mattapan": ("Dorchester South", "Mattapan"),
    "East Boston/Airport": ("East Boston",),
}

NODES = (
    Node("Downtown", 42.35549350026779, -71.06045567567168, True),
    Node("Kendall", 42.36240498040719, -71.08560190451469, True),
    Node("Longwood", 42.33896665415227, -71.10711654438173, True),
    Node("Seaport", 42.35177280205964, -71.04560814781017, True),
    Node("Back Bay", 42.349141982873384, -71.07667807891963, True),
    Node("Cambridge/Harvard", 42.37327843938996, -71.11906722856277),
    Node("Somerville", 42.38416200735421, -71.07733866792677),
    Node("Everett/Chelsea", 42.40021609854016, -71.04557798314266),
    Node("Allston/Brookline", 42.350149539940915, -71.13094617286852),
    Node("Jamaica Plain", 42.30182017123665, -71.1133384571433),
    Node("Roslindale/West Roxbury", 42.28764476488768, -71.12989236272067),
    Node("Roxbury", 42.317916714972135, -71.08012229244507),
    Node("Dorchester/Mattapan", 42.284917072012945, -71.063932739539),
    Node("East Boston/Airport", 42.3743860257306, -71.03059269053398),
)


def edges(*pairs: tuple[str, str]):
    return frozenset(edge_key(*pair) for pair in pairs)


INITIAL_RAIL = edges(
    ("Cambridge/Harvard", "Kendall"), ("Kendall", "Downtown"),
    ("Downtown", "Dorchester/Mattapan"), ("Somerville", "Downtown"),
    ("Downtown", "Back Bay"), ("Back Bay", "Jamaica Plain"),
    ("Back Bay", "Allston/Brookline"), ("Back Bay", "Longwood"),
    ("Downtown", "East Boston/Airport"),
)
INITIAL_BUS = edges(
    ("Downtown", "Seaport"), ("Roxbury", "Longwood"),
    ("Roxbury", "Dorchester/Mattapan"), ("Everett/Chelsea", "Downtown"),
    ("Roxbury", "Downtown"), ("Jamaica Plain", "Roslindale/West Roxbury"),
)
CANDIDATES = edges(
    ("Cambridge/Harvard", "Somerville"), ("Somerville", "Kendall"),
    ("Everett/Chelsea", "Somerville"), ("Cambridge/Harvard", "Allston/Brookline"),
    ("Allston/Brookline", "Longwood"), ("Cambridge/Harvard", "Back Bay"),
    ("Jamaica Plain", "Longwood"), ("East Boston/Airport", "Seaport"),
    ("Dorchester/Mattapan", "Jamaica Plain"), ("Roxbury", "Seaport"),
    ("Back Bay", "Seaport"), ("Kendall", "Back Bay"), ("Kendall", "Longwood"),
    ("Roxbury", "Back Bay"), ("East Boston/Airport", "Everett/Chelsea"),
    ("Cambridge/Harvard", "Longwood"), ("Allston/Brookline", "Kendall"),
)
EXCLUDED = edges(
    ("Roslindale/West Roxbury", "Dorchester/Mattapan"), ("Everett/Chelsea", "Kendall"),
)


def distance_km(a: Node, b: Node) -> float:
    """Great-circle endpoint distance; never calibrated travel minutes."""
    lat1, lat2 = radians(a.latitude), radians(b.latitude)
    dlat, dlon = lat2 - lat1, radians(b.longitude - a.longitude)
    h = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return EARTH_RADIUS_KM * 2 * asin(sqrt(min(1.0, max(0.0, h))))


def load_od_csv(
    path: Path | str,
    origins: Mapping[str, tuple[str, ...]] = ORIGINS,
    destinations: Mapping[str, str] = DESTINATIONS,
) -> tuple[Demand, ...]:
    """Sum only the specified origin columns; never infer absent demand."""
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = reader.fieldnames or []
        if not columns or columns[0] != "workplace_name":
            raise ValueError("CSV first column must be workplace_name.")
        if len(columns) != len(set(columns)):
            raise ValueError("CSV has duplicate column names.")
        required = {column for source in origins.values() for column in source}
        missing = required - set(columns)
        if missing:
            raise ValueError(f"CSV missing required origin columns: {', '.join(sorted(missing))}")
        rows = {}
        for row in reader:
            name = row["workplace_name"]
            if None in row or not name:
                raise ValueError("Malformed CSV row or missing workplace_name.")
            if name in rows:
                raise ValueError(f"Duplicate workplace row: {name}")
            rows[name] = row
    missing_rows = set(destinations.values()) - rows.keys()
    if missing_rows:
        raise ValueError(f"CSV missing required destination rows: {', '.join(sorted(missing_rows))}")
    demands = []
    for origin, source_columns in sorted(origins.items()):
        for destination, source_row in sorted(destinations.items()):
            values = []
            for column in source_columns:
                raw = rows[source_row].get(column)
                try:
                    count = float(raw)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"Invalid commuting count at {source_row} / {column}: {raw!r}") from exc
                if not isfinite(count) or count < 0:
                    raise ValueError(f"Commuting count must be finite and nonnegative at {source_row} / {column}.")
                values.append(count)
            demands.append(Demand(origin, destination, sum(values)))
    return tuple(demands)


def load_boston_scenario(csv_path: Path | str = DEFAULT_CSV) -> Scenario:
    lookup = {node.name: node for node in NODES}
    corridors = tuple(
        Corridor(
            edge, distance_km(lookup[edge[0]], lookup[edge[1]]),
            buildable=edge in CANDIDATES or edge in INITIAL_BUS,
            bus_allowed=edge in CANDIDATES,
            rail_allowed=edge in CANDIDATES or edge in INITIAL_BUS,
            initial_bus=edge in INITIAL_BUS, initial_rail=edge in INITIAL_RAIL,
        )
        for edge in sorted(INITIAL_RAIL | INITIAL_BUS | CANDIDATES)
    )
    return Scenario(NODES, corridors, load_od_csv(csv_path), EXCLUDED)
