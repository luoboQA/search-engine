'''
redis_search.py
Written by Josiah Carlson July 3, 2010
Released into the public domain.
Modified for Python 3 compatibility.

This module implements a simple TF/IDF indexing and search algorithm using
Redis as a datastore server.  The particular algorithm implemented uses the
standard TF/IDF scoring of (using Python notation):
    sum((document.count(term) / len(document)) *
         log(index.doc_count() / len(index.docs_with(term)), 2)
         for term in terms)

The blog post discussing the development of this Gist:
http://dr-josiah.blogspot.com/2010/07/building-search-engine-using-redis-and.html
'''

import collections
import math
import os
import re
import unittest
import binascii  

import redis

NON_WORDS = re.compile("[^a-z0-9' ]") # ^ 非

# stop words pulled from the below url
# http://www.textfixer.com/resources/common-english-words.txt
# 停用词集合：常见无意义的词，搜索时会被忽略
STOP_WORDS = set('''a able about across after all almost also am among
an and any are as at be because been but by can cannot could dear did
do does either else ever every for from get got had has have he her
hers him his how however i if in into is it its just least let like
likely may me might most must my neither no nor not of off often on
only or other our own rather said say says she should since so some
than that the their them then there these they this tis to too twas us
wants was we were what when where which while who whom why will with
would yet you your'''.split())

class ScoredIndexSearch(object):
    def __init__(self, prefix, *redis_settings):
        # All of our index keys are going to be prefixed with the provided
        # prefix string.  This will allow multiple independent indexes to
        # coexist in the same Redis db.
        self.prefix = prefix.lower().rstrip(':') + ':'

        # Create a connection to our Redis server.
        self.connection = redis.Redis(*redis_settings)

    @staticmethod
    def get_index_keys(content, add=True):
        # Very simple word-based parser.  We skip stop words and single
        # character words.
        words = NON_WORDS.sub(' ', content.lower()).split() # eg:"hello@world!" → "hello world "（@和!被替换成空格）最后.split())
        words = [word.strip("'") for word in words] # 去掉字符串两端的单引号 '
        words = [word for word in words
                    if word not in STOP_WORDS and len(word) > 1] # 去掉停用词和单字符词 eg: ...
        # Apply the Porter Stemmer here if you would like that functionality.

        # Apply the Metaphone/Double Metaphone algorithm by itself, or after
        # the Porter Stemmer.

        # 当 add=False（查询模式）时，直接返回词列表，不计算 TF/IDF 权重
        if not add:
            return words

        # Calculate the TF portion of TF/IDF.
        counts = collections.defaultdict(float) # 建一个字典，当访问不存在的键时，会自动返回默认值 0.0（因为 float() 返回 0.0）
        # 统计每个词在文档中出现的原始次数
        for word in words:
            counts[word] += 1
        wordcount = len(words)
        # Python 3: iteritems() 已移除，改为 items()
        """ TF Eg:
        {
            'quick': 0.25,
            'brown': 0.125,
            'fox': 0.25,
            'jumps': 0.125,
            'lazy': 0.125,
            'dog': 0.125
        }"""
        tf = dict((word, count / wordcount)
                    for word, count in counts.items()) # 文档的总词数归一化计算每个词的 TF 值 (词频),避免长文档天然得分更高
        return tf

    def _handle_content(self, id, content, add=True):
        # 文档索引方法，当 add=True 时添加索引，当 add=False 时删除索引
        # Get the keys we want to index.
        keys = self.get_index_keys(content)
        prefix = self.prefix # 索引前缀，eg: "unittest:"

        # Use a non-transactional pipeline here to improve performance.
        pipe = self.connection.pipeline(False) # False = 非事务模式，创建管道减少网络往返，将多个命令打包一次性发送到 Redis

        # Since adding and removing items are exactly the same, except
        # for the method used on the pipeline, we will reduce our line
        # count.
        """ Index eg: 
        全局索引：N 个文档 ID

        倒排索引：N × M 个 (文档ID, TF) 对

        全局索引集合
        unittest:indexed: -> {1, 2}

        倒排索引（有序集合）
        unittest:hello -> {(1, 0.5)}
        unittest:world -> {(1, 0.5), (2, 0.35)}
        unittest:nice -> {(2, 0.25)}
        unittest:really -> {(2, 0.45)}
        unittest:special -> {(2, 0.25)}


        ┌─────────────────────────────────────────────────────────┐
        │                    全局文档集合 SCARD                    │
        │              unittest:indexed: (Set)                    │
        │                   {"1", "2", "3"}                       │
        └─────────────────────────────────────────────────────────┘
                           │
                           │ 提供 total_docs
                           ▼
        ┌─────────────────────────────────────────────────────────┐
        │                    倒排索引集合                          │
        │                                                         │
        │  unittest:hello (Sorted Set) unittest:world (Sorted Set)│
        │       {"1":0.5, "2":0.3}           {"1":0.5, "3":0.4}   │
        │                                                         │
        │  ZCARD = 2                        ZCARD = 2             │
        └─────────────────────────────────────────────────────────┘
        """
        # 将文档ID加入全局已索引集合
        if add:
            pipe.sadd(prefix + 'indexed:', id)
            # Python 3: iteritems() 已移除，改为 items()
            # Redis-py 3.x: zadd 语法变更为 zadd(key, {member: score})
            # # 对每个词：在倒排索引中添加文档ID及其TF值
            for key, value in keys.items():
                pipe.zadd(prefix + key, {id: value})
        else:
            # 从全局集合移除文档ID
            pipe.srem(prefix + 'indexed:', id)
            # 从每个词的倒排索引中删除该文档
            for key in keys:
                pipe.zrem(prefix + key, id)

        # Execute the insertion/removal.
        pipe.execute()

        # Return the number of keys added/removed.
        return len(keys)
    
    # Api: 
    def add_indexed_item(self, id, content):
        return self._handle_content(id, content, add=True)

    def remove_indexed_item(self, id, content):
        return self._handle_content(id, content, add=False)

    def search(self, query_string, offset=0, count=10):
        # 将用户输入的查询字符串转换为对应的 Redis 键名
        # 假设 query_string = "hello world python"
        # get_index_keys(query_string, False) 返回：['hello', 'world', 'python']
        # 添加前缀后：['unittest:hello', 'unittest:world', 'unittest:python']
        keys = [self.prefix + key
                    for key in self.get_index_keys(query_string, False)]

        if not keys:
            return [], 0

        def idf(count):
            # Calculate the IDF for this particular count
            # eg: total_docs = 100，词出现在 10 个文档中：IDF = log2(100/10) = log2(10) ≈ 3.32
            # 词出现在 100 个文档中：IDF = log2(100/100) = log2(1) = 0
            if not count:
                return 0
            return max(math.log(total_docs / count, 2), 0) # max(..., 0)：确保 IDF 非负

        total_docs = max(self.connection.scard(self.prefix + 'indexed:'), 1) # SCARD - 返回集合的基数（元素个数）

        # Get our document frequency values... 将多个命令打包一次性发送到 Redis
        pipe = self.connection.pipeline(False)

        """
        keys = ['unittest:hello', 'unittest:world', 'unittest:python']
        假设：
        hello 出现在 100 个文档中
        world 出现在 50 个文档中
        python 出现在 30 个文档中
        sizes = [100, 50, 30]
        """
        for key in keys:
            pipe.zcard(key) # 获取包含该词的文档数
        sizes = pipe.execute()

        # Calculate the inverse document frequencies...
        # Python 3: map 返回迭代器，需要用 list() 转换以便后续使用
        idfs = list(map(idf, sizes)) # 计算idf

        # And generate the weight dictionary for passing to zunionstore.
        """
        假设有以下数据
        keys = ['unittest:python', 'unittest:data', 'unittest:science', 'unittest:ai']
        sizes = [20, 15, 5, 0]           # ZCARD 结果（包含该词的文档数）
        idfs = [2.32, 2.74, 4.32, 0]     # IDF 值
        step1: zip 并行配对
        zipped = zip(keys, sizes, idfs)
        输出：
        [
        ('unittest:python', 20, 2.32),    # (键, 文档数, IDF值)
        ('unittest:data', 15, 2.74),
        ('unittest:science', 5, 4.32),
        ('unittest:ai', 0, 0)
        ]

        step2: 生成器表达式（带过滤）
        遍历每个三元组，只保留 size > 0 的
        generator = ((key, idfv) 
             for key, size, idfv in zip(keys, sizes, idfs) 
             if size)
        这个生成器会产生：
        第1个: ('unittest:python', 2.32)  # size=20 > 0
        第2个: ('unittest:data', 2.74)    # size=15 > 0
        第3个: ('unittest:science', 4.32) # size=5 > 0
        第4个: 被跳过（size=0）

        step3: dict() 转换为字典
        """

        weights = dict((key, idfv)
                for key, size, idfv in zip(keys, sizes, idfs) if size)

        if not weights:
            return [], 0

        # Generate a temporary result storage key,避免多个并发查询互相干扰
        # Python 3: encode('hex') 已移除，使用 binascii.hexlify

        random_bytes = os.urandom(8) # 生成8个随机字节 例如：b'\x8e\x12\x45\xa3\x7f\xc9\x1b\x4d'
        # 转换为16进制字符串，hexlify，例如：'8e1245a37fc91b4d'
        # 组合成完整的临时键名，例如：'unittest:temp:8e1245a37fc91b4d'
        temp_key = self.prefix + 'temp:' + binascii.hexlify(random_bytes).decode('ascii')
        # 执行加权合并
        try:
            # Actually perform the union to combine the scores.
            # # 语法：ZUNIONSTORE destination numkeys key [key ...] [WEIGHTS weight] [AGGREGATE SUM|MIN|MAX]
            """
            假设 Redis 中有以下数据：
            unittest:hello = {1: 0.5, 2: 0.3, 3: 0.4}   # (文档ID: TF值)
            unittest:world = {2: 0.6, 3: 0.2, 4: 0.7}

            weights = {'unittest:hello': 2.0, 'unittest:world': 1.5}

            ZUNIONSTORE 计算：
            文档1: 0.5*2.0 + 0*1.5 = 1.0
            文档2: 0.3*2.0 + 0.6*1.5 = 0.6 + 0.9 = 1.5
            文档3: 0.4*2.0 + 0.2*1.5 = 0.8 + 0.3 = 1.1
            文档4: 0*2.0 + 0.7*1.5 = 0 + 1.05 = 1.05

            结果存储在 temp_key 中：
            unittest:temp:xxx = {2: 1.5, 3: 1.1, 4: 1.05, 1: 1.0}

            known = 4 (共有4个文档匹配)

            ZREVRANGE：按分数从高到低返回 (REV = Reverse)
            offset：起始位置 (用于分页)
            offset+count-1：结束位置
            withscores=True：同时返回分数

            假设有10个结果，要获取第2页 (每页3个)
            offset = 3    跳过前3个
            count = 3     取3个
            获取位置 3, 4, 5 的元素

            ZREVRANGE temp_key 3 5 WITHSCORES
            ids = [(b'2', 1.5), (b'3', 1.1), (b'4', 1.05)]
            """
            known = self.connection.zunionstore(temp_key, weights)
            # Get the results.
            ids = self.connection.zrevrange(
                temp_key, offset, offset+count-1, withscores=True)
        finally:
            # Clean up after ourselves.
            self.connection.delete(temp_key)
        return ids, known

class TestIndex(unittest.TestCase):
    def test_index_basic(self):
        t = ScoredIndexSearch('unittest', 'localhost')
        
        keys = t.connection.keys('unittest:*') # Redis 的 KEYS 命令，匹配所有以 unittest: 开头的键
        if keys:
            t.connection.delete(*keys) 

        t.add_indexed_item(1, 'hello world') # 文档1: 两个词，各出现1次，总词数2 → 每个词 TF=0.5
        t.add_indexed_item(2, 'this world is nice and you are really special') # 文档2: 过滤停用词后剩下 ['world', 'nice', 'really', 'special']
                                                                               # 总词数4，每个词出现1次 → 每个词 TF=0.25

        # 转换搜索结果中的字节串为字符串
        def decode_result(result):
            ids, count = result
            decoded_ids = [(id.decode('utf-8'), score) for id, score in ids]
            return (decoded_ids, count)
        
        self.assertEqual(
            decode_result(t.search('hello')),
            ([('1', 0.5)], 1))
        self.assertEqual(
            decode_result(t.search('world')),
            ([('2', 0.0), ('1', 0.0)], 2))
        self.assertEqual(decode_result(t.search('this')), ([], 0))
        self.assertEqual(
            decode_result(t.search('hello really special nice world')),
            ([('2', 0.75), ('1', 0.5)], 2))

if __name__ == '__main__':
    unittest.main()
