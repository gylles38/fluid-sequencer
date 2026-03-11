from kivy.uix.scrollview import ScrollView

class PriorityScrollView(ScrollView):
    """
    A ScrollView that prioritizes its children for mouse wheel events.
    In Kivy, ScrollView typically consumes 'scrollup' and 'scrolldown' in on_touch_down
    before children can process them. This class ensures children get first pick
    for all touches (clicks, drags, wheel) that occur within its bounds.
    """
    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            # Try to dispatch to children first (using Widget.on_touch_down logic)
            touch.push()
            touch.apply_transform_2d(self.to_local)
            # Widget.on_touch_down dispatches to children
            if super(ScrollView, self).on_touch_down(touch):
                touch.pop()
                return True
            touch.pop()

            # If no child handled it, let the ScrollView's scroll logic handle it
            return super().on_touch_down(touch)

        return False
