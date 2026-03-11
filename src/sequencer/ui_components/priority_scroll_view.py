from kivy.uix.scrollview import ScrollView

class PriorityScrollView(ScrollView):
    """
    A ScrollView that prioritizes its children for mouse wheel events.
    In Kivy, ScrollView typically consumes 'scrollup' and 'scrolldown' in on_touch_down
    before children can process them. This class ensures children get first pick.
    """
    def on_touch_down(self, touch):
        if hasattr(touch, 'button') and touch.button in ('scrollup', 'scrolldown'):
            if self.collide_point(*touch.pos):
                # Apply transformation for children, similar to what ScrollView.on_touch_down does
                touch.push()
                touch.apply_transform_2d(self.to_local)
                # Widget.on_touch_down dispatches to children in their local coordinates
                if super(ScrollView, self).on_touch_down(touch):
                    touch.pop()
                    return True
                touch.pop()

        # If children didn't consume it, use default ScrollView behavior (which handles scrolling)
        return super().on_touch_down(touch)
