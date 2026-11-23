#!/bin/sh

pid=${1:-}
video_node=${2:-/dev/video1}
if [ -n "$pid" ]; then
    # systemd bounds this loop with TimeoutStartSec.
    while kill -0 "$pid" 2>/dev/null; do
        for fd in /proc/"$pid"/fd/*; do
            if [ "$fd" -ef "$video_node" ]; then
                exit 0
            fi
        done
        sleep 0.1
    done
fi

echo "ISP media server exited before opening $video_node" >&2
exit 1
