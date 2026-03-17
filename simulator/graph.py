from __future__ import annotations

import heapq
import math
import random
from dataclasses import dataclass
from typing import Dict, List, Tuple


@dataclass
class Node:
    node_id: int
    x: float
    y: float


class WeightedGraph:
    def __init__(self) -> None:
        self.nodes: Dict[int, Node] = {}
        self._adj: Dict[int, List[Tuple[int, float]]] = {}
        self._cache: Dict[Tuple[int, int], Tuple[float, List[int]]] = {}

    def add_node(self, node_id: int, x: float, y: float) -> None:
        self.nodes[node_id] = Node(node_id=node_id, x=x, y=y)
        self._adj.setdefault(node_id, [])

    def add_edge(self, a: int, b: int, distance: float, undirected: bool = True) -> None:
        if a == b:
            return
        self._upsert_edge(a, b, distance)
        if undirected:
            self._upsert_edge(b, a, distance)
        self._cache.clear()

    def _upsert_edge(self, a: int, b: int, distance: float) -> None:
        neighbors = self._adj.setdefault(a, [])
        for idx, (node_id, old_dist) in enumerate(neighbors):
            if node_id == b:
                if distance < old_dist:
                    neighbors[idx] = (b, distance)
                return
        neighbors.append((b, distance))

    def shortest_path(self, start: int, end: int) -> Tuple[float, List[int]]:
        key = (start, end)
        if key in self._cache:
            return self._cache[key]
        if start == end:
            result = (0.0, [start])
            self._cache[key] = result
            return result

        dist: Dict[int, float] = {node_id: math.inf for node_id in self.nodes}
        prev: Dict[int, int] = {}
        dist[start] = 0.0
        heap: List[Tuple[float, int]] = [(0.0, start)]

        while heap:
            cur_dist, cur = heapq.heappop(heap)
            if cur_dist > dist[cur]:
                continue
            if cur == end:
                break
            for nxt, weight in self._adj.get(cur, []):
                cand = cur_dist + weight
                if cand < dist[nxt]:
                    dist[nxt] = cand
                    prev[nxt] = cur
                    heapq.heappush(heap, (cand, nxt))

        if math.isinf(dist[end]):
            result = (math.inf, [])
            self._cache[key] = result
            return result

        path = [end]
        node = end
        while node != start:
            node = prev[node]
            path.append(node)
        path.reverse()
        result = (dist[end], path)
        self._cache[key] = result
        return result


def euclidean(a: Node, b: Node) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def generate_random_city_graph(
    num_nodes: int,
    extra_edges: int,
    seed: int,
    width: float = 100.0,
    height: float = 100.0,
    nearest_neighbors: int = 3,
) -> WeightedGraph:
    rnd = random.Random(seed)
    graph = WeightedGraph()

    for node_id in range(num_nodes):
        graph.add_node(node_id=node_id, x=rnd.uniform(0.0, width), y=rnd.uniform(0.0, height))

    for node_id in range(num_nodes - 1):
        a = graph.nodes[node_id]
        b = graph.nodes[node_id + 1]
        graph.add_edge(node_id, node_id + 1, euclidean(a, b))

    for node_id in range(num_nodes):
        src = graph.nodes[node_id]
        others = [
            (other_id, euclidean(src, node))
            for other_id, node in graph.nodes.items()
            if other_id != node_id
        ]
        others.sort(key=lambda item: item[1])
        for other_id, dist in others[:nearest_neighbors]:
            graph.add_edge(node_id, other_id, dist)

    for _ in range(extra_edges):
        a, b = rnd.sample(range(num_nodes), 2)
        graph.add_edge(a, b, euclidean(graph.nodes[a], graph.nodes[b]))

    return graph
