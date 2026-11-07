# Channels: The Complete Mental-Model Guide

> Primary subject: **Go channels** (the most widely used and most misunderstood implementation).
> Section 17 covers other things called "channels" (Rust, Kotlin, Django Channels, etc.) so the idea transfers.

---

## Table of Contents

1. [What a Channel Really Is (Mental Model)](#1-what-a-channel-really-is-mental-model)
2. [Why Channels Exist: CSP and Go's Philosophy](#2-why-channels-exist-csp-and-gos-philosophy)
3. [Syntax and Core Operations](#3-syntax-and-core-operations)
4. [Unbuffered vs Buffered Channels](#4-unbuffered-vs-buffered-channels)
5. [Channel Internals: The Real Architecture](#5-channel-internals-the-real-architecture)
6. [Channel State Table: nil, open, closed](#6-channel-state-table-nil-open-closed)
7. [Closing Channels: Rules and Ownership](#7-closing-channels-rules-and-ownership)
8. [`range`, comma-ok, and Directional Channels](#8-range-comma-ok-and-directional-channels)
9. [`select`: Multiplexing](#9-select-multiplexing)
10. [The Go Memory Model and Channels](#10-the-go-memory-model-and-channels)
11. [Concurrency Patterns (with code)](#11-concurrency-patterns-with-code)
12. [Real-World Use Cases and Architectures](#12-real-world-use-cases-and-architectures)
13. [Channels vs Mutex vs Atomic vs WaitGroup vs Cond](#13-channels-vs-mutex-vs-atomic-vs-waitgroup-vs-cond)
14. [Pitfalls, Deadlocks, Leaks](#14-pitfalls-deadlocks-leaks)
15. [Testing, Debugging, Observability](#15-testing-debugging-observability)
16. [Performance and Sizing](#16-performance-and-sizing)
17. [Channels in Other Ecosystems](#17-channels-in-other-ecosystems)
18. [Decision Framework and Cheat Sheet](#18-decision-framework-and-cheat-sheet)
19. [Practice Exercises](#19-practice-exercises)

---

## 1. What a Channel Really Is (Mental Model)

A channel is **a typed, thread-safe conduit that lets goroutines pass values to each other and synchronize at the same time.**

Hold two ideas together:

| Idea | Meaning |
|---|---|
| **Data transfer** | A value goes in one end and comes out the other (like a queue/pipe). |
| **Synchronization** | The act of sending/receiving can *block*, so it coordinates *when* goroutines proceed. |

That second property is what separates a channel from a plain `[]T` guarded by a lock. **A channel is a queue plus a rendezvous/blocking protocol, implemented by the runtime scheduler.**

### Three mental pictures

**Picture A: A pipe between two workers**

```
 Goroutine P                                        Goroutine C
 (producer)                                         (consumer)
 +----------+     ch <- v      +-----------+      +----------+
 |          | ---------------> |  channel  | ---> |          |
 |  makes   |                  |  (queue + |      |  uses    |
 |  data    |                  |   locks + |      |  data    |
 +----------+                  |   waiters)|      +----------+
                               +-----------+      v := <-ch
```

**Picture B: A handoff (unbuffered)**
Two people must both be present to pass a baton. Whoever arrives first waits.

**Picture C: A mailbox with N slots (buffered)**
The sender only waits when the mailbox is full; the receiver only waits when it is empty.

### One-sentence rules to memorize

1. Sending to an **unbuffered** channel blocks until a receiver is ready (and vice versa).
2. Sending to a **buffered** channel blocks only when the buffer is **full**.
3. Receiving blocks when the buffer is **empty** (and the channel isn't closed).
4. Receiving from a **closed** channel never blocks: it drains the buffer, then yields zero values.
5. Sending on a **closed** channel **panics**.
6. A **nil** channel blocks forever on send and receive.
7. Channels are **reference types**: copying a channel variable copies a pointer to the same channel.

---

## 2. Why Channels Exist: CSP and Go's Philosophy

Channels come from **CSP (Communicating Sequential Processes)**, a 1978 model by Tony Hoare: independent sequential processes that interact **only by message passing** instead of shared mutable state.

Go's slogan:

> *"Do not communicate by sharing memory; instead, share memory by communicating."*

### Two styles of concurrency

```
 SHARED-MEMORY STYLE                       MESSAGE-PASSING STYLE (CSP)

 +--------+      +--------+                +--------+  msg   +--------+
 | Thread |      | Thread |                | Gorou- | -----> | Gorou- |
 |   A    |      |   B    |                | tine A |        | tine B |
 +---+----+      +----+---+                +--------+        +--------+
     |                |                       owns data         owns data
     v                v                       *after* receiving it
 +-----------------------------+
 |   Shared variable + Mutex   |            Ownership TRANSFERS with the message.
 |   (everyone must remember   |            Only one goroutine touches the data
 |    to lock correctly)       |            at a time, by convention.
 +-----------------------------+
```

**Key insight, "ownership transfer":** when you send a pointer or slice over a channel, you promise to stop touching it. The receiver now owns it. This convention removes whole classes of data races. Go does not enforce it, so *you* must.

### Why goroutines + channels are cheap

- Goroutines start with a ~2 KB stack (growable), versus ~1 MB+ for OS threads.
- The Go runtime scheduler (G-M-P model) multiplexes goroutines onto few OS threads.
- Blocking on a channel parks the *goroutine*, not the OS thread, so the thread runs another goroutine.

```
  G = goroutine   M = OS thread   P = logical processor (holds run queue)

     P0 run queue         P1 run queue         P2 run queue
   +---+---+---+        +---+---+            +---+
   |G1 |G2 |G3 |        |G4 |G5 |            |G6 |
   +---+---+---+        +---+---+            +---+
       |                   |                    |
       v                   v                    v
      M0 (thread)         M1 (thread)          M2 (thread)
       |                   |                    |
       +-------- OS kernel schedules threads ---+

  When G1 blocks on a channel: G1 is PARKED (removed from run queue),
  M0 immediately picks G2. No OS thread is blocked.
```

---

## 3. Syntax and Core Operations

### Declaration and creation

```go
var ch chan int            // nil channel (zero value!), unusable until made
ch = make(chan int)        // unbuffered
ch2 := make(chan string, 8) // buffered, capacity 8
done := make(chan struct{}) // signal-only channel (struct{} uses 0 bytes)
```

### Send, receive, close

```go
ch <- 42          // send (blocks if no receiver / buffer full)
v := <-ch         // receive
v, ok := <-ch     // receive with status: ok == false means closed AND drained
<-ch              // receive and discard (often used for signals)
close(ch)         // only a sender/owner should call this
```

### Length and capacity

```go
len(ch2) // number of queued elements right now
cap(ch2) // buffer capacity (0 for unbuffered)
```

`len` is a **snapshot**; by the time you act on it, it may be stale. Never use it for synchronization decisions.

### Channels of anything

```go
chan int                    // ints
chan *Job                   // pointers (beware shared mutation)
chan error                  // errors
chan chan int               // channel of channels (reply-channel pattern)
chan func()                 // closures to execute elsewhere
<-chan T                    // receive-only
chan<- T                    // send-only
```

### Minimal example

```go
package main

import "fmt"

func main() {
    ch := make(chan string)

    go func() {
        ch <- "hello from goroutine" // blocks until main receives
    }()

    msg := <-ch // blocks until goroutine sends
    fmt.Println(msg)
}
```

---

## 4. Unbuffered vs Buffered Channels

### Unbuffered (`make(chan T)`): synchronous rendezvous

Sender and receiver must meet. The value is handed **directly** from sender to receiver.

```
 Time ->

 Sender   :  ch <- v ...... [BLOCKED] ............. handoff ........ continues
 Receiver :  ............. <-ch (arrives) -------> handoff ........ continues

 If the receiver arrives first, the receiver blocks instead; symmetrical.
```

**Guarantee:** when the send completes, a receiver *has* the value. This is a strong sync point (a "happens-before" edge, see section 10).

### Buffered (`make(chan T, N)`): decoupled by up to N items

```
 cap = 3

 Sender        +-------------------------+        Receiver
 ch <- a  -->  | a |   |   |             |  -->   (not yet receiving)
 ch <- b  -->  | a | b |   |             |
 ch <- c  -->  | a | b | c |   FULL      |
 ch <- d  -->  BLOCKS until receiver takes 'a'
               +-------------------------+
```

**Guarantee:** a successful send only means "it fit in the queue", *not* that anyone processed it.

### When to pick which

| Situation | Choose |
|---|---|
| You need handoff / "I know it was received" semantics | **Unbuffered** |
| Signaling (done/quit) | Unbuffered or closed channel |
| Absorb short bursts between producer/consumer | **Buffered** (small, justified) |
| Producer must never block (e.g. one-shot result with a goroutine that might exit early) | Buffered of size 1 |
| Counting semaphore (limit concurrency to N) | Buffered of size N |
| "I'm not sure what size to choose" | Start with **0 or 1**; measure before enlarging |

### The most important buffered-channel warning

> **A bigger buffer hides a throughput problem; it doesn't fix it.**
> If consumers are slower than producers on average, any finite buffer eventually fills. A large buffer just delays the symptom and increases memory and latency. Real fix: more consumers, faster consumers, batching, or **backpressure** (blocking the producer on purpose).

### Backpressure: why blocking is a feature

```
 Fast producer --> [ bounded channel ] --> Slow consumer

   Producer blocks when the channel is full
   => Producer's speed is automatically throttled to consumer's speed
   => Memory stays bounded
```

Unbounded queues (e.g. an ever-growing slice) give you out-of-memory crashes under load. Bounded channels give you predictable behavior.

---

## 5. Channel Internals: The Real Architecture

Understanding the runtime structure makes behavior predictable. The core is `hchan` (in `runtime/chan.go`).

### The `hchan` struct (simplified)

```go
type hchan struct {
    qcount   uint           // number of elements currently in the buffer
    dataqsiz uint           // buffer capacity (cap)
    buf      unsafe.Pointer // pointer to ring buffer array
    elemsize uint16         // size of each element
    closed   uint32         // 0 = open, 1 = closed
    elemtype *_type         // element type info
    sendx    uint           // next write index in ring buffer
    recvx    uint           // next read index in ring buffer
    recvq    waitq          // list of goroutines blocked on receive
    sendq    waitq          // list of goroutines blocked on send
    lock     mutex          // protects ALL fields above
}
```

Yes, **a channel contains a mutex**. Channels are not lock-free; they are a well-optimized locked queue plus goroutine parking.

### Architecture diagram

```
 make(chan int, 5)  ==>  allocates one hchan (+ buffer)

 +----------------------------------------------------------------------+
 |                               hchan                                  |
 |                                                                      |
 |  lock     : mutex  (every send/recv/close takes it)                  |
 |  closed   : 0                                                        |
 |  qcount   : 3        dataqsiz : 5        elemsize : 8                |
 |                                                                      |
 |  buf (ring buffer):                                                  |
 |      index:    0     1     2     3     4                             |
 |             +-----+-----+-----+-----+-----+                          |
 |             |  A  |  B  |  C  |     |     |                          |
 |             +-----+-----+-----+-----+-----+                          |
 |                ^                 ^                                   |
 |             recvx=0           sendx=3                                |
 |         (next to read)    (next to write)                            |
 |                                                                      |
 |  recvq : [ sudog(G7) ] -> [ sudog(G9) ] -> nil   (waiting receivers) |
 |  sendq : nil                                      (waiting senders)  |
 +----------------------------------------------------------------------+
```

`sudog` is the runtime's "pseudo-goroutine" wrapper: it records *which goroutine* is waiting and *where to put/get the value*.

**Note:** when `qcount > 0`, `recvq` is empty (receivers would have taken data). When `qcount < dataqsiz`, `sendq` is empty. Both queues can't be non-empty for a buffered channel at once.

### The ring buffer wraps around

```
 After more traffic (cap=5): sendx wrapped around to 1, recvx = 3

      index:    0     1     2     3     4
             +-----+-----+-----+-----+-----+
             |  F  |     |     |  D  |  E  |
             +-----+-----+-----+-----+-----+
                       ^           ^
                    sendx=1     recvx=3
   Reading order: D (3) -> E (4) -> F (0) -> ...
   Indices advance modulo dataqsiz.
```

### Send algorithm (`chansend`)

```
                        ch <- v
                           |
                 +---------v----------+
                 |   ch == nil ?      |--yes--> block forever (park, never woken)
                 +---------+----------+
                           | no
                    lock(ch.lock)
                           |
                 +---------v----------+
                 |   ch.closed ?      |--yes--> unlock; PANIC "send on closed channel"
                 +---------+----------+
                           | no
                 +---------v----------+
                 | waiting receiver   |--yes--> copy v DIRECTLY into receiver's
                 | in recvq ?         |         stack slot, mark receiver runnable
                 +---------+----------+         (ready), unlock, return
                           | no
                 +---------v----------+
                 | qcount < dataqsiz? |--yes--> copy v into buf[sendx], sendx++,
                 | (buffer has room)  |         qcount++, unlock, return
                 +---------+----------+
                           | no (full or unbuffered)
                 +---------v----------+
                 | enqueue sudog in   |
                 | sendq; PARK this   |  <-- goroutine sleeps; scheduler runs others
                 | goroutine (gopark) |
                 +--------------------+
                           |
              (later) a receiver dequeues us, copies our value,
              and marks our goroutine runnable. We resume.
```

### Receive algorithm (`chanrecv`)

```
                        v := <-ch
                           |
                 +---------v----------+
                 |   ch == nil ?      |--yes--> block forever
                 +---------+----------+
                           | no
                    lock(ch.lock)
                           |
                 +---------v----------+
                 | closed AND         |--yes--> unlock; return zero value, ok=false
                 | qcount == 0 ?      |
                 +---------+----------+
                           | no
                 +---------v----------+
                 | waiting sender     |--yes--> unbuffered: copy value straight from
                 | in sendq ?         |         the sender. buffered: take head of
                 +---------+----------+         buffer, put sender's value at tail.
                           | no                 Wake sender. unlock; return.
                 +---------v----------+
                 | qcount > 0 ?       |--yes--> copy buf[recvx] out, recvx++,
                 +---------+----------+         qcount--, unlock, return
                           | no
                 +---------v----------+
                 | enqueue sudog in   |
                 | recvq; PARK        |
                 +--------------------+
```

### Direct handoff optimization (unbuffered)

```
   Receiver G2 already parked in recvq with a pointer to its local variable `v`

   Sender G1:   ch <- 42
        |
        +--> finds G2 in recvq
        +--> memmove(&G2.v, &42)      <-- writes DIRECTLY into G2's stack
        +--> goready(G2)              <-- G2 put on a run queue
        +--> G1 continues immediately (no blocking!)

   No intermediate buffer copy. That is why unbuffered handoff is quite cheap.
```

### How `close(ch)` works

```
 close(ch):
   lock
   if ch == nil          -> panic("close of nil channel")
   if ch.closed          -> panic("close of closed channel")
   ch.closed = 1
   release ALL goroutines in recvq  (they get zero value, ok=false)
   release ALL goroutines in sendq  (they will PANIC: "send on closed channel")
   unlock
```

This is why `close` works as a **broadcast**: one `close` wakes *every* waiting receiver. You cannot do that with a normal send.

### How `select` works (high level)

```
 select { case <-a: ...; case b <- x: ...; default: ... }

 1. Evaluate all channel expressions & send values (once, in source order).
 2. Shuffle the cases into a RANDOM poll order (fairness, avoids starvation).
 3. Lock ALL involved channels in a consistent order (by address) to avoid deadlock.
 4. Pass 1: scan in poll order; if any case is READY -> execute it, unlock, done.
 5. If none ready and `default` exists -> run default.
 6. Otherwise: enqueue a sudog on EVERY channel's queue, park the goroutine.
 7. When one channel wakes us: dequeue our sudogs from all the others, run that case.
```

Consequence: **if multiple cases are ready, Go picks one pseudo-randomly**. Never rely on case order for priority.

---

## 6. Channel State Table: nil, open, closed

Memorize this table; it explains most channel bugs.

| Operation | **nil** channel | **open** channel | **closed** channel |
|---|---|---|---|
| `ch <- v` (send) | blocks forever | sends / blocks if full | **panic** |
| `<-ch` (receive) | blocks forever | receives / blocks if empty | drains buffer, then zero value (`ok=false`) |
| `close(ch)` | **panic** | closes | **panic** |
| `len(ch)` / `cap(ch)` | 0 / 0 | current / capacity | remaining items / capacity |

### Why would anyone want a nil channel?

Because a nil channel **blocks forever**, inside `select` it effectively **disables that case**:

```go
// Merge two channels until both are closed
func merge(a, b <-chan int, out chan<- int) {
    for a != nil || b != nil {
        select {
        case v, ok := <-a:
            if !ok { a = nil; continue } // disable this case
            out <- v
        case v, ok := <-b:
            if !ok { b = nil; continue }
            out <- v
        }
    }
    close(out)
}
```

Without setting to `nil`, a closed channel would be "ready" forever and the loop would spin at 100% CPU.

---

## 7. Closing Channels: Rules and Ownership

### What closing means

`close(ch)` means: **"No more values will ever be sent."** It is a message *from the sender side to receivers*. It is **not** resource cleanup. Unclosed channels are garbage-collected like anything else.

### You do NOT have to close every channel

Close only when the receiver needs to know "no more data" (e.g. `for range`, or to broadcast). A channel used purely for signals may never need closing.

### The golden rules

1. **Only the sender (or the single owner) closes.** Never the receiver.
2. **Never close from multiple goroutines** without coordination.
3. **Never send after close.** Ensure all senders have finished first.
4. **Don't close "just in case".** Closing twice panics.

### The multi-sender problem

With N senders, who closes? Answer: a coordinator after all senders finish.

```go
func fanIn(inputs ...<-chan int) <-chan int {
    out := make(chan int)
    var wg sync.WaitGroup
    wg.Add(len(inputs))

    for _, in := range inputs {
        go func(c <-chan int) {
            defer wg.Done()
            for v := range c {
                out <- v
            }
        }(in)
    }

    // Single closer: waits for ALL senders, then closes once
    go func() {
        wg.Wait()
        close(out)
    }()
    return out
}
```

```
   in1 --> [sender goroutine 1] --\
   in2 --> [sender goroutine 2] ----> out ---> consumer
   in3 --> [sender goroutine 3] --/
                  |
           WaitGroup counts them;
           closer goroutine does close(out) exactly once
```

### Closed-channel broadcast (done/quit pattern)

```go
done := make(chan struct{})

for i := 0; i < 5; i++ {
    go func(id int) {
        for {
            select {
            case <-done:          // ALL 5 wake up when done is closed
                return
            default:
                doWork(id)
            }
        }
    }(i)
}

close(done) // one call stops every worker
```

### Safe "close once" helper

```go
type Signal struct {
    once sync.Once
    ch   chan struct{}
}
func NewSignal() *Signal { return &Signal{ch: make(chan struct{})} }
func (s *Signal) Fire()          { s.once.Do(func() { close(s.ch) }) }
func (s *Signal) Done() <-chan struct{} { return s.ch }
```

---

## 8. `range`, comma-ok, and Directional Channels

### `for range` over a channel

```go
for v := range ch {   // receives until ch is closed AND drained
    fmt.Println(v)
}
```

If nobody ever closes `ch`, this loop **blocks forever** (a classic leak/deadlock source).

### comma-ok

```go
v, ok := <-ch
if !ok {
    // channel closed and empty
}
```

Use it when you must distinguish "real zero value" from "closed".

### Directional channel types (compile-time safety)

```go
func producer(out chan<- int)  { out <- 1 }       // can only send
func consumer(in  <-chan int)  { v := <-in; _ = v } // can only receive

ch := make(chan int)
go producer(ch)   // chan int converts implicitly to chan<- int
consumer(ch)      // and to <-chan int
```

```
   bidirectional chan int
        |
        +--> chan<- int   (send-only view)   <- give to producers
        +--> <-chan int   (receive-only view) <- give to consumers
   (cannot convert back)
```

**Why it matters:** the type signature documents *who owns the close*. A function that only has `<-chan T` cannot close it. The compiler enforces that.

---

## 9. `select`: Multiplexing

`select` waits on **multiple channel operations** and runs whichever is ready.

```go
select {
case msg := <-inbox:
    handle(msg)
case out <- result:
    // sent successfully
case <-time.After(2 * time.Second):
    // timeout
case <-ctx.Done():
    return ctx.Err()
default:
    // nothing ready right now (non-blocking)
}
```

### Behavior summary

| Scenario | Result |
|---|---|
| One case ready | that case runs |
| Several ready | **one chosen pseudo-randomly** |
| None ready, has `default` | `default` runs immediately (non-blocking) |
| None ready, no `default` | blocks until one is ready |
| `select {}` (empty) | blocks forever (used to park `main`) |

### Non-blocking send / receive

```go
select {
case ch <- v:
    // sent
default:
    // would have blocked; drop or handle overflow
}
```

This is how you implement **load shedding** (drop work when saturated).

### Timeouts

```go
select {
case res := <-resultCh:
    use(res)
case <-time.After(500 * time.Millisecond):
    return errors.New("timeout")
}
```

**Pitfall (pre-Go 1.23):** `time.After` in a hot loop allocates a timer each iteration that stays alive until it fires. Prefer a reusable `time.NewTimer` + `Reset`, or `context.WithTimeout`. Since **Go 1.23** (when the module's `go` directive is 1.23+), unreferenced timers are garbage-collected and timer channels are unbuffered-style (no stale values after `Reset`/`Stop`), which removes much of this pain. Don't rely on it unless you know your Go version.

### Priority among channels (select has none, so build it)

```go
for {
    select {
    case <-quit:              // high priority check first
        return
    default:
    }
    select {                  // then normal work
    case <-quit:
        return
    case job := <-jobs:
        process(job)
    }
}
```

### `context` is built on channels

`ctx.Done()` returns a `<-chan struct{}` that is **closed** on cancellation or timeout, the broadcast pattern from section 7.

```
   ctx, cancel := context.WithTimeout(parent, 2s)

        parent ctx
           |
           +--> child ctx ---- Done() chan (closed on cancel/timeout/parent cancel)
                                   |
                  +----------------+-----------------+
                  v                v                 v
              worker 1         worker 2          worker 3
              select{<-ctx.Done()} ... all unblock at once
```

---

## 10. The Go Memory Model and Channels

Concurrency correctness depends on **happens-before** guarantees. Channels provide these (per the Go memory model):

1. A **send** on a channel *happens before* the corresponding **receive** from it completes.
2. The **closing** of a channel *happens before* a receive that returns because the channel is closed.
3. A **receive from an unbuffered channel** *happens before* the corresponding **send** completes.
4. The **k-th receive** on a channel with capacity **C** *happens before* the **(k+C)-th send** completes.

### Why this matters in practice

```go
var data []int   // plain variable, no lock

go func() {
    data = compute()   // (1) write
    ch <- struct{}{}   // (2) send
}()

<-ch                   // (3) receive: completes after (2)
use(data)              // (4) SAFE: (1) happens-before (4) through the channel
```

The channel operation creates an ordering edge, so the write in (1) is visible at (4) **without any mutex**.

Rule 4 is the formal reason a buffered channel of size N works as a **semaphore** limiting N concurrent operations.

### Caveat

Channels order *the send/receive*, not everything. If you send a **pointer** and *both* sides keep mutating the pointee, you still have a data race. Hence the "transfer ownership" convention.

---

## 11. Concurrency Patterns (with code)

### 11.1 Generator

A function returns a channel that produces values lazily.

```go
func counter(ctx context.Context, start int) <-chan int {
    out := make(chan int)
    go func() {
        defer close(out)
        for i := start; ; i++ {
            select {
            case out <- i:
            case <-ctx.Done():
                return
            }
        }
    }()
    return out
}
```

### 11.2 Pipeline

Stages connected by channels; each stage is a goroutine (or pool).

```go
func gen(nums ...int) <-chan int {
    out := make(chan int)
    go func() { defer close(out); for _, n := range nums { out <- n } }()
    return out
}
func square(in <-chan int) <-chan int {
    out := make(chan int)
    go func() { defer close(out); for n := range in { out <- n * n } }()
    return out
}
// usage
for v := range square(square(gen(1, 2, 3))) { fmt.Println(v) }
```

```
  +-------+   chan   +----------+   chan   +----------+   chan   +----------+
  |  gen  | -------> |  square  | -------> |  square  | -------> |  print   |
  +-------+          +----------+          +----------+          +----------+
   Each stage runs concurrently; the channels give natural backpressure.
   Throughput = speed of the SLOWEST stage.
```

### 11.3 Worker pool

Bounded concurrency over a stream of jobs.

```go
func workerPool(ctx context.Context, jobs <-chan Job, n int) <-chan Result {
    results := make(chan Result)
    var wg sync.WaitGroup
    wg.Add(n)
    for i := 0; i < n; i++ {
        go func() {
            defer wg.Done()
            for j := range jobs {
                r := process(j)
                select {
                case results <- r:
                case <-ctx.Done():
                    return
                }
            }
        }()
    }
    go func() { wg.Wait(); close(results) }()
    return results
}
```

```
                        +--> [ Worker 1 ] --+
                        |                   |
  producer --> jobs ----+--> [ Worker 2 ] --+--> results --> collector
   (chan Job)           |                   |     (chan Result)
                        +--> [ Worker 3 ] --+
                        |                   |
                        +--> [ Worker N ] --+

   - N fixed => bounded CPU / memory / downstream connections
   - jobs channel load-balances automatically: idle worker grabs next job
   - closing jobs => workers drain and exit => WaitGroup => close(results)
```

### 11.4 Fan-out / Fan-in

Fan-out: many consumers read one channel. Fan-in: merge many channels into one (see `fanIn` in section 7).

```
              FAN-OUT                               FAN-IN

              +--> worker A --+          srcA ---+
   source --->+--> worker B --+--> ...   srcB ---+--> merged --> consumer
              +--> worker C --+          srcC ---+
```

### 11.5 Counting semaphore (limit concurrency)

```go
sem := make(chan struct{}, 10) // max 10 concurrent

for _, url := range urls {
    sem <- struct{}{}          // acquire (blocks when 10 in flight)
    go func(u string) {
        defer func() { <-sem }() // release
        fetch(u)
    }(url)
}
// wait for all: fill the semaphore completely
for i := 0; i < cap(sem); i++ { sem <- struct{}{} }
```

(In real code prefer `golang.org/x/sync/semaphore` or `errgroup.SetLimit`.)

### 11.6 Future / promise

```go
func async(f func() (int, error)) <-chan struct {
    v   int
    err error
} {
    ch := make(chan struct {
        v   int
        err error
    }, 1) // buffer 1 so the goroutine never leaks if nobody reads
    go func() {
        v, err := f()
        ch <- struct {
            v   int
            err error
        }{v, err}
    }()
    return ch
}
```

### 11.7 Request/response with a reply channel (actor style)

```go
type req struct {
    key   string
    reply chan string   // each request carries its own reply channel
}

func server(reqs <-chan req) {
    store := map[string]string{"a": "1"}   // owned by THIS goroutine only: no mutex needed
    for r := range reqs {
        r.reply <- store[r.key]
    }
}

// client
reply := make(chan string, 1)
reqs <- req{"a", reply}
fmt.Println(<-reply)
```

```
  Client 1 --\                          +---------------------------+
  Client 2 ---+--> reqs chan --------> |  server goroutine (actor)  |
  Client 3 --/    (serializes)          |  owns `store` map          |
       ^                                +-------------+-------------+
       |                                              |
       +------------ per-request reply chan <--------+
```

This is the **actor / monitor goroutine** model: state is confined to one goroutine, and all access is via messages. No locks, no races.

### 11.8 Or-done (cancel-aware reads)

```go
func orDone(done <-chan struct{}, c <-chan int) <-chan int {
    out := make(chan int)
    go func() {
        defer close(out)
        for {
            select {
            case <-done:
                return
            case v, ok := <-c:
                if !ok { return }
                select {
                case out <- v:
                case <-done:
                    return
                }
            }
        }
    }()
    return out
}
```

### 11.9 Tee (duplicate a stream)

```go
func tee(done <-chan struct{}, in <-chan int) (<-chan int, <-chan int) {
    out1, out2 := make(chan int), make(chan int)
    go func() {
        defer close(out1)
        defer close(out2)
        for v := range orDone(done, in) {
            o1, o2 := out1, out2
            for i := 0; i < 2; i++ {          // send to both, in whichever order is ready
                select {
                case o1 <- v: o1 = nil          // nil-channel trick: disable after sending
                case o2 <- v: o2 = nil
                case <-done:
                    return
                }
            }
        }
    }()
    return out1, out2
}
```

### 11.10 Ticker / rate limiting

```go
limiter := time.NewTicker(100 * time.Millisecond) // 10 req/s
defer limiter.Stop()

for req := range requests {
    <-limiter.C       // wait for a tick
    go handle(req)
}
```

Bursty limiter using a buffered channel as a token bucket:

```go
tokens := make(chan struct{}, 5)  // burst of 5
for i := 0; i < 5; i++ { tokens <- struct{}{} }
go func() {
    for range time.Tick(200 * time.Millisecond) {
        select {
        case tokens <- struct{}{}:  // refill
        default:                    // bucket full
        }
    }
}()
// each request: <-tokens
```

### 11.11 Heartbeat

```go
func worker(done <-chan struct{}) (<-chan struct{}, <-chan int) {
    hb := make(chan struct{}, 1)
    out := make(chan int)
    go func() {
        defer close(hb)
        defer close(out)
        t := time.NewTicker(time.Second)
        defer t.Stop()
        for {
            select {
            case <-done:
                return
            case <-t.C:
                select { case hb <- struct{}{}: default: } // non-blocking pulse
            case out <- compute():
            }
        }
    }()
    return hb, out
}
```

### 11.12 Pub/Sub broker

```go
type Broker struct {
    mu   sync.RWMutex
    subs map[string][]chan string
}
func (b *Broker) Subscribe(topic string) <-chan string {
    ch := make(chan string, 16)
    b.mu.Lock()
    b.subs[topic] = append(b.subs[topic], ch)
    b.mu.Unlock()
    return ch
}
func (b *Broker) Publish(topic, msg string) {
    b.mu.RLock()
    defer b.mu.RUnlock()
    for _, ch := range b.subs[topic] {
        select {
        case ch <- msg:
        default: // slow subscriber: drop (or disconnect) instead of blocking everyone
        }
    }
}
```

### 11.13 errgroup (structured concurrency)

```go
g, ctx := errgroup.WithContext(context.Background())
g.SetLimit(8)
for _, u := range urls {
    u := u
    g.Go(func() error { return fetch(ctx, u) }) // first error cancels ctx
}
if err := g.Wait(); err != nil { /* handle */ }
```

---

## 12. Real-World Use Cases and Architectures

### 12.1 Where channels are actually used

| Domain | Use | Channel role |
|---|---|---|
| **HTTP / gRPC servers** | Graceful shutdown, per-request fan-out, streaming RPC | signal + `ctx.Done()` + result passing |
| **OS signals** | `signal.Notify(ch, SIGTERM)` | channel receives signals |
| **Kubernetes-style controllers** | Work queues, stop channels, informers' event handlers | decouple event delivery from processing |
| **Web crawlers / scrapers** | Bounded parallel fetching | worker pool + semaphore |
| **ETL / data pipelines** | read -> transform -> write | pipeline stages |
| **Real-time chat / WebSocket** | Broadcast hub | central goroutine + per-client send channel |
| **Log / metrics shippers** | Batch and flush by size/time | batcher with `select` + ticker |
| **Message-queue consumers** (Kafka, SQS, NATS) | Decouple poll loop from processing | buffered handoff + worker pool |
| **Rate limiters / throttling** | Token bucket | buffered channel + ticker |
| **Connection / object pools** | Hand out and return resources | buffered channel as free list |
| **Timers / schedulers** | `time.After`, `Ticker` | time delivered as channel events |
| **Parallel computation** | Map-reduce, image processing, simulations | fan-out/fan-in |
| **CLI tools** | Progress reporting from workers | updates channel -> UI goroutine |
| **Testing** | Synchronize goroutines deterministically | barriers/signals |
| **Event buses inside an app** | Decouple modules | pub/sub |

### 12.2 Architecture A: Production HTTP service with graceful shutdown

```
                       OS (SIGTERM / SIGINT)
                              |
                              v
                    +---------------------+
                    | signal.NotifyContext|
                    |   ctx (Done chan)   |
                    +----------+----------+
                               |
              +----------------+-------------------+
              |                                    |
              v                                    v
   +----------------------+              +------------------------+
   |   main goroutine     |              |  background workers    |
   |                      |              |  (queue consumers,     |
   |  go srv.Serve()      |              |   cron, cache refresh) |
   |  select {            |              |  each: select on       |
   |   case <-ctx.Done(): |              |  ctx.Done()            |
   |   case err := <-errCh|              +------------------------+
   |  }                   |
   +----------+-----------+
              | on shutdown:
              v
   srv.Shutdown(timeout ctx)  -->  stops accepting, drains in-flight requests
              |
              v
   cancel workers, wg.Wait(), close DB, flush logs, exit
```

```go
func main() {
    ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
    defer stop()

    srv := &http.Server{Addr: ":8080", Handler: routes()}
    errCh := make(chan error, 1)           // buffered: goroutine can always exit
    go func() { errCh <- srv.ListenAndServe() }()

    var wg sync.WaitGroup
    startWorkers(ctx, &wg)                 // each worker watches ctx.Done()

    select {
    case err := <-errCh:
        log.Fatalf("server failed: %v", err)
    case <-ctx.Done():
        log.Println("shutting down...")
    }

    shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
    defer cancel()
    _ = srv.Shutdown(shutdownCtx)          // drain in-flight requests
    wg.Wait()                              // wait for workers to finish
}
```

### 12.3 Architecture B: WebSocket chat hub (real, widely used design)

One goroutine owns all connection state; per-client goroutines do I/O.

```
   Browser 1 <==ws==> [readPump G]  --+
                      [writePump G] <-+--- client1.send (buffered chan)
                                      |
   Browser 2 <==ws==> [readPump G]  --+                           
                      [writePump G] <-+--- client2.send (buffered chan)
                                      |
   Browser 3 <==ws==> [readPump G]  --+
                      [writePump G] <-+--- client3.send (buffered chan)

   readPump goroutines                         Hub goroutine (owns clients map)
   ---------------------   hub.broadcast       +-------------------------------+
   each client's reader -----------------------> select {                      |
   pushes inbound msgs        hub.register   --->   case c := <-register       |
                              hub.unregister --->   case c := <-unregister     |
                                                    case m := <-broadcast:     |
                                                      for each client:         |
                                                        select {               |
                                                          case c.send <- m:    |
                                                          default: drop client |
                                                        }                      |
                                               }                               |
                                               +-------------------------------+
```

```go
type Hub struct {
    clients    map[*Client]struct{} // touched ONLY by Run(): no mutex
    register   chan *Client
    unregister chan *Client
    broadcast  chan []byte
}

func (h *Hub) Run() {
    for {
        select {
        case c := <-h.register:
            h.clients[c] = struct{}{}
        case c := <-h.unregister:
            if _, ok := h.clients[c]; ok {
                delete(h.clients, c)
                close(c.send)
            }
        case msg := <-h.broadcast:
            for c := range h.clients {
                select {
                case c.send <- msg:
                default: // slow consumer: evict so one bad client can't stall everyone
                    delete(h.clients, c)
                    close(c.send)
                }
            }
        }
    }
}
```

**Why this design is good:**
- `clients` map is confined to one goroutine, so no locks and no races.
- Non-blocking send to `c.send` provides isolation from slow clients.
- Each client has a dedicated writer, which ensures one writer per socket (a requirement for most WebSocket libraries).

### 12.4 Architecture C: Event ingestion with batching (metrics / logs / analytics)

```
   HTTP handlers / app code                                   Remote sink
   (many goroutines)                                          (DB / Kafka / S3)
 +---------+                                                       ^
 | emit(e) |--+                                                    |
 +---------+  |    +--------------------+   +------------------+   |
 +---------+  +--> |  events (buffered) |-->|   batcher G      |   |
 | emit(e) |--+    |  cap = 10_000      |   |  size OR time    |   |
 +---------+  |    +--------------------+   |  triggers flush  |   |
 +---------+  |         (non-blocking       +--------+---------+   |
 | emit(e) |--+          send; drop &              | batches       |
 +---------+            count if full)             v               |
                                           +------------------+    |
                                           |  flush workers   |----+
                                           |  (pool of N)     |
                                           |  retry + backoff |
                                           +------------------+
```

```go
func batcher(in <-chan Event, out chan<- []Event, maxSize int, every time.Duration) {
    defer close(out)
    buf := make([]Event, 0, maxSize)
    t := time.NewTicker(every)
    defer t.Stop()

    flush := func() {
        if len(buf) == 0 { return }
        out <- buf
        buf = make([]Event, 0, maxSize) // new slice: receiver now owns the old one
    }
    for {
        select {
        case e, ok := <-in:
            if !ok { flush(); return }
            buf = append(buf, e)
            if len(buf) >= maxSize { flush() }
        case <-t.C:
            flush()
        }
    }
}
```

Note the **ownership transfer**: after `out <- buf`, we allocate a new slice so we never mutate what the receiver holds.

### 12.5 Architecture D: Message-queue consumer (Kafka/SQS/NATS style)

```
   +-----------+     poll      +----------------+   jobs chan   +------------+
   |  Broker   | <-----------> |  poll loop G   | ------------> | worker 1   |
   | (Kafka..) |               |  (1 per        |  (bounded:    | worker 2   |
   +-----------+               |   partition)   |   backpressure| worker 3   |
         ^                     +----------------+   stops polling| worker N  |
         |                                          when full)   +-----+------+
         |                                                           |
         |        ack/commit offset      +-------------------+       |
         +-------------------------------|  ack collector G  |<------+
                                         |  (ordered commit) |  results chan
                                         +-------------------+
```

Bounded `jobs` => the poll loop **blocks** when workers are saturated => consumer lag grows safely in the broker (not in your RAM).

### 12.6 Architecture E: Web crawler (bounded parallelism + dedupe)

```
   seed URLs
       |
       v
  +----------+   to-visit    +--------------+    page    +--------------+
  | frontier | ------------> | fetch pool   | ---------> | parser pool  |
  |  (chan)  |               | N workers    |  (chan)    | M workers    |
  +----^-----+               +--------------+            +------+-------+
       |                                                         |
       |                  discovered links (chan)                |
       |          +---------------------------+                  |
       +----------|  dedupe G (owns visited   |<-----------------+
                  |  set; map, no mutex)      |
                  +---------------------------+
```

Termination is the tricky part: track in-flight work with a `WaitGroup` or a pending counter and close `frontier` when it hits zero.

### 12.7 Architecture F: Object/connection pool via channel

```go
type Pool struct{ free chan *Conn }

func NewPool(n int, mk func() *Conn) *Pool {
    p := &Pool{free: make(chan *Conn, n)}
    for i := 0; i < n; i++ { p.free <- mk() }
    return p
}
func (p *Pool) Get(ctx context.Context) (*Conn, error) {
    select {
    case c := <-p.free:   return c, nil
    case <-ctx.Done():    return nil, ctx.Err()
    }
}
func (p *Pool) Put(c *Conn) { p.free <- c }
```

```
             Get()  <--- [ c1 | c2 | c3 | c4 ]  <--- Put()
                          buffered channel = free list
             empty pool => Get() blocks (with ctx timeout) => natural queueing
```

### 12.8 Architecture G: Streaming pipeline (ETL)

```
 +---------+  rows   +----------+ parsed  +-----------+ enriched +---------+
 | Reader  | ------> | Parse    | ------> | Enrich    | -------> | Writer  |
 | (file/  | (buf    | (N wkrs) |  (buf)  | (M wkrs,  |  (buf)   | (batch  |
 |  DB)    |  128)   |          |         |  calls API|          |  insert)|
 +---------+         +----------+         |  + rate   |          +---------+
                                          |  limiter) |
                                          +-----------+
 Memory is bounded by the sum of buffer sizes, regardless of input size.
 You can process a 500 GB file with a few MB of RAM.
```

---

## 13. Channels vs Mutex vs Atomic vs WaitGroup vs Cond

Channels are **not** always the answer. The Go wiki's guidance: use whichever is **most expressive and simplest**.

| Need | Best tool | Why |
|---|---|---|
| Transfer ownership of data between goroutines | **Channel** | message passing + sync in one |
| Distribute work / pipeline / fan-out | **Channel** | natural fit |
| Signal an event / broadcast cancellation | **Channel (close) / context** | closing broadcasts |
| Protect a shared cache/counter/map with short critical sections | **`sync.Mutex` / `RWMutex`** | simpler and faster |
| Single integer counter/flag | **`sync/atomic`** | lock-free, minimal |
| Wait for N goroutines to finish | **`sync.WaitGroup` / `errgroup`** | purpose-built |
| One-time initialization | **`sync.Once`** | purpose-built |
| Condition-based wake-up with shared state | **`sync.Cond`** (rare) or channels | |
| Cache of reusable objects | **`sync.Pool`** | |
| Concurrent map with disjoint keys | **`sync.Map`** (niche) | |

### Rough heuristic

```
  Are you passing data / coordinating a FLOW of work?      --> channel
  Are you guarding a SHARED STRUCTURE with brief access?   --> mutex
  Is it a single number/flag?                              --> atomic
  Are you waiting for completion?                          --> WaitGroup / errgroup
```

### Concrete cost intuition (order of magnitude, hardware-dependent)

```
 atomic add          ~  1-10 ns
 uncontended mutex   ~ 10-30 ns
 buffered chan op    ~ 50-100+ ns  (lock + copy + possible park/wake)
 unbuffered handoff  ~ 100-300+ ns (often involves goroutine switch)
```

So **don't use a channel to protect a counter**. A mutex or atomic is simpler and faster. Always benchmark your own workload.

---

## 14. Pitfalls, Deadlocks, Leaks

### 14.1 Deadlock: all goroutines asleep

```go
func main() {
    ch := make(chan int)
    ch <- 1        // blocks forever: no receiver
    fmt.Println(<-ch)
}
// fatal error: all goroutines are asleep - deadlock!
```

The Go runtime can detect a deadlock **only when every goroutine is blocked** (and none can be woken by timers/IO). In a server where some goroutine is running, the deadlock is **silent**, so that goroutine just hangs.

### 14.2 Goroutine leak: the silent killer

```go
// BUG: if the caller returns early, the goroutine blocks on send forever
func firstResult(urls []string) string {
    ch := make(chan string)              // unbuffered
    for _, u := range urls {
        go func(u string) { ch <- fetch(u) }(u)
    }
    return <-ch                          // takes one; the other N-1 goroutines leak
}
```

Fixes:

```go
ch := make(chan string, len(urls))   // buffer = number of senders, so none block
// or use context cancellation + select on ctx.Done() in each sender
```

**Leak lifecycle diagram:**

```
  request 1 --> spawns G1,G2,G3 --> returns after G1 --> G2,G3 blocked forever
  request 2 --> spawns G4,G5,G6 --> returns after G4 --> G5,G6 blocked forever
  ...
  goroutine count and memory climb steadily  --> eventual OOM
```

**Rule:** every goroutine you start must have a clear **exit path** (a way it will end). Ask "how does this goroutine stop?" before writing `go`.

### 14.3 Send on closed channel (panic)

```go
close(ch)
ch <- 1   // panic: send on closed channel
```

Typical cause: a receiver or a second party closing the channel while senders are still active. Fix by assigning a single owner (section 7).

### 14.4 Closing a closed / nil channel (panic)

Use `sync.Once` or ownership design.

### 14.5 Forgetting to close, leading to a `range` that never ends

```go
for v := range ch { ... } // hangs forever if ch is never closed
```

### 14.6 Loop-variable capture

```go
for i := 0; i < 3; i++ {
    go func() { ch <- i }()   // Pre-Go 1.22: all goroutines may see i == 3
}
```

Since **Go 1.22**, each iteration gets its own `i` (if `go.mod` says `go 1.22`+). On older versions pass it as an argument: `go func(i int){...}(i)`.

### 14.7 Using a buffer to "fix" a deadlock

If adding a buffer makes the deadlock vanish, you probably have a **design problem** (unclear who receives, or a cycle). The deadlock reappears under load.

### 14.8 Circular wait between goroutines

```
   G1: sends to A, then receives from B
   G2: sends to B, then receives from A       (both unbuffered)

   G1 --send--> A  (waits for G2 to receive)
   G2 --send--> B  (waits for G1 to receive)
   => both blocked in send, neither reaches receive. DEADLOCK.
```

Fix: make one side use a buffer, use `select`, or restructure to a one-directional flow.

### 14.9 `time.After` in loops (older Go)

Allocates timers that live until they fire. Use `time.NewTimer` + `Stop/Reset`, or rely on Go 1.23+ semantics.

### 14.10 `select` with `default` in a loop, so busy-spinning

```go
for {
    select {
    case v := <-ch: use(v)
    default:                 // spins at 100% CPU when ch is empty!
    }
}
```

Remove `default` (so it blocks) or add a real wait (ticker/other case).

### 14.11 Data race through shared pointers

```go
ch <- &cfg        // sender keeps modifying cfg => race
```

Send **copies** or treat the sent pointer as handed off.

### 14.12 Unbounded goroutine creation

```go
for msg := range input { go handle(msg) } // 1M messages => 1M goroutines
```

Use a worker pool or semaphore to bound it.

### 14.13 Blocking inside the hub / event loop

A single goroutine that "owns" state must **never block** on a slow peer. Use non-blocking sends or per-peer buffered channels with eviction (as in the chat hub).

### 14.14 Misusing `len(ch)`

`if len(ch) < cap(ch) { ch <- v }` is a race: another goroutine may fill it in between. Use `select` with `default` instead.

### 14.15 Nil-channel surprise

Forgot `make`? `var ch chan int; ch <- 1` blocks forever. (A nil channel in `select` is a feature; elsewhere it is a bug.)

---

## 15. Testing, Debugging, Observability

### Tools

| Tool | Command | Finds |
|---|---|---|
| Race detector | `go test -race ./...` / `go run -race` | data races |
| Goroutine leak detector | `go.uber.org/goleak` (`defer goleak.VerifyNone(t)`) | leaks in tests |
| pprof goroutine profile | `import _ "net/http/pprof"`, `/debug/pprof/goroutine?debug=2` | where goroutines are stuck (shows `chan send` / `chan receive` / `select`) |
| `runtime.NumGoroutine()` | export as a metric | leak trend |
| Execution tracer | `go tool trace` | blocking, scheduling latency |
| SIGQUIT | `kill -QUIT <pid>` | dumps all goroutine stacks |

### Reading a goroutine dump

```
goroutine 42 [chan send, 10 minutes]:
main.worker(...)
        /app/worker.go:31 +0x45
```

`[chan send, 10 minutes]` means stuck sending for 10 minutes: find who should have been receiving. Other states: `chan receive`, `select`, `select (no cases)`, `chan receive (nil chan)`.

### Writing deterministic channel tests

```go
func TestWorkerStops(t *testing.T) {
    defer goleak.VerifyNone(t)

    ctx, cancel := context.WithCancel(context.Background())
    jobs := make(chan Job)
    results := workerPool(ctx, jobs, 4)

    jobs <- Job{ID: 1}
    <-results
    cancel()
    close(jobs)

    for range results {} // drain until closed
}
```

Prefer **synchronizing on channels** (not `time.Sleep`) in tests. Sleeps make tests slow and flaky.

### Metrics to export in production

- Channel fill ratio: `len(ch)/cap(ch)` (as a gauge; approximate by nature).
- Goroutine count.
- Dropped-message counter (from non-blocking sends).
- Queue wait time (timestamp on enqueue, observe on dequeue).

---

## 16. Performance and Sizing

### Where the cost comes from

```
 Every channel operation:
   1. lock(hchan.lock)                      <- contention under many goroutines
   2. memmove of the element                <- cost grows with element size
   3. maybe park/unpark a goroutine         <- scheduler work, cache effects
```

### Guidelines

1. **Batch.** Send `[]Item` of 100 items instead of 100 sends of one item. This amortizes lock and wake-up cost.
2. **Send pointers for large structs**, but only with a clear ownership hand-off.
3. **Avoid one hot channel with hundreds of contending goroutines.** Shard (several channels, hash by key) or reduce handoffs.
4. **Don't over-pipeline.** Each stage adds latency and overhead; fuse trivial stages.
5. **Size buffers deliberately:**
   - 0: handoff semantics.
   - 1: "never block the one-shot sender".
   - N = number of workers: absorb jitter.
   - Larger: only with measurement showing bursts and tolerable latency/memory.
6. **Use `struct{}`** for signal-only channels (zero-size element).
7. **Benchmark before optimizing.** Channel-vs-mutex differences matter only on hot paths.

### Little's Law (sizing intuition)

```
   L = lambda * W
   L      = average items in the system (queue + in service)
   lambda = arrival rate (items/s)
   W      = average time an item spends in the system (s)

   Example: 1000 jobs/s, each takes 50 ms
            => ~50 jobs in flight on average => ~50 workers to keep up.
```

If arrival rate exceeds service rate for long enough, **no buffer saves you**. You need to shed load or add capacity.

### Sharding for contention

```
                          +--> shard[0] chan --> worker group 0
   producer --> hash(key)-+--> shard[1] chan --> worker group 1
                          +--> shard[2] chan --> worker group 2
                          +--> shard[3] chan --> worker group 3

   Bonus: same key always goes to the same shard => per-key ordering preserved.
```

---

## 17. Channels in Other Ecosystems

The word "channel" recurs across technologies. The core idea (a conduit for messages between concurrent entities) is shared.

### 17.1 Rust: `std::sync::mpsc`, `crossbeam-channel`, `tokio::sync::mpsc`

```rust
use std::sync::mpsc;
use std::thread;

let (tx, rx) = mpsc::channel();      // multi-producer, single-consumer
let tx2 = tx.clone();                // clone sender for another producer
thread::spawn(move || { tx.send(1).unwrap(); });
thread::spawn(move || { tx2.send(2).unwrap(); });
for v in rx.iter().take(2) { println!("{v}"); }
```

Differences from Go:
- **Ownership is enforced by the compiler**: sending moves the value, so you *cannot* touch it afterward.
- The channel closes automatically when **all senders are dropped** (no explicit `close`, so no "close twice" bug).
- `mpsc` = many producers, **one** consumer; `crossbeam` and `tokio` offer MPMC/broadcast/oneshot variants.
- Async: `tokio::sync::{mpsc, oneshot, broadcast, watch}` for different semantics (`oneshot` = future-like, `watch` = latest-value, `broadcast` = every receiver gets every message).

### 17.2 Kotlin coroutines: `Channel<T>`

```kotlin
val ch = Channel<Int>(capacity = 3)       // RENDEZVOUS(0), BUFFERED, CONFLATED, UNLIMITED
launch { for (i in 1..5) ch.send(i); ch.close() }
for (v in ch) println(v)                  // suspends instead of blocking threads
```

Capacity modes: `RENDEZVOUS` (like unbuffered), `BUFFERED`, `CONFLATED` (keep only latest), `UNLIMITED`. Plus `Flow` / `SharedFlow` for reactive streams.

### 17.3 Java / .NET equivalents

- Java: `BlockingQueue` (`ArrayBlockingQueue` ~ buffered channel, `SynchronousQueue` ~ unbuffered), `Exchanger`, `Flow.Publisher`.
- .NET: `System.Threading.Channels` (`Channel.CreateBounded<T>` / `CreateUnbounded<T>`).
- Erlang/Elixir: mailboxes per process (actor model; channels are *per-process* and implicit).

### 17.4 Django Channels (a very different "Channels")

**Django Channels** extends Django beyond HTTP to **WebSockets, MQTT, chatbots, long-lived connections** using **ASGI**. Its "channel layer" lets separate processes/instances talk to each other (usually via Redis).

```
   Browser A                Browser B
      |  ws                    |  ws
      v                        v
 +--------------------------------------+
 |  ASGI server (Daphne / Uvicorn)      |   <- N worker processes / machines
 +------------------+-------------------+
                    |
          +---------v----------+
          | ProtocolTypeRouter |   http -> Django views
          |                    |   websocket -> URLRouter
          +---------+----------+
                    |
          +---------v----------+
          |  URLRouter         |  routes ws paths to Consumers
          +---------+----------+
                    |
          +---------v----------+
          |  Consumer          |  (AsyncWebsocketConsumer)
          |  connect()         |  one instance per connection
          |  receive()         |  has a unique channel_name
          |  group_add(...)    |
          +---------+----------+
                    |  group_send / channel_send
          +---------v-------------------------------+
          |  CHANNEL LAYER  (Redis / in-memory)     |
          |   group "room_42" -> {chan_A, chan_B,...}|
          +-----------------------------------------+
                    ^
                    |  can ALSO be triggered from sync code:
              Django view / Celery task / management command
              async_to_sync(channel_layer.group_send)("room_42", {...})
```

Concepts:
- **Channel**: a mailbox with a unique name (`channel_name`) for one consumer instance.
- **Group**: a named set of channels, so you can broadcast to everyone in a room.
- **Channel layer**: the backing transport (Redis) enabling cross-process messaging.
- **Consumer**: the class handling connection lifecycle and events.

```python
class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.room = "room_42"
        await self.channel_layer.group_add(self.room, self.channel_name)
        await self.accept()

    async def receive(self, text_data):
        await self.channel_layer.group_send(
            self.room, {"type": "chat.message", "text": text_data}
        )

    async def chat_message(self, event):         # handler for type "chat.message"
        await self.send(text_data=event["text"])

    async def disconnect(self, code):
        await self.channel_layer.group_discard(self.room, self.channel_name)
```

Note: Django Channel layers are **at-most-once, best-effort** and not a durable message queue. For guaranteed delivery use Celery/RabbitMQ/Kafka.

### 17.5 Other uses of the word

- **Slack/Discord/Teams channels**: chat rooms (human-level pub/sub).
- **Pub/Sub "channels"** (Redis `PUBLISH/SUBSCRIBE`): fire-and-forget broadcast, no persistence (use Redis Streams for durability).
- **Marketing/distribution channels**, **YouTube channels**, **channel (communication theory)**: different domains entirely.
- **Information-theory channel**: a medium for transmitting signals. The abstract ancestor of the concept.

### Comparison table

| Feature | Go chan | Rust mpsc | Kotlin Channel | Java BlockingQueue | Redis Pub/Sub |
|---|---|---|---|---|---|
| Scope | in-process | in-process | in-process | in-process | cross-process |
| Blocking unit | goroutine | thread (async: task) | coroutine (suspend) | thread | n/a (push) |
| Buffer options | 0..N | unbounded / bounded (sync_channel) | 0/N/conflated/unlimited | bounded/unbounded | none (no storage) |
| Close semantics | explicit `close` | auto on sender drop | explicit `close` | none built in (poison pill) | n/a |
| Multi-consumer | yes (work-sharing) | single (mpsc) | yes | yes | yes (broadcast) |
| Durable | no | no | no | no | no |

---

## 18. Decision Framework and Cheat Sheet

### 18.1 "Should I use a channel?" flowchart

```
                        Need concurrency coordination?
                                    |
              +---------------------+----------------------+
              |                                            |
     Passing data / work /                      Protecting shared state
     events between goroutines?                 with short critical sections?
              |                                            |
              v                                            v
          CHANNEL                                  Mutex / RWMutex / atomic
              |
     +--------+---------+
     |                  |
  Need handoff       Need decoupling /
  guarantee?         burst absorption?
     |                  |
  unbuffered        small buffered
                        |
              Need to limit concurrency?   --> buffered chan as semaphore / errgroup
              Need to broadcast "stop"?    --> close(done) / context
              Need N waiters to finish?    --> WaitGroup (+ channel for results)
```

### 18.2 Checklist before writing a channel-based design

1. **Who creates it? Who closes it?** (single owner)
2. **Who sends? Who receives? How many of each?**
3. **What happens when the consumer is slow?** (block / drop / evict / shed)
4. **What happens on cancellation?** (every blocking op in a `select` with `ctx.Done()`)
5. **How does every goroutine exit?** (no leak)
6. **What is the buffer size and why?**
7. **Can any pair of goroutines wait on each other in a cycle?**
8. **Is data ownership transferred cleanly?** (no shared mutation)
9. **How will I observe it?** (metrics, pprof)

### 18.3 Cheat sheet

```go
ch := make(chan T)            // unbuffered
ch := make(chan T, n)         // buffered
ch <- v                       // send
v := <-ch                     // receive
v, ok := <-ch                 // ok=false => closed & drained
close(ch)                     // sender-side only; broadcasts to all receivers
for v := range ch {}          // until closed
select { case ...; default: } // multiplex / non-blocking

nil ch      : send/recv block forever (disables select case)
closed ch   : recv => zero,false ; send => PANIC ; close => PANIC
chan<- T    : send-only        <-chan T : receive-only
done := make(chan struct{})   // signal channel; close(done) to broadcast
```

### 18.4 Golden proverbs

- *Channels orchestrate; mutexes serialize.*
- *Don't communicate by sharing memory; share memory by communicating.*
- *The sender closes; the receiver never does.*
- *Every goroutine needs an exit.*
- *A buffer is for smoothing bursts, not for fixing deadlocks.*
- *Bound everything: goroutines, queues, retries, timeouts.*
- *If in doubt: unbuffered, `select` with `ctx.Done()`, one owner.*

---

## 19. Practice Exercises

Work these out on paper first, then in code. They build the mental model.

**1. Predict the output** (and say whether it deadlocks):

```go
ch := make(chan int, 2)
ch <- 1
ch <- 2
close(ch)
for v := range ch { fmt.Println(v) }
v, ok := <-ch
fmt.Println(v, ok)
```
*Answer: prints 1, 2, then `0 false`. Closed channels drain buffered values first.*

**2. Find the bug:**

```go
func gen() <-chan int {
    ch := make(chan int)
    go func() {
        for i := 0; i < 3; i++ { ch <- i }
    }()
    return ch
}
for v := range gen() { fmt.Println(v) }
```
*Answer: the goroutine never closes `ch`, so `range` hangs after 3 values (deadlock). Add `defer close(ch)`.*

**3. Design:** build a worker pool of 5 workers that stops promptly on `ctx` cancel and returns the first error.
*Hint: errgroup, or results channel + `ctx.Done()` in every send/receive.*

**4. Implement** a rate-limited (10 req/s) HTTP fetcher with max 3 concurrent requests, graceful on Ctrl-C.
*Hint: ticker + semaphore + `signal.NotifyContext`.*

**5. Implement** a pub/sub broker where slow subscribers are dropped instead of stalling publishers.

**6. Refactor** a mutex-protected `map[string]int` cache into an actor goroutine with a request channel. Then benchmark both and discuss why the mutex version wins on throughput but the actor version wins on invariants/complexity.

**7. Explain** why this deadlocks and give three different fixes:

```go
a, b := make(chan int), make(chan int)
go func() { a <- 1; <-b }()
go func() { b <- 1; <-a }()
select {}
```

**8. Trace** (draw `hchan` state: buffer, sendx, recvx, recvq, sendq) the following with `make(chan int, 2)`: send 1, send 2, send 3 (blocked, G1), receive, receive, receive.

---

## Summary: The Mental Model in One Page

```
 +-----------------------------------------------------------------------------+
 |  CHANNEL = ring buffer + mutex + two wait-queues (senders, receivers)       |
 |            driven by the Go scheduler (park/ready), not OS threads          |
 |                                                                             |
 |  Purpose   : transfer VALUES/OWNERSHIP and SYNCHRONIZE goroutines           |
 |  Unbuffered: rendezvous; send completes when a receiver has the value       |
 |  Buffered  : decoupling up to N; blocks only when full (send) / empty (recv)|
 |  close()   : broadcast "no more data"; receivers drain then get zero,false  |
 |  nil chan  : blocks forever (useful to disable a select case)               |
 |  select    : wait on many ops; random choice if multiple ready              |
 |  ctx.Done(): a closed channel used as cancellation broadcast                |
 |                                                                             |
 |  Use for   : pipelines, worker pools, fan-in/out, signals, shutdown,        |
 |              event loops/actors, rate limits, pools, streaming              |
 |  Avoid for : protecting simple shared state (mutex/atomic), hot counters    |
 |                                                                             |
 |  Failure modes: deadlock, goroutine leak, send-on-closed panic,             |
 |                 unbounded buffering, circular waits, shared-pointer races   |
 |  Defenses     : single closer, ctx in every select, bounded queues,         |
 |                 -race, goleak, pprof goroutine dumps                        |
 +-----------------------------------------------------------------------------+
```

*End of guide.*
