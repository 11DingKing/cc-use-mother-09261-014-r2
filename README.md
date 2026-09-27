# 能力目标达成跟踪

纯 Python 服务端基础项目，提供版本化状态、幂等命令、SQLite 持久化和 JSON API 边界。

## 视图与分类口径

记录状态唯一映射到三个运营视图（定义于 `workflow.VIEW_BY_STATE`，列表筛选与各类计数共用同一份口径），任一记录同一时刻只属于一个视图：

- 待办 `todo`：draft、reviewing、rejected
- 已处理 `processed`：approved、archived
- 异常 `exception`：cancelled

## 查询接口

```
GET /cases?view=todo&state=draft&actor=ops&page=1&page_size=20
```

返回稳定结构（空结果结构不变，items 为 `[]`、total 为 0）：

```json
{"view": "todo", "page": 1, "page_size": 20, "total": 4, "pages": 1,
 "counts": {"todo": 4, "processed": 3, "exception": 1, "all": 8},
 "items": [{"id": "c1", "actor": "ops", "state": "draft", "version": 1, "view": "todo"}]}
```

一致性保证：

- `items`、`total`、`counts` 在同一事务快照中从同一个 SQLite 库读出，筛选条件同时作用于列表与计数。
- `items` 按持久化的入库序号 `seq` 升序排列，排序键唯一且不可变：翻页不重复、不漏项，系统重启后分页顺序与各类计数不变。
- 处理动作（`POST /cases/{id}/move`）用单条原子 UPDATE 完成状态迁移，完成后记录只出现在新状态对应的视图。
- 幂等键持久化在 `idempotency_keys` 表，重启后重放 `POST /cases` 仍返回原记录。

测试命令：python3 -m unittest discover -s tests -v

编译命令：python3 -m compileall -q service_09261_014 tests
