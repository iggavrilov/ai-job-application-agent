"""Standard-library-only readiness check: python -m agent.diagnostics."""
import importlib.util
import json
import sys
from pathlib import Path

MODULES = ['streamlit','sqlalchemy','httpx','openai','pymupdf','pydantic','yaml','pytest','dotenv','filelock','pandas']


def readiness():
    root = Path(__file__).resolve().parents[1]
    packages = {name: importlib.util.find_spec(name) is not None for name in MODULES}
    return {'python':sys.version.split()[0], 'python_supported':sys.version_info >= (3,12),
            'packages':packages, 'profile_present':(root/'candidate_profile.json').is_file(),
            'note':'No network calls. API keys and personal profile content are never printed.'}


def main():
    result = readiness()
    print(json.dumps(result,indent=2))
    return 0 if result['python_supported'] and all(result['packages'].values()) and result['profile_present'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
