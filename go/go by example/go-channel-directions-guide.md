# Go Channel Directions — The Complete Guide

> Send-only (`chan<- T`), receive-only (`<-chan T`), and bidirectional (`chan T`) channels:
> what they are, how they work internally, where real systems use them, and how to think about them.

---

## Table of Contents

1. [The One-Sentence Mental Model](#1-the-one-sentence-mental-model)
2. [Channel Fundamentals (Prerequisites)](#2-channel-fundamentals-prerequisites)
3. [What Are Channel Directions?](#3-what-are-channel-directions)
4. [Syntax and How to Read It](#4-syntax-and-how-to-read-it)
5. [Under the Hood: The Runtime `hchan`](#5-under-the-hood-the-runtime-hchan)
6. [Conversion and Assignability Rules](#6-conversion-and-assignability-rules)
7. [Operation Matrix: What Is Allowed Where](#7-operation-matrix-what-is-allowed-where)
8. [Channel States and Behavior Table](#8-channel-states-and-behavior-table)
9. [Ownership and Who Closes the Channel](#9-ownership-and-who-closes-the-channel)
10. [Tricky Syntax and Parsing Cases](#10-tricky-syntax-and-parsing-cases)
11. [Where Directions Appear in the Standard Library](#11-where-directions-appear-in-the-standard-library)
12. [Real-World Patterns (with Code and Architecture)](#12-real-world-patterns-with-code-and-architecture)
13. [A Realistic End-to-End Architecture](#13-a-realistic-end-to-end-architecture)
14. [API Design Guidelines](#14-api-design-guidelines)
15. [Pitfalls, Anti-Patterns, and Debugging](#15-pitfalls-anti-patterns-and-debugging)
16. [Performance and When NOT to Use Channels](#16-performance-and-when-not-to-use-channels)
17. [Same Idea in Other Languages](#17-same-idea-in-other-languages)
18. [Decision Checklist and Cheat Sheet](#18-decision-checklist-and-cheat-sheet)
19. [Practice Exercises](#19-practice-exercises)

---

## 1. The One-Sentence Mental Model

> **A channel is one pipe. A channel direction is a *restricted handle* (a capability) to that same pipe — enforced by the compiler, not the runtime.**

Think of a physical water pipe with a tap on one end and a drain on the other:

```
        SAME UNDERLYING PIPE (one hchan in memory)
   ┌──────────────────────────────────────────────────┐
   │                                                  │
   │   [ producer handle ]  ══════════▶  [ consumer handle ]
   │      chan<- T                            <-chan T
   │      "can only pour in"                  "can only drain out"
   │                                                  │
   └──────────────────────────────────────────────────┘

   The creator holds the full-power handle:   chan T   (can do everything)
   and hands out restricted handles to others.
```

Three ideas to carry through the whole guide:

| Idea | Meaning |
|------|---------|
| **Capability restriction** | Direction removes abilities (send / receive / close) from a handle. |
| **Compile-time only** | No runtime cost. Same memory, same `hchan`. The compiler rejects illegal operations. |
| **One-way narrowing** | You can go `chan T → chan<- T` or `chan T → <-chan T`. **Never** the reverse. |

---

## 2. Channel Fundamentals (Prerequisites)

Directions only make sense if the base channel semantics are crystal clear.

### 2.1 Creating channels

```go
ch := make(chan int)      // unbuffered
bc := make(chan int, 5)   // buffered, capacity 5
```

### 2.2 Unbuffered channel = rendezvous

A send blocks until a receiver is ready, and a receive blocks until a sender is ready. They meet at the same instant.

```
 Goroutine A (sender)                Goroutine B (receiver)
 ────────────────────                ──────────────────────
   ch <- 42  ─── blocks ───┐
                           │  both ready: value handed over directly
                           └──────────────▶  v := <-ch
   continues                                 continues
```

### 2.3 Buffered channel = bounded queue

A send blocks only when the buffer is **full**. A receive blocks only when the buffer is **empty**.

```
 make(chan int, 3)

 send ──▶ ┌─────┬─────┬─────┐ ──▶ receive
          │  1  │  2  │  3  │      (FIFO order)
          └─────┴─────┴─────┘
          full → next send blocks
```

### 2.4 Closing

`close(ch)` signals "no more values will ever be sent."

- Receivers can still drain remaining buffered values.
- After drain, receives return the **zero value immediately**, with `ok == false`.
- Sending on a closed channel **panics**.
- Closing a closed channel **panics**.
- Closing a `nil` channel **panics**.

```go
v, ok := <-ch   // ok == false means: closed AND drained
```

### 2.5 The core operations

| Operation | Syntax |
|-----------|--------|
| Send | `ch <- v` |
| Receive | `v := <-ch`  /  `v, ok := <-ch` |
| Close | `close(ch)` |
| Iterate | `for v := range ch { }` (ends when closed and drained) |
| Multiplex | `select { case ... }` |
| Inspect | `len(ch)`, `cap(ch)` |

---

## 3. What Are Channel Directions?

A channel type can be declared in **three forms**:

| Type | Name | Can send | Can receive | Can close |
|------|------|:--------:|:-----------:|:---------:|
| `chan T`   | Bidirectional | ✅ | ✅ | ✅ |
| `chan<- T` | **Send-only**    | ✅ | ❌ | ✅ |
| `<-chan T` | **Receive-only** | ❌ | ✅ | ❌ |

> Notice: **close is allowed on send-only, forbidden on receive-only.** This encodes a design rule: *the sender side owns closing.*

### 3.1 Why do they exist?

1. **Intent documentation in the type system.** A function signature `func f(in <-chan int, out chan<- int)` says exactly what `f` does with each channel.
2. **Compile-time safety.** A consumer cannot accidentally send or close; a producer cannot accidentally read.
3. **Ownership clarity.** Only the owner (holding `chan T` or `chan<- T`) can close. Consumers physically cannot.
4. **Smaller API surface.** Library authors return `<-chan T` so users can't corrupt internal state.
5. **Fewer concurrency bugs.** Prevents "double close", "consumer closes producer's channel", and "send on closed channel" classes of bugs at compile time.

### 3.2 The "views" diagram

```
                      make(chan Event, 16)
                               │
                               │  value type: chan Event   (full power)
                               ▼
            ┌──────────────────────────────────────┐
            │            hchan (heap)              │
            │   buf, sendq, recvq, lock, closed    │
            └──────────────────────────────────────┘
                 ▲                            │
                 │                            │
   narrowed to   │                            │   narrowed to
   chan<- Event  │                            ▼   <-chan Event
        ┌────────┴────────┐          ┌────────────────┐
        │  PRODUCER code  │          │ CONSUMER code  │
        │  can: send      │          │ can: receive   │
        │  can: close     │          │ can: range     │
        │  cannot: recv   │          │ cannot: send   │
        └─────────────────┘          │ cannot: close  │
                                     └────────────────┘
```

---

## 4. Syntax and How to Read It

### 4.1 Declarations

```go
var a chan int      // bidirectional
var b chan<- int    // send-only
var c <-chan int    // receive-only
```

### 4.2 The reading trick

**The arrow shows the direction of data flow relative to the `chan` keyword.**

```
  chan<- int      "data flows  INTO  the chan"      → you SEND
  <-chan int      "data flows OUT OF the chan"      → you RECEIVE

  Visual aid:
        ch <- v        arrow points INTO ch   ==>  chan<-
        v := <-ch      arrow points OUT OF ch ==>  <-chan
```

### 4.3 In function signatures

```go
func produce(out chan<- int)  { out <- 1 }       // can only send
func consume(in <-chan int)   { v := <-in; _ = v } // can only receive
func relay(in <-chan int, out chan<- int) {      // transforms
    for v := range in {
        out <- v * 2
    }
}
```

### 4.4 In return types

```go
func generate() <-chan int {      // callers can only receive
    ch := make(chan int)
    go func() {
        defer close(ch)           // OK: inside, ch is chan int (bidirectional)
        for i := 0; i < 3; i++ {
            ch <- i
        }
    }()
    return ch                     // implicit narrowing: chan int → <-chan int
}
```

Key insight: inside `generate`, the local variable `ch` is **bidirectional**. The *return type* narrows what callers see.

### 4.5 In struct fields

```go
type Worker struct {
    Jobs    <-chan Job       // worker reads jobs
    Results chan<- Result    // worker writes results
    quit    chan struct{}    // internal, bidirectional
}
```

### 4.6 In channel-of-channel types

```go
type Request struct {
    Payload string
    Reply   chan<- Response  // server can only send a reply
}
```

---

## 5. Under the Hood: The Runtime `hchan`

Directions are **erased after type checking**. At runtime there is exactly one structure per channel. Simplified from `runtime/chan.go`:

```go
type hchan struct {
    qcount   uint           // number of elements currently in the buffer
    dataqsiz uint           // buffer capacity (0 for unbuffered)
    buf      unsafe.Pointer // ring buffer storage
    elemsize uint16
    closed   uint32         // 0 = open, 1 = closed
    elemtype *_type
    sendx    uint           // next write index in buf
    recvx    uint           // next read index in buf
    recvq    waitq          // goroutines blocked on receive
    sendq    waitq          // goroutines blocked on send
    lock     mutex          // protects all fields above
}
```

### 5.1 Memory layout diagram

```
 ch := make(chan int, 4)       ch  (a pointer, 8 bytes)  ──┐
                                                           │
                                                           ▼
 ┌──────────────────────────── hchan ────────────────────────────┐
 │ qcount   = 3                                                  │
 │ dataqsiz = 4                                                  │
 │ closed   = 0                                                  │
 │ sendx    = 3                                                  │
 │ recvx    = 0                                                  │
 │ lock     = (mutex)                                            │
 │ recvq    ──▶ (empty list of waiting receiver goroutines)      │
 │ sendq    ──▶ (empty list of waiting sender goroutines)        │
 │ buf      ──┐                                                  │
 └────────────┼──────────────────────────────────────────────────┘
              ▼
```

### 5.2 The ring buffer

```
 buf (dataqsiz = 4)

     index:  0     1     2     3
          ┌─────┬─────┬─────┬─────┐
          │ 10  │ 20  │ 30  │     │
          └─────┴─────┴─────┴─────┘
             ▲                 ▲
           recvx             sendx
         (next read)       (next write)

   qcount = 3
   send  → writes at sendx, sendx = (sendx+1) % dataqsiz, qcount++
   recv  → reads  at recvx, recvx = (recvx+1) % dataqsiz, qcount--
```

### 5.3 What happens on a blocked operation

```
 Receiver calls <-ch on an EMPTY, open channel:

   goroutine R
      │
      ▼
   lock(hchan)
      │  buffer empty, no waiting sender
      ▼
   enqueue R into recvq  ──▶  gopark (goroutine sleeps, scheduler runs others)
      ...
   later: sender S does ch <- v
      │
      ▼
   lock(hchan); finds R in recvq
      │
      ▼
   copy v DIRECTLY into R's stack slot (bypasses buffer)
      │
      ▼
   goready(R)   ──▶  R becomes runnable
```

```
 hchan.recvq (wait queue of sudog nodes)

   ┌───────────┐    ┌───────────┐    ┌───────────┐
   │ sudog(G7) │───▶│ sudog(G9) │───▶│ sudog(G2) │───▶ nil
   └───────────┘    └───────────┘    └───────────┘
      first                              last
   (FIFO: G7 is woken first)
```

### 5.4 Where do directions live in the compiler?

```
 Source code  ──▶  parser  ──▶  type checker  ──▶  SSA / codegen  ──▶  binary
                                      │
                       direction is checked HERE
                       (cannot send to receive-only channel, etc.)

                       After this stage, `chan<- int` and `<-chan int`
                       and `chan int` compile to the SAME pointer-to-hchan.
```

Practical takeaway: **narrowing a channel is free.** No allocation, no copy, no wrapper. It's a type-level label on a pointer.

---

## 6. Conversion and Assignability Rules

### 6.1 The conversion lattice

```
                 chan T   (bidirectional)
                  /    \
         implicit/      \implicit
                /        \
               ▼          ▼
         chan<- T        <-chan T
         (send-only)     (receive-only)

   ✅  chan T   →  chan<- T      (implicit)
   ✅  chan T   →  <-chan T      (implicit)
   ❌  chan<- T →  chan T        (never)
   ❌  <-chan T →  chan T        (never)
   ❌  chan<- T →  <-chan T      (never)
   ❌  <-chan T →  chan<- T      (never)
```

You can **only remove** abilities, never add them.

### 6.2 Examples

```go
ch := make(chan int)

var s chan<- int = ch      // OK
var r <-chan int = ch      // OK

// var b chan int = s      // ERROR: cannot use s (chan<- int) as chan int
// var b2 chan int = r     // ERROR
// var x <-chan int = s    // ERROR: different directions

// Explicit conversion has the same restriction:
_ = (<-chan int)(ch)       // OK (from bidirectional)
// _ = (chan int)(r)       // ERROR
```

### 6.3 Passing to functions

```go
func send(out chan<- int)     {}
func recv(in <-chan int)      {}
func both(c chan int)         {}

ch := make(chan int)
send(ch)    // OK  (narrowed implicitly at call site)
recv(ch)    // OK
both(ch)    // OK

var s chan<- int = ch
both(s)     // ERROR: cannot use s (chan<- int) as chan int
recv(s)     // ERROR
```

### 6.4 The assignability rule (spec wording, simplified)

A value `x` of channel type `V` is assignable to channel type `T` if:

- `V` is a **bidirectional** channel, and
- `T` is a channel type with **identical element type**, and
- **at least one** of `V` or `T` is **not a named (defined) type**.

```go
type IntChan chan int
type RecvOnly <-chan int

var a IntChan = make(IntChan)
var r RecvOnly = a       // ERROR? both are defined types → not assignable
var r2 <-chan int = a    // OK: <-chan int is an unnamed type literal
```

### 6.5 Directional channels in generics

```go
func Drain[T any](in <-chan T) []T {
    var out []T
    for v := range in { out = append(out, v) }
    return out
}

// Constraint on channel types themselves:
func Collect[C ~<-chan T | ~chan T, T any](c C) {}  // possible but rarely worth it.
// Prefer: func Collect[T any](c <-chan T)  — bidirectional chans narrow automatically.
```

Recommendation: write generic functions against `<-chan T` / `chan<- T`; callers pass bidirectional channels and they narrow implicitly.

---

## 7. Operation Matrix: What Is Allowed Where

| Operation | `chan T` | `chan<- T` | `<-chan T` |
|-----------|:--------:|:----------:|:----------:|
| `ch <- v` (send) | ✅ | ✅ | ❌ compile error |
| `<-ch` (receive) | ✅ | ❌ compile error | ✅ |
| `v, ok := <-ch` | ✅ | ❌ | ✅ |
| `close(ch)` | ✅ | ✅ | ❌ compile error |
| `for range ch` | ✅ | ❌ | ✅ |
| `select` send case | ✅ | ✅ | ❌ |
| `select` receive case | ✅ | ❌ | ✅ |
| `len(ch)` / `cap(ch)` | ✅ | ✅ | ✅ |
| Compare with `==` / `nil` | ✅ | ✅ | ✅ |
| Use as map key | ✅ | ✅ | ✅ |

### 7.1 Compiler error messages you will see

```text
invalid operation: cannot send to receive-only channel in (variable of type <-chan int)
invalid operation: cannot receive from send-only channel out (variable of type chan<- int)
invalid operation: cannot close receive-only channel in (variable of type <-chan int)
cannot range over out (variable of type chan<- int): receive from send-only channel
```

### 7.2 Why is `close` allowed on send-only?

```
 Rule of thumb:  "Whoever can send decides when sending ends."

   Sender side  ──▶ knows when production is complete ──▶ may close
   Receiver side ──▶ cannot know if more senders exist  ──▶ must NOT close
```

If a receiver could close, any remaining sender would panic with *send on closed channel*. Direction makes that impossible by construction.

---

## 8. Channel States and Behavior Table

| State | Send | Receive | Close |
|-------|------|---------|-------|
| **nil** | blocks forever | blocks forever | **panic** |
| **open, unbuffered** | blocks until receiver | blocks until sender | OK |
| **open, buffered, not full** | succeeds immediately | blocks if empty | OK |
| **open, buffered, full** | blocks | succeeds immediately | OK |
| **closed, buffer non-empty** | **panic** | returns buffered values (`ok=true`) | **panic** |
| **closed, drained** | **panic** | zero value, `ok=false`, immediately | **panic** |

### 8.1 State machine

```
                 make()
                   │
                   ▼
            ┌────────────┐   close()    ┌──────────────────┐   drain    ┌────────────────┐
            │   OPEN     │─────────────▶│ CLOSED (has data)│───────────▶│ CLOSED+DRAINED │
            │ send/recv  │              │ recv only        │            │ recv → zero,   │
            └────────────┘              │ send → PANIC     │            │ ok=false       │
                                        └──────────────────┘            └────────────────┘

   Any state:  second close()  → PANIC
   nil channel: separate world — all ops block forever (close panics)
```

### 8.2 The nil-channel `select` trick

A nil channel in a `select` is **never ready**. This lets you dynamically disable cases:

```go
func merge2(a, b <-chan int) <-chan int {
    out := make(chan int)
    go func() {
        defer close(out)
        for a != nil || b != nil {
            select {
            case v, ok := <-a:
                if !ok { a = nil; continue }   // disable this case forever
                out <- v
            case v, ok := <-b:
                if !ok { b = nil; continue }
                out <- v
            }
        }
    }()
    return out
}
```

Note `a` and `b` are local copies of receive-only handles — assigning `nil` to a *local variable* is legal and does not affect the channel.

---

## 9. Ownership and Who Closes the Channel

### 9.1 The golden rule

> **The goroutine that creates (or exclusively writes to) a channel is responsible for closing it. Receivers never close.**

### 9.2 Single producer

```
   ┌──────────────────┐   chan T   ┌──────────────────┐
   │     PRODUCER     │═══════════▶│     CONSUMER     │
   │ creates channel  │            │ receives only    │
   │ sends + closes   │            │ (<-chan T)       │
   └──────────────────┘            └──────────────────┘
```

### 9.3 Multiple producers → coordinate closing

Producers cannot each close. Use a `sync.WaitGroup` and a dedicated closer goroutine:

```go
func fanIn[T any](srcs ...<-chan T) <-chan T {
    out := make(chan T)
    var wg sync.WaitGroup
    wg.Add(len(srcs))
    for _, s := range srcs {
        go func(c <-chan T) {
            defer wg.Done()
            for v := range c { out <- v }
        }(s)
    }
    go func() {          // the single closer
        wg.Wait()
        close(out)
    }()
    return out
}
```

```
   producer 1 ══╗
   producer 2 ══╬═══▶ out ═══▶ consumer
   producer 3 ══╝       ▲
                        │
              closer goroutine:  wg.Wait(); close(out)
```

### 9.4 Multiple consumers

Many goroutines can receive from the same `<-chan T`; each value goes to exactly one of them (work distribution, not broadcast).

```
                      ┌──▶ worker 1
   producer ═▶ chan ══╬──▶ worker 2     each item delivered to ONE worker
                      └──▶ worker 3
```

### 9.5 Broadcasting

Channels do **not** broadcast values. Only `close()` is observed by *all* receivers, which is why `done`/`ctx.Done()` channels are closed rather than sent on.

```
   close(done) ───▶ every goroutine blocked on <-done wakes up
```

---

## 10. Tricky Syntax and Parsing Cases

The `<-` operator associates with the **leftmost possible `chan`**.

| Written | Parsed as | Meaning |
|---------|-----------|---------|
| `chan<- chan int` | `chan<- (chan int)` | send-only channel of bidirectional int channels |
| `chan<- <-chan int` | `chan<- (<-chan int)` | send-only channel of receive-only int channels |
| `<-chan <-chan int` | `<-chan (<-chan int)` | receive-only channel of receive-only int channels |
| `<-chan chan int` | `<-chan (chan int)` | receive-only channel of bidirectional int channels |
| `chan (<-chan int)` | bidirectional chan of `<-chan int` | parentheses make it explicit |

```go
var x chan<- chan int        // you may send channels into x
var y <-chan <-chan int      // you receive channels, each of which you can only receive from
var z chan (<-chan int)      // parenthesized for clarity
```

**Style tip:** when nesting, use a named type or parentheses for readability.

```go
type Stream <-chan int
var streams <-chan Stream     // clearer than <-chan <-chan int
```

---

## 11. Where Directions Appear in the Standard Library

These are real signatures you use daily.

| API | Signature (simplified) | Direction | Why |
|-----|------------------------|-----------|-----|
| `context.Context.Done` | `Done() <-chan struct{}` | recv-only | You may *wait* for cancellation; you must never close or send. |
| `time.After` | `After(d Duration) <-chan Time` | recv-only | Runtime sends one value; you only receive. |
| `time.Tick` | `Tick(d Duration) <-chan Time` | recv-only | Same. |
| `time.Timer.C` / `time.Ticker.C` | field `C <-chan Time` | recv-only | The timer owns the channel. |
| `os/signal.Notify` | `Notify(c chan<- os.Signal, sig ...os.Signal)` | send-only param | Runtime *sends* signals into your channel; it never reads. |
| `os/signal.Stop` | `Stop(c chan<- os.Signal)` | send-only param | Unregister. |
| `net/http` request cancel patterns | `r.Context().Done()` | recv-only | Request lifecycle. |
| `sync/errgroup` | internal | — | Uses channels/Once internally; exposes `Wait()`. |
| `reflect.Value.Recv/Send` | dynamic | — | Honors directions at runtime via `ChanDir`. |

### 11.1 Anatomy of `signal.Notify`

```go
sigs := make(chan os.Signal, 1)        // MUST be buffered (runtime does non-blocking send)
signal.Notify(sigs, syscall.SIGINT, syscall.SIGTERM)

go func() {
    s := <-sigs                         // we only receive
    log.Println("got", s)
}()
```

```
   OS kernel ──signal──▶ Go runtime ──┐
                                      │  chan<- os.Signal   (runtime's view)
                                      ▼
                              ┌──────────────┐
                              │  sigs (buf 1)│
                              └──────┬───────┘
                                     │  <-chan os.Signal   (your goroutine's view)
                                     ▼
                              your handler goroutine
```

### 11.2 Anatomy of `ctx.Done()`

```go
func worker(ctx context.Context, in <-chan Job) {
    for {
        select {
        case <-ctx.Done():          // read-only cancel signal
            return
        case j, ok := <-in:
            if !ok { return }
            process(j)
        }
    }
}
```

```
   cancel() called
        │
        ▼
   context internals:  close(c.done)   ◀── only the context owns this right
        │
        ├─▶ worker A wakes (<-ctx.Done())
        ├─▶ worker B wakes
        └─▶ worker C wakes
```

### 11.3 Anatomy of `time.Ticker`

```go
t := time.NewTicker(time.Second)
defer t.Stop()
for {
    select {
    case <-t.C:        // t.C is <-chan time.Time: you can't send fake ticks
        poll()
    case <-ctx.Done():
        return
    }
}
```

---

## 12. Real-World Patterns (with Code and Architecture)

### Pattern 1 — Generator (Producer returns `<-chan`)

**Use case:** paginated API reads, file line streaming, DB row cursors, event feeds.

```go
func readLines(ctx context.Context, path string) (<-chan string, <-chan error) {
    lines := make(chan string)
    errc  := make(chan error, 1)
    go func() {
        defer close(lines)
        defer close(errc)
        f, err := os.Open(path)
        if err != nil { errc <- err; return }
        defer f.Close()
        sc := bufio.NewScanner(f)
        for sc.Scan() {
            select {
            case lines <- sc.Text():
            case <-ctx.Done():
                errc <- ctx.Err()
                return
            }
        }
        if err := sc.Err(); err != nil { errc <- err }
    }()
    return lines, errc
}
```

```
   caller                      generator goroutine
   ──────                      ───────────────────
   readLines(...) ───────────▶ spawns goroutine
      ◀── <-chan string ───────  (lines)
      ◀── <-chan error  ───────  (errc)

   for l := range lines { }  ◀═══ lines ═══ scanner.Scan()
```

Why directions: callers get a *stream they can consume but not corrupt or close*.

---

### Pattern 2 — Pipeline (Stage chaining)

**Use case:** ETL, log processing, image processing, parsing → validating → enriching → storing.

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

func square(ctx context.Context, in <-chan int) <-chan int {
    out := make(chan int)
    go func() {
        defer close(out)
        for v := range in {
            select {
            case out <- v * v:
            case <-ctx.Done(): return
            }
        }
    }()
    return out
}

func main() {
    ctx, cancel := context.WithCancel(context.Background())
    defer cancel()
    for v := range square(ctx, square(ctx, gen(ctx, 1, 2, 3))) {
        fmt.Println(v)   // 1, 16, 81
    }
}
```

```
 ┌─────────┐ <-chan ┌─────────┐ <-chan ┌─────────┐ <-chan ┌──────────┐
 │  gen    │═══════▶│ square  │═══════▶│ square  │═══════▶│ consumer │
 │ (source)│  int   │ (stage) │  int   │ (stage) │  int   │  (sink)  │
 └─────────┘        └─────────┘        └─────────┘        └──────────┘
      │                  │                  │
      └────────── all stages watch ctx.Done() ──────────┘

 Each stage:  in  <-chan T    (reads upstream)
              out chan T      (creates, writes, closes) → returned as <-chan T
 Closing propagates downstream:  gen closes → square sees range end → closes → ...
```

The arrow chain is the *type-level proof* that data flows one way.

---

### Pattern 3 — Worker Pool (Fan-out + Fan-in)

**Use case:** concurrent HTTP fetches, thumbnail generation, batch DB writes, CPU-bound task fanout.

```go
type Job    struct{ ID int; URL string }
type Result struct{ ID int; Status int; Err error }

func worker(ctx context.Context, jobs <-chan Job, results chan<- Result) {
    for j := range jobs {
        res := Result{ID: j.ID}
        req, _ := http.NewRequestWithContext(ctx, http.MethodGet, j.URL, nil)
        resp, err := http.DefaultClient.Do(req)
        if err != nil { res.Err = err } else { res.Status = resp.StatusCode; resp.Body.Close() }
        select {
        case results <- res:
        case <-ctx.Done(): return
        }
    }
}

func Run(ctx context.Context, urls []string, n int) []Result {
    jobs    := make(chan Job)
    results := make(chan Result)

    var wg sync.WaitGroup
    wg.Add(n)
    for i := 0; i < n; i++ {
        go func() { defer wg.Done(); worker(ctx, jobs, results) }()
    }

    go func() {                      // feeder: sole owner of jobs → closes it
        defer close(jobs)
        for i, u := range urls {
            select {
            case jobs <- Job{i, u}:
            case <-ctx.Done(): return
            }
        }
    }()

    go func() { wg.Wait(); close(results) }()   // closer for results

    var out []Result
    for r := range results { out = append(out, r) }
    return out
}
```

```
                          ┌────────────────────────────────────────────┐
                          │                 Run()                      │
                          │                                            │
   ┌──────────┐ chan<-    │      jobs  (chan Job)                      │
   │  feeder  │══════════▶│═══════╦═══════╦═══════╗                    │
   │ goroutine│   Job     │       ║       ║       ║  <-chan Job        │
   └──────────┘           │       ▼       ▼       ▼                    │
   (owns & closes jobs)   │   ┌───────┐┌───────┐┌───────┐              │
                          │   │worker1││worker2││worker3│  ... N       │
                          │   └───┬───┘└───┬───┘└───┬───┘              │
                          │       ║        ║        ║  chan<- Result   │
                          │       ╚════════╩════════╝                  │
                          │                ║                           │
                          │         results (chan Result)              │
                          │                ║                           │
   ┌──────────┐ <-chan    │                ▼                           │
   │collector │◀══════════│═══════════════ ◀                           │
   └──────────┘  Result   │                                            │
                          │   closer: wg.Wait(); close(results)        │
                          └────────────────────────────────────────────┘
```

**Direction roles:**
- `jobs <-chan Job` in workers → workers can't push or close the job queue.
- `results chan<- Result` in workers → workers can't steal results or close.
- Feeder closes `jobs`; closer closes `results`. Exactly one closer each.

---

### Pattern 4 — Fan-in / Merge

**Use case:** aggregate results from multiple sources (shards, microservices, sensors).

```go
func merge[T any](ctx context.Context, cs ...<-chan T) <-chan T {
    out := make(chan T)
    var wg sync.WaitGroup
    forward := func(c <-chan T) {
        defer wg.Done()
        for v := range c {
            select {
            case out <- v:
            case <-ctx.Done(): return
            }
        }
    }
    wg.Add(len(cs))
    for _, c := range cs { go forward(c) }
    go func() { wg.Wait(); close(out) }()
    return out
}
```

```
   shard A ═══ <-chan T ═══╗
   shard B ═══ <-chan T ═══╬═══▶ merge ═══ <-chan T ═══▶ consumer
   shard C ═══ <-chan T ═══╝
```

---

### Pattern 5 — Request/Response with Reply Channel

**Use case:** actor-style services, serialized state owner goroutine, in-process RPC, connection managers.

```go
type GetReq struct {
    Key   string
    Reply chan<- GetResp        // server can only send back
}
type GetResp struct {
    Val string
    OK  bool
}

type Store struct {
    reqs chan GetReq
}

func NewStore() *Store {
    s := &Store{reqs: make(chan GetReq)}
    go s.loop()
    return s
}

func (s *Store) loop() {
    data := map[string]string{"a": "1"}   // owned by this goroutine only: no mutex
    for r := range s.reqs {
        v, ok := data[r.Key]
        r.Reply <- GetResp{v, ok}
    }
}

func (s *Store) Get(key string) (string, bool) {
    reply := make(chan GetResp, 1)        // buffered 1: server never blocks if caller bails
    s.reqs <- GetReq{Key: key, Reply: reply}
    r := <-reply
    return r.Val, r.OK
}
```

```
   Client goroutine                         Server (state owner) goroutine
   ────────────────                         ──────────────────────────────
   reply := make(chan GetResp, 1)
        │
        │   GetReq{Key, Reply: reply}
        ├══════════ s.reqs ═════════════════▶ r := <-reqs
        │            (chan GetReq)               │
        │                                        │ lookup in private map
        │                                        ▼
        │◀═════════ reply (chan<- GetResp) ═══ r.Reply <- resp
        ▼
   resp := <-reply
```

Direction `Reply chan<- GetResp` ensures the server **cannot read** the client's reply channel and cannot close it.

---

### Pattern 6 — Pub/Sub Broker (returns `<-chan` per subscriber)

**Use case:** in-process event bus, config change notifications, websocket fan-out, cache invalidation.

```go
type Broker[T any] struct {
    mu     sync.RWMutex
    subs   map[int]chan T
    nextID int
    closed bool
}

func NewBroker[T any]() *Broker[T] {
    return &Broker[T]{subs: make(map[int]chan T)}
}

// Subscribe returns a receive-only channel: subscribers cannot publish or close.
func (b *Broker[T]) Subscribe(buf int) (id int, ch <-chan T) {
    b.mu.Lock()
    defer b.mu.Unlock()
    c := make(chan T, buf)
    if b.closed { close(c); return -1, c }
    id = b.nextID
    b.nextID++
    b.subs[id] = c
    return id, c
}

func (b *Broker[T]) Unsubscribe(id int) {
    b.mu.Lock()
    defer b.mu.Unlock()
    if c, ok := b.subs[id]; ok {
        delete(b.subs, id)
        close(c)                    // broker owns it → broker closes it
    }
}

// Publish does a non-blocking send so a slow subscriber can't stall everyone.
func (b *Broker[T]) Publish(v T) {
    b.mu.RLock()
    defer b.mu.RUnlock()
    for _, c := range b.subs {
        select {
        case c <- v:
        default:                    // subscriber buffer full → drop (policy choice!)
        }
    }
}

func (b *Broker[T]) Close() {
    b.mu.Lock()
    defer b.mu.Unlock()
    if b.closed { return }
    b.closed = true
    for id, c := range b.subs { close(c); delete(b.subs, id) }
}
```

```
                       ┌───────────────────────────────┐
   Publisher(s)        │            Broker             │
   ────────────        │                               │
   Publish(ev) ═══════▶│  subs map:                    │
                       │    id0 ─▶ chan T (buf N) ═════╪═══▶ <-chan T ──▶ subscriber A
                       │    id1 ─▶ chan T (buf N) ═════╪═══▶ <-chan T ──▶ subscriber B
                       │    id2 ─▶ chan T (buf N) ═════╪═══▶ <-chan T ──▶ subscriber C
                       │                               │
                       │  broker keeps chan T (full)   │
                       │  subscribers get <-chan T     │
                       └───────────────────────────────┘

   Slow subscriber policy:  drop  |  block  |  disconnect   ← explicit design decision
```

Without directions, any subscriber could `close()` its channel and cause a **panic in Publish** (send on closed channel). With `<-chan T`, that bug cannot compile.

---

### Pattern 7 — Done / Quit / Cancellation Signal

**Use case:** stopping goroutines, graceful shutdown, timeouts.

```go
func poller(done <-chan struct{}, out chan<- Tick) {
    t := time.NewTicker(time.Second)
    defer t.Stop()
    for {
        select {
        case <-done:
            return
        case now := <-t.C:
            select {
            case out <- Tick{now}:
            case <-done:
                return
            }
        }
    }
}

func main() {
    done := make(chan struct{})
    out  := make(chan Tick)
    go poller(done, out)          // poller gets done as <-chan struct{}
    ...
    close(done)                   // owner closes; broadcast to all listeners
}
```

```
   owner ─── close(done) ───▶  ┌──────────────┐
                               │ done (chan   │
                               │   struct{})  │
                               └──────┬───────┘
                      ┌───────────────┼────────────────┐
                      ▼               ▼                ▼
                 poller A          poller B         poller C
              (<-chan struct{})  (<-chan struct{}) (<-chan struct{})
```

`chan struct{}` carries zero bytes of payload — only the *signal*.

---

### Pattern 8 — Semaphore / Concurrency Limiter

**Use case:** cap concurrent outbound calls, protect a DB pool.

```go
type Sem chan struct{}

func NewSem(n int) Sem { return make(Sem, n) }

func (s Sem) Acquire(ctx context.Context) error {
    select {
    case s <- struct{}{}:  return nil
    case <-ctx.Done():     return ctx.Err()
    }
}
func (s Sem) Release() { <-s }
```

```
   capacity = 3

   Acquire ─▶ ┌───┬───┬───┐ ─▶ Release
              │ x │ x │   │     2 in use, 1 free slot
              └───┴───┴───┘
   full → Acquire blocks until someone Releases
```

(For real production use, consider `golang.org/x/sync/semaphore`.)

---

### Pattern 9 — Timeout / First-Response-Wins

```go
func fetchFirst(ctx context.Context, urls []string) (string, error) {
    ctx, cancel := context.WithCancel(ctx)
    defer cancel()

    type res struct{ body string; err error }
    out := make(chan res, len(urls))      // buffered: losers never block/leak

    for _, u := range urls {
        go func(u string) {
            b, err := get(ctx, u)
            out <- res{b, err}
        }(u)
    }

    select {
    case r := <-out:    return r.body, r.err
    case <-ctx.Done():  return "", ctx.Err()
    case <-time.After(2 * time.Second): return "", errors.New("timeout")
    }
}
```

```
   ┌── get(url1) ──┐
   ├── get(url2) ──┼──▶ out (buffered N) ──▶ first result wins ──▶ cancel() others
   └── get(url3) ──┘
```

---

### Pattern 10 — Result + Error Channels (or a combined struct)

```go
type Item struct {
    Val int
    Err error
}

func stream() <-chan Item { ... }

for it := range stream() {
    if it.Err != nil { /* handle */ continue }
    use(it.Val)
}
```

Preferred over two separate channels when ordering between values and errors matters.

---

### Pattern 11 — Rate Limiter / Token Bucket via Ticker

```go
func limiter(rps int) <-chan time.Time {
    return time.NewTicker(time.Second / time.Duration(rps)).C   // already <-chan Time
}

func main() {
    tick := limiter(5)
    for req := range requests {
        <-tick                 // wait for next token
        go handle(req)
    }
}
```

---

### Pattern 12 — Single Writer, Many Readers (Serialized Sink)

**Use case:** log writer, metrics emitter, DB batch writer — serialize access to a non-thread-safe resource.

```go
func logSink(in <-chan LogLine, w io.Writer) {
    bw := bufio.NewWriter(w)
    defer bw.Flush()
    for l := range in {
        fmt.Fprintln(bw, l)
    }
}

// Many producers receive chan<- LogLine:
func producer(id int, out chan<- LogLine) { out <- LogLine{...} }
```

```
   producer 1 ═▶ chan<- ╗
   producer 2 ═▶ chan<- ╬══▶ [ chan LogLine ] ══▶ <-chan ══▶ logSink ══▶ file/stdout
   producer 3 ═▶ chan<- ╝                                     (single writer)
```

---

### Summary table of patterns

| Pattern | Typical direction usage | Real-world example |
|---------|------------------------|--------------------|
| Generator | return `<-chan T` | DB cursor, paginated API |
| Pipeline | each stage `in <-chan`, returns `<-chan` | ETL, log parsing |
| Worker pool | workers: `jobs <-chan`, `results chan<-` | crawler, image resize |
| Fan-in | args `...<-chan T`, returns `<-chan T` | shard aggregation |
| Request/reply | `Reply chan<- Resp` in request struct | actor/state owner |
| Pub/Sub | Subscribe returns `<-chan T` | event bus, websocket hub |
| Done/cancel | `done <-chan struct{}` | graceful shutdown |
| Semaphore | internal `chan struct{}` | rate/concurrency limit |
| Signal | `chan<- os.Signal` param | SIGTERM handling |
| Ticker | `Ticker.C <-chan Time` | polling, heartbeats |

---

## 13. A Realistic End-to-End Architecture

**Scenario:** a log/event ingestion service. HTTP handlers accept events, validate them, enrich them in parallel, batch them, and write to a database. Graceful shutdown on SIGTERM.

### 13.1 The system diagram

```
                                  ┌─────────────────────────────────────────────────┐
                                  │                 INGEST SERVICE                  │
                                  │                                                 │
 ┌───────────┐   HTTP POST        │  ┌───────────────┐                              │
 │  Clients  │───────────────────▶│  │ HTTP handlers │ (N goroutines, net/http)     │
 └───────────┘                    │  └───────┬───────┘                              │
                                  │          │ chan<- RawEvent                      │
                                  │          ▼                                      │
                                  │   ┌─────────────┐                               │
                                  │   │  raw (buf)  │  chan RawEvent  cap=1024      │
                                  │   └──────┬──────┘                               │
                                  │          │ <-chan RawEvent                      │
                                  │          ▼                                      │
                                  │   ┌─────────────┐                               │
                                  │   │  Validator  │ 1 goroutine                   │
                                  │   └──────┬──────┘                               │
                                  │          │ chan<- Event (valid)                 │
                                  │          ▼                                      │
                                  │   ┌─────────────┐                               │
                                  │   │ valid (buf) │  chan Event  cap=512          │
                                  │   └──────┬──────┘                               │
                                  │          │ <-chan Event          (fan-out)      │
                                  │    ┌─────┼──────┬──────┐                        │
                                  │    ▼     ▼      ▼      ▼                        │
                                  │  ┌────┐ ┌────┐ ┌────┐ ┌────┐                    │
                                  │  │ E1 │ │ E2 │ │ E3 │ │ E4 │  Enrichers (geo-IP,│
                                  │  └─┬──┘ └─┬──┘ └─┬──┘ └─┬──┘  user lookup)      │
                                  │    │      │      │      │  chan<- Enriched       │
                                  │    └──────┴──┬───┴──────┘      (fan-in)          │
                                  │              ▼                                  │
                                  │      ┌──────────────┐                           │
                                  │      │ enriched buf │  chan Enriched cap=512    │
                                  │      └──────┬───────┘                           │
                                  │             │ <-chan Enriched                   │
                                  │             ▼                                   │
                                  │      ┌──────────────┐  flush on size OR ticker  │
                                  │      │   Batcher    │◀── time.Ticker.C          │
                                  │      └──────┬───────┘     (<-chan time.Time)    │
                                  │             │ chan<- []Enriched                 │
                                  │             ▼                                   │
                                  │      ┌──────────────┐                           │
                                  │      │  batches buf │  chan []Enriched cap=8    │
                                  │      └──────┬───────┘                           │
                                  │             │ <-chan []Enriched                 │
                                  │             ▼                                   │
                                  │      ┌──────────────┐        ┌──────────────┐   │
                                  │      │  DB Writers  │───────▶│   Database   │   │
                                  │      │   (M conns)  │        └──────────────┘   │
                                  │      └──────────────┘                           │
                                  │                                                 │
 ┌────────────┐  SIGTERM          │  ┌──────────────────────────────────────────┐   │
 │  OS / k8s  │──────────────────▶│  │ main: signal.Notify(chan<- os.Signal)    │   │
 └────────────┘                   │  │       cancel() → ctx.Done() <-chan{}     │   │
                                  │  │       broadcast to all stages            │   │
                                  │  └──────────────────────────────────────────┘   │
                                  └─────────────────────────────────────────────────┘
```

### 13.2 Why directions matter at each hop

| Hop | Producer holds | Consumer holds | Closer |
|-----|----------------|----------------|--------|
| handlers → validator | `chan<- RawEvent` | `<-chan RawEvent` | `main` after handlers stop (HTTP server shutdown) |
| validator → enrichers | `chan<- Event` | `<-chan Event` | validator (after raw closes) |
| enrichers → batcher | `chan<- Enriched` | `<-chan Enriched` | closer goroutine (after `wg.Wait()` for enrichers) |
| batcher → writers | `chan<- []Enriched` | `<-chan []Enriched` | batcher |
| main ← OS | runtime holds `chan<- os.Signal` | main holds `<-chan os.Signal` | never closed |
| all stages ← ctx | `ctx` owner | `<-chan struct{}` via `ctx.Done()` | `cancel()` |

### 13.3 Code skeleton

```go
func Run(ctx context.Context, cfg Config) error {
    raw      := make(chan RawEvent, 1024)
    valid    := make(chan Event, 512)
    enriched := make(chan Enriched, 512)
    batches  := make(chan []Enriched, 8)

    g, ctx := errgroup.WithContext(ctx)

    // Stage 1: HTTP → raw
    srv := newHTTPServer(cfg, raw)              // raw passed as chan<- RawEvent
    g.Go(func() error { return srv.Serve(ctx) })

    // Stage 2: validator
    g.Go(func() error {
        defer close(valid)
        return validate(ctx, raw, valid)        // (<-chan RawEvent, chan<- Event)
    })

    // Stage 3: enrichers (fan-out) + closer (fan-in)
    var wg sync.WaitGroup
    for i := 0; i < cfg.Enrichers; i++ {
        wg.Add(1)
        g.Go(func() error {
            defer wg.Done()
            return enrich(ctx, valid, enriched) // (<-chan Event, chan<- Enriched)
        })
    }
    g.Go(func() error { wg.Wait(); close(enriched); return nil })

    // Stage 4: batcher
    g.Go(func() error {
        defer close(batches)
        return batch(ctx, enriched, batches, cfg.BatchSize, cfg.FlushEvery)
    })

    // Stage 5: DB writers
    for i := 0; i < cfg.Writers; i++ {
        g.Go(func() error { return write(ctx, batches, cfg.DB) })  // (<-chan []Enriched)
    }

    return g.Wait()
}

func batch(ctx context.Context, in <-chan Enriched, out chan<- []Enriched,
           size int, every time.Duration) error {
    t := time.NewTicker(every)
    defer t.Stop()
    buf := make([]Enriched, 0, size)
    flush := func() {
        if len(buf) == 0 { return }
        select {
        case out <- buf:
        case <-ctx.Done():
        }
        buf = make([]Enriched, 0, size)
    }
    for {
        select {
        case e, ok := <-in:
            if !ok { flush(); return nil }
            buf = append(buf, e)
            if len(buf) >= size { flush() }
        case <-t.C:
            flush()
        case <-ctx.Done():
            return ctx.Err()
        }
    }
}
```

### 13.4 Graceful shutdown sequence

```
 t0  SIGTERM arrives
      │
 t1  main:  cancel root ctx  (or stop HTTP server first to stop intake)
      │
 t2  HTTP server stops accepting → handlers finish → raw is no longer written
      │
 t3  main closes raw        (single owner of raw)
      │
 t4  validator: range raw ends → close(valid)
      │
 t5  enrichers: range valid ends → wg.Done() ×N → closer: close(enriched)
      │
 t6  batcher: range enriched ends → final flush → close(batches)
      │
 t7  writers: range batches ends → return
      │
 t8  g.Wait() returns → process exits cleanly, no data lost

 Closing cascades DOWNSTREAM, each stage closing only the channel it OWNS.
```

This cascade only works because **ownership is enforced by direction**: no downstream stage can close an upstream channel.

### 13.5 Backpressure behavior

```
   slow DB writers
        │
        ▼
   batches buffer fills (cap 8)
        │
        ▼
   batcher blocks on `out <- buf`
        │
        ▼
   enriched buffer fills
        │
        ▼
   enrichers block
        │
        ▼
   valid buffer fills → validator blocks → raw fills
        │
        ▼
   HTTP handlers block or return 429 (use select+default to shed load)
```

Bounded buffered channels create **natural backpressure** through the whole pipeline.

---

## 14. API Design Guidelines

### 14.1 Rules of thumb

| Situation | Use |
|-----------|-----|
| Function only reads | parameter `<-chan T` |
| Function only writes | parameter `chan<- T` |
| Function creates a channel for callers to read | return `<-chan T` |
| Function accepts a channel for it to write into | `chan<- T` (caller creates, caller decides buffering/closing) |
| Internal struct field that your own code sends/receives on | `chan T` (private) |
| Public struct field exposing events | `<-chan T` |
| Cancellation | `<-chan struct{}` or `context.Context` |
| You need to both read and write in one function | `chan T` (rare; reconsider design) |

### 14.2 Who creates the channel?

```
  Option A: callee creates and returns                Option B: caller creates and passes in
  ───────────────────────────────────                 ──────────────────────────────────────
  func Gen() <-chan T                                 func Gen(out chan<- T)

  + callee controls lifecycle, closes                 + caller controls buffer size
  + caller can't misuse                               + caller can merge multiple producers
  - callee picks buffer size                          - unclear who closes → DOCUMENT IT!
```

Prefer **A** for streams. Use **B** for "register me for notifications" APIs (like `signal.Notify`), and **document who closes**.

### 14.3 Document the contract

```go
// Subscribe returns a channel of events.
// The channel is closed when ctx is cancelled or the broker closes.
// The caller must not close it.
func (b *Broker) Subscribe(ctx context.Context) <-chan Event
```

### 14.4 Public API checklist

- Don't expose `chan T` publicly unless callers genuinely need both ends.
- Never return `chan<- T` for something the callee also *reads* from unless that's the point (it is: that's the "input channel" pattern — e.g., `func (w *Worker) Inbox() chan<- Msg`).
- If a channel can be closed by the library, say so.
- Prefer `context.Context` over a hand-rolled `done` parameter in new code.
- Consider returning a **method** (`Next()`, `Close()`) or iterator (`iter.Seq`) instead of a raw channel if lifecycle is complex.

### 14.5 Directional channels in interfaces

```go
type EventSource interface {
    Events() <-chan Event     // consumers can only receive
    Close() error
}
type EventSink interface {
    Input() chan<- Event      // producers can only send
}
```

### 14.6 Directional types as func values

```go
type Handler func(in <-chan Msg, out chan<- Reply)
```

---

## 15. Pitfalls, Anti-Patterns, and Debugging

### 15.1 Goroutine leaks

A goroutine blocked forever on a channel is never collected.

```go
// LEAK: nobody receives if caller returns early
func leaky() <-chan int {
    ch := make(chan int)
    go func() { ch <- compute() }()   // blocks forever if the reader never reads
    return ch
}

// FIX 1: buffered (1 slot) so the sender can always finish
ch := make(chan int, 1)

// FIX 2: select on ctx.Done()
select {
case ch <- v:
case <-ctx.Done():
}
```

```
   goroutine ──▶ ch <- v ──▶ (no receiver ever) ──▶ parked forever ──▶ memory + stack leak
```

### 15.2 Send on closed channel (panic)

```go
// BAD: multiple producers each call close(ch)
// GOOD: one closer using WaitGroup (see §9.3)
```

### 15.3 Closing from the receiver side

```go
func consume(in <-chan int) {
    close(in)    // COMPILE ERROR — direction saved you
}
```

### 15.4 Deadlock

```go
ch := make(chan int)
ch <- 1          // fatal error: all goroutines are asleep - deadlock!
fmt.Println(<-ch)
```

Unbuffered send with no receiver in another goroutine.

### 15.5 Forgetting `range` needs `close`

```go
for v := range ch { }   // never exits if producer never closes
```

### 15.6 Using a directional channel when you actually need both

```go
func f(c chan<- int) {
    v := <-c   // ERROR: cannot receive from send-only channel
}
```

Resist the urge to "fix" by widening to `chan int`; reconsider the design first.

### 15.7 Copying channel variables

Channels are **reference-like** (pointer to `hchan`). Copying a channel variable copies the pointer; both copies refer to the same channel.

```go
a := make(chan int)
b := a                // same underlying channel
close(b)              // a is closed too
```

### 15.8 Directions don't stop nil channels

```go
var out chan<- int    // nil
out <- 1              // blocks forever (no compile error)
```

### 15.9 Unbuffered "ack" patterns and reply channels

If a server replies to an unbuffered reply channel after the client gave up, the server blocks forever. Give reply channels capacity **1**.

### 15.10 Debugging tools

| Tool | Use |
|------|-----|
| `go vet` | flags some misuse |
| `go test -race` | catches data races around channel-adjacent state |
| `runtime.NumGoroutine()` | quick leak signal in tests |
| `pprof` goroutine profile (`/debug/pprof/goroutine?debug=2`) | shows where goroutines are parked (`chan send`, `chan receive`, `select`) |
| `go.uber.org/goleak` | assert no leaked goroutines in tests |
| `GODEBUG=schedtrace=1000` | scheduler traces |

Goroutine dump snippets:

```text
goroutine 18 [chan receive]:     ← parked on <-ch
goroutine 19 [chan send]:        ← parked on ch <- v
goroutine 20 [select]:           ← parked in select
goroutine 21 [chan receive (nil chan)]:   ← forgot to initialize channel
```

---

## 16. Performance and When NOT to Use Channels

### 16.1 Cost model (rough)

| Aspect | Notes |
|--------|-------|
| Direction narrowing | **Free** (compile-time only) |
| Uncontended buffered send/recv | roughly tens to ~100 ns (lock + copy) |
| Blocking op | goroutine park/unpark: higher (hundreds of ns to µs) |
| `select` with many cases | cost grows with number of cases (locks all channels in order) |
| Per-channel memory | `hchan` ≈ 96 bytes + buffer (`cap * elemsize`) |

(Numbers are indicative; always benchmark on your hardware and workload.)

### 16.2 Choosing the right tool

```
 Need to protect shared memory simply?                ──▶ sync.Mutex / RWMutex / atomic
 Need to transfer ownership of data between goroutines?──▶ channel
 Need to coordinate lifecycle / cancellation?          ──▶ context + channel
 Need to broadcast one event to many?                  ──▶ close(chan) / sync.Cond
 Need a worker pool with bounded parallelism?          ──▶ channels or errgroup.SetLimit
 Hot path, millions of ops/sec, single counter?        ──▶ atomic, NOT a channel
 Need an unbounded queue?                              ──▶ slice + mutex (+ cond); channels are bounded
```

Go proverb: *"Share memory by communicating"* — but **a mutex is fine** when you're just guarding a map or counter.

### 16.3 Buffer sizing

| Buffer size | Behavior |
|-------------|----------|
| 0 | strict handoff, synchronization point, latency-sensitive |
| 1 | decouples one in-flight item; good for reply/result channels |
| N (small, justified) | absorbs bursts; size by measured burst or worker count |
| huge | hides backpressure; delays failure; uses memory — usually a smell |

---

## 17. Same Idea in Other Languages

The "split a channel into producer and consumer ends" idea is universal. Go does it with *type-level directions on one value*; other languages use *two separate objects*.

| Language / Library | Producer end | Consumer end |
|--------------------|--------------|--------------|
| **Go** | `chan<- T` | `<-chan T` |
| **Rust** `std::sync::mpsc` | `Sender<T>` (cloneable) | `Receiver<T>` |
| **Rust** `tokio::sync::mpsc` | `Sender<T>` | `Receiver<T>` |
| **Kotlin** coroutines | `SendChannel<T>` | `ReceiveChannel<T>` |
| **Java** | `BlockingQueue` (no split; use interface discipline) | same |
| **Python** `asyncio.Queue` | no split (single object) | same |
| **C#** `System.Threading.Channels` | `ChannelWriter<T>` | `ChannelReader<T>` |
| **Occam / CSP** | output channel end | input channel end |

```
  Go:     one object, two TYPE VIEWS            Rust/C#/Kotlin: two OBJECTS, one shared queue
  ┌───────────────┐                             ┌──────────┐      ┌──────────┐
  │   hchan       │◀─ chan<- T / <-chan T       │ Sender   │─────▶│ Receiver │
  └───────────────┘                             └──────────┘      └──────────┘
```

Rust's model additionally gives **automatic closing**: the channel closes when *all* `Sender`s are dropped. Go requires an explicit `close`, which is why ownership discipline (§9) matters so much.

---

## 18. Decision Checklist and Cheat Sheet

### 18.1 Decision flow: which direction do I use?

```
                    Am I writing a function / field / return type
                    that mentions a channel?
                                  │
                                  ▼
                   Will this code SEND on it?
                      │                   │
                     yes                  no
                      │                   │
                      ▼                   ▼
            Will it also RECEIVE?    Will it RECEIVE?
              │            │            │          │
             yes           no          yes         no
              │            │            │          │
              ▼            ▼            ▼          ▼
           chan T      chan<- T      <-chan T   (why take the
         (reconsider!)                           channel at all?)

   Additional question:  Should this code be allowed to CLOSE?
       yes → needs chan T or chan<- T
       no  → <-chan T
```

### 18.2 Cheat sheet

```
 ┌────────────────────────────────────────────────────────────────────────────┐
 │  chan T      bidirectional          send ✔  recv ✔  close ✔                │
 │  chan<- T    SEND-ONLY              send ✔  recv ✘  close ✔                │
 │  <-chan T    RECEIVE-ONLY           send ✘  recv ✔  close ✘                │
 │                                                                            │
 │  Narrowing:  chan T ─▶ chan<- T / <-chan T    (implicit, free)             │
 │  Widening:   NEVER                                                         │
 │                                                                            │
 │  Reading rule:  arrow INTO chan (chan<-) = you send                        │
 │                 arrow OUT of chan (<-chan) = you receive                   │
 │                                                                            │
 │  Close rule:    only the sender / owner closes; closing is a broadcast     │
 │  nil channel:   blocks forever (send & recv), close panics                 │
 │  closed chan:   recv → buffered data then zero,false;  send/close → panic  │
 │                                                                            │
 │  Returns:       generators return <-chan T                                 │
 │  Params:        consumers take <-chan T, producers take chan<- T           │
 │  Cancellation:  <-chan struct{}  (ctx.Done())                              │
 └────────────────────────────────────────────────────────────────────────────┘
```

### 18.3 Mental model summary

1. **One channel, many views.** Directions are compile-time capability filters.
2. **The creator/owner holds `chan T`**; everyone else gets a narrowed view.
3. **Types document data flow.** Read a pipeline's signatures and you can redraw the architecture.
4. **Closing is the owner's job**, and direction makes illegal closes uncompilable.
5. **Closing cascades downstream** in pipelines; **cancellation (`ctx`) flows upstream/everywhere**.
6. **Bounded buffers = backpressure.** Size them deliberately.
7. **Every goroutine needs an exit path** (close, `ctx.Done()`, or buffered final send).

---

## 19. Practice Exercises

Try these without looking at the answers. They build the mental model.

1. **Compile or not?** For each, say whether it compiles.
   ```go
   a) func f(c <-chan int) { c <- 1 }
   b) func f(c chan<- int) { close(c) }
   c) func f(c <-chan int) { close(c) }
   d) func f(c chan<- int) { for range c {} }
   e) var c chan int = make(<-chan int)
   f) var r <-chan int = make(chan int)
   ```
   *Answers:* a ✘, b ✔, c ✘, d ✘, e ✘ (can't `make` a directional chan with a widening assignment; `make(<-chan int)` is legal but assigning to `chan int` is not), f ✔.

2. **Parse it.** What does `chan<- <-chan chan<- int` mean?
   *Answer:* a send-only channel of (receive-only channels of (send-only int channels)): `chan<- (<-chan (chan<- int))`.

3. **Build a pipeline** `gen → filterEven → square → sum` where every stage takes `<-chan int` and returns `<-chan int` (except `sum`), with `ctx` cancellation and no goroutine leaks. Verify with `goleak`.

4. **Design a Broker** that supports `Subscribe(ctx) <-chan T`, auto-unsubscribes when `ctx` is done, and never panics regardless of subscriber behavior. What policy do you choose for slow consumers, and why?

5. **Implement an actor** that owns a `map[string]int` with `Inc(key)` and `Get(key)` using request structs with `Reply chan<- ...`. Prove with `-race` that no mutex is needed.

6. **Break it on purpose.** Create (a) a goroutine leak, (b) a send-on-closed panic, (c) a deadlock. Then fix each using directions + ownership rules.

7. **Multi-producer close.** Write `Merge(ctx, ...<-chan T) <-chan T` and a test that cancels midway and asserts no leaked goroutines.

8. **Graceful shutdown.** Extend §13 so that on SIGTERM the service stops intake, drains all in-flight events, flushes the final batch, and exits within 10 seconds, or force-cancels.

---

### Final takeaway

> Channel directions are a **zero-cost, compile-time encoding of who may do what to a pipe**.
> Use them to make data flow, ownership, and lifecycle **visible in function signatures**.
> If you can redraw your system's architecture just by reading its channel types, you're using them correctly.
