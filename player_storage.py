"""Bounded, backwards-compatible playlist persistence."""
import json
import os
from pathlib import Path
import tempfile

MAX_PROFILE = 32 * 1024 * 1024


def number(value, default, lower, upper):
    if isinstance(value, (float, int)) and not isinstance(value, bool) and lower <= value <= upper:
        return float(value)
    return default


def read_profile(path):
    with open(path, "rb") as stream:
        raw = stream.read(MAX_PROFILE + 1)
    if len(raw) > MAX_PROFILE:
        raise ValueError("Файл профиля слишком большой.")
    return json.loads(raw.decode("utf-8-sig"))


def normalize_profile(data, default, bands):
    if not isinstance(data, dict) or data.get("schema_version", 1) != 1:
        raise ValueError("Неизвестный формат профиля.")
    playlists = {}
    for name, tracks in data.get("all_playlists", {}).items():
        if not name.strip() or not isinstance(tracks, list):
            raise ValueError("Некорректная структура плейлиста.")
        clean = []
        for track in tracks:
            path = track.get("path") if isinstance(track, dict) else None
            if not isinstance(path, str) or not path or "\x00" in path:
                raise ValueError("Некорректная запись о треке.")
            title, artist = track.get("title"), track.get("artist")
            clean.append({
                **track, "path": path,
                "title": title[:2048] if isinstance(title, str) and title else Path(path).name,
                "artist": artist[:2048] if isinstance(artist, str) and artist else "Неизвестный",
                "length": number(track.get("length"), 0.0, 0, 10**8),
            })
        playlists[name] = clean
    if not playlists:
        playlists[default] = []
    active = data.get("current_playlist_name")
    if not isinstance(active, str) or active not in playlists:
        active = next(iter(playlists))
    settings = data.get("settings")
    settings = settings.copy() if isinstance(settings, dict) else {}
    gains = settings.get("equalizer_gains")
    gains = gains if isinstance(gains, dict) else {}
    settings["volume"] = number(settings.get("volume"), 0.5, 0, 1)
    settings["equalizer_gains"] = {
        freq: number(gains.get(str(freq), gains.get(freq)), 0.0, -12, 12)
        for freq in bands
    }
    return playlists, active, settings


def write_profile(path, data):
    path = Path(path)
    payload = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
    if len(payload) > MAX_PROFILE:
        raise ValueError("Файл профиля слишком большой.")
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = path.with_suffix(".json.bak")
    if path.exists() and not backup.exists():
        with path.open("rb") as stream:
            original = stream.read(MAX_PROFILE + 1)
        if len(original) > MAX_PROFILE:
            raise ValueError("Невозможно сохранить резервную копию профиля.")
        _replace(backup, original)
    _replace(path, payload)


def _replace(path, payload):
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".profile-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
