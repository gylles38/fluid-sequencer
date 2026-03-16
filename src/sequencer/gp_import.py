import guitarpro
from .models import Song, MidiTrack, Event, Note, AutomationTrack, AutomationPoint
import os

def import_gp(filepath: str) -> Song:
    """
    Imports a GuitarPro file and converts it into a Song object.
    """
    try:
        gp_song = guitarpro.parse(filepath)
    except Exception as e:
        msg = str(e)
        if "unsupported version" in msg.lower():
            raise ValueError(f"GuitarPro version not supported by current library: {msg}. (GP7/GP8 files might need to be exported to GP5 first)")
        raise ValueError(f"Failed to parse GuitarPro file: {e}")

    # GuitarPro tempo is in BPM.
    tempo = gp_song.tempo

    # Use the first measure to get time signature if possible
    time_sig_num = 4
    time_sig_den = 4
    if gp_song.tracks and gp_song.tracks[0].measures:
        first_measure = gp_song.tracks[0].measures[0]
        if first_measure.timeSignature:
            time_sig_num = first_measure.timeSignature.numerator
            time_sig_den = first_measure.timeSignature.denominator.value if hasattr(first_measure.timeSignature.denominator, 'value') else first_measure.timeSignature.denominator

    song = Song(
        name=os.path.basename(filepath).rsplit('.', 1)[0],
        tempo=int(tempo),
        time_signature_numerator=time_sig_num,
        time_signature_denominator=time_sig_den
    )

    for gp_track in gp_song.tracks:
        track_name = gp_track.name or f"Track {gp_track.number}"
        midi_track = MidiTrack(
            name=track_name,
            channel=gp_track.channel.channel - 1 if gp_track.channel else 0,
            instrument=gp_track.channel.instrument if gp_track.channel else 0
        )

        automation_track = None

        # Absolute time in beats
        current_measure_start_beat = 0.0

        for gp_measure in gp_track.measures:
            # Update time signature if it changes
            # Note: our current Song model only has one global time signature.
            # For now we'll use the song's global one or the first one found.

            # Fix: Ensure denominator is a number. In some GP versions it's a Duration object.
            denom = gp_measure.timeSignature.denominator.value if hasattr(gp_measure.timeSignature.denominator, 'value') else gp_measure.timeSignature.denominator
            measure_duration_beats = (gp_measure.timeSignature.numerator * 4.0) / denom

            for voice in gp_measure.voices:
                beat_start_in_measure = 0.0
                for beat in voice.beats:
                    start_time_beats = current_measure_start_beat + beat_start_in_measure

                    # Beat duration
                    duration_beats = 4.0 / beat.duration.value
                    if beat.duration.isDotted:
                        duration_beats *= 1.5
                    # Tuplets
                    if hasattr(beat.duration.tuplet, 'enters') and beat.duration.tuplet.enters > 0:
                        duration_beats = duration_beats * beat.duration.tuplet.times / beat.duration.tuplet.enters
                    elif hasattr(beat.duration.tuplet, 'enter') and beat.duration.tuplet.enter > 0:
                        duration_beats = duration_beats * beat.duration.tuplet.times / beat.duration.tuplet.enter

                    notes = []
                    for gp_note in beat.notes:
                        # MIDI pitch calculation
                        # GuitarPro pitch is usually string + fret
                        # But pyguitarpro might provide the real value.
                        # Actually, gp_note.realValue is the MIDI pitch.
                        pitch = gp_note.realValue
                        velocity = gp_note.velocity

                        # Handle bends
                        if gp_note.effect.bend:
                            if automation_track is None:
                                # We'll add the automation track to the song later if it contains points
                                automation_track = AutomationTrack(
                                    name=f"{track_name} Pitch Bend",
                                    target_track_index=len(song.tracks)
                                )

                            # A bend has points.
                            # BendPoint.position is from 0 to 12
                            # BendPoint.value is in quarters of semitone?
                            # Actually: position is 0 to 12 (mapping to beat duration).
                            # Value is 0 to 8 (12? depends on GP version). 1 unit = 1/4 tone.
                            # We need to normalize value to something JackManager understands.
                            # JackManager expects -1.0 to 1.0 (relative to pitch bend range, usually 2 semitones).
                            # If we assume 2 semitones range, 8 units (2 semitones) = 1.0.

                            for bend_point in gp_note.effect.bend.points:
                                # Position is 0 to 12 within the beat
                                point_time = start_time_beats + (bend_point.position / 12.0) * duration_beats
                                # Value: 0 to 12 (usually). 4 units = 1 semitone.
                                # Normalized value: 4 units = 0.5 (if range is 2 semitones).
                                # value / 8.0 gives range -1.0 to 1.0 assuming 2 semitones range.
                                normalized_value = bend_point.value / 8.0
                                automation_track.add_point(AutomationPoint(
                                    start_time=point_time,
                                    parameter="pitch",
                                    value=normalized_value,
                                    curve="linear"
                                ))

                        notes.append(Note(pitch=pitch, velocity=velocity, duration=duration_beats))

                    if notes:
                        event = Event(start_time=start_time_beats, notes=notes)
                        midi_track.add_event(event)

                    beat_start_in_measure += duration_beats

            current_measure_start_beat += measure_duration_beats

        if midi_track.events:
            song.add_track(midi_track)
            if automation_track and automation_track.points:
                song.add_track(automation_track)

    return song
