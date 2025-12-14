from kivy.uix.boxlayout import BoxLayout
from kivy.uix.widget import Widget
from kivy.properties import ObjectProperty, NumericProperty
from kivy.uix.label import Label
from kivy.metrics import dp
import math
from kivy.uix.scrollview import ScrollView
from kivy.graphics import Color, Rectangle, Line
from sequencer.models import MidiTrack

class RulerContent(Widget):
    sequencer_layout = ObjectProperty(None)
    pixels_per_beat = NumericProperty(dp(100))
    total_beats = NumericProperty(16)
    beats_per_measure = NumericProperty(4)

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

        if self.total_beats <= 0 or self.beats_per_measure <= 0:
            return

        pixels_per_beat = self.pixels_per_beat
        num_measures = math.ceil(self.total_beats / self.beats_per_measure)

        with self.canvas.after:
            for i in range(1, num_measures + 2):
                beat_pos = (i - 1) * self.beats_per_measure
                x_pos = beat_pos * pixels_per_beat
                Color(0.4, 0.4, 0.4, 1)
                Line(points=[x_pos, self.y, x_pos, self.y + self.height], width=1)

        for i in range(1, num_measures + 2):
            beat_pos = (i - 1) * self.beats_per_measure
            x_pos = (beat_pos * pixels_per_beat)

            label = Label(
                text=str(i),
                font_size='10sp',
                pos=(x_pos, 0),
                size=(pixels_per_beat * self.beats_per_measure, self.height),
                halign='left',
                valign='middle',
                color=(0.8, 0.8, 0.8, 1),
                padding_x=dp(4)
            )
            label.text_size = label.size
            self.add_widget(label)

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            if not self.sequencer_layout or self.total_beats <= 0 or self.pixels_per_beat <= 0:
                return True

            local_x, _ = self.to_local(*touch.pos)
            clicked_beat = local_x / self.pixels_per_beat

            # Only seek if the sequencer is stopped
            if self.sequencer_layout.sequencer.playback_state == 'stopped':
                self.sequencer_layout.sequencer._resync_all_at_beat(clicked_beat)

            return True
        return super().on_touch_down(touch)

class Ruler(BoxLayout):
    sequencer_layout = ObjectProperty(None)
    scroll_view = ObjectProperty(None)
    info_width = NumericProperty(0)
    controls_width = NumericProperty(0)
    pixels_per_beat = NumericProperty(dp(100))
    keyboard_width = NumericProperty(0)
    total_beats = NumericProperty(16)
    beats_per_measure = NumericProperty(4)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.orientation = 'horizontal'
        self.spacing = dp(12)
        self.padding = [dp(12), dp(6), dp(12), dp(6)]

        self.left_spacer = Widget(size_hint_x=None)
        self.controls_spacer = Widget(size_hint_x=None)
        self.keyboard_spacer = Widget(size_hint_x=None, width=self.keyboard_width)
        self.scroll_view = ScrollView(size_hint_x=1, do_scroll_y=False)
        self.ruler_content = RulerContent(
            sequencer_layout=self.sequencer_layout,
            pixels_per_beat=self.pixels_per_beat,
            total_beats=self.total_beats,
            beats_per_measure=self.beats_per_measure,
            size_hint=(None, 1)
        )
        self.scroll_view.add_widget(self.ruler_content)

        self.add_widget(self.left_spacer)
        self.add_widget(self.controls_spacer)
        self.add_widget(self.keyboard_spacer)
        self.add_widget(self.scroll_view)

        self.bind(info_width=lambda i, v: setattr(self.left_spacer, 'width', v))
        self.bind(controls_width=lambda i, v: setattr(self.controls_spacer, 'width', v))
        self.bind(pixels_per_beat=lambda i, v: setattr(self.ruler_content, 'pixels_per_beat', v))
        self.bind(keyboard_width=lambda i, v: setattr(self.keyboard_spacer, 'width', v))
        self.bind(total_beats=lambda i, v: setattr(self.ruler_content, 'total_beats', v))
        self.bind(beats_per_measure=lambda i, v: setattr(self.ruler_content, 'beats_per_measure', v))


    def redraw(self, *args):
        self.ruler_content.width = self.total_beats * self.pixels_per_beat
        self.ruler_content.redraw(*args)
