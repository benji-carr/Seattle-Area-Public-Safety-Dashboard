"""Print distinct daily totals/revisions from locally verified crime observations."""

import argparse
import json
import sys

from dashboard.crime_observations import OBSERVATIONS_DIR


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(OBSERVATIONS_DIR))
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    args = parser.parse_args(argv)
    from dashboard.crime_observation_analysis import analyze_observations
    result = analyze_observations(args.root, args.start_date, args.end_date)
    print(json.dumps(result.attrs), file=sys.stderr)
    print(result.to_csv(index=False), end="")


if __name__ == "__main__":
    main()
