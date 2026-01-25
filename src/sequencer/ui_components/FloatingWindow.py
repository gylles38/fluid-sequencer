from kivy.uix.relativelayout import RelativeLayout
from kivymd.uix.boxlayout import MDBoxLayout
from kivy.properties import StringProperty, BooleanProperty, ObjectProperty
from kivy.lang import Builder
from kivy.metrics import dp
from kivy.core.window import Window
from kivy.uix.boxlayout import BoxLayout
from kivy.clock import Clock
from kivy.uix.widget import Widget
from kivymd.uix.label import MDLabel
from kivymd.uix.button import MDIconButton

# Robust internal widget identification using custom classes with flag
class InternalWidget(Widget):
    def __init__(self, **kwargs):
        self._is_internal_widget = True
        super().__init__(**kwargs)

class InternalBoxLayout(BoxLayout):
    def __init__(self, **kwargs):
        self._is_internal_widget = True
        super().__init__(**kwargs)

Builder.load_string("""
<FloatingWindow>:
    size_hint: None, None
    size: dp(800), dp(600)

    # 1. Background Layer
    InternalWidget:
        id: card_bg
        size_hint: 1, 1
        pos_hint: {'x': 0, 'y': 0}
        canvas.before:
            Color:
                rgba: 0.12, 0.12, 0.12, 1
            RoundedRectangle:
                pos: self.pos
                size: self.size
                radius: [dp(8),]
            Color:
                rgba: 0.3, 0.3, 0.3, 1
            Line:
                width: dp(1)
                rounded_rectangle: (self.x, self.y, self.width, self.height, dp(8))

    # 2. Content Layer (fills the window, leaves space for title bar via padding)
    InternalBoxLayout:
        id: window_content
        orientation: 'vertical'
        size_hint: 1, 1
        pos_hint: {'x': 0, 'y': 0}
        padding: [dp(2), dp(42), dp(2), dp(2)] # [left, top, right, bottom]

    # 3. Chrome Layer (drawn on top)
    InternalBoxLayout:
        id: title_bar
        orientation: 'horizontal'
        size_hint: 1, None
        height: dp(40)
        pos_hint: {'top': 1}
        padding: [dp(10), 0]
        spacing: dp(5)

        canvas.before:
            Color:
                rgba: 0.18, 0.18, 0.18, 1
            RoundedRectangle:
                pos: self.pos
                size: self.size
                radius: [dp(8), dp(8), 0, 0]
            Color:
                rgba: 0.3, 0.3, 0.3, 1
            Line:
                width: dp(1)
                points: [self.x, self.y, self.right, self.y] # Bottom separator

        MDLabel:
            text: root.title
            theme_text_color: "Custom"
            text_color: 1, 1, 1, 1
            bold: True
            valign: 'middle'
            halign: 'left'

        MDIconButton:
            icon: 'window-maximize' if not root.is_maximized else 'window-restore'
            theme_icon_color: "Custom"
            icon_color: 1, 1, 1, 1
            on_release: root.toggle_maximize()
            pos_hint: {'center_y': 0.5}

        MDIconButton:
            icon: 'close'
            theme_icon_color: "Custom"
            icon_color: 1, 1, 1, 1
            on_release: root.dismiss()
            pos_hint: {'center_y': 0.5}

    # 4. Resize Handle (on top of everything)
    InternalWidget:
        id: resize_handle
        size_hint: None, None
        size: dp(30), dp(30)
        right: root.width
        y: 0

        canvas.after:
            Color:
                rgba: 1, 0.6, 0, 0.8  # Solid orange handle
            Line:
                # Relative to the widget's origin (since the canvas is translated in RelativeLayout children)
                points: [self.width - dp(4), dp(4), self.width - dp(24), dp(4), self.width - dp(4), dp(24)]
                close: True
                width: dp(2)
""")

class FloatingWindow(RelativeLayout):
    title = StringProperty("Window")
    is_maximized = BooleanProperty(False)
    _prev_state = ObjectProperty(None, allownone=True)
    _touch_lock = BooleanProperty(True)

    def __init__(self, **kwargs):
        self.register_event_type('on_open')
        self.register_event_type('on_dismiss')
        super().__init__(**kwargs)
        self._drag_mode = None
        # Unlock touch after a short delay
        Clock.schedule_once(lambda dt: setattr(self, '_touch_lock', False), 0.3)

    def add_widget(self, widget, index=0, canvas=None):
        # Recognize internal widgets via the flag set in __init__
        if getattr(widget, '_is_internal_widget', False):
            super().add_widget(widget, index, canvas)
            return

        # Redirect everything else to window_content IF it exists
        if hasattr(self, 'ids') and 'window_content' in self.ids:
            self.ids.window_content.add_widget(widget, index, canvas)
        else:
            super().add_widget(widget, index, canvas)

    def on_touch_down(self, touch):
        if not self.collide_point(*touch.pos):
            return False

        if self._touch_lock:
            return True

        local_pos = self.to_local(*touch.pos)

        # Bring to front (deferred)
        Clock.schedule_once(lambda dt: self.bring_to_front(), 0)

        # 1. Check resize handle first (it's at the top of Z-order)
        if not self.is_maximized and self.ids.resize_handle.collide_point(*local_pos):
            touch.grab(self)
            self._drag_mode = 'resize'
            return True

        # 2. Dispatch to children (buttons, editor content)
        # Kivy's RelativeLayout.on_touch_down correctly transforms touch coordinates for children.
        if super().on_touch_down(touch):
            return True

        # 3. Handle drag if title bar area was hit but no child (button) consumed it
        if self.ids.title_bar.collide_point(*local_pos):
            if not self.is_maximized:
                touch.grab(self)
                self._drag_mode = 'drag'
                return True
            return True # Block background anyway

        return True # Block touches to widgets behind

    def on_touch_move(self, touch):
        if touch.grab_current is not self:
            return super().on_touch_move(touch)

        if self._drag_mode == 'drag':
            if self.is_maximized:
                self.toggle_maximize()
            self.x += touch.dx
            self.y += touch.dy
        elif self._drag_mode == 'resize':
            # Dragging bottom-right handle
            new_w = max(dp(400), self.width + touch.dx)
            new_h = max(dp(300), self.height - touch.dy)

            delta_h = new_h - self.height
            self.width = new_w
            self.height = new_h
            self.y -= delta_h

        return True

    def on_touch_up(self, touch):
        if touch.grab_current is self:
            touch.ungrab(self)
            self._drag_mode = None
            return True
        return super().on_touch_up(touch)

    def toggle_maximize(self):
        if not self.parent: return
        if not self.is_maximized:
            self._prev_state = (self.pos[:], self.size[:])
            self.pos = (0, 0)
            self.size = self.parent.size
            self.is_maximized = True
            self.ids.resize_handle.opacity = 0
        else:
            if self._prev_state:
                self.pos, self.size = self._prev_state
            self.is_maximized = False
            self.ids.resize_handle.opacity = 1

    def bring_to_front(self):
        if self.parent and self.parent.children[0] is not self:
            p = self.parent
            p.remove_widget(self)
            p.add_widget(self)

    def open(self, *args):
        from kivy.app import App
        app = App.get_running_app()
        target = getattr(app.root, 'window_manager', None)
        if target:
            if self not in target.children:
                target.add_widget(self)
                if self.pos == [0, 0]:
                    self.center = target.center
                self.dispatch('on_open')
        else:
            if self not in Window.children:
                Window.add_widget(self)
                if self.pos == [0, 0]:
                    self.center = Window.center
                self.dispatch('on_open')

    def dismiss(self, *args):
        self.dispatch('on_dismiss')
        if self.parent:
            self.parent.remove_widget(self)

    def on_open(self): pass
    def on_dismiss(self): pass
