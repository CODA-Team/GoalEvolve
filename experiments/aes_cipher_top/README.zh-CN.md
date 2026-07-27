# AES cipher top 实验

`ae3_smoke.json` 是一 Student、一 round 的真实连通性流程。它从冻结 R54 artifact 出发，使用项目本地凭据，并且只写入 `outputs/ae3/aes_cipher_top_smoke/`。

`evolve.json` 在此基础上扩展为四个 Student 并取消一轮上限。`power_target.json`、`historical_qor.json`、`finalflow.json` 是保留的历史 campaign 记录；其中旧 runtime 路径只用于 provenance，不是可移植启动 profile。

冻结 artifact 的目标为 TNS ≤ 12 ns、dynamic power ≤ 350B pW、leakage ≤ 35M pW。这三项是决策指标；runtime、SPPA、Sfinal 始终只是 observer。
