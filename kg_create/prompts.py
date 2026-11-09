EXTRACT_MODEL_COMPONENTS_FROM_CMIP_CV="""
从非结构化文本抽取模型组件名称，只输出组件名。以下示例中的输入与组件名保留原文。

输入文本：MetUM-HadGEM3-GA7.1 (N96; 192 x 144 longitude/latitude; 85 levels; top level 85 km)
输出文本：MetUM-HadGEM3-GA7.1

输入文本：none, prescribed MACv2-SP
输出文本：MACv2-SP

输入文本：Canadian Model of Ocean Carbon (CMOC); NPZD ecosystem with OMIP prescribed carbonate chemistry
输出文本：CMOC

有多个模型组件名称时，用英文逗号分隔。
不含模型组件名称时，只返回 none。

输入文本：{input_text}
"""

__all__ =[ "EXTRACT_MODEL_COMPONENTS_FROM_CMIP_CV"]
