#!/usr/bin/env bash
# Bash twin of update-all.ps1 (same steps, same rules) for WSL / Linux / macOS.
#
# Usage:
#   ./update-all.sh /mnt/d/Users/Siegried/Desktop/Becode        # update code + rebuild images
#   ./update-all.sh /mnt/d/Users/Siegried/Desktop/Becode --run  # also `docker compose up -d`
#
# Why it is built this way:
# - Repos are found recursively (depth 4 for .git = repo at depth 3).
# - A repo with uncommitted changes is SKIPPED, never touched.
# - Backup = `git bundle` (all branches + history, small). Restore: git clone X.bundle
# - `pull --ff-only`: no hidden merge commit if local main diverged.
# - Images are only BUILT by default: most apps listen on 8501, starting all
#   of them at once would make all but one fail.
# - core.filemode=false: on a Windows drive (/mnt/c, /mnt/d) WSL sees every
#   file as "chmod changed", which would make every repo look dirty.
set -uo pipefail   # no -e: one broken repo must not stop the others

ROOT="${1:?usage: $0 <folder-with-repos> [--run]}"
RUN="${2:-}"
BACKUP_DIR="$ROOT/_backups"
STAMP="$(date +%Y%m%d_%H%M)"
REPORT=()
mkdir -p "$BACKUP_DIR"

g() { git -c core.filemode=false -C "$repo" "$@"; }   # git on the current repo

while IFS= read -r gitdir; do
  repo="$(dirname "$gitdir")"
  name="$(basename "$repo")"
  echo -e "\n\e[36m=== $name ===\e[0m"

  # 1. Skip repos with local, uncommitted work
  if [[ -n "$(g status --porcelain)" ]]; then
    echo -e "\e[33mUncommitted changes -> skipped (commit or stash first)\e[0m"
    REPORT+=("$name|SKIPPED (dirty)|-"); continue
  fi

  # 2. Backup: full history in one small file
  g bundle create "$BACKUP_DIR/${name}_$STAMP.bundle" --all 2>/dev/null

  # 3. Default branch as GitHub defines it (main or master), main as fallback
  g fetch origin --prune 2>/dev/null
  branch="$(g symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null)"
  branch="${branch#origin/}"; branch="${branch:-main}"
  g checkout -q "$branch" 2>/dev/null
  if g pull --ff-only origin "$branch"; then git_status="OK ($branch)"
  else git_status="PULL FAILED ($branch diverged?)"; fi

  # 4. Docker: compose file first (any standard name), plain Dockerfile otherwise
  compose=""
  for f in compose.yaml compose.yml docker-compose.yml docker-compose.yaml; do
    [[ -f "$repo/$f" ]] && { compose="$repo/$f"; break; }
  done
  if [[ -n "$compose" ]]; then
    if docker compose -f "$compose" build; then
      docker_status="built (compose)"
      [[ "$RUN" == "--run" ]] && docker compose -f "$compose" up -d
    else docker_status="BUILD FAILED"; fi
  elif [[ -f "$repo/Dockerfile" ]]; then
    if docker build -t "${name,,}" "$repo"; then docker_status="built (Dockerfile)"   # image name = repo name, lowercase
    else docker_status="BUILD FAILED"; fi
  else
    docker_status="no Dockerfile"
  fi
  REPORT+=("$name|$git_status|$docker_status")
done < <(find "$ROOT" -maxdepth 4 -type d -name .git -not -path "$BACKUP_DIR/*" -prune)

# Old image layers replaced by the rebuilds
docker image prune -f >/dev/null

echo -e "\n\e[32m===== SUMMARY =====\e[0m"
# printf instead of `column`: not installed on every distro
for row in "REPO|GIT|DOCKER" "${REPORT[@]}"; do
  IFS='|' read -r a b c <<< "$row"; printf '%-30s %-32s %s\n' "$a" "$b" "$c"
done
