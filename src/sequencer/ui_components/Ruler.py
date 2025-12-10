from kivy.uix.boxlayout import BoxLayout
from kivy.uix.widget import Widget
from kivy.properties import ObjectProperty, NumericProperty
from kivy.uix.label import Label
from kivy.metrics import dp
from kivy.uix.scrollview import ScrollView
from kivy.graphics import Color, Rectangle, Line
from sequencer.models import MidiTrack

class RulerContent(Widget):
    sequencer_layout = ObjectProperty(None)
    pixels_per_beat = NumericProperty(dp(100))

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        with self.canvas.before:
            Color(0.18, 0.18, 0.18, 1)
            self.bg_rect = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=self._update_bg, size=self._update_bg)

    def _update_bg(self, *args):
        self.bg_rect.pos = self.pos
        self.bg_rect.size = self.size

    def redraw(self, *args):
        self.canvas.after.clear()
        self.clear_widgets()

        if not self.sequencer_layout or not self.sequencer_layout.track_widgets:
            return

        track_widget = self.sequencer_layout.track_widgets[0]
        seq = self.sequencer_layout.sequencer
        beats_per_measure = seq.song.time_signature_numerator
        total_beats = track_widget.total_beats

        if total_beats <= 0:
            return

        pixels_per_beat = self.pixels_per_beat
        num_measures = int(total_beats / beats_per_measure)

        with self.canvas.after:
            for i in range(1, num_measures + 2):
                beat_pos = (i - 1) * beats_per_measure
                x_pos = beat_pos * pixels_per_beat
                Color(0.4, 0.4, 0.4, 1)
                Line(points=[x_pos, self.y, x_pos, self.y + self.height], width=1)

        for i in range(1, num_measures + 2):
            beat_pos = (i - 1) * beats_per_measure
            x_pos = (beat_pos * pixels_per_beat) # Position relative to self

            label = Label(
                text=str(i),
                font_size='10sp',
                pos=(x_pos, 0),
                size=(pixels_per_beat * beats_per_measure, self.height),
                halign='left',
                valign='middle',
                color=(0.8, 0.8, 0.8, 1),
                padding_x=dp(4)
            )
            label.text_size = label.size
            self.add_widget(label)

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            local_x, _ = self.to_local(*touch.pos)
            if not self.sequencer_layout.track_widgets: return True

            track_widget = self.sequencer_layout.track_widgets[0]
            if track_widget.total_beats <= 0: return True

            if self.pixels_per_beat <= 0: return True

            clicked_beat = local_x / self.pixels_per_beat

            self.sequencer_layout.sequencer._resync_all_at_beat(clicked_beat)

            return True
        return super().on_touch_down(touch)

class Ruler(BoxLayout):
    sequencer_layout = ObjectProperty(None)
    scroll_view = ObjectProperty(None)
    info_width = NumericProperty(0)
    controls_width = NumericProperty(0)
    pixels_per_beat = NumericProperty(dp(100))
    keyboard_width = NumericProperty(0) # New property for keyboard spacer

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.orientation = 'horizontal'
        self.spacing = dp(12)
        self.padding = [dp(12), 0, dp(12), 0]

        self.left_spacer = Widget(size_hint_x=None)
        self.keyboard_spacer = Widget(size_hint_x=None, width=self.keyboard_width) # Keyboard spacer
        self.scroll_view = ScrollView(size_hint_x=1, do_scroll_y=False)
        self.ruler_content = RulerContent(
            sequencer_layout=self.sequencer_layout,
            pixels_per_beat=self.pixels_per_beat,
            size_hint=(None, 1)
        )
        self.scroll_view.add_widget(self.ruler_content)
        self.right_spacer = Widget(size_hint_x=None)

        self.add_widget(self.left_spacer)
        self.add_widget(self.keyboard_spacer) # Add spacer to layout
        self.add_widget(self.scroll_view)
        self.add_widget(self.right_spacer)

        self.bind(info_width=lambda i, v: setattr(self.left_spacer, 'width', v))
        self.bind(controls_width=lambda i, v: setattr(self.right_spacer, 'width', v))
        self.bind(pixels_per_beat=lambda i, v: setattr(self.ruler_content, 'pixels_per_beat', v))
        self.bind(keyboard_width=lambda i, v: setattr(self.keyboard_spacer, 'width', v)) # Bind new property

    def redraw(self, *args):
        if self.sequencer_layout and self.sequencer_layout.track_widgets:
            first_track = self.sequencer_layout.track_widgets[0]
            # The ruler content should match the grid's width, not the whole container
            if isinstance(first_track.track, MidiTrack):
                 self.ruler_content.width = first_track.piano_roll_viewer.width
            else:
                 self.ruler_content.width = first_track.timeline_container.width

        self.ruler_content.redraw(*args)
