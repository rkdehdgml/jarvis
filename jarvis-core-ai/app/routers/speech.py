from fastapi import APIRouter, UploadFile, File
from pydantic import BaseModel
import tempfile, os

router = APIRouter()


class TranscriptResponse(BaseModel):
    text: str
    language: str


@router.post("/transcribe", response_model=TranscriptResponse)
async def transcribe(file: UploadFile = File(...)):
    from faster_whisper import WhisperModel
    from app.config import settings

    model = WhisperModel(settings.whisper_model_size, device=settings.whisper_device)

    suffix = os.path.splitext(file.filename)[1] or ".wav"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        segments, info = model.transcribe(tmp_path)
        text = " ".join(seg.text.strip() for seg in segments)
        return TranscriptResponse(text=text, language=info.language)
    finally:
        os.unlink(tmp_path)
