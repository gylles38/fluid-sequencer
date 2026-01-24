from kivy.uix.relativelayout import RelativeLayout
from kivy.lang import Builder
from kivy.properties import StringProperty, ObjectProperty, BooleanProperty, ListProperty
from kivy.metrics import dp
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color, Rectangle, Line
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.label import MDLabel
from kivymd.uix.button import MDIconButton
from kivy.uix.widget import Widget

class InternalBoxLayout(MDBoxLayout):
    _is_internal_widget = BooleanProperty(True)

class InternalWidget(Widget):
    _is_internal_widget = BooleanProperty(True)

class InternalLabel(MDLabel):
    _is_internal_widget = BooleanProperty(True)

class InternalIconButton(MDIconButton):
    _is_internal_widget = BooleanProperty(True)

Builder.load_string("""
<FloatingWindow>:
    canvas.before:
        Color:
            rgba: 0.15, 0.15, 0.15, 1
        Rectangle:
            pos: 0, 0
            size: self.size
        Color:
            rgba: 0.3, 0.3, 0.3, 1
        Line:
            rectangle: (0, 0, self.width, self.height)
            width: dp(1)

    InternalBoxLayout:
        id: main_container
        orientation: 'vertical'

        # Title Bar
        InternalBoxLayout:
            id: title_bar
            size_hint_y: None
            height: dp(40)
            md_bg_color: 0.25, 0.25, 0.25, 1
            padding: [dp(10), 0, dp(5), 0]
            spacing: dp(5)

            InternalLabel:
                text: root.title
                theme_text_color: "Custom"
                text_color: 1, 1, 1, 1
                valign: 'middle'
                shorten: True
                shorten_from: 'right'

            InternalIconButton:
                icon: "window-maximize" if not root.is_maximized else "window-restore"
                on_release: root.toggle_maximize()
                theme_icon_color: "Custom"
                icon_color: 1, 1, 1, 1

            InternalIconButton:
                icon: "close"
                on_release: root.dismiss()
                theme_icon_color: "Custom"
                icon_color: 1, 0.3, 0.3, 1

        # Content Container
        InternalBoxLayout:
            id: content_container
            padding: dp(2)

    # Resize Handle (Bottom Right)
    InternalWidget:
        id: resize_handle
        size_hint: None, None
        size: dp(25), dp(25)
        pos: root.width - self.width, 0
        canvas:
            Color:
                rgba: 0.7, 0.7, 0.7, 0.5
            Line:
                points: [self.x + self.width - dp(2), self.y + dp(2), self.x + self.width - dp(2), self.y + dp(15), self.x + self.width - dp(15), self.y + dp(2)]
                close: True
                width: dp(1.5)
""")

class FloatingWindow(RelativeLayout):
    title = StringProperty("Window")
    is_maximized = BooleanProperty(False)
    source_track = ObjectProperty(None)
    _is_internal_widget = BooleanProperty(False)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._is_dragging = False
        self._is_resizing = False
        self._drag_offset = (0, 0)
        self._restore_pos = (self.x, self.y)
        self._restore_size = self.size[:]
        self._touch_lock = True
        Clock.schedule_once(self._unlock_touch, 0.3)

    def _unlock_touch(self, dt):
        self._touch_lock = False

    def add_widget(self, widget, index=0, canvas=None):
        if getattr(widget, '_is_internal_widget', False):
            super().add_widget(widget, index, canvas)
            return

        if not hasattr(self, 'ids') or 'content_container' not in self.ids:
            super().add_widget(widget, index, canvas)
            return

        self.ids.content_container.add_widget(widget, index, canvas)

    def on_touch_down(self, touch):
        if not self.collide_point(*touch.pos):
            return False

        touch.push()
        touch.apply_transform_2d(self.to_local)

        if getattr(self, '_touch_lock', False):
            touch.pop()
            return True

        if self.parent:
            Clock.schedule_once(lambda dt: self._bring_to_front(), 0)

        # 1. Let children handle touch first
        if super(RelativeLayout, self).on_touch_down(touch):
            touch.pop()
            return True

        # 2. dragging/resizing logic
        local_pos = touch.pos

        # Check resize handle
        if 'resize_handle' in self.ids and self.ids.resize_handle.collide_point(*local_pos) and not self.is_maximized:
            self.pos_hint = {}
            self.size_hint = (None, None)
            self._is_resizing = True
            touch.grab(self)
            touch.pop()
            return True

        # Check title bar
        if 'title_bar' in self.ids and self.ids.title_bar.collide_point(*local_pos) and not self.is_maximized:
            self.pos_hint = {}
            self.size_hint = (None, None)
            self._is_dragging = True
            self._drag_offset = local_pos
            touch.grab(self)
            touch.pop()
            return True

        touch.pop()
        return True

    def _bring_to_front(self):
        parent = self.parent
        if parent:
            if parent.children[0] is not self:
                parent.remove_widget(self)
                parent.add_widget(self)

    def on_touch_move(self, touch):
        if touch.grab_current is not self:
            return super().on_touch_move(touch)

        if self._is_dragging:
            if self.parent:
                parent_pos = self.parent.to_local(*touch.pos)
                self.x = parent_pos[0] - self._drag_offset[0]
                self.y = parent_pos[1] - self._drag_offset[1]

                self.x = max(0, min(self.x, self.parent.width - self.width))
                self.y = max(0, min(self.y, self.parent.height - self.height))
            return True

        if self._is_resizing:
            local_pos = touch.pos

            new_width = max(dp(300), local_pos[0])
            delta_y = local_pos[1]
            if self.height - delta_y > dp(200):
                self.y += delta_y
                self.height -= delta_y
            self.width = new_width
            return True

        return super().on_touch_move(touch)

    def on_touch_up(self, touch):
        if touch.grab_current is self:
            self._is_dragging = False
            self._is_resizing = False
            touch.ungrab(self)
            return True
        return super().on_touch_up(touch)

    def toggle_maximize(self):
        if not self.parent: return

        if not self.is_maximized:
            # Maximize
            self._restore_pos_hint = self.pos_hint.copy()
            self._restore_size_hint = self.size_hint[:] if self.size_hint else None
            self._restore_pos = (self.x, self.y)
            self._restore_size = self.size[:]

            self.pos_hint = {'x': 0, 'y': 0}
            self.size_hint = (1, 1)
            self.is_maximized = True
        else:
            # Restore
            self.size_hint = self._restore_size_hint
            self.pos_hint = self._restore_pos_hint
            if not self.pos_hint:
                self.pos = self._restore_pos
            if not self.size_hint or self.size_hint == (None, None):
                self.size = self._restore_size
            self.is_maximized = False

    def dismiss(self, *args):
        if self.parent:
            self.parent.remove_widget(self)
        self.on_dismiss()

    def on_dismiss(self):
        pass

    def on_open(self):
        pass

    def on_parent(self, widget, parent):
        if parent:
            Clock.schedule_once(lambda dt: self.on_open(), 0)

    def open(self):
        pass
