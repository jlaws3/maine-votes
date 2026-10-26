
#!/usr/bin/env python3
"""Safely rebuild Maine Votes 2026 aggregate absentee data.

The source report contains voter identifiers and must NEVER be committed.
Only aggregate observations and aggregate audit statistics are published.
"""

import argparse
import collections
import csv
import datetime as dt
import json
import pathlib

YEAR = 2026
ACCEPT = {"ACT"}

FIELDS = [
    "RES MUNICIPALITY",
    "Voter Record #",
    "P",
    "SS",
    "SR",
    "CG",
    "Req Date",
    "Rec Date",
    "Rec Type",
    "Status",
    "DUP",
]


def parse_date(value):
    if not value:
        return ""
    try:
        return dt.datetime.strptime(
            value.strip(), "%m/%d/%Y"
        ).date().isoformat()
    except ValueError:
        return ""


def build(source):
    voters = collections.defaultdict(list)
    statuses = collections.Counter()
    source_rows = 0
    skipped = 0

    with open(
        source,
        encoding="utf-8-sig",
        errors="replace",
        newline=""
    ) as file:
        reader = csv.DictReader(file, delimiter="|")

        missing = set(FIELDS) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(
                f"Missing source columns: {sorted(missing)}"
            )

        for row in reader:
            voter_id = (row.get("Voter Record #") or "").strip()
            if not voter_id:
                continue

            source_rows += 1

            if not (row.get("RES MUNICIPALITY") or "").strip():
                skipped += 1
                continue

            status = (row.get("Status") or "").strip().upper()
            statuses[status] += 1
            voters[voter_id].append(row)

    observations = collections.Counter()
    accepted_unique = 0

    for rows in voters.values():

        def priority(row):
            status = (row.get("Status") or "").strip().upper()
            duplicate = (row.get("DUP") or "").strip()

            return (
                status in ACCEPT,
                int(duplicate) if duplicate.isdigit() else 0
            )

        selected = max(rows, key=priority)

        town = (
            selected.get("RES MUNICIPALITY") or ""
        ).strip().upper()

        party = (selected.get("P") or "").strip().upper()
        ss = (selected.get("SS") or "").strip()
        sr = (selected.get("SR") or "").strip()
        cg = (selected.get("CG") or "").strip()

        request_dates = [
            parsed
            for row in rows
            if (parsed := parse_date(row.get("Req Date", "")))
        ]

        request_date = min(request_dates, default="")

        if request_date:
            observations[
                (YEAR, town, party, ss, sr, cg,
                 "requested", request_date, "")
            ] += 1

        status = (
            selected.get("Status") or ""
        ).strip().upper()

        received_date = parse_date(
            selected.get("Rec Date", "")
        )

        if status in ACCEPT and received_date:
            accepted_unique += 1

            received_type = (
                selected.get("Rec Type") or ""
            ).strip().upper()

            observations[
                (YEAR, town, party, ss, sr, cg,
                 "returned", received_date, received_type)
            ] += 1

    rows = [
        list(key) + [count]
        for key, count in sorted(observations.items())
    ]

    audit = {
        "source_rows": source_rows,
        "unique_voters": len(voters),
        "skipped_rows": skipped,
        "statuses": dict(statuses),
        "accepted_unique": accepted_unique,
    }

    return rows, audit


def totals(rows):
    return {
        "requested": sum(
            row[-1] for row in rows
            if row[6] == "requested"
        ),
        "returned": sum(
            row[-1] for row in rows
            if row[6] == "returned"
        ),
    }


def latest_date(rows):
    dates = [
        row[7]
        for row in rows
        if row[6] in ("requested", "returned")
        and row[7]
    ]
    return max(dates, default="")


def validate(existing_rows, new_rows, audit):
    old = totals(existing_rows)
    new = totals(new_rows)

    print("Existing totals:", old)
    print("New totals:", new)
    print("Existing latest activity:", latest_date(existing_rows))
    print("New latest activity:", latest_date(new_rows))
    print("Source audit:", audit)

    if audit["unique_voters"] < 100000:
        raise ValueError(
            "Source contains unexpectedly few voters."
        )

    if audit["accepted_unique"] < 100000:
        raise ValueError(
            "Source contains unexpectedly few accepted ballots."
        )

    if audit["skipped_rows"] > audit["source_rows"] * 0.01:
        raise ValueError(
            "Too many source rows lack a municipality."
        )

    if not new_rows:
        raise ValueError("No aggregate observations generated.")

    if new["requested"] < new["returned"]:
        raise ValueError(
            "Returned ballots exceed requested ballots."
        )

    for row in new_rows:
        if len(row) != 10:
            raise ValueError(
                "Unexpected aggregate observation structure."
            )

        if row[0] != YEAR:
            raise ValueError("Unexpected election year.")

        if row[-1] <= 0:
            raise ValueError("Invalid aggregate count.")

        if row[7]:
            observation_date = dt.date.fromisoformat(row[7])

            if not (
                dt.date(2020, 1, 1)
                <= observation_date
                <= dt.date(YEAR, 12, 31)
            ):
                raise ValueError(
                    f"Observation date {observation_date} "
                    f"is outside the permitted election "
                    f"reporting period."
                )

    if existing_rows:
        old_latest = latest_date(existing_rows)
        new_latest = latest_date(new_rows)

        if new_latest < old_latest:
            raise ValueError(
                "Downloaded report appears older than "
                "the currently published data."
            )

        # Permit small official corrections, but not a
        # substantial unexplained decline in totals.
        for metric in ("requested", "returned"):
            if new[metric] < old[metric] * 0.99:
                raise ValueError(
                    f"{metric} declined by more than 1%. "
                    "Review the source before publication."
                )


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument("--source", required=True)
    parser.add_argument("--data", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--check-only", action="store_true")

    args = parser.parse_args()

    with open(args.data, encoding="utf-8") as file:
        existing = json.load(file)

    if not isinstance(existing.get("observations"), list):
        raise ValueError(
            "Existing data.json has no observations list."
        )

    new_rows, audit = build(args.source)

    old_rows = [
        row for row in existing["observations"]
        if row[0] == YEAR
    ]

    historical_dates = [
        row for row in new_rows
        if row[7] and row[7] < "2026-01-01"
    ]

    print("Pre-2026 aggregate observations:", len(historical_dates))
    print(
        "Pre-2026 requested ballots:",
        sum(r[-1] for r in historical_dates if r[6] == "requested")
    )
    print(
        "Pre-2026 returned ballots:",
        sum(r[-1] for r in historical_dates if r[6] == "returned")
    )
    print(
        "Earliest observation date:",
        min((r[7] for r in new_rows if r[7]), default="none")
    )
    print("Historical request dates by year:")
    by_year = collections.Counter()

    for row in new_rows:
        if row[6] == "requested" and row[7]:
            year = row[7][:4]
            if year < "2026":
                by_year[year] += row[-1]

    for year, count in sorted(by_year.items()):
        print(f"  {year}: {count:,} requests")
            print("Pre-2026 requests by period:")

    for start, end, label in [
        ("1900", "2024", "Before 2024"),
        ("2024", "2025", "2024"),
        ("2025", "2026", "2025"),
    ]:
        count = sum(
            row[-1]
            for row in new_rows
            if row[6] == "requested"
            and start <= row[7][:4] < end
        )
        print(f"{label}: {count:,}")
    validate(old_rows, new_rows, audit)

    old_counts = {
        tuple(row[:-1]): row[-1]
        for row in old_rows
    }

    new_counts = {
        tuple(row[:-1]): row[-1]
        for row in new_rows
    }

    changes = sum(
        old_counts.get(key) != new_counts.get(key)
        for key in old_counts.keys() | new_counts.keys()
    )

    print("Differing aggregate keys:", changes)

    if old_counts == new_counts:
        print("No new aggregate data. Nothing to publish.")
        return

    if args.check_only:
        print(
            "Validation successful. "
            "Changes detected; publication disabled."
        )
        return

    # Preserve historical observations exactly.
    historical = [
        row for row in existing["observations"]
        if row[0] != YEAR
    ]

    existing["observations"] = historical + new_rows

    if not isinstance(existing.get("audit"), dict):
        existing["audit"] = {}

    existing["audit"][str(YEAR)] = audit

    output = pathlib.Path(args.output)

    # Never write to the downloaded source report.
    if output.resolve() == pathlib.Path(args.source).resolve():
        raise ValueError(
            "Output must not overwrite the raw source."
        )

    output.write_text(
        json.dumps(existing, separators=(",", ":")),
        encoding="utf-8"
    )

    print("Updated aggregate data:", output)
    print("Historical observations preserved:", len(historical))
    print("New 2026 observations:", len(new_rows))


if __name__ == "__main__":
    main()
