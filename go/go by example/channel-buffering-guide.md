# Channel Buffering: The Complete Guide

> Primary language: **Go** (where "buffered channel" is a first-class concept).
> Section 14 maps the same idea to Kotlin, Rust, Java, Python, Node.js, Erlang, and OS-level pipes/sockets.

---

## Table of Contents

1. [What Is Channel Buffering?](#1-what-is-channel-buffering)
2. [The Core Mental Models](#2-the-core-mental-models)
3. [Syntax and Semantics (Complete Reference)](#3-syntax-and-semantics-complete-reference)
4. [Behavior Matrix: When Does Each Operation Block?](#4-behavior-matrix-when-does-each-operation-block)
5. [Under the Hood: Real Runtime Architecture](#5-under-the-hood-real-runtime-architecture)
6. [Memory Model and Happens-Before Guarantees](#6-memory-model-and-happens-before-guarantees)
7. [How to Choose a Buffer Size](#7-how-to-choose-a-buffer-size)
8. [Backpressure, Load Shedding, and Flow Control](#8-backpressure-load-shedding-and-flow-control)
9. [Core Patterns With Buffered Channels](#9-core-patterns-with-buffered-channels)
10. [Real-World Use Cases and Architectures](#10-real-world-use-cases-and-architectures)
11. [Pitfalls, Bugs, and Anti-Patterns](#11-pitfalls-bugs-and-anti-patterns)
12. [Performance Characteristics](#12-performance-characteristics)
13. [Testing, Debugging, and Observability](#13-testing-debugging-and-observability)
14. [Equivalents in Other Languages and Systems](#14-equivalents-in-other-languages-and-systems)
15. [Decision Flowchart](#15-decision-flowchart)
16. [Cheat Sheet](#16-cheat-sheet)
17. [Interview Questions and Exercises](#17-interview-questions-and-exercises)

---

## 1. What Is Channel Buffering?

A **channel** is a typed conduit for passing values between concurrent units of execution (goroutines in Go). A channel has a **capacity**:

| Capacity | Name | Meaning |
|----------|------|---------|
| `0` | **Unbuffered** (synchronous) | Sender and receiver must meet at the same moment (a *rendezvous*). |
| `N > 0` | **Buffered** (asynchronous up to N) | The channel holds up to N values. Senders only block when full; receivers only block when empty. |

```go
unbuffered := make(chan int)      // cap = 0
buffered   := make(chan int, 100) // cap = 100
```

**Channel buffering** is therefore the decision to put a *bounded FIFO queue* between a producer and a consumer so they are **decoupled in time**.

### What buffering gives you

1. **Temporal decoupling**: the producer doesn't have to wait for the consumer to be ready *right now*.
2. **Burst absorption**: short spikes in production don't stall producers.
3. **Throughput smoothing**: reduces context switches / goroutine park-unpark cycles.
4. **Bounded concurrency**: a buffered channel can act as a counting semaphore.
5. **Bounded memory**: unlike an unbounded queue, it enforces a hard upper limit, which is the foundation of **backpressure**.

### What buffering does NOT give you

- It does **not** increase sustained throughput. If the producer is permanently faster than the consumer, the buffer just fills up and then you're back to blocking.
- It does **not** fix deadlocks (it only delays them, which makes them harder to diagnose).
- It does **not** provide ordering across multiple producers (FIFO is per-channel, but interleaving of senders is non-deterministic).
- It does **not** provide durability. If the process dies, buffered items are gone.

---

## 2. The Core Mental Models

You need three mental models. Switch between them depending on what you are reasoning about.

### Mental Model A: Hand-to-hand vs. Mailbox

```
UNBUFFERED  (hand-to-hand handoff, "rendezvous")

   Producer                          Consumer
   --------                          --------
      |                                  |
      |  "I have a value, waiting..."    |
      |  (blocked)                       |
      |                                  |  "ready to receive"
      |===== value handed over =========>|
      |  (both unblock)                  |
      v                                  v

   Both parties must be present at the same instant.
   Analogy: passing a baton in a relay race.


BUFFERED  (mailbox / conveyor belt)

   Producer          +---------------------+          Consumer
   --------          |  slot slot slot ... |          --------
      |   drop in    |  [A] [B] [C] [ ] [ ]|  take    |
      |------------->|  FIFO, capacity = 5 |--------->|
      |  (continues) +---------------------+          |
      v                                                v

   Producer drops items and walks away (until the box is full).
   Analogy: a conveyor belt or a post-office mailbox.
```

### Mental Model B: Shock Absorber (Rates)

Buffering is a **shock absorber** for rate mismatch, **not** a power increase.

```
 Producer rate (items/sec)
   ^
   |        ____            <- burst
   |       |    |
   | ______|    |______________  <- average
   |
   +--------------------------------> time

 Consumer rate (constant)
   ^
   |
   | ------------------------------  <- steady drain rate
   |
   +--------------------------------> time

 Buffer fill level
   ^
 N |             ______
   |            /      \
   |           /        \
   |__________/          \_______   <- buffer absorbs the burst, then drains
   +--------------------------------> time

 If the average producer rate > consumer rate, the buffer climbs to N
 and stays full. Then producers block => this is BACKPRESSURE.
```

### Mental Model C: Ring Buffer + Two Wait Lists

Internally, a buffered channel is:

1. a **circular array** (ring buffer) of `cap` slots,
2. a **lock**,
3. a **queue of blocked senders** (`sendq`),
4. a **queue of blocked receivers** (`recvq`).

```
                  +---------------- chan T ----------------+
                  |                                        |
   senders -----> |  [ring buffer of N slots]  + lock      | -----> receivers
                  |                                        |
                  |  sendq: goroutines waiting (buffer full)|
                  |  recvq: goroutines waiting (buffer empty)|
                  +----------------------------------------+
```

Keep this picture. Section 5 expands it using the real runtime structure.

---

## 3. Syntax and Semantics (Complete Reference)

### 3.1 Creating channels

```go
ch  := make(chan int)        // unbuffered
chb := make(chan int, 3)     // buffered, capacity 3
chs := make(chan struct{}, 8) // zero-size elements: a pure signaling/semaphore buffer
var nilCh chan int           // nil channel (never ready, see 3.7)
```

`make(chan T, n)` with negative `n` panics at runtime (or fails to compile for constants). A very large `n` allocates `n * sizeof(T)` bytes immediately.

### 3.2 Sending and receiving

```go
ch <- v          // send
v := <-ch        // receive
v, ok := <-ch    // receive with "is channel still open or had data" flag
<-ch             // receive and discard (often used for signaling)
```

### 3.3 `len` and `cap`

```go
ch := make(chan int, 5)
ch <- 1
ch <- 2
fmt.Println(len(ch), cap(ch)) // 2 5
```

- `cap(ch)`: the buffer size (constant for the channel's life).
- `len(ch)`: number of elements currently queued.
- **`len` is a snapshot.** By the time you act on it, it may be stale. Use it for **metrics**, never for **control flow** (`if len(ch) < cap(ch) { ch <- x }` is a race; use `select` with `default`).

### 3.4 Closing

```go
close(ch)
```

Rules:

| Operation | On a closed channel |
|-----------|---------------------|
| Send | **panic** |
| Receive | Drains remaining buffered values first, **then** returns the zero value immediately with `ok == false` |
| Close again | **panic** |
| Close `nil` channel | **panic** |
| `len`/`cap` | work normally |

Convention: **only the sender (or a single coordinator) closes a channel**. Receivers never close. Closing is a *signal to receivers that no more values are coming*; it is not required for garbage collection.

Important buffering interaction:

```go
ch := make(chan int, 3)
ch <- 1; ch <- 2; ch <- 3
close(ch)

for v := range ch { fmt.Println(v) } // prints 1 2 3, then loop exits
```

Closing a buffered channel does **not** discard buffered data. Receivers still get all of it.

### 3.5 `range` over a channel

```go
for v := range ch { ... }  // loops until ch is closed AND drained
```

If nobody ever closes `ch`, this loop blocks forever (a goroutine leak).

### 3.6 `select` with buffered channels

```go
select {
case ch <- v:        // succeeds if there is buffer space (or a waiting receiver)
    // sent
case x := <-other:
    // received
case <-ctx.Done():
    // cancelled
case <-time.After(time.Second):
    // timeout
default:
    // nothing is ready right now; do not block
}
```

- With `default`, `select` is **non-blocking**. This is the idiom for *try-send* and *try-receive*.
- If several cases are ready, Go picks **one at random** (fairness; prevents starvation).

### 3.7 `nil` channels

A `nil` channel **blocks forever** on both send and receive, and in `select` it is **never ready**. This is a feature: set a channel variable to `nil` to *disable* that `select` case.

```go
for a != nil || b != nil {
    select {
    case v, ok := <-a:
        if !ok { a = nil; continue } // disable this case
        use(v)
    case v, ok := <-b:
        if !ok { b = nil; continue }
        use(v)
    }
}
```

### 3.8 Directional channel types

```go
func produce(out chan<- int) { out <- 1 }  // send-only
func consume(in  <-chan int) { <-in }      // receive-only

ch := make(chan int, 10)
go produce(ch) // bidirectional converts implicitly to directional
go consume(ch)
```

Directional types are enforced at compile time and document intent (who owns closing: only send-only holders may `close`).

### 3.9 Channel of channels

```go
type Request struct {
    Args  []int
    Reply chan int // typically buffered with cap 1 (see section 9.5)
}
```

---

## 4. Behavior Matrix: When Does Each Operation Block?

### 4.1 Full table

| State of channel | `send` | `receive` | `close` |
|------------------|--------|-----------|---------|
| `nil` | blocks forever | blocks forever | panic |
| Unbuffered, **no** waiting receiver | blocks until a receiver arrives | n/a | ok |
| Unbuffered, **no** waiting sender | n/a | blocks until a sender arrives | ok |
| Buffered, **empty** | succeeds (stored) *or* handed directly to a waiting receiver | **blocks** | ok |
| Buffered, **partially full** | **succeeds** immediately | **succeeds** immediately | ok |
| Buffered, **full** | **blocks** until a receiver frees a slot | **succeeds** immediately | ok |
| **Closed**, buffer has data | **panic** | **succeeds** (drains data) | panic |
| **Closed**, buffer empty | **panic** | returns zero value, `ok=false`, **never blocks** | panic |

### 4.2 State machine view (buffer of capacity N)

```
                     send (not full)                send (becomes full)
          +-----------------------------+      +-----------------------+
          |                             v      |                       v
     +---------+   send    +-------------+    |                +-------------+
     |  EMPTY  | --------> |  PARTIAL    | ---+                |    FULL     |
     | len=0   |           | 0<len<cap   |                     | len=cap     |
     +---------+ <-------- +-------------+ <------------------ +-------------+
        ^   |     recv       ^        |         recv                 |   |
        |   |  (becomes      |        |                              |   |
        |   |   empty)       +--------+                              |   |
        |   |                 send/recv                              |   |
        |   v                 (stay partial)                         |   v
        | receiver BLOCKS                                  sender BLOCKS
        | (parks in recvq)                                 (parks in sendq)
        |                                                       |
        +-------------------------------------------------------+
                  close(ch) is valid from any state
```

### 4.3 Special rule: if a receiver is already waiting

If the buffer is empty **and** a receiver is parked in `recvq`, a send **bypasses the buffer** and copies the value **directly into the receiver's stack**. Buffering only matters when nobody is waiting.

If the buffer is full **and** a sender is parked in `sendq`, a receive takes the head of the buffer and the parked sender's value is immediately moved into the freed slot, preserving FIFO order.

---

## 5. Under the Hood: Real Runtime Architecture

This is simplified from Go's `runtime/chan.go`. Exact fields vary a little between Go versions, but the architecture is stable.

### 5.1 The `hchan` structure

```go
type hchan struct {
    qcount   uint           // number of elements currently in the buffer  (== len(ch))
    dataqsiz uint           // size of the ring buffer                     (== cap(ch))
    buf      unsafe.Pointer // pointer to array of dataqsiz elements
    elemsize uint16         // size of one element
    closed   uint32         // 0 = open, 1 = closed
    elemtype *_type         // element type info (for GC / typed copy)
    sendx    uint           // next index to WRITE into buf
    recvx    uint           // next index to READ  from buf
    recvq    waitq          // list of goroutines blocked on receive
    sendq    waitq          // list of goroutines blocked on send
    lock     mutex          // protects ALL fields above
}

type waitq struct {
    first *sudog
    last  *sudog
}
```

`make(chan T, n)` returns a **pointer** to an `hchan`. That is why channels are reference types: copying a channel variable copies the pointer, not the queue.

Memory layout: for element types without pointers, the `hchan` header and `buf` are allocated in **one contiguous block**; for pointer-containing types they are allocated separately so the GC can scan the buffer correctly.

### 5.2 Architecture diagram (`make(chan int, 5)`, 3 items queued)

```
 ch (variable on stack/heap)
  |
  |  pointer
  v
 +=======================================================================+
 |                              hchan                                    |
 +=======================================================================+
 | qcount   = 3           (items in buffer)                              |
 | dataqsiz = 5           (capacity)                                     |
 | closed   = 0                                                          |
 | elemsize = 8                                                          |
 | sendx    = 3           (next write slot)                              |
 | recvx    = 0           (next read slot)                               |
 | lock     = [ mutex ]   (guards everything in this struct)             |
 |                                                                       |
 | buf  ---------------+                                                 |
 |                     |                                                 |
 | recvq: first=nil, last=nil      (no blocked receivers)                |
 | sendq: first=nil, last=nil      (no blocked senders)                  |
 +=====================|=================================================+
                       |
                       v
         slot:   [0]     [1]     [2]     [3]     [4]
               +-------+-------+-------+-------+-------+
        buf -> |  10   |  20   |  30   |       |       |
               +-------+-------+-------+-------+-------+
                  ^                       ^
                recvx=0                 sendx=3
          (next item to be received)  (next free slot to send into)

        Receive order: 10, then 20, then 30   (FIFO)
```

### 5.3 Ring buffer wrap-around

The buffer is **circular**: indices wrap to 0 when they reach `dataqsiz`.

```
 State 1: after sending 1..5 (full)            qcount=5
          slot:  [0] [1] [2] [3] [4]
                +---+---+---+---+---+
                | 1 | 2 | 3 | 4 | 5 |
                +---+---+---+---+---+
                  ^
            recvx=0, sendx=0   (sendx wrapped 5 -> 0;  FULL, not EMPTY, 
                                disambiguated by qcount, not by indices)

 State 2: receive twice (gets 1, 2)            qcount=3
                +---+---+---+---+---+
                |   |   | 3 | 4 | 5 |
                +---+---+---+---+---+
                          ^
                  recvx=2
                  sendx=0

 State 3: send 6 and 7                         qcount=5
                +---+---+---+---+---+
                | 6 | 7 | 3 | 4 | 5 |
                +---+---+---+---+---+
                          ^
                 recvx=2, sendx=2

          Logical FIFO order is: 3, 4, 5, 6, 7  (read starts at recvx=2,
          wraps around to slot 0).
```

Because `sendx == recvx` is true for both "empty" and "full", the runtime uses **`qcount`** as the source of truth.

### 5.4 The `sudog`: how a blocked goroutine waits

When a goroutine must block, the runtime wraps it in a `sudog` ("pseudo-goroutine" waiting record) and appends it to `sendq` or `recvq`.

```go
type sudog struct {
    g    *g              // the blocked goroutine
    next *sudog          // doubly linked list in waitq
    prev *sudog
    elem unsafe.Pointer  // where the value to send is / where to put the received value
    c    *hchan          // the channel it is blocked on
    ...
}
```

```
 Full buffer, three blocked senders:

 hchan.sendq
   first                                              last
     |                                                  |
     v                                                  v
  +--------+      +--------+      +--------+
  | sudog  | <--> | sudog  | <--> | sudog  |
  | g=G7   |      | g=G9   |      | g=G12  |
  | elem=  |      | elem=  |      | elem=  |
  | &valA  |      | &valB  |      | &valC  |
  +--------+      +--------+      +--------+
   (oldest)                        (newest)

 Each G is PARKED (not runnable, not consuming CPU) until a receive
 frees a slot, at which point the runtime moves valA into the buffer
 and marks G7 runnable.
```

### 5.5 Send algorithm (`ch <- v`) step by step

```
                         ch <- v
                            |
                            v
                  +-------------------+
                  | ch == nil ?       |--yes--> block forever (gopark)
                  +-------------------+
                            | no
                            v
                  +-------------------+
                  |   lock(ch.lock)   |
                  +-------------------+
                            |
                            v
                  +-------------------+
                  |  ch.closed ?      |--yes--> unlock; PANIC
                  +-------------------+
                            | no
                            v
              +-----------------------------+
              | receiver waiting in recvq ? |--yes--> dequeue sudog,
              +-----------------------------+         COPY v DIRECTLY into
                            | no                      receiver's memory,
                            v                         unlock, goready(receiver)
              +-----------------------------+         DONE (fast path)
              | qcount < dataqsiz  ?        |--yes--> copy v into buf[sendx]
              | (space in buffer)           |         sendx = (sendx+1) % dataqsiz
              +-----------------------------+         qcount++ ; unlock ; DONE
                            | no (buffer full / or unbuffered)
                            v
              +-----------------------------+
              | non-blocking (select default)|--yes--> unlock; return false
              +-----------------------------+
                            | no
                            v
              +-----------------------------+
              | create sudog{g, elem=&v}    |
              | enqueue into ch.sendq       |
              | unlock + gopark(): goroutine|
              | sleeps until a receiver     |
              | wakes it (or channel closes)|
              +-----------------------------+
```

### 5.6 Receive algorithm (`v := <-ch`) step by step

```
                         v := <-ch
                            |
                            v
                  +-------------------+
                  | ch == nil ?       |--yes--> block forever
                  +-------------------+
                            | no
                            v
                  +-------------------+
                  |   lock(ch.lock)   |
                  +-------------------+
                            |
                            v
              +-----------------------------+
              | closed AND qcount == 0 ?    |--yes--> unlock; return zero value, ok=false
              +-----------------------------+
                            | no
                            v
              +-----------------------------+
              | sender waiting in sendq ?   |--yes--+
              +-----------------------------+       |
                            | no                    v
                            |          +------------------------------+
                            |          | unbuffered:                  |
                            |          |   copy value straight from   |
                            |          |   the sender's sudog         |
                            |          | buffered (buffer is full):   |
                            |          |   1. take head: buf[recvx]   |
                            |          |   2. copy sender's value into|
                            |          |      that freed slot (tail)  |
                            |          |   3. advance recvx & sendx   |
                            |          | goready(sender); unlock; DONE|
                            |          +------------------------------+
                            v
              +-----------------------------+
              | qcount > 0 ?                |--yes--> copy buf[recvx] out;
              | (data available in buffer)  |         clear slot (for GC);
              +-----------------------------+         recvx = (recvx+1) % dataqsiz
                            | no                      qcount-- ; unlock ; DONE
                            v
              +-----------------------------+
              | create sudog, enqueue in    |
              | ch.recvq; unlock + gopark() |
              | sleep until a sender wakes  |
              | us or channel is closed     |
              +-----------------------------+
```

### 5.7 Direct handoff: the optimization that bypasses the buffer

```
 recvq has a parked receiver (buffer is empty):

   Sender goroutine                 hchan                    Receiver goroutine (PARKED)
   -----------------        ---------------------            ----------------------------
   ch <- 42
       |  lock                 recvq: [sudog(G5)]
       |------------------->   buf:   [ ][ ][ ][ ][ ]   (untouched)
       |                              
       |  copy 42 directly into G5's stack slot  ----------------> v == 42
       |  goready(G5)  --- puts G5 on a run queue --------------> becomes RUNNABLE
       |  unlock
       v  (sender continues without blocking)

 The value never touched the ring buffer.
```

### 5.8 How blocking connects to the Go scheduler (G-M-P)

```
   +------------------------- Go Runtime ---------------------------+
   |                                                                |
   |   P0 (processor)         P1 (processor)                        |
   |   +-----------+          +-----------+                         |
   |   | local runq|          | local runq|      Global runq        |
   |   | G1 G4 G8  |          | G2 G3     |      [ G11 G13 ]        |
   |   +-----------+          +-----------+                         |
   |       |                      |                                 |
   |       v                      v                                 |
   |     M0 (OS thread)         M1 (OS thread)                      |
   |     running G1             running G2                          |
   |                                                                |
   |   Parked goroutines (not in any run queue; owned by channels): |
   |     chA.sendq: G7, G9, G12   <- waiting for buffer space      |
   |     chB.recvq: G5, G6        <- waiting for data              |
   +----------------------------------------------------------------+

 When G1 executes  `ch <- v`  on a FULL buffer:
   1. G1 is enqueued as a sudog into ch.sendq
   2. gopark(): G1 leaves the run queue (state: _Gwaiting)
   3. M0 immediately picks the next runnable G (e.g., G4). NO OS thread is blocked.

 When another goroutine receives and frees a slot:
   4. runtime dequeues G1's sudog, moves its value into the buffer
   5. goready(G1): G1 goes into the receiver's P's runnext slot
   6. G1 will resume right after its `ch <- v` statement.
```

**Why this matters:** a blocked channel operation costs a few hundred nanoseconds of bookkeeping and a few KB of parked-goroutine memory, *not* an OS thread. That is why you can have 100,000 goroutines blocked on channels.

### 5.9 Where buffering saves work

```
 UNBUFFERED ping-pong (every item = 1 park + 1 wake):

   Producer:  send -> park ------------------------> wake -> send -> park ...
   Consumer:  ..... recv(wake producer) -> process -> recv -> park ...
   => each item forces a goroutine switch

 BUFFERED (capacity 64):

   Producer:  send send send ... (64 items, no parking) ... park
   Consumer:  ....................  recv recv recv ... (drain) ...
   => the cost of switching is AMORTIZED over many items
```

### 5.10 Locks: channels are NOT lock-free

Every send/recv takes the per-channel `mutex`. Under heavy contention (many producers and consumers on one channel), that lock becomes the bottleneck. Section 12 discusses sharding.

---

## 6. Memory Model and Happens-Before Guarantees

The Go Memory Model gives you four channel rules. They are what make channels safe for transferring *ownership of data*.

| # | Rule | Meaning |
|---|------|---------|
| 1 | A **send** on a channel **happens before** the corresponding **receive** from it completes. | Anything the sender wrote before sending is visible to the receiver after it receives. |
| 2 | The **closing** of a channel **happens before** a receive that returns because the channel is closed. | `close` is a publish barrier too. |
| 3 | A receive from an **unbuffered** channel **happens before** the corresponding send completes. | Unbuffered gives synchronization *both ways*. |
| 4 | The **k-th receive** on a channel with capacity **C** **happens before** the **(k+C)-th send** from that channel completes. | This is the rule that makes a buffered channel a valid **counting semaphore**. |

### 6.1 Example of rule 1 (safe publication)

```go
var config *Config          // plain variable, no mutex
ch := make(chan struct{}, 1)

go func() {
    config = loadConfig()   // (a) write
    ch <- struct{}{}        // (b) send
}()

<-ch                        // (c) receive completes AFTER (b)
use(config)                 // (d) guaranteed to see (a)
```

### 6.2 Rule 4 explained with a semaphore (capacity C=3)

```
 sends:      S1   S2   S3   S4   S5   S6
             |    |    |    ^    ^    ^
             |    |    |    |    |    |
 receives:   R1   R2   R3   |    |    |
             |    |    |    |    |    |
             +----|----|----+    |    |    R1 happens-before S4 completes
                  +----|---------+    |    R2 happens-before S5 completes
                       +--------------+    R3 happens-before S6 completes

 => "at most 3 goroutines are between S and R at a time"
```

### 6.3 What the channel does NOT protect

The channel synchronizes the *transfer*. It does not protect **shared memory reachable through the value**:

```go
ch := make(chan *Buf, 8)
b := &Buf{}
ch <- b
b.data[0] = 1   // DATA RACE: sender still touches b after handing it off
```

Rule of thumb: **after you send a pointer, you no longer own what it points to.** (Ownership transfer. "Don't communicate by sharing memory; share memory by communicating.")

---

## 7. How to Choose a Buffer Size

There is no universal number. There *are* principled ways to decide.

### 7.1 The three sizes that are almost always right

| Size | Use | Why |
|------|-----|-----|
| **0** (unbuffered) | Default. Synchronization/handoff; when you want guaranteed rendezvous and the strongest backpressure. | Easiest to reason about; bugs show up immediately as deadlocks. |
| **1** | "Fire-and-forget" completion signals, single result delivery, `signal.Notify`, mutex-like token, "latest value" slot with drop. | Lets the sender finish without a receiver, which prevents goroutine leaks. |
| **N = number of known items / workers** | You know exactly how many values will be produced (e.g., one per worker, one per job). | Sender can never block, so no leak and no deadlock. |

### 7.2 The "known cardinality" rule

```go
results := make(chan Result, len(jobs)) // exactly one result per job
for _, j := range jobs { go func(j Job){ results <- do(j) }(j) }
for range jobs { handle(<-results) }
```

Nobody can leak, even if the consumer returns early on error: every sender has a slot guaranteed.

### 7.3 Burst absorption formula

Let:
- `λ` = producer rate during the burst (items/s)
- `μ` = consumer rate (items/s)
- `T` = burst duration (s)

Required buffer to avoid any blocking during the burst:

```
    B  >=  (λ - μ) * T          (only if λ > μ)
```

Example: producers burst at 1000/s for 2 s; consumers do 600/s:

```
    B >= (1000 - 600) * 2 = 800 items
```

Add headroom (e.g., x1.25-x2) for variance.

### 7.4 Little's Law (steady-state occupancy)

```
    L = λ * W
    L: average number of items in the system (queue + in-processing)
    λ: average arrival rate
    W: average time an item spends in the system
```

Example: 500 items/s, each spends 40 ms in the pipeline:

```
    L = 500 * 0.040 = 20 items in flight on average
```

A buffer much smaller than L *forces* backpressure; much larger than peak L is wasted memory and hidden latency.

### 7.5 Latency cost of a big buffer (the "bufferbloat" effect)

A full buffer of size B in front of a consumer with service time `s` adds **`B * s`** of queueing delay to the *last* item.

```
    B = 10_000, s = 1 ms  ->  worst-case queueing delay = 10 s

 Bigger buffer => higher throughput smoothing, but WORSE tail latency
 and more stale work. For real-time/interactive paths, keep B small and shed load.
```

### 7.6 Memory cost

```
    memory ≈ cap * sizeof(T) + ~96 bytes (hchan header)

    make(chan [4096]byte, 10_000)  ->  ~41 MB   (!)
    make(chan *Buf,       10_000)  ->  ~80 KB   (but Bufs live elsewhere)
    make(chan struct{},   10_000)  ->  header only (zero-size elems)
```

Prefer pointers or small structs for large payloads; but remember rule 6.3 (ownership).

### 7.7 Practical heuristics

1. Start with **0**. Add buffering only when a profile or a reasoned burst model says so.
2. Make the size a **named constant or config value**, never a magic number: `const logQueueSize = 4096 // ≈ 2s of peak traffic`.
3. Size by **time worth of data** ("2 seconds of peak"), not by gut.
4. Pair the buffer with a **policy for full**: block, drop-newest, drop-oldest, or fail fast (section 8).
5. **Export `len(ch)/cap(ch)` as a metric**. A buffer constantly near 100% means the consumer is the bottleneck.

---

## 8. Backpressure, Load Shedding, and Flow Control

When the buffer is full, you must choose a **full-policy**. This choice matters more than the exact size.

### 8.1 The four policies

```
                      producer wants to send, buffer is FULL
                                     |
        +------------+---------------+-----------------+----------------+
        |            |                                 |                |
        v            v                                 v                v
   BLOCK         DROP NEWEST                      DROP OLDEST        FAIL FAST /
 (backpressure)  (discard the new item)       (evict head, push new)  TIMEOUT
```

**Policy 1: Block (backpressure).** Default `ch <- v`. Slows the producer to the consumer's pace. Use when losing data is unacceptable and the producer can wait (pipelines, batch jobs, internal services).

**Policy 2: Drop newest (try-send).**

```go
select {
case ch <- ev:
default:
    droppedCounter.Inc() // MUST be observable
}
```
Use for metrics, logs, telemetry, best-effort events.

**Policy 3: Drop oldest (keep freshest).**

```go
func sendLatest[T any](ch chan T, v T) {
    for {
        select {
        case ch <- v:
            return
        default:
            select {
            case <-ch: // evict the oldest
            default:
            }
        }
    }
}
```
Use for sensor readings, UI state, market ticks, where only recent values matter. (If you only ever want the latest, use capacity 1 with this function.)

**Policy 4: Timeout / fail fast.**

```go
select {
case ch <- job:
    return nil
case <-time.After(50 * time.Millisecond):
    return ErrOverloaded // HTTP 503 / 429 upstream
case <-ctx.Done():
    return ctx.Err()
}
```
Use at API boundaries so overload produces a quick error instead of a pile-up.

### 8.2 Backpressure propagating through a pipeline

```
   Stage 1           Stage 2           Stage 3           Sink
  (reader)          (parser)         (enricher)        (DB writer)
 +--------+  ch1   +--------+  ch2  +---------+  ch3  +----------+
 |        | -----> |        | ----> |         | ----> |  SLOW    |
 +--------+ cap=8  +--------+ cap=8 +---------+ cap=8 +----------+

 Sink slows down
      |
      v
 ch3 fills (8/8)  -> Stage 3 blocks on send
      |
      v
 ch2 fills (8/8)  -> Stage 2 blocks on send
      |
      v
 ch1 fills (8/8)  -> Stage 1 blocks on send
      |
      v
 Reader stops reading from the source (file / socket / Kafka poll)
      |
      v
 Upstream sees full TCP window or consumer lag => natural system-wide throttling.

 Total in-flight items bounded by  (8 + 8 + 8) + items inside stages  =>  BOUNDED MEMORY.
```

This end-to-end propagation is the single most valuable property of bounded buffers. An **unbounded** queue (slice + mutex, "infinite channel") silently turns a slow consumer into an out-of-memory crash.

### 8.3 Watermarks (advanced)

Use `len(ch)` (as a *metric/heuristic*, not exact control) to implement high/low watermarks:

```
 cap=1000
 |-------------------|-----------------------|-----------|
 0                  500 (low)             800 (high)   1000
 normal              |<---- pause producer? --|--> start shedding
```

Typical reaction: at high-watermark, tell upstream to slow down (HTTP 429, pause Kafka partition via `consumer.Pause`), at low-watermark resume.

---

## 9. Core Patterns With Buffered Channels

### 9.1 Worker pool (the canonical use)

```go
func process(jobs []Job, workers int) []Result {
    jobCh    := make(chan Job, workers)    // small buffer: keeps workers fed
    resultCh := make(chan Result, len(jobs)) // known cardinality: producers never block

    var wg sync.WaitGroup
    for i := 0; i < workers; i++ {
        wg.Add(1)
        go func() {
            defer wg.Done()
            for j := range jobCh {          // exits when jobCh is closed + drained
                resultCh <- run(j)
            }
        }()
    }

    for _, j := range jobs { jobCh <- j }   // may block only if workers are all busy AND buffer full
    close(jobCh)                            // sender closes

    wg.Wait()
    close(resultCh)

    out := make([]Result, 0, len(jobs))
    for r := range resultCh { out = append(out, r) }
    return out
}
```

```
                 +---------------------------------------------+
                 |                  WORKER POOL                |
                 |                                             |
  producer  ---> |  jobCh (cap=W)  ---+--> worker 1 --+        |
  (main)         |  [J][J][J][ ]      |                |        |
                 |                    +--> worker 2 --+--> resultCh (cap=len(jobs))
                 |                    |                |        |  [R][R][R][R]...
                 |                    +--> worker W --+        |        |
                 |                                             |        v
                 +---------------------------------------------+    collector
```

Why `jobCh` has a *small* buffer: just enough that a worker finishing a job finds the next one ready without waiting for the producer to be scheduled. A *huge* job buffer only increases memory and latency.

### 9.2 Pipeline (stages connected by channels)

```go
func gen(ctx context.Context, nums ...int) <-chan int {
    out := make(chan int, 16)
    go func() {
        defer close(out)
        for _, n := range nums {
            select {
            case out <- n:
            case <-ctx.Done():
                return
            }
        }
    }()
    return out
}

func square(ctx context.Context, in <-chan int) <-chan int {
    out := make(chan int, 16)
    go func() {
        defer close(out)
        for n := range in {
            select {
            case out <- n * n:
            case <-ctx.Done():
                return
            }
        }
    }()
    return out
}
```

Each stage: **owns** its output channel, **closes** it when done, and **respects cancellation** on every send (otherwise a stalled downstream leaks the stage).

### 9.3 Fan-out / fan-in

```
                          +--> worker A --+
                          |               |
   source --> inCh (buf) -+--> worker B --+--> outCh (buf) --> sink
                          |               |
                          +--> worker C --+

   FAN-OUT: many goroutines read the same channel (work is split, each item goes to exactly ONE worker)
   FAN-IN : many goroutines write to the same channel (merge)
```

```go
func merge[T any](chans ...<-chan T) <-chan T {
    out := make(chan T, len(chans)) // modest buffer smooths wake-ups
    var wg sync.WaitGroup
    wg.Add(len(chans))
    for _, c := range chans {
        go func(c <-chan T) {
            defer wg.Done()
            for v := range c { out <- v }
        }(c)
    }
    go func() { wg.Wait(); close(out) }() // single closer
    return out
}
```

### 9.4 Counting semaphore (bounded concurrency)

```go
sem := make(chan struct{}, 10) // at most 10 concurrent

for _, u := range urls {
    sem <- struct{}{}          // acquire (blocks when 10 in flight)
    go func(u string) {
        defer func() { <-sem }() // release
        fetch(u)
    }(u)
}
// wait for all: refill the semaphore
for i := 0; i < cap(sem); i++ { sem <- struct{}{} }
```

```
 sem (cap=3)           acquire              release
 [ ][ ][ ]   ------>  [X][ ][ ]   ...   [X][X][X]  <- 4th acquire BLOCKS
                                          |
                                          v
                                    one goroutine finishes: <-sem
                                          |
                                          v
                                       [X][X][ ]   -> 4th proceeds
```

`struct{}` has zero size, so this costs only the header. This is justified by memory-model rule 4 (section 6).

### 9.5 Single-result delivery ("future"/reply channel) with capacity 1: leak prevention

```go
func fetchWithTimeout(ctx context.Context, url string) (string, error) {
    res := make(chan string, 1)  // <-- the 1 is crucial
    go func() { res <- slowFetch(url) }()

    select {
    case s := <-res:
        return s, nil
    case <-ctx.Done():
        return "", ctx.Err()   // we walk away
    }
}
```

With an **unbuffered** `res`, if the context fires first, the worker goroutine blocks forever on `res <- ...` (a **goroutine leak**). With capacity 1, the send succeeds into the buffer, the goroutine exits, and the channel is garbage-collected.

### 9.6 Batching (size OR time)

```go
func batcher(in <-chan Event, flush func([]Event), max int, every time.Duration) {
    buf := make([]Event, 0, max)
    t := time.NewTicker(every)
    defer t.Stop()

    for {
        select {
        case ev, ok := <-in:
            if !ok {
                if len(buf) > 0 { flush(buf) }
                return
            }
            buf = append(buf, ev)
            if len(buf) >= max {
                flush(buf)
                buf = make([]Event, 0, max)
            }
        case <-t.C:
            if len(buf) > 0 {
                flush(buf)
                buf = make([]Event, 0, max)
            }
        }
    }
}
```

The *input* buffer absorbs bursts; the *batcher* converts many small items into fewer larger writes (e.g., bulk DB insert, Elasticsearch `_bulk`, S3 multipart).

### 9.7 Signal handling (the standard library requires a buffer)

```go
sigs := make(chan os.Signal, 1)       // MUST be buffered
signal.Notify(sigs, syscall.SIGINT, syscall.SIGTERM)
<-sigs
```

`signal.Notify` does **not block** when sending. If the channel is unbuffered and nobody is receiving at that instant, the signal is **dropped**. The `go vet` tool flags this.

### 9.8 Latest-value slot (capacity 1 + drop-oldest)

```go
type LatestSlot[T any] struct{ ch chan T }

func NewLatest[T any]() *LatestSlot[T] { return &LatestSlot[T]{ch: make(chan T, 1)} }

func (s *LatestSlot[T]) Put(v T) {
    select { case <-s.ch: default: }   // discard stale value if present
    select { case s.ch <- v: default: } // store new (rare race: another Put won, fine)
}
func (s *LatestSlot[T]) C() <-chan T { return s.ch }
```

Used for config reloads, UI re-render requests, "dirty" flags (coalescing: 1000 notifications become 1 wake-up).

### 9.9 Rate limiting with a token bucket

```go
tokens := make(chan struct{}, burst)       // bucket capacity = burst
for i := 0; i < burst; i++ { tokens <- struct{}{} }

go func() {                                // refill at the sustained rate
    t := time.NewTicker(time.Second / time.Duration(ratePerSec))
    defer t.Stop()
    for range t.C {
        select { case tokens <- struct{}{}: default: } // full bucket: discard
    }
}()

func handle(w http.ResponseWriter, r *http.Request) {
    select {
    case <-tokens: serve(w, r)
    default:       http.Error(w, "rate limited", 429)
    }
}
```

```
     refill goroutine                bucket (buffered chan, cap = burst)           request
   +------------------+          +-----------------------------------------+      +--------+
   | 1 token / (1/r)s |  ------> | [t][t][t][t][ ][ ][ ][ ]   (cap=8)      | ---> | take 1 |
   +------------------+          +-----------------------------------------+      +--------+
        (drops token if full)       burst allowed = cap; sustained rate = r
```

### 9.10 Pub/Sub broadcast with per-subscriber buffers

```go
type Broker struct {
    mu   sync.RWMutex
    subs map[chan Event]struct{}
}

func (b *Broker) Subscribe(buf int) <-chan Event {
    ch := make(chan Event, buf)           // each subscriber chooses its own tolerance
    b.mu.Lock(); b.subs[ch] = struct{}{}; b.mu.Unlock()
    return ch
}

func (b *Broker) Publish(e Event) {
    b.mu.RLock(); defer b.mu.RUnlock()
    for ch := range b.subs {
        select {
        case ch <- e:
        default: // slow subscriber: drop rather than block everyone
            metrics.SubscriberDrop.Inc()
        }
    }
}
```

```
                        +-----------------+
   Publish(e) --------> |     Broker      |
                        +--------+--------+
                 non-blocking    |    sends to each
            +--------------------+--------------------+
            v                    v                    v
      +-----------+        +-----------+        +-----------+
      | sub1 chan |        | sub2 chan |        | sub3 chan |
      |  cap=100  |        |  cap=10   |        |  cap=1000 |
      +-----+-----+        +-----+-----+        +-----+-----+
            v                    v                    v
        subscriber 1        subscriber 2         subscriber 3
        (fast)              (slow -> drops)      (bursty, large buffer)

 One slow subscriber must never stall the publisher or other subscribers.
 That isolation is the entire reason each subscriber gets its own buffered channel.
```

### 9.11 Graceful shutdown / drain

```go
func (s *Server) Shutdown(ctx context.Context) error {
    close(s.jobs)                // 1. stop accepting: workers drain remaining buffered jobs
    done := make(chan struct{})
    go func() { s.wg.Wait(); close(done) }()
    select {
    case <-done:     return nil  // 2. all buffered work completed
    case <-ctx.Done(): return ctx.Err() // 3. deadline: give up draining
    }
}
```

This works because closing a buffered channel keeps its data readable (section 3.4). Buffered items are *not lost* on `close`, they're lost only on process exit.

### 9.12 Errgroup-style first-error collection

```go
errCh := make(chan error, n) // n = number of goroutines; nobody blocks sending its error
for i := 0; i < n; i++ { go func() { errCh <- work() }() }
for i := 0; i < n; i++ { if err := <-errCh; err != nil { return err } } // early return leaks nothing
```

---

## 10. Real-World Use Cases and Architectures

### 10.1 Use case index

| Domain | Where buffering appears | Why buffer? |
|--------|-------------------------|-------------|
| **HTTP / API servers** | Request-to-worker queue, rate limiter, connection admission | Absorb traffic spikes; shed load past capacity |
| **Logging libraries** (zap async core, zerolog diode, logrus hooks) | App goroutines -> logging goroutine -> disk/network | Never block a request on slow I/O |
| **Metrics / tracing agents** (OpenTelemetry batch processor, Prometheus exporters) | Span/metric queue -> batch exporter | Batching + non-blocking instrumentation |
| **Message-queue consumers** (Kafka, NATS, RabbitMQ, SQS) | Poll goroutine -> worker channel | Decouple network polling from processing; control prefetch |
| **Database access** | Bulk-insert batcher, connection-slot semaphore | Fewer round trips; bounded connections |
| **Web crawlers / scrapers** | URL frontier, per-host politeness semaphores | Bounded concurrency; many tasks queued |
| **ETL / data pipelines** | Read -> transform -> load stages | Overlap I/O and CPU; bounded memory |
| **Event-driven systems / event bus** | Per-subscriber mailbox | Isolation of slow consumers |
| **Game servers / real-time apps** | Per-connection outbound queue, tick input queue | Smooth frame-time spikes, drop stale state |
| **Stream / media processing** | Frame decode -> filter -> encode | Absorb jitter; keep pipeline full |
| **CLI tools (parallel file scan, build systems)** | Directory walker -> hasher workers | Parallelize I/O, bounded |
| **Schedulers / cron / job runners** | Due-job queue -> executor pool | Decouple timer firing from execution |
| **WebSocket / SSE gateways** | Per-client send buffer | Slow client must not block broadcaster |
| **Distributed systems** (Raft, gossip) | Message inbox per peer, apply queue | Network jitter tolerance |
| **Signals and OS events** | `signal.Notify`, `fsnotify` | Non-blocking producer APIs |
| **Testing** | Fake clocks/events | Avoid goroutine deadlocks in tests |

### 10.2 Architecture A: Async logging pipeline

```
  +---------------------------------------------------------------------------------+
  |                                   Application                                   |
  |                                                                                 |
  |   HTTP handler 1 --+                                                            |
  |   HTTP handler 2 --+--> log.Info(...)                                           |
  |   HTTP handler N --+         |                                                  |
  |                              |  non-blocking try-send                           |
  |                              v                                                  |
  |                  +------------------------+                                     |
  |                  |  logCh  (cap = 8192)   |   FULL?  drop + dropped++           |
  |                  +-----------+------------+                                     |
  |                              |                                                  |
  |                              v                                                  |
  |                      +---------------+    batch: 500 lines OR 200 ms            |
  |                      |   batcher     |                                          |
  |                      +-------+-------+                                          |
  |                              |                                                  |
  |                              v                                                  |
  |                  +------------------------+                                     |
  |                  | flushCh (cap = 4)      |   small: limits in-flight batches   |
  |                  +-----------+------------+                                     |
  |                              |                                                  |
  |                 +------------+-----------+                                      |
  |                 v                        v                                      |
  |           +-----------+            +-----------+                                |
  |           | writer 1  |            | writer 2  |  (retry, backoff)              |
  |           +-----+-----+            +-----+-----+                                |
  +-----------------|------------------------|--------------------------------------+
                    v                        v
              +---------------------------------+
              |  Elasticsearch / Loki / file    |
              +---------------------------------+

  Invariant: request latency is independent of log-sink latency.
  Cost:      under overload, logs are dropped (and counted) instead of slowing users.
```

### 10.3 Architecture B: Kafka consumer with bounded in-memory work queue

```
   Kafka Broker (partitions P0, P1, P2)
        |
        |  fetch (network)
        v
  +---------------------+
  |  poll goroutine     |   - owns the consumer client
  |  (single, serial)   |   - on high watermark: consumer.Pause(partitions)
  +----------+----------+   - on low watermark:  consumer.Resume(partitions)
             |
             |  msgCh  (buffered, cap = 2000)    <== backpressure boundary
             v
  +----------------------------------------------------------------+
  |  msgCh:  [m][m][m][m][m][m][m] .......................[ ][ ]   |
  +--------------+-------------------+-------------------+---------+
                 |                   |                   |
                 v                   v                   v
          +-----------+       +-----------+       +-----------+
          | worker 1  |       | worker 2  |  ...  | worker 32 |
          +-----+-----+       +-----+-----+       +-----+-----+
                |                   |                   |
                +---------+---------+-------------------+
                          v
                   ackCh  (cap = 2000)
                          |
                          v
                 +-------------------+
                 | offset committer  |   commits highest *contiguous* offset
                 +-------------------+

  Why the buffer: network polling latency is hidden; workers never starve.
  Why bounded:   if the DB is slow, msgCh fills -> poller pauses -> Kafka lag grows
                 (visible, safe) instead of the process OOMing.
```

### 10.4 Architecture C: HTTP service with admission control

```
                        Incoming requests
                               |
                               v
                     +--------------------+
                     |  admission gate    |    select { case queue <- req: ... default: 503 }
                     +---------+----------+
                               |
                  accepted     |      rejected (queue full)  ---> 503 Retry-After
                               v
                     +--------------------+
                     | requestQueue       |   buffered, cap = 256
                     | [r][r][r][r]...    |   (≈ 256 * avg_service_time / workers  =  max wait)
                     +---------+----------+
                               |
               +---------------+---------------+
               v               v               v
          +---------+     +---------+     +---------+
          | worker  |     | worker  |     | worker  |   (cap = CPU/DB-connection count)
          +----+----+     +----+----+     +----+----+
               |               |               |
               +-------+-------+---------------+
                       v
                +-------------+
                |  DB / RPC   |
                +-------------+

   Bound on queueing delay:  max_wait ≈ (queue_cap / workers) * avg_service_time
   Example: 256 / 16 workers * 25 ms = 400 ms worst-case wait. Past that: 503, not a pileup.
```

### 10.5 Architecture D: WebSocket gateway, per-client outbound buffer

```
   +-------------+      Broadcast(msg)
   | Hub         | -----------------------------+----------------+
   | goroutine   |                              |                |
   +-------------+                              v                v
                                       +-----------------+  +-----------------+
                                       | client1.send    |  | client2.send    |
                                       | chan []byte     |  | chan []byte     |
                                       | cap=64          |  | cap=64          |
                                       +--------+--------+  +--------+--------+
                                                |                    |
                                                v                    v
                                        writePump goroutine   writePump goroutine
                                                |                    |
                                           (fast TCP)           (SLOW TCP)
                                                                     |
                                       send buffer FULL ---------->  Hub: select default:
                                                                     close(client2.send),
                                                                     disconnect the slow client
```

This is exactly how the popular `gorilla/websocket` chat example is structured: a slow consumer is evicted rather than letting one connection hold up thousands.

### 10.6 Architecture E: ETL pipeline with overlapped I/O and CPU

```
   Time ---->

   Unbuffered (stages lock-step; each waits for the next):
   read   |==R1==|        |==R2==|        |==R3==|
   parse         |==P1==|        |==P2==|        |==P3==|
   write                 |==W1==|        |==W2==|        |==W3==|
   (idle gaps everywhere)

   Buffered (cap 8-64 between stages; stages overlap):
   read   |==R1==|==R2==|==R3==|==R4==|==R5==|
   parse         |==P1==|==P2==|==P3==|==P4==|==P5==|
   write                 |==W1==|==W2==|==W3==|==W4==|==W5==|
   (steady state: all three busy simultaneously; throughput = slowest stage)

   Pipeline throughput = min(stage rates).  Buffers keep the slowest stage from ever starving.
```

### 10.7 Real examples inside the Go ecosystem

- `os/signal.Notify`: requires a buffered channel (documented contract).
- `time.Ticker.C` / `time.Timer.C`: historically buffered with capacity 1 (ticks are dropped if you're slow; intentional "latest tick" semantics). Since Go 1.23 they are implemented as synchronous channels (`cap` reports 0) but keep the same no-pile-up behavior.
- `net/http` server: uses connection-per-goroutine; queueing/limits are typically built with buffered-channel semaphores (`golang.org/x/net/netutil.LimitListener` uses one).
- `golang.org/x/sync/errgroup` with `SetLimit`: semaphore semantics.
- `context`: `Done()` channels are closed (broadcast), not buffered.
- Logging libs (`zap` BufferedWriteSyncer, `zerolog` diode, `lumberjack` rotation workers) use bounded queues + drop policies.
- `gorilla/websocket` chat example: per-client buffered `send` channel.
- OpenTelemetry SDK `BatchSpanProcessor`: bounded queue; drops spans when full (`maxQueueSize`), exports in batches (`maxExportBatchSize`, `scheduledDelay`).

---

## 11. Pitfalls, Bugs, and Anti-Patterns

### 11.1 "Adding a buffer fixed my deadlock"

```go
ch := make(chan int)        // unbuffered
ch <- 1                      // deadlock: no receiver. fatal error: all goroutines are asleep
fmt.Println(<-ch)
```

Changing to `make(chan int, 1)` "works", but you only masked a **design error**. With 2 sends it deadlocks again. Fix the **protocol** (who sends, who receives, who closes), then size the buffer.

### 11.2 Goroutine leaks

```go
func leaky() int {
    ch := make(chan int)             // unbuffered
    go func() { ch <- compute() }()  // blocks forever if nobody receives
    select {
    case v := <-ch:    return v
    case <-time.After(time.Second): return -1   // returns; sender goroutine is stuck FOREVER
    }
}
```

Fixes: capacity 1 (section 9.5), or pass `ctx` and select on `ctx.Done()` in the sender.

**Leak shapes:** blocked sender (no receiver), blocked receiver (no sender / never closed), `range` over a never-closed channel, missing `ctx` checks.

### 11.3 Sending on a closed channel (panic)

```go
// WRONG: multiple producers, one of them closes.
go func() { for ... { ch <- v }; close(ch) }() // producer A closes
go func() { for ... { ch <- v } }()             // producer B => panic: send on closed channel
```

Fix: a **coordinator** closes after all producers finish:

```go
var wg sync.WaitGroup
// ... wg.Add / producers call wg.Done ...
go func() { wg.Wait(); close(ch) }()
```

### 11.4 Receiver closing a channel

Receivers don't know whether senders are still active. Closing from the receive side leads to panics. If receivers must tell senders to stop, use a separate **`done`/`ctx.Done()` channel**, not `close(dataCh)`.

### 11.5 Using `len(ch)` for decisions

```go
if len(ch) < cap(ch) { ch <- v }   // RACE: another goroutine may fill it between check and send
```

Use `select { case ch <- v: default: }`.

### 11.6 Oversized buffers ("just make it 1,000,000")

Consequences: huge memory, huge latency (bufferbloat), delayed failure detection, lost data on crash, delayed backpressure. If you truly need a large durable queue, use a *real* queue (Kafka, Redis streams, SQS, disk-backed WAL).

### 11.7 Unbounded workaround queues

```go
// "Infinite channel": slice + goroutine in the middle.
```

This removes backpressure, so memory grows until OOM. Almost always a design smell. If you truly need it, add metrics, a hard ceiling, and a shedding policy.

### 11.8 Copying large values through channels

`chan [1<<20]byte` copies 1 MiB per send and per receive, and the buffer allocates `cap * 1 MiB`. Send pointers or slices (with clear ownership rules), or use `sync.Pool` buffers.

### 11.9 Ownership violation after sending a pointer

```go
ch <- p
p.count++     // data race: receiver may be reading p right now
```

### 11.10 Zero-value confusion on closed channels

```go
v := <-ch           // closed channel yields 0 / "" / nil, indistinguishable from real data
v, ok := <-ch       // use ok to distinguish
```

### 11.11 `select` starvation and priority assumptions

`select` picks randomly among ready cases. If you need priority ("always handle `quit` first"), check it separately:

```go
select { case <-quit: return; default: }
select { case <-quit: return; case v := <-data: handle(v) }
```

### 11.12 Timer leaks with `time.After` in loops

`time.After` in a hot loop allocates a timer per iteration (pre Go 1.23 these were not collected until firing). Prefer `time.NewTimer` + `Reset`, or `context.WithTimeout`.

### 11.13 Reading from a channel with no closer + `range`

`for v := range ch` never terminates unless `ch` is closed. Document who closes.

### 11.14 Order assumptions across multiple producers or workers

A single channel is FIFO in *arrival order*, but with multiple workers pulling from it and finishing at different times, **output order is not input order**. If you need ordering, carry a sequence number and reorder at the end, or shard by key (same key always goes to the same worker).

### 11.15 Hidden latency and head-of-line blocking

One slow item at the head of a single worker's work blocks the items behind it. Mitigation: more workers, per-key sharding, per-item timeouts.

---

## 12. Performance Characteristics

### 12.1 Rough cost model (order of magnitude; hardware dependent)

| Operation | Approx. cost |
|-----------|--------------|
| Uncontended buffered send/recv (no blocking) | ~20-50 ns |
| Send/recv requiring goroutine park + wake (unbuffered handoff) | ~200-500 ns+ |
| `sync.Mutex` lock/unlock uncontended | ~10-20 ns |
| `atomic.AddInt64` | ~5-10 ns |
| Goroutine creation | ~0.3-1 µs, ~2 KB initial stack |

Takeaways:
- Buffered channels are **cheap when they don't block**; most of the savings come from *avoiding parks*.
- Channels are **not** the fastest synchronization primitive. For protecting a counter or small struct, `sync.Mutex` / `atomic` is faster. Use channels for **ownership transfer, pipelines, and signaling**.

### 12.2 Effect of buffer size on throughput (typical shape)

```
 Throughput
   ^
   |                          ______________________  <- plateau (stage rate limited)
   |                    _____/
   |               ____/
   |          ____/
   |     ____/
   |____/
   +--------------------------------------------------> buffer size
   0   1   4   16   64   256  1024
        ^    ^
        |    +--  often "knee" of the curve: most gain captured here
        +-- going 0 -> 1 -> small removes most context switches

   Beyond the knee: more memory + more latency, no more throughput.
```

Always **benchmark your own workload**:

```go
func BenchmarkChan(b *testing.B) {
    for _, size := range []int{0, 1, 16, 128, 1024} {
        b.Run(fmt.Sprintf("buf=%d", size), func(b *testing.B) {
            ch := make(chan int, size)
            done := make(chan struct{})
            go func() { for range ch {}; close(done) }()
            b.ResetTimer()
            for i := 0; i < b.N; i++ { ch <- i }
            close(ch); <-done
        })
    }
}
```

### 12.3 Lock contention and sharding

All producers/consumers share one `hchan.lock`. With dozens of cores hammering one channel, you may see mutex contention in `pprof` (`runtime.lock`, `runtime.chansend`).

```
 BEFORE:                               AFTER (sharded by key or round-robin):

  P1 P2 P3 P4 P5 P6                     P1 P2     P3 P4     P5 P6
    \  \  |  |  /  /                      \ /       \ /       \ /
     +--- ONE chan ---+                  chan[0]   chan[1]   chan[2]
          |  lock hot                       |         |         |
     C1 C2 C3 C4 C5 C6                     C1 C2     C3 C4     C5 C6
```

Sharding also gives you **ordering per key** for free (hash(key) % shards).

### 12.4 Batching amortizes per-item overhead

Instead of `chan Item`, send `chan []Item` (batches of 64-512). Per-item channel overhead drops by ~the batch factor. Trade-off: added latency (wait to fill batch), so pair with a time-based flush.

### 12.5 Cache and false sharing

Producer and consumer constantly bounce the same cache lines (`qcount`, `sendx`, `recvx`, the slots). Very high-rate paths (millions/s) sometimes switch to specialized lock-free SPSC ring buffers with padding. That is rarely necessary; measure first.

### 12.6 GC considerations

- The buffer slot is **cleared after receive** so the GC can reclaim large payloads promptly.
- A channel of pointers keeps the pointed-to objects alive while queued.
- Huge buffers of pointer-containing types increase GC scan work.

---

## 13. Testing, Debugging, and Observability

### 13.1 Detect problems

| Tool | What it finds |
|------|---------------|
| `go test -race` | Data races on payloads shared via pointers; misuse of shared state |
| Runtime deadlock detector | `fatal error: all goroutines are asleep - deadlock!` (only when **every** goroutine is blocked) |
| `pprof` goroutine profile (`/debug/pprof/goroutine?debug=2`) | Goroutines parked in `chan send` / `chan receive`, which exposes leaks |
| `go.uber.org/goleak` | Fails tests that leave goroutines behind |
| `runtime.NumGoroutine()` | Quick leak signal (steadily rising = leak) |
| `go vet` | `signal.Notify` with unbuffered channel, copylocks, etc. |
| Execution trace (`go tool trace`) | Blocking profile, scheduler latency, who wakes whom |
| Block profile (`runtime.SetBlockProfileRate`) | Time spent blocked on channel operations |

### 13.2 Reading a goroutine dump

```
goroutine 42 [chan send, 17 minutes]:
main.worker(0xc000012345)
        /app/worker.go:31 +0x65
created by main.start
        /app/main.go:12 +0x9a

goroutine 43 [chan receive, 17 minutes]:
...
goroutine 44 [select, 3 minutes]:
...
```

- `[chan send]` for a long time: buffer full / no receiver: **consumer is stuck or gone**.
- `[chan receive]` for a long time: empty / no sender: **producer gone or channel never closed**.
- `[select]`: waiting on multiple channels (check which).
- `[chan send (nil chan)]` / `[chan receive (nil chan)]`: you blocked on a nil channel (bug).

### 13.3 Metrics to export for every important buffered channel

```go
go func() {
    t := time.NewTicker(5 * time.Second)
    for range t.C {
        queueDepth.Set(float64(len(ch)))
        queueUtil.Set(float64(len(ch)) / float64(cap(ch)))
    }
}()
// plus counters: enqueued_total, dropped_total, dequeued_total, enqueue_wait_seconds (histogram)
```

How to read them:

| Signal | Meaning |
|--------|---------|
| Utilization ~ 0 | Consumer keeps up (or oversized buffer) |
| Utilization oscillates, peaks < 100% | Healthy burst absorption |
| Utilization pinned at ~100% | Consumer is the bottleneck: scale consumers, optimize, or shed load |
| `dropped_total` rising | You are losing data by policy: alert |
| Enqueue wait histogram growing | Backpressure is reaching producers |

### 13.4 Testing patterns

**Deterministic buffering test:**

```go
func TestBufferFullBlocks(t *testing.T) {
    ch := make(chan int, 2)
    ch <- 1
    ch <- 2
    select {
    case ch <- 3:
        t.Fatal("expected buffer full")
    default: // correct
    }
    if got := <-ch; got != 1 { t.Fatalf("FIFO violated: %d", got) }
}
```

**No leak test:**

```go
func TestNoLeak(t *testing.T) {
    defer goleak.VerifyNone(t)
    _, _ = fetchWithTimeout(ctxCancelled(), "x")
}
```

**Avoid `time.Sleep` in tests**; synchronize with channels/WaitGroups, or use `testing/synctest` (Go 1.24+ experimental / newer stable) to get a fake clock.

### 13.5 Debugging checklist when "something hangs"

1. Dump goroutines (`kill -QUIT <pid>` or pprof). Find the `chan send`/`chan receive` stacks.
2. For each blocked channel ask: **who is supposed to be on the other side? Is it alive?**
3. Is the channel ever closed? By whom? Can there be two closers?
4. Is a `select` missing a `ctx.Done()`/timeout branch?
5. Is the buffer full because the consumer is slow, dead, or in a cycle (A waits on B waits on A)?
6. Print `len(ch)/cap(ch)`: pinned at cap = stalled consumer; at 0 = stalled producer.

---

## 14. Equivalents in Other Languages and Systems

The *concept* (bounded FIFO between producer and consumer, with a capacity-0 rendezvous option) exists everywhere under different names.

### 14.1 Mapping table

| Ecosystem | Unbuffered / rendezvous | Bounded buffer | Other buffering modes | Full behavior |
|-----------|-------------------------|----------------|-----------------------|---------------|
| **Go** | `make(chan T)` | `make(chan T, n)` | none built-in (build with select) | send blocks |
| **Kotlin coroutines** | `Channel(RENDEZVOUS)` | `Channel(n)` | `CONFLATED` (keep latest), `UNLIMITED`, `BUFFERED` (default 64) | configurable `onBufferOverflow`: `SUSPEND`, `DROP_OLDEST`, `DROP_LATEST` |
| **Rust** (`std::sync::mpsc`) | `sync_channel(0)` | `sync_channel(n)` | `channel()` is **unbounded** | `send` blocks (sync_channel) |
| **Rust** (`tokio::sync::mpsc`) | n/a | `mpsc::channel(n)` | `unbounded_channel()`; `watch` (latest value), `broadcast` (ring, lagging receivers) | `send().await` waits; `try_send` errors |
| **Java** | `SynchronousQueue` | `ArrayBlockingQueue(n)`, `LinkedBlockingQueue(n)` | `LinkedTransferQueue`, `Disruptor` ring buffer | `put` blocks, `offer` returns false, `offer(timeout)` |
| **Python threads** | n/a | `queue.Queue(maxsize=n)` | `LifoQueue`, `PriorityQueue`; `maxsize=0` = infinite | `put` blocks, `put_nowait` raises `Full` |
| **Python asyncio** | n/a | `asyncio.Queue(maxsize=n)` | same | `await put` waits |
| **Node.js streams** | n/a | `highWaterMark` | `pause()/resume()`, `drain` event | `write()` returns `false` = stop, wait for `'drain'` |
| **Erlang/Elixir** | n/a (mailbox) | mailbox is unbounded by default; use bounded patterns (GenStage demand, `max_heap_size`) | GenStage/Broadway demand-driven backpressure | n/a |
| **.NET** | n/a | `Channel.CreateBounded<T>(n)` | `CreateUnbounded`, `BoundedChannelFullMode` (Wait, DropNewest, DropOldest, DropWrite) | configurable |
| **Reactive Streams (RxJava, Reactor, Akka Streams)** | n/a | `onBackpressureBuffer(n)` | `onBackpressureDrop`, `onBackpressureLatest` | strategy-based |
| **Unix pipes** | n/a | kernel pipe buffer (64 KiB on Linux by default) | `fcntl(F_SETPIPE_SZ)` | `write` blocks when full |
| **TCP sockets** | n/a | send/recv buffers + window | `SO_SNDBUF`, `SO_RCVBUF` | `write` blocks / `EAGAIN` |

### 14.2 Kotlin quick examples

```kotlin
val rendezvous = Channel<Int>(Channel.RENDEZVOUS)    // like Go unbuffered
val bounded    = Channel<Int>(capacity = 100)
val latest     = Channel<Int>(Channel.CONFLATED)     // keeps only the latest (drop-oldest, cap 1)
val shed       = Channel<Int>(10, BufferOverflow.DROP_OLDEST)
```

### 14.3 Java quick example

```java
BlockingQueue<Task> q = new ArrayBlockingQueue<>(1024);   // bounded
q.put(task);                                   // blocks when full (backpressure)
boolean ok = q.offer(task);                    // non-blocking try-send (drop policy)
boolean ok2 = q.offer(task, 50, TimeUnit.MILLISECONDS); // timeout policy
Task t = q.take();                             // blocks when empty
```

### 14.4 Rust (tokio) quick example

```rust
let (tx, mut rx) = tokio::sync::mpsc::channel::<Job>(128); // bounded
tx.send(job).await?;          // waits when full
match tx.try_send(job) { Err(TrySendError::Full(_)) => shed(), _ => {} }
```

### 14.5 Node.js streams backpressure

```js
if (!writable.write(chunk)) {          // internal buffer exceeded highWaterMark
  readable.pause();                    // stop producing
  writable.once('drain', () => readable.resume());
}
```

### 14.6 OS-level analogy: it's buffers all the way down

```
  Your goroutine
       |  ch <- v                                 (Go channel buffer)
       v
  Go net.Conn.Write()
       |
       v
  Kernel socket SEND buffer   (SO_SNDBUF)         (OS buffer)
       |
       v
  NIC TX queue / qdisc                            (driver/hardware buffer)
       |
       v
  ======= network (router queues) =======
       |
       v
  NIC RX ring -> kernel socket RECV buffer (SO_RCVBUF, TCP window)
       |
       v
  Remote app read() -> remote channel/queue

  Each hop is a bounded buffer. TCP's advertised window is backpressure from the
  remote recv-buffer propagating all the way back to your `ch <- v`.
```

If you understand channel buffering, you understand TCP flow control, Kafka consumer lag, Unix pipe semantics, and reactive-streams demand.

---

## 15. Decision Flowchart

```
                    Need to pass data between goroutines?
                                   |
                                   v
                    Is it just protecting shared state (counter, map)?
                          |                          |
                         yes                         no
                          v                          |
                 Use sync.Mutex / atomic             v
                                          Do sender and receiver need to
                                          synchronize at the exact moment?
                                          (handoff, ack, strict ordering)
                                               |               |
                                              yes              no
                                               v               v
                                       UNBUFFERED (0)    Do you know exactly how many
                                                         values will be sent?
                                                              |           |
                                                             yes          no
                                                              v           v
                                                    cap = that count    Is it a one-shot
                                                    (no leak possible)  result/signal?
                                                                          |        |
                                                                         yes       no
                                                                          v        v
                                                                      cap = 1   Is there rate mismatch /
                                                                                bursts / I/O latency to hide?
                                                                                     |            |
                                                                                    yes           no
                                                                                     v            v
                                                                          Size from burst      UNBUFFERED
                                                                          math (section 7)     (start simple)
                                                                                     |
                                                                                     v
                                                                          Choose FULL-POLICY (section 8):
                                                          +-----------+-----------+------------+
                                                          v           v           v            v
                                                        block      drop-newest  drop-oldest  timeout/503
                                                                                     |
                                                                                     v
                                                                  Export len/cap, dropped, wait-time metrics
```

---

## 16. Cheat Sheet

```
CREATE      make(chan T)            unbuffered       make(chan T, n)   buffered
SEND        ch <- v                 blocks when: unbuffered w/o receiver, or buffer full
RECEIVE     v := <-ch               blocks when: empty (and open)
CHECK       v, ok := <-ch           ok=false => closed AND drained
CLOSE       close(ch)               sender-side only, once; buffered data stays readable
LOOP        for v := range ch       ends when closed + drained
TRY-SEND    select{case ch<-v: default:}
TRY-RECV    select{case v:=<-ch: default:}
TIMEOUT     select{case ch<-v: case <-time.After(d):}   (prefer ctx / reusable timer)
LEN/CAP     len(ch) = queued now (stale instantly: metrics only), cap(ch) = capacity
NIL CHAN    blocks forever; use to disable a select case
CLOSED CHAN send panics; recv returns zero immediately after draining

SIZES       0 = sync handoff | 1 = signal / one result / leak-proof | N = known count or burst math
FULL POLICY block | drop-newest | drop-oldest | timeout-fail
RULES       one owner closes; ownership transfers on send; every send/recv can be cancelled (ctx);
            bounded > unbounded; measure len/cap; buffering smooths bursts, never sustained overload
```

### The 10 commandments of channel buffering

1. Start unbuffered; add a buffer for a **reason you can state**.
2. A buffer **absorbs bursts**; it does not raise steady-state throughput.
3. Always define **what happens when it's full**.
4. Keep it **bounded** (bounded memory = bounded failure).
5. Size it by **time worth of load** or **known cardinality**, not by guess.
6. **Capacity 1** prevents leaks in one-shot result delivery.
7. **One closer**, always on the sender side/coordinator.
8. Never use `len(ch)` to make a decision, only to **observe**.
9. After sending a pointer, **don't touch it**.
10. **Instrument** every important queue: depth, drops, wait time.

---

## 17. Interview Questions and Exercises

### Conceptual

1. **Difference between buffered and unbuffered channels?** *Rendezvous vs bounded queue; blocking rules; memory-model rule 3 vs rule 4.*
2. **Does `close` on a buffered channel lose data?** *No. Receivers drain first, then get zero value with `ok=false`.*
3. **Why does `signal.Notify` need a buffered channel?** *Sender (runtime) never blocks; unbuffered + busy receiver = dropped signal.*
4. **Why capacity 1 for a timeout-able single result?** *So the worker can send and exit even if nobody is receiving; prevents goroutine leak.*
5. **When is a buffer harmful?** *Bufferbloat latency, hidden deadlocks, memory, data loss on crash, delayed backpressure.*
6. **Explain direct handoff.** *If a receiver is parked, send copies to its stack and bypasses the buffer.*
7. **How does a buffered channel implement a semaphore, and what memory-model rule backs it?** *Rule 4: k-th receive happens-before (k+C)-th send completes.*
8. **Why can't you decide with `len(ch)`?** *TOCTOU race; use `select/default`.*
9. **Are Go channels lock-free?** *No; each `hchan` has a mutex. Contention can be mitigated by sharding/batching.*
10. **How do blocked channel ops interact with the scheduler?** *Goroutine parks as a `sudog`; the M runs another G; no OS thread blocks.*

### Exercises (in increasing difficulty)

1. Write a function that processes 1M items with exactly 8 workers and a job buffer; prove no goroutine leaks on early error.
2. Implement `TrySend`, `SendTimeout`, and `SendLatest` helpers (generic) and benchmark them.
3. Build a bounded log pipeline: try-send ingestion, batcher (500 lines or 200 ms), 2 writers, drop counter, and a shutdown that drains.
4. Implement a pub/sub broker where slow subscribers are disconnected after 3 consecutive drops.
5. Build a rate limiter using a buffered channel token bucket; verify burst and sustained rate with a test using a fake clock.
6. Create a 3-stage pipeline with cancellation; inject a slow stage and graph `len/cap` of each channel to *see* backpressure propagate.
7. Implement per-key ordered processing by sharding across N buffered channels; show ordering holds per key but not globally.
8. Reproduce a goroutine leak using an unbuffered result channel + timeout, find it via pprof, then fix it using capacity 1 and again using `ctx`.
9. Implement a bounded "work-stealing-free" priority queue using two channels (high, low) and a `select` that prefers high priority without starving low.
10. Benchmark buffer sizes {0, 1, 16, 128, 1024} for a producer/consumer pair and locate the knee of the curve on your machine.

### One-paragraph summary

> A buffered channel is a bounded, thread-safe FIFO ring buffer (guarded by a mutex, with wait-queues for blocked senders and receivers) that decouples producers from consumers in time. It smooths bursts and amortizes goroutine context switches, it bounds memory, and when full it creates **backpressure**. It never increases sustained throughput, never makes a design correct by itself, and always needs an explicit answer to: *how big, who closes, what happens when full, and how do I observe it?* Master those four questions and you can reason about every queue in every system you will ever build, from a Go pipeline to Kafka to a TCP socket.

---
*End of guide.*
