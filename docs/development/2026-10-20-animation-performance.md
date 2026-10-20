# 动画播放性能调查与优化

调查开始：2026-10-20；验证与收尾：2026-10-21（Asia/Shanghai）。

## 需求

定位旋转立方体播放缓慢的瓶颈，优化速度，保留投影颜色、尺寸、固定点逻辑和用户手动场景编辑。所有开发与优化过程记录为 Markdown；该要求已加入根目录 AGENTS.md。

## 环境与基线

- 基线代码：`6f4829d`。输入：`outputs/rotating_cube_metric.blend`，3072 个气泡、3072 个参考像素对象，Cycles / OptiX / 12 视口采样。
- 上一轮 GUI 截图显示约 0.43 FPS，这是单次观察，不是严格基准。本轮开始时桌面已锁屏，停止界面操作；无法暂停已有 GUI 或复测实际 FPS。后台数据必须标记为非隔离环境，不冒充 GPU/GUI 吞吐。
- 新增 `tests/benchmark_animation_blender.py`：不覆盖输入场景，预热后测 6 个不同帧的 `frame_set`，再单独用 cProfile 测一帧。输出 `tmp/animation_performance/<label>.json`，包含版本、代码来源、对象数、每帧耗时和热点。

## 调查过程

- 初读代码：每帧走完整 `update_projection`，重复写对象变换、可见性、玻璃材质及光学设置，并调用依赖图更新。此时仅为瓶颈假设，待基线剖析确认。
- 基准命令：`blender --background outputs/rotating_cube_metric.blend --python-exit-code 1 --python tests/benchmark_animation_blender.py -- --label baseline`。
- 测量、实现、验证和残留进程检查将在开发过程中补充，不预先宣称提速。

## 基线结果与第一步

- 6 帧后台更新中位数 **3.4175 s**。单独剖析帧总耗时 **3.388 s**，`set_visibility` 调用 3236 次，累计 **2.579 s（约 76%）**；`update_optical_projection` 为 0.430 s，`point_animation_frame` 仅 0.095 s。瓶颈不是 KDTree/BVH 查询。
- 确认代码每帧对数千隐藏参考像素重复调用 `hide_set`；此 API 即使状态相同也使视图层失效。第一步只在 `hide_render` / `hide_get()` 与目标状态不同时写入，仍读取实际状态，不引入可能过期的可见性缓存。
- 点数、模型、颜色、Cycles 采样及降噪均不变。基线进程 PID 71576 正常退出，提升权限查询该 PID 及直接子进程残留数为 0。

## 分步优化

1. 可见性条件写入：后台帧中位数 **0.7051 s**；剖析中可见性部分降为 **0.015 s**。测试 PID 72068 已退出，无直接子进程残留。
2. 对不变的阴影开关、灯位置/朝向、参考像素位置/旋转、对象显示颜色进行条件写入：中位数 **0.5323 s**。颜色比较转为 float32 Vector，与 Blender 属性精度一致，避免浮点精度不同导致误判。测试 PID 72972 已退出，无直接子进程残留。
3. 加入可选语句级剖析（有 tracing 开销，不与正常耗时混比），确认剩余主要开销位于 `view_layer.update()`，不是 BVH。保留必要的依赖图求值，以保证调用者立即读取的变换正确；进一步只在值改变时写入入射色/照亮标记及光线曲线端点，减少依赖图失效。语句剖析 PID 74308 已退出，无直接子进程残留。
4. 不引入跨帧对象/可见性缓存，不合并或删除用户对象，不改变手动编辑语义；读取实际 Blender 属性后比较。暂不降低点数、关闭降噪或改变渲染引擎。

## 回归方案

- 原有动画、投影、光学检查；新增可见性写入计数、重复更新幂等性、用户修改颜色/阴影/隐藏/位置后的恢复检查。
- 基准支持 `--revision 6f4829d` 从 Git 读取旧版 projector.py（不切换分支或改文件），记录实际加载代码 SHA256。依赖 point_cloud_animation.py 本轮未改动。
- `--render` 固定第 13 帧、640×440、12 Cycles 采样、seed 2006、关闭自适应采样，各渲染两次。记录首次和第二次耗时及 PNG 哈希；该计时包含 Blender 同步、着色器准备、渲染、降噪和保存，不是纯 GPU 内核时间。

## 最终对照

旧代码和新代码在独立进程中依次运行，不同时启动本次基准。旧版通过 Git 加载，输入为同一份米制演示，没有覆盖 `.blend`；用户已有 GUI 的 GPU 负载未隔离。

| 指标 | 基线 `6f4829d` | 本次实现 |
| --- | ---: | ---: |
| 总对象数 / 气泡数 | 6337 / 3072 | 6337 / 3072 |
| 6 帧更新中位数 | 3.425101 s | 0.539216 s |
| 第一次静帧渲染（含保存） | 17.406848 s | 5.979485 s |
| 第二次静帧渲染（含保存） | 13.033659 s | 5.537156 s |

- 依次切换第 3、7、11、15、19、23 帧。旧版耗时：3.600407、3.659436、3.374559、3.406015、3.363462、3.444187 秒；新版：0.479339、0.539548、0.542847、0.524827、0.538884、0.567407 秒。
- 切帧约快 **6.35 倍**，第二次静帧渲染总耗时约快 **2.35 倍**。这不是 GPU 内核加速倍数，也不是 GUI FPS。最终 0.539 秒与第二阶段 0.532 秒属于小幅波动，不声称第三步单独获得额外显著提速。
- cProfile 最终单帧总耗时约 0.658 秒，光学更新约 0.360 秒、动画映射约 0.101 秒、可见性约 0.013 秒。剩余主要开销是 Blender 依赖图求值，保留其正确性优先于继续跳过计算。
- 输出：`tmp/animation_performance/before_render.json`、`after_render.json` 及同名 PNG；原始日志在 `tmp/perf_before_render.log`、`tmp/perf_after_render.log`。临时输出不纳入 Git，因此关键测量在本文保留。
- 实际加载的 projector.py 文本 SHA256：旧版 `1fe29e0d5ac240341a4346fb00658aaae3b3adfbb1941034422996e80e5caca1`；新版 `46967da3e6f48342b529d5de300cbb598629980f325c16c662c399728a3dc1b1`。按 UTF-8 文本和规范化换行计算，不等于 Windows 原始 CRLF 文件的字节哈希。本轮未改动画模块，旧版依赖兼容。

## 画面检查

- 两张第 13 帧 PNG 均已目视检查，点云、实体对比立方体、玻璃边框及投影仪位置无明显变化。
- PNG 文件哈希不同，不能宣称逐字节一致。用 .NET `System.Drawing.Bitmap.GetPixel` 逐像素比较 RGB：640×440 共 281600 像素，只有 **5 个像素**发生差异，任意通道最大误差 **1/255**，8-bit RGB 平均绝对误差 **0.00000591856**。不为这 5 个差异像素臆测确定原因，也不将图像比较替代逻辑回归。
- 初次使用本地 Python/Pillow 分析时因未安装 PIL 失败，改用已有 .NET API 完成检查，没有安装依赖或修改输出图片。

## 使用与兼容

- 公开更新函数仍同步完成依赖图求值；按实际属性比较，不缓存“上次已隐藏”等状态，因此手动取消隐藏、修改颜色或移动投影仪后仍能恢复派生结果。
- 不改数据格式、点密度、像素颜色映射、精确表面匹配、深度补偿、固定/设计模式、GPU 选择或采样/降噪设置；旧 `.blend` 与 JSON 无需迁移。
- 已打开窗口中的旧 Python 函数不会自动被磁盘改动替换。暂停播放后，在原窗口重新运行最新 `projector.py`；不使用 `--rebuild`，不从磁盘重载未保存的手动场景。
- 本轮桌面锁定，未向 GUI 发送输入，未关闭用户窗口；无法验证实际播放 FPS、视角拖动速度或优化后的连续播放稳定性。
- 更正历史记录：非提升权限的进程枚举未找到 Blender 不能证明演示退出。后续界面检查确认窗口仍在。测试退出以本次进程句柄及提升权限 PID 查询为准，不据此推断用户 GUI 的生命周期。

## 回归结果与进程清理

Blender 5.2.1 LTS，全部通过：

| 检查 | 结果 | 日志 |
| --- | --- | --- |
| 新增更新性能回归 | 3/3，可见性只写变化、重复更新幂等、手动修改后的派生状态恢复 | `tmp/perf_regression_error.log` |
| 动画回归 | 20/20，含实际尺寸、原始实体、旧数据、时间轴与投影颜色 | 同上 |
| 投影回归 | 43/43，含设计/固定点、FOV、姿态、隐藏光线、光学与 GPU 设置 | 同上 |
| 光学渲染 | 5 组断言通过：RGB 方向、关灯、点不产生照明、无玻璃反射、保留透射 | `tmp/perf_render_regression.log` |
| 降噪渲染 | 1 组通过，OptiX，12 采样 | 同上 |

降噪检查的高饱和像素数 4478，保留率 0.991067，归一化 RGB 平均误差 0.006247，地面高频指标 0.025109 → 0.00005355。这里只复核已有降噪未退化，不将其解释为优化新增的画质效果。结果见 `tmp/denoise_render/verification.json`。

本次每个后台 Blender 均使用隐藏窗口、记录 PID、等待进程句柄退出，再提升权限查询该 PID 及其直接子进程。下表全部退出码 0、残留数 0，无需强制结束；测试未启动需单独保留的外部工作进程。未按名称批量终止 Blender，未关闭用户编辑/演示窗口。

| 阶段 | PID |
| --- | ---: |
| 原始基线 | 71576 |
| 可见性条件写入 | 72068 |
| 变换/颜色/光学条件写入 | 72972 |
| 语句级剖析 | 74308 |
| Git 旧版切帧与渲染 | 75284 |
| 最终新版切帧与渲染 | 68476 |
| 66 项功能回归 | 74416 |
| 光学与降噪渲染 | 57440 |

不把非提升权限的空进程列表当作退出证据。日志中的 Git CRLF 提示不是测试失败；`git diff --check` 通过。代码测试全部完成后仅补写本文、README 和 CHANGELOG，不改变已验证的运行逻辑。

## 复现

在项目根目录 PowerShell 中运行；需要已有 `outputs/rotating_cube_metric.blend` 及可用 Cycles 设备。若没有该实验，先用 `demo_rotating_cube.py` 另行生成，并记录其来源及参数；重新生成的数据不自动等同于本文实验。

```powershell
& 'G:\SteamLibrary\steamapps\common\Blender\blender.exe' --background outputs/rotating_cube_metric.blend --python-exit-code 1 --python tests/benchmark_animation_blender.py -- --label before_render --revision 6f4829d --render
& 'G:\SteamLibrary\steamapps\common\Blender\blender.exe' --background outputs/rotating_cube_metric.blend --python-exit-code 1 --python tests/benchmark_animation_blender.py -- --label after_render --render
& 'G:\SteamLibrary\steamapps\common\Blender\blender.exe' --background outputs/rotating_cube_metric.blend --python-exit-code 1 --python tests/benchmark_animation_blender.py -- --label line_profile --line-profile
& 'G:\SteamLibrary\steamapps\common\Blender\blender.exe' --background --factory-startup --python-exit-code 1 --python tests/test_update_performance_blender.py --python tests/test_animation_blender.py --python tests/test_projection_blender.py
& 'G:\SteamLibrary\steamapps\common\Blender\blender.exe' --background --factory-startup --python-exit-code 1 --python tests/test_optical_render_blender.py --python tests/test_denoise_render_blender.py
```

语句剖析会增加计时开销，只用于定位热点，不与普通基准直接比较。每次运行使用不同标签可保留历史输出。Git 旧版比较限定提交哈希，不切换工作树；新版来源标记为 working-tree，并记录脚本文本哈希和当前 Git 状态。

本次 Git 提交通过 `git log -- docs/development/2026-10-20-animation-performance.md` 定位，基线为 `6f4829d`。避免把尚未生成的提交号或循环依赖的自身提交号写入正文；最终提交号在交付消息中报告。
