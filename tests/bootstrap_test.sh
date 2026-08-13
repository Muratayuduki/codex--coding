#!/usr/bin/env bash

set -euo pipefail

bootstrap_under_test="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)/bootstrap.sh"
source_repo_root="$(dirname -- "$bootstrap_under_test")"
temporary_root="$(mktemp -d)"

cleanup() {
  rm -rf -- "$temporary_root"
}

trap cleanup EXIT

fail() {
  printf 'bootstrap_test.sh: %s\n' "$1" >&2
  exit 1
}

assert_equal() {
  local expected="$1"
  local actual="$2"
  local message="$3"

  if [[ "$actual" != "$expected" ]]; then
    printf 'bootstrap_test.sh: %s\nexpected: %s\nactual:   %s\n' "$message" "$expected" "$actual" >&2
    exit 1
  fi
}

new_fixture() {
  local fixture_root

  fixture_root="$(mktemp -d "$temporary_root/fixture.XXXXXX")"
  mkdir -p "$fixture_root/repo/user-skills"
  mkdir -p "$fixture_root/repo/git-hooks"
  mkdir -p "$fixture_root/repo/hooks"
  mkdir -p "$fixture_root/home"
  cp "$bootstrap_under_test" "$fixture_root/repo/bootstrap.sh"
  chmod 755 "$fixture_root/repo/bootstrap.sh"
  cp "$source_repo_root/git-hooks/pre-commit" "$fixture_root/repo/git-hooks/pre-commit"
  cp "$source_repo_root/hooks/codex_env_guard.py" "$fixture_root/repo/hooks/codex_env_guard.py"
  chmod 755 "$fixture_root/repo/git-hooks/pre-commit"
  printf '%s\n' 'fixture skill' > "$fixture_root/repo/user-skills/fixture.txt"
  printf '%s\n' "$fixture_root"
}

read_global_hooks_path() {
  local target_home="$1"

  env -u GIT_CONFIG_GLOBAL HOME="$target_home" XDG_CONFIG_HOME="$target_home/.config" git config --global --get core.hooksPath
}

assert_skills_link() {
  local target_home="$1"
  local expected_target="$2"
  local link_path="$target_home/.agents/skills"

  [[ -L "$link_path" ]] || fail "$link_path is not a symbolic link"
  assert_equal "$expected_target" "$(readlink "$link_path")" "skills link target differs"
}

test_fresh_install() {
  local fixture_root
  local expected_hooks_path

  fixture_root="$(new_fixture)"
  expected_hooks_path="$fixture_root/repo/git-hooks"

  "$fixture_root/repo/bootstrap.sh" --target-home "$fixture_root/home"

  assert_skills_link "$fixture_root/home" "$fixture_root/repo/user-skills"
  assert_equal "$expected_hooks_path" "$(read_global_hooks_path "$fixture_root/home")" "core.hooksPath differs"
}

test_installed_hook_runs_outside_codex_home() {
  local fixture_root
  local worktree

  fixture_root="$(new_fixture)"
  worktree="$fixture_root/worktree"
  mkdir -p "$worktree"
  "$fixture_root/repo/bootstrap.sh" --target-home "$fixture_root/home"
  git -C "$worktree" init --quiet
  printf '%s\n' 'tracked' > "$worktree/tracked.txt"
  git -C "$worktree" add tracked.txt

  env -u GIT_CONFIG_GLOBAL HOME="$fixture_root/home" XDG_CONFIG_HOME="$fixture_root/home/.config" git -C "$worktree" -c user.name=Test -c user.email=test@example.com commit --quiet -m test
}

test_idempotent_install() {
  local fixture_root
  local backup_count

  fixture_root="$(new_fixture)"

  "$fixture_root/repo/bootstrap.sh" --target-home "$fixture_root/home"
  "$fixture_root/repo/bootstrap.sh" --target-home "$fixture_root/home"

  assert_skills_link "$fixture_root/home" "$fixture_root/repo/user-skills"
  backup_count='0'
  if [[ -d "$fixture_root/home/.codex/backups" ]]; then
    backup_count="$(find "$fixture_root/home/.codex/backups" -mindepth 1 -maxdepth 1 -type d | wc -l)"
  fi
  assert_equal '0' "$backup_count" 'idempotent install created a backup'
}

test_existing_directory_is_backed_up() {
  local fixture_root
  local backup_marker

  fixture_root="$(new_fixture)"
  mkdir -p "$fixture_root/home/.agents/skills"
  printf '%s\n' 'keep me' > "$fixture_root/home/.agents/skills/original.txt"

  "$fixture_root/repo/bootstrap.sh" --target-home "$fixture_root/home"

  assert_skills_link "$fixture_root/home" "$fixture_root/repo/user-skills"
  backup_marker="$(find "$fixture_root/home/.codex/backups" -path '*/.agents/skills/original.txt' -print -quit)"
  [[ -n "$backup_marker" ]] || fail 'existing skills directory was not backed up'
  assert_equal 'keep me' "$(<"$backup_marker")" 'backup contents differ'
}

test_existing_symlink_is_backed_up() {
  local fixture_root
  local backup_link
  local old_target

  fixture_root="$(new_fixture)"
  old_target="$fixture_root/old-skills"
  mkdir -p "$fixture_root/home/.agents"
  mkdir -p "$old_target"
  ln -s "$old_target" "$fixture_root/home/.agents/skills"

  "$fixture_root/repo/bootstrap.sh" --target-home "$fixture_root/home"

  assert_skills_link "$fixture_root/home" "$fixture_root/repo/user-skills"
  backup_link="$(find "$fixture_root/home/.codex/backups" -path '*/.agents/skills' -type l -print -quit)"
  [[ -n "$backup_link" ]] || fail 'existing skills symlink was not backed up'
  assert_equal "$old_target" "$(readlink "$backup_link")" 'backed-up symlink target differs'
}

test_different_hooks_path_fails_before_mutation() {
  local fixture_root
  local command_output
  local command_status
  local old_hooks_path

  fixture_root="$(new_fixture)"
  old_hooks_path="$fixture_root/home/existing-hooks"
  mkdir -p "$fixture_root/home/.agents/skills"
  printf '%s\n' 'keep me' > "$fixture_root/home/.agents/skills/original.txt"
  env -u GIT_CONFIG_GLOBAL HOME="$fixture_root/home" XDG_CONFIG_HOME="$fixture_root/home/.config" git config --global core.hooksPath "$old_hooks_path"

  set +e
  command_output="$("$fixture_root/repo/bootstrap.sh" --target-home "$fixture_root/home" 2>&1)"
  command_status=$?
  set -e

  [[ "$command_status" -ne 0 ]] || fail 'different core.hooksPath was overwritten'
  [[ "$command_output" == *'existing core.hooksPath differs'* ]] || fail 'failure did not explain the core.hooksPath conflict'
  [[ -d "$fixture_root/home/.agents/skills" ]] || fail 'skills directory changed before conflict failure'
  [[ ! -L "$fixture_root/home/.agents/skills" ]] || fail 'skills directory became a symlink before conflict failure'
  assert_equal 'keep me' "$(<"$fixture_root/home/.agents/skills/original.txt")" 'existing skills contents changed'
  assert_equal "$old_hooks_path" "$(read_global_hooks_path "$fixture_root/home")" 'existing core.hooksPath changed'
}

test_target_home_isolates_xdg_config() {
  local fixture_root
  local external_hooks_path
  local external_xdg_config

  fixture_root="$(new_fixture)"
  external_hooks_path="$fixture_root/external-hooks"
  external_xdg_config="$fixture_root/external-xdg"
  mkdir -p "$fixture_root/external-home"
  mkdir -p "$external_xdg_config/git"
  git config --file "$external_xdg_config/git/config" core.hooksPath "$external_hooks_path"

  env -u GIT_CONFIG_GLOBAL XDG_CONFIG_HOME="$external_xdg_config" "$fixture_root/repo/bootstrap.sh" --target-home "$fixture_root/home"

  assert_equal "$fixture_root/repo/git-hooks" "$(read_global_hooks_path "$fixture_root/home")" 'target home core.hooksPath differs'
  assert_equal "$external_hooks_path" "$(git config --file "$external_xdg_config/git/config" --get core.hooksPath)" 'external XDG config changed'
}

test_target_home_isolates_git_config_global() {
  local fixture_root
  local external_config
  local external_hooks_path

  fixture_root="$(new_fixture)"
  external_config="$fixture_root/external.gitconfig"
  external_hooks_path="$fixture_root/external-hooks"
  git config --file "$external_config" core.hooksPath "$external_hooks_path"

  GIT_CONFIG_GLOBAL="$external_config" "$fixture_root/repo/bootstrap.sh" --target-home "$fixture_root/home"

  assert_equal "$fixture_root/repo/git-hooks" "$(read_global_hooks_path "$fixture_root/home")" 'target home core.hooksPath differs'
  assert_equal "$external_hooks_path" "$(git config --file "$external_config" --get core.hooksPath)" 'external GIT_CONFIG_GLOBAL changed'
}

test_leading_hyphen_target_home() {
  local fixture_root
  local target_home

  fixture_root="$(new_fixture)"
  target_home="$fixture_root/-home"
  mv "$fixture_root/home" "$target_home"

  (
    cd -- "$fixture_root"
    "$fixture_root/repo/bootstrap.sh" --target-home '-home'
  )

  assert_skills_link "$target_home" "$fixture_root/repo/user-skills"
  assert_equal "$fixture_root/repo/git-hooks" "$(read_global_hooks_path "$target_home")" 'leading-hyphen target home core.hooksPath differs'
}

test_invalid_arguments_fail() {
  local fixture_root

  fixture_root="$(new_fixture)"

  if "$fixture_root/repo/bootstrap.sh" --target-home >/dev/null 2>&1; then
    fail 'missing --target-home value succeeded'
  fi
  if "$fixture_root/repo/bootstrap.sh" --unknown >/dev/null 2>&1; then
    fail 'unknown argument succeeded'
  fi
}

if [[ ! -f "$bootstrap_under_test" ]]; then
  fail "bootstrap script is missing: $bootstrap_under_test"
fi

test_fresh_install
test_installed_hook_runs_outside_codex_home
test_idempotent_install
test_existing_directory_is_backed_up
test_existing_symlink_is_backed_up
test_different_hooks_path_fails_before_mutation
test_target_home_isolates_xdg_config
test_target_home_isolates_git_config_global
test_leading_hyphen_target_home
test_invalid_arguments_fail

printf '%s\n' 'bootstrap_test.sh: all tests passed'
