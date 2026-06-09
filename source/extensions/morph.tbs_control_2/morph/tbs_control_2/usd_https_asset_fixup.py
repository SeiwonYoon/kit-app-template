# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""
https 로 참조된 이미지가 **USD 속성**(Asset / AssetArray / String / Token)으로 드러난 경우,
로드 후 로컬 캐시로 복사해 세션 레이어에서 덮어써 RTX 가 파일을 열 수 있게 한다.

일부 재질/노드는 URL 을 문자열로 들고 있어 Asset 만 훑으면 잡히지 않는다.

MDL **소스 본문**에만 박힌 https 는 여전히 바꿀 수 없다(재질 재출력·로컬 씬 권장).
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import unquote, urlparse

import omni.client
import omni.kit.app as kit_app
from pxr import Sdf, Usd

try:
    from pxr import Tf
except Exception:
    Tf = None  # type: ignore

from .prim_utils import get_stage


def _env_skip_https_fixup() -> bool:
    try:
        v = str(os.environ.get("TBS_SKIP_HTTPS_TEXTURE_FIXUP", "") or "").strip().lower()
    except Exception:
        return False
    return v in ("1", "true", "yes", "on")


def _looks_image_url(url: str) -> bool:
    low = url.split("?", 1)[0].lower()
    return low.endswith(
        (".png", ".jpg", ".jpeg", ".exr", ".tif", ".tiff", ".tga", ".hdr", ".bmp", ".webp")
    )


def _collect_https_asset_edits(stage: Any) -> List[Tuple[str, str, str, str]]:
    """
    (prim_path, attr_name_or_special, https_url, kind) 목록.

    kind: ``asset`` | ``asset_arr`` | ``string`` | ``token``
    AssetArray 는 attr_name 이 ``foo::__idx__3`` 형태.
    연결만 있는 속성은 건너뛴다.
    """
    out: List[Tuple[str, str, str, str]] = []
    seen: Set[Tuple[str, str, str, str]] = set()
    try:
        r = Usd.PrimRange(stage.GetPseudoRoot())
    except Exception:
        return out

    for prim in r:
        try:
            if not prim.IsValid():
                continue
            ppath = str(prim.GetPath())
        except Exception:
            continue
        for attr in prim.GetAttributes():
            try:
                if not attr.IsValid():
                    continue
                if attr.HasAuthoredConnections():
                    continue
                name = attr.GetName()
                tn = attr.GetTypeName()
                tcode = Usd.TimeCode.Default()
                if tn == Sdf.ValueTypeNames.Asset:
                    v = attr.Get(tcode)
                    if not isinstance(v, Sdf.AssetPath):
                        continue
                    s = (v.path or "").strip()
                    if not s.lower().startswith("https://") or not _looks_image_url(s):
                        continue
                    key = (ppath, name, s, "asset")
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append((ppath, name, s, "asset"))
                elif tn == Sdf.ValueTypeNames.AssetArray:
                    arr = attr.Get(tcode)
                    if arr is None:
                        continue
                    for i, v in enumerate(arr):
                        if not isinstance(v, Sdf.AssetPath):
                            continue
                        s = (v.path or "").strip()
                        if not s.lower().startswith("https://") or not _looks_image_url(s):
                            continue
                        key = (ppath, f"{name}#{i}", s, "asset_arr")
                        if key in seen:
                            continue
                        seen.add(key)
                        out.append((ppath, f"{name}::__idx__{i}", s, "asset_arr"))
                elif tn == Sdf.ValueTypeNames.String:
                    v = attr.Get(tcode)
                    s = str(v or "").strip()
                    if not s.lower().startswith("https://") or not _looks_image_url(s):
                        continue
                    key = (ppath, name, s, "string")
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append((ppath, name, s, "string"))
                elif tn == Sdf.ValueTypeNames.Token:
                    v = attr.Get(tcode)
                    try:
                        s = str(v).strip() if v is not None else ""
                    except Exception:
                        s = ""
                    if not s.lower().startswith("https://") or not _looks_image_url(s):
                        continue
                    key = (ppath, name, s, "token")
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append((ppath, name, s, "token"))
            except Exception:
                continue
    return out


async def _download_url_to_file(url: str, dest: Path, timeout_s: float = 30.0) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        await asyncio.wait_for(omni.client.copy_async(url, dest.as_uri()), timeout=timeout_s)
        if dest.is_file() and dest.stat().st_size > 0:
            return True
    except Exception:
        pass
    try:
        loop = asyncio.get_running_loop()

        def _dl() -> None:
            urllib.request.urlretrieve(url, str(dest))

        await asyncio.wait_for(loop.run_in_executor(None, _dl), timeout=timeout_s)
        return dest.is_file()
    except Exception:
        return False


def _cache_path_for_url(url: str) -> Path:
    h = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    name = Path(unquote(urlparse(url).path)).name or "texture.bin"
    root = Path(tempfile.gettempdir()) / "morph_tbs_https_tex_cache" / h
    return root / name


async def apply_https_image_asset_fixup_after_load(load_path: str) -> Tuple[int, int, int]:
    """
    Returns (num_urls_copied, num_attrs_rewritten, num_edit_targets).

    load_path 가 https 가 아니거나 스킵 플래그면 (0,0,0).
    """
    if _env_skip_https_fixup():
        return (0, 0, 0)
    if not str(load_path or "").strip().lower().startswith("https://"):
        return (0, 0, 0)

    for _ in range(16):
        await kit_app.get_app().next_update_async()

    stage = get_stage()
    if stage is None:
        return (0, 0, 0)

    edits = _collect_https_asset_edits(stage)
    if not edits:
        try:
            print(
                "[TBS] https 로드: USD 레이어에 https 이미지 Asset 속성이 없습니다. "
                "rtx.mdltranslator 오류는 MDL 본문·서브레이어의 URL 때문일 수 있어, "
                "씬을 로컬로 복제하거나 재질의 텍스처 경로를 상대 경로로 다시보내야 합니다.",
                flush=True,
            )
        except Exception:
            pass
        return (0, 0, 0)

    url_to_local: Dict[str, str] = {}
    copied = 0
    for _, _, url, _ in edits:
        if url in url_to_local:
            continue
        dest = _cache_path_for_url(url)
        if await _download_url_to_file(url, dest):
            local = str(dest.resolve()).replace("\\", "/")
            url_to_local[url] = local
            copied += 1

    if not url_to_local:
        try:
            print("[TBS] https 텍스처 캐시 복사에 실패했습니다(네트워크·권한).", flush=True)
        except Exception:
            pass
        return (0, 0, 0)

    session = stage.GetSessionLayer()
    if session is None:
        return (copied, 0, 0)
    rewritten = 0
    try:
        edit_target = Usd.EditTarget(session)
        with Usd.EditContext(stage, edit_target):
            for ppath, aname, url, kind in edits:
                local = url_to_local.get(url)
                if not local:
                    continue
                try:
                    over = stage.OverridePrim(ppath)
                    if not over.IsValid():
                        continue
                    if kind == "asset_arr" and "::__idx__" in aname:
                        base, _, rest = aname.partition("::__idx__")
                        idx = int(rest)
                        attr = over.GetAttribute(base)
                        if not attr or not attr.IsValid():
                            continue
                        arr = attr.Get(Usd.TimeCode.Default())
                        if arr is None:
                            continue
                        lst = list(arr)
                        if 0 <= idx < len(lst) and isinstance(lst[idx], Sdf.AssetPath):
                            lst[idx] = Sdf.AssetPath(local)
                            attr.Set(lst)
                            rewritten += 1
                    elif kind == "string":
                        attr = over.GetAttribute(aname)
                        if not attr or not attr.IsValid():
                            attr = over.CreateAttribute(aname, Sdf.ValueTypeNames.String)
                        attr.Set(local)
                        rewritten += 1
                    elif kind == "token":
                        attr = over.GetAttribute(aname)
                        if not attr or not attr.IsValid():
                            attr = over.CreateAttribute(aname, Sdf.ValueTypeNames.Token)
                        if Tf is not None:
                            try:
                                attr.Set(Tf.Token(local))
                            except Exception:
                                attr.Set(local)
                        else:
                            attr.Set(local)
                        rewritten += 1
                    else:
                        attr = over.GetAttribute(aname)
                        if not attr or not attr.IsValid():
                            attr = over.CreateAttribute(aname, Sdf.ValueTypeNames.Asset)
                        attr.Set(Sdf.AssetPath(local))
                        rewritten += 1
                except Exception:
                    continue
    except Exception as e:
        try:
            print(f"[TBS] https 에셋 덮어쓰기 실패(EditContext): {e}", flush=True)
        except Exception:
            pass
        return (copied, 0, 0)

    if rewritten:
        try:
            print(
                f"[TBS] https 이미지 {copied}종 URL 을 로컬 캐시로 복사하고 USD 속성 {rewritten}개를 갱신했습니다. "
                "MDL 본문만의 URL은 여전히 재질 수정이 필요할 수 있습니다.",
                flush=True,
            )
        except Exception:
            pass
    return (copied, rewritten, 1)


async def schedule_https_asset_fixup_if_applicable(ext: Any, load_path: str) -> None:
    """open_stage 직후 백그라운드에서 비침습적으로 시도(서브레이어 지연을 위해 2회)."""
    try:
        copied, rew, _ = await apply_https_image_asset_fixup_after_load(load_path)
        if copied or rew:
            try:
                ext._tbs_https_texture_fixup_last = (copied, rew)
            except Exception:
                pass
        await asyncio.sleep(1.2)
        for _ in range(6):
            await kit_app.get_app().next_update_async()
        copied2, rew2, _ = await apply_https_image_asset_fixup_after_load(load_path)
        if copied2 or rew2:
            try:
                ext._tbs_https_texture_fixup_last = (
                    copied + copied2,
                    rew + rew2,
                )
            except Exception:
                pass
    except Exception:
        pass
