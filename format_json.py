import json
import os
from pathlib import Path

TARGET_NAME = "rendered_template_metadata.json"

def pretty(path: Path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(
            json.dumps(data, indent=4, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"[format_json] formatted: {path}")
    except Exception as e:
        print(f"[format_json] skip {path}: {e}")

def main():
    # repo.bat 옆(= repo 루트)에 format_json.py를 둔 전제
    repo_root = Path(__file__).resolve().parent

    # ✅ 스캔 범위를 source로 제한 (template 생성물이 여기 생김)
    search_roots = [
        repo_root / "source",
    ]

    for base in search_roots:
        if not base.exists():
            continue

        for dirpath, dirnames, filenames in os.walk(base, topdown=True):
            # ✅ Windows에서 종종 문제되는 폴더/정크 스킵 규칙
            dirnames[:] = [
                d for d in dirnames
                if d not in ("_compiler", ".git", ".svn", ".hg", "node_modules")
            ]

            if TARGET_NAME in filenames:
                pretty(Path(dirpath) / TARGET_NAME)

if __name__ == "__main__":
    main()
