# 新能源物流调度对比报告

## 场景: large
### 动态策略排名
| 排名 | 策略 | 分数 | 完成 | 超时 | 未完成 |
|---|---|---:|---:|---:|---:|
| 1 | auction_multi_agent | 32487.96 | 220 | 12 | 0 |
| 2 | hyper_heuristic_ucb | 32420.66 | 220 | 15 | 0 |
| 3 | urgency_distance | 32399.52 | 220 | 14 | 0 |
| 4 | metaheuristic_sa | 32339.30 | 220 | 14 | 0 |
| 5 | reinforcement_q | 32250.00 | 220 | 18 | 0 |
| 6 | max_task_first | 32225.80 | 220 | 15 | 0 |
| 7 | nearest_task_first | 31951.03 | 220 | 17 | 0 |

## 场景: medium
### 动态策略排名
| 排名 | 策略 | 分数 | 完成 | 超时 | 未完成 |
|---|---|---:|---:|---:|---:|
| 1 | nearest_task_first | 12975.19 | 110 | 27 | 0 |
| 2 | urgency_distance | 12604.20 | 110 | 29 | 0 |
| 3 | metaheuristic_sa | 12524.47 | 110 | 29 | 0 |
| 4 | hyper_heuristic_ucb | 12499.98 | 110 | 31 | 0 |
| 5 | max_task_first | 12383.70 | 110 | 30 | 0 |
| 6 | reinforcement_q | 12382.56 | 110 | 32 | 0 |
| 7 | auction_multi_agent | 12312.43 | 110 | 31 | 0 |

## 场景: small
### 动态策略排名
| 排名 | 策略 | 分数 | 完成 | 超时 | 未完成 |
|---|---|---:|---:|---:|---:|
| 1 | reinforcement_q | 988.25 | 8 | 0 | 8 |
| 2 | hyper_heuristic_ucb | 983.71 | 8 | 0 | 8 |
| 3 | auction_multi_agent | 926.31 | 8 | 0 | 8 |
| 4 | metaheuristic_sa | 849.33 | 8 | 0 | 8 |
| 5 | nearest_task_first | 768.96 | 7 | 0 | 9 |
| 6 | max_task_first | 682.02 | 7 | 0 | 9 |
| 7 | urgency_distance | 416.45 | 6 | 0 | 10 |

### 静态全信息求解
- 求解模式: `static_exact_gurobi`
- 后端: `gurobi`
- 状态: `OPTIMAL`
- 是否证明最优: `True`
- MIP Gap: `0.000000`
- 求解耗时: `0.05s`
- 与最佳动态策略分差(静态-动态): `72.18`
