"""
voice_sensor.py — Microphone recorder + faster-whisper STT
────────────────────────────────────────────────────────────
Pipeline:
  sounddevice.rec()  →  numpy float32 array
  → WAV temp file    →  WhisperModel.transcribe()
  → TranscriptResult (text, language, duration)

VoiceSensor is designed to be instantiated once and reused;
the Whisper model is lazy-loaded on the first transcription call.
"""

from __future__ import annotations

import os
import tempfile
import time
import wave
from dataclasses import dataclass
from typing import Optional

import numpy as np
import sounddevice as sd


# ── Data type ─────────────────────────────────────────────────────────────────

@dataclass
class TranscriptResult:
    text: str
    language: str
    language_probability: float
    duration_sec: float
    processing_sec: float

    def __bool__(self) -> bool:
        return bool(self.text.strip())

    def __str__(self) -> str:
        return self.text


# ── Sensor class ──────────────────────────────────────────────────────────────

class VoiceSensor:
    """Records microphone audio and transcribes it with faster-whisper."""

    def __init__(
        self,
        model_size: str = "base",          # tiny / base / small / medium / large
        device: str = "cpu",               # "cpu" or "cuda"
        compute_type: str = "int8",        # int8 (fast, low mem) | float16 | float32
        sample_rate: int = 16_000,         # Hz — Whisper natively uses 16 kHz
        channels: int = 1,
        vad_filter: bool = True,           # skip silent segments automatically
    ) -> None:
        self._model_size   = model_size
        self._device       = device
        self._compute_type = compute_type
        self._sample_rate  = sample_rate
        self._channels     = channels
        self._vad_filter   = vad_filter
        self._model        = None          # lazy-loaded

    # ── Model loading ─────────────────────────────────────────────────────────

    def _load_model(self) -> None:
        if self._model is not None:
            return
        from faster_whisper import WhisperModel
        print(
            f"[JARVIS] Loading Whisper '{self._model_size}' "
            f"on {self._device} ({self._compute_type})..."
        )
        t0 = time.perf_counter()
        self._model = WhisperModel(
            self._model_size,
            device=self._device,
            compute_type=self._compute_type,
        )
        print(f"[JARVIS] Whisper ready ({time.perf_counter() - t0:.1f}s).")

    # ── Audio helpers ─────────────────────────────────────────────────────────

    def _record_raw(self, duration_sec: float) -> np.ndarray:
        """Block and record `duration_sec` seconds from the default microphone."""
        frames = int(duration_sec * self._sample_rate)
        audio  = sd.rec(
            frames,
            samplerate=self._sample_rate,
            channels=self._channels,
            dtype="float32",
            blocking=True,          # wait until done (no sd.wait() needed)
        )
        return audio                 # shape: (frames, channels)

    def _to_wav(self, audio: np.ndarray) -> str:
        """Convert numpy float32 array → 16-bit WAV temp file. Returns file path."""
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()

        mono   = audio[:, 0] if audio.ndim > 1 else audio
        pcm16  = np.clip(mono, -1.0, 1.0)
        pcm16  = (pcm16 * 32_767).astype(np.int16)

        with wave.open(tmp.name, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self._sample_rate)
            wf.writeframes(pcm16.tobytes())

        return tmp.name

    @staticmethod
    def _rms(audio: np.ndarray) -> float:
        """Root mean square — proxy for audio level."""
        return float(np.sqrt(np.mean(audio ** 2)))

    # ── Public API ────────────────────────────────────────────────────────────

    def record(
        self,
        duration_sec: float = 5.0,
        countdown: bool = True,
    ) -> np.ndarray:
        """Record audio from the microphone and return a numpy array.

        Args:
            duration_sec: How many seconds to record.
            countdown:    Print a visual countdown before recording starts.
        """
        if countdown:
            for i in range(3, 0, -1):
                print(f"[JARVIS] Recording in {i}...", end="\r")
                time.sleep(1.0)
            print(f"[JARVIS] 🎙  Recording {duration_sec:.0f}s — speak now!   ")

        audio = self._record_raw(duration_sec)
        level = self._rms(audio)
        print(f"[JARVIS] Recording complete. Level: {level:.4f}")
        return audio

    def transcribe(self, audio: np.ndarray) -> TranscriptResult:
        """Transcribe a numpy audio array using faster-whisper.

        Args:
            audio: float32 numpy array from `record()`.

        Returns:
            TranscriptResult with .text, .language, etc.
        """
        self._load_model()

        wav_path = self._to_wav(audio)
        t0       = time.perf_counter()

        try:
            segments, info = self._model.transcribe(
                wav_path,
                beam_size   = 5,
                vad_filter  = self._vad_filter,
                word_timestamps = False,
            )
            # Consume the generator (segments are lazy)
            text = " ".join(seg.text.strip() for seg in segments).strip()
        finally:
            os.unlink(wav_path)

        return TranscriptResult(
            text                 = text,
            language             = info.language,
            language_probability = round(info.language_probability, 3),
            duration_sec         = len(audio) / self._sample_rate,
            processing_sec       = round(time.perf_counter() - t0, 2),
        )

    def listen(
        self,
        duration_sec: float = 5.0,
        countdown: bool = True,
    ) -> TranscriptResult:
        """Record then immediately transcribe — one-stop convenience method."""
        audio = self.record(duration_sec=duration_sec, countdown=countdown)

        if self._rms(audio) < 1e-4:
            print("[JARVIS] Audio too quiet — skipping transcription.")
            return TranscriptResult(
                text="", language="", language_probability=0.0,
                duration_sec=duration_sec, processing_sec=0.0,
            )

        print("[JARVIS] Transcribing...")
        result = self.transcribe(audio)
        print(f"[JARVIS] Done in {result.processing_sec}s "
              f"[{result.language} / {result.language_probability:.0%}]")
        return result

    def listen_stream(
        self,
        chunk_sec: float = 3.0,
        max_chunks: int = 4,
    ):
        """Generator: yield TranscriptResult for each recorded chunk.

        Useful for longer utterances — yields partial results in near-real-time.
        """
        for _ in range(max_chunks):
            yield self.listen(duration_sec=chunk_sec, countdown=False)


# ── Convenience wrapper ───────────────────────────────────────────────────────

def listen_once(
    duration_sec: float = 5.0,
    model_size: str = "base",
    device: str = "cpu",
) -> TranscriptResult:
    """One-shot: create sensor → record → transcribe → return result."""
    sensor = VoiceSensor(model_size=model_size, device=device)
    return sensor.listen(duration_sec=duration_sec)
