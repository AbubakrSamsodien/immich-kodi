#!/usr/bin/env python3
"""Offline test harness for plugin.video.immich.

    python3 tests/run.py            # run everything
    python3 tests/run.py -v         # also print each passing case
    python3 tests/run.py <substr>   # run only cases whose name matches

Standard library only. Stubs the xbmc* modules against the Kodi 21 (Omega)
signatures and serves Immich 2.7.5 responses from a real loopback HTTP server,
so the addon's own http.client code path runs unmodified.
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

# The stubs must shadow anything else called xbmc*, and tests/ must be
# importable. resources/lib goes on last: addon.py puts it at sys.path[0] on
# every invocation anyway, this just lets the harness instrument it up front.
for path in (os.path.join(REPO, "resources", "lib"), HERE, os.path.join(HERE, "kodistubs")):
    if path in sys.path:
        sys.path.remove(path)
    sys.path.insert(0, path)


def instrument():
    """Wrap kodiutils.media so a missing artwork file is observable.

    `media()` silently substitutes the addon icon for a filename that is not on
    disk, which would otherwise hide a typo behind a plausible-looking icon.
    """
    import harness
    import kodiutils
    import listing
    import views

    # Record the ImmichClient constructor arguments so settings that only show
    # up inside the client (timeout, verify_ssl) are observable.
    import api
    original_init = api.ImmichClient.__init__

    def recording_init(self, base_url, api_key, timeout=20, verify_ssl=True,
                       log=None):
        harness.CLIENT_INITS.append(
            {"base_url": base_url, "api_key": api_key, "timeout": timeout,
             "verify_ssl": verify_ssl}
        )
        original_init(self, base_url, api_key, timeout, verify_ssl, log)

    api.ImmichClient.__init__ = recording_init

    real = kodiutils.media

    def recording(filename):
        resolved = real(filename)
        harness.MEDIA_CALLS.append(
            (filename, resolved, resolved == kodiutils.ADDON_ICON
             and filename != "icon.png")
        )
        return resolved

    kodiutils.media = recording
    listing.media = recording
    views.media = recording


def main(argv):
    verbose = "-v" in argv or "--verbose" in argv
    filters = [a for a in argv if not a.startswith("-")]

    import harness
    harness.MEDIA_CALLS = []
    harness.CLIENT_INITS = []
    instrument()

    import suite
    from harness import discover_routes

    routes = discover_routes()
    declared = {action for action, _name, _line in routes}

    print("=" * 78)
    print("plugin.video.immich offline harness")
    print("=" * 78)
    print(f"\n@route decorators found in resources/lib/views.py ({len(routes)}):")
    for action, name, line in routes:
        print(f"  {('(root)' if action == '' else action):<16} -> views.{name}  "
              f"(views.py:{line})")

    bench = harness.Harness()
    passed, failed = [], []
    covered = set()
    try:
        for name, function, route in suite.CASES:
            if filters and not any(f in name for f in filters):
                continue
            harness.CURRENT_CASE = name
            try:
                problems = function(bench) or []
            except Exception as error:  # noqa: BLE001 - a broken case is a failure
                import traceback
                problems = [f"the case itself raised: {error!r}\n{traceback.format_exc()}"]
            if route is not None:
                covered.add(route)
            if problems:
                failed.append((name, problems))
            else:
                passed.append(name)
    finally:
        bench.stop()

    # Any action string that actually reached dispatch also counts as covered.
    for record in bench.invocations:
        from urllib.parse import parse_qsl, urlparse
        params = dict(parse_qsl(urlparse("x://y" + record.query).query))
        action = params.get("action", "")
        if action in declared:
            covered.add(action)

    print(f"\nRoutes exercised end to end ({len(covered)}/{len(declared)}):")
    for action, name, _line in routes:
        mark = "PASS" if action in covered else "MISS"
        print(f"  [{mark}] {('(root)' if action == '' else action)}")
    uncovered = declared - covered

    matrix = {}
    for version, owner in harness.VERSION_MATRIX:
        matrix.setdefault(version, []).append(owner)
    matrix.setdefault("2.7.5", [])
    print(f"\nImmich versions impersonated ({len(matrix)}):")
    for version in sorted(matrix, key=lambda v: [int(p) for p in v.split(".")]):
        owners = sorted(set(matrix[version]))
        note = " (baseline for every case that does not override it)" \
            if version == "2.7.5" else ""
        print(f"  {version:<9} {len(owners)} explicit case(s){note}")
        for owner in owners:
            print(f"  {'':<9}   - {owner}")

    print("\n" + "-" * 78)
    if verbose:
        for name in passed:
            print(f"  ok   {name}")
    for name, problems in failed:
        print(f"\nFAIL  {name}")
        for problem in problems:
            first, _, rest = problem.partition("\n")
            print(f"        - {first}")
            for line in rest.splitlines():
                print(f"          {line}")
    print("\n" + "=" * 78)
    print(f"cases: {len(passed)} passed, {len(failed)} failed "
          f"({len(passed) + len(failed)} run)")
    print(f"routes: {len(covered)}/{len(declared)} exercised"
          + (f" - MISSING {sorted(uncovered)}" if uncovered else ""))
    print(f"invocations: {len(bench.invocations)}")
    print("=" * 78)

    return 1 if (failed or uncovered) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
