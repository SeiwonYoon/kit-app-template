"""Extract 결과 layer 디스크 캐시 — ``USE_PREEXTRACTED_LAYERS`` SSOT.

False: Extract 직후 ``data/preextract/<prim>.usdc`` + ``manifest.json`` 덮어쓰기.
True : Flatten 없이 저장된 layer 를 읽어 attach (내용·이후 경로는 Extract 성공과 동일).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Optional

from .tbs_data_paths import resolve_local_data_path_or_default

_PRINT_PREFIX = "[TBS/Preextract]"
_DIR_REL = "preextract"
_MANIFEST_NAME = "manifest.json"
_SLUG_RE = re.compile(r"[^A-Za-z0-9_]+")


def preextract_dir() -> Path:
    d = resolve_local_data_path_or_default(_DIR_REL)
    d.mkdir(parents=True, exist_ok=True)
    return d


def manifest_path() -> Path:
    return preextract_dir() / _MANIFEST_NAME


def _slug_prim(prim_path: str) -> str:
    raw = (prim_path or "").strip().strip("/") or "anon"
    s = _SLUG_RE.sub("_", raw).strip("_")
    return s or "anon"


def layer_file_path(prim_path: str) -> Path:
    return preextract_dir() / f"{_slug_prim(prim_path)}.usdc"


def _load_manifest() -> Dict[str, Any]:
    p = manifest_path()
    if not p.is_file():
        return {"instances": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"{_PRINT_PREFIX} manifest read fail {p}: {exc}", flush=True)
        return {"instances": {}}
    if not isinstance(data, dict):
        return {"instances": {}}
    inst = data.get("instances")
    if not isinstance(inst, dict):
        data["instances"] = {}
    return data


def _write_manifest(data: Dict[str, Any]) -> None:
    p = manifest_path()
    try:
        p.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"{_PRINT_PREFIX} manifest write fail {p}: {exc}", flush=True)


def _result_to_entry(prim_path: str, result: Any) -> Dict[str, Any]:
    ok = bool(getattr(result, "ok", False) and getattr(result, "layer", None) is not None)
    return {
        "ok": ok,
        "kind": str(getattr(result, "kind", "") or "UNKNOWN"),
        "error": getattr(result, "error", None),
        "layer": f"{_slug_prim(prim_path)}.usdc" if ok else "",
        "discovered_asset_path": str(
            getattr(result, "discovered_asset_path", "") or ""
        ),
        "n_prims": int(getattr(result, "n_prims", 0) or 0),
        "n_attrs_total": int(getattr(result, "n_attrs_total", 0) or 0),
        "n_attrs_with_timesamples": int(
            getattr(result, "n_attrs_with_timesamples", 0) or 0
        ),
        "n_xform_op_ts": int(getattr(result, "n_xform_op_ts", 0) or 0),
        "n_skel_anim_ts": int(getattr(result, "n_skel_anim_ts", 0) or 0),
        "n_mesh_points_ts": int(getattr(result, "n_mesh_points_ts", 0) or 0),
        "n_visibility_ts": int(getattr(result, "n_visibility_ts", 0) or 0),
        "n_other_ts": int(getattr(result, "n_other_ts", 0) or 0),
        "n_omnigraph_prims": int(getattr(result, "n_omnigraph_prims", 0) or 0),
        "tc_min": float(getattr(result, "tc_min", 0.0) or 0.0),
        "tc_max": float(getattr(result, "tc_max", 0.0) or 0.0),
    }


def save_extract_result(prim_path: str, result: Any) -> None:
    """Extract 직후 호출 — 성공이면 layer 덮어쓰기, 실패면 항목만 갱신하고 이전 layer 삭제."""
    pp = (prim_path or "").strip()
    if not pp:
        return
    entry = _result_to_entry(pp, result)
    lp = layer_file_path(pp)
    layer = getattr(result, "layer", None) if entry["ok"] else None
    if layer is not None:
        try:
            ok_exp = bool(layer.Export(str(lp)))
        except Exception as exc:
            print(f"{_PRINT_PREFIX} Export FAIL prim={pp} path={lp}: {exc}", flush=True)
            ok_exp = False
        if not ok_exp:
            entry["ok"] = False
            entry["layer"] = ""
            entry["error"] = (entry.get("error") or "") + " layer Export 실패"
        else:
            print(f"{_PRINT_PREFIX} SAVE prim={pp} path={lp}", flush=True)
    else:
        try:
            if lp.is_file():
                lp.unlink()
        except Exception:
            pass
        print(
            f"{_PRINT_PREFIX} SAVE meta-only prim={pp} ok=False kind={entry.get('kind')!r}",
            flush=True,
        )

    data = _load_manifest()
    data.setdefault("instances", {})[pp] = entry
    _write_manifest(data)


def load_preextract_layer(prim_path: str) -> Optional[Any]:
    """저장된 usdc 를 anonymous layer 로 복제 (attach 경로와 동일하게 메모리 layer 사용)."""
    pp = (prim_path or "").strip()
    if not pp:
        return None
    data = _load_manifest()
    entry = (data.get("instances") or {}).get(pp)
    if not isinstance(entry, dict) or not entry.get("ok"):
        return None
    rel = str(entry.get("layer") or "").strip()
    path = (preextract_dir() / rel) if rel else layer_file_path(pp)
    if not path.is_file():
        print(f"{_PRINT_PREFIX} LOAD missing file prim={pp} path={path}", flush=True)
        return None
    try:
        from pxr import Sdf  # type: ignore
    except Exception as exc:
        print(f"{_PRINT_PREFIX} LOAD pxr.Sdf 불가: {exc}", flush=True)
        return None
    try:
        src = Sdf.Layer.FindOrOpen(str(path))
    except Exception as exc:
        print(f"{_PRINT_PREFIX} LOAD FindOrOpen FAIL prim={pp}: {exc}", flush=True)
        return None
    if src is None:
        print(f"{_PRINT_PREFIX} LOAD FindOrOpen None prim={pp}", flush=True)
        return None
    try:
        anon = Sdf.Layer.CreateAnonymous(f"tbs_preextract_{_slug_prim(pp)}")
        anon.TransferContent(src)
    except Exception as exc:
        print(f"{_PRINT_PREFIX} LOAD TransferContent FAIL prim={pp}: {exc}", flush=True)
        return None
    print(f"{_PRINT_PREFIX} LOAD prim={pp} path={path}", flush=True)
    return anon


def manifest_entry(prim_path: str) -> Optional[Dict[str, Any]]:
    pp = (prim_path or "").strip()
    if not pp:
        return None
    data = _load_manifest()
    ent = (data.get("instances") or {}).get(pp)
    return ent if isinstance(ent, dict) else None


def apply_entry_to_result(result: Any, entry: Dict[str, Any], *, prim_path: str) -> None:
    """manifest 통계를 ExtractResult 에 채움 (UI 로그가 Extract 성공과 같도록)."""
    result.root_prim_path = prim_path
    result.ok = bool(entry.get("ok"))
    result.kind = str(entry.get("kind") or "UNKNOWN")
    result.error = entry.get("error")
    result.discovered_asset_path = str(entry.get("discovered_asset_path") or "")
    result.n_prims = int(entry.get("n_prims") or 0)
    result.n_attrs_total = int(entry.get("n_attrs_total") or 0)
    result.n_attrs_with_timesamples = int(entry.get("n_attrs_with_timesamples") or 0)
    result.n_xform_op_ts = int(entry.get("n_xform_op_ts") or 0)
    result.n_skel_anim_ts = int(entry.get("n_skel_anim_ts") or 0)
    result.n_mesh_points_ts = int(entry.get("n_mesh_points_ts") or 0)
    result.n_visibility_ts = int(entry.get("n_visibility_ts") or 0)
    result.n_other_ts = int(entry.get("n_other_ts") or 0)
    result.n_omnigraph_prims = int(entry.get("n_omnigraph_prims") or 0)
    result.tc_min = float(entry.get("tc_min") or 0.0)
    result.tc_max = float(entry.get("tc_max") or 0.0)


__all__ = [
    "apply_entry_to_result",
    "layer_file_path",
    "load_preextract_layer",
    "manifest_entry",
    "preextract_dir",
    "save_extract_result",
]
