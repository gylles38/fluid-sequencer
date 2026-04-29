import math
from kivy.uix.widget import Widget
from kivy.graphics import Color, Mesh, Line
from kivy.properties import ListProperty, NumericProperty
from kivy.metrics import dp
from kivy.clock import Clock

# --- Fonctions d'interpolation (Easing) ---
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

def _interp_sine(t, frequency=1.0):
    # Support de la fréquence pour les oscillations personnalisées
    return 0.5 * (1 - math.cos(t * math.pi * frequency))

class AutomationCurveWidget(Widget):
    """
    Affiche la courbe d'automation avec remplissage (Mesh) et contour (Line).
    """
    points = ListProperty([])
    pixels_per_beat = NumericProperty(dp(100))
    total_beats = NumericProperty(128.0)
    min_val = NumericProperty(0.0)
    max_val = NumericProperty(127.0)
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Initialisé par TrackWidget lors de la création
        self.param_type = "vol" 
        self.bind(pos=self.redraw, size=self.redraw, points=self.redraw,
                  pixels_per_beat=self.redraw, total_beats=self.redraw)

    def redraw(self, *args):
        """Debounced redraw of the curve."""
        Clock.unschedule(self.draw_curve)
        Clock.schedule_once(self.draw_curve, 0)

    def _get_interp_func(self, curve_type):
        if curve_type == "linear": return _interp_linear
        elif curve_type == "ease-in": return _interp_ease_in_quad
        elif curve_type == "ease-out": return _interp_ease_out_quad
        elif curve_type == "ease-in-out": return _interp_ease_in_out_quad
        elif curve_type == "sine": return _interp_sine
        return None

    def draw_curve(self, *args):
        if not self.canvas:
            return

        # On efface systématiquement avant de redessiner
        self.canvas.clear()
        
        if not self.points:
            return

        def normalize(val):
            v_range = self.max_val - self.min_val
            if v_range == 0: v_range = 1
            return (val - self.min_val) / v_range

        with self.canvas:
            sorted_points = sorted(self.points, key=lambda p: p.start_time)
            
            # 1. Dessin du remplissage (Mesh)
            Color(0.5, 0.5, 0.8, 0.3) # Bleu très transparent
            vertices = []
            indices = []
            v_index = 0
            
            # 2. Préparation pour le contour (Line)
            line_points = []

            # --- GESTION DU DÉBUT (0 vers premier point) ---
            first_p = sorted_points[0]
            first_x = first_p.start_time * self.pixels_per_beat
            y_first = (normalize(first_p.value) * self.height)
            
            if first_x > 0:
                vertices.extend([0, 0, 0, 0, 0, y_first, 0, 0])
                indices.extend([v_index, v_index + 1]); v_index += 2
                vertices.extend([first_x, 0, 0, 0, first_x, y_first, 0, 0])
                indices.extend([v_index, v_index + 1]); v_index += 2
                line_points.extend([0, y_first, first_x, y_first])

            # --- BOUCLE PRINCIPALE ---
            for i in range(len(sorted_points)):
                p1 = sorted_points[i]
                x1 = p1.start_time * self.pixels_per_beat
                y1 = (normalize(p1.value) * self.height)

                vertices.extend([x1, 0, 0, 0, x1, y1, 0, 0])
                indices.extend([v_index, v_index + 1]); v_index += 2
                if i == 0 and first_x == 0: line_points.extend([x1, y1])

                if i < len(sorted_points) - 1:
                    p2 = sorted_points[i+1]
                    x2 = p2.start_time * self.pixels_per_beat
                    y2 = (normalize(p2.value) * self.height)

                    # CAS ESCALIER (Program Change ou mode 'none')
                    if self.param_type == "prog" or p1.curve == "none":
                        vertices.extend([x2, 0, 0, 0, x2, y1, 0, 0])
                        indices.extend([v_index, v_index + 1]); v_index += 2
                        line_points.extend([x1, y1, x2, y1, x2, y2])
                    
                    # CAS COURBES (Sine, Easing)
                    else:
                        c_val = getattr(p1, 'curve_value', 1.0)
                        # On assure au moins 4 segments pour que le triangle_strip existe
                        # même si l'espace en pixel est très réduit au dézoom.
                        num_steps = max(4, min(60, int((x2 - x1) / 10)))
                        
                        for step in range(1, num_steps + 1):
                            t = step / num_steps
                            curr_x = x1 + t * (x2 - x1)
                            
                            if p1.curve == "sine":
                                ratio = _interp_sine(t, c_val)
                            else:
                                func = self._get_interp_func(p1.curve)
                                ratio = func(t) if func else t
                                
                            curr_val = p1.value + ratio * (p2.value - p1.value)
                            curr_y = (normalize(curr_val) * self.height)
                            
                            # On ajoute toujours la base (y=0 dans le widget) et le sommet (y=val)
                            vertices.extend([curr_x, 0, 0, 0, curr_x, curr_y, 0, 0])
                            indices.extend([v_index, v_index + 1])
                            v_index += 2
                            line_points.extend([curr_x, curr_y])

            # --- GESTION DE LA FIN ---
            last_p = sorted_points[-1]
            final_x = self.total_beats * self.pixels_per_beat
            if (last_x := last_p.start_time * self.pixels_per_beat) < final_x:
                y_last = (normalize(last_p.value) * self.height)
                vertices.extend([final_x, 0, 0, 0, final_x, y_last, 0, 0])
                indices.extend([v_index, v_index + 1]); v_index += 2
                line_points.extend([final_x, y_last])

            # Rendu du remplissage
            Mesh(vertices=vertices, indices=indices, mode='triangle_strip')
            
            # Rendu du contour (plus sombre et net)
            Color(0.6, 0.6, 1, 0.8)
            Line(points=line_points, width=1.1)

    def on_points(self, instance, value):
        # Sécurité : Si le widget n'est pas encore prêt (canvas est None), 
        # ou si la liste est vide, on nettoie si possible et on s'arrête.
        if self.canvas is None:
            return
        
        if not value:
            self.canvas.clear()
            self.canvas.after.clear()            
            return
            
        # Mise à jour des bornes selon le paramètre
        param = value[0].parameter
        self.param_type = param
        
        if param in ["prog", "vel"] or param.startswith("cc"):
            self.min_val, self.max_val = 0.0, 127.0
        elif param == "pan":
            self.min_val, self.max_val = -1.0, 1.0
        else: # vol
            self.min_val, self.max_val = 0.0, 1.0
            
        self.draw_curve()