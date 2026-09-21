import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box


@pytest.fixture
def population_inputs():
    """One full block group straddles two MCPPs and the outside-city area."""
    x, y = 1_250_000, 220_000
    mcpp = gpd.GeoDataFrame(
        {"mcpp_neighborhood": [" West  &  Center ", "EAST"]},
        geometry=[box(x, y, x + 10, y + 10), box(x + 10, y, x + 20, y + 10)],
        crs="EPSG:2285",
    )
    decennial = pd.DataFrame({
        "state": ["53"] * 3, "county": ["033"] * 3, "tract": ["000100"] * 3,
        "block": ["1001", "1002", "1003"], "P1_001N": ["30", "60", "10"],
    })
    acs = pd.DataFrame({
        "state": ["53"], "county": ["033"], "tract": ["000100"],
        "block group": ["1"], "B01003_001E": ["1000"], "B01003_001M": ["50"],
    })
    geometry = gpd.GeoDataFrame(
        {"GEOID20": ["530330001001001", "530330001001002", "530330001001003"]},
        geometry=[box(x + 1, y + 1, x + 3, y + 3),
                  box(x + 9, y + 1, x + 15, y + 3),
                  box(x + 22, y + 1, x + 24, y + 3)],
        crs="EPSG:2285",
    )
    return acs, decennial, geometry, mcpp
