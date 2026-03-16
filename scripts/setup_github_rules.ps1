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

$ErrorActionPreference = "Continue"

# ── 1. Verify gh CLI is installed and authenticated ──────────────────────────

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    Write-Error "GitHub CLI (gh) is not installed. Install it from https://cli.github.com/"
    exit 1
}

$null = gh auth status 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Error "GitHub CLI is not authenticated. Run 'gh auth login' first."
    exit 1
}

Write-Host "gh CLI is installed and authenticated." -ForegroundColor Green

# ── 2. Build the ruleset JSON payload ────────────────────────────────────────

$rulesetBody = @'
{
  "name": "main-protection",
  "target": "branch",
  "enforcement": "active",
  "conditions": {
    "ref_name": {
      "include": ["refs/heads/main"],
      "exclude": []
    }
  },
  "rules": [
    {"type": "deletion"},
    {"type": "non_fast_forward"},
    {"type": "required_linear_history"},
    {
      "type": "pull_request",
      "parameters": {
        "required_approving_review_count": 1,
        "dismiss_stale_reviews_on_push": true,
        "require_code_owner_review": true,
        "require_last_push_approval": false,
        "required_review_thread_resolution": true
      }
    },
    {
      "type": "required_status_checks",
      "parameters": {
        "strict_required_status_checks_policy": true,
        "required_status_checks": [
          {"context": "lint"},
          {"context": "test"}
        ]
      }
    }
  ],
  "bypass_actors": [
    {
      "actor_id": 5,
      "actor_type": "RepositoryRole",
      "bypass_mode": "always"
    }
  ]
}
'@

# ── 3. Dry-run or execute ────────────────────────────────────────────────────

if ($DryRun) {
    Write-Host "`n-- Dry Run: JSON payload that would be sent --" -ForegroundColor Yellow
    Write-Host $rulesetBody
    Write-Host "`nTarget: POST /repos/$Owner/$Repo/rulesets" -ForegroundColor Yellow
    exit 0
}

# ── 4. Create the ruleset via the GitHub REST API ────────────────────────────

Write-Host "`nCreating ruleset 'main-protection' for $Owner/$Repo ..." -ForegroundColor Cyan

$response = $rulesetBody | gh api "repos/$Owner/$Repo/rulesets" --method POST --input - 2>&1

if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to create ruleset: $response"
    exit 1
}

$parsed = $response | ConvertFrom-Json
$rulesetId = $parsed.id
$url = "https://github.com/$Owner/$Repo/rules/$rulesetId"

Write-Host "Ruleset 'main-protection' created successfully!" -ForegroundColor Green
Write-Host "  Ruleset URL: $url" -ForegroundColor Cyan
