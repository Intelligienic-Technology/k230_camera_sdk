#!/bin/bash
# Helpers shared by the camera release profile build hooks.
camera_is_profile() {
    [ "$(basename "$1")" = k230_canmv_EC2K230K1AI_MCM5M30M12F3_uvc_defconfig ]
}

camera_image_name() {
    local base_dir="$1" distribution="$2" version
    version=$(sed -n 's/^sdk://p' "$base_dir/target/etc/version/release_version")
    [[ "$version" =~ ^[a-zA-Z0-9._-]+$ ]] || {
        echo "missing or invalid target SDK version" >&2
        return 1
    }
    printf '%s_%s_%s.img.gz\n' "$(basename "$base_dir" _defconfig)" "$distribution" "$version"
}
