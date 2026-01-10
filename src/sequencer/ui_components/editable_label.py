from kivy.clock import Clock
from kivy.metrics import dp
from kivy.properties import StringProperty, BooleanProperty, ListProperty, NumericProperty
from kivy.uix.boxlayout import BoxLayout
from kivymd.uix.button import MDIconButton
from kivymd.uix.label import MDLabel
from kivymd.uix.textfield import MDTextField
from kivy.core.window import Window
import re


class EditableLabel(BoxLayout):
    text = StringProperty('')
    edit_mode = BooleanProperty(False)
    font_size = NumericProperty('15sp')
    bold = BooleanProperty(False)
    color = ListProperty([1, 1, 1, 1])
    halign = StringProperty('left')
    valign = StringProperty('middle')

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.register_event_type('on_text_validated')
        self.label = None
        self.text_field = None
        self.validate_button = None
        Clock.schedule_once(self._post_kv_init)

    def _post_kv_init(self, *args):
        self.orientation = 'horizontal'
        self.size_hint_y = None
        self.height = dp(36) # Set a height to better align with other components
        self._setup_view_mode()

    def _setup_view_mode(self):
        self.clear_widgets()
        self.label = MDLabel(
            text=self.text,
            adaptive_width=True,
            font_size=self.font_size,
            bold=self.bold,
            theme_text_color="Custom",
            text_color=self.color,
            halign=self.halign,
            valign=self.valign
        )
        self.label.bind(on_touch_down=self._enter_edit_mode)
        self.add_widget(self.label)
        self.edit_mode = False

    def _enter_edit_mode(self, instance, touch):
        if instance.collide_point(*touch.pos) and not self.edit_mode:
            self.clear_widgets()
            self.text_field = MDTextField(
                text=self.text,
                max_text_length=16,
                required=True,
                helper_text_mode="on_error",
                helper_text="Only a-z, A-Z, 0-9, - are allowed",
                font_size=self.font_size
            )
            self.text_field.bind(focus=self._on_focus)
            self.text_field.text_validate_func = self._filter_text
            self.validate_button = MDIconButton(icon='check', on_press=self._validate_text)
            self.add_widget(self.text_field)
            self.add_widget(self.validate_button)
            self.edit_mode = True
            Clock.schedule_once(self._request_text_field_focus)
            Window.bind(on_key_down=self._on_key_down)

    def _request_text_field_focus(self, *args):
        self.text_field.focus = True

    def _filter_text(self, text):
        return re.match(r"^[a-zA-Z0-9-]*$", text) is not None

    def _on_focus(self, instance, focused):
        if not focused:
            self._cancel_edit()

    def _validate_text(self, *args):
        if not self.text_field: return
        new_text = self.text_field.text
        if new_text and self._filter_text(new_text):
            self.text = new_text
            self.dispatch('on_text_validated', self.text)
        self._setup_view_mode()
        Window.unbind(on_key_down=self._on_key_down)

    def _cancel_edit(self):
        if not self.text_field: return
        self._setup_view_mode()
        Window.unbind(on_key_down=self._on_key_down)

    def _on_key_down(self, instance, key, scancode, codepoint, modifier):
        if key == 27:  # Escape key
            self._cancel_edit()
            return True
        if key == 13: # Enter key
            self._validate_text()
            return True
        return False

    def on_text_validated(self, *args):
        pass

    def on_text(self, instance, value):
        # This handler can be called during widget initialization before `self.label`
        # has been created. We add a check for the attribute's existence to prevent a crash.
        if not self.edit_mode:
            if hasattr(self, 'label') and self.label:
                self.label.text = value
            # If the label doesn't exist yet, _post_kv_init will handle the setup.
            # We don't need an else clause here.
