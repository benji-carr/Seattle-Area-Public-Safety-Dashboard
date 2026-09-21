"""Adapt the validated production population snapshot for dashboard consumers."""

from typing import Any

import pandas as pd

from dashboard.population_snapshot import load_population_snapshot


def load_dashboard_population() -> tuple[pd.DataFrame, float, dict[str, Any]]:
    """Return MCPP populations, the direct Seattle population, and metadata."""
    population, metadata = load_population_snapshot()
    neighborhoods = population.loc[population["geography_type"] == "mcpp"].copy()
    neighborhoods["mcpp_neighborhood"] = (
        neighborhoods["geography_name"]
        .astype("string")
        .str.strip()
        .str.lower()
        .str.replace("&", "and", regex=False)
        .str.replace(r"\s+", " ", regex=True)
    )
    neighborhoods["dispatch_neighborhood"] = neighborhoods["mcpp_neighborhood"]
    neighborhoods["population"] = pd.to_numeric(neighborhoods["population"])
    city_population = float(population.loc[
        (population["geography_type"] == "city")
        & (population["geography_name"] == "seattle"),
        "population",
    ].iloc[0])
    return neighborhoods.reset_index(drop=True), city_population, metadata
