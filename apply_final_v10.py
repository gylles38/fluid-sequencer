import re

filepath = 'src/sequencer/ui_components/piano_roll_editor.py'
with open(filepath, 'r') as f:
    content = f.read()

# 1. Refactor EditorPianoKeyboard to use RelativeLayout with local coords
new_keyboard_class = """class EditorPianoKeyboard(RelativeLayout):
    note_height = NumericProperty(round(dp(14)))
    bottom_padding = NumericProperty(0)
    highlighted_note = NumericProperty(-1)

    def on_note_height(self, instance, value): self._update_total_height()
    def on_bottom_padding(self, instance, value): self._update_total_height()

    def _update_total_height(self):
        new_h = round(128 * self.note_height) + self.bottom_padding
        if abs(self.height - new_h) > 0.001:
            self.height = new_h

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.size_hint = (None, None)
        self.width = dp(60)
        self._label_widgets = []
        self._update_total_height()
        self.bind(size=self._redraw_on_schedule,
                  note_height=self._redraw_on_schedule,
                  bottom_padding=self._redraw_on_schedule,
                  highlighted_note=self._redraw_on_schedule)
        self._redraw_on_schedule()

    def _redraw_on_schedule(self, *args):
        if getattr(self, "_redraw_pending", False): return
        self._redraw_pending = True
        Clock.schedule_once(self._redraw, 0)

    def _redraw(self, *args):
        self._redraw_pending = False
        if not self.canvas: return
        self.canvas.clear()
        highlight_color = (0.3, 0.7, 1.0, 1)
        with self.canvas:
            # White keys
            for i in range(128):
                ys = round(i * self.note_height) + self.bottom_padding
                ye = round((i + 1) * self.note_height) + self.bottom_padding
                if (i % 12) not in [1, 3, 6, 8, 10]:
                    Color(*(highlight_color if i == self.highlighted_note else (0.95, 0.95, 0.95, 1)))
                    Rectangle(pos=(0, ys), size=(self.width, ye - ys))
            # Black keys
            for i in range(128):
                ys = round(i * self.note_height) + self.bottom_padding
                ye = round((i + 1) * self.note_height) + self.bottom_padding
                if (i % 12) in [1, 3, 6, 8, 10]:
                    Color(*(highlight_color if i == self.highlighted_note else (0.1, 0.1, 0.1, 1)))
                    Rectangle(pos=(0, ys), size=(self.width * 0.65, ye - ys))
            # Separators
            for i in range(1, 129):
                yp = round(i * self.note_height) + self.bottom_padding
                if i % 12 == 0: Color(0.4, 0.4, 0.4, 0.8)
                elif i % 12 == 5: Color(0.6, 0.6, 0.6, 0.6)
                else: Color(0.7, 0.7, 0.7, 0.4)
                Line(points=[0, yp, self.width, yp], width=1.1)

        # Labels for C notes
        indices = [i for i in range(128) if i % 12 == 0]
        while len(self._label_widgets) < len(indices):
            lbl = Label(font_size=dp(10), color=(0, 0, 0, 1), size_hint=(None, None), halign="center", valign="middle", bold=True)
            self.add_widget(lbl); self._label_widgets.append(lbl)
        while len(self._label_widgets) > len(indices): self.remove_widget(self._label_widgets.pop())
        for idx, i in enumerate(indices):
            ys = round(i * self.note_height) + self.bottom_padding
            ye = round((i + 1) * self.note_height) + self.bottom_padding
            lbl = self._label_widgets[idx]
            lbl.text = f"C{i // 12 - 1}"; lbl.size = (self.width, ye - ys)
            lbl.pos = (0, ys)
            lbl.text_size = lbl.size
            lbl.texture_update()
"""

# Regex to find and replace the class
content = re.sub(r'class EditorPianoKeyboard\(.*?\):.*?class EditHistoryManager',
                 new_keyboard_class + '\n\nclass EditHistoryManager',
                 content, flags=re.DOTALL)

# 2. Re-establish bi-directional scroll sync in KV
# keyboard_sv: scroll_y: timeline_scroll.scroll_y (already there)
# timeline_scroll: scroll_y: keyboard_sv.scroll_y (needs restoration)

content = content.replace('                id: timeline_scroll', '                id: timeline_scroll\n                scroll_y: keyboard_sv.scroll_y')

with open(filepath, 'w') as f:
    f.write(content)
print("Final implementation: EditorPianoKeyboard as RelativeLayout with bi-directional sync.")
