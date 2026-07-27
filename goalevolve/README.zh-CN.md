# 实现导图

| Package | 职责 | 不应承担的职责 |
|---|---|---|
| `core/` | contract、类型化记录、确定性 I/O、provenance、plugin 协议 | 策略决策或工具执行 |
| `planning/` | diagnosis、retrieval card、scope、EPD、observation、timing recipe 经验 | 源码编辑或晋升写入 |
| `agents/` | Teacher/Student packet 与项目本地 Codex worker | build/flow 语义 |
| `execution/` | 隔离 workspace、命令策略、preflight、round 编排 | contest score 定义 |
| `evaluation/` | OpenROAD flow、官方检查、evidence、promotion、observer | LLM prompt 构建 |
| `testing/` | smoke/unit 的确定性 mock evaluator | artifact 结论 |

round 的拥有者是 `execution/engine.py`：它产生 diagnosis、获得多样且受源码 scope 约束的 fallback hypothesis、让 Teacher 细化、驱动隔离 Student 编辑、将 build/flow/telemetry 失败回传给同一 Student、记录 EPD/observation，并只晋升已验证证据。`evaluation/sfinal.py`、`evaluation/leaderboard.py`、`token_ledger.py` 均为 observer-only。

使用 `PYTHONPATH=. python3 -m goalevolve.cli --help` 查看公开命令。
