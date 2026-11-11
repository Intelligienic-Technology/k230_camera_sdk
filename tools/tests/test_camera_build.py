"""Camera profile build hooks exercised with mock image-generation tools."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

REPO_ROOT = Path(__file__).resolve().parents[2]
PROFILE = "k230_canmv_EC2K230K1AI_MCM5M30M12F3_uvc_defconfig"
BOARD = Path("buildroot-overlay/board/canaan/k230-soc")

class CameraBuildTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        (self.root / "tools").mkdir()
        shutil.copyfile(REPO_ROOT / "tools/camera-profile.sh", self.root / "tools/camera-profile.sh")
        self.base = self.root / "output" / PROFILE
        self.images = self.base / "images"
        self.images.mkdir(parents=True)
        self.target = self.base / "target"
        (self.target / "etc/version").mkdir(parents=True)
        self.env = os.environ.copy()

    def test_versioned_image_names_validate_target_version(self):
        version_file = self.target / "etc/version/release_version"
        helper = self.root / "tools/camera-profile.sh"
        for value in ("v1.2-good", "../bad", "", "a\nsdk:b"):
            version_file.write_text(f"sdk:{value}\n")
            for dist in ("linux", "debian", "ubuntu", "openouler"):
                result = subprocess.run(
                    ["bash", "-c", 'source "$1"; camera_image_name "$2" "$3"',
                     "test", str(helper), str(self.base), dist],
                    capture_output=True, text=True,
                )
                if value == "v1.2-good":
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout.strip(),
                                     f"{PROFILE.removesuffix('_defconfig')}_{dist}_{value}.img.gz")
                else:
                    self.assertNotEqual(result.returncode, 0)

    def test_distribution_hook_uses_target_version_for_aliases(self):
        # Source the actual hook up to its main section to avoid root operations.
        hook = (REPO_ROOT / BOARD / "distribution/distribution.sh").read_text()
        functions = self.root / "distribution-functions.sh"
        functions.write_text(hook.split("# Main execution", 1)[0])
        (self.target / "etc/version/release_version").write_text("sdk:v2-safe\n")
        for dist in ("debian", "ubuntu", "openouler"):
            result = subprocess.run(
                ["bash", "-c", 'source "$1" "$3" "$2"; get_image_last_name "$3"',
                 "test", str(functions), str(self.base), dist],
                capture_output=True, text=True, env=self.env, timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(),
                             f"{PROFILE.removesuffix('_defconfig')}_{dist}_v2-safe.img.gz")
        (self.target / "etc/version/release_version").write_text("sdk:../bad\n")
        result = subprocess.run(
            ["bash", "-c", 'source "$1" debian "$2"; get_image_last_name debian',
             "test", str(functions), str(self.base)],
            capture_output=True, text=True, env=self.env, timeout=10,
        )
        self.assertNotEqual(result.returncode, 0)

    def test_post_build_uses_build_metadata_and_omits_host_identity(self):
        board = self.root / BOARD
        overlay_version = board / "rootfs_overlay/etc/version/release_version"
        overlay_version.parent.mkdir(parents=True)
        overlay_version.write_text("sdk:unchanged-source\n")
        (self.target / "etc/init.d").mkdir()
        (self.base / "staging/lib").mkdir(parents=True)
        (self.base / ".config").write_text(
            'BR2_ROOTFS_OVERLAY="board/canaan/k230-soc/rootfs_overlay"\n'
        )
        env = dict(self.env, **{"BASE_DIR": str(self.base), "BUILD_DIR": str(self.base / "build"),
                          "TARGET_DIR": str(self.target), "STAGING_DIR": str(self.base / "staging")})
        result = subprocess.run(["bash", str(REPO_ROOT / BOARD / "post-build.sh"), str(self.target)],
                                env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(overlay_version.read_text(), "sdk:unchanged-source\n")
        version = (self.target / "etc/version/release_version").read_text()
        self.assertRegex(version, r"^sdk:v0\.0\.0-\d{8}-\d{6}-unknown-nncase0\.0\.0\nCONF:")

    def test_post_image_uses_canonical_logo_and_reports_generation_failure(self):
        (self.target / "etc/version/release_version").write_text(
            "sdk:v1-20260101-120000-abc-nncase1\n"
        )
        board = self.root / BOARD
        (board / "logo").mkdir(parents=True)
        (board / "logo/logo_01.yuv").write_bytes(b"canonical logo")
        shutil.copyfile(REPO_ROOT / BOARD / "post-image.sh", board / "post-image.sh")
        (board / "default.env").write_text("bootcmd=run blinux;\n")
        boot = board / "rootfs_overlay/boot"
        boot.mkdir(parents=True)
        (boot / "nuttx-7000000-uart2.bin").write_bytes(b"firmware")
        uboot = self.base / "build/uboot-2022.10"
        (uboot / "tools").mkdir(parents=True)
        (uboot / "spl").mkdir()
        (uboot / "board/canaan/common").mkdir(parents=True)
        (uboot / "board/canaan/common/sdk_autoconf.h").write_text(
            "#define CONFIG_MEM_LINUX_SYS_BASE 0\n"
        )
        (uboot / ".config").write_text('CONFIG_K230_BARE_DISP_LOGO_PATH="unused.png"\n')
        (uboot / "u-boot.bin").write_bytes(b"uboot")
        (uboot / "spl/u-boot-spl.bin").write_bytes(b"spl")
        (uboot / "tools/firmware_gen_no_securiy.py").write_text(
            "import argparse, shutil\np = argparse.ArgumentParser()\n"
            "p.add_argument('-i'); p.add_argument('-o'); p.add_argument('-n', action='store_true')\n"
            "a = p.parse_args(); shutil.copyfile(a.i, a.o)\n"
        )
        mock_bin = self.root / "mock-bin"
        mock_bin.mkdir()

        def executable(path, script):
            path.write_text("#!/bin/bash\nset -e\n" + script)
            path.chmod(0o755)

        executable(uboot / "tools/mkimage", 'touch "${@: -1}"\n')
        executable(uboot / "tools/endian-swap.py", 'cp "$1" "$2"\n')
        executable(uboot / "tools/mkenvimage", 'touch "$4"\n')
        executable(self.root / "tools/k230_priv_gzip", 'cp "${@: -1}" "${@: -1}.gz"\n')
        executable(mock_bin / "fakeroot", 'exec "$@"\n')
        executable(mock_bin / "mkfs.ext4", 'for arg; do [[ "$arg" != *.ext4 ]] || touch "$arg"; done\n')
        executable(mock_bin / "genimage", '[[ "${FAIL_GENIMAGE:-0}" = 0 ]] || exit 42\ntouch sysimage-sdcard.img\n')
        for filename in ("Image", "camera.dtb", "fw_jump.bin"):
            (self.images / filename).write_bytes(b"payload")
        (self.base / ".config").write_text('BR2_LINUX_KERNEL_INTREE_DTS_NAME="canaan/camera"\n')
        env = dict(self.env, BASE_DIR=str(self.base), BUILD_DIR=str(self.base / "build"),
                   TARGET_DIR=str(self.target), BINARIES_DIR=str(self.images),
                   BR2_CONFIG=str(self.base / ".config"),
                   PATH=str(mock_bin) + os.pathsep + self.env["PATH"])
        for fail in ("0", "1"):
            with self.subTest(fail_genimage=fail):
                env["FAIL_GENIMAGE"] = fail
                result = subprocess.run(["bash", str(board / "post-image.sh")],
                                        env=env, capture_output=True, text=True, timeout=10)
                if fail == "0":
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual((self.images / "boot/logo.yuv").read_bytes(),
                                     (board / "logo/logo_01.yuv").read_bytes())
                    expected = f"{PROFILE.removesuffix('_defconfig')}_linux_v1-20260101-120000-abc-nncase1.img.gz"
                    self.assertEqual(Path(os.readlink(self.images / expected)), Path("sysimage-sdcard.img.gz"))
                else:
                    self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
