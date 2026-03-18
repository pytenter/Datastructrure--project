# 新能源物流调度对比报告

## 场景: small
### 动态策略排名
| 排名 | 策略 | 分数 | 完成 | 超时 | 未完成 |
|---|---|---:|---:|---:|---:|
| 1 | metaheuristic_sa | 1858.07 | 14 | 1 | 0 |
| 2 | nearest_task_first | 1852.47 | 14 | 1 | 0 |
| 3 | hyper_heuristic_ucb | 1851.54 | 14 | 1 | 0 |
| 4 | reinforcement_q | 1851.39 | 14 | 1 | 0 |
| 5 | max_task_first | 1835.47 | 14 | 1 | 0 |
| 6 | urgency_distance | 1835.47 | 14 | 1 | 0 |
| 7 | auction_multi_agent | 1835.47 | 14 | 1 | 0 |

### 静态全信息求解
- 求解模式: `static_exact_cplex`
- 后端: `cplex`
- 状态: `time limit exceeded`
- 是否证明最优: `False`
- MIP Gap: `0.267757`
- 求解耗时: `1.15s`
- 与最佳动态策略分差(静态-动态): `269.56`
