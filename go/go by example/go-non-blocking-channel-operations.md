# Non-Blocking Channel Operations in Go: The Complete Guide

> **Goal of this guide:** build a correct *mental model* of non-blocking channel operations, so you can decide quickly and confidently when (and when *not*) to use them, and design systems that stay responsive under load.

---

## Table of Contents

1. [The One-Sentence Mental Model](#1-the-one-sentence-mental-model)
2. [Prerequisites: How Channels Block](#2-prerequisites-how-channels-block)
3. [What "Non-Blocking" Really Means](#3-what-non-blocking-really-means)
4. [The Core Syntax: `select` + `default`](#4-the-core-syntax-select--default)
5. [Semantics Table: Every State, Every Operation](#5-semantics-table-every-state-every-operation)
6. [Under the Hood: What the Runtime Does](#6-under-the-hood-what-the-runtime-does)
7. [The Pattern Catalog](#7-the-pattern-catalog)
8. [Real-World Use Cases (with Architecture Diagrams)](#8-real-world-use-cases-with-architecture-diagrams)
9. [Where the Standard Library and Ecosystem Use This](#9-where-the-standard-library-and-ecosystem-use-this)
10. [Pitfalls and Anti-Patterns](#10-pitfalls-and-anti-patterns)
11. [Non-Blocking vs. Timeouts vs. Context vs. Other Tools](#11-non-blocking-vs-timeouts-vs-context-vs-other-tools)
12. [Design Decisions: Drop, Block, Coalesce, or Reject?](#12-design-decisions-drop-block-coalesce-or-reject)
13. [Testing and Observability](#13-testing-and-observability)
14. [Decision Flowchart and Cheat Sheet](#14-decision-flowchart-and-cheat-sheet)
15. [Exercises](#15-exercises)
16. [Summary](#16-summary)

---

## 1. The One-Sentence Mental Model

> **A non-blocking channel operation asks the question "can I do this *right now*, without waiting?" and gets an immediate yes or no, instead of putting the goroutine to sleep.**

Everything else in this guide is a consequence of that sentence:

- *"Yes"* means the send/receive happened.
- *"No"* means nothing happened, and **you** decide what to do (drop, retry, reject, do other work).
- Non-blocking is fundamentally about **who bears the cost of congestion**. With blocking, the producer waits (backpressure). With non-blocking, the producer keeps moving, and something else gives: data is dropped, a request is rejected, or work is deferred.

```
        BLOCKING                              NON-BLOCKING
   "I'll wait until you're ready"       "Are you ready NOW? No? Moving on."

   producer ---> [ FULL ] x (sleeps)    producer ---> [ FULL ] x (continues)
        |                                    |
        +-- stalls, holds resources          +-- immediately runs fallback path
```

---

## 2. Prerequisites: How Channels Block

Before understanding "non-blocking", be precise about when ordinary channel operations *block*.

### 2.1 Unbuffered channel (`make(chan T)`)

A send and a receive must **meet** (rendezvous). Whoever arrives first parks and waits for the other.

```go
ch := make(chan int)

ch <- 1      // BLOCKS until some goroutine executes <-ch
<-ch         // BLOCKS until some goroutine executes ch <- v
```

### 2.2 Buffered channel (`make(chan T, N)`)

The channel has a FIFO queue of capacity `N`.

| Operation | Blocks when...            |
|-----------|---------------------------|
| Send      | the buffer is **full**    |
| Receive   | the buffer is **empty**   |

### 2.3 Nil channel

`var ch chan int` (nil): **send and receive block forever**. This is a feature in `select` (it disables a case), but a bug elsewhere.

### 2.4 Closed channel

| Operation | Result                                                                 |
|-----------|------------------------------------------------------------------------|
| Send      | **panic**: `send on closed channel`                                    |
| Receive   | Returns remaining buffered values, then zero value and `ok == false` forever, **never blocks** |
| Close     | Closing twice panics; closing nil panics                               |

### 2.5 Visual: blocking timeline

```
Time ------------------------------------------------------------------>

Goroutine A (sender)     :  ch <- 42 ...............[ PARKED ].......... resumes
                                      |                              ^
                                      | no receiver yet              |
                                      v                              |
Goroutine B (receiver)   :  (doing other work) ------> <-ch ---------+
                                                       rendezvous!

A was asleep (parked) the whole time. It could do NOTHING else.
That is what "blocking" costs you.
```

---

## 3. What "Non-Blocking" Really Means

Go has **no** `TrySend()` / `TryRecv()` built-in, and no `ch.TryPush()`. A non-blocking operation is built from one language feature:

> **A `select` statement with a `default` clause never blocks.** If no other case is ready, `default` runs immediately.

Because of that, you get a precise definition:

| Term | Meaning |
|------|---------|
| **Blocking op** | Goroutine may be parked (descheduled) until the channel is ready |
| **Non-blocking op** | Operation either completes immediately or is *skipped*; the goroutine is never parked on that channel |
| **Timed op** | Blocks, but only up to a deadline (`time.After`, `context`) (a *third* category, not non-blocking) |

### Important nuance: non-blocking does not mean lock-free, wait-free, or "instant"

The channel internally has a mutex. A non-blocking operation may briefly take that mutex (nanoseconds), but it **will not sleep waiting for a counterpart**. "Non-blocking" here means *no parking waiting for channel readiness*.

---

## 4. The Core Syntax: `select` + `default`

### 4.1 Non-blocking send

```go
select {
case ch <- value:
    // Sent. There was a receiver waiting, or buffer space.
default:
    // Would have blocked. Nothing was sent.
}
```

### 4.2 Non-blocking receive

```go
select {
case v := <-ch:
    // Got a value.
    fmt.Println("received", v)
default:
    // Nothing available right now.
}
```

### 4.3 Non-blocking receive with closed detection

```go
select {
case v, ok := <-ch:
    if !ok {
        // Channel closed AND drained.
        return
    }
    process(v)
default:
    // Open, but empty. No data yet.
}
```

A `default`-branch tells you *nothing* about whether the channel is closed. It only says "no value available right now" (and a closed, drained channel is **always ready**, so `default` would never fire for it).

### 4.4 Reusable generic helpers

```go
// TrySend attempts a non-blocking send. Returns true if the value was sent.
// WARNING: panics if ch is closed (same as a normal send).
func TrySend[T any](ch chan<- T, v T) bool {
    select {
    case ch <- v:
        return true
    default:
        return false
    }
}

type RecvStatus int

const (
    RecvEmpty  RecvStatus = iota // open, nothing available right now
    RecvOK                       // got a value
    RecvClosed                   // closed and drained
)

// TryRecv attempts a non-blocking receive.
func TryRecv[T any](ch <-chan T) (T, RecvStatus) {
    select {
    case v, ok := <-ch:
        if !ok {
            var zero T
            return zero, RecvClosed
        }
        return v, RecvOK
    default:
        var zero T
        return zero, RecvEmpty
    }
}
```

### 4.5 A complete runnable example

```go
package main

import "fmt"

func main() {
    msgs := make(chan string, 1) // buffered, capacity 1

    // 1) Non-blocking send into empty buffer: succeeds
    select {
    case msgs <- "hello":
        fmt.Println("sent: hello")
    default:
        fmt.Println("dropped: hello")
    }

    // 2) Non-blocking send into FULL buffer: falls to default
    select {
    case msgs <- "world":
        fmt.Println("sent: world")
    default:
        fmt.Println("dropped: world (buffer full)")
    }

    // 3) Non-blocking receive from non-empty buffer: succeeds
    select {
    case m := <-msgs:
        fmt.Println("received:", m)
    default:
        fmt.Println("nothing to receive")
    }

    // 4) Non-blocking receive from EMPTY buffer: falls to default
    select {
    case m := <-msgs:
        fmt.Println("received:", m)
    default:
        fmt.Println("nothing to receive")
    }
}
// Output:
// sent: hello
// dropped: world (buffer full)
// received: hello
// nothing to receive
```

---

## 5. Semantics Table: Every State, Every Operation

This is the table to internalize. Rows are channel states; columns are non-blocking operations.

| Channel state                  | Non-blocking **send** (`case ch <- v`) | Non-blocking **receive** (`case <-ch`)        |
|--------------------------------|----------------------------------------|-----------------------------------------------|
| **nil**                        | `default` (case never ready)           | `default` (case never ready)                  |
| **unbuffered**, no peer waiting| `default`                              | `default`                                     |
| **unbuffered**, receiver parked| **sends** (hands off directly)         | n/a                                           |
| **unbuffered**, sender parked  | n/a                                    | **receives** (takes from the parked sender)   |
| **buffered**, empty            | **sends** (into buffer)                | `default`                                     |
| **buffered**, partially full   | **sends**                              | **receives** (oldest value)                   |
| **buffered**, full             | `default`                              | **receives**                                  |
| **closed**, buffer has items   | **PANIC**                              | **receives** remaining items (`ok == true`)   |
| **closed**, drained            | **PANIC**                              | **receives zero value** (`ok == false`), never `default` |

### Two critical consequences

**1. Non-blocking ops on *unbuffered* channels are "rendezvous or nothing".**
A non-blocking send on an unbuffered channel succeeds *only if a receiver is already parked on the channel at that exact instant*. If the receiver is busy doing work and will call `<-ch` a microsecond later, you lose. This is why non-blocking sends are usually paired with **buffered** channels.

```
 Unbuffered try-send:

 Sender: select { case ch <- v: ... default: }
                      |
                      v
        Is a receiver PARKED on ch right now?
              /                  \
            YES                   NO
             |                     |
      hand off value          run default
      (success)               (dropped, even if the receiver
                               arrives 1 microsecond later)
```

**2. A non-blocking send to a closed channel panics, even with `default`.** `default` protects against *blocking*, not against *closed*.

---

## 6. Under the Hood: What the Runtime Does

Understanding the runtime tells you the *cost* of these operations and why they behave as they do. (Details follow Go's `runtime/chan.go` and `runtime/select.go`; internals may evolve, but the model is stable.)

### 6.1 The channel structure (`hchan`)

```
                        hchan (heap-allocated)
   +--------------------------------------------------------+
   | qcount    : number of items currently in buffer        |
   | dataqsiz  : buffer capacity (the N in make(chan T, N)) |
   | buf       : pointer to circular buffer ---------+      |
   | elemsize  : size of one element                 |      |
   | closed    : 0 or 1                              |      |
   | sendx     : next write index                    |      |
   | recvx     : next read index                     |      |
   | recvq     : queue of parked RECEIVERS (sudog)   |      |
   | sendq     : queue of parked SENDERS   (sudog)   |      |
   | lock      : mutex protecting all of the above   |      |
   +-------------------------------------------------|------+
                                                     v
                                  circular buffer (dataqsiz slots)
                                  +----+----+----+----+----+
                                  | v0 | v1 | v2 |    |    |
                                  +----+----+----+----+----+
                                    ^              ^
                                  recvx          sendx
                                  (read here)   (write here)
```

### 6.2 The compiler special-cases single-case `select` + `default`

A `select` with **exactly one case and a `default`** is compiled into direct calls, not the general `select` machinery:

```
 select {                               compiles to:
 case ch <- v:  ... default: ...  --->  if selectnbsend(ch, &v) { ... } else { ... }

 select {
 case v = <-ch: ... default: ...  --->  if selectnbrecv(&v, ch)  { ... } else { ... }
```

Both end up in `chansend(c, elem, block=false)` / `chanrecv(c, elem, block=false)`.

### 6.3 The fast path: a lock-free "would block?" check

Inside `chansend` with `block == false`, the runtime first does a **lock-free** check:

```
 chansend(c, ep, block=false):
 +---------------------------------------------------------------+
 | if !block && c.closed == 0 && channelIsFull(c)  {             |
 |       return false      // <-- no lock taken at all!          |
 | }                                                             |
 | lock(c)                                                       |
 | if c.closed != 0           -> unlock; panic("send on closed") |
 | if a receiver is in recvq  -> hand value directly, unlock, ok |
 | if qcount < dataqsiz       -> copy into buffer,   unlock, ok  |
 | if !block                  -> unlock, return false            |
 | ... (blocking path: enqueue sudog, gopark) ...                |
 +---------------------------------------------------------------+
```

This is why a non-blocking "channel is full" check is **very cheap**, which matters for load shedding under heavy contention: the rejection path costs almost nothing.

### 6.4 Multi-case `select` with `default` (`selectgo`)

When you have **two or more** cases plus `default`, the general `selectgo` algorithm runs:

```
 selectgo(cases[], hasDefault):

 Step 1: Build pollorder  -> RANDOM permutation of the cases
                             (this is the fairness mechanism)
 Step 2: Build lockorder  -> cases sorted by channel ADDRESS
                             (prevents deadlock between concurrent selects)
 Step 3: Lock ALL involved channels in lockorder
 Step 4: PASS 1 - scan cases in pollorder:
           for each case:
             is it ready? (value available / space / closed)
             yes -> perform it, unlock all, return that case
 Step 5: Nothing ready?
             hasDefault ? -> unlock all, return "default"      <== NON-BLOCKING EXIT
             else       -> PASS 2: enqueue a sudog on EVERY channel,
                           gopark (sleep) ... (this is the blocking path;
                           default skips it entirely)
```

```
   Flow of a select with default:

        +--------------------+
        |  lock all channels |
        +---------+----------+
                  |
                  v
        +--------------------+      yes     +--------------------------+
        | any case ready?    |------------->| execute ONE ready case   |
        | (scan in RANDOM    |              | (uniformly random among  |
        |  order)            |              |  ready ones)             |
        +---------+----------+              +--------------------------+
                  | no
                  v
        +--------------------+
        |  has default?      |--- yes --->  run default (never parks)
        +---------+----------+
                  | no
                  v
              park & wait (blocking select)
```

### 6.5 Key takeaways from the internals

| Fact | Implication for you |
|------|---------------------|
| Single-case + default is specially compiled | The simplest try-send/try-recv is the cheapest form |
| Full-channel send check is lock-free | Rejecting under overload is cheap |
| Multi-case select locks *all* channels | Selecting over many channels has real cost; don't poll 1000 channels in a hot loop |
| Ready cases are chosen **randomly** | No ordering/priority between ready cases (see priority pattern below) |
| `default` is only taken if **nothing** is ready | If *any* case is ready, `default` never runs, regardless of its position in source |

---

## 7. The Pattern Catalog

Each pattern includes **what**, **why**, **code**, and **trade-offs**.

### Pattern 1: Try-Send / Drop-on-Full (load shedding)

**Purpose:** Never let a slow consumer slow down the producer. If the consumer can't keep up, drop the item.

```go
func (l *Logger) Log(msg string) {
    select {
    case l.buf <- msg:
    default:
        atomic.AddUint64(&l.dropped, 1) // ALWAYS count drops
    }
}
```

**Trade-off:** Data loss. Only acceptable when data is lossy-by-nature (logs, metrics, telemetry, UI updates) or you handle the loss (retry, reject to client).

---

### Pattern 2: Try-Receive / Polling

**Purpose:** Check for work/messages without stopping what you're doing.

```go
// Game loop / render loop: never wait for input
for {
    select {
    case ev := <-inputEvents:
        handle(ev)
    default:
        // no input this frame; keep simulating
    }
    update()
    render()
}
```

**Trade-off:** If there's no other work in the loop body, this becomes a **busy loop burning 100% CPU** (see [Pitfalls](#10-pitfalls-and-anti-patterns)).

---

### Pattern 3: Cancellation Check Inside Hot Loops

**Purpose:** Check "should I stop?" between units of CPU-bound work, without blocking.

```go
func process(ctx context.Context, items []Item) error {
    for _, it := range items {
        select {
        case <-ctx.Done():
            return ctx.Err()
        default:
        }
        expensiveCompute(it)
    }
    return nil
}
```

This is the idiomatic way to make CPU-bound code cancellable. Blocking on `ctx.Done()` would defeat the purpose.

---

### Pattern 4: Try-Acquire Semaphore (bounded concurrency with rejection)

**Purpose:** Limit concurrent operations; *reject* instead of queueing when at the limit.

```go
type Limiter struct{ slots chan struct{} }

func NewLimiter(n int) *Limiter {
    return &Limiter{slots: make(chan struct{}, n)}
}

// TryAcquire returns true if a slot was obtained immediately.
func (l *Limiter) TryAcquire() bool {
    select {
    case l.slots <- struct{}{}:
        return true
    default:
        return false
    }
}

func (l *Limiter) Release() { <-l.slots }
```

Usage:

```go
if !limiter.TryAcquire() {
    http.Error(w, "too many concurrent requests", http.StatusTooManyRequests)
    return
}
defer limiter.Release()
```

```
   capacity = 3 (buffered chan struct{}, cap 3)

   request 1 -> [X][ ][ ]  acquired
   request 2 -> [X][X][ ]  acquired
   request 3 -> [X][X][X]  acquired
   request 4 -> [X][X][X]  TryAcquire() == false -> 429 immediately
   release   -> [X][X][ ]  slot free again
```

---

### Pattern 5: Coalescing Signal ("dirty flag" channel)

**Purpose:** Wake a worker when "something changed", but don't queue one wake-up per change. Many notifications collapse into one.

```go
type Notifier struct{ c chan struct{} }

func NewNotifier() *Notifier {
    return &Notifier{c: make(chan struct{}, 1)} // capacity 1 is the trick
}

// Notify never blocks and never accumulates more than 1 pending signal.
func (n *Notifier) Notify() {
    select {
    case n.c <- struct{}{}:
    default: // a wake-up is already pending; that's enough
    }
}

func (n *Notifier) C() <-chan struct{} { return n.c }
```

Worker:

```go
for {
    select {
    case <-notifier.C():
        reconcileEverything() // reads the CURRENT state, not "the event"
    case <-ctx.Done():
        return
    }
}
```

```
  Producers (1000 changes in a burst)        Worker
  --------------------------------           ------
   Notify() -> [S]  (buffer now full)
   Notify() -> default (already pending)
   Notify() -> default
   ... x 997 more -> default
                                        <-- wakes ONCE, reads latest state
```

**Critical requirement:** the worker must re-read *current state* when woken, not rely on the signal carrying data. The signal means "go look", not "here is what happened".

---

### Pattern 6: Latest-Value-Wins (overwrite oldest)

**Purpose:** Consumers only care about the *most recent* value (sensor reading, config snapshot, price tick, progress %).

```go
// ch must have capacity 1
func PublishLatest[T any](ch chan T, v T) {
    for {
        select {
        case ch <- v:
            return
        default:
            // Buffer full: discard the stale value, then retry the send.
            select {
            case <-ch:
            default:
            }
        }
    }
}
```

```
   buffer (cap 1)

   publish(10)  -> [10]
   publish(11)  -> full -> evict 10 -> [11]
   publish(12)  -> full -> evict 11 -> [12]
   consumer receives -> 12      (never saw 10, 11; that's the point)
```

---

### Pattern 7: Priority Select

**Problem:** When multiple cases are ready, Go picks **randomly**. If you want `quit` to always win over `jobs`:

```go
for {
    // 1) Non-blocking check of the HIGH-priority channel first
    select {
    case <-quit:
        return
    default:
    }

    // 2) Then block on both (quit is still included so we wake on quit too)
    select {
    case <-quit:
        return
    case j := <-jobs:
        handle(j)
    }
}
```

The first non-blocking `select` guarantees that if `quit` is already signaled, it is observed before taking another job.

```
   iteration:
   +-----------------------------+
   | non-blocking check(quit)    |--- ready --> return
   +--------------+--------------+
                  | not ready
                  v
   +-----------------------------+
   | blocking select(quit, jobs) |
   +-----------------------------+
```

---

### Pattern 8: Drain a Channel Without Blocking

```go
func Drain[T any](ch <-chan T) (n int) {
    for {
        select {
        case _, ok := <-ch:
            if !ok {
                return n // closed: MUST handle, or this loops forever
            }
            n++
        default:
            return n // empty: done
        }
    }
}
```

Note the `ok` check: a *closed* channel is always "ready", so without it this loop would spin forever.

---

### Pattern 9: Non-Blocking Broadcast to Many Subscribers

**Purpose:** One publisher, N subscribers, each with its own buffered channel. A slow subscriber must not stall the others.

```go
type Broker[T any] struct {
    mu   sync.RWMutex
    subs map[chan T]struct{}
}

func (b *Broker[T]) Subscribe(buf int) chan T {
    ch := make(chan T, buf)
    b.mu.Lock()
    b.subs[ch] = struct{}{}
    b.mu.Unlock()
    return ch
}

func (b *Broker[T]) Publish(v T) (delivered, dropped int) {
    b.mu.RLock()
    defer b.mu.RUnlock()
    for ch := range b.subs {
        select {
        case ch <- v:
            delivered++
        default:
            dropped++ // slow subscriber: skip (or evict it; see chat hub)
        }
    }
    return
}
```

*(Production code must also handle unsubscribe/closing safely so that Publish never sends on a closed channel.)*

---

### Pattern 10: Bounded Wait = Non-Blocking + Timer (hybrid)

When "immediately" is too strict but "forever" is too long, use a timeout. This is **not** non-blocking, but it's the next step on the spectrum:

```go
t := time.NewTimer(50 * time.Millisecond)
defer t.Stop()

select {
case q <- job:
    // accepted
case <-t.C:
    return ErrBusy // waited up to 50ms, then gave up
}
```

```
   Spectrum of waiting strategies

   non-blocking ----- bounded wait ----- blocking ----- (blocking + ctx cancel)
   "now or never"     "up to T"          "as long       "as long as it takes
                                          as it takes"   unless told to stop"
   select+default     select+timer       plain send     select+ctx.Done()
```

---

### Pattern 11: Non-Blocking "Fan-In Poll" Across Multiple Sources

```go
// Take whatever is available from either source; do idle work otherwise.
select {
case a := <-srcA:
    handleA(a)
case b := <-srcB:
    handleB(b)
default:
    idleWork() // e.g., flush, housekeeping, yield
}
```

---

## 8. Real-World Use Cases (with Architecture Diagrams)

### 8.1 WebSocket / Chat Hub: Protect the Hub from Slow Clients

**The problem:** A hub broadcasts every message to thousands of connected clients. If one client has a bad connection and its TCP buffer is full, a *blocking* send to that client would stall the hub, and thus **every** client.

**The solution:** Each client gets a buffered `send` channel. The hub does a **non-blocking send**. If the client's buffer is full, the client is considered too slow and is disconnected.

```
                          +-----------------------------------+
                          |               HUB                 |
  client A  ---- msg ---> |  single goroutine event loop      |
  (readPump)    broadcast |                                   |
                          |  for each client:                 |
                          |     select {                      |
                          |     case c.send <- msg:   (ok)    |
                          |     default:    close + remove    |
                          |     }                             |
                          +-----+------------+------------+---+
                                |            |            |
                         buffered chan  buffered chan  buffered chan
                          (cap 256)      (cap 256)      (cap 256)
                                |            |            |
                                v            v            v
                          +----------+ +----------+ +----------+
                          | Client A | | Client B | | Client C |
                          | writePump| | writePump| | writePump|
                          +----+-----+ +----+-----+ +----+-----+
                               |            |            |
                          websocket     websocket     websocket
                          (fast net)    (fast net)    (SLOW net!)
                               |            |            |
                            Browser A    Browser B    Browser C
                                                      buffer fills ->
                                                      hub's try-send hits
                                                      default -> C is
                                                      disconnected; A and B
                                                      are UNAFFECTED
```

```go
func (h *Hub) run() {
    for {
        select {
        case c := <-h.register:
            h.clients[c] = true
        case c := <-h.unregister:
            if _, ok := h.clients[c]; ok {
                delete(h.clients, c)
                close(c.send)
            }
        case msg := <-h.broadcast:
            for c := range h.clients {
                select {
                case c.send <- msg:
                default: // client too slow
                    close(c.send)
                    delete(h.clients, c)
                }
            }
        }
    }
}
```

This is the well-known structure of the Gorilla WebSocket chat example. The principle: **isolate failure domains**. One slow consumer must never become everyone's problem.

---

### 8.2 HTTP Server Load Shedding / Admission Control

**The problem:** Under a traffic spike, an unbounded queue of requests grows, latency explodes for *everyone* (queueing delay), memory balloons, and eventually the process dies. It is better to fail fast for some requests and serve others quickly.

**The solution:** A bounded queue in front of a worker pool; enqueue with a non-blocking send; on failure return `503`/`429` immediately.

```
                              HTTP SERVER PROCESS
  +----------------------------------------------------------------------+
  |                                                                      |
  |  Client --> [ net/http handler goroutine ]                           |
  |  Client -->            |                                             |
  |  Client -->            |  select {                                   |
  |  Client -->            |  case queue <- job:   -> accepted           |
  |    ...                 |  default:             -> 503 + Retry-After  |
  |                        v  }                                          |
  |               +-----------------+                                    |
  |               |  bounded queue  |   <- chan *Job, cap = 100          |
  |               | [j][j][j][j]... |                                    |
  |               +--------+--------+                                    |
  |                        |                                             |
  |         +--------------+---------------+                             |
  |         v              v               v                             |
  |    +---------+    +---------+     +---------+                       |
  |    | worker1 |    | worker2 | ... | workerN |   (fixed pool size)   |
  |    +----+----+    +----+----+     +----+----+                       |
  |         |              |               |                             |
  |         +--------------+---------------+                             |
  |                        v                                             |
  |                 Database / downstream                                |
  +----------------------------------------------------------------------+

  Queue full?  -->  rejected in microseconds, no goroutine pile-up,
                    clients can retry / failover elsewhere.
```

```go
func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
    job := &Job{w: w, r: r, done: make(chan struct{})}

    select {
    case s.queue <- job:
        <-job.done // wait for a worker to finish (also select on r.Context().Done() in production)
    default:
        w.Header().Set("Retry-After", "1")
        http.Error(w, "server overloaded", http.StatusServiceUnavailable)
        s.metrics.Rejected.Inc()
    }
}
```

**Why this is better than blocking:** Little's Law says latency = queue length / throughput. A bounded queue bounds worst-case latency. Failing fast also lets upstream load balancers and clients react (retry elsewhere, back off, circuit-break).

---

### 8.3 Asynchronous Logging / Telemetry Pipeline

**The problem:** Logging on the request path must never slow requests. If the log sink (disk, network, Kafka) stalls, request goroutines would stall too.

**The solution:** Producers do try-send into a buffered channel; one background goroutine drains it to the sink. When full, **drop** and count.

```
   request goroutines (hot path, must never block)
   +------+ +------+ +------+ +------+
   | req1 | | req2 | | req3 | | reqN |
   +--+---+ +--+---+ +--+---+ +--+---+
      |        |        |        |
      |   select { case buf <- entry: ; default: dropped++ }
      +--------+---+----+--------+
                   |
                   v
          +-------------------+
          | buffered channel  |   cap = 8192
          | [e][e][e]...[ ][ ]|
          +---------+---------+
                    |
                    v
          +-------------------+      batches, flushes every 1s
          |  writer goroutine |-------------------------------+
          +-------------------+                               |
                                                              v
                                               +----------------------------+
                                               | File / Kafka / Loki / ELK  |
                                               +----------------------------+

   Sink slow? --> channel fills --> producers DROP (and increment a
                  metric), request latency is UNCHANGED.
```

```go
type AsyncLogger struct {
    ch      chan Entry
    dropped atomic.Uint64
}

func (l *AsyncLogger) Log(e Entry) {
    select {
    case l.ch <- e:
    default:
        l.dropped.Add(1)
    }
}
```

**Policy choice:** For *audit* logs you may not be allowed to drop; there you'd block, or persist locally first. Non-blocking fits *diagnostic* logs and metrics.

---

### 8.4 Kubernetes-Style Controllers: Coalesced Reconcile Triggers

**The problem:** A controller watches resources; thousands of events may arrive per second, but it only needs to reconcile *current desired state vs actual*. Queueing one reconcile per event is wasteful.

**The solution:** The "capacity-1 signal channel" (Pattern 5). Event handlers do non-blocking sends; the reconciler wakes once and processes the latest state.

```
   Event sources                                 Reconciler
   -------------                                 ----------

   +------------------+
   | Watch: Pods      |--+
   +------------------+  |    Notify()            +-------------------+
   +------------------+  |    (non-blocking)      |  chan struct{}    |
   | Watch: Configs   |--+--------------------->  |  capacity = 1     |
   +------------------+  |    select {            |  [ signal ]       |
   +------------------+  |    case c<-s:          +---------+---------+
   | Timer: resync    |--+    default: }                    |
   +------------------+                                     v
                                                  +-------------------+
        1000 events -> 1 pending signal           | loop:             |
                                                  |   <-signal        |
                                                  |   read CURRENT    |
                                                  |   state, reconcile|
                                                  +-------------------+
```

Event broadcasting libraries in this ecosystem expose the same choice as a policy (for example, a "drop if channel full" mode versus a "block" mode), a direct illustration of the block-vs-drop design decision.

---

### 8.5 Real-Time Dashboards / Market Data / Sensors (Latest-Value-Wins)

**The problem:** A feed produces 10,000 updates/sec. The UI refreshes 30 times/sec. Queueing every update leads to unbounded lag, so the UI shows old data.

**The solution:** Per-subscriber capacity-1 channel, overwrite-oldest (Pattern 6).

```
   Market feed (10k ticks/s)
          |
          v
   +--------------+      PublishLatest()      +-----------------------+
   | tick router  |-------------------------> | per-client chan (cap 1)|
   +--------------+   (non-blocking,          |   [ newest price ]     |
                       evicts stale)          +-----------+-----------+
                                                          |
                                                          v   (30 Hz)
                                                +--------------------+
                                                |  UI render loop    |
                                                |  always shows the  |
                                                |  NEWEST value      |
                                                +--------------------+
```

---

### 8.6 Game Loops, UI Event Loops, Real-Time Systems

```
   +---------------------- frame loop (60 Hz) ----------------------+
   |                                                                |
   |   +---------+     non-blocking poll      +-------------------+ |
   |   | input   |--- select{case e:=<-in:    |                   | |
   |   | events  |    handle(e)  default:} -->| process whatever  | |
   |   +---------+                            | is there, never   | |
   |   +---------+                            | wait              | |
   |   | network |--- select{case m:=<-net:   |                   | |
   |   | packets |    apply(m)  default:} --->|                   | |
   |   +---------+                            +---------+---------+ |
   |                                                    v           |
   |                                          simulate() + render() |
   |                                                    |           |
   |                                          sleep until next frame|
   +----------------------------------------------------------------+
```

A frame must finish in ~16ms. Waiting for input would drop frames. Non-blocking poll plus a **paced** loop (ticker or sleep) is the correct combo.

---

### 8.7 Graceful Shutdown / Cancellation Checks in Workers

```
                      main
                       |
                  close(quit)            <- broadcast shutdown
          +------------+------------+
          v            v            v
     +---------+  +---------+  +---------+
     | worker1 |  | worker2 |  | worker3 |
     +----+----+  +----+----+  +----+----+
          |            |            |
   between each unit of work:  select { case <-quit: return; default: }
```

```go
func worker(quit <-chan struct{}, jobs <-chan Job) {
    for {
        // Priority check: stop requests win over new jobs
        select {
        case <-quit:
            return
        default:
        }

        select {
        case <-quit:
            return
        case j, ok := <-jobs:
            if !ok {
                return
            }
            run(j)
        }
    }
}
```

---

### 8.8 Circuit Breaker / Bulkhead / Concurrency Limiter

The try-acquire semaphore (Pattern 4) is the heart of the **bulkhead** resilience pattern: each downstream dependency gets its own fixed number of slots, so one failing dependency can't consume all your goroutines.

```
              Service
   +----------------------------------------------+
   |                                              |
   |   calls to Payments      calls to Search     |
   |          |                      |            |
   |   +------v------+        +------v------+     |
   |   | bulkhead    |        | bulkhead    |     |
   |   | cap = 10    |        | cap = 50    |     |
   |   | TryAcquire()|        | TryAcquire()|     |
   |   +------+------+        +------+------+     |
   |          | ok / fail-fast       | ok / fail  |
   +----------|----------------------|------------+
              v                      v
          Payments API           Search API
          (degraded!)            (healthy)

   Payments slow -> its 10 slots fill -> further Payments calls fail
   FAST, while Search is completely unaffected.
```

---

### 8.9 Timers and Tickers Drop Ticks for Slow Receivers

`time.Ticker` delivers ticks on a channel with **capacity 1**. If your receiver is slow, the runtime does a **non-blocking send** and drops the tick rather than queueing or blocking the timer machinery.

```
   Time -->  0    1    2    3    4    5    6    7   (ticker period = 1s)
   ticks:    T0   T1   T2   T3   T4   T5   T6   T7

   Receiver busy during t=1.0 .. t=4.5 (processing takes 3.5s):

   chan(cap 1):  [T0]        (T0 sitting in buffer)
                 T1: send -> full -> DROPPED
                 T2: send -> full -> DROPPED
                 T3: send -> full -> DROPPED
                 T4: send -> full -> DROPPED
   Receiver finally reads T0, then waits for next tick T5.

   => You do NOT get a "catch-up burst" of 4 ticks. Slow ticker
      consumers silently lose ticks. Design for it.
```

This is the same primitive you just learned, built into the standard library.

---

## 9. Where the Standard Library and Ecosystem Use This

A map of well-known places where the "never block, skip if not ready" idea shows up. (Specifics can change between versions; the *pattern* is what matters.)

| Where | How it uses non-blocking channel sends | Why |
|-------|----------------------------------------|-----|
| **`time.Timer` / `time.Ticker`** | Runtime sends the current time to `C` with a non-blocking send | Timer machinery must never block on a slow user goroutine; ticks are dropped for slow receivers |
| **`os/signal`** | When a signal arrives, it's delivered to registered channels with a non-blocking send; the docs tell you to use a **buffered** channel | The signal-handling goroutine must not block on a busy consumer; an unbuffered channel could miss signals |
| **`context`** | Cancellation is signaled by `close(done)`; callers commonly poll with `select { case <-ctx.Done(): default: }` | Cheap "should I stop?" check in loops |
| **Event/watch broadcasters** (e.g., Kubernetes `watch.Broadcaster`) | Configurable policy: block vs **drop if channel full** | Slow watchers must not stall event delivery to others |
| **Messaging clients** (e.g., NATS Go client with channel subscriptions) | If a subscriber's channel is full, messages are dropped and the subscriber is flagged a **"slow consumer"** | Protect the connection's read loop from one slow subscriber |
| **WebSocket hubs** (e.g., Gorilla chat example) | Non-blocking send to each client's `send` channel; evict on full | Isolate slow clients |
| **Async loggers / metrics clients** | Try-send to a buffer; drop and count on overflow | Observability must never degrade the product |
| **`golang.org/x/sync/errgroup`** | `Group.TryGo` starts a goroutine only if the concurrency limit permits, otherwise returns `false` (conceptually a try-acquire) | Non-blocking admission into a bounded group |
| **`golang.org/x/sync/semaphore`** | `Weighted.TryAcquire(n)` returns immediately with success/failure | Non-blocking variant of `Acquire` |
| **Schedulers/controllers/caches** | Capacity-1 "wake-up" channels | Coalesce redundant notifications |

> **Observation:** nearly every item in that table is one of three archetypes: **(a)** protect a fast, important loop from a slow dependency, **(b)** coalesce redundant notifications, **(c)** admission control / try-acquire.

---

## 10. Pitfalls and Anti-Patterns

### 10.1 The Busy-Wait (CPU spin)

```go
// BAD: spins at 100% of a CPU core when ch is empty
for {
    select {
    case v := <-ch:
        handle(v)
    default:
        // nothing: loop again immediately!
    }
}
```

**Fixes (pick one):**

```go
// (a) Just block. If you have nothing else to do, you don't need default.
for v := range ch { handle(v) }

// (b) Block, but also wake for other events:
for {
    select {
    case v := <-ch:    handle(v)
    case <-ctx.Done(): return
    }
}

// (c) If you must poll, pace the loop:
t := time.NewTicker(10 * time.Millisecond)
defer t.Stop()
for range t.C {
    select {
    case v := <-ch: handle(v)
    default:
    }
}
```

> **Rule of thumb:** `default` inside a `for` loop is a **smell** unless the loop body does *other useful work* (like a game frame) or is paced.

---

### 10.2 Check-Then-Act with `len(ch)` / `cap(ch)` (Race Condition)

```go
// BAD: TOCTOU race
if len(ch) < cap(ch) {
    ch <- v   // another goroutine may fill the buffer between check and send -> BLOCKS
}
```

`len(ch)` is a snapshot that is stale the instant you read it. **The `select`/`default` is the atomic version of this check.** Use it; reserve `len(ch)` for metrics and debugging.

```
   goroutine 1                    goroutine 2
   -----------                    -----------
   len(ch) < cap(ch)  -> true
                                  ch <- x   (buffer is now full)
   ch <- v            -> BLOCKS!   <-- the "check" lied
```

---

### 10.3 Silent Data Loss

Dropping is a **business decision**, not a default. If you drop:

- **Count it** (metric). A silent drop is a mystery bug later.
- **Log it, rate-limited**.
- **Document it** in the API ("best-effort delivery").
- Make sure the data is *actually* droppable (logs: yes; payment events: no).

---

### 10.4 Unbuffered Try-Send Loses to Timing

```go
done := make(chan struct{}) // unbuffered

go func() {
    time.Sleep(time.Millisecond) // receiver not parked yet
    <-done
}()

select {
case done <- struct{}{}:
    fmt.Println("sent")
default:
    fmt.Println("dropped") // <-- almost always taken!
}
```

For notification-style non-blocking sends, use a **buffered** channel (usually capacity 1) so the signal is *remembered* until the receiver gets around to it.

---

### 10.5 Sending on a Closed Channel Panics (Even with `default`)

```go
close(ch)

select {
case ch <- 1:  // PANIC: send on closed channel
default:
}
```

**Rules:**
- Only the *sender side* should close, and only when no more sends can occur.
- With multiple senders, coordinate with a `sync.WaitGroup`/`sync.Once`, or avoid closing the data channel and use a separate `done` channel for shutdown instead.

---

### 10.6 Starvation / Unfairness From Polling

If a loop checks channel A non-blocking first and always finds work there, it may never get to B. Polling order is **your** responsibility because you've left `select`'s random fairness. Rotate order or use weighted scheduling if fairness matters.

---

### 10.7 Draining a Closed Channel Forever

```go
// BAD: closed channel is ALWAYS ready -> infinite loop
for {
    select {
    case <-ch:
    default:
        return
    }
}
```

Always check `ok` (see Pattern 8).

---

### 10.8 `time.After` in Loops (Timer Allocation)

```go
for {
    select {
    case v := <-ch:
        handle(v)
    case <-time.After(time.Second): // allocates a timer each iteration
        return
    }
}
```

On older Go versions, an un-fired `time.After` timer isn't reclaimed until it fires, so heavy loops could accumulate garbage. Go 1.23+ made unreferenced timers collectible and changed timer channels to be effectively unbuffered. Even so, for hot loops, **reuse a `time.Timer`** (`Reset`) or use `context.WithTimeout` outside the loop. And for pure "now or never", you don't need a timer at all, only `default`.

---

### 10.9 Expecting `default` to Run First

Source order doesn't matter. If **any** other case is ready, `default` is *never* chosen. `default` is strictly "none of the others are ready".

```go
select {
default:                // written first...
    fmt.Println("default")
case v := <-readyCh:    // ...but this is ready, so THIS runs
    fmt.Println(v)
}
```

---

### 10.10 Misusing Non-Blocking for Synchronization

If correctness requires "the receiver *must* get this", a non-blocking send is the wrong tool (it can drop). Use a blocking send (optionally with context/timeout), or a durable queue.

---

### Pitfall Summary

| Pitfall | Symptom | Fix |
|---------|---------|-----|
| Busy loop with `default` | 100% CPU | Remove `default`, add other `case`s, or pace with ticker |
| `len(ch)` check-then-send | Occasional hang | `select` + `default` |
| Silent drops | "Where did my data go?" | Metric + policy + documentation |
| Unbuffered try-send | Mostly "dropped" | Buffered channel (cap 1 for signals) |
| Send on closed channel | Panic | Clear close ownership; separate `done` channel |
| Draining closed channel | Infinite loop | Check `ok` |
| Polling order bias | Starvation | Rotate / weighted scheduling |

---

## 11. Non-Blocking vs. Timeouts vs. Context vs. Other Tools

### 11.1 Comparison table

| Need | Tool | Blocks? | Notes |
|------|------|---------|-------|
| "Do it now or skip it" | `select` + `default` | Never | Primary subject of this guide |
| "Wait at most T" | `select` + `time.After` / `Timer` | Up to T | Bounded wait |
| "Wait until cancelled" | `select` + `ctx.Done()` | Until cancel | Cancellation-aware blocking |
| "Try to lock a mutex" | `sync.Mutex.TryLock()` (Go 1.18+) | Never | Not channel-based; use sparingly (docs note it's rarely correct to use) |
| "Try to take semaphore slot" | `semaphore.Weighted.TryAcquire` or buffered-chan try-send | Never | Admission control |
| "Try to start a goroutine within a limit" | `errgroup.Group.TryGo` | Never | Combined with `SetLimit` |
| "Check a flag cheaply" | `atomic.Bool` | Never | Use when you don't need channel semantics (no blocking/waking) |
| "Block until condition changes" | `sync.Cond` / channel | Yes | Different use case |

### 11.2 When *not* to use channels for non-blocking behavior

- **Just a boolean flag** ("is shutting down?"): `atomic.Bool` is simpler and faster than a channel poll, if you never need to *wait* on it.
- **Shared counter/state**: atomics or a mutex.
- **Lock attempt**: prefer redesigning to avoid `TryLock`; channels aren't a replacement.

> Choose channels when you need **blocking + non-blocking views of the same signal** (e.g., one goroutine blocks on `<-quit`, another polls it).

### 11.3 The waiting-strategy spectrum, visually

```
 Never wait                                                         Wait forever
 |-------------------|----------------------|------------------------|
 select+default      select+timer           select+ctx.Done()        plain op
 (non-blocking)      (bounded)              (cancellable)            (unbounded)

  + lowest latency     + bounded latency      + clean shutdown         + simplest
  + no resource hold   + tolerates blips      + request-scoped         - can hang
  - may drop/reject    - timer overhead       - needs ctx plumbing     - no escape
```

---

## 12. Design Decisions: Drop, Block, Coalesce, or Reject?

When a channel is full, you have **four honest options**. Choosing the right one is the real skill.

```
                       Channel is FULL. Now what?
                                  |
      +---------------+-----------+-----------+----------------+
      |               |                       |                |
      v               v                       v                v
  BLOCK          DROP NEWEST            DROP OLDEST        COALESCE /
  (backpressure) (try-send, skip)       (evict + send)     REJECT upstream
      |               |                       |                |
  Use when:       Use when:               Use when:        Use when:
  data must       data is lossy,          only freshest    events are
  not be lost;    producer must           value matters    redundant, or
  slowdown is     not stall               (prices, state)  you can tell the
  acceptable                                               client "try later"
      |               |                       |                |
  Risk:           Risk:                   Risk:            Risk:
  stalls whole    silent loss,            loses history    needs client
  pipeline;       old data kept           (by design)      cooperation
  cascading
  latency
```

| Strategy | Mechanism | Typical domain |
|----------|-----------|----------------|
| **Block** (backpressure) | Plain send | Batch pipelines, ETL, financial ledgers |
| **Drop newest** | `select`+`default` | Logs, metrics, best-effort telemetry |
| **Drop oldest / latest-wins** | evict + retry send | Live prices, sensor state, progress |
| **Coalesce** | capacity-1 signal | Reconcile triggers, cache invalidation, "dirty" flags |
| **Reject upstream** | try-send, then 429/503 | HTTP/gRPC servers, RPC admission control |
| **Disconnect consumer** | try-send, close on fail | WebSocket hubs, pub/sub brokers |

### Questions to ask yourself (the "thinking checklist")

1. **Is the data loss-tolerant?** (Logs yes, payments no.)
2. **Who should absorb the overload?** Producer (block), consumer (drop), or caller (reject)?
3. **Is the message an event, or just a "state changed" hint?** Hints coalesce; events usually don't.
4. **Does the newest value supersede older ones?** If so, latest-wins.
5. **What's the blast radius of one slow consumer?** If "everyone", you need isolation (per-consumer buffer + non-blocking send).
6. **How will I *see* drops?** If the answer is "I won't", don't drop yet.
7. **What does the producer's hot path cost?** If it must be microseconds, it can't block.

---

## 13. Testing and Observability

### 13.1 Make drops visible

```go
type Stats struct {
    Sent    atomic.Uint64
    Dropped atomic.Uint64
}

func (q *Queue) Offer(v Item) bool {
    select {
    case q.ch <- v:
        q.stats.Sent.Add(1)
        return true
    default:
        q.stats.Dropped.Add(1)
        return false
    }
}
```

Export `Dropped`, plus `len(q.ch)/cap(q.ch)` as a **saturation gauge** (approximate is fine for metrics). Alert on a sustained non-zero drop rate.

### 13.2 Deterministic tests

Fill the buffer, then verify the next try-send is rejected, no sleeps required:

```go
func TestTrySendDropsWhenFull(t *testing.T) {
    ch := make(chan int, 2)

    if !TrySend(ch, 1) || !TrySend(ch, 2) {
        t.Fatal("expected first two sends to succeed")
    }
    if TrySend(ch, 3) {
        t.Fatal("expected third send to be rejected (buffer full)")
    }
    if got := <-ch; got != 1 { // FIFO order preserved
        t.Fatalf("got %d, want 1", got)
    }
    if !TrySend(ch, 3) {
        t.Fatal("expected send to succeed after space freed")
    }
}
```

### 13.3 Race detector

Always run tests with `go test -race`. Non-blocking patterns involve many goroutines touching shared state (subscriber maps, counters).

### 13.4 Load testing

Simulate a **slow consumer** (`time.Sleep` in the reader) and verify: (1) producer latency stays flat, (2) drop counters increase, (3) no goroutine leak (`runtime.NumGoroutine()`), (4) memory is bounded.

```
   Expected healthy shape under a slow consumer:

   producer latency   : ======================  (flat)
   dropped counter    : ........../////////////  (rises)
   queue depth        : ##########################  (pinned at capacity)
   memory             : ======================  (flat)

   Unhealthy (blocking by mistake):
   producer latency   : ......////////////////// (climbs)
   memory             : ......////////////////// (climbs if queue unbounded)
```

---

## 14. Decision Flowchart and Cheat Sheet

### 14.1 Flowchart: should I use a non-blocking op?

```
                       Do I have other useful work to do
                       if this channel op isn't ready?
                        /                          \
                      YES                           NO
                       |                             |
                       v                             v
            Non-blocking is a good fit      Would waiting a bounded time
            (poll loop, frame loop,         be acceptable?
             opportunistic flush)              /              \
                                             YES               NO
                                              |                 |
                                              v                 v
                                    select + timer      Must I be able to
                                    (bounded wait)      cancel the wait?
                                                           /          \
                                                         YES           NO
                                                          |             |
                                                          v             v
                                                 select + ctx.Done()   plain
                                                                       blocking

   Separate question: the channel is a SEND target and the consumer
   may be slow/untrusted?
                       |
                       v
         Can I afford to lose / reject this item?
              /                       \
            YES                        NO
             |                          |
             v                          v
   try-send (drop/reject/evict)    blocking send (accept backpressure),
   + COUNT the drops               or use a durable queue
```

### 14.2 Cheat sheet

```go
// Non-blocking send
select { case ch <- v: ; default: }

// Non-blocking receive
select { case v := <-ch: use(v); default: }

// Non-blocking receive + closed detection
select { case v, ok := <-ch: if !ok { closed() } else { use(v) }; default: }

// Cancellation poll
select { case <-ctx.Done(): return ctx.Err(); default: }

// Coalescing signal (ch := make(chan struct{}, 1))
select { case ch <- struct{}{}: ; default: }

// Try-acquire semaphore (sem := make(chan struct{}, N))
select { case sem <- struct{}{}: ok = true; default: ok = false }   // release: <-sem

// Latest-wins (ch cap 1)
for { select { case ch <- v: return; default: select { case <-ch: default: } } }

// Priority: check high first (non-blocking), then block on both
select { case <-hi: return; default: }
select { case <-hi: return; case x := <-lo: handle(x) }
```

### 14.3 Properties at a glance

| Property | Value |
|----------|-------|
| Language feature used | `select` with `default` |
| Goroutine parked? | Never (on that channel) |
| Atomic w.r.t. channel state? | Yes (check and act are one operation) |
| Chooses among multiple ready cases | Uniformly pseudo-random |
| Works on nil channel | Yes: case is never ready, so `default` runs |
| Works on closed channel (recv) | Receives zero/remaining values immediately |
| Works on closed channel (send) | **Panics** |
| Cost (full-channel send) | Very low (lock-free fast path) |
| Danger | CPU spin, silent loss, racy `len()` checks |

---

## 15. Exercises

Working these out (not just reading) is what cements the mental model.

**Exercise 1: Predict the output.**

```go
ch := make(chan int, 2)
for i := 1; i <= 4; i++ {
    select {
    case ch <- i:
        fmt.Println("sent", i)
    default:
        fmt.Println("dropped", i)
    }
}
```
*Answer:* `sent 1`, `sent 2`, `dropped 3`, `dropped 4`.

**Exercise 2: What happens?**

```go
var ch chan int
select {
case ch <- 1:
    fmt.Println("sent")
default:
    fmt.Println("default")
}
```
*Answer:* `default`. A nil channel's case is never ready.

**Exercise 3: Spot the bug.**

```go
func (b *Broker) Publish(v int) {
    for _, ch := range b.subs {
        ch <- v
    }
}
```
*Answer:* A single slow subscriber blocks `Publish` for everyone. Use a non-blocking send (and decide: drop, evict, or buffer).

**Exercise 4: Build it.** Implement a `RateLimitedQueue[T]` with `Offer(v T) error` (returns `ErrFull` immediately when full) and a background worker that consumes. Add drop metrics and a test that proves `Offer` never blocks when the worker is stalled.

**Exercise 5: Design.** You run a service that receives 50k events/s and forwards them to a downstream that handles 20k/s with occasional stalls. Choose between block / drop-newest / drop-oldest / reject-upstream for: (a) click-stream analytics, (b) order placement, (c) live location pings. Justify each using the checklist in §12.

*Suggested answers:* (a) drop-newest with metrics, (b) reject upstream with retry or durable queue, never silently drop, (c) latest-wins per entity.

**Exercise 6: Fix the spin.** Rewrite the following without burning CPU, keeping the "do housekeeping occasionally" behavior.

```go
for {
    select {
    case m := <-in:
        handle(m)
    default:
        housekeeping()
    }
}
```
*Hint:* add a `time.Ticker` case for housekeeping and remove `default`.

---

## 16. Summary

**The essentials:**

1. **Non-blocking channel ops are `select` with `default`.** Go has no separate try-API.
2. They give an **atomic "do it now or not at all"** decision: no racy `len()` checks.
3. They exist to **move the cost of congestion** away from a critical path, onto drops, rejections, or coalescing.
4. On **unbuffered** channels they succeed only on instantaneous rendezvous; for signals use **buffered (cap 1)** channels.
5. Sending to a **closed** channel panics regardless of `default`; closed-channel receives are always "ready".
6. `default` only runs if **no other case is ready**; ready cases are chosen **randomly**; use the two-step pattern for priority.
7. The runtime makes the single-case form cheap (compiled to `selectnbsend` / `selectnbrecv`, with a lock-free "full" fast path).

**The real-world archetypes:**

```
   +-----------------------------+--------------------------------------+
   | Archetype                   | Examples                             |
   +-----------------------------+--------------------------------------+
   | Protect a fast loop from a  | WebSocket hubs, async logging,       |
   | slow consumer               | pub/sub brokers, NATS slow consumer  |
   +-----------------------------+--------------------------------------+
   | Coalesce redundant signals  | Controller reconcile triggers,       |
   |                             | cache invalidation, dirty flags      |
   +-----------------------------+--------------------------------------+
   | Admission control / reject  | HTTP load shedding, bulkheads,       |
   |                             | try-acquire semaphores, TryGo        |
   +-----------------------------+--------------------------------------+
   | Poll without stalling       | Game/UI loops, cancellation checks   |
   |                             | in CPU-bound loops                   |
   +-----------------------------+--------------------------------------+
   | Latest-value-wins           | Market data, sensors, progress       |
   +-----------------------------+--------------------------------------+
```

**The thinking habit to carry forward:** whenever you write a channel send, ask *"If the receiver is slow, who should pay: me (block), the data (drop), or the caller (reject)?"* Non-blocking operations are how you make that choice **explicit** in code, instead of leaving it to whichever goroutine happens to get stuck.

---

*Further reading:* Go spec, "Select statements" and "Send statements"; `runtime/chan.go` and `runtime/select.go` in the Go source; the `context`, `time`, and `os/signal` package docs; `golang.org/x/sync` (`errgroup`, `semaphore`); the Go blog series on concurrency patterns.
