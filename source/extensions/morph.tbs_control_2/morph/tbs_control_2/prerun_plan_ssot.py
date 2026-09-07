"""
프리런 일정 SSOT 플래너 (오프라인).

공정은 포트·우선순위 규칙으로 병렬 스케줄하고, 애니는 전역 1큐 직렬로 배치한다.
재생기는 이 모듈이 만든 ``PrerunPlan`` 을 시계에 맞춰 소비만 한다.

규칙·기준 예시: ``docs/tbs_control_2_prerun_ssot_plan_ko.md``
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# 동시 anim-ready 시 포트 우선순위 (낮을수록 먼저)
_PORT_ANIM_PRIORITY: Dict[str, int] = {
    "EP1": 10,
    "EP2": 20,
    "EP3": 30,
    "INOUT": 40,
    "BP1": 50,
    "BP2": 51,
    "BP3": 52,
    "BP4": 53,
}

KIND_OHT_TO_EP = "OHT_TO_EP"
KIND_OHT_TO_INOUT = "OHT_TO_INOUT"
KIND_INOUT_TO_BP = "INOUT_TO_BP"
KIND_BP_TO_EP = "BP_TO_EP"
KIND_REMOVE = "REMOVE"
KIND_FOUP = "FOUP"


@dataclass(frozen=True)
class PrerunPlanConfig:
    lot_count: int = 3
    ep_count: int = 2
    ebs_on: bool = True
    proc_oht_to_ep: float = 30.0
    proc_oht_to_inout: float = 30.0
    proc_inout_to_bp: float = 30.0
    proc_bp_to_ep: float = 30.0
    proc_remove: float = 30.0  # EP→OHT
    proc_foup: float = 10.0
    # True(기본): 공정별 sim_sequences JSON 길이를 읽어 반영.
    # False: 아래 anim_sec_fallback 을 모든 이송 애니에 동일 적용(문서 LOT3 예시용).
    anim_from_json: bool = True
    anim_sec_fallback: float = 10.0
    # True면 FOUP 전역 capacity=1 (엔진과 동일). 예시 LOT3는 비중첩.
    foup_global_serial: bool = True


@dataclass
class PlanProcess:
    uid: str
    kind: str
    lot_id: str
    port: str  # 주 포트 (애니 우선순위·표시)
    from_port: str = ""
    to_port: str = ""
    t_start: float = 0.0
    proc_sec: float = 0.0
    anim_sec: float = 0.0
    linked_json: str = ""
    t_anim_ready: float = 0.0
    t_anim_start: Optional[float] = None
    t_anim_end: Optional[float] = None
    # 포트 점유 반영 시각 — renewal offset 있으면 anim_start+offset, 없으면 anim_end
    t_port_sync: Optional[float] = None
    t_wall_end: Optional[float] = None
    needs_anim: bool = True


@dataclass(frozen=True)
class PlanAnimSlot:
    process_uid: str
    kind: str
    lot_id: str
    port: str
    t_start: float
    t_end: float


@dataclass(frozen=True)
class PortKeyframe:
    t: float
    ports: Dict[str, str]  # full panel map
    note: str = ""


@dataclass
class PrerunPlan:
    config: PrerunPlanConfig
    processes: List[PlanProcess] = field(default_factory=list)
    anims: List[PlanAnimSlot] = field(default_factory=list)
    port_keyframes: List[PortKeyframe] = field(default_factory=list)
    final_sim_time: float = 0.0


def _lot_id(n: int) -> str:
    return f"LOT{n:03d}"


def _empty_ports(ep_count: int, ebs_on: bool) -> Dict[str, str]:
    out = {f"EP{i}": "" for i in range(1, ep_count + 1)}
    out["INOUT"] = ""
    if ebs_on:
        bp_n = 4 if ep_count >= 3 else 3
        for i in range(1, bp_n + 1):
            out[f"BP{i}"] = ""
    return out


def _anim_priority(port: str, ready_t: float, uid: str) -> Tuple[float, int, str]:
    return (float(ready_t), int(_PORT_ANIM_PRIORITY.get(port.upper(), 90)), str(uid))


def _lead_ready(t_start: float, proc_sec: float, anim_sec: float) -> float:
    lead = max(0.0, float(proc_sec) - float(anim_sec))
    return float(t_start) + lead


class _Planner:
    def __init__(self, cfg: PrerunPlanConfig) -> None:
        self.cfg = cfg
        self.ports = _empty_ports(cfg.ep_count, cfg.ebs_on)
        self.remaining_lots = [_lot_id(i) for i in range(1, int(cfg.lot_count) + 1)]
        self.completed_lots: List[str] = []
        self.processes: List[PlanProcess] = []
        self.anims: List[PlanAnimSlot] = []
        self.keyframes: List[PortKeyframe] = []
        self._uid_seq = 0
        self._now = 0.0
        self._anim_free_at = 0.0
        self._anim_waiting: List[str] = []  # process uids waiting for anim slot
        self._active: Dict[str, PlanProcess] = {}
        self._foup_free_at = 0.0
        self._pending_foup: List[Tuple[str, str, float]] = []  # lot, ep, ready_t
        self._awaiting_remove: Dict[str, str] = {}  # ep -> lot
        # EP 잠금: 공정 진행 중 대상 포트
        self._ep_reserved: Dict[str, str] = {}  # ep -> process uid
        self._inout_reserved = False
        self._bp_reserved: Dict[str, str] = {}
        self._record_ports(0.0, "init")

    def _new_uid(self, kind: str) -> str:
        self._uid_seq += 1
        return f"{kind}_{self._uid_seq:04d}"

    def _record_ports(self, t: float, note: str) -> None:
        self.keyframes.append(PortKeyframe(t=float(t), ports=dict(self.ports), note=note))

    def _ep_list(self) -> List[str]:
        return [f"EP{i}" for i in range(1, int(self.cfg.ep_count) + 1)]

    def _bp_list(self) -> List[str]:
        if not self.cfg.ebs_on:
            return []
        n = 4 if int(self.cfg.ep_count) >= 3 else 3
        return [f"BP{i}" for i in range(1, n + 1)]

    def _has_inout_or_bp_lot(self) -> bool:
        if str(self.ports.get("INOUT") or "").strip():
            return True
        for bp in self._bp_list():
            if str(self.ports.get(bp) or "").strip():
                return True
        # INOUT→BP / BP→EP 진행 중(아직 포트 반영 전)도 버퍼 측 LOT으로 간주
        for p in self._active.values():
            if p.kind in (KIND_INOUT_TO_BP, KIND_BP_TO_EP) and p.t_wall_end is None:
                return True
        return False

    def _empty_eps(self) -> List[str]:
        out: List[str] = []
        for ep in self._ep_list():
            if self.ports.get(ep):
                continue
            if ep in self._ep_reserved:
                continue
            if ep in self._awaiting_remove:
                continue
            out.append(ep)
        return out

    def _first_empty_bp(self) -> Optional[str]:
        for bp in self._bp_list():
            if self.ports.get(bp):
                continue
            if bp in self._bp_reserved:
                continue
            return bp
        return None

    def _bp_with_lot(self) -> List[Tuple[str, str]]:
        rows: List[Tuple[str, str]] = []
        for bp in self._bp_list():
            lot = str(self.ports.get(bp) or "").strip()
            if lot and bp not in self._bp_reserved:
                rows.append((bp, lot))
        return rows

    def _reconcile_reservations(self) -> None:
        """
        활성 공정과 예약 잠금을 맞춘다.

        종료·선점 등으로 uid 예약만 남으면 REMOVE / INOUT→BP 가
        조건이 되어도 영구 보류되는 구조적 데드락이 난다.
        """
        active_uids = set(self._active.keys())
        self._ep_reserved = {
            ep: uid for ep, uid in list(self._ep_reserved.items()) if uid in active_uids
        }
        self._bp_reserved = {
            bp: uid for bp, uid in list(self._bp_reserved.items()) if uid in active_uids
        }
        inout_kinds = (KIND_INOUT_TO_BP, KIND_OHT_TO_INOUT)
        if not any(p.kind in inout_kinds for p in self._active.values()):
            self._inout_reserved = False

    def _resolve_anim(self, kind: str, from_port: str, to_port: str, port: str) -> tuple:
        fb = float(self.cfg.anim_sec_fallback)
        if not bool(self.cfg.anim_from_json):
            return fb, ""
        try:
            from .sim_sequence_duration import resolve_process_anim_sec

            return resolve_process_anim_sec(
                kind=kind,
                from_port=from_port,
                to_port=to_port,
                port=port,
                fallback_sec=fb,
            )
        except Exception:
            return fb, ""

    def _start_process(
        self,
        *,
        kind: str,
        lot_id: str,
        port: str,
        from_port: str,
        to_port: str,
        proc_sec: float,
        needs_anim: bool,
        t: float,
    ) -> PlanProcess:
        uid = self._new_uid(kind)
        anim = 0.0
        linked = ""
        if needs_anim:
            anim, linked = self._resolve_anim(kind, from_port, to_port, port)
            if anim <= 1e-9:
                anim = float(self.cfg.anim_sec_fallback)
        p = PlanProcess(
            uid=uid,
            kind=kind,
            lot_id=lot_id,
            port=port,
            from_port=from_port,
            to_port=to_port,
            t_start=float(t),
            proc_sec=float(proc_sec),
            anim_sec=anim,
            linked_json=str(linked or ""),
            t_anim_ready=_lead_ready(t, proc_sec, anim) if needs_anim else float(t) + float(proc_sec),
            needs_anim=needs_anim,
        )
        if needs_anim:
            self._anim_waiting.append(uid)
        else:
            p.t_wall_end = float(t) + float(proc_sec)
        self.processes.append(p)
        self._active[uid] = p
        return p

    def _try_start_all(self, t: float) -> None:
        """
        조건이 된 공정은 즉시 기동. 애니만 `_schedule_anims_through` 직렬 큐.

        우선순위(기동 시도 순):
          BP→EP → OHT→EP → REMOVE → INOUT→BP → OHT→INOUT → FOUP
        규칙:
          · 빈 EP + BP LOT → 먼저 BP→EP (같은 틱에 빈 BP 남으면 INOUT→BP 도 기동)
          · EP 가득 + INOUT LOT + 빈 BP → INOUT→BP 즉시
          · FOUP 종료 `_awaiting_remove` → 같은 틱 REMOVE
          · 예약은 활성 공정과 reconcile (유령 예약으로 기동 금지 방지)
        """
        cfg = self.cfg
        self._reconcile_reservations()
        # 1) BP→EP
        if cfg.ebs_on:
            for ep in list(self._empty_eps()):
                bps = self._bp_with_lot()
                if not bps:
                    break
                bp, lot = bps[0]
                p = self._start_process(
                    kind=KIND_BP_TO_EP,
                    lot_id=lot,
                    port=ep,
                    from_port=bp,
                    to_port=ep,
                    proc_sec=cfg.proc_bp_to_ep,
                    needs_anim=True,
                    t=t,
                )
                self._ep_reserved[ep] = p.uid
                self._bp_reserved[bp] = p.uid
        # 2) OHT→EP (버퍼 측 LOT 없을 때만)
        if not self._has_inout_or_bp_lot():
            for ep in list(self._empty_eps()):
                if not self.remaining_lots:
                    break
                lot = self.remaining_lots.pop(0)
                p = self._start_process(
                    kind=KIND_OHT_TO_EP,
                    lot_id=lot,
                    port=ep,
                    from_port="OHT",
                    to_port=ep,
                    proc_sec=cfg.proc_oht_to_ep,
                    needs_anim=True,
                    t=t,
                )
                self._ep_reserved[ep] = p.uid
        # 3) REMOVE — FOUP 완료·회수대기면 즉시 (예약 누수로 스킵되지 않게 reconcile 후)
        for ep, lot in list(self._awaiting_remove.items()):
            if ep in self._ep_reserved:
                continue
            p = self._start_process(
                kind=KIND_REMOVE,
                lot_id=lot,
                port=ep,
                from_port=ep,
                to_port="OHT",
                proc_sec=cfg.proc_remove,
                needs_anim=True,
                t=t,
            )
            self._ep_reserved[ep] = p.uid
            del self._awaiting_remove[ep]
        # 4) INOUT→BP
        # BP→EP 는 위에서 이미 빈 EP+BP LOT 을 선점함.
        # EP 가 가득 차 있거나, BP→EP 후 빈 BP 가 남으면 INOUT→BP 공정 즉시 기동
        # (애니는 직렬 큐). 별도 prefer 보류 없음 — 조건 충족 = 기동.
        if (
            cfg.ebs_on
            and not self._inout_reserved
            and str(self.ports.get("INOUT") or "").strip()
        ):
            bp = self._first_empty_bp()
            if bp:
                lot = str(self.ports.get("INOUT") or "")
                p = self._start_process(
                    kind=KIND_INOUT_TO_BP,
                    lot_id=lot,
                    port="INOUT",
                    from_port="INOUT",
                    to_port=bp,
                    proc_sec=cfg.proc_inout_to_bp,
                    needs_anim=True,
                    t=t,
                )
                self._inout_reserved = True
                self._bp_reserved[bp] = p.uid
        # 5) OHT→INOUT
        if (
            cfg.ebs_on
            and not self._inout_reserved
            and not str(self.ports.get("INOUT") or "").strip()
            and self.remaining_lots
        ):
            lot = self.remaining_lots.pop(0)
            p = self._start_process(
                kind=KIND_OHT_TO_INOUT,
                lot_id=lot,
                port="INOUT",
                from_port="OHT",
                to_port="INOUT",
                proc_sec=cfg.proc_oht_to_inout,
                needs_anim=True,
                t=t,
            )
            self._inout_reserved = True

        # FOUP 대기열
        self._try_start_foup(t)

    def _try_start_foup(self, t: float) -> None:
        if not self._pending_foup:
            return
        self._pending_foup.sort(key=lambda x: (x[2], _PORT_ANIM_PRIORITY.get(x[1], 90)))
        remain: List[Tuple[str, str, float]] = []
        for lot, ep, ready_t in self._pending_foup:
            start_t = max(float(t), float(ready_t))
            if self.cfg.foup_global_serial:
                start_t = max(start_t, float(self._foup_free_at))
            if start_t > float(t) + 1e-12:
                remain.append((lot, ep, ready_t))
                continue
            p = self._start_process(
                kind=KIND_FOUP,
                lot_id=lot,
                port=ep,
                from_port=ep,
                to_port=ep,
                proc_sec=self.cfg.proc_foup,
                needs_anim=False,
                t=start_t,
            )
            p.t_wall_end = start_t + float(self.cfg.proc_foup)
            if self.cfg.foup_global_serial:
                self._foup_free_at = float(p.t_wall_end)
        self._pending_foup = remain

    def _schedule_anims_through(self, horizon: float) -> None:
        """ready 된 애니를 horizon 까지 가능한 만큼 배치."""
        while True:
            candidates: List[PlanProcess] = []
            for uid in list(self._anim_waiting):
                p = self._active.get(uid)
                if p is None or not p.needs_anim or p.t_anim_start is not None:
                    continue
                if float(p.t_anim_ready) <= float(self._anim_free_at) + 1e-12 or float(
                    p.t_anim_ready
                ) <= float(horizon) + 1e-12:
                    # ready 시각이 anim_free 이전/동시면 큐에 넣을 수 있음
                    if float(p.t_anim_ready) <= max(float(self._anim_free_at), float(horizon)) + 1e-12:
                        candidates.append(p)
            if not candidates:
                break
            # anim_free 시점에 이미 ready 인 것만 선택; 없으면 다음 ready까지 점프
            free_at = float(self._anim_free_at)
            ready_now = [p for p in candidates if float(p.t_anim_ready) <= free_at + 1e-12]
            if not ready_now:
                next_ready = min(float(p.t_anim_ready) for p in candidates)
                if next_ready > float(horizon) + 1e-12:
                    break
                self._anim_free_at = next_ready
                continue
            ready_now.sort(key=lambda p: _anim_priority(p.port, p.t_anim_ready, p.uid))
            p = ready_now[0]
            t0 = max(free_at, float(p.t_anim_ready))
            if t0 > float(horizon) + 1e-12:
                break
            t1 = t0 + float(p.anim_sec)
            p.t_anim_start = t0
            p.t_anim_end = t1
            # renewal 마커가 있으면 그 시점에 포트 반영 (애니 종료보다 이름)
            p.t_port_sync = float(t1)
            try:
                bn = str(p.linked_json or "").strip()
                if bn:
                    from .sim_sequence_json import renewal_info_from_basename_or_path

                    has_r, off, _jp = renewal_info_from_basename_or_path(bn)
                    if has_r and off is not None and float(off) > 1e-9:
                        p.t_port_sync = float(t0) + float(off)
            except Exception:
                p.t_port_sync = float(t1)
            p.t_wall_end = max(float(p.t_start) + float(p.proc_sec), t1)
            self.anims.append(
                PlanAnimSlot(
                    process_uid=p.uid,
                    kind=p.kind,
                    lot_id=p.lot_id,
                    port=p.port,
                    t_start=t0,
                    t_end=t1,
                )
            )
            self._anim_waiting = [u for u in self._anim_waiting if u != p.uid]
            self._anim_free_at = t1

    def _apply_port_effects(self, p: PlanProcess, t: float) -> None:
        """포트 점유 키프레임만 반영 (renewal = t_port_sync 또는 애니 종료)."""
        kind = p.kind
        lot = p.lot_id
        if kind == KIND_OHT_TO_EP:
            ep = p.to_port
            self.ports[ep] = lot
            self._ep_reserved.pop(ep, None)
            self._record_ports(t, f"arrive {ep} {lot}")
        elif kind == KIND_OHT_TO_INOUT:
            self.ports["INOUT"] = lot
            self._inout_reserved = False
            self._record_ports(t, f"arrive INOUT {lot}")
        elif kind == KIND_INOUT_TO_BP:
            self.ports["INOUT"] = ""
            bp = p.to_port
            self.ports[bp] = lot
            self._inout_reserved = False
            self._bp_reserved.pop(bp, None)
            self._record_ports(t, f"move INOUT→{bp} {lot}")
        elif kind == KIND_BP_TO_EP:
            bp = p.from_port
            ep = p.to_port
            self.ports[bp] = ""
            self.ports[ep] = lot
            self._bp_reserved.pop(bp, None)
            self._ep_reserved.pop(ep, None)
            self._record_ports(t, f"move {bp}→{ep} {lot}")
        elif kind == KIND_REMOVE:
            ep = p.from_port
            self.ports[ep] = ""
            self._ep_reserved.pop(ep, None)
            self.completed_lots.append(lot)
            self._record_ports(t, f"remove {ep} {lot}")

    def _queue_post_arrival_foup(self, p: PlanProcess, t: float) -> None:
        """안착 완료(애니 종료) 후에만 FOUP — renewal 시점 조기 FOUP/REMOVE 금지."""
        if p.kind in (KIND_OHT_TO_EP, KIND_BP_TO_EP):
            ep = p.to_port
            lot = p.lot_id
            if str(self.ports.get(ep) or "") == lot:
                self._pending_foup.append((lot, ep, float(t)))

    def _on_foup_end(self, p: PlanProcess, t: float) -> None:
        ep = p.port
        lot = p.lot_id
        # FOUP 종료 → REMOVE 대기 → 같은 시각에 기동될 수 있게 표시
        if str(self.ports.get(ep) or "") == lot:
            self._awaiting_remove[ep] = lot

    def _finish_process(self, p: PlanProcess) -> None:
        uid = p.uid
        self._active.pop(uid, None)
        # 포트 반영 전에 종료되어도 예약이 남지 않게 정리 (기동 데드락 방지)
        self._ep_reserved = {
            ep: u for ep, u in list(self._ep_reserved.items()) if u != uid
        }
        self._bp_reserved = {
            bp: u for bp, u in list(self._bp_reserved.items()) if u != uid
        }
        if p.kind in (KIND_INOUT_TO_BP, KIND_OHT_TO_INOUT):
            if not any(
                x.kind in (KIND_INOUT_TO_BP, KIND_OHT_TO_INOUT)
                for x in self._active.values()
            ):
                self._inout_reserved = False

    def run(self) -> PrerunPlan:
        t = 0.0
        self._try_start_all(t)
        safety = 0
        while safety < 10000:
            safety += 1
            # 애니를 "다음 의미 있는 시각"까지 배치하기 위해 후보 시각 수집
            times: List[float] = []
            for p in list(self._active.values()):
                if p.needs_anim and p.t_anim_start is None:
                    times.append(float(p.t_anim_ready))
                if p.t_anim_end is not None and p.uid in self._active:
                    # 아직 포트 반영 전이면 anim_end 이벤트 필요 — 아래에서 처리
                    pass
                if p.t_wall_end is not None:
                    times.append(float(p.t_wall_end))
            if self._anim_waiting:
                times.append(float(self._anim_free_at))
                for uid in self._anim_waiting:
                    p = self._active.get(uid)
                    if p:
                        times.append(float(p.t_anim_ready))
            for _lot, _ep, ready_t in self._pending_foup:
                times.append(max(float(ready_t), float(self._foup_free_at)))

            # 먼저 현재 큐에서 가능한 애니 배치 (무한 전방)
            self._schedule_anims_through(1e12)

            # 다음 이벤트: port_sync / anim_end(FOUP) / wall_end / foup_end
            events: List[Tuple[float, str, str]] = []
            for p in list(self._active.values()):
                if p.needs_anim and getattr(p, "_port_applied", False) is not True:
                    sync_t = p.t_port_sync if p.t_port_sync is not None else p.t_anim_end
                    if sync_t is not None:
                        events.append((float(sync_t), "port_sync", p.uid))
                if (
                    p.needs_anim
                    and p.t_anim_end is not None
                    and getattr(p, "_settle_done", False) is not True
                ):
                    events.append((float(p.t_anim_end), "anim_end", p.uid))
                if p.kind == KIND_FOUP and p.t_wall_end is not None and getattr(p, "_foup_done", False) is not True:
                    events.append((float(p.t_wall_end), "foup_end", p.uid))
                elif (not p.needs_anim) and p.t_wall_end is not None and p.kind != KIND_FOUP:
                    if getattr(p, "_done", False) is not True:
                        events.append((float(p.t_wall_end), "wall_end", p.uid))
                if p.needs_anim and p.t_wall_end is not None and getattr(p, "_done", False) is not True:
                    # wall_end 는 포트 반영 이후에만
                    if getattr(p, "_port_applied", False) is True:
                        events.append((float(p.t_wall_end), "wall_end", p.uid))

            if not events and not self._anim_waiting and not self._pending_foup and not self._active:
                # 남은 LOT / 포트 적재 / 회수대기가 있으면 기동 재시도 후 종료 판단
                self._try_start_all(t)
                if self._active or self._anim_waiting or self._pending_foup:
                    continue
                leftover = any(str(v or "").strip() for v in self.ports.values())
                if self.remaining_lots or self._awaiting_remove or leftover:
                    # 한 번 더 시도 후에도 못 움직이면 데드락 — 안전 종료
                    before_n = len(self.processes)
                    self._try_start_all(t)
                    if len(self.processes) == before_n and not self._active:
                        break
                    continue
                break

            if not events:
                # 애니만 남았거나 FOUP pending
                self._try_start_foup(t)
                self._schedule_anims_through(1e12)
                leftover = any(str(v or "").strip() for v in self.ports.values())
                if (
                    not self._active
                    and not self._pending_foup
                    and not self.remaining_lots
                    and not self._awaiting_remove
                    and not leftover
                ):
                    break
                # force progress: jump to next pending foup or anim
                nxt: List[float] = []
                for p in self._active.values():
                    if p.needs_anim and not getattr(p, "_port_applied", False):
                        sync_t = p.t_port_sync if p.t_port_sync is not None else p.t_anim_end
                        if sync_t is not None:
                            nxt.append(float(sync_t))
                    if (
                        p.needs_anim
                        and p.t_anim_end is not None
                        and not getattr(p, "_settle_done", False)
                    ):
                        nxt.append(float(p.t_anim_end))
                    if p.kind == KIND_FOUP and p.t_wall_end and not getattr(p, "_foup_done", False):
                        nxt.append(float(p.t_wall_end))
                for _lot, _ep, ready_t in self._pending_foup:
                    nxt.append(max(float(ready_t), float(self._foup_free_at)))
                if not nxt:
                    if leftover or self.remaining_lots or self._awaiting_remove:
                        self._try_start_all(t)
                        if not self._active:
                            break
                        continue
                    break
                t = min(nxt)
                continue

            # 같은 시각 이벤트를 모두 반영한 뒤에만 애니 큐를 진행한다.
            # port_sync → anim_end → foup_end → wall_end
            events.sort(
                key=lambda e: (
                    e[0],
                    0
                    if e[1] == "port_sync"
                    else 1
                    if e[1] == "anim_end"
                    else 2
                    if e[1] == "foup_end"
                    else 3,
                    e[2],
                )
            )
            t = float(events[0][0])
            batch = [e for e in events if abs(float(e[0]) - t) <= 1e-9]
            for _t_ev, typ, uid in batch:
                p = self._active.get(uid)
                if p is None:
                    continue
                if typ == "port_sync":
                    if getattr(p, "_port_applied", False):
                        continue
                    self._apply_port_effects(p, t)
                    setattr(p, "_port_applied", True)
                    # renewal 없이 sync==anim_end 이면 여기서 settle(FOUP)
                    if p.t_anim_end is not None and abs(float(p.t_anim_end) - t) <= 1e-9:
                        if not getattr(p, "_settle_done", False):
                            self._queue_post_arrival_foup(p, t)
                            setattr(p, "_settle_done", True)
                    if p.t_wall_end is not None and abs(float(p.t_wall_end) - t) <= 1e-9:
                        setattr(p, "_done", True)
                        self._finish_process(p)
                elif typ == "anim_end":
                    if getattr(p, "_settle_done", False):
                        continue
                    if not getattr(p, "_port_applied", False):
                        self._apply_port_effects(p, t)
                        setattr(p, "_port_applied", True)
                    self._queue_post_arrival_foup(p, t)
                    setattr(p, "_settle_done", True)
                    if p.t_wall_end is not None and abs(float(p.t_wall_end) - t) <= 1e-9:
                        setattr(p, "_done", True)
                        self._finish_process(p)
                elif typ == "foup_end":
                    if getattr(p, "_foup_done", False):
                        continue
                    self._on_foup_end(p, t)
                    setattr(p, "_foup_done", True)
                    setattr(p, "_done", True)
                    self._finish_process(p)
                elif typ == "wall_end":
                    if getattr(p, "_done", False):
                        continue
                    setattr(p, "_done", True)
                    self._finish_process(p)
            self._try_start_all(t)
            self._schedule_anims_through(1e12)

        final_t = 0.0
        for p in self.processes:
            for x in (p.t_wall_end, p.t_anim_end, p.t_start):
                if x is not None:
                    final_t = max(final_t, float(x))
        for a in self.anims:
            final_t = max(final_t, float(a.t_end))
        return PrerunPlan(
            config=self.cfg,
            processes=self.processes,
            anims=self.anims,
            port_keyframes=self.keyframes,
            final_sim_time=final_t,
        )


def build_prerun_plan(cfg: Optional[PrerunPlanConfig] = None) -> PrerunPlan:
    """설정으로 전체 프리런 일정을 계산한다."""
    return _Planner(cfg or PrerunPlanConfig()).run()


def format_plan_timetable(plan: PrerunPlan) -> str:
    lines = [
        f"# prerun plan  final={plan.final_sim_time:.1f}s  lots={plan.config.lot_count}  ep={plan.config.ep_count}",
        "",
        "## processes",
    ]
    for p in plan.processes:
        anim = (
            f" anim={p.t_anim_start:.1f}-{p.t_anim_end:.1f}"
            if p.t_anim_start is not None and p.t_anim_end is not None
            else " anim=-"
        )
        wall = f"{p.t_wall_end:.1f}" if p.t_wall_end is not None else "-"
        lines.append(
            f"  {p.t_start:6.1f}..{wall:>6}  {p.kind:<14} {p.lot_id} {p.from_port}->{p.to_port}{anim}"
        )
    lines.append("")
    lines.append("## anims (serial queue)")
    for a in plan.anims:
        lines.append(f"  {a.t_start:6.1f}-{a.t_end:6.1f}  {a.kind:<14} {a.lot_id} @{a.port}")
    return "\n".join(lines)


def assert_example_lot3_schedule(plan: Optional[PrerunPlan] = None) -> None:
    """docs 기준 예시(LOT3·전부 30/10·애니10 고정·EP2) 핵심 타임스탬프 검증."""
    plan = plan or build_prerun_plan(
        PrerunPlanConfig(anim_from_json=False, anim_sec_fallback=10.0)
    )
    by_kind_lot = {(p.kind, p.lot_id): p for p in plan.processes}

    def _must(kind: str, lot: str) -> PlanProcess:
        p = by_kind_lot.get((kind, lot))
        if p is None:
            raise AssertionError(f"missing process {kind} {lot}")
        return p

    # 동시 시작 3개
    for kind, lot in (
        (KIND_OHT_TO_EP, "LOT001"),
        (KIND_OHT_TO_EP, "LOT002"),
        (KIND_OHT_TO_INOUT, "LOT003"),
    ):
        p = _must(kind, lot)
        if abs(p.t_start - 0.0) > 1e-6:
            raise AssertionError(f"{kind} {lot} t_start={p.t_start} want 0")

    a0 = plan.anims[0]
    if a0.kind != KIND_OHT_TO_EP or a0.port != "EP1" or abs(a0.t_start - 20.0) > 1e-6:
        raise AssertionError(f"first anim want OHT→EP1@20 got {a0}")
    a1 = plan.anims[1]
    if a1.port != "EP2" or abs(a1.t_start - 30.0) > 1e-6:
        raise AssertionError(f"second anim want EP2@30 got {a1}")
    a2 = plan.anims[2]
    if a2.kind != KIND_OHT_TO_INOUT or abs(a2.t_start - 40.0) > 1e-6:
        raise AssertionError(f"third anim want OHT→INOUT@40 got {a2}")

    rem1 = _must(KIND_REMOVE, "LOT001")
    if abs(rem1.t_start - 40.0) > 1e-6:
        raise AssertionError(f"REMOVE LOT001 start want 40 got {rem1.t_start}")

    inout_bp = _must(KIND_INOUT_TO_BP, "LOT003")
    if abs(inout_bp.t_start - 50.0) > 1e-6:
        raise AssertionError(f"INOUT→BP start want 50 got {inout_bp.t_start}")

    # 애니 큐: REMOVE EP1 @60, REMOVE EP2 @70, INOUT→BP @80
    rem_anims = [a for a in plan.anims if a.kind == KIND_REMOVE]
    if len(rem_anims) < 2:
        raise AssertionError("expected >=2 REMOVE anims")
    if abs(rem_anims[0].t_start - 60.0) > 1e-6 or rem_anims[0].port != "EP1":
        raise AssertionError(f"REMOVE anim0 want EP1@60 got {rem_anims[0]}")
    if abs(rem_anims[1].t_start - 70.0) > 1e-6 or rem_anims[1].port != "EP2":
        raise AssertionError(f"REMOVE anim1 want EP2@70 got {rem_anims[1]}")

    ib_anims = [a for a in plan.anims if a.kind == KIND_INOUT_TO_BP]
    if not ib_anims or abs(ib_anims[0].t_start - 80.0) > 1e-6:
        raise AssertionError(f"INOUT→BP anim want @80 got {ib_anims[:1]}")

    bp_ep = [p for p in plan.processes if p.kind == KIND_BP_TO_EP and p.lot_id == "LOT003"]
    if not bp_ep or abs(bp_ep[0].t_start - 90.0) > 1e-6:
        raise AssertionError(f"BP→EP LOT003 start want 90 got {bp_ep[:1]}")


def assert_process_start_rules() -> None:
    """
    #4 공정 즉시 기동 규칙:
      · EP 가득 + INOUT LOT + 빈 BP → INOUT→BP 즉시 (prefer 보류 금지)
      · 빈 EP + BP LOT → BP→EP 우선, INOUT→BP 보류
      · stale ep_reserved 가 있어도 FOUP 대기 REMOVE 기동
    """
    # --- A) EP full, INOUT occupied, empty BP → INOUT→BP ---
    pl = _Planner(
        PrerunPlanConfig(
            lot_count=0,
            ep_count=2,
            ebs_on=True,
            anim_from_json=False,
            anim_sec_fallback=10.0,
            proc_inout_to_bp=30.0,
        )
    )
    pl.remaining_lots = []
    pl.ports["EP1"] = "LOT001"
    pl.ports["EP2"] = "LOT002"
    pl.ports["INOUT"] = "LOT003"
    pl.ports["BP1"] = ""
    pl.ports["BP2"] = ""
    pl.ports["BP3"] = ""
    # 유령 예약 — reconcile 로 풀려야 함
    pl._inout_reserved = True
    pl._ep_reserved["EP1"] = "GONE_UID"
    pl._try_start_all(100.0)
    ib = [p for p in pl.processes if p.kind == KIND_INOUT_TO_BP]
    if not ib or abs(ib[0].t_start - 100.0) > 1e-6:
        raise AssertionError(f"EP-full INOUT→BP want @100 got {ib[:1]}")
    if any(p.kind == KIND_BP_TO_EP for p in pl.processes):
        raise AssertionError("EP-full 에서는 BP→EP 가 기동되면 안 됨")

    # --- B) empty EP + BP lot → BP→EP 는 반드시 기동 (같은 틱 INOUT→BP 는 빈 BP 있으면 병렬 OK) ---
    pl2 = _Planner(
        PrerunPlanConfig(
            lot_count=0,
            ep_count=2,
            ebs_on=True,
            anim_from_json=False,
            anim_sec_fallback=10.0,
        )
    )
    pl2.remaining_lots = []
    pl2.ports["EP1"] = ""
    pl2.ports["EP2"] = "LOT002"
    pl2.ports["INOUT"] = "LOT003"
    pl2.ports["BP1"] = "LOT004"
    pl2.ports["BP2"] = ""
    pl2._try_start_all(50.0)
    if not any(p.kind == KIND_BP_TO_EP and p.lot_id == "LOT004" for p in pl2.processes):
        raise AssertionError("empty EP+BP LOT → BP→EP 필수")
    # 빈 EP 를 BP→EP 가 선점한 뒤에도 빈 BP2 가 있으면 INOUT→BP 병렬 기동은 허용
    if not any(p.kind == KIND_INOUT_TO_BP and p.lot_id == "LOT003" for p in pl2.processes):
        raise AssertionError("빈 BP 남으면 INOUT→BP 도 같은 틱 기동")

    # --- C) stale reserve 가 REMOVE 를 막지 않음 ---
    pl3 = _Planner(
        PrerunPlanConfig(
            lot_count=0,
            ep_count=2,
            ebs_on=True,
            anim_from_json=False,
            anim_sec_fallback=10.0,
            proc_remove=30.0,
        )
    )
    pl3.remaining_lots = []
    pl3.ports["EP1"] = "LOT009"
    pl3.ports["EP2"] = "LOT002"
    pl3._awaiting_remove["EP1"] = "LOT009"
    pl3._ep_reserved["EP1"] = "STALE_REMOVE_BLOCKER"
    pl3._try_start_all(70.0)
    rem = [p for p in pl3.processes if p.kind == KIND_REMOVE and p.lot_id == "LOT009"]
    if not rem or abs(rem[0].t_start - 70.0) > 1e-6:
        raise AssertionError(f"FOUP대기 REMOVE want @70 got {rem[:1]}")


__all__ = [
    "PrerunPlanConfig",
    "PlanProcess",
    "PlanAnimSlot",
    "PortKeyframe",
    "PrerunPlan",
    "build_prerun_plan",
    "format_plan_timetable",
    "assert_example_lot3_schedule",
    "assert_process_start_rules",
    "KIND_OHT_TO_EP",
    "KIND_OHT_TO_INOUT",
    "KIND_INOUT_TO_BP",
    "KIND_BP_TO_EP",
    "KIND_REMOVE",
    "KIND_FOUP",
]
