import os
import hashlib
import json
from kivy.uix.widget import Widget
from kivy.graphics import Color, Mesh, Rectangle
from kivy.properties import NumericProperty, StringProperty
from kivy.clock import Clock
from kivy.metrics import dp
import threading

class AudioWaveform(Widget):
    """
    Widget that renders an audio waveform.
    It calculates min/max peaks in a background thread and caches the result.
    It automatically normalizes the peaks for visual consistency and fills the available height.
    """
    filepath = StringProperty("")
    pixels_per_beat = NumericProperty(dp(100))
    total_beats = NumericProperty(128)
    start_time = NumericProperty(0) # in beats
    tempo = NumericProperty(120)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._peaks = None # List of (min, max) peaks
        self._duration_seconds = 0
        self._loading_thread = None
        self.bind(filepath=self._on_filepath,
                  pixels_per_beat=self.redraw,
                  total_beats=self.redraw,
                  tempo=self.redraw,
                  pos=self.redraw,
                  size=self.redraw)
        if self.filepath:
            self._on_filepath(self, self.filepath)

    def _on_filepath(self, instance, value):
        if not value or not os.path.exists(value):
            self._peaks = None
            self.redraw()
            return

        self._start_loading_peaks()

    def _get_cache_path(self):
        if not self.filepath:
            return None
        try:
            mtime = os.path.getmtime(self.filepath)
        except OSError:
            return None

        hasher = hashlib.md5()
        hasher.update(os.path.basename(self.filepath).encode('utf-8'))
        hasher.update(str(mtime).encode('utf-8'))
        cache_key = hasher.hexdigest()

        cache_dir = os.path.join(".cache", "waveforms")
        os.makedirs(cache_dir, exist_ok=True)
        return os.path.join(cache_dir, f"{cache_key}.json")

    def _normalize_peaks(self, peaks):
        """Helper to normalize peaks to the [-1.0, 1.0] range."""
        if not peaks:
            return peaks
        try:
            import numpy as np
            peaks_arr = np.array(peaks)
            abs_max = np.max(np.abs(peaks_arr))
            if abs_max > 0:
                peaks_arr = peaks_arr / abs_max
            return peaks_arr.tolist()
        except Exception as e:
            print(f"Error normalizing peaks: {e}")
            return peaks

    def _start_loading_peaks(self):
        cache_path = self._get_cache_path()
        if cache_path and os.path.exists(cache_path):
            try:
                with open(cache_path, 'r') as f:
                    data = json.load(f)
                    peaks = data['peaks']
                    self._duration_seconds = data.get('duration_seconds', 0)
                    # Normalize peaks immediately after loading from cache
                    self._peaks = self._normalize_peaks(peaks)
                self.redraw()
                return
            except Exception:
                pass

        if self._loading_thread and self._loading_thread.is_alive():
            return

        self._loading_thread = threading.Thread(target=self._load_peaks_worker, args=(cache_path,))
        self._loading_thread.daemon = True
        self._loading_thread.start()

    def _load_peaks_worker(self, cache_path):
        try:
            import numpy as np
            from pydub import AudioSegment

            audio = AudioSegment.from_file(self.filepath)
            if audio.channels > 1:
                audio = audio.set_channels(1)

            samples = np.array(audio.get_array_of_samples())

            if audio.sample_width == 2: # 16-bit
                samples = samples.astype(np.float32) / 32768.0
            elif audio.sample_width == 1: # 8-bit
                samples = samples.astype(np.float32) / 128.0 - 1.0
            elif audio.sample_width == 4: # 32-bit int or float
                samples = samples.astype(np.float32) / 2147483648.0
            elif audio.sample_width == 3: # 24-bit
                samples = samples.astype(np.float32) / 8388608.0

            target_resolution = 300
            duration_beats = (len(audio) * self.tempo) / 60000.0
            num_peaks = int(duration_beats * target_resolution)
            if num_peaks < 200: num_peaks = 200

            duration_seconds = len(audio) / 1000.0

            chunk_size = len(samples) // num_peaks
            if chunk_size < 1: chunk_size = 1

            peaks = []
            for i in range(0, len(samples), chunk_size):
                chunk = samples[i:i+chunk_size]
                if len(chunk) == 0: continue
                peaks.append([float(np.min(chunk)), float(np.max(chunk))])

            if cache_path:
                try:
                    with open(cache_path, 'w') as f:
                        json.dump({
                            'peaks': peaks,
                            'duration_seconds': duration_seconds
                        }, f)
                except Exception as e:
                    print(f"Error saving cache: {e}")

            self._peaks = self._normalize_peaks(peaks)
            self._duration_seconds = duration_seconds

            Clock.schedule_once(self.redraw)
        except Exception as e:
            print(f"Error processing waveform for {self.filepath}: {e}")

    def redraw(self, *args):
        self.canvas.clear()

        duration_beats = (self._duration_seconds * self.tempo) / 60.0 if self._duration_seconds > 0 else 0
        if duration_beats == 0 or self._peaks is None or not self._peaks:
            return

        waveform_width = duration_beats * self.pixels_per_beat
        x_start = self.start_time * self.pixels_per_beat

        with self.canvas:
            # 1. Subtle background for the audio track span
            Color(0.2, 0.2, 0.25, 0.15)
            Rectangle(pos=(self.x + x_start, self.y), size=(waveform_width, self.height))

            if self._peaks is not None:
                # 2. Waveform peaks
                Color(0.3, 0.8, 1.0, 1.0) # Slightly more vibrant blue

                num_peaks = len(self._peaks)
                center_y = self.y + self.height / 2
                # Use 98% of height for the waveform, giving 1% margin top and bottom
                half_height = (self.height * 0.98) / 2

                vertices = []
                indices = []

                step = max(1, num_peaks // 8000)

                for i in range(0, num_peaks, step):
                    if step > 1:
                        chunk = self._peaks[i : min(i + step, num_peaks)]
                        p_min = min(p[0] for p in chunk)
                        p_max = max(p[1] for p in chunk)
                    else:
                        p_min, p_max = self._peaks[i]

                    rel_x = i / num_peaks
                    x = self.x + x_start + rel_x * waveform_width

                    y_min = center_y + p_min * half_height
                    y_max = center_y + p_max * half_height

                    if abs(y_max - y_min) < 1.0:
                        y_max = center_y + 0.5
                        y_min = center_y - 0.5

                    v_idx = len(vertices) // 4
                    vertices.extend([float(x), float(y_min), 0, 0,
                                     float(x), float(y_max), 0, 0])
                    indices.extend([v_idx, v_idx + 1])

                if vertices:
                    Mesh(vertices=vertices, indices=indices, mode='lines')
