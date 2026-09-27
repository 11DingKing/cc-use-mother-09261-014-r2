"""JSON API 适配器。"""
from urllib.parse import urlparse, parse_qs


def _paging(qs):
    try:
        return int(qs.get("page", ["1"])[0]), int(qs.get("page_size", ["20"])[0])
    except ValueError:
        raise ValueError("invalid pagination")


def dispatch(flow, method, path, body=None):
    body = body or {}
    if method == "POST" and path == "/cases":
        if "id" not in body or "actor" not in body:
            return 400, {"error": "missing field"}
        try:
            return 201, flow.create(body["id"], body["actor"], body.get("idempotency_key")).to_dict()
        except ValueError as e:
            return 409, {"error": str(e)}
    if method == "POST" and path.endswith("/move"):
        if "state" not in body or "actor" not in body:
            return 400, {"error": "missing field"}
        try:
            return 200, flow.move(path.split("/")[2], body["state"], body["actor"]).to_dict()
        except KeyError:
            return 404, {"error": "not_found"}
        except ValueError as e:
            return 409, {"error": str(e)}
    if method == "GET":
        route = urlparse(path)
        qs = parse_qs(route.query)
        if route.path == "/cases":
            try:
                page, page_size = _paging(qs)
                return 200, flow.query(view=qs.get("view", [None])[0],
                                       state=qs.get("state", [None])[0],
                                       actor=qs.get("actor", [None])[0],
                                       page=page, page_size=page_size)
            except ValueError as e:
                return 400, {"error": str(e)}
        if route.path == "/cases/counts":
            return 200, flow.counts()
    return 404, {"error": "not_found"}
