import sys
import os

# PythonAnywhere 경로 설정
project_home = '/home/{USERNAME}/worldcup-betting'
if project_home not in sys.path:
    sys.path.insert(0, project_home)

os.environ['DB_DIR'] = project_home  # SQLite를 프로젝트 폴더에 저장

from app import app as application  # noqa
