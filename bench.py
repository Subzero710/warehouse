#!/usr/bin/env python3

"""Command-line benchmark for the product catalog API (standard library only).

For each scenario, ONE request is issued as an unmeasured warmup, then
``--runs`` requests are timed. Every request is measured client-side as
wall-clock time (connect + full body read), in milliseconds. No request is
retried: each sample is one real round-trip.

The filter scenarios time one GET /api/products request per access pattern:
by category name, by brand name, and by product name (``search_text``).
Each resolves its lookup value once at startup — the first category name
from /api/categories, the first brand name from /api/brands, and the full
name of the first product of the page-1 response — and requests page 1
with the default page size. The displayed label shows a ``<name>``
placeholder; the real value is percent-encoded into the path. A filter
scenario is skipped when its value cannot be resolved (empty catalog, no
category / brand, or a blank category / brand name).

The rename scenario performs a two-phase PUT: rename one category to a
temporary name (phase "rename"), then rename it back to the original name
(phase
"restore"), leaving a clean state afterwards. Each phase follows the usual
1-warmup + N-measured pattern, and EVERY measured PUT performs a real
rename: before each measured PUT the category's current id is re-resolved
by name from a fresh /api/categories fetch (so an implementation that
re-issues the id on rename keeps working), and an unmeasured companion PUT
restores the starting state before the next sample. The temporary name is
chosen ONCE before the runs: it is the original name plus " (bench)"; when
that exact name is already taken a counter grows — " (bench-2)",
" (bench-3)", ... — until the candidate is outside the current name set.
After the rename scenario the script re-fetches
the category list: if the sorted name list no longer matches the one
captured before it, an error is printed and the process exits with
code 2. When the category list is empty, the rename scenario prints "skip"
and the run exits with code 0.

Usage:
    python bench.py [--url http://localhost:5000] [--runs 5]
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from urllib.parse import quote

DEFAULT_URL = "http://localhost:5000"
DEFAULT_RUNS = 5
TIMEOUT_S = 30
COLUMN_WIDTH = 46


def _get(base_url: str, path: str) -> bytes:
    """Issue one raw GET and return the full response body."""
    request = urllib.request.Request(f"{base_url}{path}")
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
        return response.read()


def _get_json(base_url: str, path: str):
    """Issue one GET and return the decoded JSON body."""
    return json.loads(_get(base_url, path).decode("utf-8"))


def _time(base_url: str, path: str, body: dict | None = None) -> float:
    """Time one full round-trip (connect + body read) and return its ms.

    A ``None`` body is a bare GET; any other body is sent as a JSON PUT.
    """
    data = None if body is None else json.dumps(body).encode("utf-8")
    method = "PUT" if data is not None else "GET"
    headers = {"Content-Type": "application/json"} if data is not None else {}
    request = urllib.request.Request(
        f"{base_url}{path}", data=data, headers=headers, method=method
    )
    start = time.perf_counter()
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
        response.read()
    return (time.perf_counter() - start) * 1000.0


def _row(name: str, runs: int, samples: list[float]) -> str:
    """Format one measured scenario row (mean / min / max in ms)."""
    return (
        f"{name:<{COLUMN_WIDTH}} {runs:>5} {sum(samples) / len(samples):>10.3f}"
        f" {min(samples):>10.3f} {max(samples):>10.3f}"
    )


def _temp_bench_name(original: str, current_names: set[str]) -> str:
    """Build the temporary benchmark name for ``original``.

    The base candidate is ``original + " (bench)"``; when that exact name is
    already in ``current_names`` a counter grows — " (bench-2)",
    " (bench-3)", ... — until the candidate is outside the set.
    """
    candidate = f"{original} (bench)"
    counter = 2
    while candidate in current_names:
        candidate = f"{original} (bench-{counter})"
        counter += 1
    return candidate


def _resolve_category_id_by_name(base_url: str, name: str) -> str:
    """Re-fetch /api/categories and return the current id of the category named ``name``.

    The name, never a cached id, is the lookup key: an implementation that
    re-issues the id on rename ends with the id equal to the (new) name.
    Raises ``RuntimeError`` when the name is no longer listed.
    """
    for entry in _get_json(base_url, "/api/categories"):
        if entry["name"] == name:
            return entry["id"]
    raise RuntimeError(f"category {name!r} is no longer listed by the server")


def _resolve_scenarios(base_url: str) -> tuple[list[tuple[str, str]], dict | None, list[str]]:
    """Resolve the GET scenarios plus the rename-scenario probe state.

    Returns ``(get_scenarios, first_category, initial_names)``: the
    (name, path) pairs handled by the generic loop, the first category of
    the /api/categories response (``None`` when the list is empty), and the
    sorted category name list captured before the rename scenario runs.
    The pre-run probe requests are NOT part of any scenario's warmup or
    measured runs.
    """
    page1 = _get_json(base_url, "/api/products?page=1")
    categories = _get_json(base_url, "/api/categories")
    brands = _get_json(base_url, "/api/brands")

    scenarios: list[tuple[str, str]] = []
    first_item = (page1.get("items") or [None])[0]
    if first_item is not None:
        product_path = f"/api/products/{first_item['id']}"
        scenarios.append(("GET /api/products?page=1 (list)", "/api/products?page=1"))
        scenarios.append((f"GET {product_path.replace(first_item['id'], '<id>')}", product_path))
        search_path = f"/api/products?search_text={quote(first_item['name'], safe='')}&page=1"
        scenarios.append(("GET /api/products?search_text=<name>&page=1", search_path))
    if categories and categories[0]["name"].strip():
        category_path = f"/api/products?category={quote(categories[0]['name'], safe='')}&page=1"
        scenarios.append(("GET /api/products?category=<name>&page=1", category_path))
    if brands and brands[0]["name"].strip():
        brand_path = f"/api/products?brand={quote(brands[0]['name'], safe='')}&page=1"
        scenarios.append(("GET /api/products?brand=<name>&page=1", brand_path))
    scenarios.append(("GET /api/categories", "/api/categories"))
    scenarios.append(("GET /api/products/stats", "/api/products/stats"))

    first_category = categories[0] if categories else None
    initial_names = sorted(category["name"] for category in categories)
    return scenarios, first_category, initial_names


def _rename_put(base_url: str, current_name: str, new_name: str) -> float:
    """Time one real rename PUT, resolving the id by name right before it.

    The id is re-resolved from a fresh /api/categories fetch (a
    re-issuing implementation keeps working: the name, never a cached id,
    is the lookup key) and percent-encoded into the path (a re-issued id
    can be any string, e.g. the name itself with spaces). ``new_name`` must
    differ from ``current_name``, so the PUT always performs a real rename.
    """
    current_id = _resolve_category_id_by_name(base_url, current_name)
    path = f"/api/categories/{quote(current_id, safe='')}"
    return _time(base_url, path, {"name": new_name})


def _run_rename_scenario(base_url: str, runs: int, category: dict, initial_names: list[str]) -> int:
    """Rename scenario: rename one category to a temporary name and restore it.

    Two PUT phases (rename to the temporary name, rename back to the
    original), timed separately with the usual 1 warmup + N measured runs
    each. Every measured PUT performs a real rename: the id is re-resolved
    by name before each PUT, and an unmeasured companion PUT restores the
    starting state before the next sample, so no sample ever measures a
    no-op and no cached id is ever trusted. The temporary name is chosen
    once, before the runs. Returns the process exit code: 0 when the final
    self-check confirms that the initial state was restored, 2 otherwise.
    """
    original = category["name"]
    temp_name = _temp_bench_name(original, set(initial_names))

    _rename_put(base_url, original, temp_name)  # unmeasured warmup
    _rename_put(base_url, temp_name, original)  # ...and back, unmeasured
    rename_samples = []
    for _ in range(runs):
        rename_samples.append(_rename_put(base_url, original, temp_name))
        _rename_put(base_url, temp_name, original)  # unmeasured reset
    print(_row(f"PUT /api/categories/<id> -> '{temp_name}'", runs, rename_samples))

    _rename_put(base_url, original, temp_name)  # unmeasured setup (also warms the path)
    _rename_put(base_url, temp_name, original)  # back to the starting state, unmeasured
    restore_samples = []
    for _ in range(runs):
        _rename_put(base_url, original, temp_name)  # unmeasured setup
        restore_samples.append(_rename_put(base_url, temp_name, original))
    print(_row(f"PUT /api/categories/<id> -> '{original}'", runs, restore_samples))

    final_names = sorted(c["name"] for c in _get_json(base_url, "/api/categories"))
    if final_names != initial_names:
        print("self-check failed: category names do not match the initial list")
        return 2
    return 0


def run_bench(base_url: str, runs: int) -> int:
    """Run every scenario, print a summary, and return the process exit code.

    Exit code: 0 on success (including a skipped rename scenario), 2 when
    the final self-check finds the category state altered by the rename
    scenario.
    """
    scenarios, first_category, initial_names = _resolve_scenarios(base_url)
    header = (
        f"{'scenario':<{COLUMN_WIDTH}} {'runs':>5} {'mean_ms':>10}"
        f" {'min_ms':>10} {'max_ms':>10}"
    )
    print(header)
    print("-" * len(header))
    for name, path in scenarios:
        _time(base_url, path)  # one unmeasured warmup
        print(_row(name, runs, [_time(base_url, path) for _ in range(runs)]))
    if not scenarios:
        print("no scenarios could be resolved (empty catalog?)")

    if first_category is None:
        skip_name = "PUT /api/categories/<id> (temp name + restore)"
        print(f"{skip_name:<{COLUMN_WIDTH}} skip")
        return 0
    return _run_rename_scenario(base_url, runs, first_category, initial_names)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark the catalog API (standard library only)."
    )
    parser.add_argument("--url", default=DEFAULT_URL, help="base URL (default %(default)s)")
    runs_help = "measured runs per scenario (default %(default)s)"
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS, help=runs_help)
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be >= 1")
    return run_bench(args.url.rstrip("/"), args.runs)


if __name__ == "__main__":
    raise SystemExit(main())
