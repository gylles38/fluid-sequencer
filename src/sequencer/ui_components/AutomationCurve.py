import math
from kivy.uix.widget import Widget
from kivy.graphics import Color, Mesh
from kivy.properties import ListProperty, NumericProperty
from kivy.metrics import dp

# --- Easing Functions (Robert Penner equations) ---
def _interp_linear(t):
    return t

def _interp_ease_in_quad(t):
    return t * t

def _interp_ease_out_quad(t):
    return t * (2 - t)

def _interp_ease_in_out_quad(t):
    t *= 2
    if t < 1:
        return 0.5 * t * t
    t -= 1
    return -0.5 * (t * (t - 2) - 1)

def _interp_sine(t):
    return 0.5 * (1 - math.cos(t * math.pi))


class AutomationCurveWidget(Widget):
    """
    A widget to display an automation curve as a filled shape.
    """
    points = ListProperty([])
    pixels_per_beat = NumericProperty(dp(100))
    total_beats = NumericProperty(128.0)
    min_val = NumericProperty(0.0)
    max_val = NumericProperty(127.0)
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.bind(pos=self.draw_curve, size=self.draw_curve, points=self.draw_curve,
                  pixels_per_beat=self.draw_curve, total_beats=self.draw_curve)

    def _get_interp_func(self, curve_type):
        """Returns the interpolation function for a given curve type."""
        if curve_type == "linear":
            return _interp_linear
        elif curve_type == "ease-in":
            return _interp_ease_in_quad
        elif curve_type == "ease-out":
            return _interp_ease_out_quad
        elif curve_type == "ease-in-out":
            return _interp_ease_in_out_quad
        elif curve_type == "sine":
            return _interp_sine
        else:  # "none" or unknown
            return None

    def draw_curve(self, *args):
        if not self.canvas:
            return
            
        self.canvas.clear()
        if not self.points:
            return

        v_range = self.max_val - self.min_val
        if v_range == 0: v_range = 1 

        def normalize(val):
            v_range = self.max_val - self.min_val
            if v_range == 0: v_range = 1
            return (val - self.min_val) / v_range

        with self.canvas:
            Color(0.5, 0.5, 0.8, 0.4)
            sorted_points = sorted(self.points, key=lambda p: p.start_time)

            vertices = []
            indices = []
            v_index = 0

            # --- 1. GESTION DU DÉBUT (VIDE JUSQU'AU PREMIER POINT) ---
            first_p = sorted_points[0]
            first_x = first_p.start_time * self.pixels_per_beat
            
            if first_x > 0:
                # On dessine un segment plat à ZÉRO (bas du widget) jusqu'au premier point
                # pour laisser la zone "vide" visuellement
                vertices.extend([0, self.y, 0, 0, 0, self.y, 0, 0])
                indices.extend([v_index, v_index + 1])
                v_index += 2
                vertices.extend([first_x, self.y, 0, 0, first_x, self.y, 0, 0])
                indices.extend([v_index, v_index + 1])
                v_index += 2

            # --- 2. BOUCLE DE DESSIN DES POINTS ---
            for i in range(len(sorted_points)):
                p1 = sorted_points[i]
                x1 = p1.start_time * self.pixels_per_beat
                y1 = self.y + (normalize(p1.value) * self.height)

                vertices.extend([x1, self.y, 0, 0, x1, y1, 0, 0])
                indices.extend([v_index, v_index + 1])
                v_index += 2

                if i < len(sorted_points) - 1:
                    p2 = sorted_points[i+1]
                    x2 = p2.start_time * self.pixels_per_beat
                    
                    # On ne fait un "escalier" QUE si curve est explicitement "none"
                    if p1.curve != "none":
                        interp_func = self._get_interp_func(p1.curve)
                        num_steps = max(2, min(100, int((x2 - x1) / 5)))
                        for step in range(1, num_steps):
                            t = step / num_steps
                            curr_x = x1 + t * (x2 - x1)
                            # Interpolation entre les valeurs 0-127
                            real_val = p1.value + interp_func(t) * (p2.value - p1.value)
                            curr_y = self.y + (normalize(real_val) * self.height)
                            vertices.extend([curr_x, self.y, 0, 0, curr_x, curr_y, 0, 0])
                            indices.extend([v_index, v_index + 1])
                            v_index += 2
                    else:
                        # Maintien de la valeur (Escalier)
                        vertices.extend([x2, self.y, 0, 0, x2, y1, 0, 0])
                        indices.extend([v_index, v_index + 1])
                        v_index += 2

            # --- 3. GESTION DE LA FIN (MAINTIEN JUSQU'AU BOUT) ---
            last_p = sorted_points[-1]
            last_x = last_p.start_time * self.pixels_per_beat
            final_x = self.total_beats * self.pixels_per_beat
            
            if last_x < final_x:
                y_last = self.y + (normalize(last_p.value) * self.height)
                # On prolonge la dernière valeur jusqu'à la fin de la timeline
                vertices.extend([final_x, self.y, 0, 0, final_x, y_last, 0, 0])
                indices.extend([v_index, v_index + 1])
                v_index += 2

            Mesh(vertices=vertices, indices=indices, mode='triangle_strip')

    def on_points(self, instance, value):
        if not value:
            return
        
        param = value[0].parameter
        # Vélocité et Program Change partagent l'échelle 0-127
        if param in ["prog", "vel"]:
            self.min_val, self.max_val = 0.0, 127.0
        elif param == "pan":
            self.min_val, self.max_val = -1.0, 1.0
        else: # vol, etc.
            self.min_val, self.max_val = 0.0, 1.0
        
        if self.canvas:
            self.draw_curve()