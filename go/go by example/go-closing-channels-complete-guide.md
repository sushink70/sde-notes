# Closing Channels in Go: The Complete In-Depth Guide

> Goal of this guide: build a **correct mental model** of `close(ch)` so that you can reason about
> concurrency bugs, design shutdown logic, and know exactly *where*, *why* and *by whom* a channel
> should be closed in real production systems.

---

## Table of Contents

1. [The One-Sentence Mental Model](#1-the-one-sentence-mental-model)
2. [Channel Refresher (just enough)](#2-channel-refresher-just-enough)
3. [Channel Internals: What `close` Really Does](#3-channel-internals-what-close-really-does)
4. [Complete Semantics Table](#4-complete-semantics-table)
5. [Receiving From a Closed Channel](#5-receiving-from-a-closed-channel)
6. [Panics Related to Closing](#6-panics-related-to-closing)
7. [Closing Is a Signal, Not Cleanup](#7-closing-is-a-signal-not-cleanup)
8. [The Golden Rule: Who Should Close?](#8-the-golden-rule-who-should-close)
9. [Close as Broadcast](#9-close-as-broadcast)
10. [Buffered Channels and Close](#10-buffered-channels-and-close)
11. [Nil Channels + Close (select tricks)](#11-nil-channels--close-select-tricks)
12. [Go Memory Model Guarantees](#12-go-memory-model-guarantees)
13. [The Patterns Catalogue (where close is used)](#13-the-patterns-catalogue)
14. [Real-World Use Cases and Architectures](#14-real-world-use-cases-and-architectures)
15. [How the Standard Library Uses Closed Channels](#15-how-the-standard-library-uses-closed-channels)
16. [Closing With Multiple Senders / Receivers (Go101 rules)](#16-closing-with-multiple-senders--receivers)
17. [Safe-Close Helpers](#17-safe-close-helpers)
18. [When You Should NOT Close a Channel](#18-when-you-should-not-close-a-channel)
19. [Pitfalls and Anti-Patterns](#19-pitfalls-and-anti-patterns)
20. [Goroutine Leaks and Detection](#20-goroutine-leaks-and-detection)
21. [Testing Close Logic](#21-testing-close-logic)
22. [Performance Notes](#22-performance-notes)
23. [Decision Flowchart](#23-decision-flowchart)
24. [Cheat Sheet](#24-cheat-sheet)
25. [Interview Questions and Answers](#25-interview-questions-and-answers)
26. [Practice Exercises](#26-practice-exercises)

---

## 1. The One-Sentence Mental Model

> **`close(ch)` is the *sender side* announcing: "No more values will ever be sent on this channel."**

Everything else follows from this sentence.

| Consequence                                             | Why                                                                 |
|---------------------------------------------------------|---------------------------------------------------------------------|
| Only **senders** (or the owner of the send side) close  | Receivers cannot know if others still intend to send                |
| Closing twice **panics**                                | The announcement can be made only once                              |
| Sending after close **panics**                          | It would contradict the announcement                                |
| Receiving after close **never blocks**                  | The receiver is told "nothing more is coming", immediately          |
| Buffered data is **still delivered** after close        | Close promises "no new values", not "discard existing values"       |
| Closing is **optional** for GC                          | It is a *signal*, not a resource release                            |
| A closed channel can wake **all** receivers at once     | The "no more" fact is true for everyone: a broadcast                |

Keep this table in your head. When you are unsure about any close-related behavior, derive it from the sentence above.

### Two uses of close

```
+-------------------------------------------------------------+
|                  WHY DO WE CLOSE CHANNELS?                  |
+-------------------------------+-----------------------------+
|  (A) END-OF-DATA SIGNAL       |  (B) BROADCAST / CANCEL     |
|-------------------------------|-----------------------------|
|  "I am done producing."       |  "Everybody stop / wake up" |
|  Consumers use `range ch`     |  Many goroutines wait on    |
|  to drain then exit.          |  <-done. close(done) wakes  |
|                               |  ALL of them.               |
|  Data-carrying channels       |  Usually chan struct{}      |
|  e.g. chan Job, chan Row      |  e.g. ctx.Done(), quit      |
+-------------------------------+-----------------------------+
```

---

## 2. Channel Refresher (just enough)

```go
ch := make(chan int)      // unbuffered: send and receive must rendezvous
bch := make(chan int, 3)  // buffered: capacity 3

ch <- 1        // send
v := <-ch      // receive
v, ok := <-ch  // receive + "is the channel still delivering real values?"
close(ch)      // announce: no more sends
len(bch)       // number of queued elements
cap(bch)       // capacity
```

Directional types (important for *enforcing* who may close):

```go
func producer(out chan<- int) { /* can send and CLOSE */ }
func consumer(in <-chan int)  { /* can only receive */ }
```

`close(in)` on a `<-chan int` is a **compile-time error**:

```
invalid operation: cannot close receive-only channel in
```

This is the compiler helping you enforce the golden rule (section 8): **give consumers the `<-chan T` type** and they physically cannot close it.

---

## 3. Channel Internals: What `close` Really Does

A channel is a pointer to a runtime struct `hchan` (see `runtime/chan.go`). Simplified:

```go
type hchan struct {
    qcount   uint           // number of elements currently in the buffer
    dataqsiz uint           // buffer capacity
    buf      unsafe.Pointer // ring buffer
    elemsize uint16
    closed   uint32         // 0 = open, 1 = closed
    elemtype *_type
    sendx    uint           // ring buffer send index
    recvx    uint           // ring buffer receive index
    recvq    waitq          // goroutines blocked on receive (linked list of sudog)
    sendq    waitq          // goroutines blocked on send    (linked list of sudog)
    lock     mutex          // protects ALL fields
}
```

### Memory picture of an open buffered channel

```
 ch (variable, 8 bytes)
 +--------+
 |  ptr --|-----+
 +--------+     |
                v
 hchan
 +-----------------------------------------------------------+
 | lock      : mutex                                         |
 | closed    : 0                                             |
 | qcount    : 3        dataqsiz : 5      elemsize : 8       |
 | sendx     : 3        recvx    : 0                         |
 | buf  ------------------+                                  |
 | recvq : (empty)         |                                 |
 | sendq : (empty)         |                                 |
 +-------------------------|---------------------------------+
                           v
          ring buffer (capacity 5)
          +-----+-----+-----+-----+-----+
          | 10  | 20  | 30  |  .  |  .  |
          +-----+-----+-----+-----+-----+
            ^                 ^
          recvx=0           sendx=3
```

### What happens inside `closechan(c)`

```
close(ch)
   |
   v
+--------------------------------------------+
| 1. if c == nil           -> panic            |   "close of nil channel"
| 2. lock(&c.lock)                             |
| 3. if c.closed != 0      -> unlock + panic   |   "close of closed channel"
| 4. c.closed = 1                              |
| 5. collect ALL waiting receivers (recvq)     |
|      -> each gets zero value, ok = false     |
| 6. collect ALL waiting senders (sendq)       |
|      -> each is marked to PANIC when it wakes|
| 7. unlock(&c.lock)                           |
| 8. goready() every collected goroutine       |
|      (put them on the run queue)             |
+--------------------------------------------+
```

Key insights from the algorithm:

1. **Close is O(n)** in the number of blocked goroutines (it must wake each one).
2. **All** blocked receivers are released at once. This is why close works as a broadcast.
3. **Blocked senders panic.** If a goroutine is *blocked in a send* when another goroutine closes the channel, that blocked sender will panic with `send on closed channel`. This is the root of many production bugs.
4. The buffer is **not cleared**. Queued items remain until received.

### State machine of a channel

```
                 make(chan T, n)
                       |
                       v
              +------------------+
              |      OPEN        |<--------+
              |  send:  ok/block |         | send/recv
              |  recv:  ok/block |---------+
              +--------+---------+
                       |
                       | close(ch)  (by sender-side owner)
                       v
              +------------------+
              |  CLOSED + DATA   |   (buffer still has items)
              |  send:  PANIC    |
              |  recv:  value,   |-----+
              |         ok=true  |     | recv (drain one)
              +--------+---------+<----+
                       |
                       | buffer empty
                       v
              +------------------+
              | CLOSED + DRAINED |
              |  send:  PANIC    |
              |  recv:  zero,    |
              |         ok=false |  (never blocks, forever)
              +------------------+
              close again -> PANIC from ANY closed state
```

---

## 4. Complete Semantics Table

| Operation                 | nil channel     | open channel         | closed (has buffered data) | closed (empty)            |
|---------------------------|-----------------|----------------------|----------------------------|---------------------------|
| `ch <- v` (send)          | blocks forever  | ok, or blocks        | **panic**                  | **panic**                 |
| `v := <-ch` (receive)     | blocks forever  | ok, or blocks        | value, immediately         | zero value, immediately   |
| `v, ok := <-ch`           | blocks forever  | `ok == true`         | `ok == true`               | `ok == false`             |
| `for v := range ch`       | blocks forever  | loops until closed   | drains then exits          | exits immediately         |
| `close(ch)`               | **panic**       | ok                   | **panic**                  | **panic**                 |
| `len(ch)`                 | 0               | queued count         | remaining count            | 0                         |
| `cap(ch)`                 | 0               | capacity             | capacity                   | capacity                  |
| `select` send case        | never ready     | ready if can proceed | ready, then **panics**     | ready, then **panics**    |
| `select` receive case     | never ready     | ready if value ready | ready                      | ready (always)            |

Notable: a **send case in `select` on a closed channel is "ready"** and will panic when chosen. A closed channel does not make `select` skip the send; it makes it *explode*.

---

## 5. Receiving From a Closed Channel

### 5.1 The comma-ok idiom

```go
v, ok := <-ch
```

- `ok == true`: `v` is a real value sent by a sender.
- `ok == false`: channel is **closed and drained**; `v` is the **zero value** of the type.

Why is this needed? Because the zero value alone is ambiguous:

```go
ch := make(chan int, 2)
ch <- 0           // a real zero
close(ch)

v1, ok1 := <-ch  // v1 = 0, ok1 = true   <- real value (zero)
v2, ok2 := <-ch  // v2 = 0, ok2 = false  <- closed sentinel
```

### 5.2 `for range`

```go
for v := range ch {
    process(v)
}
// Reaches here only after ch is closed AND drained.
```

`range` is syntactic sugar for:

```go
for {
    v, ok := <-ch
    if !ok {
        break
    }
    process(v)
}
```

**If nobody ever closes `ch`, the `range` loop blocks forever** and the goroutine leaks. This is the most common reason you *must* close data channels.

### 5.3 Single-value receive hides closure

```go
v := <-ch   // after close this silently returns zero value forever
```

Dangerous in loops: you'll spin at 100% CPU processing zero values.

```go
// BUG: busy loop after close
for {
    v := <-ch
    handle(v) // keeps receiving zero values forever
}
```

Fix: use `v, ok := <-ch` or `range`.

### 5.4 Zero value of pointers/interfaces = nil

```go
ch := make(chan *Job)
close(ch)
j := <-ch      // j == nil
j.Run()        // nil pointer dereference panic!
```

When the element type is a pointer, closing + naive receive often surfaces as a nil dereference. Always check `ok`.

---

## 6. Panics Related to Closing

There are exactly three runtime panics, plus one compile error.

| Situation                          | Message                                        | Kind          |
|------------------------------------|------------------------------------------------|---------------|
| `close(nilCh)`                     | `panic: close of nil channel`                  | runtime       |
| `close(ch)` twice                  | `panic: close of closed channel`               | runtime       |
| `ch <- v` after close              | `panic: send on closed channel`                | runtime       |
| `close(recvOnlyCh)`                | `cannot close receive-only channel`            | compile error |

```go
var ch chan int
close(ch) // panic: close of nil channel
```

```go
ch := make(chan int)
close(ch)
close(ch) // panic: close of closed channel
```

```go
ch := make(chan int, 1)
close(ch)
ch <- 1 // panic: send on closed channel
```

### The racing-sender panic (most important one)

```go
ch := make(chan int)

go func() {          // sender
    for i := 0; ; i++ {
        ch <- i      // may be blocked here or about to send...
    }
}()

time.Sleep(time.Second)
close(ch)            // ...when the main goroutine closes -> sender panics
```

Timeline:

```
 time -->
 Main:    ...sleep...........close(ch)
                                |
 Sender:  ch<-1  ch<-2  ch<-3 [blocked in send] ----> PANIC: send on closed channel
```

There is **no way to check "is it closed?" safely**. Any `isClosed(ch)` helper has a time-of-check/time-of-use race (see section 17).

---

## 7. Closing Is a Signal, Not Cleanup

Unlike files (`f.Close()`), network connections, or DB handles, **a channel does not hold OS resources**. The garbage collector reclaims an unreachable channel regardless of whether it was closed.

```go
ch := make(chan int)
// use it, never close it, drop all references -> GC collects it. Totally fine.
```

So when do you close? **Only when someone is waiting for the "no more data" / "stop" signal.**

| Do you have a goroutine doing `range ch` or `<-done`? | Close needed? |
|-------------------------------------------------------|---------------|
| Yes                                                   | **Yes**, otherwise it leaks |
| No (single value passed, receiver knows the count)    | No            |

### What leaks, really?

Not the channel. The leak is the **goroutine blocked on the channel**:

```
 goroutine G1                 channel ch (never closed)
 +------------------+         +----------------+
 | for v := range ch|-- waits-| recvq: [G1]    |
 +------------------+         +----------------+
        ^                              |
        |   G1 is pinned by recvq,     |
        |   ch is pinned by G1's stack |
        +------------------------------+
   Neither can be collected: a goroutine blocked forever is a leak (memory + stack, ~2KB+).
```

---

## 8. The Golden Rule: Who Should Close?

> **The goroutine that owns the send side closes the channel. Never the receiver.**

Rationale: a receiver cannot know whether other senders still exist or are about to send. Closing from the receiver side risks `send on closed channel` panics in senders.

### Ownership diagram

```
   OWNER (creates, sends, closes)                 CONSUMERS (receive only)
 +---------------------------------+          +---------------------------+
 | out := make(chan Item)          |          |  for it := range out {    |
 | go func() {                     |   out    |      use(it)              |
 |     defer close(out)  <---------|--------->|  }                        |
 |     for ... { out <- item }     | <-chan   |  // cannot close:         |
 | }()                             |          |  // type is <-chan Item   |
 | return out   // as <-chan Item  |          +---------------------------+
 +---------------------------------+
```

### The idiomatic generator

```go
func gen(ctx context.Context, nums ...int) <-chan int {
    out := make(chan int)          // created by the owner
    go func() {
        defer close(out)           // closed by the owner, exactly once, on all paths
        for _, n := range nums {
            select {
            case out <- n:
            case <-ctx.Done():     // owner can stop early; still closes via defer
                return
            }
        }
    }()
    return out                     // returned as receive-only
}
```

Three habits embodied here:

1. **Create, send, close in the same function/goroutine** (single owner).
2. **`defer close(out)`** so every return path (including panic recovery) closes exactly once.
3. **Return `<-chan T`** so consumers cannot close.

### Corollaries

| Situation                         | Who closes                                              |
|-----------------------------------|---------------------------------------------------------|
| 1 sender, 1..N receivers          | The sender                                              |
| N senders, 1..M receivers         | A **coordinator** that waits for all senders (`WaitGroup`) |
| Receiver wants senders to stop    | Receiver closes a **separate** `done`/`stop` channel, never the data channel |
| Channel passed into your function | Do **not** close it unless the contract says you own it |

---

## 9. Close as Broadcast

A send delivers a value to **exactly one** receiver. A close is observed by **all** receivers, present and future.

```
   Send semantics (point-to-point)           Close semantics (broadcast)

   Producer --v--> [ ch ] --v--> G1          Controller --close(done)--> [ done ]
                           (only one of                                   |  |  |  |
                            G1,G2,G3 gets v)                              v  v  v  v
                                                                          G1 G2 G3 G4   (ALL wake up)
```

This is why the cancellation idiom is `chan struct{}`:

```go
done := make(chan struct{})

for i := 0; i < 5; i++ {
    go func(id int) {
        for {
            select {
            case <-done:
                fmt.Println("worker", id, "stopping")
                return
            default:
                doWork()
            }
        }
    }(i)
}

time.Sleep(time.Second)
close(done)   // one call, five goroutines notified
```

Why `struct{}`? Zero bytes of payload; it states intent: *"this channel carries no data, only the event."*

### Late joiners also observe it

```go
close(done)
<-done   // returns immediately, even if this goroutine starts waiting after the close
```

This is a property you **cannot** get from sending a value (a sent value is consumed once). It makes `close` ideal for **"state became true" latches** like `ready`, `shutdown`, `initialized`.

---

## 10. Buffered Channels and Close

Close does not discard buffered items.

```go
ch := make(chan string, 3)
ch <- "a"
ch <- "b"
close(ch)

fmt.Println(<-ch)        // "a"  ok=true
fmt.Println(<-ch)        // "b"  ok=true
v, ok := <-ch            // "" , false
fmt.Println(v, ok)
```

```
 before close          after close           after 2 receives
 +---+---+---+         +---+---+---+          +---+---+---+
 | a | b | . |         | a | b | . |          | . | . | . |
 +---+---+---+         +---+---+---+          +---+---+---+
 closed=0              closed=1               closed=1, qcount=0
 recv ok               recv ok (drains)       recv -> zero, ok=false
```

**Design implication:** a consumer using `range` is guaranteed to see **every** item sent before `close`. That is the "happens-before" ordering explained in section 12. This is why it is safe to `close` immediately after the last send. No sleep, no ack needed.

```go
for _, j := range jobs { jobsCh <- j }
close(jobsCh)       // consumers will still process ALL jobs, then exit
```

---

## 11. Nil Channels + Close (select tricks)

A nil channel blocks forever in `select`, so you can **disable** a case by setting a channel variable to nil.

### Merging two channels until both are closed

```go
func merge(a, b <-chan int) <-chan int {
    out := make(chan int)
    go func() {
        defer close(out)
        for a != nil || b != nil {        // run until BOTH closed
            select {
            case v, ok := <-a:
                if !ok {
                    a = nil                // disable this case forever
                    continue
                }
                out <- v
            case v, ok := <-b:
                if !ok {
                    b = nil
                    continue
                }
                out <- v
            }
        }
    }()
    return out
}
```

Why not just `return` on the first closed? Because the other channel may still have data.

```
 step 1: a open,  b open    select {a, b}
 step 2: a closed          -> a = nil     select {  , b}
 step 3: b delivers values                select {  , b}
 step 4: b closed          -> b = nil     select {  ,  }  loop condition false -> exit, close(out)
```

If you forget to nil-out the closed channel, `select` picks it **every iteration** (a closed channel is always ready), causing a 100% CPU spin with zero values.

---

## 12. Go Memory Model Guarantees

From the official Go memory model:

> **The closing of a channel is synchronized before a receive that returns because the channel is closed.**

Meaning: everything the closer did **before** `close(ch)` is visible to any goroutine that observes the close.

```go
var config Config      // plain variable, no mutex

ready := make(chan struct{})

go func() {
    config = loadConfig()   // (1) write
    close(ready)            // (2) close
}()

<-ready                     // (3) observes close
use(config)                 // (4) guaranteed to see (1), no data race
```

```
 Goroutine A                 Goroutine B
 -----------                 -----------
 config = load()   --\
 close(ready)      ---+-- happens-before -->  <-ready returns
                                               use(config)   // safe
```

Additional guarantees:

| Rule                                                                                       | Practical meaning                                  |
|--------------------------------------------------------------------------------------------|----------------------------------------------------|
| A send happens-before the corresponding receive completes                                  | Data passed through channel is safely published    |
| Close happens-before a receive that returns zero because of close                          | Closing publishes prior writes to all observers    |
| An unbuffered receive happens-before the send completes                                    | Rendezvous: receiver's prior writes visible to sender |
| The k-th receive on a channel of capacity C happens-before the (k+C)-th send completes     | Basis for semaphores via buffered channels         |

Caveat: **`len(ch)` / `cap(ch)` give no synchronization.** Never use `len(ch) == 0` as a "done" check.

---

## 13. The Patterns Catalogue

Each pattern states **who closes, when, and why**.

### 13.1 Producer, Consumer (end-of-data)

```go
func main() {
    ch := make(chan int)

    go func() {                 // producer = owner
        defer close(ch)
        for i := 1; i <= 3; i++ {
            ch <- i
        }
    }()

    for v := range ch {         // consumer
        fmt.Println(v)
    }
    fmt.Println("done")
}
```

```
 Producer goroutine                         main goroutine
 +------------------+     unbuffered       +------------------+
 | ch <- 1          |--------------------->| v = 1  print     |
 | ch <- 2          |--------------------->| v = 2  print     |
 | ch <- 3          |--------------------->| v = 3  print     |
 | close(ch)        |==== closed signal ==>| range exits      |
 +------------------+                      | "done"           |
                                           +------------------+
```

### 13.2 Done / Quit channel (cancellation)

```go
func worker(done <-chan struct{}, jobs <-chan Job) {
    for {
        select {
        case <-done:
            return
        case j, ok := <-jobs:
            if !ok {
                return
            }
            j.Run()
        }
    }
}
```

Two channels, two different meanings of "close":

| Channel | Closed by              | Meaning                    |
|---------|------------------------|----------------------------|
| `jobs`  | the producer           | "no more work"             |
| `done`  | the controller/owner   | "stop now, even if work remains" |

### 13.3 Fan-out / Fan-in (N senders, one closer)

The classic "multiple senders" problem. **No single worker may close `out`**, because the others still send. A dedicated closer goroutine waits for all of them.

```go
func fanIn(ctx context.Context, inputs ...<-chan int) <-chan int {
    out := make(chan int)
    var wg sync.WaitGroup

    forward := func(c <-chan int) {
        defer wg.Done()
        for v := range c {
            select {
            case out <- v:
            case <-ctx.Done():
                return
            }
        }
    }

    wg.Add(len(inputs))
    for _, c := range inputs {
        go forward(c)
    }

    go func() {         // <-- the dedicated CLOSER
        wg.Wait()       // all senders finished
        close(out)      // now provably safe
    }()

    return out
}
```

Architecture:

```
                          +--> [ worker 1 ] --\
                          |                    \
 [ source ] --> jobs -----+--> [ worker 2 ] ----+--> results --> [ aggregator ]
  (closes jobs            |                    /     ^
   after last job)        +--> [ worker 3 ] --/      |
                                                     |
                                  +------------------+----------------+
                                  | closer goroutine:                 |
                                  |   wg.Wait()  // 1,2,3 all returned |
                                  |   close(results)                  |
                                  +-----------------------------------+
```

The invariant: **`close(out)` happens after `wg.Wait()`, and `wg.Done()` happens after the last send.** So no send can follow the close.

### 13.4 Pipeline (stage chaining)

```go
func square(ctx context.Context, in <-chan int) <-chan int {
    out := make(chan int)
    go func() {
        defer close(out)               // each stage closes ITS OWN output
        for v := range in {            // ends when upstream closes
            select {
            case out <- v * v:
            case <-ctx.Done():
                return
            }
        }
    }()
    return out
}

func main() {
    ctx, cancel := context.WithCancel(context.Background())
    defer cancel()

    nums := gen(ctx, 1, 2, 3, 4)
    sq   := square(ctx, nums)
    for v := range sq {
        fmt.Println(v)
    }
}
```

**Close propagates downstream like a domino effect:**

```
 gen            square            filter           sink
 +-----+  c1   +-------+   c2    +--------+  c3   +------+
 |     |------>|       |-------->|        |------>| range|
 +-----+       +-------+         +--------+       +------+
   |               ^                 ^                ^
   | close(c1)     | range c1 ends   |                |
   +-------------->| close(c2)       |                |
                   +---------------->| range c2 ends  |
                                     | close(c3)      |
                                     +--------------->| range c3 ends -> done
```

Cancellation propagates in the **opposite** direction via `ctx.Done()`:

```
 sink  --cancel()-->  ctx.Done() closed  --> every stage's select sees it --> all return,
                                              each `defer close(out)` runs, no goroutine leaks
```

### 13.5 Worker pool

```go
func process(jobs []Job, nWorkers int) []Result {
    jobCh    := make(chan Job)
    resultCh := make(chan Result)

    var wg sync.WaitGroup
    for i := 0; i < nWorkers; i++ {
        wg.Add(1)
        go func() {
            defer wg.Done()
            for j := range jobCh {          // exits when jobCh closed + drained
                resultCh <- j.Do()
            }
        }()
    }

    go func() {                              // feeder: owns jobCh
        defer close(jobCh)
        for _, j := range jobs {
            jobCh <- j
        }
    }()

    go func() {                              // closer: owns resultCh
        wg.Wait()
        close(resultCh)
    }()

    var out []Result
    for r := range resultCh {
        out = append(out, r)
    }
    return out
}
```

```
                 +------------------------------------------------------------+
                 |                      process()                             |
                 |                                                            |
   jobs[] -----> |  FEEDER --jobCh--> +-> W1 --+                               |
                 |  (closes jobCh     +-> W2 --+--resultCh--> COLLECTOR -> out |
                 |   after last job)  +-> W3 --+       ^                      |
                 |                                     |                      |
                 |                    CLOSER: wg.Wait(); close(resultCh)      |
                 +------------------------------------------------------------+
   Two channels, two different owners, two different close points.
```

### 13.6 `errgroup`: structured version of the above

```go
g, ctx := errgroup.WithContext(context.Background())

g.Go(func() error { return fetchA(ctx) })
g.Go(func() error { return fetchB(ctx) })

if err := g.Wait(); err != nil { // returns first error; ctx is cancelled (Done closed)
    log.Fatal(err)
}
```

When the first goroutine returns an error, `errgroup` cancels `ctx`, which **closes `ctx.Done()`**, broadcasting a stop signal to siblings.

### 13.7 Or-done channel (wrapping a channel you don't control)

```go
func orDone(ctx context.Context, c <-chan int) <-chan int {
    out := make(chan int)
    go func() {
        defer close(out)
        for {
            select {
            case <-ctx.Done():
                return
            case v, ok := <-c:
                if !ok {
                    return
                }
                select {
                case out <- v:
                case <-ctx.Done():
                    return
                }
            }
        }
    }()
    return out
}
```

Allows `for v := range orDone(ctx, c)`, which exits on either upstream close or cancellation.

### 13.8 Tee channel (duplicate a stream)

```go
func tee(ctx context.Context, in <-chan int) (<-chan int, <-chan int) {
    out1, out2 := make(chan int), make(chan int)
    go func() {
        defer close(out1)
        defer close(out2)
        for v := range orDone(ctx, in) {
            o1, o2 := out1, out2
            for i := 0; i < 2; i++ {
                select {
                case <-ctx.Done():
                    return
                case o1 <- v:
                    o1 = nil           // nil trick: do not send to out1 again
                case o2 <- v:
                    o2 = nil
                }
            }
        }
    }()
    return out1, out2
}
```

### 13.9 Pub/Sub broker with safe close

```go
type Broker struct {
    mu     sync.RWMutex
    subs   map[int]chan string
    nextID int
    closed bool
}

func NewBroker() *Broker { return &Broker{subs: make(map[int]chan string)} }

func (b *Broker) Subscribe() (<-chan string, func()) {
    b.mu.Lock()
    defer b.mu.Unlock()
    if b.closed {
        ch := make(chan string)
        close(ch)                       // subscriber to a dead broker sees immediate EOF
        return ch, func() {}
    }
    id := b.nextID
    b.nextID++
    ch := make(chan string, 16)
    b.subs[id] = ch
    return ch, func() {                 // unsubscribe
        b.mu.Lock()
        defer b.mu.Unlock()
        if c, ok := b.subs[id]; ok {
            delete(b.subs, id)
            close(c)                    // broker owns the send side -> broker closes
        }
    }
}

func (b *Broker) Publish(msg string) {
    b.mu.RLock()
    defer b.mu.RUnlock()
    if b.closed {
        return
    }
    for _, ch := range b.subs {
        select {
        case ch <- msg:
        default: // slow consumer: drop instead of blocking the publisher
        }
    }
}

func (b *Broker) Close() {
    b.mu.Lock()
    defer b.mu.Unlock()
    if b.closed {
        return
    }
    b.closed = true
    for id, ch := range b.subs {
        close(ch)
        delete(b.subs, id)
    }
}
```

Why is this **safe**? Sends (`Publish`) and closes (`unsubscribe`, `Close`) are serialized by the same mutex plus the `closed`/map-membership check. A send can never race with a close on the same channel.

```
              Publish()                           Subscribers
 Producer --> +----------+  RLock   +--> ch1 (buf16) --> consumer A  (range ends on close)
              |  Broker  |----------+--> ch2 (buf16) --> consumer B
              |  mu, map |          +--> ch3 (buf16) --> consumer C
              +----------+
                   ^ Close(): Lock, mark closed, close ch1..ch3  (broadcast "end of stream")
```

### 13.10 Request / response with a reply channel

```go
type request struct {
    key   string
    reply chan<- string       // caller creates; server sends exactly once
}

func server(reqs <-chan request) {
    cache := map[string]string{}
    for r := range reqs {                 // loop ends when reqs closed
        r.reply <- cache[r.key]           // reply chan is buffered(1) so server never blocks
    }
}

func get(reqs chan<- request, key string) string {
    reply := make(chan string, 1)         // buffered: server send never blocks even if caller gave up
    reqs <- request{key, reply}
    return <-reply                        // single value, reply channel needn't be closed
}
```

Note: the reply channel is **never closed**, since exactly one value flows and the receiver doesn't `range`. Closing it would be harmless but unnecessary (section 18).

### 13.11 Future / promise (close as "result is ready")

```go
type Future struct {
    done chan struct{}
    val  int
    err  error
}

func Async(f func() (int, error)) *Future {
    fu := &Future{done: make(chan struct{})}
    go func() {
        fu.val, fu.err = f()
        close(fu.done)             // publishes val/err (memory model: close happens-before receive)
    }()
    return fu
}

func (f *Future) Wait() (int, error) {
    <-f.done                       // any number of goroutines may Wait, even after completion
    return f.val, f.err
}
```

This is the **latch** use: close = "state became true", observable by many waiters and by late arrivals.

### 13.12 Once-only initialization signal

```go
type Service struct {
    ready chan struct{}
    once  sync.Once
}

func (s *Service) markReady() { s.once.Do(func() { close(s.ready) }) }
func (s *Service) WaitReady() { <-s.ready }
```

### 13.13 Semaphore (note: close is NOT used)

```go
sem := make(chan struct{}, 10)   // max 10 concurrent
for _, t := range tasks {
    sem <- struct{}{}            // acquire
    go func(t Task) {
        defer func() { <-sem }() // release
        t.Run()
    }(t)
}
```

Closing is irrelevant here; included to show that **not every channel is closed**.

### 13.14 Generator / iterator and early stop

```go
func fib(ctx context.Context) <-chan int {
    ch := make(chan int)
    go func() {
        defer close(ch)
        a, b := 0, 1
        for {
            select {
            case ch <- a:
                a, b = b, a+b
            case <-ctx.Done():
                return
            }
        }
    }()
    return ch
}
```

Without the `ctx.Done()` case, abandoning the iterator early leaks the generator goroutine forever (blocked on send). That is the **most common leak** in channel-based generators.

> Modern note (Go 1.23+): for pull-style iteration prefer `iter.Seq[T]` / `iter.Pull`, which handle early exit via the `yield` return value without channels or goroutines. Channels remain right when concurrency is the point.

### 13.15 Ticker / timer channels (close you do NOT do)

```go
t := time.NewTicker(time.Second)
defer t.Stop()
for {
    select {
    case <-t.C:
        poll()
    case <-ctx.Done():
        return
    }
}
```

`t.C` is **never closed by the runtime**; you call `Stop()`. Do not `range t.C` expecting an end. (Since Go 1.23, unreferenced unstopped timers are GC-able and `Stop`/`Reset` no longer leave stale values, but the channel is still not closed.)

---

## 14. Real-World Use Cases and Architectures

### 14.1 Graceful shutdown of an HTTP microservice

Signals, servers, background workers, and DB pools must all stop **in order**. Channels (and contexts, which are channels underneath) are the glue.

```go
func main() {
    ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
    defer stop()                                  // ctx.Done() is closed when SIGTERM arrives

    jobs := make(chan Job, 100)

    var workers sync.WaitGroup
    for i := 0; i < 8; i++ {
        workers.Add(1)
        go func() {
            defer workers.Done()
            for j := range jobs {                 // drains remaining jobs on shutdown
                j.Run()
            }
        }()
    }

    srv := &http.Server{Addr: ":8080", Handler: newMux(jobs)}
    go func() {
        if err := srv.ListenAndServe(); err != http.ErrServerClosed {
            log.Fatal(err)
        }
    }()

    <-ctx.Done()                                  // (1) signal received

    shutCtx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
    defer cancel()
    _ = srv.Shutdown(shutCtx)                     // (2) stop accepting; wait in-flight handlers

    close(jobs)                                   // (3) now NO handler can send -> safe to close
    workers.Wait()                                // (4) workers drain queue and exit
    log.Println("clean exit")
}
```

Critical ordering insight: **`close(jobs)` only after `srv.Shutdown` returns**, because HTTP handlers are the senders. Closing earlier would make a late request panic with `send on closed channel`.

```
   SIGTERM
      |
      v
+-------------+   (1)  ctx.Done() closed
|  OS signal  |------------------------------+
+-------------+                              |
                                             v
                                   +--------------------+
                                   |  main goroutine    |
                                   +---------+----------+
                                             | (2) srv.Shutdown(ctx)
                                             v
 clients --HTTP--> +-----------------+  jobs (buffered 100)   +---------+
                   | HTTP handlers   |----------------------->| Worker1 |
                   | (SENDERS)       |                        | Worker2 |
                   +-----------------+                        |  ...    |
                          |                                   | Worker8 |
        all handlers      |                                   +----+----+
        finished  ------->+                                        |
                                             | (3) close(jobs)     |
                                             v                     |
                                   "no more jobs" ---------------->| range ends after drain
                                             | (4) workers.Wait() <-+
                                             v
                                         process exits
```

### 14.2 Kafka / queue consumer with rebalance and stop

```
                 +-------------------------------------------------+
                 |                 Consumer Service                |
                 |                                                 |
 Kafka ---poll-->| [Fetcher] --msgCh--> [Worker pool x N] --ackCh--> [Committer]
 broker          |    ^                       |                         |
                 |    | stop (ctx.Done)       | on error: errCh         | commit offsets
                 |    |                       v                         v
                 | [Supervisor] <--------- errCh                   Kafka broker
                 +-------------------------------------------------+

 Shutdown order (each arrow = a close):
   1. cancel ctx         -> Fetcher stops polling
   2. Fetcher: close(msgCh)  -> workers drain & exit (wg.Wait)
   3. closer: close(ackCh)   -> Committer flushes final offsets & exits
   4. Supervisor returns
```

Each stage closes **its own output**. That chain guarantees **no message is lost and no offset is committed before the work finishes**.

### 14.3 Concurrent web crawler / scraper

```
        seed URLs
            |
            v
      +-----------+   urlCh    +---------------+   pageCh   +------------+
      | Frontier  |----------->| Fetchers (N)  |----------->| Parsers(M) |
      | (dedupe)  |            | HTTP GET      |            | extract    |
      +-----+-----+            +---------------+            +-----+------+
            ^                                                     |
            |                  new links                          |
            +-----------------------------------------------------+
   Termination is the hard part: the pipeline is CYCLIC, so "upstream closed"
   never happens naturally. Solution: track in-flight count (WaitGroup or counter);
   when it reaches 0 and frontier is empty, the coordinator closes urlCh,
   and closure ripples: Fetchers exit -> close(pageCh) -> Parsers exit.
```

### 14.4 Log / metrics shipper (batching with flush on close)

```go
type Shipper struct {
    in   chan Event
    done chan struct{}
}

func NewShipper() *Shipper {
    s := &Shipper{in: make(chan Event, 1024), done: make(chan struct{})}
    go s.loop()
    return s
}

func (s *Shipper) loop() {
    defer close(s.done)                      // signals "fully flushed"
    batch := make([]Event, 0, 100)
    tick := time.NewTicker(2 * time.Second)
    defer tick.Stop()
    for {
        select {
        case e, ok := <-s.in:
            if !ok {                         // Close() was called: flush and exit
                flush(batch)
                return
            }
            batch = append(batch, e)
            if len(batch) == cap(batch) {
                flush(batch)
                batch = batch[:0]
            }
        case <-tick.C:
            flush(batch)
            batch = batch[:0]
        }
    }
}

func (s *Shipper) Emit(e Event) { s.in <- e }

func (s *Shipper) Close() {                  // caller guarantees no concurrent Emit
    close(s.in)                              // "no more events"
    <-s.done                                 // wait until flush completes
}
```

Two channels with two distinct closes: `in` closed by the API (input end), `done` closed by the loop (completion). **Close-then-wait-on-done is the standard "drain and join" handshake.**

```
 app code --Emit--> [ in (buf 1024) ] --> loop goroutine --> flush --> remote collector
                         |                     |
          Close(): close(in)                   | on exit: close(done)
                         |                     v
                         +--> loop sees !ok -> final flush -> return
                                               ^
                  Close() blocks on <-done ----+
```

### 14.5 Context cancellation (the biggest real-world consumer of close)

`ctx.Done()` **is a channel that gets closed** upon cancel or timeout. Every `http.Client` request with context, every `database/sql` query, every gRPC call is cancelled through this mechanism.

```
 parent ctx
    |
    +-- child1 (WithTimeout 2s)
    |      \__ grandchild (WithCancel)
    +-- child2 (WithCancel)

 cancel(parent) --> close(parent.done) --> propagate: close(child1.done), close(child2.done),
                                                       close(grandchild.done)
 Each goroutine blocked in `<-ctx.Done()` wakes.
```

See section 15 for how it is implemented.

### 14.6 gRPC / streaming server-side end-of-stream

A server-streaming RPC handler typically reads from a channel fed by business logic:

```go
func (s *Server) Watch(req *pb.Req, stream pb.Svc_WatchServer) error {
    events, unsubscribe := s.broker.Subscribe()
    defer unsubscribe()                              // broker closes our channel
    for {
        select {
        case <-stream.Context().Done():              // client disconnected: channel closed by gRPC
            return stream.Context().Err()
        case ev, ok := <-events:
            if !ok {
                return nil                           // broker closed: end stream normally (EOF)
            }
            if err := stream.Send(toPB(ev)); err != nil {
                return err
            }
        }
    }
}
```

### 14.7 Database / batch ETL job

```
 DB cursor --> [Reader] --rowCh--> [Transformers xN] --outCh--> [Bulk Writer] --> target DB
                 |                                                  |
        close(rowCh) at EOF                              range outCh; flush on close
 If any stage fails: cancel ctx (close Done) -> everybody unwinds, partial batch rolled back.
```

### 14.8 Rate limiter / token bucket refill goroutine

```go
type Limiter struct {
    tokens chan struct{}
    stop   chan struct{}
}

func NewLimiter(rate int, burst int) *Limiter {
    l := &Limiter{tokens: make(chan struct{}, burst), stop: make(chan struct{})}
    go func() {
        t := time.NewTicker(time.Second / time.Duration(rate))
        defer t.Stop()
        for {
            select {
            case <-t.C:
                select {
                case l.tokens <- struct{}{}:
                default: // bucket full
                }
            case <-l.stop:      // closed by Stop(): broadcast to refill goroutine
                return
            }
        }
    }()
    return l
}

func (l *Limiter) Wait()  { <-l.tokens }
func (l *Limiter) Stop()  { close(l.stop) }   // note: `tokens` is NOT closed (see section 18)
```

### 14.9 Real-time chat / WebSocket hub

```
  client A <--ws--> [ conn goroutine A ] --\                   /--> send chan A --> writePump A
  client B <--ws--> [ conn goroutine B ] ---+--> broadcast --> Hub --> send chan B --> writePump B
  client C <--ws--> [ conn goroutine C ] --/      chan       (single goroutine owns client map)
                                                               \--> send chan C --> writePump C

  When a client disconnects, the HUB (owner of the send side of that client's `send` chan)
  does: delete(clients, c); close(c.send)  -> writePump's `range c.send` ends -> closes the socket.
  The readPump never closes `send`; it tells the hub via `unregister` chan.
```

This is straight from the well-known `gorilla/websocket` chat example: **the hub is the single owner**, so close is race-free.

### 14.10 Test synchronization

```go
func TestWorkerStops(t *testing.T) {
    started := make(chan struct{})
    done    := make(chan struct{})
    go func() {
        close(started)         // signal "I'm running" without sleeps
        defer close(done)
        runUntilCancelled()
    }()
    <-started
    cancel()
    select {
    case <-done:
    case <-time.After(time.Second):
        t.Fatal("worker did not stop")
    }
}
```

Replaces `time.Sleep` with deterministic latches.

---

## 15. How the Standard Library Uses Closed Channels

| Package / API              | What is closed                  | Meaning                                       |
|----------------------------|---------------------------------|-----------------------------------------------|
| `context.WithCancel`       | `ctx.Done()` channel            | cancellation broadcast                        |
| `context.WithTimeout`      | `ctx.Done()` via `time.AfterFunc` | deadline reached                            |
| `context.Background()`     | `Done()` returns **nil**        | never cancelled (nil channel blocks forever)  |
| `signal.NotifyContext`     | ctx Done                        | OS signal arrived                             |
| `http.Server.Shutdown`     | internal done chans             | stop accepting, drain                         |
| `http.Request.Context()`   | closes when client disconnects or handler returns | abort expensive work       |
| `sync.WaitGroup`           | *(not channels, uses semaphores)* | n/a                                         |
| `time.Ticker.C`            | **never closed**                | use `Stop()`                                  |
| `os/exec` `cmd.Wait`       | internal                        | process exit                                  |

### Context internals (simplified)

```go
type cancelCtx struct {
    Context
    mu       sync.Mutex
    done     atomic.Value          // chan struct{}, created lazily
    children map[canceler]struct{}
    err      error
}

var closedchan = make(chan struct{})
func init() { close(closedchan) }   // a pre-closed channel reused by every already-cancelled ctx

func (c *cancelCtx) Done() <-chan struct{} { /* lazily make(chan struct{}) */ }

func (c *cancelCtx) cancel(removeFromParent bool, err error) {
    c.mu.Lock()
    if c.err != nil { c.mu.Unlock(); return }   // already cancelled: idempotent (avoids double close)
    c.err = err
    d, _ := c.done.Load().(chan struct{})
    if d == nil {
        c.done.Store(closedchan)               // nobody asked for Done yet: share the global closed chan
    } else {
        close(d)                               // broadcast to all waiters
    }
    for child := range c.children { child.cancel(false, err) }   // cascade
    c.children = nil
    c.mu.Unlock()
    ...
}
```

What this teaches:

1. **Idempotent close** is achieved with a mutex + state check (`c.err != nil`), which is the production-grade "safe close" pattern.
2. **Lazy channel creation** avoids allocating a channel unless somebody waits.
3. **A pre-closed global channel** (`closedchan`) is a neat trick: a closed channel is a reusable "already true" latch.
4. **Cascade** closes children as part of the parent's close.

---

## 16. Closing With Multiple Senders / Receivers

Classic problem (Go101 "How to gracefully close channels"). Four situations:

```
 (1) 1 sender,  1 receiver      -> sender closes
 (2) 1 sender,  M receivers     -> sender closes
 (3) N senders, 1 receiver      -> receiver must NOT close data channel; use a stop signal
 (4) N senders, M receivers     -> nobody closes data channel directly; use a mediator + stop signal
```

### Case 3: N senders, 1 receiver

The receiver wants to stop early. It cannot close `dataCh` (senders would panic). It closes a **separate** `stopCh`; senders select on it. **`dataCh` is never closed**; it becomes unreachable and is GC'd.

```go
func main() {
    dataCh := make(chan int)
    stopCh := make(chan struct{})
    var wg sync.WaitGroup

    for i := 0; i < 5; i++ {
        wg.Add(1)
        go func(id int) {
            defer wg.Done()
            for {
                select {
                case <-stopCh:                 // receiver said stop
                    return
                case dataCh <- rand.Intn(100):
                }
            }
        }(i)
    }

    for v := range dataCh {                    // receiver
        if v > 90 {
            close(stopCh)                      // receiver owns stopCh, not dataCh
            break
        }
    }
    wg.Wait()
}
```

```
 Sender1 --+                                  +--- stopCh (closed by receiver) ---+
 Sender2 --+--> dataCh ----> Receiver         |                                    |
 Sender3 --+     (NEVER closed)  | decides    v                                    v
 Sender4 --+                     +--close(stopCh)--> all senders' select <-stopCh fires -> return
```

A subtle point: after `stopCh` is closed, a sender whose `select` has **both** `<-stopCh` and `dataCh <- x` ready picks randomly; extra values may still be sent. With no receiver left, a send on an unbuffered `dataCh` simply blocks and the `stopCh` case wins; no panic because `dataCh` was never closed.

### Case 4: N senders, M receivers (the mediator pattern)

```go
func main() {
    const N, M = 5, 3
    dataCh   := make(chan int)
    stopCh   := make(chan struct{})
    toStop   := make(chan string, 1)   // buffered(1): first requester never blocks
    var wg sync.WaitGroup
    wg.Add(N + M)

    go func() {                        // MEDIATOR: the only one that closes stopCh
        reason := <-toStop
        fmt.Println("stopping because:", reason)
        close(stopCh)
    }()

    for i := 0; i < N; i++ {           // senders
        go func() {
            defer wg.Done()
            for {
                select {
                case <-stopCh:
                    return
                default:
                }
                select {
                case <-stopCh:
                    return
                case dataCh <- rand.Intn(1000):
                }
            }
        }()
    }

    for i := 0; i < M; i++ {           // receivers
        go func() {
            defer wg.Done()
            for {
                select {
                case <-stopCh:
                    return
                case v := <-dataCh:
                    if v%97 == 0 {
                        select {
                        case toStop <- "found 97 multiple": // non-blocking request to stop
                        default:
                        }
                        return
                    }
                }
            }
        }()
    }
    wg.Wait()
}
```

```
 Senders  (N) ---> dataCh ---> Receivers (M)
     |                              |
     | <-stopCh                     | toStop <- reason   (buffered 1, non-blocking)
     |                              v
     |                         +----------+
     +------------ <-stopCh ---| MEDIATOR |--- close(stopCh) (exactly once: it's the only closer)
                               +----------+
   Single closer + many requesters = no double close, no send-on-closed.
```

Why the first inner `select{ case <-stopCh: return; default: }`? Because when both cases in the combined select are ready Go picks randomly; the priority check biases toward stopping sooner.

---

## 17. Safe-Close Helpers

### 17.1 `sync.Once` (idempotent close)

```go
type Signal struct {
    c    chan struct{}
    once sync.Once
}

func NewSignal() *Signal { return &Signal{c: make(chan struct{})} }
func (s *Signal) Fire()  { s.once.Do(func() { close(s.c) }) }
func (s *Signal) Done() <-chan struct{} { return s.c }
```

Solves **double close** only. Does **not** protect against send-after-close.
Go 1.21+: `closeFn := sync.OnceFunc(func() { close(ch) })`.

### 17.2 Mutex-guarded channel (protects sends too)

```go
type SafeChan[T any] struct {
    mu     sync.RWMutex
    ch     chan T
    closed bool
}

func NewSafeChan[T any](n int) *SafeChan[T] { return &SafeChan[T]{ch: make(chan T, n)} }

// TrySend returns false if the channel is closed or full.
func (s *SafeChan[T]) TrySend(v T) (sent bool) {
    s.mu.RLock()
    defer s.mu.RUnlock()
    if s.closed {
        return false
    }
    select {
    case s.ch <- v:
        return true
    default:
        return false
    }
}

func (s *SafeChan[T]) Close() {
    s.mu.Lock()
    defer s.mu.Unlock()
    if !s.closed {
        s.closed = true
        close(s.ch)
    }
}

func (s *SafeChan[T]) C() <-chan T { return s.ch }
```

Caveat: a **blocking** send while holding `RLock` would deadlock `Close()` (it needs the write lock). That's why this version uses non-blocking send. For blocking sends, pair with a `done` channel instead.

### 17.3 Recover-based close (works, but a smell)

```go
func safeClose[T any](ch chan T) (justClosed bool) {
    defer func() {
        if recover() != nil {
            justClosed = false
        }
    }()
    close(ch)
    return true
}
```

Works for double close but **hides design errors** and cannot make senders safe. Use only in last-resort legacy code.

### 17.4 Why an `isClosed(ch)` function is a bad idea

```go
func isClosed(ch <-chan int) bool {
    select {
    case <-ch:
        return true     // WRONG in general: may have consumed a real value!
    default:
        return false
    }
}
```

Problems:

1. If the channel is open and has data, the select **steals** a value.
2. Even if correct at the instant of the check, it can close **right after** you checked (TOCTOU race).
3. Fixing the race requires a lock, at which point use the mutex-guarded design above.

---

## 18. When You Should NOT Close a Channel

| Scenario                                                          | Close?       | Explanation |
|-------------------------------------------------------------------|--------------|-------------|
| Channel used as a **semaphore / token bucket**                    | No           | No one waits for "end" |
| **Request/reply** channel carrying exactly one value              | No           | Receiver does `<-reply` once |
| Multiple senders and no clean coordinator                         | No (use `stop` chan) | Closing risks panic; GC handles it |
| You are the **receiver**                                          | No           | Not your side (golden rule) |
| Channel passed in as parameter, you are not the creator           | No (unless documented) | Ownership unclear |
| Just to "release memory"                                          | No           | GC does it |
| Result channel buffered to exact N and receiver reads exactly N   | Optional     | Count is known |
| `time.Ticker.C`, `time.Timer.C`, `ctx.Done()` from others         | **Cannot**/should not | Owned by runtime/library |

---

## 19. Pitfalls and Anti-Patterns

### 19.1 Closing from the receiver

```go
// BAD
go func() { for i := 0; ; i++ { ch <- i } }()
for v := range ch {
    if v > 10 { close(ch); break }     // sender will panic on next send
}
```

Fix: close a separate `done` channel and have the sender select on it.

### 19.2 Multiple closers racing

```go
// BAD: two workers each "finish" and close
go func() { work(); close(results) }()
go func() { work(); close(results) }()   // second one panics (or the first sender panics)
```

Fix: `WaitGroup` + one closer goroutine (13.3), or `sync.Once` (17.1).

### 19.3 Forgetting to close: `range` hangs forever

```go
ch := make(chan int)
go func() { ch <- 1; ch <- 2 }()   // forgot close(ch)
for v := range ch { fmt.Println(v) }  // prints 1 2 then: fatal error: all goroutines are asleep - deadlock!
```

(If other goroutines exist, the deadlock detector won't fire, and you get a silent hang/leak, which is worse.)

### 19.4 Closing too early in a loop with goroutines

```go
// BAD
for _, u := range urls {
    go func(u string) { results <- fetch(u) }(u)
}
close(results)                  // closes immediately; goroutines then panic on send
```

Fix:

```go
var wg sync.WaitGroup
for _, u := range urls {
    wg.Add(1)
    go func(u string) { defer wg.Done(); results <- fetch(u) }(u)
}
go func() { wg.Wait(); close(results) }()
```

### 19.5 `wg.Add` inside the goroutine

```go
// BAD: wg.Wait() may run before any Add executes -> closer closes too early
for i := 0; i < n; i++ {
    go func() { wg.Add(1); defer wg.Done(); out <- work() }()
}
```

`Add` must happen **before** the `go` statement (or before `Wait` can possibly run).

### 19.6 Busy loop on closed channel in `select`

```go
// BAD: when `in` closes, this case is always ready -> 100% CPU, zero values
for {
    select {
    case v := <-in:
        handle(v)
    case <-quit:
        return
    }
}
```

Fix: `v, ok := <-in; if !ok { in = nil }` (section 11) or `return`.

### 19.7 Sending while holding a lock that the closer needs

```go
// Goroutine A (sender)     // Goroutine B (closer)
mu.Lock()                   mu.Lock()     // waits for A
ch <- v   // blocks: no receiver (it is waiting for close, which needs mu)  -> DEADLOCK
mu.Unlock()
```

Never block on a channel operation while holding a lock that the closing path also needs.

### 19.8 Closing a channel in `defer` in a function that may be called twice

```go
func (s *S) Stop() { defer close(s.quit); ... }   // 2nd call panics
```

Fix: `sync.Once`.

### 19.9 `for ... range` over a channel and re-sending to the same channel

Deadlock-prone and conceptually wrong: a goroutine that both consumes from and produces to the same unbuffered channel can wait on itself.

### 19.10 Ignoring the zero value after close

```go
for {
    select {
    case msg := <-ch:      // after close: msg is "" forever
        send(msg)
    }
}
```

### 19.11 Closing `nil` channel

Struct fields initialized lazily: `close(s.ch)` when `s.ch` was never made → `panic: close of nil channel`. Initialize in constructors.

### 19.12 Copying a struct that holds a channel + Once/mutex

Channels are reference-like (copies share the same channel) but `sync.Once` and `sync.Mutex` are **values**; copying them breaks guarantees. `go vet` (copylocks) catches many cases. Use pointer receivers.

### 19.13 Passing `time.After` in a loop

Not close-related, but common in `select` loops: each call allocates a timer that is not freed until it fires (pre-Go 1.23). Prefer `time.NewTimer` + `Reset`.

---

## 20. Goroutine Leaks and Detection

### Typical leak shapes

```
 (A) Sender blocked, receiver gone:      G: ch <- v            (no receiver ever)
 (B) Receiver blocked, sender never closes: G: for v := range ch   (no close ever)
 (C) Waiting on a never-closed done:     G: <-done              (done never closed)
 (D) Both waiting on each other:         deadlock cycle
```

### Detect them

1. **Runtime deadlock detector**: only when *all* goroutines are blocked: `fatal error: all goroutines are asleep - deadlock!`.
2. **`pprof` goroutine dump**:

```go
import _ "net/http/pprof"
// go tool pprof http://localhost:6060/debug/pprof/goroutine
// or open  /debug/pprof/goroutine?debug=2  to see stacks "chan receive", "chan send", "select"
```

   Stack states to look for: `chan receive`, `chan send`, `select`, with growing counts over time.

3. **`go.uber.org/goleak`** in tests:

```go
func TestMain(m *testing.M) { goleak.VerifyTestMain(m) }
// or per test:  defer goleak.VerifyNone(t)
```

4. **`runtime.NumGoroutine()`** sampled before/after in tests or exported as a metric (a monotonically increasing value in production signals a leak).

### Leak-proof generator checklist

- [ ] Every blocking send is in a `select` with `<-ctx.Done()` (or `<-done`).
- [ ] Every blocking receive from a channel you don't control is in a `select` with cancellation (or wrapped in `orDone`).
- [ ] Every goroutine has a documented **exit condition**.
- [ ] Owner calls `defer close(out)`.
- [ ] Callers `defer cancel()`.

---

## 21. Testing Close Logic

### Race detector

```bash
go test -race ./...
```

Detects data races, including unsynchronized flags used as `closed` booleans. (It does not always report `send on closed channel`; that is a logic panic, which tests must exercise.)

### Stress / repeat

```bash
go test -race -count=1000 -run TestShutdown ./...
go test -race -cpu=1,2,4,8 ./...
```

Close/send ordering bugs often appear only under unusual interleavings.

### Deterministic shutdown test

```go
func TestPipelineCancel(t *testing.T) {
    defer goleak.VerifyNone(t)

    ctx, cancel := context.WithCancel(context.Background())
    out := square(ctx, gen(ctx, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10))

    <-out        // read one value
    cancel()     // abandon the rest

    // Drain with timeout to ensure the pipeline closes its outputs and exits.
    timeout := time.After(time.Second)
    for {
        select {
        case _, ok := <-out:
            if !ok {
                return           // closed: success
            }
        case <-timeout:
            t.Fatal("pipeline did not shut down")
        }
    }
}
```

### Test that double close is impossible

```go
func TestStopIdempotent(t *testing.T) {
    s := NewService()
    s.Stop()
    s.Stop()   // must not panic
}
```

---

## 22. Performance Notes

| Topic                              | Detail                                                                                          |
|------------------------------------|-------------------------------------------------------------------------------------------------|
| Cost of `close`                    | O(waiters); takes the channel lock; wakes goroutines. Typically sub-microsecond for few waiters.|
| Receiving on a closed channel      | Fast path: lock, check `closed && qcount == 0`, return zero. Cheap but still takes the lock.     |
| Broadcast scalability              | One close wakes 100k goroutines but scheduling them is not free; cost shifts to scheduler.       |
| `chan struct{}` vs `chan bool`     | `struct{}` has zero element size: no buffer memory for payload; preferred for signals.           |
| `sync.Cond` vs close               | `Cond.Broadcast` can be repeated; **close is one-shot**. Use close for one-time events.          |
| `atomic.Bool` polling vs close     | Polling burns CPU or needs sleeps; close lets goroutines **park** and wake instantly.            |
| Contention                         | Heavy send/recv on one channel serializes on its lock; shard channels for extreme throughput.    |
| Allocation                         | `make(chan struct{})` is one small heap alloc (~96 bytes hchan header). Cheap to create per-operation latches (e.g., per request). |

---

## 23. Decision Flowchart

```
                         Do I own / create the channel?
                          /                          \
                        NO                            YES
                         |                             |
           Do NOT close it.                 Is anyone waiting for "no more data"
        (unless contract says so)           or "stop" (range / <-done)?
                                              /                    \
                                            NO                      YES
                                             |                       |
                                    Don't bother closing.     How many SENDERS?
                                    (GC will collect.)         /              \
                                                             ONE             MANY (N)
                                                              |                |
                                                   Sender does           Can I coordinate
                                                   `defer close(ch)`     "all senders finished"?
                                                   when finished.          /            \
                                                                        YES              NO
                                                                         |                |
                                                        WaitGroup + dedicated        Use a separate `stop`/`done`
                                                        closer goroutine:            channel (+ mediator if needed).
                                                        wg.Wait(); close(ch)         Never close the data channel;
                                                                                     senders select on stop.

  Also ask: "Could Close() be called twice?"  -> yes: sync.Once / mutex+flag.
            "Could a send race with Close?"   -> yes: redesign ownership or serialize with a mutex.
```

---

## 24. Cheat Sheet

```
 RULES
  1. Only the sender side closes.            5. Closing twice panics.
  2. Never close from the receiver.          6. Sending after close panics.
  3. Close once, ideally via defer.          7. Receiving after close: drain buffer, then zero,false.
  4. Many senders => WaitGroup + closer.     8. Closing is optional for GC; required to end `range`.

 IDIOMS
  done := make(chan struct{}); close(done)       // broadcast cancellation / latch
  for v := range ch { ... }                      // consume until closed
  v, ok := <-ch                                  // ok=false => closed and drained
  go func(){ wg.Wait(); close(out) }()           // close after all senders
  case v, ok := <-a: if !ok { a = nil }          // disable closed case in select
  func f() <-chan T { ch := ...; go func(){ defer close(ch); ... }(); return ch }

 BUG SMELLS
  - `close(ch)` inside a receiver loop
  - `close(ch)` right after spawning senders without waiting
  - `for { v := <-ch ... }` without ok check
  - blocking send without `select` on ctx.Done()
  - `isClosed(ch)` helper
  - closing a channel in a method callable more than once
```

| Question                                       | Answer                         |
|------------------------------------------------|--------------------------------|
| Does close drop buffered items?                | No                             |
| Does close unblock receivers?                  | Yes, all                       |
| Does close unblock senders?                    | Yes, by **panicking** them     |
| Can I reopen a closed channel?                 | No; make a new one             |
| Can I detect closed without receiving?         | Not safely                     |
| Is close a memory barrier?                     | Yes (happens-before receive)   |
| Is `len(ch)==0` equal to closed?               | No                             |
| Does `ctx.Done()` get closed?                  | Yes, on cancel/timeout         |

---

## 25. Interview Questions and Answers

**Q1. What happens when you receive from a closed channel?**
Remaining buffered values are delivered first. After the buffer is empty, receive returns the zero value immediately with `ok == false`, never blocking.

**Q2. Who should close a channel?**
The sender (owner of the send side). With multiple senders, a coordinator closes after all senders are done.

**Q3. What happens on closing a closed or nil channel?**
Both panic: `close of closed channel`, `close of nil channel`.

**Q4. Is it necessary to close every channel?**
No. Close only when receivers depend on it (range loop, broadcast). GC handles unreferenced channels.

**Q5. How do you broadcast to many goroutines?**
Close a `chan struct{}`. All current and future receivers unblock.

**Q6. How do you check whether a channel is closed?**
You can't safely without receiving. Use `v, ok := <-ch` (consumes a value) or design with a separate `done` channel.

**Q7. Why is `for { v := <-ch }` dangerous?**
After close it spins forever receiving zero values.

**Q8. How does `context` cancellation work?**
`ctx.Done()` returns a channel closed on cancel/timeout, with parent-to-child cascade and idempotent cancel guarded by a mutex.

**Q9. How do you close a channel with multiple producers?**
`WaitGroup` for producers, and a separate goroutine does `wg.Wait(); close(ch)`.

**Q10. What guarantee does the memory model give for close?**
Close happens-before a receive that returns because of the close.

**Q11. What is the nil-channel-in-select trick?**
Set a channel variable to `nil` after it closes so its `select` case never fires again.

**Q12. Why can closing cause a panic in another goroutine?**
A goroutine blocked in (or entering) a send on that channel panics when it observes the closed state.

**Q13. Difference between `close(done)` and `done <- struct{}{}`?**
Send wakes one receiver and requires a receiver per signal. Close wakes all, forever, and never blocks the closer.

**Q14. How can you implement a one-time "ready" event?**
`ready := make(chan struct{})`, `close(ready)` once (with `sync.Once` if multiple callers), waiters do `<-ready`.

---

## 26. Practice Exercises

1. **Safe broadcaster:** implement `type Event struct{...}` with `Fire()` (idempotent) and `Wait(ctx) error`.
2. **Bounded parallel map:** `ParallelMap(ctx, in <-chan T, n int, f func(T) U) <-chan U` which preserves no ordering, closes output after all workers finish, and stops promptly on `ctx` cancel.
3. **Ordered fan-in:** merge N sorted channels into one sorted channel; close output when all inputs are closed (use the nil trick).
4. **Graceful TCP server:** accept loop + per-conn goroutines; on SIGTERM, stop accepting, wait for connections with a timeout, then close the work queue.
5. **Leak hunt:** take a program with five leaky patterns from section 19; fix each and verify with `goleak`.
6. **Reimplement `context.WithCancel`** using a lazily-created channel, a mutex, and a children map; ensure idempotent cancel and cascade.
7. **Pub/Sub with slow-consumer policy:** extend section 13.9 so that slow consumers are disconnected (channel closed) rather than dropped messages.

---

### Final Mental Model Recap

```
 +------------------------------------------------------------------------------+
 |  close(ch) = "I, the owner of the send side, promise no more values."        |
 |                                                                              |
 |  * It is a SIGNAL (not resource cleanup).                                    |
 |  * It is a BROADCAST (all receivers, now and later, observe it).             |
 |  * It is ONE-SHOT (second close panics).                                     |
 |  * It must come AFTER the last send (else send panics).                      |
 |  * It publishes prior writes (happens-before).                               |
 |                                                                              |
 |  Design for it: single owner, defer close, WaitGroup-closer for many senders,|
 |  context/done channels for cancellation, `<-chan T` for consumers.           |
 +------------------------------------------------------------------------------+
```
