from kivy.uix.boxlayout import BoxLayout
from kivy.uix.widget import Widget
from kivy.uix.relativelayout import RelativeLayout
from kivy.properties import ObjectProperty, NumericProperty, ListProperty, StringProperty
from kivy.uix.label import Label
from kivy.metrics import dp
import math
from kivy.uix.scrollview import ScrollView
from kivy.graphics import Color, Rectangle, Line

class RulerContent(RelativeLayout):
    sequencer_layout = ObjectProperty(None)
    pixels_per_beat = NumericProperty(dp(100))
    total_beats = NumericProperty(16)
    beats_per_measure = NumericProperty(4)
    label_padding_x = NumericProperty(dp(4))
    end_pos_str = StringProperty('')

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.drawing_widget = Widget(size_hint=(1, 1), pos=(0, 0))
        self.add_widget(self.drawing_widget)

        with self.drawing_widget.canvas.before:
            Color(0.18, 0.18, 0.18, 1)
            self.bg_rect = Rectangle(pos=(0, 0), size=self.size)

        self.bind(size=self._update_bg, end_pos_str=self.redraw,
                  total_beats=self.redraw, pixels_per_beat=self.redraw)

    def _update_bg(self, *args):
        self.bg_rect.size = self.size
        self.drawing_widget.size = self.size
        self.drawing_widget.pos = (0, 0)

    def redraw(self, *args):
        for child in list(self.children):
            if child is not self.drawing_widget:
                self.remove_widget(child)

        self.drawing_widget.canvas.clear()
        if self.total_beats <= 0 or self.beats_per_measure <= 0:
            return

        pixels_per_beat = self.pixels_per_beat
        num_measures = math.ceil(self.total_beats / self.beats_per_measure)

        with self.drawing_widget.canvas:
            for i in range(1, num_measures + 2):
                beat_pos = (i - 1) * self.beats_per_measure
                x_pos = beat_pos * pixels_per_beat
                Color(0.4, 0.4, 0.4, 1)
                Line(points=[x_pos, 0, x_pos, self.height], width=1)

            if self.sequencer_layout and self.end_pos_str:
                end_beat = self.sequencer_layout.sequencer.parse_position_to_beats(self.end_pos_str)
                if end_beat is not None:
                    end_x_pos = end_beat * pixels_per_beat
                    Color(0.2, 0.5, 0.8, 1)
                    Line(points=[end_x_pos, 0, end_x_pos, self.height], width=dp(1.5))

        for i in range(1, num_measures + 2):
            beat_pos = (i - 1) * self.beats_per_measure
            x_pos = beat_pos * pixels_per_beat

            label = Label(
                text=str(i),
                font_size='10sp',
                pos=(x_pos, 0),
                size=(pixels_per_beat * self.beats_per_measure, self.height),
                size_hint=(None, None),
                halign='left',
                valign='middle',
                color=(0.8, 0.8, 0.8, 1),
                padding_x=self.label_padding_x
            )
            label.text_size = label.size
            self.add_widget(label)

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            if not self.sequencer_layout or self.total_beats <= 0 or self.pixels_per_beat <= 0:
                return True
            local_x, _ = self.to_local(*touch.pos)
            clicked_beat = local_x / self.pixels_per_beat
            if self.sequencer_layout.sequencer.playback_state == 'stopped':
                if touch.button == 'left':
                    self.sequencer_layout.sequencer._resync_all_at_beat(clicked_beat)
                    new_pos_str = self.sequencer_layout.sequencer._format_beats_to_position(clicked_beat)
                    self.sequencer_layout.sequencer.ui_start_pos_str = new_pos_str
                    self.sequencer_layout.start_pos_input.text = new_pos_str
                elif touch.button == 'right':
                    new_pos_str = self.sequencer_layout.sequencer._format_beats_to_position(clicked_beat)
                    self.sequencer_layout.sequencer.ui_end_pos_str = new_pos_str
                    self.sequencer_layout.end_pos_input.text = new_pos_str
            return True
        return super().on_touch_down(touch)

class Ruler(BoxLayout):
    sequencer_layout = ObjectProperty(None)
    scroll_view = ObjectProperty(None)
    info_width = NumericProperty(dp(150))
    controls_width = NumericProperty(dp(430))
    keyboard_width = NumericProperty(dp(40))
    pixels_per_beat = NumericProperty(dp(100))
    total_beats = NumericProperty(16)
    beats_per_measure = NumericProperty(4)
    spacing = NumericProperty(dp(12))
    padding = ListProperty([0, 0, 0, 0])
    label_padding_x = NumericProperty(dp(4))
    end_pos_str = StringProperty('')

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.orientation = 'horizontal'
        self.spacing = dp(12)
        self.padding = [0, 0, 0, 0]

        # --- Exact mirror of TrackWidget layout ---
        self.ruler_left_panel = BoxLayout(
            orientation='horizontal',
            size_hint_x=None,
            spacing=self.spacing,
            width=self.info_width + self.controls_width + self.spacing
        )
        self.left_spacer = Widget(size_hint_x=None, width=self.info_width)
        self.controls_spacer = Widget(size_hint_x=None, width=self.controls_width)
        self.ruler_left_panel.add_widget(self.left_spacer)
        self.ruler_left_panel.add_widget(self.controls_spacer)

        self.keyboard_spacer = Widget(size_hint_x=None, width=self.keyboard_width)

        self.scroll_view = ScrollView(size_hint_x=1, do_scroll_y=False)
        self.ruler_content = RulerContent(
            sequencer_layout=self.sequencer_layout,
            pixels_per_beat=self.pixels_per_beat,
            total_beats=self.total_beats,
            beats_per_measure=self.beats_per_measure,
            end_pos_str=self.end_pos_str,
            size_hint=(None, 1)
        )
        self.scroll_view.add_widget(self.ruler_content)

        self.add_widget(self.ruler_left_panel)
        self.add_widget(self.keyboard_spacer)
        self.add_widget(self.scroll_view)

        # Dynamic updates
        def update_left_panel_width(*args):
             self.ruler_left_panel.width = self.info_width + self.controls_width + self.spacing

        self.bind(info_width=update_left_panel_width, controls_width=update_left_panel_width)
        self.bind(info_width=lambda i, v: setattr(self.left_spacer, 'width', v))
        self.bind(controls_width=lambda i, v: setattr(self.controls_spacer, 'width', v))
        self.bind(keyboard_width=lambda i, v: setattr(self.keyboard_spacer, 'width', v))
        self.bind(pixels_per_beat=lambda i, v: setattr(self.ruler_content, 'pixels_per_beat', v))
        self.bind(total_beats=lambda i, v: setattr(self.ruler_content, 'total_beats', v))
        self.bind(beats_per_measure=lambda i, v: setattr(self.ruler_content, 'beats_per_measure', v))
        self.bind(label_padding_x=lambda i,v: setattr(self.ruler_content, 'label_padding_x', v))
        self.bind(end_pos_str=lambda i, v: setattr(self.ruler_content, 'end_pos_str', v))

    def redraw(self, *args):
        self.ruler_content.width = self.total_beats * self.pixels_per_beat
        self.ruler_content.redraw(*args)
