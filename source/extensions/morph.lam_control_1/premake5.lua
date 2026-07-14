local ext = get_current_extension_info()
project_ext(ext)
-- 개발 시 source ↔ _build/exts 동기화: copy 대신 link (저장 즉시 Kit fswatcher 반영)
repo_build.prebuild_link {
    { "data", ext.target_dir.."/data" },
    { "docs", ext.target_dir.."/docs" },
    { "morph", ext.target_dir.."/morph" },
}
