"""
vision_sensor.py — Webcam-based hand gesture detector
──────────────────────────────────────────────────────
MediaPipe Hand landmarks reference (per hand):
  Wrist  : 0
  Thumb  : 1(CMC) 2(MCP) 3(IP)  4(TIP)
  Index  : 5(MCP) 6(PIP) 7(DIP) 8(TIP)
  Middle : 9(MCP)10(PIP)11(DIP)12(TIP)
  Ring   :13(MCP)14(PIP)15(DIP)16(TIP)
  Pinky  :17(MCP)18(PIP)19(DIP)20(TIP)

Finger "up" rule:   tip.y  < pip.y  (image Y grows downward)
Finger "down" rule: tip.y  > pip.y
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

import cv2
import mediapipe as mp


# ── Data types ────────────────────────────────────────────────────────────────

@dataclass
class GestureResult:
    detected: bool
    gesture_name: str
    hand_count: int
    fps: float = 0.0


@dataclass
class _FPSCounter:
    _times: list = field(default_factory=list)

    def tick(self) -> float:
        now = time.perf_counter()
        self._times = [t for t in self._times if now - t < 1.0]
        self._times.append(now)
        return len(self._times)


# ── Gesture detector ──────────────────────────────────────────────────────────

class VisionSensor:
    """Manages the webcam and detects predefined hand gestures via MediaPipe."""

    GESTURE_INDEX_UP = "index_up"     # ☝  only index extended
    GESTURE_PEACE    = "peace"        # ✌  index + middle extended
    GESTURE_OPEN     = "open_hand"    # 🖐  all five fingers extended

    # Tip / PIP landmark pairs for each finger
    _FINGER_PAIRS = {
        "index":  (8,  6),
        "middle": (12, 10),
        "ring":   (16, 14),
        "pinky":  (20, 18),
    }

    def __init__(
        self,
        camera_index: int = 0,
        detection_confidence: float = 0.75,
        tracking_confidence: float = 0.6,
    ) -> None:
        self._mp_hands = mp.solutions.hands
        self._mp_draw  = mp.solutions.drawing_utils
        self._hands = self._mp_hands.Hands(
            static_image_mode=False,
            max_num_hands=1,
            min_detection_confidence=detection_confidence,
            min_tracking_confidence=tracking_confidence,
        )
        self._cap: Optional[cv2.VideoCapture] = None
        self._camera_index = camera_index
        self._fps = _FPSCounter()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def open(self) -> None:
        self._cap = cv2.VideoCapture(self._camera_index)
        if not self._cap.isOpened():
            raise RuntimeError(f"Cannot open camera index {self._camera_index}")

    def close(self) -> None:
        if self._cap and self._cap.isOpened():
            self._cap.release()
        self._hands.close()
        cv2.destroyAllWindows()

    def __enter__(self) -> "VisionSensor":
        self.open()
        return self

    def __exit__(self, *_) -> None:
        self.close()

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _finger_up(self, lm, finger: str) -> bool:
        tip_idx, pip_idx = self._FINGER_PAIRS[finger]
        return lm[tip_idx].y < lm[pip_idx].y

    def _classify_gesture(self, hand_landmarks) -> str:
        lm = hand_landmarks.landmark
        up = {f: self._finger_up(lm, f) for f in self._FINGER_PAIRS}

        if up["index"] and not up["middle"] and not up["ring"] and not up["pinky"]:
            return self.GESTURE_INDEX_UP

        if up["index"] and up["middle"] and not up["ring"] and not up["pinky"]:
            return self.GESTURE_PEACE

        if all(up.values()):
            return self.GESTURE_OPEN

        return ""

    def _draw_overlay(self, frame, hand_landmarks, gesture: str, fps: float) -> None:
        self._mp_draw.draw_landmarks(
            frame, hand_landmarks, self._mp_hands.HAND_CONNECTIONS,
            self._mp_draw.DrawingSpec(color=(0, 255, 180), thickness=2, circle_radius=3),
            self._mp_draw.DrawingSpec(color=(0, 180, 255), thickness=2),
        )
        label  = f"  {gesture}" if gesture else "  ---"
        color  = (0, 255, 100) if gesture else (80, 80, 80)
        cv2.putText(frame, f"GESTURE:{label}", (10, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, color, 2, cv2.LINE_AA)
        cv2.putText(frame, f"FPS: {fps:.0f}", (10, 75),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1, cv2.LINE_AA)

    # ── Public API ────────────────────────────────────────────────────────────

    def read_gesture(self, show_preview: bool = False) -> GestureResult:
        """Capture one frame, classify gesture, and return result."""
        if self._cap is None:
            raise RuntimeError("Camera not opened — call open() first.")

        ret, frame = self._cap.read()
        if not ret:
            return GestureResult(detected=False, gesture_name="", hand_count=0)

        frame   = cv2.flip(frame, 1)           # mirror so left-right is natural
        rgb     = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = self._hands.process(rgb)

        gesture    = ""
        hand_count = 0

        if results.multi_hand_landmarks:
            hand_count = len(results.multi_hand_landmarks)
            gesture    = self._classify_gesture(results.multi_hand_landmarks[0])

            if show_preview:
                self._draw_overlay(
                    frame,
                    results.multi_hand_landmarks[0],
                    gesture,
                    self._fps.tick(),
                )

        if show_preview:
            if not results.multi_hand_landmarks:
                cv2.putText(frame, "No hand detected", (10, 40),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (80, 80, 80), 1)
            cv2.imshow("JARVIS Vision", frame)
            cv2.waitKey(1)

        return GestureResult(
            detected    = bool(gesture),
            gesture_name= gesture,
            hand_count  = hand_count,
            fps         = self._fps.tick(),
        )

    def wait_for_gesture(
        self,
        target: str = GESTURE_INDEX_UP,
        show_preview: bool = True,
        hold_frames: int = 6,
        timeout_sec: Optional[float] = None,
    ) -> bool:
        """Block until `target` is held steadily for `hold_frames` consecutive frames.

        Args:
            timeout_sec: None = wait forever; otherwise returns False on timeout.

        Returns:
            True when gesture confirmed, False on timeout.
        """
        consecutive = 0
        deadline    = (time.monotonic() + timeout_sec) if timeout_sec else None

        while True:
            result = self.read_gesture(show_preview=show_preview)

            if result.detected and result.gesture_name == target:
                consecutive += 1
                if consecutive >= hold_frames:
                    return True
            else:
                consecutive = 0

            if not show_preview:
                time.sleep(0.02)    # yield CPU when no display loop

            if deadline and time.monotonic() > deadline:
                return False


# ── Convenience wrapper ───────────────────────────────────────────────────────

def detect_index_up(
    camera_index: int = 0,
    timeout_sec: Optional[float] = None,
    show_preview: bool = True,
) -> bool:
    """One-shot: open camera → wait for index-up → close camera → return result."""
    with VisionSensor(camera_index=camera_index) as sensor:
        return sensor.wait_for_gesture(
            target       = VisionSensor.GESTURE_INDEX_UP,
            show_preview = show_preview,
            timeout_sec  = timeout_sec,
        )
