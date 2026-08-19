"""open_stage 전 — 외부 자산 경로를 뺀 복사본을 ``data/stripped_open/`` 에 둔다.

Extract 의 ``data/preextract/`` 와 같은 사용법:
False 로 로컬에서 생성 → 배포는 True 로 그 파일만 연다.
원본 USD 는 수정하지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from .lam_data_paths import resolve_local_data_path_or_default

_PRINT_PREFIX = "[LAM/UsdStrip]"
_DIR_REL = "stripped_open"
_MANIFEST_NAME = "manifest.json"
_SLUG_RE = re.compile(r"[^A-Za-z0-9_]+")


def stripped_open_dir() -> Path:
    d = resolve_local_data_path_or_default(_DIR_REL)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _manifest_path() -> Path:
    return stripped_open_dir() / _MANIFEST_NAME


def _load_manifest() -> Dict[str, Any]:
    p = _manifest_path()
    if not p.is_file():
        return {"version": 1, "roots": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"{_PRINT_PREFIX} manifest read fail: {exc}", flush=True)
        return {"version": 1, "roots": {}}
    if not isinstance(data, dict):
        return {"version": 1, "roots": {}}
    if not isinstance(data.get("roots"), dict):
        data["roots"] = {}
    return data


def _write_manifest(data: Dict[str, Any]) -> None:
    try:
        _manifest_path().write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"{_PRINT_PREFIX} manifest write fail: {exc}", flush=True)


def _cache_file_for(orig: str) -> str:
    base = os.path.splitext(os.path.basename(orig))[0]
    slug = _SLUG_RE.sub("_", base).strip("_") or "layer"
    digest = hashlib.md5(os.path.normpath(orig).encode("utf-8")).hexdigest()[:8]
    return str(stripped_open_dir() / f"{slug}_{digest}.usdc")


def _cache_rel(mapped_abs: str) -> str:
    return "./" + os.path.basename(mapped_abs)


def is_blocked_asset_path(raw: str) -> bool:
    """Kit 이 네트워크로 나가려 할 수 있는 경로 (로컬 파일은 False)."""
    s = (raw or "").strip().strip('"')
    if not s:
        return False
    low = s.lower()
    if low.startswith(("omniverse://", "omni://", "http://", "https://", "s3://")):
        return True
    if s.startswith("\\\\"):
        return True
    if s.startswith("//") and not low.startswith("file:"):
        return True
    if low.startswith("file://"):
        rest = s[7:]
        if rest.startswith("//") and not rest.startswith("///"):
            return True
    return False


def stripped_sidecar_path(root_path: str) -> str:
    """``data/stripped_open/`` 의 이 Master 용 열기 파일. 없으면 빈 문자열."""
    saved = resolve_prestripped_open_path(root_path)
    return saved or ""


def is_stripped_open_cache_path(path: str) -> bool:
    """``data/stripped_open/`` 안의 저장본인지."""
    raw = (path or "").strip()
    if not raw:
        return False
    try:
        p = Path(os.path.normpath(os.path.abspath(raw)))
        return p.parent.resolve() == stripped_open_dir().resolve()
    except Exception:
        return False


def apply_prestripped_open_stage_policy(src_path: str) -> Optional[str]:
    """True: 캐시 경로(없으면 None). False: 캐시 생성 후 원본 경로."""
    from .lam_sim_control_defaults import USE_PRESTRIPPED_OPEN_STAGE

    src = (src_path or "").strip()
    if not src:
        return None
    if is_stripped_open_cache_path(src):
        return src
    if bool(USE_PRESTRIPPED_OPEN_STAGE):
        return resolve_prestripped_open_path(src)
    try:
        prepare_open_path_without_external_assets(src)
    except Exception as exc:
        print(f"{_PRINT_PREFIX} cache prepare skip: {exc}", flush=True)
    return src


def isolate_prestripped_open_for_aux(src_path: str, fallback_src: str = "") -> Optional[str]:
    """화면2 True: 캐시 세트 전체를 임시 폴더에 복사해 연다.

    루트만 복제하면 같은 폴더의 상대 참조가 깨진다.
    ``src_path`` 캐시가 없으면 화면1 Master(내용 동일 복사본) 캐시를 쓴다.
    """
    from .lam_sim_control_defaults import USE_PRESTRIPPED_OPEN_STAGE

    if not bool(USE_PRESTRIPPED_OPEN_STAGE):
        return apply_prestripped_open_stage_policy(src_path)

    fb = (fallback_src or "").strip()
    if not fb:
        try:
            from . import lam_window as _tuw
            from .lam_data_paths import resolve_local_data_path

            raw = str(getattr(_tuw, "default_load_usd_path", "") or "").strip()
            fb = str(resolve_local_data_path(raw) or raw or "").strip()
        except Exception:
            fb = ""

    open_file = ""
    names: List[str] = []
    used = ""
    for cand in (src_path, fb):
        root = os.path.normpath(os.path.abspath((cand or "").strip()))
        if not root:
            continue
        entry = (_load_manifest().get("roots") or {}).get(os.path.basename(root))
        if not isinstance(entry, dict):
            continue
        of = str(entry.get("open_file") or "").strip()
        if not of:
            continue
        src = stripped_open_dir() / of
        if not src.is_file():
            continue
        files = entry.get("files")
        names = [str(x) for x in files] if isinstance(files, list) and files else [of]
        open_file = of
        used = root
        break
    if not open_file:
        return None

    tmp = tempfile.mkdtemp(prefix="morph_lam_stripped_aux_")
    cache = stripped_open_dir()
    for name in names:
        bn = os.path.basename(str(name))
        src = cache / bn
        if src.is_file():
            shutil.copy2(src, os.path.join(tmp, bn))
    out = os.path.normpath(os.path.join(tmp, os.path.basename(open_file)))
    if not os.path.isfile(out):
        return None
    print(
        f"{_PRINT_PREFIX} aux isolated prestrip open={out} src={src_path} via={used}",
        flush=True,
    )
    return out


def prepare_aux_open_stage_cache_if_needed(primary_path: str = "") -> None:
    """화면1 False 생성 시 화면2 Master 도 같은 폴더에 캐시."""
    from .lam_sim_control_defaults import USE_PRESTRIPPED_OPEN_STAGE

    if bool(USE_PRESTRIPPED_OPEN_STAGE):
        return
    try:
        from . import lam_window as _tuw
        from .lam_data_paths import resolve_local_data_path

        raw = str(getattr(_tuw, "default_aux_load_usd_path", "") or "").strip()
        aux = resolve_local_data_path(raw) if raw else None
        if not aux:
            return
        pri = os.path.normcase(os.path.normpath(os.path.abspath(primary_path or "")))
        aux_n = os.path.normcase(os.path.normpath(os.path.abspath(aux)))
        if pri and pri == aux_n:
            return
        prepare_open_path_without_external_assets(str(aux))
    except Exception as exc:
        print(f"{_PRINT_PREFIX} aux cache prepare skip: {exc}", flush=True)


def resolve_prestripped_open_path(root_path: str) -> Optional[str]:
    """배포 True — ``data/stripped_open`` 저장본. 경로가 바뀌면 Master 폴더로 rebase."""
    root = os.path.normpath(os.path.abspath((root_path or "").strip()))
    if not root:
        return None
    base = os.path.basename(root)
    entry = (_load_manifest().get("roots") or {}).get(base)
    if not isinstance(entry, dict):
        return None
    open_file = str(entry.get("open_file") or "").strip()
    if not open_file:
        return None
    src = stripped_open_dir() / open_file
    if not src.is_file():
        return None
    old_dir = os.path.normpath(str(entry.get("source_master_dir") or ""))
    new_dir = os.path.normpath(os.path.dirname(root))
    files = entry.get("files")
    names = [str(x) for x in files] if isinstance(files, list) and files else [open_file]
    if old_dir and os.path.normcase(old_dir) != os.path.normcase(new_dir):
        return _rebase_cache_to_master_dir(names, open_file, old_dir, new_dir)
    return str(src.resolve())


def _rebase_cache_to_master_dir(
    names: List[str], open_file: str, old_dir: str, new_dir: str
) -> Optional[str]:
    try:
        from pxr import Sdf  # type: ignore
    except Exception:
        return None
    tmp = tempfile.mkdtemp(prefix="lam_stripped_")
    cache = stripped_open_dir()
    for name in names:
        src = cache / os.path.basename(str(name))
        if src.is_file():
            shutil.copy2(src, os.path.join(tmp, src.name))
    root_tmp = os.path.join(tmp, os.path.basename(open_file))
    if not os.path.isfile(root_tmp):
        return None
    for name in names:
        p = os.path.join(tmp, os.path.basename(str(name)))
        if not os.path.isfile(p):
            continue
        layer = Sdf.Layer.FindOrOpen(p)
        if layer is None:
            continue
        _rebase_paths_in_layer(layer, old_dir, new_dir)
        try:
            layer.Save()
        except Exception:
            pass
    print(
        f"{_PRINT_PREFIX} rebase {old_dir} -> {new_dir} open={root_tmp}",
        flush=True,
    )
    return root_tmp


def _rebase_paths_in_layer(layer: Any, old_dir: str, new_dir: str) -> None:
    old_n = os.path.normpath(old_dir)
    new_n = os.path.normpath(new_dir)

    def _fix(s: str) -> str:
        raw = (s or "").strip()
        if not raw or is_blocked_asset_path(raw):
            return raw
        if raw.startswith("./") and not os.path.isabs(raw):
            return raw
        try:
            norm = os.path.normpath(raw)
        except Exception:
            return raw
        old_c = os.path.normcase(old_n)
        if os.path.normcase(norm).startswith(old_c):
            rest = norm[len(old_n) :].lstrip("\\/")
            return os.path.normpath(os.path.join(new_n, rest))
        return raw

    try:
        old_subs = [str(x) for x in list(layer.subLayerPaths or [])]
        new_subs = [_fix(x) for x in old_subs]
        if new_subs != old_subs:
            while len(layer.subLayerPaths):
                del layer.subLayerPaths[0]
            for p in new_subs:
                layer.subLayerPaths.append(p)
    except Exception:
        pass
    try:
        from pxr import Sdf  # type: ignore
    except Exception:
        Sdf = None
    try:
        roots = list(layer.rootPrims.values())
    except Exception:
        roots = []
    for root in roots:
        for spec in _iter_prim_specs(root):
            try:
                attrs = list(spec.attributes.values())
            except Exception:
                attrs = []
            for attr in attrs:
                try:
                    tname = str(getattr(attr, "typeName", "") or "")
                except Exception:
                    continue
                if "asset" not in tname.lower():
                    continue
                try:
                    d = attr.default
                    ns = _fix(_asset_str(d))
                    if ns and ns != _asset_str(d):
                        attr.default = Sdf.AssetPath(ns) if Sdf is not None else ns
                except Exception:
                    pass


def prepare_open_path_without_external_assets(root_path: str) -> str:
    """외부 참조를 뺀 로컬 sidecar 경로. 손댈 것이 없으면 원본 경로."""
    root = os.path.normpath(os.path.abspath((root_path or "").strip()))
    if not root or not os.path.isfile(root):
        return root_path
    try:
        from pxr import Sdf  # type: ignore
    except Exception as exc:
        print(f"{_PRINT_PREFIX} pxr 없음 — 원본 유지: {exc}", flush=True)
        return root_path

    try:
        local_layers, children = _collect_local_layers(Sdf, root)
        if not local_layers:
            return root_path
        needs_strip = {p: _layer_has_blocked(Sdf, p) for p in local_layers}
        needs_copy: Set[str] = {p for p, hit in needs_strip.items() if hit}
        changed = True
        while changed:
            changed = False
            for parent, chs in children.items():
                if parent in needs_copy:
                    continue
                if any(c in needs_copy for c in chs):
                    needs_copy.add(parent)
                    changed = True
        needs_copy.add(root)

        remap: Dict[str, str] = {}
        for orig in needs_copy:
            remap[orig] = _cache_file_for(orig)

        stripped_total = 0
        for orig in needs_copy:
            n = _write_sanitized_layer(Sdf, orig, remap[orig], remap)
            stripped_total += n

        out = remap[root]
        man = _load_manifest()
        man.setdefault("roots", {})
        man["roots"][os.path.basename(root)] = {
            "source_master_dir": os.path.dirname(root),
            "open_file": os.path.basename(out),
            "files": [os.path.basename(remap[p]) for p in needs_copy],
        }
        _write_manifest(man)
        print(
            f"{_PRINT_PREFIX} data/stripped_open copied={len(needs_copy)} "
            f"stripped_refs={stripped_total} open={out}",
            flush=True,
        )
        return out if os.path.isfile(out) else root_path
    except Exception as exc:
        print(f"{_PRINT_PREFIX} 실패 — 원본 유지: {exc}", flush=True)
        return root_path


def _collect_local_layers(Sdf: Any, root: str) -> Tuple[List[str], Dict[str, List[str]]]:
    seen: Set[str] = set()
    order: List[str] = []
    children: Dict[str, List[str]] = {}
    queue = [root]
    while queue:
        cur = queue.pop(0)
        cur_n = os.path.normpath(cur)
        if cur_n in seen:
            continue
        if not os.path.isfile(cur_n):
            continue
        seen.add(cur_n)
        order.append(cur_n)
        children[cur_n] = []
        layer = Sdf.Layer.FindOrOpen(cur_n)
        if layer is None:
            continue
        for ref in _layer_dep_strings(layer):
            if is_blocked_asset_path(ref):
                continue
            abs_p = _resolve_on_layer(layer, ref)
            if not abs_p or is_blocked_asset_path(abs_p):
                continue
            if not os.path.isfile(abs_p):
                continue
            if not _looks_like_usd(abs_p):
                continue
            abs_n = os.path.normpath(abs_p)
            children[cur_n].append(abs_n)
            if abs_n not in seen:
                queue.append(abs_n)
    return order, children


def _looks_like_usd(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in (
        ".usd",
        ".usda",
        ".usdc",
        ".usdz",
    )


def _layer_dep_strings(layer: Any) -> List[str]:
    out: List[str] = []
    try:
        out.extend(str(x) for x in list(layer.subLayerPaths or []))
    except Exception:
        pass
    try:
        fn = getattr(layer, "GetExternalReferences", None)
        if callable(fn):
            out.extend(str(x) for x in list(fn() or []))
    except Exception:
        pass
    return out


def _resolve_on_layer(layer: Any, asset_path: str) -> str:
    ap = (asset_path or "").strip()
    if not ap:
        return ""
    try:
        abs_p = layer.ComputeAbsolutePath(ap)
        if abs_p:
            return os.path.normpath(str(abs_p))
    except Exception:
        pass
    try:
        base = os.path.dirname(str(getattr(layer, "realPath", "") or ""))
        if base:
            return os.path.normpath(os.path.join(base, ap))
    except Exception:
        pass
    return ap


def _layer_has_blocked(Sdf: Any, path: str) -> bool:
    layer = Sdf.Layer.FindOrOpen(path)
    if layer is None:
        return False
    for ref in _layer_dep_strings(layer):
        if is_blocked_asset_path(ref):
            return True
        abs_p = _resolve_on_layer(layer, ref)
        if abs_p and is_blocked_asset_path(abs_p):
            return True
    n = _sanitize_layer_in_place(Sdf, layer, remap={}, dry_run=True, resolve_layer=layer)
    return n > 0


def _write_sanitized_layer(
    Sdf: Any, orig: str, dest: str, remap: Dict[str, str]
) -> int:
    src = Sdf.Layer.FindOrOpen(orig)
    if src is None:
        return 0
    if os.path.abspath(orig) != os.path.abspath(dest):
        if os.path.isfile(dest):
            try:
                os.remove(dest)
            except Exception:
                pass
        dst = Sdf.Layer.CreateNew(dest)
        if dst is None:
            return 0
        dst.TransferContent(src)
    else:
        dst = src
    n = _sanitize_layer_in_place(
        Sdf, dst, remap=remap, dry_run=False, resolve_layer=src
    )
    _bake_local_assets_absolute(Sdf, dst, src)
    try:
        dst.Save()
    except Exception as exc:
        print(f"{_PRINT_PREFIX} Save 실패 {dest}: {exc}", flush=True)
    return n


def _bake_local_assets_absolute(Sdf: Any, dst: Any, src: Any) -> None:
    """캐시가 data/ 에 있어도 텍스처는 원본 폴더의 절대 경로로 남긴다."""
    try:
        roots = list(dst.rootPrims.values())
    except Exception:
        return
    for root in roots:
        for spec in _iter_prim_specs(root):
            try:
                attrs = list(spec.attributes.values())
            except Exception:
                attrs = []
            for attr in attrs:
                try:
                    tname = str(getattr(attr, "typeName", "") or "")
                except Exception:
                    continue
                if "asset" not in tname.lower():
                    continue
                try:
                    s = _asset_str(attr.default)
                except Exception:
                    s = ""
                if not s or is_blocked_asset_path(s):
                    continue
                abs_p = _resolve_on_layer(src, s)
                if abs_p and os.path.isfile(abs_p) and not is_blocked_asset_path(abs_p):
                    try:
                        attr.default = Sdf.AssetPath(abs_p)
                    except Exception:
                        pass
            for key in ("info:mdl:sourceAsset", "sourceAsset"):
                try:
                    info = spec.GetInfo(key)
                except Exception:
                    continue
                s = _asset_str(info)
                if not s or is_blocked_asset_path(s):
                    continue
                abs_p = _resolve_on_layer(src, s)
                if abs_p and os.path.isfile(abs_p) and not is_blocked_asset_path(abs_p):
                    try:
                        spec.SetInfo(key, Sdf.AssetPath(abs_p))
                    except Exception:
                        pass


def _iter_prim_specs(spec: Any) -> Iterable[Any]:
    yield spec
    try:
        for child in spec.nameChildren.values():
            yield from _iter_prim_specs(child)
    except Exception:
        pass
    try:
        for vset in spec.variantSets.values():
            for variant in vset.variants.values():
                yield from _iter_prim_specs(variant)
    except Exception:
        pass


def _asset_str(val: Any) -> str:
    if val is None:
        return ""
    try:
        p = getattr(val, "path", None)
        if p is not None:
            return str(p or "")
    except Exception:
        pass
    return str(val or "")


def _sanitize_layer_in_place(
    Sdf: Any,
    layer: Any,
    *,
    remap: Dict[str, str],
    dry_run: bool,
    resolve_layer: Any = None,
) -> int:
    n = 0
    src = resolve_layer if resolve_layer is not None else layer
    n += _filter_sublayers(layer, remap, dry_run, src)
    try:
        roots = list(layer.rootPrims.values())
    except Exception:
        roots = []
    for root in roots:
        for spec in _iter_prim_specs(root):
            n += _filter_prim_arcs(Sdf, src, spec, remap, dry_run)
            n += _filter_prim_assets(Sdf, spec, dry_run)
    return n


def _kept_asset_path(
    resolve_layer: Any, asset_path: str, remap: Dict[str, str]
) -> Optional[str]:
    """차단 경로는 None. 캐시로 복사된 USD 는 ./파일, 그 외 로컬 파일은 절대 경로."""
    if is_blocked_asset_path(asset_path):
        return None
    abs_p = _resolve_on_layer(resolve_layer, asset_path)
    if abs_p and is_blocked_asset_path(abs_p):
        return None
    if abs_p:
        mapped = remap.get(os.path.normpath(abs_p), "")
        if mapped:
            return _cache_rel(mapped)
        if os.path.isfile(abs_p):
            return abs_p.replace("\\", "/")
    return asset_path


def _filter_sublayers(
    layer: Any, remap: Dict[str, str], dry_run: bool, resolve_layer: Any
) -> int:
    n = 0
    try:
        old = [str(x) for x in list(layer.subLayerPaths or [])]
    except Exception:
        return 0
    new: List[str] = []
    for item in old:
        kept = _kept_asset_path(resolve_layer, item, remap)
        if kept is None:
            n += 1
            continue
        new.append(kept)
    if dry_run:
        return n
    if new == old:
        return n
    try:
        while len(layer.subLayerPaths):
            del layer.subLayerPaths[0]
        for p in new:
            layer.subLayerPaths.append(p)
    except Exception:
        pass
    return n


def _rewrite_arc_path(
    layer: Any, asset_path: str, remap: Dict[str, str]
) -> Optional[str]:
    return _kept_asset_path(layer, asset_path, remap)


def _filter_prim_arcs(
    Sdf: Any, layer: Any, spec: Any, remap: Dict[str, str], dry_run: bool
) -> int:
    n = 0
    for list_name, ctor in (
        ("referenceList", getattr(Sdf, "Reference", None)),
        ("payloadList", getattr(Sdf, "Payload", None)),
    ):
        lst = getattr(spec, list_name, None)
        if lst is None or ctor is None:
            continue
        n += _filter_list_op(Sdf, layer, lst, ctor, remap, dry_run)
    return n


def _filter_list_op(
    Sdf: Any,
    layer: Any,
    list_op: Any,
    ctor: Any,
    remap: Dict[str, str],
    dry_run: bool,
) -> int:
    n = 0
    for attr in ("explicitItems", "addedItems", "prependedItems", "appendedItems"):
        try:
            cur = list(getattr(list_op, attr) or [])
        except Exception:
            continue
        new_items = []
        changed = False
        for item in cur:
            try:
                ap = str(getattr(item, "assetPath", "") or "")
            except Exception:
                new_items.append(item)
                continue
            rewritten = _rewrite_arc_path(layer, ap, remap)
            if rewritten is None:
                n += 1
                changed = True
                continue
            if rewritten != ap:
                changed = True
                try:
                    prim_path = getattr(item, "primPath", None)
                    lo = getattr(item, "layerOffset", None)
                    try:
                        new_items.append(ctor(rewritten, prim_path, lo))
                    except Exception:
                        new_items.append(ctor(assetPath=rewritten))
                except Exception:
                    new_items.append(item)
            else:
                new_items.append(item)
        if dry_run or not changed:
            continue
        try:
            setattr(list_op, attr, new_items)
        except Exception:
            pass
    return n


def _filter_prim_assets(Sdf: Any, spec: Any, dry_run: bool) -> int:
    n = 0
    empty = None
    try:
        empty = Sdf.AssetPath("")
    except Exception:
        empty = ""
    try:
        attrs = list(spec.attributes.values())
    except Exception:
        attrs = []
    for attr in attrs:
        try:
            tname = str(getattr(attr, "typeName", "") or "")
        except Exception:
            continue
        if "asset" not in tname.lower():
            continue
        try:
            default = attr.default
        except Exception:
            default = None
        if _asset_str(default) and is_blocked_asset_path(_asset_str(default)):
            n += 1
            if not dry_run:
                try:
                    attr.default = empty
                except Exception:
                    pass
        try:
            samples = dict(getattr(attr, "timeSamples", None) or {})
        except Exception:
            samples = {}
        for t, val in list(samples.items()):
            if _asset_str(val) and is_blocked_asset_path(_asset_str(val)):
                n += 1
                if not dry_run:
                    try:
                        samples[t] = empty
                        attr.timeSamples = samples
                    except Exception:
                        pass
    for key in ("info:mdl:sourceAsset", "sourceAsset"):
        try:
            info = spec.GetInfo(key)
        except Exception:
            continue
        if _asset_str(info) and is_blocked_asset_path(_asset_str(info)):
            n += 1
            if not dry_run:
                try:
                    spec.SetInfo(key, empty)
                except Exception:
                    pass
    return n


__all__ = [
    "apply_prestripped_open_stage_policy",
    "isolate_prestripped_open_for_aux",
    "is_blocked_asset_path",
    "is_stripped_open_cache_path",
    "prepare_aux_open_stage_cache_if_needed",
    "prepare_open_path_without_external_assets",
    "resolve_prestripped_open_path",
    "stripped_sidecar_path",
]
