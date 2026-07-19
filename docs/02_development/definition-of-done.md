# Definition of Done

一个版本只有满足以下条件才算完成：

- 需求文档已确认。
- 必要的 RFC / ADR 已补充。
- 代码实现与文档范围一致。
- 单元测试通过。
- 必要的集成测试通过。
- 必要的 eval 报告已生成。
- README、roadmap 或 contract 已同步更新。
- 不包含密钥、cookie、真实个人数据。
- 新增事实性输出能够追溯到 evidence；推断有明确类型和置信度。
- 新增 RAG、分布式存储或 Multi-Agent 时，已与简单基线比较并记录收益、成本和失败案例。
- 长任务版本已测试中断、恢复、幂等和停止条件。
- human-in-the-loop 输入已归档为 evidence，checkpoint 或聊天摘要没有绕过 Claim/Validator 更新画像。
- 文档设计阶段只能标记 Ready for Implementation；实际测试与 eval report 完成后才能标记 Implemented。
- 引入 live source 的版本已区分离线 CI 与 opt-in live smoke，并记录真实来源、时间、结果和限制。
- 外部内容遵守 raw-before-parse；来源 authority、freshness、去重分母和 credential redaction 均有测试。
- 如果文档要求的 live smoke 未实际通过，版本不得以 fixture 结果替代并标记 Implemented。
