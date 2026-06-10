"""
main_standalone.py — PyInstaller 패키징용 단독 실행 엔트리포인트

개발 환경에서는 사용하지 않습니다.
electron-builder 빌드 시 PyInstaller가 이 파일을 jarvis-core.exe로 변환합니다.
"""

import sys
import os

# PyInstaller 번들 내 상대 경로 처리
if getattr(sys, 'frozen', False):
    BASE_DIR = sys._MEIPASS
    sys.path.insert(0, BASE_DIR)
    os.chdir(BASE_DIR)

import uvicorn
from app.main import app

if __name__ == '__main__':
    uvicorn.run(
        app,
        host      = '127.0.0.1',
        port      = 8000,
        log_level = 'info',
    )
