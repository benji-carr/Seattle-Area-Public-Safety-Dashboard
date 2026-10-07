# Seattle Public Safety Dashboards

[View the live dashboard](https://spdcalldashboard.onrender.com)

This application is a collection of dashboards that aim to provide information on the most recent available data on violent, drug-related, and property-related crimes calls to SPD in the City of Seattle as well as the most recent records of crimes released by the City of Seattle. The data is from Seattle's Open Data Portal and can be found at [The Seattle Government Data Website](https://data.seattle.gov). Within this repository you can find the scripts for the dashboard, scripts for pulling data from the Socrata API that SPD uses, and the scripts for automatically updating the rolling snapshot of the last year of available data.  

## Contributing

Source collection lives in `dashboard/crime_source.py`, `dashboard/uof_source.py`
and `dashboard/spd_source.py`. Snapshot modules handle persistence; dashboard
contexts and `app.py` handle presentation. The daily calls refresh uses
`python -m scripts.dashboard.refresh_call_metrics` to reconcile the full retained
window into a compact metric snapshot. See the [architecture and module guide](docs/dashboard_architecture.md)
for refresh commands and responsibilities.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the Git workflow and contribution process.

## Roadmap

Development tasks and planned features are tracked in GitHub Issues. Github Issues labeled STAGING are issues regarding the v1.1 currently in development.
