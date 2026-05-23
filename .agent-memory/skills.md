# Skills — 技能与经验

> 加载策略：On Demand | 大小限制：<2KB
> 温度分层：条目按日期自动迁移（Hot≤14天 / Warm 14-60天 / Cold>60天）

## Hot（≤14天）

### 2026-05-21 | mx-data API 配额优化
- 合并 analyst_target + analyst_rating → analyst_consensus（2次查询→1次）
- 催化剂搜索 7组→2组
- 实现 cache.py 文件缓存层（TTL: 4h/12h/24h）
- fetch_company_profile / fetch_gs_financial_metrics 接受 price_data 去重
- 删除死代码 fetch_financial_from_mx / enrich_earnings_from_mx
- 修复 run_weekly_check ticker 格式错误

## Warm
（暂无）

## Cold
（暂无）
