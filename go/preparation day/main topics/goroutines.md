# Goroutines: Purpose, Mechanics, Trade-offs, and Production Use

## 1. The problem goroutines solve

A cloud service is mostly waiting: on sockets, disks, RPCs, locks. Before Go, there were two bad options:

```
Model A: thread-per-request            Model B: event loop / callbacks
-------------------------------        --------------------------------
+ simple, linear code                  + tiny memory per connection
- ~8MB virtual stack / thread          - inverted control flow ("callback hell")
- kernel context switch ~1-5us         - one CPU-bound handler stalls everything
- 10k-100k threads = death             - hard to use all cores
- kernel scheduler knows nothing       - stack traces are meaningless
  about your workload
```

Go's answer: **write Model A code, get Model B economics.** The runtime multiplexes millions of cheap user-space tasks (goroutines) onto a small pool of OS threads, and turns blocking I/O into non-blocking I/O under the hood.

> Purpose: make *blocking-style, sequential code* scale to massive concurrency without paying kernel-thread costs, while using all cores.

## 2. Mental model: the G-M-P scheduler

```
   G = goroutine  (stack + saved registers + status, ~2KB initial stack)
   M = machine    (an OS thread)
   P = processor  (a scheduling context / token; count = GOMAXPROCS)

                 Global run queue (G G G ...)
                          |
        +-----------------+------------------+
        |                                    |
   +----v-----+                        +-----v----+
   |    P0    |                        |    P1    |
   | local RQ |  <--work stealing-->   | local RQ |
   | [G][G][G]|                        | [G][G]   |
   |  runnext |                        |  runnext |
   +----+-----+                        +-----+----+
        |  bound while running                |
   +----v-----+                        +-----v----+
   |    M0    |  (OS thread)           |    M1    |
   | running  |                        | running  |
   |    G7    |                        |    G12   |
   +----------+                        +----------+

   Invariant: a M must hold a P to run Go code.
   #Ps = GOMAXPROCS (parallelism cap). #Ms grows as needed (blocked syscalls).
   #Gs = unbounded (only limited by memory).
```

### Goroutine lifecycle

```
          go f()
            |
            v
        +--------+   scheduled onto P    +---------+
        | _Grunnable| ------------------> |_Grunning|
        +--------+ <---------------------+----+----+
            ^        preempted (async,         |
            |        ~10ms) / Gosched          | chan op, mutex,
            |                                  | time.Sleep, netpoll wait
            |  ready() (data arrived,          v
            |  lock released, timer fired)  +--------+
            +------------------------------ |_Gwaiting|  (gopark)
                                            +--------+
            _Gsyscall: in a blocking syscall; P may be handed off
            _Gdead:    finished; G struct recycled (free list)
```

### What happens on blocking I/O (the key trick)

```
 Network read (netpoller path)            Blocking syscall (e.g. file I/O, cgo)
 ---------------------------------        ----------------------------------------
 G calls conn.Read()                      G enters syscall on M0 (holding P0)
   |                                        |
   v                                        v
 fd is non-blocking; EAGAIN               sysmon sees M0 stuck >20us
   |                                        |
   v                                        v
 G is parked (gopark)                     P0 is detached from M0 and handed
 fd registered in epoll/kqueue/IOCP       to another M (spawned if needed)
   |                                        |
   v                                        v
 M immediately runs another G             other Gs keep running on P0
   |                                        |
   v                                        v
 epoll says readable -> G made runnable   syscall returns: M0 tries to reacquire a P,
                                          else G goes to global queue, M0 parks
```

So one thread can serve thousands of connections, while you write plain `conn.Read()`.

### Why they are cheap

| Property | OS thread | Goroutine |
|---|---|---|
| Initial stack | 1-8 MB (virtual) | ~2 KB, **growable** (contiguous stack copy) |
| Create | syscall (`clone`), ~10-50 us | ~0.2-1 us, an allocation + queue push |
| Context switch | kernel, ~1-5 us, TLB/cache cost | user-space, ~100-200 ns (save PC/SP/BP) |
| Scheduling | kernel, preemptive, workload-blind | Go runtime, cooperative + async preemption |
| Practical limit | ~10^4 | ~10^6+ (RAM-bound) |

## 3. Production-style reference program

Single file, stdlib only. It demonstrates:

1. Cost of goroutines (memory and thread count)
2. I/O multiplexing (10k "requests" in about 100 ms)
3. Bounded worker pool with context, panic isolation, and no lost results
4. Fan-out / fan-in inside an HTTP handler with a deadline and no leaks
5. Goroutine leak demonstration and fix
6. Graceful shutdown

```go
// main.go
//
//	go run .            # run all demos
//	go run . -serve :8080   # run the HTTP service demo
//	curl localhost:8080/aggregate ; curl localhost:8080/debug/goroutines
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"log/slog"
	"math/rand"
	"net/http"
	"os"
	"os/signal"
	"runtime"
	"runtime/debug"
	"runtime/pprof"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
)

var logger = slog.New(slog.NewTextHandler(os.Stdout, nil))

/* ---------------------------------------------------------------------------
 * DEMO 1: What does a goroutine actually cost?
 * Park n goroutines on a channel and measure stack memory + OS threads.
 * ------------------------------------------------------------------------- */

func demoCost(n int) {
	runtime.GC()
	var before, after runtime.MemStats
	runtime.ReadMemStats(&before)

	var started, done sync.WaitGroup
	release := make(chan struct{})
	started.Add(n)
	done.Add(n)
	for range n { // Go 1.22+: range over int
		go func() {
			defer done.Done()
			started.Done()
			<-release // parked: _Gwaiting, costs memory only, zero CPU
		}()
	}
	started.Wait()
	runtime.ReadMemStats(&after)

	logger.Info("demo1: cost",
		"goroutines", runtime.NumGoroutine(),
		"stack_bytes_per_g", (after.StackInuse-before.StackInuse)/uint64(n),
		"os_threads_created", pprof.Lookup("threadcreate").Count(),
	)
	// 100k OS threads with default 8MB stacks would be ~800GB of virtual
	// address space plus 100k kernel task structs. Here: a few hundred MB.

	close(release)
	done.Wait()
}

/* ---------------------------------------------------------------------------
 * DEMO 2: I/O multiplexing. 10k concurrent "RPCs" of 100ms each.
 * Sequentially: 1000s. Goroutines: ~100ms. Threads used: a handful.
 * ------------------------------------------------------------------------- */

func demoIOMultiplex(n int) {
	start := time.Now()
	var wg sync.WaitGroup
	for range n {
		wg.Add(1)
		go func() {
			defer wg.Done()
			time.Sleep(100 * time.Millisecond) // stand-in for network I/O
		}()
	}
	wg.Wait()
	logger.Info("demo2: io multiplex",
		"requests", n,
		"wall", time.Since(start).Round(time.Millisecond),
		"sequential_would_be", time.Duration(n)*100*time.Millisecond,
	)
}

/* ---------------------------------------------------------------------------
 * DEMO 3: Bounded worker pool (the #1 production pattern).
 *
 *   inputs --> [idx chan] --> W workers --> results[i]   (index-addressed:
 *                                                         no mutex, no race)
 *
 * Guarantees:
 *   - concurrency is capped (protects downstreams / memory / fd limits)
 *   - a panicking job cannot crash the process
 *   - ctx cancellation stops feeding and drains cleanly
 *   - no goroutine outlives the call
 * ------------------------------------------------------------------------- */

type Result[In, Out any] struct {
	In   In
	Out  Out
	Err  error
	Done bool // false => never attempted (ctx cancelled before dispatch)
}

func safeCall[In, Out any](
	ctx context.Context,
	fn func(context.Context, In) (Out, error),
	in In,
) (out Out, err error) {
	// An unrecovered panic in ANY goroutine kills the whole process.
	// Isolate at the goroutine boundary.
	defer func() {
		if r := recover(); r != nil {
			err = fmt.Errorf("panic: %v\n%s", r, debug.Stack())
		}
	}()
	return fn(ctx, in)
}

func ProcessAll[In, Out any](
	ctx context.Context,
	workers int,
	inputs []In,
	fn func(context.Context, In) (Out, error),
) []Result[In, Out] {
	results := make([]Result[In, Out], len(inputs))
	idx := make(chan int) // unbuffered: backpressure on the feeder

	var wg sync.WaitGroup
	wg.Add(workers)
	for range workers {
		go func() {
			defer wg.Done()
			for i := range idx { // exits when idx is closed
				out, err := safeCall(ctx, fn, inputs[i])
				// each index is written by exactly one worker -> race-free
				results[i] = Result[In, Out]{In: inputs[i], Out: out, Err: err, Done: true}
			}
		}()
	}

feed:
	for i := range inputs {
		select {
		case idx <- i:
		case <-ctx.Done():
			break feed
		}
	}
	close(idx) // signals workers to finish; MUST be closed by the sender side
	wg.Wait()  // happens-before edge: all results[] writes visible after this

	if err := ctx.Err(); err != nil {
		for i := range results {
			if !results[i].Done {
				results[i].In, results[i].Err = inputs[i], err
			}
		}
	}
	return results
}

func demoPool() {
	targets := make([]int, 40)
	for i := range targets {
		targets[i] = i
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()

	var inflight, peak atomic.Int64
	res := ProcessAll(ctx, 8, targets, func(ctx context.Context, id int) (string, error) {
		n := inflight.Add(1)
		defer inflight.Add(-1)
		for { // lock-free "max" update
			p := peak.Load()
			if n <= p || peak.CompareAndSwap(p, n) {
				break
			}
		}
		if id%13 == 0 {
			panic("corrupt input") // isolated by safeCall
		}
		select {
		case <-time.After(50 * time.Millisecond): // simulated work
			return fmt.Sprintf("target-%d", id), nil
		case <-ctx.Done():
			return "", ctx.Err()
		}
	})

	ok, failed := 0, 0
	for _, r := range res {
		if r.Err != nil {
			failed++
		} else {
			ok++
		}
	}
	logger.Info("demo3: worker pool", "ok", ok, "failed", failed,
		"peak_concurrency", peak.Load(), "cap", 8)
}

/* ---------------------------------------------------------------------------
 * DEMO 4: Fan-out / fan-in with a deadline (what an API gateway does).
 *
 *            +--> G(users)     --+
 *   request -+--> G(billing)   --+--> buffered chan(len=N) --> aggregate
 *            +--> G(inventory) --+
 *
 * The channel is buffered to len(backends) so a sender can NEVER block, even
 * if the receiver gave up. That is what prevents goroutine leaks here.
 * ------------------------------------------------------------------------- */

type reply struct {
	name string
	body string
	err  error
}

func callBackend(ctx context.Context, name string) (string, error) {
	d := time.Duration(rand.Intn(400)) * time.Millisecond
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-t.C:
		return fmt.Sprintf("%s ok in %s", name, d), nil
	case <-ctx.Done(): // honor cancellation: never do un-cancellable work
		return "", ctx.Err()
	}
}

func aggregate(parent context.Context, backends []string) []reply {
	ctx, cancel := context.WithTimeout(parent, 250*time.Millisecond)
	defer cancel() // releases timer; cancels stragglers on early return

	ch := make(chan reply, len(backends))
	for _, b := range backends { // Go 1.22+: b is per-iteration, safe to capture
		go func() {
			body, err := callBackend(ctx, b)
			ch <- reply{b, body, err}
		}()
	}
	out := make([]reply, 0, len(backends))
	for range backends {
		out = append(out, <-ch) // bounded: every callee obeys ctx
	}
	return out
}

/* ---------------------------------------------------------------------------
 * DEMO 5: The classic goroutine leak, and the fix.
 * A leaked goroutine is a parked G + its stack + everything it references,
 * forever. GC cannot reclaim it because the runtime holds a reference.
 * ------------------------------------------------------------------------- */

func leakyCall() {
	ch := make(chan int) // unbuffered
	go func() { ch <- 42 }() // blocks forever: caller returned, nobody receives
}

func fixedCall(ctx context.Context) {
	ch := make(chan int, 1) // buffered: sender completes regardless
	go func() { ch <- 42 }()
	select {
	case <-ch:
	case <-ctx.Done():
	}
}

func demoLeak() {
	base := runtime.NumGoroutine()
	for range 1000 {
		leakyCall()
	}
	time.Sleep(100 * time.Millisecond)
	leaked := runtime.NumGoroutine() - base

	ctx, cancel := context.WithCancel(context.Background())
	cancel() // caller "gives up" immediately
	base = runtime.NumGoroutine()
	for range 1000 {
		fixedCall(ctx)
	}
	time.Sleep(100 * time.Millisecond)
	fixed := runtime.NumGoroutine() - base

	logger.Info("demo5: leak", "leaky_extra_goroutines", leaked, "fixed_extra_goroutines", fixed)
}

/* ---------------------------------------------------------------------------
 * HTTP service: net/http already spawns 1 goroutine per connection (HTTP/1)
 * or per stream (HTTP/2). Your handler IS a goroutine.
 * ------------------------------------------------------------------------- */

func serve(addr string) error {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /aggregate", func(w http.ResponseWriter, r *http.Request) {
		// r.Context() is cancelled if the client disconnects -> propagates
		// down to every goroutine spawned by aggregate().
		for _, x := range aggregate(r.Context(), []string{"users", "billing", "inventory"}) {
			fmt.Fprintf(w, "%-10s err=%v body=%q\n", x.name, x.err, x.body)
		}
	})
	mux.HandleFunc("GET /debug/goroutines", func(w http.ResponseWriter, _ *http.Request) {
		fmt.Fprintf(w, "%d\n", runtime.NumGoroutine()) // a leak = monotonic growth
	})

	srv := &http.Server{
		Addr:              addr,
		Handler:           mux,
		ReadHeaderTimeout: 5 * time.Second, // slowloris protection
		IdleTimeout:       60 * time.Second,
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	errCh := make(chan error, 1)
	go func() { errCh <- srv.ListenAndServe() }()
	logger.Info("listening", "addr", addr)

	select {
	case err := <-errCh:
		return err
	case <-ctx.Done(): // SIGTERM from Kubernetes
	}

	shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	return srv.Shutdown(shutdownCtx) // stops accepting, drains in-flight handler goroutines
}

func main() {
	addr := flag.String("serve", "", "run the HTTP demo on this address, e.g. :8080")
	flag.Parse()

	logger.Info("env", "GOMAXPROCS", runtime.GOMAXPROCS(0), "NumCPU", runtime.NumCPU(),
		"go", runtime.Version())

	if *addr != "" {
		if err := serve(*addr); err != nil && !errors.Is(err, http.ErrServerClosed) {
			logger.Error("server", "err", err)
			os.Exit(1)
		}
		return
	}

	demoCost(100_000)
	demoIOMultiplex(10_000)
	demoPool()
	for _, r := range aggregate(context.Background(), []string{"users", "billing", "inventory"}) {
		logger.Info("demo4: fan-out", "backend", r.name, "err", r.err, "body", r.body)
	}
	demoLeak()
}
```

Run it with `go run -race .` during development. The race detector will not flag this program, and it will flag the broken variants in the next section.

## 4. Pros and cons

### Pros

```
+---------------------------+------------------------------------------------------+
| Cheap                     | 2KB stacks, ~100ns switches; 10^5-10^6 per process   |
| Sequential-looking code   | No callbacks/async-await coloring; blocking style    |
| Automatic I/O multiplex   | netpoller (epoll/kqueue/IOCP) is invisible to you    |
| Multicore by default      | Work-stealing across P's; no manual thread pools     |
| Preemptible               | Async preemption (Go 1.14+) via signals; a tight     |
|                           | `for {}` can't starve the scheduler                  |
| Great tooling             | pprof goroutine profile, race detector, trace,       |
|                           | runtime/metrics, goroutine dumps on SIGQUIT          |
| Composable cancellation   | context.Context threads deadlines through the tree   |
+---------------------------+------------------------------------------------------+
```

### Cons

```
+---------------------------+------------------------------------------------------+
| No structured concurrency | `go f()` returns nothing: no handle, no join, no     |
| built in                  | parent/child ownership. You must build it            |
|                           | (WaitGroup, errgroup, ctx).                          |
| Leaks are silent          | Blocked forever = memory forever, no error, no GC    |
| Panics are process-fatal  | Unrecovered panic in any goroutine kills everything  |
| Data races                | Shared memory + no ownership types. Races are        |
|                           | undefined behavior in practice (torn reads, etc.)    |
| Unbounded by default      | `go` in a loop over untrusted input = OOM / DoS      |
| Not free                  | Each G still costs stack + scheduler work; 1M Gs     |
|                           | is ~2-4GB+ and GC must scan every stack              |
| Parallelism != speedup    | Contended mutexes, false sharing, GC pressure and    |
|                           | channel overhead can make it SLOWER than serial      |
| Hard to observe/kill      | You cannot kill a goroutine from outside; it must    |
|                           | cooperate (check ctx)                                |
| Scheduler opacity         | Latency spikes from GC, syscall hand-off, runnext    |
|                           | starvation; need `go tool trace` to see why          |
| cgo / blocking syscalls   | Each blocked call pins an M (OS thread) -> thread    |
|                           | explosion (default cap 10000 -> fatal)               |
+---------------------------+------------------------------------------------------+
```

## 5. Goroutines vs. channels vs. mutexes: pick the right tool

```
Need                                         Use
-------------------------------------------  -------------------------------
Pass ownership of data / pipeline stages     channels
Protect a small shared struct (cache, map)   sync.Mutex / RWMutex
Counters, flags, config pointer swap         sync/atomic (atomic.Int64, atomic.Pointer[T])
Wait for N goroutines                        sync.WaitGroup
Wait + first error + cancel siblings         golang.org/x/sync/errgroup
Limit concurrency                            worker pool or semaphore.Weighted
Run once                                     sync.Once / sync.OnceValue
Dedupe in-flight identical work              golang.org/x/sync/singleflight
```

Rule of thumb: channels for *flow of ownership*, mutexes for *protecting state*. Channels are not "faster," they are a coordination tool.

## 6. Where goroutines are actually used in production

```
  Kubernetes / etcd / Docker / containerd   one G per watch, per connection, per controller loop
  gRPC / net/http servers                   one G per connection or stream
  Prometheus, Envoy-control-plane, Caddy    scrape fan-out, health checks, TLS handshakes
  Message consumers (Kafka/NATS)            one G per partition + worker pool downstream
  Proxies / API gateways                    two Gs per proxied connection (io.Copy each way)
  CI runners, crawlers, scanners            bounded pools over huge target sets
  Background duties in a service            ticker loops: flush, cache refresh, lease renew
```

**Good fit:** I/O-bound fan-out, many concurrent connections, background loops, pipelines, anything that waits.

**Poor fit:**
- Pure CPU work on one small dataset. Goroutine count above `GOMAXPROCS` gives no speedup, only overhead.
- Work that is fast and tiny. `go` + sync overhead exceeds the work.
- Anything needing strict ordering and shared mutable state. Serialize it in one goroutine that owns the data (the actor pattern).

## 7. Production checklist

```
[ ] Every `go` statement has an owner and a defined exit condition
[ ] Every goroutine that can block selects on ctx.Done() (or its channel is closed by the sender)
[ ] Concurrency is bounded (pool/semaphore) wherever input size is externally controlled
[ ] Panic recovery at goroutine boundaries you don't control (job runners, handlers)
[ ] Channels: only the sender closes; buffered when the receiver may abandon
[ ] No shared mutable state without a mutex/atomic; CI runs `go test -race`
[ ] No time.Sleep for synchronization; use ctx, channels, WaitGroup
[ ] Metrics exported: runtime.NumGoroutine(), runtime/metrics (/sched/latencies:seconds,
    /sched/goroutines:goroutines), alert on monotonic growth
[ ] GOMAXPROCS matches the container CPU limit:
      Go 1.25+ reads cgroup CPU limits automatically
      older versions: uber-go/automaxprocs, else you get throttling (CFS) at p99
[ ] pprof exposed (internal port only): /debug/pprof/goroutine?debug=2 for stack dump
[ ] Graceful shutdown: SIGTERM -> stop accepting -> cancel ctx -> wait -> exit
[ ] Tests use goleak (go.uber.org/goleak) to fail on leaked goroutines
```

## 8. Reverse-engineering notes (Go binaries and goroutines)

These details matter when you analyze Go binaries, including malware, which often uses goroutines for concurrent scanning, C2 beaconing, and worker fan-out.

```
Source                       What it looks like in a disassembler
---------------------------  ---------------------------------------------------
go f(x)                      call runtime.newproc(fn *funcval) ; args are captured
                             in a closure object (Go 1.17+ regabi: no arg copying)
closures / go func(){...}    symbols like main.main.func1, main.(*T).Run.gowrap1
<-ch / ch <- v               runtime.chanrecv1 / runtime.chansend1 (chanrecv2 for v,ok)
select {...}                 runtime.selectgo with an scase array
sync.Mutex.Lock slow path    sync.(*Mutex).lockSlow -> runtime.semacquire -> gopark
time.Sleep                   runtime.timeSleep -> gopark
Current goroutine pointer    amd64: R14 (ABIInternal); arm64: R28; else TLS slot
g struct                     stack.lo/hi, stackguard0, sched (sp/pc/bp), atomicstatus
Stack growth check           function prologue: cmp rsp, [r14+0x10] ; jbe morestack
```

- Function prologues contain `runtime.morestack_noctxt` calls. Stack-bound checks against `g.stackguard0` are how goroutine stacks grow, and also how async preemption is triggered (the runtime poisons `stackguard0`).
- `gopclntab` retains function names even in stripped binaries, so goroutine entry points are recoverable. Tools: `GoReSym`, Ghidra Go analyzers, IDA's Go plugins.
- Goroutine-heavy malware often shows `runtime.newproc` in loops over target lists, with a semaphore channel (`make(chan struct{}, N)`) as a concurrency limiter. Spotting `chansend1` before `newproc` and `chanrecv1` in a deferred closure identifies that pattern.
- At runtime, `GOTRACEBACK=all` plus SIGQUIT dumps every goroutine's stack. In dynamic analysis, `dlv goroutines` lists them, and parked goroutines show the wait reason (`chan receive`, `select`, `IO wait`, `semacquire`), which maps the program's concurrency structure quickly.

## 9. One-paragraph answer

**Goroutines exist to let you write simple, blocking, sequential code and still handle 10^5-10^6 concurrent activities on a few cores.** They are user-space tasks with growable 2 KB stacks, scheduled by a work-stealing M:N scheduler (G-M-P). The runtime turns blocking network I/O into netpoller-driven parking and hands P's off when threads block in syscalls. The cost is that *you* own lifecycle, cancellation, bounding, panic isolation, and data-race safety, because `go f()` gives you nothing back. Use them for I/O-bound fan-out, connection handling, and background loops, always with `context`, bounded pools, and `-race` in CI.

If you want, I can follow up with a `go tool trace` walkthrough of this program, a deeper dive into the scheduler source (`runtime/proc.go`: `schedule()`, `findRunnable()`, `runqsteal`), or a structured-concurrency version using `errgroup`.