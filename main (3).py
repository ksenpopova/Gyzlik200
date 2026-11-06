import sys
import os
import logging
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from threading import Event
import tempfile

from player_eq import render_eq
from player_storage import normalize_profile, read_profile, write_profile

log = logging.getLogger(__name__)


def resource_path(relative_path):
    return str(Path(__file__).resolve().parent / relative_path)


try:
    from transliterate.contrib.languages.ru.translit_language_pack import RussianLanguagePack
except ImportError:
    RussianLanguagePack = None

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QSlider, QLabel, QFileDialog, QSizeGrip, QFrame,
    QSizePolicy, QStackedWidget, QListWidget, QLineEdit, QMessageBox,
    QInputDialog, QListWidgetItem, QMenu
)
from PyQt6.QtCore import (
    Qt, QUrl, QPoint, QPropertyAnimation, QEasingCurve, QRect,
    pyqtProperty, pyqtSignal, QTimer, QSize, QEvent, QLockFile
)
from PyQt6.QtGui import (
    QFont, QPainter, QBrush, QColor, QLinearGradient,
    QPixmap, QIcon, QPaintEvent, QPainterPath, QShortcut, QKeySequence, QMouseEvent
)
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from mutagen import File

JELLYFISH_IMAGE_PATH = resource_path("image/pngwing.com.png")
PLACEHOLDER_IMAGE_PATH = resource_path("image/Медуза заглушка.jpg")
WAVE_IMAGE_1_PATH = resource_path("image/рамка раз.png")
WAVE_IMAGE_2_PATH = resource_path("image/рамка два.png")

ICONS_DIR = resource_path("icons")
PLAY_ICON_PATH = os.path.join(ICONS_DIR, "play.svg")
PAUSE_ICON_PATH = os.path.join(ICONS_DIR, "pause.svg")
NEXT_ICON_PATH = os.path.join(ICONS_DIR, "next_track.svg")
PREVIOUS_ICON_PATH = os.path.join(ICONS_DIR, "prev_track.svg")

COLORS = {
    "background_start": "#1a1f36",
    "background_end": "#3c3a5e",
    "primary_text": "#FFFFFF",
    "secondary_text": "#b0aed4",
    "accent": "#e0dffe",
    "slider_groove": "#4a486b",
    "icon_color": "#FFFFFF",
    "menu_background": "#1c2038",
    "menu_text": "#FFFFFF",
    "menu_hover": "#3c8dd1",
    "placeholder_bg": "#2a284a",
    "placeholder_border": "#4a486b",
    "volume_slider_bg": "rgba(0, 0, 0, 0.3)",
    "list_item_bg": "transparent",
    "list_item_hover_bg": "rgba(255, 255, 255, 0.1)",
    "list_item_selected_bg": "rgba(255, 255, 255, 0.2)",
    "button_bg": "rgba(255, 255, 255, 0.1)",
    "button_hover_bg": "rgba(255, 255, 255, 0.2)",
    "settings_label_text": "#FFFFFF",
    "settings_group_bg": "rgba(30, 35, 54, 0.7)",
}


def format_time(ms):
    if ms is None or ms < 0:
        ms = 0
    s = ms // 1000
    m, s = divmod(s, 60)
    return f"{int(m):01d}:{int(s):02d}"


@lru_cache(maxsize=2048)
def to_latin(text_input):
    if not text_input: return ""
    try:
        return RussianLanguagePack().translit(str(text_input), reversed=True) if RussianLanguagePack else str(text_input)
    except Exception as e:
        print(f"Ошибка транслитерации '{text_input}': {e}")
        return str(text_input)


def contains_cyrillic(text_input):
    if not text_input: return False
    text_str = str(text_input)
    return any('\u0400' <= char <= '\u04FF' for char in text_str)


class GradientWidget(QWidget):
    def __init__(self, start_color, end_color, image_path=None, parent=None):
        super().__init__(parent)
        self.start_color = QColor(start_color)
        self.end_color = QColor(end_color)
        self.bg_pixmap = None
        if image_path and os.path.exists(image_path):
            self.bg_pixmap = QPixmap(image_path)
            if self.bg_pixmap.isNull():
                print(f"Предупреждение: не найден задник: {image_path}")
                self.bg_pixmap = None
        elif image_path:
            print(f"Предупреждение: не найден задник: {image_path}")

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    def paintEvent(self, event: QPaintEvent):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0.0, self.start_color)
        gradient.setColorAt(1.0, self.end_color)
        painter.fillRect(self.rect(), QBrush(gradient))
        if self.bg_pixmap:
            scaled_pixmap = self.bg_pixmap.scaled(self.size(),
                                                  Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                                  Qt.TransformationMode.SmoothTransformation)
            x_offset = (self.width() - scaled_pixmap.width()) / 2
            y_offset = (self.height() - scaled_pixmap.height()) / 2
            painter.drawPixmap(int(x_offset), int(y_offset), scaled_pixmap)


class AnimatedAlbumArtWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.album_art_pixmap: QPixmap | None = None
        self.wave_pixmap1: QPixmap | None = None
        self.wave_pixmap2: QPixmap | None = None
        self.current_wave_is_one = True
        self.animation_timer = QTimer(self)
        self.animation_timer.timeout.connect(self._tick_animation)
        self.animation_interval = 200
        self.is_animating = False
        self.placeholder_text = "Обложка"
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    def load_wave_images(self, path1: str, path2: str):
        self.wave_pixmap1 = QPixmap(path1)
        self.wave_pixmap2 = QPixmap(path2)
        if self.wave_pixmap1.isNull():
            print(f"Предупреждение: Изображение волны 1 не найдено или неверно: {path1}")
            self.wave_pixmap1 = None
        if self.wave_pixmap2.isNull():
            print(f"Предупреждение: Изображение волны 2 не найдено или неверно: {path2}")
            self.wave_pixmap2 = None
        self.update()

    def set_album_art(self, pixmap: QPixmap | None):
        if pixmap and pixmap.isNull():
            self.album_art_pixmap = None
        else:
            self.album_art_pixmap = pixmap
        self.update()

    def start_animation(self):
        if self.wave_pixmap1 and self.wave_pixmap2:
            if not self.is_animating:
                self.is_animating = True
                self.animation_timer.start(self.animation_interval)
        else:
            self.stop_animation()

    def stop_animation(self):
        if self.is_animating:
            self.is_animating = False
            self.animation_timer.stop()
            self.update()

    def _tick_animation(self):
        self.current_wave_is_one = not self.current_wave_is_one
        self.update()

    def setPlaceholderText(self, text: str):
        self.placeholder_text = text
        self.update()

    def paintEvent(self, event: QPaintEvent):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        widget_rect = self.rect()
        art_margin_h_ratio = 0.10
        art_margin_v_ratio = 0.10
        art_width = widget_rect.width() * (1 - 2 * art_margin_h_ratio)
        art_height = widget_rect.height() * (1 - 2 * art_margin_v_ratio)
        art_x_offset = (widget_rect.width() - art_width) / 2
        art_y_offset = (widget_rect.height() - art_height) / 2
        art_target_rect = QRect(int(art_x_offset), int(art_y_offset), int(art_width), int(art_height))

        if self.album_art_pixmap:
            scaled_art = self.album_art_pixmap.scaled(art_target_rect.size(),
                                                      Qt.AspectRatioMode.KeepAspectRatio,
                                                      Qt.TransformationMode.SmoothTransformation)
            final_art_x = art_target_rect.x() + (art_target_rect.width() - scaled_art.width()) / 2
            final_art_y = art_target_rect.y() + (art_target_rect.height() - scaled_art.height()) / 2
            painter.drawPixmap(int(final_art_x), int(final_art_y), scaled_art)
        else:
            painter.setPen(QColor(COLORS['secondary_text']))
            font = QFont("Arial", 11)
            painter.setFont(font)
            painter.drawText(art_target_rect, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                             self.placeholder_text)

        if self.is_animating and self.wave_pixmap1 and self.wave_pixmap2:
            current_wave_pixmap = self.wave_pixmap1 if self.current_wave_is_one else self.wave_pixmap2
            scaled_wave = current_wave_pixmap.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                                     Qt.TransformationMode.SmoothTransformation)
            wave_x = (self.width() - scaled_wave.width()) / 2
            wave_y = (self.height() - scaled_wave.height()) / 2
            painter.drawPixmap(int(wave_x), int(wave_y), scaled_wave)


class SideMenu(QWidget):
    item_clicked = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(250)
        self.setStyleSheet(f"""
            QWidget {{
                border-top-right-radius: 15px;
                border-bottom-right-radius: 15px;
            }}
             QPushButton {{
                background: transparent;
                color: {COLORS['menu_text']};
                font-size: 18px;
                padding: 12px 15px;
                border: none;
                text-align: left;
                border-radius: 8px;
            }}
            QPushButton:hover {{ background: {COLORS['menu_hover']}; }}
        """)
        self.setVisible(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)

        self.close_btn = QPushButton("✕")
        self.close_btn.setFixedSize(30, 30)
        self.close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {COLORS['menu_text']};
                font-size: 18px;
                font-weight: bold;
                border: none;
                border-radius: 15px;
            }}
            QPushButton:hover {{ background: {COLORS['menu_hover']}; }}
        """)
        self.close_btn.clicked.connect(self.hide_menu)

        close_layout = QHBoxLayout()
        close_layout.addWidget(self.close_btn)
        close_layout.addStretch()
        layout.addLayout(close_layout)

        layout.addSpacing(20)

        self.search_btn = self.create_menu_button("Поиск")
        self.playlists_btn = self.create_menu_button("Плейлисты")
        self.settings_btn = self.create_menu_button("Настройки")
        self.add_files_btn = self.create_menu_button("Добавить файлы")

        self.search_btn.clicked.connect(lambda: self.item_clicked.emit("search"))
        self.playlists_btn.clicked.connect(lambda: self.item_clicked.emit("playlists"))
        self.settings_btn.clicked.connect(lambda: self.item_clicked.emit("settings"))

        layout.addWidget(self.search_btn)
        layout.addWidget(self.playlists_btn)
        layout.addWidget(self.settings_btn)
        layout.addWidget(self.add_files_btn)

        layout.addStretch()

        self.animation = QPropertyAnimation(self, b"geometry", self)
        self.animation.setDuration(300)
        self.animation.setEasingCurve(QEasingCurve.Type.InOutCubic)

    def create_menu_button(self, text):
        btn = QPushButton(text)
        return btn

    def paintEvent(self, event: QPaintEvent):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QBrush(QColor(COLORS['menu_background'])))
        super().paintEvent(event)

    def show_menu(self):
        self.animation.stop()
        try:
            self.animation.finished.disconnect(self._on_hide_anim_finished)
        except TypeError:
            pass
        start_geometry = QRect(-self.width(), 0, self.width(), self.parent().height())
        end_geometry = QRect(0, 0, self.width(), self.parent().height())
        self.setGeometry(start_geometry)
        self.setVisible(True)
        self.raise_()
        self.animation.setStartValue(start_geometry)
        self.animation.setEndValue(end_geometry)
        self.animation.start()

    def hide_menu(self):
        if not self.isVisible(): return
        start_geometry = self.geometry()
        end_geometry = QRect(-self.width(), 0, self.width(), self.parent().height())
        try:
            self.animation.finished.disconnect(self._on_hide_anim_finished)
        except TypeError:
            pass
        self.animation.setStartValue(start_geometry)
        self.animation.setEndValue(end_geometry)
        self.animation.finished.connect(self._on_hide_anim_finished)
        self.animation.start()

    def _on_hide_anim_finished(self):
        self.setVisible(False)
        try:
            self.animation.finished.disconnect(self._on_hide_anim_finished)
        except TypeError:
            pass


class PlaylistViewWidget(QWidget):
    def __init__(self, mp3_player_instance, parent=None):
        super().__init__(parent)
        self.mp3_player = mp3_player_instance
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)
        header_layout = QHBoxLayout()
        self.back_button = QPushButton("⬅ Назад к плееру")
        self.back_button.setStyleSheet(self.get_button_style())
        self.back_button.clicked.connect(self.mp3_player.show_player_view)
        header_layout.addWidget(self.back_button)
        header_layout.addStretch()
        layout.addLayout(header_layout)
        playlist_management_layout = QHBoxLayout()
        playlist_list_layout = QVBoxLayout()
        playlist_label = QLabel("Плейлисты:")
        playlist_label.setStyleSheet(f"color: {COLORS['primary_text']}; font-size: 16px; font-weight: bold;")
        playlist_list_layout.addWidget(playlist_label)
        self.playlists_listwidget = QListWidget()
        self.playlists_listwidget.setStyleSheet(self.get_list_widget_style())
        self.playlists_listwidget.itemClicked.connect(self.on_playlist_selected)
        self.playlists_listwidget.itemDoubleClicked.connect(self.load_selected_playlist_and_play)
        playlist_list_layout.addWidget(self.playlists_listwidget)
        playlist_management_layout.addLayout(playlist_list_layout, 2)
        playlist_actions_layout = QVBoxLayout()
        playlist_actions_layout.setSpacing(10)
        self.new_playlist_button = QPushButton("➕ Создать")
        self.rename_playlist_button = QPushButton("✏️ Переименовать")
        self.delete_playlist_button = QPushButton("🗑️ Удалить")
        self.load_playlist_button = QPushButton("🎵 Загрузить и играть")
        buttons_to_style = [self.new_playlist_button, self.rename_playlist_button, self.delete_playlist_button,
                            self.load_playlist_button]
        for btn in buttons_to_style:
            btn.setStyleSheet(self.get_button_style(True))
            playlist_actions_layout.addWidget(btn)
        self.new_playlist_button.clicked.connect(self.create_new_playlist)
        self.rename_playlist_button.clicked.connect(self.rename_selected_playlist)
        self.delete_playlist_button.clicked.connect(self.delete_selected_playlist)
        self.load_playlist_button.clicked.connect(self.load_selected_playlist_and_play)
        playlist_actions_layout.addStretch()
        playlist_management_layout.addLayout(playlist_actions_layout, 1)
        layout.addLayout(playlist_management_layout, 1)
        tracks_label = QLabel("Треки в плейлисте:")
        tracks_label.setStyleSheet(f"color: {COLORS['primary_text']}; font-size: 16px; font-weight: bold;")
        layout.addWidget(tracks_label)
        self.tracks_in_playlist_listwidget = QListWidget()
        self.tracks_in_playlist_listwidget.itemDoubleClicked.connect(self.play_selected_track)
        self.tracks_in_playlist_listwidget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tracks_in_playlist_listwidget.customContextMenuRequested.connect(self.track_menu)
        self.tracks_in_playlist_listwidget.setStyleSheet(self.get_list_widget_style())
        layout.addWidget(self.tracks_in_playlist_listwidget, 1)
        self.refresh_playlists_list()
        self.update_playlist_action_buttons_state()

    def get_button_style(self, smaller=False):
        padding = "8px 12px" if smaller else "10px 15px"
        font_size = "13px" if smaller else "14px"
        return f"""
            QPushButton {{
                background-color: {COLORS['button_bg']}; color: {COLORS['primary_text']};
                border: 1px solid {COLORS['placeholder_border']}; border-radius: 8px;
                padding: {padding}; font-size: {font_size};
            }}
            QPushButton:hover {{ background-color: {COLORS['button_hover_bg']}; }}
            QPushButton:pressed {{ background-color: {COLORS['button_bg']}; }}
            QPushButton:disabled {{
                background-color: rgba(74, 72, 107, 0.5); color: rgba(176, 174, 212, 0.7);
                border-color: rgba(74, 72, 107, 0.7);
            }}"""

    def get_list_widget_style(self):
        return f"""
            QListWidget {{
                background-color: {COLORS['placeholder_bg']}; border: 1px solid {COLORS['placeholder_border']};
                color: {COLORS['secondary_text']}; border-radius: 8px; padding: 5px;
            }}
            QListWidget::item {{ padding: 8px; border-radius: 4px; }}
            QListWidget::item:hover {{ background-color: {COLORS['list_item_hover_bg']}; color: {COLORS['primary_text']}; }}
            QListWidget::item:selected {{ background-color: {COLORS['list_item_selected_bg']}; color: {COLORS['accent']}; }}"""

    def refresh_playlists_list(self):
        self.playlists_listwidget.clear()
        current_pl_name = self.mp3_player.current_playlist_name
        for name in self.mp3_player.all_playlists.keys():
            item = QListWidgetItem(name)
            self.playlists_listwidget.addItem(item)
            if name == current_pl_name: item.setSelected(True)
        self.on_playlist_selected()

    def on_playlist_selected(self):
        selected_items = self.playlists_listwidget.selectedItems()
        self.tracks_in_playlist_listwidget.clear()
        if selected_items:
            selected_playlist_name = selected_items[0].text()
            tracks = self.mp3_player.all_playlists.get(selected_playlist_name, [])
            for i, track_info in enumerate(tracks):
                display_text = f"{i + 1}. {track_info.get('title', 'N/A')} - {track_info.get('artist', 'N/A')}"
                self.tracks_in_playlist_listwidget.addItem(QListWidgetItem(display_text))
        self.update_playlist_action_buttons_state()

    def update_playlist_action_buttons_state(self):
        selected_items = self.playlists_listwidget.selectedItems()
        has_selection = bool(selected_items)
        is_current_playlist_selected = False
        if has_selection:
            selected_playlist_name = selected_items[0].text()
            is_current_playlist_selected = (selected_playlist_name == self.mp3_player.current_playlist_name)
        self.rename_playlist_button.setEnabled(has_selection)
        self.delete_playlist_button.setEnabled(has_selection and len(self.mp3_player.all_playlists) > 1)
        self.load_playlist_button.setEnabled(has_selection and not is_current_playlist_selected)

    def create_new_playlist(self):
        name, ok = QInputDialog.getText(self, "Новый плейлист", "Название плейлиста:")
        name = name.strip()
        if ok and name:
            if name in self.mp3_player.all_playlists:
                QMessageBox.warning(self, "Ошибка", "Плейлист с таким именем уже существует.")
                return
            self.mp3_player.all_playlists[name] = []
            self.mp3_player.save_app_data()
            self.refresh_playlists_list()
            for i in range(self.playlists_listwidget.count()):
                if self.playlists_listwidget.item(i).text() == name:
                    self.playlists_listwidget.setCurrentRow(i);
                    break
            self.on_playlist_selected()

    def rename_selected_playlist(self):
        selected_items = self.playlists_listwidget.selectedItems()
        if not selected_items: return
        old_name = selected_items[0].text()
        new_name, ok = QInputDialog.getText(self, "Переименовать плейлист", "Новое название:", text=old_name)
        new_name = new_name.strip()
        if ok and new_name and new_name != old_name:
            if new_name in self.mp3_player.all_playlists:
                QMessageBox.warning(self, "Ошибка", "Плейлист с таким именем уже существует.")
                return
            tracks = self.mp3_player.all_playlists.pop(old_name)
            self.mp3_player.all_playlists[new_name] = tracks
            if self.mp3_player.current_playlist_name == old_name:
                self.mp3_player.current_playlist_name = new_name
                self.mp3_player.playlist_name_label.setText(f"Плейлист: {new_name}")
            self.mp3_player.save_app_data()
            self.refresh_playlists_list()

    def delete_selected_playlist(self):
        selected_items = self.playlists_listwidget.selectedItems()
        if not selected_items: return
        name_to_delete = selected_items[0].text()
        if len(self.mp3_player.all_playlists) <= 1:
            QMessageBox.warning(self, "Ошибка", "Нельзя удалить единственный плейлист.")
            return
        reply = QMessageBox.question(self, "Удалить плейлист",
                                     f"Вы уверены, что хотите удалить плейлист '{name_to_delete}'?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            del self.mp3_player.all_playlists[name_to_delete]
            if self.mp3_player.current_playlist_name == name_to_delete:
                self.mp3_player.current_playlist_name = next(iter(self.mp3_player.all_playlists))
                self.mp3_player.switch_to_playlist(self.mp3_player.current_playlist_name, autoplay=False)
            self.mp3_player.save_app_data()
            self.refresh_playlists_list()

    def play_selected_track(self, item):
        selection = self.playlists_listwidget.selectedItems()
        if selection:
            name = selection[0].text()
            row = self.tracks_in_playlist_listwidget.row(item)
            tracks = self.mp3_player.all_playlists[name]
            if 0 <= row < len(tracks):
                self.mp3_player.play_track_from_search(tracks[row], name)

    def track_menu(self, position):
        selection = self.playlists_listwidget.selectedItems()
        if not selection:
            return
        name = selection[0].text()
        tracks = self.mp3_player.all_playlists[name]
        view = self.tracks_in_playlist_listwidget
        item = view.itemAt(position)
        if item is None:
            return
        row = view.row(item)
        menu = QMenu(view)
        up = menu.addAction("Переместить выше")
        down = menu.addAction("Переместить ниже")
        remove = menu.addAction("Убрать из плейлиста")
        up.setEnabled(row > 0)
        down.setEnabled(row + 1 < len(tracks))
        action = menu.exec(view.viewport().mapToGlobal(position))
        if action not in (up, down, remove):
            return
        player = self.mp3_player
        active = name == player.current_playlist_name
        current = tracks[player.current_index] if active and 0 <= player.current_index < len(tracks) else None
        if action == remove:
            tracks.pop(row)
        else:
            other = row - 1 if action == up else row + 1
            tracks[row], tracks[other] = tracks[other], tracks[row]
        if current is not None:
            player.current_index = next((i for i, track in enumerate(tracks) if track is current), -1)
            if player.current_index == -1:
                player.display_no_track_info()
        player.save_app_data()
        self.on_playlist_selected()

    def load_selected_playlist_and_play(self):
        selected_items = self.playlists_listwidget.selectedItems()
        if not selected_items: return
        playlist_name = selected_items[0].text()
        self.mp3_player.switch_to_playlist(playlist_name, autoplay=True)
        self.mp3_player.show_player_view()
        self.refresh_playlists_list()


class SearchViewWidget(QWidget):
    track_selected_for_play = pyqtSignal(dict, str)

    def __init__(self, mp3_player_instance, parent=None):
        super().__init__(parent)
        self.mp3_player = mp3_player_instance
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        header_layout = QHBoxLayout()
        self.back_button = QPushButton("⬅ Назад к плееру")
        self.back_button.setStyleSheet(self.mp3_player.playlist_view_widget.get_button_style())
        self.back_button.clicked.connect(self.mp3_player.show_player_view)
        header_layout.addWidget(self.back_button)
        header_layout.addStretch()
        layout.addLayout(header_layout)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Поиск (название, исполнитель)...")
        self.search_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {COLORS['placeholder_bg']};
                border: 1px solid {COLORS['placeholder_border']};
                color: {COLORS['primary_text']};
                border-radius: 8px;
                padding: 10px;
                font-size: 14px;
            }}
        """)
        self.search_input.setMaxLength(256)
        self.search_timer = QTimer(self, interval=180, singleShot=True)
        self.search_timer.timeout.connect(self.perform_search)
        self.search_input.textChanged.connect(lambda _: self.search_timer.start())
        layout.addWidget(self.search_input)

        self.results_listwidget = QListWidget()
        self.results_listwidget.setStyleSheet(self.mp3_player.playlist_view_widget.get_list_widget_style())
        self.results_listwidget.itemDoubleClicked.connect(self.on_track_double_clicked)
        layout.addWidget(self.results_listwidget)

    def perform_search(self):
        query_original = self.search_input.text().strip()
        self.results_listwidget.clear()

        if not query_original:
            return

        query_lower = query_original.lower()

        query_terms_to_check = {query_lower}
        if contains_cyrillic(query_lower):
            latin_query = to_latin(query_lower).lower()
            if latin_query != query_lower:
                query_terms_to_check.add(latin_query)

        added_track_paths = set()

        for playlist_name, tracks in self.mp3_player.all_playlists.items():
            for track_info in tracks:
                original_title = str(track_info.get('title', ''))
                original_artist = str(track_info.get('artist', ''))
                track_path = track_info.get('path')

                if not track_path: continue

                fields_to_search_in = set()
                title_lower = original_title.lower()
                artist_lower = original_artist.lower()
                fields_to_search_in.add(title_lower)
                fields_to_search_in.add(artist_lower)

                if contains_cyrillic(title_lower):
                    latin_title = to_latin(title_lower).lower()
                    if latin_title != title_lower:
                        fields_to_search_in.add(latin_title)
                if contains_cyrillic(artist_lower):
                    latin_artist = to_latin(artist_lower).lower()
                    if latin_artist != artist_lower:
                        fields_to_search_in.add(latin_artist)

                match_found = False
                for query_term in query_terms_to_check:
                    if not query_term: continue
                    for field_content in fields_to_search_in:
                        if not field_content: continue
                        if query_term in field_content:
                            match_found = True
                            break
                    if match_found:
                        break

                if match_found and track_path not in added_track_paths:
                    display_text = f"{original_title} - {original_artist} ({playlist_name})"
                    item = QListWidgetItem(display_text)
                    item.setData(Qt.ItemDataRole.UserRole, {'track': track_info, 'playlist': playlist_name})
                    self.results_listwidget.addItem(item)
                    added_track_paths.add(track_path)
                    if len(added_track_paths) >= 500:
                        self.mp3_player.statusBar().showMessage("Показаны первые 500 результатов. Уточните запрос.", 5000)
                        return

    def on_track_double_clicked(self, item):
        data = item.data(Qt.ItemDataRole.UserRole)
        if data:
            track_info = data['track']
            playlist_name = data['playlist']
            self.track_selected_for_play.emit(track_info, playlist_name)


    def activate_search(self):
        self.search_input.setFocus()

class SettingsViewWidget(QWidget):
    settings_updated = pyqtSignal()
    EQ_BANDS = [(60, "60Hz"), (170, "170Hz"), (310, "310Hz"), (600, "600Hz"), (1000, "1kHz"), (3000, "3kHz"), (6000, "6kHz"), (12000, "12kHz")]
    EQ_GAIN_RANGE_DB = (-12, 12)

    def __init__(self, mp3_player_instance, parent=None):
        super().__init__(parent)
        self.mp3_player = mp3_player_instance
        self.eq_sliders = {}
        self.eq_labels = {}
        self.init_ui()
        self.load_settings()

    def init_ui(self):
        main_layout = QVBoxLayout(self);
        main_layout.setContentsMargins(20, 20, 20, 20);
        main_layout.setSpacing(20)
        header_layout = QHBoxLayout()
        self.back_button = QPushButton("⬅ Назад к плееру")
        self.back_button.setStyleSheet(self.mp3_player.playlist_view_widget.get_button_style())
        self.back_button.clicked.connect(self.mp3_player.show_player_view)
        header_layout.addWidget(self.back_button);
        header_layout.addStretch();
        main_layout.addLayout(header_layout)

        self.eq_group_box = QWidget();
        self.eq_group_box.setStyleSheet(
            f"QWidget{{background-color:{COLORS['settings_group_bg']};border-radius:10px;}}")
        eq_group_layout = QVBoxLayout(self.eq_group_box);
        eq_group_layout.setContentsMargins(15, 15, 15, 15);
        eq_group_layout.setSpacing(10)
        self.eq_title = QLabel("Эквалайзер");
        self.eq_title.setStyleSheet(f"color:{COLORS['settings_label_text']};font-size:18px;font-weight:bold;")
        eq_group_layout.addWidget(self.eq_title)

        eq_sliders_layout = QHBoxLayout();
        eq_sliders_layout.setSpacing(10)
        for freq, name in self.EQ_BANDS:
            band_layout = QVBoxLayout();
            band_layout.setSpacing(3)
            freq_label = QLabel(name);
            freq_label.setAlignment(Qt.AlignmentFlag.AlignCenter);
            freq_label.setStyleSheet(f"color:{COLORS['secondary_text']};font-size:10px;")
            band_layout.addWidget(freq_label)
            slider = QSlider(Qt.Orientation.Vertical)
            slider.setRange(self.EQ_GAIN_RANGE_DB[0] * 10, self.EQ_GAIN_RANGE_DB[1] * 10);
            slider.setValue(0)
            slider.setFixedHeight(120)
            vertical_slider_style = self.mp3_player.get_slider_stylesheet().replace(":horizontal", ":vertical")
            vertical_slider_style = vertical_slider_style.replace("height: 5px", "width: 5px")
            vertical_slider_style = vertical_slider_style.replace("width: 14px", "height: 14px")
            vertical_slider_style = vertical_slider_style.replace("margin: -5px 0", "margin: 0 -5px")
            slider.setStyleSheet(vertical_slider_style)

            slider.valueChanged.connect(lambda val, s=slider, f=freq: self.update_gain_label(s, f, val / 10.0))
            self.eq_sliders[freq] = slider;
            band_layout.addWidget(slider)
            gain_value_label = QLabel("0.0 dB");
            gain_value_label.setAlignment(Qt.AlignmentFlag.AlignCenter);
            gain_value_label.setStyleSheet(f"color:{COLORS['accent']};font-size:10px;")
            self.eq_labels[freq] = gain_value_label;
            band_layout.addWidget(gain_value_label)
            eq_sliders_layout.addLayout(band_layout)
        eq_group_layout.addLayout(eq_sliders_layout)

        eq_actions_layout = QHBoxLayout()
        self.reset_eq_button = QPushButton("Сбросить")
        self.reset_eq_button.setStyleSheet(
            self.mp3_player.playlist_view_widget.get_button_style(smaller=True))
        self.reset_eq_button.clicked.connect(self.reset_equalizer)

        self.apply_eq_button = QPushButton("Применить к треку")
        self.apply_eq_button.setStyleSheet(
            self.mp3_player.playlist_view_widget.get_button_style(smaller=True))
        self.apply_eq_button.clicked.connect(self.trigger_apply_equalizer)

        eq_actions_layout.addStretch();
        eq_actions_layout.addWidget(self.reset_eq_button);
        eq_actions_layout.addWidget(self.apply_eq_button);
        eq_actions_layout.addStretch()
        eq_group_layout.addLayout(eq_actions_layout)
        main_layout.addWidget(self.eq_group_box);
        main_layout.addStretch()

    def update_gain_label(self, slider, freq, value_db):
        if freq in self.eq_labels: self.eq_labels[freq].setText(f"{value_db:.1f} dB")

    def reset_equalizer(self):
        for slider in self.eq_sliders.values():
            slider.setValue(0)
        self.trigger_apply_equalizer()

    def trigger_apply_equalizer(self):
        self.mp3_player.process_and_apply_equalizer(self.get_eq_gains())

    def get_eq_gains(self):
        return {freq: slider.value() / 10.0 for freq, slider in self.eq_sliders.items()}

    def set_eq_gains(self, gains_dict):
        for freq, gain_db in gains_dict.items():
            if freq in self.eq_sliders:
                self.eq_sliders[freq].setValue(int(gain_db * 10))
                self.update_gain_label(self.eq_sliders[freq], freq, gain_db)

    def load_settings(self):
        if hasattr(self.mp3_player, 'app_settings') and 'equalizer_gains' in self.mp3_player.app_settings:
            self.set_eq_gains(self.mp3_player.app_settings['equalizer_gains'])
        else:
            default_gains = {freq: 0.0 for freq, _ in SettingsViewWidget.EQ_BANDS}
            self.set_eq_gains(default_gains)
            if hasattr(self.mp3_player, 'app_settings'):
                self.mp3_player.app_settings['equalizer_gains'] = default_gains



class MP3Player(QMainWindow):
    DEFAULT_PLAYLIST_NAME = "Моя музыка"

    def __init__(self):
        super().__init__()
        self.setWindowTitle("AudioFlow Player")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setGeometry(100, 100, 380, 650)
        self.setMinimumSize(360, 600)
        self.drag_pos = QPoint()

        self.main_widget = GradientWidget(COLORS['background_start'],
                                          COLORS['background_end'],
                                          JELLYFISH_IMAGE_PATH)
        self.main_widget.setStyleSheet("border-radius: 15px;")
        self.setCentralWidget(self.main_widget)

        self.main_layout = QVBoxLayout(self.main_widget);
        self.main_layout.setContentsMargins(0, 0, 0, 0);
        self.main_layout.setSpacing(0)

        self.init_title_bar()

        self.all_playlists = {};
        self.current_playlist_name = self.DEFAULT_PLAYLIST_NAME
        self.active_playlist_tracks = [];
        self.current_index = -1
        self.media_player = QMediaPlayer();
        self.audio_output = QAudioOutput()
        self.media_player.setAudioOutput(self.audio_output);
        self.initial_volume = 0.5;
        self.audio_output.setVolume(self.initial_volume)

        self.play_icon = QIcon(PLAY_ICON_PATH);
        self.pause_icon = QIcon(PAUSE_ICON_PATH);
        self.next_icon = QIcon(NEXT_ICON_PATH);
        self.previous_icon = QIcon(PREVIOUS_ICON_PATH)

        self.placeholder_album_art_pixmap = QPixmap(PLACEHOLDER_IMAGE_PATH)
        if self.placeholder_album_art_pixmap.isNull(): self.placeholder_album_art_pixmap = None

        self.app_settings = {}
        self.current_processed_track_path = None
        self.original_track_path_before_eq = None
        self._pending_position = None
        self._profile_readonly = False
        self._profile_lock = None
        self._eq_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="audio-eq")
        self._eq_dir = None
        self._eq_job = None
        self._eq_timer = QTimer(self, interval=75)
        self._eq_timer.timeout.connect(self._poll_eq)

        self.stacked_content_widget = QStackedWidget()
        self.player_view_widget = QWidget()
        self.playlist_view_widget = PlaylistViewWidget(self)
        self.search_view_widget = SearchViewWidget(self)
        self.settings_view_widget = SettingsViewWidget(self)

        self.init_player_view_ui(self.player_view_widget)

        self.stacked_content_widget.addWidget(self.player_view_widget)
        self.stacked_content_widget.addWidget(self.playlist_view_widget)
        self.stacked_content_widget.addWidget(self.search_view_widget)
        self.stacked_content_widget.addWidget(self.settings_view_widget)
        self.main_layout.addWidget(self.stacked_content_widget)

        self.setup_connections()
        self.search_view_widget.track_selected_for_play.connect(self.play_track_from_search)
        self.settings_view_widget.settings_updated.connect(
            self.handle_settings_update)

        self.init_shortcuts()
        self.side_menu = SideMenu(self.main_widget);
        self.menu_btn.clicked.connect(self.side_menu.show_menu)
        self.side_menu.add_files_btn.clicked.connect(self.add_files_to_current_playlist)
        self.side_menu.item_clicked.connect(self.handle_menu_click)

        self.size_grip = QSizeGrip(self.main_widget);
        self.size_grip.setStyleSheet("background-color:transparent;")

        self.load_app_data()
        self.settings_view_widget.load_settings()
        self.playlist_view_widget.refresh_playlists_list()
        for label in (self.title_label, self.artist_label, self.playlist_name_label):
            label.setTextFormat(Qt.TextFormat.PlainText)
        for button, name in ((self.prev_btn, "Предыдущий трек"), (self.next_btn, "Следующий трек"),
                             (self.play_btn, "Воспроизведение или пауза")):
            button.setAccessibleName(name)
            button.setToolTip(name)

        if self.active_playlist_tracks:
            self.current_index = 0
            self.load_track(autoplay=False)
        else:
            self.display_no_track_info()

        self.update_buttons(self.media_player.playbackState())
        self.show_player_view()

    def init_title_bar(self):
        self.title_bar_widget = QWidget()
        self.title_bar_widget.setFixedHeight(45)
        self.title_bar_widget.setStyleSheet("background-color: transparent;")
        title_bar_layout = QHBoxLayout(self.title_bar_widget)
        title_bar_layout.setContentsMargins(15, 5, 15, 0)
        self.menu_btn = self.create_styled_button("☰", 30)
        self.menu_btn.setToolTip("Меню")
        title_bar_layout.addWidget(self.menu_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        title_bar_layout.addStretch()
        self.close_btn = self.create_styled_button("×", 30)
        self.close_btn.clicked.connect(self.close)
        title_bar_layout.addWidget(self.close_btn, alignment=Qt.AlignmentFlag.AlignRight)
        self.main_layout.addWidget(self.title_bar_widget)

    def init_player_view_ui(self, parent_widget):
        content_layout = QVBoxLayout(parent_widget)
        content_layout.setContentsMargins(30, 0, 30, 25);
        content_layout.setSpacing(15)
        self.album_art_label = AnimatedAlbumArtWidget()
        self.album_art_label.load_wave_images(WAVE_IMAGE_1_PATH, WAVE_IMAGE_2_PATH)
        art_size = 250;
        self.album_art_label.setFixedSize(art_size, art_size)
        self.album_art_label.setStyleSheet(f"""
            AnimatedAlbumArtWidget {{ background-color: {COLORS['placeholder_bg']};
                border: 1px solid {COLORS['placeholder_border']}; border-radius: 15px; }}""")
        content_layout.addStretch(1)
        content_layout.addWidget(self.album_art_label, alignment=Qt.AlignmentFlag.AlignCenter)
        content_layout.addSpacing(20)
        self.artist_label = QLabel("Исполнитель")
        self.artist_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.artist_label.setFont(QFont("Arial", 14, QFont.Weight.Normal))
        self.artist_label.setStyleSheet(f"color: {COLORS['secondary_text']}; background-color: transparent;")
        self.title_label = QLabel("Название трека")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label.setFont(QFont("Arial", 18, QFont.Weight.Bold))
        self.title_label.setStyleSheet(f"color: {COLORS['primary_text']}; background-color: transparent;")
        self.playlist_name_label = QLabel(self.current_playlist_name)
        self.playlist_name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.playlist_name_label.setFont(QFont("Arial", 10, QFont.Weight.Normal))
        self.playlist_name_label.setStyleSheet(
            f"color: {COLORS['secondary_text']}; background-color: transparent; margin-top: 5px;")
        content_layout.addWidget(self.title_label);
        content_layout.addWidget(self.artist_label)
        content_layout.addWidget(self.playlist_name_label);
        content_layout.addSpacing(20)
        progress_layout = QHBoxLayout();
        progress_layout.setSpacing(10)
        self.current_time_label = QLabel("0:00")
        self.current_time_label.setStyleSheet(
            f"color: {COLORS['secondary_text']}; font-size: 12px; background-color: transparent;")
        self.progress_slider = QSlider(Qt.Orientation.Horizontal)
        self.progress_slider.setStyleSheet(self.get_slider_stylesheet())
        self.progress_slider.sliderMoved.connect(self.seek_position)
        self.progress_slider.sliderPressed.connect(self.slider_pressed)
        self.progress_slider.sliderReleased.connect(self.slider_released)
        self.is_slider_pressed = False
        self.total_time_label = QLabel("0:00")
        self.total_time_label.setStyleSheet(
            f"color: {COLORS['secondary_text']}; font-size: 12px; background-color: transparent;")
        progress_layout.addWidget(self.current_time_label);
        progress_layout.addWidget(self.progress_slider)
        progress_layout.addWidget(self.total_time_label);
        content_layout.addLayout(progress_layout)
        content_layout.addSpacing(15)
        control_layout = QHBoxLayout();
        control_layout.setSpacing(20)
        self.prev_btn = self.create_control_button(self.previous_icon, 50)
        self.play_btn = self.create_control_button(self.play_icon, 60, icon_size_ratio=0.55)
        self.next_btn = self.create_control_button(self.next_icon, 50)
        control_layout.addStretch();
        control_layout.addWidget(self.prev_btn)
        control_layout.addWidget(self.play_btn);
        control_layout.addWidget(self.next_btn);
        control_layout.addStretch()
        content_layout.addLayout(control_layout);
        content_layout.addStretch(2)
        volume_area_layout = QVBoxLayout();
        volume_area_layout.setSpacing(5)
        volume_button_layout = QHBoxLayout();
        volume_button_layout.addStretch()
        self.volume_btn = self.create_styled_button("🔊", 30, is_icon=True)
        self.volume_btn.setToolTip("Громкость (Up/Down)")
        volume_button_layout.addWidget(self.volume_btn);
        volume_area_layout.addLayout(volume_button_layout)
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100);
        self.volume_slider.setValue(int(self.initial_volume * 100))
        self.volume_slider.setStyleSheet(self.get_slider_stylesheet())
        self.volume_slider.setFixedHeight(20);
        self.volume_slider.setVisible(False)
        volume_area_layout.addWidget(self.volume_slider);
        content_layout.addLayout(volume_area_layout)

    def resizeEvent(self, event: QPaintEvent):
        QMainWindow.resizeEvent(self, event)
        grip_size = self.size_grip.sizeHint()
        self.size_grip.move(self.width() - grip_size.width(), self.height() - grip_size.height())
        self.size_grip.raise_()
        if self.side_menu.isVisible():
            self.side_menu.setFixedHeight(self.height())
            current_geo = self.side_menu.geometry()
            self.side_menu.setGeometry(current_geo.x(), current_geo.y(), current_geo.width(), self.height())

    def create_styled_button(self, text, size, is_icon=False):
        btn = QPushButton(text)
        btn.setFixedSize(size, size)
        font_size = int(size * 0.5)
        btn.setFont(QFont("Arial", font_size, QFont.Weight.Bold if not is_icon else QFont.Weight.Normal))
        btn.setStyleSheet(f"""
            QPushButton {{ background-color: {COLORS['button_bg']}; color: {COLORS['icon_color']};
                border: none; border-radius: {size // 2}px; padding: 0px; }}
            QPushButton:hover {{ background-color: {COLORS['button_hover_bg']}; }}
            QPushButton:pressed {{ background-color: {COLORS['button_bg']}; }}""")
        return btn

    def get_slider_stylesheet(self):
        return f"""
            QSlider::groove:horizontal {{ background: {COLORS['slider_groove']}; height: 5px; border-radius: 2px; }}
            QSlider::handle:horizontal {{ background: {COLORS['accent']}; width: 14px; margin: -5px 0; border-radius: 7px; }}
            QSlider::sub-page:horizontal {{
                 background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {COLORS['accent']}, stop:1 #a09de0);
                 height: 5px; border-radius: 2px; }}
            QSlider {{ background: transparent; }}"""

    def create_control_button(self, icon_or_path, size=50, icon_size_ratio=0.5):
        btn = QPushButton()
        btn.setFixedSize(size, size)
        current_icon = QIcon()
        if isinstance(icon_or_path, str):
            if os.path.exists(icon_or_path): current_icon = QIcon(icon_or_path)
        elif isinstance(icon_or_path, QIcon):
            current_icon = icon_or_path
        btn.setIcon(current_icon)
        btn.setIconSize(QSize(int(size * icon_size_ratio), int(size * icon_size_ratio)))
        btn.setStyleSheet(f"""
            QPushButton {{ background-color: transparent; border: 2px solid {COLORS['icon_color']};
                border-radius: {size // 2}px; padding: 0px; }}
            QPushButton:hover {{ background-color: rgba(255, 255, 255, 0.1); border-color: {COLORS['accent']}; }}
            QPushButton:pressed {{ background-color: rgba(255, 255, 255, 0.05); }}
            QPushButton:disabled {{ border-color: {COLORS['slider_groove']}; background-color: transparent; }}""")
        return btn

    def setup_connections(self):
        self.media_player.positionChanged.connect(self.update_position)
        self.media_player.durationChanged.connect(self.update_duration)
        self.media_player.playbackStateChanged.connect(self.update_buttons)
        self.media_player.errorOccurred.connect(self.handle_error)
        self.media_player.mediaStatusChanged.connect(self.handle_media_status)
        self.media_player.seekableChanged.connect(self._restore_position)
        self.prev_btn.clicked.connect(self.prev_track)
        self.play_btn.clicked.connect(self.toggle_play)
        self.next_btn.clicked.connect(self.next_track)
        self.volume_btn.clicked.connect(self.toggle_volume_slider)
        self.volume_slider.valueChanged.connect(self.set_volume)

    def init_shortcuts(self):
        def player_shortcut(key):
            shortcut = QShortcut(QKeySequence(key), self.player_view_widget)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            return shortcut
        self.player_view_widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        player_shortcut("Space").activated.connect(self.toggle_play)
        player_shortcut(Qt.Key.Key_Right).activated.connect(
            lambda: self.seek_position(self.media_player.position() + 5000))
        player_shortcut(Qt.Key.Key_Left).activated.connect(
            lambda: self.seek_position(self.media_player.position() - 5000))
        QShortcut(QKeySequence(Qt.Key.Key_MediaTogglePlayPause), self).activated.connect(self.toggle_play)
        QShortcut(QKeySequence(Qt.Key.Key_MediaNext), self).activated.connect(self.next_track)
        QShortcut(QKeySequence(Qt.Key.Key_MediaPrevious), self).activated.connect(self.prev_track)
        QShortcut(QKeySequence(Qt.Key.Key_MediaStop), self).activated.connect(self.stop_playback)
        QShortcut(QKeySequence("Ctrl+O"), self).activated.connect(self.add_files_to_current_playlist)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self).activated.connect(self.handle_escape_key)
        player_shortcut(Qt.Key.Key_Up).activated.connect(lambda: self.change_volume(5))
        player_shortcut(Qt.Key.Key_Down).activated.connect(lambda: self.change_volume(-5))


    def handle_escape_key(self):
        if self.side_menu.isVisible():
            self.side_menu.hide_menu()
        elif self.volume_slider.isVisible():
            self.volume_slider.hide()
        elif self.stacked_content_widget.currentWidget() == self.playlist_view_widget:
            self.show_player_view()
        elif self.stacked_content_widget.currentWidget() == self.search_view_widget:
            self.show_player_view()
        else:
            self.close()

    def mousePressEvent(self, event: QEvent):
        if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            if isinstance(event, QMouseEvent):
                click_pos_global = event.globalPosition().toPoint()
                click_pos_local = self.mapFromGlobal(click_pos_global)
                can_drag_by_title = False
                title_bar_local_click = self.title_bar_widget.mapFromGlobal(click_pos_global)

                if self.title_bar_widget.rect().contains(title_bar_local_click):

                    child_on_title = self.title_bar_widget.childAt(title_bar_local_click)

                    if child_on_title is None or not isinstance(child_on_title, QPushButton):
                        can_drag_by_title = True


                actual_widget_clicked = QApplication.widgetAt(click_pos_global)

                is_on_interactive_element = False
                if actual_widget_clicked is not None and (
                    isinstance(actual_widget_clicked, QPushButton) or
                    isinstance(actual_widget_clicked, QSlider) or
                    isinstance(actual_widget_clicked, QSizeGrip) or
                    isinstance(actual_widget_clicked, QListWidget) or
                    isinstance(actual_widget_clicked, QLineEdit)
                ):
                    is_on_interactive_element = True

                if self.side_menu.isVisible() and self.side_menu.geometry().contains(click_pos_local):

                     if actual_widget_clicked is self.side_menu or self.side_menu.isAncestorOf(actual_widget_clicked):
                         is_on_interactive_element = True


                if can_drag_by_title or (actual_widget_clicked is not None and self.isAncestorOf(actual_widget_clicked) and not is_on_interactive_element):
                    self.drag_pos = click_pos_global
                    event.accept()
                else:
                    self.drag_pos = QPoint()
                    event.ignore()
            else:

                super().mousePressEvent(event)
        else:

            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QEvent):

        if event.type() == QEvent.Type.MouseMove and event.buttons() == Qt.MouseButton.LeftButton:
            if not self.drag_pos.isNull():
                if isinstance(event, QMouseEvent):
                    new_pos = event.globalPosition().toPoint()
                    self.move(self.pos() + new_pos - self.drag_pos)
                    self.drag_pos = new_pos
                    event.accept()
                else:
                    super().mouseMoveEvent(event)
            else:
                event.ignore()
        else:
            super().mouseMoveEvent(event)


    def mouseReleaseEvent(self, event: QEvent):
        if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            if not self.drag_pos.isNull():
                self.drag_pos = QPoint()
                event.accept()
            else:
                event.ignore()
        else:
            super().mouseReleaseEvent(event)


    def add_files_to_current_playlist(self):
        if self.side_menu.isVisible(): self.side_menu.hide_menu()
        if self.volume_slider.isVisible(): self.volume_slider.hide()
        files, _ = QFileDialog.getOpenFileNames(self, f"Добавить аудиофайлы в «{self.current_playlist_name}»", "",
                                                "Audio Files (*.mp3 *.ogg *.wav *.m4a *.flac)")
        if not files: return
        if not self.current_playlist_name or self.current_playlist_name not in self.all_playlists:
            QMessageBox.warning(self, "Ошибка", "Нет активного плейлиста для добавления файлов.")
            return
        current_playlist_tracks = self.all_playlists[self.current_playlist_name]
        new_files_added_to_current = False
        existing_paths_in_current = {os.path.normcase(os.path.normpath(item['path']))
                                     for item in current_playlist_tracks}
        for file_path in files:
            key = os.path.normcase(os.path.normpath(file_path))
            if key not in existing_paths_in_current:
                track_info = self.get_track_info(file_path)
                current_playlist_tracks.append(track_info)
                new_files_added_to_current = True
                existing_paths_in_current.add(key)
        if new_files_added_to_current:
            self.active_playlist_tracks = current_playlist_tracks
            self.save_app_data()
            if self.current_index == -1 and self.active_playlist_tracks:
                self.current_index = 0;
                self.load_track(autoplay=False)
            self.update_buttons(self.media_player.playbackState())
            if self.stacked_content_widget.currentWidget() == self.playlist_view_widget:
                self.playlist_view_widget.refresh_playlists_list()

    def get_track_info(self, file_path):
        try:
            audio = File(file_path)
            if audio is None:
                title = os.path.basename(file_path); artist = "Неизвестный"; length = 0
            else:
                title_tags = audio.get('title') or audio.get('TIT2') or audio.get('©nam')
                artist_tags = audio.get('artist') or audio.get('TPE1') or audio.get('©ART')
                title = str(title_tags[0]) if title_tags else os.path.basename(file_path)
                artist = str(artist_tags[0]) if artist_tags else "Неизвестный"
                length = audio.info.length if hasattr(audio, 'info') and hasattr(audio.info, 'length') else 0
                title = title.strip() if title else os.path.basename(file_path)
                artist = artist.strip() if artist else "Неизвестный"
                length = float(length) if length else 0
            return {'path': file_path, 'title': title[:2048], 'artist': artist[:2048],
                    'length': length if 0 <= length <= 10**8 else 0}
        except Exception as e:
            print(f"Ошибка чтения информации файла {file_path}: {e}")
            return {'path': file_path, 'title': os.path.basename(file_path), 'artist': 'Ошибка', 'length': 0}

    def load_track(self, autoplay=True):
        self._cancel_eq()
        path_to_load = None
        current_original_path_in_playlist = None

        if 0 <= self.current_index < len(self.active_playlist_tracks):
            current_original_path_in_playlist = self.active_playlist_tracks[self.current_index]['path']

        if self.current_processed_track_path and \
                self.original_track_path_before_eq == current_original_path_in_playlist and \
                os.path.exists(self.current_processed_track_path):
            path_to_load = self.current_processed_track_path
        elif current_original_path_in_playlist:
            path_to_load = current_original_path_in_playlist
            if self.original_track_path_before_eq and self.original_track_path_before_eq != current_original_path_in_playlist:
                self._clear_current_eq_state()

        if 0 <= self.current_index < len(self.active_playlist_tracks) and path_to_load and os.path.exists(path_to_load):
            track_metadata = self.active_playlist_tracks[self.current_index]


            self._set_source(path_to_load, autoplay)

            self.title_label.setText(track_metadata.get('title', 'Название трека'))
            self.artist_label.setText(track_metadata.get('artist', 'Исполнитель'))
            self.playlist_name_label.setText(f"Плейлист: {self.current_playlist_name}")


            if self.placeholder_album_art_pixmap:
                self.album_art_label.set_album_art(self.placeholder_album_art_pixmap)
            else:
                self.album_art_label.set_album_art(None)
                self.album_art_label.setPlaceholderText(track_metadata.get('title', 'Обложка'))

        else:
            self.display_no_track_info()
            if path_to_load:
                self.artist_label.setText(f"Файл недоступен: {os.path.basename(path_to_load)}")

    def _set_source(self, path, autoplay=False, position=None):
        self.media_player.stop()
        self._pending_position = position
        self.media_player.setSource(QUrl.fromLocalFile(path) if path else QUrl())
        self._restore_position()
        if autoplay:
            self.media_player.play()

    def _restore_position(self, *_):
        if self._pending_position is not None and self.media_player.isSeekable():
            position, self._pending_position = self._pending_position, None
            self.seek_position(position)

    def stop_playback(self):
        self._pending_position = None
        self._cancel_eq()
        self.media_player.stop()

    def display_no_track_info(self):
        self._cancel_eq()
        self._set_source(None)
        self._clear_current_eq_state()
        self.title_label.setText("Нет треков");
        self.artist_label.setText(f"Добавьте музыку в '{self.current_playlist_name}'")
        self.playlist_name_label.setText(f"Плейлист: {self.current_playlist_name}")
        self.progress_slider.setRange(0, 0);
        self.progress_slider.setValue(0)
        self.current_time_label.setText("0:00");
        self.total_time_label.setText("0:00")
        if hasattr(self, 'album_art_label'):
            if self.placeholder_album_art_pixmap:
                self.album_art_label.set_album_art(self.placeholder_album_art_pixmap)
            else:
                self.album_art_label.set_album_art(None)
            self.album_art_label.setPlaceholderText("Нет треков")
            if isinstance(self.album_art_label, AnimatedAlbumArtWidget): self.album_art_label.stop_animation()
        self.update_buttons(QMediaPlayer.PlaybackState.StoppedState)

    def toggle_play(self):
        if not self.active_playlist_tracks: return
        if self.current_index == -1: self.current_index = 0; self.load_track(autoplay=True); return
        state = self.media_player.playbackState()
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.media_player.pause()
        else:
            if state == QMediaPlayer.PlaybackState.StoppedState and self.media_player.source().isEmpty():
                self.load_track(autoplay=True)
            else:
                self.media_player.play()

    def update_buttons(self, state):
        is_playing = (state == QMediaPlayer.PlaybackState.PlayingState)
        self.play_btn.setIcon(self.pause_icon if is_playing else self.play_icon)
        has_tracks = bool(self.active_playlist_tracks)
        self.play_btn.setEnabled(has_tracks)
        self.prev_btn.setEnabled(has_tracks and self.current_index != -1)
        self.next_btn.setEnabled(has_tracks and self.current_index != -1)
        if hasattr(self, 'album_art_label') and isinstance(self.album_art_label, AnimatedAlbumArtWidget):
            can_play = has_tracks and self.current_index != -1
            if is_playing and can_play:
                self.album_art_label.start_animation()
            else:
                self.album_art_label.stop_animation()

    def update_position(self, position):
        if self.media_player.duration() > 0 and not self.is_slider_pressed: self.progress_slider.setValue(position)
        self.current_time_label.setText(format_time(position))

    def update_duration(self, duration):
        if duration > 0:
            self.progress_slider.setRange(0, duration);
            self.total_time_label.setText(format_time(duration))
            if 0 <= self.current_index < len(self.active_playlist_tracks):
                stored_length_ms = int(self.active_playlist_tracks[self.current_index].get('length', 0) * 1000)
                if abs(stored_length_ms - duration) > 1000:
                    self.active_playlist_tracks[self.current_index]['length'] = duration / 1000.0



    def seek_position(self, position):
        if self.media_player.duration() > 0:
            duration = self.media_player.duration();
            pos_to_set = max(0, min(duration, position))
            self.media_player.setPosition(pos_to_set);
            self.current_time_label.setText(format_time(pos_to_set))

    def slider_pressed(self):
        if self.media_player.duration() > 0: self.is_slider_pressed = True

    def slider_released(self):
        if self.is_slider_pressed: self.is_slider_pressed = False; self.seek_position(self.progress_slider.value())

    def next_track(self, *, autoplay=None):
        if not self.active_playlist_tracks: return
        self.current_index = (self.current_index + 1) % len(self.active_playlist_tracks)
        was_playing = self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        self.load_track(autoplay=was_playing if autoplay is None else autoplay)

    def prev_track(self):
        if not self.active_playlist_tracks: return
        was_playing = self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        if self.media_player.position() > 3000:
            self.seek_position(0)
            return
        else:
            self.current_index = (self.current_index - 1 + len(self.active_playlist_tracks)) % len(
                self.active_playlist_tracks)
        self.load_track(autoplay=was_playing)

    def handle_media_status(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.next_track(autoplay=True)
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            self.handle_playback_error("Ошибка файла")
        elif status == QMediaPlayer.MediaStatus.LoadedMedia:
            self.update_duration(self.media_player.duration())
            self._restore_position()

    def handle_error(self):
        self._pending_position = None
        error_string = self.media_player.errorString()
        log.warning("Playback error: %s", error_string)
        self.handle_playback_error(f"Ошибка: {error_string}")
    def handle_playback_error(self, message):
        if 0 <= self.current_index < len(self.active_playlist_tracks):
            base_name = os.path.basename(self.active_playlist_tracks[self.current_index]['path'])
            self.artist_label.setText(f"{message}: {base_name}")
        else:
            self.artist_label.setText(message)

    def handle_menu_click(self, item_name):
        self.side_menu.hide_menu()
        if item_name == "playlists":
            self.show_playlist_view()
        elif item_name == "search":
            self.show_search_view()
        elif item_name == "settings":
            self.show_settings_view()

    def show_settings_view(self):
        self.settings_view_widget.load_settings()
        self.stacked_content_widget.setCurrentWidget(self.settings_view_widget)



    def show_player_view(self):
        self.stacked_content_widget.setCurrentWidget(self.player_view_widget)
        self.player_view_widget.setFocus()

    def show_playlist_view(self):
        self.playlist_view_widget.refresh_playlists_list()
        self.stacked_content_widget.setCurrentWidget(self.playlist_view_widget)

    def show_search_view(self):
        self.stacked_content_widget.setCurrentWidget(self.search_view_widget)
        self.search_view_widget.activate_search()

    def play_track_from_search(self, track_info, playlist_name):
        if self.current_playlist_name != playlist_name or not self.active_playlist_tracks:
            self.switch_to_playlist(playlist_name, autoplay=False)

        found_idx = -1
        for i, t in enumerate(self.active_playlist_tracks):
            if t.get('path') == track_info.get('path'):
                found_idx = i
                break

        if found_idx != -1:
            self.current_index = found_idx
            self.load_track(autoplay=True)
            self.show_player_view()
            self.search_view_widget.search_input.clear()
        else:
            QMessageBox.warning(self, "Ошибка воспроизведения",
                                f"Трек '{track_info.get('title')}' не найден в плейлисте '{playlist_name}'.")
            self.display_no_track_info()


    def switch_to_playlist(self, playlist_name, autoplay=True):
        if playlist_name in self.all_playlists:
            if self.current_playlist_name != playlist_name:
                self._clear_current_eq_state()

            self.current_playlist_name = playlist_name
            self.active_playlist_tracks = self.all_playlists[self.current_playlist_name]
            self.current_index = -1

            if self.active_playlist_tracks:
                self.current_index = 0
                self.load_track(autoplay=autoplay)
            else:
                self.display_no_track_info()

            self.playlist_name_label.setText(f"Плейлист: {self.current_playlist_name}")
        else:

            pass

    def toggle_volume_slider(self):
        self.volume_slider.setVisible(not self.volume_slider.isVisible())
        if self.volume_slider.isVisible(): self.volume_slider.raise_()

    def set_volume(self, value):
        volume_float = value / 100.0;
        self.audio_output.setVolume(volume_float)
        self.volume_btn.setToolTip(f"Громкость: {value}% (Up/Down)");
        self.initial_volume = volume_float

    def change_volume(self, delta_percent):
        current_val = self.volume_slider.value();
        new_val = max(0, min(100, current_val + delta_percent))
        if new_val != current_val: self.volume_slider.setValue(new_val)

    def get_app_data_path(self):
        override = os.environ.get("AUDIOFLOW_PROFILE_DIR")
        folder = Path(override) if override else Path.home() / ".AudioFlowPlayer"
        return folder / "app_data.json"

    def load_app_data(self):
        bands = [freq for freq, _ in SettingsViewWidget.EQ_BANDS]
        data = {}
        try:
            path = Path(self.get_app_data_path())
            path.parent.mkdir(parents=True, exist_ok=True)
            self._profile_lock = QLockFile(str(path.with_suffix(".lock")))
            if not self._profile_lock.tryLock(0):
                raise OSError("Профиль уже открыт или недоступен для записи.")
            try:
                data = read_profile(path)
            except FileNotFoundError:
                pass
            result = normalize_profile(data, self.DEFAULT_PLAYLIST_NAME, bands)
        except (OSError, ValueError, AttributeError, TypeError, RecursionError) as error:
            self._profile_readonly = True
            result = normalize_profile({}, self.DEFAULT_PLAYLIST_NAME, bands)
            log.warning("Cannot open profile: %s", error)
            self.statusBar().showMessage(f"Профиль не будет изменён: {error}")
        self.all_playlists, self.current_playlist_name, self.app_settings = result
        self.active_playlist_tracks = self.all_playlists[self.current_playlist_name]
        self.initial_volume = self.app_settings["volume"]
        self.volume_slider.setValue(round(self.initial_volume * 100))
        self.audio_output.setVolume(self.initial_volume)
        self.playlist_name_label.setText(f"Плейлист: {self.current_playlist_name}")

    def save_app_data(self):
        if self._profile_readonly:
            self.statusBar().showMessage("Профиль недоступен для записи. Изменения действуют только до закрытия.")
            return
        self.app_settings["volume"] = self.audio_output.volume()
        try:
            write_profile(self.get_app_data_path(), {
                "schema_version": 1,
                "all_playlists": self.all_playlists,
                "current_playlist_name": self.current_playlist_name,
                "settings": self.app_settings,
            })
        except (OSError, ValueError, TypeError) as error:
            log.warning("Cannot save profile: %s", error)
            self.statusBar().showMessage(f"Не удалось сохранить профиль: {error}")

    def closeEvent(self, event: QEvent):
        self.save_app_data()
        self._cancel_eq()
        self._set_source(None)
        self._eq_timer.stop()
        self._eq_executor.shutdown(wait=True, cancel_futures=True)
        if self._eq_dir is not None:
            self._eq_dir.cleanup()
        if self._profile_lock is not None:
            self._profile_lock.unlock()
        super().closeEvent(event)

    def handle_settings_update(self):
        pass

    def _clear_current_eq_state(self):
        path = self.current_processed_track_path
        self.current_processed_track_path = self.original_track_path_before_eq = None
        if path is not None:
            if (os.path.normcase(os.path.normpath(self.media_player.source().toLocalFile()))
                    == os.path.normcase(os.path.normpath(path))):
                self._set_source(None)
            self._remove_eq_file(path)

    def _remove_eq_file(self, path):
        try:
            Path(path).unlink(missing_ok=True)
        except OSError as error:
            log.debug("Temporary audio will be cleaned on close: %s", error)

    def _cancel_eq(self):
        if self._eq_job is not None:
            self._eq_job[1].set()

    def process_and_apply_equalizer(self, gains):
        self.app_settings["equalizer_gains"] = gains.copy()
        self.save_app_data()
        if not 0 <= self.current_index < len(self.active_playlist_tracks):
            self.statusBar().showMessage("Настройки EQ сохранены. Выберите трек для обработки.")
            return
        source = self.active_playlist_tracks[self.current_index]["path"]
        if not any(gains.values()):
            self._cancel_eq()
            if self.current_processed_track_path is not None:
                self._swap_eq_source(source, None)
            self.statusBar().showMessage("EQ выключен: играет оригинальный файл.")
            return
        if self._eq_job is not None:
            return
        if not os.path.isfile(source):
            self.statusBar().showMessage("Файл недоступен для EQ.")
            return
        try:
            if self._eq_dir is None:
                self._eq_dir = tempfile.TemporaryDirectory(prefix="audioflow-eq-", ignore_cleanup_errors=True)
            with tempfile.NamedTemporaryFile(dir=self._eq_dir.name, suffix=".wav", delete=False) as output:
                target = output.name
        except OSError as error:
            self.statusBar().showMessage(f"Не удалось подготовить файл EQ: {error}")
            return
        cancel = Event()
        future = self._eq_executor.submit(render_eq, source, target, gains.copy(), cancel)
        self._eq_job = future, cancel, source, target
        self.settings_view_widget.apply_eq_button.setEnabled(False)
        self.statusBar().showMessage("EQ: обработка текущего трека…")
        self._eq_timer.start()

    def _poll_eq(self):
        future, cancel, source, target = self._eq_job
        if not future.done():
            return
        self._eq_job = None
        self._eq_timer.stop()
        self.settings_view_widget.apply_eq_button.setEnabled(True)
        if cancel.is_set():
            self._remove_eq_file(target)
            return
        try:
            future.result()
        except Exception as error:
            self._remove_eq_file(target)
            log.exception("EQ processing failed")
            self.statusBar().showMessage(f"EQ не применён: {error}")
            return
        self._swap_eq_source(source, target)
        self.statusBar().showMessage("EQ применён к текущему треку.")

    def _swap_eq_source(self, source, target):
        previous = self.current_processed_track_path
        playing = self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        position = self.media_player.position()
        self.current_processed_track_path = target
        self.original_track_path_before_eq = source if target else None
        self._set_source(target or source, playing, position)
        if previous:
            self._remove_eq_file(previous)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    if "--smoke-test" in sys.argv and not os.environ.get("AUDIOFLOW_PROFILE_DIR"):
        sys.exit("Smoke test requires AUDIOFLOW_PROFILE_DIR")
    player = MP3Player()
    player.show()
    if "--smoke-test" in sys.argv:
        def smoke_test():
            try:
                assert not player.play_icon.pixmap(24, 24).isNull()
                assert player.placeholder_album_art_pixmap is not None
                assert to_latin("музыка") == "muzyka"
                import wave
                with tempfile.TemporaryDirectory() as directory:
                    original = Path(directory) / "source.wav"
                    processed = Path(directory) / "processed.wav"
                    with wave.open(str(original), "wb") as stream:
                        stream.setparams((2, 2, 24000, 0, "NONE", "not compressed"))
                        stream.writeframes(b"\0" * 9600)
                    render_eq(str(original), str(processed), {600: 6}, Event())
                    assert processed.stat().st_size > 9600
                player.close()
                app.exit(0)
            except Exception:
                log.exception("Smoke test failed")
                app.exit(1)
        QTimer.singleShot(0, smoke_test)
    sys.exit(app.exec())
