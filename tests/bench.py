#!/usr/bin/env python3
"""Measure what a listing costs against a realistically sized library.

    python3 tests/bench.py

The metric that matters is HTTP requests per listing, not wall clock. Kodi
holds the interface frozen until endOfDirectory returns, and this mock answers
on loopback in microseconds, so the timings here understate a real server by
however much round-trip latency it has. A listing that issues 40 requests
against a LAN Immich at 15ms is 600ms of frozen UI; the same listing here
looks free.

The 22-asset test fixture cannot show any of this, so the dataset is scaled to
a ten-year library before measuring.
"""

from __future__ import annotations

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
for path in (os.path.join(REPO, "resources", "lib"), HERE, os.path.join(HERE, "kodistubs")):
    if path in sys.path:
        sys.path.remove(path)
    sys.path.insert(0, path)

import harness  # noqa: E402
from mockimmich import asset_dto  # noqa: E402

# A ten-year library: 120 months, heavier in summer, ~40k assets.
MONTHS = 120
BIG_ALBUM = 3000
VIDEO_COUNT = 2000
LIBRARY_SAMPLE = 4000  # what the search endpoints draw from


def build_library(dataset):
    buckets, sizes = [], {}
    for index in range(MONTHS):
        year = 2026 - (index // 12)
        month = 12 - (index % 12)
        key = f"{year:04d}-{month:02d}-01"
        # Summer months carry more; this is what makes paging bite.
        count = 900 if month in (6, 7, 8) else 220
        buckets.append({"timeBucket": key, "count": count})
        sizes[key] = count
    dataset.buckets = buckets
    dataset.bucket_sizes = sizes

    album_id = dataset.albums[0]["id"]
    dataset.album_assets[album_id] = [
        asset_dto(i, is_video=(i % 40 == 0)) for i in range(BIG_ALBUM)
    ]
    dataset.albums[0]["assetCount"] = BIG_ALBUM

    dataset.search_results = [
        asset_dto(i, is_video=(i % 2 == 0)) for i in range(LIBRARY_SAMPLE)
    ]
    return album_id, sum(sizes.values())


def measure(bench, query, label):
    bench.server.reset()
    build_library(bench.dataset)
    start = time.perf_counter()
    record = bench.invoke(query)
    elapsed = time.perf_counter() - start

    requests = bench.server.requests
    paths = {}
    for entry in requests:
        paths[entry["path"]] = paths.get(entry["path"], 0) + 1
    return {
        "label": label,
        "requests": len(requests),
        "items": len(record.items),
        "seconds": elapsed,
        "paths": paths,
        "failed": record.exception is not None,
    }


def main():
    bench = harness.Harness()
    try:
        bench.reset()
        album_id, total = build_library(bench.dataset)
        print(f"library: {MONTHS} months, {total} assets, "
              f"one album of {BIG_ALBUM}\n")

        cases = [
            ("", "root menu"),
            ("action=timeline", "timeline (month list)"),
            ("action=bucket&id=2026-08-01", "one 900-asset month, page 1"),
            ("action=bucket&id=2026-08-01&page=1", "same month, page 2"),
            ("action=albums", "album list"),
            (f"action=album&id={album_id}", f"{BIG_ALBUM}-asset album, page 1"),
            (f"action=album&id={album_id}&page=1", "same album, page 2"),
            (f"action=album&id={album_id}&page=5", "same album, page 6"),
            ("action=videos", "videos, page 1"),
            ("action=videos&page=2", "videos, page 3"),
            ("action=random", "random"),
            ("action=places", "places"),
        ]

        print(f"{'listing':34s} {'reqs':>5s} {'items':>6s} {'ms':>7s}   endpoints")
        print("-" * 100)
        results = []
        for query, label in cases:
            bench.reset()
            result = measure(bench, query, label)
            results.append(result)
            endpoints = ", ".join(
                f"{path.replace('/api/', '')}x{count}" if count > 1
                else path.replace("/api/", "")
                for path, count in sorted(result["paths"].items())
            )
            flag = "  FAILED" if result["failed"] else ""
            print(f"{label:34s} {result['requests']:5d} {result['items']:6d} "
                  f"{result['seconds'] * 1000:7.0f}   {endpoints[:44]}{flag}")

        print("\nAt 15ms round-trip on a LAN, each request is 15ms of frozen UI:")
        for result in sorted(results, key=lambda r: -r["requests"])[:4]:
            print(f"  {result['label']:34s} {result['requests']:3d} reqs "
                  f"= {result['requests'] * 15:5d}ms")
    finally:
        bench.stop()


if __name__ == "__main__":
    main()
