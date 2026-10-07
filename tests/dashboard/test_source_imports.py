"""Source/schema import order must not pull in optional analysis stacks."""

import subprocess
import sys

import pytest


@pytest.mark.parametrize("first_module", [
    "dashboard.crime_source", "dashboard.uof_source", "dashboard.spd_source",
    "dashboard.uof_schemas", "dashboard.population_snapshot",
])
def test_collection_imports_without_analysis_dependencies(first_module):
    script = """
import importlib
import importlib.abc
import sys

blocked = (
    'duckdb', 'pmdarima', 'forecasting.backtests.sarima',
    'dashboard.crime_observation_analysis',
)

class BlockAnalysis(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + '.') for name in blocked):
            raise ModuleNotFoundError('Analysis dependency imported: ' + fullname)
        return None

sys.meta_path.insert(0, BlockAnalysis())
importlib.import_module(sys.argv[1])
import dashboard.crime_source
import dashboard.uof_source
import dashboard.spd_source
import dashboard.uof_schemas
import dashboard.population_snapshot
import dashboard.crime_dashboard_data
import dashboard.uof_dashboard_data
import dashboard.crime_call_support_data
import scripts.dashboard.refresh_crime_data
import scripts.dashboard.refresh_call_metrics
import scripts.dashboard.refresh_uof_data
import forecasting.production.data_refresh
assert not any(name in sys.modules for name in blocked)
"""
    subprocess.run(
        [sys.executable, "-c", script, first_module],
        check=True, capture_output=True, text=True,
    )
