#!/usr/bin/env bash
# Create the `comfy` user/group at a specific uid/gid (usually the host user's, so bind-mounted
# models/input/output stay writable). The base image sometimes already has an account at that id (its own default
# non-root user, a conda account, etc.) — groupadd/useradd then refuse with "already exists". Instead of failing,
# rename that existing account to `comfy` so the requested id is still the one we end up with.
set -euo pipefail
UID_WANT="$1"
GID_WANT="$2"
HOME_DIR="$3"

existing_group="$(getent group "$GID_WANT" | cut -d: -f1 || true)"
if [[ -z "$existing_group" ]]; then
  groupadd --gid "$GID_WANT" comfy
elif [[ "$existing_group" != "comfy" ]]; then
  echo "gid $GID_WANT already belongs to group '$existing_group' in the base image; renaming it to 'comfy'"
  groupmod -n comfy "$existing_group"
fi

existing_user="$(getent passwd "$UID_WANT" | cut -d: -f1 || true)"
if [[ -z "$existing_user" ]]; then
  useradd --uid "$UID_WANT" --gid "$GID_WANT" --home-dir "$HOME_DIR" --no-create-home --shell /usr/sbin/nologin comfy
elif [[ "$existing_user" != "comfy" ]]; then
  echo "uid $UID_WANT already belongs to user '$existing_user' in the base image; renaming it to 'comfy'"
  usermod -l comfy -g "$GID_WANT" -d "$HOME_DIR" -s /usr/sbin/nologin "$existing_user"
fi
