"""Upload verified crime bundles; never refresh source data."""

import argparse

from dashboard.crime_observation_archive import GitHubArchive, upload_observation
from dashboard.crime_observations import OBSERVATIONS_DIR, observation_directories


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="OWNER/REPO")
    parser.add_argument("--root", default=str(OBSERVATIONS_DIR))
    args = parser.parse_args(argv)
    archive = GitHubArchive(args.repo)
    for directory in observation_directories(args.root):
        print(f"{directory.name}: verified in {upload_observation(directory, archive)}")


if __name__ == "__main__":
    main()
