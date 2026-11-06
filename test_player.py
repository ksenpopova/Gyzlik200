import importlib.util
import json
import os
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import numpy as np
import soundfile as sf
from PyQt6.QtMultimedia import QMediaPlayer
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from player_eq import Cancelled, render_eq
from player_storage import write_profile

spec = importlib.util.spec_from_file_location("player", Path(__file__).with_name("main (3).py"))
player = importlib.util.module_from_spec(spec)
spec.loader.exec_module(player)
app = QApplication.instance() or QApplication([])


class PlayerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.profile = self.root / "app_data.json"
        environment = patch.dict(os.environ, {"AUDIOFLOW_PROFILE_DIR": directory.name})
        environment.start()
        self.addCleanup(environment.stop)

    def make_player(self):
        window = player.MP3Player()
        window.audio_output.setMuted(True)
        self.addCleanup(window.close)
        return window

    def test_profiles_and_lock(self):
        absent = str(self.root / "offline.mp3")
        original = {"all_playlists": {"Test": [{"path": absent, "title": 123}]},
                    "settings": {"equalizer_gains": {"60": 7.2}}}
        self.profile.write_text(json.dumps(original), encoding="utf-8")
        window = self.make_player()
        self.assertEqual(window.settings_view_widget.eq_sliders[60].value(), 72)
        self.assertEqual(window.active_playlist_tracks[0]["title"], "offline.mp3")
        self.assertTrue(self.make_player()._profile_readonly)
        window.save_app_data()
        actual = json.loads(self.profile.read_text(encoding="utf-8"))
        self.assertEqual(actual["all_playlists"]["Test"][0]["path"], absent)
        self.assertEqual(actual["settings"]["equalizer_gains"]["60"], 7.2)
        self.assertEqual(json.loads(self.profile.with_suffix(".json.bak").read_text()), original)

    def test_corrupt_profile_is_not_overwritten(self):
        self.profile.write_bytes(b'{"broken":')
        self.make_player().save_app_data()
        self.assertEqual(self.profile.read_bytes(), b'{"broken":')

    def test_interrupted_replace_keeps_old_profile(self):
        write_profile(self.profile, {"old": True})
        write_profile(self.profile, {"old": True})
        with patch("player_storage.os.replace", side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                write_profile(self.profile, {"new": True})
        self.assertEqual(json.loads(self.profile.read_text()), {"old": True})

    def test_real_qt_auto_advance_and_empty_playlist(self):
        first, second = self.root / "first.wav", self.root / "second.wav"
        sf.write(first, np.zeros(2400), 24000)
        sf.write(second, np.zeros(48000), 24000)
        tracks = [{"path": str(p), "title": p.stem} for p in (first, second)]
        self.profile.write_text(json.dumps({"all_playlists": {"Test": tracks, "Empty": []}}))
        window = self.make_player()
        window.load_track(autoplay=True)
        for _ in range(100):
            QTest.qWait(25)
            if window.current_index == 1 and window.media_player.isPlaying():
                break
        self.assertEqual(window.current_index, 1)
        self.assertTrue(window.media_player.isPlaying())
        window.switch_to_playlist("Empty")
        self.assertTrue(window.media_player.source().isEmpty())
        self.assertEqual(window.media_player.playbackState(), QMediaPlayer.PlaybackState.StoppedState)

    def test_eq_keeps_stereo_and_prevents_overload(self):
        source, target = self.root / "source.wav", self.root / "target.wav"
        rate = 24000
        tone = 0.9 * np.sin(2 * np.pi * 600 * np.arange(rate * 2) / rate)
        sf.write(source, np.stack((tone, -tone), axis=1), rate, subtype="FLOAT")
        render_eq(source, target, {600: 12.0}, Event())
        audio, out_rate = sf.read(target)
        self.assertEqual(audio.shape, (len(tone), 2))
        self.assertEqual(out_rate, rate)
        self.assertGreater(np.max(np.abs(audio)), 0.5)
        self.assertLessEqual(np.max(np.abs(audio)), 0.981)
        np.testing.assert_allclose(audio[:, 0], -audio[:, 1], atol=3e-7)
        cancel = Event()
        cancel.set()
        with self.assertRaises(Cancelled):
            render_eq(source, target, {600: 12.0}, cancel)
        self.assertFalse(target.exists())
        self.assertFalse(target.with_suffix(".working.wav").exists())

    def test_eq_job_cannot_replace_source_after_switch(self):
        source = self.root / "source.wav"
        sf.write(source, np.zeros(24000), 24000)
        self.profile.write_text(json.dumps({"all_playlists": {
            "Test": [{"path": str(source)}], "Empty": []
        }}))
        window = self.make_player()
        started = Event()
        def cancelled_render(source, target, gains, cancel):
            started.set()
            if not cancel.wait(5):
                raise AssertionError("Cancellation was not propagated")
            raise Cancelled
        with patch.object(player, "render_eq", side_effect=cancelled_render):
            window.process_and_apply_equalizer({600: 6})
            self.assertTrue(started.wait(2))
            window.switch_to_playlist("Empty")
            for _ in range(40):
                if window._eq_job is None:
                    break
                QTest.qWait(25)
            self.assertIsNone(window._eq_job)
        self.assertTrue(window.media_player.source().isEmpty())
        self.assertIsNone(window.current_processed_track_path)

    def test_settings_arrow_controls_slider_not_volume(self):
        window = self.make_player()
        window.show()
        window.show_settings_view()
        slider = window.settings_view_widget.eq_sliders[60]
        slider.setFocus()
        QTest.qWait(50)
        volume = window.audio_output.volume()
        QTest.keyClick(slider, Qt.Key.Key_Up)
        self.assertGreater(slider.value(), 0)
        self.assertEqual(window.audio_output.volume(), volume)


if __name__ == "__main__":
    unittest.main()
