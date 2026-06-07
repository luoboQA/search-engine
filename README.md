# Goals
使用 Redis 作为数据存储，实现了一个简单的 TF/IDF 索引和搜索算法。

- 索引：倒排列表
- 检索：加权并集

TF（词频）：衡量一个词在当前文档中重不重要（出现次数多就重要）
IDF（逆文档频率）：衡量一个词在整个文档集合中稀不稀有（出现文档越少越重要）

算法使用标准的 TF/IDF 评分公式（Python 表示法）：
    sum((文档中词频 / 文档总词数) * log(总文档数 / 包含该词的文档数, 2) 对所有查询词求和)
$$
score(D, Q) = \sum_{t \in Q} \left( \frac{f_{t,D}}{|D|} \right) \times \log_2 \left( \frac{N}{df(t)} \right)
$$

其中：
- $D$：目标文档
- $Q$：查询词集合
- $t$：查询中的某个词
- $f_{t,D}$：词 $t$ 在文档 $D$ 中出现的次数（原始词频）
- $|D|$：文档 $D$ 的总词数
- $N$：文档集合中的总文档数
- $df(t)$：包含词 $t$ 的文档数量

# 子公式

TF（词频）
$$\text{TF}(t, d) = \frac{\text{词 } t \text{ 在文档 } d \text{ 中出现的次数}}{\text{文档 } d \text{ 的总词数}}$$

IDF（逆文档频率）
$$\text{IDF}(t) = \log\left(\frac{N}{df(t)}\right)$$

其中：
- $N$ 是文档总数
- $df(t)$ 是包含词 $t$ 的文档数量

TF-IDF:
$$\text{TF-IDF}(t, d) = \text{TF}(t, d) \times \text{IDF}(t)$$

# 直观理解
TF 高 + IDF 高 → 这个词在当前文档中既常见又稀有 → 对这篇文档很重要

TF 高 + IDF 低（比如“的”） → 常见但没区分度 → 最终得分被拉低

# Resource
https://www.dr-josiah.com/2010/07/building-search-engine-using-redis-and.html