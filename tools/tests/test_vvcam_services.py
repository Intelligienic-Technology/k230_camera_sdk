"""Host checks for camera ISP readiness and VVCAM boot migration."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
CAMERA_DEB = REPO_ROOT / "buildroot-overlay/package/vvcam/deb-camera"
WAIT_FOR_ISP = CAMERA_DEB / "etc/vvcam/wait-for-isp.sh"
SYSTEM_UNITS = Path("etc/systemd/system")
DISTRIBUTION_SCRIPT = REPO_ROOT / "buildroot-overlay/board/canaan/k230-soc/distribution/distribution.sh"
CAMERA_PROFILE = "k230_canmv_EC2K230K1AI_MCM5M30M12F3_uvc_defconfig"


class VvcamReadinessTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.video = Path(workspace.name) / "video1"
        self.video.touch()

    def mock_server(self):
        # The first byte opens the video, and a second read keeps it open.
        return subprocess.Popen(
            [sys.executable, "-c",
             "import sys\n"
             "if sys.stdin.buffer.read(1):\n"
             "    with open(sys.argv[1]):\n"
             "        sys.stdin.buffer.read(1)\n",
             str(self.video)],
            stdin=subprocess.PIPE,
        )

    def start_waiter(self, server):
        waiter = subprocess.Popen(
            [str(WAIT_FOR_ISP), str(server.pid), str(self.video)],
            stderr=subprocess.PIPE, text=True,
        )
        self.addCleanup(waiter.wait)
        self.addCleanup(waiter.stderr.close)
        self.addCleanup(waiter.kill)
        return waiter

    def test_only_supervised_process_opening_video_signals_readiness(self):
        with self.video.open("rb"), self.mock_server() as server:
            waiter = self.start_waiter(server)
            with self.assertRaises(subprocess.TimeoutExpired):
                waiter.wait(timeout=0.3)
            server.stdin.write(b"o")
            server.stdin.flush()
            self.assertEqual(waiter.wait(timeout=3), 0)
            server.stdin.close()

    def test_reports_server_exit_before_opening_video(self):
        with self.mock_server() as server:
            waiter = self.start_waiter(server)
            server.stdin.close()
            self.assertEqual(server.wait(timeout=3), 0)
            _, stderr = waiter.communicate(timeout=3)
            self.assertEqual(waiter.returncode, 1)
            self.assertIn("exited before opening", stderr)

    def test_service_bounds_readiness_and_starts_after_drivers(self):
        service = (CAMERA_DEB / SYSTEM_UNITS / "vvcam-isp.service").read_text()
        self.assertIn("Requires=vvcam-drivers.service\n", service)
        self.assertIn("After=vvcam-drivers.service\n", service)
        self.assertIn("ExecStart=/usr/bin/isp_media_server_debian\n", service)
        self.assertIn("ExecStartPost=/etc/vvcam/wait-for-isp.sh ${MAINPID}\n", service)
        self.assertIn("TimeoutStartSec=10s\n", service)
        self.assertNotIn("[Install]", service)

    def test_driver_commands_load_in_order_and_propagate_failure(self):
        service = (CAMERA_DEB / SYSTEM_UNITS / "vvcam-drivers.service").read_text()
        self.assertIn("Type=oneshot\n", service)
        self.assertIn("RemainAfterExit=yes\n", service)
        self.assertNotIn("ExecStop=", service)
        self.assertEqual(
            [line for line in service.splitlines() if line.startswith("ExecStart=")],
            [f"ExecStart=/sbin/modprobe {module}" for module in
             ("vvcam_isp", "vvcam_mipi", "vvcam_vb", "vvcam_isp_subdev", "vvcam_video")],
        )


class VvcamBootTests(unittest.TestCase):
    def test_configure_enables_drivers_and_removes_old_boot_links(self):
        for has_legacy_unit in (False, True):
            with self.subTest(has_legacy_unit=has_legacy_unit), tempfile.TemporaryDirectory() as root:
                root = Path(root)
                units = root / SYSTEM_UNITS
                shutil.copytree(CAMERA_DEB / "etc", root / "etc")
                bin_dir = root / "bin"
                bin_dir.mkdir()
                for utility in ("find", "ln", "mkdir", "rm"):
                    (bin_dir / utility).symlink_to(shutil.which(utility))
                for path in ("basic.target.wants", "multi-user.target.requires"):
                    (units / path).mkdir()
                    (units / path / "vvcam.service").symlink_to("/etc/systemd/system/vvcam.service")
                if has_legacy_unit:
                    (units / "vvcam.service").touch()
                    (root / "etc/vvcam/isp_start.sh").touch()

                result = subprocess.run(
                    [str(CAMERA_DEB / "DEBIAN/postinst"), "configure"],
                    env=dict(os.environ, DPKG_ROOT=str(root), PATH=str(bin_dir)),
                    capture_output=True, text=True, timeout=5,
                )

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse((root / "etc/vvcam/isp_start.sh").exists())
                self.assertEqual(
                    [str(path.relative_to(units)) for path in units.rglob("*.service")
                     if path.is_symlink()],
                    ["multi-user.target.wants/vvcam-drivers.service"],
                )
                driver_link = units / "multi-user.target.wants/vvcam-drivers.service"
                self.assertEqual(os.readlink(driver_link), "../vvcam-drivers.service")
                self.assertEqual(driver_link.resolve(), units / "vvcam-drivers.service")
                self.assertFalse((units / "vvcam.service").exists())


@unittest.skipUnless(shutil.which("dpkg") and shutil.which("dpkg-deb"), "dpkg is needed")
class DistributionDebTests(unittest.TestCase):
    def test_camera_packages_extract_once_with_local_precedence(self):
        with tempfile.TemporaryDirectory() as workspace:
            workspace = Path(workspace)
            tools = workspace / "tools"
            tools.mkdir()
            shutil.copy(REPO_ROOT / "tools/camera-profile.sh", tools)
            archives = workspace / "archives"
            remote_archives = archives / "remote_deb"
            remote_archives.mkdir(parents=True)
            for package in ("k230-vvcam", "k230-camera", "k230-opencv4", "test-unrelated"):
                for source, version, parent in (("local", "2", archives), ("remote", "1", remote_archives)):
                    tree = workspace / f"{package}-{source}"
                    (tree / "DEBIAN").mkdir(parents=True)
                    (tree / "DEBIAN/control").write_text(
                        f"Package: {package}\nVersion: {version}\nArchitecture: all\n"
                        "Maintainer: SDK tests <tests@example.com>\nDescription: Test archive\n"
                    )
                    (tree / "test").mkdir()
                    (tree / "test" / package).write_text(source)
                    filename = f"{package}.deb" if source == "local" else f"{package}_{version}_all.deb"
                    subprocess.run(["dpkg-deb", "--build", str(tree), str(parent / filename)],
                                   check=True, capture_output=True)

            scenarios = (
                ("debian", CAMERA_PROFILE, ("local", "remote"), 5),
                ("ubuntu", CAMERA_PROFILE, ("local", "local"), 4),
                ("debian", "k230_canmv_defconfig", ("remote", "remote"), 7),
            )
            for distro, profile, expected, extraction_count in scenarios:
                with self.subTest(distro=distro, profile=profile):
                    build_dir = workspace / "output" / profile
                    (build_dir / "images").mkdir(parents=True, exist_ok=True)
                    deb_dir = build_dir / "images/deb"
                    if not deb_dir.is_symlink():
                        deb_dir.symlink_to(archives, target_is_directory=True)
                    rootfs = workspace / f"{distro}-{profile}"
                    rootfs.mkdir()
                    log = rootfs / "extracted.log"
                    subprocess.run(
                        ["bash", "-c", r'''
dist_script="$1"
shift
source <(sed '/^# Main execution/,$d' "$dist_script")
wget() { return 0; }
extract_log="$4"
dpkg() { printf '%s\n' "${2##*/}" >> "$extract_log"; command dpkg "$@"; }
DISTR_DOWN_URI=https://example.com
extract_distribution_debs "$1" "$3"
''', "test", str(DISTRIBUTION_SCRIPT), distro, str(build_dir), str(rootfs), str(log)],
                        check=True, capture_output=True, text=True,
                    )
                    installed = rootfs / "test"
                    for package in ("k230-vvcam", "k230-camera"):
                        self.assertEqual((installed / package).read_text(), expected[0])
                    for package in ("k230-opencv4", "test-unrelated"):
                        self.assertEqual((installed / package).read_text(), expected[1])
                    extracted = log.read_text().splitlines()
                    self.assertEqual(len(extracted), extraction_count, extracted)
                    self.assertEqual(len(extracted), len(set(extracted)), extracted)
                    self.assertTrue((archives / "k230-opencv4.deb").exists())


if __name__ == "__main__":
    unittest.main()
