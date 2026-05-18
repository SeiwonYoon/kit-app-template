local ext = get_current_extension_info()
project_ext(ext)
repo_build.prebuild_link {
    { "config", ext.target_dir .. "/config" },
    { "docs", ext.target_dir .. "/docs" },
    { "morph", ext.target_dir .. "/morph" },
    { "web", ext.target_dir .. "/web" },
}
