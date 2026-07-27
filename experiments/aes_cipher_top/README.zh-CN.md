# AES cipher top 实验

`evolve.json` 是唯一的 AES AE-3 启动 profile。它继承
`experiments/ae3_base.json` 的统一四 Student 配置，从冻结 R54 源码出发，默认写入
`outputs/ae3/aes_cipher_top/`。

执行 `PYTHONPATH=. python3 -m goalevolve.cli run --config
experiments/aes_cipher_top/evolve.json --rounds 10`。重复同一命令会从该目录最后一个
已完成 round 继续并追加 round。`archive/` 保存历史记录和旧 smoke profile，只用于
provenance，不能作为启动 profile。

冻结 artifact 的目标为 TNS ≤ 12 ns、dynamic power ≤ 350B pW、leakage ≤ 35M pW。这三项是决策指标；runtime、SPPA、Sfinal 始终只是 observer。
