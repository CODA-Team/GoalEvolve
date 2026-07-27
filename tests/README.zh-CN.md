# 测试

在项目根目录运行确定性的 core suite：

```bash
PYTHONPATH=. python3 -m unittest tests.unit.test_core -q
```

`unit/` 保存确定性的 controller 与 official-check 回归；`integration/` 留给已安装 OpenROAD 的真实流程；`artifact/` 验证 release manifest 与固定 artifact 接口，不需要 LLM。若已安装也可使用 `pytest`。

官方 checker 测试只会在官方 benchmark 数据缺失时跳过，且不重建 OpenROAD。真正的 build/flow/4-of-4 验证仍属于 integration campaign，不能由 unit test 替代。
