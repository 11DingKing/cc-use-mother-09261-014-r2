# 能力目标达成跟踪

纯 Python 服务端基础项目，提供版本化状态、幂等命令、SQLite 持久化和 JSON API 边界。

查询边界（筛选、分类口径与总数来自同一张持久化表）：

- `GET /cases?view=todo|processed|exception&state=&actor=&page=&page_size=` → `{"items":[...],"total":N,"page":P,"page_size":S}`，空结果结构不变
- `GET /cases/counts` → `{"todo":N,"processed":N,"exception":N,"total":N}`
- 分类口径定义在 `service_09261_014/store.py` 的 `VIEW_STATES`，列表筛选与各视图计数共用；排序键为持久化的 `seq,id`，重启后分页顺序与计数不变

测试命令：python3 -m unittest discover -s tests -v

编译命令：python3 -m compileall -q service_09261_014 tests
