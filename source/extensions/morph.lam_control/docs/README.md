# morph.lam_control

LAM 멀티 USD 독립 타임라인 재생 + LAM 시퀀스 편집기 + 외부 이벤트 러너 확장.

본 확장은 `morph.tbs_control_1` 와 **물리적으로 코드 격리** 되어 있으며, `omni.timeline` 도 사용하지 않습니다(`USD_Timeline_Spec.md` §3.1, REQ-002, REQ-004).

자세한 내용:
- **Jenkins / Linux 배포 (`lam/` csv·USD·JSON)** : `docs/LAM_Jenkins_Deployment_Guide.md`
- **유지보수 가이드 (수정 위치·실행 코드·Z/JSON/로그)** : `docs/LAM_Control_Maintenance_Guide.md`
- 본 확장 자체 사양: `docs/LAM_Spec.md`
- **장비 모델 (VTM / ATM / Chamber / Robot / Airlock + wafer 가시성 매핑)** : `docs/LAM_Equipment_Model.md`
- 변경 이력: `docs/CHANGELOG.md`
- 더 큰 설계 사양(REQ-002 ~ REQ-006): `../../morph.tbs_control_1/docs/USD_Timeline_Spec.md`
