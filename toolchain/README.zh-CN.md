# 工具链锁

`lock.json` 记录固定 AES artifact 所观察到的 OpenROAD seed revision、reference-flow revision 与 host compiler/CMake/Python 版本。AE-1 检查随 release 提供的输入；AE-2 记录实际 binary 与日志。任一锁定输入变化都构成新的 artifact claim，必须建立新的 expected manifest。

`environment.yml` 是 `make setup` 使用的项目内 Conda Python 配置。生成的
Miniforge、Python 环境、Codex CLI、下载文件与 activation script 均位于被忽略的
`outputs/toolchain/`；它不安装 OpenROAD 或其原生构建依赖。

AE-2 和 AE-3 有意要求用户单独准备版本匹配的 OpenROAD/ORFS workspace。先在 shell
中激活该 workspace；AE-2 设置 `OPENROAD_EXE`，AE-3 设置与之匹配的 `source_root`
或 `GOALEVOLVE_OPENROAD_SEED`。编译器、CMake、原生依赖、动态库与 OpenROAD binary
均由该宿主 workspace 负责。

release 在 `third_party/benchmarks/` 提供 AE-2 所需 AES benchmark 与 ASAP7 数据。其他 design 可能需要单独取得有许可证限制的 benchmark 输入。
