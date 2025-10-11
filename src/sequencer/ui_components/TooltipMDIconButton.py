from kivymd.uix.button import MDIconButton
from kivy.uix.label import Label
from kivy.core.window import Window
from kivy.metrics import dp
from kivy.graphics import Color, Rectangle
from kivy.clock import Clock
from kivy.properties import StringProperty

class TooltipMDIconButton(MDIconButton):
    tooltip_text = StringProperty()

    def __init__(self, **kwargs):
        self.tooltip_text = kwargs.pop("tooltip_text", "")
        super().__init__(**kwargs)
        self.tooltip_delay = 0.2
        self.tooltip_label = None
        self._show_event = None
        Window.bind(mouse_pos=self.on_mouse_pos)
        self.bind(tooltip_text=self._on_tooltip_text_changed)

    def _on_tooltip_text_changed(self, instance, value):
        if self.tooltip_label:
            self.tooltip_label.text = value
            self.tooltip_label.texture_update()
            natural_size = self.tooltip_label.texture_size
            new_size = (natural_size[0] + dp(20), dp(32)) if natural_size else (dp(120), dp(32))
            self.tooltip_label.size = new_size
            if hasattr(self, "_bg_rect"):
                self._bg_rect.size = new_size

    def on_mouse_pos(self, window, pos):
        collide = self.collide_point(*self.to_widget(*pos))
        if collide:
            self._ensure_tooltip()
            self._update_tooltip_position(pos)
            if not self._show_event:
                self._show_event = Clock.schedule_once(self._do_show_tooltip, self.tooltip_delay)
        else:
            self._hide_tooltip()

    def _ensure_tooltip(self):
        if self.tooltip_label:
            return
        self.tooltip_label = Label(
            text=self.tooltip_text,
            size_hint=(None, None),
            color=(1, 1, 1, 1),
            font_size=dp(14),
            padding=(dp(10), dp(6))
        )
        self.tooltip_label.texture_update()
        natural_size = self.tooltip_label.texture_size
        self.tooltip_label.size = (natural_size[0] + dp(20), dp(32)) if natural_size else (dp(120), dp(32))
        with self.tooltip_label.canvas.before:
            Color(0, 0, 0, 0.85)
            self._bg_rect = Rectangle(pos=self.tooltip_label.pos, size=self.tooltip_label.size)
        self.tooltip_label.bind(pos=self._update_bg, size=self._update_bg)

    def _update_bg(self, instance, value):
        if hasattr(self, "_bg_rect"):
            self._bg_rect.pos = instance.pos
            self._bg_rect.size = instance.size

    def _do_show_tooltip(self, dt):
        if self.tooltip_label and self.tooltip_label.parent is None:
            Window.add_widget(self.tooltip_label)
        self._show_event = None

    def _update_tooltip_position(self, mouse_pos):
        if not self.tooltip_label:
            return
        x, y = mouse_pos
        self.tooltip_label.pos = (x + dp(10), y + dp(10))
        self._update_bg(self.tooltip_label, None)

    def _hide_tooltip(self):
        if self._show_event:
            self._show_event.cancel()
            self._show_event = None
        if self.tooltip_label and self.tooltip_label.parent:
            Window.remove_widget(self.tooltip_label)

    def on_parent(self, instance, parent):
        if parent is None:
            self._hide_tooltip()
            if self.tooltip_label:
                self.tooltip_label = None
