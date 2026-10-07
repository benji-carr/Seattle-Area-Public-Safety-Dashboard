"""Download and verify crime release observations for local analysis."""

import argparse

from dashboard.crime_observation_archive import GitHubArchive, sync_observations
from dashboard.crime_observations import OBSERVATIONS_DIR


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="OWNER/REPO")
    parser.add_argument("--root", default=str(OBSERVATIONS_DIR))
    parser.add_argument("--month", help="Optional UTC collection month YYYY-MM; otherwise all archive releases")
    args = parser.parse_args(argv)
    for directory in sync_observations(GitHubArchive(args.repo), args.root, args.month):
        print(directory)


if __name__ == "__main__":
    main()
