from kivy.uix.textinput import TextInput

class CustomTextInput(TextInput):
    def __init__(self, field_type='', callback=None, **kwargs):
        self.field_type = field_type
        self.callback = callback
        super().__init__(**kwargs)
    
    def keyboard_on_key_down(self, window, keycode, text, modifiers):
        if isinstance(keycode, tuple) and len(keycode) > 1:
            key_name = keycode[1]
        else:
            key_name = str(keycode)
        if key_name in ('up', 'down') and self.callback:
            cursor_pos = self.cursor_index()
            self.callback(self, key_name, modifiers, cursor_pos)
            return True
        return super().keyboard_on_key_down(window, keycode, text, modifiers)

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            self.focus = True
        return super().on_touch_down(touch)   

    def on_touch_up(self, touch):
        if self.collide_point(*touch.pos) and hasattr(touch, 'button'):
            cursor_pos = self.cursor_index()
            if touch.button == 'scrollup' and self.callback:
                self.callback(self, 'down', [], cursor_pos)
                return True
            elif touch.button == 'scrolldown' and self.callback:
                self.callback(self, 'up', [], cursor_pos)
                return True
        return super().on_touch_up(touch)
