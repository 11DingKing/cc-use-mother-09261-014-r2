"""JSON API 适配器。"""
from urllib.parse import parse_qs, urlsplit


def _param(params, name):
    values = params.get(name)
    return values[0] if values else None


def dispatch(flow, method, path, body=None):
    body = body or {}
    split = urlsplit(path)
    route, params = split.path, parse_qs(split.query)
    parts = [p for p in route.split("/") if p]
    if method == "POST" and route == "/cases":
        case_id, actor = body.get("id"), body.get("actor")
        if not case_id or not actor:
            return 400, {"error": "missing_field"}
        try:
            return 201, flow.create(case_id, actor, body.get("idempotency_key")).__dict__
        except ValueError as exc:
            return 409, {"error": str(exc)}
    if method == "POST" and len(parts) == 3 and parts[0] == "cases" and parts[2] == "move":
        state, actor = body.get("state"), body.get("actor")
        if not state or not actor:
            return 400, {"error": "missing_field"}
        try:
            return 200, flow.move(parts[1], state, actor).__dict__
        except KeyError:
            return 404, {"error": "unknown_case"}
        except ValueError as exc:
            return 409, {"error": str(exc)}
    if method == "GET" and route == "/cases":
        try:
            return 200, flow.query(
                view=_param(params, "view"),
                state=_param(params, "state"),
                actor=_param(params, "actor"),
                page=_param(params, "page") or 1,
                page_size=_param(params, "page_size") or 20)
        except ValueError as exc:
            return 400, {"error": str(exc)}
    return 404, {"error": "not_found"}
