# Lessons — 经验教训库

> 加载策略：On Demand | 大小限制：<50KB
> Warm/Cold 条目的原文归档于此

## mx-data 踩坑
- A股 ticker 格式：6开头→`.SS`，0/3开头→`.SZ`（mx-data查询必须转换）
- 港股 ticker 格式：HK02590→02590.HK
- mx-data 返回多表时需遍历所有 dataTableDTOList，用 nameMap 做语义映射
- 技术指标解析：MACD/RSI 在表1，MA5/MA20 在表2，不能只取第一张表
- CLI 关键词必须精确匹配 mx-data 预定义字段名，模糊查询会返回空结果

## API 配额管理
- Bocha/Tavily 搜索配额容易耗尽，需提前规划缓存策略
- mx-data 3组 API key 轮换机制：自动切换但总量有限
- 文件缓存 TTL 分层：共识数据4h、财报12h、公司画像24h
