#!/usr/bin/env bash
set -Eeuo pipefail

repo_dir="${DOMLY_REPO_DIR:-/srv/domly/repo}"
project_dir="$repo_dir/domly"
venv_dir="${DOMLY_VENV_DIR:-/srv/domly/venv}"
env_file="${DOMLY_ENV_FILE:-/etc/domly/domly.env}"
expected_user="${DOMLY_DEPLOY_USER:-domly}"

if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    echo "Run this script as $expected_user, not as root." >&2
    exit 1
fi

if [[ "$(id -un)" != "$expected_user" ]]; then
    echo "Expected deployment user $expected_user, got $(id -un)." >&2
    exit 1
fi

for required_path in "$repo_dir/.git" "$project_dir/manage.py" "$venv_dir/bin/python" "$env_file"; do
    if [[ ! -e "$required_path" ]]; then
        echo "Required deployment path is missing: $required_path" >&2
        exit 1
    fi
done

if [[ -n "$(git -C "$repo_dir" status --porcelain --untracked-files=normal)" ]]; then
    echo "Deployment stopped: the server checkout has local changes." >&2
    git -C "$repo_dir" status --short >&2
    exit 1
fi

git -C "$repo_dir" pull --ff-only

set -a
# shellcheck disable=SC1090
source "$env_file"
set +a

cd "$project_dir"
"$venv_dir/bin/python" -m pip install --requirement requirements.txt
"$venv_dir/bin/python" manage.py check --deploy
"$venv_dir/bin/python" manage.py migrate --noinput
"$venv_dir/bin/python" manage.py collectstatic --noinput

sudo systemctl restart domly.service
curl --fail --show-error --silent --retry 5 --retry-delay 2 \
    https://domly.site/health/
echo
echo "Domly deployed at commit $(git -C "$repo_dir" rev-parse --short HEAD)."
