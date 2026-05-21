Refactor_Spec: Dual Engine Analysis 重构规格说明书
1. 目标 (Objectives)
格式标准化：确保分析报告输出格式统一（结构化 JSON），去除冗余日志，统一数值精度。

逻辑解耦：将数据解析、计算逻辑与报告生成模块彻底分离。

准确性增强：所有数值计算必须使用高精度处理，消除浮点数精度误差。

2. 输入输出规范 (I/O Specification)
2.1 输入源
保持现有 dual_engine_analyze.py 的输入接口不变，确保能处理当前格式的数据字典。

2.2 输出规范 (Target JSON Format)
重构后的代码必须输出如下结构的 JSON 报告：

JSON
{
  "timestamp": "ISO8601",
  "engine_status": { "engine1": "active", "engine2": "active" },
  "metrics": {
    "precision_factor": "0.000000",
    "composite_score": "0.00"
  },
  "metadata": { "version": "1.0.0", "engine_id": "dual-analysis-v2" }
}
3. 技术约束 (Constraints)
精度控制：引入 decimal 库进行核心指标计算，禁用 float 进行财务/评分运算。

模块化：

DataParser: 负责清洗和标准化原始数据。

EngineProcessor: 负责双引擎的核心交叉逻辑。

ReportGenerator: 负责按照上述 JSON 规范格式化输出。

异常处理：若任何引擎数据缺失或格式非法，须抛出自定义异常 AnalysisError，且必须捕获并记录在报告中的 error_log 字段。

4. 验收与质量检查 (Verification & QA)
重构完成后，必须通过以下自动化校验：

完整性测试：验证输出 JSON 的 schema 是否与上述规范完全匹配。

数值一致性测试：对比旧代码与重构代码在 1000 组随机模拟数据下的计算结果差异（差异值应 < 1e-9）。

零异常运行：确保在输入非法数据（如空值、非数字字符串）时，系统能稳健报错而非崩溃。