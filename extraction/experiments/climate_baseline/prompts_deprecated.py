"""
This is a unified file that houses all the prompts relevant for information extraction. 
"""
TITLE_EXTRACTION = """论文标题是什么？"""
FIELD_EXTRACTION = """用一个短语概括论文的研究领域，例如“气候变化”。"""
KEYWORDS_EXTRACTION = """抽取3—5个概括论文内容、主题和领域的关键词，用逗号分隔，例如：气候变化、全球变暖、温室气体。"""
PROBLEM_EXTRACTION = """用一句话概括论文要解决的问题，不超过50词，例如现有方法不足、任务挑战或影响性能的因素。示例：现有气候模型因空间分辨率较粗、对流过程表示不足，难以准确模拟极端降水。"""
METHOD_EXTRACTION = """用一句话概括论文为解决问题提出的方法，不超过50词。示例：使用分位数映射进行偏差订正，提高区域气候模型降尺度温度预测的准确性。"""
MODEL_EXTRACTION = """列出论文讨论的气候模型；没有时回答 no。模型名称用单个缩写形式。正确示例：ACCESS-CM2；错误示例：ACCESS-CM2 后附括号内全名。"""
TASK_EXTRACTION = """用一句话概括论文的研究任务，不超过50词。示例：使用降尺度的 CMIP6 气候模型输出预测南亚未来温度和降水格局。"""
PAPER_SCREENING_PROMPT = """论文是否同时包含模型产生或用于评价模型的观测数据集，以及评价指标？仅在两项同时满足时回答 yes，否则回答 no。"""
OBSERVATION_AND_METRIC_EXTRACTION = """仅从论文表格抽取每个气候模型的观测数据集、评价指标及结果。指标必须是定量的，不能是描述性评价。"""
INNOVATION_EXTRACTION = """用一句话描述论文的创新或贡献，不超过50词。示例：提出新的偏差订正技术，提高区域气候模型对南亚极端降水的预测准确性。"""
NO_EXPLANATION_PROMPT = """不添加任何评论或解释。"""
# Water bodies removed as it is merged with No_Country_Region - Water_Bodies: Oceans, seas, and lakes (e.g., Pacific Ocean).

ENTITY_TYPE_DEFINITIONS = """对 ESGF 派生类型，使用 ESGF／CMIP／CORDEX 受控词表含义；模型设计术语遵循 WCRP／ES-DOC／基本模型文档的语义。
Activity：CMIP／ESGF 的 activity_id，表示协调的模式比较计划或建模活动，如 CMIP、ScenarioMIP、AerChemMIP、C4MIP、CORDEX；不是单个情景或实验。
ExperimentFamily：具有共同科学目标的一组相关实验，通常比单个 experiment_id 更广。
Experiment：CMIP／ESGF 的 experiment_id，表示具体实验、情景或协议，如 historical、piControl、ssp585、amip、abrupt-4xCO2；不是活动、项目或成员集合标签。
SubExperiment：CMIP 的 sub_experiment_id，表示实验内受控子情形，如起始年份或回报／预报配置；仅原文明示元数据值时使用 none。
Source：CMIP／ESGF 的 source_id 或类似模型／数据集标识，通常是产生数据的注册气候模型或再分析来源，如 GFDL-ESM4、ERA5；不是论文、机构、组件、变量或实验。
SourceType：CMIP／ESGF 的 source_type，表示模型配置类别，如 AOGCM、AGCM、OGCM、LAND、AER、CHEM、BGC、ISM、RCM。
SourceComponent：来源模型中有名称的组件或子模型，如 CICE4.1、MOM、JSBACH、NEMO、UKCA、ECHAM；可以属于大气、海洋、陆面、海冰、气溶胶、化学、陆冰或生物地球化学，但领域标签本身不是组件名。
PhysicalScheme：模型／组件用于表示未解析或简化的物理、化学、生物地球化学、表面交换过程的具名或明确描述的表示、参数化、闭合、公式或算法；如浅对流方案、Chikira-Sugiyama 对流方案、ecRad 辐射方案、云微物理方案、K剖面参数化、地形重力波拖曳参数化。
PhysicalFeature：模型／组件的结构、数值、网格、坐标、动力核心、垂直层、耦合或配置属性；如三极网格、立方球面网格、有限体积动力核心、混合sigma气压坐标、Arakawa C网格、80个垂直层、0.01 hPa模式顶、随地形网格或规定气溶胶配置。
SimulationType：模拟或运行行为类型，如瞬变、平衡、控制、初始化、耦合、仅大气、平板海洋模拟；不是实验标识。
Metric：定量评价、诊断或性能测度，如 RMSE、偏差、相关性、平衡气候敏感度、瞬态气候响应或气溶胶光学厚度误差；不是模型设计特征。
Project：ESGF 顶层项目或数据集合，如 CMIP6、CORDEX、obs4MIPs、input4MIPs；原文同时给出活动与项目时应区分。
Institute：负责模型开发、数据产生或模拟的 CMIP／ESGF 机构标识或机构名。
Variable：CMIP／CF 数据变量或请求输出量，包括变量标识、长名称、单位或标准名，如 tas、pr、海表温度、air_temperature。
Realm：CMIP／ESGF 受控地球系统领域，如 atmos、ocean、land、seaIce、aerosol、atmosChem、landIce、ocnBgchem。
Frequency：CMIP／ESGF 时间采样频率，如 fx、mon、day、6hr、3hr、subhr。
Resolution：ESGF 标称分辨率或明确空间网格尺度／分辨率区间，如100 km、1度、0.25度、192×144；不是网格架构。
Ensemble：相关模式运行或成员的集合；CMIP6变体标签优先用 Member。
Member：CMIP6 的 member_id 或 variant_label，通常形如 r<i>i<j>p<k>f<l>，如 r1i1p1f1。
MIPEra：CMIP代际，如 CMIP5、CMIP6。
RCM：用于精细区域模拟的 CORDEX 区域气候模型标识／名称，如 REMO、RACMO、RegCM、HIRHAM、ALADIN。
Domain：CORDEX或区域建模的域标识／地理区域，如 EUR-11、AFR-44、NAM-22，或具名区域。
Continent：亚洲、非洲等大洲。
Country：印度、美国等主权国家或领土。
Country_Subdivision：国家内行政区域，如加利福尼亚州。
City：巴黎等城市。
No_Country_Region：非国家／行政区／城市的地区，包括开阔海域、流域、海洋、极地、沙漠及具名非主权区域。
Instrument：AVHRR、MODIS、辐射计、激光雷达等观测设备或传感器。
Platform：仪器搭载的物理载体，如 ENVISAT、Aqua、Terra、飞机、站点、浮标、卫星。
Weather_Event：风暴、干旱、热浪、洪水等具体天气事件或事件类别。
Teleconnection：ENSO、NAO、PDO、IOD等大尺度气候模态或联系格局。
Ocean_Circulation：AMOC、湾流、翻转环流等海洋环流特征或洋流系统。
Natural_Hazard：海啸、地震、野火、洪水等自然灾害发生或具名灾害。
NaturalHazardType：受控灾害类型，优先使用 NOAA 风格事件类型，如 Flood、Tornado、Wildfire、Drought。
ClimateZone：基于温度和降水的气候区分类，仅使用 Tropical、Dry、Temperate、Continental、Polar。
"""

EXTRACTION_BOUNDARIES = """类型判别：
- PhysicalScheme：模型用什么表示／参数化／闭合／公式／算法描述某个过程？
- PhysicalFeature：模型有哪些结构、数值、网格、坐标、动力核心、垂直层、耦合或配置属性？
- Metric：使用或报告了什么定量评价或诊断测度？
排除边界：
- land、ocean、atmos、seaIce、aerosol、atmosChem 等领域标签属于 Realm，不是 SourceComponent。
- CICE4.1、JSBACH、MOM、NEMO、UKCA、ECHAM、CAM、CLM、MPIOM等组件名属于 SourceComponent，不是 PhysicalScheme。
- CF标准名、CMIP变量标识及物理量，如 tas、pr、air_temperature、降水、海表温度，属于 Variable，不是 PhysicalFeature 或 PhysicalScheme。
- 100 km、1度等标称分辨率属于 Resolution；三极网格、立方球面网格、混合sigma气压坐标等架构或坐标设计可属于 PhysicalFeature。
- historical、piControl、ssp126、ssp370、ssp585 等实验／情景属于 Experiment，不是 Activity 或 Project。
- CMIP6、ScenarioMIP、CORDEX、obs4MIPs 等活动／项目标签不属于 Experiment。
- 仅有过程名称时，不归为 PhysicalScheme；须明确标识方案、参数化、闭合、公式、算法或具名表示。
抽取要求：
- 宁可省略，不作薄弱推断。仅抽取论文明确支持的实体或关系。
- 有具体名称时保留准确的模型、组件、方案、特征、变量、实验和成员字符串。
- 原文提供具体名称或版本时，不虚构泛化标签。
"""

ENTITY_EXTRACTION_PROMPT = f"""给定气候建模或地球系统科学论文或其中段落，知识图谱主要由 ESGF 元数据构成。
抽取以下类型的实体实例。类型定义如下：
{ENTITY_TYPE_DEFINITIONS}
{EXTRACTION_BOUNDARIES}
仅输出论文实际出现的实体。
"""

KG_LINKING_PROMPT = f"""给定气候建模或地球系统科学论文或其中段落，知识图谱主要由 ESGF 元数据构成。
抽取符合以下本体的带类型三元组（来源、关系、目标）。每条提供文本中所有支持段落，
并将“它”“该模型”等指代明确解析为完整实体名。
本体按（来源类型、目标类型、关系）给出：
RELATIONSHIP_SOURCE_TO_TARGET = [
                                 ('Source','Source','HAS_SUBSEQUENT_VERSION'),
                                 ('Source','Source','INHERITED_FROM'),
                                 ('Source','SourceComponent','HAS_SOURCE_COMPONENT'),
                                 ('SourceComponent','SourceComponent','HAS_SUBSEQUENT_VERSION'),
                                 ('SourceComponent','SourceComponent','HAS_SOURCE_COMPONENT'),
                                 ('SourceComponent','Variable','PRODUCES_VARIABLE'),
                                 ('SourceComponent','Realm','APPLIES_TO_REALM'),
                                 ('Source','PhysicalScheme','USES_PHYSICAL_SCHEME'),
                                 ('Source','PhysicalFeature','HAS_PHYSICAL_FEATURE'),
                                 ('Source','SimulationType','HAS_SIMULATION_TYPE'),
                                 ('Source','Metric','HAS_METRIC'),
                                 ('Instrument','Variable','MEASURES_VARIABLE'),
                                 ('Instrument','Platform','MOUNTED_ON'),
                                 ('Weather_Event', 'Continent', 'IMPACTED_REGION'),
                                 ('Weather_Event', 'Country', 'IMPACTED_REGION'),
                                 ('Weather_Event', 'Country_Subdivision', 'IMPACTED_REGION'),
                                 ('Weather_Event', 'City', 'IMPACTED_REGION'), 
                                 ('Weather_Event', 'No_Country_Region', 'IMPACTED_REGION'),
                                 ('Teleconnection', 'Continent', 'INFLUENCES_REGION'),
                                 ('Teleconnection', 'Country', 'INFLUENCES_REGION'),
                                 ('Teleconnection', 'Country_Subdivision', 'INFLUENCES_REGION'),
                                 ('Teleconnection', 'City', 'INFLUENCES_REGION'), 
                                 ('Teleconnection', 'No_Country_Region', 'INFLUENCES_REGION'),
                                 ('Ocean_Circulation', 'Continent', 'OBSERVED_IN_REGION'),
                                 ('Ocean_Circulation', 'Country', 'OBSERVED_IN_REGION'),
                                 ('Ocean_Circulation', 'Country_Subdivision', 'OBSERVED_IN_REGION'),
                                 ('Ocean_Circulation', 'City', 'OBSERVED_IN_REGION'), 
                                 ('Ocean_Circulation', 'No_Country_Region', 'OBSERVED_IN_REGION'),
                                 ('Natural_Hazard', 'Continent', 'AFFECTED_REGION'),
                                 ('Natural_Hazard', 'Country', 'AFFECTED_REGION'),
                                 ('Natural_Hazard', 'Country_Subdivision', 'AFFECTED_REGION'),
                                 ('Natural_Hazard', 'City', 'AFFECTED_REGION'), 
                                 ('Natural_Hazard', 'No_Country_Region', 'AFFECTED_REGION'),
                                 ('Natural_Hazard', 'NaturalHazardType', 'IS_OF_TYPE'),
                                 ('No_Country_Region', 'ClimateZone', 'HAS_CLIMATE_ZONE')
                                 ]
实体类型定义：
{ENTITY_TYPE_DEFINITIONS}
{EXTRACTION_BOUNDARIES}
关系定义：
INCLUDES_EXPERIMENT：实验族包含具体实验。
HAS_SUB_EXPERIMENT：实验具有细化、缩小范围或扩展的子实验。
IS_OF_TYPE：来源对应其模型配置类型，或自然灾害对应其灾害类型。
APPLIES_TO_REALM：来源、组件或实验关联某个地球系统领域。
FOCUSES_ON_REALM：活动旨在研究或重点关注某个领域。
PARTICIPATED_IN：机构参与项目。
GENERATED_BY_ACTIVITY：来源作为某项活动的一部分产生。
PRODUCED_BY_INSTITUTE：来源由某个机构负责产生。
BELONGS_TO_MIP_ERA：来源或实验族属于某个CMIP代际。
USED_IN_EXPERIMENT：来源或区域气候模型用于某个实验。
PRODUCES_VARIABLE：来源或组件输出某个变量。
HAS_SUBSEQUENT_VERSION：来源或组件具有直接后续版本。
INHERITED_FROM：来源由另一来源衍生或继承其特征。
HAS_SOURCE_COMPONENT：来源包含组件，或组件包含子组件。
USES_PHYSICAL_SCHEME：来源使用某个物理方案，如辐射或对流方案。
HAS_PHYSICAL_FEATURE：来源具有某种物理／设计特征，如分辨率或地形处理。
HAS_SIMULATION_TYPE：来源对应某种模拟类型，如控制、历史或情景运行。
HAS_METRIC：来源关联用于评价其性能的具体指标。
PART_OF_PROJECT：来源、区域气候模型、实验或活动属于某项目。
ASSOCIATED_WITH_MEMBER：来源关联某个集合成员。
SAMPLED_AT_FREQUENCY：来源按某时间频率输出。
HAS_SPATIAL_RESOLUTION：来源、变量、区域气候模型或实验具有某空间分辨率。
PART_OF_ENSEMBLE：来源或子实验属于某个集合。
USES_FORCING：实验使用某种强迫，如温室气体、气溶胶或太阳强迫。
INCLUDES_ENSEMBLE_MEMBER：实验包含某集合成员。
DRIVEN_BY_SOURCE：区域气候模型由某个来源驱动，通常是全球气候模型。
COVERS_DOMAIN：区域气候模型或项目覆盖某个域。
MEASURES_VARIABLE：仪器测量某个变量。
MOUNTED_ON：仪器搭载于某个平台。
PERFORMED_BY_INSTITUTE：实验由某个机构实施。
COVERS_REGION：区域气候模型覆盖某个地理区域。
IMPACTED_REGION：天气事件影响某区域。
INFLUENCES_REGION：遥相关影响某区域。
OBSERVED_IN_REGION：海洋环流现象在某区域被观测。
AFFECTED_REGION：自然灾害影响某区域。
IN_CONTINENT：国家位于某大洲。
IN_COUNTRY：行政区或城市位于某国家。
HAS_CLIMATE_ZONE：非国家区域对应某气候区分类。
仅输出论文中实际有支持的三元组。
"""

# 早期局部验证模板已停用，现行模板如下。

VALIDATION_PROMPT = """给定气候建模或地球系统科学论文的一个段落，以及从中抽取的候选三元组（来源实例、关系、目标实例）。
知识图谱主要由 ESGF 元数据及相关受控词表构成。每个实例附有语义类型。
判断段落是否合理支持该三元组。允许：
- 来源或目标未逐字出现，但可从上下文推知；
- 利用领域知识解析缩写或隐含提及；
- 解析段落中的指代或隐含引用。
段落明确陈述或清楚暗示该关系时判 valid；关系被否定或文本不支持时判 invalid。
只返回 valid 或 invalid，不解释，不添加其他文字。
来源：{source}
来源类型：{source_type}
关系：{relation}
目标：{target}
目标类型：{target_type}
段落上下文：
{paragraph}
"""



GPT_VALIDATION_PROMPT = """给定气候建模或地球系统科学相关段落及从中抽取的三元组（来源实例、关系、目标实例）。
知识图谱主要由 ESGF 元数据构成。核查论文段落是否支持三元组。
允许段落存在指代，利用已有知识将来源或目标代入段落上下文。
只回答 valid 或 invalid，不解释。
来源：{source}
关系：{relation}
目标：{target}
段落上下文：{paragraph}
"""

EMBEDDING_FALLBACK_LLM_PROMPT = """给定从段落上下文抽取的某类型实体关键词／短语，选择它最可能指代的候选实例。
若所有实例均不匹配，返回 no match。不解释。
关键词／短语：{key_word_phrase}
实体类型：{entity_type}
候选实例：
{instances}
段落上下文：{paragraph}
"""
