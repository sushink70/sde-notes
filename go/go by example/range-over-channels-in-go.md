# Range Over Channels in Go

**A complete engineering guide: mental model, runtime internals, patterns, real-world usage, failure modes, performance, debugging, and the reverse-engineering view**

> **What this covers.** The statement `for v := range ch`: what the language promises, what the compiler turns it into, what the runtime does on every iteration (parking, waking, handoff, close), how real systems use it, how it fails, how to test and debug it, and what it looks like inside a compiled Go binary.
>
> **Verification note.** Every complete program in this guide (anything starting with `package main`) was compiled and run with **Go 1.25.14 (linux/amd64)**. Error messages, deadlock output, goroutine-dump wait reasons, disassembly, runtime struct offsets, and benchmark numbers quoted here were captured from real runs or read from the real runtime source of that toolchain. Runtime-internal details (struct layouts, function names) are version-sensitive; the text says so where it matters.

---

## Table of Contents

**Part I: Foundations**
1. [The contract in one page](#1-the-contract-in-one-page)
2. [What a channel really is (the `hchan` object)](#2-what-a-channel-really-is-the-hchan-object)
3. [Syntax and spec rules for `range` over a channel](#3-syntax-and-spec-rules-for-range-over-a-channel)
4. [Desugaring: what the compiler actually generates](#4-desugaring-what-the-compiler-actually-generates)

**Part II: Runtime mechanics**
5. [Life of one iteration: the `chanrecv` state machine](#5-life-of-one-iteration-the-chanrecv-state-machine)
6. [Termination: close, drain, and happens-before](#6-termination-close-drain-and-happens-before)
7. [Scheduler interplay: park, ready, and the deadlock detector](#7-scheduler-interplay-park-ready-and-the-deadlock-detector)

**Part III: Ownership and lifecycle**
8. [Who closes, who ranges: the ownership model](#8-who-closes-who-ranges-the-ownership-model)
9. [Directional channel types](#9-directional-channel-types)
10. [Leaving the loop early and the goroutine-leak problem](#10-leaving-the-loop-early-and-the-goroutine-leak-problem)

**Part IV: Patterns and real-world use cases**
11. [Generator](#11-pattern-generator)
12. [Pipeline](#12-pattern-pipeline)
13. [Fan-out: worker pool](#13-pattern-fan-out--worker-pool)
14. [Fan-in: merge](#14-pattern-fan-in--merge)
15. [Cancellation-aware range (context)](#15-pattern-cancellation-aware-range-context)
16. [Errors and results over channels](#16-pattern-errors-and-results-over-channels)
17. [Batching, and where `range` stops being enough](#17-pattern-batching-and-where-range-stops-being-enough)
18. [Dynamic work: the crawler problem](#18-pattern-dynamic-work-the-crawler-problem)
19. [Request/response loops (actor / serializer)](#19-pattern-requestresponse-loops-actor--serializer)
20. [Real-world catalog](#20-real-world-catalog)
21. [Security-research workloads: a parallel sample-triage pipeline](#21-security-research-workloads-a-parallel-sample-triage-pipeline)

**Part V: Pitfalls**
22. [Catalog of failure modes with fixes](#22-catalog-of-failure-modes-with-fixes)
23. [Channels that never close: tickers, timers, signals](#23-channels-that-never-close-tickers-timers-signals)
24. [Loop-variable semantics (Go 1.22 change)](#24-loop-variable-semantics-go-122-change)

**Part VI: Alternatives and evolution**
25. [`range ch` vs `for-select` vs manual receive](#25-range-ch-vs-for-select-vs-manual-receive)
26. [Range-over-func (Go 1.23), `iter.Seq`, and bridging channels](#26-range-over-func-go-123-iterseq-and-bridging-channels)
27. [When NOT to use a channel](#27-when-not-to-use-a-channel)

**Part VII: Performance**
28. [Cost model, measured numbers, buffer sizing](#28-cost-model-measured-numbers-buffer-sizing)

**Part VIII: Testing and debugging**
29. [Race detector, goroutine dumps, leak detection, `synctest`](#29-race-detector-goroutine-dumps-leak-detection-synctest)

**Part IX: Reverse-engineering view**
30. [What range-over-channel looks like in a compiled Go binary](#30-what-range-over-channel-looks-like-in-a-compiled-go-binary)

**Part X: Reference**
31. [Operation matrix (nil / open / closed)](#31-operation-matrix-nil--open--closed)
32. [Predict-the-output exercises](#32-predict-the-output-exercises)
33. [Checklists and the final mental model](#33-checklists-and-the-final-mental-model)

---

# Part I: Foundations

## 1. The contract in one page

The language specification says, for ranging over a channel:

> The iteration values produced are the successive values sent on the channel until the channel is closed. If the channel is `nil`, the range expression blocks forever.

Everything in this guide is a consequence of that sentence. Unpacked into rules you can rely on:

| # | Rule | Consequence |
|---|------|-------------|
| 1 | **Each iteration is exactly one receive.** | If no value is available, the goroutine **parks** (sleeps) until one arrives or the channel closes. |
| 2 | **The loop ends only when the channel is closed *and* empty.** | `close(ch)` does not discard buffered values; the loop drains them first. |
| 3 | **Nothing ends the loop except close** (or you leaving with `break`, `return`, `goto`, a labeled `break`, or a panic). | If no one closes the channel, the loop blocks forever: a **goroutine leak** or, if everything is blocked, a **deadlock**. |
| 4 | **Only one iteration variable exists: the element.** | No index. `for i, v := range ch` is a compile error. |
| 5 | **The range expression is evaluated once**, before the loop starts. | Reassigning the channel variable inside the loop does not change what is being ranged. |
| 6 | **A `nil` channel blocks forever.** | An uninitialised channel field turns your loop into a silent hang. |
| 7 | **You can only range over a channel you can receive from.** | `chan T` and `<-chan T` work; `chan<- T` is a compile error. |

### The mental model: a conveyor belt with a gate

```text
   PRODUCER SIDE                                                CONSUMER SIDE
   (owns the gate)                                              (for v := range ch)

   +-----------+      +----------------------------+  gate     +-------------------+
   | send(v)   | ---> |  belt: items in transit    | ========> | take next item    |
   | send(v)   |      |  (buffer / handoff point)  |  open     | do work with v    |
   | close()   |      +----------------------------+           | take next item ...|
   +-----------+                                               +-------------------+

   * Belt empty, gate OPEN    -> consumer waits (parks).
   * Belt has items           -> consumer takes them, whether the gate is open or closed.
   * Belt empty, gate CLOSED  -> consumer's loop ends. This is the ONLY normal exit.
```

If you keep one picture in your head, keep this one. A `range` loop is a consumer standing at the end of the belt, taking items until the producer closes the gate **and** the belt is empty.

---

## 2. What a channel really is (the `hchan` object)

To predict how `range` behaves in every edge case, you need to know what a channel is physically. Three facts explain nearly everything:

1. A channel value is **a single pointer** to a heap-allocated runtime struct called `hchan`.
2. That struct holds **a ring buffer** (possibly zero-length), **two FIFO queues of parked goroutines** (receivers and senders), a **closed flag**, and a **mutex**.
3. Sending, receiving, closing, blocking, and waking are all **manipulations of those fields** plus `park`/`ready` calls to the scheduler.

### 2.1 The struct (Go 1.25, amd64; offsets verified against the compiled runtime)

```text
 ch := make(chan int, 4)

   ch  (a local variable: ONE machine word, a pointer)
 +--------+
 |  ptr   |------------------------------+
 +--------+                              |
                                         v
 +------------------------- hchan (heap object, 0x70 = 112 bytes) ----------------------+
 | +0x00 qcount    uint            elements currently buffered              (here: 2)   |
 | +0x08 dataqsiz  uint            ring capacity, 0 means unbuffered        (here: 4)   |
 | +0x10 buf       unsafe.Pointer  pointer to the ring array of dataqsiz slots          |
 | +0x18 elemsize  uint16          size in bytes of one element             (here: 8)   |
 | +0x1c closed    uint32          0 = open, 1 = closed                     (here: 0)   |
 | +0x20 timer     *timer          non-nil only for time.Timer / Ticker channels        |
 | +0x28 elemtype  *_type          element type descriptor (GC, typedmemmove)           |
 | +0x30 sendx     uint            ring index where the next send writes    (here: 2)   |
 | +0x38 recvx     uint            ring index where the next receive reads  (here: 0)   |
 | +0x40 recvq     {first, last}   FIFO of parked RECEIVERS (sudog linked list)         |
 | +0x50 sendq     {first, last}   FIFO of parked SENDERS   (sudog linked list)         |
 | +0x60 bubble    *synctestBubble support for testing/synctest (Go 1.25)               |
 | +0x68 lock      mutex           protects every field above                           |
 +--------------------------------------------------------------------------------------+
```

> Offsets above were confirmed by disassembling `runtime.closechan` and `runtime.chanrecv` in a Go 1.25.14 binary (for example `CMPL 0x1c(CX), $0x0` is the `c.closed != 0` test, and `ADDQ $0x68, AX` computes `&c.lock`). Older releases have no `timer` or `bubble` field, so offsets shift. Always diff against `runtime/chan.go` for the exact Go version you are looking at.

### 2.2 The ring buffer

For `make(chan int, 4)` holding two values (`10`, `20`):

```text
 buf: ring array, dataqsiz = 4, elemsize = 8

        slot 0     slot 1     slot 2     slot 3
      +----------+----------+----------+----------+
      |    10    |    20    |  (empty) |  (empty) |
      +----------+----------+----------+----------+
            ^                     ^
          recvx                 sendx
          (next read)           (next write)

 qcount = 2.   After the next send:  sendx -> 3.   After the next receive:  recvx -> 1.
 Both indices wrap to 0 when they reach dataqsiz.  That wrap is why it is a RING.
```

Receive takes the element at `recvx`, **zeroes that slot** (so the GC does not see a stale pointer), advances `recvx`, and decrements `qcount`. Send writes at `sendx`, advances it, and increments `qcount`.

### 2.3 The three regimes

```text
 UNBUFFERED  make(chan T)                     BUFFERED  make(chan T, N)
 dataqsiz = 0, there is NO ring               dataqsiz = N, ring of N slots

   sender ---- copy ----> receiver              sender ---> [ ring buffer ] ---> receiver

   Rendezvous: a send completes only            Decoupled: a send completes immediately
   when a receiver takes the value,             while the ring has room; it parks only
   and vice versa. Both sides must be           when the ring is full. A receive parks
   present at the same instant.                 only when the ring is empty.
```

### 2.4 Other facts that follow from "it's a pointer to hchan"

| Fact | Why |
|------|-----|
| `var ch chan int` is `nil` (a nil pointer) | there is no `hchan` yet; `make` allocates it |
| `ch2 := ch` shares the same channel | copies the pointer, not the `hchan` |
| `ch1 == ch2` compares pointers | same `hchan` means equal |
| `len(ch)` is `qcount`, `cap(ch)` is `dataqsiz` | read straight out of the struct (racy for control flow: see section 22) |
| Elements are **copied** in and out | send does `typedmemmove` into the ring or directly into the receiver's stack slot; receive copies out. Sending a pointer copies the *pointer*, not the pointee |
| A channel with `cap` 0 and struct{} elements still has an `hchan` | ~96-112 bytes of heap, never free |
| Allocation strategy (`makechan`) | zero-size buffer: `hchan` only. Buffer without pointers: **one** allocation, `hchan` immediately followed by the ring. Buffer with pointers: `hchan` plus a **separate** GC-scannable ring |

### 2.5 `sudog`: the "parked goroutine" record

When a goroutine must wait, the runtime builds a `sudog` ("pseudo-g") on its behalf and links it into `recvq` or `sendq`:

```text
 hchan.recvq (parked receivers, FIFO)

   first --> +------------------+  next  +------------------+ <-- last
             | sudog            | -----> | sudog            |
             |  g    = G7       | <----- |  g    = G12      |
             |  elem = &v (G7's |  prev  |  elem = &v (G12's|
             |         stack)   |        |         stack)   |
             |  success = ?     |        |  success = ?     |
             |  isSelect= false |        |  isSelect= false |
             +------------------+        +------------------+

   elem    : where the value must be delivered (a pointer INTO the parked goroutine's stack frame)
   success : set by the waker. true  = "a value was delivered"
                               false = "woken because the channel closed"
```

`success` is how a goroutine woken inside `chanrecv` learns whether it got a value or a close. That is exactly the `ok` that `range` tests on every iteration.

---

## 3. Syntax and spec rules for `range` over a channel

### 3.1 The legal forms

| Form | Meaning |
|------|---------|
| `for v := range ch { ... }` | declare `v` (a **new variable per iteration** since Go 1.22) |
| `for v = range ch { ... }` | assign into an **existing** variable `v` |
| `for range ch { ... }` | receive and **discard** the value (Go 1.4+). Use for `chan struct{}` and "wait until closed" |
| `for _ = range ch { ... }` | legal, but `gofmt -s` rewrites it to `for range ch` |
| `for i, v := range ch { ... }` | **compile error**: channels have no index |

Verified compiler errors (Go 1.25):

```text
$ go run ./a        # for i, v := range ch
a/main.go:4:9: range over ch (variable of type chan int) permits only one iteration variable

$ go run ./b        # ch := make(chan<- int); for v := range ch
b/main.go:4:17: cannot range over ch (variable of type chan<- int): receive from send-only channel chan<- int
```

### 3.2 What you can range over

| Range expression type | Allowed? | Notes |
|-----------------------|----------|-------|
| `chan T` | Yes | bidirectional |
| `<-chan T` | Yes | receive-only (the idiomatic parameter/return type for consumers) |
| `chan<- T` | **No** | send-only; compile error |
| `nil` channel value | Compiles, **blocks forever** at runtime | wait reason `chan receive (nil chan)` |
| A type parameter whose core type is a channel | Yes | `func Drain[C ~chan T, T any](c C)` |
| Closed channel | Yes | drains remaining buffered values, then exits immediately |

### 3.3 The range expression is evaluated exactly once

```go
package main

import "fmt"

func getChan(name string, ch chan int) chan int {
	fmt.Println("getChan called for", name)
	return ch
}

func main() {
	a := make(chan int, 2)
	b := make(chan int, 2)
	a <- 1
	a <- 2
	close(a)
	b <- 100
	close(b)

	ch := a
	for v := range getChan("a", ch) { // evaluated ONCE here
		fmt.Println("v =", v)
		ch = b // reassigning the variable does not redirect the loop
	}
	fmt.Println("b still holds", len(b), "item(s): the loop never touched it")
}
```

Output:

```text
getChan called for a
v = 1
v = 2
b still holds 1 item(s): the loop never touched it
```

The compiler copies the channel into a hidden temporary before the loop starts. Reassigning `ch` later affects the variable, not the temporary.

### 3.4 `break`, `continue`, labels, `goto`

* `continue` jumps to the **next receive**. It does not skip or consume anything extra.
* `break` leaves the loop. The channel is **not** closed and **not** drained (see section 10).
* A labeled `break Outer` / `continue Outer` works exactly as for other `for` loops.
* `return`, `goto` out of the loop, and `panic` also leave without closing anything.

---

## 4. Desugaring: what the compiler actually generates

### 4.1 The semantic equivalent

```go
for v := range ch {
	body
}
```

is, to the letter, this loop:

```go
{
	ha := ch                     // hidden copy: evaluated once
	for {
		hv, ok := <-ha           // ONE blocking receive
		if !ok {                 // closed AND empty
			break
		}
		v := hv                  // per-iteration variable (Go 1.22+)
		body
	}
}
```

`for range ch { body }` is identical but never copies `hv` into a variable.

### 4.2 What the compiler really emits

The front end rewrites the loop in `cmd/compile/internal/walk/range.go` into an `OAS2RECV` node (`hv, ok = <-ha`). Later passes lower `OAS2RECV` to a call to the runtime function:

```go
// runtime/chan.go
func chanrecv2(c *hchan, elem unsafe.Pointer) (received bool) {
	_, received = chanrecv(c, elem, true) // block = true
	return
}
```

So the whole loop becomes a **call to `runtime.chanrecv2` in a loop**, with the result tested against zero. This is not a `select`: a `select` with a single receive case is compiled into the same fast path as a plain receive, but a `range` is *always* the direct form. The full disassembly is in [section 30](#30-what-range-over-channel-looks-like-in-a-compiled-go-binary).

### 4.3 Why this matters to your mental model

```text
 range ch                    v, ok := <-ch               select { case v, ok := <-ch: }
     |                            |                               |
     +-------- runtime.chanrecv2 -+                               +--- runtime.selectgo
                    |                                               (heavier: locks all cases,
                    v                                                randomises order, etc.)
          chanrecv(c, elem, block=true)
```

* `range` and `v, ok := <-ch` share the **same** runtime path. There is no extra cost for choosing `range`.
* `range` has **one wait source**. The moment you need to also react to a timeout, a cancellation, or a second channel, `range` is no longer enough and you must use `for { select { ... } }` (section 25).
* The `ok` result is the *only* signal the loop uses to stop. It is `false` precisely when the channel is closed and `qcount == 0`.

---

