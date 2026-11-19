"""Shared domain and sufficient-path rules; no annotation lookup or verdicts."""
INDICATOR_SCOPE = '''科学计量指标准入范围（适用于每条关系的所有Indicator端点）：
测量学术文献、专利、作者、期刊、机构、学科、技术领域、研究主题的科研产出、引用影响、学术使用、参考文献特征、知识结构、多样性、技术距离、合作、主题分布或演化。
允许基础计量：发文量、被引次数、Usage count、Number of references、Number of articles per journal（表中Total articles）。按期刊的论文数属于科研产出/分布，不因也描述语料构成而排除。
允许主题流行度、年度主题占比、Annual Growth Rate及Average Annual Growth Rate用于文献主题演化。不能把这些与模型内部θ、β、coherence、perplexity或概率阈值覆盖统计混同。
允许具有技术关系测量含义的技术距离；technological distance与technology distance是同一测量概念的措辞，Tech_dist/Tech_dis等符号需按原文含义与角色绑定。0/1不自动排除这类知识结构测度。
排除HDI/GINI/教育/寿命等社会经济发展指数；排除通用统计量、相关系数、显著性、拟合优度、编码一致性；排除标题字数、页数、年份/地区/方法编码等普通属性；排除模型内部概率、词权重、质量分数和阈值文档数量/百分比。
排除仅为计算中间量的P_g/p_i、thematic strengths、mean-field citations等参考基准；独立的归一化/分数计数引用分数不因此一概排除。
不能仅因具名、有数值、公式或进入回归就承认指标，也不能因是控制变量或非复合指数而排除。局部引用/使用计数、正式期刊指标、知识结构测度的结果无需每组重复全文定义。
候选声明Indicator是待核查类型；须检查给定原文中的测量对象和实际角色，不读取金标标签。
'''
SUFFICIENT_PATHS = '''充分路径执行规则：各路径之间OR，路径内必要项AND。一个句子可以覆盖多个条件。只要一条完整路径成立，就不能额外强求另一条路径的公式、独立数值、历史定义或全名重复。
Paper由元数据识别；当前论文的结果句、图表描述和讨论中的本研究结果归属当前Paper，不要求重复we/标题。名字可由语义全称、缩写、数学符号和明确别名绑定；citation/citations/cited more frequently在论文被引语境下即引用计数，References在参考文献计数表中即Number of references，JIF/SJR可绑定标准具名期刊指标。不能借给未被提及的另一个指标。
proposes：当前作者直接具名构建；或构建类别+目标具名定义及层级连接；或构建后命名；或有当前工作先行项的变体组名单。任一路径充分。
modifies：当前工作直接修改声明；或原新规则对照并归属当前作者；或构建新版并明确对基准具体构造作改造。不是只有提出新版名字就自动修改任意指标。
applies有五条充分路径：明确当前采用/计算；已采用的分析角色；当前数值/分布/分组/相关/回归/比较结果；计算演示+真实数据；目标的结果实际进入另一个已应用测度的计算。最后一条不要求再单列底层指标的结果。
当前对象被描述为指标高/低组、按指标门槛划分类别，或报告其数量/占比差异，已经应用该指标，不是未来分类计划。Figure presents distribution across levels of A/B同时支持使用A和B。当前研究讨论“higher A led to more B”“A was influential”“did not predict B”“we found no relationship”均报告实际分析；不显著、负向、无关联仍支持双方各自被使用，不要求结果显著或再给系数表。单独背景普遍猜测或未来may研究不足；讨论中的may lower若明确是当前分析结果的审慎表述不因may一词自动否决。
若S是两份列表A分数的和，且报告S当前分布/分类/回归，计算该S结果已经实际使用A；无需再给A的公式或A的单项数值。组内给定名单+“these variants computed ... in this dataset”可以逐一绑定实际使用，不要求每个变体单独写一次computed。
variant_of：明确变体归属；或完整构造对照且核心保留；或有先行项的变体组成员。方向新→基准。
derived_from：明确来源+实质改造；完整机制对照；或逐步构造链。仅嵌套称谓不代替实质聚合变化证据；主体指标值进入新阈值/均值聚合是实质变化。与简单适配variant_of互斥。
improves_on：直接具名改进声明；或基准局限+新指标应对同一局限；或局限+机制+来源采用机制。直接声明/明确能力补足不额外强求底层算法、数值提升。
alternative_to：直接同任务替代声明；或基准任务+组级条件替代规则+具名成员归属。保留条件，不要求实际样本已满足条件。
component_of方向固定为输入A→使用它的B。明确组成句已是完整路径，不要求再额外提供A/B定义或独立公式。例如“B is the sum of the corresponding A scores of two lists”直接证明A component_of B，B作语法主语不改变计算依赖方向。并列“B or D is the sum of corresponding A or C”按corresponding的语义逐项配对A→B、C→D，不交叉配对。
component_of也可由具名公式输入、完整中间步骤、同版本间接依赖链证明。完整精确子表达式已定义为A值且在B原公式中出现，逐项对象/权重/时间/数值一致即可；不要求改写成A符号，不能借代数重排或共享原数据推导组成。
公式引导句属于证据：明确说“从年度增长率计算平均年度增长率”就是输入/输出绑定，后接Total Growth Rate/Number of Years可与引导句联合辨认该计算链；保持原公式，不要求替换分子名或补造未给的等式。若给定文本只是另一个量的公式、无来源绑定，则不能臆造输入。
方法在其他时期/对象重算、相关性、共享数据均不是component_of；反方向拒绝。只有RQ、定义或理论取值范围不证明当前applies。每个supported须引用给定原文ID；不通过候选名称补证。
'''
TYPE_AUDIT_PROMPT = INDICATOR_SCOPE + '''独立核对当前候选target的类型，只看原文对target的测量含义与角色，不将原文其他实体的类型移给target。引用结果句、具名期刊分数行无需重复定义；常见全称/缩写及普通单复数允许语义绑定。排除社会经济指数时也须引用其原文ID。
只返回JSON {"role":"scientific_metric或formal_index或out_of_scope或content_attribute或metadata或method或unknown","ids":["原文ID"],"reason":"原文依据"}。formal_index仅限项目科学计量范围，HDI等填out_of_scope。仅未知缩写可填unknown，不自动推为普通属性。'''

