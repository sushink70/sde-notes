Graph algorithms are techniques for traversing, searching, and analyzing graphs — structures made of **nodes (vertices)** connected by **edges**. They power things like shortest-path routing, dependency resolution, network analysis, and cycle detection.

## Core Ideas

**Representation**: Most commonly an **adjacency list** — a map/array where each node points to a list of its neighbors. This is more space-efficient than an adjacency matrix for sparse graphs.

**Traversal — the foundation of almost everything:**

- **BFS (Breadth-First Search)**: Explore level by level using a **queue**. Visit all neighbors of a node before moving further out. Guarantees the *shortest path* in an unweighted graph.
- **DFS (Depth-First Search)**: Explore as deep as possible before backtracking, using a **stack** (or recursion). Good for detecting cycles, topological sorting, connected components.

**Common algorithms built on these:**
- **Dijkstra's** — shortest path in weighted graphs (BFS + priority queue)
- **Union-Find** — track connected components efficiently
- **Topological Sort** — ordering with dependencies (DFS-based)
- **A\*** — heuristic-guided shortest path

For LeetCode-style problems, BFS/DFS cover probably 80% of graph questions (number of islands, course schedule, clone graph, etc.), so let's nail those.

---

## BFS Example: Shortest path / level traversal on an unweighted graph

**Go:**

```go
package main

import "fmt"

func bfs(graph map[int][]int, start int) map[int]int {
	dist := map[int]int{start: 0}
	queue := []int{start}

	for len(queue) > 0 {
		node := queue[0]
		queue = queue[1:]

		for _, neighbor := range graph[node] {
			if _, visited := dist[neighbor]; !visited {
				dist[neighbor] = dist[node] + 1
				queue = append(queue, neighbor)
			}
		}
	}
	return dist
}

func main() {
	graph := map[int][]int{
		1: {2, 3},
		2: {1, 4},
		3: {1, 4},
		4: {2, 3, 5},
		5: {4},
	}

	distances := bfs(graph, 1)
	for node, d := range distances {
		fmt.Printf("Node %d: distance %d\n", node, d)
	}
}
```

**Rust:**

```rust
use std::collections::{HashMap, VecDeque};

fn bfs(graph: &HashMap<i32, Vec<i32>>, start: i32) -> HashMap<i32, i32> {
    let mut dist: HashMap<i32, i32> = HashMap::new();
    dist.insert(start, 0);

    let mut queue: VecDeque<i32> = VecDeque::new();
    queue.push_back(start);

    while let Some(node) = queue.pop_front() {
        if let Some(neighbors) = graph.get(&node) {
            for &neighbor in neighbors {
                if !dist.contains_key(&neighbor) {
                    let d = dist[&node] + 1;
                    dist.insert(neighbor, d);
                    queue.push_back(neighbor);
                }
            }
        }
    }
    dist
}

fn main() {
    let mut graph: HashMap<i32, Vec<i32>> = HashMap::new();
    graph.insert(1, vec![2, 3]);
    graph.insert(2, vec![1, 4]);
    graph.insert(3, vec![1, 4]);
    graph.insert(4, vec![2, 3, 5]);
    graph.insert(5, vec![4]);

    let distances = bfs(&graph, 1);
    let mut nodes: Vec<&i32> = distances.keys().collect();
    nodes.sort();
    for node in nodes {
        println!("Node {}: distance {}", node, distances[node]);
    }
}
```

---

## Key differences you'll notice going Go → Rust

- Go's `map` is native and easy; Rust's `HashMap` needs explicit imports and slightly more ceremony around borrowing (`&`, `.get()`).
- Rust forces you to handle the "might not exist" case via `Option` (`if let Some(...)`), whereas Go uses the `value, ok := map[key]` idiom.
- Go's garbage collector means you don't think about ownership; Rust's borrow checker will flag if you try to mutate `graph` while iterating over it — a classic gotcha in Rust graph algorithms.

Want me to follow up with DFS, a weighted-graph example (Dijkstra), or a specific LeetCode problem (e.g., "Number of Islands" or "Course Schedule") worked through in both languages?

Let's break down all three — they're the backbone of most graph/tree problems.

## BFS (Breadth-First Search)

**What it does**: Explores a graph/tree level by level, visiting all neighbors at the current depth before moving to the next depth.

**Data structure**: Queue (FIFO — First In, First Out)

**Why it works**: Since you fully process one "layer" before starting the next, the first time you reach a node, you've reached it via the shortest possible path (in terms of number of edges, unweighted).

**Mental model**: Imagine dropping a stone in a pond — ripples expand outward evenly. BFS is that ripple.

**Use cases**: Shortest path (unweighted), level-order tree traversal, "minimum steps to reach X", finding connected components.

```
Start at A
Queue: [A]
Visit A, enqueue its neighbors (B, C) → Queue: [B, C]
Visit B, enqueue its neighbors → Queue: [C, D, E]
Visit C, enqueue its neighbors → Queue: [D, E, F]
... and so on
```

---

## DFS (Depth-First Search)

**What it does**: Goes as deep as possible down one path before backtracking to try another.

**Data structure**: Stack (LIFO — Last In, First Out) — either explicit, or implicit via **recursion** (the call stack *is* a stack).

**Mental model**: Like exploring a maze by always taking the first available turn, going until you hit a dead end, then backtracking to the last junction and trying the next option.

**Use cases**: Cycle detection, topological sort, path existence, counting connected components/islands, backtracking problems.

```
Start at A
Go to B (first neighbor)
Go to D (first neighbor of B)
Dead end → backtrack to B
Go to E (next neighbor of B)
...
```

**BFS vs DFS — the core tradeoff:**

| | BFS | DFS |
|---|---|---|
| Structure | Queue | Stack / recursion |
| Finds shortest path? | Yes (unweighted) | No |
| Memory | Can be worse (stores whole frontier) | Can be better (stores one path) |
| Natural fit for | "Closest"/"minimum" questions | "Does a path exist", exhaustive search |

---

## Priority Queue

**What it is**: Not FIFO or LIFO — it's a queue where each element has a **priority**, and you always pop the element with the highest (or lowest) priority first. Typically implemented as a **binary heap** under the hood, giving O(log n) insert/extract.

**Why graphs need it**: In a *weighted* graph, BFS's "level by level" guarantee breaks — a node 3 hops away with cheap edges might actually be closer than a node 1 hop away with an expensive edge. A priority queue lets you always expand the currently-cheapest-known node next. This is exactly **Dijkstra's algorithm** — it's BFS, but the queue is replaced with a min-priority-queue ordered by distance.

**Mental model**: A hospital ER triage line — not first-come-first-served, but most-critical-first.

**Use cases**: Dijkstra's shortest path, A*, task scheduling, "k closest points", merging k sorted lists.

---

### Quick reference in Go and Rust

| Concept | Go | Rust |
|---|---|---|
| Queue (BFS) | slice used as queue (`queue = queue[1:]`), or `container/list` | `VecDeque` (`push_back`/`pop_front`) |
| Stack (DFS) | slice used as stack (`append`/pop last), or plain recursion | `Vec` (`push`/`pop`), or plain recursion |
| Priority Queue | `container/heap` — you implement `Len`, `Less`, `Swap`, `Push`, `Pop` yourself (a bit verbose) | `std::collections::BinaryHeap` — built-in max-heap; for min-heap wrap values in `Reverse(...)` or implement `Ord` inverted |

Want me to show DFS and a priority-queue-based Dijkstra example next, side by side in Go and Rust — the way we did BFS?

This is the same graph from the BFS code example earlier — nodes 1 through 5, edges connecting them. The color shows what BFS discovers as it runs: node 1 is the start (distance 0), nodes 2 and 3 are its direct neighbors (distance 1), node 4 is reachable through either of them (distance 2), and node 5 is furthest out (distance 3).

Notice node 4 has *two* incoming edges — from node 2 and node 3 — but BFS only records it once, at distance 2, because that's the moment the queue *first* reaches it. Whichever of node 2 or 3 gets processed first in the queue is what "discovers" node 4; the second path to it is simply ignored since it's already marked visited. That's exactly the `if _, visited := dist[neighbor]; !visited` check in the Go code and `if !dist.contains_key(&neighbor)` in Rust — the guard that keeps BFS from reprocessing a node or producing an incorrect (longer) distance.

If you traced this with a stack instead (DFS), you might hit 1 → 2 → 4 → 5 → *then* backtrack to 3 — no clean "distance from start" ordering, just depth-first wandering with backtracking. That's the structural difference between the two.

Want to see the same graph traced with DFS next, or move on to a weighted version with a priority queue (Dijkstra)?