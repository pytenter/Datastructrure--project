from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class Task:
    task_id: int
    release_time: int
    node_id: int
    x: float
    y: float
    weight: float
    deadline: int


@dataclass
class Vehicle:
    vehicle_id: int
    capacity: float
    battery_capacity: float
    speed: float
    energy_per_distance: float
    current_node: int
    battery: float
    available_time: float = 0.0


@dataclass
class ChargingStation:
    station_id: int
    node_id: int
    charge_rate: float
    ports: int
    _port_available_times: List[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self._port_available_times:
            self._port_available_times = [0.0 for _ in range(self.ports)]

    def expected_start_time(self, arrival_time: float) -> float:
        earliest = min(self._port_available_times)
        return max(arrival_time, earliest)

    def expected_wait_time(self, arrival_time: float) -> float:
        return self.expected_start_time(arrival_time) - arrival_time

    def reserve(self, arrival_time: float, charge_duration: float) -> tuple[float, float]:
        best_port = min(range(len(self._port_available_times)), key=self._port_available_times.__getitem__)
        start = max(arrival_time, self._port_available_times[best_port])
        finish = start + charge_duration
        self._port_available_times[best_port] = finish
        return start, finish

    def utilization(self, now: float) -> float:
        busy = sum(1 for end_time in self._port_available_times if end_time > now)
        return busy / float(self.ports)


@dataclass
class VehicleMission:
    vehicle_id: int
    start_time: float
    completion_time: float
    start_battery: float
    charge_start_time: float | None
    charge_end_time: float | None
    travel_distance: float
    final_battery: float
    final_node: int
    charging_wait: float
    charge_amount: float
    charged_station_id: int | None
    route_nodes: List[int]


@dataclass
class TaskResult:
    task_id: int
    strategy_name: str
    vehicle_ids: List[int]
    release_time: int
    completion_time: float
    distance: float
    response_time: float
    overtime: bool
    score: float
    charging_wait: float


@dataclass
class DispatchEvent:
    task_id: int
    strategy_name: str
    dispatch_time: float
    completion_time: float
    vehicle_ids: List[int]
    task_node_id: int
    score: float
    overtime: bool
    total_distance: float
    total_charging_wait: float
    route_by_vehicle: Dict[int, List[int]]
    station_by_vehicle: Dict[int, int | None]
    travel_distance_by_vehicle: Dict[int, float]
    completion_time_by_vehicle: Dict[int, float]
    final_battery_by_vehicle: Dict[int, float]
    start_battery_by_vehicle: Dict[int, float]
    final_node_by_vehicle: Dict[int, int]
    charge_amount_by_vehicle: Dict[int, float]
    charging_wait_by_vehicle: Dict[int, float]
    charge_start_time_by_vehicle: Dict[int, float | None]
    charge_end_time_by_vehicle: Dict[int, float | None]


@dataclass
class SimulationConfig:
    name: str
    seed: int
    horizon: int
    depot_node: int
    service_time: float
    overtime_penalty: float
    unserved_penalty: float
    allow_collaboration: bool
    min_battery_reserve_ratio: float = 0.30
    task_end_target_ratio: float = 0.55
    idle_recharge_trigger_ratio: float = 0.55
    idle_recharge_target_ratio: float = 0.90
    allow_depot_charging: bool = True
    depot_charge_rate: float = 7.2
    depot_charge_ports: int = 4
    rush_windows: List[tuple[int, int, float]] = field(default_factory=list)


@dataclass
class SimulationSummary:
    scenario: str
    strategy: str
    total_tasks: int
    completed_tasks: int
    unserved_tasks: int
    overtime_tasks: int
    total_distance: float
    avg_response_time: float
    total_charging_wait: float
    final_score: float
    station_utilization: Dict[int, float]
