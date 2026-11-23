#!/bin/sh
set -e

root=${1:-/}
unit_dir="${root%/}/etc/systemd/system"

# Previous distribution images used dpkg -x. Remove their boot links even
# when dpkg has already deleted the legacy unit or has never tracked it.
find "$unit_dir" -type l \( -path '*.wants/vvcam.service' -o -path '*.requires/vvcam.service' \) -delete
rm -f "$unit_dir/vvcam.service" "${root%/}/etc/vvcam/isp_start.sh"

# Match WantedBy in vvcam-drivers.service using a link inside the target root.
mkdir -p "$unit_dir/multi-user.target.wants"
ln -sfn ../vvcam-drivers.service "$unit_dir/multi-user.target.wants/vvcam-drivers.service"
