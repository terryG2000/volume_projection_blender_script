# Blender 体积投影模型

普通透视投影仪照射玻璃中的固定散射点。当前版本在 Blender 5.2.0 LTS 验证。
默认建立独立的 `VolumeProjection` 场景，保留原场景；重复运行会重新连接控制并保留手动修改。

## 运行与调整

在项目目录的 PowerShell 中运行：

```powershell
& 'G:\SteamLibrary\steamapps\common\Blender\blender.exe' --python projector.py -- --keep-open
```

生成后窗口保持打开，默认选中 `Projector_Body`。在三维视图按 `N`，打开“体积投影”页签。
可以直接移动、旋转投影仪，或在侧栏输入位置、旋转角和相对强度。自动更新约每 0.15 秒检查一次变化。

- 气泡固定在玻璃中，投影仪移动后会采样新的投影像素；背向投影仪或超出视场的气泡不发光。
- “恢复标定位置”恢复初始投影仪姿态；原始气泡对应关系在此姿态下成立。
- 投影内容提供测试图、全白、深度编码三种模式；照亮数量显示当前响应。
- “像素光线”“散射示意”“参考图像平面”“深度层”分别控制辅助显示。
- “导出坐标和投影图”写入 `outputs/bubble_coordinates.csv` 和 `outputs/projector_input.png`。
- `Ctrl+S` 保存手动修改。重新运行脚本不会重建；需要重新生成时显式传入 `--rebuild`。

默认 16 x 10 个投影采样、7 个深度层。将 `FAST_PREVIEW` 改为 `False` 后用 `--rebuild` 生成 34 x 22 个散射点。
这些是教学示意的采样网格，不是商业投影仪的原生分辨率。光线辅助显示在高密度模式最多显示 260 条。
改变点云参数后必须重建；不要把“恢复标定位置”当作重新雕刻点云。

## 保存、重开和预览

```powershell
& 'G:\SteamLibrary\steamapps\common\Blender\blender.exe' --background --python-exit-code 1 --python projector.py -- --render-preview --save-blend --export-data
& 'G:\SteamLibrary\steamapps\common\Blender\blender.exe' 'outputs\volume_projection_scene.blend' --python projector.py -- --keep-open
```

第一条命令输出预览 PNG、可编辑 `.blend`、气泡坐标 CSV、低分辨率投影输入 PNG。
第二条打开保存的场景并重新注册交互控制，窗口保持打开。
单独双击 `.blend` 可以编辑几何，但在新的 Blender 进程中需要运行脚本才能恢复自动更新和侧栏。
脚本也嵌入在场景的 `VolumeProjection_Controls.py` 文本中，可在 Blender 文本编辑器中手动运行。
不要求开启自动执行 Python 脚本。

## 与论文的关系

参考本目录 `Nayar_TR06.pdf`：Nayar 和 Anand，*Projection Volumetric Display using Passive Optical Scatterers*，CUCS-030-06，2006。

- 第 3 节、图 2：可索引点云要求每个散射点能被独立照亮。随机分配深度/像素对应关系时，点不能离开投影射线。
- 第 4 节、图 4：散射体是激光诱导损伤产生的微裂纹，球体只是几何替代。暗像素的物理散射点仍然存在。
- 第 5 节、图 5：论文主要用菲涅尔透镜将普通投影仪的发散光转换为平行光，改善各深度的分辨率与标定。
- 第 6 节：实际装置需要分层、分色标定。当前采用已知几何的理想透视映射，不复现其多项式标定实验。

本模型依照普通投影仪的要求使用透视射线；每个像素对应一个固定点。
在相邻的 7 条像素射线间随机排列深度层，再沿射线加入有界深度抖动。
这是对论文索引原则的简化应用，不是图 2 三维点云生成算法或可见率实验的完整复现。

初始位置使用 `P = L + t(T - L)`，其中 `L` 为镜头、`T` 为参考像素位置，`t` 由玻璃内选定深度确定。
移动后通过投影仪变换的逆矩阵将固定点映射回像素坐标，并以最近像素单元采样颜色。
CSV 的 `pixel_*_calibrated` 是初始索引，`pixel_*_current` 是当前连续像素坐标；超出视场时为空。
坐标单位为米，尺寸和气泡半径均为放大的示意参数，不是论文的制造尺寸。

当前仍属于几何和辐射响应的可视化近似：颜色以发光材质表示，玻璃透明材质用于观察，未计算折射光路、
焦外光斑、串扰、多次散射、裂纹角度响应或绝对光强。金色线表示像素中心射线，有限像素单元内的点也可能被照亮。
可选远侧图像平面是随投影仪移动的参考平面，不是透明玻璃上真实出现的不透明屏幕。
关闭所有辅助显示可单独观察气泡。散射线只表示方向，未实现定量散射模型。

## 验证

```powershell
& 'G:\SteamLibrary\steamapps\common\Blender\blender.exe' --background --factory-startup --python-exit-code 1 --python tests/test_projection_blender.py
```

测试覆盖初始像素映射、有限气泡与其他中心射线的分离、玻璃内部边界、投影仪变换、
固定点不随投影仪移动、全白/关闭响应、恢复标定、参考图切换、重复运行保留编辑和导出数据。

Blender 定时更新 API：https://docs.blender.org/api/5.2/bpy.app.timers.html

原始资料：[球坐标公式](https://zh.wikipedia.org/zh-cn/%E7%90%83%E5%BA%A7%E6%A8%99%E7%B3%BB)
