"""Record endpoint timings and summarize one or more CSV logs offline."""

import argparse
import csv
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

FIELDS = ("timestamp_utc", "side", "peer", "operation", "path", "bytes",
          "transfer_seconds", "response_ms", "total_seconds", "mb_per_second",
          "outcome")


class Metrics:
    def __init__(self, path, side):
        """Create a CSV log for metrics recorded by the given endpoint side."""
        self.path = Path(path)
        self.side = side
        self.lock = Lock()
        if not self.path.exists() or self.path.stat().st_size == 0:
            with self.path.open("w", newline="", encoding="utf-8") as output:
                csv.DictWriter(output, fieldnames=FIELDS).writeheader()

    def record(self, peer, operation, path, *, outcome, total,
               response=None, size=None, transfer=None):
        """Append timing, transfer, and outcome details for one operation."""
        rate = size / 1_000_000 / transfer if size is not None and transfer else None
        with self.lock, self.path.open("a", newline="", encoding="utf-8") as output:
            csv.writer(output).writerow((
                datetime.now(timezone.utc).isoformat(), self.side, peer,
                operation, path, size,
                None if transfer is None else round(transfer, 6),
                None if response is None else round(response * 1000, 3),
                round(total, 6), None if rate is None else round(rate, 3),
                outcome
            ))


def main():
    """Print grouped outcome counts and average timings for CSV metric logs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logs", nargs="+", type=Path)
    args = parser.parse_args()
    groups = {}
    for path in args.logs:
        with path.open(newline="", encoding="utf-8") as log:
            for row in csv.DictReader(log):
                groups.setdefault((row["side"], row["operation"]), []).append(row)

    def average(rows, field):
        """Format the average populated value for a metrics field."""
        values = [float(row[field]) for row in rows if row[field]]
        return f"{sum(values) / len(values):.3f}" if values else "-"

    print(f"{'Side':<8} {'Operation':<12} {'OK':>5} {'Error':>5} {'Cancel':>6}"
          f" {'Resp ms':>10} {'Total s':>10} {'Data s':>10} {'MB/s':>10}")
    for (side, operation), rows in sorted(groups.items()):
        ok = [row for row in rows if row["outcome"] == "ok"]
        errors = sum(row["outcome"] == "error" for row in rows)
        cancelled = sum(row["outcome"] == "cancelled" for row in rows)
        print(f"{side:<8} {operation:<12} {len(ok):>5} {errors:>5} {cancelled:>6}"
              f" {average(ok, 'response_ms'):>10} {average(ok, 'total_seconds'):>10}"
              f" {average(ok, 'transfer_seconds'):>10} {average(ok, 'mb_per_second'):>10}")


if __name__ == "__main__":
    main()
