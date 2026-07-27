# 冻结源码 artifact：AES R54 Student 1

`source/` 是被晋升为 `round_054:student_1` 的完整源码快照，不是 build cache，也不是指向 `GoalEvolve_v2` 的符号链接；它是 AE-2 的权威输入。

R54 的 implementation diff 同时保存在 `../../../../expected/aes_cipher_top/r054_student1/implementation.diff`。AE-2 使用冻结源码树，而不是回放不完整的历史 patch 链：这样才能在不调用 LLM、不依赖旧 campaign runtime 的条件下验证已报告的精确源码结果。

build 输出必须写入 `outputs/ae2/`，不得在 `source/` 内新增 build tree。
