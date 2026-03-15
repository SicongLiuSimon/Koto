<#
.SYNOPSIS
    Creates a "main-protection" repository ruleset for the Koto repo using the GitHub CLI.

.DESCRIPTION
    Uses the GitHub REST API (POST /repos/{owner}/{repo}/rulesets) to create an active
    ruleset that protects the default branch (main) with the following rules:
      - Required linear history (squash/rebase only)
      - Block force push (non-fast-forward)
      - Block branch deletion
      - Required status checks (lint + test, branch must be up to date)
      - Pull request required (1 approving review minimum)
    Repository admins (role_id 5) can bypass in emergencies.

.PARAMETER Owner
    GitHub repository owner. Defaults to "Loganwon".

.PARAMETER Repo
    GitHub repository name. Defaults to "Koto".

.PARAMETER DryRun
    If set, prints the JSON payload without making the API call.

.EXAMPLE
    .\setup_github_rules.ps1
    .\setup_github_rules.ps1 -Owner myorg -Repo myrepo
    .\setup_github_rules.ps1 -DryRun
#>

[CmdletBinding()]
param(
    [string]$Owner = "Loganwon",
    [string]$Repo  = "Koto",
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── 1. Verify gh CLI is installed and authenticated ──────────────────────────

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    Write-Error "GitHub CLI (gh) is not installed. Install it from https://cli.github.com/"
    exit 1
}

$authStatus = gh auth status 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Error "GitHub CLI is not authenticated. Run 'gh auth login' first.`n$authStatus"
    exit 1
}

Write-Host "✓ gh CLI is installed and authenticated." -ForegroundColor Green

# ── 2. Build the ruleset JSON payload ────────────────────────────────────────

# Each rule object maps to a specific branch protection behaviour.
$rulesetBody = @{
    # Human-readable name shown in the repo Settings → Rules → Rulesets tab
    name        = "main-protection"

    # "branch" means this ruleset applies to branches (vs. "tag")
    target      = "branch"

    # "active" enforces immediately; use "evaluate" for audit-only mode
    enforcement = "active"

    # Which branches this ruleset applies to
    conditions  = @{
        ref_name = @{
            include = @("refs/heads/main")
            exclude = @()
        }
    }

    # The protection rules themselves
    rules = @(
        # Prevent branch deletion
        @{ type = "deletion" }

        # Block force pushes (non-fast-forward pushes)
        @{ type = "non_fast_forward" }

        # Enforce linear history — only squash or rebase merges allowed
        @{ type = "required_linear_history" }

        # Require a pull request with at least 1 approving review
        @{
            type       = "pull_request"
            parameters = @{
                required_approving_review_count  = 1
                dismiss_stale_reviews_on_push    = $true
                require_code_owner_review        = $true
                require_last_push_approval       = $false
                required_review_thread_resolution = $true
            }
        }

        # Require lint and test CI jobs to pass; branch must be up to date
        @{
            type       = "required_status_checks"
            parameters = @{
                strict_required_status_checks_policy = $true
                required_status_checks               = @(
                    @{ context = "lint" }
                    @{ context = "test" }
                )
            }
        }
    )

    # Allow repository admins (role_id 5) to bypass rules in emergencies
    bypass_actors = @(
        @{
            actor_id    = 5
            actor_type  = "RepositoryRole"
            bypass_mode = "always"
        }
    )
} | ConvertTo-Json -Depth 10

# ── 3. Dry-run or execute ────────────────────────────────────────────────────

if ($DryRun) {
    Write-Host "`n── Dry Run: JSON payload that would be sent ──" -ForegroundColor Yellow
    Write-Host $rulesetBody
    Write-Host "`nTarget: POST /repos/$Owner/$Repo/rulesets" -ForegroundColor Yellow
    exit 0
}

# ── 4. Create the ruleset via the GitHub REST API ────────────────────────────

Write-Host "`nCreating ruleset 'main-protection' for $Owner/$Repo ..." -ForegroundColor Cyan

try {
    $response = $rulesetBody | gh api "repos/$Owner/$Repo/rulesets" `
        --method POST `
        --input - 2>&1

    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to create ruleset.`n$response"
        exit 1
    }

    # Parse the response to extract the ruleset ID and build the URL
    $parsed   = $response | ConvertFrom-Json
    $rulesetId = $parsed.id
    $url       = "https://github.com/$Owner/$Repo/rules/$rulesetId"

    Write-Host "✓ Ruleset 'main-protection' created successfully!" -ForegroundColor Green
    Write-Host "  Ruleset URL: $url" -ForegroundColor Cyan
}
catch {
    Write-Error "An error occurred while creating the ruleset: $_"
    exit 1
}
