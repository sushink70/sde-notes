# Multi-Source BFS: The Complete Guide

> Goal of this guide: give you a **mental model** so solid that you can recognize a multi-source BFS problem in seconds, derive the algorithm from first principles, implement it bug-free in Go, Rust or C, and know exactly when it stops being the right tool.

---

## Table of Contents

1. [The One-Sentence Idea](#1-the-one-sentence-idea)
2. [Prerequisites: Single-Source BFS From the Ground Up](#2-prerequisites-single-source-bfs-from-the-ground-up)
3. [Multi-Source BFS: Definition and Two Equivalent Views](#3-multi-source-bfs-definition-and-two-equivalent-views)
4. [Why It Is Correct (Proof Sketch + Invariants)](#4-why-it-is-correct-proof-sketch--invariants)
5. [Complexity Analysis](#5-complexity-analysis)
6. [Architecture Diagrams (ASCII)](#6-architecture-diagrams-ascii)
7. [Implementation: Generic Graph (Go, Rust, C)](#7-implementation-generic-graph-go-rust-c)
8. [Implementation: Grid With Walls and Owner Labels (Go, Rust, C)](#8-implementation-grid-with-walls-and-owner-labels-go-rust-c)
9. [Level-by-Level Variant: "Time to Spread" (Go, Rust, C)](#9-level-by-level-variant-time-to-spread-go-rust-c)
10. [Variants and Extensions](#10-variants-and-extensions)
11. [Classic Problems and the Pattern Behind Each](#11-classic-problems-and-the-pattern-behind-each)
12. [Pitfalls and Bugs Everyone Hits](#12-pitfalls-and-bugs-everyone-hits)
13. [Alternatives: When NOT to Use Multi-Source BFS](#13-alternatives-when-not-to-use-multi-source-bfs)
14. [Performance Engineering](#14-performance-engineering)
15. [Testing Strategy](#15-testing-strategy)
16. [Real-World Systems Architecture](#16-real-world-systems-architecture)
17. [Decision Checklist and Mental Model Summary](#17-decision-checklist-and-mental-model-summary)

---

## 1. The One-Sentence Idea

**Multi-source BFS starts the search from *all* source nodes at the same time (all at distance 0), so each node is reached first by whichever source is closest to it.**

Think of dropping several stones into a pond at once. Each stone makes ripples. Ripples expand at the same speed. The first ripple to touch a point defines that point's "nearest stone" and "distance to the nearest stone". You never simulate each pond separately; you simulate the combined wavefront once.

### The question it answers

> "For every node `v`, what is `min over sources s` of `dist(s, v)`?" (in an unweighted graph)

The naive approach runs BFS from each of `k` sources and takes the minimum: `O(k * (V + E))`. Multi-source BFS does it in `O(V + E)`, independent of `k`.

---

## 2. Prerequisites: Single-Source BFS From the Ground Up

If the single-source version is not crystal clear, the multi-source version will feel like magic. Let's make it mechanical.

### 2.1 What BFS computes

In an **unweighted** graph (every edge has cost 1), BFS computes the shortest-path distance (in edges) from a start node to every reachable node.

### 2.2 The data structures

| Structure | Role |
|-----------|------|
| `queue` (FIFO) | Holds the **frontier**: discovered nodes whose neighbors we haven't explored yet |
| `dist[v]` | Distance from the source; also doubles as the **visited** marker (`-1` / `INF` = unvisited) |
| `parent[v]` (optional) | Predecessor on a shortest path, to reconstruct paths |

### 2.3 The algorithm

```
BFS(graph, s):
    dist[*] = UNVISITED
    dist[s] = 0
    queue.push(s)
    while queue not empty:
        u = queue.pop_front()
        for each neighbor v of u:
            if dist[v] == UNVISITED:
                dist[v] = dist[u] + 1
                queue.push(v)
```

### 2.4 Why a queue (and not a stack)?

A FIFO queue processes nodes in **order of discovery**. Nodes discovered earlier are closer. So nodes are popped in non-decreasing order of distance: all distance-`d` nodes are popped before any distance-`d+1` node. A stack would dive deep (DFS) and give you *a* path, not the *shortest* one.

### 2.5 Layers

BFS naturally partitions the graph into **layers** (a.k.a. levels):

```
Layer 0:  { s }
Layer 1:  all nodes at distance 1
Layer 2:  all nodes at distance 2
...
```

The queue always contains at most two adjacent layers at any time (current layer's remaining nodes + next layer's discovered nodes). This fact is the heart of the correctness proof and will be reused below.

### 2.6 "Mark visited when you ENQUEUE, not when you DEQUEUE"

This is the single most important implementation rule.

- Mark at **enqueue**: each node enters the queue at most once. Total work `O(V + E)`.
- Mark at **dequeue**: a node can be enqueued many times by different neighbors before it's popped; the queue can blow up to `O(E)` entries (or worse on grids), and in dense grids you will see TLE.

---

## 3. Multi-Source BFS: Definition and Two Equivalent Views

### 3.1 The algorithm

```
MultiSourceBFS(graph, sources):
    dist[*]  = UNVISITED
    owner[*] = NONE                # optional: which source reached me first
    for each s in sources:
        if dist[s] == UNVISITED:   # guard against duplicate sources
            dist[s]  = 0
            owner[s] = s
            queue.push(s)
    while queue not empty:
        u = queue.pop_front()
        for each neighbor v of u:
            if dist[v] == UNVISITED:
                dist[v]  = dist[u] + 1
                owner[v] = owner[u]
                queue.push(v)
```

The **only** difference from single-source BFS: the initialization loop seeds the queue with *every* source at distance 0.

### 3.2 View A: The Virtual Super-Source

Add a fictitious node `S*` with an edge to every real source. Run ordinary single-source BFS from `S*`.

```
                    +-------+
                    |  S*   |      (virtual super source)
                    +-------+
                   /    |    \
                  /     |     \        <- edges of cost 1 (virtual)
                 v      v      v
              +----+ +----+ +----+
              | s1 | | s2 | | s3 |    <- real sources: dist = 1 from S*
              +----+ +----+ +----+
               / \     |      / \
              v   v    v     v   v
             ...  ...  ...  ...  ...   <- everything else
```

Every real node's distance from `S*` equals `(its distance to the nearest real source) + 1`. Subtract 1 and you have the answer. This view makes correctness *trivial*: it's just normal BFS on a slightly bigger graph.

### 3.3 View B: Simultaneous Waves

All sources emit a wave at time `t = 0`. At each tick the wave advances one edge. A node's distance is the tick at which a wave first touches it. When two waves meet, the earlier one wins (ties are broken by queue order).

```
 t=0         t=1         t=2         t=3
 A . . . B   A a . b B   A a a b B   A a a b B   ("a"/"b" = reached by A or B)
```

### 3.4 Which view should I use mentally?

- Use **View A** when proving or reasoning about correctness, or when you're unsure whether a variant is still BFS.
- Use **View B** when designing algorithms for problems phrased as "spreads", "burns", "infects", "floods", "rots", "nearest".

---

## 4. Why It Is Correct (Proof Sketch + Invariants)

### 4.1 Invariant (the queue is "sorted" with spread at most 1)

At every moment, the queue contents, from front to back, look like:

```
[ d, d, d, ..., d, d+1, d+1, ..., d+1 ]
```

i.e. distances are non-decreasing, and the difference between the back and front is at most 1.

**Initialization:** All sources have distance 0. The queue is `[0, 0, ..., 0]`. Invariant holds.

**Step:** We pop a node `u` with distance `d` (the front, which is the minimum). Every newly discovered neighbor gets `d + 1` and is appended at the back. Since the back was either `d` or `d+1`, the sequence stays non-decreasing and the spread stays at most 1. Invariant preserved.

### 4.2 Consequence: first-discovery = shortest distance

Suppose some node `v` has true nearest-source distance `D`. We show `dist[v] = D` by induction on `D`.

- `D = 0`: `v` is a source; we set `dist = 0`.
- `D = k + 1`: there is a path `s -> ... -> u -> v` with `dist(u) = k`. By induction `u` gets `dist[u] = k` correctly. When `u` is popped, either `v` is already discovered (by someone popped earlier, with `dist <= k`... which, by the invariant that pops are in non-decreasing order, means `dist[v] <= k + 1`; and it can't be smaller than `D = k + 1`) or `u` discovers it with `k + 1`. Either way `dist[v] = D`.

### 4.3 Ownership and tie-breaking

`owner[v]` is inherited from the node that first discovered `v`. Because queue order is FIFO and children are enqueued in parent order, **among ties, the source listed earlier wins** (its descendants precede later sources' descendants within the same layer). If you need a different tie-break rule (e.g. smallest label), seed in sorted order, or carry `(dist, label)` and compare explicitly.

### 4.4 Why not mark at dequeue? (Correctness view)

Marking at dequeue gives correct distances only if you also ignore stale entries; it is *correct but wasteful*. Marking at enqueue is both correct and optimal.

---

## 5. Complexity Analysis

Let `V` = nodes, `E` = edges, `k` = number of sources.

| Resource | Cost | Notes |
|----------|------|-------|
| Time | `O(V + E)` (+ `O(k)` seeding, and `k <= V`) | Each node enqueued once, each adjacency list scanned once |
| Space | `O(V)` for `dist`, `O(V)` queue, `O(V)` optional `owner` | Plus the graph itself |
| Naive alternative | `O(k * (V + E))` | Run BFS from each source |

**Grid with `R x C` cells and 4-neighbors:** `V = R*C`, `E <= 4*R*C`, so `O(R*C)`.

**Important:** the cost does **not** grow with `k`. That's the entire point.

---

## 6. Architecture Diagrams (ASCII)

### 6.1 Algorithm Control Flow

```
                 +--------------------------+
                 |  Input: graph/grid,      |
                 |         sources[]        |
                 +------------+-------------+
                              |
                              v
                 +--------------------------+
                 | Allocate dist[], owner[] |
                 | fill with UNVISITED      |
                 +------------+-------------+
                              |
                              v
              +-------------------------------+
              | for each source s:            |
              |   if dist[s] == UNVISITED:    |
              |      dist[s] = 0              |
              |      owner[s] = s             |
              |      enqueue(s)               |
              +---------------+---------------+
                              |
                              v
                    +-------------------+
          +-------->| queue empty ?     |-- yes --> +---------+
          |         +---------+---------+           |  DONE   |
          |                   | no                  +---------+
          |                   v
          |         +-------------------+
          |         | u = dequeue()     |
          |         +---------+---------+
          |                   |
          |                   v
          |         +-------------------------+
          |         | for each neighbor v of u|<---------+
          |         +---------+---------------+          |
          |                   |                          |
          |                   v                          |
          |         +-------------------------+          |
          |         | dist[v] == UNVISITED ?  |-- no ----+
          |         +---------+---------------+  (skip)  |
          |                   | yes                      |
          |                   v                          |
          |         +-------------------------+          |
          |         | dist[v]  = dist[u] + 1  |          |
          |         | owner[v] = owner[u]     |          |
          |         | enqueue(v)              |----------+
          |         +-------------------------+
          |                                    (all neighbors done)
          +---------------------------------------------+
```

### 6.2 Memory Architecture of the BFS Engine

```
  Flat arrays indexed by node id  (grid: id = row * COLS + col)

  index :   0    1    2    3    4    5    6    7    8   ...  N-1
          +----+----+----+----+----+----+----+----+----+-----+----+
  dist  : | 0  | 1  | 2  | -1 | 1  | 2  | 3  | ...                |
          +----+----+----+----+----+----+----+----+----+-----+----+
            ^ visited marker AND answer in a single array

          +----+----+----+----+----+----+----+----+----+-----+----+
  owner : | 0  | 0  | 0  | -1 | 0  | 0  | 4  | ...                |
          +----+----+----+----+----+----+----+----+----+-----+----+

  queue (array of node ids, each id enqueued at most once => size N suffices)

          head                              tail
           |                                 |
           v                                 v
          +----+----+----+----+----+----+----+----+----+-----+----+
  queue : | s1 | s2 | 5  | 9  | 14 | 3  |    |    |    |     |    |
          +----+----+----+----+----+----+----+----+----+-----+----+
            \_________ already popped _________/\_ live frontier _/ \_ free _/

  dequeue  : u = queue[head++]
  enqueue  : queue[tail++] = v
  empty    : head == tail
```

Because every node is enqueued **at most once**, a plain array of size `N` with two indices (`head`, `tail`) is a complete queue. No circular buffer needed.

### 6.3 Wavefront Expansion on a Grid

Grid 5x5, no walls. Sources: `A` at (0,0) and `B` at (4,4).

```
 t = 0                t = 1                t = 2
 A . . . .            A a . . .            A a a . .
 . . . . .            a . . . .            a a . . .
 . . . . .            . . . . .            a . . . b
 . . . . .            . . . . b            . . . b b
 . . . . B            . . . b B            . . b b B

 t = 3                t = 4  (grid fully covered)
 A a a a .            A a a a a
 a a a . .            a a a a b
 a a . . b            a a a b b
 a . . b b            a a b b b
 . b b b B            a b b b B
```

Final **distance** map (Manhattan distance to nearest source):

```
 0 1 2 3 4
 1 2 3 4 3
 2 3 4 3 2
 3 4 3 2 1
 4 3 2 1 0
```

Final **owner** map (ties on the anti-diagonal `r+c = 4` go to `A` because `A` was seeded first):

```
 A A A A A
 A A A A B
 A A A B B
 A A B B B
 A B B B B
```

This second picture is a **Voronoi diagram** on the grid under shortest-path metric. Multi-source BFS with owner labels computes it for free.

### 6.4 Queue Timeline (layers inside the queue)

Using the 5x5 example, queue contents as we proceed (showing `(r,c)` and owner):

```
 start:      [ A(0,0) | B(4,4) ]                          <- layer 0

 pop A(0,0) -> discovers (0,1) (1,0)
 pop B(4,4) -> discovers (3,4) (4,3)
             [ A(0,1) A(1,0) | B(3,4) B(4,3) ]            <- layer 1 now at front

 pop A(0,1) -> (0,2) (1,1)
 pop A(1,0) -> (2,0)             ((1,1) already seen)
 pop B(3,4) -> (2,4) (3,3)
 pop B(4,3) -> (4,2)
             [ A(0,2) A(1,1) A(2,0) B(2,4) B(3,3) B(4,2) ] <- layer 2

 ... and so on, each layer one step wider.
```

Notice how at all times the queue holds at most two distinct distance values (the invariant of Section 4).

### 6.5 Super-Source Equivalence Diagram

```
   Multi-source view                 Single-source view (equivalent)

   s1      s2      s3                       [S*]
   |       |       |                      /  |   \
   v       v       v                     v   v    v
  (dist 0 for all)                      s1  s2   s3    (dist 1 from S*)
   |       |       |                     |   |    |
   ...     ...     ...                   ... ...  ...
                                    answer = dist_from_S* - 1
```

---

## 7. Implementation: Generic Graph (Go, Rust, C)

All three versions compute for every node:
- `dist[v]`: hops to the nearest source (`-1` or `UNREACHED` if unreachable)
- `owner[v]`: index (in the `sources` list) of the source that reached it first

Demo graph (undirected):

```
 0 -- 1 -- 2 -- 3 -- 4 -- 5 -- 6
           |
           7 -- 8
```

Sources: `[0, 6]`. Expected result:

```
 node : 0 1 2 3 4 5 6 7 8
 dist : 0 1 2 3 2 1 0 3 4
 owner: 0 0 0 0 1 1 1 0 0     (node 3 is a tie at distance 3; source 0 wins since seeded first)
```

### 7.1 Go

```go
package main

import "fmt"

const Unreached = -1

// MultiSourceBFS computes, for every node, the hop distance to its nearest
// source and the index (in `sources`) of the source that reached it first.
// adj is an adjacency list. Runs in O(V + E).
func MultiSourceBFS(n int, adj [][]int, sources []int) (dist, owner []int) {
	dist = make([]int, n)
	owner = make([]int, n)
	for i := range dist {
		dist[i] = Unreached
		owner[i] = -1
	}

	// Each node is enqueued at most once, so capacity n is enough and the
	// slice never reallocates.
	queue := make([]int, 0, n)

	for i, s := range sources {
		if s < 0 || s >= n || dist[s] != Unreached {
			continue // ignore invalid or duplicate sources
		}
		dist[s] = 0
		owner[s] = i
		queue = append(queue, s)
	}

	for head := 0; head < len(queue); head++ {
		u := queue[head]
		for _, v := range adj[u] {
			if dist[v] != Unreached {
				continue
			}
			dist[v] = dist[u] + 1
			owner[v] = owner[u]
			queue = append(queue, v)
		}
	}
	return dist, owner
}

func main() {
	n := 9
	edges := [][2]int{
		{0, 1}, {1, 2}, {2, 3}, {3, 4}, {4, 5}, {5, 6}, {2, 7}, {7, 8},
	}
	adj := make([][]int, n)
	for _, e := range edges {
		adj[e[0]] = append(adj[e[0]], e[1])
		adj[e[1]] = append(adj[e[1]], e[0])
	}

	dist, owner := MultiSourceBFS(n, adj, []int{0, 6})
	fmt.Println("dist :", dist)
	fmt.Println("owner:", owner)
}
```

### 7.2 Rust

```rust
use std::collections::VecDeque;

pub const UNREACHED: u32 = u32::MAX;

/// Returns (dist, owner). `owner[v]` is the index into `sources` of the source
/// that first reached `v`, or None if unreachable. O(V + E).
pub fn multi_source_bfs(
    adj: &[Vec<usize>],
    sources: &[usize],
) -> (Vec<u32>, Vec<Option<usize>>) {
    let n = adj.len();
    let mut dist = vec![UNREACHED; n];
    let mut owner: Vec<Option<usize>> = vec![None; n];
    let mut queue: VecDeque<usize> = VecDeque::with_capacity(n);

    for (i, &s) in sources.iter().enumerate() {
        if s >= n || dist[s] != UNREACHED {
            continue; // ignore invalid or duplicate sources
        }
        dist[s] = 0;
        owner[s] = Some(i);
        queue.push_back(s);
    }

    while let Some(u) = queue.pop_front() {
        for &v in &adj[u] {
            if dist[v] != UNREACHED {
                continue;
            }
            dist[v] = dist[u] + 1;
            owner[v] = owner[u];
            queue.push_back(v);
        }
    }
    (dist, owner)
}

fn main() {
    let n = 9;
    let edges = [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (2, 7), (7, 8)];
    let mut adj = vec![Vec::new(); n];
    for &(a, b) in &edges {
        adj[a].push(b);
        adj[b].push(a);
    }

    let (dist, owner) = multi_source_bfs(&adj, &[0, 6]);
    println!("dist : {:?}", dist);
    println!("owner: {:?}", owner);
}
```

### 7.3 C

Uses a **CSR (Compressed Sparse Row)** graph: cache-friendly and allocation-light.

```
 CSR layout for the demo graph:

 offset: [0, 1, 3, 6, 8, 10, 12, 13, 15, 16]   (size n+1)
 edges : neighbors of node 0, then of node 1, ...  (size 2*m)

 neighbors of u are edges[ offset[u] .. offset[u+1] )
```

```c
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define UNREACHED (-1)

typedef struct {
    int  n;
    int *offset;  /* size n + 1 */
    int *edges;   /* size 2 * m (undirected) */
} Graph;

static Graph graph_from_edges(int n, const int edges[][2], int m) {
    Graph g;
    g.n = n;
    g.offset = calloc((size_t)n + 1, sizeof(int));
    g.edges  = malloc((size_t)(2 * m + 1) * sizeof(int));
    int *cur = malloc((size_t)n * sizeof(int));
    if (!g.offset || !g.edges || !cur) { perror("alloc"); exit(1); }

    for (int i = 0; i < m; i++) {
        g.offset[edges[i][0] + 1]++;
        g.offset[edges[i][1] + 1]++;
    }
    for (int i = 0; i < n; i++) g.offset[i + 1] += g.offset[i];

    memcpy(cur, g.offset, (size_t)n * sizeof(int));
    for (int i = 0; i < m; i++) {
        int a = edges[i][0], b = edges[i][1];
        g.edges[cur[a]++] = b;
        g.edges[cur[b]++] = a;
    }
    free(cur);
    return g;
}

static void graph_free(Graph *g) {
    free(g->offset);
    free(g->edges);
}

/* dist and owner must each have room for g->n ints.
 * Returns the number of reached nodes, or -1 on allocation failure. */
int multi_source_bfs(const Graph *g, const int *sources, int nsrc,
                     int *dist, int *owner) {
    int n = g->n;
    int *queue = malloc((size_t)n * sizeof(int));
    if (!queue) return -1;

    for (int i = 0; i < n; i++) { dist[i] = UNREACHED; owner[i] = -1; }

    int head = 0, tail = 0;
    for (int i = 0; i < nsrc; i++) {
        int s = sources[i];
        if (s < 0 || s >= n || dist[s] != UNREACHED) continue;
        dist[s]  = 0;
        owner[s] = i;
        queue[tail++] = s;
    }

    while (head < tail) {
        int u = queue[head++];
        for (int k = g->offset[u]; k < g->offset[u + 1]; k++) {
            int v = g->edges[k];
            if (dist[v] != UNREACHED) continue;
            dist[v]  = dist[u] + 1;
            owner[v] = owner[u];
            queue[tail++] = v;
        }
    }
    free(queue);
    return tail;
}

int main(void) {
    enum { N = 9, M = 8 };
    const int edges[M][2] = {
        {0,1},{1,2},{2,3},{3,4},{4,5},{5,6},{2,7},{7,8}
    };
    Graph g = graph_from_edges(N, edges, M);

    int sources[] = {0, 6};
    int dist[N], owner[N];
    multi_source_bfs(&g, sources, 2, dist, owner);

    printf("dist :");
    for (int i = 0; i < N; i++) printf(" %d", dist[i]);
    printf("\nowner:");
    for (int i = 0; i < N; i++) printf(" %d", owner[i]);
    printf("\n");

    graph_free(&g);
    return 0;
}
```

---

## 8. Implementation: Grid With Walls and Owner Labels (Go, Rust, C)

Grids are where multi-source BFS shows up most in interviews and in real systems (maps, games, robotics).

### 8.1 Grid Conventions

```
 Cell id      :  id = r * COLS + c        (flatten 2D -> 1D)
 Recover (r,c):  r = id / COLS ; c = id % COLS
 4 directions :  dr = {-1, +1,  0,  0}
                 dc = { 0,  0, -1, +1}

         (r-1,c)
            ^
 (r,c-1) <- (r,c) -> (r,c+1)
            v
         (r+1,c)
```

Demo grid (`#` = wall). Sources are placed at (0,0) and (4,6):

```
 col:  0 1 2 3 4 5 6
 r0    . . . . . # .
 r1    . # # # . # .
 r2    . . . . . # .
 r3    . # # # . . .
 r4    . . . . . . .
```

Output prints the distance field (`##` = wall, ` ?` = unreachable).

### 8.2 Go

```go
package main

import "fmt"

// GridMultiSourceBFS runs a 4-directional multi-source BFS over a grid where
// '#' is a wall. Returns flat dist/owner slices (len rows*cols), -1 = unreached.
func GridMultiSourceBFS(grid []string, sources [][2]int) (dist, owner []int) {
	rows := len(grid)
	if rows == 0 {
		return nil, nil
	}
	cols := len(grid[0])
	n := rows * cols

	dist = make([]int, n)
	owner = make([]int, n)
	for i := range dist {
		dist[i], owner[i] = -1, -1
	}
	queue := make([]int, 0, n)

	for i, s := range sources {
		r, c := s[0], s[1]
		if r < 0 || r >= rows || c < 0 || c >= cols || grid[r][c] == '#' {
			continue
		}
		id := r*cols + c
		if dist[id] != -1 {
			continue
		}
		dist[id], owner[id] = 0, i
		queue = append(queue, id)
	}

	dr := [4]int{-1, 1, 0, 0}
	dc := [4]int{0, 0, -1, 1}

	for head := 0; head < len(queue); head++ {
		u := queue[head]
		r, c := u/cols, u%cols
		for k := 0; k < 4; k++ {
			nr, nc := r+dr[k], c+dc[k]
			if nr < 0 || nr >= rows || nc < 0 || nc >= cols {
				continue
			}
			if grid[nr][nc] == '#' {
				continue
			}
			v := nr*cols + nc
			if dist[v] != -1 {
				continue
			}
			dist[v] = dist[u] + 1
			owner[v] = owner[u]
			queue = append(queue, v)
		}
	}
	return dist, owner
}

func main() {
	grid := []string{
		".....#.",
		".###.#.",
		".....#.",
		".###...",
		".......",
	}
	sources := [][2]int{{0, 0}, {4, 6}}
	dist, owner := GridMultiSourceBFS(grid, sources)

	cols := len(grid[0])
	fmt.Println("distance field:")
	for r := range grid {
		for c := 0; c < cols; c++ {
			switch d := dist[r*cols+c]; {
			case grid[r][c] == '#':
				fmt.Print("## ")
			case d < 0:
				fmt.Print(" ? ")
			default:
				fmt.Printf("%2d ", d)
			}
		}
		fmt.Println()
	}

	fmt.Println("owner field (0 = first source, 1 = second):")
	for r := range grid {
		for c := 0; c < cols; c++ {
			o := owner[r*cols+c]
			if grid[r][c] == '#' || o < 0 {
				fmt.Print("# ")
			} else {
				fmt.Printf("%d ", o)
			}
		}
		fmt.Println()
	}
}
```

### 8.3 Rust

```rust
use std::collections::VecDeque;

/// 4-directional multi-source BFS on a byte grid where b'#' is a wall.
/// Returns (dist, owner) as flat vectors of length rows*cols.
/// dist = -1 means unreached; owner = -1 means no owner.
fn grid_multi_source_bfs(
    grid: &[Vec<u8>],
    sources: &[(usize, usize)],
) -> (Vec<i32>, Vec<i32>) {
    let rows = grid.len();
    let cols = if rows == 0 { 0 } else { grid[0].len() };
    let n = rows * cols;

    let mut dist = vec![-1i32; n];
    let mut owner = vec![-1i32; n];
    let mut queue: VecDeque<usize> = VecDeque::with_capacity(n);

    for (i, &(r, c)) in sources.iter().enumerate() {
        if r >= rows || c >= cols || grid[r][c] == b'#' {
            continue;
        }
        let id = r * cols + c;
        if dist[id] != -1 {
            continue;
        }
        dist[id] = 0;
        owner[id] = i as i32;
        queue.push_back(id);
    }

    const DIRS: [(isize, isize); 4] = [(-1, 0), (1, 0), (0, -1), (0, 1)];

    while let Some(u) = queue.pop_front() {
        let (r, c) = ((u / cols) as isize, (u % cols) as isize);
        for &(dr, dc) in &DIRS {
            let (nr, nc) = (r + dr, c + dc);
            if nr < 0 || nc < 0 || nr >= rows as isize || nc >= cols as isize {
                continue;
            }
            let (nr, nc) = (nr as usize, nc as usize);
            if grid[nr][nc] == b'#' {
                continue;
            }
            let v = nr * cols + nc;
            if dist[v] != -1 {
                continue;
            }
            dist[v] = dist[u] + 1;
            owner[v] = owner[u];
            queue.push_back(v);
        }
    }
    (dist, owner)
}

fn main() {
    let raw = [
        ".....#.",
        ".###.#.",
        ".....#.",
        ".###...",
        ".......",
    ];
    let grid: Vec<Vec<u8>> = raw.iter().map(|s| s.bytes().collect()).collect();
    let cols = grid[0].len();

    let (dist, owner) = grid_multi_source_bfs(&grid, &[(0, 0), (4, 6)]);

    println!("distance field:");
    for r in 0..grid.len() {
        for c in 0..cols {
            let d = dist[r * cols + c];
            if grid[r][c] == b'#' {
                print!("## ");
            } else if d < 0 {
                print!(" ? ");
            } else {
                print!("{:2} ", d);
            }
        }
        println!();
    }

    println!("owner field:");
    for r in 0..grid.len() {
        for c in 0..cols {
            let o = owner[r * cols + c];
            if grid[r][c] == b'#' || o < 0 {
                print!("# ");
            } else {
                print!("{} ", o);
            }
        }
        println!();
    }
}
```

### 8.4 C

```c
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* blocked[id] != 0 means wall. dist/owner have rows*cols entries.
 * Returns number of reached cells, or -1 on allocation failure. */
int grid_multi_source_bfs(const unsigned char *blocked, int rows, int cols,
                          const int *sources, int nsrc,
                          int *dist, int *owner) {
    static const int DR[4] = {-1, 1, 0, 0};
    static const int DC[4] = {0, 0, -1, 1};

    int n = rows * cols;
    int *queue = malloc((size_t)n * sizeof(int));
    if (!queue) return -1;

    for (int i = 0; i < n; i++) { dist[i] = -1; owner[i] = -1; }

    int head = 0, tail = 0;
    for (int i = 0; i < nsrc; i++) {
        int s = sources[i];
        if (s < 0 || s >= n || blocked[s] || dist[s] != -1) continue;
        dist[s]  = 0;
        owner[s] = i;
        queue[tail++] = s;
    }

    while (head < tail) {
        int u = queue[head++];
        int r = u / cols, c = u % cols;
        for (int k = 0; k < 4; k++) {
            int nr = r + DR[k], nc = c + DC[k];
            if (nr < 0 || nr >= rows || nc < 0 || nc >= cols) continue;
            int v = nr * cols + nc;
            if (blocked[v] || dist[v] != -1) continue;
            dist[v]  = dist[u] + 1;
            owner[v] = owner[u];
            queue[tail++] = v;
        }
    }
    free(queue);
    return tail;
}

int main(void) {
    const char *rows_txt[] = {
        ".....#.",
        ".###.#.",
        ".....#.",
        ".###...",
        ".......",
    };
    enum { R = 5, C = 7, N = R * C };

    unsigned char blocked[N];
    for (int r = 0; r < R; r++)
        for (int c = 0; c < C; c++)
            blocked[r * C + c] = (rows_txt[r][c] == '#');

    int sources[] = { 0 * C + 0, 4 * C + 6 };
    int dist[N], owner[N];
    grid_multi_source_bfs(blocked, R, C, sources, 2, dist, owner);

    printf("distance field:\n");
    for (int r = 0; r < R; r++) {
        for (int c = 0; c < C; c++) {
            int id = r * C + c;
            if (blocked[id])        printf("## ");
            else if (dist[id] < 0)  printf(" ? ");
            else                    printf("%2d ", dist[id]);
        }
        printf("\n");
    }

    printf("owner field:\n");
    for (int r = 0; r < R; r++) {
        for (int c = 0; c < C; c++) {
            int id = r * C + c;
            if (blocked[id] || owner[id] < 0) printf("# ");
            else                              printf("%d ", owner[id]);
        }
        printf("\n");
    }
    return 0;
}
```

---

## 9. Level-by-Level Variant: "Time to Spread" (Go, Rust, C)

Sometimes you don't need per-cell distances; you need **how many rounds until everything is affected** (rotting oranges, fire spread, infection). Instead of storing `dist`, process the queue **one layer at a time** and count the rounds.

```
 Layer processing:

   queue:  [ a a a | b b b b | c c ]
             ^^^^^   layer k  (size captured BEFORE the inner loop)
   inner loop pops exactly `size` items, pushing layer k+1 behind them
   after the inner loop: rounds++
```

Problem: grid cells are `0` empty, `1` fresh, `2` rotten. Each minute, rotten cells infect 4-adjacent fresh cells. Return the minutes until no fresh cell remains, or `-1` if impossible. The demo grid `[[2,1,1],[1,1,0],[0,1,1]]` should print `4`.

The loop stops as soon as `fresh == 0`, so we don't count a final empty round.

### 9.1 Go

```go
package main

import "fmt"

// orangesRotting mutates the grid (marks infected cells as 2).
func orangesRotting(grid [][]int) int {
	rows, cols := len(grid), len(grid[0])
	type P struct{ r, c int }

	queue := []P{}
	fresh := 0
	for r := 0; r < rows; r++ {
		for c := 0; c < cols; c++ {
			switch grid[r][c] {
			case 2:
				queue = append(queue, P{r, c}) // all sources seeded together
			case 1:
				fresh++
			}
		}
	}

	dirs := [4][2]int{{1, 0}, {-1, 0}, {0, 1}, {0, -1}}
	minutes := 0

	for len(queue) > 0 && fresh > 0 {
		size := len(queue) // freeze the current layer
		for i := 0; i < size; i++ {
			p := queue[i]
			for _, d := range dirs {
				nr, nc := p.r+d[0], p.c+d[1]
				if nr < 0 || nr >= rows || nc < 0 || nc >= cols {
					continue
				}
				if grid[nr][nc] == 1 {
					grid[nr][nc] = 2 // mark at enqueue time
					fresh--
					queue = append(queue, P{nr, nc})
				}
			}
		}
		queue = queue[size:] // drop the processed layer
		minutes++
	}

	if fresh > 0 {
		return -1
	}
	return minutes
}

func main() {
	g := [][]int{{2, 1, 1}, {1, 1, 0}, {0, 1, 1}}
	fmt.Println(orangesRotting(g)) // 4
}
```

### 9.2 Rust

```rust
use std::collections::VecDeque;

fn oranges_rotting(grid: &mut Vec<Vec<i32>>) -> i32 {
    let rows = grid.len();
    let cols = grid[0].len();
    let mut queue: VecDeque<(usize, usize)> = VecDeque::new();
    let mut fresh = 0usize;

    for r in 0..rows {
        for c in 0..cols {
            match grid[r][c] {
                2 => queue.push_back((r, c)), // seed every source
                1 => fresh += 1,
                _ => {}
            }
        }
    }

    const DIRS: [(isize, isize); 4] = [(1, 0), (-1, 0), (0, 1), (0, -1)];
    let mut minutes = 0;

    while !queue.is_empty() && fresh > 0 {
        for _ in 0..queue.len() {
            // range is evaluated once, so it freezes the layer size
            let (r, c) = queue.pop_front().unwrap();
            for &(dr, dc) in &DIRS {
                let nr = r as isize + dr;
                let nc = c as isize + dc;
                if nr < 0 || nc < 0 || nr >= rows as isize || nc >= cols as isize {
                    continue;
                }
                let (nr, nc) = (nr as usize, nc as usize);
                if grid[nr][nc] == 1 {
                    grid[nr][nc] = 2;
                    fresh -= 1;
                    queue.push_back((nr, nc));
                }
            }
        }
        minutes += 1;
    }

    if fresh > 0 { -1 } else { minutes }
}

fn main() {
    let mut g = vec![vec![2, 1, 1], vec![1, 1, 0], vec![0, 1, 1]];
    println!("{}", oranges_rotting(&mut g)); // 4
}
```

### 9.3 C

```c
#include <stdio.h>
#include <stdlib.h>

/* grid is flat, rows*cols ints: 0 empty, 1 fresh, 2 rotten. Mutated. */
int oranges_rotting(int *grid, int rows, int cols) {
    static const int DR[4] = {1, -1, 0, 0};
    static const int DC[4] = {0, 0, 1, -1};

    int n = rows * cols;
    int *queue = malloc((size_t)n * sizeof(int));
    if (!queue) return -2;

    int head = 0, tail = 0, fresh = 0;
    for (int i = 0; i < n; i++) {
        if (grid[i] == 2)      queue[tail++] = i;  /* seed all sources */
        else if (grid[i] == 1) fresh++;
    }

    int minutes = 0;
    while (head < tail && fresh > 0) {
        int layer_end = tail;              /* freeze the current layer */
        while (head < layer_end) {
            int u = queue[head++];
            int r = u / cols, c = u % cols;
            for (int k = 0; k < 4; k++) {
                int nr = r + DR[k], nc = c + DC[k];
                if (nr < 0 || nr >= rows || nc < 0 || nc >= cols) continue;
                int v = nr * cols + nc;
                if (grid[v] == 1) {
                    grid[v] = 2;
                    fresh--;
                    queue[tail++] = v;
                }
            }
        }
        minutes++;
    }
    free(queue);
    return fresh > 0 ? -1 : minutes;
}

int main(void) {
    int g[9] = { 2,1,1,
                 1,1,0,
                 0,1,1 };
    printf("%d\n", oranges_rotting(g, 3, 3)); /* 4 */
    return 0;
}
```

**When to use layer-batching vs. a `dist[]` array?**

| Need | Use |
|------|-----|
| Distance for every cell | `dist[]` array (store it) |
| Only the total time / number of rounds | Layer batching (`size = len(queue)`) |
| Both | `dist[]` array; the max of it is the total time |

---

## 10. Variants and Extensions

### 10.1 Reverse Thinking: Multi-Target as Multi-Source

"For every node, distance to the nearest *target*" is multi-source BFS **from the targets**, because distance in an undirected graph is symmetric. For **directed** graphs, run BFS from the targets on the **reversed** graph (swap every edge direction).

```
 Original (directed):   a -> b -> c(target)
 Reversed graph:        a <- b <- c
 BFS from c on the reversed graph gives dist(a -> c), dist(b -> c).
```

### 10.2 Voronoi / Owner Labelling

Carry `owner[v]` as shown above. This partitions the graph into regions "closest to source `i`". Applications: territory assignment, nearest warehouse, service-area maps.

### 10.3 K-Nearest Sources (Visit Each Node Up to K Times)

If you need the `k` nearest *distinct* sources for each node, allow each node to be dequeued/accepted up to `k` times, once per distinct owner. Track `count[v]` and a small set of owners already used at `v`. Complexity becomes `O(k * (V + E))` in the worst case. Still much better than `O(S * (V + E))` when `k << S`.

### 10.4 0-1 Multi-Source BFS

If edge weights are `0` or `1`, use a **deque**: push-front for 0-weight edges, push-back for 1-weight edges. Seed all sources at distance 0. You must allow a node's distance to be **improved** (so relax: `if dist[u] + w < dist[v]`), because a node may first be seen via a 1-edge and later via a 0-edge.

```
 deque:  front <- [ 0-cost neighbors pushed here ]   ...   [ 1-cost neighbors pushed here ] -> back
```

### 10.5 Weighted Edges: Multi-Source Dijkstra

Seed the priority queue with `(0, s)` for each source. Everything else is standard Dijkstra: `O((V + E) log V)`. Plain BFS no longer works because FIFO order doesn't match weight order.

### 10.6 Sources With Different Start Times

If source `i` becomes active at time `t_i` (e.g., fires igniting at different moments), a FIFO queue seeded all at once is wrong. Options:
- Seed `(t_i, s_i)` into a **priority queue** (Dijkstra-style).
- Or bucket the sources by start time and inject each bucket into the queue when the BFS clock reaches that time (layer-batched version).

### 10.7 Multi-Source on Implicit Graphs (State-Space Search)

Nodes needn't be stored: they may be **states** (puzzle configurations, word-ladder words, bitmasks). Neighbors are generated on the fly. Multi-source BFS here means multiple start states, e.g. "min steps from any of these words to the target". Use a hash map/set for `dist`.

### 10.8 8-Directional (Chebyshev) and Other Neighborhoods

Replace the 4-direction arrays with 8 directions. The resulting distance is Chebyshev distance on an empty grid. For knight moves, use the 8 knight offsets. The algorithm itself is unchanged.

### 10.9 Bidirectional + Multi-Source

When you have a *specific* source set and a *specific* target set and want just the min distance between the sets, you can run one multi-source wave from each side and stop when they meet (expand the smaller frontier). Great on huge graphs with high branching factor.

### 10.10 Early Termination

If you only need the distance to the nearest source for a **single** query node `q`, stop as soon as `q` is dequeued/discovered. Multi-source BFS gives the answer for all nodes; you only pay for what you explore.

### 10.11 Path Reconstruction

Keep `parent[v] = u` when discovering `v`. Walk `v -> parent[v] -> ... -> source` (`parent[source] = -1`). The walk ends at the owning source.

### 10.12 Parallel / Direction-Optimizing BFS (Advanced)

For massive graphs: process each layer in parallel (frontier split across threads, atomic CAS on `dist[v]`), and switch between **top-down** (scan frontier's out-edges) and **bottom-up** (each unvisited node checks whether any neighbor is in the frontier) when the frontier becomes a large fraction of the graph. The multi-source formulation just starts with a larger initial frontier, which makes bottom-up kick in earlier.

---

## 11. Classic Problems and the Pattern Behind Each

| Problem | Sources | What is spread / measured | Notes |
|---------|---------|---------------------------|-------|
| **01 Matrix** (distance to nearest 0) | all cells with `0` | `dist` to nearest zero | Sources are zeros; answer is `dist` for each `1` |
| **Rotting Oranges** | all rotten cells | rounds until all fresh rot | Layer batching; check leftover fresh |
| **Walls and Gates** | all gates | distance to nearest gate for each empty room | Skip walls; fill unreached with INF |
| **As Far from Land as Possible** | all land cells | max `dist` over water | Answer = max of `dist`; `-1` if all land/all water |
| **Map of Highest Peak** | all water cells (height 0) | height = distance from water, adjacent diff <= 1 | BFS distance is exactly the max valid height |
| **Shortest Bridge** | all cells of island #1 (found by DFS first) | steps to reach island #2 | Two-phase: DFS to find island, multi-source BFS to cross |
| **Pacific Atlantic Water Flow** | all border cells touching an ocean | reverse flow: reachable cells | Run twice (one per ocean) on the reversed "uphill" graph, then intersect |
| **Time to infect a binary tree / burn tree** | start node(s) | rounds | Convert tree to graph with parent pointers, then BFS |
| **Escape the Spreading Fire** | fire cells | fire arrival time per cell | Precompute fire `dist`, then BFS the person only through cells they reach strictly before the fire |
| **Nearest exit / shelter / hospital** | all facilities | distance per cell | Classic production use (Section 16) |
| **Minimum steps from any of several start words/states** | all start states | steps | Implicit graph; hash map for `dist` |
| **Bus Routes (BFS over routes)** | all routes containing the start stop | number of buses | Nodes are routes, not stops |
| **Cheapest hops with a set of free "teleports"** | all teleport nodes | distance | Sometimes combined with a super-source of cost 0/1 |

### 11.1 Pattern Recognition Cheat Sheet

Trigger phrases that should light up "multi-source BFS" in your head:

- "**nearest** X for every cell/node"
- "distance to the **closest** ..."
- "**simultaneously** spreads / rots / burns / floods / infects"
- "minimum time until **everything** is ..."
- "for **each** position, distance to **any** of the marked positions"
- Many starting points but ONE shared clock

If the problem has one start and one target, it's plain BFS (or bidirectional BFS).

### 11.2 The "Flip the Question" Trick

Naive: for every cell, search outward to find the nearest special cell. That's `O((V) * (V + E))` worst case.
Flipped: start from all special cells and flow **outward once**, filling everything.

> Whenever you find yourself running a search *from each of many items*, ask: "can I run one search *from the answers* instead?"

---

## 12. Pitfalls and Bugs Everyone Hits

1. **Marking visited at dequeue instead of enqueue.** Queue explodes; TLE or MLE. Always set `dist[v]` (or a `visited` flag) at the moment you push.
2. **Forgetting duplicate sources.** If the same source appears twice, it's enqueued twice (wasted work, wrong `owner`). Guard with `if dist[s] == UNVISITED`.
3. **Seeding sources lazily inside the loop.** All sources must be in the queue at distance 0 **before** the first pop. Otherwise a late source's distance is wrong.
4. **Using DFS/stack accidentally.** A stack gives a valid traversal but wrong distances.
5. **Using `dist[v] > dist[u] + 1` relaxations in plain BFS.** Not needed; first discovery is optimal. (It *is* needed for 0-1 BFS and Dijkstra.)
6. **Not guarding bounds on grids.** Check `0 <= nr < rows` and `0 <= nc < cols` **before** indexing. In Rust, mixing `usize` and negative offsets underflows; convert to `isize` first.
7. **Layer batching bug:** reading `len(queue)` inside the loop condition instead of freezing it before. In Go, `for i := 0; i < len(queue); i++` while appending will process the *next* layers too and your "minutes" counter will be wrong.
8. **Off-by-one in answer:** counting the last empty round. Stop as soon as the target count (e.g. `fresh`) hits 0, or track `max(dist)` instead.
9. **Treating unreachable cells as `0` or `-1` in a max.** Handle "can't reach" explicitly (return `-1`, or skip).
10. **Integer overflow with `INF + 1`.** If you use `INT_MAX` as INF and then compute `dist[u] + 1` on an unvisited node, you overflow. BFS never expands from unvisited nodes, but variants (0-1 BFS, Dijkstra) might; use a safe INF like `INT_MAX / 2`.
11. **Using BFS on weighted graphs.** BFS is only correct for unit weights. Use 0-1 BFS or Dijkstra otherwise.
12. **Assuming ties go to a specific source "by value".** They go by queue order. If the label order matters, seed in that order.
13. **Mutating the input grid and then reusing it.** The rotting-oranges version mutates; copy first if the caller needs the original.
14. **Recursion for BFS.** Not needed and dangerous (stack overflow). BFS is iterative by nature.

---

## 13. Alternatives: When NOT to Use Multi-Source BFS

| Situation | Better tool | Why |
|-----------|-------------|-----|
| Weighted edges (non-negative) | Multi-source **Dijkstra** | FIFO order != cost order |
| Edge weights only 0 or 1 | **0-1 BFS** (deque) | Linear time with weights |
| Negative weights | **Bellman-Ford** (or SPFA) with a super-source | Dijkstra/BFS invalid |
| All-pairs distances needed | **Floyd-Warshall** / repeated BFS | Multi-source collapses sources into one min; all-pairs keeps them separate |
| Empty grid, Manhattan distance, many sources | **Two-pass DP distance transform** (forward + backward sweep) | `O(RC)` with tiny constants, no queue. *With obstacles it does not generally work in two passes* (paths can wind around walls) |
| Euclidean nearest source | **Distance transform** (Felzenszwalb) / k-d tree | BFS gives graph/Manhattan/Chebyshev distance, not Euclidean |
| Per-source distances are needed (not just min) | Run BFS per source | You need `dist(s_i, v)` for each `i` |
| Only a single `(s, t)` query on a huge graph | **Bidirectional BFS** or A* | Explores far fewer nodes |
| Dynamic graph with frequent edge changes | Incremental/decremental SSSP structures | Recomputing BFS each time is wasteful |

### 13.1 The "Min Over Sources" Limitation

Multi-source BFS fundamentally merges sources into a single "nearest" answer. It **loses** which distances belonged to which source (except the owner of the minimum). If you ever need *"distance from source 3 specifically"*, you need a separate BFS from source 3.

---

## 14. Performance Engineering

### 14.1 Data Layout

- **Flatten the grid** (`id = r * cols + c`). One contiguous array is dramatically more cache-friendly than `[][]int`.
- Use **`int32`/`u32`** for `dist` and queue entries when `n < 2^31`; halves memory bandwidth versus 64-bit.
- Use **CSR** for general graphs (Section 7.3) rather than per-node vectors/slices; neighbors are contiguous.

### 14.2 Queue

- Array + `head`/`tail` is the fastest queue for BFS since each node is pushed once. Size `n` is a hard upper bound.
- In Rust, `VecDeque` is fine; for max speed use `Vec<u32>` + `head` index (never pop).
- In Go, `queue = queue[1:]` leaks the front of the backing array; prefer the `head` index approach used above.

### 14.3 Avoiding Bounds-Check Overhead

- Pad the grid with a **sentinel wall border** (`(R+2) x (C+2)`), then no bounds checks are needed inside the inner loop.
- Precompute neighbor offsets as `{-cols, +cols, -1, +1}` and do `v = u + off[k]`; the border guarantees safety.

```
 padded grid (W = walls):

   W W W W W W W
   W . . . . . W
   W . . . . . W        neighbors of id: id-W, id+W, id-1, id+1
   W . . . . . W        (no `if` for bounds)
   W W W W W W W
```

### 14.4 Early Exit

If you only care about distance to a limited radius `D`, stop when `dist[u] >= D`. If you only need the nearest source for a few queries, stop when the query nodes are all reached.

### 14.5 Memory

`dist` doubles as `visited`; do not allocate a separate `visited` array unless you need `dist` to hold something else. `owner` can be dropped if not needed.

### 14.6 Complexity vs. Reality

`O(V + E)` hides constants. A `10^4 x 10^4` grid has `10^8` cells: around 400 MB for `int32 dist` + `int32 queue` alone. At that scale, consider tiling, bitset frontiers, or streaming algorithms.

---

## 15. Testing Strategy

### 15.1 Brute-Force Oracle

For small random graphs/grids, compare multi-source BFS with: "for each source run single-source BFS; `dist[v] = min over sources`". They must match exactly.

```
 for trial in 1..10000:
     g       = random graph / grid (n <= 12)
     sources = random non-empty subset
     fast    = multi_source_bfs(g, sources)
     slow    = [ min_s bfs(g, s)[v] for v in nodes ]
     assert fast.dist == slow
```

### 15.2 Edge Cases Checklist

- Zero sources (everything unreachable)
- All nodes are sources (all distances 0)
- Duplicate sources
- Source on a wall/invalid cell
- Single-cell grid
- Disconnected components (some unreachable)
- 1xN and Nx1 grids
- Ties between sources (owner determinism)
- Large grid for performance and memory

### 15.3 Property Checks (no oracle needed)

For every edge `(u, v)` with both ends reached: `|dist[u] - dist[v]| <= 1`. For every non-source reached node there exists a neighbor with `dist = dist[v] - 1`. Both properties together characterize a correct BFS distance field.

---

## 16. Real-World Systems Architecture

### 16.1 "Nearest Facility" Service (maps, delivery, emergency response)

Distances are precomputed offline with multi-source BFS (or Dijkstra for weighted roads), then served with O(1) lookups.

```
 +-------------------+      +----------------------+      +---------------------+
 |  Road graph /     |      |  Facility registry   |      |  Config: metric,    |
 |  walkable grid    |      |  (hospitals, depots) |      |  max radius, mode   |
 +---------+---------+      +-----------+----------+      +----------+----------+
           |                            |                            |
           +----------------------------+----------------------------+
                                        |
                                        v
                    +-----------------------------------------+
                    |   OFFLINE PRECOMPUTE JOB (batch)        |
                    |                                         |
                    |   seed queue with ALL facilities        |
                    |   multi-source BFS / Dijkstra           |
                    |   outputs: dist[node], owner[node]      |
                    +--------------------+--------------------+
                                         |
                              writes versioned snapshot
                                         |
                                         v
                    +-----------------------------------------+
                    |   Snapshot store (object storage / KV)  |
                    |   dist.bin, owner.bin  (flat arrays)    |
                    +--------------------+--------------------+
                                         |
                                     loads / mmap
                                         |
                                         v
        +-----------------------------------------------------------+
        |  ONLINE QUERY SERVICE                                     |
        |                                                           |
        |  request(lat, lon) -> snap to node id -> dist[id], owner[id]
        |                                       -> O(1) response    |
        +-----------------------------------------------------------+
                                         ^
                                         |
                       facility added/removed => trigger recompute
                       (or incremental update for small changes)
```

**Why this architecture works:** one `O(V + E)` precompute replaces `O(users x search)` at query time. Owner labels immediately tell which facility to route to; path reconstruction then needs only a local walk along `parent` pointers.

### 16.2 Game AI: Flow Field / Influence Map

Many agents need to move toward the nearest goal (or away from nearest threat). Instead of one pathfinding per agent, compute one distance field and let agents descend the gradient.

```
 +-----------------+     +------------------------+     +--------------------+
 | World tile map  |---->| Goal tiles (exits,     |---->| Multi-source BFS   |
 | (obstacles)     |     | players, resources)    |     | from all goals     |
 +-----------------+     +------------------------+     +----------+---------+
                                                                   |
                                                         dist[] (cost field)
                                                                   |
                                                                   v
                                                     +-----------------------------+
                                                     | Flow field: for each tile   |
                                                     | pick the neighbor with the  |
                                                     | smallest dist -> direction  |
                                                     +--------------+--------------+
                                                                    |
                    +-------------------+-------------------+-------+------------+
                    v                   v                   v                    v
                 agent 1             agent 2             agent 3     ...      agent N
              (O(1) per step: lookup tile direction, no per-agent search)
```

Recompute only when the obstacle map or goal set changes; hundreds or thousands of agents then share a single BFS.

### 16.3 Network / CDN: Nearest Replica and Hop-Count Routing

```
   Clients ----> [ Edge router ] ----> lookup(region) ----> nearest replica
                        ^
                        |
         Control plane periodically runs multi-source BFS
         over the topology graph, seeds = replica nodes,
         publishes hop-distance table + owner (replica id)
```

### 16.4 Image Processing / Robotics

- **Distance transform** on a binary image: BFS from all foreground pixels gives the distance of every background pixel to the nearest foreground.
- **Occupancy-grid inflation:** BFS from all obstacle cells to the robot's radius, marking "unsafe" cells.
- **Watershed-like segmentation:** seeds = markers, owner = region label.

```
 binary image            seeds (1s)              distance map (Manhattan)
 0 0 0 0 0               . . . . .               2 1 0 1 2
 0 0 1 0 0     ---->     . . S . .     ---->     3 2 1 2 3
 0 0 0 0 0               . . . . .               4 3 2 3 4
```

### 16.5 Epidemic / Fire / Flood Simulation (Discrete Model)

Sources = initially infected/burning/flooded cells. Layer-batched BFS gives "arrival time" per cell. Combine with **blocked cells** (firebreaks, rivers) and you have a coarse but fast spread model, useful for first-order planning before running heavier physics-based simulation.

---

## 17. Decision Checklist and Mental Model Summary

### 17.1 The 6-Question Decision Flow

```
  Q1. Is the graph unweighted (or all edges equal cost)?
        no  -> Dijkstra (multi-source: seed all at 0)  /  0-1 BFS if weights in {0,1}
        yes -> continue
  Q2. Do I need distance to the NEAREST of several special nodes?
        no  -> plain BFS / bidirectional BFS
        yes -> continue
  Q3. Do all sources start at the same time?
        no  -> priority queue keyed by time, or bucket-inject by time
        yes -> continue
  Q4. Do I need Euclidean distance on an empty grid?
        yes -> distance transform, not BFS
        no  -> continue
  Q5. Do I need only a global time (rounds)?
        yes -> layer-batched BFS (size = len(queue))
        no  -> store dist[] (and owner[] if regions matter)
  Q6. Directed graph and sources are TARGETS?
        yes -> reverse the edges, BFS from the targets
        no  -> BFS from the sources
```

### 17.2 The Mental Model in Five Lines

1. **All sources are one source** (virtual super-node). Everything is just ordinary BFS afterward.
2. **The queue is a clock.** Nodes come out in order of distance; at most two adjacent distance values live in it.
3. **Mark at enqueue.** Each node is visited exactly once, so total work is `O(V + E)`.
4. **First touch wins.** The first wave to reach a node defines its distance and owner.
5. **Flip the question.** When you'd search *from each item*, search *from the answers* once.

### 17.3 Implementation Template to Memorize

```
 1. dist[*] = -1
 2. for s in sources (dedupe): dist[s] = 0; push(s)
 3. while queue:
        u = pop()
        for v in neighbors(u):
            if dist[v] == -1:          # visited check + answer in one array
                dist[v] = dist[u] + 1  # (or owner, parent, etc.)
                push(v)
 4. read dist[] / max(dist) / owner[] as the answer
```

If you can write these 4 steps from memory and explain *why* each line is there (Sections 2 to 5), you own this algorithm.

### 17.4 Practice Ladder

1. Implement single-source BFS on a grid (no peeking).
2. Convert to multi-source: only change the initialization.
3. Solve **01 Matrix**, **Walls and Gates**, **Rotting Oranges**.
4. Solve **As Far from Land as Possible**, **Map of Highest Peak**.
5. Solve **Shortest Bridge** (DFS + multi-source BFS) and **Escape the Spreading Fire** (two BFS passes).
6. Add owner labels and render a Voronoi map.
7. Implement the 0-1 multi-source variant and multi-source Dijkstra; test against the brute-force oracle.
8. Build a mini flow-field demo for N agents sharing one BFS.

---

*End of guide.*
