#!/usr/bin/env bash
# Stage 00 (runs ON the VM): make a fresh Debian VM ready, idempotently.
# Expects ~/bhl-bin/{rudof,qlever-index,qlever-server} (copied by bhl-refresh.sh,
# from the versions the production server runs — an index is tied to the
# binary that wrote it). Prints the chosen big-volume path as its last line.
set -euo pipefail

need=(unzip curl python3-venv)
missing=()
for p in "${need[@]}"; do dpkg -s "$p" >/dev/null 2>&1 || missing+=("$p"); done
if [ ${#missing[@]} -gt 0 ]; then
  sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${missing[@]}"
fi

if [ ! -x "$HOME/bhl-venv/bin/python3" ]; then python3 -m venv "$HOME/bhl-venv"; fi
"$HOME/bhl-venv/bin/pip" install -q morph-kgc requests pyyaml jinja2 qlever

chmod +x "$HOME"/bhl-bin/* 2>/dev/null || true
"$HOME/bhl-bin/rudof" --version >/dev/null
"$HOME/bhl-bin/qlever-index" --help >/dev/null 2>&1 || true

# Big volume: bulk build needs ~75 MB temp per million triples plus the index
# (~100 GB for BHL). Reuse a mounted volume, else format an empty unmounted
# disk, else fall back to the root disk if it is large enough.
MIN_GB=90
avail_gb() { df -BG --output=avail "$1" | tail -1 | tr -dc 0-9; }
TARGET=/mnt/bhl-build
if mountpoint -q "$TARGET"; then :
else
  dev=$(lsblk -dnbo NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT | awk -v m=$((MIN_GB*1000000000)) \
        '$3=="disk" && $2>=m && $4=="" && $5=="" {print $1; exit}')
  if [ -n "${dev:-}" ] && [ -z "$(lsblk -no NAME "/dev/$dev" | tail -n +2)" ]; then
    echo "Formatting empty disk /dev/$dev -> $TARGET" >&2
    sudo mkfs.ext4 -q "/dev/$dev"
    sudo mkdir -p "$TARGET"; sudo mount "/dev/$dev" "$TARGET"
  elif [ "$(avail_gb /)" -ge 150 ]; then
    TARGET="$HOME/bhl-build-root"
  else
    echo "ERROR: no volume with >=${MIN_GB} GB free. Attach a >=100 GB empty volume and re-run." >&2
    exit 2
  fi
fi
sudo mkdir -p "$TARGET"; sudo chown "$USER" "$TARGET"
echo "$TARGET"
