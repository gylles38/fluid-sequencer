from kivy.uix.boxlayout import BoxLayout
from kivy.uix.widget import Widget
from kivy.properties import ObjectProperty
from kivy.uix.label import Label
from kivy.metrics import dp

class RulerContent(Widget):
    sequencer_layout = ObjectProperty(None)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.bind(pos=self.redraw, size=self.redraw)

    def redraw(self, *args):
        self.canvas.clear()
        self.clear_widgets()

        if not self.sequencer_layout or not self.sequencer_layout.track_widgets:
            return

        track_widget = self.sequencer_layout.track_widgets[0]
        self.width = track_widget.timeline_container.width

        seq = self.sequencer_layout.sequencer
        beats_per_measure = seq.song.time_signature_numerator
        total_beats = track_widget.total_beats

        if total_beats <= 0:
            return

        pixels_per_beat = self.width / total_beats
        num_measures = int(total_beats / beats_per_measure)

        for i in range(1, num_measures + 2):
            beat_pos = (i - 1) * beats_per_measure
            x_pos = self.x + (beat_pos * pixels_per_beat)

            label = Label(
                text=str(i),
                font_size='10sp',
                pos=(x_pos, self.y),
                size=(pixels_per_beat * beats_per_measure, self.height),
                halign='left',
                valign='middle',
                color=(0.8, 0.8, 0.8, 1)
            )
            label.text_size = label.size
            self.add_widget(label)

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            if self.sequencer_layout.sequencer.playback_state == "stopped":
                self.sequencer_layout.handle_ruler_click(touch)
                return True
        return super().on_touch_down(touch)

class Ruler(BoxLayout):
    sequencer_layout = ObjectProperty(None)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.orientation = 'horizontal'

        self.left_spacer = Widget(size_hint_x=None, width=0)
        self.ruler_content = RulerContent(sequencer_layout=self.sequencer_layout)
        self.right_spacer = Widget(size_hint_x=None, width=0)

        self.add_widget(self.left_spacer)
        self.add_widget(self.ruler_content)
        self.add_widget(self.right_spacer)

    def redraw(self, *args):
        self.ruler_content.redraw(*args)
