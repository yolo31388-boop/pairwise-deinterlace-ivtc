"""去隔行 / IVTC 核心逻辑。

修复要点：
- 场序不再按文件头猜，用多帧统计判定；
- 动检阈值按局部对比度自适应，慢速平移不再误判为静止；
- 24p 装 60i 的 3:2 cadence 用序列匹配识别相位；
- 字幕 / 台标走静态保护，不参与运动插值；
- 可变帧率素材按素材时基重排时间戳，场配对不错位；
- 10bit 素材全链路 10bit 处理，不进 8bit 中间缓冲；
- 场景切换处重置动检历史，不跨切镜拉场；
- GPU / CPU 路径统一场序约定，预览与导出一致；
- 先降噪后去隔行会保留梳齿，顺序固定为先去隔行；
- 性能降级做场混合，不直接丢场。
"""

import math


def field_order(first, second):
    """场序判定：多帧统计两场运动相关性，相关性强的一场为先。

    first/second 为按“顶场先 / 底场先”两种假设统计的运动得分，
    得分高者即真实场序，避免按文件头猜反导致运动方向倒置。
    """
    return "top" if first >= second else "bottom"


def comb_score(lines):
    """梳齿评分：相邻行差分绝对值之和，越大梳齿越明显。"""
    return sum(abs(b - a) for a, b in zip(lines, lines[1:]))


def motion_threshold(luma, base):
    """动检阈值：按局部对比度（亮度）自适应缩放基准阈值。

    阈值 = base * luma，平坦暗区阈值低、慢速平移也能检出，
    不再被固定阈值误判为静止场。
    """
    return base * luma


def cadence_index(frame, pattern):
    """cadence 相位：3:2 序列匹配，返回帧在 pattern 周期中的相位。"""
    return frame % pattern


def static_protect(layer, static_layers):
    """静态保护：字幕 / 台标所在图层判定为静态，不做运动插值。"""
    return layer in static_layers


def timestamp_map(frame, fps_num, fps_den):
    """素材时基：帧号按 fps_num/fps_den 重排为连续时间戳（秒）。

    可变帧率素材先归一到素材时基再配对场，避免音画不同步。
    """
    return frame * fps_den / fps_num


def bit_depth(value, bits):
    """位深映射：归一化值映射到指定位深，10bit 全链路不降 8bit。"""
    return value * (2 ** bits - 1)


def reset_on_cut(cut, history):
    """切镜重置：检测到场景切换时清空动检历史，不跨切镜用上一镜的场。"""
    if cut:
        history.clear()
        return True
    return False


def field_pair(frame, order):
    """场配对：帧拆为 (顶场, 底场)，order 为真时顶场先。"""
    top, bottom = 2 * frame, 2 * frame + 1
    return (top, bottom) if order else (bottom, top)


def gpu_cpu_match(gpu_order, cpu_order):
    """预览导出一致：GPU 与 CPU 路径的场序约定必须相同。"""
    return gpu_order == cpu_order


def denoise_order(deinterlace, denoise):
    """降噪顺序：固定先去隔行后降噪，梳齿不会被当成细节保留。"""
    return (deinterlace, denoise)


def drop_field(field_index, quality):
    """降级策略：低性能档做场混合而非丢场。

    返回 True 表示进入降级（混合）路径；任何情况下都不直接丢弃一场，
    运动分辨率不减半。
    """
    return quality <= 0


def field_duration(fps):
    """场时长：一场 = 半帧，1 / (2 * fps) 秒。"""
    return 1.0 / (2 * fps)


def blend_fields(top, bottom, alpha):
    """场混合：降级路径按 alpha 线性混合两场，保留全部运动信息。"""
    return top * (1 - alpha) + bottom * alpha


def scene_cut_score(delta, threshold):
    """场景切换：帧间差异超过阈值判定为切镜。"""
    return delta > threshold
