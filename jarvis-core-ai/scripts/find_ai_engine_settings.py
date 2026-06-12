import os
import subprocess

# jarvis-core 디렉토리 전체 구조 탐색
base = r'C:\Users\CEO\AppData\Roaming\jarvis-core'

print('=== Directory Structure ===')
for root, dirs, files in os.walk(base):
    # 너무 깊은 node_modules 등 제외
    dirs[:] = [d for d in dirs if d not in ['node_modules', '.git', '__pycache__', 'dist', '.next']]
    level = root.replace(base, '').count(os.sep)
    indent = '  ' * level
    print(f'{indent}{os.path.basename(root)}/')
    subindent = '  ' * (level + 1)
    for file in files:
        print(f'{subindent}{file}')
