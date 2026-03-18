# 新能源物流调度对比报告

## 场景: large
### 动态策略排名
| 排名 | 策略 | 分数 | 完成 | 超时 | 未完成 |
|---|---|---:|---:|---:|---:|
| 1 | reinforcement_q | 36250.64 | 220 | 6 | 0 |
| 2 | metaheuristic_sa | 36147.90 | 220 | 7 | 0 |
| 3 | urgency_distance | 36100.85 | 220 | 7 | 0 |
| 4 | auction_multi_agent | 35935.62 | 220 | 6 | 0 |
| 5 | max_task_first | 35862.45 | 220 | 7 | 0 |
| 6 | hyper_heuristic_ucb | 35854.60 | 220 | 8 | 0 |
| 7 | nearest_task_first | 35736.95 | 220 | 8 | 0 |

### 静态全信息求解
- 求解模式: `static_exact_cplex`
- 后端: `cplex`
- 状态: `time limit exceeded`
- 是否证明最优: `False`
- MIP Gap: `2.468633`
- 求解耗时: `1375.08s`
- 与最佳动态策略分差(静态-动态): `231.98`

## 场景: medium
### 动态策略排名
| 排名 | 策略 | 分数 | 完成 | 超时 | 未完成 |
|---|---|---:|---:|---:|---:|
| 1 | nearest_task_first | 18692.42 | 110 | 7 | 0 |
| 2 | auction_multi_agent | 18550.88 | 110 | 7 | 0 |
| 3 | metaheuristic_sa | 18536.81 | 110 | 7 | 0 |
| 4 | urgency_distance | 18530.90 | 110 | 7 | 0 |
| 5 | hyper_heuristic_ucb | 18463.19 | 110 | 8 | 0 |
| 6 | max_task_first | 18426.67 | 110 | 7 | 0 |
| 7 | reinforcement_q | 18371.02 | 110 | 10 | 0 |

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
