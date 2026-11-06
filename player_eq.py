"""Streaming EQ for the selected track. Runs outside the Qt event loop."""
import math
from pathlib import Path
import shutil

BLOCK = 32768
MAX_BYTES = 512 * 1024 * 1024


class Cancelled(Exception):
    pass


def render_eq(source, target, gains, cancelled):
    import numpy as np
    from scipy.signal import sosfilt
    import soundfile as sf

    scratch = Path(target).with_suffix(".working.wav")
    try:
        with sf.SoundFile(source) as audio:
            rate, channels = audio.samplerate, audio.channels
            size = audio.frames * channels * 4
            if not 0 < size <= MAX_BYTES or not 1 <= channels <= 8:
                raise ValueError("Для EQ нужен файл с 1–8 каналами и объёмом до 512 МиБ после декодирования.")
            if shutil.disk_usage(scratch.parent).free < 2 * size + 16 * 1024**2:
                raise ValueError("Недостаточно места для временного аудио.")
            sections = []
            for frequency, gain in gains.items():
                if not gain or not 0 < frequency < rate / 2:
                    continue
                # W3C Audio EQ Cookbook: peaking EQ with Q = 1.
                a = 10 ** (gain / 40)
                omega = 2 * math.pi * frequency / rate
                alpha = math.sin(omega) / 2
                middle = -2 * math.cos(omega)
                a0 = 1 + alpha / a
                sections.append([(1 + alpha*a)/a0, middle/a0, (1-alpha*a)/a0,
                                 1, middle/a0, (1-alpha/a)/a0])
            state = np.zeros((len(sections), 2, channels))
            peak, frames = 0.0, 0
            with sf.SoundFile(scratch, "w", rate, channels, subtype="FLOAT") as output:
                for block in audio.blocks(blocksize=BLOCK, dtype="float32", always_2d=True):
                    if cancelled.is_set():
                        raise Cancelled
                    frames += len(block)
                    if frames * channels * 4 > MAX_BYTES:
                        raise ValueError("Достигнут предел размера EQ.")
                    if sections:
                        block, state = sosfilt(sections, block, axis=0, zi=state)
                    if not np.isfinite(block).all():
                        raise ValueError("В аудиофайле есть недопустимые значения.")
                    peak = max(peak, float(np.max(np.abs(block))))
                    output.write(block)
        scale = min(1.0, 0.98 / peak) if peak else 1.0
        with sf.SoundFile(scratch) as audio, sf.SoundFile(target, "w", rate, channels, subtype="PCM_24") as output:
            for block in audio.blocks(blocksize=BLOCK, dtype="float32", always_2d=True):
                if cancelled.is_set():
                    raise Cancelled
                output.write(block * scale)
    except BaseException:
        Path(target).unlink(missing_ok=True)
        raise
    finally:
        scratch.unlink(missing_ok=True)
