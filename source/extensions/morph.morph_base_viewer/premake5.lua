-- Use folder name to build extension name and tag.
local ext = get_current_extension_info()

project_ext (ext)

repo_build.prebuild_link {
    { "docs", ext.target_dir.."/docs" },
    { "layouts", ext.target_dir.."/layouts" },
    { "morph", ext.target_dir.."/morph" },
}
