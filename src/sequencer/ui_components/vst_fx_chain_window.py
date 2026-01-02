from . import *
from sequencer.models import AudioTrack, VSTPlugin
from kivy.uix.popup import Popup
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.gridlayout import GridLayout
from kivy.properties import ObjectProperty, StringProperty
from kivy.lang import Builder
from kivymd.uix.button import MDButton, MDButtonText, MDIconButton
from kivymd.uix.label import MDLabel
from kivymd.uix.slider import MDSlider
from kivymd.uix.card import MDCard
from kivymd.uix.selectioncontrol import MDCheckbox
from kivy.uix.label import Label
from kivymd.uix.list import MDListItem, MDListItemHeadlineText, MDListItemSupportingText
from kivy.uix.filechooser import FileChooserListView
from kivy.metrics import dp
import os

Builder.load_string("""
<VstFxChainWindow>:
    size_hint: 0.6, 0.8
    auto_dismiss: False

    BoxLayout:
        orientation: 'vertical'
        padding: dp(10)
        spacing: dp(10)

        MDLabel:
            id: track_label
            halign: 'center'
            bold: True
            font_size: '20sp'
            size_hint_y: None
            height: self.texture_size[1]

        BoxLayout:
            orientation: 'horizontal'
            spacing: dp(10)

            # Left Panel: Plugin List
            BoxLayout:
                orientation: 'vertical'
                size_hint_x: 0.4
                spacing: dp(5)
                ScrollView:
                    id: scroll_view
                    size_hint_y: 1
                    MDGridLayout:
                        id: plugin_list
                        cols: 1
                        size_hint_y: None
                        adaptive_height: True
                        spacing: dp(5)
                MDButton:
                    on_press: root.open_file_chooser()
                    size_hint_y: None
                    height: dp(36)
                    MDButtonText:
                        text: "Add Plugin"

            # Right Panel: Parameter Editor
            ScrollView:
                size_hint_x: 0.6
                MDGridLayout:
                    id: parameter_list
                    cols: 1
                    size_hint_y: None
                    adaptive_height: True
                    spacing: dp(10)

        MDButton:
            on_press: root.dismiss()
            size_hint_y: None
            height: dp(36)
            MDButtonText:
                text: "Close"

<PluginListItem>:
    size_hint_y: None
    height: dp(60)

    MDListItemHeadlineText:
        text: root.headline_text

    MDListItemSupportingText:
        text: root.supporting_text

    MDIconButton:
        icon: 'close'
        pos_hint: {"center_y": 0.5, "right": 0.98}
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
        self.bind(on_press=self.select_plugin)

    def remove_plugin(self):
        self.fx_window.remove_plugin(self.plugin)

    def select_plugin(self, instance):
        self.fx_window.select_plugin(self.plugin)


class VstFxChainWindow(Popup):
    track = ObjectProperty(None)
    sequencer = ObjectProperty(None)
    track_index = ObjectProperty(None)

    def __init__(self, track, sequencer, track_index, **kwargs):
        super().__init__(**kwargs)
        self.track = track
        self.sequencer = sequencer
        self.track_index = track_index
        self.title = f"FX Chain - {self.track.name}"
        self.ids.track_label.text = f"Track: {self.track.name}"
        self.selected_plugin = None
        self.refresh_plugin_list()

    def refresh_plugin_list(self):
        plugin_list = self.ids.plugin_list
        plugin_list.clear_widgets()
        for plugin in self.track.plugins:
            item = PluginListItem(plugin=plugin, fx_window=self)
            plugin_list.add_widget(item)

    def select_plugin(self, plugin):
        self.selected_plugin = plugin
        param_list = self.ids.parameter_list
        param_list.clear_widgets()

        for name, value in plugin.parameters.items():
            # Container for each parameter
            param_layout = BoxLayout(orientation='vertical', size_hint_y=None, height=dp(60))

            # Label for the parameter name
            label = MDLabel(text=f"{name}: {value:.2f}", size_hint_y=None, height=dp(24))

            # Slider for the parameter value
            slider = MDSlider(min=0.0, max=1.0, value=value)
            slider.bind(value=lambda instance, v, p_name=name, lbl=label: self.on_parameter_change(p_name, v, lbl))

            param_layout.add_widget(label)
            param_layout.add_widget(slider)
            param_list.add_widget(param_layout)

    def on_parameter_change(self, param_name, value, label):
        if self.selected_plugin:
            # Update the data model
            self.selected_plugin.parameters[param_name] = value
            self.sequencer.is_dirty = True

            # Update the UI label
            label.text = f"{param_name}: {value:.2f}"

            # Trigger audio re-processing
            self.sequencer.vst_audio_processor.process_track(self.track_index)


    def add_plugin(self, path):
        # The user selects the .so file, but pedalboard needs the .vst3 bundle path.
        # We traverse up the path to find the parent directory that ends in .vst3
        plugin_path = path
        is_bundle = False
        while plugin_path != os.path.dirname(plugin_path): # Stop at the root
            if plugin_path.endswith('.vst3'):
                is_bundle = True
                break
            plugin_path = os.path.dirname(plugin_path)

        if not is_bundle:
             print(f"Invalid file selected: {path}. Not part of a .vst3 bundle.")
             return

        # Get the default parameters for the new plugin
        default_params = self.sequencer.vst_audio_processor.get_plugin_parameters(plugin_path)
        plugin = VSTPlugin(path=plugin_path, parameters=default_params)

        self.track.plugins.append(plugin)
        self.sequencer.is_dirty = True

        # Re-process the audio with the new plugin
        self.sequencer.vst_audio_processor.process_track(self.track_index)

        self.refresh_plugin_list()

    def remove_plugin(self, plugin):
        self.track.plugins.remove(plugin)
        self.sequencer.is_dirty = True
        self.sequencer.vst_audio_processor.process_track(self.track_index)
        self.refresh_plugin_list()

    def open_file_chooser(self):
        content = BoxLayout(orientation='vertical', spacing=dp(10))

        # --- VST3 Path Detection ---
        home_dir = os.path.expanduser('~')
        vst3_paths = [
            os.path.join(home_dir, '.vst3'),
            '/usr/lib/vst3',
            '/usr/local/lib/vst3',
        ]
        vst3_path_env = os.environ.get('VST3_PATH')
        if vst3_path_env:
            vst3_paths.extend(vst3_path_env.split(':'))

        start_path = home_dir  # Default fallback
        for path in vst3_paths:
            if os.path.isdir(path):
                start_path = path
                break

        file_chooser = FileChooserListView(path=start_path, filters=['*.so'], show_hidden=False)

        # Checkbox for showing hidden files
        hidden_files_layout = BoxLayout(size_hint_y=None, height=dp(32), spacing=dp(10))
        checkbox = MDCheckbox(size_hint_x=None, width=dp(32))
        checkbox.bind(active=lambda instance, value: setattr(file_chooser, 'show_hidden', value))
        label = Label(text="Show Hidden Files")
        hidden_files_layout.add_widget(checkbox)
        hidden_files_layout.add_widget(label)

        button_layout = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(10))

        popup = Popup(title="Select VST3 Plugin", content=content, size_hint=(0.8, 0.8))

        def select_file(instance):
            if file_chooser.selection:
                self.add_plugin(file_chooser.selection[0])
            popup.dismiss()

        select_button = MDButton(MDButtonText(text="Select"), style="outlined")
        select_button.bind(on_press=select_file)

        cancel_button = MDButton(MDButtonText(text="Cancel"), style="outlined")
        cancel_button.bind(on_press=popup.dismiss)

        button_layout.add_widget(select_button)
        button_layout.add_widget(cancel_button)

        content.add_widget(hidden_files_layout)
        content.add_widget(file_chooser)
        content.add_widget(button_layout)

        popup.open()
