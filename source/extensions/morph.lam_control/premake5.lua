local ext = get_current_extension_info()
project_ext (ext)
repo_build.prebuild_copy {
    { "data", ext.target_dir.."/data" },
    { "docs", ext.target_dir.."/docs" },
    { "morph", ext.target_dir.."/morph" },
}
