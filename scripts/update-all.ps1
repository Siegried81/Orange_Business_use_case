<#
.SYNOPSIS
  Bring every local clone under a folder up to date with its GitHub default
  branch, then rebuild the Docker images of the repos that have one.

.EXAMPLE
  # Update code + rebuild images (nothing is started: avoids port clashes)
  .\update-all.ps1 -Root "D:\Users\Siegried\Desktop\Becode"

  # Same, and also (re)start every compose project in the background
  .\update-all.ps1 -Root "D:\Users\Siegried\Desktop\Becode" -Run

  # Skip every path matching a regex (e.g. old copies of repos)
  .\update-all.ps1 -Root "D:\Users\Siegried\Desktop\Becode" -Exclude "backup|\\old\\"

.NOTES
  Why it is built this way:
  - Repos are found recursively (depth 3), so nested folders like
    Becode\projects\my-repo are covered too.
  - A repo with uncommitted changes to TRACKED files is SKIPPED, never
    touched: a checkout or pull there could mix your local work with main.
    Untracked files (.venv, data, notebooks...) do not block a pull, so they
    do not count.
  - Docker is checked once up front: if it is not running, git is still
    updated and the builds are skipped instead of failing repo after repo.
  - The backup is a `git bundle` (all branches + history, a few MB), not a zip:
    a zip would also copy .venv / node_modules and take minutes per repo.
    Restore with: git clone <file>.bundle restored-repo
  - `git pull --ff-only` refuses to create merge commits: if local main has
    diverged from GitHub, the repo is reported instead of silently merged.
  - Images are only BUILT by default. Most of these apps listen on 8501, so
    starting them all at once would make every compose project but one fail.
#>
param(
    [Parameter(Mandatory = $true)][string]$Root,   # folder holding the clones
    [switch]$Run,                                   # also `docker compose up -d`
    [string]$Exclude = ""                           # regex of paths to skip
)

$ErrorActionPreference = "Continue"
$backupDir = Join-Path $Root "_backups"
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmm"
$report = @()

# Docker reachable? (Docker Desktop must be started)
docker info *> $null
$dockerOk = ($LASTEXITCODE -eq 0)
if (-not $dockerOk) { Write-Host "Docker unreachable -> git only, builds skipped" -ForegroundColor Yellow }

# Every folder that contains a .git directory is a repo
$repos = Get-ChildItem $Root -Directory -Recurse -Depth 3 -Force -Filter ".git" -ErrorAction SilentlyContinue |
         ForEach-Object { $_.Parent } |
         Where-Object { $_.FullName -notlike "$backupDir*" } |
         Where-Object { -not $Exclude -or $_.FullName -notmatch $Exclude }

foreach ($r in $repos) {
    $repo = $r.FullName
    $name = $r.Name
    Write-Host "`n=== $name ===" -ForegroundColor Cyan

    # 1. Skip repos with local, uncommitted work
    if (git -C $repo status --porcelain --untracked-files=no) {
        Write-Host "Uncommitted changes -> skipped (commit or stash first)" -ForegroundColor Yellow
        $report += [pscustomobject]@{ Repo = $name; Git = "SKIPPED (dirty)"; Docker = "-" }
        continue
    }

    # 2. Backup: full history in one small file
    git -C $repo bundle create (Join-Path $backupDir "${name}_$stamp.bundle") --all 2>$null

    # 3. Default branch as GitHub defines it (main or master), main as fallback
    git -C $repo fetch origin --prune 2>$null
    $branch = (git -C $repo symbolic-ref --short refs/remotes/origin/HEAD 2>$null) -replace '^origin/', ''
    if (-not $branch) { $branch = "main" }

    git -C $repo checkout $branch 2>$null
    git -C $repo pull --ff-only origin $branch
    $gitStatus = if ($LASTEXITCODE -eq 0) { "OK ($branch)" } else { "PULL FAILED ($branch diverged?)" }

    # 4. Docker: compose file first (any of the standard names), plain Dockerfile otherwise
    $compose = @("compose.yaml", "compose.yml", "docker-compose.yml", "docker-compose.yaml") |
               ForEach-Object { Join-Path $repo $_ } | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $dockerOk -and ($compose -or (Test-Path (Join-Path $repo "Dockerfile")))) {
        $dockerStatus = "skipped (Docker off)"
    } elseif ($compose) {
        docker compose -f $compose build
        if ($Run -and $LASTEXITCODE -eq 0) { docker compose -f $compose up -d }
        $dockerStatus = if ($LASTEXITCODE -eq 0) { "built (compose)" } else { "BUILD FAILED" }
    } elseif (Test-Path (Join-Path $repo "Dockerfile")) {
        docker build -t $name.ToLower() $repo     # image name = repo name
        $dockerStatus = if ($LASTEXITCODE -eq 0) { "built (Dockerfile)" } else { "BUILD FAILED" }
    } else {
        $dockerStatus = "no Dockerfile"
    }
    $report += [pscustomobject]@{ Repo = $name; Git = $gitStatus; Docker = $dockerStatus }
}

# Old image layers replaced by the rebuilds
if ($dockerOk) { docker image prune -f | Out-Null }

Write-Host "`n===== SUMMARY =====" -ForegroundColor Green
$report | Format-Table -AutoSize
