# Goroutines: The Complete In-Depth Guide

> Goal: build a correct **mental model** of goroutines so you can reason about concurrent Go code quickly and safely. This guide goes from "what is it" to scheduler internals, patterns, real-world architectures, pitfalls, and debugging.

---

## Table of Contents

1. [What is a goroutine?](#1-what-is-a-goroutine)
2. [Why goroutines exist: threads vs goroutines vs async/await](#2-why-goroutines-exist)
3. [Concurrency vs Parallelism](#3-concurrency-vs-parallelism)
4. [Runtime architecture: the G-M-P model](#4-runtime-architecture-the-g-m-p-model)
5. [The scheduler in depth](#5-the-scheduler-in-depth)
6. [Goroutine lifecycle and states](#6-goroutine-lifecycle-and-states)
7. [Goroutine stacks](#7-goroutine-stacks)
8. [Creating goroutines: syntax, closures, gotchas](#8-creating-goroutines)
9. [Waiting and synchronization: `sync` package](#9-waiting-and-synchronization-the-sync-package)
10. [Channels (the communication primitive)](#10-channels)
11. [`select` statement](#11-the-select-statement)
12. [Go memory model and happens-before](#12-go-memory-model-and-happens-before)
13. [`context`: cancellation, deadlines, propagation](#13-context)
14. [Concurrency patterns](#14-concurrency-patterns)
15. [Where goroutines are used in the real world](#15-where-goroutines-are-used-in-the-real-world)
16. [Real-world architectures (ASCII)](#16-real-world-architectures)
17. [Common bugs: races, deadlocks, leaks, panics](#17-common-bugs)
18. [Debugging and observability](#18-debugging-and-observability)
19. [Performance engineering](#19-performance-engineering)
20. [Testing concurrent code](#20-testing-concurrent-code)
21. [Runtime knobs and special APIs](#21-runtime-knobs-and-special-apis)
22. [Mental-model cheat sheet and decision tree](#22-mental-model-cheat-sheet)
23. [Interview-style Q&A](#23-interview-style-qa)

---

## 1. What is a goroutine?

A **goroutine** is a function executing **concurrently**, managed by the **Go runtime**, not by the operating system.

```go
go doWork()          // starts doWork in a new goroutine; caller continues immediately
```

Key facts:

| Property | Value |
|---|---|
| Managed by | Go runtime scheduler (user space) |
| Initial stack | ~2 KB (grows and shrinks dynamically) |
| Creation cost | ~ hundreds of nanoseconds, small allocation |
| Context switch | ~100-200 ns (no kernel involvement in the common case) |
| Practical count | 100,000s to millions per process |
| Identity | No public ID, no handle, no "kill goroutine" API |
| Mapping to OS threads | **M:N**: many goroutines multiplexed onto few threads |

### The mental model in one picture

```text
+--------------------------------------------------------------+
|                       YOUR GO PROGRAM                        |
|   go f()    go g()    go h()   ...  (100,000s of goroutines) |
+------------------------------+-------------------------------+
                               |
                               v
+--------------------------------------------------------------+
|                    GO RUNTIME SCHEDULER                      |
|        multiplexes M goroutines onto N OS threads (M:N)      |
+------------------------------+-------------------------------+
                               |
                               v
+--------------------------------------------------------------+
|                     OS KERNEL SCHEDULER                      |
|                 threads (a handful, ~GOMAXPROCS+)            |
+------------------------------+-------------------------------+
                               |
                               v
+--------------------------------------------------------------+
|                    CPU CORES (hardware)                      |
+--------------------------------------------------------------+
```

**Think of it like this:** goroutines are *tasks*; OS threads are *workers*; the Go scheduler is a *dispatcher* that hands tasks to workers and swaps tasks when one waits (I/O, channel, lock, sleep).

### What a goroutine is NOT

- Not an OS thread (many goroutines share a thread over time).
- Not a coroutine in the cooperative-only sense (since Go 1.14 it is also **preempted asynchronously**).
- Not a "future/promise": `go f()` returns nothing. Results come back via channels, shared memory + sync, or `errgroup`.
- Not free: each has a stack, a descriptor (`g` struct), and consumes scheduler/GC work.

---

## 2. Why goroutines exist

### 2.1 The problem

Servers must handle thousands of simultaneous activities (connections, requests, timers) where most of the time is spent **waiting** (network, disk, DB). You need a cheap way to say: "do this thing, and while it waits, run something else."

### 2.2 Three models compared

```text
MODEL A: Thread per task (OS threads)
-------------------------------------
 task1 --> [Thread 1: 1-8 MB stack]  --+
 task2 --> [Thread 2: 1-8 MB stack]  --+--> Kernel schedules all
 task3 --> [Thread 3: 1-8 MB stack]  --+    (expensive context switch, 
 ...                                         limited to ~10k threads)

MODEL B: Event loop / async-await (Node.js, Python asyncio, JS)
----------------------------------------------------------------
 task1 --+
 task2 --+--> [ ONE thread: event loop + callbacks/await ]
 task3 --+     (cheap, but: function "coloring", CPU work blocks loop,
               one core unless you add workers)

MODEL C: Goroutines (M:N)
-------------------------
 g1  --+                +--> [Thread 1] --> Core 1
 g2  --+                |
 g3  --+--> [Scheduler]-+--> [Thread 2] --> Core 2
 ...   |                |
 g100k-+                +--> [Thread N] --> Core N
     (write blocking-style code; runtime makes it non-blocking underneath)
```

### 2.3 Comparison table

| Aspect | OS Thread | Goroutine | async/await task |
|---|---|---|---|
| Stack | fixed, 1-8 MB reserved | 2 KB start, growable | heap state machine |
| Switch cost | ~1-5 us (kernel) | ~0.1-0.2 us (user) | cheap |
| Count limit | thousands | millions | millions |
| Uses all cores | yes | yes (automatic) | usually 1 core per loop |
| Code style | blocking | blocking (looks synchronous) | colored functions (`async`) |
| Preemption | kernel, preemptive | runtime, preemptive (1.14+) | cooperative at `await` |
| Blocking syscall | blocks thread | runtime hands off the P | blocks the loop (unless offloaded) |

**The big idea:** *write simple blocking code, get scalable non-blocking behavior.* The runtime turns blocking operations (network I/O, channel ops, mutexes, sleeps) into "park this goroutine, run another."

---

## 3. Concurrency vs Parallelism

- **Concurrency** = *structuring* a program as independently executing activities (about **dealing with** many things at once).
- **Parallelism** = *actually running* multiple things at the same instant on multiple cores (about **doing** many things at once).

```text
CONCURRENCY (1 core, interleaved)          PARALLELISM (2 cores, simultaneous)

 time ->                                    time ->
 core1: [A][B][A][C][B][A][C]               core1: [A][A][A][A][A]
                                            core2: [B][B][B][C][C]
```

Goroutines give you **concurrency by design**; the runtime provides **parallelism** when `GOMAXPROCS > 1` and the hardware allows it.

- With `GOMAXPROCS=1` you still have concurrency (interleaving), but not parallelism.
- Concurrency does not automatically make code faster. It makes **waiting** overlap (I/O-bound) and lets CPU-bound work use multiple cores (parallelism).

---

## 4. Runtime architecture: the G-M-P model

The Go scheduler has three core entities:

| Symbol | Name | What it is |
|---|---|---|
| **G** | Goroutine | The task: stack, instruction pointer (saved registers), status, wait reason |
| **M** | Machine | An **OS thread**. Executes Go code only when it holds a P |
| **P** | Processor | A **scheduling context / token**. Holds a local run queue, a memory cache (`mcache`), timers. Count = `GOMAXPROCS` |

> **Rule:** An M must hold a P to run Go code. The number of Ps = max number of goroutines running Go code *simultaneously*.

### 4.1 The structure

```text
                  +------------------------------------------------------+
                  |               GLOBAL RUN QUEUE  (has a lock)         |
                  |        [G10] -> [G11] -> [G12] -> [G13] -> ...       |
                  +------------------------------------------------------+
                         ^                 ^                  ^
                         | (overflow /     | (checked every   | (checked when
                         |  fairness)      |  61st schedule   |  local queue
                         |                 |  tick)           |  is empty)

+-------------------+   +-------------------+   +-------------------+
| P0                |   | P1                |   | P2                |
| runnext: [G1]     |   | runnext: [ ]      |   | runnext: [ ]      |
| local Q: [G2][G3] |   | local Q: [G4]     |   | local Q: (empty)  |
| mcache (mem cache)|   | mcache (mem cache)|   | mcache (mem cache)|
+---------+---------+   +---------+---------+   +---------+---------+
          |                       |                       |
          | attached              | attached              | attached
          v                       v                       v
+-------------------+   +-------------------+   +-------------------+
| M0 (OS thread)    |   | M1 (OS thread)    |   | M2 (OS thread)    |
| running: G1       |   | running: G4       |   | running: G7       |
+---------+---------+   +---------+---------+   +---------+---------+
          |                       |                       |
          v                       v                       v
+-------------------+   +-------------------+   +-------------------+
| CPU core 0        |   | CPU core 1        |   | CPU core 2        |
+-------------------+   +-------------------+   +-------------------+

 Idle Ms (parked, no P):  M3  M4        Idle Ps: (none right now)
 Blocked-in-syscall Ms:   M5 (holds no P; its P was handed off)
```

### 4.2 Why a P exists at all (why not just G and M?)

Early Go (pre-1.1) had only G and M with a single global queue and a global lock, which didn't scale. The **P** was introduced to give each running thread **its own local queue and cache**, so that:

1. Scheduling is mostly **lock-free** (local queue access needs no global lock).
2. Memory allocator caches (`mcache`) are per-P: allocation is mostly contention-free.
3. When an M blocks in a syscall, its P (with all its queued goroutines) can be **handed to another M** so work isn't stranded.

### 4.3 Internal structs (simplified)

```text
g  (goroutine)                m  (machine/thread)         p  (processor)
+---------------------+       +---------------------+     +----------------------+
| stack (lo, hi)      |       | g0 (scheduler stack)|     | status               |
| stackguard          |       | curg  -> running g  |     | runq [256]*g (ring)  |
| sched (SP, PC, ...) |       | p     -> attached p |     | runnext *g           |
| atomicstatus        |       | spinning bool       |     | mcache               |
| waitreason          |       | id, tls, ...        |     | timers heap          |
| m (current M)       |       +---------------------+     | gFree (dead g cache) |
| goid                |                                   +----------------------+
+---------------------+
```

- Every M has a special **g0** goroutine whose stack runs the *scheduler itself* (so scheduling code doesn't run on user goroutine stacks).
- `runq` is a fixed ring buffer of 256 goroutines per P; overflow spills half to the global queue.
- `runnext` is a one-slot "run me next" cache (explained below).

---

## 5. The scheduler in depth

### 5.1 The main loop: how the next goroutine is chosen

When a goroutine blocks, yields, finishes, or is preempted, the M runs `schedule()` on its `g0` stack. This calls `findRunnable()`:

```text
                         schedule()
                              |
                              v
              +-------------------------------+
              | 1. tick % 61 == 0 ?           |--yes--> take 1 from GLOBAL queue
              +---------------+---------------+         (prevents global starvation)
                              | no
                              v
              +-------------------------------+
              | 2. P.runnext / P.local queue  |--found--> run it
              +---------------+---------------+
                              | empty
                              v
              +-------------------------------+
              | 3. GLOBAL queue (grab a batch)|--found--> run it
              +---------------+---------------+
                              | empty
                              v
              +-------------------------------+
              | 4. NETPOLL (non-blocking)     |--ready Gs--> run them
              |    which sockets are ready?   |
              +---------------+---------------+
                              | nothing
                              v
              +-------------------------------+
              | 5. WORK STEALING              |--stole-->  run it
              |    try other Ps (random order)|
              |    take HALF of their queue   |
              +---------------+---------------+
                              | nothing
                              v
              +-------------------------------+
              | 6. Release P, park M          |
              |    (sleep until work arrives) |
              +-------------------------------+
```

### 5.2 `runnext` and why new goroutines run "soon"

When goroutine **A** does `go B()`, or wakes goroutine **B** via a channel, **B is placed in A's P `runnext` slot** (not the tail of the queue). It inherits the remainder of A's time slice. This gives:

- **Cache locality** (producer/consumer pairs run on the same core with hot data).
- **Low latency** for ping-pong communication.

The old `runnext` occupant (if any) is kicked to the tail of the local queue.

### 5.3 Work stealing

```text
BEFORE:                                    AFTER P2 steals from P0:

 P0 local Q: [G2][G3][G4][G5][G6][G7]       P0 local Q: [G2][G3][G4]
 P2 local Q: (empty)                        P2 local Q: [G5][G6][G7]   <- stole half

        M2 (idle, "spinning") looks for work:
          1) global queue  2) netpoll  3) random victim P -> steal half
```

- **Spinning Ms:** idle Ms may "spin" (busy-look for work) briefly before sleeping. The runtime limits the number of spinning Ms to about half of busy Ps to avoid burning CPU.
- When a new goroutine is created and there are idle Ps with no spinners, the runtime **wakes a P/M** (`wakep()`) so parallelism ramps up quickly.

### 5.4 Blocking operations and how the scheduler reacts

There are four categories. This is the heart of why goroutines scale.

| Blocking cause | What happens | OS thread blocked? |
|---|---|---|
| **Channel op / mutex / `sync.Cond` / `select`** | G is parked on a wait queue; M picks another G | No |
| **Network I/O** (via netpoller) | G is parked; fd registered in epoll/kqueue/IOCP; M picks another G | No |
| **Timers / `time.Sleep`** | G parked; timer lives in the P's timer heap | No |
| **Blocking syscall** (file I/O, cgo) | M blocks with the G; runtime **hands off the P** to another M | **Yes** (but P keeps working) |

#### 5.4.1 Channel blocking

```text
 G1: ch <- v   (no receiver ready)
      |
      v
 G1 status: running -> waiting (chan send)
 G1 enqueued in ch.sendq
 M calls schedule() -> picks G2 and keeps running.   (M never sleeps)

 Later G3: <-ch
      |
      v
 finds G1 in sendq -> copies value directly -> marks G1 runnable
 G1 placed in G3's P.runnext
```

#### 5.4.2 Network I/O via the netpoller

```text
  +-------------+         +-----------------------+
  | goroutine G |  Read() | net package (internal)|
  +------+------+-------->+-----------+-----------+
         |                            |
         | fd not ready (EAGAIN)      | register fd with poller
         v                            v
   G parked (waiting: IO wait)   +-----------------------------+
   M runs another G              | NETPOLLER                   |
                                 | epoll (Linux) / kqueue (BSD)|
                                 | / IOCP (Windows)            |
                                 +--------------+--------------+
                                                | fd becomes readable
                                                v
                              scheduler (findRunnable step 4 or sysmon)
                              marks G runnable -> run queue
```

Sockets are set **non-blocking**. Your code looks blocking (`conn.Read(buf)`), but underneath it's event-driven. That's why Go servers can hold 100k+ idle connections cheaply.

#### 5.4.3 Blocking syscalls: P handoff

```text
 Step 1: M1 runs G1 with P1.        P1 queue: [G2][G3]

   +------+     +------+
   |  P1  |<--->|  M1  | running G1
   +------+     +------+
   [G2][G3]

 Step 2: G1 enters a blocking syscall (e.g. read from disk).
         M1 is stuck in the kernel with G1.

   +------+     +------+
   |  P1  |     |  M1  | blocked in syscall (G1)
   +------+     +------+
   [G2][G3]       (P1 still attached "loosely", marked _Psyscall)

 Step 3: sysmon (background monitor) notices after ~20us - 10ms
         and RETAKES P1, giving it to another M (M2, woken or created).

   +------+     +------+       +------+
   |  P1  |<--->|  M2  |       |  M1  | still blocked (G1)
   +------+     +------+       +------+
   [G2][G3]   runs G2, G3

 Step 4: syscall returns. M1 tries to get a P back:
         - idle P available?  -> continue running G1
         - none?  -> put G1 on global queue, M1 sleeps (parked)
```

**Consequence:** number of OS threads can exceed `GOMAXPROCS` (the limit on *running Go code*, not on threads blocked in syscalls). Default max threads is 10,000 (`debug.SetMaxThreads`).

### 5.5 `sysmon`: the background monitor

`sysmon` runs on its own M **without a P**. It wakes periodically (20 us up to 10 ms, adaptive) and:

1. **Retakes Ps** from Ms stuck in syscalls.
2. **Preempts** goroutines running for more than **10 ms** continuously.
3. Polls the **network** if nobody has done so for a while.
4. Helps trigger **GC** forcing (every 2 minutes) and scavenger work.

### 5.6 Preemption (cooperative to asynchronous)

```text
Go < 1.14 : cooperative only.  Preemption check occurs in function prologues.
            A tight loop `for {}` with no function calls could HANG the scheduler
            (starve other goroutines and block GC stop-the-world).

Go >= 1.14: ASYNCHRONOUS preemption.
            sysmon sees G running > 10ms -> sends signal (SIGURG on Unix) to the thread
            -> signal handler injects a call to the scheduler at a "safe point"
            -> G is paused and requeued.
```

So long CPU loops can no longer monopolize a P.

### 5.7 Fairness mechanisms (summary)

| Mechanism | Prevents |
|---|---|
| 10 ms time slice + async preemption | CPU hog goroutine starving others |
| Check global queue every 61 ticks | Global-queue goroutines starving behind local ones |
| `runnext` inherits time slice | Two ping-ponging goroutines monopolizing a P forever |
| Work stealing | Idle Ps while others are overloaded |
| Mutex starvation mode (>1 ms wait) | A waiter being starved by newcomers barging in |

### 5.8 `GOMAXPROCS`

- Sets the number of **Ps** (max goroutines executing Go code simultaneously).
- Default: number of logical CPUs. Since **Go 1.25**, on Linux the default also respects **cgroup CPU limits** (container-aware), and updates if limits change. For older versions in Kubernetes, use `go.uber.org/automaxprocs` or set `GOMAXPROCS` explicitly, otherwise you may run 64 Ps on a 2-CPU quota and suffer CFS throttling.

```go
import "runtime"

runtime.GOMAXPROCS(0)   // query current value (0 = don't change)
runtime.NumCPU()        // logical CPUs visible
runtime.NumGoroutine()  // live goroutines right now
```

---

## 6. Goroutine lifecycle and states

```text
                     go f()
                       |
                       v
                  +---------+
                  | _Gidle  |  (allocated, not yet initialized)
                  +----+----+
                       |
                       v
   +--------------+  schedule   +-----------+
   | _Grunnable   |------------>| _Grunning |-----------+
   | (in a queue) |<------------| (on an M) |           |
   +------^-------+  preempted/ +-----+-----+           | function returns
          |          yield           |                   v
          |                          | block (chan, mutex,      +-----------+
          | ready()                  | sleep, IO wait)          |  _Gdead   |
          |                          v                          | (recycled |
          |                   +-------------+                   |  in gFree)|
          +-------------------| _Gwaiting   |                   +-----------+
            event happened    | (parked)    |
                              +-------------+

   _Gsyscall : running in a blocking syscall (holds M, not P)
   _Gcopystack/_Gpreempted/_Gscan* : transient states (stack growth, preemption, GC scan)
```

**Dead goroutines are not freed immediately.** Their `g` structs are cached in per-P free lists and reused for new goroutines (cheap creation).

### Goroutine termination: how does a goroutine end?

1. Its function **returns**.
2. It calls `runtime.Goexit()` (runs deferred calls then ends only that goroutine).
3. An **unrecovered panic** occurs: this crashes the **entire program**, not just the goroutine.
4. The program exits (`main` returns / `os.Exit`): all goroutines are **killed abruptly**, with no deferred functions run.

> There is **no way to forcibly kill another goroutine.** You must cooperate: use `context`, a `done` channel, or close an input channel.

---

## 7. Goroutine stacks

### 7.1 Growable, contiguous stacks

```text
Initial (2 KB):               After deep recursion needs more:

 high addr +------------+     high addr +----------------------+
           | frame f3   |               | frame f1             |
           | frame f2   |      copy     | frame f2             |
           | frame f1   |  ---------->  | frame f3             |
 low addr  +------------+               | frame f4 ...         |
           [guard check]      (2x size) | (new 4 KB block)     |
                                low addr +----------------------+
```

How it works:

1. Every function prologue compares SP against `stackguard`.
2. On overflow, the runtime calls `morestack` -> allocates a stack **2x bigger**, **copies** the old stack, and **adjusts pointers** into the stack.
3. During GC, stacks may be **shrunk** (halved) if less than 1/4 used.
4. Maximum stack: **1 GB** on 64-bit (configurable by `debug.SetMaxStack`). Exceeding gives `fatal error: stack overflow` (not recoverable).

Since Go 1.19, the **initial stack size is adaptive** (based on average stack use observed by GC), reducing early growth copies.

### 7.2 Consequences

- Pointers to stack variables are only valid inside the runtime's control; escape analysis moves variables to the heap if their address outlives the frame.
- 100,000 goroutines x ~2-8 KB = ~200-800 MB at most: very feasible. 100,000 threads x 1+ MB is not.
- Deep recursion is OK in Go (stack grows), unlike C.

---

## 8. Creating goroutines

### 8.1 Syntax

```go
go f(x, y)                 // named function
go func() { ... }()        // anonymous function (note the call parentheses)
go obj.Method(arg)         // method
```

**Argument evaluation timing:** arguments of `go f(args)` are evaluated **in the calling goroutine, immediately**; only the call itself runs later.

```go
x := 10
go fmt.Println(x)   // x evaluated NOW (10), printed later
x = 20              // does not affect the already-evaluated argument
```

### 8.2 `main` must wait

When `main()` returns, the program ends, killing all goroutines.

```go
func main() {
    go fmt.Println("hello")   // may NEVER print
}
```

Fix with synchronization (never with `time.Sleep` in real code):

```go
func main() {
    var wg sync.WaitGroup
    wg.Add(1)
    go func() {
        defer wg.Done()
        fmt.Println("hello")
    }()
    wg.Wait()
}
```

### 8.3 The loop-variable capture gotcha

```go
// Go < 1.22: ONE shared variable i for the whole loop
for i := 0; i < 3; i++ {
    go func() { fmt.Println(i) }()   // often prints 3 3 3
}

// Fixes for old Go
for i := 0; i < 3; i++ {
    i := i                           // shadow copy
    go func() { fmt.Println(i) }()
}
for i := 0; i < 3; i++ {
    go func(n int) { fmt.Println(n) }(i)   // pass as argument
}
```

**Go 1.22+**: with `go 1.22` (or newer) in `go.mod`, each loop iteration has its **own** variable, so the first example behaves "as expected" (0 1 2 in some order). Still check the `go` directive in `go.mod`.

### 8.4 Goroutine function signature and return values

A goroutine's return values are **discarded**. Use channels or shared state:

```go
result := make(chan int, 1)
go func() { result <- compute() }()
fmt.Println(<-result)
```

### 8.5 Panics in goroutines

```go
go func() {
    panic("boom")        // crashes THE WHOLE PROGRAM
}()
```

A `recover()` only works **in the same goroutine** (in a deferred function). Parent cannot recover a child's panic.

```go
go func() {
    defer func() {
        if r := recover(); r != nil {
            log.Printf("recovered: %v\n%s", r, debug.Stack())
        }
    }()
    riskyWork()
}()
```

### 8.6 Ownership rule (very important mental model)

> **Whoever starts a goroutine is responsible for knowing when and how it stops.**

Before writing `go ...`, answer:
1. How does this goroutine **end**?
2. Who **waits** for it (or is it intentionally fire-and-forget)?
3. How are **errors** reported?
4. How is it **cancelled**?

---

## 9. Waiting and synchronization: the `sync` package

Goroutines share memory. Unsynchronized concurrent access with at least one write is a **data race** (undefined-ish behavior: torn values, stale reads, corrupted maps).

### 9.1 `sync.WaitGroup`: wait for N goroutines

```go
var wg sync.WaitGroup
for _, url := range urls {
    wg.Add(1)                 // BEFORE starting the goroutine
    go func() {
        defer wg.Done()       // always via defer
        fetch(url)
    }()
}
wg.Wait()                     // blocks until counter hits 0
```

**Go 1.25+** adds a helper that does Add/Done for you:

```go
var wg sync.WaitGroup
for _, url := range urls {
    wg.Go(func() { fetch(url) })
}
wg.Wait()
```

Rules: `Add` must happen-before `Wait`; calling `Add` inside the goroutine is racy. Never copy a WaitGroup (pass pointer). Counter must not go negative (panic).

### 9.2 `sync.Mutex`: mutual exclusion

```go
type Counter struct {
    mu sync.Mutex
    n  int
}

func (c *Counter) Inc() {
    c.mu.Lock()
    defer c.mu.Unlock()
    c.n++
}
```

Internals (simplified):

```text
Lock():
  fast path: CAS(state: 0 -> locked)          <- uncontended = one atomic op
  slow path: spin a few times (if multi-core), then park goroutine in a semaphore queue
  Normal mode : woken waiter competes with newly arriving goroutines (can lose)
  Starvation mode: if a waiter waits > 1 ms, lock is handed directly (FIFO) to it
```

Rules: don't copy a mutex; don't unlock an unlocked mutex (fatal); mutexes are **not reentrant** (locking twice in the same goroutine deadlocks); keep critical sections small; don't hold locks across blocking calls (I/O, channel ops) unless you must.

### 9.3 `sync.RWMutex`: many readers or one writer

```go
var mu sync.RWMutex
mu.RLock();  v := data[k];  mu.RUnlock()   // many allowed concurrently
mu.Lock();   data[k] = v;   mu.Unlock()    // exclusive
```

Pending writer blocks new readers (to prevent writer starvation). Don't upgrade `RLock` to `Lock` (deadlock). Only worth it when reads vastly outnumber writes and critical sections are non-trivial.

### 9.4 `sync/atomic`: lock-free primitives

```go
var counter atomic.Int64
counter.Add(1)
v := counter.Load()

var cfg atomic.Pointer[Config]       // atomically swap immutable config
cfg.Store(newCfg)
cur := cfg.Load()
```

Great for counters, flags, and copy-on-write snapshots. Not composable: two atomics do not form a transaction.

### 9.5 `sync.Once` (and `OnceFunc/OnceValue`)

```go
var once sync.Once
once.Do(initExpensive)       // runs exactly once, others wait for completion

getCfg := sync.OnceValue(loadConfig)   // Go 1.21+: lazy cached value
cfg := getCfg()
```

### 9.6 `sync.Cond`: wait/notify on a condition

```go
mu := sync.Mutex{}
cond := sync.NewCond(&mu)
ready := false

// waiter
mu.Lock()
for !ready { cond.Wait() }   // atomically unlocks, parks; relocks on wake
mu.Unlock()

// signaler
mu.Lock(); ready = true; mu.Unlock()
cond.Broadcast()             // or Signal() to wake one
```

Always wait in a `for` loop (spurious/changed conditions). Often channels are simpler.

### 9.7 `sync.Pool`: reuse temporary objects

```go
var bufPool = sync.Pool{New: func() any { return new(bytes.Buffer) }}

b := bufPool.Get().(*bytes.Buffer)
b.Reset()
// use b
bufPool.Put(b)
```

A cache for GC relief, **not** a pool with guarantees (items can disappear at GC). Per-P sharded internally for low contention.

### 9.8 `sync.Map`

Concurrent map optimized for (a) write-once-read-many keys, or (b) goroutines touching disjoint key sets. Otherwise a regular `map` + `RWMutex` (or sharded map) is usually clearer and faster.

### 9.9 Choosing a tool

| Need | Use |
|---|---|
| Wait for goroutines to finish | `WaitGroup` / `errgroup` |
| Protect shared struct | `Mutex` |
| Mostly-read shared data | `RWMutex` or `atomic.Pointer` snapshot |
| Counter/flag | `atomic` |
| Transfer ownership of data / pipeline | channel |
| Signal event (one-shot, broadcast) | `close(chan struct{})` |
| Lazy init | `sync.Once` / `OnceValue` |
| Cancel work | `context.Context` |
| Limit concurrency | buffered channel semaphore / `errgroup.SetLimit` / `semaphore.Weighted` |

---

## 10. Channels

> "Do not communicate by sharing memory; instead, share memory by communicating." (Go proverb)

A **channel** is a typed, thread-safe queue that also provides **synchronization** (it creates happens-before edges).

### 10.1 Creating and using

```go
ch := make(chan int)        // unbuffered
bch := make(chan int, 10)   // buffered, capacity 10

ch <- 42                    // send
v := <-ch                   // receive
v, ok := <-ch               // ok == false if channel closed AND drained
close(ch)                   // only the SENDER side should close
for v := range ch { ... }   // receive until closed
len(bch); cap(bch)          // buffered count / capacity
```

### 10.2 Unbuffered vs buffered

```text
UNBUFFERED (cap 0): RENDEZVOUS. Sender and receiver must both be ready.

   G1: ch <- v  ----------+
                          |  handoff (direct copy sender -> receiver)
   G2: v := <-ch ---------+
   Whichever arrives first PARKS until the other shows up.

BUFFERED (cap N): sender blocks only when FULL; receiver blocks only when EMPTY.

   G1 --> [ v1 | v2 | v3 |   |   ] --> G2         (cap 5, len 3)
           ^ recvx            ^ sendx   (circular buffer)
```

Rules of thumb: default to **unbuffered** (strongest synchronization, simplest reasoning). Use buffered when you *know* why: decoupling bursts, semaphores, or avoiding goroutine leaks (`make(chan T, 1)` for single-result handoff).

### 10.3 Channel behavior table (memorize this)

| Operation | nil channel | closed channel | open channel |
|---|---|---|---|
| send `ch <- v` | blocks forever | **panic** | send or block |
| receive `<-ch` | blocks forever | returns zero value immediately (`ok=false`) after draining | receive or block |
| `close(ch)` | **panic** | **panic** | closes |
| `len/cap` | 0 | ok | ok |

Nil channels are useful in `select`: setting a channel variable to `nil` **disables** that case.

### 10.4 Directional channel types

```go
func producer(out chan<- int)  { out <- 1 }      // send-only
func consumer(in  <-chan int)  { v := <-in }     // receive-only
```

A `chan int` converts implicitly to either direction; never the reverse. Use directions in function signatures to encode ownership.

### 10.5 Internals: `hchan`

```text
 hchan (what make(chan T, N) allocates)
+------------------------------------------------------------+
| qcount   : number of items currently in buffer             |
| dataqsiz : buffer capacity (N)                             |
| buf      : pointer to circular buffer array [N]T           |
| elemsize : size of T                                       |
| closed   : 0/1                                             |
| sendx    : next send index          \  circular buffer     |
| recvx    : next receive index       /  cursors             |
| recvq    : wait queue of parked RECEIVERS (sudog list)     |
| sendq    : wait queue of parked SENDERS   (sudog list)     |
| lock     : mutex protecting all of the above               |
+------------------------------------------------------------+

 sudog = "a goroutine waiting on a channel", with pointer to the
         element slot to copy from/to, and which select case (if any).
```

Send algorithm (simplified):

```text
 ch <- v
   lock(ch)
   if ch.closed             -> panic
   if receiver in recvq     -> copy v DIRECTLY into receiver's stack, ready(receiver), unlock
   else if qcount < dataqsiz-> copy v into buf[sendx], advance, unlock
   else                     -> enqueue self in sendq, park (unlock + gopark)
```

Direct handoff to a waiting receiver (bypassing the buffer) is an important optimization.

### 10.6 Ping-pong on an unbuffered channel (sequence diagram)

```text
   G1 (sender)                  ch (unbuffered)                 G2 (receiver)
       |                              |                              |
       |                              |   <-ch (nobody sending)      |
       |                              |<-----------------------------|
       |                              |   G2 parked in recvq         |
       |   ch <- 7                    |                              |
       |----------------------------->|                              |
       |   sees G2 in recvq,          |                              |
       |   copies 7 straight to G2    |                              |
       |   marks G2 runnable          |----------------------------->|
       |   (G2 goes to runnext)       |           v == 7, continues  |
       v   continues                  |                              v
```

### 10.7 Closing channels: who and when

- **Only the sender closes** (never the receiver), and only when no more sends will happen.
- Closing is a **broadcast** to all receivers: all wake with zero value `ok=false`. This makes `close(done)` ideal for signaling.
- With multiple senders, coordinate: a `WaitGroup` + a closer goroutine.

```go
var wg sync.WaitGroup
out := make(chan int)
for i := 0; i < 5; i++ {
    wg.Add(1)
    go func() { defer wg.Done(); out <- work(i) }()
}
go func() { wg.Wait(); close(out) }()      // closer goroutine
for v := range out { use(v) }
```

### 10.8 Channels vs Mutex: which?

| Prefer channels when | Prefer mutex when |
|---|---|
| Passing ownership of data between stages | Protecting internal state of a struct (cache, counters) |
| Distributing work / collecting results | Very hot, tiny critical sections (channel ops are slower: ~50-100 ns vs ~10-20 ns uncontended) |
| Signaling events / cancellation | Simple "get/set shared value" |
| Coordinating lifecycles | Many readers (RWMutex) |

> A channel is **not** a faster mutex. Pick by *intent*: transfer/coordination = channel; guarding state = mutex.

---

## 11. The `select` statement

`select` waits on **multiple channel operations** simultaneously.

```go
select {
case v := <-ch1:
    handle(v)
case ch2 <- x:
    // sent
case <-time.After(2 * time.Second):
    // timeout
case <-ctx.Done():
    return ctx.Err()
default:
    // runs immediately if no case is ready (non-blocking)
}
```

Semantics:

1. If **multiple** cases are ready, one is chosen **pseudo-randomly** (fairness, no starvation by order).
2. With no `default`, it blocks until some case is ready.
3. With `default`, it never blocks.
4. `select {}` with no cases blocks forever.
5. A `nil` channel case is never ready (useful to disable cases dynamically).

Internals: the runtime **locks all involved channels in a consistent (address) order**, checks readiness, otherwise enqueues a `sudog` on every channel, parks, and on wake-up dequeues from the others.

### Common select idioms

```go
// 1. Timeout
select {
case res := <-resultCh:
case <-time.After(time.Second): // careful in loops: allocate timer each time, prefer time.NewTimer
}

// 2. Non-blocking send/receive
select {
case ch <- v:
default: // drop if full
}

// 3. Disable a case with nil
var in, out = inCh, outCh
for in != nil || out != nil {
    select {
    case v, ok := <-in:
        if !ok { in = nil; continue }
        ...
    }
}

// 4. Periodic work + cancellation
t := time.NewTicker(time.Second)
defer t.Stop()
for {
    select {
    case <-t.C:     doTick()
    case <-ctx.Done(): return
    }
}
```

---

## 12. Go memory model and happens-before

When goroutine B can **observe** what goroutine A wrote depends on **happens-before** edges. Without one, you have a data race.

Synchronization events that create happens-before:

| Action in A | Guarantees to B |
|---|---|
| `go f()` statement | everything before `go` in A happens-before `f` starts |
| `close(ch)` | happens-before a receive that returns because it's closed |
| `ch <- v` (send) | happens-before the matching receive completes |
| receive from **unbuffered** chan | happens-before the matching send completes |
| k-th receive on chan with cap C | happens-before the (k+C)-th send completes (semaphore pattern) |
| `mu.Unlock()` | happens-before the next `mu.Lock()` returns |
| `once.Do(f)` returns | f's completion happens-before any `Do` returns |
| `wg.Done()` | happens-before `wg.Wait()` returns |
| atomic op (Go memory model treats as sequentially consistent) | observed atomically ordered |

**Goroutine exit has NO happens-before guarantee.** The fact a goroutine finished tells you nothing unless you synchronized.

```go
var done bool
go func() { work(); done = true }()
for !done {}           // DATA RACE: no HB edge; compiler may hoist the load -> infinite loop
```

Always use `-race` in tests/CI.

---

## 13. `context`

`context.Context` carries **cancellation**, **deadlines**, and request-scoped values across API boundaries and goroutines.

### 13.1 Creating contexts

```go
ctx := context.Background()                              // root
ctx, cancel := context.WithCancel(parent)                // manual cancel
ctx, cancel := context.WithTimeout(parent, 2*time.Second)
ctx, cancel := context.WithDeadline(parent, t)
ctx, cancel := context.WithCancelCause(parent)           // Go 1.20+: record why
ctx = context.WithValue(parent, key, val)                // request-scoped data only
defer cancel()          // ALWAYS call to release resources
```

### 13.2 Cancellation propagates down the tree

```text
                  Background
                      |
              WithCancel (server shutdown)
                      |
              WithTimeout 5s  (per request)
               /      |       \
              /       |        \
   WithCancel      WithCancel   WithCancel
   (DB query)      (cache)      (RPC call)
       |              |            |
   goroutine A    goroutine B   goroutine C

 cancel() or timeout at any node -> ctx.Done() closes for that node
 AND ALL DESCENDANTS (never ancestors/siblings).
```

### 13.3 Using it in goroutines

```go
func worker(ctx context.Context, jobs <-chan Job) error {
    for {
        select {
        case <-ctx.Done():
            return ctx.Err()            // context.Canceled or DeadlineExceeded
        case j, ok := <-jobs:
            if !ok { return nil }
            if err := process(ctx, j); err != nil { return err }
        }
    }
}
```

Rules: pass `ctx` as the **first parameter**, named `ctx`; don't store it in structs (usually); don't pass `nil`; cancellation is **cooperative**: your code (and libraries) must check `ctx.Done()` or pass `ctx` into blocking calls (`http.NewRequestWithContext`, `db.QueryContext`, ...).

---

## 14. Concurrency patterns

### 14.1 Worker pool (bounded concurrency)

```go
func pool(ctx context.Context, jobs []Job, n int) []Result {
    jobCh := make(chan Job)
    resCh := make(chan Result)

    var wg sync.WaitGroup
    for i := 0; i < n; i++ {
        wg.Add(1)
        go func() {
            defer wg.Done()
            for j := range jobCh {
                resCh <- handle(j)
            }
        }()
    }

    go func() {                      // producer
        defer close(jobCh)
        for _, j := range jobs {
            select {
            case jobCh <- j:
            case <-ctx.Done(): return
            }
        }
    }()

    go func() { wg.Wait(); close(resCh) }()   // closer

    var out []Result
    for r := range resCh { out = append(out, r) }
    return out
}
```

Architecture:

```text
                      +----------+
                      | producer |
                      +----+-----+
                           |  jobCh (unbuffered)
        +------------------+------------------+
        |                  |                  |
        v                  v                  v
  +-----------+      +-----------+      +-----------+
  | worker 1  |      | worker 2  |      | worker N  |
  +-----+-----+      +-----+-----+      +-----+-----+
        |                  |                  |
        +------------------+------------------+
                           |  resCh
                           v
                     +-----------+
                     | collector |
                     +-----------+
```

Sizing: **CPU-bound** -> `N ~ GOMAXPROCS`. **I/O-bound** -> N set by downstream capacity (DB connections, rate limits, file descriptors), not CPU count.

### 14.2 Fan-out / Fan-in

```text
                  +--> [worker A] --+
   source -->  ---+--> [worker B] --+--> merge --> sink
   (1 chan)       +--> [worker C] --+   (fan-in)
              (fan-out: many receivers on ONE channel)
```

```go
func merge[T any](cs ...<-chan T) <-chan T {
    out := make(chan T)
    var wg sync.WaitGroup
    wg.Add(len(cs))
    for _, c := range cs {
        go func() {
            defer wg.Done()
            for v := range c { out <- v }
        }()
    }
    go func() { wg.Wait(); close(out) }()
    return out
}
```

### 14.3 Pipeline (stages connected by channels)

```text
  generate --ch--> square --ch--> filter --ch--> print
    (G1)           (G2)           (G3)           (G4)

  each stage:  receives from upstream, processes, sends downstream,
               closes its OUT channel when its IN is drained.
```

```go
func gen(ctx context.Context, nums ...int) <-chan int {
    out := make(chan int)
    go func() {
        defer close(out)
        for _, n := range nums {
            select {
            case out <- n:
            case <-ctx.Done(): return
            }
        }
    }()
    return out
}

func sq(ctx context.Context, in <-chan int) <-chan int {
    out := make(chan int)
    go func() {
        defer close(out)
        for n := range in {
            select {
            case out <- n * n:
            case <-ctx.Done(): return
            }
        }
    }()
    return out
}
```

Benefits: natural **backpressure** (slow stage slows the upstream), each stage is independently testable, memory bounded by channel capacity.

### 14.4 `errgroup`: WaitGroup + errors + cancellation

```go
import "golang.org/x/sync/errgroup"

g, ctx := errgroup.WithContext(parent)
g.SetLimit(8)                          // at most 8 goroutines at once

for _, u := range urls {
    g.Go(func() error {
        return fetch(ctx, u)           // first error cancels ctx for all
    })
}
if err := g.Wait(); err != nil {       // waits for all, returns FIRST error
    return err
}
```

This is the **go-to** pattern for "run these things concurrently, fail fast."

### 14.5 Semaphore: limit concurrency

```go
sem := make(chan struct{}, 10)         // capacity = max concurrent
for _, t := range tasks {
    sem <- struct{}{}                  // acquire (blocks when 10 in flight)
    go func() {
        defer func() { <-sem }()       // release
        run(t)
    }()
}
```

Or `golang.org/x/sync/semaphore` for weighted semaphores with context support.

### 14.6 Generator / iterator

```go
func fib(ctx context.Context) <-chan int {
    ch := make(chan int)
    go func() {
        defer close(ch)
        a, b := 0, 1
        for {
            select {
            case ch <- a: a, b = b, a+b
            case <-ctx.Done(): return
            }
        }
    }()
    return ch
}
```

(Since Go 1.23, `iter.Seq` range-over-func is often a better, goroutine-free iterator for simple cases.)

### 14.7 Or-done, tee, bridge

- **or-done**: wrap `for v := range ch` with cancellation so reads abort on `ctx.Done()`.
- **tee**: duplicate one stream into two channels.
- **bridge**: flatten `<-chan <-chan T` into `<-chan T`.

### 14.8 Rate limiting / throttling

```go
limiter := time.NewTicker(100 * time.Millisecond)   // 10 req/s
defer limiter.Stop()
for _, req := range reqs {
    <-limiter.C
    go do(req)
}
// or golang.org/x/time/rate (token bucket): limiter.Wait(ctx)
```

### 14.9 Pub/Sub (broadcast hub)

```text
                         +-------------------+
 publisher(s) --msg-->   |    HUB goroutine  |
                         |  owns subscribers |
                         |  map (no locks!)  |
                         +----+----+----+----+
                              |    |    |
                              v    v    v
                            sub1 sub2 sub3    (each has own buffered chan)
```

The hub goroutine **owns** the map; others interact via channels (`register`, `unregister`, `broadcast`). This is the "**monitor goroutine**" / actor-like pattern: confine mutable state to one goroutine instead of using locks.

### 14.10 Singleflight (dedupe concurrent identical work)

```go
import "golang.org/x/sync/singleflight"
var g singleflight.Group
v, err, shared := g.Do(key, func() (any, error) { return loadFromDB(key) })
```

100 goroutines asking for the same key at once -> **one** DB call, 100 results. Great for cache-stampede protection.

### 14.11 Graceful shutdown

```go
func main() {
    ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
    defer stop()

    srv := &http.Server{Addr: ":8080", Handler: mux}
    go func() {
        if err := srv.ListenAndServe(); err != http.ErrServerClosed { log.Fatal(err) }
    }()

    <-ctx.Done()                                     // wait for signal
    shutdownCtx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
    defer cancel()
    _ = srv.Shutdown(shutdownCtx)                    // stop accepting, drain in-flight
    // then wait for your own background goroutines (WaitGroup) to finish
}
```

### 14.12 Confinement (the safest concurrency)

Make data **owned by exactly one goroutine** at a time. Pass ownership via channels. No sharing = no locks = no races.

---

## 15. Where goroutines are used in the real world

### 15.1 Domains

| Domain | How goroutines are used |
|---|---|
| **HTTP / API servers** | `net/http` runs **each connection (HTTP/1.1) / each stream (HTTP/2)** in its own goroutine. Your handler is already running in a goroutine. |
| **gRPC / microservices** | One goroutine per RPC; streaming RPCs use separate send/recv goroutines. |
| **Aggregators / BFF / API gateways** | Fan-out to many backends in parallel, merge, enforce deadlines (`errgroup` + `context`). |
| **Databases & drivers** | Connection pools, background cleaners, replication, compaction (CockroachDB, etcd, Vitess, TiDB). |
| **Container & cloud infra** | Docker, Kubernetes controllers/informers/watchers, containerd, Terraform, Consul, Vault, Prometheus. |
| **Web crawlers / scrapers** | Thousands of concurrent fetchers with rate limits and politeness. |
| **Message brokers / streaming** | NATS, Kafka consumers (one goroutine per partition), event processors. |
| **Real-time / WebSocket / chat** | Reader + writer goroutine per connection, hub for fan-out. |
| **Proxies / load balancers / CDNs** | Traefik, Caddy, Envoy-like Go proxies: goroutine pair per connection piping bytes. |
| **ETL / data pipelines** | Staged pipelines: extract -> transform -> load with bounded channels. |
| **Background jobs** | Cron-like tickers, queue consumers, cache refreshers, metric flushers, health checks. |
| **CLI tools** | Parallel file processing, concurrent downloads, `go build` itself parallelizes compiles. |
| **Game servers / bots** | Per-player/room goroutines (actor-style), tick loops. |
| **Observability agents** | Collectors scraping many targets in parallel (Prometheus, OpenTelemetry collectors). |

### 15.2 Typical use-case categories

1. **Request-per-goroutine servers** (one goroutine per unit of external work).
2. **Parallel I/O** (call N things, wait for all or first).
3. **CPU parallelism** (split data, process chunks on all cores, merge).
4. **Background tasks** (tickers, watchers, flushers, janitors).
5. **Pipelines & streaming** (staged processing with backpressure).
6. **Actor / monitor goroutines** (single owner of state, others message it).
7. **Timeouts, retries, hedging** (race multiple attempts, cancel losers).
8. **Event-driven connection handling** (long-lived sockets).

### 15.3 When NOT to use goroutines

- **Trivial work**: spawning a goroutine for `a + b` costs more than the work.
- **Strictly sequential logic** with data dependencies: concurrency adds complexity only.
- **Tiny loops** where scheduling overhead dominates; batch the work instead (chunks).
- **When ordering matters and you'd re-serialize anyway.**
- **Unbounded fan-out** over untrusted input (a million goroutines each opening a DB connection = outage). Always bound.

---

## 16. Real-world architectures

### 16.1 HTTP server: where goroutines live

```text
                              Internet clients
                       (c1)     (c2)     (c3)  ...  (cN)
                         \        |        /          /
                          v       v       v          v
+----------------------------------------------------------------------+
|                         YOUR GO PROCESS                              |
|                                                                      |
|   +-----------------------+                                          |
|   | Accept loop goroutine |  srv.Serve(listener)                     |
|   |  for { l.Accept() }   |                                          |
|   +-----------+-----------+                                          |
|               | per accepted connection:  go c.serve(ctx)            |
|               v                                                      |
|   +-------------+  +-------------+  +-------------+                  |
|   | conn-G (c1) |  | conn-G (c2) |  | conn-G (c3) |   ...            |
|   | read req    |  | read req    |  | read req    |                  |
|   | handler()   |  | handler()   |  | handler()   |                  |
|   | write resp  |  | write resp  |  | write resp  |                  |
|   +------+------+  +------+------+  +------+------+                  |
|          |                |                |                         |
|          |   handlers often spawn child goroutines:                  |
|          v                v                v                         |
|   +----------------------------------------------------+             |
|   | errgroup: db.Query() | cache.Get() | rpc.Call() ... |            |
|   +--------+---------------+--------------+------------+             |
|            |               |              |                          |
|            v               v              v                          |
|     [DB conn pool]   [Redis pool]   [HTTP client pool]               |
|                                                                      |
|   Background goroutines: metrics flusher, config watcher, ticker     |
+----------------------------------------------------------------------+
            all multiplexed by the G-M-P scheduler onto few OS threads
```

Details worth knowing:
- HTTP/1.1: one goroutine per connection, requests on that connection processed sequentially.
- HTTP/2: one goroutine per stream plus connection-level reader/writer goroutines.
- Request `ctx` is cancelled when the client disconnects or the handler returns, so propagate it.
- If a handler starts a goroutine that outlives the request, **do not** use `r.Context()` (it will be cancelled); use a detached context or a job queue.

### 16.2 API aggregator (fan-out with deadline)

```text
 Client request  GET /dashboard/42
        |
        v
 +----------------------------------------------+
 | Handler goroutine                            |
 | ctx, cancel = WithTimeout(r.Context(), 800ms)|
 | g, ctx = errgroup.WithContext(ctx)           |
 +------+-----------+-----------+---------------+
        |           |           |
   g.Go |      g.Go |      g.Go |
        v           v           v
 +-----------+ +-----------+ +-----------+
 | G: user   | | G: orders | | G: recos  |
 | service   | | service   | | service   |
 | (50 ms)   | | (120 ms)  | | (700 ms)  |
 +-----+-----+ +-----+-----+ +-----+-----+
       |             |             |
       +-------------+-------------+
                     |
               g.Wait()  (total latency = MAX of the three, not the SUM)
                     |
                     v
          assemble JSON -> respond

   On any error or timeout: ctx cancelled -> in-flight calls abort.
```

Sequential: 50 + 120 + 700 = 870 ms. Concurrent: ~700 ms. With a 800 ms deadline you can also degrade gracefully (return partial results if the "recos" call times out).

### 16.3 Web crawler with bounded concurrency and dedupe

```text
                +------------------+
  seed URLs --> |  frontier queue  |<-----------------------------+
                |  (channel/slice) |                              |
                +---------+--------+                              |
                          |                                       |
                          v                                       |
              +-----------------------+                           |
              | dispatcher goroutine  |  owns `visited` set       |
              | (no locks needed!)    |  (confinement)            |
              +-----+-----+-----+-----+                           |
                    |     |     |      jobs chan                  |
                    v     v     v                                 |
              +-----+ +-----+ +-----+                             |
              | W1  | | W2  | | WN  |  N fetch workers            |
              +--+--+ +--+--+ +--+--+  (rate limiter per host)    |
                 |       |       |                                |
                 +-------+-------+                                |
                         | results chan {url, links, body}        |
                         v                                        |
              +-----------------------+   new links -------------+
              | dispatcher / parser   |
              +-----------+-----------+
                          |
                          v
                     storage writer goroutine
```

### 16.4 WebSocket chat hub (reader/writer pair per client)

```text
        client A              client B              client C
           |                     |                     |
   +-------+-------+     +-------+-------+     +-------+-------+
   | readPump  (G) |     | readPump  (G) |     | readPump  (G) |
   | writePump (G) |     | writePump (G) |     | writePump (G) |
   +---+-------^---+     +---+-------^---+     +---+-------^---+
       |       |             |       |             |       |
       | msgs  | send chan   | msgs  | send chan   | msgs  | send chan
       v       | (buffered)  v       | (buffered)  v       | (buffered)
   +---------------------------------------------------------------+
   |                         HUB goroutine                         |
   |   clients map[*Client]bool   (owned ONLY by this goroutine)   |
   |   select {                                                    |
   |     case c := <-register:   add                               |
   |     case c := <-unregister: delete, close(c.send)             |
   |     case m := <-broadcast:  for each c: select{ c.send<-m;    |
   |                                       default: drop slow c }  |
   |   }                                                           |
   +---------------------------------------------------------------+
```

Why two goroutines per connection: reading and writing a socket are independent blocking operations; one goroutine can't wait on both without them being separate. The **hub** pattern avoids a mutex around the client set.

### 16.5 Streaming ETL pipeline with batching

```text
 +----------+  rows   +-----------+  parsed  +-----------+  batches  +---------+
 | Reader   |-------->| Transform |--------->|  Batcher  |---------->| Writers |
 | (1 G)    | chan    | (N Gs)    |   chan   |  (1 G)    |   chan    | (M Gs)  |
 | file/S3  | cap 1000| CPU-bound |  cap 1000| size/time |  cap 10   | DB bulk |
 +----------+         +-----------+          +-----------+           +---------+
      ^                                                                   |
      |        backpressure: if Writers are slow, the batch chan fills,   |
      +--------- Batcher blocks, Transform blocks, Reader blocks. --------+
                 Memory stays bounded. No unbounded queue.
```

### 16.6 Background job queue consumer (Kafka-style)

```text
 Kafka topic (partitions P0..P5)
      |         |         |
      v         v         v
 +---------+---------+---------+
 | consumer goroutine per partition (preserves ordering within partition)
 +----+----+----+----+----+----+
      |         |         |
      v         v         v
   [bounded worker pool per partition or shared]
      |         |         |
      +---------+---------+
                |
                v
         commit offsets goroutine (periodic, after processing)
```

### 16.7 Kubernetes-style controller (informers + workqueue)

```text
   API Server
       | watch (long-lived HTTP stream)
       v
  +-----------------+     +---------------------+
  | Reflector (G)   |---->| Local cache / store |
  | list+watch      |     +---------+-----------+
  +-----------------+               | event handlers
                                    v
                          +--------------------+
                          |  Rate-limited      |
                          |  WorkQueue (dedupe)|
                          +----+----+----+-----+
                               |    |    |
                               v    v    v
                         worker worker worker   (N goroutines)
                         reconcile(key) -> API calls -> requeue on error
```

---

## 17. Common bugs

### 17.1 Data race

```go
var m = map[string]int{}
for i := 0; i < 10; i++ {
    go func() { m["k"]++ }()      // RACE; concurrent map writes can fatally crash
}
```

Go detects concurrent map misuse and throws `fatal error: concurrent map writes` (non-recoverable). Fix with `sync.Mutex`, `sync.Map`, atomics (for ints), or confinement.

### 17.2 Deadlock

```go
ch := make(chan int)
ch <- 1            // blocks forever: no receiver. 
fmt.Println(<-ch)  // never reached
// runtime: "fatal error: all goroutines are asleep - deadlock!"
```

Typical causes:
- Unbuffered channel send with no receiver (or vice versa).
- Lock ordering cycles: G1 locks A then B, G2 locks B then A.
- `wg.Wait()` while a goroutine forgot `Done()`.
- Locking the same non-reentrant mutex twice.

The runtime's deadlock detector only fires when **all** goroutines are blocked. In a real server with timers/network goroutines alive, deadlocks are **silent**: requests just hang.

```text
 Lock-order deadlock:

   G1: Lock(A) ----> wants B  ----+
                                  |   each holds what the other needs
   G2: Lock(B) ----> wants A  ----+

 Fix: ALWAYS acquire locks in one global order (A then B), or use a single lock.
```

### 17.3 Goroutine leak (the most common production issue)

A goroutine that **never exits** holds its stack, referenced memory, and connections forever.

```go
func leaky() int {
    ch := make(chan int)             // unbuffered
    go func() { ch <- slow() }()     // blocks forever if nobody receives
    select {
    case v := <-ch: return v
    case <-time.After(time.Second): return -1   // returns; sender goroutine LEAKS
    }
}
// Fix: ch := make(chan int, 1)   // sender can always complete
```

Other leak sources:
- Receiver waiting on a channel that is never closed.
- Forgotten `cancel()`, tickers not stopped (`ticker.Stop()`).
- Goroutines blocked on a send to a consumer that exited.
- HTTP response bodies not closed; goroutines blocked on I/O without deadlines.
- `for range ch` where the producer never calls `close`.

```text
 Healthy:   goroutines ____________________  (flat)
 Leaking:   goroutines          /
                           /   <- grows steadily with traffic until OOM
                      /
 Monitor runtime.NumGoroutine() / metrics `go_goroutines`.
```

### 17.4 Panic from unrecovered goroutine

Crashes the whole process. Libraries that spawn goroutines on your behalf (request handlers are recovered by `net/http`, but **your own `go func()` inside handlers is not**). Wrap long-lived/background goroutines with a recovery helper.

### 17.5 Closing a channel twice / sending on closed

Both `panic`. Design a single owner for close. Pattern: only the producer closes; consumers never close.

### 17.6 Misusing `WaitGroup`

```go
go func() { wg.Add(1); defer wg.Done(); ... }()   // WRONG: Add may run after Wait()
wg.Add(1); go func() { defer wg.Done(); ... }()   // RIGHT
```

### 17.7 Capturing loop variables (pre-1.22) and shared pointers

See section 8.3. Also: appending to a shared slice from many goroutines is a race; write to `results[i]` (distinct indices) or use a channel.

### 17.8 Livelock and starvation

- **Livelock**: goroutines keep reacting to each other without progress (e.g., retry loops with identical backoff).
- **Starvation**: a goroutine never gets the resource (writer starved by readers; unfair locking). Go mitigates but doesn't eliminate; use jittered backoff.

### 17.9 Holding locks across blocking calls

```go
mu.Lock()
resp, _ := http.Get(url)   // lock held for entire network latency
mu.Unlock()
// Copy data out, unlock, then do I/O.
```

### 17.10 `time.After` in loops / unstopped timers

Before Go 1.23, `time.After` timers were not GC'd until firing; Go 1.23 improved this, but `time.NewTimer` + `Stop/Reset` remains best practice in hot loops.

### 17.11 Unbounded goroutine creation

```go
for _, item := range hugeSlice { go process(item) }   // 10M goroutines => memory, DB overload
```

Always apply a limit (worker pool, `errgroup.SetLimit`, semaphore).

### 17.12 Copying sync types

`sync.Mutex`, `WaitGroup`, `Once`, `Cond` must not be copied after first use. `go vet` (copylocks) catches this.

---

## 18. Debugging and observability

### 18.1 Race detector

```bash
go test -race ./...
go run -race main.go
go build -race
```

Instruments memory accesses (about 5-10x slower, ~5-10x memory). Finds races **that actually execute**. Run it in CI.

### 18.2 Goroutine dumps

- Send `SIGQUIT` (Ctrl+\\) to a Go program: dumps all goroutine stacks and exits.
- `GOTRACEBACK=all` for fuller output on crash.
- Programmatic: `pprof.Lookup("goroutine").WriteTo(w, 2)`.
- HTTP: import `_ "net/http/pprof"` then:

```bash
curl http://localhost:6060/debug/pprof/goroutine?debug=2     # full stacks
go tool pprof http://localhost:6060/debug/pprof/goroutine    # aggregated view
```

Dump entry anatomy:

```text
goroutine 42 [chan receive, 5 minutes]:          <- state + how long (LEAK hint!)
main.worker(0xc000012345)
        /app/worker.go:27 +0x45                  <- exact blocking line
created by main.main in goroutine 1
        /app/main.go:15 +0x7a                    <- who spawned it
```

Common states: `running`, `runnable`, `chan send`, `chan receive`, `select`, `semacquire` (mutex/WaitGroup), `IO wait`, `sleep`, `syscall`, `sync.Cond.Wait`.

### 18.3 Execution tracer

```bash
go test -trace=trace.out
go tool trace trace.out
```

Shows per-P timelines, goroutine run/wait/block intervals, GC, syscalls, network blocking, scheduler latency. Invaluable for "why is my parallelism low?" Also `runtime/trace` Regions/Tasks for custom annotations.

### 18.4 Profiles related to concurrency

| Profile | Shows |
|---|---|
| `goroutine` | Where goroutines are right now (leaks, pile-ups) |
| `block` (`runtime.SetBlockProfileRate`) | Where goroutines block on channels/mutexes |
| `mutex` (`runtime.SetMutexProfileFraction`) | Lock **contention** hot spots |
| `cpu` | Where CPU time is spent |
| `heap` / `allocs` | Memory, goroutine closures retaining memory |

### 18.5 Scheduler trace

```bash
GODEBUG=schedtrace=1000,scheddetail=1 ./app
# every 1000ms prints: gomaxprocs=8 idleprocs=2 threads=12 spinningthreads=1
#                      runqueue=3 [0 1 4 0 0 2 0 0]   (global, per-P local queues)
```

`runqueue` persistently high while `idleprocs=0` = CPU-saturated. High `runqueue` with idle procs = a scheduler/latency anomaly.

### 18.6 Static and library tools

- `go vet`: copylocks, loopclosure, lostcancel, etc.
- `staticcheck`, `golangci-lint`.
- `go.uber.org/goleak`: fails tests that leave goroutines behind.

```go
func TestMain(m *testing.M) { goleak.VerifyTestMain(m) }
```

---

## 19. Performance engineering

### 19.1 Cost model (rough, order-of-magnitude)

| Operation | Approx cost |
|---|---|
| Spawn goroutine | ~0.3-1 us (+ 2 KB+ stack, reused from free list) |
| Goroutine switch | ~0.1-0.2 us |
| Uncontended mutex Lock/Unlock | ~10-25 ns |
| Atomic add | ~5-20 ns (more when cache lines bounce) |
| Unbuffered channel send/recv pair | ~100-300 ns |
| Buffered channel op (uncontended) | ~50-100 ns |
| OS thread context switch | ~1-5 us |

Use these only for intuition; **always benchmark** (`go test -bench . -benchmem -cpu 1,2,4,8`).

### 19.2 CPU-bound vs I/O-bound

```text
 I/O-bound (waiting on network/disk):
   concurrency level >> cores is GOOD (hundreds/thousands of goroutines)
   limited by remote capacity, FD limits, rate limits

 CPU-bound (computation):
   concurrency level ~= GOMAXPROCS is optimal
   more goroutines than cores just adds scheduling overhead + cache thrash
```

### 19.3 Chunking: amortize goroutine and channel overhead

```go
// Bad: 1 goroutine per element for 1e7 tiny items
// Good: split into ~NumCPU chunks
n := runtime.GOMAXPROCS(0)
chunk := (len(data) + n - 1) / n
var wg sync.WaitGroup
for i := 0; i < n; i++ {
    lo, hi := i*chunk, min((i+1)*chunk, len(data))
    if lo >= hi { break }
    wg.Add(1)
    go func() { defer wg.Done(); process(data[lo:hi]) }()
}
wg.Wait()
```

### 19.4 Contention and false sharing

- **Lock contention**: many goroutines fighting for one mutex serialize. Fix: shard (e.g., 64 mutex-protected maps keyed by hash), reduce critical section, use per-goroutine accumulators and merge.
- **False sharing**: two independent variables on the same 64-byte cache line updated by different cores ping-pong the line.

```go
type padded struct {
    n int64
    _ [56]byte      // pad to 64 bytes so each counter has its own cache line
}
var counters [8]padded
```

### 19.5 GC interactions

- GC must **scan every goroutine's stack**: huge goroutine counts increase GC root-scanning work.
- Goroutines blocked while holding references keep that memory alive (leaks look like memory leaks).
- During GC, allocation-heavy goroutines may be forced into **mark assist** (slowing them).
- `sync.Pool` and buffer reuse reduce garbage.

### 19.6 Latency tips

- Buffer channels sensibly to absorb bursts, but remember: **buffers hide backpressure** (a huge buffer = unbounded latency).
- Avoid blocking in the hot path; set **timeouts** on every external call.
- Prefer `atomic.Pointer` snapshot for read-mostly config instead of RWMutex on hot paths.
- Use `GOMAXPROCS` appropriate to the container quota.
- Consider **hedged requests** (send a second request after p95 latency; cancel the loser) for tail latency.

---

## 20. Testing concurrent code

1. **Always run `go test -race`.**
2. **Don't use `time.Sleep` to synchronize tests**: flaky. Use channels, `WaitGroup`, `sync.Cond`, or polling helpers like `require.Eventually` with a bounded timeout.
3. **Make goroutine lifetimes testable**: expose `Close()/Stop()/Wait()` and `context` parameters.
4. **Leak checks**: `goleak.VerifyNone(t)`.
5. **Deterministic time**: `testing/synctest` (experimental in Go 1.24, stable in Go 1.25) runs code in a "bubble" with a fake clock: sleeps/timers advance instantly when all goroutines in the bubble are blocked.

```go
// Go 1.25+
func TestTimeout(t *testing.T) {
    synctest.Test(t, func(t *testing.T) {
        ctx, cancel := context.WithTimeout(context.Background(), time.Hour)
        defer cancel()
        go doThing(ctx)
        time.Sleep(time.Hour)     // virtual time: returns instantly
        synctest.Wait()           // wait until all bubble goroutines are blocked
        // assert...
    })
}
```

6. **Stress**: `go test -race -count=100 -cpu=1,2,4,8 ./...` to shake out ordering bugs.

---

## 21. Runtime knobs and special APIs

| API | Purpose |
|---|---|
| `runtime.Gosched()` | Yield the processor; put current G on global queue, let others run. Rarely needed now. |
| `runtime.Goexit()` | Terminate calling goroutine after running defers. Does not affect others. |
| `runtime.NumGoroutine()` | Count of existing goroutines (monitor for leaks). |
| `runtime.GOMAXPROCS(n)` | Set/get P count. |
| `runtime.LockOSThread()` | Pin goroutine to its current OS thread (needed for GUI libs, OpenGL, some syscalls with thread-local state, namespaces). |
| `runtime.UnlockOSThread()` | Undo pin. |
| `debug.SetMaxThreads(n)` | Limit OS threads (default 10,000). |
| `debug.SetMaxStack(n)` | Max goroutine stack size. |
| `runtime.Stack(buf, all)` | Capture stack(s) as text. |
| `runtime/pprof` labels | Tag goroutines (`pprof.Do(ctx, labels, f)`) to attribute CPU/goroutine profiles per tenant/endpoint. |

Environment variables: `GOMAXPROCS`, `GODEBUG=schedtrace=...`, `GOTRACEBACK`, `GOGC`, `GOMEMLIMIT`.

---

## 22. Mental-model cheat sheet

### 22.1 Ten laws of goroutines

1. A goroutine is a **cheap task**, not a thread; you may create many, but not **unboundedly**.
2. **Every goroutine needs an exit plan** (done channel, context, closed input).
3. **Whoever starts it, owns it** (waits for it, handles its errors).
4. Shared mutable state needs **synchronization** or **confinement**. No exceptions.
5. **Channels transfer ownership/coordinate**; **mutexes protect state**.
6. **Only senders close channels**; closing is a broadcast.
7. **Pass `context` everywhere** blocking work happens; honor `ctx.Done()`.
8. **Bound concurrency**: worker pools, semaphores, `errgroup.SetLimit`.
9. **Blocking is fine** (the runtime handles it); **busy waiting is not**.
10. **Test with `-race`**, monitor goroutine count, and profile before optimizing.

### 22.2 Quick decision tree

```text
 Need concurrency?
   |
   +-- Is the work independent and slow (I/O or heavy CPU)? -- no --> stay sequential
   |        | yes
   |        v
   +-- How many items?
   |       few & fixed ........................ plain `go` + WaitGroup / errgroup
   |       many / unbounded ................... worker pool or errgroup.SetLimit
   |
   +-- Need results back?
   |       one result ......................... buffered chan(1) or errgroup
   |       stream of results .................. channel + closer goroutine
   |
   +-- Need to share state?
   |       counter/flag ....................... atomic
   |       struct/map ......................... Mutex (or confine to 1 goroutine)
   |       read-mostly snapshot ............... atomic.Pointer / RWMutex
   |
   +-- Need cancellation/timeouts? ........... context
   |
   +-- Need to stop on first error? .......... errgroup.WithContext
   |
   +-- Long-lived connections? ............... goroutine(s) per conn (+ hub for fan-out)
```

### 22.3 Pre-flight checklist before every `go` statement

```text
 [ ] How does it terminate?                (return path on ctx.Done / closed chan)
 [ ] Who waits for it?                     (WaitGroup / errgroup / done chan)
 [ ] How are errors surfaced?              (error chan / errgroup)
 [ ] What if it panics?                    (recover + log, or let process crash intentionally)
 [ ] What shared data does it touch?       (sync / confinement)
 [ ] Is the number of such goroutines bounded?
 [ ] Does every blocking call have a timeout/ctx?
```

### 22.4 The whole system in one diagram

```text
 +-------------------------- Go process -----------------------------+
 |                                                                   |
 |  Goroutines (G):   g1  g2  g3  g4 ... g100000   (2KB+ stacks)     |
 |        |  park/ready via:                                         |
 |        |    channels / select / mutex / WaitGroup / timers / IO   |
 |        v                                                          |
 |  Scheduler:  Ps (= GOMAXPROCS), each with local run queue         |
 |               + global queue + work stealing + netpoller          |
 |        |                                                          |
 |        v                                                          |
 |  Threads (M):  run Go code only with a P;                         |
 |                extra Ms exist for blocked syscalls & sysmon       |
 |        |                                                          |
 +--------|----------------------------------------------------------+
          v
     OS kernel  ->  CPU cores, epoll/kqueue/IOCP, disk, network
```

---

## 23. Interview-style Q&A

**Q1. What's the difference between a goroutine and a thread?**
Goroutines are user-space tasks scheduled by the Go runtime onto a small pool of OS threads (M:N). They have tiny growable stacks (~2 KB), cheap creation/switching, and are blocked/parked without blocking threads for most operations (channels, locks, network I/O).

**Q2. Explain G, M, P.**
G = goroutine, M = OS thread, P = scheduling context holding a local run queue and caches. An M needs a P to run Go code. `GOMAXPROCS` = number of Ps.

**Q3. What happens when a goroutine makes a blocking syscall?**
The M blocks in the kernel. After a short time `sysmon` (or the syscall entry path) hands the P to another M so the P's queued goroutines keep running. When the syscall returns, the original M tries to reacquire a P, or parks the goroutine on the global queue.

**Q4. How does Go handle network I/O without blocking threads?**
Sockets are non-blocking and registered with the netpoller (epoll/kqueue/IOCP). A goroutine waiting on a socket is parked; when the fd is ready, the scheduler marks it runnable.

**Q5. What is work stealing?**
An idle P takes half of another P's local run queue (after checking global queue and netpoll), balancing load without a single global lock.

**Q6. Are goroutines preemptive?**
Yes since Go 1.14: `sysmon` signals threads running a goroutine > 10 ms, triggering asynchronous preemption at safe points. Earlier versions only preempted at function calls.

**Q7. Why do goroutine leaks happen and how do you find them?**
Goroutines blocked forever on channels/locks/I/O with no cancellation path. Detect with `runtime.NumGoroutine()` trends, `pprof` goroutine profile, `goleak` in tests, and stack dumps showing long wait durations.

**Q8. Buffered vs unbuffered channels?**
Unbuffered = synchronous rendezvous (sender and receiver meet). Buffered = queue with capacity; sender blocks only when full. Unbuffered gives the strongest synchronization; buffered decouples timing.

**Q9. What does `select` do when multiple cases are ready?**
Picks one uniformly at random (pseudo-random) to avoid starvation.

**Q10. How do you cancel a goroutine?**
You can't kill it; you signal it: `context` cancellation or closing a `done` channel; the goroutine must check and return.

**Q11. How do you limit concurrency?**
Worker pool, buffered-channel semaphore, `semaphore.Weighted`, or `errgroup.SetLimit(n)`.

**Q12. What happens if a goroutine panics?**
If not recovered **in that goroutine**, the entire program crashes.

**Q13. Does a mutex or channel give happens-before?**
Both: `Unlock` happens-before the next `Lock` returns; a send happens-before the corresponding receive completes (and for unbuffered channels the receive happens-before the send completes); `close` happens-before a receive observing closure.

**Q14. How many goroutines can I run?**
Millions are possible memory-wise (~2-8 KB each); practical limit is determined by memory, downstream resources (DB, FDs), and GC/scheduling overhead. Always bound work by capacity of dependencies.

**Q15. What's the loop variable issue?**
Pre-Go 1.22, `for` loop variables were shared across iterations, so closures captured one variable. Go 1.22+ (per `go.mod` version) makes variables per-iteration.

---

### Further reading (to deepen your model)

- The Go Memory Model: <https://go.dev/ref/mem>
- Go source: `runtime/proc.go` (scheduler), `runtime/chan.go` (channels), `runtime/select.go`, `runtime/stack.go`
- "Go scheduler: Ms, Ps & Gs" and Kavya Joshi's talk "Understanding Channels" (GopherCon 2017)
- Effective Go: Concurrency section
- *Concurrency in Go* by Katherine Cox-Buday
- Rob Pike: "Concurrency Is Not Parallelism"
- Go blog: "Go Concurrency Patterns: Pipelines and cancellation", "Go Concurrency Patterns: Context"

---

*End of guide.*
