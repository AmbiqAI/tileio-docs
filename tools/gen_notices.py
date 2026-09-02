# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026, Ambiq
"""Generate THIRD-PARTY-NOTICES.md for the environment that builds the site.

Scope. The notices file describes one declared target environment: the Poetry
environment that builds the published Tileio documentation site on the deploy
runner (see .github/workflows/deploy.yaml), which is Linux with CPython 3.11.
The package set is therefore not "whatever is installed on the machine that
ran this script". It is every poetry.lock entry that belongs to the main
dependency group and whose environment marker evaluates true for the target
environment declared in TARGET_ENVIRONMENT below. Markers are evaluated with
packaging against that explicit dictionary, never against the running
interpreter, so the same file is produced on Linux and on macOS. Marker
variables the dictionary does not model, such as the runner's CPU architecture
or patch-level Python version, are listed in UNMODELLED_MARKER_VARIABLES and
hard-fail before evaluation instead of being guessed.

Usage:

    poetry sync --no-root --with dev
    poetry run python tools/gen_notices.py

pip-licenses is pinned in the "dev" Poetry group, so it is resolved in
poetry.lock like every other dependency and cannot silently upgrade a package
that it shares a transitive dependency with. Run the generator from a Python
3.11 Poetry environment: every package in the target set must be installed
locally for pip-licenses to report its metadata, and the script hard-fails
naming any target package it cannot find. Packages installed locally but
outside the target set (for example appnope on macOS) are excluded from the
generated file and reported on the console only, so that the file stays
identical whichever host generated it.

As defense in depth, the script verifies that every installed version
pip-licenses reports matches the version pinned in poetry.lock; a mismatch is
a hard failure naming the offending package.

Where a package declares an ambiguous license string, such as bare "Apache"
with no version, AMBIGUOUS_LICENSE_RESOLUTIONS records the resolution together
with the lines that must appear in the license text bundled with the installed
distribution. The check runs on every run: missing or mismatched text blocks
the package rather than resolving it.

ADR-0005 (Tier 1) allows MIT, BSD, Apache-2.0, ISC, MPL-2.0 and PSF licensed
dependencies. This is an allow-list built on an exact alias table, not a
substring search: any license string that does not resolve, term by term, to
one of those families is BLOCKED and fails the run, unless the package is
listed in APPROVED_EXCEPTIONS below with a named approver and date. There is
no separate "flagged but passing" bucket; an unrecognized or restrictive
license (GPL, LGPL, AGPL, EUPL, CDDL, SSPL, "UNKNOWN", etc.) fails closed.

Run with --check in CI to fail on drift between the committed file and a
freshly generated one, and with --selftest to run the license-classifier test
cases.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tomllib
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as installed_version
from pathlib import Path

from packaging.markers import InvalidMarker, Marker, UndefinedEnvironmentName

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCK_FILE = REPO_ROOT / "poetry.lock"
OUTPUT_FILE = REPO_ROOT / "THIRD-PARTY-NOTICES.md"

# The dependency group that builds the published site. The "dev" group holds
# the notices tooling itself and is optional, so `poetry install --no-root` on
# the deploy runner does not install it and it is not part of the site.
TARGET_GROUP = "main"

# The declared target environment: the deploy runner in
# .github/workflows/deploy.yaml (ubuntu-latest, actions/setup-python 3.11).
# Marker evaluation uses this dictionary and not the running interpreter, so
# generating on macOS and checking on Linux CI produce the same file.
TARGET_PLATFORM = "linux"
TARGET_PYTHON_VERSION = "3.11"
#
# Only variables this script can state with confidence are listed. Anything
# absent here is unmodelled: see UNMODELLED_MARKER_VARIABLES below, which is
# checked before any marker is evaluated. That order matters, because
# packaging's Marker.evaluate() merges this dictionary over the running
# interpreter's environment, so an unmodelled variable that reached evaluation
# would silently take the value of the generating machine.
TARGET_ENVIRONMENT = {
    "implementation_name": "cpython",
    "os_name": "posix",
    "platform_python_implementation": "CPython",
    "platform_system": "Linux",
    "python_version": "3.11",
    "sys_platform": "linux",
}

# Marker variables this script does not model with enough confidence to decide
# inclusion. The runner's kernel release, CPU architecture and patch-level
# Python version are not fixed by anything in this repository, so a lock marker
# that depends on them needs a human decision rather than a guess from this
# script. Every name here is deliberately absent from TARGET_ENVIRONMENT, and
# marker_matches_target() hard-fails on it before evaluation, so no guessed or
# host-supplied value can decide inclusion.
UNMODELLED_MARKER_VARIABLES = (
    "platform_release",
    "platform_version",
    "platform_machine",
    "python_full_version",
    "implementation_version",
    "extra",
)

# ADR-0005 Tier 1 allow-list families. Names are literal: MPL-2.0 and
# Apache-2.0 only, so MPL-1.1, Apache-1.x and BSD-4-Clause are not covered by
# any alias below and block.
ALLOWED_FAMILIES = {"mit", "bsd", "apache-2.0", "isc", "mpl-2.0", "psf"}

# Exact license strings mapped to canonical families. Keys are lower-cased and
# whitespace-collapsed. Matching is exact against a whole term, never a
# substring search over free text: "Acme Limited Proprietary" contains "mit"
# and a substring search would wrongly allow it. Anything not listed here
# blocks, so extending this table is a deliberate act.
CANONICAL_ALIASES: dict[str, str] = {
    # MIT
    "mit": "mit",
    "mit license": "mit",
    "the mit license (mit)": "mit",
    # BSD. The "BSD License" classifier does not name a clause count; ADR-0005
    # treats the BSD family as Tier 1, and BSD-4-Clause is deliberately absent.
    "bsd": "bsd",
    "bsd license": "bsd",
    "bsd-2-clause": "bsd",
    "bsd-3-clause": "bsd",
    "bsd 2-clause license": "bsd",
    "bsd 3-clause license": "bsd",
    # Apache. Bare "Apache" is deliberately absent: it names no version and
    # Apache-1.x is not Tier 1. See AMBIGUOUS_LICENSE_RESOLUTIONS.
    "apache software license": "apache-2.0",
    "apache-2.0": "apache-2.0",
    "apache 2.0": "apache-2.0",
    "apache license 2.0": "apache-2.0",
    "apache license, version 2.0": "apache-2.0",
    "apache software license 2.0": "apache-2.0",
    # ISC
    "isc": "isc",
    "isc license": "isc",
    "isc license (iscl)": "isc",
    # MPL
    "mpl-2.0": "mpl-2.0",
    "mozilla public license 2.0 (mpl 2.0)": "mpl-2.0",
    # PSF
    "psf-2.0": "psf",
    "python software foundation license": "psf",
}

# Packages whose declared license string is ambiguous but whose bundled license
# text settles it. Applied only when the reported license string matches
# exactly, only to resolve to a family that is already on the allow-list, and
# only when the bundled license text the run collected actually carries the
# identifying lines in "evidence_text". This records evidence checked against
# the distribution on every run; it does not grant an exception to the
# allow-list. A package with no bundled license text, or with text missing any
# required line, is blocked rather than resolved.
#
# "evidence_text" holds lines that must all appear in the bundled text, matched
# after whitespace is collapsed. For Apache-2.0 the header lines "Apache
# License" and "Version 2.0, January 2004" together distinguish the 2.0 text
# from Apache-1.x, which is not Tier 1.
AMBIGUOUS_LICENSE_RESOLUTIONS: dict[str, dict] = {
    "mkdocs-exclude": {
        "license": "Apache",
        "family": "apache-2.0",
        "evidence_text": ("Apache License", "Version 2.0, January 2004"),
        "evidence": (
            "the LICENSE file bundled in the distribution is the Apache "
            "License, Version 2.0, verbatim; the package metadata just says "
            "'Apache' with no version"
        ),
    },
}

# Explicitly approved exceptions to the allow-list above. Empty until ADR-0005
# grants one; adding an entry here is a licensing decision for a human owner,
# not something this script should do on its own. Example:
#   "some-package": {"approver": "jane.doe", "date": "2026-01-01",
#                     "reason": "explain why this is acceptable"},
APPROVED_EXCEPTIONS: dict[str, dict[str, str]] = {}


class LicenseParseError(ValueError):
    """Raised when a license string cannot be parsed as an SPDX expression."""


def normalize_name(name: str) -> str:
    return name.lower().replace("_", "-")


def _normalize_text(text: str) -> str:
    return " ".join(text.split()).strip()


def _canonical_family(term: str) -> str | None:
    """Return the canonical family for one exact license term, or None."""
    return CANONICAL_ALIASES.get(_normalize_text(term).lower())


_TOKEN_RE = re.compile(r"\(|\)|[^\s()]+")


def _parse_or(tokens: list[str], pos: int) -> tuple[bool, int]:
    # SPDX choice semantics: "A OR B" lets the user pick either license, so the
    # expression is allowed when at least one operand is allowed.
    allowed, pos = _parse_and(tokens, pos)
    while pos < len(tokens) and tokens[pos] == "OR":
        right, pos = _parse_and(tokens, pos + 1)
        allowed = allowed or right
    return allowed, pos


def _parse_and(tokens: list[str], pos: int) -> tuple[bool, int]:
    # SPDX conjunction: "A AND B" imposes both licenses, so every operand must
    # be allowed.
    allowed, pos = _parse_with(tokens, pos)
    while pos < len(tokens) and tokens[pos] == "AND":
        right, pos = _parse_with(tokens, pos + 1)
        allowed = allowed and right
    return allowed, pos


def _parse_with(tokens: list[str], pos: int) -> tuple[bool, int]:
    # "A WITH exception" is a modified license. The exception text is not
    # reviewed here, so it blocks unless the whole expression is an exact
    # entry in CANONICAL_ALIASES (checked before parsing).
    allowed, pos = _parse_primary(tokens, pos)
    if pos < len(tokens) and tokens[pos] == "WITH":
        if pos + 1 >= len(tokens):
            raise LicenseParseError("WITH with no exception identifier")
        return False, pos + 2
    return allowed, pos


def _parse_primary(tokens: list[str], pos: int) -> tuple[bool, int]:
    if pos >= len(tokens):
        raise LicenseParseError("unexpected end of license expression")
    token = tokens[pos]
    if token == "(":
        allowed, pos = _parse_or(tokens, pos + 1)
        if pos >= len(tokens) or tokens[pos] != ")":
            raise LicenseParseError("unbalanced parenthesis")
        return allowed, pos + 1
    if token in (")", "AND", "OR", "WITH"):
        raise LicenseParseError(f"unexpected operator {token!r}")
    family = _canonical_family(token.rstrip("+"))
    return (family in ALLOWED_FAMILIES if family else False), pos + 1


def license_allowed(license_str: str) -> bool:
    """Return True only if every term of the license string is allow-listed.

    Three shapes are understood, in order:

    1. An exact entry in CANONICAL_ALIASES ("MIT License", "ISC License
       (ISCL)", ...).
    2. A "; "-joined list of trove classifiers as emitted by pip-licenses. A
       package that declares both a GPL and an MIT classifier is ambiguous, so
       every classifier must be allowed for the package to pass.
    3. An SPDX expression with AND, OR, WITH and parentheses.

    Anything else, including empty text and "UNKNOWN", blocks.
    """
    normalized = _normalize_text(license_str)
    if not normalized:
        return False

    family = _canonical_family(normalized)
    if family is not None:
        return family in ALLOWED_FAMILIES

    if ";" in normalized:
        terms = [t.strip() for t in normalized.split(";")]
        if any(not t for t in terms):
            return False
        return all(license_allowed(t) for t in terms)

    tokens = _TOKEN_RE.findall(normalized)
    try:
        allowed, pos = _parse_or(tokens, 0)
    except LicenseParseError:
        return False
    if pos != len(tokens):
        # Leftover tokens mean this is free text, not an SPDX expression.
        return False
    return allowed


def evidence_failure(name: str, license_text: str | None) -> str | None:
    """Return why a recorded ambiguous-license resolution is unsupported.

    Returns None when the package's bundled license text carries every line
    required by its AMBIGUOUS_LICENSE_RESOLUTIONS entry. Otherwise it returns a
    sentence naming the package and what is missing, and the caller blocks the
    package. A package with no entry also returns a sentence, because asking
    this question about it means the caller has already lost the plot.
    """
    key = normalize_name(name)
    resolution = AMBIGUOUS_LICENSE_RESOLUTIONS.get(key)
    if resolution is None:
        return f"{name} has no recorded ambiguous-license resolution"
    required = resolution["evidence_text"]
    text = _normalize_text(license_text or "")
    if not text or text.upper() == "UNKNOWN":
        return (
            f"{name} declares an ambiguous license and no license text is "
            "bundled with the distribution, so the recorded resolution to "
            f"{resolution['family']} cannot be confirmed"
        )
    missing = [line for line in required if _normalize_text(line) not in text]
    if missing:
        return (
            f"{name} bundles license text that does not carry "
            + ", ".join(repr(line) for line in missing)
            + f", so the recorded resolution to {resolution['family']} does "
            "not match the distribution"
        )
    return None


def classify(
    name: str,
    license_str: str,
    exceptions: dict[str, dict[str, str]] | None = None,
    license_text: str | None = None,
) -> str:
    """Classify a package as one of: allowed, exception, resolved, blocked.

    This is an allow-list gate: anything that does not resolve to an ADR-0005
    family is blocked unless it has an explicitly approved exception. There is
    no "recorded but passing" middle ground. "resolved" means the declared
    string was ambiguous and was settled from the bundled license text through
    AMBIGUOUS_LICENSE_RESOLUTIONS; it still lands inside the allow-list, and it
    applies only when license_text, the text this run collected from the
    installed distribution, carries the evidence lines recorded there. Missing
    or mismatched text blocks.
    """
    if exceptions is None:
        exceptions = APPROVED_EXCEPTIONS
    key = normalize_name(name)
    if key in exceptions:
        return "exception"
    if license_allowed(license_str):
        return "allowed"
    resolution = AMBIGUOUS_LICENSE_RESOLUTIONS.get(key)
    if (
        resolution is not None
        and _normalize_text(resolution["license"]) == _normalize_text(license_str)
        and resolution["family"] in ALLOWED_FAMILIES
        and evidence_failure(name, license_text) is None
    ):
        return "resolved"
    return "blocked"


def classify_package(p: dict, exceptions: dict[str, dict[str, str]] | None = None) -> str:
    """Classify one pip-licenses record, including its bundled license text."""
    return classify(p["Name"], p["License"], exceptions, p.get("LicenseText"))


# --------------------------------------------------------------------------
# Target environment selection
# --------------------------------------------------------------------------


def lock_entries() -> list[dict]:
    data = tomllib.loads(LOCK_FILE.read_text())
    return data["package"]


def marker_matches_target(name: str, marker_text: str) -> bool:
    for variable in UNMODELLED_MARKER_VARIABLES:
        if variable in marker_text:
            sys.exit(
                f"gen_notices: HARD FAIL - poetry.lock entry {name} has marker "
                f"{marker_text!r}, which depends on {variable}. "
                "TARGET_ENVIRONMENT does not model that variable; a human owner "
                "must decide whether the package is part of the deploy runner "
                "environment and extend TARGET_ENVIRONMENT."
            )
    try:
        return Marker(marker_text).evaluate(environment=TARGET_ENVIRONMENT)
    except (InvalidMarker, UndefinedEnvironmentName) as exc:
        sys.exit(
            f"gen_notices: HARD FAIL - cannot evaluate marker {marker_text!r} "
            f"for poetry.lock entry {name}: {exc}"
        )


def select_target_packages() -> tuple[dict[str, str], list[dict], list[dict]]:
    """Split poetry.lock into the target set and the two excluded sets.

    Returns (target {normalized name: version}, excluded by marker, excluded by
    group). The two excluded lists carry the raw lock entries so the generated
    file can account for every lock entry.
    """
    target: dict[str, str] = {}
    excluded_by_marker: list[dict] = []
    excluded_by_group: list[dict] = []
    for entry in lock_entries():
        if TARGET_GROUP not in entry.get("groups", ["main"]):
            excluded_by_group.append(entry)
            continue
        marker_text = entry.get("markers")
        if marker_text and not marker_matches_target(entry["name"], marker_text):
            excluded_by_marker.append(entry)
            continue
        target[normalize_name(entry["name"])] = entry["version"]
    return target, excluded_by_marker, excluded_by_group


# --------------------------------------------------------------------------
# pip-licenses
# --------------------------------------------------------------------------


def pip_licenses_version() -> str:
    try:
        return installed_version("pip-licenses")
    except PackageNotFoundError:
        sys.exit(
            "gen_notices: HARD FAIL - pip-licenses is not installed in the "
            "active environment. Run `poetry sync --no-root --with dev` first."
        )


def run_pip_licenses(package_names: list[str]) -> list[dict]:
    exe = shutil.which("pip-licenses")
    if exe is None:
        sys.exit(
            "gen_notices: HARD FAIL - the pip-licenses executable is not on "
            "PATH. Run `poetry sync --no-root --with dev` and invoke this "
            "script with `poetry run`."
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
        # locked in poetry.lock.
        "--with-system",
        "--packages",
        *package_names,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def verify_no_version_drift(packages: list[dict], locked: dict[str, str]) -> None:
    """Hard-fail if any reported package version differs from poetry.lock.

    A drifted environment would make the notices file describe an environment
    that poetry.lock does not build, so this is fatal, not a warning.
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
            "Run `poetry sync --no-root --with dev` to restore the locked "
            "versions and re-run.",
        ]
        for name, installed, locked_version in drifted:
            lines.append(f"  - {name}: installed {installed}, locked {locked_version}")
        sys.exit("\n".join(lines))


def verify_target_set_complete(packages: list[dict], target: dict[str, str]) -> None:
    """Hard-fail if pip-licenses did not report a package in the target set.

    A missing package would silently truncate the notices file, so this exits
    non-zero before anything is written. Generate from a Python 3.11 Poetry
    environment, where every package selected for the target environment is
    installed.
    """
    reported = {normalize_name(p["Name"]) for p in packages}
    missing = sorted(set(target) - reported)
    if missing:
        sys.exit(
            "gen_notices: HARD FAIL - "
            f"{len(missing)} package(s) in the target environment "
            f"({TARGET_PLATFORM} / CPython {TARGET_PYTHON_VERSION}) were not "
            "reported by pip-licenses, so the notices file would be "
            "incomplete: " + ", ".join(missing) + ". Run "
            "`poetry sync --no-root --with dev` in a Python "
            f"{TARGET_PYTHON_VERSION} environment and re-run."
        )


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def _marker_of(entry: dict) -> str:
    return entry.get("markers") or "no marker"


def render(
    packages: list[dict],
    excluded_by_marker: list[dict],
    excluded_by_group: list[dict],
    tool_version: str,
) -> str:
    packages = sorted(packages, key=lambda p: p["Name"].lower())

    blocked = [p for p in packages if classify_package(p) == "blocked"]
    exceptions = [p for p in packages if classify_package(p) == "exception"]
    resolved = [p for p in packages if classify_package(p) == "resolved"]

    lines: list[str] = []
    lines.append("# Third-Party Notices")
    lines.append("")
    lines.append(
        "This file is generated by `tools/gen_notices.py`. Do not edit by "
        "hand; re-run the generator instead. Tileio brand assets under "
        "`assets/` are Ambiq marks and are not third-party software; they are "
        "not listed here."
    )
    lines.append("")
    lines.append(
        "Scope: the Poetry environment that builds the published "
        "documentation site on the deploy runner "
        "(`.github/workflows/deploy.yaml`). That is every `poetry.lock` entry "
        f"in the `{TARGET_GROUP}` dependency group whose environment marker "
        f"holds on {TARGET_PLATFORM} with CPython {TARGET_PYTHON_VERSION}. "
        "Lock entries outside that set are listed, with the reason, in the "
        "final section, so this file accounts for every entry in the lock."
    )
    lines.append("")
    lines.append(
        f"- Target platform: {TARGET_PLATFORM} "
        f"(`sys_platform == \"{TARGET_ENVIRONMENT['sys_platform']}\"`, "
        f"`platform_system == \"{TARGET_ENVIRONMENT['platform_system']}\"`)"
    )
    lines.append(
        f"- Target Python: CPython {TARGET_PYTHON_VERSION} "
        f"(`python_version == \"{TARGET_ENVIRONMENT['python_version']}\"`)"
    )
    lines.append(f"- License metadata collected with pip-licenses {tool_version}")
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
            if normalize_name(p["Name"]) in AMBIGUOUS_LICENSE_RESOLUTIONS:
                reason = evidence_failure(p["Name"], p.get("LicenseText"))
                if reason:
                    lines.append(f"  - Recorded resolution not applied: {reason}.")
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

    if resolved:
        lines.append("## Ambiguous license strings resolved from the bundled text")
        lines.append("")
        for p in resolved:
            info = AMBIGUOUS_LICENSE_RESOLUTIONS[normalize_name(p["Name"])]
            lines.append(
                f"- **{p['Name']} {p['Version']}** - declared license "
                f"\"{p['License']}\" read as {info['family']}: {info['evidence']}."
            )
        lines.append("")

    lines.append("## Not part of the target environment")
    lines.append("")
    lines.append(
        "These `poetry.lock` entries are not installed by the site build on "
        "the deploy runner, so their licenses are not reproduced above."
    )
    lines.append("")
    if excluded_by_marker:
        lines.append(
            f"Excluded because their marker is false on {TARGET_PLATFORM} / "
            f"CPython {TARGET_PYTHON_VERSION}:"
        )
        lines.append("")
        for entry in sorted(excluded_by_marker, key=lambda e: e["name"].lower()):
            lines.append(
                f"- {entry['name']} {entry['version']} - `{_marker_of(entry)}`"
            )
        lines.append("")
    if excluded_by_group:
        lines.append(
            "Excluded because they belong only to optional dependency "
            "group(s) that the site build does not install (notices tooling):"
        )
        lines.append("")
        for entry in sorted(excluded_by_group, key=lambda e: e["name"].lower()):
            groups = ", ".join(entry.get("groups", []))
            lines.append(f"- {entry['name']} {entry['version']} - group(s): {groups}")
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


# --------------------------------------------------------------------------
# Self-test
# --------------------------------------------------------------------------

# (license string, expected allowed) for license_allowed(). These are the
# strings the classifier has to get right, including the substring traps that
# an `in` test over free text would wave through.
SELFTEST_CASES: list[tuple[str, bool]] = [
    # Plain classifiers and SPDX identifiers that are on the allow-list.
    ("MIT License", True),
    ("MIT", True),
    ("BSD License", True),
    ("BSD-3-Clause", True),
    ("Apache Software License", True),
    ("Apache 2.0", True),
    ("ISC License (ISCL)", True),
    ("Mozilla Public License 2.0 (MPL 2.0)", True),
    ("Python Software Foundation License", True),
    # Classifier lists: every classifier must be allowed.
    ("Apache Software License; BSD License", True),
    ("GNU General Public License v3 (GPLv3); MIT License", False),
    ("Other/Proprietary License; MIT License", False),
    ("Commercial; MIT", False),
    # SPDX expressions.
    ("MIT OR Apache-2.0", True),
    ("BSD-3-Clause AND MIT", True),
    ("MIT AND GPL-3.0-or-later", False),
    ("GPL-2.0-or-later WITH Classpath-exception", False),
    # Substring traps: "limited" contains "mit", "DISCLAIMED" contains "isc".
    ("Acme Limited Software Proprietary License", False),
    (
        "THE SOFTWARE IS PROVIDED AS IS AND ALL WARRANTIES ARE DISCLAIMED",
        False,
    ),
    # Family names are literal: only MPL-2.0 and Apache-2.0 are Tier 1.
    ("MPL-1.1", False),
    ("Mozilla Public License 1.1 (MPL 1.1)", False),
    ("Apache-1.1", False),
    ("Apache Software License 1.1", False),
    ("BSD-4-Clause", False),
    # Bare "Apache" names no version, so it is not allow-listed generically.
    ("Apache", False),
    # Unknown and empty text.
    ("UNKNOWN", False),
    ("", False),
    ("   ", False),
    ("LGPL", False),
    ("GNU Lesser General Public License v2 (LGPLv2)", False),
]

# Stand-in license texts for the classify() cases below. The Apache-2.0 sample
# reproduces the header lines the resolution table requires, with the same
# ragged indentation the real file uses, so the evidence check is exercised
# against text that needs whitespace collapsing.
SELFTEST_APACHE_TEXT = """
                                 Apache License
                           Version 2.0, January 2004
                        http://www.apache.org/licenses/

   TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION
"""

SELFTEST_APACHE_1_1_TEXT = """
                                 Apache License
                           Version 1.1, 2000
"""

SELFTEST_MIT_TEXT = """
MIT License

Permission is hereby granted, free of charge, to any person obtaining a copy
"""

# (package name, license string, exceptions table, bundled license text,
# expected classification).
SELFTEST_CLASSIFY_CASES: list[
    tuple[str, str, dict[str, dict[str, str]], str | None, str]
] = [
    ("requests", "Apache Software License", {}, SELFTEST_APACHE_TEXT, "allowed"),
    (
        "some-pkg",
        "GNU General Public License v3 (GPLv3); MIT License",
        {},
        SELFTEST_MIT_TEXT,
        "blocked",
    ),
    (
        "some-pkg",
        "GNU General Public License v3 (GPLv3); MIT License",
        {"some-pkg": {"approver": "test", "date": "2026-01-01", "reason": "test"}},
        SELFTEST_MIT_TEXT,
        "exception",
    ),
    # The recorded resolution applies only when the bundled text backs it.
    ("mkdocs-exclude", "Apache", {}, SELFTEST_APACHE_TEXT, "resolved"),
    ("mkdocs-exclude", "GPL-3.0-only", {}, SELFTEST_APACHE_TEXT, "blocked"),
    # Evidence mismatch: the text is some other license entirely.
    ("mkdocs-exclude", "Apache", {}, SELFTEST_MIT_TEXT, "blocked"),
    # Evidence mismatch: right family name, wrong version line.
    ("mkdocs-exclude", "Apache", {}, SELFTEST_APACHE_1_1_TEXT, "blocked"),
    # No bundled license text at all, in either shape pip-licenses emits.
    ("mkdocs-exclude", "Apache", {}, "", "blocked"),
    ("mkdocs-exclude", "Apache", {}, "UNKNOWN", "blocked"),
    ("mkdocs-exclude", "Apache", {}, None, "blocked"),
]

# (poetry.lock entry name, marker text, expected outcome). "hard-fail" means
# marker_matches_target() must exit rather than guess, because the marker names
# a variable TARGET_ENVIRONMENT does not model.
SELFTEST_MARKER_CASES: list[tuple[str, str, object]] = [
    # Modelled variables still decide inclusion normally.
    ("modelled-pkg", 'python_version == "3.11"', True),
    ("modelled-pkg", 'python_version < "3.12"', True),
    ("modelled-pkg", 'sys_platform == "linux"', True),
    ("modelled-pkg", 'sys_platform == "win32"', False),
    ("modelled-pkg", 'platform_system == "Darwin"', False),
    ("modelled-pkg", 'implementation_name == "pypy"', False),
    (
        "modelled-pkg",
        'sys_platform != "win32" and sys_platform != "emscripten"',
        True,
    ),
    # Unmodelled variables must hard-fail rather than evaluate against a
    # guessed patch level, architecture or interpreter build.
    ("unmodelled-pkg", 'python_full_version >= "3.11.4"', "hard-fail"),
    ("unmodelled-pkg", 'implementation_version >= "3.11.4"', "hard-fail"),
    ("unmodelled-pkg", 'platform_machine == "x86_64"', "hard-fail"),
    ("unmodelled-pkg", 'platform_release > "5.0"', "hard-fail"),
    ("unmodelled-pkg", 'platform_version != ""', "hard-fail"),
    ("unmodelled-pkg", 'extra == "docs"', "hard-fail"),
    # An unmodelled variable anywhere in the expression is enough to fail.
    (
        "unmodelled-pkg",
        'sys_platform == "linux" and platform_machine == "aarch64"',
        "hard-fail",
    ),
]


def _marker_outcome(name: str, marker_text: str) -> object:
    """Run marker_matches_target(), reporting a hard failure as "hard-fail"."""
    try:
        return marker_matches_target(name, marker_text)
    except SystemExit:
        return "hard-fail"


def selftest() -> int:
    failures = 0
    print("gen_notices --selftest: license_allowed()")
    print(f"  {'result':7} {'expected':8} license string")
    for license_str, expected in SELFTEST_CASES:
        actual = license_allowed(license_str)
        ok = actual == expected
        failures += 0 if ok else 1
        status = "ok" if ok else "FAIL"
        print(
            f"  {status:7} {'allow' if expected else 'block':8} {license_str!r}"
        )
    print("gen_notices --selftest: classify()")
    for name, license_str, exceptions, license_text, expected in SELFTEST_CLASSIFY_CASES:
        actual = classify(name, license_str, exceptions, license_text)
        ok = actual == expected
        failures += 0 if ok else 1
        status = "ok" if ok else "FAIL"
        evidence = _normalize_text(license_text or "")[:32] or "no license text"
        print(
            f"  {status:7} {expected:8} {name} {license_str!r} "
            f"[{evidence}] -> {actual}"
        )
    print("gen_notices --selftest: marker_matches_target()")
    for name, marker_text, expected in SELFTEST_MARKER_CASES:
        actual = _marker_outcome(name, marker_text)
        ok = actual == expected
        failures += 0 if ok else 1
        status = "ok" if ok else "FAIL"
        print(f"  {status:7} {str(expected):8} {marker_text!r} -> {actual}")
    total = (
        len(SELFTEST_CASES)
        + len(SELFTEST_CLASSIFY_CASES)
        + len(SELFTEST_MARKER_CASES)
    )
    if failures:
        print(f"gen_notices --selftest: {failures} of {total} case(s) FAILED")
        return 1
    print(f"gen_notices --selftest: {total} case(s) passed")
    return 0


# --------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if the committed file is out of date instead of writing it",
    )
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="run the license-classifier test cases and exit",
    )
    args = parser.parse_args()

    if args.selftest:
        return selftest()

    target, excluded_by_marker, excluded_by_group = select_target_packages()

    # Query pip-licenses for every lock entry, then partition. Asking about
    # non-target entries too is what lets the console report which of them
    # happen to be installed on this host.
    all_names = sorted({entry["name"] for entry in lock_entries()}, key=str.lower)
    reported = run_pip_licenses(all_names)

    locked_versions = {
        normalize_name(entry["name"]): entry["version"] for entry in lock_entries()
    }
    verify_no_version_drift(reported, locked_versions)
    verify_target_set_complete(reported, target)

    packages = [p for p in reported if normalize_name(p["Name"]) in target]
    outside = sorted(
        p["Name"] for p in reported if normalize_name(p["Name"]) not in target
    )

    tool_version = pip_licenses_version()
    content = render(packages, excluded_by_marker, excluded_by_group, tool_version)

    blocked = [p for p in packages if classify_package(p) == "blocked"]
    exceptions = [p for p in packages if classify_package(p) == "exception"]
    resolved = [p for p in packages if classify_package(p) == "resolved"]
    allowed = [p for p in packages if classify_package(p) == "allowed"]

    print(
        f"gen_notices: target environment {TARGET_PLATFORM} / CPython "
        f"{TARGET_PYTHON_VERSION}, pip-licenses {tool_version}"
    )
    print(
        f"gen_notices: {len(packages)} package(s) - allowed={len(allowed)}, "
        f"resolved={len(resolved)}, exception={len(exceptions)}, "
        f"blocked={len(blocked)}"
    )
    if outside:
        # Console only: whether these are installed depends on the host, and
        # the generated file must not.
        print(
            f"gen_notices: not part of the target environment, installed here "
            f"but excluded from the file: {', '.join(outside)}"
        )
    if blocked:
        print(
            f"gen_notices: BLOCKED - license(s) outside the ADR-0005 allow-list: "
            f"{[(p['Name'], p['License']) for p in blocked]}",
            file=sys.stderr,
        )
        for p in blocked:
            if normalize_name(p["Name"]) in AMBIGUOUS_LICENSE_RESOLUTIONS:
                reason = evidence_failure(p["Name"], p.get("LicenseText"))
                if reason:
                    print(f"gen_notices: BLOCKED - {reason}.", file=sys.stderr)

    if args.check:
        if not OUTPUT_FILE.exists():
            print(f"gen_notices --check: {OUTPUT_FILE} does not exist", file=sys.stderr)
            return 1
        if OUTPUT_FILE.read_text() != content:
            print(
                f"gen_notices --check: {OUTPUT_FILE} is out of date; re-run without --check",
                file=sys.stderr,
            )
            return 1
        print(f"gen_notices --check: {OUTPUT_FILE.name} is up to date")
    else:
        OUTPUT_FILE.write_text(content)
        print(f"gen_notices: wrote {OUTPUT_FILE}")

    return 1 if blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
