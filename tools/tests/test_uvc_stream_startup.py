"""Compile patched production UVC/VPU paths with host USB/FFmpeg boundaries."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).with_name('fixtures')
PACKAGE = ROOT / 'buildroot-overlay/package/uvc-gadget'


def apply_source_patches(source, stop_before_startup=False):
    """Apply full hunks for relevant files; unrelated build/main hunks are omitted."""
    paths = {str(path.relative_to(source)) for path in source.rglob('*')
             if path.is_file()}
    paths.update(('lib/vpu-source.c', 'include/uvcgadget/vpu-source.h'))
    for patch in sorted(PACKAGE.glob('*.patch')):
        if stop_before_startup and patch.name.startswith('0005-'):
            break
        lines = patch.read_text().splitlines(keepends=True)
        selected = []
        index = 0
        while index < len(lines):
            if (lines[index].startswith('--- ') and index + 1 < len(lines)
                    and lines[index + 1].startswith('+++ ')):
                end = index + 2
                while end < len(lines) and not lines[end].startswith(('diff --git ', '--- ')):
                    end += 1
                path = lines[index + 1].split()[1].removeprefix('b/')
                if path in paths:
                    selected.extend(lines[index:end])
                index = end
            else:
                index += 1
        if selected:
            result = subprocess.run(
                ['patch', '--batch', '--fuzz=0', '-p1', '-d', str(source)],
                input=''.join(selected), text=True, capture_output=True)
            if result.returncode:
                raise AssertionError(f'{patch.name}: {result.stdout}{result.stderr}')


@unittest.skipUnless(shutil.which('cc') and shutil.which('patch'),
                     'host C compiler and patch are required')
class StreamStartupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.work = Path(cls.tmp.name)
        cls.binaries = {}
        for legacy in (True, False):
            source = cls.work / ('legacy' if legacy else 'current')
            shutil.copytree(FIXTURES / 'uvc-gadget', source)
            apply_source_patches(source, legacy)
            (source / 'config.h').write_text('/* Host test configuration. */\n')
            for header in ('libavformat/avformat.h', 'libavcodec/avcodec.h',
                           'libavdevice/avdevice.h', 'libavutil/imgutils.h',
                           'libavutil/opt.h'):
                path = source / header
                path.parent.mkdir(exist_ok=True)
                path.write_text('#include "uvc-startup-ffmpeg.h"\n')
            binary = source / 'startup-test'
            command = ['cc', '-std=gnu11', '-D_GNU_SOURCE', '-g', '-O1',
                       '-Wall', '-Wextra', '-Wno-unused-function',
                       '-Wno-unused-parameter', '-Wno-sign-compare',
                       '-Wno-format', '-Wno-comment', '-pthread',
                       '-Werror=implicit-function-declaration',
                       '-Werror=incompatible-pointer-types',
                       '-I', str(source), '-I', str(source / 'lib'),
                       '-I', str(source / 'include/uvcgadget'),
                       '-I', str(FIXTURES),
                       '-DLEGACY_STARTUP=' + str(int(legacy)),
                       str(FIXTURES / 'uvc-startup-harness.c'), '-o', str(binary)]
            result = subprocess.run(command, text=True, capture_output=True)
            if result.returncode:
                raise AssertionError(result.stdout + result.stderr)
            cls.binaries[legacy] = binary

    def run_scenario(self, name, legacy=False):
        result = subprocess.run([str(self.binaries[legacy]), name],
                                text=True, capture_output=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_previous_startup_queues_empty_buffers(self):
        self.run_scenario('legacy', legacy=True)

    def test_delayed_frames_wakes_and_starvation_recovery(self):
        self.run_scenario('delayed')

    def test_oversize_packet_aborts_without_queueing_empty(self):
        self.run_scenario('oversize')

    def test_zero_packet_aborts_without_queueing_empty(self):
        self.run_scenario('zero')

    def test_encoder_failure_aborts_startup(self):
        self.run_scenario('encoder-error')

    def test_startup_timeout_releases_resources(self):
        self.run_scenario('timeout')

    def test_usb_queue_failure_aborts_startup(self):
        self.run_scenario('queue-error')

    def test_usb_stream_on_failure_releases_resources(self):
        self.run_scenario('stream-error')

    def test_stop_during_startup_is_responsive(self):
        self.run_scenario('stop')

    def test_producer_full_queue_stop_and_repeated_cycles(self):
        self.run_scenario('cycles')

    def test_restart_after_stop_ignores_stale_wakes(self):
        self.run_scenario('restart')

    def test_wedged_encoder_has_bounded_failure_exit(self):
        result = subprocess.run([str(self.binaries[False]), 'wedged-encoder'],
                                text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('worker shutdown failed', result.stderr.lower())


if __name__ == '__main__':
    unittest.main()
