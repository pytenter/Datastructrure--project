from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from .graph import WeightedGraph
from .models import Task, Vehicle


SERVICE_TIME_APPROX = 3.8
DEPOT_NODE = 0


@dataclass
class DispatchDecision:
    task: Task
    vehicle_ids: List[int]


class DispatchStrategy:
    name = "base"

    def choose(
        self,
        pending_tasks: Sequence[Task],
        free_vehicle_ids: Sequence[int],
        vehicles: Dict[int, Vehicle],
        graph: WeightedGraph,
        allow_collaboration: bool,
        now: int,
    ) -> Optional[DispatchDecision]:
        raise NotImplementedError


class NearestTaskFirstStrategy(DispatchStrategy):
    name = "nearest_task_first"

    def choose(
        self,
        pending_tasks: Sequence[Task],
        free_vehicle_ids: Sequence[int],
        vehicles: Dict[int, Vehicle],
        graph: WeightedGraph,
        allow_collaboration: bool,
        now: int,
    ) -> Optional[DispatchDecision]:
        best_task: Task | None = None
        best_vehicle = -1
        best_dist = float("inf")

        for task in pending_tasks:
            for vehicle_id in free_vehicle_ids:
                vehicle = vehicles[vehicle_id]
                if vehicle.capacity + 1e-9 < task.weight:
                    continue
                distance, _ = graph.shortest_path(vehicle.current_node, task.node_id)
                if distance < best_dist:
                    best_dist = distance
                    best_task = task
                    best_vehicle = vehicle_id

        if best_task is not None and best_vehicle >= 0:
            return DispatchDecision(task=best_task, vehicle_ids=[best_vehicle])

        if not allow_collaboration:
            return None

        collab_best: DispatchDecision | None = None
        collab_dist = float("inf")
        for task in pending_tasks:
            group, group_dist = _nearest_collab_group(task, free_vehicle_ids, vehicles, graph)
            if group and group_dist < collab_dist:
                collab_dist = group_dist
                collab_best = DispatchDecision(task=task, vehicle_ids=group)
        return collab_best


class MaxWeightFirstStrategy(DispatchStrategy):
    name = "max_task_first"

    def choose(
        self,
        pending_tasks: Sequence[Task],
        free_vehicle_ids: Sequence[int],
        vehicles: Dict[int, Vehicle],
        graph: WeightedGraph,
        allow_collaboration: bool,
        now: int,
    ) -> Optional[DispatchDecision]:
        best: tuple[float, Task, int] | None = None

        for task in pending_tasks:
            for vehicle_id in free_vehicle_ids:
                vehicle = vehicles[vehicle_id]
                if vehicle.capacity + 1e-9 < task.weight:
                    continue
                feat = _pair_features(task, vehicle, graph, now)
                if feat is None:
                    continue

                priority = (
                    50.0 * task.weight
                    + 920.0 * feat["urgency"]
                    - 1.45 * feat["round_trip"]
                    - 7.0 * feat["lateness"]
                    - 85.0 * feat["battery_deficit"]
                    + 24.0 * feat["battery_margin"]
                )
                if best is None or priority > best[0]:
                    best = (priority, task, vehicle_id)

        if best is not None:
            return DispatchDecision(task=best[1], vehicle_ids=[best[2]])

        if allow_collaboration:
            best_group: tuple[float, Task, List[int]] | None = None
            for task in pending_tasks:
                group, dist_sum = _capacity_first_collab_group(task, free_vehicle_ids, vehicles, graph)
                if not group:
                    continue
                slack = max(1.0, task.deadline - now)
                urgency = 1.0 / slack
                group_score = 48.0 * task.weight + 760.0 * urgency - 1.05 * dist_sum
                if best_group is None or group_score > best_group[0]:
                    best_group = (group_score, task, group)
            if best_group is not None:
                return DispatchDecision(task=best_group[1], vehicle_ids=best_group[2])

        return None


class UrgencyDistanceStrategy(DispatchStrategy):
    name = "urgency_distance"

    def choose(
        self,
        pending_tasks: Sequence[Task],
        free_vehicle_ids: Sequence[int],
        vehicles: Dict[int, Vehicle],
        graph: WeightedGraph,
        allow_collaboration: bool,
        now: int,
    ) -> Optional[DispatchDecision]:
        best: tuple[float, Task, int] | None = None

        for task in pending_tasks:
            for vehicle_id in free_vehicle_ids:
                vehicle = vehicles[vehicle_id]
                if vehicle.capacity + 1e-9 < task.weight:
                    continue
                feat = _pair_features(task, vehicle, graph, now)
                if feat is None:
                    continue

                priority = (
                    36.0 * task.weight
                    + 1700.0 * feat["urgency"]
                    - 1.85 * feat["round_trip"]
                    - 8.2 * feat["lateness"]
                    - 100.0 * feat["battery_deficit"]
                    + 22.0 * feat["battery_margin"]
                )
                if best is None or priority > best[0]:
                    best = (priority, task, vehicle_id)

        if best is not None:
            return DispatchDecision(task=best[1], vehicle_ids=[best[2]])

        if allow_collaboration:
            best_group: tuple[float, Task, List[int]] | None = None
            for task in pending_tasks:
                group, dist_sum = _nearest_collab_group(task, free_vehicle_ids, vehicles, graph)
                if not group:
                    continue
                slack = max(1.0, task.deadline - now)
                urgency = 1.0 / slack
                priority = 36.0 * task.weight + 1500.0 * urgency - 1.1 * dist_sum
                if best_group is None or priority > best_group[0]:
                    best_group = (priority, task, group)
            if best_group is not None:
                return DispatchDecision(task=best_group[1], vehicle_ids=best_group[2])

        return None


class AuctionBasedStrategy(DispatchStrategy):
    name = "auction_multi_agent"

    def choose(
        self,
        pending_tasks: Sequence[Task],
        free_vehicle_ids: Sequence[int],
        vehicles: Dict[int, Vehicle],
        graph: WeightedGraph,
        allow_collaboration: bool,
        now: int,
    ) -> Optional[DispatchDecision]:
        best_bid: tuple[float, Task, int] | None = None

        for vehicle_id in free_vehicle_ids:
            vehicle = vehicles[vehicle_id]
            for task in pending_tasks:
                if vehicle.capacity + 1e-9 < task.weight:
                    continue
                feat = _pair_features(task, vehicle, graph, now)
                if feat is None:
                    continue

                value = 34.0 * task.weight + 1280.0 * feat["urgency"]
                cost = (
                    2.1 * feat["round_trip"]
                    + 9.0 * feat["lateness"]
                    + 120.0 * feat["battery_deficit"]
                    - 18.0 * feat["battery_margin"]
                )
                bid = value - cost
                if best_bid is None or bid > best_bid[0]:
                    best_bid = (bid, task, vehicle_id)

        if best_bid is not None:
            return DispatchDecision(task=best_bid[1], vehicle_ids=[best_bid[2]])

        if allow_collaboration:
            best_group: tuple[float, Task, List[int]] | None = None
            for task in pending_tasks:
                group, dist_sum = _capacity_first_collab_group(task, free_vehicle_ids, vehicles, graph)
                if not group:
                    continue
                slack = max(1.0, task.deadline - now)
                urgency = 1.0 / slack
                group_bid = 34.0 * task.weight + 1120.0 * urgency - 1.9 * dist_sum
                if best_group is None or group_bid > best_group[0]:
                    best_group = (group_bid, task, group)
            if best_group is not None:
                return DispatchDecision(task=best_group[1], vehicle_ids=best_group[2])

        return None


class SimulatedAnnealingStrategy(DispatchStrategy):
    name = "metaheuristic_sa"

    def __init__(self, seed: int = 2026, iterations: int = 95) -> None:
        self._rnd = random.Random(seed)
        self._iterations = iterations

    def choose(
        self,
        pending_tasks: Sequence[Task],
        free_vehicle_ids: Sequence[int],
        vehicles: Dict[int, Vehicle],
        graph: WeightedGraph,
        allow_collaboration: bool,
        now: int,
    ) -> Optional[DispatchDecision]:
        candidates: List[tuple[Task, int]] = []
        for task in pending_tasks:
            for vehicle_id in free_vehicle_ids:
                if vehicles[vehicle_id].capacity + 1e-9 < task.weight:
                    continue
                candidates.append((task, vehicle_id))

        if candidates:
            current = self._rnd.choice(candidates)
            current_score = _estimate_pair_value(current[0], vehicles[current[1]], graph, now)
            best = current
            best_score = current_score

            temp = 26.0
            for _ in range(self._iterations):
                neighbor = self._rnd.choice(candidates)
                neighbor_score = _estimate_pair_value(neighbor[0], vehicles[neighbor[1]], graph, now)
                delta = neighbor_score - current_score
                if delta >= 0 or self._rnd.random() < math.exp(delta / max(0.001, temp)):
                    current = neighbor
                    current_score = neighbor_score
                if current_score > best_score:
                    best = current
                    best_score = current_score
                temp *= 0.95

            return DispatchDecision(task=best[0], vehicle_ids=[best[1]])

        if not allow_collaboration:
            return None

        collab_candidates: List[tuple[float, Task, List[int]]] = []
        for task in pending_tasks:
            group, dist_sum = _nearest_collab_group(task, free_vehicle_ids, vehicles, graph)
            if not group:
                continue
            slack = max(1.0, task.deadline - now)
            urgency = 1.0 / slack
            value = 36.0 * task.weight + 1100.0 * urgency - 1.1 * dist_sum
            collab_candidates.append((value, task, group))

        if not collab_candidates:
            return None

        collab_candidates.sort(key=lambda item: item[0], reverse=True)
        return DispatchDecision(task=collab_candidates[0][1], vehicle_ids=collab_candidates[0][2])


class ReinforcementLearningDispatchStrategy(DispatchStrategy):
    name = "reinforcement_q"

    def __init__(self, seed: int = 2026, epsilon: float = 0.12, alpha: float = 0.30) -> None:
        self._rnd = random.Random(seed)
        self._epsilon = epsilon
        self._alpha = alpha
        self._actions = ["nearest", "max_weight", "urgency", "auction"]
        self._q: Dict[tuple[int, int, int, int], Dict[str, float]] = {}

    def choose(
        self,
        pending_tasks: Sequence[Task],
        free_vehicle_ids: Sequence[int],
        vehicles: Dict[int, Vehicle],
        graph: WeightedGraph,
        allow_collaboration: bool,
        now: int,
    ) -> Optional[DispatchDecision]:
        if not pending_tasks or not free_vehicle_ids:
            return None

        state = _state_bucket(pending_tasks, free_vehicle_ids, vehicles, now)
        qvals = self._q.setdefault(state, {action: 0.0 for action in self._actions})

        if self._rnd.random() < self._epsilon:
            action = self._rnd.choice(self._actions)
        else:
            action = max(self._actions, key=lambda name: qvals[name])

        best = _best_pair_by_heuristic(action, pending_tasks, free_vehicle_ids, vehicles, graph, now)
        if best is not None:
            _, task, vehicle_id, feat = best
            reward = _estimated_immediate_reward(task, feat)
            qvals[action] = (1.0 - self._alpha) * qvals[action] + self._alpha * reward
            return DispatchDecision(task=task, vehicle_ids=[vehicle_id])

        if allow_collaboration:
            collab = _best_collab_decision(pending_tasks, free_vehicle_ids, vehicles, graph, now)
            if collab is not None:
                return collab
        return None


class HyperHeuristicStrategy(DispatchStrategy):
    name = "hyper_heuristic_ucb"

    def __init__(self, seed: int = 2026) -> None:
        self._rnd = random.Random(seed)
        self._actions = ["nearest", "max_weight", "urgency", "auction"]
        self._counts: Dict[str, int] = {name: 0 for name in self._actions}
        self._means: Dict[str, float] = {name: 0.0 for name in self._actions}
        self._total_round = 0

    def choose(
        self,
        pending_tasks: Sequence[Task],
        free_vehicle_ids: Sequence[int],
        vehicles: Dict[int, Vehicle],
        graph: WeightedGraph,
        allow_collaboration: bool,
        now: int,
    ) -> Optional[DispatchDecision]:
        if not pending_tasks or not free_vehicle_ids:
            return None

        self._total_round += 1
        action = self._select_ucb_action()

        best = _best_pair_by_heuristic(action, pending_tasks, free_vehicle_ids, vehicles, graph, now)
        if best is not None:
            _, task, vehicle_id, feat = best
            reward = _estimated_immediate_reward(task, feat)
            self._update_action_reward(action, reward)
            return DispatchDecision(task=task, vehicle_ids=[vehicle_id])

        if allow_collaboration:
            collab = _best_collab_decision(pending_tasks, free_vehicle_ids, vehicles, graph, now)
            if collab is not None:
                return collab
        return None

    def _select_ucb_action(self) -> str:
        untried = [name for name in self._actions if self._counts[name] == 0]
        if untried:
            return self._rnd.choice(untried)

        log_term = math.log(max(2, self._total_round))
        best_action = self._actions[0]
        best_ucb = float("-inf")
        for action in self._actions:
            bonus = math.sqrt(2.0 * log_term / self._counts[action])
            ucb = self._means[action] + bonus
            if ucb > best_ucb:
                best_ucb = ucb
                best_action = action
        return best_action

    def _update_action_reward(self, action: str, reward: float) -> None:
        old_count = self._counts[action]
        new_count = old_count + 1
        old_mean = self._means[action]
        self._counts[action] = new_count
        self._means[action] = old_mean + (reward - old_mean) / float(new_count)


def _estimate_pair_value(task: Task, vehicle: Vehicle, graph: WeightedGraph, now: int) -> float:
    feat = _pair_features(task, vehicle, graph, now)
    if feat is None:
        return -1e12
    return (
        34.0 * task.weight
        + 1500.0 * feat["urgency"]
        + 30.0 * feat["battery_margin"]
        - 2.1 * feat["round_trip"]
        - 8.5 * feat["lateness"]
        - 120.0 * feat["battery_deficit"]
    )


def _state_bucket(
    pending_tasks: Sequence[Task],
    free_vehicle_ids: Sequence[int],
    vehicles: Dict[int, Vehicle],
    now: int,
) -> tuple[int, int, int, int]:
    pending_bin = min(5, len(pending_tasks) // 8)
    free_bin = min(5, len(free_vehicle_ids) // 4)
    if pending_tasks:
        avg_slack = sum(max(0.0, task.deadline - now) for task in pending_tasks) / len(pending_tasks)
    else:
        avg_slack = 999.0
    slack_bin = 0 if avg_slack < 25 else 1 if avg_slack < 55 else 2 if avg_slack < 95 else 3

    if free_vehicle_ids:
        battery_ratio = sum(
            vehicles[vehicle_id].battery / max(vehicles[vehicle_id].battery_capacity, 1e-6)
            for vehicle_id in free_vehicle_ids
        ) / len(free_vehicle_ids)
    else:
        battery_ratio = 0.0
    battery_bin = 0 if battery_ratio < 0.25 else 1 if battery_ratio < 0.45 else 2 if battery_ratio < 0.7 else 3
    return pending_bin, free_bin, slack_bin, battery_bin


def _best_pair_by_heuristic(
    heuristic_name: str,
    pending_tasks: Sequence[Task],
    free_vehicle_ids: Sequence[int],
    vehicles: Dict[int, Vehicle],
    graph: WeightedGraph,
    now: int,
) -> tuple[float, Task, int, dict[str, float]] | None:
    best: tuple[float, Task, int, dict[str, float]] | None = None
    for task in pending_tasks:
        for vehicle_id in free_vehicle_ids:
            vehicle = vehicles[vehicle_id]
            if vehicle.capacity + 1e-9 < task.weight:
                continue
            feat = _pair_features(task, vehicle, graph, now)
            if feat is None:
                continue

            score = _heuristic_score(heuristic_name, task, feat)
            if best is None or score > best[0]:
                best = (score, task, vehicle_id, feat)
    return best


def _heuristic_score(heuristic_name: str, task: Task, feat: dict[str, float]) -> float:
    if heuristic_name == "nearest":
        return -feat["round_trip"] - 5.5 * feat["lateness"] + 15.0 * feat["battery_margin"]

    if heuristic_name == "max_weight":
        return (
            50.0 * task.weight
            + 900.0 * feat["urgency"]
            - 1.45 * feat["round_trip"]
            - 7.5 * feat["lateness"]
            - 85.0 * feat["battery_deficit"]
            + 24.0 * feat["battery_margin"]
        )

    if heuristic_name == "urgency":
        return (
            35.0 * task.weight
            + 1650.0 * feat["urgency"]
            - 1.9 * feat["round_trip"]
            - 8.5 * feat["lateness"]
            - 100.0 * feat["battery_deficit"]
            + 22.0 * feat["battery_margin"]
        )

    # auction-like scoring as default.
    value = 34.0 * task.weight + 1280.0 * feat["urgency"]
    cost = 2.1 * feat["round_trip"] + 9.0 * feat["lateness"] + 120.0 * feat["battery_deficit"] - 18.0 * feat["battery_margin"]
    return value - cost


def _estimated_immediate_reward(task: Task, feat: dict[str, float]) -> float:
    return (
        220.0
        + 25.0 * task.weight
        - 0.52 * feat["round_trip"]
        - 12.0 * feat["lateness"]
        - 45.0 * feat["battery_deficit"]
        + 18.0 * feat["battery_margin"]
    )


def _best_collab_decision(
    pending_tasks: Sequence[Task],
    free_vehicle_ids: Sequence[int],
    vehicles: Dict[int, Vehicle],
    graph: WeightedGraph,
    now: int,
) -> DispatchDecision | None:
    best_group: tuple[float, Task, List[int]] | None = None
    for task in pending_tasks:
        group, dist_sum = _nearest_collab_group(task, free_vehicle_ids, vehicles, graph)
        if not group:
            continue
        slack = max(1.0, task.deadline - now)
        urgency = 1.0 / slack
        score = 33.0 * task.weight + 1200.0 * urgency - 1.15 * dist_sum
        if best_group is None or score > best_group[0]:
            best_group = (score, task, group)
    if best_group is None:
        return None
    return DispatchDecision(task=best_group[1], vehicle_ids=best_group[2])


def _pair_features(task: Task, vehicle: Vehicle, graph: WeightedGraph, now: int) -> dict[str, float] | None:
    d_to_task, _ = graph.shortest_path(vehicle.current_node, task.node_id)
    d_task_depot, _ = graph.shortest_path(task.node_id, DEPOT_NODE)
    if d_to_task == float("inf") or d_task_depot == float("inf"):
        return None

    round_trip = d_to_task + d_task_depot
    slack = max(1.0, task.deadline - now)
    urgency = 1.0 / slack

    travel_time_est = round_trip / max(vehicle.speed, 1e-6) + SERVICE_TIME_APPROX
    completion_est = now + travel_time_est
    lateness = max(0.0, completion_est - task.deadline)

    energy_need = round_trip * vehicle.energy_per_distance
    battery_margin = (vehicle.battery - energy_need) / max(vehicle.battery_capacity, 1e-6)
    battery_deficit = max(0.0, energy_need - vehicle.battery)

    return {
        "round_trip": round_trip,
        "urgency": urgency,
        "lateness": lateness,
        "battery_margin": battery_margin,
        "battery_deficit": battery_deficit,
    }


def _nearest_collab_group(
    task: Task,
    free_vehicle_ids: Sequence[int],
    vehicles: Dict[int, Vehicle],
    graph: WeightedGraph,
) -> tuple[List[int], float]:
    distances: List[tuple[float, int]] = []
    for vehicle_id in free_vehicle_ids:
        vehicle = vehicles[vehicle_id]
        dist, _ = graph.shortest_path(vehicle.current_node, task.node_id)
        distances.append((dist, vehicle_id))
    distances.sort(key=lambda item: item[0])

    selected: List[int] = []
    cap_sum = 0.0
    dist_sum = 0.0
    for dist, vehicle_id in distances:
        selected.append(vehicle_id)
        cap_sum += vehicles[vehicle_id].capacity
        dist_sum += dist
        if cap_sum + 1e-9 >= task.weight:
            return selected, dist_sum
    return [], float("inf")


def _capacity_first_collab_group(
    task: Task,
    free_vehicle_ids: Sequence[int],
    vehicles: Dict[int, Vehicle],
    graph: WeightedGraph,
) -> tuple[List[int], float]:
    ranked = sorted(
        free_vehicle_ids,
        key=lambda vehicle_id: (-vehicles[vehicle_id].capacity, graph.shortest_path(vehicles[vehicle_id].current_node, task.node_id)[0]),
    )
    selected: List[int] = []
    cap_sum = 0.0
    dist_sum = 0.0
    for vehicle_id in ranked:
        selected.append(vehicle_id)
        cap_sum += vehicles[vehicle_id].capacity
        dist_sum += graph.shortest_path(vehicles[vehicle_id].current_node, task.node_id)[0]
        if cap_sum + 1e-9 >= task.weight:
            return selected, dist_sum
    return [], float("inf")
