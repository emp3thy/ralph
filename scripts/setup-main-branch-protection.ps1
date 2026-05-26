# Add branch protection to `main` requiring the `refuse` status check.
#
# The `refuse` check is the workflow in .github/workflows/refuse-queue-merge.yml
# which fails any PR whose head branch is `ralph-queue`. Marking it required
# here means GitHub disables the merge button on such PRs.
#
# Idempotent: re-running this overwrites the protection with the same shape,
# so it is safe as a recurring setup script.
#
# Requires: a `gh` CLI logged in with `repo` + `admin:repo` scopes.

$ErrorActionPreference = "Stop"

$body = '{"required_status_checks":{"strict":false,"contexts":["refuse"]},"enforce_admins":false,"required_pull_request_reviews":null,"restrictions":null}'

$tempFile = Join-Path $env:TEMP "ralph-main-protection.json"
try {
    # WPS 5.1 quirk: Set-Content -Encoding utf8 writes UTF-8 WITH BOM,
    # which gh api's JSON parser rejects with a 400. Write a BOM-less
    # UTF-8 file via .NET directly.
    $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::WriteAllText($tempFile, $body, $utf8NoBom)

    gh api -X PUT repos/emp3thy/ralph/branches/main/protection --input $tempFile
    if ($LASTEXITCODE -ne 0) {
        throw "gh api failed with exit code $LASTEXITCODE"
    }
} finally {
    if (Test-Path $tempFile) {
        Remove-Item $tempFile -Force
    }
}

Write-Output "main branch protection updated - required check: refuse"
