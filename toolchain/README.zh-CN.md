# 工具链锁

`lock.json` 记录固定 AES artifact 所观察到的 OpenROAD seed revision、MLCAD contest revision 与 host compiler/CMake/Python 版本。AE-1 检查随 release 提供的输入；AE-2 记录实际 binary 与日志。任一锁定输入变化都构成新的 artifact claim，必须建立新的 expected manifest。

release 在 `third_party/mlcad2026_benchmarks/` 提供 AE-2 所需 AES benchmark 与 ASAP7 数据。其他 design 可能需要单独取得有许可证限制的 benchmark 输入。
