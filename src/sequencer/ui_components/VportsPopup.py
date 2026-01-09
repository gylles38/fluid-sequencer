from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivymd.uix.button import MDButton, MDButtonText
from kivymd.uix.label import MDLabel
from kivy.metrics import dp

from sequencer.models import MidiTrack
from sequencer.ui_components.ConfirmationPopup import ConfirmationPopup
from sequencer.ui_components.YesNoPopup import YesNoPopup

class VportsPopup(Popup):
    def __init__(self, sequencer, **kwargs):
        super(VportsPopup, self).__init__(**kwargs)
        self.sequencer = sequencer
        self.title = "Virtual Port Management"
        self.size_hint = (0.7, 0.8)

        self.main_layout = BoxLayout(orientation='vertical', padding=dp(10), spacing=dp(10))
        self.setContent(self.main_layout)

        self.ports_list_view = ScrollView()
        self.ports_grid = GridLayout(cols=1, size_hint_y=None, spacing=dp(5))
        self.ports_grid.bind(minimum_height=self.ports_grid.setter('height'))
        self.ports_list_view.add_widget(self.ports_grid)
        self.main_layout.add_widget(self.ports_list_view)

        buttons_layout = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(10))
        add_button = MDButton(MDButtonText(text="Add New Port"))
        add_button.bind(on_release=self.add_vport_popup)
        buttons_layout.add_widget(add_button)

        close_button = MDButton(MDButtonText(text="Close"))
        close_button.bind(on_release=self.dismiss)
        buttons_layout.add_widget(close_button)
        self.main_layout.add_widget(buttons_layout)

        self.refresh_ports()

    def setContent(self, content):
        self.content = content

    def refresh_ports(self):
        self.ports_grid.clear_widgets()
        for port in self.sequencer.virtual_ports:
            port_layout = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(10))
            port_label = MDLabel(text=port.name, halign='left')
            rename_button = MDButton(MDButtonText(text="Rename"))
            rename_button.bind(on_release=lambda btn, p=port.name: self.rename_vport_popup(p))
            delete_button = MDButton(MDButtonText(text="Delete"))
            delete_button.bind(on_release=lambda btn, p=port.name: self.confirm_delete_vport(p))

            port_layout.add_widget(port_label)
            port_layout.add_widget(rename_button)
            port_layout.add_widget(delete_button)
            self.ports_grid.add_widget(port_layout)

    def add_vport_popup(self, instance):
        def callback(port_name):
            if port_name:
                self.sequencer.create_virtual_port(port_name)
                self.refresh_ports()
        popup = ConfirmationPopup(prompt_text="Enter new port name:", callback=callback)
        popup.open()

    def rename_vport_popup(self, old_name):
        def callback(new_name):
            if new_name:
                self.rename_vport(old_name, new_name)
                self.refresh_ports()

        popup = ConfirmationPopup(prompt_text=f"Enter new name for '{old_name}':", callback=callback)
        popup.open()

    def confirm_delete_vport(self, port_name):
        def on_confirm(choice):
            if choice and choice.lower() == 'y':
                self.sequencer.delete_virtual_port(port_name)
                self.refresh_ports()

        popup = YesNoPopup(
            prompt_text=f"Are you sure you want to delete the virtual port '{port_name}'?",
            callback=on_confirm
        )
        popup.open()

    def rename_vport(self, old_name, new_name):
        # 1. Create the new port
        self.sequencer.create_virtual_port(new_name)

        # 2. Reassign any tracks using the old port
        for track in self.sequencer.song.tracks:
            if isinstance(track, MidiTrack) and track.output_port_name == old_name:
                self.sequencer.assign_port(self.sequencer.song.tracks.index(track), new_name)

        # 3. Delete the old port
        self.sequencer.delete_virtual_port(old_name)
