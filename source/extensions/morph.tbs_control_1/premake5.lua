local ext = get_current_extension_info()
project_ext (ext)
repo_build.prebuild_link {
    { "docs", ext.target_dir.."/docs" },
    { "morph", ext.target_dir.."/morph" },
}
