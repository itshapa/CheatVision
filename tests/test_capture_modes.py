"""MODE picker sources: only what the device advertised, only devices that exist."""

from __future__ import annotations

import unittest
from unittest import mock

from src.core import frame_source
from src.core.frame_source import discover_directshow_devices, selectable_modes_from_ranges


class SelectableModesTests(unittest.TestCase):
    def test_only_rates_inside_each_advertised_span(self) -> None:
        ranges = {(2560, 1440): (23.976, 144.001), (1280, 720): (50.0, 60.0002)}
        modes = selectable_modes_from_ranges(ranges)
        self.assertIn((2560, 1440, 144), modes)
        self.assertIn((2560, 1440, 120), modes)
        self.assertIn((1280, 720, 60), modes)
        self.assertIn((1280, 720, 50), modes)
        self.assertNotIn((1280, 720, 30), modes)  # below the advertised floor
        self.assertNotIn((1920, 1080, 60), modes)  # never advertised at all

    def test_span_ceiling_kept_when_no_standard_step_lands_on_it(self) -> None:
        modes = selectable_modes_from_ranges({(1920, 1080): (30.0, 100.0)})
        self.assertIn((1920, 1080, 100), modes)

    def test_bandwidth_ceiling_filters_capture_card_modes(self) -> None:
        # 3840*2160*3 bytes * 120 fps ~= 2.99 GB/s: over the raw-pipe ceiling; 60 fps is under it.
        ranges = {(3840, 2160): (24.0, 120.0)}
        modes = selectable_modes_from_ranges(ranges)
        self.assertNotIn((3840, 2160, 120), modes)
        self.assertIn((3840, 2160, 60), modes)
        unfiltered = selectable_modes_from_ranges(ranges, apply_bandwidth_ceiling=False)
        self.assertIn((3840, 2160, 120), unfiltered)

    def test_sorted_largest_resolution_then_fastest(self) -> None:
        modes = selectable_modes_from_ranges({(1280, 720): (60.0, 60.0), (2560, 1440): (60.0, 144.0)})
        self.assertEqual(modes[0], (2560, 1440, 144))
        self.assertEqual(modes[-1], (1280, 720, 60))

    def test_nothing_advertised_offers_nothing(self) -> None:
        self.assertEqual(selectable_modes_from_ranges({}), [])


class DeviceDiscoveryTests(unittest.TestCase):
    def test_lists_every_present_device_with_no_cap(self) -> None:
        names = [f"Device {i}" for i in range(8)]
        with mock.patch.object(frame_source, "_list_directshow_video_names", return_value=names):
            devices = discover_directshow_devices()
        self.assertEqual([d["name"] for d in devices], names)

    def test_no_devices_gives_one_empty_placeholder_not_fakes(self) -> None:
        with mock.patch.object(frame_source, "_list_directshow_video_names", return_value=[]), mock.patch.object(
            frame_source, "ffmpeg_on_path", return_value=True
        ):
            devices = discover_directshow_devices()
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["name"], "")
        self.assertIn("No video devices", str(devices[0]["label"]))

    def test_placeholder_is_never_picked_as_a_device(self) -> None:
        placeholder = [{"label": "No video devices found", "name": "", "index": 0, "kind": "Input"}]
        self.assertIsNone(frame_source.pick_preferred_capture_device(placeholder))
        real = placeholder + [{"label": "Cam (DirectShow 1)", "name": "Cam", "index": 1, "kind": "Webcam"}]
        self.assertEqual(frame_source.pick_preferred_capture_device(real)["name"], "Cam")

    def test_missing_ffmpeg_is_named_as_the_reason(self) -> None:
        with mock.patch.object(frame_source, "_list_directshow_video_names", return_value=[]), mock.patch.object(
            frame_source, "ffmpeg_on_path", return_value=False
        ):
            devices = discover_directshow_devices()
        self.assertEqual(devices[0]["name"], "")
        self.assertIn("ffmpeg", str(devices[0]["label"]))


# Trimmed from `ffmpeg -list_options true -f dshow -i "video=AVerMedia HD Capture GC573 1"`
# on the development machine (FFmpeg 9.0): 1440p is advertised to 144 in every
# pixel format, 1080p to 240, 4K to 60. bgr24 is enumerated last; the P010
# block appears as an "unknown compression type" and must be ignored.
_GC573_LIST_OPTIONS = """
[dshow @ 0] DirectShow video device options (from video devices)
[dshow @ 0]  Pin "Capture" (alternative pin name "0")
[dshow @ 0]   pixel_format=yuyv422  min s=1920x1080 fps=24 max s=1920x1080 fps=240.004
[dshow @ 0]   pixel_format=yuyv422  min s=2560x1440 fps=59.9402 max s=2560x1440 fps=144.001
[dshow @ 0]   pixel_format=yuyv422  min s=3840x2160 fps=24 max s=3840x2160 fps=60.0002
[dshow @ 0]   pixel_format=nv12  min s=1920x1080 fps=24 max s=1920x1080 fps=240.004
[dshow @ 0]   pixel_format=nv12  min s=2560x1440 fps=59.9402 max s=2560x1440 fps=144.001
[dshow @ 0]   pixel_format=nv12  min s=3840x2160 fps=24 max s=3840x2160 fps=60.0002
[dshow @ 0]   unknown compression type 0x30313050  min s=2560x1440 fps=59.9402 max s=2560x1440 fps=144.001
[dshow @ 0]   pixel_format=bgr24  min s=1920x1080 fps=24 max s=1920x1080 fps=240.004
[dshow @ 0]   pixel_format=bgr24  min s=2560x1440 fps=59.9402 max s=2560x1440 fps=120
[dshow @ 0]   pixel_format=bgr24  min s=2560x1440 fps=144.001 max s=2560x1440 fps=144.001
[dshow @ 0]   pixel_format=bgr24  min s=3840x2160 fps=24 max s=3840x2160 fps=60.0002
[dshow @ 0]   pixel_format=bgr24  min s=1280x720 fps=50 max s=1280x720 fps=60.0002
"""


class HighRefreshLadderTests(unittest.TestCase):
    def test_common_high_refresh_steps_are_offered_when_advertised(self) -> None:
        modes = selectable_modes_from_ranges({(1920, 1080): (24.0, 240.004)})
        for fps in (240, 165, 144, 120, 100, 60):
            self.assertIn((1920, 1080, fps), modes, fps)
        self.assertEqual(modes[0], (1920, 1080, 240))

    def test_360_is_offered_only_where_advertised_and_affordable(self) -> None:
        # 1280x720 x 3 bytes x 360 = 1.0 GB/s: under the raw-pipe ceiling.
        self.assertIn((1280, 720, 360), selectable_modes_from_ranges({(1280, 720): (24.0, 360.0)}))
        # 1920x1080 x 3 x 360 = 2.24 GB/s: over it, so not offered for a capture card...
        self.assertNotIn((1920, 1080, 360), selectable_modes_from_ranges({(1920, 1080): (24.0, 360.0)}))
        # ...while a virtual camera (fixed software rate) skips that filter.
        self.assertIn(
            (1920, 1080, 360),
            selectable_modes_from_ranges({(1920, 1080): (24.0, 360.0)}, apply_bandwidth_ceiling=False),
        )
        # Never invented: a 240 ceiling does not grow a 360 entry.
        self.assertNotIn((1920, 1080, 360), selectable_modes_from_ranges({(1920, 1080): (24.0, 240.0)}))


class DeviceModeParsingTests(unittest.TestCase):
    def _source(self, **extra) -> frame_source.FrameSource:
        return frame_source.FrameSource(
            {
                "capture_mode": "camera",
                "capture_device_name": "AVerMedia HD Capture GC573 1",
                "capture_device_kind": "Capture Card",
                **extra,
            }
        )

    def test_gc573_output_parses_to_its_real_ceilings(self) -> None:
        ranges = self._source()._parse_device_modes(_GC573_LIST_OPTIONS)
        self.assertEqual(ranges[(2560, 1440)], (59.9402, 144.001))
        self.assertEqual(ranges[(1920, 1080)], (24.0, 240.004))
        self.assertEqual(ranges[(3840, 2160)], (24.0, 60.0002))
        modes = selectable_modes_from_ranges(ranges)
        self.assertIn((2560, 1440, 144), modes)
        self.assertIn((1920, 1080, 240), modes)
        self.assertNotIn((3840, 2160, 120), modes)  # never advertised

    def test_requested_pixel_format_scopes_the_parse(self) -> None:
        source = self._source(capture_pixel_format="nv12")
        self.assertEqual(source._card_pixel_format, "nv12")
        ranges = source._parse_device_modes(_GC573_LIST_OPTIONS, preferred_format=source._card_pixel_format)
        self.assertEqual(ranges[(2560, 1440)], (59.9402, 144.001))
        self.assertNotIn((1280, 720), ranges)  # only the bgr24 block lists it here
        # A format the device does not list falls back to bgr24, not to nothing.
        fallback = source._parse_device_modes(_GC573_LIST_OPTIONS, preferred_format="p010")
        self.assertIn((1280, 720), fallback)

    def test_inf_lower_bound_keeps_fixed_high_resolution_mode(self) -> None:
        output = (
            "pixel_format=bgr24 min s=2560x1440 fps=inf "
            "max s=2560x1440 fps=120\n"
        )
        ranges = self._source()._parse_device_modes(output)
        self.assertEqual(ranges[(2560, 1440)], (120.0, 120.0))
        self.assertIn((2560, 1440, 120), selectable_modes_from_ranges(ranges))

    def test_pixel_format_setting_normalisation(self) -> None:
        for raw in (None, "", "auto", "AUTO", "default", "driver"):
            self.assertIsNone(frame_source.normalize_pixel_format(raw), raw)
        self.assertEqual(frame_source.normalize_pixel_format(" NV12 "), "nv12")
        self.assertIsNone(self._source()._card_pixel_format)

    def test_elgato_without_bgr24_uses_nv12_modes(self) -> None:
        output = (
            "pixel_format=yuyv422  min s=1920x1080 fps=60 max s=1920x1080 fps=60\n"
            "pixel_format=nv12  min s=2560x1440 fps=50 max s=2560x1440 fps=120\n"
            "pixel_format=nv12  min s=1920x1080 fps=24 max s=1920x1080 fps=240\n"
        )
        source = self._source()
        by_format = source._parse_all_device_formats(output)
        order = source._format_try_order(by_format, None)
        self.assertEqual(order[0], "nv12")
        self.assertIn("yuyv422", order)
        self.assertNotIn("bgr24", order)
        ranges = source._parse_device_modes(output)
        self.assertEqual(ranges[(2560, 1440)], (50.0, 120.0))
        self.assertIn((2560, 1440, 120), selectable_modes_from_ranges(ranges))

    def test_unsupported_mode_error_is_not_busy(self) -> None:
        source = self._source()
        text = "Could not set video options | Error opening input: I/O error | Error opening input file video=Game Capture 4K60 Pro MK.2."
        self.assertTrue(source._error_means_unsupported_mode(text))
        self.assertFalse(source._error_means_busy(text))
        source.last_open_error = text
        self.assertIn("NV12", source.open_error_message())


class CalibrationFallbackTests(unittest.TestCase):
    def test_strict_fail_with_frames_reopens_degraded_live_capture(self) -> None:
        source = frame_source.FrameSource(
            {
                "capture_mode": "camera",
                "capture_device_name": "AVerMedia HD Capture GC573 1",
                "capture_device_kind": "Capture Card",
            }
        )
        probe = mock.Mock()
        probe.isOpened.return_value = True
        probe.read.return_value = (True, object())
        probe.get_last_error.return_value = ""

        def fake_measure(_device, width, height, fps):
            return False, 40, None

        with mock.patch.object(source, "_measure_candidate", side_effect=fake_measure), mock.patch.object(
            frame_source, "FFmpegRawVideoCapture", return_value=probe
        ), mock.patch.object(frame_source, "_wait_for_lingering_ffmpeg", return_value=0), mock.patch.object(
            frame_source.time, "sleep"
        ):
            width, height, fps, live = source._calibrate_capture_mode("card", [(1920, 1080, 60.0), (1280, 720, 60.0)])
        self.assertEqual((width, height, fps), (1920, 1080, 60.0))
        self.assertIs(live, probe)

    def test_zero_frame_io_error_does_not_reopen_the_rejected_mode(self) -> None:
        source = frame_source.FrameSource(
            {
                "capture_mode": "camera",
                "capture_device_name": "Game Capture 4K60 Pro MK.2",
                "capture_device_kind": "Capture Card",
            }
        )
        source.last_open_error = "Could not set video options"
        with mock.patch.object(source, "_measure_candidate", return_value=(False, 0, None)), mock.patch.object(
            frame_source, "FFmpegRawVideoCapture"
        ) as ctor, mock.patch.object(frame_source, "_wait_for_lingering_ffmpeg", return_value=0), mock.patch.object(
            frame_source.time, "sleep"
        ):
            width, height, fps, live = source._calibrate_capture_mode("card", [(2560, 1440, 120.0)])
        self.assertEqual((width, height, fps), (2560, 1440, 120.0))
        self.assertIsNone(live)
        ctor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
