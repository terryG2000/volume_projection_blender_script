"""材质创建工具函数"""

import bpy


def create_emission_material(name: str, color: tuple, strength: float = 3.0) -> bpy.types.Material:
    """
    创建发光材质

    Args:
        name: 材质名称
        color: RGBA 颜色元组
        strength: 发光强度

    Returns:
        创建的材质对象
    """
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    emission = nodes.new('ShaderNodeEmission')
    emission.inputs['Color'].default_value = color
    emission.inputs['Strength'].default_value = strength

    output = nodes.new('ShaderNodeOutputMaterial')
    links.new(emission.outputs['Emission'], output.inputs['Surface'])

    mat.diffuse_color = color
    return mat


def create_principled_material(name: str, color: tuple, alpha: float = 1.0) -> bpy.types.Material:
    """
    创建原理化 BSDF 材质

    Args:
        name: 材质名称
        color: RGBA 颜色元组
        alpha: 透明度 (0=全透明, 1=不透明)

    Returns:
        创建的材质对象
    """
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    mat.blend_method = 'BLEND'

    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    principled = nodes.new('ShaderNodeBsdfPrincipled')
    principled.inputs['Base Color'].default_value = color
    principled.inputs['Alpha'].default_value = alpha

    output = nodes.new('ShaderNodeOutputMaterial')
    links.new(principled.outputs['BSDF'], output.inputs['Surface'])

    return mat


def create_simple_material(name: str, color: tuple) -> bpy.types.Material:
    """
    创建简单材质（无节点）

    Args:
        name: 材质名称
        color: RGBA 颜色元组

    Returns:
        创建的材质对象
    """
    mat = bpy.data.materials.new(name=name)
    mat.diffuse_color = color
    return mat
