"""E6 の相談窓口（エンジン v1.0 §2.5）。

安全対応をしたキャラの返答（messages.safety_triggered）の下に、Web は相談窓口のカードを出す。返答を受け取った
端末では ChatResponse.safety に窓口の一覧が入るが、履歴の読み込み・別の端末ではこの一覧を使う。
内容は `packages/prompts/safety/resources.ja.yaml`（起動時に読み込んで検証。
番号・受付時間は運用者が公開前に再確認する）。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.container import ServicesDep
from app.core.security import CurrentUserDep
from app.models.common import ERROR_RESPONSES
from app.models.dm import SafetyResource, SafetyResourcesResponse

router = APIRouter(prefix="/safety", tags=["safety"])


@router.get(
    "/resources",
    summary="相談窓口の一覧（E6）",
    description=(
        "自傷・希死念慮のシグナルに安全対応をした返答（messages.safety_triggered = true）の下に出す相談窓口。"
        "ChatResponse.safety.resources と同じ内容・順序。ログインしている利用者だけが取得できる。"
    ),
    responses=ERROR_RESPONSES,
)
async def get_safety_resources(user: CurrentUserDep, services: ServicesDep) -> SafetyResourcesResponse:
    del user  # 認証だけに使う（利用者ごとに内容は変わらない）
    return SafetyResourcesResponse(
        resources=[
            SafetyResource(name=r.name, phone=r.phone, hours=r.hours, url=r.url)
            for r in services.engine.safety.resources()
        ]
    )
