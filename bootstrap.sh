#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
target_home="${HOME}"
target_home_overridden=false

usage() {
  printf 'Usage: %s [--target-home PATH]\n' "$0"
}

while (($# > 0)); do
  case "$1" in
    --target-home)
      if (($# < 2)); then
        printf '%s\n' 'bootstrap.sh: --target-home requires a path' >&2
        exit 2
      fi
      target_home="$2"
      target_home_overridden=true
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'bootstrap.sh: unknown argument: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ ! -d "$target_home" ]]; then
  printf 'bootstrap.sh: target home is not a directory: %s\n' "$target_home" >&2
  exit 1
fi

target_home="$(cd -- "$target_home" && pwd -P)"
skills_source="$repo_root/user-skills"
git_hooks_source="$repo_root/git-hooks"
skills_link="$target_home/.agents/skills"
target_codex="$target_home/.codex"

if [[ ! -d "$skills_source" ]]; then
  printf 'bootstrap.sh: managed skills directory is missing: %s\n' "$skills_source" >&2
  exit 1
fi

if [[ ! -d "$git_hooks_source" ]]; then
  printf 'bootstrap.sh: managed Git hooks directory is missing: %s\n' "$git_hooks_source" >&2
  exit 1
fi

if ! command -v git >/dev/null 2>&1; then
  printf '%s\n' 'bootstrap.sh: git is required' >&2
  exit 1
fi

git_global_config() {
  if [[ "$target_home_overridden" == true ]]; then
    env -u GIT_CONFIG_GLOBAL HOME="$target_home" XDG_CONFIG_HOME="$target_home/.config" git config --global "$@"
  else
    git config --global "$@"
  fi
}

current_hooks_path=""
if current_hooks_path="$(git_global_config --get-all core.hooksPath)"; then
  if [[ "$current_hooks_path" != "$git_hooks_source" ]]; then
    printf 'bootstrap.sh: existing core.hooksPath differs: %s\n' "$current_hooks_path" >&2
    printf 'bootstrap.sh: expected core.hooksPath: %s\n' "$git_hooks_source" >&2
    exit 1
  fi
else
  git_config_status=$?
  if [[ "$git_config_status" -ne 1 ]]; then
    printf 'bootstrap.sh: failed to read global core.hooksPath (exit %s)\n' "$git_config_status" >&2
    exit 1
  fi
fi

skills_link_matches=false
if [[ -L "$skills_link" ]] && [[ "$(readlink -f -- "$skills_link")" == "$skills_source" ]]; then
  skills_link_matches=true
fi

umask 077

if [[ "$skills_link_matches" == false ]]; then
  if [[ -e "$skills_link" || -L "$skills_link" ]]; then
    timestamp="$(date '+%Y%m%d-%H%M%S')"
    backup_root="$target_codex/backups/codex-bootstrap-$timestamp-$$"
    mkdir -p "$backup_root/.agents"
    mv -- "$skills_link" "$backup_root/.agents/skills"
    printf 'bootstrap.sh: existing skills path was backed up to %s\n' "$backup_root/.agents/skills"
  fi

  mkdir -p "$target_home/.agents"
  ln -s "$skills_source" "$skills_link"
fi

git_global_config core.hooksPath "$git_hooks_source"

printf 'bootstrap.sh: user skills: %s -> %s\n' "$skills_link" "$skills_source"
printf 'bootstrap.sh: core.hooksPath: %s\n' "$git_hooks_source"
