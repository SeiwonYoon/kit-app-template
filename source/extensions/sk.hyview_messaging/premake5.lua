local ext = get_current_extension_info()
project_ext(ext)
repo_build.prebuild_copy {
    { "sk", ext.target_dir.."/sk" },
}
