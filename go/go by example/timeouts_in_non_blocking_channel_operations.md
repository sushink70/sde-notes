# Timeouts in Non-Blocking Channel Operations
### A Complete, In-Depth Guide: Concepts, Internals, Patterns, Real-World Architectures

> **Scope note.** The phrase "non-blocking channel operations with timeouts" is most
> naturally a **Go** topic (channels + `select` + `default` + `time.After` + `context`).
> This guide therefore uses Go as the primary language, and **Part 13** maps every idea to
> other ecosystems (Java NIO / `BlockingQueue`, Rust/Tokio, Kotlin Coroutines, Python asyncio).
> The mental models are language-independent.

---

## Table of Contents

1. [The Big Idea: Why Every Wait Must Be Bounded](#part-1)
2. [Channel Refresher (the Rules You Must Know Cold)](#part-2)
3. [The Waiting Spectrum: Blocking, Non-Blocking, Timed, Cancellable](#part-3)
4. [Non-Blocking Operations (`select` + `default`)](#part-4)
5. [Timed Operations (Timeouts) - All the Tools](#part-5)
6. [Timers Deep Dive (Stop, Reset, Go 1.23 Changes, Leaks)](#part-6)
7. [How `select` Works Internally](#part-7)
8. [Timeout Is Not Cancellation (the Goroutine Leak Problem)](#part-8)
9. [Core Patterns (with Code)](#part-9)
10. [Where This Is Used: Real-World Use Cases](#part-10)
11. [Real Architectures (ASCII Diagrams)](#part-11)
12. [Choosing Timeout Values (Budgets, Percentiles, Jitter)](#part-12)
13. [Other Languages and Runtimes](#part-13)
14. [Testing Timeout Code](#part-14)
15. [Observability: What to Measure](#part-15)
16. [Pitfalls and Anti-Patterns Checklist](#part-16)
17. [Cheat Sheet and Decision Tree](#part-17)
18. [Exercises and Interview Questions](#part-18)

---

<a id="part-1"></a>
## Part 1 - The Big Idea: Why Every Wait Must Be Bounded

### 1.1 The three fundamental truths

1. **A remote (or concurrent) peer can be slow, dead, or partitioned, and from your side these look identical.**
   You send a request and hear nothing. Is the peer slow? Crashed? Is the network cut? You cannot
   know. The only thing you control is *how long you are willing to wait*. That bound is a **timeout**.

2. **Resources are finite.** Every blocked waiter holds something: a goroutine (~2-8 KB stack and up),
   a thread (~1 MB stack), a socket, a DB connection, memory for buffered requests. Unbounded waiting
   converts a *slow dependency* into a *resource-exhaustion outage* in **your** service.

3. **Latency is a feature.** Users and upstream callers have their own deadlines. A response that arrives
   after the caller gave up is wasted work that still costs you CPU, memory and money.

### 1.2 How a missing timeout becomes an outage (the cascade)

```text
+----------------------------------------------------+
| 1. Dependency D becomes slow (not down, just slow) |
+----------------------------------------------------+
        |
        v
+-------------------------------------------------------------+
| 2. Calls to D have NO timeout -> goroutines/threads pile up |
+-------------------------------------------------------------+
        |
        v
+---------------------------------------------------------+
| 3. Memory grows, connection pool exhausted, queues fill |
+---------------------------------------------------------+
        |
        v
+----------------------------------------------------------------+
| 4. YOUR service becomes slow/unresponsive (health checks fail) |
+----------------------------------------------------------------+
        |
        v
+--------------------------------------------------------------+
| 5. Callers of YOUR service pile up -> the failure propagates |
|    upward through the whole call graph (cascading failure)   |
+--------------------------------------------------------------+
```

A timeout is the **circuit that breaks this chain** at the earliest possible point.

### 1.3 Why "non-blocking" and "timeout" appear together

| Concept | Question it answers |
|---|---|
| **Non-blocking** | "Can I make progress *right now*, yes or no? Don't make me wait at all." |
| **Timeout** | "Can I make progress *within T*? If not, give up and let me handle it." |
| **Cancellation** | "Someone else decided the result is no longer needed. Stop." |
| **Deadline** | "The absolute point in time after which the result is worthless." |

They are points on one spectrum (Part 3). Mature systems use all four.

---

<a id="part-2"></a>
## Part 2 - Channel Refresher (the Rules You Must Know Cold)

A **channel** is a typed, goroutine-safe conduit for passing values (and, more importantly,
for **synchronizing** goroutines).

```go
ch := make(chan int)      // unbuffered: send and receive must rendezvous
bc := make(chan int, 3)   // buffered: capacity 3, send blocks only when full
```

### 2.1 Unbuffered vs. buffered

| | Unbuffered `make(chan T)` | Buffered `make(chan T, N)` |
|---|---|---|
| Send blocks when | no receiver is ready | buffer holds N items |
| Receive blocks when | no sender is ready | buffer is empty |
| Semantics | synchronous handoff (rendezvous) | bounded queue (decoupling) |
| `len(ch)` / `cap(ch)` | 0 / 0 | items / N |

### 2.2 The behavior matrix (memorize this)

| Operation | `nil` channel | **closed** channel | open, can proceed now | open, cannot proceed now |
|---|---|---|---|---|
| `ch <- v` (send) | blocks **forever** | **panics** | proceeds | **blocks** |
| `<-ch` (receive) | blocks **forever** | returns buffered items, then zero value + `ok=false` **immediately** | proceeds | **blocks** |
| `close(ch)` | **panics** | **panics** | ok | ok |

Two consequences used constantly with timeouts:

- **A closed channel is "always ready" for receive.** That is why `<-ctx.Done()` and
  `<-done` (closed to broadcast) work as universal "stop" signals.
- **A nil channel is "never ready".** That lets you *disable* a `select` case dynamically (see 9.15).

### 2.3 Who should close?

Only the **sender** (the owner of the producing side) closes a channel, and only once.
Receivers never close. Closing is a *broadcast*: all current and future receivers observe it.

### 2.4 `select`: waiting on many channel operations

```go
select {
case v := <-a:        // receive from a
case b <- x:          // send to b
case <-time.After(d): // a timer channel is just another channel
default:              // optional: makes the whole select NON-BLOCKING
}
```

Rules:

1. All channel expressions and send values are evaluated **once**, on entry.
2. If **one or more** cases can proceed, the runtime picks one **uniformly at random** (no priority, no fairness guarantee by source order).
3. If none can proceed and there is a `default`, run `default` (non-blocking).
4. If none can proceed and there is no `default`, the goroutine **blocks** until one case can proceed.
5. `select {}` (no cases) blocks forever.

> **Key insight:** a timeout in Go is *not a special feature*. It is simply **another channel
> in the `select` that becomes ready after a duration**. Everything in this guide follows from that.

---

<a id="part-3"></a>
## Part 3 - The Waiting Spectrum

```text
 Blocking              Timed (bounded wait)                 Non-blocking
 ----------------      --------------------------------     ----------------------
 ch <- v               select {                             select {
                       case ch <- v:                        case ch <- v:
 waits 0 .. infinity   case <-timer.C:                      default:
                       }                                    }

 max wait = INFINITY   max wait = T                         max wait = 0
 "I need this."        "I need this, but not at any cost."  "Only if it is free."
```

| Mode | Max wait | Typical meaning | Failure signal |
|---|---|---|---|
| Blocking | infinite | internal pipeline stages where the peer is trusted and bounded | none (deadlock risk) |
| Non-blocking | 0 | "try", opportunistic, drop-on-full, polling | `false` / `default` branch |
| Timed | `T` | RPCs, queue admission, lock/pool acquisition | timeout branch / `ErrTimeout` |
| Context-cancellable | until `ctx.Done()` | request-scoped work with deadline + manual cancel | `ctx.Err()` |

**Important vocabulary trap.** In the title, "non-blocking" does not mean "never waits for anything".
It means *the calling goroutine retains control of how long it waits*. A timed `select` is a
**bounded-blocking** operation; a `select` with `default` is a **zero-wait** operation. Both
are "non-blocking" in the sense of *not blocking indefinitely*.

---

<a id="part-4"></a>
## Part 4 - Non-Blocking Operations (`select` + `default`)

### 4.1 Try-send and try-receive

```go
// TrySend attempts a send without ever waiting.
func TrySend[T any](ch chan<- T, v T) bool {
    select {
    case ch <- v:
        return true
    default:
        return false // channel full or no receiver ready
    }
}

// TryRecv attempts a receive without ever waiting.
func TryRecv[T any](ch <-chan T) (v T, ok bool) {
    select {
    case v = <-ch:
        return v, true
    default:
        return v, false // nothing available right now
    }
}
```

> Careful with closed channels: `TryRecv` on a closed channel returns `(zero, true)` because the
> receive case *can proceed*. Use the two-value form `v, ok := <-ch` inside the case if you must
> distinguish "closed" from "a real zero value".

### 4.2 What `default` really means

`default` runs **only if no other case is ready at the instant of evaluation**. It is a *snapshot*, not
a promise. Between the check and your next line, another goroutine may make the channel ready.
This is inherent in all try-operations (compare `Mutex.TryLock`).

### 4.3 Typical uses of zero-wait operations

| Use | Idea |
|---|---|
| **Drop-on-full (load shedding)** | If the queue is full, drop the item instead of stalling the producer. |
| **Notification coalescing** | `chan struct{}` with capacity 1; many "poke" events collapse into one wakeup. |
| **Polling / opportunistic work** | Check for a cancel signal between units of CPU-bound work. |
| **Priority select** | Check a high-priority channel first, non-blockingly, then fall through. |
| **Best-effort telemetry/logging** | Never let metrics slow down the hot path. |
| **Slow-consumer isolation** | A publisher must never be held hostage by one slow subscriber. |

### 4.4 The busy-loop anti-pattern

```go
// BAD: burns 100% of a CPU core while idle.
for {
    select {
    case v := <-ch:
        handle(v)
    default:
        // nothing - spins
    }
}
```

Fixes, in order of preference:

1. **Remove `default`** so the goroutine parks and costs nothing while idle.
2. If you must do background work when idle, do it *inside* `default` and keep each unit small.
3. If you must poll, add a `time.Ticker` case (a bounded sleep), not a spin.

### 4.5 Priority select (non-blocking check first)

Because `select` picks randomly among ready cases, **to give `quit` priority** over `work`:

```go
for {
    // 1) Non-blocking: is there a stop request? (priority check)
    select {
    case <-ctx.Done():
        return ctx.Err()
    default:
    }

    // 2) Blocking wait for work OR stop
    select {
    case <-ctx.Done():
        return ctx.Err()
    case job := <-jobs:
        process(job)
    }
}
```

---

<a id="part-5"></a>
## Part 5 - Timed Operations: All the Tools

### 5.1 Tool 1 - `time.After(d)` (simplest)

```go
select {
case res := <-resultCh:
    use(res)
case <-time.After(2 * time.Second):
    return ErrTimeout
}
```

`time.After(d)` is shorthand for `time.NewTimer(d).C`. Perfect for **one-shot** waits. In **loops**
or hot paths prefer `time.NewTimer` (see 6.4) because each call allocates a new timer.

### 5.2 Tool 2 - `time.NewTimer(d)` (controllable)

```go
t := time.NewTimer(2 * time.Second)
defer t.Stop() // release the timer as soon as we leave

select {
case res := <-resultCh:
    use(res)
case <-t.C:
    return ErrTimeout
}
```

Why better: you can `Stop()` it when you don't need it anymore and `Reset()` it for reuse.

### 5.3 Tool 3 - `context.WithTimeout` / `context.WithDeadline` (the production standard)

```go
ctx, cancel := context.WithTimeout(parent, 2*time.Second)
defer cancel() // ALWAYS: releases the internal timer and child links

select {
case res := <-resultCh:
    use(res)
case <-ctx.Done():
    return ctx.Err() // context.DeadlineExceeded or context.Canceled
}
```

Why it is the standard:

- **Composable:** a child context can never outlive its parent; the *earlier* deadline wins.
- **Propagates:** pass `ctx` through every function and into network/DB calls
  (`http.NewRequestWithContext`, `db.QueryContext`, gRPC calls use it automatically).
- **Distinguishes** *why* it ended: `errors.Is(err, context.DeadlineExceeded)` vs `context.Canceled`.
- **Carries cancellation** (a timeout alone does not stop work; see Part 8).

### 5.4 Tool 4 - `time.Ticker` (repeating)

```go
tk := time.NewTicker(1 * time.Second)
defer tk.Stop()
for {
    select {
    case <-tk.C:
        sendHeartbeat()
    case <-ctx.Done():
        return
    }
}
```

A ticker drops ticks for slow receivers (its channel holds at most one pending tick). It does
**not** queue up a backlog.

### 5.5 Tool 5 - `time.AfterFunc(d, f)` (callback, no channel)

```go
t := time.AfterFunc(5*time.Second, func() { forceClose(conn) })
// ...happy path finishes early:
t.Stop()
```

`f` runs in its own goroutine. Useful to *enforce* a deadline on something that cannot select
(e.g., closing a connection to unblock a blocked `Read`).

### 5.6 Tool 6 - I/O deadlines (`SetDeadline`)

Network connections are not channels, yet the same idea applies:

```go
conn.SetReadDeadline(time.Now().Add(10 * time.Second))
n, err := conn.Read(buf)
var ne net.Error
if errors.As(err, &ne) && ne.Timeout() { /* read timed out */ }
```

Note: deadlines are **absolute times**, not durations. You must reset them before each I/O if you want a *per-operation* timeout.

### 5.7 Which tool when?

| Need | Use |
|---|---|
| One-shot wait, simple code | `time.After` |
| Wait inside a loop / reuse / explicit stop | `time.NewTimer` + `Stop`/`Reset` |
| Request-scoped, propagated across calls | `context.WithTimeout` / `WithDeadline` |
| Periodic action | `time.Ticker` |
| Enforce a deadline on a non-selectable blocking call | `time.AfterFunc` or `SetDeadline` |
| Absolute wall-clock cutoff (e.g., "by 12:00:03.250") | `context.WithDeadline` |

### 5.8 Timeout vs. deadline

- **Timeout** = a *duration* ("wait up to 2 s"). Resets with each new operation.
- **Deadline** = an *instant* ("must be finished by T"). Shared across a whole call chain.

```text
 Timeout per step (each step gets a fresh 2 s):  A[2s] -> B[2s] -> C[2s]   total up to 6 s
 Deadline for the chain (budget shared):         A -> B -> C  all must finish before T0+2s
```

In multi-hop systems **always prefer deadlines (contexts)** so downstream work never exceeds the
caller's remaining budget.

### 5.9 Edge semantics worth knowing

- A **zero or negative** duration fires immediately. `time.After(0)` is **not** equivalent to `default`:
  if another case is also ready, `select` may randomly pick either.
- Go's timers use the **monotonic clock** for durations; wall-clock adjustments (NTP, DST) do not
  make timeouts fire early or late.
- Timer precision is "roughly at least d". Never rely on microsecond accuracy; the scheduler decides when your goroutine actually runs.
- `ctx.Err()` is `nil` until done; after done it is stable.

---

<a id="part-6"></a>
## Part 6 - Timers Deep Dive

### 6.1 What a timer is, physically

A Go timer is an entry in a **per-P (per scheduler processor) min-heap ordered by fire time**.
The scheduler checks the heap's earliest entry when it looks for work. On expiry the runtime
sends the current time on the timer's channel (or calls `f` for `AfterFunc`).

```text
+----------------------------------------------------------+
| P (scheduler processor)                                  |
|                                                          |
|   timer min-heap (ordered by "when")                     |
|                                                          |
|             [ t=+50ms  ]   <-- root: next to fire        |
|             /          \                                 |
|     [ t=+200ms ]    [ t=+2s ]                            |
|         /                                                |
|     [ t=+5s ]                                            |
|                                                          |
|   findRunnable(): run timers whose "when" <= now         |
|        |                                                 |
|        +--> send time on t.C  --> wakes parked goroutine |
+----------------------------------------------------------+
```

Cost model: add/remove is O(log n). Millions of live timers are feasible but not free; this is
why creating a timer per loop iteration in a hot path (`time.After` in a `for`) matters.

### 6.2 `Stop()` and `Reset()` semantics

| Method | Returns | Meaning |
|---|---|---|
| `t.Stop()` | `bool` | `true` if the call stopped the timer before it fired; `false` if it had already fired/stopped |
| `t.Reset(d)` | `bool` | `true` if the timer was active; `false` if it had expired/stopped. Re-arms with new `d` |

### 6.3 The historic trap (Go < 1.23) and the modern fix

**Before Go 1.23**, the timer channel was a *buffered channel of capacity 1*. If the timer fired
but nobody had received yet, the stale value stayed in the channel. A subsequent `Reset` could then
cause an immediate, bogus "timeout" from the **old** tick. The required idiom was:

```go
// Go < 1.23 idiom to safely reuse a timer
if !t.Stop() {
    select {
    case <-t.C: // drain a possible stale tick
    default:
    }
}
t.Reset(d)
```

**Since Go 1.23** (when your `go.mod` declares `go 1.23` or newer):

- Timer/Ticker channels are **synchronous** (`cap` and `len` report 0). No stale values can exist.
- After `Stop` or `Reset` returns, **no stale value will be received** from the channel.
- Timers that are no longer referenced become **eligible for garbage collection immediately**,
  even if `Stop` was never called. (Previously an un-stopped, un-fired timer stayed alive until it fired.)

So on modern Go, `t.Reset(d)` is simply safe. Still keep `defer t.Stop()` as good hygiene
and for older-toolchain compatibility.

> The new behavior is gated by the module's `go` directive (and `GODEBUG=asynctimerchan=1` restores the old behavior).
> A library compiled in a module declaring an older Go version keeps the old semantics.

### 6.4 `time.After` in loops

```go
// Pre-1.23: each iteration allocated a timer that lived until it fired,
// even if the other case won. Under high message rates this ballooned memory.
for {
    select {
    case m := <-in:
        handle(m)
    case <-time.After(30 * time.Second): // new timer per iteration
        return ErrIdle
    }
}
```

Go 1.23+ makes this much less dangerous (unreferenced timers are collectable), but the allocation
and heap churn still exist. **Best practice:** hoist one timer out of the loop and `Reset` it.

```go
idle := time.NewTimer(30 * time.Second)
defer idle.Stop()
for {
    select {
    case m := <-in:
        handle(m)
        idle.Reset(30 * time.Second) // slide the idle window
    case <-idle.C:
        return ErrIdle
    case <-ctx.Done():
        return ctx.Err()
    }
}
```

### 6.5 Timer state machine

```text
+----------------------------------------------+
| IDLE   : not in the timer heap               |
+----------------------------------------------+
                        |
                        |  NewTimer(d) / Reset(d)
                        v
+----------------------------------------------+
| ACTIVE : sitting in the timer heap           |
+----------------------------------------------+
                        |
                        |  when <= now  (expiry)
                        v
+----------------------------------------------+
| FIRED  : value sent on t.C (or f runs)       |
+----------------------------------------------+

Stop()  on ACTIVE -> back to IDLE (returns true)
Reset(d) on any state -> ACTIVE again (re-armed)
```

---

<a id="part-7"></a>
## Part 7 - How `select` Works Internally

Understanding this explains *why* timeouts compose so cleanly and what they cost.

### 7.1 The channel struct (`hchan`, simplified)

```text
+---------------------------------------------------+
| hchan                                             |
|---------------------------------------------------|
| qcount   = 2   (items currently buffered)         |
| dataqsiz = 4   (capacity)                         |
| buf      = [ A ][ B ][   ][   ]  (ring buffer)    |
| sendx    = 2   recvx = 0                          |
| closed   = 0                                      |
| recvq    -> G7 <-> G9   (goroutines blocked recv) |
| sendq    -> G3          (goroutines blocked send) |
| lock     = mutex protecting all fields            |
+---------------------------------------------------+
```

- `recvq` / `sendq` are linked lists of **`sudog`** records: "goroutine G is waiting here, wanting to send/receive value V".
- A send first checks `recvq`: if a receiver is parked, the value is copied **directly** to it (bypassing the buffer) and the receiver is made runnable.

### 7.2 What `select` does step by step

```text
+------------------------------------------------------------------+
| select { case <-a: ; case b <- x: ; case <-timer.C: ; default }  |
+------------------------------------------------------------------+
                                  |
                                  v
+------------------------------------------------------------------+
| STEP 1: evaluate channel exprs + send values (once)              |
+------------------------------------------------------------------+
                                  |
                                  v
+------------------------------------------------------------------+
| STEP 2: random poll order + lock order (by address)              |
+------------------------------------------------------------------+
                                  |
                                  v
+------------------------------------------------------------------+          +------------------------------+
| STEP 3: lock all channels; any case ready now?                   |-yes----->| Execute that case, unlock,   |
+------------------------------------------------------------------+          | return (random winner)       |
                                  |                                           +------------------------------+
                                  |  no
                                  v
+------------------------------------------------------------------+          +------------------------------+
| Is there a default case?                                         |-yes----->| run default                  |
+------------------------------------------------------------------+          | (NON-BLOCKING path)          |
                                  |                                           +------------------------------+
                                  |  no
                                  v
+------------------------------------------------------------------+
| STEP 4: enqueue a sudog on EVERY channel queue,                  |
|         unlock, PARK the goroutine                               |
+------------------------------------------------------------------+
                                  |
                                  v
+------------------------------------------------------------------+
| STEP 5: first channel to become ready wakes G                    |
| (data, close, or TIMER FIRING)                                   |
+------------------------------------------------------------------+
                                  |
                                  v
+------------------------------------------------------------------+
| STEP 6: relock, dequeue sudogs from the LOSING                   |
| channels, run the winning case                                   |
+------------------------------------------------------------------+
```

### 7.3 Consequences you can *use*

| Fact | Practical consequence |
|---|---|
| The timer channel is enqueued like any other | A timeout costs no extra goroutine; the goroutine is simply parked once. |
| Winner chosen randomly among ready cases | If data and timeout are *both* ready, either can win. Add a priority pre-check if it matters. |
| Locks are taken on all channels | `select` with many cases is O(cases); keep case counts reasonable on hot paths. |
| Losing sudogs are cleaned up on wake | A timeout losing the race does not leak by itself (the timer is freed when it fires, or GC'd on 1.23+). |
| Evaluated once on entry | `case ch <- compute():` computes **even if that case loses**. Don't put side effects there. |

---

<a id="part-8"></a>
## Part 8 - Timeout Is Not Cancellation (the Goroutine Leak Problem)

This is the **most important and most commonly misunderstood** idea in the whole topic.

> A timeout only stops **you from waiting**. It does **not** stop the work being waited on.

### 8.1 The classic leak

```go
// BUG: leaks a goroutine every time the timeout wins.
func fetch() (string, error) {
    ch := make(chan string) // UNBUFFERED
    go func() {
        ch <- slowCall() // blocks forever if nobody receives
    }()

    select {
    case r := <-ch:
        return r, nil
    case <-time.After(1 * time.Second):
        return "", ErrTimeout // we leave; the goroutine is stuck on `ch <-` forever
    }
}
```

```text
+----------------------------------------------------+
| t=0     caller starts goroutine G, waits in select |
+----------------------------------------------------+
        |
        v
+----------------------------------------------------------------+
| t=1s    timeout wins; caller RETURNS; nobody will ever receive |
+----------------------------------------------------------------+
        |
        v
+---------------------------------------------------------------+
| t=3s    G finishes slowCall(), executes `ch <- result`        |
|         unbuffered + no receiver  =>  G blocks FOREVER (leak) |
+---------------------------------------------------------------+
```

### 8.2 Fix #1 - buffer of 1 (the sender never blocks)

```go
ch := make(chan string, 1) // the late result is parked in the buffer, G exits, GC reclaims all
```

### 8.3 Fix #2 - make the work itself cancellable (best)

```go
func fetch(ctx context.Context) (string, error) {
    ctx, cancel := context.WithTimeout(ctx, time.Second)
    defer cancel()

    type result struct {
        v   string
        err error
    }
    ch := make(chan result, 1) // buffer 1: sender can always finish

    go func() {
        v, err := slowCall(ctx) // slowCall MUST honor ctx (e.g., pass to http/db)
        ch <- result{v, err}
    }()

    select {
    case r := <-ch:
        return r.v, r.err
    case <-ctx.Done():
        return "", ctx.Err()
    }
}
```

Now when the deadline hits, `slowCall` also sees `ctx.Done()` and **stops consuming resources**.

### 8.4 Fix #3 - sender selects on a `done` channel

```go
go func() {
    v := slowCall()
    select {
    case ch <- v:
    case <-ctx.Done(): // nobody is listening anymore; just exit
    }
}()
```

### 8.5 What a timeout does *not* give you

| Misconception | Reality |
|---|---|
| "After timeout, the operation is rolled back." | No. It may still **complete** on the other side (e.g., the payment was charged). Design for **idempotency** (idempotency keys). |
| "Timeout means the peer failed." | No. It means *you stopped waiting*. State is **unknown**. |
| "Timeout frees the goroutine doing the work." | Only if the work honors the context or the sender can't block. |

This "unknown outcome" property is why **retries need idempotent operations**.

---

<a id="part-9"></a>
## Part 9 - Core Patterns (with Code)

### 9.1 Receive with timeout

```go
var ErrTimeout = errors.New("timeout")

func RecvTimeout[T any](ch <-chan T, d time.Duration) (T, error) {
    t := time.NewTimer(d)
    defer t.Stop()
    select {
    case v, ok := <-ch:
        if !ok {
            var zero T
            return zero, io.EOF // channel closed
        }
        return v, nil
    case <-t.C:
        var zero T
        return zero, ErrTimeout
    }
}
```

### 9.2 Send with timeout (backpressure with a bounded wait)

```go
func SendTimeout[T any](ch chan<- T, v T, d time.Duration) error {
    t := time.NewTimer(d)
    defer t.Stop()
    select {
    case ch <- v:
        return nil
    case <-t.C:
        return ErrTimeout // consumer too slow: caller decides (drop, 503, retry later)
    }
}
```

Three producer policies when a queue is full, from gentlest to harshest:

| Policy | Code shape | When |
|---|---|---|
| **Block** | `ch <- v` | internal, trusted, bounded pipeline |
| **Wait up to T** | `SendTimeout` | admission control with fairness to bursts |
| **Drop now** | `TrySend` | telemetry, logs, "latest wins" streams |

### 9.3 Context-aware send/receive (preferred in services)

```go
func Send[T any](ctx context.Context, ch chan<- T, v T) error {
    select {
    case ch <- v:
        return nil
    case <-ctx.Done():
        return ctx.Err()
    }
}

func Recv[T any](ctx context.Context, ch <-chan T) (T, error) {
    select {
    case v, ok := <-ch:
        if !ok {
            var zero T
            return zero, io.EOF
        }
        return v, nil
    case <-ctx.Done():
        var zero T
        return zero, ctx.Err()
    }
}
```

### 9.4 Per-operation timeout inside an overall deadline

```go
func Process(ctx context.Context, items []Item) error {
    // Overall budget for the whole batch
    ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
    defer cancel()

    for _, it := range items {
        // Each item gets at most 2s, but never beyond the overall budget
        ictx, icancel := context.WithTimeout(ctx, 2*time.Second)
        err := handleItem(ictx, it)
        icancel() // cancel per iteration, NOT defer in the loop
        if err != nil {
            return err
        }
    }
    return nil
}
```

Child timeout = `min(2s, remaining parent budget)` automatically.

### 9.5 Idle timeout (sliding window)

Close a connection/session if **no activity** for `T`. See 6.4 for the reusable-timer loop.
Real uses: HTTP keep-alive idle timeout, WebSocket sessions, SSH sessions, DB idle connections.

### 9.6 Heartbeat / watchdog

```go
// Worker proves liveness by pulsing; supervisor restarts it if pulses stop.
func supervise(ctx context.Context, pulse <-chan struct{}, restart func()) {
    const maxSilence = 3 * time.Second
    wd := time.NewTimer(maxSilence)
    defer wd.Stop()
    for {
        select {
        case <-pulse:
            wd.Reset(maxSilence)
        case <-wd.C:
            restart()
            wd.Reset(maxSilence)
        case <-ctx.Done():
            return
        }
    }
}
```

### 9.7 Retry with exponential backoff + full jitter (context-aware sleep)

```go
func Retry(ctx context.Context, attempts int, base time.Duration,
    fn func(context.Context) error) error {

    var err error
    for i := 0; i < attempts; i++ {
        actx, cancel := context.WithTimeout(ctx, 2*time.Second) // per-attempt timeout
        err = fn(actx)
        cancel()
        if err == nil {
            return nil
        }

        backoff := base << i                                  // 100ms, 200ms, 400ms...
        sleep := time.Duration(rand.Int63n(int64(backoff)))   // FULL JITTER: random in [0, backoff)

        t := time.NewTimer(sleep)
        select {
        case <-t.C:
        case <-ctx.Done(): // never sleep past the caller's deadline
            t.Stop()
            return ctx.Err()
        }
    }
    return fmt.Errorf("after %d attempts: %w", attempts, err)
}
```

Why jitter: without it, thousands of clients that failed together retry **together** (thundering herd / retry storm) and re-crash the recovering service.

### 9.8 Hedged requests (first response wins, tame tail latency)

```go
// Send to replica 0. If no answer within hedgeAfter, ALSO ask replica 1, etc.
// First success wins; the rest are cancelled.
func Hedged(ctx context.Context, hedgeAfter time.Duration,
    replicas []func(context.Context) (string, error)) (string, error) {

    ctx, cancel := context.WithCancel(ctx)
    defer cancel() // cancels the losers

    type res struct {
        v   string
        err error
    }
    out := make(chan res, len(replicas)) // buffered: losers never block

    launch := func(i int) {
        go func() {
            v, err := replicas[i](ctx)
            out <- res{v, err}
        }()
    }

    launch(0)
    next, pending := 1, 1
    timer := time.NewTimer(hedgeAfter)
    defer timer.Stop()

    var lastErr error
    for pending > 0 {
        select {
        case r := <-out:
            pending--
            if r.err == nil {
                return r.v, nil
            }
            lastErr = r.err
            if next < len(replicas) { // fail-fast hedge
                launch(next); next++; pending++
            }
        case <-timer.C: // slow-primary hedge
            if next < len(replicas) {
                launch(next); next++; pending++
                timer.Reset(hedgeAfter)
            }
        case <-ctx.Done():
            return "", ctx.Err()
        }
    }
    return "", lastErr
}
```

Hedge delay is typically set near the **p95** latency, so only ~5% of requests incur extra load.
Only hedge **idempotent** (read-like) operations.

### 9.9 Scatter-gather with a deadline (partial results are better than none)

```go
type outcome struct {
    r   Result
    err error
}

func Gather(ctx context.Context, d time.Duration,
    calls []func(context.Context) (Result, error)) []Result {

    ctx, cancel := context.WithTimeout(ctx, d)
    defer cancel()

    ch := make(chan outcome, len(calls)) // buffered: no leaked senders
    for _, c := range calls {
        go func() { // Go 1.22+: per-iteration loop variable
            r, err := c(ctx)
            ch <- outcome{r, err}
        }()
    }

    var results []Result
    for range calls {
        select {
        case o := <-ch:
            if o.err == nil {
                results = append(results, o.r)
            }
        case <-ctx.Done():
            return results // deadline hit: return what we have (degraded response)
        }
    }
    return results
}
```

Real example: a search page that queries 20 shards; any shard slower than 150 ms is skipped and the
page still renders ("partial results").

### 9.10 Worker pool with admission control (load shedding)

```go
var ErrOverloaded = errors.New("overloaded")

type Pool struct {
    jobs chan Job // bounded queue = bounded memory = bounded latency
}

func NewPool(workers, queue int) *Pool {
    p := &Pool{jobs: make(chan Job, queue)}
    for i := 0; i < workers; i++ {
        go func() {
            for j := range p.jobs {
                j.Run()
            }
        }()
    }
    return p
}

// Fast reject: zero wait
func (p *Pool) TrySubmit(j Job) error {
    select {
    case p.jobs <- j:
        return nil
    default:
        return ErrOverloaded
    }
}

// Bounded wait: absorb short bursts, reject sustained overload
func (p *Pool) Submit(ctx context.Context, j Job, maxWait time.Duration) error {
    t := time.NewTimer(maxWait)
    defer t.Stop()
    select {
    case p.jobs <- j:
        return nil
    case <-t.C:
        return ErrOverloaded
    case <-ctx.Done():
        return ctx.Err()
    }
}
```

Rule of thumb: **reject early and cheaply** (HTTP 429/503) instead of accepting work you can't finish in time.

### 9.11 Non-blocking pub/sub fan-out (one slow subscriber must not stall everyone)

```go
type Sub struct {
    C     chan Msg
    drops atomic.Int64
}

type Hub struct {
    mu   sync.RWMutex
    subs map[*Sub]struct{}
}

func (h *Hub) Publish(m Msg) {
    h.mu.RLock()
    defer h.mu.RUnlock()
    for s := range h.subs {
        select {
        case s.C <- m:
        default: // subscriber's buffer is full
            s.drops.Add(1) // count it; a janitor may evict chronic offenders
        }
    }
}
```

Variants for the full-buffer case: **drop newest** (above), **drop oldest** (pop one then push),
**disconnect** the slow subscriber (what many brokers do), or **wait up to T** then disconnect.

### 9.12 Semaphore / pool acquisition with timeout

```go
type Sem chan struct{}

func NewSem(n int) Sem { return make(Sem, n) }

func (s Sem) TryAcquire() bool {
    select {
    case s <- struct{}{}:
        return true
    default:
        return false
    }
}

func (s Sem) Acquire(ctx context.Context) error {
    select {
    case s <- struct{}{}:
        return nil
    case <-ctx.Done():
        return ctx.Err()
    }
}

func (s Sem) Release() { <-s }
```

This is exactly the shape of **DB connection pool checkout** (`db.Conn(ctx)`): wait for a free connection, but only until the context expires.

### 9.13 Wait for a `WaitGroup` with timeout

```go
func WaitTimeout(wg *sync.WaitGroup, d time.Duration) bool {
    done := make(chan struct{})
    go func() {
        wg.Wait()
        close(done)
    }()
    t := time.NewTimer(d)
    defer t.Stop()
    select {
    case <-done:
        return true
    case <-t.C:
        return false // note: the helper goroutine lives until wg completes
    }
}
```

### 9.14 Graceful shutdown with a hard timeout

```go
func shutdown(srv *http.Server, workers *sync.WaitGroup) {
    ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
    defer cancel()

    // Phase 1: stop accepting; let in-flight requests finish (bounded by ctx)
    if err := srv.Shutdown(ctx); err != nil {
        srv.Close() // Phase 2: deadline exceeded -> force close
    }

    // Phase 3: wait for background workers within the SAME budget
    done := make(chan struct{})
    go func() { workers.Wait(); close(done) }()
    select {
    case <-done:
    case <-ctx.Done():
        log.Println("workers did not stop in time; exiting anyway")
    }
}
```

Same pattern as container runtimes: `SIGTERM` -> wait grace period -> `SIGKILL`.

### 9.15 Coalescing wake-ups (capacity-1 signal channel)

```go
notify := make(chan struct{}, 1)

func Poke() { // callable from anywhere, never blocks
    select {
    case notify <- struct{}{}:
    default: // a wakeup is already pending: coalesce
    }
}

func loop(ctx context.Context) {
    for {
        select {
        case <-notify:
            drainAndProcessAllPendingWork() // one wake handles many pokes
        case <-ctx.Done():
            return
        }
    }
}
```

### 9.16 Disabling a `select` case dynamically (nil-channel trick)

```go
func merge(ctx context.Context, a, b <-chan int, out chan<- int) {
    for a != nil || b != nil {
        select {
        case v, ok := <-a:
            if !ok { a = nil; continue } // receive on nil blocks forever => case disabled
            out <- v
        case v, ok := <-b:
            if !ok { b = nil; continue }
            out <- v
        case <-ctx.Done():
            return
        }
    }
}
```

### 9.17 Circuit breaker (timeouts as the input signal)

```text
        success                         failures >= threshold
   +---------------+                  +---------------------+
   v               |                  |                     v
 [ CLOSED ] ------------------------------------------> [ OPEN ]
 (calls flow)                                           (fail fast, no calls)
     ^                                                      |
     |            probe succeeded                           | after cool-down
     |                                                      v
     +-------------------------------------------------[ HALF-OPEN ]
                 probe failed -> back to OPEN             (allow 1 trial call)
```

Timeouts feed the breaker: repeated timeouts increment the failure count; once OPEN, callers
get an instant error (a *zero-wait* non-blocking rejection) instead of burning a full timeout each time.

---

<a id="part-10"></a>
## Part 10 - Where This Is Used: Real-World Use Cases

### 10.1 Master table

| # | Domain | Scenario | Non-blocking / Timeout role |
|---|---|---|---|
| 1 | **HTTP/gRPC clients** | Calling downstream services | Per-call timeout + propagated deadline; client gives up, server sees cancellation |
| 2 | **HTTP servers** | Slow/malicious clients | Read/Write/Idle timeouts bound each connection (defeats slowloris) |
| 3 | **DB access** | Connection pool checkout, query execution | Wait for free connection up to `ctx`; statement timeout |
| 4 | **Message consumers** (Kafka/RabbitMQ/SQS style) | Poll loop | `poll(timeout)`: wait up to T for messages then check shutdown flags / heartbeats |
| 5 | **Pub/Sub brokers & chat hubs** | Fan-out to thousands of subscribers | Non-blocking send; slow consumer dropped or disconnected |
| 6 | **WebSocket servers** | Ping/pong keepalive | Write deadline per message; read deadline refreshed by pongs |
| 7 | **Worker pools / job queues** | Overload protection | Bounded queue; `TrySubmit` / `Submit` with max wait -> 429/503 |
| 8 | **Distributed systems** | Leader election, leases | Lease renew deadline; lose leadership if renewals time out |
| 9 | **Kubernetes-style controllers** | Reconcile loops, leader election, graceful pod termination | Work-queue wait with timeouts, renew deadlines, `terminationGracePeriod` |
| 10 | **Service discovery / health checks** | Probing instances | Probe timeout -> mark unhealthy; non-blocking aggregation of results |
| 11 | **Load balancers / API gateways** | Upstream selection | Connect timeout, response timeout, retries with budget, hedging |
| 12 | **Search / aggregation backends** | Query many shards | Scatter-gather with deadline, partial results |
| 13 | **Logging / metrics / tracing pipelines** | Telemetry must not hurt the app | Non-blocking enqueue; drop on full; count drops |
| 14 | **Game servers / real-time loops** | Fixed tick (e.g., 60 Hz) | Drain inputs non-blockingly each tick; never stall the tick |
| 15 | **Trading / market data** | Latest-price streams | Conflation: slow consumer gets only the latest value (drop-oldest) |
| 16 | **CLI tools / orchestration scripts** | `kubectl wait`-style, "wait for port" | Poll with ticker + overall timeout |
| 17 | **Embedded / IoT gateways** | Device may vanish | Request-response with timeout; watchdog resets |
| 18 | **Distributed locks / consensus** | Acquire lock | `TryLock` / acquire-with-timeout to avoid deadlock |
| 19 | **Payment / order flows** | Calling a bank / provider | Timeout + idempotency key + reconcile later (unknown outcome!) |
| 20 | **Test harnesses** | "Eventually" assertions | Poll until condition or timeout; fail the test instead of hanging CI |

### 10.2 Deeper narratives

**(a) The API gateway.**
Browser -> gateway -> 5 microservices. The browser gives up after ~10 s. The gateway therefore
assigns a total budget (say 2 s) and passes the *remaining* budget downstream as a deadline. Each
service subtracts its own work time. When any layer's deadline expires, it stops, and cancellation
propagates down so no service continues computing a result nobody will read.

**(b) The chat/notification hub.**
10,000 WebSocket users in a room. One user is on a failing mobile network and reads slowly. If the
hub did `sub.C <- msg` (blocking), the hub's goroutine would block on that user and **all 9,999
others would stall**. With non-blocking sends and per-subscriber bounded buffers, the hub never waits;
the slow user's messages are dropped or the user is disconnected.

**(c) The database connection pool.**
Pool size 20, traffic spike. Requests 21+ must wait for a connection. With no timeout they wait forever,
holding request goroutines and memory. With `ctx` they wait until their deadline, then fail with
`DeadlineExceeded`, which turns an *overload* into a *bounded, visible error* rather than a hang.

**(d) Telemetry pipeline.**
Your service emits 50k log lines/s to a shipper. If the shipper stalls, a blocking enqueue would stall
request handling. A non-blocking enqueue with drop-on-full means the business logic never depends on the
logging backend. You lose a few logs and gain an available service: usually the right trade.

**(e) Kafka-style consumer loop.**
`poll(timeout=500ms)` returns early with data or after 500 ms with none. The timeout lets the loop
regularly check "am I being shut down?", send heartbeats (so the broker doesn't think the consumer
died), and commit offsets.

---

<a id="part-11"></a>
## Part 11 - Real Architectures (ASCII Diagrams)

### 11.1 End-to-end request with deadline propagation

Each hop receives the **remaining** budget, never a fresh one.

```text
+----------------------------------------------------------------+
| CLIENT (browser/mobile)                                        |
| gives up after ~10 s; sends request                            |
+----------------------------------------------------------------+
                                 |
                                 |  request
                                 v
+----------------------------------------------------------------+
| API GATEWAY                                                    |
| ctx,cancel := WithTimeout(req.Context(), 2000ms)               |
| deadline = T0 + 2000ms                                         |
+----------------------------------------------------------------+
                                 |
                                 |  gRPC/HTTP, deadline propagated (remaining 1980ms)
                                 v
+----------------------------------------------------------------+
| ORDER SERVICE                                                  |
| inherits deadline T0+2000ms                                    |
| own work ~100ms -> remaining ~1850ms                           |
+----------------------------------------------------------------+
                |                                 |
                |  both calls get the remaining ~1850ms
                |                                 |
                v                                 v
+------------------------------+  +------------------------------+
| INVENTORY SERVICE            |  | PRICING SERVICE              |
| DB query uses same ctx       |  | cache lookup, then calc      |
+------------------------------+  +------------------------------+
                |
                |  QueryContext(ctx, ...)
                v
+----------------------------------------------------------------+
| DATABASE (connection pool)                                     |
| pool checkout waits at most until ctx.Done()                   |
| statement cancelled server-side when ctx expires               |
+----------------------------------------------------------------+

If T0 + 2000ms is reached ANYWHERE: ctx.Done() closes -> every layer below
returns early -> no work continues for a request nobody is waiting for.
```

### 11.2 Worker pool with bounded queue and admission control

```text
+------------------------------------------------+
| PRODUCERS (HTTP handlers, 1000s of goroutines) |
+------------------------------------------------+
        |
        |  Submit(ctx, job, maxWait=50ms)
        v
+-------------------------------------------------------+
| ADMISSION GATE (select)                               |
|   case jobs <- job          -> accepted               |
|   case <-timer (50ms)       -> reject: 503 Overloaded |
|   case <-ctx.Done()         -> client already gone    |
+-------------------------------------------------------+
        |
        |  accepted
        v
+----------------------------------------------+
| BOUNDED QUEUE: chan Job, capacity = 100      |
| [ J ][ J ][ J ][ J ][ J ][   ][   ][   ] ... |
+----------------------------------------------+
        |
        |  for job := range jobs
        v
+-----------------------------------------+
| WORKERS (fixed N = 8 goroutines)        |
| each job runs with its own ctx deadline |
+-----------------------------------------+
        |
        v
+------------------------------+
| DOWNSTREAM (DB / API / disk) |
+------------------------------+

 Max latency in system  <=  maxWait + queue_len * avg_job / N + job_time
 Max memory in system   <=  (queue + N) * job_size            (BOUNDED)
```

### 11.3 Pub/Sub hub with non-blocking fan-out

```text
+----------------------------------------------------------------+
| PUBLISHER(S)                                                   |
+----------------------------------------------------------------+
                                 |
                                 |  hub.Publish(msg)
                                 v
+----------------------------------------------------------------+
| HUB   for each subscriber:                                     |
|   select {                                                     |
|     case sub.C <- msg:  // delivered into its buffer           |
|     default:            // buffer full -> drop / count         |
|   }                                                            |
+----------------------------------------------------------------+
           |                        |                        |
           |                        |                        |
           v                        v                        v
+--------------------+   +--------------------+   +--------------------+
| SUB A              |   | SUB B              |   | SUB C (slow)       |
| buf 64             |   | buf 64             |   | buf 64 [FULL]      |
| drops = 0          |   | drops = 0          |   | drops = 1,203      |
+--------------------+   +--------------------+   +--------------------+
           |                        |                        |
           |                        |                        |
           v                        v                        v
+--------------------+   +--------------------+   +--------------------+
| socket write       |   | socket write       |   | write deadline hit |
| (fast)             |   | (fast)             |   | -> evict & close   |
+--------------------+   +--------------------+   +--------------------+

A and B are NEVER delayed by C.  Failure is isolated and observable.
```

### 11.4 Hedged scatter-gather (tail-latency control)

```text
+-------------------------------+
| CALLER  ctx deadline = 300 ms |
+-------------------------------+
        |
        |  t = 0
        v
+---------------------------+
| send request to REPLICA 1 |
+---------------------------+
        |
        |  no answer by t = 80 ms (≈ p95)  -> hedge timer fires
        v
+--------------------------------+
| ALSO send request to REPLICA 2 |
+--------------------------------+
        |
        |  first successful reply wins
        v
+-----------------------------------------------------------+
| return result, cancel() the loser (ctx cancel propagates) |
+-----------------------------------------------------------+
```

### 11.5 Timeline: how a timed `select` actually resolves

Hand-aligned timeline (1 character = 20 ms):

```text
 t(ms)   0         100       200       300       400       500
         |---------|---------|---------|---------|---------|
 caller  [ select { case <-result  case <-timer(300ms) } ]
                                        ^
 worker  [========== slowCall() ==================>]  finishes at 450 ms
                                        |            \
 timer   [-----------------------------](fires 300ms)  result arrives AFTER timeout
                                        |
                                        +--> timeout branch wins at t=300
                                             caller returns ErrTimeout
                                             worker keeps running unless it honors ctx
                                             and result channel must be buffered (cap 1)
```

### 11.6 The three outcomes of `select { send / timeout / default }`

```text
+------------------------------------------------------------+
| select {                                                   |
|   case q <- job:     // (A) space available                |
|   case <-timer.C:    // (B) waited too long                |
|   default:           // (C) pure try-send (NO timer)       |
| }                                                          |
+------------------------------------------------------------+
          |                      |                      |
          |                      |                      |
          v                      v                      v
+------------------+   +------------------+   +------------------+
| (A) ACCEPTED     |   | (B) TIMED OUT    |   | (C) REJECTED NOW |
| latency ~0..T    |   | latency = T      |   | latency ~0       |
+------------------+   +------------------+   +------------------+

NOTE: `default` plus a timer case in one select is almost never what you want:
default makes the select return instantly, so the timer never gets to win.
```

---

<a id="part-12"></a>
## Part 12 - Choosing Timeout Values

A timeout is a *design decision*, not a magic number.

### 12.1 Where numbers come from

1. **Start from the caller's budget** (SLO). If the user-facing SLO is 1 s, the sum of everything beneath it must be < 1 s.
2. **Measure the dependency's latency distribution.** Set the per-call timeout near **p99 - p99.9** of healthy latency (plus a small margin), not at the average.
3. **Account for retries.** `total = attempts x per_attempt + backoff_sleeps` must still fit the budget.
4. **Nested timeouts must decrease downward.** A parent must wait *longer* than its child; otherwise the parent gives up first and the child's error is never seen.

```text
 Gateway   : 2000 ms  (outermost)
   Service : 1500 ms
     DB    :  800 ms
```

### 12.2 Too short vs. too long

| Too short | Too long |
|---|---|
| False failures under normal jitter | Slow failure detection |
| Retry amplification (more load on a struggling system) | Resource pile-up (goroutines, connections, memory) |
| Flapping health/leader state | User-visible hangs |

### 12.3 Retry budgets and amplification

If each of 3 layers retries 3 times, one failure at the bottom can create **3 x 3 x 3 = 27** calls.
Mitigations: retry at **one layer** only, use a **retry budget** (e.g., retries <= 10% of requests),
add **jitter**, and obey the deadline.

### 12.4 Adaptive approaches

- **Adaptive timeouts** from recent latency percentiles (e.g., p99 x 1.5, clamped to [min, max]).
- **Concurrency limits** (adaptive, AIMD / gradient style) so overload shows up as fast rejects, not as timeouts.
- **Load shedding** at the edge using queue time as the signal (reject if a request already waited > X ms before getting a worker).

### 12.5 Typical starting points (adjust with data)

| Operation | Rough starting timeout |
|---|---|
| TCP connect | 1-3 s (LAN: 100-500 ms) |
| Intra-datacenter RPC | 50 ms - 1 s |
| DB point query | 100 ms - 2 s |
| Cache lookup | 5-50 ms |
| Queue admission wait | 0 - 100 ms |
| Graceful shutdown | 10-60 s (match orchestrator grace period) |
| Heartbeat interval / dead-peer detection | interval T, declare dead after 3T |

---

<a id="part-13"></a>
## Part 13 - Other Languages and Runtimes

The concepts (try, wait-with-timeout, cancel, deadline) are universal; only the spelling changes.

| Concept | Go | Java | Rust (Tokio) | Kotlin | Python asyncio |
|---|---|---|---|---|---|
| Try-send | `select{case ch<-v: default:}` | `queue.offer(v)` | `tx.try_send(v)` | `channel.trySend(v)` | `q.put_nowait(v)` |
| Try-receive | `select{case v=<-ch: default:}` | `queue.poll()` | `rx.try_recv()` | `channel.tryReceive()` | `q.get_nowait()` |
| Send with timeout | select + timer | `queue.offer(v, 1, SECONDS)` | `timeout(d, tx.send(v)).await` | `withTimeoutOrNull(d){ch.send(v)}` | `wait_for(q.put(v), d)` |
| Receive with timeout | select + timer | `queue.poll(1, SECONDS)` | `timeout(d, rx.recv()).await` | `withTimeoutOrNull(d){ch.receive()}` | `wait_for(q.get(), d)` |
| Multiplex | `select` | `Selector.select(timeout)` (NIO) | `tokio::select!` | `select {}` | `asyncio.wait(FIRST_COMPLETED)` |
| Cancellation | `context` | `Future.cancel`, interrupts | drop the future / `CancellationToken` | structured concurrency | `task.cancel()` |

### 13.1 Java

```java
// BlockingQueue: three modes
queue.put(item);                         // blocks forever
boolean ok = queue.offer(item);          // non-blocking, false if full
boolean ok2 = queue.offer(item, 200, TimeUnit.MILLISECONDS); // timed

Item x = queue.poll();                              // non-blocking, null if empty
Item y = queue.poll(500, TimeUnit.MILLISECONDS);    // timed, null on timeout
```

```java
// NIO: a *Channel* (java.nio) in non-blocking mode + Selector with timeout
SocketChannel ch = SocketChannel.open();
ch.configureBlocking(false);                 // read()/write() return immediately
Selector sel = Selector.open();
ch.register(sel, SelectionKey.OP_READ);

int ready = sel.select(1000);                // wait up to 1000 ms for any readiness
if (ready == 0) { /* timeout: no channel ready; check idle timers, shutdown flags */ }
```

> In Java NIO the word "channel" means an I/O endpoint (socket/file). `configureBlocking(false)`
> makes operations return immediately (`read` may return 0), and `Selector.select(timeout)` is the
> **timed wait over many channels**: exactly the same architecture as Go's `select` with a timer,
> and it is the foundation of Netty, Jetty, Kafka's network layer, and Elasticsearch.

### 13.2 Rust (Tokio)

```rust
use tokio::{sync::mpsc, time::{timeout, Duration}};

// non-blocking
match tx.try_send(msg) {
    Ok(()) => {}
    Err(mpsc::error::TrySendError::Full(_))   => { /* shed load */ }
    Err(mpsc::error::TrySendError::Closed(_)) => { /* receiver gone */ }
}

// timed
match timeout(Duration::from_millis(200), rx.recv()).await {
    Ok(Some(v)) => { /* got value */ }
    Ok(None)    => { /* channel closed */ }
    Err(_)      => { /* elapsed */ }
}

// multiplex + timeout
tokio::select! {
    v = rx.recv() => { /* ... */ }
    _ = tokio::time::sleep(Duration::from_secs(1)) => { /* timeout */ }
}
```

In Tokio, dropping a future cancels it at its next `.await`, so timeouts naturally cancel the work
(a different default than Go, where you must wire context yourself).

### 13.3 Kotlin Coroutines

```kotlin
val ch = Channel<Int>(capacity = 16)

ch.trySend(1)                         // non-blocking: returns ChannelResult
val r = withTimeoutOrNull(200) { ch.receive() }  // null on timeout; cancels the block
```

### 13.4 Python asyncio

```python
q = asyncio.Queue(maxsize=100)
try:
    q.put_nowait(item)                         # non-blocking
except asyncio.QueueFull:
    ...                                        # shed load

try:
    item = await asyncio.wait_for(q.get(), timeout=0.5)   # timed (cancels q.get() on timeout)
except asyncio.TimeoutError:
    ...
```

### 13.5 Key semantic difference to remember

| Runtime | When a timeout fires, the underlying work... |
|---|---|
| Go | **keeps running** unless you wire `context` / done channels |
| Rust (futures) | is **dropped/cancelled** at next await |
| Kotlin | is **cancelled** (structured concurrency) |
| Python asyncio | is **cancelled** by `wait_for` |
| Java threads | keeps running unless interrupted / future cancelled |

---

<a id="part-14"></a>
## Part 14 - Testing Timeout Code

Timeout code is notoriously flaky to test because it depends on real time.

### 14.1 Principles

1. **Make durations parameters** (or fields), so tests use 10 ms and production uses 2 s.
2. **Test both branches deliberately**: "result arrives first" and "timeout arrives first".
3. **Check for goroutine leaks** after timeout paths (`goleak`, or compare `runtime.NumGoroutine()`).
4. **Use `-race`** and run repeatedly: `go test -race -count=100`.
5. **Never `time.Sleep` to "wait for the other goroutine"** in tests; synchronize with channels.

### 14.2 Example

```go
func TestRecvTimeout_Times_Out(t *testing.T) {
    ch := make(chan int)
    start := time.Now()
    _, err := RecvTimeout(ch, 20*time.Millisecond)
    if !errors.Is(err, ErrTimeout) {
        t.Fatalf("want ErrTimeout, got %v", err)
    }
    if el := time.Since(start); el < 20*time.Millisecond {
        t.Fatalf("returned too early: %v", el)
    }
}

func TestRecvTimeout_Gets_Value(t *testing.T) {
    ch := make(chan int, 1)
    ch <- 42
    v, err := RecvTimeout(ch, time.Second)
    if err != nil || v != 42 {
        t.Fatalf("got %v, %v", v, err)
    }
}
```

### 14.3 Fake / virtual time

- **Inject a clock** interface (`Now()`, `NewTimer()`, `After()`), swap in a fake in tests
  (libraries such as `benbjohnson/clock`, `jonboulle/clockwork`).
- Recent Go versions ship the **`testing/synctest`** package (experimental in Go 1.24, stable in Go 1.25) that runs code in a
  "bubble" with virtual time: `time.Sleep(10 * time.Minute)` returns instantly once all goroutines in
  the bubble are blocked. Check your toolchain's docs for the exact API.

---

<a id="part-15"></a>
## Part 15 - Observability: What to Measure

A timeout you can't see is a failure you can't fix.

| Metric | Why |
|---|---|
| `timeouts_total{op, dependency}` | Rate of timeouts per operation/dependency; alert on spikes |
| `queue_depth` / `queue_wait_seconds` (histogram) | Leading indicator of overload *before* timeouts start |
| `rejected_total{reason="overload"}` | Load-shedding activity |
| `dropped_total{subscriber/stream}` | Non-blocking drops (data loss visibility) |
| `inflight_requests` / goroutine count | Leak/pile-up detector |
| `latency_seconds` histogram with p50/p95/p99 | Calibrating timeout values |
| `retries_total`, `retry_budget_exhausted_total` | Retry storm detection |
| `deadline_remaining_at_call_seconds` | Are downstream calls starting with too little budget? |

Log with context: operation, timeout value, elapsed, attempt number, remaining deadline, trace ID.
Use **`context.Cause`** / `WithTimeoutCause` to record *why* a context ended.

```go
ctx, cancel := context.WithTimeoutCause(parent, 2*time.Second,
    errors.New("inventory lookup exceeded 2s"))
defer cancel()
// later: context.Cause(ctx)
```

---

<a id="part-16"></a>
## Part 16 - Pitfalls and Anti-Patterns Checklist

| # | Pitfall | Why it hurts | Fix |
|---|---|---|---|
| 1 | Unbuffered result channel + goroutine that sends after timeout | Goroutine leak | Buffer of 1, or `select` with `ctx.Done()` on the send |
| 2 | Forgetting `defer cancel()` after `WithTimeout` | Timer/context retained until it fires | Always `defer cancel()` |
| 3 | `defer cancel()` inside a long loop | Defers pile up until function returns | Call `cancel()` explicitly per iteration |
| 4 | `time.After` in a hot loop | Allocation churn (and leaks pre-1.23) | Reuse a `time.Timer` with `Reset` |
| 5 | Reusing/resetting a timer without draining (Go < 1.23) | Stale tick causes instant bogus timeout | Stop-drain-Reset idiom, or upgrade `go.mod` |
| 6 | `select` with `default` in a loop | 100% CPU spin | Remove `default` or add a blocking case/ticker |
| 7 | Both `default` and timer in same `select` | Timer never wins; select returns instantly | Choose one: zero-wait *or* timed |
| 8 | Assuming timeout cancels the work | Wasted resources, duplicate side effects | Pass `ctx` into the work; design idempotent ops |
| 9 | Retrying non-idempotent operations | Double charges / double writes | Idempotency keys, dedupe, reconcile |
| 10 | Same fixed backoff on all clients | Thundering herd | Exponential + jitter |
| 11 | Child timeout > parent timeout | Parent fires first; confusing errors | Make timeouts decrease downward, or just use contexts |
| 12 | Retrying at every layer | Exponential load amplification | Retry at one layer; budgets |
| 13 | Ignoring `ctx.Done()` in long loops | Cancellation never takes effect | Check `ctx.Err()`/select regularly |
| 14 | Sending on a closed channel | **Panic** | Single owner closes; or signal with `done` channel instead |
| 15 | Using `len(ch) < cap(ch)` as "can send" | Race (check-then-act) | Use `select` with `default` (atomic attempt) |
| 16 | Unbounded queues ("just make the buffer huge") | Hides overload, memory blow-up, huge latency | Bounded buffers + reject policy |
| 17 | Treating timeout as "peer is dead" | Wrong failure model | Treat as *unknown state*; verify before assuming |
| 18 | Expecting `select` priority by case order | It's random among ready cases | Nested/pre-check `select` |
| 19 | Side effects inside case expressions | Evaluated even if the case loses | Compute before the `select` |
| 20 | Hard-coded magic timeouts | Wrong in different environments | Config + defaults + metrics-driven tuning |
| 21 | No timeout on connect/TLS/DNS, only on read | Hangs in handshake phase | Configure each phase (dialer, TLS handshake, response header) |
| 22 | Timeouts everywhere but no overload protection | Timeouts become the *only* signal; too late | Add admission control and concurrency limits |
| 23 | Zero-value `http.Client{}` / `http.DefaultClient` | No overall timeout by default | Set `Timeout` and a tuned `Transport` |

---

<a id="part-17"></a>
## Part 17 - Cheat Sheet and Decision Tree

### 17.1 Snippets

```go
// Non-blocking send / receive
select { case ch <- v: ok = true; default: ok = false }
select { case v = <-ch: ok = true; default: ok = false }

// Timed send / receive
t := time.NewTimer(d); defer t.Stop()
select { case ch <- v: ; case <-t.C: /* timeout */ }
select { case v := <-ch: ; case <-t.C: /* timeout */ }

// Context-aware
select { case ch <- v: ; case <-ctx.Done(): return ctx.Err() }

// Deadline for a call chain
ctx, cancel := context.WithTimeout(parent, d); defer cancel()

// Reusable idle timer
idle.Reset(d)           // Go 1.23+; before that: Stop + drain + Reset

// Coalesced wakeup
select { case sig <- struct{}{}: default: }
```

### 17.2 Decision tree

```text
+------------------------------------------+              +--------------------------------+
| Must this operation NEVER wait           |              | Non-blocking:                  |
| (hot path, telemetry, notification)?     |-yes--------->| select + default               |
+------------------------------------------+              | (drop / reject + count it)     |
                      |                                   +--------------------------------+
                      |  no
                      v
+------------------------------------------+              +--------------------------------+
| Is there a caller deadline or            |              | context.WithTimeout/           |
| cancellation (request-scoped)?           |-yes--------->| WithDeadline; pass ctx to      |
+------------------------------------------+              | ALL callees                    |
                      |                                   +--------------------------------+
                      |  no
                      v                                   (preferred in services; then continue with the two checks below)
+------------------------------------------+
| Local bounded wait:                      |
| time.NewTimer + select                   |
| (reuse with Reset in loops)              |
+------------------------------------------+
                      |
                      |
                      v
+--------------------------------------------------------------+
| Can the work be cancelled, and is the result                 |
| channel buffered(1) so a late sender cannot leak?            |
+--------------------------------------------------------------+
                                |
                                |
                                v
+--------------------------------------------------------------+
| Is it idempotent? If retrying: backoff + jitter +            |
| budget, and ALWAYS obey the overall deadline                 |
+--------------------------------------------------------------+
```

### 17.3 One-page mental model

```text
 +----------------------------------------------------------------+
 |  Timeouts are just another channel in a select.                |
 |                                                                |
 |  zero wait   -> default                                        |
 |  bounded wait-> timer.C                                        |
 |  caller scope-> ctx.Done()                                     |
 |  broadcast   -> close(done)                                    |
 |                                                                |
 |  A timeout stops WAITING, not WORKING.                         |
 |  Bound every queue. Bound every wait. Make outcomes idempotent.|
 |  Measure drops, rejects, timeouts, queue wait.                 |
 +----------------------------------------------------------------+
```

---

<a id="part-18"></a>
## Part 18 - Exercises and Interview Questions

### 18.1 Exercises (increasing difficulty)

1. Implement `TrySend`, `SendTimeout`, `RecvTimeout` generically and unit-test both branches.
2. Write a function that calls a slow function with a timeout **without leaking a goroutine**. Prove it with a leak test.
3. Build an idle-timeout TCP echo server that closes connections idle for 10 s using one reusable timer per connection.
4. Build a pub/sub hub with per-subscriber buffers, drop counters, and eviction after N consecutive drops.
5. Implement a worker pool with `TrySubmit`, `Submit(maxWait)`, graceful shutdown with a timeout, and metrics.
6. Implement hedged requests; show that p99 latency improves in a simulation with random slow replicas.
7. Implement `Retry` with exponential backoff, jitter, per-attempt timeout, and a shared retry budget.
8. Build a priority `select` loop where `quit` must always win over `work`, even when both are ready.
9. Write a semaphore with `TryAcquire`, `Acquire(ctx)`, and fairness tests under contention.
10. Reproduce the pre-1.23 stale-timer bug using `GODEBUG=asynctimerchan=1`, then fix it with Stop/drain/Reset.

### 18.2 Interview questions (with short answers)

**Q1. How do you implement a non-blocking send in Go?**
`select { case ch <- v: ... default: ... }`; the `default` runs only if no other case can proceed immediately.

**Q2. How do you add a timeout to a channel receive?**
`select { case v := <-ch: ... case <-time.After(d): ... }` or, better in services, `case <-ctx.Done()` with `context.WithTimeout`.

**Q3. What happens if two `select` cases are ready?**
One is chosen pseudo-randomly. There is no priority by order.

**Q4. Why can `time.After` in a loop be a problem?**
It allocates a timer per iteration; before Go 1.23 each lived until it fired even if unused. Reuse a `time.Timer` with `Reset`.

**Q5. What is the goroutine-leak risk with timeouts?**
After a timeout the worker may later block forever sending on an unbuffered channel nobody reads. Use a buffered(1) result channel or a cancellable context.

**Q6. Does a timeout cancel the operation?**
No; it only stops waiting. You need context/cancellation to stop the work, and idempotency because the outcome is unknown.

**Q7. Timeout vs deadline?**
Timeout is a duration for one operation; a deadline is an absolute instant shared across a call chain. Prefer deadlines/contexts across hops.

**Q8. Why must `cancel()` always be called?**
It releases the context's timer and detaches it from its parent; forgetting it leaks resources until expiry.

**Q9. How do you avoid a slow consumer blocking a broadcaster?**
Per-consumer bounded buffer + non-blocking send; drop, drop-oldest, or disconnect on full.

**Q10. What is a busy loop and how does `select/default` cause it?**
A loop that spins without blocking, burning CPU. `default` makes the `select` return instantly; remove it or add a blocking case/ticker.

**Q11. How do you pick a timeout value?**
From the caller's SLO budget and the dependency's measured latency percentiles (≈ p99+margin), with nested timeouts decreasing downward, and retries/backoff included in the total.

**Q12. How do you disable a `select` case at runtime?**
Set its channel variable to `nil`; operations on nil channels block forever, so the case is never ready.

**Q13. Why add jitter to retries?**
To desynchronize clients and avoid retry storms / thundering herds that re-overload a recovering dependency.

**Q14. What changed with timers in Go 1.23?**
Timer channels became synchronous (no stale values after `Stop`/`Reset`), and unreferenced timers can be garbage collected without being stopped (gated by the module's `go` version).

**Q15. Why is non-blocking + bounded queue + timeout a strong combination?**
It bounds memory (queue size), bounds latency (wait limit), and converts overload into fast, observable rejections instead of cascading hangs.

---

## Final Takeaways

1. **Every wait needs a bound.** Infinite waiting is a latent outage.
2. **Choose the wait mode deliberately:** zero (`default`), bounded (`timer`), scoped (`context`).
3. **A timeout abandons the wait, not the work.** Plan for cancellation, leaks, and unknown outcomes (idempotency).
4. **Bound the queues, shed load early, isolate slow consumers.** Non-blocking operations are how you keep one bad actor from taking down everyone.
5. **Propagate deadlines, don't restart them.** Budgets shrink as they travel down the call graph.
6. **Measure it.** Timeouts, drops, queue wait and retries are first-class signals of system health.

*Master these six ideas and you can reason about timeouts and backpressure in any language, runtime, or distributed architecture.*
