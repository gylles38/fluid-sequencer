from kivy.uix.widget import Widget
from kivy.uix.scrollview import ScrollView
from kivy.properties import NumericProperty, ObjectProperty, ListProperty
from kivy.metrics import dp
from kivy.clock import Clock
from kivy.graphics import Color, Rectangle, Line, Mesh
from sequencer.models import MidiTrack
import bisect

class PianoRoll(Widget):
    """
    Represents the drawing area of the piano roll's grid and notes.
    """
    total_beats = NumericProperty(128.0)
    pixels_per_beat = NumericProperty(dp(100))
    track = ObjectProperty(None, allownone=True)
    beat_per_measure = NumericProperty(4)
    note_height = NumericProperty(round(dp(14)))
    editor = ObjectProperty(None, allownone=True)
    selected_notes = ListProperty([])

    # Virtual drag offsets for performance
    drag_delta_beat = NumericProperty(0.0)
    drag_delta_pitch = NumericProperty(0)

    def __init__(self, **kwargs):
        super(PianoRoll, self).__init__(**kwargs)
        self.size_hint = (None, None)
        self.width = self.total_beats * self.pixels_per_beat
        self.height = round(128 * self.note_height)
        self._scroll_view_cache = None
        self._selected_ids_cache = set()

        self.bind(total_beats=self._update_size,
                  pixels_per_beat=self._update_size,
                  note_height=self._update_size)

        self.bind(pos=self.redraw, size=self.redraw,
                  track=self.redraw,
                  drag_delta_beat=self.redraw, drag_delta_pitch=self.redraw)
        self.redraw()

    def _update_size(self, *args):
        # Optimization: Only update if changed to avoid triggering listeners
        target_w = self.total_beats * self.pixels_per_beat
        target_h = round(128 * self.note_height)
        if self.width != target_w: self.width = target_w
        if self.height != target_h:
            self.height = target_h
            # Update children height (like PlaybackLine)
            for child in self.children:
                if getattr(child, 'size_hint_y', None) == 1:
                    child.height = target_h

    def on_selected_notes(self, instance, value):
        self._selected_ids_cache = {id(n) for n in value}
        self.redraw()

    def redraw(self, *args):
        """Debounced redraw of the canvas."""
        if getattr(self, '_redraw_pending', False):
            return
        self._redraw_pending = True
        Clock.unschedule(self.draw)
        Clock.schedule_once(self.draw, 0)

    def _velocity_to_color(self, velocity):
        """Converts MIDI velocity (0-127) to a color for visualization."""
        normalized_velocity = velocity / 127.0
        red = normalized_velocity
        blue = 1.0 - normalized_velocity
        green = 0.3
        return (red, green, blue, 0.9)

    def get_viewport_info(self):
        """Returns (beat_start, beat_end, pitch_start, pitch_end) currently visible."""
        p = self._scroll_view_cache
        if not p or p.parent is None: # Validate cache
            p = self.parent
            while p and not isinstance(p, ScrollView):
                p = p.parent
            self._scroll_view_cache = p

        if not p:
            return 0, self.total_beats, 0, 127

        # Horizontal range
        max_scroll_x = max(0, self.width - p.width)
        view_start_x = p.scroll_x * max_scroll_x
        beat_start = view_start_x / self.pixels_per_beat
        beat_end = (view_start_x + p.width) / self.pixels_per_beat

        # Vertical range (ScrollView uses scroll_y from 0 to 1, where 1 is top)
        max_scroll_y = max(0, self.height - p.height)
        view_start_y = p.scroll_y * max_scroll_y
        pitch_start = int(view_start_y / self.note_height)
        pitch_end = int((view_start_y + p.height) / self.note_height)

        return beat_start, beat_end, pitch_start, pitch_end

    def draw(self, *args):
        self._redraw_pending = False
        if not self.canvas or not self.parent: return
        self.canvas.clear()

        beat_start, beat_end, pitch_start, pitch_end = self.get_viewport_info()

        # Local copies of hot properties for speed
        ppb = float(self.pixels_per_beat)
        nh = float(self.note_height)
        ox, oy = float(self.x), float(self.y)

        with self.canvas:
            # Main background (drawn only for visible area)
            view_x = ox + (beat_start * ppb)
            view_w = (beat_end - beat_start) * ppb
            Color(0.1, 0.1, 0.12, 1)
            Rectangle(pos=(view_x, oy), size=(view_w, self.height))

            # --- Row backgrounds for black keys ---
            Color(0.14, 0.14, 0.16, 1)
            for i in range(max(0, pitch_start), min(128, pitch_end + 1)):
                if (i % 12) in [1, 3, 6, 8, 10]:
                    y_start = round(i * nh)
                    y_end = round((i + 1) * nh)
                    Rectangle(pos=(view_x, oy + y_start), size=(view_w, y_end - y_start))

            # --- Horizontal Grid Lines using Mesh ---
            black_keys_vertices = []
            white_keys_vertices = []
            octave_vertices = []

            for i in range(129):
                line_y = round(i * self.note_height)
                if (i % 12) == 0:
                    octave_vertices.extend([self.x, self.y + line_y, 0, 0, self.x + self.width, self.y + line_y, 0, 0])
                elif (i % 12) == 5:
                    white_keys_vertices.extend([self.x, self.y + line_y, 0, 0, self.x + self.width, self.y + line_y, 0, 0])
                else:
                    black_keys_vertices.extend([self.x, self.y + line_y, 0, 0, self.x + self.width, self.y + line_y, 0, 0])

            if black_keys_vertices:
                Color(0.12, 0.12, 0.14, 1)
                Mesh(vertices=black_keys_vertices, indices=list(range(len(black_keys_vertices)//4)), mode='lines')
            if white_keys_vertices:
                Color(0.18, 0.18, 0.20, 1)
                Mesh(vertices=white_keys_vertices, indices=list(range(len(white_keys_vertices)//4)), mode='lines')
            if octave_vertices:
                Color(0.4, 0.4, 0.45, 0.8)
                Mesh(vertices=octave_vertices, indices=list(range(len(octave_vertices)//4)), mode='lines')

            # Vertical grid lines (Viewport clipped)
            major_vertices = []
            minor_vertices = []
            for i in range(int(beat_start), int(beat_end) + 2):
                if i > self.total_beats: break
                x_pos = round(i * self.pixels_per_beat)
                if i % self.beat_per_measure == 0:
                    major_vertices.extend([self.x + x_pos, self.y, 0, 0, self.x + x_pos, self.y + self.height, 0, 0])
                else:
                    minor_vertices.extend([self.x + x_pos, self.y, 0, 0, self.x + x_pos, self.y + self.height, 0, 0])

            if major_vertices:
                Color(0.8, 0.8, 0.8, 0.8)
                Mesh(vertices=major_vertices, indices=list(range(len(major_vertices)//4)), mode='lines')
            if minor_vertices:
                Color(0.5, 0.5, 0.5, 0.4)
                Mesh(vertices=minor_vertices, indices=list(range(len(minor_vertices)//4)), mode='lines')

        # --- Notes (Viewport clipped and optimized) ---
        if isinstance(self.track, MidiTrack) and self.track.events:
            selected_ids = self._selected_ids_cache
            is_dragging = (self.drag_delta_beat != 0 or self.drag_delta_pitch != 0)

            with self.canvas:
                # 1. Draw unselected notes and non-dragged notes
                search_start = max(0, beat_start - 8)
                events = self.track.events
                start_idx = bisect.bisect_left(events, search_start, key=lambda e: e.start_time)

                last_color = None
                for i in range(start_idx, len(events)):
                    event = events[i]
                    if event.start_time > beat_end: break

                    for note in event.notes:
                        is_selected = id(note) in selected_ids
                        if is_selected and is_dragging: continue

                        if not (pitch_start - 2 <= note.pitch <= pitch_end + 2): continue
                        if event.start_time + note.duration < beat_start: continue

                        x_start = round(event.start_time * ppb)
                        x_end = round((event.start_time + note.duration) * ppb)
                        y_start = round(note.pitch * nh)
                        y_end = round((note.pitch + 1) * nh)

                        note_x, note_y = ox + x_start, oy + y_start
                        note_width, note_h = x_end - x_start, y_end - y_start
                        note_color = self._velocity_to_color(note.velocity)

                        if note_color != last_color:
                            Color(*note_color)
                            last_color = note_color

                        Rectangle(pos=(note_x, note_y), size=(note_width, note_h))

                        # Skip handles if too small or during heavy drag for performance
                        if note_width > dp(16) and not is_dragging:
                            handle_width = min(dp(8), note_width / 4)
                            handle_color = (min(1.0, note_color[0] * 1.2), min(1.0, note_color[1] * 1.2), min(1.0, note_color[2] * 1.2), 1.0)
                            Color(*handle_color)
                            last_color = None # Reset color state
                            Rectangle(pos=(note_x, note_y), size=(handle_width, note_h))
                            Rectangle(pos=(note_x + note_width - handle_width, note_y), size=(handle_width, note_h))

                        if is_selected:
                            Color(1, 1, 1, 1)
                            last_color = None
                            Line(rectangle=(note_x, note_y, note_width, note_h), width=1.1)

                # 2. Draw dragged selected notes
                if is_dragging and hasattr(self, '_multi_drag_data'):
                    ddb, ddp = float(self.drag_delta_beat), int(self.drag_delta_pitch)
                    last_color = None

                    for item in self._multi_drag_data:
                        note = item['note']
                        start_time = max(0.0, item['original_start'] + ddb)
                        pitch = max(0, min(127, item['original_pitch'] + ddp))

                        if start_time > beat_end: continue
                        if start_time + note.duration < beat_start: continue
                        if not (pitch_start - 2 <= pitch <= pitch_end + 2): continue

                        x_start = round(start_time * ppb)
                        x_end = round((start_time + note.duration) * ppb)
                        y_start = round(pitch * nh)
                        y_end = round((pitch + 1) * nh)

                        note_x, note_y = ox + x_start, oy + y_start
                        note_width, note_h = x_end - x_start, y_end - y_start
                        note_color = self._velocity_to_color(note.velocity)

                        if note_color != last_color:
                            Color(*note_color)
                            last_color = note_color

                        Rectangle(pos=(note_x, note_y), size=(note_width, note_h))

                        # Simplified drag feedback: White outline for ALL dragged notes
                        Color(1, 1, 1, 1)
                        last_color = None
                        Line(rectangle=(note_x, note_y, note_width, note_h), width=1.1)


class PianoRollViewer(ScrollView):
    """
    A scrollable container for the PianoRoll grid widget.
    It handles vertical scrolling for the grid part of the piano roll.
    """
    total_beats = NumericProperty(128.0)
    pixels_per_beat = NumericProperty(dp(100))
    track = ObjectProperty(None, allownone=True)
    note_height = NumericProperty(dp(14))

    def __init__(self, **kwargs):
        super(PianoRollViewer, self).__init__(**kwargs)
        self.size_hint_x = None
        self.do_scroll_x = False
        self.do_scroll_y = True

        self.grid = PianoRoll(
            track=self.track,
            total_beats=self.total_beats,
            pixels_per_beat=self.pixels_per_beat,
            note_height=self.note_height
        )
        self.add_widget(self.grid)
        self.grid.bind(width=self.setter('width'))

    def on_track(self, instance, value):
        if hasattr(self, 'grid'): self.grid.track = value

    def on_total_beats(self, instance, value):
        if hasattr(self, 'grid'): self.grid.total_beats = value

    def on_pixels_per_beat(self, instance, value):
        if hasattr(self, 'grid'): self.grid.pixels_per_beat = value

    def on_note_height(self, instance, value):
        if hasattr(self, 'grid'): self.grid.note_height = value
