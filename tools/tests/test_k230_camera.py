import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / 'buildroot-overlay/package/k230_camera/files/k230-camera'


class CameraTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.events = self.root / 'events'
        self.isp_invocation = self.root / 'isp-invocation'
        self.isp_ready = self.root / 'isp-ready'
        self.env = dict(os.environ, PATH=f'{self.bin}:' + os.environ['PATH'],
                        EVENTS=str(self.events), GADGETS=str(self.root / 'gadgets'),
                        INVOCATION_FILE=str(self.isp_invocation), ISP_READY=str(self.isp_ready))
        self.make('helper', '''echo "helper $1" >> "$EVENTS"
[ "$1" != start-managed ] || exit "${FAIL_SETUP:-0}"''')
        self.make('adb', '''echo "adb $1" >> "$EVENTS"
case "$1" in
    start) mkdir -p "$GADGETS/g1"; echo 91500000.usb > "$GADGETS/g1/UDC" ;;
    stop) : > "$GADGETS/g1/UDC" ;;
esac''')
        self.make('systemctl', '''case "$*" in
    'show --property=ActiveState --value vvcam-isp.service') echo "${ISP_STATE:-inactive}" ;;
    'show --property=InvocationID --value vvcam-isp.service') cat "$INVOCATION_FILE" ;;
    '--job-mode=fail start vvcam-isp.service')
        echo 'isp start' >> "$EVENTS"
        [ "${FAIL_ISP:-0}" = 0 ] || exit 1
        if [ "${WAIT_ISP_READY:-0}" = 1 ]; then
            while [ ! -f "$ISP_READY" ]; do sleep 0.01; done
        fi
        echo 2cf5778655d049f3bc91214221adaa8d > "$INVOCATION_FILE"
        echo 'isp ready' >> "$EVENTS"
        ;;
    '--job-mode=fail stop vvcam-isp.service') echo 'isp stop' >> "$EVENTS" ;;
    *) exit 1 ;;
esac''')
        self.make('pgrep', 'exit 1')
        self.make('fuser', 'exit 1')
        self.make('uvc-gadget', '''echo uvc >> "$EVENTS"
if [ "${UVC_EXIT:-0}" = 1 ]; then exit 1; fi
exec sleep 60''')
        self.make('v4l2-drm', 'echo lcd >> "$EVENTS"; exit 1')
        self.harness = self.root / 'harness'
        self.harness.write_text(f'''#!/bin/bash
source "{SCRIPT}"
RUN_DIR="{self.root}/run"
GADGET_ROOT="{self.root}/gadgets"
VIDEO_UVC=/dev/null
VIDEO_LCD=/dev/null
HELPER="{self.bin}/helper"
ADB_SCRIPT="{self.bin}/adb"
prerequisites() {{ [ "${{FAIL_PREREQ:-0}}" = 0 ]; }}
main "$@"
''')
        self.harness.chmod(0o755)

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, name, body):
        p = self.bin / name
        p.write_text('#!/bin/sh\n' + body + '\n')
        p.chmod(0o755)

    def start(self, *, start_new_session=False, **env):
        if env.get('ADB_ACTIVE') == '1':
            udc = self.root / 'gadgets/g1/UDC'
            udc.parent.mkdir(parents=True, exist_ok=True)
            udc.write_text('91500000.usb\n')
        return subprocess.Popen([str(self.harness)], env=dict(self.env, **env),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=start_new_session)

    def pending_adb(self):
        gadget = self.root / 'gadgets/g1'
        config = gadget / 'configs/c.1'
        config.mkdir(parents=True)
        for function in ('ffs.adb', 'ffs.mtp'):
            (config / function).symlink_to(f'../../functions/{function}')
        udc = gadget / 'UDC'
        udc.touch()
        return udc

    def wait_for(self, text, count=1, timeout=6):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.events.exists() and self.events.read_text().splitlines().count(text) >= count:
                return
            time.sleep(.05)
        self.fail(f'missing {text}: {self.events.read_text() if self.events.exists() else ""}')

    def test_retry_lcd_exit_and_signal_cleanup(self):
        p = self.start(UVC_EXIT='1', ADB_ACTIVE='1')
        try:
            self.wait_for('uvc', 2)
            self.assertEqual(self.events.read_text().splitlines().count('lcd'), 1)
        finally:
            p.send_signal(signal.SIGTERM)
            p.wait(timeout=8)
        events = self.events.read_text().splitlines()
        self.assertIn('helper cleanup', events)
        self.assertLess(events.index('isp ready'), events.index('adb stop'))
        self.assertLess(events.index('adb stop'), events.index('helper start-managed'))
        self.assertLess(events.index('helper cleanup'), events.index('isp stop'))
        self.assertLess(events.index('isp stop'), events.index('adb start'))
        self.assertEqual((self.root / 'gadgets/g1/UDC').read_text().strip(), '91500000.usb')

    def test_duplicate_and_ctrl_c(self):
        p = self.start()
        try:
            self.wait_for('uvc')
            duplicate = self.start()
            self.assertNotEqual(duplicate.wait(timeout=3), 0)
        finally:
            p.send_signal(signal.SIGINT)
            p.wait(timeout=8)
        self.assertEqual(p.returncode, 130)
        events = self.events.read_text().splitlines()
        self.assertEqual(events.count('isp start'), 1)
        self.assertEqual(events.count('isp stop'), 1)
        self.assertEqual(events.count('helper cleanup'), 1)
        self.assertFalse(any(event.startswith('adb ') for event in events))

    def test_partial_setup_and_prerequisite(self):
        p = self.start(FAIL_PREREQ='1')
        self.assertNotEqual(p.wait(timeout=3), 0)
        self.assertFalse(self.events.exists())
        p = self.start(FAIL_SETUP='1', ADB_ACTIVE='1')
        self.assertNotEqual(p.wait(timeout=8), 0)
        self.assertEqual(self.events.read_text().splitlines(),
                         ['isp start', 'isp ready', 'adb stop', 'helper start-managed',
                          'helper cleanup', 'isp stop', 'adb start'])

    def test_failed_isp_start_leaves_usb_untouched(self):
        p = self.start(FAIL_ISP='1', ADB_ACTIVE='1')
        self.assertNotEqual(p.wait(timeout=8), 0)
        self.assertEqual(self.events.read_text().splitlines(), ['isp start'])
        self.assertEqual((self.root / 'gadgets/g1/UDC').read_text().strip(), '91500000.usb')

    def test_ctrl_c_during_isp_start_cleans_up_before_switching_usb(self):
        p = self.start(start_new_session=True, WAIT_ISP_READY='1', ADB_ACTIVE='1')
        try:
            self.wait_for('isp start')
            os.killpg(p.pid, signal.SIGINT)
        finally:
            self.isp_ready.touch()
            p.wait(timeout=8)
        self.assertEqual(p.returncode, 130)
        self.assertEqual(self.events.read_text().splitlines(), ['isp start', 'isp ready', 'isp stop'])
        self.assertEqual((self.root / 'gadgets/g1/UDC').read_text().strip(), '91500000.usb')

    def test_preserves_replacement_isp(self):
        p = self.start()
        try:
            self.wait_for('uvc')
            self.isp_invocation.write_text('ce46dd3d9e704c37aec1c9bfc9f53569\n')
        finally:
            p.send_signal(signal.SIGINT)
            p.wait(timeout=8)
        events = self.events.read_text().splitlines()
        self.assertIn('helper cleanup', events)
        self.assertNotIn('isp stop', events)

    def test_preserves_manual_isp(self):
        for state, expected in (('active', []), ('activating', ['isp start', 'isp ready'])):
            with self.subTest(state=state):
                self.events.unlink(missing_ok=True)
                p = self.start(ISP_STATE=state)
                try:
                    self.wait_for('uvc')
                finally:
                    p.send_signal(signal.SIGINT)
                    p.wait(timeout=8)
                events = self.events.read_text().splitlines()
                self.assertEqual([event for event in events if event.startswith('isp ')], expected)
                self.assertIn('helper cleanup', events)

    def test_setup_ownership_race_does_not_cleanup(self):
        p = self.start(FAIL_SETUP='75', ADB_ACTIVE='1')
        self.assertNotEqual(p.wait(timeout=8), 0)
        self.assertEqual(self.events.read_text().splitlines(),
                         ['isp start', 'isp ready', 'adb stop', 'helper start-managed',
                          'isp stop', 'adb start'])

    def test_pending_adb_binds_before_launcher_switches_usb(self):
        udc = self.pending_adb()
        self.make('sleep', '''if [ "$1" = 0.1 ] && [ ! -s "$GADGETS/g1/UDC" ]; then
    echo 'waiting for ADB UDC' >> "$EVENTS"
fi
exec /bin/sleep "$@"''')
        p = self.start()
        try:
            self.wait_for('waiting for ADB UDC')
            self.assertNotIn('helper start-managed', self.events.read_text().splitlines())
            udc.write_text('91500000.usb\n')
            self.wait_for('uvc')
        finally:
            p.send_signal(signal.SIGINT)
            p.wait(timeout=8)
        events = self.events.read_text().splitlines()
        self.assertLess(events.index('adb stop'), events.index('helper start-managed'))
        self.assertLess(events.index('helper cleanup'), events.index('adb start'))
        self.assertEqual(udc.read_text().strip(), '91500000.usb')

    def test_pending_adb_timeout_leaves_usb_untouched(self):
        udc = self.pending_adb()
        self.make('sleep', 'exit 0')
        result = subprocess.run([str(self.harness)], env=self.env, capture_output=True,
                                text=True, timeout=8)
        self.assertEqual(result.returncode, 1)
        self.assertIn('ADB/MTP gadget is configured but did not bind', result.stderr)
        self.assertEqual(self.events.read_text().splitlines(), ['isp start', 'isp ready', 'isp stop'])
        self.assertEqual(udc.read_text(), '')
        self.assertTrue((udc.parent / 'configs/c.1/ffs.adb').is_symlink())

    def test_prerequisites_check_uvc_help(self):
        self.make('id', 'echo 0')
        self.make('uvc-gadget', 'echo "usage: uvc-gadget ${UVC_OPTION:--v} DEVICE"')
        check = (f'source "{SCRIPT}"; VIDEO_UVC=/dev/null; '
                 f'HELPER="{self.bin}/helper"; prerequisites')
        def run(**extra):
            return subprocess.run(['bash', '-c', check], env=dict(self.env, **extra),
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(run().returncode, 0)
        self.assertNotEqual(run(UVC_OPTION='--other').returncode, 0)

    def test_usb_rejects_unrelated_bound_and_existing_uvc(self):
        gadgets = self.root / 'gadgets'
        (gadgets / 'other').mkdir(parents=True)
        (gadgets / 'other' / 'UDC').write_text('91500000.usb')
        check = f'source "{SCRIPT}"; GADGET_ROOT="{gadgets}"; VIDEO_UVC=/dev/null; check_usb'
        result = subprocess.run(['bash', '-c', check], env=self.env, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        (gadgets / 'other' / 'UDC').write_text('')
        (gadgets / 'uvc_g1').mkdir()
        result = subprocess.run(['bash', '-c', check], env=self.env, capture_output=True)
        self.assertNotEqual(result.returncode, 0)

    def test_usb_allows_isp_server_but_rejects_other_video_holders(self):
        self.make('pgrep', 'exit 1')
        self.make('fuser', 'echo "$HOLDERS"')
        self.make('readlink', '''
case "$1" in
    /proc/242/exe) echo /usr/bin/isp_media_server_debian ;;
    /proc/243/exe) echo /usr/bin/other-capture ;;
    *) exit 1 ;;
esac''')
        check = f'source "{SCRIPT}"; GADGET_ROOT="{self.root}/gadgets"; check_usb'
        for holders, status in (('242', 0), ('242 243', 1), ('243', 1)):
            with self.subTest(holders=holders):
                result = subprocess.run(['bash', '-c', check],
                                        env=dict(self.env, HOLDERS=holders),
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, status, result.stderr)
                if status:
                    self.assertIn('/dev/video1 is already in use', result.stderr)

    def test_no_lcd(self):
        p = subprocess.Popen([str(self.harness), '--no-lcd'], env=self.env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            self.wait_for('uvc')
            self.assertNotIn('lcd', self.events.read_text().splitlines())
        finally:
            p.send_signal(signal.SIGTERM)
            p.wait(timeout=8)

    def test_cli(self):
        self.assertEqual(subprocess.run([str(SCRIPT), '--help'], capture_output=True).returncode, 0)
        self.assertEqual(subprocess.run([str(SCRIPT), '--bad'], capture_output=True).returncode, 2)


if __name__ == '__main__':
    unittest.main()
