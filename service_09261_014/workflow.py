"""版本化业务工作流：状态经 SQLiteStore 持久化，重启后视图、顺序与计数不变。"""
from dataclasses import dataclass

from .store import SQLiteStore, STATE_VIEW


@dataclass(frozen=True)
class Case:
    id: str
    actor: str
    state: str
    version: int = 1

    @property
    def view(self):
        return STATE_VIEW[self.state]

    def move(self, state, actor):
        if state == self.state:
            return self  # 幂等命令：重复的处理动作不产生新版本
        allowed = {
            "draft": {"reviewing", "cancelled"},
            "reviewing": {"approved", "rejected"},
            "rejected": {"draft"},
            "approved": {"archived"},
        }
        if state not in allowed.get(self.state, set()):
            raise ValueError("invalid transition")
        return Case(self.id, actor, state, self.version + 1)

    def to_dict(self):
        return {"id": self.id, "actor": self.actor, "state": self.state,
                "version": self.version, "view": self.view}


class Workflow:
    def __init__(self, store=None):
        self.store = store or SQLiteStore()

    @staticmethod
    def _case(row):
        return Case(row["id"], row["actor"], row["state"], row["version"])

    @staticmethod
    def _public(row):
        return {"id": row["id"], "actor": row["actor"], "state": row["state"],
                "version": row["version"], "view": STATE_VIEW[row["state"]]}

    def create(self, id, actor, key=None):
        if key:
            hit = self.store.get_by_key(key)
            if hit:
                return self._case(hit)  # 幂等键命中（含重启后）：返回已存在的记录
        if self.store.get(id):
            raise ValueError("duplicate")
        row = self.store.insert({"id": id, "actor": actor, "state": "draft",
                                 "version": 1, "idempotency_key": key})
        return self._case(row)

    def move(self, id, state, actor):
        row = self.store.get(id)
        if row is None:
            raise KeyError(id)
        case = self._case(row).move(state, actor)
        # 处理动作落库后，各视图查询立即反映新状态，记录只出现在正确视图里。
        self.store.update({"id": case.id, "actor": case.actor,
                           "state": case.state, "version": case.version})
        return case

    def query(self, view=None, state=None, actor=None, page=1, page_size=20):
        if page < 1 or not 1 <= page_size <= 200:
            raise ValueError("invalid pagination")
        result = self.store.query(view=view, state=state, actor=actor,
                                  page=page, page_size=page_size)
        result["items"] = [self._public(r) for r in result["items"]]
        return result

    def counts(self):
        return self.store.counts()

    def snapshot(self):
        return [self._public(r) for r in self.store.all()]
