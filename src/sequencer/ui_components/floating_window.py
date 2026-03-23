from kivy.uix.relativelayout import RelativeLayout
from kivy.lang import Builder
from kivy.properties import StringProperty, ObjectProperty, BooleanProperty, ListProperty, NumericProperty
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
                id: maximize_button
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

    min_width = NumericProperty(dp(400))
    min_height = NumericProperty(dp(250))

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._is_dragging = False
        self._is_resizing = False
        self._drag_start_touch_pos = (0, 0)
        self._drag_start_widget_pos = (0, 0)
        self._resize_start_touch_pos = (0, 0)
        self._resize_start_widget_size = (0, 0)
        self._resize_start_widget_pos = (0, 0)
        self._resize_start_top = 0

        self._restore_pos = (self.x, self.y)
        self._restore_size = self.size[:]
        self._restore_pos_hint = {}
        self._restore_size_hint = (None, None)

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

        # Capture GLOBAL coordinates here before transformation
        global_touch_pos = (touch.x, touch.y)

        # Apply transformation to get local coordinates for collision checks
        touch.push()
        touch.apply_transform_2d(self.to_local)
        local_pos = touch.pos

        if getattr(self, '_touch_lock', False):
            touch.pop()
            return True

        if self.parent:
            Clock.schedule_once(lambda dt: self._bring_to_front(), 0)

        # 1. Check resize handle first (chrome priority)
        if 'resize_handle' in self.ids and self.ids.resize_handle.collide_point(*local_pos) and not self.is_maximized:
            if self.parent:
                # Capture current absolute state
                old_pos = self.pos[:]
                old_size = self.size[:]
                self.pos_hint = {}
                self.size_hint = (None, None)
                self.pos = old_pos
                self.size = old_size

                self._is_resizing = True
                self._resize_start_touch_pos = global_touch_pos
                self._resize_start_widget_size = self.size[:]
                self._resize_start_widget_pos = self.pos[:]
                # Use real top as anchor to avoid jumps during move
                self._resize_start_top = self.y + self.height

                touch.grab(self)
                touch.pop()
                return True

        # 2. Let children handle touch (like title bar buttons or content)
        if super(RelativeLayout, self).on_touch_down(touch):
            touch.pop()
            return True

        # 3. dragging logic (title bar)
        if 'title_bar' in self.ids and self.ids.title_bar.collide_point(*local_pos) and not self.is_maximized:
            if self.parent:
                # Capture current absolute state
                old_pos = self.pos[:]
                old_size = self.size[:]
                self.pos_hint = {}
                self.size_hint = (None, None)
                self.pos = old_pos
                self.size = old_size

                self._is_dragging = True
                self._drag_start_touch_pos = global_touch_pos
                self._drag_start_widget_pos = self.pos[:]

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
                # Calculate delta using window coordinates (touch.x, touch.y)
                dx = touch.x - self._drag_start_touch_pos[0]
                dy = touch.y - self._drag_start_touch_pos[1]

                # Update position based on initial position + delta
                new_x = self._drag_start_widget_pos[0] + dx
                new_y = self._drag_start_widget_pos[1] + dy

                # Robust clamping allowing scrolling if taller/wider than parent
                if self.width <= self.parent.width:
                    self.x = max(0, min(new_x, self.parent.width - self.width))
                else:
                    self.x = max(self.parent.width - self.width, min(new_x, 0))

                if self.height <= self.parent.height:
                    self.y = max(0, min(new_y, self.parent.height - self.height))
                else:
                    self.y = max(self.parent.height - self.height, min(new_y, 0))
            return True

        if self._is_resizing:
            if self.parent:
                # Calculate delta using window coordinates
                dx = touch.x - self._resize_start_touch_pos[0]
                dy = touch.y - self._resize_start_touch_pos[1]

                # 1. Width (Right edge)
                new_width = self._resize_start_widget_size[0] + dx
                new_width = max(self.min_width, new_width)
                # Cap width so right edge doesn't go off-screen
                if self.x + new_width > self.parent.width:
                    new_width = self.parent.width - self.x

                # 2. Height (Bottom edge)
                # Anchor at the real top captured in on_touch_down
                start_top = self._resize_start_top
                new_y = self._resize_start_widget_pos[1] + dy

                # Ensure minimum height
                if start_top - new_y < self.min_height:
                    new_y = start_top - self.min_height

                # Clamp bottom edge to parent bottom
                new_y = max(0, new_y)

                new_height = start_top - new_y

                self.width = new_width
                self.y = new_y
                self.height = new_height
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
            self._restore_size_hint = self.size_hint[:] if self.size_hint else (None, None)
            self._restore_pos = self.pos[:]
            self._restore_size = self.size[:]

            self.pos_hint = {'x': 0, 'y': 0}
            self.size_hint = (1, 1)
            self.is_maximized = True
        else:
            # Restore
            self.size_hint = self._restore_size_hint
            self.pos_hint = self._restore_pos_hint

            def finish_restore(dt):
                if not self.parent: return
                # Restore absolute size if hints are empty
                if self.size_hint[0] is None and self.size_hint[1] is None:
                    # Ensure restored size does not exceed parent size
                    r_w = min(self._restore_size[0], self.parent.width)
                    r_h = min(self._restore_size[1], self.parent.height)
                    self.size = (r_w, r_h)
                # Restore absolute position if hint is empty
                if not self.pos_hint:
                    # Use current size for clamping to ensure it stays on screen
                    target_x = max(0, min(self._restore_pos[0], self.parent.width - self.width))
                    target_y = max(0, min(self._restore_pos[1], self.parent.height - self.height))
                    self.pos = (target_x, target_y)

            Clock.schedule_once(finish_restore)
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