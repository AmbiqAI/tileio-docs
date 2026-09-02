# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026, Ambiq
"""Generate THIRD-PARTY-NOTICES.md from the locked Poetry environment.

The package set is read from poetry.lock (every direct and transitive
dependency needed to build the Tileio docs site). License metadata for each
package is pulled from pip-licenses, which must be installed into the
active environment first, e.g.:

    poetry install --no-root
    poetry install --no-root --sync
    poetry run pip install -c <(python3 -c "import tomllib; [print(f'{p[\"name\"]}=={p[\"version\"]}') for p in tomllib.load(open('poetry.lock','rb'))['package']]") pip-licenses
    poetry run python tools/gen_notices.py

The constraints file in that pip install pins every already-locked package
to its poetry.lock version, so installing pip-licenses (and its own
prettytable/wcwidth dependency chain) cannot silently upgrade a package
that happens to share a name with one of pip-licenses' own dependencies.
Run poetry install --no-root --sync beforehand so the environment starts
from an exact match of the lock file.

As defense in depth, this script independently verifies that every
installed package version pip-licenses reports matches the version pinned
in poetry.lock; a mismatch is a hard failure naming the offending package,
not a warning, so a drifted environment can never produce a silently wrong
notices file.

ADR-0005 (Tier 1) allows MIT, BSD, Apache-2.0, ISC, MPL-2.0 and PSF licensed
dependencies. This is an allow-list: any license that does not normalize to
one of those families is BLOCKED and fails the run, unless the package is
listed in APPROVED_EXCEPTIONS below with a named approver and date. There is
no separate "flagged but passing" bucket; an unrecognized or restrictive
license (GPL, LGPL, AGPL, EUPL, CDDL, SSPL, "UNKNOWN", etc.) fails closed.

Run with --check in CI to fail on drift between the committed file and a
freshly generated one, in addition to the license-family and version checks
above.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCK_FILE = REPO_ROOT / "poetry.lock"
OUTPUT_FILE = REPO_ROOT / "THIRD-PARTY-NOTICES.md"

# ADR-0005 Tier 1 allow-list families.
ALLOWED_FAMILIES = {"mit", "bsd", "apache-2.0", "isc", "mpl-2.0", "psf"}

# Explicitly approved exceptions to the allow-list above. Empty until ADR-0005
# grants one; adding an entry here is a licensing decision for a human owner,
# not something this script should do on its own. Example:
#   "some-package": {"approver": "jane.doe", "date": "2026-01-01",
#                     "reason": "explain why this is acceptable"},
APPROVED_EXCEPTIONS: dict[str, dict[str, str]] = {}


def normalize_name(name: str) -> str:
    return name.lower().replace("_", "-")


def normalize_families(license_str: str) -> set[str]:
    """Map a pip-licenses free-text license string to canonical ADR-0005 families."""
    s = license_str.lower()
    families: set[str] = set()
    if "mit" in s:
        families.add("mit")
    if "bsd" in s:
        families.add("bsd")
    if "apache" in s:
        families.add("apache-2.0")
    if "isc" in s:
        families.add("isc")
    if "mpl" in s or "mozilla public license" in s:
        families.add("mpl-2.0")
    if "psf" in s or "python software foundation" in s:
        families.add("psf")
    return families


def classify(name: str, license_str: str) -> str:
    """Classify a package as one of: allowed, exception, blocked.

    This is an allow-list gate: anything that does not normalize to an
    ADR-0005 family is blocked unless it has an explicitly approved
    exception. There is no "recorded but passing" middle ground.
    """
    if normalize_name(name) in APPROVED_EXCEPTIONS:
        return "exception"
    if normalize_families(license_str) & ALLOWED_FAMILIES:
        return "allowed"
    return "blocked"


def locked_packages() -> dict[str, str]:
    """Return {normalized package name: locked version} from poetry.lock."""
    data = tomllib.loads(LOCK_FILE.read_text())
    return {normalize_name(pkg["name"]): pkg["version"] for pkg in data["package"]}


def run_pip_licenses(package_names: list[str]) -> list[dict]:
    exe = shutil.which("pip-licenses")
    if exe is None:
        sys.exit(
            "pip-licenses is not installed in the active environment.\n"
            "Install it first, e.g.:\n"
            "  poetry install --no-root --sync\n"
            "  poetry run pip install -c <lock-constraints> pip-licenses\n"
            "  poetry run python tools/gen_notices.py\n"
        )
    cmd = [
        exe,
        "--format=json",
        "--with-urls",
        "--with-license-file",
        "--no-license-path",
        "--order=name",
        # pip-licenses excludes its own tooling dependencies (e.g. wcwidth,
        # prettytable) by default. --with-system re-includes them; the
        # --packages filter below still restricts output to what's actually
        # locked in poetry.lock, so pip-licenses' own tooling never leaks in.
        "--with-system",
        "--packages",
        *package_names,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def verify_no_version_drift(packages: list[dict], locked: dict[str, str]) -> None:
    """Hard-fail if any reported package version differs from poetry.lock.

    Installing pip-licenses can pull a newer transitive dependency (e.g.
    wcwidth, via prettytable) that happens to share a name with a package
    already pinned in poetry.lock. Silently reporting that drifted version
    would make the notices file describe an environment that isn't actually
    the one poetry.lock builds the site with, so this is fatal, not a
    warning.
    """
    drifted = []
    for p in packages:
        key = normalize_name(p["Name"])
        locked_version = locked.get(key)
        if locked_version is not None and locked_version != p["Version"]:
            drifted.append((p["Name"], p["Version"], locked_version))
    if drifted:
        lines = [
            "gen_notices: HARD FAIL - installed package version(s) do not match poetry.lock.",
            "This is the pip-licenses side effect described in this script's docstring "
            "(installing pip-licenses can upgrade a shared transitive dependency, e.g. "
            "wcwidth via prettytable). Run `poetry install --no-root --sync` to restore "
            "the locked versions, reinstall pip-licenses with a constraints file pinned "
            "to poetry.lock, and re-run.",
        ]
        for name, installed, locked_version in drifted:
            lines.append(f"  - {name}: installed {installed}, locked {locked_version}")
        sys.exit("\n".join(lines))


def render(packages: list[dict]) -> str:
    packages = sorted(packages, key=lambda p: p["Name"].lower())

    blocked = [p for p in packages if classify(p["Name"], p["License"]) == "blocked"]
    exceptions = [p for p in packages if classify(p["Name"], p["License"]) == "exception"]

    lines: list[str] = []
    lines.append("# Third-Party Notices")
    lines.append("")
    lines.append(
        "This file is generated by `tools/gen_notices.py` from the locked "
        "Poetry environment (`poetry.lock`) used to build the Tileio "
        "documentation site. Do not edit by hand; re-run the generator "
        "instead. Tileio brand assets under `assets/` are Ambiq marks and "
        "are not third-party software; they are not listed here."
    )
    lines.append("")
    lines.append(
        f"ADR-0005 (Tier 1) allow-list: {', '.join(sorted(ALLOWED_FAMILIES))}. "
        f"{len(packages)} package(s) inventoried. Any license outside this "
        "allow-list is blocked unless it has an explicitly approved exception."
    )
    lines.append("")

    if blocked:
        lines.append(
            "> BLOCKED: one or more dependencies carry a license outside "
            "the ADR-0005 allow-list. This is a licensing decision, not an "
            "automated one; see the Blocked section below before this "
            "file is treated as compliant."
        )
        lines.append("")

    lines.append("## Packages")
    lines.append("")
    lines.append("| Name | Version | License | URL |")
    lines.append("|------|---------|---------|-----|")
    for p in packages:
        url = p.get("URL") or "UNKNOWN"
        lines.append(f"| {p['Name']} | {p['Version']} | {p['License']} | {url} |")
    lines.append("")

    if blocked:
        lines.append("## Blocked (outside the ADR-0005 allow-list)")
        lines.append("")
        for p in blocked:
            lines.append(f"- **{p['Name']} {p['Version']}** - {p['License']}")
        lines.append("")

    if exceptions:
        lines.append("## Approved exceptions")
        lines.append("")
        for p in exceptions:
            info = APPROVED_EXCEPTIONS[normalize_name(p["Name"])]
            lines.append(
                f"- **{p['Name']} {p['Version']}** - {p['License']} "
                f"(approved by {info.get('approver', 'unknown')} on "
                f"{info.get('date', 'unknown')}: {info.get('reason', '')})"
            )
        lines.append("")

    lines.append("## License texts")
    lines.append("")
    for p in packages:
        lines.append(f"### {p['Name']} {p['Version']} ({p['License']})")
        lines.append("")
        text = (p.get("LicenseText") or "").strip()
        if not text or text.upper() == "UNKNOWN":
            lines.append(
                f"License text not bundled with the package distribution; "
                f"see {p.get('URL') or 'the package index'} for the canonical text."
            )
        else:
            lines.append("```")
            lines.append(text)
            lines.append("```")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if the committed file is out of date instead of writing it",
    )
    args = parser.parse_args()

    locked = locked_packages()
    # pip-licenses --packages matches against original (non-normalized) names,
    # so pull those from poetry.lock rather than the normalized lookup keys.
    data = tomllib.loads(LOCK_FILE.read_text())
    original_names = sorted({pkg["name"] for pkg in data["package"]}, key=str.lower)
    packages = run_pip_licenses(original_names)

    verify_no_version_drift(packages, locked)

    found_names = {normalize_name(p["Name"]) for p in packages}
    locked_names = set(locked.keys())
    missing = locked_names - found_names
    if missing:
        print(
            f"warning: {len(missing)} locked package(s) not found by pip-licenses "
            f"in the active environment (is it installed via `poetry install --no-root`?): "
            f"{', '.join(sorted(missing))}",
            file=sys.stderr,
        )

    content = render(packages)

    blocked = [p for p in packages if classify(p["Name"], p["License"]) == "blocked"]
    exceptions = [p for p in packages if classify(p["Name"], p["License"]) == "exception"]
    allowed = [p for p in packages if classify(p["Name"], p["License"]) == "allowed"]

    print(
        f"gen_notices: {len(packages)} package(s) - "
        f"allowed={len(allowed)}, exception={len(exceptions)}, blocked={len(blocked)}"
    )
    if blocked:
        print(
            f"gen_notices: BLOCKED - license(s) outside the ADR-0005 allow-list: "
            f"{[(p['Name'], p['License']) for p in blocked]}",
            file=sys.stderr,
        )

    if args.check:
        if not OUTPUT_FILE.exists():
            print(f"gen_notices --check: {OUTPUT_FILE} does not exist", file=sys.stderr)
            return 1
        if OUTPUT_FILE.read_text() != content:
            print(f"gen_notices --check: {OUTPUT_FILE} is out of date; re-run without --check", file=sys.stderr)
            return 1
    else:
        OUTPUT_FILE.write_text(content)
        print(f"gen_notices: wrote {OUTPUT_FILE}")

    return 1 if blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
