from kivy.uix.widget import Widget
from kivy.uix.scrollview import ScrollView
from kivy.properties import NumericProperty, ObjectProperty, ListProperty
from kivy.metrics import dp
from kivy.graphics import Color, Rectangle, Line
from kivy.clock import Clock
import bisect
from sequencer.models import MidiTrack

class PianoRoll(Widget):
    """
    Represents the drawing area of the piano roll's grid and notes.
    This widget is intended to be placed inside a ScrollView.
    """
    total_beats = NumericProperty(128.0)
    pixels_per_beat = NumericProperty(dp(100))
    track = ObjectProperty(None, allownone=True)
    beat_per_measure = NumericProperty(4)
    note_height = NumericProperty(dp(12))
    editor = ObjectProperty(None, allownone=True)
    selected_notes = ListProperty([])
    drag_delta_beat = NumericProperty(0.0)
    drag_delta_pitch = NumericProperty(0)

    def __init__(self, **kwargs):
        super(PianoRoll, self).__init__(**kwargs)
        self.size_hint = (None, None)
        self._redraw_pending = False
        self._selected_ids_cache = None
        self._bound_svs = set()

        # Update width when beats or zoom changes
        self.bind(total_beats=self._update_geometry,
                  pixels_per_beat=self._update_geometry,
                  note_height=self._update_geometry)

        # Redraw when state or geometry changes
        self.bind(track=self.redraw,
                  pos=self.redraw,
                  size=self.redraw,
                  drag_delta_beat=self.redraw,
                  drag_delta_pitch=self.redraw)

        self.bind(selected_notes=self._on_selected_notes_change)

        self._update_geometry()
        self.redraw()

    def _on_selected_notes_change(self, *args):
        self._selected_ids_cache = None # Invalidate cache
        self.redraw()

    def _update_geometry(self, *args):
        """Update widget size based on track properties. Avoids redraw recursion."""
        new_width = self.total_beats * self.pixels_per_beat
        new_height = 128 * self.note_height

        size_changed = False
        if abs(self.width - new_width) > 0.001:
            self.width = new_width
            size_changed = True
        if abs(self.height - new_height) > 0.001:
            self.height = new_height
            size_changed = True

        if size_changed:
            self.redraw()

    def redraw(self, *args):
        """Debounced redraw of grid and notes."""
        if self._redraw_pending:
            return
        self._redraw_pending = True
        Clock.unschedule(self._do_redraw)
        Clock.schedule_once(self._do_redraw, 0)

    def _do_redraw(self, dt):
        self._redraw_pending = False
        if self.width <= 0 or self.height <= 0:
            return
        self.draw()

    _color_cache = {}
    def _velocity_to_color(self, velocity):
        """Converts MIDI velocity (0-127) to a color for visualization."""
        if velocity not in self._color_cache:
            normalized_velocity = velocity / 127.0
            red = normalized_velocity
            blue = 1.0 - normalized_velocity
            green = 0.3
            self._color_cache[velocity] = (red, green, blue, 0.9)
        return self._color_cache[velocity]

    def _get_viewport(self):
        """
        Calculate visible viewport by walking up the parent tree and aggregating
        scroll offsets from all parent ScrollViews.
        """
        vx, vy = 0, 0
        vw, vh = self.width, self.height
        x_res, y_res = False, False

        curr = self.parent
        while curr:
            if isinstance(curr, ScrollView):
                # Bind to the scrollview once to ensure we redraw when it moves
                if id(curr) not in self._bound_svs:
                    curr.bind(scroll_x=self.redraw, scroll_y=self.redraw)
                    self._bound_svs.add(id(curr))

                # We aggregate scroll offsets. Usually, one SV handles X and another handles Y.
                if curr.do_scroll_x and not x_res:
                    mw = max(0, self.width - curr.width)
                    vx = curr.scroll_x * mw
                    vw = curr.width
                    x_res = True
                if curr.do_scroll_y and not y_res:
                    mh = max(0, self.height - curr.height)
                    vy = curr.scroll_y * mh
                    vh = curr.height
                    y_res = True
            curr = curr.parent

        return vx, vy, vw, vh

    def draw(self, *args):
        if not self.canvas:
            return

        self.canvas.before.clear()
        self.canvas.clear()

        vx, vy, vw, vh = self._get_viewport()

        with self.canvas.before:
            # Background
            Color(0.1, 0.1, 0.12, 1)
            Rectangle(pos=(0, 0), size=self.size)

            # --- Grid Lines ---
            # Horizontal lines
            start_pitch = max(0, int(vy / self.note_height))
            end_pitch = min(127, int((vy + vh) / self.note_height) + 1)

            for i in range(start_pitch, end_pitch + 1):
                y = i * self.note_height
                if (i % 12) in [1, 3, 6, 8, 10]: # Black key rows
                    Color(0.15, 0.15, 0.17, 1)
                    Rectangle(pos=(0, y), size=(self.width, self.note_height))

                # Line between rows
                Color(0.2, 0.2, 0.22, 1)
                Line(points=[0, y, self.width, y], width=1)

                # Octave line
                if (i % 12) == 11: # Octave separation (between B and C)
                    Color(0.8, 0.8, 0.8, 0.3)
                    Line(points=[0, y + self.note_height, self.width, y + self.note_height], width=1.5)

            # Vertical lines
            ppb = self.pixels_per_beat
            start_beat = max(0, int(vx / ppb))
            end_beat = min(int(self.total_beats), int((vx + vw) / ppb) + 1)

            for i in range(start_beat, end_beat + 1):
                x = i * ppb
                if i % self.beat_per_measure == 0:
                    Color(0.8, 0.8, 0.8, 0.5)
                    Line(points=[x, 0, x, self.height], width=1.2)
                else:
                    Color(0.5, 0.5, 0.5, 0.2)
                    Line(points=[x, 0, x, self.height], width=1)

        # --- Notes ---
        if not (self.track and self.track.events):
            return

        if self._selected_ids_cache is None:
            self._selected_ids_cache = {id(n) for n in self.selected_notes}
            if self.editor and self.editor.selected_note:
                self._selected_ids_cache.add(id(self.editor.selected_note))

        selected_ids = self._selected_ids_cache

        with self.canvas:
            search_beat = max(0, (vx / ppb) - 32)
            start_idx = bisect.bisect_left(self.track.events, search_beat, key=lambda e: e.start_time)

            for i in range(start_idx, len(self.track.events)):
                event = self.track.events[i]
                note_x_orig = event.start_time * ppb

                # Stop if past viewport (allowing for drag delta)
                if note_x_orig > vx + vw + 32 * ppb:
                    break

                for note in event.notes:
                    is_selected = id(note) in selected_ids

                    eff_start = event.start_time + (self.drag_delta_beat if is_selected else 0)
                    eff_pitch = note.pitch + (self.drag_delta_pitch if is_selected else 0)

                    x = eff_start * ppb
                    y = eff_pitch * self.note_height
                    w = note.duration * ppb
                    h = self.note_height

                    # Clipping
                    if x + w < vx or x > vx + vw or y + h < vy or y > vy + vh:
                        continue

                    Color(*self._velocity_to_color(note.velocity))
                    Rectangle(pos=(x, y), size=(w, h))

                    if is_selected:
                        Color(1, 1, 1, 1)
                        Line(rectangle=(x, y, w, h), width=dp(1.5))
                    else:
                        Color(0, 0, 0, 0.4)
                        Line(rectangle=(x, y, w, h), width=1)

                    if w > dp(16):
                        handle_w = min(dp(8), w / 4)
                        Color(1, 1, 1, 0.3)
                        Rectangle(pos=(x, y), size=(handle_w, h))
                        Rectangle(pos=(x + w - handle_w, y), size=(handle_w, h))


class PianoRollViewer(ScrollView):
    """
    A scrollable container for the PianoRoll grid widget.
    It handles vertical scrolling for the grid part of the piano roll.
    """
    total_beats = NumericProperty(128.0)
    pixels_per_beat = NumericProperty(dp(100))
    track = ObjectProperty(None, allownone=True)
    note_height = NumericProperty(dp(12))

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
        if hasattr(self, 'grid'):
            self.grid.track = value

    def on_total_beats(self, instance, value):
        if hasattr(self, 'grid'):
            self.grid.total_beats = value

    def on_pixels_per_beat(self, instance, value):
        if hasattr(self, 'grid'):
            self.grid.pixels_per_beat = value

    def on_note_height(self, instance, value):
        if hasattr(self, 'grid'):
            self.grid.note_height = value
