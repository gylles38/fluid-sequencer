import math
from kivy.uix.widget import Widget
from kivy.graphics import Color, Mesh
from kivy.properties import ListProperty, NumericProperty
from kivy.metrics import dp

class AutomationCurveWidget(Widget):
    """
    A widget to display an automation curve as a filled shape.
    """
    points = ListProperty([])
    pixels_per_beat = NumericProperty(dp(100))
    total_beats = NumericProperty(128.0)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.bind(pos=self.draw_curve, size=self.draw_curve, points=self.draw_curve,
                  pixels_per_beat=self.draw_curve, total_beats=self.draw_curve)

    def _get_interp_func(self, curve_type):
        """Returns the interpolation function for a given curve type."""
    # Using standard Robert Penner easing functions for correctness
        if curve_type == "linear":
            return lambda t: t
        elif curve_type == "ease-in":
            # easeInQuad
            return lambda t: pow(t, 2)
        elif curve_type == "ease-out":
            # easeOutQuad
            return lambda t: 1 - pow(1 - t, 2)
        elif curve_type == "ease-in-out":
            # easeInOutQuad
            return lambda t: 2 * t * t if t < 0.5 else 1 - pow(-2 * t + 2, 2) / 2
        elif curve_type == "sine":
            return lambda t: 0.5 * (1 - math.cos(t * math.pi))
        else:  # "none" or unknown
            return None

    def draw_curve(self, *args):
        """
        Draws the automation curve based on the provided points.
        """
        self.canvas.clear()
        if len(self.points) < 1:
            return

        with self.canvas:
            Color(0.5, 0.5, 0.8, 0.4)

            # Sort points just in case they are not ordered
            sorted_points = sorted(self.points, key=lambda p: p.start_time)

            vertices = []
            indices = []

            # Start the filled area from the bottom-left of the first point
            start_x = sorted_points[0].start_time * self.pixels_per_beat
            vertices.extend([start_x, self.y, 0, 0])

            # Add the first point's value at its start time
            start_y = self.y + sorted_points[0].value * self.height
            vertices.extend([start_x, start_y, 0, 0])

            # Iterate through segments
            for i in range(len(sorted_points)):
                p1 = sorted_points[i]

                # For the last point, or segments with "none" curve, just draw a line to it
                if i == len(sorted_points) - 1 or p1.curve == "none":
                    x1 = p1.start_time * self.pixels_per_beat
                    y1 = self.y + p1.value * self.height

                    # If the previous point was also "none", we need to create a step
                    if i > 0 and sorted_points[i-1].curve == "none":
                         prev_x = sorted_points[i-1].start_time * self.pixels_per_beat
                         prev_y = self.y + sorted_points[i-1].value * self.height
                         vertices.extend([x1, prev_y, 0, 0])

                    vertices.extend([x1, y1, 0, 0])
                    continue

                p2 = sorted_points[i+1]
                interp_func = self._get_interp_func(p1.curve)

                start_beat = p1.start_time
                end_beat = p2.start_time
                start_val = p1.value
                end_val = p2.value

                beat_range = end_beat - start_beat
                val_range = end_val - start_val

                # If there's no time difference, just jump to the next point
                if beat_range <= 0:
                    continue

                # Generate interpolated points for the curve
                # Use a reasonable number of steps, e.g., 2 steps per pixel
                num_steps = int((beat_range * self.pixels_per_beat) * 2)
                if num_steps < 2: num_steps = 2
                if num_steps > 200: num_steps = 200 # Avoid too many vertices

                for step in range(1, num_steps + 1):
                    t = step / num_steps
                    eased_t = interp_func(t)

                    current_beat = start_beat + t * beat_range
                    current_val = start_val + eased_t * val_range

                    x = current_beat * self.pixels_per_beat
                    y = self.y + current_val * self.height
                    vertices.extend([x, y, 0, 0])

            # Add the bottom-right point to close the shape
            last_x = sorted_points[-1].start_time * self.pixels_per_beat
            vertices.extend([last_x, self.y, 0, 0])

            # Create indices for the triangle fan
            for i in range(len(vertices) // 4):
                indices.append(i)

            Mesh(vertices=vertices, indices=indices, mode='triangle_fan')
