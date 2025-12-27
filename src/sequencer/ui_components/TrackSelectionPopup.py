from kivy.uix.popup import Popup
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.gridlayout import GridLayout
from kivymd.uix.label import MDLabel
from kivymd.uix.selectioncontrol import MDCheckbox
from sequencer.ui_components.TooltipMDIconButton import TooltipMDIconButton
from kivy.metrics import dp

class TrackSelectionPopup(Popup):
    def __init__(self, tracks, callback, sequencer_layout, **kwargs):
        super(TrackSelectionPopup, self).__init__(**kwargs)
        self.title = "Select MIDI Tracks to Export"
        self.size_hint = (0.6, 0.8)
        self.tracks = tracks
        self.callback = callback
        self.sequencer_layout = sequencer_layout
        self.checkboxes = []

        # Main layout
        layout = BoxLayout(orientation='vertical', padding=dp(10), spacing=dp(10))

        # Scrollable list for tracks
        scroll_view = ScrollView()
        grid = GridLayout(cols=1, size_hint_y=None, spacing=dp(5))
        grid.bind(minimum_height=grid.setter('height'))

        for i, track in enumerate(self.tracks):
            # Only list MIDI tracks
            from sequencer.models import MidiTrack
            if isinstance(track, MidiTrack):
                item_layout = BoxLayout(size_hint_y=None, height=dp(40))

                checkbox = MDCheckbox(size_hint_x=None, width=dp(48))
                self.checkboxes.append((i, checkbox)) # Store index and checkbox

                label = MDLabel(text=f"{i}: {track.name}")

                item_layout.add_widget(checkbox)
                item_layout.add_widget(label)
                grid.add_widget(item_layout)

        scroll_view.add_widget(grid)
        layout.add_widget(scroll_view)

        # Buttons layout
        buttons_layout = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(10))
        export_button = TooltipMDIconButton(icon='file-export', tooltip_text='Export')
        export_button.bind(on_press=self.on_export)
        cancel_button = TooltipMDIconButton(icon='cancel', tooltip_text='Cancel')
        cancel_button.bind(on_press=self.dismiss)
        buttons_layout.add_widget(export_button)
        buttons_layout.add_widget(cancel_button)
        layout.add_widget(buttons_layout)

        self.content = layout

    def on_export(self, instance):
        selected_track_indices = []
        for index, checkbox in self.checkboxes:
            if checkbox.active:
                selected_track_indices.append(index)

        if not selected_track_indices:
            self.sequencer_layout.show_info_popup("Info", "No tracks were selected for export.")
            return

        self.callback(selected_track_indices)
        self.dismiss()
