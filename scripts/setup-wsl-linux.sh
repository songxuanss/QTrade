#!/usr/bin/env bash
# Bootstrap only the dedicated Ubuntu 26 distro. Project data and credentials
# stay on its Linux filesystem; existing Windows/WSL environments are not reset.
set -euo pipefail

if [[ ${WSL_DISTRO_NAME:-} != QTrade-Ubuntu-26 || $EUID != 0 ]]; then
  echo 'Run as root in the dedicated QTrade-Ubuntu-26 distro.' >&2
  exit 1
fi
exec 9>/run/lock/qtrade-local-setup.lock
if ! flock -n 9; then
  echo 'Another QTrade bootstrap is running; observe that process instead.' >&2
  exit 1
fi
source /etc/os-release
if [[ $ID != ubuntu || $VERSION_ID != 26.04 ]]; then
  echo 'This bootstrap requires Ubuntu 26.04 LTS.' >&2
  exit 1
fi

project_source=${1:?Supply the mounted Windows QTrade source directory}
sso_source=${2:?Supply the mounted Windows sibling SSO source directory}
if [[ ! -f $project_source/pyproject.toml || ! -f $sso_source/pyproject.toml ]]; then
  echo 'Both source projects must be present before setup.' >&2
  exit 1
fi

# The host uses a loopback proxy. Consomme makes this existing listener
# reachable from the guest; keep internal service traffic off that proxy.
export no_proxy='localhost,127.0.0.1,::1,postgres,market-db,collector-db,object-store,valkey,api,collector,host.docker.internal'
export NO_PROXY=$no_proxy
export HTTP_PROXY=${http_proxy:-}
export HTTPS_PROXY=${https_proxy:-}
export DEBIAN_FRONTEND=noninteractive

# Install the drop-in before apt can auto-start Docker. On an existing engine,
# changed settings still require the guarded reload below before image pulls.
if [[ -n ${http_proxy:-} ]]; then
  install -d /etc/systemd/system/docker.service.d
  cat > /etc/systemd/system/docker.service.d/qtrade-proxy.conf <<EOF
[Service]
Environment="HTTP_PROXY=$http_proxy"
Environment="HTTPS_PROXY=${https_proxy:-$http_proxy}"
Environment="NO_PROXY=$no_proxy"
EOF
fi
systemctl daemon-reload
apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates curl git rsync python3 python3-venv \
  docker.io docker-buildx docker-compose-v2 nodejs npm

if ! id qtrade >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash qtrade
fi
usermod -aG docker qtrade
install -d -o qtrade -g qtrade /home/qtrade/workspaces

for name in QTrade sso; do
  source_dir=$project_source
  [[ $name != sso ]] || source_dir=$sso_source
  target=/home/qtrade/workspaces/$name
  install -d -o qtrade -g qtrade "$target"
  # Copy code only. Do not delete destination files or copy old private state,
  # credentials, databases, node modules or host-specific Python environments.
  rsync -rlt --chown=qtrade:qtrade \
    --exclude='.git' --exclude='.tools' --exclude='.venv' \
    --exclude='.env*' --exclude='node_modules' --exclude='.next' \
    --exclude='__pycache__' --exclude='.pytest_cache' --exclude='.mypy_cache' \
    --exclude='test-results' --exclude='*.tsbuildinfo' \
    "$source_dir/" "$target/"
done

python3 -m venv /opt/qtrade-tools
/opt/qtrade-tools/bin/pip install --index-url https://pypi.org/simple 'uv==0.12.22'

systemctl daemon-reload
systemctl enable --now docker
if [[ -n ${http_proxy:-} && $(docker info --format '{{.HTTPProxy}}') != "$http_proxy" ]]; then
  if [[ -n $(docker ps -aq) ]]; then
    echo 'Docker proxy changed; preserve and review existing containers before restarting.' >&2
    exit 1
  fi
  systemctl restart docker
fi
# Docker bridge containers need their own reachable proxy route; configuring
# the daemon alone does not certify supplier access from a running collector.

cd /home/qtrade/workspaces/QTrade
runuser -u qtrade -- env \
  http_proxy="${http_proxy:-}" https_proxy="${https_proxy:-}" \
  HTTP_PROXY="$HTTP_PROXY" HTTPS_PROXY="$HTTPS_PROXY" NO_PROXY="$NO_PROXY" no_proxy="$no_proxy" \
  /opt/qtrade-tools/bin/uv sync --locked --python 3.12

printf '%s\n' 'Ubuntu 26 base tools and locked QTrade Python environment prepared.'
docker version --format '{{.Server.Version}}'
docker compose version
node --version
runuser -u qtrade -- .venv/bin/python --version
