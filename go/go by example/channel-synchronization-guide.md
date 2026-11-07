# Channel Synchronization: The Complete Guide

> Goal: build a **mental model** so you can look at any concurrent problem and know
> *which synchronization tool fits, why, and what can go wrong*.
> Examples are in Go (where channels are first-class), but every concept maps to other languages (Section 17).

---

## Table of Contents

1. [What "Channel Synchronization" Means](#1-what-channel-synchronization-means)
2. [Why Synchronization Exists At All](#2-why-synchronization-exists-at-all)
3. [The Core Mental Model (CSP)](#3-the-core-mental-model-csp)
4. [Anatomy of a Channel (Internals)](#4-anatomy-of-a-channel-internals)
5. [Unbuffered vs Buffered Channels](#5-unbuffered-vs-buffered-channels)
6. [Channel State Behavior Table (nil / open / closed)](#6-channel-state-behavior-table)
7. [Closing Channels: Rules and Ownership](#7-closing-channels-rules-and-ownership)
8. [The Memory Model: Happens-Before](#8-the-memory-model-happens-before)
9. [`select`: Multiplexing and Its Internals](#9-select-multiplexing-and-its-internals)
10. [Synchronization Patterns (the toolbox)](#10-synchronization-patterns-the-toolbox)
11. [Channels vs Mutex vs WaitGroup vs Atomic vs Cond](#11-channels-vs-mutex-vs-waitgroup-vs-atomic-vs-cond)
12. [Real-World Use Cases With Architecture](#12-real-world-use-cases-with-architecture)
13. [Failure Modes: Deadlock, Leak, Race, Starvation](#13-failure-modes)
14. [Backpressure and Flow Control](#14-backpressure-and-flow-control)
15. [Performance Characteristics](#15-performance-characteristics)
16. [Testing and Debugging](#16-testing-and-debugging)
17. [The Same Idea in Other Languages and Systems](#17-the-same-idea-in-other-languages-and-systems)
18. [Other Meaning: Channel Sync in Telecom / Media](#18-other-meaning-channel-sync-in-telecom--media)
19. [Decision Framework and Cheat Sheet](#19-decision-framework-and-cheat-sheet)
20. [Practice Exercises](#20-practice-exercises)

---

## 1. What "Channel Synchronization" Means

A **channel** is a typed conduit through which concurrent units of execution
(goroutines, threads, tasks, processes) **send and receive values**.

"Channel synchronization" means **using channel operations to coordinate the timing
and ordering** of those concurrent units. A channel does two jobs at once:

| Job                     | What it gives you                                                   |
|-------------------------|---------------------------------------------------------------------|
| **Communication**       | Moves a value from producer A to consumer B                         |
| **Synchronization**     | Forces ordering: "B proceeds only after A did X" (even with no data)|

The second job is the one people miss. You can send an *empty* value
(`struct{}`) purely to say "I'm done" or "you may go". That is pure synchronization.

```go
done := make(chan struct{})
go func() {
    doWork()
    close(done)          // signal: "work finished"
}()
<-done                   // block until the signal; now doWork()'s effects are visible
```

---

## 2. Why Synchronization Exists At All

Modern CPUs have multiple cores, each with caches, store buffers, and compilers/CPUs
reorder instructions. Without coordination, two concurrent actors that touch shared state produce:

1. **Data races**: two accesses, at least one a write, no ordering between them. Undefined behavior.
2. **Lost updates**: both read `x=5`, both write `x=6`; one increment vanished.
3. **Visibility problems**: writer's update sits in a cache; reader sees stale data.
4. **Ordering problems**: consumer runs before producer has produced.

```
 Core 0 (Goroutine A)                 Core 1 (Goroutine B)
 +--------------+                     +--------------+
 |  L1 cache    |                     |  L1 cache    |
 |  x = 6       |   <-- not yet -->   |  x = 5 (stale)|
 +------+-------+     visible         +------+-------+
        |                                    |
        +-----------------+------------------+
                          |
                   +------v-------+
                   |  Main memory |
                   |    x = 5     |
                   +--------------+
```

Synchronization primitives (channels, mutexes, atomics) insert **ordering guarantees
and memory barriers** so one actor's effects become visible to another in a defined order.

---

## 3. The Core Mental Model (CSP)

Go's channels come from **CSP: Communicating Sequential Processes** (Tony Hoare, 1978).

> **"Don't communicate by sharing memory; share memory by communicating."**

Two philosophies:

```
 SHARED-MEMORY MODEL                         MESSAGE-PASSING (CSP) MODEL
 -------------------                         ---------------------------

 +--------+     +--------+                   +--------+            +--------+
 |  G1    |     |  G2    |                   |  G1    |  ---v--->  |  G2    |
 +---+----+     +---+----+                   | owns x |   value    | owns x |
     |              |                         +--------+            +--------+
     |   lock/unlock|                          (ownership TRANSFERS with the message)
     v              v
   +-------------------+                      Only ONE goroutine touches
   |  shared variable  |                      the data at any time, so
   |        x          |                      no lock is needed.
   +-------------------+
   Everyone touches x; protect with a mutex.
```

**The key mental shift: ownership transfer.**
When you send a pointer or value on a channel, you *hand off* ownership. After the send,
the sender must not touch it. The receiver now owns it. No two goroutines ever
access it concurrently, so races are structurally impossible.

**Three mental pictures** to carry around:

1. **A pipe / conveyor belt**: values flow one way, in order (FIFO).
2. **A rendezvous point** (unbuffered): sender and receiver must meet at the same instant, like a handshake.
3. **A mailbox with N slots** (buffered): sender drops a letter and walks away unless the mailbox is full.

---

## 4. Anatomy of a Channel (Internals)

In Go, `make(chan T, n)` allocates an `hchan` struct on the heap (runtime/chan.go).

```
 hchan (heap object)
 +--------------------------------------------------------------+
 | qcount    uint      number of elements currently in buffer   |
 | dataqsiz  uint      capacity of circular buffer (n)          |
 | buf       *ptr  ----+--------------------------------------+ |
 | elemsize  uint16    |  circular ring buffer                | |
 | closed    uint32    |                                      | |
 | sendx     uint      |   idx: 0    1    2    3    4         | |
 | recvx     uint      |      +----+----+----+----+----+      | |
 | recvq     waitq     |      | A  | B  | C  |    |    |      | |
 | sendq     waitq     |      +----+----+----+----+----+      | |
 | lock      mutex     |        ^              ^              | |
 +--------------------+-------- recvx         sendx ----------+-+
                                (next to read) (next to write)
```

### The two wait queues

When an operation can't proceed, the goroutine is **parked** (descheduled, not spinning)
and put on a queue:

```
                     hchan
        +--------------------------------+
        |  recvq  (receivers waiting)    |   <- goroutines blocked on  <-ch  (buffer empty)
        |   [G7] -> [G9] -> [G12]        |
        |                                |
        |  sendq  (senders waiting)      |   <- goroutines blocked on  ch<-v (buffer full)
        |   [G3] -> [G4]                 |
        +--------------------------------+
```

A channel is never both "has blocked receivers" and "has blocked senders" for long: if a receiver is waiting
and a sender arrives, the sender **hands the value directly to the receiver** and wakes it.

### Send algorithm (`ch <- v`)

```
                 ch <- v
                    |
          +---------v----------+
          |  lock(ch.lock)     |
          +---------+----------+
                    |
          +---------v----------+   yes   panic: send on closed channel
          |  ch.closed ?       +------------------------------------->
          +---------+----------+
                 no |
          +---------v----------+   yes   copy v DIRECTLY to receiver's stack,
          | recvq has waiter ? +-------> mark receiver runnable (goready), unlock, return
          +---------+----------+
                 no |
          +---------v----------+   yes   copy v into buf[sendx]; sendx++ (wrap);
          | qcount < dataqsiz ?+-------> qcount++; unlock; return
          +---------+----------+
                 no |
          +---------v----------+
          | enqueue self in    |
          | sendq; gopark()    |   <-- goroutine sleeps until a receiver (or close) wakes it
          +--------------------+
```

### Receive algorithm (`v := <-ch`)

```
                 v := <-ch
                    |
          +---------v----------+
          |  lock(ch.lock)     |
          +---------+----------+
                    |
          +---------v----------+   yes   return zero value, ok=false
          | closed && empty ?  +------------------------------------->
          +---------+----------+
                 no |
          +---------v----------+   yes   take from sender directly (unbuffered), or
          | sendq has waiter ? +-------> take buf head & move sender's value to buf tail
          +---------+----------+         wake sender, unlock, return
                 no |
          +---------v----------+   yes   v = buf[recvx]; recvx++ (wrap);
          |   qcount > 0 ?     +-------> qcount--; unlock; return
          +---------+----------+
                 no |
          +---------v----------+
          | enqueue self in    |
          | recvq; gopark()    |
          +--------------------+
```

**Insight:** every channel op takes a lock internally. A channel is a *mutex + queue + wait lists*
packaged with a nicer contract. That's why channels are slower than atomics, but far safer to reason about.

---

## 5. Unbuffered vs Buffered Channels

### 5.1 Unbuffered: `make(chan T)` (capacity 0): the Rendezvous

Sender and receiver must **both be present**. Whoever arrives first waits.

```
 Time --->

 Sender   : ----[ ch <- v ]=====blocked=====+
                                            |  HANDSHAKE (value copied, both continue)
 Receiver : ---------------------[ <-ch ]---+----->

 Sender  continues only AFTER receiver has taken the value.
```

Guarantee: when the send completes, **the receiver has received it**. This is a strong sync point.

### 5.2 Buffered: `make(chan T, n)`: Decoupling

Sender only blocks if the buffer is full; receiver only blocks if empty.

```
 Producer                   Buffer (cap 4)                    Consumer
 +------+      +----+----+----+----+      +------+
 | G1   | ---> | v1 | v2 | v3 |    | ---> | G2   |
 +------+      +----+----+----+----+      +------+
                  ^ head              ^ tail
 Producer runs ahead by up to 4 items without waiting for consumer.
```

Guarantee: when the send completes, the value is **in the buffer, not necessarily received**.

### 5.3 Which to choose?

| Question                                           | Answer            |
|----------------------------------------------------|-------------------|
| Need to know the receiver *has* the data?          | Unbuffered        |
| Need a pure handshake / lock-step?                 | Unbuffered        |
| Want to smooth out bursts / decouple speeds?       | Buffered          |
| Want a semaphore with N permits?                   | Buffered (cap N)  |
| Want "fire and forget" up to a limit?              | Buffered          |
| Unsure?                                            | Start unbuffered  |

**Warning:** a buffer is not a performance cheat code. It **hides** bugs (deadlocks appear only
when the buffer fills) and **hides backpressure** (see Section 14). Size buffers deliberately,
with a reason such as "burst of at most N" or "N workers".

### 5.4 Directional channels (compile-time contracts)

```go
func producer(out chan<- int)   // send-only: can't receive or close... well, CAN close
func consumer(in  <-chan int)   // receive-only: can't send or close
```

Directional types document intent and let the compiler prevent misuse. **Only the sender side should close.**

---

## 6. Channel State Behavior Table

Memorize this. Most channel bugs are one of these cells.

| Operation        | `nil` channel        | Open, not full/empty   | Open, FULL (send) / EMPTY (recv) | Closed                         |
|------------------|----------------------|------------------------|-----------------------------------|--------------------------------|
| `ch <- v`        | **blocks forever**   | succeeds               | **blocks** until space/receiver   | **PANIC**                      |
| `<-ch`           | **blocks forever**   | succeeds               | **blocks** until value/send       | returns buffered values, then zero value + `ok=false` |
| `close(ch)`      | **PANIC**            | succeeds               | succeeds                          | **PANIC** (double close)       |
| `len(ch)`        | 0                    | #items                 | #items                            | #remaining items               |
| `cap(ch)`        | 0                    | capacity               | capacity                          | capacity                       |

Useful consequences:

- **Receiving from a closed channel never blocks.** That's why `close` works as a *broadcast*.
- **A nil channel in a `select` is never chosen**: used to *disable* a case dynamically (Section 10.14).
- **Sending on a closed channel panics**: so only the owner/sender closes.

```go
v, ok := <-ch
// ok == true  -> v is a real value sent by someone
// ok == false -> channel is closed AND drained; v is the zero value
```

---

## 7. Closing Channels: Rules and Ownership

`close(ch)` means: **"no more values will ever be sent."** It's a message from sender to receivers.

### The golden rules

1. **Only the sender closes**, never the receiver. The receiver can't know whether other senders exist.
2. **Closing is optional.** Only close when receivers need to know "no more data" (e.g. `for range`). GC reclaims unclosed channels.
3. **Never close twice**, never send after close.
4. With **multiple senders**, don't let any of them close. Use a coordinator (Section 10.4).

### Close as broadcast

```
                      close(done)
                           |
        +------------+-----+------+------------+
        v            v            v            v
   +--------+   +--------+   +--------+   +--------+
   |Worker 1|   |Worker 2|   |Worker 3|   |Worker 4|
   | <-done |   | <-done |   | <-done |   | <-done |
   | returns|   | returns|   | returns|   | returns|
   +--------+   +--------+   +--------+   +--------+

   A single close() wakes EVERY goroutine blocked on <-done.
   A plain send (done <- struct{}{}) would wake only ONE.
```

### Ownership diagram (single producer, many consumers)

```
   +----------+   ch    +-----------+
   | Producer |========>| Consumer 1|
   | (owner:  |    |    +-----------+
   |  closes) |    +===>| Consumer 2|
   +----------+    |    +-----------+
        |          +===>| Consumer 3|
        |               +-----------+
        +-- defer close(ch)   <- closes when finished producing
```

---

## 8. The Memory Model: Happens-Before

This is *why* channels are a valid synchronization tool and not just a queue.
Go's memory model gives these **guarantees**:

| # | Rule                                                                                         |
|---|----------------------------------------------------------------------------------------------|
| 1 | A **send** on a channel *happens before* the corresponding **receive** completes.            |
| 2 | The **closing** of a channel *happens before* a receive that returns because it is closed.   |
| 3 | A **receive from an unbuffered** channel *happens before* the corresponding **send** completes.|
| 4 | The **k-th receive** on a channel with capacity C *happens before* the **(k+C)-th send** completes.|

What "happens before" gives you: everything the sender wrote **before** sending is
**visible** to the receiver **after** receiving.

```go
var data string
done := make(chan struct{})

go func() {
    data = "hello"      // (1) write
    close(done)         // (2) close  ----- happens-before --->
}()

<-done                  // (3) receive returns because closed
fmt.Println(data)       // (4) GUARANTEED to print "hello"
```

```
 Goroutine A                         Goroutine B
 -----------                         -----------
 data = "hello"
      |
 close(done) ----- happens-before ----> <-done returns
                                             |
                                        print(data)  // sees "hello"
```

Rule 4 is the semaphore guarantee: with `make(chan T, C)`, at most C sends can be "ahead" of receives.
Rule 3 is the surprising one: in an unbuffered channel, the *receive* happens-before the *send finishes*, which is why
unbuffered channels give lock-step behavior.

---

## 9. `select`: Multiplexing and Its Internals

`select` waits on **multiple channel operations** and proceeds with whichever is ready.

```go
select {
case v := <-ch1:         // receive ready
    handle(v)
case ch2 <- x:           // send ready
case <-time.After(2*time.Second):   // timeout
case <-ctx.Done():       // cancellation
default:                 // runs immediately if nothing is ready (non-blocking)
}
```

### Semantics

| Situation                                    | Behavior                                              |
|----------------------------------------------|-------------------------------------------------------|
| Exactly one case ready                       | That case runs                                        |
| Multiple cases ready                         | **One chosen pseudo-randomly** (fairness, no starvation)|
| None ready, has `default`                    | `default` runs (non-blocking)                         |
| None ready, no `default`                     | Goroutine **parks on ALL channels at once**           |
| `select {}` (empty)                          | Blocks forever                                        |

### Internals

```
 select { case <-a: ... case b <- x: ... case <-c: ... }

 Phase 1: randomize poll order
 Phase 2: LOCK all channels (in a global address order -> avoids deadlock between selects)
 Phase 3: scan in random order; if any ready -> perform it, unlock all, return
 Phase 4: none ready -> enqueue this goroutine on a.recvq, b.sendq, c.recvq (a "sudog" each), park
 Phase 5: when woken by ONE channel -> dequeue from all the others, return chosen case

   goroutine G
      |
      +--sudog--> a.recvq
      +--sudog--> b.sendq        G sleeps once, woken once.
      +--sudog--> c.recvq
```

### Priority trick (select has no priority)

```go
// Prefer 'quit' over 'work':
for {
    select {
    case <-quit:
        return
    default:
    }
    select {
    case <-quit:
        return
    case w := <-work:
        process(w)
    }
}
```

---

## 10. Synchronization Patterns (the toolbox)

Each pattern: **what it does -> diagram -> code -> when to use**.

### 10.1 Done signal / completion notification

```
 main                      worker
  |  go worker(done)         |
  |------------------------->| doWork()
  |  <-done  (blocks)        |
  |                          | close(done)
  |<-------------------------|
  |  continues               |
```

```go
done := make(chan struct{})
go func() { defer close(done); work() }()
<-done
```

### 10.2 Ready / start gate (starting gun)

Make N goroutines start *at the same time* (benchmarks, tests, coordinated starts).

```
                  start := make(chan struct{})

   +------+ +------+ +------+ +------+
   |  G1  | |  G2  | |  G3  | |  G4  |    all blocked on <-start
   +--+---+ +--+---+ +--+---+ +--+---+
      \        |        |       /
       \       |        |      /
        +------+--------+-----+
                  ^
            close(start)   <- everybody released together
```

```go
start := make(chan struct{})
for i := 0; i < n; i++ {
    go func() { <-start; run() }()
}
close(start)
```

### 10.3 Semaphore (bounded concurrency)

A buffered channel of capacity N is a counting semaphore with N permits.

```
 sem := make(chan struct{}, 3)       Slots: [ ][ ][ ]     (3 permits)

   acquire:  sem <- struct{}{}       fills a slot (blocks when all 3 taken)
   release:  <-sem                   frees a slot

   +----+ +----+ +----+ +----+ +----+
   | T1 | | T2 | | T3 | | T4 | | T5 |
   +-+--+ +-+--+ +-+--+ +-+--+ +-+--+
     |      |      |      |      |
     v      v      v      x      x      <- T4, T5 block until a slot frees
   +---------------------+
   |  sem  [T1][T2][T3]  |  cap=3
   +---------------------+
```

```go
sem := make(chan struct{}, 3)
for _, url := range urls {
    sem <- struct{}{}                // acquire
    go func(u string) {
        defer func() { <-sem }()     // release
        fetch(u)
    }(url)
}
```

### 10.4 Worker pool (fan-out)

```
                         jobs (chan Job)
   +----------+         +---------------+        +----------+
   | Producer |-------->|  J J J J J J  |------->| Worker 1 |--+
   +----------+         +---------------+   |    +----------+  |
                                            +--->| Worker 2 |--+--> results (chan Result)
                                            |    +----------+  |
                                            +--->| Worker 3 |--+
                                                 +----------+
```

```go
func pool(jobs <-chan Job, n int) <-chan Result {
    results := make(chan Result)
    var wg sync.WaitGroup
    wg.Add(n)
    for i := 0; i < n; i++ {
        go func() {
            defer wg.Done()
            for j := range jobs {           // exits when jobs is closed & drained
                results <- process(j)
            }
        }()
    }
    go func() { wg.Wait(); close(results) }()   // single closer, after ALL workers finish
    return results
}
```

**Multi-sender closing rule in action:** workers are many senders to `results`. None of them close it.
A dedicated goroutine waits for all, then closes once.

### 10.5 Fan-in (merge many -> one)

```
   ch1 ----+
           |
   ch2 ----+---->[ merge ]----> out
           |
   ch3 ----+
```

```go
func merge[T any](cs ...<-chan T) <-chan T {
    out := make(chan T)
    var wg sync.WaitGroup
    wg.Add(len(cs))
    for _, c := range cs {
        go func(c <-chan T) {
            defer wg.Done()
            for v := range c { out <- v }
        }(c)
    }
    go func() { wg.Wait(); close(out) }()
    return out
}
```

### 10.6 Pipeline (stages connected by channels)

```
 +--------+  ch  +-----------+  ch  +-----------+  ch  +--------+
 | Source |----->| Stage 1   |----->| Stage 2   |----->|  Sink  |
 | (read) |      | (parse)   |      | (enrich)  |      | (write)|
 +--------+      +-----------+      +-----------+      +--------+
                    each stage = goroutine(s); each owns & closes its OUTPUT channel
```

```go
func gen(nums ...int) <-chan int {
    out := make(chan int)
    go func() { defer close(out); for _, n := range nums { out <- n } }()
    return out
}
func sq(in <-chan int) <-chan int {
    out := make(chan int)
    go func() { defer close(out); for n := range in { out <- n*n } }()
    return out
}
// for v := range sq(sq(gen(1,2,3))) { fmt.Println(v) }
```

Closing cascades **downstream**: source closes -> stage 1 sees end -> closes its output -> ...
Cancellation cascades **upstream** via `context` / `done` (10.8).

### 10.7 Request-response (reply channel)

Embed a channel in the request so the server can answer a specific caller.

```
   Client                           Server goroutine (owns the state)
     |   req{key, reply:ch}  ---->    |
     |  (blocks on <-ch)              | lookup(key)
     |   <----- value on reply -------|
```

```go
type req struct{ key string; reply chan string }

func server(reqs <-chan req) {
    store := map[string]string{}         // owned ONLY by this goroutine: no mutex needed
    for r := range reqs {
        r.reply <- store[r.key]
    }
}
```

This is the **actor / monitor-goroutine** pattern: state is confined to one goroutine.

### 10.8 Cancellation with `context`

```
   ctx, cancel := context.WithCancel(parent)

            parent ctx
               |
        +------+-------+
        v              v
     ctx A          ctx B     <- cancel(parent) closes the Done() channel of every child
        |              |
    +---+---+      +---+---+
    G1     G2      G3     G4  <- all select on ctx.Done()
```

```go
func worker(ctx context.Context, in <-chan Job) error {
    for {
        select {
        case <-ctx.Done():
            return ctx.Err()            // context.Canceled / DeadlineExceeded
        case j, ok := <-in:
            if !ok { return nil }
            if err := handle(ctx, j); err != nil { return err }
        }
    }
}
```

`ctx.Done()` is just a channel that gets **closed** on cancel. That's the broadcast pattern again.

### 10.9 Or-done / safe-send (never leak on cancel)

Every **blocking send** should also listen for cancellation, or the goroutine leaks.

```go
select {
case out <- v:
case <-ctx.Done():
    return
}
```

### 10.10 Timeouts and deadlines

```go
select {
case res := <-callRemote():
    use(res)
case <-time.After(500 * time.Millisecond):
    return ErrTimeout
}
```

Prefer `context.WithTimeout` for propagation. In hot loops, reuse a `time.Timer` (`time.After` allocates each call).

### 10.11 Ticker / periodic work

```go
t := time.NewTicker(10 * time.Second)
defer t.Stop()
for {
    select {
    case <-t.C:        flushMetrics()
    case <-ctx.Done(): return
    }
}
```

### 10.12 Pub/Sub broadcast hub

```
                      +---------------------------------------+
   Publisher -------->|                 HUB                   |
   (publish chan)     |  goroutine owns subscribers map       |
                      |   +-----+  +-----+  +-----+           |
                      |   |sub A|  |sub B|  |sub C|           |
                      +---+--+--+--+--+--+--+--+--+-----------+
                             |        |        |
                             v        v        v
                          chan A    chan B    chan C   (each buffered; slow subscriber policy!)
```

```go
type Hub struct {
    pub   chan Msg
    sub   chan chan Msg
    unsub chan chan Msg
}

func (h *Hub) run() {
    subs := map[chan Msg]struct{}{}      // confined to this goroutine
    for {
        select {
        case c := <-h.sub:   subs[c] = struct{}{}
        case c := <-h.unsub: delete(subs, c); close(c)
        case m := <-h.pub:
            for c := range subs {
                select {
                case c <- m:             // deliver
                default:                 // slow consumer: DROP (or disconnect it)
                }
            }
        }
    }
}
```

**Design decision:** what do you do when a subscriber is slow? Block everyone (bad), drop messages (lossy), or disconnect (strict).

### 10.13 Barrier / rendezvous of N

All N goroutines must reach a point before any proceeds. Usually done with `sync.WaitGroup` + a gate channel:

```
   G1 ----> |
   G2 ----> |  wg.Wait() all arrived  --> close(gate) --> all proceed
   G3 ----> |
```

### 10.14 Disabling a `select` case with `nil`

```go
in1, in2 := a, b
for in1 != nil || in2 != nil {
    select {
    case v, ok := <-in1:
        if !ok { in1 = nil; continue }   // closed -> disable this case forever
        out <- v
    case v, ok := <-in2:
        if !ok { in2 = nil; continue }
        out <- v
    }
}
```

A nil channel blocks forever, so `select` never picks it. This is the idiomatic way to merge until both finish.

### 10.15 Future / promise

```go
func async(f func() int) <-chan int {
    ch := make(chan int, 1)          // buffer 1 -> sender never blocks, no leak if nobody reads
    go func() { ch <- f() }()
    return ch
}
r := async(slowCompute)
// ... do other work ...
fmt.Println(<-r)
```

### 10.16 Generator (lazy producer)

```go
func fib(ctx context.Context) <-chan int {
    ch := make(chan int)
    go func() {
        defer close(ch)
        a, b := 0, 1
        for {
            select {
            case ch <- a:       a, b = b, a+b
            case <-ctx.Done():  return
            }
        }
    }()
    return ch
}
```

### 10.17 Mutex built from a channel (educational)

```go
lock := make(chan struct{}, 1)
lock <- struct{}{}   // Lock
// critical section
<-lock               // Unlock
```

Works, but `sync.Mutex` is faster and clearer. It shows that a 1-capacity channel **is** a lock.

### 10.18 Errgroup (fail-fast group)

```go
g, ctx := errgroup.WithContext(ctx)
for _, u := range urls {
    u := u
    g.Go(func() error { return fetch(ctx, u) })   // first error cancels ctx for all
}
if err := g.Wait(); err != nil { ... }
```

### 10.19 Rate limiting with a token bucket

```
   refill goroutine (every 100ms)
          |  +1 token
          v
   +---------------------+
   | tokens chan (cap 10)|----> each request must take a token before proceeding
   +---------------------+
```

```go
tokens := make(chan struct{}, 10)
go func() {
    t := time.NewTicker(100 * time.Millisecond)
    for range t.C {
        select { case tokens <- struct{}{}: default: }   // full bucket: discard
    }
}()
// per request: <-tokens
```

### 10.20 Tee and bridge

- **tee**: duplicate one stream into two (read `in`, write to both outputs; remember both must be sent each time).
- **bridge**: flatten a `<-chan <-chan T` into a single `<-chan T`.

---

## 11. Channels vs Mutex vs WaitGroup vs Atomic vs Cond

| Tool                  | Best for                                                       | Mental model                    | Cost    |
|-----------------------|----------------------------------------------------------------|---------------------------------|---------|
| **Channel**           | Ownership transfer, pipelines, signaling, fan-in/out, cancel   | Conveyor belt / handshake       | Medium  |
| **sync.Mutex/RWMutex**| Protecting a shared struct / cache / counter map               | A single-key room               | Low     |
| **sync.WaitGroup**    | "Wait for N goroutines to finish"                              | Countdown latch                 | Low     |
| **sync/atomic**       | Single word counters, flags, lock-free reads                   | Indivisible CPU instruction     | Lowest  |
| **sync.Cond**         | Wait for a condition on shared state; Broadcast                | Waiting room + bell             | Low     |
| **sync.Once**         | One-time init                                                  | Single-shot gate                | Low     |
| **sync.Pool**         | Reusing temp objects, reducing GC                              | Object recycling bin            | Low     |
| **errgroup**          | Goroutine group with first-error cancellation                  | WaitGroup + errors + context    | Low     |

### Decision tree

```
                        Do goroutines need to talk / hand off data?
                          |                                   |
                         YES                                  NO
                          |                                   |
          Passing ownership or work items?            Protecting shared state?
              |                |                          |            |
             YES               NO                     simple counter  complex struct
              |                |                          |            |
           CHANNEL      Just signaling?                 atomic       Mutex / RWMutex
                        (done / start)                              
                          |        |
                   one-shot    many events
                   close(ch)    channel / Cond

   Just "wait until N goroutines finish"?   ->  WaitGroup (or errgroup if errors)
   "Limit concurrency to N"?                ->  buffered channel semaphore / x/sync/semaphore
   "Cancel everything"?                     ->  context
```

**Rule of thumb from the Go team's wiki:**
- Use **channels** when passing ownership of data, distributing units of work, communicating async results.
- Use **mutexes** when protecting internal state (caches, registries, counters).

If you're passing a channel around only to guard a map, a mutex is usually simpler and faster.

---

## 12. Real-World Use Cases With Architecture

### 12.1 HTTP server with graceful shutdown

```
                            OS signal (SIGTERM)
                                   |
                                   v
 +-----------------------------------------------------------------------+
 |  main goroutine                                                       |
 |   sigCh := make(chan os.Signal, 1)                                    |
 |   signal.Notify(sigCh, SIGINT, SIGTERM)                               |
 |                                                                       |
 |   select {                                                            |
 |   case <-sigCh:    srv.Shutdown(ctx)  // stop accepting, drain        |
 |   case err := <-errCh: log(err)                                       |
 |   }                                                                   |
 +--------+----------------------------------+---------------------------+
          | go srv.ListenAndServe()          | go backgroundWorkers(ctx)
          v                                  v
   +--------------+                  +---------------------+
   | HTTP server  |                  | consumers, tickers  |
   | handler per  |                  | select on ctx.Done()|
   | request (G)  |                  +---------------------+
   +--------------+
```

```go
func main() {
    ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
    defer stop()

    srv := &http.Server{Addr: ":8080", Handler: mux}
    errCh := make(chan error, 1)
    go func() { errCh <- srv.ListenAndServe() }()

    select {
    case <-ctx.Done():
        shutCtx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
        defer cancel()
        _ = srv.Shutdown(shutCtx)
    case err := <-errCh:
        log.Fatal(err)
    }
}
```

**Used by:** virtually every production Go service (Kubernetes pods get SIGTERM, then SIGKILL after the grace period).

### 12.2 Web crawler (bounded concurrency + dedupe + termination)

```
                     +--------------------------------+
   seed URLs ------->|   frontier  chan URL (buffered)|<-----------+
                     +----------------+---------------+            |
                                      |                            |
                  +-------------------+------------------+         | new links
                  v                   v                  v         |
            +-----------+       +-----------+      +-----------+   |
            | fetcher 1 |       | fetcher 2 |      | fetcher N |   |
            +-----+-----+       +-----+-----+      +-----+-----+   |
                  |                   |                  |         |
                  +---------+---------+------------------+         |
                            v                                      |
                     +-------------+    extract links    +--------+--------+
                     |  parse/     |------------------->| dedupe (visited)|
                     |  extract    |                    | goroutine-owned |
                     +------+------+                    +-----------------+
                            |
                            v
                     +-------------+
                     |  store/sink |
                     +-------------+
   Termination: track in-flight count; when 0 and frontier empty -> close.
```

**Hard part:** deciding when to stop. Solved via an in-flight counter (`WaitGroup` or an atomic) owned by a coordinator.

### 12.3 Log / metrics / event ingestion pipeline

```
 +----------+   +-----------+  batch   +------------+  retry   +-----------+
 | app logs |-->| in chan   |--------->| batcher    |--------->| shipper   |--> Elasticsearch/Kafka
 | (many G) |   | (buffered)|  (size N | goroutine  |  w/      | workers   |
 +----------+   +-----+-----+  or T ms)| select{    |  backoff | (pool)    |
                      |                 |  case<-in  |          +-----------+
          full? drop / block / sample   |  case<-tick|
                                        |  }         |
                                        +------------+
```

```go
func batcher(in <-chan Event, out chan<- []Event, size int, every time.Duration) {
    buf := make([]Event, 0, size)
    t := time.NewTicker(every)
    defer t.Stop()
    flush := func() { if len(buf) > 0 { out <- buf; buf = make([]Event, 0, size) } }
    for {
        select {
        case e, ok := <-in:
            if !ok { flush(); close(out); return }
            if buf = append(buf, e); len(buf) >= size { flush() }
        case <-t.C:
            flush()
        }
    }
}
```

**Used by:** Fluent Bit-like agents, Prometheus remote-write queues, OpenTelemetry collector pipelines, application tracing exporters.

### 12.4 Database / HTTP connection pool

```
          Get()                                      Put()
   +----------------+                          +----------------+
   |   caller G     |                          |   caller G     |
   +-------+--------+                          +--------+-------+
           | <-pool.idle                                 | pool.idle <- conn
           v                                             v
        +----------------------------------------------------+
        |  idle  chan *Conn   (buffered, cap = maxIdle)      |
        |  [conn][conn][conn][    ][    ]                    |
        +----------------------------------------------------+
        If empty: either dial a new conn (below max) or block (wait for Put).
```

Channels naturally model "a set of reusable tokens". (`database/sql` itself uses mutexes + request channels, same idea.)

### 12.5 Kubernetes-style controller (work queue + informer)

```
   API server
       |  watch stream
       v
 +-------------+  event   +----------------------+  key   +--------------------+
 |  Informer   |--------->|  Workqueue           |------->| Worker 1..N        |
 | (reflector) |          | (dedupe, rate limit, |        | reconcile(key)     |
 +-------------+          |  retry w/ backoff)   |        +----------+---------+
                          +----------------------+                   |
                                   ^                                 | on error
                                   +---------- AddRateLimited -------+
```

The **workqueue** guarantees one key is processed by only one worker at a time and coalesces duplicates. It is a synchronization abstraction built from channels, mutexes and conds.

### 12.6 Streaming / real-time (WebSockets, chat, game server)

```
   Clients                      Server
 +--------+                +-----------------------------+
 | WS  C1 |<----writeCh----|  conn goroutine pair per    |
 |        |-----readCh---->|  client (reader + writer)   |
 +--------+                +--------------+--------------+
 +--------+                               |
 | WS  C2 |<--- ... ---->            +----v-----+   broadcast  +----------+
 +--------+                           |   Hub    |------------->| room /   |
                                      | (select) |              | topic    |
                                      +----------+              +----------+
```

Each client: `readPump` + `writePump` goroutines, linked by a buffered `send` channel. If `send` is full the client is too slow -> disconnect.

### 12.7 Parallel API aggregation (scatter-gather)

```
                       +-----------+
   Request ----------->|  Handler  |
                       +-----+-----+
          +----------------+-+-----------------+
          v                v                   v
     +---------+     +----------+        +-----------+
     | users   |     | orders   |        | inventory |    each in own goroutine,
     | service |     | service  |        | service   |    same ctx with timeout
     +----+----+     +----+-----+        +-----+-----+
          |               |                    |
          +------> results chan (cap 3) <------+
                          |
                   +------v-------+
                   |  assemble    |---> Response
                   +--------------+
   If ctx deadline hits: return partial results / error; goroutines exit via ctx.
```

### 12.8 Job scheduler / cron / task runner

```
   +-----------+  due   +----------------+  job   +---------------+
   | timer wheel|------>|  dispatch chan |------->|  worker pool  |
   | / heap     |       +----------------+        |  (N workers)  |
   +-----------+                                  +-------+-------+
        ^                                                  |
        +------------ reschedule on completion ------------+
```

### 12.9 Event-driven stream processing (Kafka consumer inside a service)

```
   Kafka partitions                      Process                                 
 +---------+                  +---------------------------------------------+
 |  P0     |--- fetch --->    |  consumer G (per partition)                 |
 |  P1     |--- fetch --->    |      |                                      |
 |  P2     |--- fetch --->    |      v   keyed hash(key) % N                |
 +---------+                  |  +-------+ +-------+ +-------+              |
                              |  | chan0 | | chan1 | | chanN |   <- ordering per key preserved
                              |  +---+---+ +---+---+ +---+---+              |
                              |      v         v         v                  |
                              |   worker0   worker1    workerN              |
                              |      \         |         /                  |
                              |       +---- commit offsets (after processing)
                              +---------------------------------------------+
```

**Key insight:** hashing keys to per-worker channels gives *parallelism across keys and ordering within a key*.

### 12.10 Other real-world places you'll see this

| Domain                  | Where channels/queues synchronize work                                      |
|-------------------------|------------------------------------------------------------------------------|
| Web servers             | Accept loop -> worker handoff; request cancellation (`ctx.Done()`)           |
| Message brokers         | NATS, Redis streams consumers, RabbitMQ consumer prefetch + ack channels     |
| Observability agents    | Telemetry batching and export                                                |
| Build systems           | Parallel task graph with dependency completion signals                       |
| Game servers            | Tick loop + input channel per player                                         |
| Distributed DBs         | etcd/Raft: proposal channel -> raft goroutine -> apply channel               |
| CLI tools               | Parallel file scanning/hashing with a bounded worker pool                    |
| ML data loaders         | Prefetch pipeline: read -> decode -> augment -> batch (queue between stages) |
| Trading / finance       | Market data fan-in, order routing, single-writer order book goroutine        |
| IoT / edge              | Sensor goroutines -> aggregation channel -> uplink with backoff              |

---

## 13. Failure Modes

### 13.1 Deadlock

All goroutines are blocked forever. Go detects it **only if every goroutine is asleep**
(`fatal error: all goroutines are asleep - deadlock!`). Partial deadlocks in a running server are silent.

**Classic causes**

```go
ch := make(chan int)
ch <- 1            // no receiver exists: blocks forever (unbuffered, same goroutine)
fmt.Println(<-ch)
```

```
   Circular wait:

   G1: holds A, wants B ----+
   G2: holds B, wants A <---+      (or: G1 sends to G2 while G2 sends to G1, both unbuffered)

   +----+  ch12 <- x (blocks)   +----+
   | G1 |---------------------->| G2 |
   |    |<----------------------|    |
   +----+   ch21 <- y (blocks)  +----+   neither ever reaches its receive
```

**Fixes:** consistent ordering, buffer where justified, `select` with `ctx.Done()`, restructure so one side only receives.

### 13.2 Goroutine leak

A goroutine blocked forever on a channel nobody will serve. Memory and stack never freed.

```go
func leaky() int {
    ch := make(chan int)             // unbuffered
    go func() { ch <- compute() }()  // blocks forever if caller returns early
    select {
    case v := <-ch:               return v
    case <-time.After(time.Second): return -1   // returns; the goroutine above is STUCK
    }
}
// Fix: make(chan int, 1) so the send never blocks, or use ctx.
```

**Rule:** for every goroutine, you must be able to answer "**how does it stop?**"

### 13.3 Data race (still possible!)

Channels don't magically protect shared memory.

```go
// BUG: both goroutines mutate the same slice after "sending" it
ch <- slice
slice[0] = 99     // sender kept using it -> race with receiver
```

Fix: truly transfer ownership (don't touch after send), or send a copy.

### 13.4 Panics from misuse

| Panic                         | Cause                                              |
|-------------------------------|----------------------------------------------------|
| `send on closed channel`      | Producer sends after someone closed                |
| `close of closed channel`     | Two closers                                        |
| `close of nil channel`        | Forgot to initialize                               |

### 13.5 Starvation / unfairness

A busy producer fills the channel and slow consumers lag; or one `select` case is always ready.
Use buffering policies, separate channels per priority, and bounded queues.

### 13.6 Loop-variable capture (Go < 1.22)

```go
for _, v := range items {
    go func() { use(v) }()      // all see the last v (pre-1.22). Fix: go func(v T){...}(v)
}
```

### 13.7 Forgetting the receiver side of `range`

```go
for v := range ch { ... }   // loops forever if ch is never closed -> goroutine leak/deadlock
```

### Bug taxonomy diagram

```
                         CONCURRENCY BUGS
                                |
       +------------+-----------+------------+-------------+
       v            v           v            v             v
   Deadlock       Leak        Race        Panic        Starvation
   (all stuck)  (one stuck) (unordered) (close misuse) (unfair)
       |            |           |            |             |
    cycle in    no exit     missing      multiple      no priority /
    waits       path        happens-     closers /     unbounded
                            before       send-after-   queue
                                         close
```

---

## 14. Backpressure and Flow Control

**Backpressure** = a slow consumer tells fast producers to slow down. Bounded channels provide it for free:
when the buffer fills, `send` blocks, which slows the producer, which slows *its* source, etc.

```
   Fast source ===> [ buffer FULL ] ===> Slow consumer
        ^                  |
        |  send BLOCKS     |
        +------------------+  pressure propagates upstream

   Unbounded queue (e.g. append to slice forever):
   Fast source ===> [ queue grows ... OOM ] ===> Slow consumer     <- production outage
```

### Policies when full

| Policy             | Code shape                                  | Use when                          |
|--------------------|---------------------------------------------|-----------------------------------|
| **Block**          | `ch <- v`                                   | Lossless, producer can wait       |
| **Drop newest**    | `select { case ch <- v: default: }`         | Metrics, telemetry, best-effort   |
| **Drop oldest**    | pop one then push (needs care/ring buffer)  | "Latest value matters" (sensors)  |
| **Timeout**        | `select { case ch<-v: case <-time.After(d): }` | Bounded wait, then error       |
| **Reject (shed load)** | return 429/503 immediately              | Public APIs under overload        |
| **Sample**         | forward 1 of N                              | High-volume logging               |

**Mental rule:** *Every queue in a system must be bounded, and you must decide what happens when it is full.*

### Buffer sizing intuition

Little's Law: `L = lambda x W` (items in system = arrival rate x time in system).
If bursts are 500 items and drain takes 100 ms, a buffer of ~500 absorbs the burst. A buffer of 1,000,000
isn't "safer"; it's latency and memory you don't control.

---

## 15. Performance Characteristics

Rough orders of magnitude (hardware-dependent; measure your own):

| Operation                               | Approx cost                 |
|-----------------------------------------|-----------------------------|
| Atomic add                              | ~1-5 ns                     |
| Uncontended mutex lock/unlock           | ~10-25 ns                   |
| Buffered channel send+recv (no blocking)| ~50-100 ns                  |
| Unbuffered handoff w/ goroutine switch  | ~150-300+ ns                |
| Goroutine creation                      | ~0.3-1 us (2 KB initial stack) |
| OS thread context switch                | ~1-5 us                     |

**Takeaways**

- Channels are plenty fast for I/O-bound and coarse-grained work.
- For **tight loops with millions of tiny items**, batch items (send `[]T` not `T`) or use a mutex/atomic.
- Contention on one hot channel with many goroutines serializes on its internal lock. Shard channels (per-worker queues).
- `time.After` in a loop allocates timers: reuse `time.Timer` or use `context`.
- `select` with many cases costs more (locks all channels).

```
   One hot channel (contention):          Sharded channels (scales):

   G G G G G G                            G G -> [ch0] -> W0
        \ | /                             G G -> [ch1] -> W1
       [ ch ]                             G G -> [ch2] -> W2
        / | \                             (hash or round-robin)
   W W W W W W
```

---

## 16. Testing and Debugging

### Tools

| Tool                                   | What it finds                                         |
|----------------------------------------|-------------------------------------------------------|
| `go test -race` / `go run -race`       | Data races (instrumented memory accesses)             |
| `go vet`                               | Copying locks, bad `Printf`, some misuse              |
| `runtime.NumGoroutine()`               | Leak detection in tests (before/after)                |
| `go.uber.org/goleak`                   | Fails tests that leave goroutines behind              |
| `pprof` goroutine profile (`/debug/pprof/goroutine?debug=2`) | See who is blocked and **on what** |
| `GOTRACEBACK=all`, send `SIGQUIT`      | Dump all goroutine stacks                             |
| `go tool trace`                        | Scheduler timeline, blocking events                   |
| `testing/synctest` (newer Go)          | Deterministic testing of time-dependent concurrent code|

### Reading a goroutine dump

```
goroutine 18 [chan send, 10 minutes]:        <- state and how long stuck
main.worker(...)
        /app/worker.go:42 +0x65              <- exact line blocked on channel send
```

Common states: `chan send`, `chan receive`, `select`, `select (no cases)`, `semacquire` (mutex/WaitGroup), `sleep`, `IO wait`.
`[chan receive (nil chan)]` is a nil-channel bug.

### Testing practices

1. Never use `time.Sleep` to "wait for goroutines". Use channels / `WaitGroup` / `Eventually`.
2. Always run CI with `-race`.
3. Add `goleak.VerifyNone(t)` to catch leaks.
4. Use a timeout in tests: `select { case <-done: case <-time.After(2*time.Second): t.Fatal("hang") }`.
5. Inject clocks / tickers so time can be controlled.

---

## 17. The Same Idea in Other Languages and Systems

| Language / System    | Channel-like primitive                              | Notes                                                           |
|----------------------|-----------------------------------------------------|-----------------------------------------------------------------|
| **Go**               | `chan T`                                            | Built-in; `select`; goroutines                                  |
| **Rust**             | `std::sync::mpsc`, `crossbeam-channel`, `tokio::sync::mpsc/oneshot/broadcast/watch` | Ownership enforced by the type system; `Send` trait |
| **Kotlin**           | `Channel<T>`, `Flow`, `select {}`                   | Coroutines; rendezvous / buffered / conflated / unlimited       |
| **Java**             | `BlockingQueue` (`ArrayBlockingQueue`, `SynchronousQueue`), `Exchanger`, `Flow.Publisher` | `SynchronousQueue` = unbuffered |
| **C#**               | `System.Threading.Channels`, `BlockingCollection`   | Bounded/unbounded, async-friendly                               |
| **Erlang/Elixir**    | Process mailboxes + `receive`                       | Actor model: each process has an *inbox* (channel per actor)    |
| **Clojure**          | `core.async` channels                               | Go-style `go` blocks and `alts!`                                |
| **Python**           | `queue.Queue`, `asyncio.Queue`, `multiprocessing.Queue/Pipe` | Thread / async / process variants                      |
| **JavaScript/Node**  | Streams, `EventEmitter`, `MessageChannel`, async iterators | Backpressure via stream `highWaterMark`                  |
| **Swift**            | `AsyncStream`, actors                               | Structured concurrency                                          |
| **Unix**             | Pipes, sockets, `select/poll/epoll`                 | The original channels: bounded kernel buffer + blocking writes  |
| **Distributed**      | Kafka topics, RabbitMQ queues, SQS, NATS            | Channels across machines; adds durability, partitions, delivery semantics |

### Local channel vs distributed message queue

```
   IN-PROCESS CHANNEL                       DISTRIBUTED QUEUE (Kafka/SQS/NATS)
   ------------------                       ----------------------------------

   Same memory, same failure domain         Different machines, independent failures
   Exactly-once, in-order (trivially)       At-most / at-least / effectively-once
   Lost on crash                            Durable on disk, replayable
   ns-us latency                            ms latency
   No serialization                         Serialize/deserialize (JSON/Protobuf)
   Backpressure = block                     Backpressure = lag / consumer groups
   Deadlock detection by runtime            Timeouts, retries, DLQs, idempotency
```

**The conceptual bridge:** Kafka partition ~ ordered channel; consumer group ~ worker pool over channels; offset commit ~ ack.
If you understand channel synchronization, you already understand 80% of messaging systems; the other 20% is *failure handling*.

### The Actor model (cousin of CSP)

```
   CSP (Go):                                   Actor (Erlang/Akka):
   anonymous goroutines talk via NAMED        named actors, each with ONE inbox;
   channels (channel is the first-class       you send to an actor's address
   thing).                                    (actor is the first-class thing).

   G1 --ch--> G2                              A1 ----> [ A2 | mailbox ]
```

---

## 18. Other Meaning: Channel Sync in Telecom / Media

If you meant "channel synchronization" in a different field, the shared idea is **aligning multiple channels in time**:

| Domain                        | Meaning                                                                                  |
|-------------------------------|------------------------------------------------------------------------------------------|
| **Telecom (TDMA/GSM)**        | Slots/frames on a shared medium; receivers lock onto sync words/training sequences to know where a frame starts |
| **Digital comms (PLL, clock recovery)** | Receiver aligns its clock to the transmitter's symbol timing and carrier phase   |
| **Multichannel audio/video**  | Keeping audio, video, subtitles in lip-sync (PTS/DTS timestamps, A/V sync)              |
| **Multi-channel EEG/sensors** | Aligning timestamps across sensors via shared clock/trigger                              |
| **Distributed systems**       | Clock sync (NTP/PTP), logical clocks (Lamport/vector) to order events across nodes       |
| **Network multipath**         | Bonded links (MPTCP): reorder packets arriving over different channels                   |

```
   TDMA frame (time-division):
   |<-------------------- one frame -------------------->|
   +------+--------+--------+--------+--------+---------+
   | SYNC | slot 1 | slot 2 | slot 3 | slot 4 | guard   |
   +------+--------+--------+--------+--------+---------+
      ^
      receiver correlates against a known sync pattern to find the frame boundary,
      then every user knows exactly when its slot starts.

   A/V sync:
   Audio  : --|A1|--|A2|--|A3|--|A4|--       Each packet carries a presentation
   Video  : ----|V1|-----|V2|-----|V3|--     timestamp (PTS); the player waits/
   Clock  : ----t1--------t2--------t3-->     drops so both align to one master clock.
```

Same principle as software channels: **establish a common reference for ordering** so independent streams don't drift.
If this section is what you needed, ask and I'll write a dedicated deep dive.

---

## 19. Decision Framework and Cheat Sheet

### The mental checklist (ask in this order)

1. **What is shared, and who owns it?** Can I make exactly one goroutine own it? -> channel/actor.
2. **What is the lifecycle?** How does each goroutine *start* and *stop*? (If you can't answer, you have a leak.)
3. **Who closes each channel?** Exactly one party: the sender (or a coordinator for many senders).
4. **What happens when the channel is full / empty / closed / nil?** (Section 6.)
5. **What happens on cancellation / timeout?** Every blocking op should have an exit.
6. **Where is the backpressure, and what is the overflow policy?** (Section 14.)
7. **What are the error paths?** First error cancels others? Collect all? Retry?
8. **Is a channel the simplest tool?** Maybe a mutex or WaitGroup is clearer.

### Channel cheat sheet

```go
ch := make(chan T)         // unbuffered: rendezvous
ch := make(chan T, n)      // buffered: n slots
ch <- v                    // send
v := <-ch                  // receive
v, ok := <-ch              // receive + closed check
close(ch)                  // sender announces "no more"
for v := range ch {}       // receive until closed & drained
select { case ... default: }   // multiplex / non-blocking
var in  <-chan T           // receive-only
var out chan<- T           // send-only
```

### The 10 commandments

1. Only senders close; never close from the receiver side.
2. Every goroutine needs a defined exit path.
3. Every blocking send/receive in a long-lived goroutine should be in a `select` with `ctx.Done()`.
4. Prefer unbuffered until you can justify a buffer size.
5. Sending a pointer = transferring ownership. Don't touch it afterwards.
6. `close` broadcasts; a send wakes one.
7. Nil channels block forever. Use that to disable `select` cases, and avoid it by accident.
8. Always bound your queues. Decide the overflow policy.
9. Run tests with `-race` and check for goroutine leaks.
10. If a mutex is simpler, use the mutex.

### One-page mental map

```
                         CHANNEL SYNCHRONIZATION
                                   |
     +------------------+----------+----------+----------------------+
     |                  |                     |                      |
  WHAT IT IS        HOW IT WORKS          PATTERNS               FAILURE MODES
     |                  |                     |                      |
  typed pipe +     ring buffer +        done/start gate        deadlock
  sync point       sendq/recvq +        semaphore              leak
  (CSP)            lock +               worker pool            race on shared ptr
                   happens-before       fan-in/out             close panics
                                        pipeline               starvation
     |                  |               pub/sub                    |
     |                  |               request/reply              |
  ownership         unbuffered =        context cancel          FIX WITH:
  transfer          handshake           rate limit              ownership rules,
                    buffered =          timeout/ticker          select+ctx, bounded
                    decoupling                                  queues, -race, goleak
```

---

## 20. Practice Exercises

Build these to cement the mental model (roughly in increasing difficulty):

1. **Ping-pong**: two goroutines alternate printing "ping"/"pong" using only unbuffered channels.
2. **Ordered printing**: three goroutines print `A`, `B`, `C` repeatedly in strict order for 10 rounds.
3. **Bounded downloader**: download 100 URLs with at most 5 concurrent, collect results in order.
4. **Pipeline with cancellation**: `read -> transform -> write`, cancel mid-run without leaking goroutines (verify with `goleak`).
5. **Rate limiter**: allow 10 req/s with bursts of 20 using a token-bucket channel.
6. **Pub/sub hub** with slow-subscriber policy of "drop after buffer full, disconnect after 3 drops".
7. **Dining philosophers** without deadlock (try: channel-based waiter/semaphore for N-1 seats).
8. **Merge with nil-disabling**: merge two channels until both close, using the nil-channel trick.
9. **Graceful shutdown**: HTTP server + background worker + ticker; on SIGTERM drain in-flight jobs within a 10 s deadline.
10. **Keyed worker pool**: preserve per-key ordering while processing different keys in parallel (hash to N channels).
11. **Debug challenge**: take a program with a leaked goroutine and a double-close panic; find them using `-race`, pprof, and goroutine dumps.
12. **Rebuild** a `sync.WaitGroup`, a `sync.Mutex`, and a `Once` using only channels. Then explain why the stdlib versions are better.

---

### Further reading

- Tony Hoare, *Communicating Sequential Processes* (1978/1985)
- Rob Pike, *Go Concurrency Patterns* and *Concurrency Is Not Parallelism* (talks)
- *The Go Memory Model* (go.dev/ref/mem)
- Sameer Ajmani, *Advanced Go Concurrency Patterns*
- Katherine Cox-Buday, *Concurrency in Go* (O'Reilly)
- Go source: `runtime/chan.go`, `runtime/select.go`
- Dave Cheney's articles on channel axioms and goroutine lifecycle

---

*Core idea: a channel is a lock, a queue, and a wait list wearing a clean contract. Ownership transfer plus
happens-before is what lets you reason about concurrent code as if it were sequential.*
