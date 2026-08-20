"""open_stage 전 — 외부 자산을 빼고, 로컬 참조는 캐시 안으로 상대경로로 묶는다.

Extract 의 ``data/preextract/`` 와 같은 사용법:
모드 1 로 로컬에서 생성 → 모드 2 로 배포에서 ``data/stripped_open/`` 만 연다.
모드 0 은 원본만 연다 (캐시 없음).
원본 USD 는 수정하지 않는다.

캐시는 자체 완결이다. USD·텍스처·payload 등 로컬 파일을 같이 복사하고
참조는 ``./파일명`` 만 쓴다. 외부(네트워크) 참조는 비워 빨강이 된다.
개발자 PC 절대경로·rebase(tmp) 는 쓰지 않는다.
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

from .tbs_data_paths import resolve_local_data_path_or_default

_PRINT_PREFIX = "[TBS/UsdStrip]"
_DIR_REL = "stripped_open"
_MANIFEST_NAME = "manifest.json"
_MANIFEST_VERSION = 2
_SLUG_RE = re.compile(r"[^A-Za-z0-9_]+")


def stripped_open_dir() -> Path:
    d = resolve_local_data_path_or_default(_DIR_REL)
    d.mkdir(parents=True, exist_ok=True)
    return d


def clear_stripped_open_cache() -> None:
    """``data/stripped_open/`` 안 파일을 전부 지운다 (폴더는 유지). 모드 1 재생성용."""
    d = stripped_open_dir()
    removed = 0
    try:
        for child in list(d.iterdir()):
            try:
                if child.is_file() or child.is_symlink():
                    child.unlink()
                    removed += 1
                elif child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                    removed += 1
            except Exception as exc:
                print(f"{_PRINT_PREFIX} clear skip {child}: {exc}", flush=True)
    except Exception as exc:
        print(f"{_PRINT_PREFIX} clear fail: {exc}", flush=True)
        return
    print(f"{_PRINT_PREFIX} cleared data/stripped_open removed={removed}", flush=True)


def _manifest_path() -> Path:
    return stripped_open_dir() / _MANIFEST_NAME


def _load_manifest() -> Dict[str, Any]:
    p = _manifest_path()
    if not p.is_file():
        return {"version": _MANIFEST_VERSION, "roots": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"{_PRINT_PREFIX} manifest read fail: {exc}", flush=True)
        return {"version": _MANIFEST_VERSION, "roots": {}}
    if not isinstance(data, dict):
        return {"version": _MANIFEST_VERSION, "roots": {}}
    if not isinstance(data.get("roots"), dict):
        data["roots"] = {}
    return data


def _write_manifest(data: Dict[str, Any]) -> None:
    data = dict(data)
    data["version"] = _MANIFEST_VERSION
    try:
        _manifest_path().write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"{_PRINT_PREFIX} manifest write fail: {exc}", flush=True)


def _path_key(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def _cache_file_for(orig: str) -> str:
    stem, ext = os.path.splitext(os.path.basename(orig))
    slug = _SLUG_RE.sub("_", stem).strip("_") or "asset"
    digest = hashlib.md5(_path_key(orig).encode("utf-8")).hexdigest()[:8]
    if not ext:
        ext = ".usdc"
    return str(stripped_open_dir() / f"{slug}_{digest}{ext.lower()}")


def _cache_rel(mapped_abs: str) -> str:
    """캐시 폴더 기준 상대경로 (배포 Linux 포함 — 슬래시만 사용)."""
    return "./" + os.path.basename(mapped_abs).replace("\\", "/")


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


def prestripped_open_stage_mode() -> int:
    """0=원본만, 1=캐시생성+원본, 2=캐시만. 잘못된 값은 0."""
    from .sim_control_defaults import USE_PRESTRIPPED_OPEN_STAGE

    try:
        mode = int(USE_PRESTRIPPED_OPEN_STAGE)
    except Exception:
        mode = 0
    return mode if mode in (0, 1, 2) else 0


def apply_prestripped_open_stage_policy(src_path: str) -> Optional[str]:
    """모드에 따라 열 경로를 돌려준다. 2에서 캐시 없으면 None."""
    src = (src_path or "").strip()
    if not src:
        return None
    if is_stripped_open_cache_path(src):
        return src
    mode = prestripped_open_stage_mode()
    if mode == 2:
        return resolve_prestripped_open_path(src)
    if mode == 1:
        try:
            prepare_open_path_without_external_assets(src)
        except Exception as exc:
            print(f"{_PRINT_PREFIX} cache prepare skip: {exc}", flush=True)
    return src


def isolate_prestripped_open_for_aux(src_path: str, fallback_src: str = "") -> Optional[str]:
    """화면2: 모드 2면 캐시 세트 전체를 임시 폴더에 복사해 연다.

    상대경로 ``./파일`` 이므로 세트 전체를 같이 복사해야 한다.
    ``src_path`` 캐시가 없으면 화면1 Master 캐시를 쓴다.
    """
    mode = prestripped_open_stage_mode()
    if mode != 2:
        return apply_prestripped_open_stage_policy(src_path)

    fb = (fallback_src or "").strip()
    if not fb:
        try:
            from . import tbs_usd_window as _tuw
            from .tbs_data_paths import resolve_local_data_path

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

    tmp = tempfile.mkdtemp(prefix="morph_tbs_stripped_aux_")
    cache = stripped_open_dir()
    for name in names:
        bn = os.path.basename(str(name).replace("\\", "/"))
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
    """모드 1일 때 화면2 Master 도 같은 폴더에 캐시."""
    if prestripped_open_stage_mode() != 1:
        return
    try:
        from . import tbs_usd_window as _tuw
        from .tbs_data_paths import resolve_local_data_path

        raw = str(getattr(_tuw, "default_aux_load_usd_path", "") or "").strip()
        aux = resolve_local_data_path(raw) if raw else None
        if not aux:
            return
        pri = _path_key(os.path.abspath(primary_path or ""))
        aux_n = _path_key(os.path.abspath(aux))
        if pri and pri == aux_n:
            return
        prepare_open_path_without_external_assets(str(aux))
    except Exception as exc:
        print(f"{_PRINT_PREFIX} aux cache prepare skip: {exc}", flush=True)


def resolve_prestripped_open_path(root_path: str) -> Optional[str]:
    """배포 True — ``data/stripped_open`` 저장본을 그대로 연다 (rebase/tmp 없음)."""
    root = os.path.normpath(os.path.abspath((root_path or "").strip()))
    if not root:
        return None
    base = os.path.basename(root)
    entry = (_load_manifest().get("roots") or {}).get(base)
    if not isinstance(entry, dict):
        return None
    open_file = str(entry.get("open_file") or "").strip().replace("\\", "/")
    open_file = os.path.basename(open_file)
    if not open_file:
        return None
    src = stripped_open_dir() / open_file
    if not src.is_file():
        return None
    return str(src.resolve())


def prepare_open_path_without_external_assets(root_path: str) -> str:
    """외부 참조 제거 + 로컬 USD/텍스처를 캐시에 복사. 참조는 ``./파일``.

    외부(차단) 경로는 비워 모드 2에서 빨강이 되고,
    로컬 파일은 캐시로 복사되어 정상 표시되도록 한다.
    """
    root = os.path.normpath(os.path.abspath((root_path or "").strip()))
    if not root or not os.path.isfile(root):
        return root_path
    try:
        from pxr import Sdf  # type: ignore
    except Exception as exc:
        print(f"{_PRINT_PREFIX} pxr 없음 — 원본 유지: {exc}", flush=True)
        return root_path

    try:
        local_layers, _children = _collect_local_layers(Sdf, root)
        if not local_layers:
            return root_path

        asset_files: Set[str] = set()
        for lyr_path in local_layers:
            asset_files |= _collect_local_non_usd_assets(Sdf, lyr_path)

        # 자체 완결: 도달 가능한 로컬 USD + 텍스처 전부 복사
        needs_copy: Set[str] = set(local_layers) | set(asset_files)
        needs_copy.add(root)

        remap: Dict[str, str] = {}
        for orig in needs_copy:
            remap[_path_key(orig)] = _cache_file_for(orig)

        copied_assets = 0
        for orig in sorted(asset_files):
            dest = remap[_path_key(orig)]
            try:
                os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
                if os.path.abspath(orig) != os.path.abspath(dest):
                    shutil.copy2(orig, dest)
                copied_assets += 1
            except Exception as exc:
                print(f"{_PRINT_PREFIX} asset copy fail {orig}: {exc}", flush=True)

        stripped_total = 0
        for orig in local_layers:
            dest = remap[_path_key(orig)]
            n = _write_sanitized_layer(Sdf, orig, dest, remap)
            stripped_total += n

        out = remap[_path_key(root)]
        man = _load_manifest()
        man.setdefault("roots", {})
        # 상대경로만 — 절대 source_master_dir / rebase 금지
        man["roots"][os.path.basename(root)] = {
            "open_file": os.path.basename(out).replace("\\", "/"),
            "files": sorted(
                {
                    os.path.basename(remap[k]).replace("\\", "/")
                    for k in remap
                }
            ),
        }
        _write_manifest(man)
        print(
            f"{_PRINT_PREFIX} data/stripped_open "
            f"usd={len(local_layers)} assets={copied_assets} "
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
        key = _path_key(cur_n)
        if key in seen:
            continue
        if not os.path.isfile(cur_n):
            continue
        seen.add(key)
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
            if _path_key(abs_n) not in seen:
                queue.append(abs_n)
        # reference / payload 도 레이어 그래프에 포함
        for abs_p in _collect_arc_usd_paths(Sdf, layer):
            if _path_key(abs_p) not in seen:
                children[cur_n].append(abs_p)
                queue.append(abs_p)
    return order, children


def _collect_arc_usd_paths(Sdf: Any, layer: Any) -> List[str]:
    out: List[str] = []
    try:
        roots = list(layer.rootPrims.values())
    except Exception:
        return out
    for root in roots:
        for spec in _iter_prim_specs(root):
            for list_name in ("referenceList", "payloadList"):
                lst = getattr(spec, list_name, None)
                if lst is None:
                    continue
                for attr in (
                    "explicitItems",
                    "addedItems",
                    "prependedItems",
                    "appendedItems",
                ):
                    try:
                        items = list(getattr(lst, attr) or [])
                    except Exception:
                        continue
                    for item in items:
                        try:
                            ap = str(getattr(item, "assetPath", "") or "")
                        except Exception:
                            continue
                        if not ap or is_blocked_asset_path(ap):
                            continue
                        abs_p = _resolve_on_layer(layer, ap)
                        if (
                            abs_p
                            and os.path.isfile(abs_p)
                            and not is_blocked_asset_path(abs_p)
                            and _looks_like_usd(abs_p)
                        ):
                            out.append(os.path.normpath(abs_p))
    return out


def _collect_local_non_usd_assets(Sdf: Any, layer_path: str) -> Set[str]:
    """레이어가 가리키는 로컬 비-USD 파일(텍스처·mdl 등)."""
    found: Set[str] = set()
    layer = Sdf.Layer.FindOrOpen(layer_path)
    if layer is None:
        return found

    def _maybe_add(raw: str) -> None:
        if not raw or is_blocked_asset_path(raw):
            return
        abs_p = _resolve_on_layer(layer, raw)
        if not abs_p or is_blocked_asset_path(abs_p):
            return
        if not os.path.isfile(abs_p):
            return
        if _looks_like_usd(abs_p):
            return
        found.add(os.path.normpath(abs_p))

    try:
        fn = getattr(layer, "GetExternalReferences", None)
        if callable(fn):
            for ref in list(fn() or []):
                _maybe_add(str(ref))
    except Exception:
        pass

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
                    _maybe_add(_asset_str(attr.default))
                except Exception:
                    pass
                try:
                    samples = dict(getattr(attr, "timeSamples", None) or {})
                except Exception:
                    samples = {}
                for val in samples.values():
                    _maybe_add(_asset_str(val))
            for key in ("info:mdl:sourceAsset", "sourceAsset"):
                try:
                    _maybe_add(_asset_str(spec.GetInfo(key)))
                except Exception:
                    pass
    return found


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
    try:
        dst.Save()
    except Exception as exc:
        print(f"{_PRINT_PREFIX} Save 실패 {dest}: {exc}", flush=True)
    return n


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
            n += _filter_prim_assets(Sdf, src, spec, remap, dry_run)
    return n


def _kept_asset_path(
    resolve_layer: Any, asset_path: str, remap: Dict[str, str]
) -> Optional[str]:
    """차단·미해결 로컬은 None. 캐시에 있는 로컬은 ``./파일`` (절대경로 금지)."""
    if is_blocked_asset_path(asset_path):
        return None
    abs_p = _resolve_on_layer(resolve_layer, asset_path)
    if abs_p and is_blocked_asset_path(abs_p):
        return None
    if abs_p:
        mapped = remap.get(_path_key(abs_p), "")
        if mapped:
            return _cache_rel(mapped)
        if os.path.isfile(abs_p):
            # 캐시에 없으면 배포에서 깨지므로 비움
            return None
    # 파일이 없는 상대경로 등은 배포에서 따라가지 않도록 제거
    if asset_path and not is_blocked_asset_path(asset_path):
        # 이미 ./캐시파일 형태면 유지
        base = os.path.basename(asset_path.replace("\\", "/"))
        for dest in remap.values():
            if os.path.basename(dest.replace("\\", "/")) == base:
                return _cache_rel(dest)
    return None


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
            if not ap:
                new_items.append(item)
                continue
            rewritten = _kept_asset_path(layer, ap, remap)
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


def _filter_prim_assets(
    Sdf: Any,
    resolve_layer: Any,
    spec: Any,
    remap: Dict[str, str],
    dry_run: bool,
) -> int:
    n = 0
    empty = None
    try:
        empty = Sdf.AssetPath("")
    except Exception:
        empty = ""

    def _rewrite_val(raw: str) -> Tuple[Optional[Any], bool]:
        """(새 AssetPath 또는 empty, 변경/제거 여부)."""
        s = (raw or "").strip()
        if not s:
            return None, False
        kept = _kept_asset_path(resolve_layer, s, remap)
        if kept is None:
            return empty, True
        if kept == s:
            return None, False
        try:
            return Sdf.AssetPath(kept), True
        except Exception:
            return kept, True

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
        new_v, changed = _rewrite_val(_asset_str(default))
        if changed:
            n += 1
            if not dry_run and new_v is not None:
                try:
                    attr.default = new_v
                except Exception:
                    pass
        try:
            samples = dict(getattr(attr, "timeSamples", None) or {})
        except Exception:
            samples = {}
        sample_changed = False
        for t, val in list(samples.items()):
            new_s, ch = _rewrite_val(_asset_str(val))
            if ch:
                n += 1
                sample_changed = True
                if not dry_run and new_s is not None:
                    samples[t] = new_s
        if sample_changed and not dry_run:
            try:
                attr.timeSamples = samples
            except Exception:
                pass

    for key in ("info:mdl:sourceAsset", "sourceAsset"):
        try:
            info = spec.GetInfo(key)
        except Exception:
            continue
        new_v, changed = _rewrite_val(_asset_str(info))
        if changed:
            n += 1
            if not dry_run and new_v is not None:
                try:
                    spec.SetInfo(key, new_v)
                except Exception:
                    pass
    return n


__all__ = [
    "apply_prestripped_open_stage_policy",
    "clear_stripped_open_cache",
    "isolate_prestripped_open_for_aux",
    "is_blocked_asset_path",
    "is_stripped_open_cache_path",
    "prepare_aux_open_stage_cache_if_needed",
    "prepare_open_path_without_external_assets",
    "prestripped_open_stage_mode",
    "resolve_prestripped_open_path",
    "stripped_sidecar_path",
]
