"""Local speaker verification for the HAL frontend — the Crew Manifest.

Voiceprints via sherpa-onnx (Apache-2.0) and the 3D-Speaker CAM++ model
(Apache-2.0, ~28MB, auto-downloaded on first enrollment). Everything runs on
CPU in ~70ms per utterance and nothing leaves the machine — same rule as
STT/TTS. Decoding reuses PyAV, already present as a faster-whisper
dependency.

Profiles live in data/speakers.json. The first enrolled voice becomes the
commander: the only voice that can approve tool-permission requests by
speech (typed and on-screen approvals are unaffected — a keyboard already
implies physical access).

Measured on this machine: same voice ≈ 0.78 cosine, different voices ≈
0.12–0.29, so the 0.5 default threshold has wide margin. Piper's own HAL
voice self-scores ≈ 0.17 — HAL's speech through the speakers cannot
false-accept as a crew member.

Degrades gracefully: without sherpa-onnx (`uv pip install --python
<hermes-venv>/bin/python sherpa-onnx`) or the model file, available()/
ready() gate the whole feature off and callers explain instead of failing.
"""
from __future__ import annotations

import io
import json
import os
import threading
import time
import urllib.request
from pathlib import Path

import numpy as np

MODEL_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "speaker-recongition-models/"  # (sic — the upstream tag has the typo)
    "3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx"
)
THRESHOLD = float(os.environ.get("HAL_VOICE_THRESHOLD", "0.5"))
SAMPLE_RATE = 16000


class SpeakerID:
    def __init__(self, data_dir: Path):
        self.model_path = data_dir / "speaker" / "campplus.onnx"
        self.profiles_path = data_dir / "speakers.json"
        self._extractor = None
        # sherpa streams are cheap but the extractor is shared; serialize
        # compute so concurrent transports can't interleave inside it.
        self._lock = threading.Lock()
        try:
            data = json.loads(self.profiles_path.read_text())
        except (OSError, json.JSONDecodeError):
            data = {}
        self._commander: str | None = data.get("commander")
        self._profiles: dict[str, dict] = data.get("profiles", {})

    # -- capability gates ----------------------------------------------------

    def available(self) -> bool:
        try:
            import sherpa_onnx  # noqa: F401
        except ImportError:
            return False
        return True

    def ready(self) -> bool:
        return self.available() and self.model_path.exists()

    def enrolled(self) -> bool:
        return bool(self._profiles)

    def commander(self) -> str | None:
        return self._commander

    def names(self) -> list[str]:
        return sorted(self._profiles)

    def ensure_model(self) -> bool:
        """Fetch the embedding model on first use (blocking — call from a
        worker thread)."""
        if self.model_path.exists():
            return True
        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.model_path.with_suffix(".onnx.tmp")
        try:
            print(f"[speaker_id] downloading voiceprint model ({MODEL_URL})")
            urllib.request.urlretrieve(MODEL_URL, tmp)
            tmp.rename(self.model_path)
            return True
        except OSError as exc:
            print(f"[speaker_id] model download failed: {exc}")
            tmp.unlink(missing_ok=True)
            return False

    # -- embedding -----------------------------------------------------------

    def _get_extractor(self):
        if self._extractor is None:
            import sherpa_onnx

            config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                model=str(self.model_path), num_threads=1
            )
            self._extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)
        return self._extractor

    @staticmethod
    def _decode(audio_bytes: bytes) -> np.ndarray:
        """Any browser container (webm/mp4/wav) -> 16k mono float32."""
        import av

        container = av.open(io.BytesIO(audio_bytes))
        resampler = av.audio.resampler.AudioResampler(
            format="fltp", layout="mono", rate=SAMPLE_RATE
        )
        chunks = []
        for frame in container.decode(audio=0):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1))
        container.close()
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(chunks).astype(np.float32)

    def embed(self, audio_bytes: bytes) -> np.ndarray | None:
        """L2-normalized voiceprint, or None when the feature is off or the
        clip is too short to embed (< ~0.5s of audio)."""
        if not self.ready():
            return None
        try:
            samples = self._decode(audio_bytes)
        except Exception as exc:
            print(f"[speaker_id] decode failed: {exc!r}")
            return None
        if len(samples) < SAMPLE_RATE // 2:
            return None
        with self._lock:
            extractor = self._get_extractor()
            stream = extractor.create_stream()
            stream.accept_waveform(SAMPLE_RATE, samples)
            stream.input_finished()
            vector = np.array(extractor.compute(stream), dtype=np.float32)
        norm = float(np.linalg.norm(vector))
        return vector / norm if norm > 0 else None

    # -- profiles ------------------------------------------------------------

    def enroll(self, name: str, audio_bytes: bytes) -> bool:
        vector = self.embed(audio_bytes)
        if vector is None:
            return False
        name = name.strip().title()
        self._profiles[name] = {
            "embedding": [float(x) for x in vector],
            "enrolled_at": time.time(),
        }
        if self._commander is None:
            self._commander = name
        self._save()
        return True

    def identify(self, audio_bytes: bytes) -> tuple[str | None, float]:
        """Best-matching enrolled name above threshold, with its score."""
        if not self._profiles:
            return None, 0.0
        vector = self.embed(audio_bytes)
        if vector is None:
            return None, 0.0
        best_name, best_score = None, -1.0
        for name, profile in self._profiles.items():
            score = float(np.dot(vector, np.array(profile["embedding"], dtype=np.float32)))
            if score > best_score:
                best_name, best_score = name, score
        if best_score >= THRESHOLD:
            return best_name, best_score
        return None, best_score

    def forget(self, name: str) -> bool:
        name = name.strip().title()
        if name not in self._profiles:
            return False
        del self._profiles[name]
        if self._commander == name:
            # Succession: the earliest remaining enrollment takes command.
            remaining = sorted(self._profiles.items(), key=lambda kv: kv[1]["enrolled_at"])
            self._commander = remaining[0][0] if remaining else None
        self._save()
        return True

    def _save(self) -> None:
        payload = {"commander": self._commander, "profiles": self._profiles}
        tmp = self.profiles_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload))
        tmp.replace(self.profiles_path)


manager: SpeakerID | None = None


def init(data_dir: Path) -> None:
    global manager
    manager = SpeakerID(data_dir)
