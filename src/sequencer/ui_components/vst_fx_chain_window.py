from . import *
from sequencer.models import AudioTrack, VSTPlugin
from kivy.uix.popup import Popup
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.gridlayout import GridLayout
from kivy.properties import ObjectProperty, StringProperty
from kivy.lang import Builder
from kivymd.uix.button import MDButton
from kivymd.uix.label import MDLabel
from kivymd.uix.card import MDCard
from kivymd.uix.list import MDListItem, MDListItemHeadlineText, MDListItemSupportingText, MDListItemTrailingIcon
from kivy.uix.filechooser import FileChooserListView
from kivy.metrics import dp
import os

Builder.load_string("""
<VstFxChainWindow>:
    title: f"FX Chain - {root.track.name}"
    size_hint: 0.6, 0.8
    auto_dismiss: False

    BoxLayout:
        orientation: 'vertical'
        padding: dp(10)
        spacing: dp(10)

        MDLabel:
            text: f"Track: {root.track.name}"
            halign: 'center'
            font_style: 'H6'
            size_hint_y: None
            height: self.texture_size[1]

        ScrollView:
            id: scroll_view
            size_hint_y: 1
            MDGridLayout:
                id: plugin_list
                cols: 1
                size_hint_y: None
                adaptive_height: True
                spacing: dp(5)

        BoxLayout:
            size_hint_y: None
            height: dp(48)
            spacing: dp(10)
            MDButton:
                text: "Add Plugin"
                on_press: root.open_file_chooser()
            MDButton:
                text: "Close"
                on_press: root.dismiss()

<PluginListItem>:
    size_hint_y: None
    height: dp(60)

    MDListItemHeadlineText:
        text: root.headline_text

    MDListItemSupportingText:
        text: root.supporting_text

    MDListItemTrailingIcon:
        icon: 'close'
        on_press: root.remove_plugin()
""")

class PluginListItem(MDListItem):
    plugin = ObjectProperty(None)
    fx_window = ObjectProperty(None)
    headline_text = StringProperty("")
    supporting_text = StringProperty("")

    def __init__(self, plugin, fx_window, **kwargs):
        super().__init__(**kwargs)
        self.plugin = plugin
        self.fx_window = fx_window
        self.headline_text = os.path.basename(plugin.path)
        self.supporting_text = plugin.path

    def remove_plugin(self):
        self.fx_window.remove_plugin(self.plugin)


class VstFxChainWindow(Popup):
    track = ObjectProperty(None)
    sequencer = ObjectProperty(None)
    track_index = ObjectProperty(None)

    def __init__(self, track, sequencer, track_index, **kwargs):
        super().__init__(**kwargs)
        self.track = track
        self.sequencer = sequencer
        self.track_index = track_index
        self.refresh_plugin_list()

    def refresh_plugin_list(self):
        plugin_list = self.ids.plugin_list
        plugin_list.clear_widgets()
        for plugin in self.track.plugins:
            item = PluginListItem(plugin=plugin, fx_window=self)
            plugin_list.add_widget(item)

    def add_plugin(self, path):
        if path and path.endswith('.vst3'):
            plugin = VSTPlugin(path=path)
            self.track.plugins.append(plugin)
            self.sequencer.is_dirty = True
            self.sequencer.vst_audio_processor.process_track(self.track_index)
            self.refresh_plugin_list()
        else:
            print(f"Invalid file selected: {path}. Please select a .vst3 file.")

    def remove_plugin(self, plugin):
        self.track.plugins.remove(plugin)
        self.sequencer.is_dirty = True
        self.sequencer.vst_audio_processor.process_track(self.track_index)
        self.refresh_plugin_list()

    def open_file_chooser(self):
        content = BoxLayout(orientation='vertical', spacing=dp(10))
        # For now, let's assume VSTs are in a known location or user can navigate
        # Starting in the home directory is a safe bet.
        home_dir = os.path.expanduser('~')
        file_chooser = FileChooserListView(path=home_dir, filters=['*.vst3'])

        button_layout = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(10))

        popup = Popup(title="Select VST3 Plugin", content=content, size_hint=(0.8, 0.8))

        def select_file(instance):
            if file_chooser.selection:
                self.add_plugin(file_chooser.selection[0])
            popup.dismiss()

        select_button = MDButton(text="Select")
        select_button.bind(on_press=select_file)

        cancel_button = MDButton(text="Cancel")
        cancel_button.bind(on_press=popup.dismiss)

        button_layout.add_widget(select_button)
        button_layout.add_widget(cancel_button)

        content.add_widget(file_chooser)
        content.add_widget(button_layout)

        popup.open()
