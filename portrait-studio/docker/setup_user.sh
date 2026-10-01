#!/usr/bin/env bash
# Create the given user/group at a specific uid/gid (usually the host user's, so a bind-mounted volume — models/
# input/output for ComfyUI, /data for the API — stays writable by the container process). The base image sometimes
# already has an account at that id (a distro default user, a conda account, …) — groupadd/useradd then refuse
# with "already exists". Instead of failing, rename that existing account to the one we want.
#
# Usage: setup_user.sh <username> <uid> <gid> <home-dir>
set -euo pipefail
USERNAME="$1"
UID_WANT="$2"
GID_WANT="$3"
HOME_DIR="$4"

existing_group="$(getent group "$GID_WANT" | cut -d: -f1 || true)"
if [[ -z "$existing_group" ]]; then
  groupadd --gid "$GID_WANT" "$USERNAME"
elif [[ "$existing_group" != "$USERNAME" ]]; then
  echo "gid $GID_WANT already belongs to group '$existing_group' in the base image; renaming it to '$USERNAME'"
  groupmod -n "$USERNAME" "$existing_group"
fi

existing_user="$(getent passwd "$UID_WANT" | cut -d: -f1 || true)"
if [[ -z "$existing_user" ]]; then
  useradd --uid "$UID_WANT" --gid "$GID_WANT" --home-dir "$HOME_DIR" --no-create-home --shell /usr/sbin/nologin "$USERNAME"
elif [[ "$existing_user" != "$USERNAME" ]]; then
  echo "uid $UID_WANT already belongs to user '$existing_user' in the base image; renaming it to '$USERNAME'"
  usermod -l "$USERNAME" -g "$GID_WANT" -d "$HOME_DIR" -s /usr/sbin/nologin "$existing_user"
fi
