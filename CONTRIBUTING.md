# Contributing

Thanks for contributing to SPDCallDashboard.

## Git Workflow

The `main` branch is protected. Changes must be made on a separate branch and merged through a pull request.

Start by updating your local `main` branch:

```bash
git switch main
git pull
```

Create a new branch for your work:

```bash
git switch -c feature/short-description
```

Use a branch prefix that matches the type of work:

```text
feature/   New features
fix/       Bug fixes
docs/      Documentation changes
```

Examples:

```text
feature/dashboard-date-controls
fix/crime-map-filter
docs/dashboard-architecture
```

## Making Changes

Crime, UOF and calls ingestion belong in `dashboard/crime_source.py`,
`dashboard/uof_source.py` and `dashboard/spd_source.py`. Keep query construction,
HTTP access, record normalization and dataset assembly in their respective
sections. Snapshot persistence, UI, observations, classification and validation
models/schemas remain separate. `load_dashboard_population` is exported by
`dashboard.population_snapshot`.

Update callers and mock targets at the dependency's lookup location. Parse
notebooks as JSON and edit only relevant cell source, preserving IDs, metadata,
execution counts and historical outputs. Keep DuckDB observation analysis and
SARIMA dependencies out of source collection and dashboard startup imports.

Validate with `python -m pytest tests/dashboard tests/forecasting -q`,
`python -m pip check`, `git diff --check` and `git diff --cached --check`.
Use mocked HTTP and temporary snapshot directories for tests. Operational
refresh commands fetch live data and write snapshots; they are not offline
validation commands. See [the architecture guide](docs/dashboard_architecture.md).

After making and testing your changes:

```bash
git status
git add .
git commit -m "Short description of change"
```

Push the branch to GitHub:

```bash
git push -u origin <branch-name>
```

Direct pushes to `main` are blocked.

## Pull Requests

Open a pull request from your branch into `main`.

Before merging:

* Review the changed files.
* Make sure the application still runs correctly.
* Run relevant tests or smoke checks.
* Resolve any review comments.
* Make sure required GitHub checks pass.

Use **Squash and merge** or **Rebase and merge** to preserve the repository's required linear history.

## After Merging

Update your local copy of `main`:

```bash
git switch main
git pull
```

Then delete the completed local branch:

```bash
git branch -d <branch-name>
```

If the remote branch was not automatically deleted:

```bash
git push origin --delete <branch-name>
```

## Keep Changes Focused

Pull requests should focus on one feature, bug fix, or documentation change whenever practical. Avoid including unrelated changes in the same pull request.
