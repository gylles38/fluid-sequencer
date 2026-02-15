from . import *  # Importe tous les imports communs

class ThreeStateRecordButton(TooltipMDIconButton):
    def __init__(self, track, track_index, sequencer_layout, callback=None, **kwargs):
        super().__init__(**kwargs)
        
        self.track = track
        self.track_index = track_index
        self.sequencer_layout = sequencer_layout
        self.callback = callback
        self.size_hint_x = None
        self.width = dp(44)
        self.size_hint_y = None
        self.height = dp(44)
        self.pos_hint = {'center_y': 0.5}
        self.theme_icon_color = "Custom"
        self.theme_bg_color = "Custom"
        
        self.states = {
            'OFF': {
                'icon': 'record-circle-outline',
                'tooltip': 'Record: OFF - Piste désactivée',
                'icon_color': [0.5, 0.5, 0.5, 1],
                'bg_color': [0.1, 0.1, 0.1, 1]
            },
            'OVERWRITE': {
                'icon': 'record-rec',
                'tooltip': 'Record: OVERWRITE - Écrase les notes existantes',
                'icon_color': [1, 0, 0, 1],  # Rouge
                'bg_color': [0.1, 0.1, 0.1, 1]
            },
            'KEEP': {
                'icon': 'record-circle',
                'tooltip': 'Record: KEEP - Conserve les notes existantes',
                'icon_color': [1, 0.6, 0, 1],  # Orange pour différencier
                'bg_color': [0.1, 0.1, 0.1, 1]
            }
        }
        
        self.update_appearance()
        
    def on_press(self):
        """Cycle through the 3 states on press."""
        old_mode = self.track.record_mode
        
        # Si on essaie d'activer une piste (passer de OFF à OVERWRITE/KEEP)
        if old_mode == 'OFF':
            self.track.record_mode = 'OVERWRITE'
            
        # Si on est déjà activé, cycler entre OVERWRITE et KEEP
        elif old_mode == 'OVERWRITE':
            self.track.record_mode = 'KEEP'
            
        else:  # 'KEEP' -> retour à OFF
            self.track.record_mode = 'OFF'
            
        print(f"DEBUG: Track {self.track_index} record mode changed from {old_mode} to {self.track.record_mode}")
            
        self.update_appearance()
        
        # FORCER la mise à jour de tous les boutons record
        self.sequencer_layout.update_track_record_buttons()
        
        if self.callback:
            self.callback(self.track)
    
    def update_appearance(self):
        """Update button appearance and tooltip based on current state and routing."""
        mode = self.track.record_mode
        state_config = self.states[mode].copy()

        # Visual feedback for dynamic recording
        is_recording = self.sequencer_layout.sequencer.is_recording
        is_playing = self.sequencer_layout.sequencer.playback_state != 'stopped'
        is_target = self.sequencer_layout.sequencer.current_routing_index == self.track_index

        if is_recording and is_playing and is_target and mode != 'OFF':
            # Solid bright red background when this track is actively receiving recorded input
            state_config['bg_color'] = [0.8, 0, 0, 1]
            state_config['icon_color'] = [1, 1, 1, 1]

        self.icon = state_config['icon']
        self.tooltip_text = state_config['tooltip']
        self.icon_color = state_config['icon_color']
        self.md_bg_color = state_config['bg_color']
