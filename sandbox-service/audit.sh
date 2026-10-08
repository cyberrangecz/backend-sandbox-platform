#!/usr/bin/env bash
# Dependency audit of the locked set the sandbox-service image ships. Run it as the
# `audit` tox env (`tox -e audit`), which CI does too. sandbox-service depends on every
# library, so this one audit covers the whole workspace.
#
# What fails depends on BASE:
#   unset       Every known vulnerability fails. Local runs, the nightly scheduled run
#               and the master (release) run. The master run still builds the image, but
#               tags it v<version>-staging instead of releasing it.
#   BASE=<rev>  Only vulnerabilities not already present at <rev> fail; the rest are
#               inherited, listed but left to their own PR to master. CI sets this for
#               pull requests and branch pushes.
# Findings are compared as package + advisory id. A change that touches uv.lock for any
# other reason is not blocked by what master already carries; one that adds a dependency,
# or moves one, into a known vulnerability is.
#
# Accepted vulnerabilities are listed in audit-ignore.txt and ignored on both sides.
set -uo pipefail

here=$(cd "$(dirname "$0")" && pwd)
root=$(git -C "$here" rev-parse --show-toplevel)
tmp=$(mktemp -d "${RUNNER_TEMP:-/tmp}/audit.XXXXXX")
summary=${GITHUB_STEP_SUMMARY:-/dev/null}

# shellcheck disable=SC2329  # invoked by the EXIT trap below
cleanup() {
    if [ -d "$tmp/base" ]; then
        git -C "$root" worktree remove --force "$tmp/base"
    fi
    rm -rf "$tmp"
}
trap cleanup EXIT

annotate() { # <error|warning> <message>
    if [ -n "${GITHUB_ACTIONS:-}" ]; then
        echo "::$1 title=Dependency audit::$2"
    else
        echo "$1: $2"
    fi
}

ignore_args=()
while read -r id; do
    ignore_args+=(--ignore-vuln "$id")
done < <(sed 's/#.*//' "$here/audit-ignore.txt" | awk 'NF { print $1 }')

# The runtime set the Dockerfile installs: --no-default-groups drops the `dev` type stubs,
# and --no-emit-workspace drops the crczp-* members, whose paths uv writes relative to the
# workspace root. Auditing this export rather than `pip-audit .` matters: pip knows
# nothing of [tool.uv.sources] or uv.lock, so it would audit published sibling wheels.
export_set() { # <sandbox-service dir> <--locked|--frozen> <output>
    (cd "$1" && uv export "$2" --no-default-groups --no-emit-workspace --no-hashes \
        --no-annotate --no-header -o "$3" >/dev/null)
}

# pip-audit exits 1 both for "vulnerabilities found" and for real failures, so the
# report file, not the exit code, says whether it ran.
audit() { # <requirements> <json output>
    pip-audit --progress-spinner=off --no-deps --requirement "$1" \
        --format json --output "$2" "${ignore_args[@]}"
    if [ ! -s "$2" ]; then
        annotate error "pip-audit produced no report for $1"
        exit 2
    fi
    # The script runs without errexit, so a truncated or reshaped report would otherwise
    # parse as "no vulnerabilities" and pass the gate. Accept it only if it lists the
    # audited packages (never none: the set has ~130) and every finding has an id.
    if ! jq -e '(.dependencies | type == "array" and length > 0)
            and all(.dependencies[]; (.name | type == "string")
                and ((.vulns // []) | type == "array")
                and all((.vulns // [])[]; .id | type == "string"))' "$2" >/dev/null; then
        annotate error "pip-audit report for $1 is not in the expected format"
        exit 2
    fi
}

pairs() { # <json> <output>: "package advisory" per line; any failure stops the audit
    if ! jq -r '.dependencies[] | .name as $n | (.vulns // [])[] | "\($n) \(.id)"' "$1" |
        sort -u > "$2"; then
        annotate error "could not read the findings in $1"
        exit 2
    fi
}

table() { # <pairs file>
    echo '| Package | Version | Advisory | Fixed in |'
    echo '|---|---|---|---|'
    jq -r '.dependencies[] | .name as $n | .version as $v | (.vulns // [])[]
        | [$n, $v, .id, (.fix_versions // [] | if . == [] then "no fix yet" else join(", ") end)]
        | @tsv' "$tmp/head.json" | sort -u |
        awk -F'\t' 'NR == FNR { want[$0]; next }
            ($1 " " $3) in want { printf "| %s | %s | %s | %s |\n", $1, $2, $3, $4 }' "$1" -
}

if ! export_set "$here" --locked "$tmp/head.txt"; then
    annotate error 'uv export failed: is uv.lock up to date? Run: uv lock'
    exit 2
fi
audit "$tmp/head.txt" "$tmp/head.json"
pairs "$tmp/head.json" "$tmp/head.pairs"

if [ -z "${BASE:-}" ]; then
    cp "$tmp/head.pairs" "$tmp/new"
    : > "$tmp/inherited"
else
    git -C "$root" worktree add --detach --quiet "$tmp/base" "$BASE" || exit 2
    # --frozen: the base lock is audited as it is, not re-validated by today's uv.
    export_set "$tmp/base/sandbox-service" --frozen "$tmp/base.txt" || exit 2
    if cmp -s "$tmp/head.txt" "$tmp/base.txt"; then
        # Same locked set as the base, so nothing can be new: skip the second audit.
        cp "$tmp/head.pairs" "$tmp/base.pairs"
    else
        audit "$tmp/base.txt" "$tmp/base.json"
        pairs "$tmp/base.json" "$tmp/base.pairs"
    fi
    comm -23 "$tmp/head.pairs" "$tmp/base.pairs" > "$tmp/new"
    comm -12 "$tmp/head.pairs" "$tmp/base.pairs" > "$tmp/inherited"
fi

{
    echo '### Dependency audit'
    echo
    if [ ! -s "$tmp/head.pairs" ]; then
        echo 'No known vulnerabilities.'
    fi
    if [ -s "$tmp/new" ]; then
        if [ -n "${BASE:-}" ]; then
            echo "**Blocking**: not present at the base (${BASE:0:12}), so introduced by this change."
        else
            # shellcheck disable=SC2016  # a markdown code span, not a command substitution
            echo '**Failing**: with no base to compare with, every known vulnerability fails the audit. On the master run the image is still built, but tagged `v<version>-staging` instead of released.'
        fi
        echo
        table "$tmp/new"
        echo
    fi
    if [ -s "$tmp/inherited" ]; then
        echo "**Not blocking**: already present at the base (${BASE:0:12}). Fix them in their own PR to master."
        echo
        table "$tmp/inherited"
        echo
    fi
} | tee -a "$summary"

if [ -s "$tmp/new" ]; then
    annotate error "$(wc -l < "$tmp/new") known vulnerabilities fail the audit; see the table above."
    exit 1
fi
if [ -s "$tmp/inherited" ]; then
    annotate warning "$(wc -l < "$tmp/inherited") inherited vulnerabilities, not blocking. Fix them in a separate PR to master."
fi
exit 0
