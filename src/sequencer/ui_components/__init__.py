# Importations communes pour tous les composants UI
import kivy
kivy.require('2.3.1')

from sequencer.ui_components import *  # Importe tous les imports communs
from sequencer.models import MidiTrack, AudioTrack, AutomationTrack

from kivy.metrics import dp
from kivy.properties import StringProperty, NumericProperty, ListProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.graphics import Color, Rectangle, Line
from kivy.clock import Clock

# KivyMD imports
from kivymd.uix.button import MDIconButton, MDButton, MDButtonText
from kivymd.uix.slider import MDSlider
from kivymd.uix.label import MDLabel

# Import des composants personnalisés
from .TooltipMDIconButton import TooltipMDIconButton
from .ThreeStateRecordButton import ThreeStateRecordButton
from sequencer.ui_components.ValueSpinner import ValueSpinner

from kivy.uix.widget import Widget
from kivymd.uix.label import MDIcon
from .PianoRoll import PianoRoll, PianoRollViewer
from .Ruler import Ruler
from .PianoKeyboard import PianoKeyboard
from .floating_window import FloatingWindow
from .bounded_scroll_view import BoundedScrollView
from .piano_roll_editor import PianoRollEditor, EditableMidiGrid
from .automation_editor import AutomationEditor
from .AutomationControls import AutomationControls
from .VportsPopup import VportsPopup
from .editable_label import EditableLabel
from .AutomationCurve import AutomationCurveWidget
from .AudioWaveform import AudioWaveform
