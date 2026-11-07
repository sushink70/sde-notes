# Timeouts in Go: The Complete Guide

> **Goal of this guide:** build a durable *mental model* of timeouts so you can reason about
> them from first principles, not just copy snippets. Every section answers three questions:
> **What is it? Why does it exist? How do I use it correctly in production?**

---

## Table of Contents

1. [What a timeout really is (the mental model)](#1-what-a-timeout-really-is-the-mental-model)
2. [Why timeouts matter: the failure physics](#2-why-timeouts-matter-the-failure-physics)
3. [Where timeouts live: the layers of a real system](#3-where-timeouts-live-the-layers-of-a-real-system)
4. [Timeout vs Deadline vs Cancellation](#4-timeout-vs-deadline-vs-cancellation)
5. [Go's time primitives](#5-gos-time-primitives)
6. [The `context` package (the heart of Go timeouts)](#6-the-context-package-the-heart-of-go-timeouts)
7. [Cooperative cancellation: why Go cannot kill goroutines](#7-cooperative-cancellation-why-go-cannot-kill-goroutines)
8. [Network level: `net` deadlines and dialers](#8-network-level-net-deadlines-and-dialers)
9. [HTTP client timeouts](#9-http-client-timeouts)
10. [HTTP server timeouts](#10-http-server-timeouts)
11. [Databases (`database/sql`) and connection pools](#11-databases-databasesql-and-connection-pools)
12. [gRPC deadlines and propagation](#12-grpc-deadlines-and-propagation)
13. [Redis, queues and other clients](#13-redis-queues-and-other-clients)
14. [Processes: `os/exec`](#14-processes-osexec)
15. [Concurrency primitives with timeouts (mutex, WaitGroup, semaphore, errgroup)](#15-concurrency-primitives-with-timeouts)
16. [Resilience patterns built on timeouts](#16-resilience-patterns-built-on-timeouts)
17. [Real-world architectures and use cases](#17-real-world-architectures-and-use-cases)
18. [How to choose timeout values](#18-how-to-choose-timeout-values)
19. [Error handling and classification](#19-error-handling-and-classification)
20. [Testing timeouts](#20-testing-timeouts)
21. [Observability for timeouts](#21-observability-for-timeouts)
22. [Anti-patterns and pitfalls (the hall of shame)](#22-anti-patterns-and-pitfalls)
23. [Go version notes](#23-go-version-notes)
24. [Cheat sheet and mental checklist](#24-cheat-sheet-and-mental-checklist)

---

## 1. What a timeout really is (the mental model)

A **timeout** is a *bound on how long you are willing to wait* for something that you do not control.

That sentence has two important parts:

* **"bound on how long you wait"**: you convert an *unbounded* wait (potentially forever) into a
  *bounded* wait with a defined failure outcome.
* **"something you do not control"**: networks, disks, other services, databases, humans, child
  processes, locks held by someone else. Anything that crosses a boundary of your process's
  control can hang.

```text
WITHOUT a timeout                        WITH a timeout
-----------------                        --------------

 your code ---> remote call              your code ---> remote call
      |                                       |
      |   (peer hangs / packet lost)          |   (peer hangs / packet lost)
      |                                       |
      v                                       v
  waits...                                 waits... until deadline
  waits...                                      |
  waits...  <-- forever                         v
  (goroutine, memory, socket,              returns error (DeadlineExceeded)
   DB connection all held hostage)         resources released, caller decides:
                                           retry / fallback / fail fast
```

### The core insight

> A timeout does **not** make the slow thing faster.
> A timeout **protects the caller** by limiting the blast radius of the slow thing.

It is a *failure-containment* tool. Every external call in a production system should answer
the question: **"What happens to me if this never returns?"** If the answer is "I hang too",
you need a timeout.

### The three questions to ask for every blocking operation

```text
+--------------------------------------------------------------+
|  1. How long is it reasonable to wait?        (the value)    |
|  2. What do I do when the wait expires?       (the policy)   |
|  3. What happens to the work still in flight? (the cleanup)  |
+--------------------------------------------------------------+
```

Most bugs come from ignoring #2 and #3.

---

## 2. Why timeouts matter: the failure physics

### 2.1 Little's Law

Little's Law says, for a stable system:

```text
    L  =  lambda  x  W

    L      = average number of requests "in the system" (concurrency)
    lambda = arrival rate (requests per second)
    W      = average time each request spends in the system (latency)
```

Say your service receives **1,000 req/s** and normally each takes **50 ms**:

```text
    L = 1000 x 0.050 = 50 concurrent requests   (healthy)
```

Now a downstream dependency starts hanging and **requests take 30 s** (or never end):

```text
    L = 1000 x 30 = 30,000 concurrent requests  (disaster)
```

Each in-flight request holds: a goroutine (~2 KB+ stack, more as it grows), a socket / file
descriptor, buffers, maybe a DB connection and locks. Memory, FDs and pool slots run out.
Your service dies **even though your own code was fine**.

### 2.2 The cascading failure

```text
   TIME  ------------------------------------------------------------>

   Service C (slow DB)       ok ok ok ok  SLOW SLOW SLOW SLOW SLOW SLOW
                                          |
   Service B (calls C)       ok ok ok ok  threads/goroutines pile up
                                          |       -> pool exhausted
   Service A (calls B)       ok ok ok ok  ........ waits on B
                                          |       -> goroutines pile up
   Edge / LB                 ok ok ok ok  ........ 502 / 504, user retries
                                          |
                          retries multiply the load  (retry storm)
                                          v
                              WHOLE PLATFORM DOWN from ONE slow dependency
```

A timeout at each hop **breaks the chain**: B gives up on C after 300 ms, returns a fast error
(or a cached fallback), and A, the edge and the user are shielded.

### 2.3 Resources a hung call holds hostage

```text
+---------------------+------------------------------------------------+
| Resource            | What goes wrong when calls hang                |
+---------------------+------------------------------------------------+
| Goroutine           | Leak; memory grows; scheduler pressure         |
| File descriptor     | "too many open files" (ulimit -n)              |
| DB connection       | Pool exhausted -> every other query waits      |
| HTTP connection     | Transport can't reuse it; new ones pile up     |
| Mutex / lock        | Other goroutines blocked behind the holder     |
| Memory buffers      | Request bodies, response bodies stay alive     |
| User / client       | Spinner forever, then a retry that doubles load|
+---------------------+------------------------------------------------+
```

---

## 3. Where timeouts live: the layers of a real system

A single user click crosses many layers. **Each layer has its own timeout**, and they must be
**nested correctly** (inner timeouts shorter than outer ones; see §18).

```text
   USER (browser / mobile)
        |   client-side timeout (fetch AbortController, SDK default): ~10-30s
        v
+--------------------------------------------------------------+
| CDN / WAF / Edge                                             |
|   origin response timeout, idle timeout                      |
+--------------------------------------------------------------+
        |
        v
+--------------------------------------------------------------+
| Load Balancer (ALB / NGINX / Envoy)                          |
|   idle timeout (e.g. 60s), upstream request timeout          |
+--------------------------------------------------------------+
        |
        v
+--------------------------------------------------------------+
| Go API Gateway / BFF                                         |
|   http.Server: ReadHeaderTimeout, ReadTimeout,               |
|                WriteTimeout, IdleTimeout                     |
|   per-request context.WithTimeout (the "budget")             |
+--------------------------------------------------------------+
        |
        v
+--------------------------------------------------------------+
| Go microservice (orders, payments, ...)                      |
|   http.Client / gRPC client deadlines                        |
|   context propagated from the inbound request                |
+--------------------------------------------------------------+
        |                |                 |
        v                v                 v
+---------------+ +---------------+ +--------------------------+
| PostgreSQL    | | Redis         | | 3rd-party API (Stripe,   |
| statement_    | | read/write    | | SMS, email, maps ...)    |
| timeout,      | | timeouts,     | | dial / TLS / header /    |
| ctx deadline  | | pool timeout  | | total timeouts           |
+---------------+ +---------------+ +--------------------------+
```

> **Rule of thumb:** every arrow in this diagram is a place where a timeout must be configured.
> If you can't point to the timeout for an arrow, that arrow is a latent outage.

---

## 4. Timeout vs Deadline vs Cancellation

These three words are used loosely. In Go they are precise and distinct.

```text
+--------------+--------------------------------------------+---------------------------+
| Concept      | Meaning                                    | Go representation         |
+--------------+--------------------------------------------+---------------------------+
| Timeout      | RELATIVE duration: "wait at most 2s"       | time.Duration,            |
|              |                                            | context.WithTimeout       |
| Deadline     | ABSOLUTE instant: "give up at 12:00:03.5"  | time.Time,                |
|              |                                            | context.WithDeadline,     |
|              |                                            | conn.SetDeadline          |
| Cancellation | EXPLICIT signal: "stop, I no longer care"  | context.WithCancel,       |
|              | (caller disconnected, sibling failed...)   | cancel(), ctx.Done()      |
+--------------+--------------------------------------------+---------------------------+
```

### Why deadlines are better than timeouts across layers

A **timeout** restarts its clock every time you apply it. A **deadline** is a fixed point in
time and is therefore *composable* across many calls:

```text
Timeouts (each call gets a fresh 1s):         Deadline (one shared clock):

 t=0          call A (1s)  -> t=0.9           t=0                      deadline t=1.0
 t=0.9        call B (1s)  -> t=1.8              call A -> t=0.4
 t=1.8        call C (1s)  -> t=2.7              call B -> t=0.8
                                                  call C -> only 0.2s left!
 total can reach 3s even though                 total can NEVER exceed 1.0s
 "the request timeout is 1s"
```

`context.WithTimeout(ctx, d)` is just sugar for `context.WithDeadline(ctx, time.Now().Add(d))`.
Once created, the context carries an **absolute deadline** that every downstream call can respect.
This is why Go uses `context.Context` as the universal carrier of "how long you have left".

### Cancellation is broader than timeout

A context is cancelled for *any* of these reasons:

```text
          +------------------------+
          |  ctx.Done() closes     |
          +------------------------+
             ^        ^         ^
             |        |         |
   deadline passed   cancel()   parent context cancelled
   (DeadlineExceeded) called    (Canceled or DeadlineExceeded)
                    (Canceled)
   e.g. client disconnected, sibling goroutine failed, server shutting down
```

---

## 5. Go's time primitives

Before `context`, there are the building blocks in package `time`. You still need them.

### 5.1 `time.Sleep`: not cancellable

```go
time.Sleep(5 * time.Second) // blocks 5s no matter what. Cannot be interrupted.
```

In production code that must honor cancellation, **do not use `time.Sleep`** in long waits.
Use a timer + `select`:

```go
func sleepCtx(ctx context.Context, d time.Duration) error {
    t := time.NewTimer(d)
    defer t.Stop()
    select {
    case <-t.C:
        return nil
    case <-ctx.Done():
        return ctx.Err()
    }
}
```

### 5.2 `time.After`: a channel that fires once

```go
select {
case res := <-resultCh:
    handle(res)
case <-time.After(2 * time.Second):
    return errors.New("timed out waiting for result")
}
```

This is the **simplest timeout pattern in Go**. Two possible outcomes race; the first wins.

```text
      resultCh            time.After(2s)
          |                     |
          v                     v
   +-------------------------------------+
   |               select                |
   +-------------------------------------+
          |                     |
          v                     v
   case 1: got result      case 2: timeout
          whichever is READY FIRST is chosen
```

**Gotcha (before Go 1.23):** a timer created by `time.After` is not garbage-collected until it
fires. In a hot loop, `time.After(1 * time.Hour)` creates millions of live timers = memory leak.
**Go 1.23+** fixed this *if your `go.mod` declares `go 1.23` or later* (unreferenced timers are
collectible even if not stopped). To be portable and explicit, prefer `time.NewTimer` + `Stop`
in loops.

### 5.3 `time.NewTimer`, `Stop`, `Reset`

```go
timer := time.NewTimer(3 * time.Second)
defer timer.Stop() // release resources if we return early

select {
case <-done:
    // finished in time
case <-timer.C:
    // timed out
}
```

* `timer.Stop()` returns `true` if it stopped the timer before it fired.
* `timer.Reset(d)` re-arms the timer.

**Pre-1.23 Reset dance** (needed to avoid a stale value in `t.C`):

```go
if !t.Stop() {
    select { case <-t.C: default: }   // drain a possibly-fired value
}
t.Reset(d)
```

**Go 1.23+ (go.mod go >= 1.23):** timer channels are synchronous (unbuffered-like); after
`Stop`/`Reset` no stale value can be received, so just call `t.Reset(d)`.

### 5.4 Idle-timeout pattern (reset on activity)

A very common real-world pattern: "close the connection if nothing happens for 30 s".

```go
func idleWatchdog(activity <-chan struct{}, idle time.Duration, onIdle func()) {
    t := time.NewTimer(idle)
    defer t.Stop()
    for {
        select {
        case <-activity:
            t.Reset(idle)      // activity seen: push the deadline out
        case <-t.C:
            onIdle()           // nothing for `idle`: act
            return
        }
    }
}
```

```text
 activity:   x      x   x                      (silence)
 time:   |---|------|---|------------------------|-------->
         0   reset  reset  reset                 idle expires -> onIdle()
```

### 5.5 `time.AfterFunc`: run a function after a delay

```go
t := time.AfterFunc(5*time.Second, func() {
    log.Println("operation took too long; cancelling")
    cancel()
})
defer t.Stop()
```

The callback runs in its **own goroutine**. Great for "if not done by X, trigger Y".

### 5.6 `time.Ticker`: periodic events (heartbeats, polling)

```go
tk := time.NewTicker(10 * time.Second)
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

Always `Stop()` a ticker. A forgotten ticker leaks.

### 5.7 Monotonic clock note

`time.Now()` contains a **monotonic clock reading**, and `time.Since` / `time.Until` /
timers use it, so timeouts are **not** affected by wall-clock changes (NTP jumps, manual clock
changes). Never implement timeouts by comparing wall-clock `UnixNano()` values.

---

## 6. The `context` package (the heart of Go timeouts)

### 6.1 The interface

```go
type Context interface {
    Deadline() (deadline time.Time, ok bool) // when will it expire (if ever)
    Done() <-chan struct{}                   // closed on cancel/timeout
    Err() error                              // why it ended (nil until Done closes)
    Value(key any) any                       // request-scoped values
}
```

### 6.2 Constructors

```text
+-------------------------------------+-------------------------------------------------+
| Function                            | Use                                             |
+-------------------------------------+-------------------------------------------------+
| context.Background()                | Root. main(), init, tests, top-level jobs       |
| context.TODO()                      | Placeholder when unsure (refactoring)           |
| context.WithCancel(parent)          | Manual cancellation                             |
| context.WithTimeout(parent, d)      | Cancel after duration d                         |
| context.WithDeadline(parent, t)     | Cancel at absolute time t                       |
| context.WithCancelCause(parent)     | Cancel with a custom error (Go 1.20)            |
| context.WithTimeoutCause(...)       | Timeout with custom cause error (Go 1.21)       |
| context.WithDeadlineCause(...)      | Deadline with custom cause (Go 1.21)            |
| context.WithoutCancel(parent)       | Keep values, DROP cancellation (Go 1.21)        |
| context.AfterFunc(ctx, f)           | Run f after ctx is done (Go 1.21)               |
| context.WithValue(parent, k, v)     | Attach request-scoped data                      |
+-------------------------------------+-------------------------------------------------+
```

### 6.3 The canonical timeout recipe

```go
func fetchUser(ctx context.Context, id string) (*User, error) {
    ctx, cancel := context.WithTimeout(ctx, 500*time.Millisecond)
    defer cancel() // ALWAYS. Releases the timer and unlinks from the parent.

    return repo.GetUser(ctx, id) // pass ctx down
}
```

**Why `defer cancel()` is mandatory:** even if the work finishes in 10 ms, the context's internal
timer and its registration in the parent's children set would live until the 500 ms expires
(or forever, for `WithCancel`). `go vet` (the `lostcancel` check) flags a missing cancel.

### 6.4 The context tree and propagation

Contexts form a **tree**. Cancelling a node cancels **all its descendants**, never its parents
or siblings.

```text
                        Background()
                             |
                  r.Context()   (HTTP request; cancelled if client disconnects
                             |    or server shuts the conn down)
                             |
              WithTimeout(3s)  <-- "request budget"
                  /          |             \
                 /           |              \
    WithTimeout(500ms) WithTimeout(1.5s)  WithCancel
      (DB query)       (payment call)     (background audit)
          |                  |                  |
      query goroutine    HTTP call           worker goroutines

 If the 3s budget expires or the client disconnects:
     ALL children are cancelled immediately.
 If the DB-query context (500ms) expires:
     only THAT subtree is cancelled; siblings keep running.
```

### 6.5 The deadline can only shrink, never grow

```go
parent, cancel := context.WithTimeout(context.Background(), 1*time.Second)
defer cancel()

child, cancel2 := context.WithTimeout(parent, 10*time.Second) // asks for 10s
defer cancel2()

dl, _ := child.Deadline()
fmt.Println(time.Until(dl)) // ~1s: the EARLIER deadline wins
```

A child can never outlive its parent. This is what makes budgets **safe** to nest.

### 6.6 Reading remaining time ("budget awareness")

```go
if dl, ok := ctx.Deadline(); ok {
    remaining := time.Until(dl)
    if remaining < 50*time.Millisecond {
        return ErrNotEnoughTime // fail fast rather than start doomed work
    }
}
```

Failing fast when the remaining budget is too small to finish is a powerful pattern: it avoids
doing wasted work for a caller that has already (or will have) given up.

### 6.7 `ctx.Err()` values

```text
+-----------------------------+-----------------------------------------------+
| ctx.Err()                   | Meaning                                       |
+-----------------------------+-----------------------------------------------+
| nil                         | Not done yet                                  |
| context.Canceled            | cancel() called / parent cancelled            |
| context.DeadlineExceeded    | The deadline/timeout passed                   |
+-----------------------------+-----------------------------------------------+
```

`context.DeadlineExceeded` implements `Timeout() bool` returning `true`, so it satisfies
`net.Error`-style checks.

### 6.8 Causes (Go 1.20+/1.21+): know *why*

```go
ctx, cancel := context.WithTimeoutCause(parent, 2*time.Second,
    errors.New("inventory service too slow"))
defer cancel()

<-ctx.Done()
fmt.Println(ctx.Err())            // context deadline exceeded
fmt.Println(context.Cause(ctx))   // inventory service too slow
```

`context.Cause` makes logs and error messages far more useful than a bare
"context deadline exceeded".

### 6.9 `context.WithoutCancel`: detach for cleanup/async work

Sometimes work **must** outlive the request (audit log write, publishing an event, rollback),
but should keep the request's values (trace IDs):

```go
func handler(w http.ResponseWriter, r *http.Request) {
    // ... main work ...

    // Detach from the request's cancellation, but give it its own bounded life:
    bg, cancel := context.WithTimeout(context.WithoutCancel(r.Context()), 5*time.Second)
    go func() {
        defer cancel()
        publishAuditEvent(bg, evt)
    }()
}
```

Note the *new timeout*: detached work still needs a bound.

### 6.10 `context.AfterFunc`: react to cancellation

```go
stop := context.AfterFunc(ctx, func() {
    conn.Close() // unblock a blocking Read/Write when ctx is done
})
defer stop()
```

This is the standard way to bridge a context to APIs that only understand "close it to
unblock" (many legacy or low-level libraries).

### 6.11 Context rules (from the Go docs / best practice)

```text
 1. ctx is the FIRST parameter, named ctx:      func Do(ctx context.Context, ...)
 2. Do NOT store a Context in a struct (pass it per call).   [exceptions: request-scoped types]
 3. Do NOT pass a nil Context. Use context.TODO() if unsure.
 4. ALWAYS call the cancel function (defer cancel()).
 5. Use WithValue only for request-scoped data crossing API boundaries
    (trace IDs, auth identity). NOT for optional function params.
 6. Contexts are immutable and safe for concurrent use.
```

---

## 7. Cooperative cancellation: why Go cannot kill goroutines

This is the **most important conceptual point** in the whole guide.

> **Go has no way to forcibly stop a goroutine.** A timeout *signals*; it does not *terminate*.
> The goroutine must **notice** and **return voluntarily**.

```text
  ctx timeout fires
        |
        v
  ctx.Done() channel is CLOSED  (a signal / a flag)
        |
        v
  Does the worker CHECK it?
        |
   +----+-----------------+
   |                      |
  YES                     NO
   |                      |
 returns promptly       keeps running: a LEAKED goroutine,
 (resources freed)      still using CPU / memory / connections
```

### 7.1 The leaky timeout (classic bug)

```go
// BAD: caller returns after 1s, but the goroutine blocks FOREVER on ch <- ...
func slowWithTimeout() (string, error) {
    ch := make(chan string)           // UNBUFFERED
    go func() {
        ch <- doSlowThing()           // nobody receives after timeout -> stuck forever
    }()
    select {
    case v := <-ch:
        return v, nil
    case <-time.After(1 * time.Second):
        return "", errors.New("timeout") // goroutine leaks!
    }
}
```

**Fix #1: buffered channel** so the sender never blocks:

```go
ch := make(chan string, 1) // capacity 1: the goroutine can always deposit and exit
```

**Fix #2 (better): make the work itself cancellable** by passing `ctx` and having it check
`ctx.Done()` / use context-aware APIs.

### 7.2 A reusable, leak-safe generic helper

```go
func DoWithTimeout[T any](
    ctx context.Context,
    d time.Duration,
    fn func(ctx context.Context) (T, error),
) (T, error) {
    ctx, cancel := context.WithTimeout(ctx, d)
    defer cancel()

    type result struct {
        v   T
        err error
    }
    ch := make(chan result, 1) // buffered: sender never blocks

    go func() {
        v, err := fn(ctx)
        ch <- result{v, err}
    }()

    select {
    case r := <-ch:
        return r.v, r.err
    case <-ctx.Done():
        var zero T
        return zero, ctx.Err()
    }
}
```

```text
        caller goroutine                         worker goroutine
        ----------------                         ----------------
        WithTimeout(d)
        go worker  ------------------------->    fn(ctx) running
        select {                                      |
          <-ch        <------(result)---------  ch <- result  (buffered, never blocks)
          <-ctx.Done()  (timeout)                     |
        }                                        fn sees ctx.Done()
        return                                   (if it is cancellable) and exits
```

> This helper bounds how long the **caller waits**. The worker still stops *only if* `fn`
> honors `ctx`. If `fn` ignores the context, it keeps running in the background until done.

### 7.3 Making your own loops cancellable

CPU-bound or polling loops must check the context themselves:

```go
func process(ctx context.Context, items []Item) error {
    for i, it := range items {
        // Cheap check; do it every iteration or every N iterations.
        select {
        case <-ctx.Done():
            return ctx.Err()
        default:
        }
        if err := handle(it); err != nil {
            return err
        }
        _ = i
    }
    return nil
}
```

Cheaper alternative in very hot loops: `if i%1024 == 0 && ctx.Err() != nil { return ctx.Err() }`.

### 7.4 Blocking operations that ignore context

Some blocking calls **cannot** observe a context: plain `net.Conn.Read`, `time.Sleep`,
`sync.Mutex.Lock`, `sync.WaitGroup.Wait`, `cgo` calls, many legacy libraries. Strategies:

```text
+-------------------------------+----------------------------------------------------+
| Blocking call                 | How to make it time-bounded                        |
+-------------------------------+----------------------------------------------------+
| conn.Read / conn.Write        | conn.SetReadDeadline / SetWriteDeadline            |
| time.Sleep                    | timer + select on ctx.Done()                       |
| sync.Mutex.Lock               | channel-based lock or TryLock polling (see §15)    |
| sync.WaitGroup.Wait           | wait in goroutine + select with timeout (see §15)  |
| Library without ctx support   | ctx.AfterFunc -> Close(); or wrap in goroutine     |
| os/exec process               | exec.CommandContext + WaitDelay (see §14)          |
+-------------------------------+----------------------------------------------------+
```

---

## 8. Network level: `net` deadlines and dialers

Everything in Go networking (HTTP, gRPC, DB drivers, Redis) bottoms out in `net.Conn`. Understanding
its deadline model explains how all higher-level timeouts really work.

### 8.1 Deadlines are ABSOLUTE and per-connection

```go
conn.SetDeadline(t time.Time)       // read + write
conn.SetReadDeadline(t time.Time)   // pending and future Reads
conn.SetWriteDeadline(t time.Time)  // pending and future Writes
```

Critical properties:

```text
 1. ABSOLUTE time (not a duration). A zero time.Time{} means "no deadline".
 2. Applies to ALL pending and FUTURE I/O on that connection until changed.
 3. It is NOT reset automatically after a successful Read/Write.
 4. On expiry, I/O returns an error where:
        errors.Is(err, os.ErrDeadlineExceeded) == true
        err.(net.Error).Timeout() == true
 5. After a timeout the connection is still usable if you set a new deadline
    (but the protocol state may be corrupted: a half-read frame).
```

### 8.2 The classic mistake: setting the deadline once

```go
// BAD: deadline set ONCE; after 5s EVERY later read fails, even if the peer is healthy.
conn.SetReadDeadline(time.Now().Add(5 * time.Second))
for {
    n, err := conn.Read(buf)  // fails permanently after the first 5s
    ...
}

// GOOD: slide the deadline before every read -> this is an IDLE timeout.
for {
    conn.SetReadDeadline(time.Now().Add(30 * time.Second))
    n, err := conn.Read(buf)
    if err != nil { return err }
    process(buf[:n])
}
```

```text
 BAD  (fixed deadline)                      GOOD (rolling deadline)

 deadline ---------------X                  read1 -> push deadline
 read read read read read | FAIL            read2 -> push deadline
 |-----------------------|                  read3 -> push deadline
 0                      5s                  ... silence for 30s -> FAIL
                                            (detects DEAD peers, not long-lived ones)
```

### 8.3 `net.Dialer`: connection establishment

```go
d := &net.Dialer{
    Timeout:   3 * time.Second,  // max time for DNS + TCP connect (per dial)
    KeepAlive: 30 * time.Second, // TCP keepalive probes on the connection
}
conn, err := d.DialContext(ctx, "tcp", "api.example.com:443")
```

* `Timeout` bounds the **connect** phase (including DNS resolution) for each attempt.
* If a hostname resolves to several IPs, Go tries them (Happy Eyeballs for dual-stack); the
  `Timeout`/`ctx` bound the whole dial.
* `ctx` cancellation also aborts the dial. Prefer `DialContext` always.
* `KeepAlive` is **not** an application timeout. It lets the OS detect a dead peer (no ACKs) on
  otherwise idle connections.

### 8.4 TCP-level reality (why app timeouts are needed)

```text
 Peer crashes / cable pulled / NAT drops state / firewall silently drops packets
                       |
                       v
 Your TCP stack keeps retransmitting for MINUTES (tcp_retries2 ~ 15 min default on Linux)
 or, for an idle connection with no keepalive, FOREVER.
                       |
                       v
 Without an app-level deadline, Read() just blocks. TCP will not save you.
```

---

## 9. HTTP client timeouts

### 9.1 The #1 production foot-gun

```go
http.Get("https://example.com")   // uses http.DefaultClient -> NO TIMEOUT AT ALL
```

`http.DefaultClient` has **no timeout**. A server that accepts the connection and never replies
will hang your goroutine forever. **Never use the default client in production.**

### 9.2 The anatomy of one HTTP request (and who times each phase)

```text
 Your code: client.Do(req)
        |
        v
+-----------------------------------------------------------+
| 1. DNS lookup + TCP connect                               |
|    -> Transport.DialContext (net.Dialer.Timeout)          |
+-----------------------------------------------------------+
        |
        v
+-----------------------------------------------------------+
| 2. TLS handshake (https)                                  |
|    -> Transport.TLSHandshakeTimeout                       |
+-----------------------------------------------------------+
        |
        v
+-----------------------------------------------------------+
| 3. Write request headers + body                           |
|    -> (bounded by overall Client.Timeout / ctx)           |
|    -> Transport.ExpectContinueTimeout (100-continue)      |
+-----------------------------------------------------------+
        |
        v
+-----------------------------------------------------------+
| 4. Wait for response HEADERS (time-to-first-byte)         |
|    -> Transport.ResponseHeaderTimeout                     |
+-----------------------------------------------------------+
        |
        v
+-----------------------------------------------------------+
| 5. Read response BODY (stream)                            |
|    -> bounded by Client.Timeout / ctx (NOT by headers)    |
+-----------------------------------------------------------+
        |
        v
+-----------------------------------------------------------+
| 6. Connection returns to idle pool (keep-alive)           |
|    -> Transport.IdleConnTimeout (then closed)             |
+-----------------------------------------------------------+

 http.Client.Timeout  = ONE clock covering steps 1 through 5 (incl. redirects
                        and reading the body).
 req.Context()        = the same idea per request, plus manual cancel.
```

### 9.3 `Client.Timeout`: overall limit

```go
client := &http.Client{Timeout: 10 * time.Second}
```

It covers **connection time, redirects, and reading the response body**. If you
`io.ReadAll(resp.Body)` and the timer fires mid-body, the read fails. For large downloads or
streaming (SSE, long polling), a short `Client.Timeout` will kill healthy transfers; use
per-phase transport timeouts and a context instead.

### 9.4 A production-grade client

```go
func NewHTTPClient() *http.Client {
    transport := &http.Transport{
        Proxy: http.ProxyFromEnvironment,

        DialContext: (&net.Dialer{
            Timeout:   3 * time.Second,  // connect (DNS+TCP)
            KeepAlive: 30 * time.Second,
        }).DialContext,

        TLSHandshakeTimeout:   3 * time.Second,
        ResponseHeaderTimeout: 5 * time.Second,  // time-to-first-byte after request is sent
        ExpectContinueTimeout: 1 * time.Second,

        // Connection pooling (reuse = big latency win; but bound it)
        MaxIdleConns:        100,
        MaxIdleConnsPerHost: 20,   // default is only 2! raise for busy upstreams
        MaxConnsPerHost:     0,    // 0 = unlimited; set to cap concurrency to a host
        IdleConnTimeout:     90 * time.Second,

        ForceAttemptHTTP2: true,
    }
    return &http.Client{
        Transport: transport,
        Timeout:   15 * time.Second, // safety net for the whole exchange
    }
}
```

Create **one** client and share it (clients and transports are safe for concurrent use and hold
the connection pool). Creating a new `http.Client{}`/`Transport` per request defeats pooling and
leaks idle connections.

### 9.5 Per-request deadline with context

```go
func callAPI(ctx context.Context, c *http.Client, url string) ([]byte, error) {
    ctx, cancel := context.WithTimeout(ctx, 2*time.Second) // tighter than the client default
    defer cancel()

    req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
    if err != nil {
        return nil, err
    }

    resp, err := c.Do(req)
    if err != nil {
        return nil, fmt.Errorf("call %s: %w", url, err)
    }
    defer resp.Body.Close()                      // ALWAYS close the body

    if resp.StatusCode >= 500 {
        io.Copy(io.Discard, io.LimitReader(resp.Body, 1<<20)) // drain for connection reuse
        return nil, fmt.Errorf("upstream status %d", resp.StatusCode)
    }
    return io.ReadAll(io.LimitReader(resp.Body, 10<<20))     // cap size too!
}
```

Per-request `ctx` is **more flexible** than `Client.Timeout`: the effective limit is the
*minimum* of ctx deadline and `Client.Timeout`, and the ctx can also be cancelled manually or by
the parent.

### 9.6 Body handling and timeouts

* The deadline stays in force while you read the body. If you pass the response to another
  goroutine, the context must stay alive until the body is fully consumed.
* Do **not** `defer cancel()` and then return `resp` to the caller: the body read would break.
* Always close the body, and drain it if you want connection reuse.

### 9.7 Retry safety

A timeout after the request was *sent* means **you do not know whether the server executed it**.
Only retry automatically if the operation is **idempotent** (GET, PUT, DELETE) or protected
by an **idempotency key** (see payment example in §17).

---

## 10. HTTP server timeouts

### 10.1 The #2 production foot-gun

```go
http.ListenAndServe(":8080", mux) // http.Server with ZERO timeouts
```

A server with no timeouts is vulnerable to **Slowloris**-style attacks (open connections and
trickle bytes forever) and to leaking resources from broken clients.

### 10.2 The four server timeouts: when each clock runs

```text
 Connection accepted
 |
 |   <------ ReadHeaderTimeout ------>
 |   (time allowed to read request headers)
 |
 |   <-------------- ReadTimeout ---------------->
 |   (headers + body: whole request must be read)
 |
 |                                   <------------ WriteTimeout ------------->
 |                                   (from end of request-header read
 |                                    until the response is fully written)
 |
 v
 +-----------+   +--------------+   +------------------+   +-----------------+
 | read      |-->| handler runs |-->| response written |-->| keep-alive idle |
 | request   |   | (your logic) |   |                  |   | waiting next req|
 +-----------+   +--------------+   +------------------+   +-----------------+
                                                             <--- IdleTimeout --->
                                                             (time between requests
                                                              on a keep-alive conn)
```

```text
+--------------------+----------------------------------------------------------------+
| Field              | Meaning / guidance                                             |
+--------------------+----------------------------------------------------------------+
| ReadHeaderTimeout  | Max time to read request headers. SET THIS (e.g. 5s). Stops    |
|                    | Slowloris. Lets handlers choose their own body deadlines.      |
| ReadTimeout        | Max time to read the ENTIRE request incl. body. Simple but     |
|                    | blunt: hurts large uploads. Falls back for the two below if 0. |
| WriteTimeout       | Max time from request read end to response write end. Includes |
|                    | handler time! Must exceed your slowest handler. Hurts          |
|                    | streaming/SSE/large downloads (use ResponseController).        |
| IdleTimeout        | Max wait for the next request on a keep-alive conn. If 0,      |
|                    | uses ReadTimeout. Set explicitly (e.g. 60-120s, > LB idle).    |
| MaxHeaderBytes     | Cap header size (default 1 MiB).                               |
+--------------------+----------------------------------------------------------------+
```

### 10.3 A production-grade server

```go
srv := &http.Server{
    Addr:              ":8080",
    Handler:           mux,
    ReadHeaderTimeout: 5 * time.Second,
    ReadTimeout:       15 * time.Second,
    WriteTimeout:      30 * time.Second, // > slowest handler budget
    IdleTimeout:       120 * time.Second,
    MaxHeaderBytes:    1 << 20,
}
```

### 10.4 Per-request budget inside handlers

`WriteTimeout` is a blunt socket-level kill. A **context deadline** inside the handler is the
graceful, composable way to bound work:

```go
func (h *Handler) GetOrder(w http.ResponseWriter, r *http.Request) {
    // r.Context() is cancelled automatically if the client disconnects
    // or the server connection is closed/shut down.
    ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
    defer cancel()

    order, err := h.svc.Get(ctx, r.PathValue("id"))
    switch {
    case err == nil:
        json.NewEncoder(w).Encode(order)
    case errors.Is(err, context.DeadlineExceeded):
        http.Error(w, "upstream timeout", http.StatusGatewayTimeout)  // 504
    case errors.Is(err, context.Canceled):
        // client went away; nobody is listening. Just stop (log at debug).
    default:
        http.Error(w, "internal error", http.StatusInternalServerError)
    }
}
```

### 10.5 `http.TimeoutHandler`

```go
mux.Handle("/report", http.TimeoutHandler(reportHandler, 5*time.Second, "request timed out"))
```

* Runs the handler with a context that has the deadline and returns **503** with your message
  if it doesn't finish.
* Caveats: it buffers the response, doesn't support `Flusher`/`Hijacker` (no streaming or
  WebSockets), and the handler goroutine keeps running if it ignores the context.

### 10.6 `http.ResponseController` (Go 1.20+): per-request deadlines

Server-wide `ReadTimeout`/`WriteTimeout` are too coarse (an upload endpoint needs minutes,
a health check needs milliseconds). Override per request:

```go
func upload(w http.ResponseWriter, r *http.Request) {
    rc := http.NewResponseController(w)
    // This endpoint legitimately needs up to 5 minutes to receive the body.
    _ = rc.SetReadDeadline(time.Now().Add(5 * time.Minute))
    _ = rc.SetWriteDeadline(time.Now().Add(5 * time.Minute))
    ...
}

func sse(w http.ResponseWriter, r *http.Request) {
    rc := http.NewResponseController(w)
    for {
        _ = rc.SetWriteDeadline(time.Now().Add(10 * time.Second)) // rolling: per event
        fmt.Fprintf(w, "data: %s\n\n", next())
        if err := rc.Flush(); err != nil { return }
        ...
    }
}
```

### 10.7 Graceful shutdown (a timeout in its own right)

```go
func run() error {
    ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
    defer stop()

    srv := &http.Server{ /* ...timeouts... */ }
    errCh := make(chan error, 1)
    go func() { errCh <- srv.ListenAndServe() }()

    select {
    case err := <-errCh:
        return err
    case <-ctx.Done(): // SIGTERM received (e.g. Kubernetes)
    }

    // Stop accepting new conns, let in-flight requests finish, but NOT forever.
    shutdownCtx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
    defer cancel()
    if err := srv.Shutdown(shutdownCtx); err != nil {
        _ = srv.Close() // force-close what's left
        return err
    }
    return nil
}
```

```text
 SIGTERM
    |
    v
 stop accepting new connections  (readiness probe fails -> LB stops routing)
    |
    v
 wait for in-flight requests ---- up to shutdown timeout (e.g. 20s) ----+
    |                                                                    |
    v (all done)                                                         v (still busy)
 exit 0                                              srv.Close(): force-close, exit
                                                     (must finish BEFORE the platform's
                                                      hard SIGKILL, e.g. K8s
                                                      terminationGracePeriodSeconds)
```

---

## 11. Databases (`database/sql`) and connection pools

### 11.1 Always use the `...Context` methods

```go
ctx, cancel := context.WithTimeout(ctx, 300*time.Millisecond)
defer cancel()

var name string
err := db.QueryRowContext(ctx, `SELECT name FROM users WHERE id = $1`, id).Scan(&name)
```

`Query`, `Exec`, `QueryRow` without Context have **no timeout** (they use `context.Background()`).
With a ctx, a cancelled context makes the driver abort the in-flight query (e.g. Postgres drivers
send a cancel request or close the connection).

### 11.2 Where does the time go? (pool wait + query)

```text
 db.QueryContext(ctx, ...)
        |
        v
+-----------------------------------------------------------+
| 1. WAIT for a free connection from the pool               |
|    (blocked if MaxOpenConns reached)                      |
|    -> bounded ONLY by ctx (there is no separate setting)  |
+-----------------------------------------------------------+
        |
        v
+-----------------------------------------------------------+
| 2. (maybe) open a NEW connection                          |
|    -> driver connect_timeout / dial timeout               |
+-----------------------------------------------------------+
        |
        v
+-----------------------------------------------------------+
| 3. Execute the query on the server                        |
|    -> ctx deadline  +  server-side statement_timeout      |
+-----------------------------------------------------------+
        |
        v
+-----------------------------------------------------------+
| 4. Stream rows back (rows.Next loop)                      |
|    -> ctx still applies; always rows.Close()              |
+-----------------------------------------------------------+
        |
        v
 connection returned to pool
```

```text
 Pool exhaustion, the silent killer:

   MaxOpenConns = 10
   +----------------------------------------------+
   | conn1 conn2 conn3 ... conn10   all BUSY      |
   +----------------------------------------------+
        ^          (10 slow queries hold every connection)
        | request #11, #12, #13 ... WAIT for a connection
        | (every waiting request burns its ctx budget doing NOTHING)
        |
   With no ctx deadline they wait FOREVER. With a deadline they fail fast
   with DeadlineExceeded and you can see saturation in metrics (db.Stats().WaitCount).
```

### 11.3 Pool configuration (these are *lifetimes*, not request timeouts)

```go
db.SetMaxOpenConns(25)                  // cap concurrency against the DB
db.SetMaxIdleConns(25)                  // keep warm connections
db.SetConnMaxLifetime(30 * time.Minute) // recycle: survives LB/DNS/failover changes
db.SetConnMaxIdleTime(5 * time.Minute)  // drop idle conns before middleboxes do
```

Set `ConnMaxLifetime`/`ConnMaxIdleTime` **below** any firewall / NAT / proxy idle timeout (e.g.
AWS NLB 350 s, many corporate firewalls 5-15 min); otherwise you get "broken pipe" /
"connection reset" on the first query after a quiet period.

### 11.4 Server-side timeouts (defence in depth)

The client context stops *you* waiting. Make sure the **database also stops working**:

```sql
-- PostgreSQL
SET statement_timeout = '2s';                    -- kill queries running longer
SET lock_timeout = '500ms';                      -- don't wait forever on locks
SET idle_in_transaction_session_timeout = '30s'; -- kill forgotten open transactions
```

```text
 DSN example (pgx):
   postgres://app@db/shop?connect_timeout=3&options=-c%20statement_timeout%3D2000
```

MySQL: `max_execution_time` (SELECT), `innodb_lock_wait_timeout`, DSN `timeout`,
`readTimeout`, `writeTimeout`.

### 11.5 Transactions and timeouts

```go
tx, err := db.BeginTx(ctx, nil) // ctx cancel => tx is rolled back automatically
if err != nil { return err }
defer tx.Rollback()             // safe no-op after Commit

if _, err := tx.ExecContext(ctx, `UPDATE accounts SET bal = bal - $1 WHERE id = $2`, amt, from); err != nil {
    return err
}
if _, err := tx.ExecContext(ctx, `UPDATE accounts SET bal = bal + $1 WHERE id = $2`, amt, to); err != nil {
    return err
}
return tx.Commit()
```

**Keep transactions short.** A long transaction holds locks; a timeout mid-transaction just
rolls back; but do **not** make network calls to third parties *inside* a transaction (the lock
is held for the entire duration of the slowest external call).

---

## 12. gRPC deadlines and propagation

gRPC has first-class **deadline propagation**, the most elegant implementation of the budget idea.

### 12.1 Client side

```go
conn, err := grpc.NewClient("inventory:50051",
    grpc.WithTransportCredentials(creds),
)
client := pb.NewInventoryClient(conn)

ctx, cancel := context.WithTimeout(ctx, 300*time.Millisecond)
defer cancel()
resp, err := client.Reserve(ctx, &pb.ReserveReq{Sku: "A1", Qty: 2})
if status.Code(err) == codes.DeadlineExceeded {
    // timed out
}
```

If no deadline is set on a gRPC call, it can wait **forever**. Always set one.

### 12.2 How propagation works

```text
 Client                          Server A                         Server B
 ------                          --------                         --------
 ctx deadline = now+1000ms
 header: grpc-timeout: 1000m --> receives; builds ctx with
                                 deadline ~ now+990ms (minus latency)
                                 does 200ms of work
                                 calls B with the SAME ctx -----> header: grpc-timeout: ~780m
                                                                  ctx deadline ~ now+780ms
                                                                  if A's deadline passes, B's
                                                                  ctx is cancelled too

     deadline travels as RELATIVE remaining time in the grpc-timeout header
     and is re-materialised as an absolute deadline at each hop.
```

As long as you **pass the incoming `ctx` to outgoing calls**, the whole call chain shares one
shrinking budget, and when the originator gives up, every hop stops working (no wasted work).

### 12.3 Server side

```go
func (s *server) Reserve(ctx context.Context, in *pb.ReserveReq) (*pb.ReserveResp, error) {
    if dl, ok := ctx.Deadline(); ok && time.Until(dl) < 20*time.Millisecond {
        return nil, status.Error(codes.DeadlineExceeded, "not enough time left")
    }
    if err := s.repo.Reserve(ctx, in.Sku, in.Qty); err != nil { // ctx passed to DB
        return nil, err
    }
    return &pb.ReserveResp{}, nil
}
```

### 12.4 Default timeouts via interceptor (guard rail for forgetful callers)

```go
func defaultTimeout(d time.Duration) grpc.UnaryClientInterceptor {
    return func(ctx context.Context, method string, req, reply any,
        cc *grpc.ClientConn, invoker grpc.UnaryInvoker, opts ...grpc.CallOption) error {

        if _, has := ctx.Deadline(); !has {          // only if caller set none
            var cancel context.CancelFunc
            ctx, cancel = context.WithTimeout(ctx, d)
            defer cancel()
        }
        return invoker(ctx, method, req, reply, cc, opts...)
    }
}
// grpc.NewClient(addr, grpc.WithChainUnaryInterceptor(defaultTimeout(2*time.Second)))
```

### 12.5 Keepalive (dead-peer detection for long-lived streams)

```go
grpc.WithKeepaliveParams(keepalive.ClientParameters{
    Time:                30 * time.Second, // ping if idle this long
    Timeout:             10 * time.Second, // wait this long for the ping ack
    PermitWithoutStream: true,
})
```

Servers enforce policy (`keepalive.EnforcementPolicy`) and will `GOAWAY` clients that ping too
aggressively; tune both sides together.

---

## 13. Redis, queues and other clients

Almost every client library exposes the **same four knobs**. Learn the pattern once:

```text
+-------------------+------------------------------------------------------------+
| Knob              | Equivalent                                                 |
+-------------------+------------------------------------------------------------+
| Dial/Connect      | max time to establish the connection                       |
| Read              | max time waiting for a response                            |
| Write             | max time to send a command                                 |
| Pool/Acquire      | max time waiting for a free pooled connection              |
| (+ ctx)           | per-call deadline, honored by modern clients               |
+-------------------+------------------------------------------------------------+
```

```go
// go-redis v9 (verify option names/defaults for your version)
rdb := redis.NewClient(&redis.Options{
    Addr:         "redis:6379",
    DialTimeout:  2 * time.Second,
    ReadTimeout:  200 * time.Millisecond, // a cache that is slow is WORSE than no cache
    WriteTimeout: 200 * time.Millisecond,
    PoolTimeout:  300 * time.Millisecond, // wait for a free conn
    PoolSize:     50,
})

ctx, cancel := context.WithTimeout(ctx, 150*time.Millisecond)
defer cancel()
val, err := rdb.Get(ctx, "user:42").Result()
if errors.Is(err, redis.Nil) { /* miss */ } else if err != nil { /* treat as miss, go to DB */ }
```

**Caching principle:** cache timeouts should be **much tighter** than the source of truth's.
If Redis is slow, skip it and hit the database; never let the optimization become the outage.

Other systems, same ideas:

```text
 Kafka consumer   : session.timeout.ms, max.poll.interval.ms, request.timeout.ms
                    (heartbeat-based liveness: if you don't poll in time you are
                     kicked out of the group -> rebalance)
 RabbitMQ/AMQP    : connection heartbeat, publisher confirm timeout, consumer ack timeout
 SQS              : visibility timeout (a LEASE: finish before it ends or the
                    message is redelivered to someone else)
 S3 / object store: per-request ctx, multipart upload part timeouts
 SMTP / SMS / push: provider API call = http.Client rules (§9)
```

---

## 14. Processes: `os/exec`

```go
ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
defer cancel()

cmd := exec.CommandContext(ctx, "ffmpeg", "-i", in, out)
cmd.Cancel = func() error {                 // Go 1.20+: how to cancel (default: Kill)
    return cmd.Process.Signal(syscall.SIGTERM) // ask nicely first
}
cmd.WaitDelay = 3 * time.Second             // then force-kill + close pipes after 3s

out, err := cmd.CombinedOutput()
if errors.Is(ctx.Err(), context.DeadlineExceeded) {
    return fmt.Errorf("ffmpeg timed out: %w", err)
}
```

```text
 t=0 ........................ t=10s (ctx deadline)  ....... t=13s (WaitDelay)
 process runs                 cmd.Cancel() -> SIGTERM         Kill -9 + close pipes
                              process gets a chance to        Wait() returns regardless
                              clean up temp files             (no hang on stuck children
                                                               that inherited stdout)
```

`WaitDelay` fixes the notorious hang where a killed process left a child holding the stdout
pipe open, so `Wait()` blocked forever. Set it whenever you capture output.

---

## 15. Concurrency primitives with timeouts

Go's synchronization primitives mostly **don't take a timeout**. Here is how to add one properly.

### 15.1 Channels: send/receive with timeout

```go
// Receive with timeout
select {
case v := <-ch:
    use(v)
case <-ctx.Done():
    return ctx.Err()
}

// Send with timeout (backpressure!)
select {
case queue <- job:
    // accepted
case <-ctx.Done():
    return ctx.Err()
default:
    // alternative: non-blocking, shed load immediately
    return ErrQueueFull
}
```

### 15.2 `sync.Mutex`: no timeout; use a channel-based lock when you need one

`Mutex.Lock()` blocks indefinitely. `TryLock()` (Go 1.18) never blocks. For a bounded wait:

```go
type TimedMutex struct{ ch chan struct{} }

func NewTimedMutex() *TimedMutex { return &TimedMutex{ch: make(chan struct{}, 1)} }

func (m *TimedMutex) Lock(ctx context.Context) error {
    select {
    case m.ch <- struct{}{}:
        return nil
    case <-ctx.Done():
        return ctx.Err()
    }
}
func (m *TimedMutex) Unlock() { <-m.ch }
```

Design smell check: if you need a *timeout* on an in-process mutex, the critical section is
probably doing I/O. Shrink the critical section instead.

### 15.3 `sync.WaitGroup`: no timeout

```go
func waitTimeout(wg *sync.WaitGroup, d time.Duration) bool {
    done := make(chan struct{})
    go func() { wg.Wait(); close(done) }()

    t := time.NewTimer(d)
    defer t.Stop()
    select {
    case <-done:
        return true  // all finished
    case <-t.C:
        return false // timed out (the helper goroutine lingers until wg completes)
    }
}
```

Typical use: bounded wait for background workers during shutdown.

### 15.4 `errgroup`: fan-out with shared context, first error cancels all

```go
import "golang.org/x/sync/errgroup"

func loadDashboard(ctx context.Context, uid string) (*Dashboard, error) {
    ctx, cancel := context.WithTimeout(ctx, 800*time.Millisecond) // shared budget
    defer cancel()

    g, ctx := errgroup.WithContext(ctx) // ctx is cancelled when any task errors
    g.SetLimit(8)                        // bound concurrency

    var d Dashboard
    g.Go(func() (err error) { d.Profile, err = profileSvc.Get(ctx, uid); return })
    g.Go(func() (err error) { d.Orders, err = orderSvc.Recent(ctx, uid);  return })
    g.Go(func() (err error) { d.Recs, err = recSvc.For(ctx, uid);         return })

    if err := g.Wait(); err != nil {
        return nil, err
    }
    return &d, nil
}
```

```text
                       WithTimeout(800ms)
                              |
                     errgroup.WithContext
              /               |               \
     profileSvc(ctx)    orderSvc(ctx)     recSvc(ctx)
          |                   |                |
          +---- any error OR timeout => ctx cancelled => the others stop early

   latency = MAX(children), not SUM(children)   <- why fan-out is worthwhile
```

**Partial results (graceful degradation):** if recommendations are optional, don't return their
error from the group function; log it and continue so the page still renders.

### 15.5 `semaphore.Weighted`: bounded concurrency with a context-aware acquire

```go
import "golang.org/x/sync/semaphore"

sem := semaphore.NewWeighted(20)

func handle(ctx context.Context) error {
    if err := sem.Acquire(ctx, 1); err != nil { // waits, but honors ctx deadline
        return err                              // shed load rather than queue forever
    }
    defer sem.Release(1)
    return doWork(ctx)
}
```

This is a **bulkhead**: it caps how much of your capacity one dependency can consume.

### 15.6 Worker pool with a per-job timeout

```go
func worker(ctx context.Context, jobs <-chan Job, results chan<- Result) {
    for {
        select {
        case <-ctx.Done():
            return
        case j, ok := <-jobs:
            if !ok {
                return
            }
            jctx, cancel := context.WithTimeout(ctx, 30*time.Second) // per-job bound
            res, err := j.Run(jctx)
            cancel() // NOT defer: we're inside a loop
            results <- Result{Job: j, Val: res, Err: err}
        }
    }
}
```

> **Never `defer cancel()` inside a loop**: the defers pile up until the function returns. Call
> `cancel()` explicitly at the end of each iteration (or factor the body into its own function).

---

## 16. Resilience patterns built on timeouts

A timeout alone says "stop waiting". These patterns decide **what to do next**.

### 16.1 Retries: per-attempt timeout inside an overall deadline

```go
func Retry(
    ctx context.Context,
    maxAttempts int,
    perAttempt time.Duration,
    baseBackoff time.Duration,
    fn func(ctx context.Context) error,
) error {
    var err error
    for attempt := 0; attempt < maxAttempts; attempt++ {
        actx, cancel := context.WithTimeout(ctx, perAttempt) // child of overall ctx
        err = fn(actx)
        cancel()
        if err == nil {
            return nil
        }
        if !retryable(err) || ctx.Err() != nil {
            return err // permanent error, or the overall budget is gone
        }

        // Exponential backoff with FULL jitter: sleep random(0, base * 2^attempt)
        maxSleep := baseBackoff << attempt
        sleep := time.Duration(rand.Int63n(int64(maxSleep)))
        t := time.NewTimer(sleep)
        select {
        case <-t.C:
        case <-ctx.Done():
            t.Stop()
            return ctx.Err()
        }
    }
    return fmt.Errorf("after %d attempts: %w", maxAttempts, err)
}
```

```text
 overall deadline (ctx) ===================================================>|
 |                                                                          |
 | attempt 1        backoff   attempt 2         backoff     attempt 3       |
 |[== 300ms ==X]....(rand)....[== 300ms ==X].....(rand).....[== 300ms ==]   |
 |  per-attempt timeout          per-attempt timeout          succeeds      |
 |                                                                          |
 + overall ctx always wins: no matter how many retries remain, total time
   never exceeds the deadline the caller gave you.
```

**Why jitter?** Without it, thousands of clients that failed together retry together (the
"thundering herd") and re-crash the recovering service.

```text
 No jitter:   fail fail fail -> retry @1s retry @1s retry @1s  ==> synchronized spike
 With jitter: fail fail fail -> retry @0.3s  @0.9s  @0.6s      ==> smoothed load
```

**Retry only when safe:** idempotent operations, transient errors (timeouts, 502/503/504, 429
with `Retry-After`, connection reset), never on 4xx client errors.

### 16.2 The retry amplification trap

```text
 Each layer retries 3x  ->  failures multiply:

   Client --(3 tries)--> Gateway --(3 tries)--> Service --(3 tries)--> DB
                                                       
   1 user action  =  3 x 3 x 3  =  27 requests hitting the struggling DB.
```

Mitigations: retry at **one layer only** (usually the edge/outermost caller), use **retry
budgets** (e.g. retries <= 10% of requests), honor deadlines so retries stop when the budget is
gone, and use circuit breakers.

### 16.3 Circuit breaker

Stop calling a dependency that is clearly failing/timing out; fail instantly instead of waiting
the full timeout on every request.

```text
                  failures >= threshold
      +--------+ ----------------------> +--------+
      | CLOSED |                         |  OPEN  |
      | (calls |                         | (fail  |
      |  flow) | <----+                  |  fast) |
      +--------+      |                  +--------+
          ^           |                      |
          |           | trial call           | after cooldown
          |           | succeeds             v
          |           |              +-------------+
          |           +------------- | HALF-OPEN   |
          |                          | (let a few  |
          +--------------------------|  probe calls|
              (trial fails -> back   |  through)   |
               to OPEN)              +-------------+
```

A minimal sketch (in production use a battle-tested library such as `sony/gobreaker`):

```go
type Breaker struct {
    mu        sync.Mutex
    failures  int
    threshold int
    cooldown  time.Duration
    openUntil time.Time
}

var ErrOpen = errors.New("circuit open")

func (b *Breaker) Do(fn func() error) error {
    b.mu.Lock()
    if time.Now().Before(b.openUntil) {
        b.mu.Unlock()
        return ErrOpen // fail fast: no waiting, no timeout burned
    }
    b.mu.Unlock()

    err := fn()

    b.mu.Lock()
    defer b.mu.Unlock()
    if err != nil {
        b.failures++
        if b.failures >= b.threshold {
            b.openUntil = time.Now().Add(b.cooldown) // trip
            b.failures = 0
        }
        return err
    }
    b.failures = 0
    return nil
}
```

Timeouts feed breakers: **a timeout is a failure signal**. Breaker + timeout together turn a
"30 s hang per request" into "instant failure while the dependency recovers".

### 16.4 Fallbacks and graceful degradation

```go
func getRecommendations(ctx context.Context, uid string) []Item {
    rctx, cancel := context.WithTimeout(ctx, 150*time.Millisecond)
    defer cancel()

    items, err := recSvc.For(rctx, uid)
    if err != nil {
        metrics.Inc("recs_fallback")
        return cachedPopularItems() // degrade: page still renders
    }
    return items
}
```

Decide per dependency: **critical** (fail the request) vs **optional** (fallback/skip).

### 16.5 Hedged requests (tail-latency killer)

Send a second request if the first hasn't answered within, say, the p95 latency. Use the first
reply, cancel the other. Only for **idempotent**, **read** operations.

```go
func Hedged(ctx context.Context, delay time.Duration,
    fn func(ctx context.Context) (string, error)) (string, error) {

    ctx, cancel := context.WithCancel(ctx)
    defer cancel() // cancels the loser(s)

    type res struct {
        v   string
        err error
    }
    ch := make(chan res, 2) // buffered: losers never block
    launch := func() {
        v, err := fn(ctx)
        ch <- res{v, err}
    }

    go launch()
    pending := 1
    t := time.NewTimer(delay)
    defer t.Stop()

    var lastErr error
    for {
        select {
        case <-t.C:
            if pending == 1 {
                pending++
                go launch() // hedge
            }
        case r := <-ch:
            if r.err == nil {
                return r.v, nil
            }
            lastErr = r.err
            pending--
            if pending == 0 {
                return "", lastErr
            }
        case <-ctx.Done():
            return "", ctx.Err()
        }
    }
}
```

```text
 t=0      primary request sent  ------------------------------X (slow replica, 900ms)
 t=95ms   (p95 reached)  hedge request sent -----> OK at t=120ms  <- we use this
 t=120ms  cancel(primary)   total latency = 120ms instead of 900ms
 cost: ~5% extra requests    benefit: dramatically better p99
```

### 16.6 Load shedding and backpressure

When overloaded, reject early rather than queue until everything times out:

```go
sem := make(chan struct{}, 200) // max concurrent requests

func shed(next http.Handler) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        select {
        case sem <- struct{}{}:
            defer func() { <-sem }()
            next.ServeHTTP(w, r)
        default:
            w.Header().Set("Retry-After", "1")
            http.Error(w, "overloaded", http.StatusServiceUnavailable) // fast 503
        }
    })
}
```

A fast 503 is **kinder** than a slow timeout: the client learns instantly and can back off,
while your service stays responsive for the requests it does accept.

### 16.7 Deadline budgeting across sequential calls

Give each step a **slice** of the remaining budget; keep a reserve for your own post-processing:

```go
func checkout(ctx context.Context, o Order) error {
    ctx, cancel := context.WithTimeout(ctx, 2500*time.Millisecond) // total
    defer cancel()

    if err := withSlice(ctx, 300*time.Millisecond, func(c context.Context) error {
        return inventory.Reserve(c, o)
    }); err != nil { return err }

    if err := withSlice(ctx, 1500*time.Millisecond, func(c context.Context) error {
        return payment.Charge(c, o)
    }); err != nil {
        _ = compensate(context.WithoutCancel(ctx), o) // release inventory even if ctx is dying
        return err
    }
    return nil
}

// A slice can never exceed what's left in the parent (child deadline <= parent deadline).
func withSlice(ctx context.Context, d time.Duration, f func(context.Context) error) error {
    c, cancel := context.WithTimeout(ctx, d)
    defer cancel()
    return f(c)
}
```

Note `compensate` runs on a **detached** context (`WithoutCancel`, ideally with its own
timeout) because the original ctx may already be expired exactly when you need to clean up.

### 16.8 Heartbeats, leases and TTLs

Timeouts also implement **liveness detection in distributed systems**:

```text
 Worker  -- heartbeat every 5s --> Coordinator
 Coordinator: "no heartbeat for 15s  =>  presume worker dead  =>  reassign its work"

 Lock/lease (Redis SET NX PX, etcd lease, DB row with expires_at):
   holder must RENEW before TTL; if it crashes, TTL expiry frees the lock
   (otherwise a crashed holder would block everyone forever).
```

Choose TTL > (heartbeat interval x 3) to tolerate jitter and GC pauses; and design for the
case where a "dead" worker is merely slow and comes back (use fencing tokens).

---

## 17. Real-world architectures and use cases

### 17.1 Reference architecture: e-commerce checkout

This is a realistic request path with **every timeout labelled**. Read it top-to-bottom as a
single click on "Place order".

```text
   USER taps "Place order"
        |  client SDK timeout: 10s
        v
+----------------------------------------------------------------------+
| CDN / WAF                                                            |
|   origin_read_timeout = 30s                                          |
+----------------------------------------------------------------------+
        |
        v
+----------------------------------------------------------------------+
| Load Balancer (ALB / Envoy)                                          |
|   idle_timeout = 60s   upstream_request_timeout = 9s                 |
+----------------------------------------------------------------------+
        |
        v
+----------------------------------------------------------------------+
| Go API Gateway   (http.Server)                                       |
|   ReadHeaderTimeout = 5s   IdleTimeout = 120s (> LB 60s)             |
|   WriteTimeout = 12s                                                 |
|   handler: ctx = WithTimeout(r.Context(), 8s)    <-- request budget  |
+----------------------------------------------------------------------+
        |  gRPC  (deadline propagated: ctx has <= 8s left)
        v
+----------------------------------------------------------------------+
| Order Service (Go)                                                   |
|   ctx deadline inherited, sliced per dependency (see below)          |
|   circuit breaker per dependency, bulkhead semaphore per dependency  |
+----------------------------------------------------------------------+
        |                  |                    |
        | 300ms            | 5s                 | 200ms
        v                  v                    v
+----------------+ +--------------------+ +----------------------+
| Inventory svc  | | Payment svc        | | PostgreSQL           |
| gRPC deadline  | | -> PSP (Stripe...) | | ctx 200ms            |
| 300ms          | |   http.Client:     | | statement_timeout    |
| + Redis cache  | |   dial 2s, TLS 2s, | |   = 500ms (backstop) |
|   read 100ms   | |   headers 4s,      | | pool: MaxOpen=25     |
|                | |   total 5s         | |                      |
|                | | idempotency key    | |                      |
+----------------+ +--------------------+ +----------------------+
                           |
                           | async (NOT in the request path)
                           v
                  +--------------------+
                  | Message queue      |
                  | publish timeout 1s |
                  +--------------------+
                           |
                           v
          email / SMS / analytics workers (own retries,
          own timeouts, visibility timeout = lease)
```

**Why this shape works:**

```text
 1. Budgets are NESTED:  client 10s > LB 9s > gateway ctx 8s > order svc slices > leaf calls.
    Each inner layer gives up BEFORE the outer one, so the outer layer can still return a
    clean error instead of being cut off mid-flight.
 2. Slow-but-optional work (emails, analytics) is moved OFF the request path (queue).
 3. Every dependency has: timeout + breaker + bulkhead + (where possible) fallback.
 4. The DB has a server-side statement_timeout as a safety net behind the client ctx.
 5. IdleTimeout of the Go server (120s) is longer than the LB's idle (60s) so the LB,
    not the server, closes idle connections first (avoids racey "connection reset").
```

### 17.2 The timeout budget, drawn to scale

Sequential steps inside the Order Service, with `1 character = 50 ms`:

```text
1 char = 100ms
0                                                                         8000ms
|------------------------------------------------------------------------------|
[=====================gateway request budget: ctx = 8000ms=====================]
 [============order-service (inherits deadline, ~7500ms usable)============]
  [=]  1) inventory.Reserve   300ms slice (typical 20ms)
     [===2) payment.Charge  5000ms slice (PSP call)===]
                                                       []  3) db insert order  200ms
                                                         <---- ~1900ms slack ---->
                                                         (serialisation, logging, retries,
                                                          compensation, scheduling jitter)
```

Notice the **slack** at the end: the gap between the inner work and the outer budget is where
retries, serialization, logging, and compensating actions live.

### 17.3 Payments: a timeout does NOT mean failure

The most important real-world lesson about timeouts:

```text
 You ---- POST /charge ---->  Payment provider
                              (charges the card)  <-- succeeded!
 You <---- response lost / slow ---- X
 You: timeout!  ... did the customer get charged or not?  UNKNOWN.

 If you retry blindly: double charge.  If you assume failure: money taken, no order.
```

**Solution: idempotency keys + reconciliation.**

```go
func charge(ctx context.Context, o Order) error {
    key := "order-" + o.ID                         // stable per logical operation
    ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
    defer cancel()

    req, _ := http.NewRequestWithContext(ctx, http.MethodPost, pspURL, bytes.NewReader(body))
    req.Header.Set("Idempotency-Key", key)        // PSP dedupes retries with the same key

    resp, err := client.Do(req)
    if err != nil {
        if isTimeout(err) {
            // OUTCOME UNKNOWN: mark PENDING, then resolve asynchronously:
            //   - retry with the SAME key (safe), or
            //   - query PSP "get payment by key" / wait for webhook, then settle.
            return markPending(ctx, o, err)
        }
        return err
    }
    defer resp.Body.Close()
    ...
}
```

```text
 Outcome states:   SUCCEEDED   FAILED   PENDING_UNKNOWN --(reconcile job / webhook)--> final
 Principle: after a timeout on a non-idempotent write, you must either make it idempotent
            or have a way to ASK what happened.
```

### 17.4 API aggregation / Backend-for-Frontend

A mobile home screen needs profile + orders + recommendations + banners. Fan out in parallel
(§15.4) with a **shared deadline** and **per-section fallbacks**:

```text
                 GET /home    (budget 800ms)
                      |
        +------+------+------+--------+
        |      |             |        |
     profile  orders      recs     banners
     (critical)(critical) (optional)(optional)
      300ms    400ms      150ms     150ms
        |        |           |         |
        |        |       timeout?   timeout?
        |        |       -> popular  -> omit
        |        |          items
        +--------+-----------+---------+
                      |
              merge -> respond (partial content is fine)
```

The user sees a fast, slightly degraded page instead of a spinner and a retry.

### 17.5 Web scraping / crawling / third-party integrations

* Unknown servers misbehave in creative ways: tarpits, never-ending streams, huge bodies.
* Use: short dial + header timeouts, **body size limits** (`io.LimitReader`), a total deadline,
  `MaxConnsPerHost` to be polite, and per-domain circuit breakers.
* A crawler without timeouts will inevitably stall on one slow host and, with a bounded worker pool,
  **all workers end up stuck on bad hosts** and throughput drops to zero.

### 17.6 Background jobs and queues (lease-style timeouts)

```text
 Queue (SQS/RabbitMQ/DB):  message delivered to worker W1 with a LEASE of 60s
        |
        +-- W1 finishes in 20s  -> ack/delete               (normal)
        |
        +-- W1 crashes at 10s   -> lease expires at 60s -> redelivered to W2   (recovery)
        |
        +-- W1 is just slow     -> lease expires at 60s -> W2 ALSO starts  (duplicate!)
```

Consequences: (a) the job timeout (context) must be **shorter** than the lease, (b) jobs must be
**idempotent**, (c) long jobs should **extend the lease** (heartbeat).

```go
jobCtx, cancel := context.WithTimeout(ctx, 50*time.Second) // < 60s visibility timeout
defer cancel()
err := process(jobCtx, msg)
```

### 17.7 Uploads, downloads, streaming

Fixed whole-request timeouts break large transfers. Use **progress-based (idle) timeouts**:
"fail if no bytes move for 30s", not "fail if it takes more than 30s total".

```go
// Wrap a reader so each Read must make progress within `idle`.
type idleReader struct {
    r     io.Reader
    conn  interface{ SetReadDeadline(time.Time) error }
    idle  time.Duration
}
func (i *idleReader) Read(p []byte) (int, error) {
    _ = i.conn.SetReadDeadline(time.Now().Add(i.idle))   // rolling deadline
    return i.r.Read(p)
}
```

In HTTP handlers, use `http.ResponseController` (§10.6) for rolling per-chunk deadlines.

### 17.8 WebSockets and long-lived connections

```text
 Server  --ping every 30s-->  Client
 Client  --pong-->            Server
 If no pong within 10s => presume dead => close, free resources.

 Also: read deadline = pingInterval + pongWait, reset on every pong/message.
 Also: set write deadline per message so a stalled client can't block your writer goroutine.
```

Without this, half-open connections (mobile network switch, laptop lid closed) accumulate
forever.

### 17.9 Health checks and Kubernetes

```text
+---------------------------+-------------------------------------------------------+
| Setting                   | Interaction with timeouts                             |
+---------------------------+-------------------------------------------------------+
| readinessProbe            | probe timeoutSeconds: if your /ready handler is slow  |
|                           | under load, pod is pulled from rotation (good/bad!)   |
| livenessProbe             | keep it CHEAP and independent of dependencies; a slow |
|                           | DB must not cause restarts of every pod               |
| terminationGracePeriod    | must be > your Shutdown(ctx) timeout (+ preStop)      |
| Health endpoint itself    | give it a tight ctx (e.g. 500ms) for dependency pings |
+---------------------------+-------------------------------------------------------+
```

### 17.10 CLIs, cron jobs, migrations

* **Cron jobs**: wrap `main` in `context.WithTimeout` so a stuck run can't overlap the next
  schedule and pile up forever.
* **CLI tools**: wire Ctrl-C to cancel (`signal.NotifyContext`) *and* add a global `--timeout` flag.
* **Migrations**: use `lock_timeout` so a DDL statement never blocks production traffic while
  waiting for a lock; fail and retry instead.

### 17.11 Startup dependency wait

```go
func waitForDB(ctx context.Context, db *sql.DB) error {
    ctx, cancel := context.WithTimeout(ctx, 60*time.Second)
    defer cancel()
    backoff := 500 * time.Millisecond
    for {
        pctx, pcancel := context.WithTimeout(ctx, 2*time.Second)
        err := db.PingContext(pctx)
        pcancel()
        if err == nil {
            return nil
        }
        select {
        case <-ctx.Done():
            return fmt.Errorf("db not ready: %w (last: %v)", ctx.Err(), err)
        case <-time.After(backoff):
            if backoff < 5*time.Second { backoff *= 2 }
        }
    }
}
```

### 17.12 Summary table of use cases

```text
+---------------------------+----------------------+-------------------------------------+
| Use case                  | Timeout flavour      | Key idea                            |
+---------------------------+----------------------+-------------------------------------+
| Public API endpoint       | Server + ctx budget  | Bound work, return 504 on expiry    |
| Calling 3rd-party API     | Dial/TLS/header/total| Don't trust the network or the peer |
| DB query                  | ctx + statement_to   | Defence in depth, protect the pool  |
| Microservice chain        | Propagated deadline  | One shared shrinking budget         |
| Payment / write op        | Timeout = UNKNOWN    | Idempotency key + reconciliation    |
| Cache lookup              | Very tight           | Cache must never become the outage  |
| Queue consumer            | Job timeout < lease  | Idempotent jobs, lease extension    |
| File transfer / streaming | Idle (rolling)       | Progress-based, not total-based     |
| WebSocket / gRPC stream   | Heartbeat / keepalive| Detect half-open connections        |
| Distributed lock / leader | TTL lease            | Crash recovery; fencing tokens      |
| Graceful shutdown         | Shutdown(ctx)        | Drain, then force; < SIGKILL        |
| Cron / batch              | Global ctx timeout   | No overlapping, stuck runs          |
| Subprocess                | CommandContext+Delay | Terminate then kill, no hung pipes  |
+---------------------------+----------------------+-------------------------------------+
```

---

## 18. How to choose timeout values

### 18.1 The rule of nesting

```text
 CALLER's timeout  >  CALLEE's timeout (+ network + retries it performs)

   Good:                                  Bad:
   client 10s                             client 3s
    gateway 8s                             gateway 8s     <- keeps working 5s for a
     service 5s                             service 5s       caller who left (wasted work,
      db 1s                                  db 1s            piles up under load)
```

```text
GOOD: each inner layer expires BEFORE its caller (1 char = 100ms)
[============================================client 10s============================================]
 [====================================load balancer 9s====================================]
  [================================gateway ctx 8s================================]
   [===================service 5s===================]
    [=db 1s==]

BAD: the client gives up at 3s; the rest keeps burning resources for nobody
[=========client 3s==========]X  <- user is gone
 [=================gateway ctx 8s .... still working for no one=================]
   [=========service 5s .... still working==========]
```

### 18.2 Derive from data, not guesses

```text
 1. Measure the callee's latency distribution (p50 / p95 / p99 / max) in production.
 2. Set the timeout slightly above the latency you consider "healthy":
        typical:  p99 x 1.5  up to  p99.9
 3. Check it against the caller's budget (SLO):  sum of sequential slices + slack <= budget.
 4. Re-evaluate with real data after deploy; make values configurable (env/config).
```

```text
 latency
 histogram
  |  ####
  |  ######
  |  #########
  |  ############                         p99     p99.9
  |  ###############                       |        |
  |__##################____________________v________v_____ ...tail.........> ms
        p50     p95
                                     timeout here ^ (not at p50!)
   Too low  -> you cut off healthy requests (false timeouts, retries, amplification)
   Too high -> a failing dependency holds your resources for a long time
```

### 18.3 Starting points (tune to your system)

```text
+---------------------------------+------------------------------------+
| Operation                       | Typical starting range             |
+---------------------------------+------------------------------------+
| TCP connect (same region)       | 1-3 s                              |
| TLS handshake                   | 2-5 s                              |
| Redis / cache read              | 50-200 ms                          |
| Simple DB query (OLTP)          | 100 ms - 2 s                       |
| Internal service call           | 200 ms - 3 s                       |
| 3rd-party API call              | 3-15 s                             |
| Server ReadHeaderTimeout        | 3-10 s                             |
| Server IdleTimeout              | 60-120 s (> LB idle)               |
| Graceful shutdown               | 10-30 s (< SIGKILL grace period)   |
| Batch / report generation       | Minutes, but ALWAYS bounded        |
+---------------------------------+------------------------------------+
```

These are **starting points**, not rules. User-facing paths should be far tighter than batch ones.

### 18.4 Make values configurable and named

```go
type Timeouts struct {
    Dial          time.Duration `env:"HTTP_DIAL_TIMEOUT"   default:"3s"`
    TLS           time.Duration `env:"HTTP_TLS_TIMEOUT"    default:"3s"`
    ResponseHdr   time.Duration `env:"HTTP_HEADER_TIMEOUT" default:"5s"`
    Total         time.Duration `env:"HTTP_TOTAL_TIMEOUT"  default:"10s"`
}
```

Magic numbers scattered through code are impossible to audit. Centralize them.

---

## 19. Error handling and classification

### 19.1 The error zoo

```text
+--------------------------------+---------------------------------------------------+
| Error                          | How to detect                                     |
+--------------------------------+---------------------------------------------------+
| ctx deadline passed            | errors.Is(err, context.DeadlineExceeded)          |
| ctx cancelled                  | errors.Is(err, context.Canceled)                  |
| net conn deadline              | errors.Is(err, os.ErrDeadlineExceeded)            |
| any net timeout                | errors.As(err, &ne) on net.Error, ne.Timeout()    |
| http.Client.Timeout exceeded   | usually errors.Is(..DeadlineExceeded) or          |
|                                | *url.Error with Timeout() == true                 |
| gRPC deadline                  | status.Code(err) == codes.DeadlineExceeded        |
| DB statement_timeout           | driver-specific (e.g. Postgres SQLSTATE 57014)    |
+--------------------------------+---------------------------------------------------+
```

### 19.2 A robust classifier

```go
func isTimeout(err error) bool {
    if err == nil {
        return false
    }
    if errors.Is(err, context.DeadlineExceeded) || errors.Is(err, os.ErrDeadlineExceeded) {
        return true
    }
    var ne net.Error
    if errors.As(err, &ne) && ne.Timeout() {
        return true
    }
    return status.Code(err) == codes.DeadlineExceeded // gRPC (import google.golang.org/grpc/...)
}

func isCanceled(err error) bool { return errors.Is(err, context.Canceled) }
```

### 19.3 Timeout vs cancel: treat them differently

```text
 DeadlineExceeded -> the DEPENDENCY or the BUDGET was too slow   -> count as failure, alert on rate
 Canceled         -> the CALLER left / shutdown / sibling failed -> usually NOT your fault; don't
                     page anyone, don't trip breakers, log at debug
```

### 19.4 Mapping to protocols

```text
+---------------------------+-----------------------+----------------------------------+
| Situation                 | HTTP status           | gRPC code                        |
+---------------------------+-----------------------+----------------------------------+
| Upstream dependency slow  | 504 Gateway Timeout   | DeadlineExceeded                 |
| Client too slow sending   | 408 Request Timeout   | (n/a)                            |
| We are overloaded / shed  | 503 + Retry-After     | Unavailable / ResourceExhausted  |
| Client disconnected       | (no response; or 499) | Canceled                         |
+---------------------------+-----------------------+----------------------------------+
```

### 19.5 Wrap with context

```go
return nil, fmt.Errorf("reserve inventory sku=%s: %w", sku, err) // keeps errors.Is working
```

Use `%w` so classification still works up the stack, and include *what you were doing* so a
bare "context deadline exceeded" in the logs is not a mystery.

---

## 20. Testing timeouts

### 20.1 Slow server with `httptest`

```go
func TestClientTimesOut(t *testing.T) {
    srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        select {
        case <-time.After(2 * time.Second): // simulate a hung upstream
        case <-r.Context().Done():          // exit promptly when the client gives up
        }
    }))
    defer srv.Close()

    ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
    defer cancel()

    req, _ := http.NewRequestWithContext(ctx, http.MethodGet, srv.URL, nil)
    start := time.Now()
    _, err := http.DefaultClient.Do(req)

    if !errors.Is(err, context.DeadlineExceeded) {
        t.Fatalf("want DeadlineExceeded, got %v", err)
    }
    if d := time.Since(start); d > 500*time.Millisecond {
        t.Fatalf("returned too late: %v", d) // proves we did not wait the full 2s
    }
}
```

### 20.2 Deterministic time with `testing/synctest` (Go 1.25+)

Real sleeps make tests slow and flaky. `synctest` gives a **fake clock** inside a "bubble":
time only advances when all goroutines are blocked, so a 1-hour timeout tests in microseconds.

```go
import "testing/synctest"

func TestTimeoutFires(t *testing.T) {
    synctest.Test(t, func(t *testing.T) {
        ctx, cancel := context.WithTimeout(context.Background(), time.Hour)
        defer cancel()

        time.Sleep(time.Hour) // virtual time: returns instantly in real time
        synctest.Wait()       // wait until every goroutine in the bubble is idle

        if !errors.Is(ctx.Err(), context.DeadlineExceeded) {
            t.Fatalf("expected deadline exceeded, got %v", ctx.Err())
        }
    })
}
```

(Go 1.24 had an experimental version behind `GOEXPERIMENT=synctest` using `synctest.Run`.
Alternatively inject a clock interface or pass durations as parameters so tests can use
millisecond values.)

### 20.3 Detect goroutine leaks

```go
func TestMain(m *testing.M) { goleak.VerifyTestMain(m) } // go.uber.org/goleak
```

A timeout test that passes but leaves a goroutine behind is exactly the leak from §7.1; goleak
catches it. Also run tests with `-race`.

### 20.4 Fault injection

* Use a TCP proxy (e.g. Toxiproxy) to add latency, drop packets, and cut connections.
* Write tests for the three nasty cases: **accepts connection but never responds**, **responds
  headers then stalls mid-body**, **closes the connection mid-response**.

---

## 21. Observability for timeouts

You cannot tune what you cannot see.

```text
 Metrics to export (per dependency / per endpoint):
   - request duration histogram (p50/p95/p99)            -> choose timeout values
   - timeout count / rate (DeadlineExceeded)             -> alert
   - cancellation count (Canceled)                       -> separate series
   - retries, hedges, breaker state, fallbacks used
   - in-flight requests / goroutines                     -> Little's law early warning
   - db.Stats(): WaitCount, WaitDuration, InUse, Idle    -> pool saturation
   - remaining budget at call time (histogram)           -> are we starving downstream calls?
```

```go
stats := db.Stats()
metrics.Gauge("db_in_use", stats.InUse)
metrics.Counter("db_wait_count", stats.WaitCount)
metrics.Counter("db_wait_ms", stats.WaitDuration.Milliseconds())
```

* Use **distributed tracing** (OpenTelemetry): a span showing "payment call: 5.0s, timed out"
  instantly reveals which hop in the chain consumed the budget.
* Log the *cause* (`context.Cause(ctx)`), the configured timeout, and the elapsed time.
* Alert on **timeout rate**, not just error rate, because timeouts precede outages.
* Use `runtime.NumGoroutine()` / pprof goroutine profiles: a growing count with many goroutines
  parked in `select`/`chan send` is the signature of a timeout leak.

---

## 22. Anti-patterns and pitfalls

```text
+----+---------------------------------------------+------------------------------------------+
| #  | Anti-pattern                                | Fix                                      |
+----+---------------------------------------------+------------------------------------------+
| 1  | http.Get / http.DefaultClient in prod       | Custom client with timeouts              |
| 2  | http.ListenAndServe without Server timeouts | Explicit http.Server{...}                |
| 3  | Forgetting defer cancel()                   | Always defer; use go vet                 |
| 4  | defer cancel() inside a loop                | Call cancel() each iteration             |
| 5  | context.Background() deep in a call chain   | Accept and pass ctx from the caller      |
| 6  | Unbuffered result channel + timeout select  | Buffer of 1, or cancellable work         |
| 7  | Ignoring ctx in loops / long computations   | Check ctx.Err()/Done() periodically      |
| 8  | time.Sleep in cancellable code              | Timer + select on ctx.Done()             |
| 9  | time.After in hot loops (pre-1.23)          | NewTimer + Stop/Reset                    |
| 10 | Same timeout at every layer                 | Nest: inner < outer                      |
| 11 | Retrying non-idempotent ops on timeout      | Idempotency keys; reconcile              |
| 12 | Retries at every layer                      | Retry at one layer; budgets; breakers    |
| 13 | Retries without jitter/backoff              | Exponential backoff + full jitter        |
| 14 | Network calls inside DB transactions        | Keep transactions short & local          |
| 15 | Client.Timeout on streaming/large download  | Per-phase timeouts + idle deadline       |
| 16 | WriteTimeout shorter than handler time      | Size it > slowest handler; per-req ctrl  |
| 17 | Setting conn deadline once, not per op      | Slide the deadline before each I/O       |
| 18 | New http.Client per request                 | Share one client (pooling)               |
| 19 | Not closing/draining resp.Body              | defer Close; drain when reusing conn     |
| 20 | Storing ctx in a struct                     | Pass ctx as first param                  |
| 21 | Using the request ctx for post-response work| WithoutCancel + its own timeout          |
| 22 | Cleanup on an already-expired ctx           | Detached ctx with fresh short timeout    |
| 23 | Timeout too tight (p50-based)               | Base on p99/p99.9 + SLO                  |
| 24 | Timeout = failure assumption for writes     | Treat as UNKNOWN outcome                 |
| 25 | Cache with a long timeout                   | Tight cache timeouts; fall through       |
| 26 | Conn lifetimes longer than middlebox idle   | ConnMaxLifetime/IdleTime < LB/NAT idle   |
| 27 | Liveness probe depending on the database    | Cheap, dependency-free liveness          |
| 28 | Shutdown timeout > K8s grace period         | Shutdown < terminationGracePeriod        |
+----+---------------------------------------------+------------------------------------------+
```

### 22.1 Pitfall deep-dive: using the request context after the handler returns

```go
// BAD: r.Context() is cancelled when the handler returns (or client leaves).
func handler(w http.ResponseWriter, r *http.Request) {
    go sendEmail(r.Context(), user)    // gets cancelled almost immediately!
    w.WriteHeader(http.StatusAccepted)
}

// GOOD: detach (keep trace values), re-bound with its own timeout.
func handler(w http.ResponseWriter, r *http.Request) {
    bg := context.WithoutCancel(r.Context())
    go func() {
        ctx, cancel := context.WithTimeout(bg, 10*time.Second)
        defer cancel()
        sendEmail(ctx, user)
    }()
    w.WriteHeader(http.StatusAccepted)
}
// BEST: enqueue the job durably (queue/outbox) and let a worker do it with retries.
```

### 22.2 Pitfall deep-dive: expired context during cleanup

```go
// BAD: ctx already expired -> rollback/compensation fails immediately, state is inconsistent.
if err := step2(ctx); err != nil {
    undoStep1(ctx)            // ctx.Err() != nil: undo never runs!
}

// GOOD:
if err := step2(ctx); err != nil {
    cctx, cancel := context.WithTimeout(context.WithoutCancel(ctx), 3*time.Second)
    defer cancel()
    undoStep1(cctx)
}
```

---

## 23. Go version notes

```text
+---------+------------------------------------------------------------------------+
| Version | Timeout-relevant change                                                |
+---------+------------------------------------------------------------------------+
| 1.7     | context package enters the standard library                            |
| 1.8     | http.Server.Shutdown (graceful shutdown); IdleTimeout                  |
| 1.15    | ReadHeaderTimeout; DB SetConnMaxIdleTime                               |
| 1.18    | sync.Mutex.TryLock                                                     |
| 1.20    | WithCancelCause; http.ResponseController (per-request deadlines);      |
|         | exec.Cmd.Cancel and WaitDelay                                          |
| 1.21    | WithTimeoutCause/WithDeadlineCause, WithoutCancel, context.AfterFunc   |
| 1.23    | Timer/Ticker overhaul: unreferenced timers are GC'd; Stop/Reset have   |
|         | no stale values (requires go.mod `go 1.23`+)                           |
| 1.24    | testing/synctest experimental (GOEXPERIMENT=synctest)                  |
| 1.25    | testing/synctest generally available (synctest.Test)                   |
+---------+------------------------------------------------------------------------+
```

Always check the release notes of the Go version you actually run; behaviors tied to `go.mod`'s
`go` directive (such as the 1.23 timer semantics) depend on that declared language version.

---

## 24. Cheat sheet and mental checklist

### 24.1 The 10-second mental model

```text
 Timeout   = how long I'm willing to wait               (relative, a Duration)
 Deadline  = the moment I stop caring                   (absolute, a Time)
 Context   = the carrier of deadline + cancellation     (flows DOWN the call tree)
 Cancel    = a SIGNAL, never a kill; code must LISTEN   (cooperative)
 Budget    = one deadline shared and sliced by all callees
 Rule      = inner timeouts < outer timeouts
 Reality   = timeout on a write = UNKNOWN outcome -> idempotency
```

### 24.2 Code skeletons to memorize

```go
// 1. Bounded call
ctx, cancel := context.WithTimeout(parent, d)
defer cancel()
res, err := svc.Call(ctx, ...)

// 2. Race against a timer
select {
case v := <-ch:
case <-ctx.Done(): return ctx.Err()
}

// 3. Leak-safe async
ch := make(chan result, 1)       // buffered
go func() { ch <- do(ctx) }()

// 4. HTTP client
client := &http.Client{Timeout: 10*time.Second, Transport: &http.Transport{
    DialContext: (&net.Dialer{Timeout: 3*time.Second}).DialContext,
    TLSHandshakeTimeout: 3*time.Second, ResponseHeaderTimeout: 5*time.Second,
    IdleConnTimeout: 90*time.Second}}

// 5. HTTP server
srv := &http.Server{ReadHeaderTimeout: 5*time.Second, ReadTimeout: 15*time.Second,
    WriteTimeout: 30*time.Second, IdleTimeout: 120*time.Second}

// 6. DB
db.QueryRowContext(ctx, q, args...)
```

### 24.3 Pre-merge checklist for any code that waits

```text
[ ] Does every network/DB/process/lock wait have a bound?              (timeout or ctx)
[ ] Is ctx the first parameter and passed to EVERY downstream call?
[ ] Is cancel() always called (defer, or explicit in loops)?
[ ] Do my loops and goroutines actually observe ctx.Done()?
[ ] Can a goroutine block forever on send/receive after a timeout?     (buffer / select)
[ ] Are inner timeouts shorter than outer ones? Is there slack?
[ ] What happens when the timeout fires: retry? fallback? fail? (policy decided?)
[ ] Is the operation safe to retry? If not, is there an idempotency key?
[ ] Are retries backed off with jitter and limited to one layer?
[ ] Is the work in flight cleaned up (rollback, release, compensate) with a DETACHED ctx?
[ ] Are timeout values configurable, named, and based on measured percentiles?
[ ] Are timeouts, cancellations, retries and pool waits visible in metrics/traces?
[ ] Is there a test for "peer never answers" and a leak check?
```

### 24.4 One-page summary diagram

```text
                      THE LIFE OF A BOUNDED REQUEST

   caller sets deadline
          |
          v
   +-------------------------------------------------------------+
   | handler / main                                              |
   +-------------------------------------------------------------+
          |
          |  ctx (deadline + cancel) flows DOWN the call tree
          v
   +-------------------------------------------------------------+
   | every dependency call: HTTP, gRPC, DB, cache, exec          |
   +-------------------------------------------------------------+
          |
          |  result  OR  DeadlineExceeded / Canceled   (flows UP)
          v
   classify error:
        - retry (idempotent, jittered, within budget)
        - fallback / degrade (optional deps)
        - breaker records failure
        - fail fast with 504 / DeadlineExceeded
          |
          v
   clean up on a detached ctx; record metrics; log cause
```

> **Final takeaway:** Think of every external call as a *loan of your resources to something
> unreliable*. A timeout is the **repayment date**. Choose it deliberately, share it across the
> call chain through `context`, make your code *listen* for it, and always decide in advance what
> you will do when it passes.
