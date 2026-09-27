"""版本化业务工作流：状态机、视图分类口径与分页查询。"""
from dataclasses import dataclass, asdict

from .store import SQLiteStore

# 唯一的分类口径：待办/已处理/异常。列表筛选与各类计数都从这里推导，
# 每条记录任一时刻只属于一个视图，处理动作完成后只出现在新状态对应的视图。
VIEW_BY_STATE = {
    "draft": "todo",
    "reviewing": "todo",
    "rejected": "todo",
    "approved": "processed",
    "archived": "processed",
    "cancelled": "exception",
}
VIEWS = ("todo", "processed", "exception")
STATES_BY_VIEW = {v: tuple(s for s in VIEW_BY_STATE if VIEW_BY_STATE[s] == v) for v in VIEWS}

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 200


@dataclass(frozen=True)
class Case:
    id: str
    actor: str
    state: str
    version: int = 1

    def move(self, state, actor):
        allowed = {"draft": {"reviewing", "cancelled"},
                   "reviewing": {"approved", "rejected"},
                   "rejected": {"draft"},
                   "approved": {"archived"}}
        if state not in allowed.get(self.state, set()):
            raise ValueError("invalid transition")
        return Case(self.id, actor, state, self.version + 1)


class Workflow:
    def __init__(self, store=None):
        self.store = store or SQLiteStore()

    def create(self, id, actor, key=None):
        if key:
            known = self.store.case_id_for_key(key)
            if known is not None:
                return Case(**self.store.get_case(known))
        if self.store.get_case(id) is not None:
            raise ValueError("duplicate")
        row = Case(id, actor, "draft")
        self.store.insert_case(asdict(row), key)
        return row

    def move(self, id, state, actor):
        current = self.store.get_case(id)
        if current is None:
            raise KeyError(id)
        nxt = Case(**current).move(state, actor)
        self.store.update_case(id, nxt.state, nxt.actor, nxt.version)
        return nxt

    def snapshot(self):
        return self.store.list_all()

    def query(self, view=None, state=None, actor=None, page=1, page_size=DEFAULT_PAGE_SIZE):
        """分页查询。空结果也返回同样的结构；items/total/counts 来自同一事务快照。"""
        if view in (None, "", "all"):
            view_states, view_name = None, "all"
        elif view in STATES_BY_VIEW:
            view_states, view_name = STATES_BY_VIEW[view], view
        else:
            raise ValueError("invalid view")
        if state is not None and state not in VIEW_BY_STATE:
            raise ValueError("invalid state")
        page = max(1, int(page))
        page_size = min(MAX_PAGE_SIZE, max(1, int(page_size)))
        items, total, state_counts = self.store.page(
            view_states=view_states, state=state, actor=actor,
            limit=page_size, offset=(page - 1) * page_size)
        counts = {v: 0 for v in VIEWS}
        for s, n in state_counts.items():
            counts[VIEW_BY_STATE[s]] += n
        counts["all"] = sum(counts.values())
        return {
            "view": view_name,
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": -(-total // page_size),
            "counts": counts,
            "items": [dict(item, view=VIEW_BY_STATE[item["state"]]) for item in items],
        }
