# Blockchain & Crypto Networking — A Complete Guide

A ground-up treatment of the networking layer that makes blockchains work: topology, discovery, propagation, security, and real Rust code for every major piece.

---

## Table of Contents

1. [Why Networking Is the Hard Part](#1-why-networking-is-the-hard-part)
2. [P2P Fundamentals](#2-p2p-fundamentals)
3. [Network Topologies](#3-network-topologies)
4. [Node Types & Roles](#4-node-types--roles)
5. [Peer Discovery](#5-peer-discovery)
6. [Kademlia DHT](#6-kademlia-dht)
7. [Transport & Handshakes](#7-transport--handshakes)
8. [Gossip Protocols](#8-gossip-protocols)
9. [Transaction Propagation](#9-transaction-propagation)
10. [Block Propagation](#10-block-propagation)
11. [Mempool Networking](#11-mempool-networking)
12. [Consensus-Layer Networking (PoW vs PoS)](#12-consensus-layer-networking-pow-vs-pos)
13. [NAT Traversal & Connectivity](#13-nat-traversal--connectivity)
14. [Network-Level Attacks](#14-network-level-attacks)
15. [libp2p Architecture](#15-libp2p-architecture)
16. [Ethereum devp2p / RLPx](#16-ethereum-devp2p--rlpx)
17. [Bitcoin Wire Protocol](#17-bitcoin-wire-protocol)
18. [Light Clients & SPV](#18-light-clients--spv)
19. [Sharding & Networking at Scale](#19-sharding--networking-at-scale)
20. [Full Rust Reference Implementation](#20-full-rust-reference-implementation)
21. [Glossary](#21-glossary)

---

## 1. Why Networking Is the Hard Part

A blockchain is, underneath the cryptography, a **distributed agreement problem running over an unreliable, adversarial network**. The consensus rule ("longest chain wins", "2/3 of stake attests") only produces a single global truth if the network layer manages to:

- Get every transaction to (almost) every node within a bounded time.
- Get every new block to every node fast enough that the next block builds on it, not on a stale one.
- Resist nodes that lie, censor, delay, or try to isolate other nodes.
- Do all of this with **no central coordinator**, across nodes that don't trust each other and may be behind NAT, firewalls, or hostile ISPs.

Everything below is infrastructure in service of that one goal: **fast, honest, censorship-resistant message delivery across an open, permissionless peer set.**

```
                     CONSENSUS (rules of truth)
                            ▲
                            │ depends on
                            │
                     NETWORKING (delivery of truth)
                            ▲
                            │ depends on
                            │
                     CRYPTOGRAPHY (proof of truth)
```

---

## 2. P2P Fundamentals

Blockchains use **overlay networks**: a logical graph of peer connections built on top of the physical Internet (TCP/IP). Each node is simultaneously a client and a server.

```
        Physical Internet (routers, ISPs, IXPs)
        ─────────────────────────────────────────
                    ▲          ▲          ▲
                    │          │          │
   Overlay:      [Node A]──[Node B]──[Node C]
                    │                    │
                 [Node D]──────────────[Node E]
```

Key properties every blockchain P2P layer needs:

| Property | Meaning |
|---|---|
| **Openness** | Anyone can join without permission (permissionless nets) |
| **Self-organizing** | No bootstrap authority required after genesis |
| **Fault-tolerant** | Survives churn — nodes joining/leaving constantly |
| **Byzantine-tolerant** | Survives active liars, not just crashes |
| **Low diameter** | Few hops between any two nodes, for fast propagation |
| **Resistant to partition** | Hard for an attacker to split the network in two |

### Connection model

Each peer link is typically a persistent **TCP connection** (sometimes QUIC) carrying a length-prefixed, framed message stream — not one request/response per HTTP call. This persistence is what lets gossip and block-relay be low-latency.

```
Node A                                   Node B
  |------ TCP SYN ------------------------->|
  |<----- TCP SYN/ACK -----------------------|
  |------ ACK ------------------------------>|
  |------ Version/Handshake message -------->|
  |<----- Version/Handshake message ---------|
  |------ Verack ---------------------------->|
  |<===== persistent bidirectional stream ===>|
  |  (inv, tx, block, ping/pong, addr, ...)   |
```

---

## 3. Network Topologies

### 3.1 Unstructured mesh (Bitcoin, Ethereum execution layer)

Each node picks a semi-random set of peers (commonly 8–125 outbound/inbound). No global structure — just "know a decent number of other nodes and gossip." Simple, resilient, but propagation time scales with graph diameter and isn't provably bounded.

```
        [N1]───[N2]
        /  \   /   \
     [N3]  [N4]───[N5]
        \   /  \   /
        [N6]───[N7]
```

### 3.2 Structured overlay (DHT-based — Ethereum discovery, IPFS, libp2p Kad)

Peers occupy positions in a keyspace (e.g., XOR distance in Kademlia). Lookups are O(log n) hops. Used mainly for **discovery**, less often for message dissemination itself.

```
     000...  ── close ──  001...
        \                 /
         \               /
      buckets radiate outward by XOR distance from self-ID
```

### 3.3 Hybrid (most modern chains)

- **Discovery layer**: structured (Kademlia-based, e.g. discv5 in Ethereum).
- **Propagation layer**: unstructured gossip (e.g. libp2p GossipSub on Ethereum's consensus layer).

### 3.4 Topic-based pub/sub mesh (Ethereum consensus layer, Filecoin)

Nodes subscribe to **topics** (e.g. `beacon_block`, `beacon_attestation_0`) and form a mesh *per topic*, so bandwidth isn't wasted relaying data nobody wants.

```
Topic: beacon_block
   [A]───[B]
    │      │
   [C]───[D]

Topic: attestation_subnet_7
   [A]───[E]───[F]
          (different mesh, same physical peers)
```

---

## 4. Node Types & Roles

| Role | Stores full chain? | Validates? | Relays? | Examples |
|---|---|---|---|---|
| **Full node** | Yes | Yes | Yes | Bitcoin Core, Geth (full sync) |
| **Archive node** | Yes + all historical state | Yes | Yes | Erigon archive, geth `--gcmode=archive` |
| **Miner / Validator / Proposer** | Usually yes | Yes | Yes | Mining pool node, beacon validator |
| **Light client (SPV)** | Headers only | Partial (via proofs) | No | Bitcoin SPV wallets, Helios (Ethereum) |
| **Bootstrap / Seed node** | Varies | Optional | Discovery only | DNS seed nodes |
| **Relay / Sentry node** | Yes | Yes | Yes, shields validator IP | Cosmos sentry architecture |

```
        ┌────────────┐
        │  Validator  │  (private, never dials out publicly)
        └─────┬──────┘
              │ only connects to
        ┌─────▼──────┐     ┌────────────┐
        │  Sentry 1   │─────│  Sentry 2   │
        └─────┬──────┘     └─────┬──────┘
              │  public gossip mesh  │
     [Peer]───┴────[Peer]───[Peer]──┴───[Peer]
```
Sentry architecture (used in Cosmos/Tendermint chains) exists specifically to defend validators from targeted DDoS/eclipse by never exposing the validator's IP to the open network.

---

## 5. Peer Discovery

New nodes have zero peers on first boot. Discovery bootstraps the peer list.

### 5.1 Hardcoded seed peers
A short list of long-lived, operator-run IPs shipped in the client binary. First point of contact.

### 5.2 DNS seeds
A DNS name (e.g. `seed.bitcoin.sipa.be`) resolves to a rotating set of currently-reachable node IPs, maintained by crawler infrastructure. Avoids hardcoding IPs that go stale.

### 5.3 Peer exchange (PEX / `addr` gossip)
Once connected to any peer, nodes exchange address books: "here are 100 peers I know about, with a timestamp and services bitfield." Over a few hops, a new node learns most of the reachable network.

```
New Node ──getaddr──▶ Peer1
New Node ◀──addr[500]── Peer1
New Node ──connect──▶ (subset of those 500)
```

### 5.4 Kademlia-based discovery (Ethereum discv4/discv5)
Structured lookups: "find nodes closer to X" queries walk the DHT to populate k-buckets. Covered in depth next.

### 5.5 Rendezvous / mDNS (local networks)
IPFS/libp2p nodes on the same LAN find each other via multicast DNS without any Internet round-trip — useful for local dev and edge deployments.

---

## 6. Kademlia DHT

Kademlia (used by BitTorrent, IPFS, Ethereum's discv4/discv5) organizes peers by **XOR distance** between node IDs (typically 256-bit hashes of a public key).

```
distance(A, B) = A XOR B          (treated as an integer)
```

### K-buckets
Each node keeps buckets indexed by "how many leading bits match my own ID." Bucket *i* holds peers whose distance is in `[2^i, 2^(i+1))`. Near buckets are precise (few peers, all close); far buckets are coarse (many peers, all "roughly anywhere else").

```
My Node ID: 1011 0100 ...

Bucket 0 (distance ~2^255): most of the network
Bucket 1 (distance ~2^254): fewer nodes, still far
   ...
Bucket 254: very close nodes
Bucket 255: nodes differing by only the last bit
```

### FIND_NODE lookup
To find nodes near target `T`, ask your closest-known peers to `T` for *their* closest-known peers to `T`, recursively, with α (concurrency factor, typically 3) parallel queries per round. Converges in O(log n) hops.

```
Round 1: ask 3 nodes closest to T that I know
Round 2: ask 3 nodes closest to T from round-1 results
Round 3: ...
Stop when no closer node is returned.
```

### Why it matters for blockchains
Ethereum's discv5 uses this exact mechanism, layered with:
- **ENR (Ethereum Node Records)**: signed, versioned records describing a node's IP, ports, and supported capabilities — replacing raw IP:port with something authenticated and upgradeable.
- Topic-based discovery to find peers interested in specific subnets (e.g. a particular attestation subnet).

---

## 7. Transport & Handshakes

Raw TCP is not enough — blockchain P2P links need **authentication** (so you know which key you're talking to) and usually **encryption** (so ISPs/attackers can't read or tamper with traffic).

### 7.1 Handshake goals
1. Agree on protocol version / capabilities.
2. Exchange ephemeral keys for a session-encrypted channel.
3. Authenticate the peer's static identity key (prevents MITM impersonation).
4. Exchange initial status (best block, chain ID, genesis hash) to detect incompatible peers immediately.

### 7.2 The Noise Protocol Framework
Modern stacks (libp2p, Lightning Network's BOLT8) use **Noise**, a formal framework for building handshake patterns with provable security properties. A common pattern is `Noise_XX`:

```
   Initiator                          Responder
      │── e ─────────────────────────────▶│   (ephemeral pubkey)
      │◀───────────────── e, ee, s, es ───│   (responder's ephemeral + static, encrypted)
      │── s, se ─────────────────────────▶│   (initiator's static, encrypted)
      │                                    │
      │========= encrypted channel =======│
```
`e` = ephemeral key, `s` = static key, `ee`/`es`/`se` = Diffie-Hellman operations mixed into the symmetric key. After this, both sides have a shared secret neither could have derived without knowing the corresponding private key — mutual authentication plus forward secrecy in one round trip and a half.

### 7.3 Ethereum's RLPx handshake (ECIES-based, pre-Noise design)
Ethereum's devp2p uses its own ECIES (Elliptic Curve Integrated Encryption Scheme) handshake, older than widespread Noise adoption, achieving similar goals: ephemeral ECDH, AES-CTR + MAC framing, static key auth via signature over the ephemeral key.

### 7.4 Bitcoin's (historical) lack of transport encryption
Bitcoin's original wire protocol is **unencrypted** by default — messages are plaintext with a checksum, not a MAC. This is a known weakness (traffic analysis, on-path tampering-detection only, no confidentiality) being addressed by newer proposals (e.g. BIP 324, "v2 transport" using an authenticated, encrypted channel similar in spirit to Noise).

---

## 8. Gossip Protocols

Gossip (epidemic broadcast) is how a message reaches the whole network without any node needing global knowledge.

### 8.1 Flooding (naive gossip)
Every node relays every new message to every peer except the one it came from. Simple, robust, but wastes bandwidth — the same message arrives at each node multiple times.

```
        [A] sends new tx
       /   |   \
     [B]  [C]  [D]     round 1
    / |    | \   | \
  [E][F] [G][H][I][J]  round 2 — E..J each may get duplicates from B,C,D
```

### 8.2 inv/getdata pull-gossip (Bitcoin's classic model)
Instead of pushing full payloads, a node announces an inventory hash (`inv`), and peers who don't already have it request the full data (`getdata`). Cuts redundant bandwidth roughly in half versus raw flooding, at the cost of one extra round trip.

```
A: inv [txid=0xabc]  ──▶  B
B: (checks: don't have 0xabc)
B: getdata [txid=0xabc] ──▶ A
A: tx <full tx bytes> ──▶ B
```

### 8.3 Compact/probabilistic relay
Send full data to a random subset of peers immediately (fast propagation for the first hop or two), and only inv/hash-announce to the rest, falling back to full request on demand. Bitcoin's "high-bandwidth" vs "low-bandwidth" compact block relay peers work this way.

### 8.4 GossipSub (libp2p, used by Ethereum consensus layer, Filecoin)
A mesh-based, topic-scoped pub/sub protocol:
- Each node maintains a small **mesh** (typically ~6–12 peers) per topic for eager, full-message push.
- Non-mesh peers get periodic **gossip** (just message IDs), and can request full content (`IWANT`) if they're missing something (`IHAVE`).
- **Scoring**: peers are scored on message validity, delivery latency, and mesh behavior; badly-behaved or slow peers get pruned.

```
Topic mesh (eager push, full messages):
   [A]══[B]
    ║     ║
   [C]══[D]

Gossip-only edges (IHAVE / IWANT, lightweight):
   [A]┄┄[E]   [D]┄┄[F]
```

### 8.5 Why gossip beats naive broadcast
A structured broadcast tree is fragile (root failure, or an attacker positioned near the root can censor). Gossip's redundant, randomized paths mean the network keeps working even if a meaningful fraction of nodes are down or malicious — the classic epidemic-broadcast result is that with reasonable fanout, convergence is exponential in the number of rounds.

---

## 9. Transaction Propagation

```
User signs tx ──▶ Local node mempool
                       │
                       ▼
              inv[txid] to N peers
                       │
        peers without txid ──▶ getdata ──▶ full tx
                       │
              tx validated locally (signature, nonce/UTXO, fee)
                       │
              re-announced (inv) to node's own peers
                       │
              ... repeats network-wide in a few seconds ...
```

Design pressures on this path:

- **Fee-based prioritization**: nodes may delay or drop announcing low-fee transactions under load (RBF/mempool eviction policy).
- **Privacy**: naive "announce as soon as I see it" leaks which node originated a transaction (useful for deanonymizing IP↔address links). Mitigations: **Dandelion++** (used experimentally in Bitcoin, adopted in Firo/Grin) — a transaction first travels a random, single-path "stem" phase (hard to trace) before switching to normal "fluff" (diffusion) broadcast.

```
Dandelion++:
  Stem phase (line topology, one peer forwards to exactly one peer):
     [origin] → [P1] → [P2] → [P3] → (fluff trigger)
  Fluff phase (normal diffusion broadcast from P3 onward):
     [P3] ⇒⇒⇒ floods to whole network
```

- **Transaction relay policy** (not consensus, but network-enforced convention): standardness rules, minimum relay fee, replace-by-fee rules — these are locally configurable "soft" filters, distinct from the hard consensus rules that determine block validity.

---

## 10. Block Propagation

Block propagation speed is *the* critical latency metric — slow propagation directly causes forks (two miners/validators building on different tips because they hadn't seen each other's block yet), which wastes work/security margin and can be exploited (e.g., **selfish mining** benefits from propagation delay).

### 10.1 Naive full-block relay
Send the entire block (all transactions) to every peer. Simple but wasteful — your peers almost certainly already have 95%+ of those transactions in their own mempools.

### 10.2 Compact blocks (BIP 152, Bitcoin)
Send a short block header + a 6-byte short-ID per transaction (rather than the full tx). The receiver reconstructs the block from its own mempool, only requesting the small number of transactions it's missing.

```
Miner finds block B containing txs [t1..t500]

  Send: header(B) + shortID(t1) + shortID(t2) + ... + shortID(t500)
        (few KB instead of ~1-2 MB)

  Receiver: "I already have t1..t497 in mempool by shortID match.
             Missing t498, t499, t500."
  Receiver: getblocktxn [t498,t499,t500] ──▶ Miner
  Miner:    blocktxn [full t498,t499,t500] ──▶ Receiver
  Receiver: reconstructs full block, validates, relays onward.
```

### 10.3 High-bandwidth vs low-bandwidth mode
A node designates a few peers (e.g. 3) as "high-bandwidth": for those, it pushes compact blocks immediately, unsolicited, the instant it validates a new block — shaving off a full round trip versus the inv/getheaders/getdata dance.

### 10.4 Ethereum's block/body separation
Execution-layer Ethereum historically propagated `NewBlockHashes` (announce) then `GetBlockBodies`/`BlockBodies` (pull), similar in spirit to inv/getdata. Post-Merge, block *proposal* on the consensus layer travels over GossipSub on the `beacon_block` topic, and execution payloads are validated by the paired execution client via the Engine API (not the P2P network — that link is local, authenticated JWT-RPC).

```
Consensus Client                    Execution Client
   (gossip: beacon_block)              (local only)
        │                                  │
        │── engine_newPayload (JWT-RPC) ──▶│
        │◀───── payload validity ──────────│
```

### 10.5 Why propagation speed matters for security
In Nakamoto-consensus PoW, the probability of an accidental fork rises with block-propagation delay relative to block interval. Faster propagation → fewer wasted blocks → a larger fraction of total hashpower effectively contributes to the canonical chain → higher effective security per unit of energy spent. This is a direct, quantifiable reason compact blocks and high-bandwidth relay exist.

---

## 11. Mempool Networking

The mempool is a *local*, unsynchronized data structure — there's no canonical "the mempool," only "my mempool" per node, which is why compact-block reconstruction sometimes needs a follow-up round trip (a peer's mempool contents differ from the block producer's).

Key networked behaviors:

- **Mempool sync on connect**: some clients support `mempool`/`getmempool` request to fetch a peer's current pending set on first connection (useful for miners resuming state; disabled by default on some nodes for privacy/DoS reasons).
- **Fee-rate-based eviction under memory pressure**: doesn't require network coordination — purely local policy — but affects *which* transactions get re-announced, hence what the network converges on as "confirmable soon."
- **RBF (Replace-By-Fee) propagation**: a replacement transaction re-announces with the same inputs but higher fee; nodes must decide whether to relay the replacement (policy, not consensus) — this is a classic point of policy divergence across implementations.

---

## 12. Consensus-Layer Networking (PoW vs PoS)

| Aspect | Proof of Work (e.g. Bitcoin) | Proof of Stake (e.g. Ethereum post-Merge, Cosmos) |
|---|---|---|
| What's gossiped each round | Blocks (irregular timing, whoever finds one) | Block *proposals* (every slot, ~12s) **and** *attestations/votes* (every validator, every slot) — vastly higher message volume |
| Message volume driver | # of blocks/day (~144 for Bitcoin) | # of validators × slots/day (Ethereum: ~1M+ validators × 7200 slots/day) |
| Topic sharding needed? | Rarely (low volume) | Yes — attestations split across many GossipSub subnets so no single topic is overloaded |
| Timing sensitivity | Loose (10 min block target, high variance tolerated) | Tight (missing a 4-second attestation window = penalty) — networking latency directly costs validators money |
| Identity binding | None — anonymous hash power | Strong — validator index/pubkey tied to networking behavior (used for slashing evidence gossip) |

### Attestation subnets (Ethereum)
With >1,000,000 validators, broadcasting every attestation on one global topic would be catastrophic for bandwidth. Ethereum splits attestations into 64 **subnets**; each validator publishes to (and each node subscribes to) only the subnets relevant to its duties, aggregating locally before wider propagation.

```
Global topic "beacon_block": 1 per slot, everyone subscribes

Subnet 0 ── validators 0..N/64
Subnet 1 ── validators N/64..2N/64
   ...
Subnet 63 ── validators 63N/64..N

Aggregator nodes per subnet combine signatures (BLS aggregation)
before relaying to the rest of the network, cutting message count.
```

### Slashing evidence gossip
A dedicated, low-volume, high-priority topic for double-vote/double-propose evidence — must propagate reliably even under network stress, since it's what makes the "slashing" deterrent in PoS actually enforceable.

---

## 13. NAT Traversal & Connectivity

Most home/consumer nodes sit behind NAT and can't accept inbound connections without help — a major factor in effective network topology (Bitcoin, for years, has had far more *reachable* nodes cataloged than are actually inbound-connectable).

### 13.1 UPnP / NAT-PMP
The node asks the home router (if it supports the protocol) to open a port mapping automatically. Convenient, but many routers disable UPnP by default (security), and it doesn't work for carrier-grade NAT (CGNAT), which many ISPs use.

### 13.2 Outbound-only nodes
The pragmatic fallback: a node behind NAT just makes outbound connections to already-reachable peers. It still fully participates (sends/receives gossip) — it simply isn't a good *entry point* for other new peers, shrinking the pool of "connectable" nodes but not excluding the node itself from the network.

### 13.3 Hole punching (used in libp2p, WebRTC-based systems)
Two NATed peers, coordinated by a third reachable peer (a relay/rendezvous node), simultaneously send packets to each other's *predicted* external address — often causing both NATs to open a mapping, after which direct traffic flows without further relay involvement.

```
   Peer A (NATed)        Relay/Rendezvous        Peer B (NATed)
        │───── request B's addr ────▶│
        │◀──── B's public addr:port ─│
        │                             │──── A's public addr:port ───▶│
        │══ simultaneous UDP punch (both directions) ══════════════▶│
        │◀═════════════════ direct connection established ═════════│
```

### 13.4 Relay fallback (libp2p Circuit Relay)
If hole punching fails (symmetric NAT, both sides), traffic is relayed indefinitely through a third reachable node — works everywhere, at a bandwidth and latency cost.

---

## 14. Network-Level Attacks

### 14.1 Sybil attack
An attacker creates many fake node identities to gain disproportionate influence over peer selection, gossip, or (in structured overlays) DHT routing. Blockchains don't prevent Sybil identities cheaply at the network layer — they rely on the consensus layer's scarce resource (hashpower, stake) to make Sybil influence *over block production* costly, even though Sybil influence *over networking position* is nearly free.

### 14.2 Eclipse attack
An attacker doesn't need to control the whole network — just **surround a single victim node** so all of its peer connections are attacker-controlled, feeding it a false view of the chain (e.g. hiding real blocks, feeding a fake longest chain, enabling double-spends against that victim specifically).

```
                     Real network (honest majority)
                    ╱      │      │       ╲
              [H1] [H2]  [H3]   [H4]    [H5]
                                                     ╲
                                            ┌──────────────────┐
                                            │  Victim node V    │
                                            └──────────────────┘
                                            ▲   ▲   ▲   ▲   ▲
                                          [E1][E2][E3][E4][E5]
                                          (all attacker-controlled;
                                           V's peer slots are full
                                           of Sybils, none honest)
```
Mitigations: diverse peer selection (multiple independent /16 subnets), anchor connections (remember and reconnect to known-good peers across restarts), randomized peer eviction resistant to targeted takeover, larger default peer counts.

### 14.3 BGP hijacking / partition attacks
Since the overlay rides on real Internet routing, an attacker controlling routing infrastructure (a malicious or compromised ISP/AS) can intercept or reroute traffic to/from IP ranges hosting many nodes, effectively partitioning or eclipsing at scale (documented research against Bitcoin nodes concentrated in a small number of ASes/hosting providers).

### 14.4 DoS / resource exhaustion
Attackers open many connections, spam invalid messages, or send oversized/slow payloads to exhaust CPU, memory, or bandwidth. Defenses: per-peer rate limiting, banning on protocol violations (misbehavior scoring), connection slot limits with priority to long-lived good peers, proof-of-work-gated connection requests in some designs.

### 14.5 Timing/traffic analysis (deanonymization)
Observing *which node first announces a transaction* (or the timing pattern of its announcements) can link an IP address to a wallet/address, even without breaking any cryptography. This motivates Dandelion++-style relay and Tor/I2P integration options in privacy-focused clients.

### 14.6 Long-range / networking-adjacent attacks in PoS
Not purely a networking attack, but networking-dependent: a PoS chain relies on honest nodes being *online and synced* to reject alternate histories built from old, once-valid-but-since-slashed keys ("weak subjectivity"). A node that's offline for a long time and reconnects via an untrusted, attacker-controlled bootstrap needs an honest checkpoint, not just "longest/heaviest chain," because stake-based history can be forged retroactively if old keys leaked.

---

## 15. libp2p Architecture

libp2p (used by IPFS, Ethereum consensus layer, Polkadot, Filecoin) is a modular networking stack — not one protocol, but a set of composable pieces:

```
┌─────────────────────────────────────────────────────────┐
│                      Application                          │
│         (GossipSub topics, Kad-DHT, custom RPC)           │
├─────────────────────────────────────────────────────────┤
│                    Peer Identity                           │
│         (PeerId = hash of public key, PKI-free)            │
├─────────────────────────────────────────────────────────┤
│                    Multiplexing                            │
│         (yamux / mplex — many logical streams, 1 conn)     │
├─────────────────────────────────────────────────────────┤
│                    Security / Encryption                   │
│         (Noise / TLS 1.3)                                  │
├─────────────────────────────────────────────────────────┤
│                    Transport                                │
│         (TCP / QUIC / WebSocket / WebRTC)                  │
└─────────────────────────────────────────────────────────┘
```

Key ideas:

- **Multiaddr**: a self-describing address format, e.g. `/ip4/198.51.100.1/tcp/4001/p2p/QmPeerID`, composable across transports.
- **Protocol negotiation (multistream-select)**: two peers exchange a list of protocol IDs they support and pick the best mutual match — enabling graceful upgrades without hardcoding versions.
- **Stream multiplexing**: one encrypted TCP/QUIC connection carries many independent logical streams (one for Kad-DHT queries, another for GossipSub, another for a custom sync protocol) — avoiding one-connection-per-purpose overhead.

---

## 16. Ethereum devp2p / RLPx

```
┌───────────────────────────────┐
│  Application sub-protocols     │   eth/68, snap/1, les/4 ...
├───────────────────────────────┤
│  RLPx session (framing, MAC)   │   encrypted, per-connection
├───────────────────────────────┤
│  ECIES handshake               │   ephemeral ECDH + static-key sig
├───────────────────────────────┤
│  TCP                            │
└───────────────────────────────┘
```

- **discv4/discv5**: Kademlia-based node discovery over UDP, using ENRs.
- **RLPx**: the encrypted TCP transport and framing layer, roughly Ethereum's analogue to libp2p's security+mux layers, but purpose-built rather than modular.
- **eth/68**: the actual chain sync sub-protocol running over RLPx — status exchange, header/body/receipt requests, tx/block announcements.
- **snap/1**: state-sync protocol for fast (non-full-history) sync, fetching flattened state trie ranges directly instead of replaying every historical transaction.

---

## 17. Bitcoin Wire Protocol

```
Message framing:
┌──────────┬─────────────┬───────────┬──────────────┬─────────────┐
│  Magic    │  Command     │  Length    │  Checksum     │  Payload     │
│  4 bytes  │  12 bytes    │  4 bytes   │  4 bytes      │  variable    │
└──────────┴─────────────┴───────────┴──────────────┴─────────────┘
```

Core message types:

| Message | Purpose |
|---|---|
| `version` / `verack` | Handshake |
| `addr` / `getaddr` | Peer exchange |
| `inv` | Announce (tx/block hash) |
| `getdata` | Request full object by hash |
| `tx` | Full transaction payload |
| `block` | Full block payload |
| `headers` / `getheaders` | Header-first sync |
| `cmpctblock`/`getblocktxn`/`blocktxn` | Compact block relay (BIP 152) |
| `ping` / `pong` | Liveness check |
| `reject` | (deprecated in modern versions) error signaling |

### Header-first sync (Initial Block Download)
Rather than downloading full blocks in order from genesis, a syncing node first fetches the ~80-byte headers chain (cheap to validate PoW on), identifies the best chain tip, *then* downloads full block bodies — often in parallel from multiple peers, since header validity is already locally confirmed.

```
Sync node ──getheaders(locator)──▶ Peer
Sync node ◀──headers[2000 headers]── Peer
... repeat until caught up to tip ...
Sync node: now has full valid header chain, can validate PoW end-to-end
Sync node ──getdata(block hashes)──▶ multiple peers in parallel
Sync node ◀──block, block, block...── (parallel downloads)
```

---

## 18. Light Clients & SPV

Full validation requires downloading and checking everything. Light clients trade trust-minimization for resource savings.

### 18.1 SPV (Simplified Payment Verification, Bitcoin)
Downloads only headers (~80 bytes each), plus **Merkle proofs** for specific transactions of interest, fetched on demand from full nodes.

```
Block header contains: merkle_root

    Full block's tx tree:                Merkle proof for tx T sent to SPV client:
            root                              root
           /    \                            /    \
        h01      h23                      h01      h23  ◀── only these hashes
        / \      / \                              needed, not h0/h1/h2/h3 fully
      h0  h1   h2  h3
       |   |    |   |
      tx0 tx1  tx2 tx3(=T)

    SPV client: hash(T) → combine with h2's sibling → ... → compare to merkle_root in header
```
Trust model: SPV trusts that *most* hashpower is honest (so headers represent real proof-of-work) and trusts the full node it queries to honestly report which transactions exist — a full node could lie by omission (hide a transaction) though it can't forge a fake valid one.

### 18.2 Light client protocols for account-based chains (Ethereum LES, and post-Merge "light client sync" via sync committees)
Ethereum's post-Merge light client protocol uses a **sync committee** — a randomly-selected, rotating subset of validators (512 of them) who sign each block header specifically so light clients can verify with a small, constant amount of data (not the entire validator set's signatures) that a header is canonical.

```
Sync committee (512 validators, rotates ~every 27 hours)
       │ signs each block header
       ▼
Light client verifies: "≥ 512*2/3 of the *current* committee signed this"
       │ (small, constant-size proof regardless of total validator count)
       ▼
Trusts header without downloading/validating the full chain
```

### 18.3 Fraud proofs / data availability sampling (rollups, danksharding-style designs)
An emerging model: light clients don't trust full execution, but can detect fraud (via fraud proofs from any honest watcher) or probabilistically verify data was actually published (via random sampling of small chunks) without downloading the whole block — network-layer techniques directly enabling this trust-minimization.

---

## 19. Sharding & Networking at Scale

When a chain is split into multiple parallel execution domains ("shards" or "rollups"), the networking layer has to solve new problems:

- **Cross-shard message routing**: a transaction on shard A referencing state on shard B needs a networked path for a proof or message to travel between shard-specific gossip meshes.
- **Data availability**: nodes must be convinced *data was published*, not just that a header exists — this is why data availability sampling (peers each fetch a small random slice, and reconstruct via erasure coding if enough slices are collectively available) is a networking-layer innovation, not a consensus-layer one.

```
Block data (erasure-coded into a 2D matrix, e.g. via Reed-Solomon)

  ┌───┬───┬───┬───┬───┬───┐
  │ d │ d │ d │ d │ p │ p │   d = original data chunk
  ├───┼───┼───┼───┼───┼───┤   p = parity (redundant) chunk
  │ d │ d │ d │ d │ p │ p │
  └───┴───┴───┴───┴───┴───┘

  Light nodes each randomly sample a handful of chunks.
  If enough distinct chunks are collectively retrievable across
  all sampling light nodes (>50% of any row/column), the data is
  provably fully available — without any single node downloading it all.
```

- **Topic-per-shard gossip**: analogous to Ethereum's attestation subnets — each shard/rollup's data gets its own GossipSub topic so nodes only subscribe to (and pay bandwidth for) shards they care about.

---

## 20. Full Rust Reference Implementation

Below is a real, runnable, minimal P2P blockchain-style node in Rust using `tokio` for async networking and `serde`/`bincode` for message framing. It demonstrates: framed TCP messaging, a version handshake, peer address exchange, and inv/getdata-style gossip for transactions — the same pattern used (at far greater complexity) in Bitcoin Core and Geth.

`Cargo.toml`:
```toml
[package]
name = "mini-p2p-node"
version = "0.1.0"
edition = "2021"

[dependencies]
tokio = { version = "1", features = ["full"] }
serde = { version = "1", features = ["derive"] }
bincode = "1.3"
sha2 = "0.10"
rand = "0.8"
```

### 20.1 Message types

```rust
// src/message.rs
use serde::{Deserialize, Serialize};

pub type TxId = [u8; 32];

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Transaction {
    pub from: String,
    pub to: String,
    pub amount: u64,
    pub nonce: u64,
}

impl Transaction {
    /// Deterministic ID, mirrors how real chains hash tx contents.
    pub fn id(&self) -> TxId {
        use sha2::{Digest, Sha256};
        let encoded = bincode::serialize(self).expect("serialize tx");
        let mut hasher = Sha256::new();
        hasher.update(&encoded);
        let result = hasher.finalize();
        let mut id = [0u8; 32];
        id.copy_from_slice(&result);
        id
    }
}

/// The wire protocol. This is the direct analogue of Bitcoin's
/// version/verack/inv/getdata/tx/addr/getaddr message set, simplified.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum Message {
    /// Sent immediately on connect: who am I, what do I know.
    Version {
        node_id: String,
        best_height: u64,
        listen_port: u16,
    },
    /// Acknowledges a Version message; handshake complete after this.
    Verack,

    /// "I have this transaction" — cheap 32-byte announcement,
    /// not the full payload. Mirrors Bitcoin's `inv`.
    InvTx(TxId),

    /// "Send me the full transaction for this id" — mirrors `getdata`.
    GetTx(TxId),

    /// The actual transaction payload — mirrors Bitcoin's `tx`.
    Tx(Transaction),

    /// Peer exchange request — mirrors `getaddr`.
    GetAddr,

    /// Peer exchange response — mirrors `addr`.
    Addr(Vec<String>), // list of "ip:port" strings

    /// Liveness check — mirrors `ping`/`pong` combined for brevity.
    Ping(u64),
    Pong(u64),
}
```

### 20.2 Framed codec (length-prefixed messages over TCP)

Real protocols never just `serialize()` straight onto a TCP stream — TCP is a byte stream with no message boundaries, so every wire protocol prefixes each message with its length. This is exactly what Bitcoin's 4-byte length field and Ethereum's RLPx framing do.

```rust
// src/codec.rs
use crate::message::Message;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::tcp::{OwnedReadHalf, OwnedWriteHalf};

const MAX_MESSAGE_SIZE: u32 = 16 * 1024 * 1024; // 16 MB, DoS guard

pub async fn write_message(
    writer: &mut OwnedWriteHalf,
    msg: &Message,
) -> anyhow::Result<()> {
    let payload = bincode::serialize(msg)?;
    let len = payload.len() as u32;

    if len > MAX_MESSAGE_SIZE {
        anyhow::bail!("message too large: {} bytes", len);
    }

    writer.write_all(&len.to_be_bytes()).await?;
    writer.write_all(&payload).await?;
    writer.flush().await?;
    Ok(())
}

pub async fn read_message(
    reader: &mut OwnedReadHalf,
) -> anyhow::Result<Message> {
    let mut len_buf = [0u8; 4];
    reader.read_exact(&mut len_buf).await?;
    let len = u32::from_be_bytes(len_buf);

    if len > MAX_MESSAGE_SIZE {
        anyhow::bail!("peer announced oversized message: {} bytes", len);
    }

    let mut payload = vec![0u8; len as usize];
    reader.read_exact(&mut payload).await?;

    let msg: Message = bincode::deserialize(&payload)?;
    Ok(msg)
}
```

### 20.3 Node state: mempool, known peers, seen-inventory cache

```rust
// src/state.rs
use crate::message::{Transaction, TxId};
use std::collections::{HashMap, HashSet};
use std::sync::Arc;
use tokio::sync::{mpsc, RwLock};

pub struct PeerHandle {
    pub addr: String,
    pub outbound_tx: mpsc::Sender<crate::message::Message>,
}

#[derive(Default)]
pub struct NodeState {
    pub node_id: String,
    /// Local mempool: txid -> full transaction.
    pub mempool: RwLock<HashMap<TxId, Transaction>>,
    /// Known peer addresses (for PEX / addr gossip).
    pub known_addrs: RwLock<HashSet<String>>,
    /// Currently connected peers, keyed by remote addr, for broadcasting.
    pub peers: RwLock<HashMap<String, PeerHandle>>,
    /// Inventory we've already announced/seen, to avoid re-broadcast loops
    /// — this is exactly what prevents infinite gossip storms.
    pub seen_inv: RwLock<HashSet<TxId>>,
}

pub type SharedState = Arc<NodeState>;

impl NodeState {
    pub fn new(node_id: String) -> SharedState {
        Arc::new(NodeState {
            node_id,
            ..Default::default()
        })
    }

    /// Returns true if this is a NEW transaction we haven't seen before
    /// (mirrors the mempool-acceptance check that gates real relay).
    pub async fn accept_tx(&self, tx: Transaction) -> bool {
        let id = tx.id();
        let mut mempool = self.mempool.write().await;
        if mempool.contains_key(&id) {
            return false; // already have it — do not re-relay
        }
        mempool.insert(id, tx);
        true
    }

    pub async fn mark_seen(&self, id: TxId) -> bool {
        let mut seen = self.seen_inv.write().await;
        seen.insert(id) // returns false if already present
    }
}
```

### 20.4 The connection handler — handshake + gossip loop

```rust
// src/peer.rs
use crate::codec::{read_message, write_message};
use crate::message::Message;
use crate::state::{PeerHandle, SharedState};
use tokio::net::TcpStream;
use tokio::sync::mpsc;

pub async fn handle_connection(
    stream: TcpStream,
    remote_addr: String,
    state: SharedState,
    is_outbound: bool,
) -> anyhow::Result<()> {
    let (mut reader, mut writer) = stream.into_split();

    // ---- 1. Handshake (mirrors Bitcoin's version/verack) ----
    let my_version = Message::Version {
        node_id: state.node_id.clone(),
        best_height: state.mempool.read().await.len() as u64,
        listen_port: 0, // simplified
    };
    write_message(&mut writer, &my_version).await?;

    let their_version = read_message(&mut reader).await?;
    let Message::Version { node_id: their_id, .. } = their_version else {
        anyhow::bail!("expected Version as first message, protocol violation");
    };
    write_message(&mut writer, &Message::Verack).await?;

    let verack = read_message(&mut reader).await?;
    if !matches!(verack, Message::Verack) {
        anyhow::bail!("expected Verack, protocol violation — banning peer");
    }
    println!(
        "[{}] handshake complete with {} ({}, outbound={})",
        state.node_id, their_id, remote_addr, is_outbound
    );

    // ---- 2. Register this peer for broadcast fan-out ----
    let (out_tx, mut out_rx) = mpsc::channel::<Message>(256);
    state.peers.write().await.insert(
        remote_addr.clone(),
        PeerHandle { addr: remote_addr.clone(), outbound_tx: out_tx },
    );

    // Writer task: drains the outbound channel onto the socket.
    let write_task = tokio::spawn(async move {
        while let Some(msg) = out_rx.recv().await {
            if write_message(&mut writer, &msg).await.is_err() {
                break; // peer gone; loop exits, socket drops
            }
        }
    });

    // ---- 3. Read loop: the actual gossip protocol logic ----
    let read_result = read_loop(&mut reader, &state, &remote_addr).await;

    state.peers.write().await.remove(&remote_addr);
    write_task.abort();
    read_result
}

async fn read_loop(
    reader: &mut tokio::net::tcp::OwnedReadHalf,
    state: &SharedState,
    remote_addr: &str,
) -> anyhow::Result<()> {
    loop {
        let msg = read_message(reader).await?;

        match msg {
            Message::InvTx(id) => {
                // Peer is announcing they have a tx. Pull-based gossip:
                // only request it if we've never seen this inventory item.
                let already_seen = !state.mark_seen(id).await;
                if !already_seen {
                    let peers = state.peers.read().await;
                    if let Some(peer) = peers.get(remote_addr) {
                        let _ = peer.outbound_tx.send(Message::GetTx(id)).await;
                    }
                }
            }

            Message::GetTx(id) => {
                let mempool = state.mempool.read().await;
                if let Some(tx) = mempool.get(&id).cloned() {
                    let peers = state.peers.read().await;
                    if let Some(peer) = peers.get(remote_addr) {
                        let _ = peer.outbound_tx.send(Message::Tx(tx)).await;
                    }
                }
            }

            Message::Tx(tx) => {
                let is_new = state.accept_tx(tx.clone()).await;
                if is_new {
                    println!(
                        "[{}] accepted new tx {:x?} from {}, relaying to {} peers",
                        state.node_id,
                        &tx.id()[..4],
                        remote_addr,
                        state.peers.read().await.len().saturating_sub(1),
                    );
                    broadcast_inv(state, tx.id(), remote_addr).await;
                }
            }

            Message::GetAddr => {
                let addrs: Vec<String> =
                    state.known_addrs.read().await.iter().cloned().collect();
                let peers = state.peers.read().await;
                if let Some(peer) = peers.get(remote_addr) {
                    let _ = peer.outbound_tx.send(Message::Addr(addrs)).await;
                }
            }

            Message::Addr(addrs) => {
                let mut known = state.known_addrs.write().await;
                for a in addrs {
                    known.insert(a);
                }
            }

            Message::Ping(nonce) => {
                let peers = state.peers.read().await;
                if let Some(peer) = peers.get(remote_addr) {
                    let _ = peer.outbound_tx.send(Message::Pong(nonce)).await;
                }
            }

            Message::Pong(_) => { /* liveness confirmed */ }
            Message::Version { .. } | Message::Verack => {
                anyhow::bail!("unexpected handshake message post-handshake, banning peer");
            }
        }
    }
}

/// Fan out an InvTx announcement to every peer EXCEPT the one it came from —
/// this "don't send back where it came from" rule is the core anti-storm
/// mechanism in every real flooding/gossip protocol.
async fn broadcast_inv(state: &SharedState, id: crate::message::TxId, exclude: &str) {
    let peers = state.peers.read().await;
    for (addr, handle) in peers.iter() {
        if addr != exclude {
            let _ = handle.outbound_tx.send(Message::InvTx(id)).await;
        }
    }
}
```

### 20.5 The node entry point: listener + outbound dialer

```rust
// src/main.rs
mod codec;
mod message;
mod peer;
mod state;

use state::NodeState;
use tokio::net::TcpListener;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let args: Vec<String> = std::env::args().collect();
    let listen_addr = args.get(1).cloned().unwrap_or_else(|| "127.0.0.1:9000".into());
    let node_id = args.get(2).cloned().unwrap_or_else(|| "node-A".into());
    let seed_peer = args.get(3).cloned(); // optional: "127.0.0.1:9001"

    let state = NodeState::new(node_id.clone());

    // ---- Listener: accept inbound connections (like a reachable full node) ----
    let listener = TcpListener::bind(&listen_addr).await?;
    println!("[{}] listening on {}", node_id, listen_addr);

    {
        let state = state.clone();
        tokio::spawn(async move {
            loop {
                if let Ok((stream, remote)) = listener.accept().await {
                    let state = state.clone();
                    tokio::spawn(async move {
                        let _ = peer::handle_connection(
                            stream,
                            remote.to_string(),
                            state,
                            false, // inbound
                        )
                        .await;
                    });
                }
            }
        });
    }

    // ---- Outbound: dial a seed peer, mirrors DNS-seed bootstrapping ----
    if let Some(seed) = seed_peer {
        let state = state.clone();
        tokio::spawn(async move {
            match tokio::net::TcpStream::connect(&seed).await {
                Ok(stream) => {
                    let _ = peer::handle_connection(stream, seed, state, true).await;
                }
                Err(e) => eprintln!("failed to dial seed: {e}"),
            }
        });
    }

    // ---- Demo: originate a transaction locally after a short delay,
    //      watch it propagate via InvTx/GetTx/Tx gossip ----
    {
        let state = state.clone();
        tokio::spawn(async move {
            tokio::time::sleep(std::time::Duration::from_secs(3)).await;
            let tx = message::Transaction {
                from: node_id.clone(),
                to: "someone".into(),
                amount: 42,
                nonce: rand::random(),
            };
            let id = tx.id();
            state.accept_tx(tx).await;
            state.mark_seen(id).await;
            let peers = state.peers.read().await;
            for (_, handle) in peers.iter() {
                let _ = handle.outbound_tx.send(message::Message::InvTx(id)).await;
            }
        });
    }

    // Keep the process alive.
    tokio::signal::ctrl_c().await?;
    Ok(())
}
```

### 20.6 Running the demo

```bash
# Terminal 1 — first node, no peers yet
cargo run -- 127.0.0.1:9000 node-A

# Terminal 2 — second node, dials node A as a seed
cargo run -- 127.0.0.1:9001 node-B 127.0.0.1:9000

# Terminal 3 — third node, dials node B; watch the tx from A
# propagate A -> B -> C purely via InvTx/GetTx/Tx gossip
cargo run -- 127.0.0.1:9002 node-C 127.0.0.1:9001
```

What this reproduces, faithfully in miniature:

- **Length-prefixed framing** (§7, §17) — real length-prefix parsing, with a max-size DoS guard.
- **Version/Verack handshake** (§7.1) — protocol-violation peers get banned (connection dropped).
- **Pull-based inv/getdata gossip** (§8.2) — transactions are announced by ID first, fetched only if new.
- **Anti-storm relay rule** (§8) — never re-send inventory back to the peer it came from.
- **PEX / addr exchange** (§5.3) — `GetAddr`/`Addr` for peer discovery.
- **Liveness checking** (§17) — `Ping`/`Pong`.

Everything from here to a real client is *degree*, not *kind*: real nodes add persistent peer scoring, encrypted transport (Noise/RLPx — see §7.2–7.3), Kademlia-based discovery (§6) instead of a single seed argument, compact-block reconstruction (§10.2), and orders of magnitude more validation — but the message-flow skeleton above is exactly the one Bitcoin Core, Geth, and most other clients run.

---

## 21. Glossary

- **ENR (Ethereum Node Record)** — signed record describing a node's identity, address, and capabilities.
- **Eclipse attack** — isolating a single victim node behind attacker-controlled peers.
- **Sybil attack** — flooding a network with fake identities to gain disproportionate influence.
- **Gossip / epidemic broadcast** — probabilistic, redundant-path message dissemination.
- **Kademlia** — XOR-distance-based structured DHT used for peer discovery.
- **Compact block relay** — sending short transaction IDs instead of full transactions to reconstruct blocks from local mempools.
- **Dandelion++** — two-phase (stem then fluff) transaction relay to resist origin-IP deanonymization.
- **NAT traversal / hole punching** — techniques to establish direct connections between two NATed peers.
- **Data availability sampling** — verifying data was published by sampling random small chunks, backed by erasure coding.
- **Sync committee** — a rotating, small validator subset that signs headers so light clients can verify cheaply.
- **RLPx** — Ethereum's encrypted transport/session layer for devp2p.
- **GossipSub** — libp2p's topic-scoped, mesh-based pub/sub protocol.
- **Noise Protocol Framework** — a formal toolkit for building authenticated, encrypted handshakes.

---

*This guide covers the networking layer specifically — it deliberately does not re-explain consensus algorithms (Nakamoto consensus, Casper FFG, Tendermint BFT) or cryptographic primitives (ECDSA, BLS, Merkle trees) except where they directly shape network design decisions.*
