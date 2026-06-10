from fastapi import APIRouter, UploadFile, File
from pydantic import BaseModel
import tempfile, os

router = APIRouter()


class FaceDetectResponse(BaseModel):
    face_count: int
    detections: list[dict]


@router.post("/detect-faces", response_model=FaceDetectResponse)
async def detect_faces(file: UploadFile = File(...)):
    import cv2
    import numpy as np

    data = await file.read()
    arr = np.frombuffer(data, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)

    face_cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5)

    detections = [{"x": int(x), "y": int(y), "w": int(w), "h": int(h)} for x, y, w, h in faces]
    return FaceDetectResponse(face_count=len(detections), detections=detections)
