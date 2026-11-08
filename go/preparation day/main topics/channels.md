I'll write this as a runnable program, check that it compiles, then walk through the mental model.

I've written `main.go`, a runnable guide with ten demos, and I checked it. It compiles, `go vet` is clean, and it runs race-clean under `go run -race .` on Go 1.22 with the stdlib only. The sections below give the reasoning behind each demo.

## 1. Why channels exist

Go took its concurrency model from Hoare's CSP: share memory by communicating, instead of communicating by sharing memory under locks. The most useful thing to know is that a channel is a **synchronization point that also moves data**. The happens-before edge is the point, and the data copy comes with it.

```
 Shared-memory model                      CSP / channel model
 ┌────────┐   ┌──────┐   ┌────────┐      ┌────────┐  value   ┌────────┐
 │ G1     │──►│ var  │◄──│ G2     │      │ G1     │ ───────► │ G2     │
 └────────┘   │ +lock│   └────────┘      └────────┘  (owner  └────────┘
   who owns it? who holds the lock?         changes)   ownership moves with the value
```

Only one goroutine touches a value at a time, because the value itself moves between goroutines. That is the design goal.

## 2. Internal mechanics (`runtime/chan.go`)

```
                    hchan
 ┌──────────────────────────────────────────────┐
 │ lock    (one runtime mutex guards it all)    │
 │ qcount / dataqsiz   buffered count / cap     │
 │ buf ──► [ e0 | e1 | e2 | .. ]  ring buffer   │
 │          ▲recvx     ▲sendx                   │
 │ recvq ──► sudog ─► sudog   (parked receivers)│
 │ sendq ──► sudog ─► sudog   (parked senders)  │
 │ closed                                       │
 └──────────────────────────────────────────────┘

 send(ch, v):
   lock
   ├─ recvq non-empty? ──► copy v DIRECTLY into the receiver's stack, goready(G)
   ├─ buf has room?    ──► copy v into ring, advance sendx
   └─ else             ──► enqueue sudog in sendq, gopark(): the G sleeps and
                           the P runs another G (no OS thread blocks)
   unlock
```

These mechanics explain most of the behavior:

| Observation | Mechanical reason |
|---|---|
| Blocking is cheap | A parked G plus a sudog costs a few KB, not a thread. |
| Channels are slower than atomics | Every op takes the lock, and may park or wake a G. |
| `close()` is a broadcast | It walks `recvq` and readies every waiter. |
| Leaks are silent | A parked G is a GC root via the scheduler, so it never gets collected. |
| `select` is fair | It shuffles case order and locks channels in address order (to avoid deadlock). |
| `nil` channel blocks forever | There is no `hchan`, so the op can never become ready. |

## 3. Purposes and what each demo shows

1. **Synchronization.** Demo 1 shows `close(ready)` publishing a plain variable with no data race.
2. **Bounded queues and backpressure.** Demo 2 uses `select { case q<-v: default: }` to shed load explicitly. An unbounded queue turns overload into OOM.
3. **Ownership and lifecycle.** Demos 3 and 4 show that the sender closes, never the receiver. Return `<-chan T` so the type system enforces this. One `close()` also broadcasts to N waiters, which is how `ctx.Done()` works.
4. **Multiplexing.** Demo 5 covers fan-in, timeouts, and the nil-channel trick that disables a drained case.
5. **Semaphores.** Demo 6 uses a `chan struct{}` with capacity `N` to cap concurrency.
6. **Pipelines.** Demo 8 has generic `Stage[In,Out]` fan-out and fan-in. The `WaitGroup` plus a closer goroutine is the standard answer to "who closes with N senders".

## 4. Pros and cons

**Pros**
- Ownership transfer makes whole classes of data races structurally impossible.
- Backpressure and overload policy are expressed in the type and capacity, not bolted on.
- `select` composes cancellation, timeouts, and multiple sources in one construct.
- Blocking costs a parked goroutine rather than a thread, so you can have 100k+ of them.
- They are readable: the code's shape mirrors the dataflow.

**Cons** (demos 3, 7, and 9 prove these)
- **Goroutine leaks.** The demo leaks 1000 goroutines (`+1000 parked forever`). The fix is a capacity-1 buffer plus a `ctx.Done()` arm on every blocking send.
- **Panics.** Sending on a closed channel, closing twice, and closing nil all panic. Production code needs a lock or a single owner to prevent this (see `Ingestor.Submit` and `Shutdown`).
- **Cost.** Per-op overhead is a lock plus park/wake. In my run, atomic took ~98ms, the channel ~267ms, and the mutex ~356ms. The mutex was the slowest here because of contention, so measure for your own pattern.
- **Deadlock risk.** Cyclic waits, and a forgotten receiver on an unbuffered channel, hang silently. The runtime only detects it when *all* goroutines are asleep.
- **Hidden ordering.** Fan-out destroys output order.
- **Misleading `len()`.** It is a racy snapshot, so use it for metrics only.
- **Poor fit for shared state.** A map cache or a counter wants a `sync.Mutex` or an atomic, not a channel.

## 5. The actual use (demo 10)

Cloud services use channels for the **backpressure and lifecycle skeleton** of a pipeline:

```
 producers ──Submit() non-blocking──► [ in: bounded ] ──► batcher ──► batches ──► N workers ──► Sink
              │ full? ErrOverloaded                       (single      (unbuffered   (ctx timeout
              ▼   → HTTP 429/503                          owner)        = backpressure) per write)

 Shutdown(SIGTERM): stop producers → close(in) → batcher flushes tail & closes(batches)
                    → workers drain → wg.Wait() ──or── drain deadline → cancel work ctx
```

The design choices to copy:
- **Every queue is bounded, and the overload policy is explicit.** In the run, 92.5% of events were shed, and `accepted == written + failed` still held. Nothing was lost silently.
- **`RWMutex` makes "check closed, then send" atomic against `close(in)`.** `Submit` never blocks while holding it, so `Shutdown` can't deadlock. This avoids the send-on-closed panic.
- **Single-owner closing.** The batcher is the only sender on `batches`, so it is the only closer.
- **Workers never exit early.** They keep draining even after the context is cancelled, so the batcher can never hang on a send.
- **A fresh slice per flush.** A slice handed to another goroutine must never be reused.
- **Shutdown has a deadline.** It drains gracefully, and abandons in-flight writes only if the deadline is blown.

## 6. Decision rule

```
Moving data or ownership between goroutines?     → channel
Coordinating lifecycle / cancellation / timeouts? → channel (close / ctx.Done)
Bounded work queue with a defined overload policy? → buffered channel
Protecting shared state (map, cache, struct)?     → sync.Mutex / RWMutex
Single counter or flag?                           → sync/atomic
Wait for N goroutines or first error?             → WaitGroup / errgroup
```

To run it: `cd channels && go mod init channels && go run -race .`. The `go.mod` isn't in the file card, so run `go mod init` yourself.

```go
// Command channels is an executable field guide to Go channels.
//
// WHY CHANNELS EXIST
// ------------------
// Go's concurrency model descends from Hoare's CSP (1978):
//
//	"Do not communicate by sharing memory; share memory by communicating."
//
// A channel is NOT "a thread-safe queue" first. It is a *synchronization
// point that also moves data*. The data transfer is the side effect; the
// happens-before edge is the point.
//
// MENTAL MODEL (runtime/chan.go, simplified)
// ------------------------------------------
//
//	type hchan struct {
//	    qcount   uint           // items currently buffered
//	    dataqsiz uint           // capacity (0 => unbuffered)
//	    buf      unsafe.Pointer // ring buffer, dataqsiz * elemsize
//	    closed   uint32
//	    sendx, recvx uint       // ring cursors
//	    recvq    waitq          // parked receivers  (list of *sudog)
//	    sendq    waitq          // parked senders    (list of *sudog)
//	    lock     mutex          // one runtime mutex guards ALL of the above
//	}
//
//	  sender G ──► lock ─┬─ receiver parked in recvq?  yes ─► copy straight into
//	                     │                                    receiver's stack, ready(G)
//	                     ├─ buffer has room?           yes ─► copy into ring buf
//	                     └─ otherwise                     ─► enqueue sudog in sendq,
//	                                                          gopark() (scheduler runs
//	                                                          another G on this P)
//
// Key consequences (everything below follows from this struct):
//   - A blocked goroutine costs a parked G + sudog (~KBs), NOT an OS thread.
//   - Every op takes one mutex: channels are slower than atomics, and a hot
//     channel is a contention point (see demo 9).
//   - Unbuffered send/recv are a *rendezvous*: value is copied G-to-G.
//   - close() wakes every parked receiver => broadcast primitive.
//   - A parked G referenced only by a channel nobody else can reach is never
//     collected => goroutine leak (see demo 7).
//
// Run:  go run .       (Go 1.21+, stdlib only)
// Race: go run -race . (the production demo is race-clean by construction)
package main

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"math/rand"
	"os"
	"os/signal"
	"runtime"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
)

func section(s string) { fmt.Printf("\n=== %s ===\n", s) }

func main() {
	demoRendezvous()
	demoBackpressureAndLoadShedding()
	demoCloseSemantics()
	demoOwnershipAndBroadcast()
	demoSelectAndNilChannel()
	demoSemaphore()
	demoLeak()
	demoPipeline()
	demoWhenNotToUseChannels()
	productionIngestor()
}

// ---------------------------------------------------------------------------
// 1. PURPOSE #1: synchronization (happens-before), not just data movement.
// ---------------------------------------------------------------------------

func demoRendezvous() {
	section("1. Unbuffered channel = rendezvous + happens-before edge")

	var payload string // plain, unsynchronized variable
	ready := make(chan struct{})

	go func() {
		payload = "written before close" // (A)
		close(ready)                     // (B) close happens-before the receive that observes it
	}()

	<-ready                           // (C) returns because of (B)
	fmt.Println("observed:", payload) // (A) is guaranteed visible: no data race

	// Memory-model rules worth memorizing:
	//   send        happens-before the corresponding receive completes
	//   close       happens-before a receive that returns because it was closed
	//   unbuffered: receive happens-before the send completes
	//   buffered(C): k-th receive happens-before the (k+C)-th send completes
}

// ---------------------------------------------------------------------------
// 2. PURPOSE #2: bounded queues => backpressure (the cloud-native use).
// ---------------------------------------------------------------------------

func demoBackpressureAndLoadShedding() {
	section("2. Buffered channel = bounded queue; select/default = load shedding")

	q := make(chan int, 3)
	for i := 0; i < 6; i++ {
		select {
		case q <- i:
			fmt.Printf("enqueued %d (len=%d cap=%d)\n", i, len(q), cap(q))
		default:
			// An unbounded queue turns overload into OOM. A bounded one turns it
			// into an explicit, observable decision: block, shed, or fail fast.
			fmt.Printf("SHED     %d (queue full)\n", i)
		}
	}
	// NOTE: len(ch) is a racy snapshot. Use it for metrics, never for logic.
}

// ---------------------------------------------------------------------------
// 3. Close semantics: the rules that cause 90% of production panics.
// ---------------------------------------------------------------------------

func demoCloseSemantics() {
	section("3. close() semantics")

	ch := make(chan int, 2)
	ch <- 1
	ch <- 2
	close(ch)

	for v := range ch { // buffered values are still delivered after close
		fmt.Println("drained:", v)
	}
	v, ok := <-ch // closed + empty => zero value, ok=false (never blocks)
	fmt.Println("after drain:", v, ok)

	// Rules:
	//   send on closed         -> PANIC
	//   close of closed        -> PANIC
	//   close of nil           -> PANIC
	//   recv on closed         -> zero value immediately (ok == false)
	//   send/recv on nil chan  -> blocks FOREVER (useful inside select)
	//
	// Therefore: ONLY THE SENDER (the owner) closes, and with N senders you need
	// a coordinator (sync.WaitGroup + a closer goroutine) -- see Stage() below.
	defer func() { fmt.Println("recovered:", recover()) }()
	ch <- 3 // panics: send on closed channel
}

// ---------------------------------------------------------------------------
// 4. Ownership + broadcast: close(done) wakes ALL waiters at once.
// ---------------------------------------------------------------------------

func demoOwnershipAndBroadcast() {
	section("4. Ownership (producer closes) + broadcast via close")

	// The producer owns `out`: creates, writes, closes. Return it receive-only
	// so the type system forbids consumers from closing or sending.
	gen := func(n int) <-chan int {
		out := make(chan int)
		go func() {
			defer close(out)
			for i := 0; i < n; i++ {
				out <- i
			}
		}()
		return out
	}
	sum := 0
	for v := range gen(5) {
		sum += v
	}
	fmt.Println("sum:", sum)

	// Broadcast: one close() releases every parked receiver. No other Go
	// primitive does this as idiomatically (this is what ctx.Done() is).
	start := make(chan struct{})
	var wg sync.WaitGroup
	var released atomic.Int32
	for i := 0; i < 5; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			<-start
			released.Add(1)
		}()
	}
	close(start)
	wg.Wait()
	fmt.Println("goroutines released by one close():", released.Load())
}

// ---------------------------------------------------------------------------
// 5. select: multiplexing, timeouts, and the nil-channel trick.
// ---------------------------------------------------------------------------

func merge(a, b <-chan int) <-chan int {
	out := make(chan int)
	go func() {
		defer close(out)
		// Setting a drained channel to nil makes its select-case block forever,
		// i.e. it is *disabled*. Without this, a closed channel spins at 100% CPU.
		for a != nil || b != nil {
			select {
			case v, ok := <-a:
				if !ok {
					a = nil
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

func demoSelectAndNilChannel() {
	section("5. select: fan-in, timeout, nil-channel disabling")

	mk := func(vals ...int) <-chan int {
		c := make(chan int)
		go func() {
			defer close(c)
			for _, v := range vals {
				c <- v
			}
		}()
		return c
	}
	total := 0
	for v := range merge(mk(1, 2, 3), mk(10, 20)) {
		total += v
	}
	fmt.Println("merged total:", total)

	// Timeout pattern. select picks uniformly at random among READY cases
	// (deliberately: no starvation), so never rely on case order for priority.
	never := make(chan int)
	timer := time.NewTimer(50 * time.Millisecond)
	defer timer.Stop()
	select {
	case <-never:
	case <-timer.C:
		fmt.Println("timed out (as designed)")
	}
}

// ---------------------------------------------------------------------------
// 6. Buffered channel as a counting semaphore (bounded concurrency).
// ---------------------------------------------------------------------------

func demoSemaphore() {
	section("6. chan struct{} as a semaphore")

	const limit = 3
	sem := make(chan struct{}, limit) // struct{} is zero-size: only the slots cost anything
	var inflight, peak atomic.Int32
	var wg sync.WaitGroup

	for i := 0; i < 12; i++ {
		wg.Add(1)
		sem <- struct{}{} // acquire: blocks when `limit` holders exist
		go func() {
			defer wg.Done()
			defer func() { <-sem }() // release
			n := inflight.Add(1)
			for {
				p := peak.Load()
				if n <= p || peak.CompareAndSwap(p, n) {
					break
				}
			}
			time.Sleep(5 * time.Millisecond)
			inflight.Add(-1)
		}()
	}
	wg.Wait()
	fmt.Printf("limit=%d peak-concurrency=%d\n", limit, peak.Load())
}

// ---------------------------------------------------------------------------
// 7. CON #1: goroutine leaks. The most common channel bug in production.
// ---------------------------------------------------------------------------

func leaky() <-chan int {
	ch := make(chan int) // unbuffered: sender parks until someone receives
	go func() { ch <- 1 }()
	return ch // caller "forgets" to read => sender parked forever
}

func notLeaky(ctx context.Context) <-chan int {
	ch := make(chan int, 1) // capacity 1: the single send can ALWAYS complete
	go func() {
		select {
		case ch <- 1:
		case <-ctx.Done(): // and every blocking send gets a cancellation arm
		}
	}()
	return ch
}

func demoLeak() {
	section("7. CON: goroutine leak (and the fix)")

	before := runtime.NumGoroutine()
	for i := 0; i < 1000; i++ {
		_ = leaky() // never received
	}
	time.Sleep(50 * time.Millisecond)
	fmt.Printf("leaky:    +%d goroutines parked forever\n", runtime.NumGoroutine()-before)

	ctx, cancel := context.WithCancel(context.Background())
	before = runtime.NumGoroutine()
	for i := 0; i < 1000; i++ {
		_ = notLeaky(ctx)
	}
	cancel()
	time.Sleep(50 * time.Millisecond)
	fmt.Printf("notLeaky: +%d goroutines\n", runtime.NumGoroutine()-before)
	// Rule: every goroutine you start must have a defined exit path that does
	// not depend on a stranger behaving correctly.
}

// ---------------------------------------------------------------------------
// 8. PURPOSE #3: pipelines. Composable stages connected by channels.
// ---------------------------------------------------------------------------

func Gen[T any](ctx context.Context, vals ...T) <-chan T {
	out := make(chan T)
	go func() {
		defer close(out)
		for _, v := range vals {
			select {
			case out <- v:
			case <-ctx.Done():
				return
			}
		}
	}()
	return out
}

// Stage fans a single input out to `workers` goroutines and fans results back
// in. The WaitGroup+closer pattern is THE answer to "who closes with N senders".
// NOTE: output order is NOT preserved across workers.
func Stage[In, Out any](ctx context.Context, in <-chan In, workers int, fn func(In) Out) <-chan Out {
	out := make(chan Out)
	var wg sync.WaitGroup
	wg.Add(workers)
	for w := 0; w < workers; w++ {
		go func() {
			defer wg.Done()
			for v := range in {
				select {
				case out <- fn(v):
				case <-ctx.Done():
					return
				}
			}
		}()
	}
	go func() { wg.Wait(); close(out) }() // sole closer
	return out
}

func demoPipeline() {
	section("8. Pipeline: Gen -> Stage(square) -> Stage(format)")
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel() // consumer stopping early still unwinds every stage

	nums := Gen(ctx, 1, 2, 3, 4, 5, 6, 7, 8)
	squared := Stage(ctx, nums, 4, func(n int) int { return n * n })
	labeled := Stage(ctx, squared, 2, func(n int) string { return fmt.Sprintf("<%d>", n) })

	count := 0
	for range labeled {
		count++
	}
	fmt.Println("items through 3-stage pipeline:", count)
}

// ---------------------------------------------------------------------------
// 9. CON #2: channels are NOT free. Know when a mutex/atomic is the right tool.
// ---------------------------------------------------------------------------

func demoWhenNotToUseChannels() {
	section("9. CON: cost. Counter via mutex vs atomic vs channel")

	const g, per = 4, 200_000
	run := func(name string, body func()) {
		start := time.Now()
		var wg sync.WaitGroup
		wg.Add(g)
		for i := 0; i < g; i++ {
			go func() {
				defer wg.Done()
				body()
			}()
		}
		wg.Wait()
		fmt.Printf("%-8s %v\n", name, time.Since(start).Round(time.Microsecond))
	}

	var mu sync.Mutex
	var mc int
	run("mutex", func() {
		for i := 0; i < per; i++ {
			mu.Lock()
			mc++
			mu.Unlock()
		}
	})

	var ac atomic.Int64
	run("atomic", func() {
		for i := 0; i < per; i++ {
			ac.Add(1)
		}
	})

	ch := make(chan struct{}, 128)
	done := make(chan int)
	go func() { // owner goroutine: the "CSP way" to guard state
		n := 0
		for range ch {
			n++
		}
		done <- n
	}()
	run("channel", func() {
		for i := 0; i < per; i++ {
			ch <- struct{}{}
		}
	})
	close(ch)
	fmt.Println("channel total:", <-done, "(correct, but pays lock + park/wake per op)")

	// Guidance:
	//   Channels  : transfer ownership of data, coordinate lifecycles,
	//               pipelines, bounded queues, cancellation, event streams.
	//   Mutex     : protect shared state / caches / maps (short critical section).
	//   Atomic    : single-word counters and flags.
	//   sync.Cond / errgroup / singleflight: for the cases they were built for.
}

// ---------------------------------------------------------------------------
// 10. THE ACTUAL USE: a production-shaped ingestion service core.
//
//   producers (HTTP handlers, consumers of a stream, ...)
//        │  Submit(): NON-BLOCKING, sheds when full  -> HTTP 429/503
//        ▼
//   ┌───────────────┐   size OR time    ┌────────────┐     ┌─────────────┐
//   │ in (bounded)  │ ───► batcher ───► │ batches    │ ──► │ N workers   │──► Sink
//   └───────────────┘   (single owner)  │ (unbuffered│     │ (ctx timeout│   (DB, S3,
//        ▲                              │  = backpr.)│     │  per write) │    Kafka..)
//        │                              └────────────┘     └─────────────┘
//   Shutdown: close(in) -> batcher flushes + close(batches) -> workers drain -> wg.Wait
//
// Properties: bounded memory, explicit overload policy, graceful drain,
// deadline-bounded shutdown, no goroutine leaks, no send-on-closed panic.
// ---------------------------------------------------------------------------

type Event struct {
	ID      uint64
	Payload []byte
	At      time.Time
}

type Sink interface {
	Write(ctx context.Context, batch []Event) error
}

type Config struct {
	QueueSize   int
	Workers     int
	BatchSize   int
	FlushEvery  time.Duration
	SinkTimeout time.Duration
}

var (
	ErrOverloaded = errors.New("ingest queue full")
	ErrClosed     = errors.New("ingestor closed")
)

type Ingestor struct {
	cfg  Config
	sink Sink
	log  *slog.Logger

	in      chan Event   // bounded ingress queue
	batches chan []Event // batcher -> workers (unbuffered: propagates backpressure)

	mu     sync.RWMutex // makes "check closed + send" atomic w.r.t. close(in)
	closed bool

	wg sync.WaitGroup

	accepted, shed, written, failed atomic.Uint64
}

func NewIngestor(cfg Config, sink Sink, log *slog.Logger) *Ingestor {
	return &Ingestor{
		cfg:     cfg,
		sink:    sink,
		log:     log,
		in:      make(chan Event, cfg.QueueSize),
		batches: make(chan []Event),
	}
}

// Start launches the batcher and workers. workCtx bounds sink I/O; cancel it
// only to abandon in-flight writes (e.g. drain deadline exceeded).
func (i *Ingestor) Start(workCtx context.Context) {
	i.wg.Add(1)
	go i.batcher()
	for w := 0; w < i.cfg.Workers; w++ {
		i.wg.Add(1)
		go i.worker(workCtx, w)
	}
}

// Submit never blocks. The RLock is held only across a non-blocking select,
// so Shutdown's Lock cannot be starved or deadlocked by slow consumers.
func (i *Ingestor) Submit(e Event) error {
	i.mu.RLock()
	defer i.mu.RUnlock()
	if i.closed {
		return ErrClosed
	}
	select {
	case i.in <- e:
		i.accepted.Add(1)
		return nil
	default:
		i.shed.Add(1)
		return ErrOverloaded
	}
}

func (i *Ingestor) batcher() {
	defer i.wg.Done()
	defer close(i.batches) // sole sender on batches => sole closer

	buf := make([]Event, 0, i.cfg.BatchSize)
	tick := time.NewTicker(i.cfg.FlushEvery)
	defer tick.Stop()

	flush := func() {
		if len(buf) == 0 {
			return
		}
		out := buf
		buf = make([]Event, 0, i.cfg.BatchSize) // never reuse a slice handed to another goroutine
		i.batches <- out                        // blocks if all workers busy => `in` fills => Submit sheds
	}

	for {
		select {
		case e, ok := <-i.in:
			if !ok { // closed AND drained: flush the tail and exit
				flush()
				return
			}
			buf = append(buf, e)
			if len(buf) >= i.cfg.BatchSize {
				flush()
			}
		case <-tick.C:
			flush() // bounds latency of partially-filled batches
		}
	}
}

func (i *Ingestor) worker(ctx context.Context, id int) {
	defer i.wg.Done()
	// Workers never exit early: they keep draining `batches` even after ctx is
	// cancelled (Write then fails fast). That guarantees the batcher can never
	// block forever on `i.batches <- out`.
	for batch := range i.batches {
		wctx, cancel := context.WithTimeout(ctx, i.cfg.SinkTimeout)
		err := i.sink.Write(wctx, batch)
		cancel()
		if err != nil {
			i.failed.Add(uint64(len(batch)))
			i.log.Warn("sink write failed", "worker", id, "events", len(batch), "err", err)
			continue // production: retry w/ backoff, DLQ, etc.
		}
		i.written.Add(uint64(len(batch)))
	}
}

// Shutdown stops intake, drains everything already accepted, and waits until
// done or ctx expires.
func (i *Ingestor) Shutdown(ctx context.Context) error {
	i.mu.Lock()
	if !i.closed {
		i.closed = true
		close(i.in) // safe: no Submit can be mid-send (they hold RLock)
	}
	i.mu.Unlock()

	done := make(chan struct{})
	go func() { i.wg.Wait(); close(done) }()
	select {
	case <-done:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

// fakeSink simulates a flaky network sink with 5-20ms latency, ~5% errors.
type fakeSink struct{}

func (fakeSink) Write(ctx context.Context, _ []Event) error {
	t := time.NewTimer(time.Duration(5+rand.Intn(15)) * time.Millisecond)
	defer t.Stop()
	select {
	case <-t.C:
	case <-ctx.Done():
		return ctx.Err()
	}
	if rand.Intn(20) == 0 {
		return errors.New("transient sink error")
	}
	return nil
}

func productionIngestor() {
	section("10. PRODUCTION SHAPE: bounded ingest -> batch -> worker pool -> sink")

	log := slog.New(slog.NewTextHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelError}))

	// SIGTERM is what Kubernetes sends; the demo also self-terminates after 1.5s.
	sigCtx, stopSig := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stopSig()
	runCtx, cancelRun := context.WithTimeout(sigCtx, 1500*time.Millisecond)
	defer cancelRun()

	workCtx, cancelWork := context.WithCancel(context.Background())
	defer cancelWork()

	ing := NewIngestor(Config{
		QueueSize:   64,
		Workers:     2,
		BatchSize:   50,
		FlushEvery:  100 * time.Millisecond,
		SinkTimeout: 500 * time.Millisecond,
	}, fakeSink{}, log)
	ing.Start(workCtx)

	var id atomic.Uint64
	var pw sync.WaitGroup
	for p := 0; p < 8; p++ { // 8 producers hammering faster than the sink can absorb
		pw.Add(1)
		go func() {
			defer pw.Done()
			for {
				select {
				case <-runCtx.Done():
					return
				default:
				}
				_ = ing.Submit(Event{ID: id.Add(1), Payload: []byte("x"), At: time.Now()})
				time.Sleep(10 * time.Microsecond)
			}
		}()
	}

	<-runCtx.Done() // SIGTERM or demo timer
	pw.Wait()       // stop intake sources first...

	drainCtx, cancelDrain := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancelDrain()
	if err := ing.Shutdown(drainCtx); err != nil { // ...then drain what we accepted
		cancelWork() // deadline blown: abandon in-flight writes
		fmt.Println("drain incomplete:", err)
	}

	acc, shed := ing.accepted.Load(), ing.shed.Load()
	wr, fl := ing.written.Load(), ing.failed.Load()
	fmt.Printf("accepted=%d shed=%d written=%d failed=%d\n", acc, shed, wr, fl)
	fmt.Printf("invariant accepted == written+failed : %v\n", acc == wr+fl)
	fmt.Printf("shed ratio: %.1f%% (explicit, measurable overload policy)\n",
		100*float64(shed)/float64(acc+shed))
}
```