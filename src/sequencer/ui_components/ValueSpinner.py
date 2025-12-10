from kivy.uix.boxlayout import BoxLayout
from kivy.metrics import dp
from sequencer.ui_components.CustomTextInput import CustomTextInput

class ValueSpinner(BoxLayout):
    def __init__(self, min_val, max_val, initial_value, callback, **kwargs):
        super(ValueSpinner, self).__init__(**kwargs)
        self.min_val = min_val
        self.max_val = max_val
        self.callback = callback
        self.last_valid_value = initial_value
        self.orientation = 'horizontal'
        self.size_hint_y = None
        self.height = dp(30)
        self.text_input = CustomTextInput(
            text=str(initial_value),
            multiline=False,
            halign='center',
            padding=[dp(6), dp(4), dp(6), dp(4)],
            size_hint_x=None,
            width=dp(50),
            size_hint_y=None,
            height=dp(28),
            pos_hint={'center_y': 0.65},
            font_size=dp(16),
            field_type='program',
            callback=self.handle_arrow_keys
        )
        self.text_input.bind(on_text_validate=self.on_text_change)
        self.add_widget(self.text_input)

    def handle_arrow_keys(self, textinput, direction, modifiers, cursor_pos=None):
        try:
            current_value = int(self.text_input.text)
            step = 10 if 'shift' in modifiers else 1
            if direction == 'up':
                new_value = min(self.max_val, current_value + step)
            else:
                new_value = max(self.min_val, current_value - step)
            self._update_value(new_value)
        except ValueError:
            self.text_input.text = str(self.last_valid_value)

    def _update_value(self, new_value):
        self.text_input.text = str(new_value)
        self.last_valid_value = new_value
        if self.callback:
            self.callback(self.text_input)

    def increment(self, instance):
        try:
            value = int(self.text_input.text)
            if value < self.max_val:
                self._update_value(value + 1)
        except ValueError:
            self.text_input.text = str(self.last_valid_value)

    def decrement(self, instance):
        try:
            value = int(self.text_input.text)
            if value > self.min_val:
                self._update_value(value - 1)
        except ValueError:
            self.text_input.text = str(self.last_valid_value)

    def on_text_change(self, instance):
        try:
            value = int(instance.text)
            if not (self.min_val <= value <= self.max_val):
                value = max(self.min_val, min(value, self.max_val))
        except ValueError:
            value = self.last_valid_value
        self._update_value(value)
