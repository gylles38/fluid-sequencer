from kivy.uix.relativelayout import RelativeLayout
from kivymd.uix.boxlayout import MDBoxLayout
from kivy.properties import StringProperty, BooleanProperty, ObjectProperty
from kivy.lang import Builder
from kivy.metrics import dp
from kivy.core.window import Window
from kivy.uix.boxlayout import BoxLayout
from kivy.clock import Clock
from kivy.uix.widget import Widget

Builder.load_string("""
<FloatingWindow>:
    size_hint: None, None
    size: dp(800), dp(600)

    # Use a normal Widget for background/card simulation to avoid MDCard interference
    Widget:
        id: card_bg
        _is_internal_widget: True
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

    # Main container for title bar and content
    BoxLayout:
        id: main_container
        _is_internal_widget: True
        orientation: 'vertical'
        size_hint: 1, 1
        pos_hint: {'x': 0, 'y': 0}
        padding: dp(1)

        # Title Bar (KivyMD layout for buttons)
        MDBoxLayout:
            id: title_bar
            size_hint_y: None
            height: dp(40)
            md_bg_color: 0.18, 0.18, 0.18, 1
            padding: [dp(10), 0]
            radius: [dp(8), dp(8), 0, 0]

            MDLabel:
                text: root.title
                theme_text_color: "Custom"
                text_color: 1, 1, 1, 1
                bold: True
                valign: 'middle'

            MDIconButton:
                icon: 'window-maximize' if not root.is_maximized else 'window-restore'
                theme_icon_color: "Custom"
                icon_color: 1, 1, 1, 1
                on_release: root.toggle_maximize()

            MDIconButton:
                icon: 'close'
                theme_icon_color: "Custom"
                icon_color: 1, 1, 1, 1
                on_release: root.dismiss()

        # The content area where editors will be added
        BoxLayout:
            id: window_content

    # Resize handle (Widget with custom drawing)
    Widget:
        id: resize_handle
        _is_internal_widget: True
        size_hint: None, None
        size: dp(30), dp(30)
        # Use reactive positioning
        right: root.width
        y: 0

        canvas:
            Color:
                rgba: 1, 1, 1, 0.6
            Line:
                points: [self.right - dp(4), self.y + dp(4), self.right - dp(20), self.y + dp(4), self.right - dp(4), self.y + dp(20)]
                close: True
                width: dp(1.5)
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
        if hasattr(widget, '_is_internal_widget'):
            super().add_widget(widget, index, canvas)
            return

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

        # 1. Check resize handle first
        if not self.is_maximized and self.ids.resize_handle.collide_point(*local_pos):
            touch.grab(self)
            self._drag_mode = 'resize'
            return True

        # 2. Let children (buttons and editor content) handle touch
        # super().on_touch_down handles coordinate transformation for us
        if super().on_touch_down(touch):
            return True

        # 3. Handle drag in title bar if children didn't handle it
        if local_pos[1] >= self.height - dp(40):
            if not self.is_maximized:
                touch.grab(self)
                self._drag_mode = 'drag'
                return True
            return True

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
            self.width = max(dp(400), self.width + touch.dx)
            old_h = self.height
            self.height = max(dp(300), self.height - touch.dy)
            self.y += (old_h - self.height) # Keep top edge fixed
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
