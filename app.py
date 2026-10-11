import sys
import subprocess
import json
import os
import wave
import struct
import math
import tempfile
import uuid
import csv
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QFileDialog, QLabel, QPlainTextEdit, 
    QGroupBox, QSlider, QTimeEdit, QMessageBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QCheckBox, QComboBox, QInputDialog, 
    QColorDialog, QDialog, QFormLayout, QDialogButtonBox, QSpinBox
)
from PySide6.QtMultimedia import (
    QMediaPlayer, QAudioOutput, QMediaCaptureSession, QMediaRecorder, 
    QAudioInput, QMediaDevices
)
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtTextToSpeech import QTextToSpeech
from PySide6.QtCore import Qt, QUrl, QTime, QTimer
from PySide6.QtGui import QShortcut, QKeySequence, QColor, QBrush

# --- Helper function to generate the ADR beep ---
def generate_beep():
    f = 1000.0; fs = 44100; duration = 0.05
    wav_file = os.path.join(tempfile.gettempdir(), "dubsub_adr_beep.wav")
    with wave.open(wav_file, 'w') as f_wav:
        f_wav.setnchannels(1); f_wav.setsampwidth(2); f_wav.setframerate(fs)
        for _ in range(int(fs * duration)):
            value = int(32767 * 0.5 * math.sin(2 * math.pi * f * _ / fs))
            f_wav.writeframesraw(struct.pack('<h', value))
    return wav_file

class FindReplaceDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Find & Replace"); self.setModal(True)
        layout = QFormLayout(self)
        self.txt_find = QPlainTextEdit(); self.txt_find.setFixedHeight(40)
        self.txt_replace = QPlainTextEdit(); self.txt_replace.setFixedHeight(40)
        layout.addRow("Find:", self.txt_find); layout.addRow("Replace with:", self.txt_replace)
        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(self.accept); btn_box.rejected.connect(self.reject)
        layout.addRow(btn_box)

class DubSubApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Pro Dub & Sub Studio v12.0 - Master Edition")
        self.resize(1400, 900)

        # State Variables
        self.video_url = None
        self.subtitles = []
        self.is_recording = False
        self.dubbing_audio_url = None
        self.editing_sub_index = -1
        self.is_looping = False
        self.is_previewing_dub = False
        self.rapid_entry_active = False
        self.sub_color = "#FFFFFF"
        self.dub_offset_ms = 0
        self.is_punching_in = False
        self.is_punch_and_roll = False
        
        # ADR State
        self.adr_temp_dir = tempfile.mkdtemp(prefix="dubsub_adr_")
        self.dub_takes = {}  # Maps subtitle UID to list of file paths
        self.current_take_index = {} # Maps uid to the selected take index
        self.beep_played_3 = False
        self.beep_played_2 = False
        self.beep_played_1 = False

        self.setup_ui()
        self.setup_media()
        self.setup_tts()
        self.setup_shortcuts()
        self.apply_dark_theme()
        self.populate_audio_devices()

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)

        # --- Left Side: Video ---
        left_layout = QVBoxLayout()
        self.video_widget = QVideoWidget()
        self.video_widget.setMinimumSize(640, 480)
        
        self.subtitle_label = QLabel("", self.video_widget)
        self.update_sub_style()
        self.subtitle_label.setAlignment(Qt.AlignCenter)
        self.subtitle_label.hide()

        self.countdown_label = QLabel("", self.video_widget)
        self.countdown_label.setStyleSheet("color: rgba(255, 255, 255, 200); font-size: 120px; font-weight: bold;")
        self.countdown_label.setAlignment(Qt.AlignCenter)
        self.countdown_label.hide()

        seek_layout = QHBoxLayout()
        self.lbl_time = QLabel("00:00:00 / 00:00:00")
        self.lbl_time.setFixedWidth(180)
        self.seek_slider = QSlider(Qt.Horizontal)
        self.seek_slider.setRange(0, 0)
        self.seek_slider.sliderMoved.connect(self.set_position)
        seek_layout.addWidget(self.lbl_time); seek_layout.addWidget(self.seek_slider)
        
        controls_layout = QHBoxLayout()
        self.btn_load = QPushButton("Load Video")
        self.btn_play = QPushButton("Play")
        self.btn_skip_back = QPushButton("<< 5s")
        self.btn_skip_fwd = QPushButton("5s >>")
        self.btn_play.clicked.connect(self.toggle_play_pause)
        self.btn_load.clicked.connect(self.load_video)
        self.btn_skip_back.clicked.connect(lambda: self.skip_time(-5000))
        self.btn_skip_fwd.clicked.connect(lambda: self.skip_time(5000))

        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100); self.volume_slider.setValue(80)
        self.volume_slider.setFixedWidth(100)
        self.volume_slider.valueChanged.connect(self.set_volume)

        self.combo_speed = QComboBox()
        self.combo_speed.addItems(["0.5x", "0.75x", "1.0x", "1.25x", "1.5x", "2.0x"])
        self.combo_speed.setCurrentIndex(2); self.combo_speed.setFixedWidth(70)
        self.combo_speed.currentIndexChanged.connect(self.change_speed)

        controls_layout.addWidget(self.btn_load); controls_layout.addWidget(self.btn_skip_back)
        controls_layout.addWidget(self.btn_play); controls_layout.addWidget(self.btn_skip_fwd)
        controls_layout.addStretch()
        controls_layout.addWidget(QLabel("Speed:")); controls_layout.addWidget(self.combo_speed)
        controls_layout.addWidget(QLabel("Vol:")); controls_layout.addWidget(self.volume_slider)

        av_layout = QHBoxLayout()
        av_layout.addWidget(QLabel("Sub Size:"))
        self.font_slider = QSlider(Qt.Horizontal)
        self.font_slider.setRange(12, 48); self.font_slider.setValue(24)
        self.font_slider.valueChanged.connect(self.update_sub_style)
        av_layout.addWidget(self.font_slider)
        
        self.chk_bold = QCheckBox("B"); self.chk_bold.setStyleSheet("font-weight: bold;")
        self.chk_bold.stateChanged.connect(self.update_sub_style)
        self.chk_italic = QCheckBox("I"); self.chk_italic.setStyleSheet("font-style: italic;")
        self.chk_italic.stateChanged.connect(self.update_sub_style)
        av_layout.addWidget(self.chk_bold); av_layout.addWidget(self.chk_italic)
        
        self.btn_text_color = QPushButton("Text Color")
        self.btn_text_color.clicked.connect(self.change_text_color)
        av_layout.addWidget(self.btn_text_color)

        self.btn_preview_dub = QPushButton("Preview Dub")
        self.btn_preview_dub.setCheckable(True)
        self.btn_preview_dub.clicked.connect(self.toggle_dub_preview)
        av_layout.addWidget(self.btn_preview_dub)

        left_layout.addWidget(self.video_widget)
        left_layout.addLayout(seek_layout)
        left_layout.addLayout(controls_layout)
        left_layout.addLayout(av_layout)
        main_layout.addLayout(left_layout, 2)

        # --- Right Side: Controls ---
        right_layout = QVBoxLayout()

        # Subtitle Group
        sub_group = QGroupBox("Subtitles")
        sub_layout = QVBoxLayout()
        self.sub_table = QTableWidget(0, 8)
        self.sub_table.setHorizontalHeaderLabels(["#", "Start", "End", "CPS", "Pos", "Takes", "Status", "Text"])
        self.sub_table.horizontalHeader().setSectionResizeMode(7, QHeaderView.Stretch)
        self.sub_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.sub_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.sub_table.itemSelectionChanged.connect(self.on_sub_select)
        sub_layout.addWidget(self.sub_table)

        time_layout = QHBoxLayout()
        self.time_start = QTimeEdit(); self.time_start.setDisplayFormat("HH:mm:ss,zzz")
        self.time_end = QTimeEdit(); self.time_end.setDisplayFormat("HH:mm:ss,zzz")
        self.btn_set_start = QPushButton("Set Start (I)")
        self.btn_set_end = QPushButton("Set End (O)")
        self.btn_auto_end = QPushButton("Auto End")
        self.btn_set_start.clicked.connect(lambda: self.grab_time("start"))
        self.btn_set_end.clicked.connect(lambda: self.grab_time("end"))
        self.btn_auto_end.clicked.connect(self.auto_set_end_time)
        self.btn_nudge_start_back = QPushButton("-100ms")
        self.btn_nudge_start_fwd = QPushButton("+100ms")
        self.btn_nudge_start_back.clicked.connect(lambda: self.nudge_time("start", -100))
        self.btn_nudge_start_fwd.clicked.connect(lambda: self.nudge_time("start", 100))
        time_layout.addWidget(self.time_start); time_layout.addWidget(self.btn_nudge_start_back)
        time_layout.addWidget(self.btn_nudge_start_fwd); time_layout.addWidget(self.btn_set_start)
        time_layout.addWidget(self.time_end); time_layout.addWidget(self.btn_set_end)
        time_layout.addWidget(self.btn_auto_end)
        sub_layout.addLayout(time_layout)

        end_nudge_layout = QHBoxLayout()
        self.btn_nudge_end_back = QPushButton("-100ms")
        self.btn_nudge_end_fwd = QPushButton("+100ms")
        self.btn_nudge_end_back.clicked.connect(lambda: self.nudge_time("end", -100))
        self.btn_nudge_end_fwd.clicked.connect(lambda: self.nudge_time("end", 100))
        self.btn_shift_all = QPushButton("Shift All (Sync)")
        self.btn_shift_all.clicked.connect(self.shift_all_subs)
        self.btn_auto_fix = QPushButton("Auto-Fix Timings")
        self.btn_auto_fix.setStyleSheet("background-color: #27ae60; color: white;")
        self.btn_auto_fix.clicked.connect(self.auto_fix_timings)
        end_nudge_layout.addWidget(self.btn_nudge_end_back); end_nudge_layout.addWidget(self.btn_nudge_end_fwd)
        end_nudge_layout.addWidget(self.btn_shift_all); end_nudge_layout.addWidget(self.btn_auto_fix)
        end_nudge_layout.addStretch()
        sub_layout.addLayout(end_nudge_layout)

        text_layout = QHBoxLayout()
        self.input_text = QPlainTextEdit()
        self.input_text.setFixedHeight(60)
        self.input_text.setPlaceholderText("Type text... (Ctrl+Enter to Save/Add Next)")
        self.input_text.textChanged.connect(self.update_cpl_label)
        text_controls = QVBoxLayout()
        self.lbl_cpl = QLabel("Max CPL: 0")
        self.lbl_cpl.setStyleSheet("font-weight: bold; color: green;")
        self.combo_position = QComboBox()
        self.combo_position.addItems(["Bottom", "Middle", "Top"])
        self.combo_position.currentIndexChanged.connect(self.update_preview_position)
        text_controls.addWidget(self.lbl_cpl); text_controls.addWidget(self.combo_position)
        text_layout.addLayout(text_controls); text_layout.addWidget(self.input_text, 1)
        sub_layout.addLayout(text_layout)

        input_layout = QHBoxLayout()
        self.btn_add_sub = QPushButton("Add")
        self.btn_update_sub = QPushButton("Update")
        self.btn_delete_sub = QPushButton("Delete")
        self.btn_add_sub.clicked.connect(self.add_subtitle)
        self.btn_update_sub.clicked.connect(self.update_subtitle)
        self.btn_delete_sub.clicked.connect(self.delete_subtitle)
        input_layout.addWidget(self.btn_add_sub); input_layout.addWidget(self.btn_update_sub); input_layout.addWidget(self.btn_delete_sub)
        sub_layout.addLayout(input_layout)

        mod_layout = QHBoxLayout()
        self.btn_merge_next = QPushButton("Merge Next")
        self.btn_split_at_cursor = QPushButton("Split at Cursor")
        self.btn_find_replace = QPushButton("Find & Replace")
        self.btn_merge_next.clicked.connect(self.merge_with_next)
        self.btn_split_at_cursor.clicked.connect(self.split_at_cursor)
        self.btn_find_replace.clicked.connect(self.open_find_replace)
        mod_layout.addWidget(self.btn_merge_next); mod_layout.addWidget(self.btn_split_at_cursor); mod_layout.addWidget(self.btn_find_replace)
        sub_layout.addLayout(mod_layout)

        file_layout = QHBoxLayout()
        self.btn_import_srt = QPushButton("Import SRT")
        self.btn_export_srt = QPushButton("Export SRT")
        self.btn_export_vtt = QPushButton("Export VTT")
        self.btn_export_csv = QPushButton("Export CSV")
        self.btn_save_proj = QPushButton("Save Project")
        self.btn_load_proj = QPushButton("Load Project")
        self.btn_import_srt.clicked.connect(self.import_srt)
        self.btn_export_srt.clicked.connect(self.export_srt)
        self.btn_export_vtt.clicked.connect(self.export_vtt)
        self.btn_export_csv.clicked.connect(self.export_csv)
        self.btn_save_proj.clicked.connect(self.save_project)
        self.btn_load_proj.clicked.connect(self.load_project)
        file_layout.addWidget(self.btn_import_srt); file_layout.addWidget(self.btn_export_srt)
        file_layout.addWidget(self.btn_export_vtt); file_layout.addWidget(self.btn_export_csv)
        file_layout.addWidget(self.btn_save_proj); file_layout.addWidget(self.btn_load_proj)
        sub_layout.addLayout(file_layout)

        sub_group.setLayout(sub_layout)
        right_layout.addWidget(sub_group)

        # Dubbing Group
        dub_group = QGroupBox("Dubbing & ADR (FFmpeg)")
        dub_layout = QVBoxLayout()
        
        device_layout = QHBoxLayout()
        device_layout.addWidget(QLabel("Mic:"))
        self.combo_input_device = QComboBox()
        self.combo_input_device.currentIndexChanged.connect(self.change_input_device)
        device_layout.addWidget(self.combo_input_device, 1)
        device_layout.addWidget(QLabel("Speakers:"))
        self.combo_output_device = QComboBox()
        self.combo_output_device.currentIndexChanged.connect(self.change_output_device)
        device_layout.addWidget(self.combo_output_device, 1)
        dub_layout.addLayout(device_layout)

        gain_layout = QHBoxLayout()
        gain_layout.addWidget(QLabel("Mic Gain:"))
        self.slider_mic_gain = QSlider(Qt.Horizontal)
        self.slider_mic_gain.setRange(0, 100); self.slider_mic_gain.setValue(80)
        self.slider_mic_gain.valueChanged.connect(self.set_mic_gain)
        gain_layout.addWidget(self.slider_mic_gain)
        gain_layout.addWidget(QLabel("Pre-Roll:"))
        self.spin_preroll = QSpinBox()
        self.spin_preroll.setRange(1, 10); self.spin_preroll.setValue(3)
        gain_layout.addWidget(self.spin_preroll)
        dub_layout.addLayout(gain_layout)

        adr_layout = QHBoxLayout()
        self.chk_auto_next = QCheckBox("Auto-Next")
        self.chk_auto_next.setChecked(True)
        self.chk_loop_adr = QCheckBox("Loop ADR (3x)")
        adr_layout.addWidget(self.chk_auto_next); adr_layout.addWidget(self.chk_loop_adr)
        adr_layout.addStretch()
        self.btn_tts = QPushButton("Speak Line (TTS)")
        self.btn_tts.clicked.connect(self.speak_tts)
        adr_layout.addWidget(self.btn_tts)
        dub_layout.addLayout(adr_layout)
        
        record_layout = QHBoxLayout()
        self.btn_punch_in = QPushButton("Record Line (Beep)")
        self.btn_punch_in.setStyleSheet("background-color: #8e44ad; color: white; padding: 10px;")
        self.btn_punch_in.clicked.connect(self.start_punch_in)
        record_layout.addWidget(self.btn_punch_in)
        
        self.btn_punch_roll = QPushButton("Punch & Roll")
        self.btn_punch_roll.setStyleSheet("background-color: #2c3e50; color: white; padding: 10px;")
        self.btn_punch_roll.clicked.connect(self.start_punch_and_roll)
        record_layout.addWidget(self.btn_punch_roll)
        dub_layout.addLayout(record_layout)
        
        take_layout = QHBoxLayout()
        take_layout.addWidget(QLabel("Take:"))
        self.combo_takes = QComboBox()
        self.combo_takes.currentIndexChanged.connect(self.select_take)
        take_layout.addWidget(self.combo_takes)
        self.btn_play_take = QPushButton("Play Take")
        self.btn_play_take.clicked.connect(self.play_selected_take)
        take_layout.addWidget(self.btn_play_take)
        take_layout.addStretch()
        dub_layout.addLayout(take_layout)
        
        dub_controls = QHBoxLayout()
        self.btn_record = QPushButton("Record Full Dub")
        self.btn_record.setStyleSheet("background-color: #c0392b; color: white; font-weight: bold; padding: 10px;")
        self.btn_record.clicked.connect(self.toggle_recording)
        self.btn_assemble = QPushButton("Assemble Dub Track")
        self.btn_assemble.setStyleSheet("background-color: #d35400; color: white; padding: 10px;")
        self.btn_assemble.clicked.connect(self.assemble_dub_track)
        self.btn_import_audio = QPushButton("Import Audio")
        self.btn_import_audio.clicked.connect(self.import_dub_audio)
        self.btn_loop = QPushButton("Loop Segment")
        self.btn_loop.setCheckable(True)
        self.btn_loop.clicked.connect(self.toggle_loop)

        dub_controls.addWidget(self.btn_record); dub_controls.addWidget(self.btn_assemble)
        dub_controls.addWidget(self.btn_import_audio); dub_controls.addWidget(self.btn_loop)
        dub_layout.addLayout(dub_controls)
        
        self.lbl_record_status = QLabel("Status: Not Recording | Dub Track: None")

        mix_layout = QHBoxLayout()
        mix_layout.addWidget(QLabel("Duck Vol:"))
        self.slider_duck_vol = QSlider(Qt.Horizontal)
        self.slider_duck_vol.setRange(0, 100); self.slider_duck_vol.setValue(20)
        self.slider_duck_vol.setFixedWidth(100)
        mix_layout.addWidget(self.slider_duck_vol)
        mix_layout.addStretch()
        mix_layout.addWidget(QLabel("DubOffset (ms):"))
        self.lbl_dub_offset = QLabel("0")
        self.slider_dub_offset = QSlider(Qt.Horizontal)
        self.slider_dub_offset.setRange(-1000, 1000); self.slider_dub_offset.setValue(0)
        self.slider_dub_offset.setFixedWidth(150)
        self.slider_dub_offset.valueChanged.connect(self.update_dub_offset)
        mix_layout.addWidget(self.slider_dub_offset); mix_layout.addWidget(self.lbl_dub_offset)
        dub_layout.addLayout(mix_layout)

        merge_layout = QHBoxLayout()
        self.btn_merge = QPushButton("Merge Dub & Video")
        self.btn_merge.setStyleSheet("background-color: #2980b9; color: white; padding: 10px;")
        self.btn_merge.clicked.connect(self.merge_audio_video)
        self.btn_export_audio = QPushButton("Export Mixed Audio")
        self.btn_export_audio.clicked.connect(self.export_mixed_audio)
        self.chk_normalize = QCheckBox("Normalize (EBU R128)")
        self.chk_normalize.setChecked(True)
        self.chk_clean = QCheckBox("Clean Audio (Denoise)")
        self.chk_clean.setChecked(True)
        merge_layout.addWidget(self.chk_clean); merge_layout.addWidget(self.chk_normalize)
        merge_layout.addWidget(self.btn_merge); merge_layout.addWidget(self.btn_export_audio)
        dub_layout.addLayout(merge_layout)
        
        dub_layout.addWidget(self.lbl_record_status)
        dub_group.setLayout(dub_layout)
        right_layout.addWidget(dub_group)

        main_layout.addLayout(right_layout, 1)
        self.video_widget.resizeEvent = self.on_video_resize

    def setup_media(self):
        self.media_player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.media_player.setAudioOutput(self.audio_output)
        self.media_player.setVideoOutput(self.video_widget)
        self.audio_output.setVolume(0.8)

        self.media_player.positionChanged.connect(self.position_changed)
        self.media_player.durationChanged.connect(self.duration_changed)
        self.media_player.positionChanged.connect(self.update_subtitles)
        self.media_player.positionChanged.connect(self.check_loop)
        self.media_player.positionChanged.connect(self.check_punch_in)
        self.media_player.positionChanged.connect(self.check_punch_and_roll)
        self.media_player.mediaStatusChanged.connect(self.check_media_end)

        self.dub_player = QMediaPlayer()
        self.dub_audio_output = QAudioOutput()
        self.dub_player.setAudioOutput(self.dub_audio_output)
        self.dub_audio_output.setVolume(1.0)

        self.beep_player = QMediaPlayer()
        self.beep_audio_output = QAudioOutput()
        self.beep_player.setAudioOutput(self.beep_audio_output)
        self.beep_player.setSource(QUrl.fromLocalFile(generate_beep()))

        self.capture_session = QMediaCaptureSession()
        self.recorder = QMediaRecorder()
        self.audio_input = QAudioInput()
        self.capture_session.setRecorder(self.recorder)
        self.capture_session.setAudioInput(self.audio_input)

    def setup_tts(self):
        self.tts = QTextToSpeech(self)
        voices = self.tts.availableVoices()
        if voices: self.tts.setVoice(voices[0])

    def populate_audio_devices(self):
        self.combo_input_device.clear()
        for device in QMediaDevices.audioInputs(): self.combo_input_device.addItem(device.description(), device)
        self.combo_output_device.clear()
        for device in QMediaDevices.audioOutputs(): self.combo_output_device.addItem(device.description(), device)

    def change_input_device(self, index):
        device = self.combo_input_device.itemData(index)
        if device: self.audio_input.setDevice(device)

    def change_output_device(self, index):
        device = self.combo_output_device.itemData(index)
        if device:
            self.audio_output.setDevice(device); self.dub_audio_output.setDevice(device)
            self.beep_audio_output.setDevice(device)

    def set_mic_gain(self, vol): self.audio_input.setVolume(vol / 100.0)

    def setup_shortcuts(self):
        QShortcut(QKeySequence(Qt.Key_Space), self, activated=self.toggle_play_pause)
        QShortcut(QKeySequence(Qt.Key_I), self, activated=lambda: self.grab_time("start"))
        QShortcut(QKeySequence(Qt.Key_O), self, activated=lambda: self.grab_time("end"))
        QShortcut(QKeySequence(Qt.Key_Right), self, activated=lambda: self.skip_time(5000))
        QShortcut(QKeySequence(Qt.Key_Left), self, activated=lambda: self.skip_time(-5000))
        QShortcut(QKeySequence(Qt.Key_Escape), self, activated=self.cancel_rapid_entry)
        QShortcut(QKeySequence(Qt.Key_Up), self, activated=lambda: self.navigate_table(-1))
        QShortcut(QKeySequence(Qt.Key_Down), self, activated=lambda: self.navigate_table(1))
        QShortcut(QKeySequence("Ctrl+F"), self, activated=self.open_find_replace)
        QShortcut(QKeySequence("Ctrl+M"), self, activated=self.merge_with_next)
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self.rapid_subtitle_entry)
        QShortcut(QKeySequence("Ctrl+P"), self, activated=self.start_punch_in)
        QShortcut(QKeySequence("Ctrl+R"), self, activated=self.start_punch_and_roll)
        QShortcut(QKeySequence("Ctrl+T"), self, activated=self.speak_tts)
        QShortcut(QKeySequence(Qt.Key_1), self, activated=lambda: self.set_line_status("Todo"))
        QShortcut(QKeySequence(Qt.Key_2), self, activated=lambda: self.set_line_status("Done"))
        QShortcut(QKeySequence(Qt.Key_3), self, activated=lambda: self.set_line_status("Re-take"))
        QShortcut(QKeySequence(Qt.Key_Z), self, activated=lambda: self.nudge_time("start", -100))
        QShortcut(QKeySequence(Qt.Key_X), self, activated=lambda: self.nudge_time("start", 100))
        QShortcut(QKeySequence(Qt.Key_N), self, activated=lambda: self.nudge_time("end", -100))
        QShortcut(QKeySequence(Qt.Key_M), self, activated=lambda: self.nudge_time("end", 100))

    def apply_dark_theme(self):
        dark_stylesheet = """
            QMainWindow { background-color: #1e1e1e; color: #d4d4d4; }
            QWidget { background-color: #1e1e1e; color: #d4d4d4; }
            QPushButton { background-color: #333333; border: 1px solid #555555; padding: 6px; border-radius: 4px; }
            QPushButton:hover { background-color: #3a3a3a; border: 1px solid #888888; }
            QPushButton:checked { background-color: #444444; border: 1px solid #aaaaaa; }
            QLineEdit, QTimeEdit, QComboBox, QPlainTextEdit, QSpinBox { background-color: #2d2d2d; border: 1px solid #555555; padding: 4px; border-radius: 2px; }
            QTableWidget { background-color: #252525; alternate-background-color: #2d2d2d; gridline-color: #444444; }
            QHeaderView::section { background-color: #333333; padding: 4px; border: 1px solid #555555; color: #d4d4d4; }
            QGroupBox { border: 1px solid #555555; border-radius: 4px; margin-top: 10px; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 3px; }
            QSlider::groove:horizontal { border: 1px solid #555555; height: 4px; background: #333333; }
            QSlider::handle:horizontal { background: #d4d4d4; width: 10px; margin: -4px 0; border-radius: 5px; }
            QCheckBox { spacing: 8px; } QLabel { background-color: transparent; }
        """
        self.setStyleSheet(dark_stylesheet)

    # --- Video & TTS Functions ---
    def load_video(self):
        file_name, _ = QFileDialog.getOpenFileName(self, "Open Video File", "", "Video Files (*.mp4 *.avi *.mkv *.mov)")
        if file_name:
            self.video_url = QUrl.fromLocalFile(file_name)
            self.media_player.setSource(self.video_url)

    def toggle_play_pause(self):
        if self.media_player.playbackState() == QMediaPlayer.PlayingState: self.pause_video()
        else: self.play_video()

    def play_video(self):
        if self.media_player.source():
            self.media_player.play()
            if self.is_previewing_dub and self.dub_player.source(): self.dub_player.play()

    def pause_video(self):
        self.media_player.pause()
        if self.is_previewing_dub: self.dub_player.pause()

    def change_speed(self, index):
        speed = float(self.combo_speed.currentText().replace('x', ''))
        self.media_player.setPlaybackRate(speed)
        if self.is_previewing_dub: self.dub_player.setPlaybackRate(speed)

    def speak_tts(self):
        text = self.input_text.toPlainText().strip()
        if text: self.tts.say(text)

    def toggle_dub_preview(self):
        if not self.dubbing_audio_url:
            QMessageBox.warning(self, "Error", "No assembled dub track! Record lines and click 'Assemble Dub Track' first.")
            self.btn_preview_dub.setChecked(False); return
            
        self.is_previewing_dub = self.btn_preview_dub.isChecked()
        if self.is_previewing_dub:
            self.dub_player.setSource(QUrl.fromLocalFile(self.dubbing_audio_url))
            self.audio_output.setMuted(True); self.dub_audio_output.setMuted(False)
            self.btn_preview_dub.setText("Stop Dub Preview")
            self.dub_player.setPosition(max(0, self.media_player.position() - self.dub_offset_ms))
            self.play_video()
        else:
            self.dub_player.stop(); self.audio_output.setMuted(False)
            self.btn_preview_dub.setText("Preview Dub")

    def skip_time(self, ms): self.media_player.setPosition(max(0, min(self.media_player.position() + ms, self.media_player.duration())))
    def set_volume(self, vol): self.audio_output.setVolume(vol / 100.0)
    def set_position(self, position):
        self.media_player.setPosition(position)
        if self.is_previewing_dub: self.dub_player.setPosition(max(0, position - self.dub_offset_ms))

    def position_changed(self, position):
        self.seek_slider.setValue(position)
        self.update_time_label(position, self.media_player.duration())
        
        if self.is_previewing_dub and self.dub_player.source():
            target_dub_pos = position - self.dub_offset_ms
            if abs(self.dub_player.position() - target_dub_pos) > 200: self.dub_player.setPosition(max(0, target_dub_pos))

        if self.is_punching_in and not self.is_recording and not self.is_punch_and_roll:
            sub = self.subtitles[self.editing_sub_index]
            time_to_start = sub['start'] - position
            preroll = self.spin_preroll.value() * 1000
            
            if preroll >= 3000 and time_to_start <= 3000 and time_to_start > 2000 and not self.beep_played_3: self.beep_player.play(); self.beep_played_3 = True
            elif preroll >= 2000 and time_to_start <= 2000 and time_to_start > 1000 and not self.beep_played_2: self.beep_player.play(); self.beep_played_2 = True
            elif preroll >= 1000 and time_to_start <= 1000 and time_to_start > 0 and not self.beep_played_1: self.beep_player.play(); self.beep_played_1 = True

            if time_to_start > 0 and time_to_start < self.spin_preroll.value() * 1000:
                self.countdown_label.setText(str(int(time_to_start / 1000) + 1)); self.countdown_label.show()
            else: self.countdown_label.hide()

    def duration_changed(self, duration): self.seek_slider.setRange(0, duration)
    def update_time_label(self, current, total):
        c = QTime(0, 0, 0).addMSecs(current).toString("HH:mm:ss"); t = QTime(0, 0, 0).addMSecs(total).toString("HH:mm:ss")
        self.lbl_time.setText(f"{c} / {t}")

    def check_media_end(self, status):
        if status == QMediaPlayer.EndOfMedia:
            if self.is_recording: self.toggle_recording() 

    def on_video_resize(self, event):
        self.update_preview_position()
        self.countdown_label.setGeometry(0, 0, self.video_widget.width(), self.video_widget.height())

    def update_sub_style(self):
        size = self.font_slider.value()
        weight = "bold" if self.chk_bold.isChecked() else "normal"
        style = "italic" if self.chk_italic.isChecked() else "normal"
        self.subtitle_label.setStyleSheet(f"color: {self.sub_color}; background-color: rgba(0, 0, 0, 150); font-size: {size}px; font-weight: {weight}; font-style: {style}; padding: 10px;")

    def change_text_color(self):
        color = QColorDialog.getColor(QColor(self.sub_color), self, "Select Subtitle Color")
        if color.isValid(): self.sub_color = color.name(); self.update_sub_style()

    def update_preview_position(self):
        w = self.video_widget.width(); h = self.video_widget.height()
        pos = self.combo_position.currentText()
        if pos == "Top": self.subtitle_label.setGeometry(0, 50, w, 80)
        elif pos == "Middle": self.subtitle_label.setGeometry(0, int(h/2) - 40, w, 80)
        else: self.subtitle_label.setGeometry(0, h - 100, w, 80)

    # --- Loop & Multi-Take ADR Logic ---
    def toggle_loop(self):
        self.is_looping = self.btn_loop.isChecked()
        if self.is_looping and self.editing_sub_index != -1:
            self.media_player.setPosition(self.subtitles[self.editing_sub_index]['start']); self.play_video()

    def check_loop(self, position):
        if self.is_looping and self.editing_sub_index != -1:
            sub = self.subtitles[self.editing_sub_index]
            if position >= sub['end'] or position < sub['start']: self.media_player.setPosition(sub['start'])

    def start_punch_in(self):
        if self.is_recording: return
        if self.editing_sub_index == -1: return
            
        sub = self.subtitles[self.editing_sub_index]
        uid = sub.get('uid')
        if uid not in self.dub_takes: self.dub_takes[uid] = []
        
        take_num = len(self.dub_takes[uid])
        if take_num >= 3:
            QMessageBox.information(self, "Max Takes", "Already have 3 takes. Please delete one or assemble.")
            return
            
        file_path = os.path.join(self.adr_temp_dir, f"line_{uid}_take{take_num}.wav")
        self.current_punch_in_file = file_path
        self.recorder.setOutputLocation(QUrl.fromLocalFile(file_path))
        
        start_pos = max(0, sub['start'] - (self.spin_preroll.value() * 1000))
        self.beep_played_3 = False; self.beep_played_2 = False; self.beep_played_1 = False
        
        self.media_player.setPosition(start_pos); self.audio_output.setMuted(True)
        self.play_video(); self.is_punching_in = True
        self.lbl_record_status.setText(f"Status: ADR Active (Take {take_num + 1})...")

    def check_punch_in(self, position):
        if not self.is_punching_in or self.is_punch_and_roll: return
        sub = self.subtitles[self.editing_sub_index]
        
        if position >= sub['start'] and not self.is_recording:
            self.countdown_label.hide(); self.recorder.record(); self.is_recording = True
            self.lbl_record_status.setText(f"Status: RECORDING (Take {len(self.dub_takes[sub.get('uid')]) + 1})...")
            
        if position >= sub['end'] and self.is_recording:
            self.recorder.stop(); self.is_recording = False; self.is_punching_in = False
            uid = sub.get('uid')
            self.dub_takes[uid].append(self.current_punch_in_file)
            self.current_take_index[uid] = len(self.dub_takes[uid]) - 1 
            self.update_table_row(self.editing_sub_index)
            self.populate_take_combobox()
            
            if self.chk_loop_adr.isChecked() and len(self.dub_takes[uid]) < 3:
                QTimer.singleShot(500, self.start_punch_in) 
            else:
                self.lbl_record_status.setText(f"Status: Take {len(self.dub_takes[uid])} saved. Ready to review.")
                self.play_selected_take()

    def start_punch_and_roll(self):
        if self.is_recording: return
        if self.editing_sub_index == -1: return
        
        curr_sub = self.subtitles[self.editing_sub_index]
        uid = curr_sub.get('uid')
        if uid not in self.dub_takes: self.dub_takes[uid] = []
        take_num = len(self.dub_takes[uid])
        if take_num >= 3:
            QMessageBox.information(self, "Max Takes", "Already have 3 takes.")
            return
            
        self.roll_back_pos = 0
        self.roll_back_take_file = None
        
        for i in range(self.editing_sub_index - 1, -1, -1):
            p_sub = self.subtitles[i]
            p_uid = p_sub.get('uid')
            if p_uid in self.dub_takes and self.dub_takes[p_uid]:
                self.roll_back_pos = p_sub['start']
                self.roll_back_take_file = self.dub_takes[p_uid][-1]
                break
                
        if not self.roll_back_take_file:
            self.start_punch_in()
            return
            
        file_path = os.path.join(self.adr_temp_dir, f"line_{uid}_take{take_num}.wav")
        self.current_punch_in_file = file_path
        self.recorder.setOutputLocation(QUrl.fromLocalFile(file_path))
        
        self.is_punching_in = True
        self.is_punch_and_roll = True
        
        self.dub_player.setSource(QUrl.fromLocalFile(self.roll_back_take_file))
        self.dub_player.setPosition(0)
        self.dub_audio_output.setMuted(False)
        self.audio_output.setMuted(True) 
        
        self.media_player.setPosition(self.roll_back_pos)
        self.play_video()
        self.dub_player.play()
        self.lbl_record_status.setText(f"Status: Rolling... (Take {take_num + 1})")

    def check_punch_and_roll(self, position):
        if not self.is_punching_in or not self.is_punch_and_roll: return
        curr_sub = self.subtitles[self.editing_sub_index]
        
        if position >= curr_sub['start'] and not self.is_recording:
            self.dub_player.stop()
            self.recorder.record(); self.is_recording = True
            self.lbl_record_status.setText(f"Status: RECORDING (Punch & Roll)...")
            
        if position >= curr_sub['end'] and self.is_recording:
            self.recorder.stop(); self.is_recording = False; self.is_punching_in = False; self.is_punch_and_roll = False
            uid = curr_sub.get('uid')
            self.dub_takes[uid].append(self.current_punch_in_file)
            self.current_take_index[uid] = len(self.dub_takes[uid]) - 1 
            self.update_table_row(self.editing_sub_index)
            self.populate_take_combobox()
            self.audio_output.setMuted(False)
            self.lbl_record_status.setText(f"Status: Take {len(self.dub_takes[uid])} saved.")
            self.play_selected_take()

    def populate_take_combobox(self):
        self.combo_takes.clear()
        if self.editing_sub_index != -1:
            sub = self.subtitles[self.editing_sub_index]
            uid = sub.get('uid')
            takes = self.dub_takes.get(uid, [])
            for i, _ in enumerate(takes): self.combo_takes.addItem(f"Take {i+1}")
            if uid in self.current_take_index and self.current_take_index[uid] < len(takes):
                self.combo_takes.setCurrentIndex(self.current_take_index[uid])

    def select_take(self, index):
        if self.editing_sub_index != -1:
            sub = self.subtitles[self.editing_sub_index]
            uid = sub.get('uid')
            if uid in self.dub_takes and index < len(self.dub_takes[uid]):
                self.current_take_index[uid] = index

    def play_selected_take(self):
        if self.editing_sub_index == -1: return
        sub = self.subtitles[self.editing_sub_index]
        uid = sub.get('uid')
        idx = self.current_take_index.get(uid, 0)
        
        if uid in self.dub_takes and idx < len(self.dub_takes[uid]):
            take_file = self.dub_takes[uid][idx]
            self.dub_player.setSource(QUrl.fromLocalFile(take_file))
            self.dub_audio_output.setMuted(False)
            self.media_player.setPosition(sub['start'])
            self.play_video()

    # --- Time Helpers ---
    def qtime_to_ms(self, qtime): return qtime.hour() * 3600000 + qtime.minute() * 60000 + qtime.second() * 1000 + qtime.msec()
    def ms_to_qtime(self, ms): return QTime(0, 0, 0).addMSecs(ms)
    def ms_to_srt_time(self, ms):
        ms = max(0, ms); h = ms // 3600000; ms %= 3600000; m = ms // 60000; ms %= 60000; s = ms // 1000; ms %= 1000
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
    def ms_to_vtt_time(self, ms):
        ms = max(0, ms); h = ms // 3600000; ms %= 3600000; m = ms // 60000; ms %= 60000; s = ms // 1000; ms %= 1000
        return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"
    def srt_time_to_ms(self, time_str):
        h, m, s_ms = time_str.split(':'); s, ms = s_ms.split(',')
        return int(h)*3600000 + int(m)*60000 + int(s)*1000 + int(ms)

    def grab_time(self, target):
        qtime = self.ms_to_qtime(self.media_player.position())
        if target == "start": self.time_start.setTime(qtime)
        else: self.time_end.setTime(qtime)

    def auto_set_end_time(self):
        text = self.input_text.toPlainText().strip()
        if not text: return
        start_ms = self.qtime_to_ms(self.time_start.time())
        char_count = len(text.replace(" ", "").replace("\n", ""))
        duration_ms = (char_count / 15.0) * 1000 + 1000
        self.time_end.setTime(self.ms_to_qtime(start_ms + int(duration_ms)))

    def nudge_time(self, target, ms_delta):
        if self.editing_sub_index == -1: return
        sub = self.subtitles[self.editing_sub_index]
        if target == "start":
            new_start = max(0, sub['start'] + ms_delta)
            if new_start < sub['end']: sub['start'] = new_start; self.time_start.setTime(self.ms_to_qtime(new_start))
        else:
            new_end = max(sub['start'] + 1, sub['end'] + ms_delta)
            sub['end'] = new_end; self.time_end.setTime(self.ms_to_qtime(new_end))
        self.update_table_row(self.editing_sub_index)

    # --- Subtitle Entry Logic ---
    def update_cpl_label(self):
        text = self.input_text.toPlainText(); lines = text.split('\n')
        max_cpl = max([len(line) for line in lines]) if lines and text else 0
        self.lbl_cpl.setText(f"Max CPL: {max_cpl}")
        self.lbl_cpl.setStyleSheet("font-weight: bold; color: red;" if max_cpl > 42 else "font-weight: bold; color: green;")

    def rapid_subtitle_entry(self):
        if not self.rapid_entry_active:
            self.grab_time("start"); self.rapid_entry_active = True
            self.input_text.setStyleSheet("border: 2px solid #27ae60;")
        else:
            self.grab_time("end"); self.add_subtitle()
            self.time_start.setTime(self.time_end.time()); self.rapid_entry_active = True 
            
    def cancel_rapid_entry(self):
        if self.rapid_entry_active:
            self.rapid_entry_active = False; self.input_text.setStyleSheet(""); self.input_text.clear()

    def add_subtitle(self):
        start_ms = self.qtime_to_ms(self.time_start.time()); end_ms = self.qtime_to_ms(self.time_end.time())
        text = self.input_text.toPlainText().strip(); pos = self.combo_position.currentText()
        if start_ms >= end_ms or not text: return
        uid = uuid.uuid4().hex
        self.subtitles.append({'start': start_ms, 'end': end_ms, 'text': text, 'position': pos, 'uid': uid, 'status': 'Todo'})
        self.sort_and_refresh_subs(); self.input_text.clear()
        self.input_text.setStyleSheet(""); self.rapid_entry_active = False

    def update_subtitle(self):
        if self.editing_sub_index == -1: return
        start_ms = self.qtime_to_ms(self.time_start.time()); end_ms = self.qtime_to_ms(self.time_end.time())
        text = self.input_text.toPlainText().strip(); pos = self.combo_position.currentText()
        if start_ms >= end_ms or not text: return
        uid = self.subtitles[self.editing_sub_index].get('uid', uuid.uuid4().hex)
        status = self.subtitles[self.editing_sub_index].get('status', 'Todo')
        self.subtitles[self.editing_sub_index] = {'start': start_ms, 'end': end_ms, 'text': text, 'position': pos, 'uid': uid, 'status': status}
        self.sort_and_refresh_subs(); self.input_text.clear()
        self.input_text.setStyleSheet(""); self.sub_table.clearSelection()
        self.editing_sub_index = -1; self.rapid_entry_active = False

    def delete_subtitle(self):
        if self.editing_sub_index == -1: return
        del self.subtitles[self.editing_sub_index]; self.editing_sub_index = -1
        self.input_text.clear(); self.sub_table.clearSelection(); self.sort_and_refresh_subs()

    def merge_with_next(self):
        if self.editing_sub_index == -1 or self.editing_sub_index >= len(self.subtitles) - 1: return
        curr = self.subtitles[self.editing_sub_index]; next_sub = self.subtitles[self.editing_sub_index + 1]
        curr['text'] += "\n" + next_sub['text']; curr['end'] = next_sub['end']
        del self.subtitles[self.editing_sub_index + 1]
        self.sort_and_refresh_subs(); self.sub_table.selectRow(self.editing_sub_index)

    def split_at_cursor(self):
        if self.editing_sub_index == -1: return
        curr = self.subtitles[self.editing_sub_index]; cursor_pos = self.media_player.position()
        if not (curr['start'] < cursor_pos < curr['end']): return
        new_sub = {'start': cursor_pos, 'end': curr['end'], 'text': curr['text'], 'position': curr.get('position', 'Bottom'), 'uid': uuid.uuid4().hex, 'status': 'Todo'}
        curr['end'] = cursor_pos
        self.subtitles.insert(self.editing_sub_index + 1, new_sub)
        self.sort_and_refresh_subs(); self.sub_table.selectRow(self.editing_sub_index + 1)

    def open_find_replace(self):
        dialog = FindReplaceDialog(self)
        if dialog.exec() == QDialog.Accepted:
            find_text = dialog.txt_find.toPlainText(); replace_text = dialog.txt_replace.toPlainText()
            if not find_text: return
            count = 0
            for sub in self.subtitles:
                if find_text in sub['text']: sub['text'] = sub['text'].replace(find_text, replace_text); count += 1
            if count > 0: self.sort_and_refresh_subs(); QMessageBox.information(self, "Success", f"Replaced text in {count} subtitles.")

    def navigate_table(self, direction):
        if self.sub_table.rowCount() == 0: return
        new_row = max(0, min(self.editing_sub_index + direction, self.sub_table.rowCount() - 1))
        self.sub_table.selectRow(new_row)

    def set_line_status(self, status):
        if self.editing_sub_index == -1: return
        self.subtitles[self.editing_sub_index]['status'] = status
        self.update_table_row(self.editing_sub_index)

    def on_sub_select(self):
        selected = self.sub_table.selectionModel().selectedRows()
        if not selected: return
        row = selected[0].row(); self.editing_sub_index = row; sub = self.subtitles[row]
        self.time_start.setTime(self.ms_to_qtime(sub['start'])); self.time_end.setTime(self.ms_to_qtime(sub['end']))
        self.input_text.setPlainText(sub['text']); self.combo_position.setCurrentText(sub.get('position', 'Bottom'))
        self.media_player.setPosition(sub['start'])
        self.populate_take_combobox()

    def shift_all_subs(self):
        if not self.subtitles: return
        ms, ok = QInputDialog.getInt(self, "Shift All Subtitles", "Enter ms to shift:", 0, -1000000, 1000000, 100)
        if ok:
            for sub in self.subtitles: sub['start'] = max(0, sub['start'] + ms); sub['end'] = max(0, sub['end'] + ms)
            self.sort_and_refresh_subs()

    def auto_fix_timings(self):
        if not self.subtitles: return
        self.subtitles.sort(key=lambda x: x['start']); fixed_count = 0
        for i, sub in enumerate(self.subtitles):
            was_fixed = False
            if sub['end'] - sub['start'] < 1000: sub['end'] = sub['start'] + 1000; was_fixed = True
            elif sub['end'] - sub['start'] > 7000: sub['end'] = sub['start'] + 7000; was_fixed = True
            if i > 0:
                prev = self.subtitles[i-1]
                if sub['start'] < prev['end'] + 100:
                    sub['start'] = prev['end'] + 100
                    if sub['start'] >= sub['end']: sub['end'] = sub['start'] + 1000
                    was_fixed = True
            if was_fixed: fixed_count += 1
        self.sort_and_refresh_subs()
        QMessageBox.information(self, "Auto-Fix Complete", f"Processed {len(self.subtitles)} subtitles.\nFixed timing issues on {fixed_count} lines.")

    def calculate_cps(self, start, end, text):
        duration_sec = max(0.1, (end - start) / 1000.0)
        return round(len(text.replace(" ", "").replace("\n", "")) / duration_sec, 1)

    def update_table_row(self, row):
        sub = self.subtitles[row]
        self.sub_table.setItem(row, 0, QTableWidgetItem(str(row+1)))
        self.sub_table.setItem(row, 1, QTableWidgetItem(self.ms_to_srt_time(sub['start'])))
        self.sub_table.setItem(row, 2, QTableWidgetItem(self.ms_to_srt_time(sub['end'])))
        cps = self.calculate_cps(sub['start'], sub['end'], sub['text']); duration = sub['end'] - sub['start']
        cps_item = QTableWidgetItem(str(cps))
        cps_item.setForeground(QBrush(QColor("red")) if cps > 25.0 or duration < 1000 or duration > 7000 else QBrush(QColor("green")))
        self.sub_table.setItem(row, 3, cps_item)
        self.sub_table.setItem(row, 4, QTableWidgetItem(sub.get('position', 'Bottom')[0]))
        
        uid = sub.get('uid')
        take_count = len(self.dub_takes.get(uid, []))
        take_item = QTableWidgetItem(f"{take_count}/3" if take_count > 0 else "")
        if take_count > 0: take_item.setForeground(QBrush(QColor("cyan")))
        self.sub_table.setItem(row, 5, take_item)
        
        status = sub.get('status', 'Todo')
        status_item = QTableWidgetItem(status)
        if status == "Done": status_item.setForeground(QBrush(QColor("green")))
        elif status == "Re-take": status_item.setForeground(QBrush(QColor("red")))
        else: status_item.setForeground(QBrush(QColor("white")))
        self.sub_table.setItem(row, 6, status_item)
        
        self.sub_table.setItem(row, 7, QTableWidgetItem(sub['text'].replace('\n', ' ')))

    def sort_and_refresh_subs(self):
        self.subtitles.sort(key=lambda x: x['start']); self.sub_table.setRowCount(0)
        for i, sub in enumerate(self.subtitles): self.sub_table.insertRow(i); self.update_table_row(i)

    def update_subtitles(self, position):
        for sub in self.subtitles:
            if sub['start'] <= position <= sub['end']:
                self.subtitle_label.setText(sub['text']); pos = sub.get('position', 'Bottom')
                w = self.video_widget.width(); h = self.video_widget.height()
                if pos == "Top": self.subtitle_label.setGeometry(0, 50, w, 80)
                elif pos == "Middle": self.subtitle_label.setGeometry(0, int(h/2) - 40, w, 80)
                else: self.subtitle_label.setGeometry(0, h - 100, w, 80)
                self.subtitle_label.show(); return
        self.subtitle_label.hide()

    # --- SRT/VTT/CSV & Project Files ---
    def export_srt(self):
        if not self.subtitles: return
        file_name, _ = QFileDialog.getSaveFileName(self, "Save SRT", "", "*.srt")
        if file_name:
            with open(file_name, 'w', encoding='utf-8') as f:
                for i, sub in enumerate(self.subtitles, 1):
                    f.write(f"{i}\n{self.ms_to_srt_time(sub['start'])} --> {self.ms_to_srt_time(sub['end'])}\n")
                    pos = sub.get('position', 'Bottom'); prefix = ""
                    if pos == "Top": prefix = "{\\an8}"
                    elif pos == "Middle": prefix = "{\\an5}"
                    f.write(f"{prefix}{sub['text']}\n\n")

    def export_vtt(self):
        if not self.subtitles: return
        file_name, _ = QFileDialog.getSaveFileName(self, "Save VTT", "", "*.vtt")
        if file_name:
            with open(file_name, 'w', encoding='utf-8') as f:
                f.write("WEBVTT\n\n")
                for i, sub in enumerate(self.subtitles, 1):
                    start = self.ms_to_vtt_time(sub['start']); end = self.ms_to_vtt_time(sub['end'])
                    pos = sub.get('position', 'Bottom'); align = "line:90%"
                    if pos == "Top": align = "line:10%"
                    elif pos == "Middle": align = "line:50%"
                    f.write(f"{i}\n{start} --> {end} {align}\n{sub['text']}\n\n")

    def export_csv(self):
        if not self.subtitles: return
        file_name, _ = QFileDialog.getSaveFileName(self, "Export CSV Timing Sheet", "", "*.csv")
        if file_name:
            with open(file_name, 'w', encoding='utf-8', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(["Index", "Start", "End", "Duration (ms)", "CPS", "Takes", "Status", "Text"])
                for i, sub in enumerate(self.subtitles, 1):
                    dur = sub['end'] - sub['start']
                    cps = self.calculate_cps(sub['start'], sub['end'], sub['text'])
                    takes = len(self.dub_takes.get(sub.get('uid'), []))
                    writer.writerow([i, self.ms_to_srt_time(sub['start']), self.ms_to_srt_time(sub['end']), dur, cps, f"{takes}/3", sub.get('status', 'Todo'), sub['text']])

    def import_srt(self):
        file_name, _ = QFileDialog.getOpenFileName(self, "Open SRT", "", "*.srt")
        if file_name:
            self.subtitles.clear()
            with open(file_name, 'r', encoding='utf-8') as f:
                lines = f.readlines(); i = 0
                while i < len(lines):
                    if lines[i].strip().isdigit():
                        i += 1; times = lines[i].strip().split(" --> ")
                        start_ms = self.srt_time_to_ms(times[0]); end_ms = self.srt_time_to_ms(times[1])
                        i += 1; text = ""; pos = "Bottom"
                        while i < len(lines) and lines[i].strip():
                            line_text = lines[i].strip()
                            if line_text.startswith("{\\an8}"): pos = "Top"; line_text = line_text.replace("{\\an8}", "")
                            elif line_text.startswith("{\\an5}"): pos = "Middle"; line_text = line_text.replace("{\\an5}", "")
                            text += line_text + "\n"; i += 1
                        self.subtitles.append({'start': start_ms, 'end': end_ms, 'text': text.strip(), 'position': pos, 'uid': uuid.uuid4().hex, 'status': 'Todo'})
                    i += 1
            self.sort_and_refresh_subs()

    def save_project(self):
        file_name, _ = QFileDialog.getSaveFileName(self, "Save Project", "", "*.dubproj")
        if file_name:
            with open(file_name, 'w', encoding='utf-8') as f:
                json.dump({
                    'video_path': self.video_url.toLocalFile() if self.video_url else "",
                    'dub_path': self.dubbing_audio_url if self.dubbing_audio_url else "",
                    'subtitles': self.subtitles,
                    'dub_takes': self.dub_takes
                }, f, indent=4)

    def load_project(self):
        file_name, _ = QFileDialog.getOpenFileName(self, "Open Project", "", "*.dubproj")
        if file_name:
            with open(file_name, 'r', encoding='utf-8') as f: data = json.load(f)
            if data['video_path']: self.video_url = QUrl.fromLocalFile(data['video_path']); self.media_player.setSource(self.video_url)
            if data['dub_path']: self.dubbing_audio_url = data['dub_path']; self.lbl_record_status.setText(f"Status: Assembled Track Ready | {os.path.basename(self.dubbing_audio_url)}")
            self.subtitles = data['subtitles']; self.sort_and_refresh_subs()
            if 'dub_takes' in data: self.dub_takes = data['dub_takes']; self.sort_and_refresh_subs()

    # --- Dubbing & FFmpeg Functions ---
    def import_dub_audio(self):
        file_name, _ = QFileDialog.getOpenFileName(self, "Import Dub Audio", "", "Audio Files (*.mp3 *.wav *.aac)")
        if file_name:
            self.dubbing_audio_url = file_name
            self.lbl_record_status.setText(f"Status: Dub Track Ready | {os.path.basename(file_name)}")

    def update_dub_offset(self, ms): self.dub_offset_ms = ms; self.lbl_dub_offset.setText(str(ms))

    def toggle_recording(self):
        if not self.video_url: return
        if not self.is_recording:
            output_file, _ = QFileDialog.getSaveFileName(self, "Save Full Dubbing Audio", "", "Audio Files (*.mp3 *.wav)")
            if output_file:
                self.dubbing_audio_url = output_file
                self.recorder.setOutputLocation(QUrl.fromLocalFile(output_file)); self.recorder.record()
                self.is_recording = True; self.btn_record.setText("Stop Recording")
                self.lbl_record_status.setText("Status: RECORDING FULL DUB..."); self.audio_output.setMuted(True)
                self.media_player.setPosition(0); self.play_video()
        else:
            self.recorder.stop(); self.is_recording = False; self.btn_record.setText("Record Full Dub")
            self.lbl_record_status.setText(f"Status: Full Dub Ready | {os.path.basename(self.dubbing_audio_url)}")
            self.audio_output.setMuted(False); self.pause_video()

    def assemble_dub_track(self):
        if not self.dub_takes:
            QMessageBox.warning(self, "No Takes", "Please record some ADR lines first.")
            return
            
        output_file, _ = QFileDialog.getSaveFileName(self, "Save Assembled Dub Track", "", "Audio Files (*.wav *.mp3)")
        if not output_file: return
        
        clean_filter = ",highpass=f=80,afftdn=nr=15" if self.chk_clean.isChecked() else ""
        
        cmd = ["ffmpeg", "-y"]
        filter_parts = []; amix_inputs = []
        
        i = 0
        for sub in self.subtitles:
            uid = sub.get('uid')
            if uid in self.dub_takes and self.dub_takes[uid]:
                idx = self.current_take_index.get(uid, 0)
                if idx >= len(self.dub_takes[uid]): idx = 0
                file_path = self.dub_takes[uid][idx]
                
                delay = sub['start']
                cmd.extend(["-i", file_path])
                filter_parts.append(f"[{i}:a]adelay={delay}|{delay}{clean_filter}[a{i}]")
                amix_inputs.append(f"[a{i}]")
                i += 1
            
        if not amix_inputs: return
        
        filter_complex = ";".join(filter_parts) + f";{ ''.join(amix_inputs) }amix=inputs={len(amix_inputs)}:duration=longest:normalize=0[aout]"
        cmd.extend(["-filter_complex", filter_complex, "-map", "[aout]", "-c:a", "libmp3lame", output_file])
        
        self.run_ffmpeg(cmd, output_file)
        self.dubbing_audio_url = output_file
        self.lbl_record_status.setText(f"Status: Assembled Track Ready | {os.path.basename(output_file)}")

    def run_ffmpeg(self, cmd, output_file):
        self.statusBar().showMessage("Processing via FFmpeg... Please wait.")
        QApplication.processEvents()
        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0: QMessageBox.information(self, "Success", f"Successfully exported to:\n{output_file}")
            else: QMessageBox.critical(self, "FFmpeg Error", f"FFmpeg failed.\nError:\n{result.stderr[-800:]}")
        except FileNotFoundError: QMessageBox.critical(self, "FFmpeg Not Found", "FFmpeg is not installed or not in PATH.")

    def merge_audio_video(self):
        if not self.video_url: return
        if not self.dubbing_audio_url and self.dub_takes:
            QMessageBox.warning(self, "Assemble Required", "You have ADR takes but haven't assembled them. Click 'Assemble Dub Track' first.")
            return
        if not self.dubbing_audio_url: return
            
        output_file, _ = QFileDialog.getSaveFileName(self, "Save Merged Video", "", "*.mp4")
        if not output_file: return

        duck_vol = self.slider_duck_vol.value() / 100.0
        delay_filter = f"adelay={self.dub_offset_ms}|{self.dub_offset_ms}" if self.dub_offset_ms != 0 else "anull"
        clean_filter = ",highpass=f=80,afftdn=nr=15" if self.chk_clean.isChecked() else ""
        norm_filter = ",loudnorm=I=-16:TP=-1.5:LRA=11" if self.chk_normalize.isChecked() else ""
        
        if duck_vol > 0:
            cmd = ["ffmpeg", "-y", "-i", self.video_url.toLocalFile(), "-i", self.dubbing_audio_url,
                   "-filter_complex", f"[0:a]volume={duck_vol}[orig];[1:a]{delay_filter}{clean_filter}{norm_filter}[dub];[orig][dub]amix=inputs=2:duration=shortest[aout]",
                   "-map", "0:v", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac", "-shortest", output_file]
        else:
            cmd = ["ffmpeg", "-y", "-i", self.video_url.toLocalFile(), "-i", self.dubbing_audio_url,
                   "-filter_complex", f"[1:a]{delay_filter}{clean_filter}{norm_filter}[dub]",
                   "-c:v", "copy", "-c:a", "aac", "-map", "0:v:0", "-map", "[dub]", "-shortest", output_file]
        self.run_ffmpeg(cmd, output_file)

    def export_mixed_audio(self):
        if not self.video_url or not self.dubbing_audio_url: return
        output_file, _ = QFileDialog.getSaveFileName(self, "Save Mixed Audio", "", "*.mp3")
        if not output_file: return

        duck_vol = self.slider_duck_vol.value() / 100.0
        delay_filter = f"adelay={self.dub_offset_ms}|{self.dub_offset_ms}" if self.dub_offset_ms != 0 else "anull"
        clean_filter = ",highpass=f=80,afftdn=nr=15" if self.chk_clean.isChecked() else ""
        norm_filter = ",loudnorm=I=-16:TP=-1.5:LRA=11" if self.chk_normalize.isChecked() else ""
        
        if duck_vol > 0:
            cmd = ["ffmpeg", "-y", "-i", self.video_url.toLocalFile(), "-i", self.dubbing_audio_url,
                   "-filter_complex", f"[0:a]volume={duck_vol}[orig];[1:a]{delay_filter}{clean_filter}{norm_filter}[dub];[orig][dub]amix=inputs=2:duration=shortest[aout]",
                   "-map", "[aout]", "-c:a", "libmp3lame", output_file]
        else:
            cmd = ["ffmpeg", "-y", "-i", self.dubbing_audio_url, "-filter_complex", f"[0:a]{delay_filter}{clean_filter}{norm_filter}", "-c:a", "libmp3lame", output_file]
            
        self.run_ffmpeg(cmd, output_file)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = DubSubApp()
    window.show()
    sys.exit(app.exec())