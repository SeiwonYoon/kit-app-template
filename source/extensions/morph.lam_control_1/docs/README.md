# morph.lam_control_1

`morph.lam_control` 의 복제·개발 브랜치 확장입니다. **듀얼 화면·CSV 프리런** 등 신규 기능은 본 확장에서만 수정합니다.

- Kit 앱: `morph.editor.kit` → `"morph.lam_control_1"`
- Python 패키지: `morph/lam_control_1/`
- CSV 프리런 export: `data/csv_prerun/` (저장 ON/OFF 설정 가능)

기획 문서: `docs/lam_control_dual_screen_tbs_pattern_port_brief_ko.md` (repo `docs/`)

원본 확장(`morph.lam_control`)은 롤백·비교용으로 유지합니다.

## 개발 — 저장 시 자동 리로드 (hot reload)

1. `morph.editor.kit` 에서 **`morph.lam_control_1` 만** 활성화 (`morph.lam_control` 은 끄기).
2. `config/extension.toml` 의 Python 모듈은 **`morph.lam_control_1`** 이어야 함 (폴더명과 일치).
3. `.py` 저장 시 Kit 콘솔에 extension reload 로그가 보여야 함 (`app.extensions.debugMode=true` in editor.kit).
4. 리로드가 안 되면: Extension Manager → `morph.lam_control_1` 토글 OFF/ON, 또는 Kit 완전 재시작.
5. Windows: `repo build` 가 꺼져 있어도 `${root}/source/extensions` 경로에서 직접 로드됨.

`premake5.lua` 는 `prebuild_link` 로 `morph/` 를 _build/exts 와 연결 (Linux build 시).
