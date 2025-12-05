from kivy.uix.widget import Widget
from kivy.properties import NumericProperty, ObjectProperty
from kivy.graphics import Color, Line
from kivy.uix.label import Label
from kivy.metrics import dp

class Ruler(Widget):
    sequencer_layout = ObjectProperty(None)
    scroll_x = NumericProperty(0)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.bind(pos=self.redraw, size=self.redraw, scroll_x=self.redraw)
        self.sequencer_layout.bind(on_kv_post=self.redraw)

    def redraw(self, *args):
        self.canvas.clear()
        self.clear_widgets()

        if not self.sequencer_layout or not self.sequencer_layout.track_widgets:
            return

        seq = self.sequencer_layout.sequencer
        track_widget = self.sequencer_layout.track_widgets[0]
        beats_per_measure = seq.song.time_signature_numerator
        total_beats = track_widget.total_beats

        if total_beats <= 0:
            return

        pixels_per_beat = track_widget.width / total_beats
        num_measures = int(total_beats / beats_per_measure)

        # Calculate the scroll offset in pixels
        scroll_offset_x = self.scroll_x * (track_widget.width - self.sequencer_layout.scroll_view.width)

        for i in range(1, num_measures + 2):
            beat_pos = (i - 1) * beats_per_measure
            # Adjust x_pos by the scroll offset
            x_pos = self.x + (beat_pos * pixels_per_beat) - scroll_offset_x

            # Culling: Only draw labels that are within or near the visible area
            if x_pos > self.right + 50 or (x_pos + (pixels_per_beat * beats_per_measure)) < self.x - 50:
                continue

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
                # Correctly call the handler on the sequencer_layout instance
                self.sequencer_layout.handle_ruler_click(touch)
                return True
        return super().on_touch_down(touch)
