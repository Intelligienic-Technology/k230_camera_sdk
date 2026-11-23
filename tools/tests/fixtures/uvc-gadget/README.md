# UVC startup host-test source fixture

These unmodified upstream files come from uvc-gadget commit
`04c18aa6c4a7017957e2dfe88c3ff7ef34a1b3a0` at
<https://gitlab.freedesktop.org/camera/uvc-gadget> (the version pinned in
`buildroot-overlay/package/uvc-gadget/uvc-gadget.mk`). Their original
`LGPL-2.1-or-later` SPDX notices and copyright notices are retained.

The fixture contains only upstream preimages and headers needed by
`test_uvc_stream_startup.py`. The test copies these files into a temporary
directory and applies the relevant file sections of every production package
patch, with zero patch fuzz. In particular, `vpu-source.c` is always created
from the production patch, never copied into this fixture. No generated build
tree, downloaded source, network access, camera, or FFmpeg installation is
required by the test.

To refresh when the pinned upstream revision changes, extract exactly the
paths already present here from Buildroot's matching pristine source archive
in `dl/uvc-gadget/`, preserving bytes and notices. Do not copy patched files
from `output/`. Review the fixture diff and run the host test. The host harness
in the parent fixture directory supplies only FFmpeg and V4L2 device boundary
mocks; event dispatch, source/stream lifecycle, packet queueing, and buffer
copying execute the patched production C sources.
