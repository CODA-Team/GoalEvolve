# JPEG encoder 实验

`baseline.json` 与 `evolve.json` 使用项目内的共享 OpenROAD p0、JPEG 输入和统一的
AE-3 配置模型。baseline 写入 `outputs/baseline/jpeg_encoder/baseline.json`；当源码
快照或 flow 改变时，先重新执行：

```bash
PYTHONPATH=. python3 -m goalevolve.cli baseline \
  --config experiments/jpeg_encoder/baseline.json
```

`evolve.json` 已冻结同 flow p0 指标 TNS `70.08 ns`、dynamic `278.839B pW`、leakage
`161M pW`，以及绝对目标 `53 ns`、`250B pW`、`80M pW`。启动多轮进化：

```bash
PYTHONPATH=. python3 -m goalevolve.cli run \
  --config experiments/jpeg_encoder/evolve.json --rounds 10
```
