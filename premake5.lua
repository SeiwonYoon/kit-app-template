-- Shared build scripts from repo_build package.
repo_build = require("omni/repo/build")

-- Repo root
root = repo_build.get_abs_path(".")

-- Run repo_kit_tools premake5-kit that includes a bunch of Kit-friendly tooling configuration.
kit = require("_repo/deps/repo_kit_tools/kit-template/premake5-kit")
kit.setup_all({ cppdialect = "C++17" })


-- Registries config for testing
repo_build.prebuild_copy {
    { "%{root}/tools/deps/user.toml", "%{root}/_build/deps/user.toml" },
}

-- Apps: for each app generate batch files and a project based on kit files (define only existing kits)
define_app("moprh.base.kit")
define_app("morph.prim_info_viewer.kit")
define_app("morph.editor.kit")
define_app("morph.editor_streaming.kit")
define_app("my_company.my_usd_viewer.kit")
define_app("my_company.my_usd_viewer_streaming.kit")
define_app("morph.base_ui.kit")