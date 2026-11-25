"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。"""
from __future__ import annotations

from app.routers import resource, source_handover

# 专用路由必须排在通用 `/{module}` 之前，避免动态模块路径抢先匹配。
ROUTERS = [source_handover, resource]
